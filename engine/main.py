"""Engine utama (GitHub Actions cron tiap 5 menit) – v19.
v19: Auto Fibonacci (H4 utama + Fib lokal per TF), MODE MONITOR (BUY/SELL dinonaktifkan sementara; engine
     mengirim kejadian/setup untuk dicek manual), antrean Telegram + retry + log status per pesan,
     catatan run (trigger & durasi) untuk memantau scheduler.
v18 (zone engine: Sweep Reversal + CHoCH Retest, notif zona).

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
import monitor
import session
import telegram_notify
from analysis import analyze_tf, overall
from binance import Binance
from charts import render
from data_source import fetch_candles, fetch_history
from journal import Journal, stats
from intel import SnapshotStore, build_intelligence, telegram_suffix
from intel.common import compact_snapshot
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
                     ("book_hist", {}), ("rendered", {}), ("tg_queue", []), ("tg_log", []), ("runs", [])):
            self.p.setdefault(k, v)
        self.p.pop("lvl", None)
        self.bn = binance or Binance(self.p.get("binance_host"))
        self.tg_ok = True
        self.outbox, self.warnings, self.errors = [], [], {}
        self.monitors = {}
        self.t_start = time.time()

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
        self.p["sent"] = self.p["sent"][-6000:]

    def notify(self, text, photo=None, prio=0, key=None, sym=None):
        self.outbox.append({"prio": prio, "text": text, "photo": photo, "key": key, "sym": sym,
                            "created": self.now, "attempts": 0})

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
        fib_primary = (tfs.get(config.FIB_PRIMARY_TF) or {}).get("fib")

        # ----- Phase 1 Intelligence: read-only context over the canonical engine -----
        intel_ctx = None
        if config.INTEL_ENABLED:
            try:
                intel_ctx = build_intelligence(sym, market, stores, tfs, self.now, price, d)
                intel_store = SnapshotStore(self.store, sym)
                intel_store.add(intel_ctx)
                intel_store.save(os.path.join(self.site, "data", f"intel_{sym}.json"))
            except Exception as e:      # intelligence failure must never stop legacy v20
                self.warnings.append(f"Intel {sym} gagal: {e.__class__.__name__}: {e}")
                intel_ctx = {"enabled": True, "mode": config.INTEL_MODE, "error": f"{e.__class__.__name__}: {e}",
                             "blocking": False, "asof": self.now}

        # ----- jurnal permanen: setup semua TF + entry top-down, lalu nilai hasilnya -----
        jr = Journal(self.store, sym)
        intel_journal = compact_snapshot(intel_ctx)
        new_setups = {tf: jr.merge("setups", tfs[tf].get("setups", []), self.now, intel_journal) for tf in tfs_list}
        new_entries = jr.merge("entries", ov.get("entries", []), self.now, intel_journal)
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
                                               "liquidity", "liquidity_done", "pd_zones", "strong_weak", "zones")},
                "setup": a.get("setup") if config.SIGNALS_ON else None,
                "signal": a.get("signal") if config.SIGNALS_ON else None,
                "mode": config.SIGNAL_MODE,
                "fib_primary": fib_primary, "fib_local": a.get("fib") if tf != config.FIB_PRIMARY_TF else None})
            self.render_png(sym, tf, stores[tf], a, series[tf], d, market, fib_primary)

        tfs_small = {}
        for tf, a in tfs.items():
            q = {k: v for k, v in a.items() if not k.startswith("_") and k not in ("setups", "liquidity_done")}
            for k in ("events", "sweeps", "liquidity"):
                q[k] = a.get(k, [])[-STATE_TAIL:]
            for k in ("fvg", "ob"):
                q[k] = a.get(k, [])[-6:]
            q["fvg_mit"], q["ob_mit"] = [], []
            q["zones"] = [{k: v for k, v in z.items() if k != "events"}
                          for z in a.get("zones", []) if not z.get("end_t")][-8:]
            tfs_small[tf] = q
        ov_small = {k: v for k, v in ov.items() if k != "entries"}
        pair = {"market": market, "digits": d, "price": round(price, d), "last_ts": src[-1]["t"],
                "received": received, "counts": {tf: len(stores[tf][-config.STORE_LIMIT[tf]:]) for tf in tfs_list},
                "tf": tfs_small, "overall": ov_small, "journal": pub["stats"], "fib_primary": fib_primary,
                "intel": intel_ctx, **extra}
        if config.SIGNALS_ON:
            self.alerts(sym, pair, new_setups, new_entries, jr.closed_now)
            self.zone_alerts(sym, pair, tfs)
        elif not self.p["init"].get(sym):
            self.p["init"][sym] = self.now
        # ----- v19: monitoring kejadian/setup (observation mode) -----
        if sym in config.MONITOR_SYMBOLS:
            try:
                mon = monitor.Monitor(self.store, sym)
                new = mon.scan(tfs, d, self.now)
                self.monitors[sym] = mon
                for rec in new:
                    if rec.get("src") == "live" and intel_journal is not None:
                        rec["intel"] = intel_journal
                    if rec["notif"] != "QUEUED":
                        continue
                    photo = None
                    big = rec["type"] == "CONFLUENCE" or (rec["type"] == "HTF_POI" and rec.get("stage") in ("CHOCH", "CONFIRMED"))
                    if big and config.ALERT_WITH_CHART:
                        photo = os.path.join(self.store, "charts", f"{sym}_{config.MONITOR_TF}.png")
                    prio = {"CONFLUENCE": 3, "OB_FVG_OVERLAP": 2, "HTF_FIB": 2}.get(rec["type"], 1)
                    if rec["type"] == "HTF_POI":     # v20
                        prio = {"POI": 2, "CHOCH": 3, "CONFIRMED": 3}.get(rec.get("stage"), 1)
                    self.notify(monitor.message(sym, rec, d, price) + telegram_suffix(intel_ctx),
                                photo, prio=prio, key=rec["id"], sym=sym)
                pair["monitor"] = mon.summary()
            except Exception as e:      # noqa: BLE001 – monitor gagal tidak boleh menghentikan engine
                self.warnings.append(f"Monitor {sym} gagal: {e.__class__.__name__}: {e}")
        if not config.SIGNALS_ON:
            pair["overall"] = {"signal": "MONITOR", "setup": None, "pending": None, "reasons": [],
                               "summary": "Mode monitoring: sinyal BUY/SELL dinonaktifkan sementara. "
                                          "Engine mencatat kejadian & setup untuk dicek manual."}
            for q in tfs_small.values():
                q["signal"], q["setup"] = "MONITOR", None
        return pair

    def render_png(self, sym, tf, store_bars, a, series, d, market, fib_primary=None):
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
        an = dict(a, series=series, fib_primary=fib_primary,
                  fib_local=a.get("fib") if tf != config.FIB_PRIMARY_TF else None)
        for k in ("events", "sweeps", "fvg", "ob", "fvg_mit", "ob_mit", "liquidity", "zones"):
            an[k] = [z for z in a.get(k, []) if keep(z)][-40:]
        if not config.SIGNALS_ON:       # v19: PNG minimalis untuk notif monitoring (tanpa BUY/SELL)
            px = store_bars[-1]["c"]

            def near(zs):
                out = []
                for dr in ("bull", "bear"):
                    out += sorted((z for z in zs if z["dir"] == dr),
                                  key=lambda z: min(abs(px - z["top"]), abs(px - z["bottom"])))[:3]
                return out
            an.update(setup=None, signal=None, zones=[], fvg_mit=[], ob_mit=[], liquidity=[], pd_zones=[],
                      series={}, fvg=near(an.get("fvg", [])),
                      ob=near([z for z in an.get("ob", []) if z.get("scope") == "swing"]),
                      events=[e for e in an.get("events", []) if e.get("scope") == "swing"])
        try:
            out = os.path.join(self.store, "charts", f"{sym}_{tf}.png")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            render(out, sym, tf, bars, an, d)
            self.p["rendered"][key] = last_closed
        except Exception as e:      # noqa: BLE001 – chart gagal tidak boleh menghentikan engine
            self.warnings.append(f"Chart {sym} {tf} gagal dibuat: {e}")

    # ---------- ALERT: 🟡 STRUCTURE -> 🟠 SETUP READY -> 🚨 ENTRY -> 🏁 HASIL ----------
    @staticmethod
    def _with_intel(text, pair):
        return text + telegram_suffix((pair or {}).get("intel"))

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
                    self.notify(self._with_intel(self.msg_structure(sym, tf, e, pair), pair), prio=1)
        if not first:
            for s in new_setups.get(config.SETUP_TF, []):
                if s["src"] == "live":
                    self.notify(self._with_intel(self.msg_setup(sym, s, pair), pair), prio=2)
            for e in new_entries:
                if e["src"] == "live":
                    photo = (os.path.join(self.store, "charts", f"{sym}_{config.SETUP_TF}.png")
                             if config.ALERT_WITH_CHART else None)
                    self.notify(self._with_intel(self.msg_entry(sym, e, pair), pair), photo, prio=3)
            for e in closed_now:
                if e["src"] == "live":
                    self.notify(self._with_intel(self.msg_result(sym, e, pair), pair), prio=2)
        else:
            self.p["init"][sym] = self.now
            ov = pair["overall"]
            js = pair["journal"]["entries"]
            self.notify(f"🤖 <b>Alert Bot aktif – {sym}</b>\nHarga: {messages.f(pair['price'], pair['digits'])}\n"
                        f"Strategi: {config.BIAS_TF} → {config.SETUP_TF} → {config.CONFIRM_TF}\n"
                        f"Sinyal saat ini: {messages.ICON[ov['signal']]}\n"
                        f"Riwayat (replay data tersimpan): {js['total']} entry · winrate {js['winrate'] if js['winrate'] is not None else '-'}%\n"
                        f"Ketik /help untuk command.")

    # ---------- ALERT ZONA: 🔵/🟠 AREA -> ⚡ SETUP -> ✅ CONFIRMED (1x per kejadian) ----------
    def zone_alerts(self, sym, pair, tfs):
        first = self.p["init"].get(sym) == self.now        # run pertama pair ini: tandai saja, jangan kirim
        stages = set(config.ZONE_ALERT_STAGES)
        for tf in config.ZONE_ALERT_TFS:
            an = tfs.get(tf) or {}
            if not an.get("enough") or not an.get("zones"):
                continue
            recent = an.get("last_closed_t", 0) - TF_SEC[tf]          # 2 candle tertutup terakhir
            picked = {}
            rank = {"AREA": 1, "INVALID": 1, "SETUP": 2, "CONFIRMED": 3}
            for z in an["zones"]:
                top_at = {}                                   # tahap tertinggi per candle untuk zona ini
                for ev in z.get("events", []):
                    if ev["stage"] in stages and rank.get(ev["stage"], 0) > rank.get(top_at.get(ev["t"]), 0):
                        top_at[ev["t"]] = ev["stage"]
                for ev in z.get("events", []):
                    if ev["stage"] not in stages or ev["t"] < recent:
                        continue
                    key = f"{sym}:ZN:{tf}:{z['id']}:{ev['stage']}:{ev['t']}"
                    if self.sent(key):
                        continue
                    self.mark(key)
                    if top_at.get(ev["t"]) != ev["stage"]:
                        continue                              # AREA+SETUP di candle sama -> kirim tahap tertinggi saja
                    # kejadian sama (arah+tahap+candle) dari zona yang overlap -> cukup 1 notif (skor tertinggi)
                    dk = (z["dir"], ev["stage"], ev["t"])
                    if dk not in picked or ev["score"] > picked[dk][1]["score"]:
                        picked[dk] = (z, ev)
            if first:
                continue
            for z, ev in sorted(picked.values(), key=lambda x: x[1]["t"]):
                photo = None
                if ev["stage"] == "CONFIRMED" and config.ALERT_WITH_CHART:
                    photo = os.path.join(self.store, "charts", f"{sym}_{tf}.png")
                prio = {"CONFIRMED": 3, "SETUP": 2}.get(ev["stage"], 1)
                self.notify(self._with_intel(self.msg_zone(sym, tf, z, ev, pair), pair), photo, prio=prio)

    def msg_zone(self, sym, tf, z, ev, pair):
        d, bull, st = pair["digits"], z["dir"] == "bull", ev["stage"]
        side = "BUY" if bull else "SELL"
        model = ev.get("model")
        mname = {"retest": "CHoCH Retest", "sweep": "Sweep Reversal"}.get(model)
        head = {"AREA": ("🔵" if bull else "🟠") + f" <b>POTENTIAL {side} AREA</b>",
                "SETUP": f"⚡ <b>{side} SETUP</b> – " + ("retest zona setelah CHoCH" if model == "retest" else "sweep terjadi"),
                "CONFIRMED": ("🟢" if bull else "🔴") + f" <b>{side} CONFIRMED</b>",
                "INVALID": f"❌ <b>{side} AREA INVALID</b>"}.get(st, st)
        status = {"AREA": "Harga masuk zona. Tunggu sweep liquidity atau retest setelah CHoCH – belum entry.",
                  "SETUP": ("Retest zona di karakter baru. Tunggu rejection: displacement / iBOS / CISD."
                            if model == "retest" else
                            "Sweep di zona. Tunggu displacement / iCHoCH / CISD untuk konfirmasi."),
                  "CONFIRMED": f"{side} terkonfirmasi di zona (bukan nasihat keuangan).",
                  "INVALID": "Candle close menembus zona – area batal."}.get(st, "")
        lines = [head, f"<b>{sym} • {tf}</b>"]
        if mname:
            lines.append(f"Model: <b>{mname}</b>")
        lines += ["",
                  f"Zona: {'Bull' if bull else 'Bear'} {messages.escape(z['kind'])} "
                  f"{messages.f(z['bottom'], d)} – {messages.f(z['top'], d)}",
                  f"Harga close: {messages.f(ev['price'], d)} · sekarang {messages.f(pair['price'], d)}",
                  f"Skor confluence: <b>{ev['score']}/{z.get('max_score', 10)}</b>", "", "<b>Alasan</b>"]
        for txt, pts in ev.get("why") or []:
            lines.append(f"✅ {messages.escape(txt)}" + (f" (+{pts})" if pts else ""))
        if st == "CONFIRMED" and ev.get("by"):
            lines.append(f"Konfirmasi: {messages.escape(' + '.join(ev['by']))}")
        lines += ["", f"🛑 Invalidation: close {'di bawah' if bull else 'di atas'} {messages.f(z['invalidation'], d)}"]
        sw = z.get("sweep")
        if sw and model == "sweep" and st in ("SETUP", "CONFIRMED"):
            lines.append(f"Ekstrem sweep: {messages.f(sw['price'], d)}")
        if model == "retest" and z.get("struct_invalid") is not None and z["struct_invalid"] != z["invalidation"]:
            lines.append(f"Struktur gagal bila close {'di bawah' if bull else 'di atas'} asal leg CHoCH "
                         f"{messages.f(z['struct_invalid'], d)}")
        lines += [f"Candle: {messages.wib(ev['t'], '%d/%m %H:%M')}", "", f"📊 {status}"]
        return "\n".join(lines)

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
        """v19: antrean Telegram persisten.
        - Pesan dianggap TERKIRIM hanya bila Telegram membalas ok=true (message_id disimpan).
        - Gagal -> status RETRYING, dicoba lagi di run berikutnya (maks TELEGRAM_RETRY kali &
          TELEGRAM_RETRY_MAX_AGE_MIN menit), setelah itu FAILED. Tidak ada status 'pending' palsu.
        - Status per pesan dicatat di tg_log (dashboard: panel Notifikasi Telegram)."""
        self.tg_sent = self.tg_fail = 0
        old = self.p.get("tg_queue") or []
        retry = [q for q in old if self.now - q.get("created", 0) <= config.TELEGRAM_RETRY_MAX_AGE_MIN * 60
                 and q.get("attempts", 0) < config.TELEGRAM_RETRY]
        for q in old:
            if q not in retry:
                self._tg_log(q, "FAILED", q.get("last_error") or "kedaluwarsa di antrean", [])
                self._tg_status(q, "FAILED")
        keep = []
        for q in sorted(retry + self.outbox, key=lambda x: (-x["prio"], x["created"])):
            ok, err = False, None
            photo = q.get("photo")
            if photo and os.path.exists(photo):
                ok, err = send_photo(photo, q["text"])
            if not ok:
                ok, err = send_message(q["text"])
            q["attempts"] = q.get("attempts", 0) + 1
            ids = [i for i in (telegram_notify.LAST.get("ids") or []) if i] if ok else []
            if ok:
                status = "SENT"
                self.tg_sent += 1
                self.p["tg_last_ok_ts"] = self.now
            else:
                status = "RETRYING" if q["attempts"] < config.TELEGRAM_RETRY else "FAILED"
                self.tg_fail += 1
                q["last_error"] = err
                self.p["tg_last_err"], self.p["tg_last_err_ts"] = err, self.now
                print("  TELEGRAM GAGAL:", err)
                if status == "RETRYING":
                    keep.append(q)
            self._tg_log(q, status, err, ids)
            self._tg_status(q, status)
        self.p["tg_queue"] = keep
        configured = telegram_notify.configured()
        self.tg_ok = configured and not self.tg_fail
        if not configured:
            self.warnings.append("Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tidak terbaca oleh engine "
                                 "(cek env di .github/workflows/engine.yml & GitHub Secrets).")
        elif self.tg_fail:
            self.warnings.append(f"Telegram: {self.tg_fail} pesan gagal ({self.p.get('tg_last_err')}). "
                                 f"{len(keep)} akan dicoba ulang di run berikutnya.")
        print(f"  Telegram: antrean {len(retry) + len(self.outbox)} (retry {len(retry)}) · terkirim {self.tg_sent} · "
              f"gagal {self.tg_fail} · menunggu retry {len(keep)}" + ("" if configured else " · ⚠️ TOKEN/CHAT ID KOSONG"))

    def _tg_log(self, q, status, err, ids):
        first = q["text"].split("\n")[0]
        title = " ".join(first.replace("<b>", "").replace("</b>", "").split())[:90]
        sec = q["text"].split("\n")[1] if "\n" in q["text"] else ""
        sec = " ".join(sec.replace("<b>", "").replace("</b>", "").split())[:120]
        self.p["tg_log"].append({"ts": self.now, "status": status, "key": q.get("key"), "sym": q.get("sym"),
                                 "title": title, "detail": sec, "attempts": q.get("attempts", 0),
                                 "message_ids": ids, "error": (err or None) if status != "SENT" else None,
                                 "created": q.get("created")})
        self.p["tg_log"] = self.p["tg_log"][-80:]

    def _tg_status(self, q, status):
        mon = self.monitors.get(q.get("sym"))
        if mon and q.get("key"):
            mon.set_notif(q["key"], status)

    def safe_analyze(self, sym, stores, got, extra):
        """v19: error di satu pair tidak boleh membuat seluruh run gagal (state & data lain tetap tersimpan)."""
        t0 = time.time()
        try:
            return self.analyze(sym, stores, got, extra)
        except Exception as e:      # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.errors[sym] = f"analisis gagal: {e.__class__.__name__}: {e}"
            return None
        finally:
            self.timing[sym] = round(time.time() - t0, 1)

    # ---------- run ----------
    def run(self):
        pairs = {}
        self.timing = {}
        if self.p.get("mode_announced") != config.SIGNAL_MODE and self.p.get("init"):
            self.notify("🛠 <b>Engine v19 aktif</b>\n" + (
                "Mode: <b>MONITORING</b> – sinyal BUY/SELL dinonaktifkan sementara.\n"
                f"Notifikasi kejadian {config.MONITOR_TF} ({', '.join(config.MONITOR_SYMBOLS)}): "
                f"{', '.join(config.MONITOR_NOTIFY)}.\nAuto Fibonacci: {config.FIB_PRIMARY_TF} utama + Fib lokal per TF."
                if not config.SIGNALS_ON else "Mode: <b>SIGNAL</b> – sinyal BUY/SELL aktif."), prio=5)
        self.p["mode_announced"] = config.SIGNAL_MODE
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
            pr = self.safe_analyze(sym, stores, got, {})
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
            pr = self.safe_analyze(sym, stores, got, self.crypto_extra(sym, stores, got))
            if pr:
                pairs[sym] = pr
        if self.bn.host:
            self.p["binance_host"] = self.bn.host
        self.flush()
        for sym, mon in self.monitors.items():
            try:
                mon.save()
                jsave(os.path.join(self.site, "data", f"monitor_{sym}.json"), mon.public())
                if sym in pairs:
                    pairs[sym]["monitor"] = mon.summary()
            except Exception as e:      # noqa: BLE001
                self.warnings.append(f"Monitor {sym} gagal disimpan: {e}")

        src_dir = os.path.join(self.store, "charts")
        if os.path.isdir(src_dir):
            shutil.copytree(src_dir, os.path.join(self.site, "charts"), dirs_exist_ok=True)

        last_fetch = self.p.get("forex_fetch_ts") or self.now
        bf = self.p.get("backfill", {})
        dur = round(time.time() - self.t_start, 1)
        trig = os.getenv("GITHUB_EVENT_NAME") or "local"
        runs = self.p["runs"]
        gaps = [round((runs[i][0] - runs[i - 1][0]) / 60, 1) for i in range(max(1, len(runs) - 23), len(runs))]
        gap_now = round((self.now - runs[-1][0]) / 60, 1) if runs else None
        runs.append([self.now, trig, dur])
        self.p["runs"] = runs[-60:]
        by_trig = {}
        for r in self.p["runs"]:
            by_trig[r[1]] = by_trig.get(r[1], 0) + 1
        if gap_now is not None and gap_now > 20:
            self.warnings.append(f"Engine sempat tidak berjalan ±{gap_now:.0f} menit sebelum run ini "
                                 f"(trigger sekarang: {trig}). Cek scheduler / watchdog Cloudflare.")
        st = {
            "version": 21, "mode": config.SIGNAL_MODE, "updated_ts": self.now, "telegram_ok": self.tg_ok,
            "intel": {"enabled": config.INTEL_ENABLED, "mode": config.INTEL_MODE,
                      "blocking": False, "version": 1,
                      "description": "Live decision-support context; never gates legacy signals."},
            "telegram": {"sent": getattr(self, "tg_sent", 0), "failed": getattr(self, "tg_fail", 0),
                         "configured": telegram_notify.configured(),
                         "pending_retry": len(self.p.get("tg_queue") or []),
                         "last_sent_ts": self.p.get("tg_last_ok_ts") or self.p.get("tg_last_sent_ts"),
                         "last_error": self.p.get("tg_last_err"), "last_error_ts": self.p.get("tg_last_err_ts"),
                         "log": self.p["tg_log"][-40:],
                         "notify_types": config.MONITOR_NOTIFY if not config.SIGNALS_ON else None,
                         "zone_tfs": config.ZONE_ALERT_TFS if config.SIGNALS_ON else [],
                         "zone_stages": config.ZONE_ALERT_STAGES if config.SIGNALS_ON else []},
            "run": {"trigger": trig, "duration_sec": dur, "gap_min": gap_now, "recent_gaps_min": gaps,
                    "by_trigger": by_trig, "timing": self.timing,
                    "avg_gap_min": round(sum(gaps) / len(gaps), 1) if gaps else None},
            "fib": {"primary_tf": config.FIB_PRIMARY_TF, "levels": [0, 23.6, 38.2, 50, 61.8, 78.6, 100]},
            "monitor": {"tf": config.MONITOR_TF, "symbols": config.MONITOR_SYMBOLS, "notify": config.MONITOR_NOTIFY,
                        "confluence_min": config.MONITOR_CONFLUENCE_MIN,
                        "htf_poi": {"enabled": config.HTF_POI_ENABLED, "tf": config.HTF_POI_TF,
                                    "kinds": config.HTF_POI_KINDS, "stages": config.HTF_POI_STAGES}},
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
        print(f"Durasi run {dur}s (trigger {trig}, jeda dari run sebelumnya {gap_now} mnt) · per pair {self.timing}")
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
