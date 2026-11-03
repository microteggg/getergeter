"""Struktur pasar sederhana ala SMC: swing, BOS, CHoCH, Order Block, Fair Value Gap."""


def swings(bars, k=2):
    hs, ls = [], []
    for i in range(k, len(bars) - k):
        h, l = bars[i]["h"], bars[i]["l"]
        if all(h > bars[j]["h"] for j in range(i - k, i)) and all(h >= bars[j]["h"] for j in range(i + 1, i + k + 1)):
            hs.append(i)
        if all(l < bars[j]["l"] for j in range(i - k, i)) and all(l <= bars[j]["l"] for j in range(i + 1, i + k + 1)):
            ls.append(i)
    return hs, ls


def structure(bars, k=2):
    """Telusuri candle satu per satu. Swing baru dianggap 'diketahui' setelah k candle sesudahnya close.
    Return dict: trend ('bull'/'bear'/None), events, order_blocks, last_swing_high/low."""
    hs, ls = swings(bars, k)
    hi_ptr = lo_ptr = 0
    last_h = last_l = None          # index swing yang aktif
    h_broken = l_broken = False
    trend = None
    events, obs = [], []
    for i, b in enumerate(bars):
        while hi_ptr < len(hs) and hs[hi_ptr] + k <= i:
            last_h, h_broken = hs[hi_ptr], False
            hi_ptr += 1
        while lo_ptr < len(ls) and ls[lo_ptr] + k <= i:
            last_l, l_broken = ls[lo_ptr], False
            lo_ptr += 1
        if last_h is not None and not h_broken and b["c"] > bars[last_h]["h"]:
            typ = "CHoCH" if trend == "bear" else "BOS"
            events.append({"i": i, "t": b["t"], "type": typ, "dir": "bull", "level": bars[last_h]["h"]})
            start = last_l if last_l is not None else max(0, i - 10)
            for j in range(i - 1, max(start, i - 30) - 1, -1):
                if bars[j]["c"] < bars[j]["o"]:
                    obs.append({"dir": "bull", "t": bars[j]["t"], "i": j, "top": bars[j]["h"], "bottom": bars[j]["l"], "created": i})
                    break
            trend, h_broken = "bull", True
        if last_l is not None and not l_broken and b["c"] < bars[last_l]["l"]:
            typ = "CHoCH" if trend == "bull" else "BOS"
            events.append({"i": i, "t": b["t"], "type": typ, "dir": "bear", "level": bars[last_l]["l"]})
            start = last_h if last_h is not None else max(0, i - 10)
            for j in range(i - 1, max(start, i - 30) - 1, -1):
                if bars[j]["c"] > bars[j]["o"]:
                    obs.append({"dir": "bear", "t": bars[j]["t"], "i": j, "top": bars[j]["h"], "bottom": bars[j]["l"], "created": i})
                    break
            trend, l_broken = "bear", True
    # Order block yang belum ditembus close
    active_obs = []
    for ob in obs:
        later = bars[ob["created"] + 1:]
        if ob["dir"] == "bull" and any(x["c"] < ob["bottom"] for x in later):
            continue
        if ob["dir"] == "bear" and any(x["c"] > ob["top"] for x in later):
            continue
        active_obs.append(ob)
    return {
        "trend": trend,
        "events": events,
        "order_blocks": active_obs[-6:],
        "swing_high": bars[last_h]["h"] if last_h is not None else None,
        "swing_low": bars[last_l]["l"] if last_l is not None else None,
        "swing_high_t": bars[last_h]["t"] if last_h is not None else None,
        "swing_low_t": bars[last_l]["t"] if last_l is not None else None,
    }


def fvgs(bars, min_size=0.0):
    """FVG yang belum tertutup penuh (belum di-mitigasi)."""
    out = []
    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]
        if c["l"] > a["h"] and c["l"] - a["h"] >= min_size:
            z = {"dir": "bull", "t": bars[i - 1]["t"], "i": i - 1, "top": c["l"], "bottom": a["h"]}
            if not any(x["l"] <= z["bottom"] for x in bars[i + 1:]):
                out.append(z)
        if c["h"] < a["l"] and a["l"] - c["h"] >= min_size:
            z = {"dir": "bear", "t": bars[i - 1]["t"], "i": i - 1, "top": a["l"], "bottom": c["h"]}
            if not any(x["h"] >= z["top"] for x in bars[i + 1:]):
                out.append(z)
    return out[-8:]
