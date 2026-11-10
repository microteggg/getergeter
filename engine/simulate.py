"""Simulasi offline: RealMarketAPI (XAUUSD), Binance (crypto) dan Telegram di-mock.

python simulate.py [folder]     SIM_DAYS=1  SIM_CRYPTO=BTCUSDT,ETHUSDT  (default 2 crypto agar cepat)
Juga mencetak proyeksi pemakaian kuota RealMarketAPI satu bulan penuh.
"""
import contextlib
import io
import math
import os
import random
import shutil
import sys

import config

config.CRYPTO_SYMBOLS = [s for s in os.getenv("SIM_CRYPTO", "BTCUSDT,ETHUSDT").split(",") if s]
import main      # noqa: E402
import session   # noqa: E402
from resample import resample   # noqa: E402

random.seed(11)
START = 1793577600 + 2 * 3600            # Senin 2026-11-02 02:00 UTC
NOW = [0]


def walk(p0, pct, n, t0, step_sec, vol_scale=1.0):
    bars, p = [], p0
    vol = p0 * pct
    for i in range(n):
        t = t0 + i * step_sec
        o = p
        c = o + random.gauss(math.sin(i / (2000 * 60 / step_sec)) * vol * 0.3, vol)
        h, l = max(o, c) + abs(random.gauss(0, vol / 2)), min(o, c) - abs(random.gauss(0, vol / 2))
        v = abs(random.gauss(50, 20)) * vol_scale
        bv = v * min(0.95, max(0.05, 0.5 + (c - o) / (h - l + 1e-12) * 0.3))
        bars.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "bv": bv, "qv": v * c, "tc": int(v * 10)})
        p = c
    return bars


FX = {"XAUUSD": [{k: b[k] for k in "tohlc"} for b in walk(3950.0, 0.0009, 288 * 40, START - 288 * 30 * 300, 300)
                 if not session.market_closed(b["t"])]}
CR = {s: walk(p0, 0.0004, 1440 * 40, START - 1440 * 35 * 60, 60) for s, p0 in
      (("BTCUSDT", 75000.0), ("ETHUSDT", 2100.0), ("BNBUSDT", 690.0), ("XRPUSDT", 1.09), ("SOLUSDT", 86.0),
       ("TRXUSDT", 0.317), ("DOGEUSDT", 0.0697), ("ZECUSDT", 1150.0), ("LINKUSDT", 10.6), ("ADAUSDT", 0.177))}
CALLS = {"forex": 0}


def fake_fx(sym, tf):
    CALLS["forex"] += 1
    src = [b for b in FX[sym] if b["t"] + 300 <= NOW[0] + 300]
    if tf == "H1":
        return [dict(b) for b in resample(src, "H1", "forex")][-299:], None
    return [dict(b) for b in src[-288:]], None


class FakeBinance:
    def __init__(self, *_):
        self.host, self.calls, self.weight = "https://data-api.binance.vision", 0, 0

    def klines(self, sym, tf, limit=1000, start_ms=None):
        self.calls += 1
        src = [b for b in CR[sym] if b["t"] <= NOW[0]]
        if start_ms:
            src = [b for b in src if b["t"] >= start_ms // 1000]
            rows = src[:limit] if tf == "M1" else resample(src, tf, "crypto")[:limit]
        else:
            rows = src[-limit:] if tf == "M1" else resample(src, tf, "crypto")[-limit:]
        return [{k: v for k, v in b.items() if k != "n"} for b in rows], None

    def depth(self, sym, limit=100):
        p = [b for b in CR[sym] if b["t"] <= NOW[0]][-1]["c"]
        tick = p * 0.00002
        bids = [[f"{p - tick * (i + 1) * 3:.8f}", f"{abs(random.gauss(2, 1.5)):.4f}"] for i in range(limit)]
        asks = [[f"{p + tick * (i + 1) * 3:.8f}", f"{abs(random.gauss(2, 1.5)):.4f}"] for i in range(limit)]
        return {"bids": bids, "asks": asks}, None

    def agg_trades(self, sym, limit=1000):
        p = [b for b in CR[sym] if b["t"] <= NOW[0]][-1]["c"]
        return [{"p": f"{p:.8f}", "q": f"{abs(random.expovariate(8)):.5f}", "T": (NOW[0] - 300) * 1000 + i * 300,
                 "m": random.random() < 0.47} for i in range(limit)], None

    def ticker24(self, sym):
        return {"priceChangePercent": "0.5", "highPrice": "1", "lowPrice": "1", "volume": "1000",
                "quoteVolume": "98000000", "count": 123456}, None


SENT = []
main.send_message = lambda t: (SENT.append(t), (True, None))[1]
main.send_photo = lambda p, t: (SENT.append("[FOTO] " + t), (True, None))[1]
config.REALMARKET_API_KEY = "x"


def month_projection():
    t = 1796083200          # 2026-12-01 00:00 UTC
    end = session.month_bounds(t)
    used, last = 0, 0
    while t < end:
        if not session.market_closed(t):
            iv = session.plan(t, used, 1)["interval_min"]
            if iv is not None and t - last >= iv * 60 - config.FETCH_TOLERANCE_SEC:
                used += 1
                last = t
        t += 300
    return used


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "../_sim"
    shutil.rmtree(base, ignore_errors=True)
    n = int(288 * float(os.getenv("SIM_DAYS", "1")))
    fb = FakeBinance()
    for k in range(n):
        NOW[0] = START + k * 300 + 30
        main.RENDER = k in (0, n - 1)
        fb.calls = 0
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main.Engine(f"{base}/store", f"{base}/site", now=NOW[0], fetch=fake_fx, binance=fb).run()
        if k % 96 == 0 or k == n - 1:
            print(buf.getvalue().strip(), flush=True)
    print("runs", n, "forex API calls", CALLS["forex"], "telegram", len(SENT))
    for m in SENT[:2] + SENT[-4:]:
        print("-----\n" + m[:700])
    print("\nProyeksi kuota XAUUSD 1 bulan penuh (Des 2026):", month_projection(), "/", config.MONTHLY_QUOTA)
