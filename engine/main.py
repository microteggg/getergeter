"""Engine utama (dijalankan GitHub Actions tiap ±10 menit).

FOREX  : RealMarketAPI M5, diambil hanya bila sudah ±30 menit sejak fetch terakhir (hemat kuota)
         -> M15 / H1 / H4 / D / W
CRYPTO : Binance Spot M1 tiap run -> M5 / M15 / H1 / H4 (+ D / W native)
         + order book (depth) + transaksi (aggTrades) + statistik 24 jam
Hasil  : state.json + data chart (dashboard) + PNG (Telegram) + alert Telegram

python main.py --store ../store --site ../_site
"""
import argparse
import json
import os
import shutil
import time
from datetime import datetime, timezone

import config
import messages
from analysis import analyze_tf, overall
from binance import Binance
from charts import render
from data_source import fetch_candles
from orderflow import book_metrics, trade_metrics
from resample import TF_SEC, closed_only, resample, upsert
from telegram_notify import send_message, send_photo

RENDER = os.getenv("RENDER_CHARTS", "true").lower() != "false"


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


def forex_closed(now):
    dt = datetime.fromtimestamp(now, timezone.utc)
    wd, h = dt.weekday(), dt.hour
    return wd == 5 or (wd == 4 and h >= 22) or (wd == 6 and h < 21)


def rnd(b, d):
    x = {"t": b["t"], "o": round(b["o"], d), "h": round(b["h"], d), "l": round(b["l"], d), "c": round(b["c"], d)}
    if "v" in b:
        x["v"] = round(b["v"], 6)
        x["bv"] = round(b.get("bv", 0), 6)
        x["tc"] = b.get("tc", 0)
    return x


class Engine:
    def __init__(self, store_dir, site_dir, now=None, binance=None):
        self.store, self.site = store_dir, site_dir
        self.now = int(now or time.time())
        self.p = jload(os.path.join(store_dir, "private_state.json"), {})
        for k, v in (("sent", []), ("init", {}), ("bootstrap", {}), ("req", {}), ("fail", {}), ("book_hist", {})):
            self.p.setdefault(k, v)
        self.bn = binance or Binance(self.p.get("binance_host"))
        self.tg_ok = True
        self.outbox = []
        self.warnings, self.errors = [], {}
        self.rendered = []

    # ---------- util ----------
    def month(self):
        return datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m")

    def req_month(self):
        return self.p["req"].get(self.month(), 0)

    def cpath(self, sym, tf):
        return os.path.join(self.store, "candles", f"{sym}_{tf}.json")

    def load(self, sym, tfs):
        return {tf: jload(self.cpath(sym, tf), []) for tf in tfs}

    def save(self, sym, stores):
        for tf, bars in stores.items():
            jsave(self.cpath(sym, tf), bars[-config.STORE_LIMIT[tf]:])

    def sent(self, key):
        return key in self.p["sent"]

    def mark(self, key):
        self.p["sent"].append(key)
        self.p["sent"] = self.p["sent"][-800:]

    def notify(self, text, photo=None):
        self.outbox.append((text, photo))

    def fail(self, sym, err):
        self.errors[sym] = err
        self.p["fail"][sym] = self.p["fail"].get(sym, 0) + 1
        if self.p["fail"][sym] == 3:
            self.notify(f"⚠️ <b>{sym}</b>: pengambilan data gagal 3x berturut-turut.\n{messages.escape(err)}")

    def recover(self, sym, src):
        if self.p["fail"].get(sym, 0) >= 3:
            self.notify(f"✅ <b>{sym}</b>: pengambilan data {src} pulih kembali.")
        self.p["fail"][sym] = 0

    # ---------- FOREX ----------
    def forex_fetch(self, sym, stores):
        if not config.REALMARKET_API_KEY:
            self.fail(sym, "REALMARKET_API_KEY belum diisi")
            return 0

        def get(tf):
            self.p["req"] = {self.month(): self.req_month() + 1}
            return fetch_candles(sym, tf)

        b = self.p["bootstrap"].get(sym, 0)
        if config.BOOTSTRAP_H1 and b < 2 and len(stores["H1"]) < 100:
            rows, _ = get("H1")
            self.p["bootstrap"][sym] = 9 if rows else b + 1
            if rows:
                for r in rows:
                    r["n"] = 12
                stores["H1"] = upsert(stores["H1"], rows, rows[0]["t"])
                for tf in ("H4", "D", "W"):
                    stores[tf] = upsert(stores[tf], resample(rows, tf, config.FOREX_DAY_START_UTC), rows[0]["t"])
        rows, err = get(config.FOREX_SOURCE_TF)
        if err:
            self.fail(sym, err)
            return 0
        self.recover(sym, "RealMarketAPI")
        m5 = stores["M5"]
        if m5:
            gap = rows[0]["t"] - m5[-1]["t"]
            if 300 < gap < 40 * 3600:
                self.warnings.append(f"{sym}: celah data M5 ±{gap // 60} menit sebelum {messages.wib(rows[0]['t'], '%d/%m %H:%M')}")
        if len(rows) < 7:
            self.warnings.append(f"{sym}: API hanya mengirim {len(rows)} candle M5 – kurang untuk interval {config.FOREX_INTERVAL_MIN} menit")
        stores["M5"] = upsert(m5, rows, rows[0]["t"])[-config.STORE_LIMIT["M5"]:]
        first = stores["M5"][0]["t"]
        for tf in ("M15", "H1", "H4", "D", "W"):
            stores[tf] = upsert(stores[tf], resample(stores["M5"], tf, config.FOREX_DAY_START_UTC), first)
        return len(rows)

    # ---------- CRYPTO ----------
    def crypto_fetch(self, sym, stores):
        bn = self.bn
        if not self.p["bootstrap"].get(sym):
            ok = True
            for tf, lim in (("H1", 1000), ("H4", 1000), ("D", 1000), ("W", 500)):
                rows, err = bn.klines(sym, tf, lim)
                if err:
                    ok = False
                    break
                stores[tf] = upsert(stores[tf], rows, rows[0]["t"] if rows else None)
            if ok:
                self.p["bootstrap"][sym] = self.now
        # M1: ambil 1000 terakhir; bila tertinggal jauh, kejar dengan startTime (maks 4 halaman)
        m1 = stores["M1"]
        got = []
        if m1 and self.now - m1[-1]["t"] > 950 * 60:
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
            stores[tf] = upsert(stores[tf], resample(stores["M1"], tf, 0), first)
        for tf in ("H4", "D", "W"):                       # candle besar native agar volume persis
            rows, err = bn.klines(sym, tf, 3)
            if rows:
                stores[tf] = upsert(stores[tf], rows, rows[0]["t"])
        return len(got)

    # ---------- analisis + output satu pair ----------
    def analyze(self, sym, market, stores, received, extra):
        tfs_list = config.CRYPTO_TFS if market == "crypto" else config.FOREX_TFS
        src = stores["M1" if market == "crypto" else "M5"] or stores["H1"]
        if not src:
            return None
        price = src[-1]["c"]
        d = config.digits(sym, price)
        tfs, series = {}, {}
        for tf in tfs_list:
            an = analyze_tf(tf, closed_only(stores[tf], tf, self.now), price, d)
            series[tf] = an.pop("series", {})
            tfs[tf] = an
        ov = overall(tfs, extra.get("book"), extra.get("trades")) if market == "crypto" else overall(tfs)
        do_render = RENDER and (market == "crypto" or received)
        for tf in tfs_list:
            bars = []
            for b in stores[tf][-config.CHART_BARS[tf]:]:
                x = rnd(b, d)
                if b["t"] + TF_SEC[tf] > self.now:
                    x["forming"] = True
                bars.append(x)
            ema = {k: [[t, round(v, d)] for t, v in zip(series[tf].get("times", []), series[tf].get(k, []))
                       if v is not None][-config.CHART_BARS[tf]:] for k in ("ema20", "ema50", "ema200")}
            a = tfs[tf]
            jsave(os.path.join(self.site, "data", f"chart_{sym}_{tf}.json"),
                  {"symbol": sym, "market": market, "tf": tf, "digits": d, "candles": bars, **ema,
                   "events": a.get("events", []), "sweeps": a.get("sweeps", []), "fvg": a.get("fvg", []), "ob": a.get("ob", []),
                   "fvg_mit": a.get("fvg_mit", []), "ob_mit": a.get("ob_mit", []), "setup": a.get("setup"), "signal": a.get("signal")})
            if do_render:
                try:
                    out = os.path.join(self.store, "charts", f"{sym}_{tf}.png")
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    render(out, sym, tf, bars, dict(a, series=series[tf]), d)
                except Exception as e:
                    self.warnings.append(f"Chart {sym} {tf} gagal dibuat: {e}")
        pair = {"market": market, "digits": d, "price": round(price, d), "last_ts": src[-1]["t"], "received": received,
                "counts": {tf: len(stores[tf]) for tf in tfs_list}, "tf": tfs, "overall": ov, **extra}
        self.alerts(sym, pair)
        return pair

    def alerts(self, sym, pair):
        d = pair["digits"]
        ov = pair["overall"]
        first = not self.p["init"].get(sym)
        ev_keys = []
        for tf in config.ALERT_EVENT_TFS:
            for e in pair["tf"].get(tf, {}).get("events", []):
                if e["t"] >= self.now - 4 * TF_SEC[tf]:
                    ev_keys.append((f"{sym}:{tf}:{e['type']}:{e['dir']}:{e['t']}", tf, e))
        sig_key = None
        if ov["signal"] in ("BUY", "SELL") and ov.get("setup"):
            z = ov["setup"]["zone"]
            sig_key = f"{sym}:SIG:{ov['signal']}:{z['kind']}:{z['t']}"
        whales = []
        if config.ALERT_WHALE and pair.get("trades"):
            whales = [(f"{sym}:W:{x['ms']}", x) for x in pair["trades"]["large"][:5]]
        if first:
            for k, _, _ in ev_keys:
                self.mark(k)
            for k, _ in whales:
                self.mark(k)
            if sig_key:
                self.mark(sig_key)
            self.p["init"][sym] = self.now
            src = "Binance M1 + order book" if pair["market"] == "crypto" else f"RealMarketAPI M5 tiap {config.FOREX_INTERVAL_MIN} menit"
            self.notify(f"🤖 <b>Alert Bot aktif – {sym}</b>\nSumber: {src}\nHarga: {messages.f(pair['price'], d)}\n"
                        f"Sinyal saat ini: {messages.ICON[ov['signal']]}\n<i>{messages.escape(ov['summary'])}</i>\nKetik /help untuk command.")
            return
        for k, tf, e in ev_keys:
            if self.sent(k):
                continue
            self.mark(k)
            self.notify(f"🔔 <b>{sym} {tf} – {e['type']} {'Bullish 🟢' if e['dir'] == 'bull' else 'Bearish 🔴'}</b>\n"
                        f"Level: {messages.f(e['level'], d)}\nCandle: {messages.wib(e['t'], '%d/%m %H:%M')}\n"
                        f"Harga: {messages.f(pair['price'], d)}\nTrend {tf}: {messages.short(pair['tf'][tf].get('trend'))}\n"
                        f"Sinyal saat ini: {messages.ICON[ov['signal']]}")
        new_whales = [(k, x) for k, x in whales if not self.sent(k)]
        for k, _ in new_whales:
            self.mark(k)
        if new_whales:
            self.notify(f"🐋 <b>{sym} transaksi besar</b>\n" + "\n".join(
                f"{'🟢 BUY' if x['side'] == 'BUY' else '🔴 SELL'} {x['q']:.4f} @ {messages.f(x['p'], d)} ({messages.usd(x['usd'])}) {messages.wib(x['t'], '%H:%M')}"
                for _, x in new_whales))
        if sig_key and not self.sent(sig_key):
            self.mark(sig_key)
            if config.ALERT_SIGNAL:
                s = ov["setup"]
                txt = (f"🚨 <b>{sym} {ov['signal']}</b> ({config.SETUP_TF})\nEntry: {messages.f(s['entry'], d)}\n"
                       f"SL: {messages.f(s['sl'], d)}\nTP1: {messages.f(s['tp1'], d)} | TP2: {messages.f(s['tp2'], d)}\n"
                       f"Zona: {s['zone']['kind']} {messages.f(s['zone']['bottom'], d)}–{messages.f(s['zone']['top'], d)}\n\n"
                       + "\n".join(f"{messages.mark(ok)} {messages.escape(t)}" for ok, t in ov["reasons"]))
                photo = os.path.join(self.store, "charts", f"{sym}_{config.SETUP_TF}.png") if config.ALERT_WITH_CHART else None
                self.notify(txt, photo)

    def flush(self):
        for text, photo in self.outbox:
            ok = False
            if photo and os.path.exists(photo):
                ok, _ = send_photo(photo, text)
            if not ok:
                ok, _ = send_message(text)
            self.tg_ok = self.tg_ok and ok

    # ---------- run ----------
    def run(self):
        pairs = {}
        # FOREX
        fx_closed = config.SKIP_WEEKEND and forex_closed(self.now)
        last_fx = self.p.get("forex_fetch_ts") or 0
        fx_due = not fx_closed and self.now - last_fx >= (config.FOREX_INTERVAL_MIN - 4) * 60
        fx_ok = self.p.get("forex_ok", True)
        if fx_due and config.FOREX_SYMBOLS:
            self.p["forex_fetch_ts"] = self.now
        for sym in config.FOREX_SYMBOLS:
            stores = self.load(sym, config.FOREX_TFS)
            got = self.forex_fetch(sym, stores) if fx_due else 0
            if fx_due:
                fx_ok = fx_ok and sym not in self.errors
            self.save(sym, stores)
            pr = self.analyze(sym, "forex", stores, got, {})
            if pr:
                pairs[sym] = pr
        if fx_due:
            self.p["forex_ok"] = all(s not in self.errors for s in config.FOREX_SYMBOLS)
        # CRYPTO
        cr_ok = True
        for sym in config.CRYPTO_SYMBOLS:
            stores = self.load(sym, config.CRYPTO_TFS)
            got = self.crypto_fetch(sym, stores)
            cr_ok = cr_ok and sym not in self.errors
            self.save(sym, stores)
            extra = {}
            price = stores["M1"][-1]["c"] if stores["M1"] else None
            if got and price:
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
            pr = self.analyze(sym, "crypto", stores, got, extra)
            if pr:
                pairs[sym] = pr
        if self.bn.host:
            self.p["binance_host"] = self.bn.host
        self.flush()

        # copy chart PNG tersimpan ke site
        src_dir = os.path.join(self.store, "charts")
        if os.path.isdir(src_dir):
            shutil.copytree(src_dir, os.path.join(self.site, "charts"), dirs_exist_ok=True)

        nxt_fx = (self.p.get("forex_fetch_ts") or self.now) + config.FOREX_INTERVAL_MIN * 60
        st = {
            "version": 3, "updated_ts": self.now, "telegram_ok": self.tg_ok,
            "errors": self.errors, "warnings": self.warnings,
            "strategy": {"bias": config.BIAS_TF, "setup": config.SETUP_TF, "confirm": config.CONFIRM_TF},
            "sources": {
                "forex": {"symbols": config.FOREX_SYMBOLS, "ok": self.p.get("forex_ok", True), "market_closed": fx_closed,
                          "fetch_ts": self.p.get("forex_fetch_ts"), "next_ts": nxt_fx, "interval_min": config.FOREX_INTERVAL_MIN,
                          "req_month": self.req_month(), "quota": config.MONTHLY_QUOTA, "fetched_now": fx_due},
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
        print(f"Selesai {messages.wib(self.now)} | forex fetch={fx_due} tutup={fx_closed} | kuota RealMarketAPI={self.req_month()} "
              f"| Binance host={self.bn.host} panggilan={self.bn.calls} weight={self.bn.weight}")
        for sym, pr in pairs.items():
            print(f"  {sym}: {pr['price']} | diterima {pr['received']} | sinyal {pr['overall']['signal']} | {pr['counts']}")
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
