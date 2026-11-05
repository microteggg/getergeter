"""Liquidity-first SMC/ICT model: Liquidity -> Sweep -> Displacement -> CISD/MSS -> contextual FVG/OB."""

def swings(bars, k=3):
    hs, ls = [], []
    for i in range(k, len(bars) - k):
        if bars[i]["h"] == max(x["h"] for x in bars[i-k:i+k+1]): hs.append(i)
        if bars[i]["l"] == min(x["l"] for x in bars[i-k:i+k+1]): ls.append(i)
    return hs, ls


def liquidity_pools(bars, k=3, tolerance=0.0):
    hs, ls = swings(bars, k); out = []
    for side, ids, key in (("BSL", hs, "h"), ("SSL", ls, "l")):
        for n, i in enumerate(ids):
            level = bars[i][key]; touches = [i]
            for j in ids[n+1:]:
                if abs(bars[j][key] - level) <= tolerance:
                    touches.append(j)
            out.append({"id": f"{side}:{bars[i]['t']}", "side": side, "level": level,
                        "t": bars[i]["t"], "touches": len(touches), "index": i})
    return sorted(out, key=lambda x: x["index"])


def _displacement(bars, i, direction, atr_value, body_mult=0.8):
    b = bars[i]; body = abs(b["c"] - b["o"]); rng = max(b["h"] - b["l"], 1e-12)
    directional = b["c"] > b["o"] if direction == "bull" else b["c"] < b["o"]
    return directional and body / rng >= 0.60 and body >= body_mult * max(atr_value, 1e-12)


def model(bars, atr_value, k=3, eq_tolerance=0.0):
    pools = liquidity_pools(bars, k, eq_tolerance)
    sweeps, events, fvgs, obs, sequences = [], [], [], [], []
    trend = None
    for p in pools:
        level, pi = p["level"], p["index"]
        for i in range(pi + k, len(bars)):
            b = bars[i]
            if p["side"] == "SSL":
                swept = b["l"] < level and b["c"] >= level; direction = "bull"
            else:
                swept = b["h"] > level and b["c"] <= level; direction = "bear"
            if not swept: continue
            sw = {"id": f"SW:{p['id']}:{b['t']}", "t": b["t"], "from_t": p["t"], "dir": direction,
                  "side": p["side"], "level": level, "price": b["l"] if direction == "bull" else b["h"], "index": i}
            sweeps.append(sw)
            # Confirm only within a limited post-sweep window.
            for j in range(i, min(len(bars), i + 10)):
                if not _displacement(bars, j, direction, atr_value): continue
                prior = bars[max(i-8, 0):j+1]
                break_level = max(x["h"] for x in prior[:-1]) if direction == "bull" else min(x["l"] for x in prior[:-1])
                broken = bars[j]["c"] > break_level if direction == "bull" else bars[j]["c"] < break_level
                if not broken: continue
                typ = "CISD/MSS"
                ev = {"t": bars[j]["t"], "from_t": sw["t"], "type": typ, "dir": direction,
                      "level": break_level, "sweep_id": sw["id"], "displacement": True, "index": j}
                events.append(ev); trend = direction
                # Contextual FVG formed by the displacement leg.
                z = None
                if j >= 2 and direction == "bull" and bars[j]["l"] > bars[j-2]["h"]:
                    z = {"dir":"bull","t":bars[j-1]["t"],"top":bars[j]["l"],"bottom":bars[j-2]["h"],"created":j}
                elif j >= 2 and direction == "bear" and bars[j]["h"] < bars[j-2]["l"]:
                    z = {"dir":"bear","t":bars[j-1]["t"],"top":bars[j-2]["l"],"bottom":bars[j]["h"],"created":j}
                if z:
                    z.update(context=True, sweep_id=sw["id"], event_t=ev["t"], mid=(z["top"]+z["bottom"])/2, kind="FVG")
                    fvgs.append(z)
                # Last opposing candle inside impulse leg, valid only because it led to MSS.
                ob = None
                for q in range(j-1, max(i-1, j-8), -1):
                    opposing = bars[q]["c"] < bars[q]["o"] if direction == "bull" else bars[q]["c"] > bars[q]["o"]
                    if opposing:
                        ob = {"dir":direction,"t":bars[q]["t"],"top":bars[q]["h"],"bottom":bars[q]["l"],
                              "created":j,"context":True,"sweep_id":sw["id"],"event_t":ev["t"],"kind":"OB"}; obs.append(ob); break
                sequences.append({"id": sw["id"], "dir": direction, "sweep": sw, "event": ev, "fvg": z, "ob": ob})
                break
            break
    def state(z):
        touched = invalid = None
        for j in range(z["created"]+1, len(bars)):
            b=bars[j]; overlap = b["l"] <= z["top"] and b["h"] >= z["bottom"]
            if overlap and touched is None: touched=j
            if (z["dir"]=="bull" and b["c"] < z["bottom"]) or (z["dir"]=="bear" and b["c"] > z["top"]): invalid=j; break
        x=dict(z, touch_t=bars[touched]["t"] if touched is not None else None)
        if invalid is not None: x["end_t"]=bars[invalid]["t"]
        return x, invalid is None
    af, mf, ao, mo = [], [], [], []
    for z in fvgs:
        x,a=state(z); (af if a else mf).append(x)
    for z in obs:
        x,a=state(z); (ao if a else mo).append(x)
    return {"trend":trend,"liquidity":pools[-20:],"sweeps":sweeps[-20:],"events":events[-20:],
            "fvg":af[-8:],"fvg_mit":mf[-8:],"order_blocks":ao[-8:],"ob_mitigated":mo[-8:],"sequences":sequences[-10:]}
