"""Engine utama (GitHub Actions cron tiap 5 menit) – v16.

FOREX  : XAUUSD – RealMarketAPI M5. Engine memutuskan sendiri kapan fetch: 5 menit di sesi aktif
         (07–12 New York), 10 menit di luar sesi, 0 saat pasar tutup, diperlambat otomatis bila kuota menipis.
         -> M15 / H1 / H4 / D / W (sesi 17:00 New York)
CRYPTO : top 10 Binance Spot – M1 tiap run -> M5/M15/H1/H4 (+ native D/W) + order book + transaksi + 24 jam.
BACKFILL (v16): penyimpanan candle diisi otomatis sampai kapasitas maksimal (backfill.py), lalu model geser.
Hasil  : state.json + chart JSON (SELURUH riwayat) + journal (semua setup & entry + hasil) + PNG + alert Telegram.

python main.py --store ../store --site ../_site
"""
import argparse
import json
import os
import shutil
import time
from datetime import datetime, timezone

import backfill
import config
import messages
import session
from analysis import analyze_tf, overall
from binance import Binance
from charts import render
from data_source import fetch_candles, fetch_history
from journal import Journal, stats
from orderflow import book_metrics, flow_summary, trade_metrics
from resample import TF_SEC, closed_only, resample, upsert
from telegram_notify import send_message, send_photo

RENDER = os.getenv("RENDER_CHARTS", "true").lower() != "false"
STATE_TAIL = 10          # state.json hanya ringkasan; riwayat lengkap ada di chart_*.json & journal_*.json


def jload(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))


def rnd(b, d):
    x = {"t": b["t"], "o": round(b["o"], d), "h": round(b["h"], d), "l": round(b["l"], d), "c": round(b["c"], d)}
    if "v" in b:
        x["v"] = round(b["v"], 6)
        x["bv"] = round(b.get("bv", 0), 6)
        x["tc"] = b.get("tc", 0)
    return x


class Engine:
    def __init__(self, store_dir, site_dir, now=None, fetch=None, binance=None, fetch_hist=None):
        self.store, self.site = store_dir, site_dir
        self.now = int(now or time.time())
        self.fetch = fetch or fetch_candles
        self.fetch_hist = fetch_hist or fetch_history
        self.p = jload(os.path.join(store_dir, "private_state.json"), {})
        for k, v in (("sent", []), ("init", {}), ("bootstrap", {}), ("req", {}), ("fail", {}),
                     ("book_hist", {}), ("rendered", {})):
            self.p.setdefault(k, v)
        self.p.pop("lvl", None)
        self.bn = binance or Binance(self.p.get("binance_host"))
        self.tg_ok = True
        self.outbox, self.warnings, self.errors = [], [], {}

    # ---------- util ----------
    def month(self):
        return datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m")

    def req_month(self):
        return self.p["req"].get(self.month(), 0)

    def count_req(self):
        self.p["req"] = {self.month(): self.req_month() + 1}

    def cpath(self, sym, tf):
        return os.path.join(self.store, "candles", f"{sym}_{tf}.json")

    def load(self, sym):
        return {tf: jload(self.cpath(sym, tf), []) for tf in config.tfs_of(sym)}

    def save(self, sym, stores):
        for tf, bars in stores.items():
            jsave(self.cpath(sym, tf), bars[-config.STORE_LIMIT[tf]:])

    def sent(self, key):
        return key in self.p["sent"]

    def mark(self, key):
        self.p["sent"].append(key)
        self.p["sent"] = self.p["sent"][-3000:]

    def notify(self, text, photo=None, prio=0):
        self.outbox.append((prio, text, photo))

    def fail(self, sym, err):
        self.errors[sym] = err
        self.p["fail"][sym] = self.p["fail"].get(sym, 0) + 1
        if self.p["fail"][sym] == 3:
            self.notify(f"⚠️ <b>{sym}</b>: pengambilan data gagal 3x berturut-turut.\n{messages.escape(err)}")

    def recover(self, sym, src):
        if self.p["fail"].get(sym, 0) >= 3:
            self.notify(f"✅ <b>{sym}</b>: pengambilan data {src} pulih kembali.")
        self.p["fail"][sym] = 0

    # ---------- FOREX (XAUUSD) ----------
    def forex_fetch(self, sym, stores):
        if not config.REALMARKET_API_KEY:
            self.fail(sym, "REALMARKET_API_KEY belum diisi")
            return 0
        b = self.p["bootstrap"].get(sym, 0)
        if config.BOOTSTRAP_H1 and b < 2 and len(stores["H1"]) < 100:
            self.count_req()
            rows, _ = self.fetch(sym, "H1")
            self.p["bootstrap"][sym] = 9 if rows else b + 1
            if rows:
                for r in rows:
                    r["n"] = 12
                stores["H1"] = upsert(stores["H1"], rows, rows[0]["t"])
                for tf in ("H4", "D", "W"):
                    stores[tf] = upsert(stores[tf], resample(rows, tf, "forex"), rows[0]["t"])
        self.count_req()
        rows, err = self.fetch(sym, config.FOREX_SOURCE_TF)
        if err:
            self.fail(sym, err)
            return 0
        self.recover(sym, "RealMarketAPI")
        m5 = stores["M5"]
        if m5:
            gap = rows[0]["t"] - m5[-1]["t"]
            if 300 < gap < 40 * 3600:
                self.warnings.append(f"{sym}: celah data M5 ±{gap // 60} menit sebelum {messages.wib(rows[0]['t'], '%d/%m %H:%M')}")
        stores["M5"] = upsert(m5, rows, rows[0]["t"])[-config.STORE_LIMIT["M5"]:]
        first = stores["M5"][0]["t"]
        for tf in ("M15", "H1", "H4", "D", "W"):
            stores[tf] = upsert(stores[tf], resample(stores["M5"], tf, "forex"), first)
        return len(rows)

    # ---------- CRYPTO (Binance) ----------
    def crypto_fetch(self, sym, stores):
        bn = self.bn
        if not self.p["bootstrap"].get(sym):
            ok = True
            for tf, lim in (("M5", 1000), ("M15", 1000), ("H1", 1000), ("H4", 1000), ("D", 1000), ("W", 500)):
                rows, err = bn.klines(sym, tf, lim)
                if err:
                    ok = False
                    self.warnings.append(f"{sym}: bootstrap {tf} gagal ({err})")
                    break
                if rows:
                    stores[tf] = upsert(stores[tf], rows, rows[0]["t"])
            if ok:
                self.p["bootstrap"][sym] = self.now
        m1 = stores["M1"]
        got = []
        if m1 and self.now - m1[-1]["t"] > 950 * 60:          # tertinggal jauh -> kejar dengan startTime
            start = m1[-1]["t"] + 60
            for _ in range(4):
                rows, err = bn.klines(sym, "M1", 1000, start * 1000)
                if err or not rows:
                    break
                got += rows
                start = rows[-1]["t"] + 60
                if len(rows) < 1000:
                    break
        else:
            got, err = bn.klines(sym, "M1", 1000)
            if err:
                self.fail(sym, err)
                return 0
        if not got:
            self.fail(sym, "Binance tidak mengirim candle M1")
            return 0
        self.recover(sym, "Binance")
        if m1 and got[0]["t"] - m1[-1]["t"] > 60:
            self.warnings.append(f"{sym}: celah data M1 ±{(got[0]['t'] - m1[-1]['t']) // 60} menit")
        stores["M1"] = upsert(m1, got, got[0]["t"])[-config.STORE_LIMIT["M1"]:]
        first = stores["M1"][0]["t"]
        for tf in ("M5", "M15", "H1", "H4"):
            stores[tf] = upsert(stores[tf], resample(stores["M1"], tf, "crypto"), first)
        for tf in ("H4", "D", "W"):                       # candle besar native agar volume persis
            rows, _ = bn.klines(sym, tf, 3)
            if rows:
                stores[tf] = upsert(stores[tf], rows, rows[0]["t"])
        return len(got)

    def crypto_extra(self, sym, stores, got):
        price = stores["M1"][-1]["c"] if stores["M1"] else None
        if not (got and price):
            return {}
        dep, err = self.bn.depth(sym, config.DEPTH_LIMIT)
        bk = book_metrics(dep, price) if dep else None
        tr_raw, err2 = self.bn.agg_trades(sym, 1000)
        tr = trade_metrics(tr_raw, config.LARGE_TRADE_USD) if tr_raw else None
        tk, _ = self.bn.ticker24(sym)
        if bk:
            hist = self.p["book_hist"].setdefault(sym, [])
            hist.append([self.now, round(bk["imb_0.5"], 1), round(bk["imb_1.0"], 1), round(tr["buy_pct"], 1) if tr else None])
            self.p["book_hist"][sym] = hist[-300:]
        extra = {"book": bk, "trades": tr, "book_hist": self.p["book_hist"].get(sym, [])}
        if tk:
            extra["ticker"] = {"change_pct": float(tk["priceChangePercent"]), "high": float(tk["highPrice"]),
                               "low": float(tk["lowPrice"]), "volume": float(tk["volume"]),
                               "quote_volume": float(tk["quoteVolume"]), "trades": int(tk.get("count", 0))}
        if err or err2:
            self.warnings.append(f"{sym}: order book/transaksi tidak lengkap ({err or err2})")
        return extra

    # ---------- analisis + output satu pair ----------
    def analyze(self, sym, stores, received, extra):
        market = config.market_of(sym)
        tfs_list = config.tfs_of(sym)
        src = stores["M1" if market == "crypto" else "M5"] or stores["H1"]
        if not src:
            return None
        price = src[-1]["c"]
        d = config.digits(sym, price)
        tfs, series, closed = {}, {}, {}
        for tf in tfs_list:
            closed[tf] = closed_only(stores[tf][-config.STORE_LIMIT[tf]:], tf, self.now)
            an = analyze_tf(tf, closed[tf], price, d)
            if market == "crypto" and closed[tf]:
                an["flow"] = flow_summary(closed[tf])
            series[tf] = an.pop("series", {})
            tfs[tf] = an
        ov = overall(tfs, d)

        # ----- jurnal permanen: setup semua TF + entry top-down, lalu nilai hasilnya -----
        jr = Journal(self.store, sym)
        new_setups = {tf: jr.merge("setups", tfs[tf].get("setups", []), self.now) for tf in tfs_list}
        new_entries = jr.merge("entries", ov.get("entries", []), self.now)
        entry_tfs = [tf for tf in ("M1", "M5", "M15", "H1") if tf in tfs_list]
        jr.update_outcomes(closed, entry_tfs)
        jr.save()
        pub = jr.public()
        pub.update(digits=d, market=market, updated_ts=self.now,
                   stats={"entries": stats(pub["entries"]),
                          "setups": {tf: stats([r for r in pub["setups"] if r["tf"] == tf]) for tf in tfs_list}})
        jsave(os.path.join(self.site, "data", f"journal_{sym}.json"), pub)

        # ----- chart JSON: SELURUH candle & overlay tersimpan (tidak dipotong) -----
        for tf in tfs_list:
            a = tfs[tf]
            rows, ma_idx = [], {}
            vol = market == "crypto"
            for i, b in enumerate(stores[tf][-config.CHART_BARS[tf]:]):
                x = rnd(b, d)
                row = [x["t"], x["o"], x["h"], x["l"], x["c"]]
                if vol:
                    row += [x.get("v", 0), x.get("bv", 0), x.get("tc", 0)]
                row.append(1 if b["t"] + TF_SEC[tf] > self.now else 0)
                rows.append(row)
                ma_idx[b["t"]] = i
            mas = {}
            for k in ("ma20", "ma50", "ma200"):
                arr = [None] * len(rows)
                for t, v in zip(series[tf].get("times", []), series[tf].get(k, [])):
                    i = ma_idx.get(t)
                    if i is not None and v is not None:
                        arr[i] = round(v, d)
                mas[k] = arr
            jsave(os.path.join(self.site, "data", f"chart_{sym}_{tf}.json"), {
                "v": 2, "symbol": sym, "market": market, "tf": tf, "digits": d,
                "cols": ["t", "o", "h", "l", "c"] + (["v", "bv", "tc"] if vol else []) + ["forming"],
                "rows": rows, **mas,
                **{k: a.get(k, []) for k in ("events", "sweeps", "fvg", "ob", "fvg_mit", "ob_mit",
                                               "liquidity", "liquidity_done", "pd_zones", "strong_weak")},
                "setup": a.get("setup"), "signal": a.get("signal")})
            self.render_png(sym, tf, stores[tf], a, series[tf], d, market)

        tfs_small = {}
        for tf, a in tfs.items():
            q = {k: v for k, v in a.items() if not k.startswith("_") and k not in ("setups", "liquidity_done")}
            for k in ("events", "sweeps", "liquidity"):
                q[k] = a.get(k, [])[-STATE_TAIL:]
            for k in ("fvg", "ob"):
                q[k] = a.get(k, [])[-6:]
            q["fvg_mit"], q["ob_mit"] = [], []
            tfs_small[tf] = q
        ov_small = {k: v for k, v in ov.items() if k != "entries"}
        pair = {"market": market, "digits": d, "price": round(price, d), "last_ts": src[-1]["t"],
                "received": received, "counts": {tf: len(stores[tf][-config.STORE_LIMIT[tf]:]) for tf in tfs_list},
                "tf": tfs_small, "overall": ov_small, "journal": pub["stats"], **extra}
        self.alerts(sym, pair, new_setups, new_entries, jr.closed_now)
        return pair

    def render_png(self, sym, tf, store_bars, a, series, d, market):
        if not RENDER or not store_bars:
            return
        last_closed = a.get("last_closed_t") or 0
        key = f"{sym}_{tf}"
        prev = self.p["rendered"].get(key, 0)
        min_gap = 900 if tf == "M1" else 0
        if last_closed == prev or (min_gap and last_closed - prev < min_gap):
            return
        bars = [rnd(b, d) | ({"forming": True} if b["t"] + TF_SEC[tf] > self.now else {}) for b in store_bars[-150:]]
        t0 = bars[0]["t"]
        keep = lambda z: (z.get("end_t") or z.get("t", 0)) >= t0      # noqa: E731
        an = dict(a, series=series)
        for k in ("events", "sweeps", "fvg", "ob", "fvg_mit", "ob_mit", "liquidity"):
            an[k] = [z for z in a.get(k, []) if keep(z)][-40:]
        try:
            out = os.path.join(self.store, "charts", f"{sym}_{tf}.png")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            render(out, sym, tf, bars, an, d)
            self.p["rendered"][key] = last_closed
        except Exception as e:      # noqa: BLE001 – chart gagal tidak boleh menghentikan engine
            self.warnings.append(f"Chart {sym} {tf} gagal dibuat: {e}")

    # ---------- ALERT: 🟡 STRUCTURE -> 🟠 SETUP READY -> 🚨 ENTRY -> 🏁 HASIL ----------
    def alerts(self, sym, pair, new_setups, new_entries, closed_now):
        first = not self.p["init"].get(sym)
        for tf in config.ALERT_TFS:
            an = pair["tf"].get(tf) or {}
            if not an.get("enough"):
                continue
            recent = an.get("last_closed_t", 0) - 2 * TF_SEC[tf]
            for e in an.get("events", []):
                if e["t"] < recent or (e.get("scope") == "internal" and not config.ALERT_INTERNAL_STRUCTURE):
                    continue
                k = f"{sym}:EV:{tf}:{e['id']}"
                if self.sent(k):
                    continue
                self.mark(k)
                if not first:
                    self.notify(self.msg_structure(sym, tf, e, pair), prio=1)
        if not first:
            for s in new_setups.get(config.SETUP_TF, []):
                if s["src"] == "live":
                    self.notify(self.msg_setup(sym, s, pair), prio=2)
            for e in new_entries:
                if e["src"] == "live":
                    photo = (os.path.join(self.store, "charts", f"{sym}_{config.SETUP_TF}.png")
                             if config.ALERT_WITH_CHART else None)
                    self.notify(self.msg_entry(sym, e, pair), photo, prio=3)
            for e in closed_now:
                if e["src"] == "live":
                    self.notify(self.msg_result(sym, e, pair), prio=2)
        else:
            self.p["init"][sym] = self.now
            ov = pair["overall"]
            js = pair["journal"]["entries"]
            self.notify(f"🤖 <b>Alert Bot aktif – {sym}</b>\nHarga: {messages.f(pair['price'], pair['digits'])}\n"
                        f"Strategi: {config.BIAS_TF} → {config.SETUP_TF} → {config.CONFIRM_TF}\n"
                        f"Sinyal saat ini: {messages.ICON[ov['signal']]}\n"
                        f"Riwayat (replay data tersimpan): {js['total']} entry · winrate {js['winrate'] if js['winrate'] is not None else '-'}%\n"
                        f"Ketik /help untuk command.")

    # ---------- format pesan ----------
    @staticmethod
    def _mtf(pair, want):
        rows, same, n = [], 0, 0
        for t in (config.BIAS_TF, config.SETUP_TF, config.CONFIRM_TF):
            a = pair["tf"].get(t) or {}
            s = a.get("swing_structure") if a.get("enough") else None
            n += 1
            if not s:
                rows.append(f"{t}: ⚪ Netral/data kurang")
                continue
            same += s == want
            rows.append(f"{t}: {'🟢 Bullish' if s == 'bull' else '🔴 Bearish'} {'✅' if s == want else '⚠️'}")
        lab = "STRONG 💪" if same == n else "MIXED" if same else "BERLAWANAN ⚠️"
        return "\n<b>Swing structure</b>\n" + "\n".join(rows) + f"\nAlignment: {lab}\n"

    def msg_structure(self, sym, tf, e, pair):
        d, bull, ch = pair["digits"], e["dir"] == "bull", e["type"] == "CHoCH"
        icon = "🔄" if ch else ("⬆️" if bull else "⬇️")
        desc = (("Struktur bearish → bullish" if bull else "Struktur bullish → bearish") if ch
                else ("Continuation bullish" if bull else "Continuation bearish"))
        return (f"🟡 <b>STRUCTURE ALERT</b>\n<b>{sym} • {tf}</b>\n\n{icon} <b>{'BULLISH' if bull else 'BEARISH'} {e['label']}</b>\n{desc}\n"
                f"Displacement: {'ya' if e.get('displacement') else 'tidak'}\n\n"
                f"Break level: {messages.f(e['level'], d)}\nCandle: {messages.wib(e['t'], '%d/%m %H:%M')}\n"
                f"Harga: {messages.f(pair['price'], d)}\n{self._mtf(pair, e['dir'])}\n"
                f"📊 Status: INFO STRUKTUR – bukan sinyal entry")

    def msg_setup(self, sym, s, pair):
        d = pair["digits"]
        return (f"🟠 <b>SETUP READY</b>\n<b>{sym} • {config.SETUP_TF}</b> – {'🟢 BUY' if s['signal'] == 'BUY' else '🔴 SELL'}\n\n"
                f"Model: {messages.escape(s['model'])}\n"
                f"Zona: {s['zone']['kind']} {messages.f(s['zone']['bottom'], d)}–{messages.f(s['zone']['top'], d)}\n"
                f"Target: {s['target']['label']} {messages.f(s['target']['level'], d)} · RR {s['rr']}\n\n"
                + "\n".join(f"{messages.mark(ok)} {messages.escape(t)}" for ok, t in pair["overall"]["reasons"])
                + f"\n{self._mtf(pair, s['dir'])}\n⏳ WAIT – menunggu bias {config.BIAS_TF} & trigger {config.CONFIRM_TF}. Belum entry.")

    def msg_entry(self, sym, s, pair):
        d, sig = pair["digits"], s["signal"]
        return (f"🚨 <b>ENTRY SIGNAL</b>\n<b>{sym}</b> – <b>{'🟢 BUY' if sig == 'BUY' else '🔴 SELL'}</b>\n"
                f"⭐ Top-down terkonfirmasi ({config.BIAS_TF}→{config.SETUP_TF}→{config.CONFIRM_TF})\n\n"
                f"Model: {messages.escape(s['model'])} • RR {s['rr']}\n"
                f"Zona: {s['zone']['kind']} {messages.f(s['zone']['bottom'], d)}–{messages.f(s['zone']['top'], d)}\n"
                f"Sweep: {s['sweep']['side']} @ {messages.f(s['sweep']['level'], d)}\n"
                f"Trigger {config.CONFIRM_TF}: {s['trigger']['label']} @ {messages.f(s['trigger']['level'], d)}\n"
                f"Target: {s['target']['label']} {messages.f(s['target']['level'], d)}\n\n"
                f"🎯 Entry: {messages.f(s['entry'], d)}\n🛑 SL: {messages.f(s['sl'], d)}\n"
                f"✅ TP1: {messages.f(s['tp1'], d)} ({config.TP1_R:g}R) | TP2: {messages.f(s['tp2'], d)}\n"
                f"Candle: {messages.wib(s['close_t'], '%d/%m %H:%M')}\n\n🔥 STATUS: {sig} CONFIRMED")

    def msg_result(self, sym, e, pair):
        d = pair["digits"]
        win = e["status"] == "TP2"
        js = pair["journal"]["entries"]
        return (f"🏁 <b>HASIL ENTRY {sym}</b> – {'✅ TP2' if win else '❌ SL'}\n"
                f"{e['signal']} @ {messages.f(e['entry'], d)} ({messages.wib(e['close_t'], '%d/%m %H:%M')})\n"
                f"Hasil: <b>{e['result_r']:+.2f}R</b> · TP1 {'tersentuh' if e['tp1_hit'] else 'tidak'} · MFE {e['mfe_r']}R\n"
                f"Statistik {sym}: {js['closed']} selesai · winrate {js['winrate']}% · net {js['net_r']:+}R")

    def flush(self):
        for _, text, photo in sorted(self.outbox, key=lambda x: -x[0]):
            ok = False
            if photo and os.path.exists(photo):
                ok, _ = send_photo(photo, text)
            if not ok:
                ok, _ = send_message(text)
            self.tg_ok = self.tg_ok and ok

    # ---------- run ----------
    def run(self):
        pairs = {}
        # FOREX (XAUUSD) – jadwal berbasis kuota
        fx_closed = config.SKIP_WEEKEND and session.market_closed(self.now)
        for sym in config.FOREX_SYMBOLS:                 # backfill dulu (memakai kuota) sebelum rencana kuota dihitung
            stores = self.load(sym)
            backfill.run(self, sym, stores, self.fetch_hist)
            self.save(sym, stores)
        pl = session.plan(self.now, self.req_month(), max(1, len(config.FOREX_SYMBOLS)))
        interval = pl["interval_min"]
        last_fx = self.p.get("forex_fetch_ts") or 0
        fx_due = bool(config.FOREX_SYMBOLS) and not fx_closed and interval is not None and \
            self.now - last_fx >= interval * 60 - config.FETCH_TOLERANCE_SEC
        if interval is None and not fx_closed and config.FOREX_SYMBOLS:
            self.warnings.append("Kuota RealMarketAPI bulan ini habis (cadangan tersisa) – fetch XAUUSD dihentikan sampai bulan depan.")
        elif pl["throttled"] and config.FOREX_SYMBOLS:
            self.warnings.append(f"Kuota menipis – interval XAUUSD dinaikkan otomatis ke {interval} menit.")
        if fx_due:
            self.p["forex_fetch_ts"] = self.now
        for sym in config.FOREX_SYMBOLS:
            stores = self.load(sym)
            got = self.forex_fetch(sym, stores) if fx_due else 0
            self.save(sym, stores)
            pr = self.analyze(sym, stores, got, {})
            if pr:
                pairs[sym] = pr
        if fx_due:
            self.p["forex_ok"] = all(s not in self.errors for s in config.FOREX_SYMBOLS)
        # CRYPTO – tiap run
        cr_ok = True
        for sym in config.CRYPTO_SYMBOLS:
            stores = self.load(sym)
            backfill.run(self, sym, stores, None)
            got = self.crypto_fetch(sym, stores)
            cr_ok = cr_ok and sym not in self.errors
            self.save(sym, stores)
            pr = self.analyze(sym, stores, got, self.crypto_extra(sym, stores, got))
            if pr:
                pairs[sym] = pr
        if self.bn.host:
            self.p["binance_host"] = self.bn.host
        self.flush()

        src_dir = os.path.join(self.store, "charts")
        if os.path.isdir(src_dir):
            shutil.copytree(src_dir, os.path.join(self.site, "charts"), dirs_exist_ok=True)

        last_fetch = self.p.get("forex_fetch_ts") or self.now
        bf = self.p.get("backfill", {})
        st = {
            "version": 16, "updated_ts": self.now, "telegram_ok": self.tg_ok,
            "errors": self.errors, "warnings": self.warnings,
            "strategy": {"bias": config.BIAS_TF, "setup": config.SETUP_TF, "confirm": config.CONFIRM_TF},
            "backfill": {"fx_requests": bf.get("_fx_req", 0), "fx_budget": backfill.FX_BUDGET,
                         "done": {s: all(v.get("done") for v in bf.get(s, {}).values()) if bf.get(s) else False
                                  for s in pairs}},
            "sources": {
                "forex": {"symbols": config.FOREX_SYMBOLS, "ok": self.p.get("forex_ok", True), "market_closed": fx_closed,
                          "fetch_ts": self.p.get("forex_fetch_ts"), "fetched_now": fx_due,
                          "interval_min": interval, "base_min": pl["base_min"],
                          "fast_min": config.FAST_INTERVAL_MIN, "slow_min": config.SLOW_INTERVAL_MIN,
                          "next_ts": last_fetch + (interval or config.SLOW_INTERVAL_MIN) * 60,
                          "req_month": self.req_month(), "quota": config.MONTHLY_QUOTA,
                          "projected_month": pl["projected"], "throttled": pl["throttled"]},
                "crypto": {"symbols": config.CRYPTO_SYMBOLS, "ok": cr_ok, "host": self.bn.host,
                           "fetch_ts": self.now if config.CRYPTO_SYMBOLS else None,
                           "next_ts": self.now + config.CRYPTO_INTERVAL_MIN * 60, "interval_min": config.CRYPTO_INTERVAL_MIN,
                           "calls": self.bn.calls, "weight": self.bn.weight},
            },
            "symbols": list(pairs.keys()), "dashboard_url": config.DASHBOARD_URL, "pairs": pairs,
        }
        st["messages"] = messages.build_all(st)
        jsave(os.path.join(self.site, "data", "state.json"), st)
        jsave(os.path.join(self.store, "private_state.json"), self.p)
        print(f"Selesai {messages.wib(self.now)} | XAU fetch={fx_due} interval={interval} tutup={fx_closed} | "
              f"kuota={self.req_month()}/{config.MONTHLY_QUOTA} proyeksi={pl['projected']} | "
              f"backfill XAU req={bf.get('_fx_req', 0)}/{backfill.FX_BUDGET} | "
              f"Binance host={self.bn.host} panggilan={self.bn.calls} weight={self.bn.weight}")
        for sym, pr in pairs.items():
            js = pr["journal"]["entries"]
            print(f"  {sym}: {pr['price']} | diterima {pr['received']} | sinyal {pr['overall']['signal']} | "
                  f"candle {pr['counts']} | entry tercatat {js['total']} (WR {js['winrate']}%)")
        for w in self.warnings:
            print("  WARNING:", w)
        for s, e in self.errors.items():
            print("  ERROR:", s, e)
        return st


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="../store")
    ap.add_argument("--site", default="../_site")
    a = ap.parse_args()
    Engine(a.store, a.site).run()
