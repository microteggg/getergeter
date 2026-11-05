"""Membentuk candle TF besar dari candle dasar (forex: M5, crypto: M1) + penyimpanan bertahap."""
TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D": 86400, "W": 604800}
VOL_KEYS = ("v", "bv", "qv", "tc")


def bucket_start(t, tf, day_start=0):
    sec = TF_SEC[tf]
    if tf in ("M1", "M5", "M15", "H1"):
        return t - t % sec
    s = ((24 - day_start) % 24) * 3600
    x = t + s
    if tf == "W":
        day = x // 86400
        return (day - (day + 3) % 7) * 86400 - s     # Senin
    return x - x % sec - s


def resample(bars, tf, day_start=0):
    out = {}
    for b in bars:
        k = bucket_start(b["t"], tf, day_start)
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
    Bucket lebih tua (hanya sebagian tercakup data baru) -> gabung dengan yang tersimpan:
    open lama, high/low gabungan, close baru. Benar selama run saling tumpang-tindih."""
    m = {b["t"]: b for b in store}
    for b in new:
        ex = m.get(b["t"])
        if ex is None or (covered_from is not None and b["t"] >= covered_from):
            m[b["t"]] = dict(b)
            continue
        if b.get("n", 0) >= ex.get("n", 0) and ex.get("partial") is not False:
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
