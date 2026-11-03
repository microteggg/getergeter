"""Membentuk candle M15/H1/H4/D/W dari M5 + menyimpan riwayat secara bertahap."""
import config

TF_SEC = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D": 86400, "W": 604800}


def _shift():
    # contoh: hari dimulai 22:00 UTC -> geser +2 jam agar 22:00 menjadi 00:00
    return ((24 - config.DAY_START_UTC_HOUR) % 24) * 3600


def bucket_start(t, tf):
    sec = TF_SEC[tf]
    if tf in ("M5", "M15", "H1"):
        return t - t % sec
    s = _shift()
    x = t + s
    if tf == "W":
        day = x // 86400
        weekday = (day + 3) % 7          # 1970-01-01 = Kamis; Senin = 0
        return (day - weekday) * 86400 - s
    return x - x % sec - s


def resample(bars, tf):
    """bars: list M5 (atau H1) terurut. Return list candle tf dengan jumlah sub-candle (n)."""
    out = {}
    for b in bars:
        k = bucket_start(b["t"], tf)
        cur = out.get(k)
        if cur is None:
            out[k] = {"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "n": 1}
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["n"] += 1
    return [out[k] for k in sorted(out)]


def upsert(store_bars, new_bars, covered_from=None, only_missing=False):
    """Gabungkan candle baru ke penyimpanan.
    - Bucket yang awalnya >= covered_from dianggap lengkap -> boleh menimpa.
    - Bucket lebih tua dari covered_from hanya ditambahkan bila belum ada (tanda partial)."""
    m = {b["t"]: b for b in store_bars}
    for b in new_bars:
        exist = m.get(b["t"])
        complete = covered_from is not None and b["t"] >= covered_from
        if only_missing:
            if exist is None or exist.get("partial"):
                m[b["t"]] = dict(b)
            continue
        if exist is None:
            nb = dict(b)
            if not complete:
                nb["partial"] = True
            m[b["t"]] = nb
        elif complete:
            m[b["t"]] = dict(b)
        elif exist.get("partial") and b.get("n", 0) > exist.get("n", 0):
            nb = dict(b)
            nb["partial"] = True
            m[b["t"]] = nb
    return [m[k] for k in sorted(m)]


def closed_only(bars, tf, now):
    return [b for b in bars if b["t"] + TF_SEC[tf] <= now]
