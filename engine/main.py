"""Engine utama: 1x jalan = ambil M5 (1 request/pair) -> bentuk M15/H1/H4/D/W -> analisis
-> simpan hasil (untuk dashboard & command Telegram) -> kirim alert bila ada kejadian baru.

python main.py --store ../store --site ../_site
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

import config
import messages
from analysis import analyze_tf, overall
from charts import render
from data_source import fetch_candles
from resample import TF_SEC, closed_only, resample, upsert
from telegram_notify import send_message, send_photo

HIGHER = ["M15", "H1", "H4", "D", "W"]
RENDER = os.getenv("RENDER_CHARTS", "true").lower() != "false"


# ---------------- util file ----------------
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


def market_closed(now):
    dt = datetime.fromtimestamp(now, timezone.utc)
    wd, h = dt.weekday(), dt.hour
    return wd == 5 or (wd == 4 and h >= 22) or (wd == 6 and h < 21)


# ---------------- engine ----------------
class Engine:
    def __init__(self, store_dir, site_dir, now=None):
        self.store, self.site = store_dir, site_dir
        self.now = int(now or time.time())
        self.p = jload(os.path.join(store_dir, "private_state.json"), {})
        self.p.setdefault("sent", [])
        self.p.setdefault("init", {})
        self.p.setdefault("bootstrap", {})
        self.p.setdefault("req", {})
        self.p.setdefault("fail", {})
        self.tg_ok = True
        self.outbox = []

    # --- kuota ---
    def _count_req(self):
        key = datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m")
        self.p["req"] = {key: self.p["req"].get(key, 0) + 1}

    def req_month(self):
        key = datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m")
        return self.p["req"].get(key, 0)

    def fetch(self, sym, tf):
        if not config.REALMARKET_API_KEY:
            return [], "REALMARKET_API_KEY belum diisi"
        self._count_req()
        return fetch_candles(sym, tf)

    # --- penyimpanan candle ---
    def cpath(self, sym, tf):
        return os.path.join(self.store, "candles", f"{sym}_{tf}.json")

    def load(self, sym, tf):
        return jload(self.cpath(sym, tf), [])

    def save(self, sym, tf, bars):
        jsave(self.cpath(sym, tf), bars[-config.STORE_LIMIT[tf]:])

    # --- alert ---
    def sent(self, key):
        return key in self.p["sent"]

    def mark(self, key):
        self.p["sent"].append(key)
        self.p["sent"] = self.p["sent"][-600:]

    def notify(self, text, photo=None):
        self.outbox.append((text, photo))

    def flush(self):
        for text, photo in self.outbox:
            ok, _ = send_photo(photo, text) if photo and os.path.exists(photo) else (False, None)
            if not ok:
                ok, _ = send_message(text)
            self.tg_ok = self.tg_ok and ok

    # --- proses satu pair ---
    def process(self, sym, closed_mkt, warnings, errors):
        d = config.digits(sym)
        stores = {tf: self.load(sym, tf) for tf in config.TIMEFRAMES}
        received = 0
        fetched = False
        if not closed_mkt:
            # Bootstrap riwayat H1 sekali saja (opsional, 1 request)
            b = self.p["bootstrap"].get(sym, 0)
            if config.BOOTSTRAP_H1 and b < 2 and len(stores["H1"]) < 100:
                rows, err = self.fetch(sym, "H1")
                self.p["bootstrap"][sym] = 9 if rows else b + 1
                if rows:
                    for r in rows:
                        r["n"] = 12
                    stores["H1"] = upsert(stores["H1"], rows, covered_from=rows[0]["t"])
                    for tf in ("H4", "D", "W"):
                        stores[tf] = upsert(stores[tf], resample(rows, tf), covered_from=rows[0]["t"])
            rows, err = self.fetch(sym, config.SOURCE_TF)
            fetched = True
            if err:
                errors[sym] = err
                self.p["fail"][sym] = self.p["fail"].get(sym, 0) + 1
                if self.p["fail"][sym] == 3:
                    self.notify(f"⚠️ <b>{sym}</b>: pengambilan data gagal 3x berturut-turut.\n{messages.escape(err)}")
            else:
                if self.p["fail"].get(sym, 0) >= 3:
                    self.notify(f"✅ <b>{sym}</b>: pengambilan data RealMarketAPI pulih kembali.")
                self.p["fail"][sym] = 0
                received = len(rows)
                m5 = stores["M5"]
                if m5:
                    gap = rows[0]["t"] - m5[-1]["t"]
                    if 300 < gap < 40 * 3600:
                        warnings.append(f"{sym}: celah data M5 ±{gap // 60} menit sebelum {messages.wib(rows[0]['t'], '%d/%m %H:%M')}")
                if received < 7:
                    warnings.append(f"{sym}: API hanya mengirim {received} candle M5 per request – kurang untuk interval {config.FETCH_INTERVAL_MIN} menit")
                stores["M5"] = upsert(m5, rows, covered_from=rows[0]["t"])
                stores["M5"] = stores["M5"][-config.STORE_LIMIT["M5"]:]
                first = stores["M5"][0]["t"]
                for tf in HIGHER:
                    stores[tf] = upsert(stores[tf], resample(stores["M5"], tf), covered_from=first)
        for tf in config.TIMEFRAMES:
            self.save(sym, tf, stores[tf])
        if not stores["M5"] and not stores["H1"]:
            return None
        src = stores["M5"] or stores["H1"]
        price = src[-1]["c"]
        tfs, series = {}, {}
        for tf in config.TIMEFRAMES:
            an = analyze_tf(tf, closed_only(stores[tf], tf, self.now), price, sym)
            series[tf] = an.pop("series", {})
            tfs[tf] = an
        ov = overall(tfs)

        # ---- output dashboard + chart ----
        os.makedirs(os.path.join(self.site, "charts"), exist_ok=True)
        for tf in config.TIMEFRAMES:
            bars = [dict(b) for b in stores[tf][-config.CHART_BARS[tf]:]]
            for b in bars:
                if b["t"] + TF_SEC[tf] > self.now:
                    b["forming"] = True
            an = dict(tfs[tf], series=series[tf])
            ser_map = {}
            for k in ("ema20", "ema50"):
                ser_map[k] = [[t, round(v, d)] for t, v in zip(series[tf].get("times", []), series[tf].get(k, [])) if v is not None][-config.CHART_BARS[tf]:]
            jsave(os.path.join(self.site, "data", f"chart_{sym}_{tf}.json"),
                  {"symbol": sym, "tf": tf, "digits": d, "candles": bars, **ser_map,
                   "events": tfs[tf].get("events", []), "fvg": tfs[tf].get("fvg", []), "ob": tfs[tf].get("ob", []),
                   "setup": tfs[tf].get("setup"), "signal": tfs[tf].get("signal")})
            if not RENDER:
                continue
            try:
                render(os.path.join(self.site, "charts", f"{sym}_{tf}.png"), sym, tf, bars, an, d)
            except Exception as e:  # chart gagal tidak boleh menghentikan engine
                warnings.append(f"Chart {sym} {tf} gagal dibuat: {e}")

        pair = {"price": round(price, d), "last_m5": src[-1]["t"], "received": received, "fetched": fetched,
                "counts": {tf: len(stores[tf]) for tf in config.TIMEFRAMES}, "tf": tfs, "overall": ov}
        self.alerts(sym, pair)
        return pair

    def alerts(self, sym, pair):
        d = config.digits(sym)
        ov = pair["overall"]
        first_run = not self.p["init"].get(sym)
        ev_keys = []
        for tf in config.ALERT_EVENT_TFS:
            for e in pair["tf"].get(tf, {}).get("events", []):
                if e["t"] >= self.now - 4 * TF_SEC[tf]:
                    ev_keys.append((f"{sym}:{tf}:{e['type']}:{e['dir']}:{e['t']}", tf, e))
        sig_key = None
        if ov["signal"] in ("BUY", "SELL") and ov.get("setup"):
            z = ov["setup"]["zone"]
            sig_key = f"{sym}:SIG:{ov['signal']}:{z['kind']}:{z['t']}"
        if first_run:
            for k, _, _ in ev_keys:
                self.mark(k)
            if sig_key:
                self.mark(sig_key)
            self.p["init"][sym] = self.now
            self.notify(f"🤖 <b>Alert Bot v2 aktif – {sym}</b>\nSumber: M5 tiap {config.FETCH_INTERVAL_MIN} menit → "
                        f"M15/H1/H4/D/W\nHarga: {messages.f(pair['price'], d)}\nSinyal saat ini: {messages.ICON[ov['signal']]}\n"
                        f"<i>{messages.escape(ov['summary'])}</i>\nKetik /help untuk daftar command.")
            return
        for k, tf, e in ev_keys:
            if self.sent(k):
                continue
            self.mark(k)
            arah = "Bullish 🟢" if e["dir"] == "bull" else "Bearish 🔴"
            self.notify(f"🔔 <b>{sym} {tf} – {e['type']} {arah}</b>\nLevel: {messages.f(e['level'], d)}\n"
                        f"Candle: {messages.wib(e['t'], '%d/%m %H:%M')}\nHarga: {messages.f(pair['price'], d)}\n"
                        f"Trend {tf}: {pair['tf'][tf].get('trend')}\nSinyal saat ini: {messages.ICON[ov['signal']]}")
        if sig_key and not self.sent(sig_key):
            self.mark(sig_key)
            if config.ALERT_SIGNAL:
                s = ov["setup"]
                txt = (f"🚨 <b>{sym} {ov['signal']}</b> ({config.SETUP_TF})\nEntry: {messages.f(s['entry'], d)}\n"
                       f"SL: {messages.f(s['sl'], d)}\nTP1: {messages.f(s['tp1'], d)} | TP2: {messages.f(s['tp2'], d)}\n"
                       f"Zona: {s['zone']['kind']} {messages.f(s['zone']['bottom'], d)}–{messages.f(s['zone']['top'], d)}\n\n"
                       + "\n".join(f"{'✅' if ok else '➖' if ok is None else '❌'} {messages.escape(t)}" for ok, t in ov["reasons"]))
                photo = os.path.join(self.site, "charts", f"{sym}_{config.SETUP_TF}.png") if config.ALERT_WITH_CHART else None
                self.notify(txt, photo)

    def run(self):
        closed_mkt = config.SKIP_WEEKEND and market_closed(self.now)
        warnings, errors, pairs = [], {}, {}
        for sym in config.SYMBOLS:
            pr = self.process(sym, closed_mkt, warnings, errors)
            if pr:
                pairs[sym] = pr
        self.flush()
        prev = jload(os.path.join(self.store, "public_state.json"), {})
        st = {
            "version": 2, "updated_ts": self.now,
            "fetch_ts": self.now if not closed_mkt else prev.get("fetch_ts"),
            "next_ts": self.now + config.FETCH_INTERVAL_MIN * 60,
            "interval_min": config.FETCH_INTERVAL_MIN, "market_closed": closed_mkt,
            "api_ok": (not errors) if not closed_mkt else prev.get("api_ok", True),
            "telegram_ok": self.tg_ok, "errors": errors, "warnings": warnings,
            "req_month": self.req_month(), "quota": config.MONTHLY_QUOTA,
            "symbols": config.SYMBOLS, "timeframes": config.TIMEFRAMES,
            "strategy": {"bias": config.BIAS_TF, "setup": config.SETUP_TF, "confirm": config.CONFIRM_TF},
            "dashboard_url": config.DASHBOARD_URL, "pairs": pairs,
        }
        st["messages"] = messages.build_all(st)
        jsave(os.path.join(self.site, "data", "state.json"), st)
        jsave(os.path.join(self.store, "public_state.json"), {k: st[k] for k in ("fetch_ts", "api_ok")})
        jsave(os.path.join(self.store, "private_state.json"), self.p)
        print(f"Selesai {messages.wib(self.now)} | pasar tutup={closed_mkt} | request bulan ini={self.req_month()}")
        for sym, pr in pairs.items():
            print(f"  {sym}: harga {pr['price']} | diterima {pr['received']} M5 | sinyal {pr['overall']['signal']} | candle {pr['counts']}")
        for w in warnings:
            print("  WARNING:", w)
        for s, e in errors.items():
            print("  ERROR:", s, e)
        return st


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="../store")
    ap.add_argument("--site", default="../_site")
    a = ap.parse_args()
    Engine(a.store, a.site).run()
