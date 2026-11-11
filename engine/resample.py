"""Membentuk candle TF besar dari candle dasar + penyimpanan bertahap.

Forex (XAUUSD, sumber M5)  : sesi TradingView/OANDA – H4/D mulai 17:00 New York (DST otomatis),
                             W mulai Minggu 17:00 NY.
Crypto (Binance, sumber M1): UTC – D mulai 00:00 UTC, W mulai Senin 00:00 UTC (sama dengan Binance).
"""
from session import session_start, week_start

TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D": 86400, "W": 604800}
VOL_KEYS = ("v", "bv", "qv", "tc")


def bucket_start(t, tf, market="forex"):
    sec = TF_SEC[tf]
    if tf in ("M1", "M5", "M15", "H1") or (market == "crypto" and tf in ("H4", "D")):
        return t - t % sec
    if market == "crypto":                                   # W: Senin 00:00 UTC
        day = t // 86400
        return (day - (day + 3) % 7) * 86400
    if tf == "W":
        return week_start(t)
    s = session_start(t)
    if tf == "D":
        return s
    return s + (t - s) // TF_SEC["H4"] * TF_SEC["H4"]


def resample(bars, tf, market="forex"):
    out = {}
    for b in bars:
        k = bucket_start(b["t"], tf, market)
        cur = out.get(k)
        if cur is None:
            cur = out[k] = {"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "n": b.get("n", 1)}
            for v in VOL_KEYS:
                if v in b:
                    cur[v] = b[v]
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["n"] += b.get("n", 1)
            for v in VOL_KEYS:
                if v in b:
                    cur[v] = cur.get(v, 0) + b[v]
    return [out[k] for k in sorted(out)]


def upsert(store, new, covered_from=None):
    """Bucket >= covered_from = lengkap -> timpa.
    Bucket lebih tua (hanya sebagian tercakup data baru) -> gabung dengan yang tersimpan."""
    m = {b["t"]: b for b in store}
    for b in new:
        ex = m.get(b["t"])
        if ex is None or (covered_from is not None and b["t"] >= covered_from):
            m[b["t"]] = dict(b)
            continue
        if b.get("n", 0) >= ex.get("n", 0):
            merged = dict(ex)
            merged["h"] = max(ex["h"], b["h"])
            merged["l"] = min(ex["l"], b["l"])
            merged["c"] = b["c"]
            merged["n"] = max(ex.get("n", 0), b.get("n", 0))
            for v in VOL_KEYS:
                if v in b:
                    merged[v] = max(ex.get(v, 0), b[v])
            m[b["t"]] = merged
    return [m[k] for k in sorted(m)]


def closed_only(bars, tf, now):
    return [b for b in bars if b["t"] + TF_SEC[tf] <= now]
