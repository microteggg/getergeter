"""Smart Money Concepts model for the Market Monitor dashboard.

The visual structure and order-block behaviour are independently implemented from
public SMC rules, with LuxAlgo's "Smart Money Concepts" indicator used as a
behavioural reference. Reference: © LuxAlgo, CC BY-NC-SA 4.0,
https://creativecommons.org/licenses/by-nc-sa/4.0/. This Python implementation
is rewritten for the dashboard and keeps its own liquidity-first sequence and
richer order-block mitigation states.

Pipeline:
    internal/swing pivots -> BOS/CHoCH -> internal/swing OB
    EQH/EQL -> sweep -> displacement -> contextual FVG/OB -> setup sequence
"""

_EPS = 1e-12


def _at(values, i, fallback=0.0):
    """Return a usable scalar from a per-bar series or a scalar."""
    if isinstance(values, (int, float)):
        return float(values)
    if values and 0 <= i < len(values) and values[i] is not None:
        return float(values[i])
    if values:
        for j in range(min(i, len(values) - 1), -1, -1):
            if values[j] is not None:
                return float(values[j])
    return float(fallback or 0.0)


def swings(bars, k=3):
    """Confirmed symmetric fractal pivots; ties select the first bar."""
    k = max(1, int(k))
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        window = bars[i - k:i + k + 1]
        high_values = [x["h"] for x in window]
        low_values = [x["l"] for x in window]
        if high_values.index(max(high_values)) == k:
            highs.append(i)
        if low_values.index(min(low_values)) == k:
            lows.append(i)
    return highs, lows


def _wick_confluence(bar, direction):
    """LuxAlgo-style wick confluence used only for internal breaks."""
    upper = max(0.0, bar["h"] - max(bar["o"], bar["c"]))
    lower = max(0.0, min(bar["o"], bar["c"]) - bar["l"])
    return upper > lower if direction == "bull" else upper < lower


def _displacement(bars, i, direction, atr14, body_mult=0.8, body_ratio=0.60):
    bar = bars[i]
    body = abs(bar["c"] - bar["o"])
    candle_range = max(bar["h"] - bar["l"], _EPS)
    directional = bar["c"] > bar["o"] if direction == "bull" else bar["c"] < bar["o"]
    return directional and body / candle_range >= body_ratio and body >= body_mult * max(_at(atr14, i), _EPS)


def _structure_events(bars, k, scope, atr14, displacement_atr, displacement_body_ratio,
                      confluence=False):
    """Stream confirmed pivots and emit one break per current high/low pivot."""
    high_ids, low_ids = swings(bars, k)
    confirmations = {}
    for i in high_ids:
        confirmations.setdefault(i + k, []).append(("high", i))
    for i in low_ids:
        confirmations.setdefault(i + k, []).append(("low", i))

    current = {"high": None, "low": None}
    crossed = {"high": False, "low": False}
    trend = None
    events = []
    for i, bar in enumerate(bars):
        for side, pivot_index in confirmations.get(i, []):
            current[side] = pivot_index
            crossed[side] = False
        if i == 0:
            continue

        high_index = current["high"]
        if high_index is not None and not crossed["high"]:
            level = bars[high_index]["h"]
            crossed_now = bars[i - 1]["c"] <= level < bar["c"]
            wick_ok = not confluence or _wick_confluence(bar, "bull")
            if crossed_now and wick_ok:
                event_type = "CHoCH" if trend == "bear" else "BOS"
                events.append({
                    "id": f"{scope}:bull:{bars[high_index]['t']}:{bar['t']}",
                    "t": bar["t"], "from_t": bars[high_index]["t"],
                    "type": event_type, "label": ("i" if scope == "internal" else "") + event_type,
                    "dir": "bull", "level": level, "scope": scope,
                    "index": i, "pivot_index": high_index,
                    "displacement": _displacement(bars, i, "bull", atr14, displacement_atr, displacement_body_ratio),
                    "confluence": wick_ok,
                })
                crossed["high"] = True
                trend = "bull"

        low_index = current["low"]
        if low_index is not None and not crossed["low"]:
            level = bars[low_index]["l"]
            crossed_now = bars[i - 1]["c"] >= level > bar["c"]
            wick_ok = not confluence or _wick_confluence(bar, "bear")
            if crossed_now and wick_ok:
                event_type = "CHoCH" if trend == "bull" else "BOS"
                events.append({
                    "id": f"{scope}:bear:{bars[low_index]['t']}:{bar['t']}",
                    "t": bar["t"], "from_t": bars[low_index]["t"],
                    "type": event_type, "label": ("i" if scope == "internal" else "") + event_type,
                    "dir": "bear", "level": level, "scope": scope,
                    "index": i, "pivot_index": low_index,
                    "displacement": _displacement(bars, i, "bear", atr14, displacement_atr, displacement_body_ratio),
                    "confluence": wick_ok,
                })
                crossed["low"] = True
                trend = "bear"
    return events, trend, high_ids, low_ids


def _liquidity_pools(bars, high_ids, low_ids, atr200, threshold=0.10):
    """Cluster consecutive swing pivots into BSL/SSL and label EQH/EQL."""
    pools = []
    for side, pivot_ids, key, label in (
        ("BSL", high_ids, "h", "EQH"),
        ("SSL", low_ids, "l", "EQL"),
    ):
        side_pools = []
        for i in pivot_ids:
            price = bars[i][key]
            tolerance = max(_at(atr200, i) * threshold, _EPS)
            previous = side_pools[-1] if side_pools else None
            if previous and abs(previous["level"] - price) <= max(previous["tolerance"], tolerance):
                previous["level"] = (previous["level"] * previous["touches"] + price) / (previous["touches"] + 1)
                previous["touches"] += 1
                previous["last_t"] = bars[i]["t"]
                previous["last_index"] = i
                previous["label"] = label
                previous["tolerance"] = max(previous["tolerance"], tolerance)
            else:
                side_pools.append({
                    "id": f"{side}:{bars[i]['t']}", "side": side, "level": price,
                    "t": bars[i]["t"], "last_t": bars[i]["t"], "touches": 1,
                    "index": i, "last_index": i, "label": None, "tolerance": tolerance,
                })
        pools.extend(side_pools)
    return sorted(pools, key=lambda x: x["last_index"])


def _sweeps(bars, pools, confirmation_bars):
    out = []
    for pool in pools:
        start = min(len(bars), pool["last_index"] + max(1, confirmation_bars))
        for i in range(start, len(bars)):
            bar = bars[i]
            if pool["side"] == "SSL":
                swept = bar["l"] < pool["level"] <= bar["c"]
                direction, price = "bull", bar["l"]
            else:
                swept = bar["h"] > pool["level"] >= bar["c"]
                direction, price = "bear", bar["h"]
            if swept:
                out.append({
                    "id": f"SW:{pool['id']}:{bar['t']}", "t": bar["t"],
                    "from_t": pool["last_t"], "dir": direction, "side": pool["side"],
                    "level": pool["level"], "price": price, "index": i,
                    "pool_touches": pool["touches"], "pool_label": pool.get("label"),
                })
                break
    return out


def _order_block_for_event(bars, event, atr200, volatility_mult):
    """Select the normalized leg extreme between pivot and structure break."""
    start = max(0, event["pivot_index"])
    stop = max(start + 1, event["index"])
    candidates = []
    for i in range(start, stop):
        bar = bars[i]
        volatility = max(_at(atr200, i), _EPS)
        high_volatility = (bar["h"] - bar["l"]) >= volatility_mult * volatility
        parsed_high = bar["l"] if high_volatility else bar["h"]
        parsed_low = bar["h"] if high_volatility else bar["l"]
        score = parsed_low if event["dir"] == "bull" else parsed_high
        candidates.append((score, i, high_volatility))
    if not candidates:
        return None
    _, selected, normalized = (min(candidates, key=lambda x: x[0]) if event["dir"] == "bull"
                               else max(candidates, key=lambda x: x[0]))
    bar = bars[selected]
    return {
        "id": f"OB:{event['scope']}:{event['dir']}:{bar['t']}",
        "dir": event["dir"], "scope": event["scope"], "kind": "OB",
        "t": bar["t"], "top": max(bar["h"], bar["l"]), "bottom": min(bar["h"], bar["l"]),
        "mid": (bar["h"] + bar["l"]) / 2, "created": event["index"],
        "event_t": event["t"], "event_type": event["type"],
        "pivot_t": event["from_t"], "volatility_normalized": normalized,
    }


def _body_percent(bar):
    return abs(bar["c"] - bar["o"]) / max(abs(bar["o"]), _EPS)


def _fair_value_gaps(bars, atr200, mode="HYBRID", atr_threshold=0.10,
                     auto_multiplier=2.0, min_body_ratio=0.50):
    """Three-candle FVG with ATR, automatic, or combined visual filtering."""
    mode = str(mode or "HYBRID").upper()
    body_percent = [_body_percent(bar) for bar in bars]
    cumulative = 0.0
    out = []
    for i, value in enumerate(body_percent):
        if i < 2:
            cumulative += value
            continue
        first, middle, current = bars[i - 2], bars[i - 1], bars[i]
        prior_mean = cumulative / max(i, 1)
        auto_threshold_value = prior_mean * auto_multiplier
        middle_range = max(middle["h"] - middle["l"], _EPS)
        middle_body_ratio = abs(middle["c"] - middle["o"]) / middle_range
        direction = None
        if current["l"] > first["h"]:
            direction, top, bottom = "bull", current["l"], first["h"]
            close_break = middle["c"] > first["h"]
        elif current["h"] < first["l"]:
            direction, top, bottom = "bear", first["l"], current["h"]
            close_break = middle["c"] < first["l"]
        else:
            cumulative += value
            continue

        gap = top - bottom
        atr_pass = gap >= atr_threshold * max(_at(atr200, i), _EPS) and middle_body_ratio >= min_body_ratio
        auto_pass = close_break and body_percent[i - 1] > auto_threshold_value
        accepted = atr_pass if mode == "ATR" else auto_pass if mode == "AUTO" else atr_pass or auto_pass
        if accepted:
            filter_mode = "ATR+AUTO" if atr_pass and auto_pass else "ATR" if atr_pass else "AUTO"
            out.append({
                "id": f"FVG:{direction}:{middle['t']}", "dir": direction, "kind": "FVG",
                "t": middle["t"], "top": top, "bottom": bottom, "mid": (top + bottom) / 2,
                "created": i, "filter_mode": filter_mode, "context": False,
                "gap_atr200": gap / max(_at(atr200, i), _EPS),
            })
        cumulative += value
    return out


def _ob_state(zone, bars):
    touched = ce = mitigated = invalid = None
    for i in range(zone["created"] + 1, len(bars)):
        bar = bars[i]
        overlap = bar["l"] <= zone["top"] and bar["h"] >= zone["bottom"]
        if overlap and touched is None:
            touched = i
        if ce is None and ((zone["dir"] == "bull" and bar["l"] <= zone["mid"]) or
                           (zone["dir"] == "bear" and bar["h"] >= zone["mid"])):
            ce = i
        wick_through = (zone["dir"] == "bull" and bar["l"] < zone["bottom"]) or (
            zone["dir"] == "bear" and bar["h"] > zone["top"])
        close_through = (zone["dir"] == "bull" and bar["c"] < zone["bottom"]) or (
            zone["dir"] == "bear" and bar["c"] > zone["top"])
        if wick_through and mitigated is None:
            mitigated = i
        if close_through:
            invalid = i
            if mitigated is None:
                mitigated = i
            break
    status = "invalid" if invalid is not None else "mitigated" if mitigated is not None else "CE" if ce is not None else "tapped" if touched is not None else "fresh"
    result = dict(zone, status=status,
                  touch_t=bars[touched]["t"] if touched is not None else None,
                  ce_t=bars[ce]["t"] if ce is not None else None,
                  mitigated_t=bars[mitigated]["t"] if mitigated is not None else None,
                  invalid_t=bars[invalid]["t"] if invalid is not None else None)
    end = invalid if invalid is not None else mitigated
    if end is not None:
        result["end_t"] = bars[end]["t"]
    return result, end is None


def _fvg_state(zone, bars):
    touched = filled = None
    for i in range(zone["created"] + 1, len(bars)):
        bar = bars[i]
        overlap = bar["l"] <= zone["top"] and bar["h"] >= zone["bottom"]
        if overlap and touched is None:
            touched = i
        full_fill = (zone["dir"] == "bull" and bar["l"] <= zone["bottom"]) or (
            zone["dir"] == "bear" and bar["h"] >= zone["top"])
        if full_fill:
            filled = i
            break
    result = dict(zone, status="filled" if filled is not None else "tapped" if touched is not None else "fresh",
                  touch_t=bars[touched]["t"] if touched is not None else None,
                  filled_t=bars[filled]["t"] if filled is not None else None)
    if filled is not None:
        result["end_t"] = bars[filled]["t"]
    return result, filled is None


def _dealing_range(bars, swing_highs, swing_lows, trend):
    if not swing_highs or not swing_lows:
        return None, [], []
    hi_i, lo_i = swing_highs[-1], swing_lows[-1]
    top, bottom = bars[hi_i]["h"], bars[lo_i]["l"]
    if top <= bottom:
        top, bottom = max(top, bottom), min(top, bottom)
    width = top - bottom
    if width <= _EPS:
        return None, [], []
    start_t = min(bars[hi_i]["t"], bars[lo_i]["t"])
    zones = [
        {"name": "Extreme Premium", "kind": "premium", "t": start_t, "top": top, "bottom": bottom + 0.95 * width},
        {"name": "Equilibrium", "kind": "equilibrium", "t": start_t, "top": bottom + 0.525 * width, "bottom": bottom + 0.475 * width},
        {"name": "Extreme Discount", "kind": "discount", "t": start_t, "top": bottom + 0.05 * width, "bottom": bottom},
    ]
    strong_weak = [
        {"t": bars[hi_i]["t"], "level": top, "side": "high", "strength": "weak" if trend == "bull" else "strong" if trend == "bear" else "neutral"},
        {"t": bars[lo_i]["t"], "level": bottom, "side": "low", "strength": "strong" if trend == "bull" else "weak" if trend == "bear" else "neutral"},
    ]
    return {"top": top, "bottom": bottom, "mid": (top + bottom) / 2, "t": start_t, "width": width}, zones, strong_weak


def premium_discount_at(price, dealing_range):
    if not dealing_range:
        return None
    ratio = (price - dealing_range["bottom"]) / max(dealing_range["width"], _EPS)
    if ratio <= 0.05:
        return "extreme_discount"
    if ratio < 0.475:
        return "discount"
    if ratio <= 0.525:
        return "equilibrium"
    if ratio < 0.95:
        return "premium"
    return "extreme_premium"


def model(bars, atr14, *legacy_args, atr200=None, internal_len=4, swing_len=24,
          eq_threshold=0.10, displacement_atr=0.80, displacement_body_ratio=0.60,
          fvg_mode="HYBRID", fvg_atr_threshold=0.10, fvg_auto_multiplier=2.0,
          fvg_min_body_ratio=0.50, ob_volatility_atr=2.0,
          internal_confluence=True, sequence_window=12,
          max_visual_zones=12):
    """Return a schema-compatible SMC snapshot for one timeframe.

    ``atr14`` and ``atr200`` may be scalars or per-bar series. Legacy four- and
    eight-positional-argument calls are accepted to prevent the TypeError seen
    in the previous workflow, while ``analysis.py`` uses explicit keywords.
    """
    if not bars:
        return {"trend": None, "internal_trend": None, "swing_trend": None,
                "liquidity": [], "sweeps": [], "events": [], "fvg": [],
                "fvg_mit": [], "order_blocks": [], "ob_mitigated": [],
                "sequences": [], "dealing_range": None, "pd_zones": [],
                "strong_weak": []}
    if legacy_args:
        if len(legacy_args) not in (2, 6):
            raise TypeError("legacy model() expects 4 or 8 total positional arguments")
        legacy_k, legacy_eq = legacy_args[:2]
        atr200 = atr14
        eq_threshold = float(legacy_eq)
        if len(legacy_args) == 6:
            internal_len = legacy_args[2]
            swing_len = legacy_args[3]
            displacement_atr = legacy_args[4]
            # legacy strong-displacement value is no longer a structure-body ratio
        else:
            internal_len = max(2, int(legacy_k))
            swing_len = max(internal_len + 1, int(legacy_k) * 4)
    atr200 = atr200 if atr200 is not None else atr14
    internal_len = max(1, int(internal_len))
    swing_len = max(internal_len + 1, int(swing_len))

    internal_events, internal_trend, _, _ = _structure_events(
        bars, internal_len, "internal", atr14, displacement_atr,
        displacement_body_ratio, internal_confluence)
    swing_events, swing_trend, swing_highs, swing_lows = _structure_events(
        bars, swing_len, "swing", atr14, displacement_atr,
        displacement_body_ratio, False)
    events = sorted(internal_events + swing_events, key=lambda x: (x["index"], x["scope"] == "swing"))

    # One OB is produced for each structure break and kept separate by scope.
    order_blocks = []
    seen_ob = set()
    for event in events:
        zone = _order_block_for_event(bars, event, atr200, ob_volatility_atr)
        if zone and zone["id"] not in seen_ob:
            seen_ob.add(zone["id"])
            order_blocks.append(zone)

    fvgs = _fair_value_gaps(bars, atr200, fvg_mode, fvg_atr_threshold,
                            fvg_auto_multiplier, fvg_min_body_ratio)
    for zone in fvgs:
        candidates = [e for e in events if e["dir"] == zone["dir"] and e["displacement"]
                      and abs(e["index"] - zone["created"]) <= 2]
        if candidates:
            event = min(candidates, key=lambda e: abs(e["index"] - zone["created"]))
            zone["context"] = True
            zone["event_t"] = event["t"]
            zone["event_scope"] = event["scope"]

    pools = _liquidity_pools(bars, swing_highs, swing_lows, atr200, eq_threshold)
    sweeps = _sweeps(bars, pools, swing_len)

    active_ob, mitigated_ob = [], []
    for zone in order_blocks:
        state, active = _ob_state(zone, bars)
        (active_ob if active else mitigated_ob).append(state)
    active_fvg, mitigated_fvg = [], []
    for zone in fvgs:
        state, active = _fvg_state(zone, bars)
        (active_fvg if active else mitigated_fvg).append(state)

    sequences = []
    for event in events:
        if not event["displacement"]:
            continue
        prior_sweeps = [s for s in sweeps if s["dir"] == event["dir"] and
                        0 <= event["index"] - s["index"] <= sequence_window]
        if not prior_sweeps:
            continue
        sweep = max(prior_sweeps, key=lambda s: s["index"])
        event_obs = [z for z in order_blocks if z.get("event_t") == event["t"] and z["scope"] == event["scope"]]
        event_fvgs = [z for z in fvgs if z.get("event_t") == event["t"]]
        ob = event_obs[-1] if event_obs else None
        fvg = min(event_fvgs, key=lambda z: abs(z["created"] - event["index"])) if event_fvgs else None
        sequences.append({
            "id": f"SEQ:{sweep['id']}:{event['id']}", "dir": event["dir"],
            "scope": event["scope"], "sweep": sweep, "event": event,
            "ob": ob, "fvg": fvg,
        })

    trend = swing_trend or internal_trend
    dealing_range, pd_zones, strong_weak = _dealing_range(bars, swing_highs, swing_lows, swing_trend)
    limit = max(1, int(max_visual_zones))
    return {
        "trend": trend, "internal_trend": internal_trend, "swing_trend": swing_trend,
        "liquidity": pools[-30:], "sweeps": sweeps[-30:], "events": events[-40:],
        "fvg": active_fvg[-limit:], "fvg_mit": mitigated_fvg[-limit:],
        "order_blocks": active_ob[-limit:], "ob_mitigated": mitigated_ob[-limit:],
        "sequences": sequences[-15:], "dealing_range": dealing_range,
        "pd_zones": pd_zones, "strong_weak": strong_weak,
    }
