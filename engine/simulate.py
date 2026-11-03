"""Simulasi offline: API & Telegram di-mock. python simulate.py"""
import math, os, random, shutil, sys
import config, main

random.seed(7)
START = 1793577600 + 2 * 3600  # Senin 2026-11-02 02:00 UTC
PRICES = {"XAUUSD": 3950.0, "EURUSD": 1.1150}
FEED = {}
for sym, p0 in PRICES.items():
    bars, p = [], p0
    vol = p0 * 0.0009
    for i in range(288 * 14):
        t = START - 288 * 7 * 300 + i * 300
        drift = math.sin(i / 400) * vol * 0.25
        o = p
        c = o + random.gauss(drift, vol)
        h, l = max(o, c) + abs(random.gauss(0, vol / 2)), min(o, c) - abs(random.gauss(0, vol / 2))
        if not main.market_closed(t):
            bars.append({"t": t, "o": o, "h": h, "l": l, "c": c})
        p = c
    FEED[sym] = bars
CALLS = []
NOW = [0]


def fake_fetch(sym, tf):
    CALLS.append((sym, tf))
    src = [b for b in FEED[sym] if b["t"] <= NOW[0]]
    if tf == "H1":
        from resample import resample
        return [dict(b) for b in resample(src, "H1")][-299:], None
    return [dict(b) for b in src[-288:]], None


SENT = []
main.fetch_candles = fake_fetch
main.send_message = lambda t: (SENT.append(t), (True, None))[1]
main.send_photo = lambda p, t: (SENT.append("[FOTO] " + t), (True, None))[1]
config.REALMARKET_API_KEY = "x"

base = "../_sim" if len(sys.argv) < 2 else sys.argv[1]
shutil.rmtree(base, ignore_errors=True)
import contextlib, io
N = 48 * int(os.getenv("SIM_DAYS", "5"))
for k in range(N):                      # 5 hari, tiap 30 menit
    NOW[0] = START + k * 1800 + 180
    main.RENDER = k == N - 1
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        st = main.Engine(f"{base}/store", f"{base}/site", now=NOW[0]).run()
    if k % 48 == 0 or k == N - 1:
        print(buf.getvalue())
print("runs", N, "API calls", len(CALLS), "telegram", len(SENT))
for m in SENT[:3] + SENT[-5:]:
    print("-----\n" + m)
