"""Simulasi offline: RealMarketAPI, Binance, dan Telegram di-mock.
python simulate.py [folder]    (SIM_DAYS=2, run tiap 10 menit)"""
import contextlib, io, math, os, random, shutil, sys
import config, main
from resample import resample

random.seed(11)
START = 1793577600 + 2 * 3600            # Senin 2026-11-02 02:00 UTC
NOW = [0]


def walk(p0, step, n, t0, vol_scale=1.0):
    bars, p = [], p0
    vol = p0 * step
    for i in range(n):
        t = t0 + i * step_sec
        o = p
        c = o + random.gauss(math.sin(i / (400 * 5 / (step_sec / 60))) * vol * 0.25, vol)
        h, l = max(o, c) + abs(random.gauss(0, vol / 2)), min(o, c) - abs(random.gauss(0, vol / 2))
        v = abs(random.gauss(50, 20)) * vol_scale
        bv = v * min(0.95, max(0.05, 0.5 + (c - o) / (h - l + 1e-12) * 0.3))
        bars.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "bv": bv, "qv": v * c, "tc": int(v * 10)})
        p = c
    return bars


step_sec = 300
FX = {}
for sym, p0 in {"XAUUSD": 3950.0, "EURUSD": 1.115}.items():
    FX[sym] = [dict(t=b["t"], o=b["o"], h=b["h"], l=b["l"], c=b["c"])
               for b in walk(p0, 0.0009, 288 * 16, START - 288 * 7 * 300) if not main.forex_closed(b["t"])]
step_sec = 60
CR = {s: walk(p0, 0.0004, 1440 * 30, START - 1440 * 25 * 60, sc) for s, p0, sc in
      (("BTCUSDT", 98000.0, 1), ("ETHUSDT", 3400.0, 15))}
CALLS = {"forex": 0, "binance": 0}


def fake_fx(sym, tf):
    CALLS["forex"] += 1
    src = [b for b in FX[sym] if b["t"] <= NOW[0]]
    if tf == "H1":
        return [dict(b) for b in resample(src, "H1", 22)][-299:], None
    return [dict(b) for b in src[-288:]], None


class FakeBinance:
    def __init__(self, *_):
        self.host, self.calls, self.weight = "https://data-api.binance.vision", 0, 0

    def klines(self, sym, tf, limit=1000, start_ms=None):
        self.calls += 1
        self.weight += 2
        src = [b for b in CR[sym] if b["t"] <= NOW[0]]
        if start_ms:
            src = [b for b in src if b["t"] >= start_ms // 1000]
            rows = src[:limit] if tf == "M1" else resample(src, tf, 0)[:limit]
        else:
            rows = src[-limit:] if tf == "M1" else resample(src, tf, 0)[-limit:]
        return [{k: v for k, v in b.items() if k != "n"} for b in rows], None

    def depth(self, sym, limit=100):
        self.calls += 1
        p = [b for b in CR[sym] if b["t"] <= NOW[0]][-1]["c"]
        tick = p * 0.00002
        bids = [[f"{p - tick * (i + 1) * 3:.2f}", f"{abs(random.gauss(2, 1.5)) * (8 if i == 37 else 1):.4f}"] for i in range(limit)]
        asks = [[f"{p + tick * (i + 1) * 3:.2f}", f"{abs(random.gauss(2, 1.5)) * (8 if i == 55 else 1):.4f}"] for i in range(limit)]
        return {"lastUpdateId": 1, "bids": bids, "asks": asks}, None

    def agg_trades(self, sym, limit=1000):
        self.calls += 1
        p = [b for b in CR[sym] if b["t"] <= NOW[0]][-1]["c"]
        return [{"a": i, "p": f"{p + random.gauss(0, p * 0.0002):.2f}", "q": f"{abs(random.expovariate(8)):.5f}",
                 "T": (NOW[0] - 300) * 1000 + i * 300, "m": random.random() < 0.47} for i in range(limit)], None

    def ticker24(self, sym):
        self.calls += 1
        return {"priceChangePercent": f"{random.gauss(0, 2):.3f}", "highPrice": "1", "lowPrice": "1",
                "volume": "1000", "quoteVolume": "98000000", "count": 123456}, None


SENT = []
main.fetch_candles = fake_fx
main.send_message = lambda t: (SENT.append(t), (True, None))[1]
main.send_photo = lambda p, t: (SENT.append("[FOTO] " + t), (True, None))[1]
config.REALMARKET_API_KEY = "x"

base = sys.argv[1] if len(sys.argv) > 1 else "../_sim"
shutil.rmtree(base, ignore_errors=True)
N = 144 * float(os.getenv("SIM_DAYS", "2"))
N = int(N)
fb = FakeBinance()
for k in range(N):
    NOW[0] = START + k * 600 + 60
    main.RENDER = k == N - 1 or k == 0
    fb.calls = fb.weight = 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main.Engine(f"{base}/store", f"{base}/site", now=NOW[0], binance=fb).run()
    if k % 72 == 0 or k == N - 1:
        print(buf.getvalue().strip())
print("runs", N, "forex API calls", CALLS["forex"], "telegram", len(SENT))
for m in SENT[:3] + SENT[-4:]:
    print("-----\n" + m[:600])
