"""Struktur pasar ala SMC: swing, BOS, CHoCH, Order Block, Fair Value Gap, Liquidity Sweep."""


def swings(bars, k=2):
    hs, ls = [], []
    for i in range(k, len(bars) - k):
        h, l = bars[i]["h"], bars[i]["l"]
        if all(h > bars[j]["h"] for j in range(i - k, i)) and all(h >= bars[j]["h"] for j in range(i + 1, i + k + 1)):
            hs.append(i)
        if all(l < bars[j]["l"] for j in range(i - k, i)) and all(l <= bars[j]["l"] for j in range(i + 1, i + k + 1)):
            ls.append(i)
    return hs, ls


def _mitigate(z, bars, start):
    for j in range(start, len(bars)):
        if z["dir"] == "bull" and bars[j]["c"] < z["bottom"]:
            return j
        if z["dir"] == "bear" and bars[j]["c"] > z["top"]:
            return j
    return None


def structure(bars, k=2):
    hs, ls = swings(bars, k)
    hp = lp = 0
    last_h = last_l = None
    h_broken = l_broken = False
    trend = None
    events, obs, sweeps = [], [], []
    for i, b in enumerate(bars):
        while hp < len(hs) and hs[hp] + k <= i:
            last_h, h_broken = hs[hp], False
            hp += 1
        while lp < len(ls) and ls[lp] + k <= i:
            last_l, l_broken = ls[lp], False
            lp += 1
        if last_h is not None and not h_broken and b["h"] > bars[last_h]["h"] and b["c"] <= bars[last_h]["h"]:
            if not sweeps or sweeps[-1]["from_t"] != bars[last_h]["t"] or sweeps[-1]["dir"] != "bear":
                sweeps.append({"t": b["t"], "from_t": bars[last_h]["t"], "dir": "bear", "level": bars[last_h]["h"], "price": b["h"]})
        if last_l is not None and not l_broken and b["l"] < bars[last_l]["l"] and b["c"] >= bars[last_l]["l"]:
            if not sweeps or sweeps[-1]["from_t"] != bars[last_l]["t"] or sweeps[-1]["dir"] != "bull":
                sweeps.append({"t": b["t"], "from_t": bars[last_l]["t"], "dir": "bull", "level": bars[last_l]["l"], "price": b["l"]})
        if last_h is not None and not h_broken and b["c"] > bars[last_h]["h"]:
            events.append({"t": b["t"], "from_t": bars[last_h]["t"], "type": "CHoCH" if trend == "bear" else "BOS",
                           "dir": "bull", "level": bars[last_h]["h"]})
            start = last_l if last_l is not None else max(0, i - 10)
            for j in range(i - 1, max(start, i - 30) - 1, -1):
                if bars[j]["c"] < bars[j]["o"]:
                    obs.append({"dir": "bull", "t": bars[j]["t"], "top": bars[j]["h"], "bottom": bars[j]["l"], "created": i})
                    break
            trend, h_broken = "bull", True
        if last_l is not None and not l_broken and b["c"] < bars[last_l]["l"]:
            events.append({"t": b["t"], "from_t": bars[last_l]["t"], "type": "CHoCH" if trend == "bull" else "BOS",
                           "dir": "bear", "level": bars[last_l]["l"]})
            start = last_h if last_h is not None else max(0, i - 10)
            for j in range(i - 1, max(start, i - 30) - 1, -1):
                if bars[j]["c"] > bars[j]["o"]:
                    obs.append({"dir": "bear", "t": bars[j]["t"], "top": bars[j]["h"], "bottom": bars[j]["l"], "created": i})
                    break
            trend, l_broken = "bear", True
    active, mitigated = [], []
    for ob in obs:
        m = _mitigate(ob, bars, ob["created"] + 1)
        (active if m is None else mitigated).append(ob if m is None else dict(ob, end_t=bars[m]["t"]))
    return {"trend": trend, "events": events, "sweeps": sweeps,
            "order_blocks": active[-6:], "ob_mitigated": mitigated[-6:],
            "swing_high": bars[last_h]["h"] if last_h is not None else None,
            "swing_low": bars[last_l]["l"] if last_l is not None else None}


def fvgs(bars, min_size=0.0):
    act, mit = [], []
    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]
        z = None
        if c["l"] > a["h"] and c["l"] - a["h"] >= min_size:
            z = {"dir": "bull", "t": bars[i - 1]["t"], "top": c["l"], "bottom": a["h"]}
        elif c["h"] < a["l"] and a["l"] - c["h"] >= min_size:
            z = {"dir": "bear", "t": bars[i - 1]["t"], "top": a["l"], "bottom": c["h"]}
        if not z:
            continue
        end = None
        for j in range(i + 1, len(bars)):
            if (z["dir"] == "bull" and bars[j]["l"] <= z["bottom"]) or (z["dir"] == "bear" and bars[j]["h"] >= z["top"]):
                end = j
                break
        (act if end is None else mit).append(z if end is None else dict(z, end_t=bars[end]["t"]))
    return act[-8:], mit[-8:]
