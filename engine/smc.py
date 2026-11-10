"""SMC canonical engine v6 (satu-satunya definisi struktur/liquidity/POI).

Definisi formal lengkap: LOGIC_SPEC.md. Pine Script nanti WAJIB mengikuti file itu.

Pipeline (semua hanya memakai candle tertutup, tanpa lookahead):
  pivot terkonfirmasi (internal & swing)
    -> BOS/CHoCH (close menembus pivot aktif)
    -> liquidity pool BSL/SSL (EQH/EQL) -> sweep (wick lewat, close kembali) / broken (close lewat)
    -> displacement (candle impulsif di leg sweep..break)
    -> sequence: sweep -> displacement -> BOS/MSS
    -> POI: Order Block (last opposite candle) + FVG di leg yang sama
"""

_EPS = 1e-12


def _at(values, i, fallback=0.0):
    if isinstance(values, (int, float)):
        return float(values)
    if values and 0 <= i < len(values) and values[i] is not None:
        return float(values[i])
    if values:
        for j in range(min(i, len(values) - 1), -1, -1):
            if values[j] is not None:
                return float(values[j])
    return float(fallback or 0.0)


# ---------------------------------------------------------------- pivots & structure
def swings(bars, k):
    """Pivot fractal simetris k kiri / k kanan. Seri: bar pertama yang menang.
    Pivot di index i baru DIKETAHUI pada bar i + k."""
    k = max(1, int(k))
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        window = bars[i - k:i + k + 1]
        hv = [x["h"] for x in window]
        lv = [x["l"] for x in window]
        if hv.index(max(hv)) == k:
            highs.append(i)
        if lv.index(min(lv)) == k:
            lows.append(i)
    return highs, lows


def is_displacement(bar, direction, atr_value, mult, body_ratio):
    body = abs(bar["c"] - bar["o"])
    rng = max(bar["h"] - bar["l"], _EPS)
    directional = bar["c"] > bar["o"] if direction == "bull" else bar["c"] < bar["o"]
    return directional and body / rng >= body_ratio and body >= mult * max(atr_value, _EPS)


def structure_events(bars, k, scope, atr14, disp_mult, disp_ratio):
    """Satu break per pivot. BOS = searah trend, CHoCH = berlawanan trend sebelumnya."""
    high_ids, low_ids = swings(bars, k)
    confirm = {}
    for i in high_ids:
        confirm.setdefault(i + k, []).append(("high", i))
    for i in low_ids:
        confirm.setdefault(i + k, []).append(("low", i))
    current = {"high": None, "low": None}
    crossed = {"high": True, "low": True}
    trend, events = None, []
    for i, bar in enumerate(bars):
        for side, p in confirm.get(i, []):
            current[side], crossed[side] = p, False
        if i == 0:
            continue
        for side, direction in (("high", "bull"), ("low", "bear")):
            p = current[side]
            if p is None or crossed[side]:
                continue
            level = bars[p]["h"] if side == "high" else bars[p]["l"]
            prev_c, c = bars[i - 1]["c"], bar["c"]
            hit = prev_c <= level < c if direction == "bull" else prev_c >= level > c
            if not hit:
                continue
            opposite = "bear" if direction == "bull" else "bull"
            etype = "CHoCH" if trend == opposite else "BOS"
            events.append({
                "id": f"{scope}:{direction}:{bars[p]['t']}:{bar['t']}",
                "t": bar["t"], "from_t": bars[p]["t"], "type": etype,
                "label": ("i" if scope == "internal" else "") + etype,
                "dir": direction, "level": level, "scope": scope,
                "index": i, "pivot_index": p,
                "displacement": is_displacement(bar, direction, _at(atr14, i), disp_mult, disp_ratio),
            })
            crossed[side] = True
            trend = direction
    return events, trend, high_ids, low_ids


# ---------------------------------------------------------------- liquidity
def liquidity(bars, pivot_sets, atr200, threshold):
    """Streaming pool BSL/SSL dari pivot internal+swing.

    pivot_sets: [(scope, k, highs, lows), ...] – swing diproses setelah internal sehingga pool
    yang sama di-upgrade ke scope 'swing' (tidak dobel).
    Level pool = ekstrem cluster (BSL = high tertinggi, SSL = low terendah).
    Per bar: close lewat level -> 'broken' (bukan sweep); wick lewat & close kembali -> 'swept'.
    """
    adds = {}
    for order, (scope, k, highs, lows) in enumerate(pivot_sets):
        for i in highs:
            adds.setdefault(i + k, []).append((order, scope, "BSL", i))
        for i in lows:
            adds.setdefault(i + k, []).append((order, scope, "SSL", i))
    active, done, sweeps = [], [], []
    for i, bar in enumerate(bars):
        keep = []
        for p in active:
            lvl = p["level"]
            if p["side"] == "BSL":
                broken, swept, price, direction = bar["c"] > lvl, bar["h"] > lvl, bar["h"], "bear"
            else:
                broken, swept, price, direction = bar["c"] < lvl, bar["l"] < lvl, bar["l"], "bull"
            if broken or swept:
                p.update(status="broken" if broken else "swept", end_t=bar["t"], end_i=i)
                done.append(p)
                if swept and not broken:
                    sweeps.append({
                        "id": f"SW:{p['id']}:{bar['t']}", "t": bar["t"], "from_t": p["last_t"],
                        "dir": direction, "side": p["side"], "scope": p["scope"],
                        "level": lvl, "price": price, "index": i,
                        "pool_touches": p["touches"], "pool_label": p["label"],
                    })
                continue
            keep.append(p)
        active = keep
        for _, scope, side, pi in sorted(adds.get(i, [])):
            price = bars[pi]["h"] if side == "BSL" else bars[pi]["l"]
            tol = max(_at(atr200, pi) * threshold, _EPS)
            match = None
            for p in reversed(active):
                if p["side"] == side and (p["last_index"] == pi or abs(p["level"] - price) <= max(p["tol"], tol)):
                    match = p
                    break
            if match:
                if match["last_index"] != pi:
                    match["touches"] += 1
                    match["label"] = "EQH" if side == "BSL" else "EQL"
                match["level"] = max(match["level"], price) if side == "BSL" else min(match["level"], price)
                if match["last_index"] != pi:
                    match["hist"].append((i, match["level"]))
                match["last_t"], match["last_index"] = bars[pi]["t"], pi
                match["tol"] = max(match["tol"], tol)
                if scope == "swing":
                    match["scope"] = "swing"
            else:
                active.append({
                    "id": f"{side}:{bars[pi]['t']}", "side": side, "scope": scope, "level": price,
                    "t": bars[pi]["t"], "last_t": bars[pi]["t"], "last_index": pi,
                    "touches": 1, "label": None, "tol": tol, "status": "active",
                    "start": i, "end_i": None, "hist": [(i, price)],
                })
    return active, done, sweeps


# ---------------------------------------------------------------- POI
def order_block(bars, event, lookback):
    """OB = candle berlawanan terakhir sebelum impuls yang menghasilkan break.
    Bull: titik terendah leg (pivot..break) -> mundur maks `lookback` bar mencari candle bearish."""
    lo, hi = event["pivot_index"], event["index"]
    if hi <= lo:
        return None
    bull = event["dir"] == "bull"
    leg = range(lo, hi)
    origin = (min(leg, key=lambda j: (bars[j]["l"], -j)) if bull
              else max(leg, key=lambda j: (bars[j]["h"], j)))
    sel = origin
    for j in range(origin, max(-1, origin - lookback - 1), -1):
        b = bars[j]
        if (bull and b["c"] < b["o"]) or (not bull and b["c"] > b["o"]):
            sel = j
            break
    b = bars[sel]
    return {
        "id": f"OB:{event['scope']}:{event['dir']}:{b['t']}", "dir": event["dir"],
        "scope": event["scope"], "kind": "OB", "t": b["t"], "top": b["h"], "bottom": b["l"],
        "mid": (b["h"] + b["l"]) / 2, "created": event["index"], "event_id": event["id"],
        "event_t": event["t"], "event_type": event["type"], "context": False,
    }


def _body_pct(bar):
    return abs(bar["c"] - bar["o"]) / max(abs(bar["o"]), _EPS)


def bar_step(bars):
    """Durasi candle normal (median selisih waktu) – untuk mendeteksi celah sesi/weekend."""
    diffs = sorted(bars[i]["t"] - bars[i - 1]["t"] for i in range(1, len(bars)))
    return diffs[len(diffs) // 2] if diffs else 0


def fair_value_gaps(bars, atr200, mode, atr_threshold, auto_multiplier, min_body_ratio, skip_session_gap=True):
    """FVG 3 candle. Bila 3 candle itu melintasi celah sesi/weekend (durasi > 3 candle normal),
    FVG diabaikan karena gap-nya berasal dari pasar tutup, bukan imbalance."""
    mode = str(mode or "HYBRID").upper()
    step = bar_step(bars)
    body = [_body_pct(b) for b in bars]
    cumulative, out = 0.0, []
    for i, value in enumerate(body):
        if i < 2:
            cumulative += value
            continue
        first, middle, cur = bars[i - 2], bars[i - 1], bars[i]
        auto_threshold = cumulative / i * auto_multiplier
        mid_ratio = abs(middle["c"] - middle["o"]) / max(middle["h"] - middle["l"], _EPS)
        direction = None
        if skip_session_gap and step and cur["t"] - first["t"] > 3 * step:
            cumulative += value
            continue
        if cur["l"] > first["h"]:
            direction, top, bottom, close_break = "bull", cur["l"], first["h"], middle["c"] > first["h"]
        elif cur["h"] < first["l"]:
            direction, top, bottom, close_break = "bear", first["l"], cur["h"], middle["c"] < first["l"]
        if direction:
            gap = top - bottom
            atr_pass = gap >= atr_threshold * max(_at(atr200, i), _EPS) and mid_ratio >= min_body_ratio
            auto_pass = close_break and body[i - 1] > auto_threshold
            ok = atr_pass if mode == "ATR" else auto_pass if mode == "AUTO" else (atr_pass or auto_pass)
            if ok:
                out.append({
                    "id": f"FVG:{direction}:{middle['t']}", "dir": direction, "kind": "FVG",
                    "t": middle["t"], "top": top, "bottom": bottom, "mid": (top + bottom) / 2,
                    "created": i, "context": False,
                    "filter_mode": "ATR+AUTO" if atr_pass and auto_pass else "ATR" if atr_pass else "AUTO",
                    "gap_atr200": gap / max(_at(atr200, i), _EPS),
                })
        cumulative += value
    return out


def ob_state(zone, bars):
    """Aktif sampai CLOSE menembus sisi jauh (invalid). Status: fresh/tapped/CE/deep."""
    touched = ce = deep = invalid = None
    bull = zone["dir"] == "bull"
    for i in range(zone["created"] + 1, len(bars)):
        b = bars[i]
        if touched is None and b["l"] <= zone["top"] and b["h"] >= zone["bottom"]:
            touched = i
        if ce is None and ((bull and b["l"] <= zone["mid"]) or (not bull and b["h"] >= zone["mid"])):
            ce = i
        if deep is None and ((bull and b["l"] < zone["bottom"]) or (not bull and b["h"] > zone["top"])):
            deep = i
        if (bull and b["c"] < zone["bottom"]) or (not bull and b["c"] > zone["top"]):
            invalid = i
            break
    status = ("invalid" if invalid is not None else "deep" if deep is not None else
              "CE" if ce is not None else "tapped" if touched is not None else "fresh")
    out = dict(zone, status=status, touch_t=bars[touched]["t"] if touched is not None else None, end_i=invalid)
    if invalid is not None:
        out["end_t"] = bars[invalid]["t"]
    return out, invalid is None


def fvg_state(zone, bars):
    """Aktif sampai gap terisi penuh (wick mencapai sisi jauh)."""
    touched = filled = None
    bull = zone["dir"] == "bull"
    for i in range(zone["created"] + 1, len(bars)):
        b = bars[i]
        if touched is None and b["l"] <= zone["top"] and b["h"] >= zone["bottom"]:
            touched = i
        if (bull and b["l"] <= zone["bottom"]) or (not bull and b["h"] >= zone["top"]):
            filled = i
            break
    out = dict(zone, status="filled" if filled is not None else "tapped" if touched is not None else "fresh",
               touch_t=bars[touched]["t"] if touched is not None else None, end_i=filled)
    if filled is not None:
        out["end_t"] = bars[filled]["t"]
    return out, filled is None


# ---------------------------------------------------------------- dealing range
def dealing_range(bars, swing_highs, swing_lows, trend):
    if not swing_highs or not swing_lows:
        return None, [], []
    hi_i, lo_i = swing_highs[-1], swing_lows[-1]
    top, bottom = max(bars[hi_i]["h"], bars[lo_i]["l"]), min(bars[hi_i]["h"], bars[lo_i]["l"])
    width = top - bottom
    if width <= _EPS:
        return None, [], []
    t0 = min(bars[hi_i]["t"], bars[lo_i]["t"])
    zones = [
        {"name": "Extreme Premium", "kind": "premium", "t": t0, "top": top, "bottom": bottom + 0.95 * width},
        {"name": "Equilibrium", "kind": "equilibrium", "t": t0, "top": bottom + 0.525 * width, "bottom": bottom + 0.475 * width},
        {"name": "Extreme Discount", "kind": "discount", "t": t0, "top": bottom + 0.05 * width, "bottom": bottom},
    ]
    sw = [
        {"t": bars[hi_i]["t"], "level": bars[hi_i]["h"], "side": "high",
         "strength": "weak" if trend == "bull" else "strong" if trend == "bear" else "neutral"},
        {"t": bars[lo_i]["t"], "level": bars[lo_i]["l"], "side": "low",
         "strength": "strong" if trend == "bull" else "weak" if trend == "bear" else "neutral"},
    ]
    return {"top": top, "bottom": bottom, "mid": (top + bottom) / 2, "t": t0, "width": width}, zones, sw


def premium_discount_at(price, rng):
    if not rng:
        return None
    r = (price - rng["bottom"]) / max(rng["width"], _EPS)
    if r <= 0.05:
        return "extreme_discount"
    if r < 0.475:
        return "discount"
    if r <= 0.525:
        return "equilibrium"
    if r < 0.95:
        return "premium"
    return "extreme_premium"


# ---------------------------------------------------------------- model
def model(bars, *, atr14, atr200, internal_len, swing_len, eq_threshold, displacement_atr,
          displacement_body_ratio, fvg_mode, fvg_atr_threshold, fvg_auto_multiplier,
          fvg_min_body_ratio, ob_lookback, sequence_window, max_visual_zones=None):
    empty = {"trend": None, "internal_trend": None, "swing_trend": None, "liquidity": [],
             "sweeps": [], "events": [], "fvg": [], "fvg_mit": [], "order_blocks": [],
             "ob_mitigated": [], "sequences": [], "dealing_range": None, "pd_zones": [],
             "strong_weak": [], "liquidity_done": [], "s_highs": [], "s_lows": [], "swing_len": swing_len}
    if not bars:
        return empty
    internal_len = max(1, int(internal_len))
    swing_len = max(internal_len + 1, int(swing_len))

    i_events, i_trend, i_highs, i_lows = structure_events(
        bars, internal_len, "internal", atr14, displacement_atr, displacement_body_ratio)
    s_events, s_trend, s_highs, s_lows = structure_events(
        bars, swing_len, "swing", atr14, displacement_atr, displacement_body_ratio)
    events = sorted(i_events + s_events, key=lambda e: (e["index"], e["scope"] == "swing"))

    obs, ob_by_event, seen = [], {}, set()
    for e in events:
        z = order_block(bars, e, ob_lookback)
        if z and z["id"] not in seen:
            seen.add(z["id"])
            obs.append(z)
            ob_by_event[e["id"]] = z

    fvgs = fair_value_gaps(bars, atr200, fvg_mode, fvg_atr_threshold, fvg_auto_multiplier, fvg_min_body_ratio)

    pools, pools_done, sweeps = liquidity(
        bars, [("internal", internal_len, i_highs, i_lows), ("swing", swing_len, s_highs, s_lows)],
        atr200, eq_threshold)

    # Sequence: sweep -> displacement (leg) -> break searah. Satu sequence per sweep (break pertama).
    sequences, used = [], set()
    for e in events:
        prior = [s for s in sweeps if s["dir"] == e["dir"] and 0 <= e["index"] - s["index"] <= sequence_window]
        if not prior:
            continue
        sw = max(prior, key=lambda s: s["index"])
        if sw["id"] in used:
            continue
        disp = next((j for j in range(sw["index"], e["index"] + 1)
                     if is_displacement(bars[j], e["dir"], _at(atr14, j), displacement_atr, displacement_body_ratio)), None)
        if disp is None:
            continue
        used.add(sw["id"])
        leg_fvgs = [z for z in fvgs if z["dir"] == e["dir"] and sw["index"] <= z["created"] <= e["index"] + 2]
        fvg = leg_fvgs[-1] if leg_fvgs else None
        ob = ob_by_event.get(e["id"])
        for z in (fvg, ob):
            if z:
                z["context"] = True
        invalid_t = invalid_i = None
        for j in range(e["index"] + 1, len(bars)):
            if (e["dir"] == "bull" and bars[j]["c"] < sw["price"]) or (e["dir"] == "bear" and bars[j]["c"] > sw["price"]):
                invalid_t, invalid_i = bars[j]["t"], j
                break
        sequences.append({
            "id": f"SEQ:{sw['id']}:{e['id']}", "dir": e["dir"], "scope": e["scope"],
            "type": "MSS" if e["type"] == "CHoCH" else "BOS",
            "sweep": sw, "event": e, "displacement_t": bars[disp]["t"],
            "fvg": fvg, "ob": ob, "invalid_t": invalid_t, "invalid_i": invalid_i,
        })

    act_ob, mit_ob = [], []
    for z in obs:
        st, ok = ob_state(z, bars)
        (act_ob if ok else mit_ob).append(st)
    act_fvg, mit_fvg = [], []
    for z in fvgs:
        st, ok = fvg_state(z, bars)
        (act_fvg if ok else mit_fvg).append(st)
    state = {z["id"]: z for z in act_ob + mit_ob + act_fvg + mit_fvg}
    for s in sequences:
        s["fvg"] = state.get(s["fvg"]["id"]) if s["fvg"] else None
        s["ob"] = state.get(s["ob"]["id"]) if s["ob"] else None

    drange, pd_zones, strong_weak = dealing_range(bars, s_highs, s_lows, s_trend)
    # Riwayat LENGKAP (tidak dipotong) – dashboard menampilkan semuanya untuk cek/backtest.
    return {
        "trend": s_trend or i_trend, "internal_trend": i_trend, "swing_trend": s_trend,
        "liquidity": pools, "liquidity_done": pools_done, "sweeps": sweeps,
        "events": events, "fvg": act_fvg, "fvg_mit": mit_fvg,
        "order_blocks": act_ob, "ob_mitigated": mit_ob,
        "sequences": sequences, "dealing_range": drange, "pd_zones": pd_zones,
        "strong_weak": strong_weak, "s_highs": s_highs, "s_lows": s_lows, "swing_len": swing_len,
    }
