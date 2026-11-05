"""SMC/ICT engine v3 – liquidity-first, dengan struktur internal/eksternal.

Alur model:
  External structure (BOS/CHoCH, strong/weak level, dealing range)
  -> Liquidity pool (swing, EQH/EQL, PDH/PDL, Asian/London H/L)
  -> Sweep valid -> Displacement -> CISD (internal) / MSS (eksternal)
  -> PD Array kontekstual (FVG di kaki impuls + Order Block)
  -> Inducement -> Retracement ke discount/premium (OTE) -> Target liquidity

Semua perhitungan hanya memakai candle yang sudah close (dikirim dari analysis.py).
"""

SWEEP_CONFIRM_BARS = 2      # candle setelah sweep yang tidak boleh close melewati level
MSS_WINDOW = 10             # jarak maksimal candle sweep -> displacement
OB_LOOKBACK = 5             # OB dicari sampai 5 candle sebelum sweep
PD_DAYS = 3                 # jumlah hari terakhir untuk PDH/PDL & session H/L
SESSIONS = (("AS", 0, 7), ("LO", 7, 12))   # jam UTC: Asia 07-14 WIB, London 14-19 WIB
HIGH_VALUE = ("EQH", "EQL", "PDH", "PDL", "ASH", "ASL", "LOH", "LOL")


# ------------------------------------------------------------------ dasar
def swings(bars, k=3):
    hs, ls = [], []
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(x["h"] for x in win): hs.append(i)
        if bars[i]["l"] == min(x["l"] for x in win): ls.append(i)
    return hs, ls


def _tf_sec(bars):
    if len(bars) < 3: return 3600
    ds = sorted(bars[i + 1]["t"] - bars[i]["t"] for i in range(len(bars) - 1))
    return max(ds[len(ds) // 2], 60)


def _body_ok(b, direction, atr_value, mult):
    body = abs(b["c"] - b["o"]); rng = max(b["h"] - b["l"], 1e-12)
    directional = b["c"] > b["o"] if direction == "bull" else b["c"] < b["o"]
    return directional and body / rng >= 0.60 and body >= mult * max(atr_value, 1e-12)


# ------------------------------------------------------------------ struktur
def structure(bars, k):
    """BOS/CHoCH berbasis swing terkonfirmasi (tanpa lookahead) + protected level."""
    hs, ls = swings(bars, k)
    conf_h = {i + k: i for i in hs}; conf_l = {i + k: i for i in ls}
    last_h = last_l = None; trend = None; protected = None; events = []
    for j, b in enumerate(bars):
        if j in conf_h: last_h = conf_h[j]
        if j in conf_l: last_l = conf_l[j]
        if last_h is not None and b["c"] > bars[last_h]["h"]:
            pl = min(range(last_h, j + 1), key=lambda q: bars[q]["l"])
            events.append({"t": b["t"], "from_t": bars[last_h]["t"], "type": "CHoCH" if trend == "bear" else "BOS",
                           "dir": "bull", "level": bars[last_h]["h"], "index": j})
            trend, protected, last_h = "bull", pl, None
        elif last_l is not None and b["c"] < bars[last_l]["l"]:
            ph = max(range(last_l, j + 1), key=lambda q: bars[q]["h"])
            events.append({"t": b["t"], "from_t": bars[last_l]["t"], "type": "CHoCH" if trend == "bull" else "BOS",
                           "dir": "bear", "level": bars[last_l]["l"], "index": j})
            trend, protected, last_l = "bear", ph, None
    return {"trend": trend, "events": events, "protected": protected, "highs": hs, "lows": ls}


def dealing_range(bars, ext, price):
    """Range dari strong (protected) level ke weak level; premium/discount & OTE."""
    p, trend = ext["protected"], ext["trend"]
    if p is None or trend is None: return None
    if trend == "bull":
        lo = bars[p]["l"]; hi_i = max(range(p, len(bars)), key=lambda q: bars[q]["h"]); hi = bars[hi_i]["h"]
        t_lo, t_hi = bars[p]["t"], bars[hi_i]["t"]; strong, weak = "low", "high"
    else:
        hi = bars[p]["h"]; lo_i = min(range(p, len(bars)), key=lambda q: bars[q]["l"]); lo = bars[lo_i]["l"]
        t_hi, t_lo = bars[p]["t"], bars[lo_i]["t"]; strong, weak = "high", "low"
    if hi <= lo: return None
    eq = (hi + lo) / 2; pos = (price - lo) / (hi - lo)
    ote = (hi - 0.79 * (hi - lo), hi - 0.62 * (hi - lo)) if trend == "bull" else (lo + 0.62 * (hi - lo), lo + 0.79 * (hi - lo))
    return {"dir": trend, "hi": hi, "lo": lo, "eq": eq, "t_hi": t_hi, "t_lo": t_lo, "strong": strong, "weak": weak,
            "pos": round(pos, 3), "pd": "discount" if price < eq else "premium",
            "ote_bottom": ote[0], "ote_top": ote[1], "t": min(t_hi, t_lo)}


# ------------------------------------------------------------------ liquidity
def _taken(bars, side, level, start):
    for i in range(start, len(bars)):
        if (side == "BSL" and bars[i]["h"] > level) or (side == "SSL" and bars[i]["l"] < level):
            return i
    return None


def liquidity_pools(bars, k=3, tolerance=0.0, ext_highs=(), ext_lows=()):
    hs, ls = swings(bars, k); out = []
    eh, el = set(ext_highs), set(ext_lows)
    for side, ids, key, ext in (("BSL", hs, "h", eh), ("SSL", ls, "l", el)):
        for n, i in enumerate(ids):
            level = bars[i][key]; kind = "EXT" if i in ext else "INT"
            for j in ids[max(0, n - 6):n]:              # equal highs/lows dengan swing sebelumnya
                if abs(bars[j][key] - level) <= tolerance:
                    kind = "EQH" if side == "BSL" else "EQL"
                    level = max(level, bars[j][key]) if side == "BSL" else min(level, bars[j][key])
            out.append({"id": f"{side}:{bars[i]['t']}", "side": side, "level": level, "t": bars[i]["t"],
                        "kind": kind, "index": i, "start": i + k})
    tf = _tf_sec(bars)
    if tf < 86400 and bars:
        days = {}
        for i, b in enumerate(bars): days.setdefault(b["t"] // 86400, []).append(i)
        keys = sorted(days)
        for n, dk in enumerate(keys[:-1][-PD_DAYS:]):
            ids = days[dk]; nxt = days[keys[keys.index(dk) + 1]][0]
            hi_i = max(ids, key=lambda q: bars[q]["h"]); lo_i = min(ids, key=lambda q: bars[q]["l"])
            out.append({"id": f"PDH:{dk}", "side": "BSL", "level": bars[hi_i]["h"], "t": bars[hi_i]["t"], "kind": "PDH", "index": hi_i, "start": nxt})
            out.append({"id": f"PDL:{dk}", "side": "SSL", "level": bars[lo_i]["l"], "t": bars[lo_i]["t"], "kind": "PDL", "index": lo_i, "start": nxt})
        if tf <= 3600:
            for dk in keys[-PD_DAYS:]:
                for name, h0, h1 in SESSIONS:
                    ids = [q for q in days[dk] if h0 <= (bars[q]["t"] % 86400) // 3600 < h1]
                    if not ids: continue
                    close_t = dk * 86400 + h1 * 3600
                    end = next((q for q in range(ids[-1] + 1, len(bars)) if bars[q]["t"] >= close_t), None)
                    if end is None: continue             # sesi belum selesai
                    hi_i = max(ids, key=lambda q: bars[q]["h"]); lo_i = min(ids, key=lambda q: bars[q]["l"])
                    out.append({"id": f"{name}H:{dk}", "side": "BSL", "level": bars[hi_i]["h"], "t": bars[hi_i]["t"], "kind": name + "H", "index": hi_i, "start": end})
                    out.append({"id": f"{name}L:{dk}", "side": "SSL", "level": bars[lo_i]["l"], "t": bars[lo_i]["t"], "kind": name + "L", "index": lo_i, "start": end})
    for p in out:
        ti = _taken(bars, p["side"], p["level"], p["start"])
        p["taken_t"] = bars[ti]["t"] if ti is not None else None
        p["resting"] = ti is None
    return sorted(out, key=lambda x: x["start"])


def _find_sweep(bars, p):
    """Sweep valid: wick menembus, close kembali, 2 candle berikut tidak close melewati level."""
    level, side = p["level"], p["side"]
    for i in range(p["start"], len(bars)):
        b = bars[i]
        if side == "SSL":
            if b["c"] < level: return None
            if not b["l"] < level: continue
            direction = "bull"
        else:
            if b["c"] > level: return None
            if not b["h"] > level: continue
            direction = "bear"
        nxt = bars[i + 1:i + 1 + SWEEP_CONFIRM_BARS]
        if any((x["c"] < level) if direction == "bull" else (x["c"] > level) for x in nxt): return None
        return {"id": f"SW:{p['id']}:{b['t']}", "t": b["t"], "from_t": p["t"], "dir": direction, "side": side,
                "kind": p["kind"], "level": level, "price": b["l"] if direction == "bull" else b["h"], "index": i}
    return None


# ------------------------------------------------------------------ PD array
def _fvg_in_leg(bars, i, j, direction):
    best = None
    for m in range(max(i + 1, 1), min(len(bars) - 1, j + 2)):
        a, c = bars[m - 1], bars[m + 1]
        if direction == "bull" and c["l"] > a["h"]:
            z = {"dir": "bull", "t": bars[m]["t"], "top": c["l"], "bottom": a["h"], "created": m + 1}
        elif direction == "bear" and c["h"] < a["l"]:
            z = {"dir": "bear", "t": bars[m]["t"], "top": a["l"], "bottom": c["h"], "created": m + 1}
        else: continue
        if best is None or z["top"] - z["bottom"] > best["top"] - best["bottom"]: best = z
    return best


def _order_block(bars, i, j, direction):
    for q in range(j - 1, max(-1, i - OB_LOOKBACK - 1), -1):
        b = bars[q]
        if (b["c"] < b["o"]) if direction == "bull" else (b["c"] > b["o"]): return q
    return i


def _state(bars, z, kind):
    """fresh -> tapped -> mitigated (FVG: CE 50%, OB: close lewat 50%) / invalid (close tembus sisi jauh)."""
    mid = (z["top"] + z["bottom"]) / 2; status, touch, end = "fresh", None, None
    for j in range(z["created"] + 1, len(bars)):
        b = bars[j]
        if z["dir"] == "bull":
            if b["c"] < z["bottom"]: status, end = "invalid", j; break
            if b["l"] <= z["top"] and touch is None: touch, status = j, "tapped"
            reached = b["l"] <= mid if kind == "FVG" else b["c"] < mid
        else:
            if b["c"] > z["top"]: status, end = "invalid", j; break
            if b["h"] >= z["bottom"] and touch is None: touch, status = j, "tapped"
            reached = b["h"] >= mid if kind == "FVG" else b["c"] > mid
        if reached: status, end = "mitigated", j; break
    x = dict(z, mid=mid, status=status, touch_t=bars[touch]["t"] if touch is not None else None, touch_i=touch)
    if end is not None: x["end_t"] = bars[end]["t"]
    return x


def _inducement(bars, j, direction, zone_edge, k=2):
    """Swing internal pertama setelah MSS yang berada di antara harga dan POI."""
    for i in range(j + 1, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if direction == "bull" and bars[i]["l"] == min(x["l"] for x in win) and bars[i]["l"] > zone_edge:
            lvl = bars[i]["l"]; tk = _taken(bars, "SSL", lvl, i + k)
            return {"t": bars[i]["t"], "level": lvl, "dir": "bull", "taken": tk is not None,
                    "taken_t": bars[tk]["t"] if tk is not None else None}
        if direction == "bear" and bars[i]["h"] == max(x["h"] for x in win) and bars[i]["h"] < zone_edge:
            lvl = bars[i]["h"]; tk = _taken(bars, "BSL", lvl, i + k)
            return {"t": bars[i]["t"], "level": lvl, "dir": "bear", "taken": tk is not None,
                    "taken_t": bars[tk]["t"] if tk is not None else None}
    return None


# ------------------------------------------------------------------ model utama
def model(bars, atr_value, k=3, eq_tolerance=0.0, k_int=2, k_ext=5, disp_atr=0.8, strong_disp_atr=1.2):
    ext = structure(bars, k_ext); itl = structure(bars, k_int)
    price = bars[-1]["c"] if bars else 0
    dr = dealing_range(bars, ext, price)
    pools = liquidity_pools(bars, k, eq_tolerance, ext["highs"], ext["lows"])
    ext_hi_conf = sorted((i + k_ext, i) for i in ext["highs"]); ext_lo_conf = sorted((i + k_ext, i) for i in ext["lows"])

    def last_ext(conf, j):
        r = None
        for c, i in conf:
            if c <= j: r = i
            else: break
        return r

    sweeps, sequences = [], {}
    for p in pools:
        sw = _find_sweep(bars, p)
        if not sw: continue
        sweeps.append(sw)
        i, direction = sw["index"], sw["dir"]
        for j in range(i, min(len(bars), i + MSS_WINDOW)):
            if not _body_ok(bars[j], direction, atr_value, disp_atr): continue
            prior = bars[max(i - 8, 0):j]
            if not prior: continue
            cisd = max(x["h"] for x in prior) if direction == "bull" else min(x["l"] for x in prior)
            if not (bars[j]["c"] > cisd if direction == "bull" else bars[j]["c"] < cisd): continue
            xi = last_ext(ext_hi_conf if direction == "bull" else ext_lo_conf, j)
            mss = xi is not None and (bars[j]["c"] > bars[xi]["h"] if direction == "bull" else bars[j]["c"] < bars[xi]["l"])
            level = (bars[xi]["h"] if direction == "bull" else bars[xi]["l"]) if mss else cisd
            ev = {"t": bars[j]["t"], "from_t": bars[xi]["t"] if mss else sw["t"], "type": "MSS" if mss else "CISD",
                  "dir": direction, "level": level, "sweep_id": sw["id"], "displacement": True, "index": j,
                  "strong": _body_ok(bars[j], direction, atr_value, strong_disp_atr)}
            meta = {"context": True, "sweep_id": sw["id"], "event_t": ev["t"]}
            fz = _fvg_in_leg(bars, i, j, direction)
            if fz: fz.update(meta, kind="FVG"); fz = _state(bars, fz, "FVG")
            q = _order_block(bars, i, j, direction)
            ob = _state(bars, {"dir": direction, "t": bars[q]["t"], "top": bars[q]["h"], "bottom": bars[q]["l"],
                               "created": j, "kind": "OB", **meta}, "OB")
            # range kaki setup: sweep extreme -> extreme setelah displacement
            if direction == "bull":
                lo, hi = sw["price"], max(b["h"] for b in bars[j:])
            else:
                hi, lo = sw["price"], min(b["l"] for b in bars[j:])
            leg = {"lo": lo, "hi": hi, "eq": (lo + hi) / 2,
                   "ote_bottom": hi - 0.79 * (hi - lo) if direction == "bull" else lo + 0.62 * (hi - lo),
                   "ote_top": hi - 0.62 * (hi - lo) if direction == "bull" else lo + 0.79 * (hi - lo)}
            poi = [z for z in (fz, ob) if z]
            edge = max(z["top"] for z in poi) if direction == "bull" else min(z["bottom"] for z in poi)
            idm = _inducement(bars, j, direction, edge)
            inv = next((n for n in range(j + 1, len(bars)) if (bars[n]["c"] < sw["price"] if direction == "bull" else bars[n]["c"] > sw["price"])), None)
            seq = {"id": sw["id"], "dir": direction, "sweep": sw, "event": ev, "fvg": fz, "ob": ob, "leg": leg,
                   "inducement": idm, "invalid": inv is not None}
            key = (ev["t"], direction)                    # satu MSS = satu setup (sweep terakhir dipakai)
            if key not in sequences or sequences[key]["sweep"]["index"] < i: sequences[key] = seq
            break

    seqs = sorted(sequences.values(), key=lambda s: s["event"]["index"])
    fvgs = [s["fvg"] for s in seqs if s["fvg"]]; obs = [s["ob"] for s in seqs]
    active = lambda z: z["status"] in ("fresh", "tapped")
    used = {s["id"] for s in seqs}
    shown = [s for s in sweeps if s["id"] in used]
    events = sorted(ext["events"][-12:] + [s["event"] for s in seqs][-10:], key=lambda e: e["index"])
    ints = [dict(e, type="i" + e["type"]) for e in itl["events"][-10:]]
    return {"trend": ext["trend"], "internal_trend": itl["trend"], "dealing_range": dr,
            "liquidity": pools, "sweeps": sorted(shown, key=lambda s: s["index"])[-12:],
            "events": events[-20:], "internal_events": ints,
            "fvg": [z for z in fvgs if active(z)][-6:], "fvg_mit": [z for z in fvgs if not active(z)][-8:],
            "order_blocks": [z for z in obs if active(z)][-6:], "ob_mitigated": [z for z in obs if not active(z)][-8:],
            "sequences": seqs[-10:]}
