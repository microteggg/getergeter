"""Auto Fibonacci v1 (H4 utama + Fib lokal per TF) – hanya candle tertutup, tanpa lookahead.

KONVENSI (dikunci, sama untuk semua TF):
  Bullish impulse : Swing Low  = 100%  ->  Swing High = 0%
  Bearish impulse : Swing High = 100%  ->  Swing Low  = 0%
  Level retracement dihitung DARI ujung impulse (0%) KEMBALI ke awal impulse (100%):
    bull : harga(r) = high - r x (high - low)      (turun dari swing high)
    bear : harga(r) = low  + r x (high - low)      (naik dari swing low)

PEMILIHAN ANCHOR (bukan highest-high / lowest-low sembarang):
  1. Impulse = leg yang menghasilkan BOS/CHoCH (definisi struktur dari smc.py, pivot terkonfirmasi).
       bull: pivot high p ditembus close di bar i  -> 100% = low terendah di [p..i] (asal impuls)
       bear: pivot low  p ditembus close di bar i  -> 100% = high tertinggi di [p..i]
  2. 0% = ekstrem impuls sejak asal impuls. 0% HANYA bergeser bila harga membuat ekstrem baru
     (impuls masih memanjang) – bukan karena noise candle.
  3. Anchor diganti hanya oleh BOS/CHoCH baru (scope sama). Leg kecil (< FIB_MIN_ATR x ATR14) diabaikan.
  4. Close menembus 100% -> fib INVALID. Bila fib swing invalid, dipakai fib internal terbaru yang
     masih valid (ditandai scope=internal) sampai muncul leg swing baru.
"""
from bisect import bisect_right

import config

LEVELS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
GOLD = (0.5, 0.618)                    # zona utama yang dipantau (50 – 61.8)
_RANK = {"internal": 0, "swing": 1}


def price_at(direction, hi, lo, r):
    return hi - r * (hi - lo) if direction == "bull" else lo + r * (hi - lo)


def retrace_of(direction, hi, lo, price):
    rng = hi - lo
    if rng <= 1e-12:
        return None
    return (hi - price) / rng if direction == "bull" else (price - lo) / rng


def legs(bars, events, atr14, scope):
    """Impulse leg untuk setiap BOS/CHoCH dengan scope tertentu (urut index break)."""
    out = []
    min_atr = config.FIB_MIN_ATR
    for e in events:
        if e.get("scope") != scope:
            continue
        p, i = e.get("pivot_index"), e.get("index")
        if p is None or i is None or i <= p:
            continue
        bull = e["dir"] == "bull"
        seg = range(p, i + 1)
        o = min(seg, key=lambda j: (bars[j]["l"], j)) if bull else min(seg, key=lambda j: (-bars[j]["h"], j))
        ext = max(range(o, i + 1), key=lambda j: (bars[j]["h"], -j)) if bull else \
            max(range(o, i + 1), key=lambda j: (-bars[j]["l"], -j))
        lo = bars[o]["l"] if bull else bars[ext]["l"]
        hi = bars[ext]["h"] if bull else bars[o]["h"]
        a = atr14[i] if i < len(atr14) and atr14[i] else (bars[i]["h"] - bars[i]["l"])
        if hi - lo < min_atr * max(a, 1e-12):
            continue
        out.append({"id": f"FIB:{scope}:{e['dir']}:{bars[o]['t']}:{e['t']}", "scope": scope, "dir": e["dir"],
                    "i": i, "origin_i": o, "ext_i": ext, "lo": lo, "hi": hi,
                    "event": {"t": e["t"], "label": e["label"], "type": e["type"], "level": e["level"]}})
    out.sort(key=lambda x: x["i"])
    return out


def _series_one(bars, lg, start_i):
    """Fib (scope tunggal) yang diketahui pada close bar j, untuk j >= start_i."""
    out = {}
    n = len(bars)
    for li, leg in enumerate(lg):
        a = leg["i"]
        b = lg[li + 1]["i"] if li + 1 < len(lg) else n
        if b <= start_i or a >= n:
            continue
        bull = leg["dir"] == "bull"
        hi, lo, ext_i, inv = leg["hi"], leg["lo"], leg["ext_i"], None
        for j in range(a, b):
            bj = bars[j]
            if j > a and inv is None:
                if bull and bj["h"] > hi:
                    hi, ext_i = bj["h"], j
                elif not bull and bj["l"] < lo:
                    lo, ext_i = bj["l"], j
            if inv is None and ((bull and bj["c"] < lo) or (not bull and bj["c"] > hi)):
                inv = j
            if j >= start_i:
                out[j] = (leg, hi, lo, ext_i, inv)
    return out


def series(bars, legs_by_scope, start_i=0):
    """Fib aktif per bar j: swing bila valid, kalau tidak internal valid terbaru, kalau tidak swing invalid."""
    sw = _series_one(bars, legs_by_scope.get("swing", []), start_i)
    it = _series_one(bars, legs_by_scope.get("internal", []), start_i) if config.FIB_FALLBACK_INTERNAL else {}
    out = {}
    for j in range(start_i, len(bars)):
        s, x = sw.get(j), it.get(j)
        if s and s[4] is None:
            out[j] = s
        elif x and x[4] is None and (not s or x[0]["i"] > s[4]):
            out[j] = x
        else:
            out[j] = s
    return out


def lite(rec, bars, j):
    """Ringkasan internal (dipakai monitor)."""
    if not rec:
        return None
    leg, hi, lo, ext_i, inv = rec
    return {"leg_id": leg["id"], "id": f"{leg['id']}@{bars[ext_i]['t']}", "scope": leg["scope"], "dir": leg["dir"],
            "hi": hi, "lo": lo, "origin_i": leg["origin_i"], "ext_i": ext_i, "valid": inv is None or inv > j,
            "invalid_i": inv}


def public(rec, bars, j, tf, digits, price, k_confirm):
    """Format JSON untuk dashboard / state / Telegram."""
    f = lite(rec, bars, j)
    if not f:
        return None
    leg = rec[0]
    bull = f["dir"] == "bull"
    hi, lo = f["hi"], f["lo"]
    a100 = {"t": bars[f["origin_i"]]["t"], "price": round(lo if bull else hi, digits)}
    a0 = {"t": bars[f["ext_i"]]["t"], "price": round(hi if bull else lo, digits)}
    lv = [{"r": r, "price": round(price_at(f["dir"], hi, lo, r), digits)} for r in LEVELS]
    g = sorted(round(price_at(f["dir"], hi, lo, r), digits) for r in GOLD)
    rt = retrace_of(f["dir"], hi, lo, price) if price is not None else None
    zone = None
    if rt is not None:
        zone = ("di atas 0% (impuls memanjang)" if rt < 0 else "0 – 38.2" if rt < 0.382 else
                "38.2 – 50" if rt < 0.5 else "50 – 61.8 (zona utama)" if rt <= 0.618 else
                "61.8 – 78.6" if rt <= 0.786 else "78.6 – 100" if rt <= 1 else "lewat 100% (invalid)")
    return {"id": f["id"], "tf": tf, "scope": f["scope"], "dir": f["dir"],
            "anchor100": a100, "anchor0": a0, "start_t": leg["event"]["t"],
            "event": {"t": leg["event"]["t"], "label": leg["event"]["label"], "level": round(leg["event"]["level"], digits)},
            "levels": lv, "gold": g, "range": round(hi - lo, digits),
            "retrace": None if rt is None else round(rt * 100, 1), "zone": zone,
            "extending": f["ext_i"] > j - k_confirm,
            "status": "invalid" if not f["valid"] else "active"}


def build(bars, smc, atr14, tf, digits, price):
    """Return (fib_public|None, legs_by_scope, series_fn). series_fn(start_i) -> {j: rec}."""
    if not config.FIB_ENABLED or len(bars) < 10:
        return None, {}, (lambda start_i=0: {})
    ev = smc.get("events") or []
    lg = {"swing": legs(bars, ev, atr14, "swing"), "internal": legs(bars, ev, atr14, "internal")}
    k_int, _ = config.structure_lengths(tf)
    last = len(bars) - 1
    cur = series(bars, lg, last).get(last)
    return public(cur, bars, last, tf, digits, price, k_int), lg, (lambda start_i=0: series(bars, lg, start_i))


def index_at(close_times, T):
    """Index bar terakhir yang sudah close pada waktu T (close_times urut)."""
    return bisect_right(close_times, T) - 1
