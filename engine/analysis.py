"""Liquidity-first top-down SMC analysis.

ATR14 is reserved for displacement, stop/risk and proximity. ATR200 is used for
structure-related tolerances: EQH/EQL, OB volatility normalization and FVG ATR
thresholds. MA/RSI remain display-only.
"""
import config
from indicators import ma, rsi, atr
from smc import model, premium_discount_at
from orderflow import flow_summary

MIN_BARS = 100
ANALYZE_BARS = 700


def _r(value, digits):
    return None if value is None else round(value, digits)


def _zl(items, digits):
    out = []
    for item in items:
        q = {k: v for k, v in item.items() if k not in ("created", "index", "pivot_index", "last_index", "tolerance")}
        for key in ("top", "bottom", "mid", "level", "price", "width", "gap_atr200"):
            if key in q and q[key] is not None:
                q[key] = _r(q[key], digits if key != "gap_atr200" else 3)
        out.append(q)
    return out


def _near(zone, price, atr14_value):
    return zone["bottom"] - 0.15 * atr14_value <= price <= zone["top"] + 0.15 * atr14_value


def _fill_atr(bars, values, max_window):
    """Use exact Wilder ATR when ready; before that, use expanding TR mean."""
    out, tr_sum, trs = [], 0.0, []
    for i, bar in enumerate(bars):
        previous_close = bars[i - 1]["c"] if i else bar["c"]
        tr = max(bar["h"] - bar["l"], abs(bar["h"] - previous_close), abs(bar["l"] - previous_close))
        trs.append(tr)
        tr_sum += tr
        if len(trs) > max_window:
            tr_sum -= trs[-max_window - 1]
        fallback = tr_sum / min(len(trs), max_window)
        out.append(values[i] if i < len(values) and values[i] is not None else fallback)
    return out


def _location_ok(direction, location):
    return ((direction == "bull" and location in ("discount", "extreme_discount")) or
            (direction == "bear" and location in ("premium", "extreme_premium")))


def analyze_tf(tf, closed, price, digits):
    internal_len, swing_len = config.structure_lengths(tf)
    need = max(20 if tf == "W" else 30 if tf == "D" else MIN_BARS, swing_len * 2 + 5)
    result = {"tf": tf, "bars": len(closed), "need": need, "enough": len(closed) >= need, "price": _r(price, digits)}
    if len(closed) < min(need, 20):
        result.update(
            signal="WAIT", trend="Data belum cukup", structure=None,
            internal_structure=None, swing_structure=None, setup=None,
            checklist=[], reason="Data belum cukup.", events=[], sweeps=[],
            liquidity=[], fvg=[], ob=[], fvg_mit=[], ob_mit=[], pd_zones=[],
            strong_weak=[], dealing_range=None, flow=None,
            atr=None, atr14=None, atr200=None, atr200_ready=False,
            series={"times": [], "ma20": [], "ma50": [], "ma200": [], "ma100": []},
        )
        return result

    bars = closed[-ANALYZE_BARS:]
    closes = [bar["c"] for bar in bars]
    ma20 = ma(closes, config.MA_FAST)
    ma50 = ma(closes, config.MA_MID)
    ma200 = ma(closes, config.MA_LONG)
    rsi14 = rsi(closes, config.RSI_LEN)
    raw_atr14 = atr(bars, config.ATR_LEN)
    raw_atr200 = atr(bars, config.STRUCTURE_ATR_LEN)
    atr14 = _fill_atr(bars, raw_atr14, config.ATR_LEN)
    atr200 = _fill_atr(bars, raw_atr200, config.STRUCTURE_ATR_LEN)
    risk_atr = atr14[-1]

    smc = model(
        bars,
        atr14=atr14,
        atr200=atr200,
        internal_len=internal_len,
        swing_len=swing_len,
        eq_threshold=config.EQUAL_LIQ_ATR,
        displacement_atr=config.DISPLACEMENT_ATR,
        displacement_body_ratio=config.DISPLACEMENT_BODY_RATIO,
        fvg_mode=config.FVG_MODE,
        fvg_atr_threshold=config.FVG_ATR_THRESHOLD,
        fvg_auto_multiplier=config.FVG_AUTO_MULTIPLIER,
        fvg_min_body_ratio=config.FVG_MIN_BODY_RATIO,
        ob_volatility_atr=config.OB_VOLATILITY_ATR,
        internal_confluence=config.INTERNAL_CONFLUENCE_FILTER,
        sequence_window=config.SEQUENCE_WINDOW,
        max_visual_zones=config.MAX_VISUAL_ZONES,
    )

    last = bars[-1]
    sequence = smc["sequences"][-1] if smc["sequences"] else None
    direction = sequence["dir"] if sequence else None

    active_ob_ids = {z["id"] for z in smc["order_blocks"]}
    active_fvg_ids = {z["id"] for z in smc["fvg"]}
    zones = []
    if sequence:
        fvg_zone, ob_zone = sequence.get("fvg"), sequence.get("ob")
        if fvg_zone and fvg_zone.get("id") in active_fvg_ids:
            zones.append(fvg_zone)
        if ob_zone and ob_zone.get("id") in active_ob_ids:
            zones.append(ob_zone)
    zone = next((item for item in zones if _near(item, price, risk_atr)), None)
    retrace = bool(zone and last["t"] > zone["t"])

    target = None
    if direction:
        candidates = [pool for pool in smc["liquidity"] if
                      (pool["side"] == "BSL" and direction == "bull" and pool["level"] > price) or
                      (pool["side"] == "SSL" and direction == "bear" and pool["level"] < price)]
        if candidates:
            target = min(candidates, key=lambda item: abs(item["level"] - price))

    dealing_range = smc.get("dealing_range")
    premium_discount = premium_discount_at(price, dealing_range)
    location_ok = _location_ok(direction, premium_discount)
    checklist = [
        [bool(sequence), "Liquidity sweep terhubung ke struktur internal/swing"],
        [bool(sequence and sequence.get("event") and sequence["event"].get("displacement")), "Displacement ATR14 + BOS/CHoCH terkonfirmasi"],
        [bool(zone), "Harga retrace ke contextual FVG/OB aktif"],
        [bool(location_ok), f"Lokasi {premium_discount or '-'} sesuai arah"],
        [bool(target), "Opposing liquidity tersedia sebagai target"],
    ]

    signal, setup = "WAIT", None
    if sequence and retrace and location_ok and target:
        signal = "BUY" if direction == "bull" else "SELL"
        extreme = sequence["sweep"]["price"]
        sl = extreme - config.SL_BUFFER_ATR * risk_atr if direction == "bull" else extreme + config.SL_BUFFER_ATR * risk_atr
        risk = abs(price - sl)
        tp2 = target["level"]
        tp1 = price + (tp2 - price) * 0.5
        rr_target = abs(tp2 - price) / risk if risk else 0.0
        if rr_target >= config.MIN_TARGET_RR:
            setup = {
                "id": sequence["id"], "entry": _r(price, digits), "sl": _r(sl, digits),
                "tp1": _r(tp1, digits), "tp2": _r(tp2, digits), "risk": _r(risk, digits),
                "rr": round(rr_target, 2), "scope": sequence.get("scope"),
                "target": {"side": target["side"], "level": _r(target["level"], digits)},
                "zone": {"kind": zone["kind"], "scope": zone.get("scope"),
                         "top": _r(zone["top"], digits), "bottom": _r(zone["bottom"], digits), "t": zone["t"]},
            }
        else:
            signal = "WAIT"
            checklist.append([False, f"RR ke liquidity target {rr_target:.2f} < {config.MIN_TARGET_RR}"])

    swing_direction = smc.get("swing_trend")
    internal_direction = smc.get("internal_trend")
    structure = swing_direction or internal_direction
    trend = "Bullish (Swing)" if swing_direction == "bull" else "Bearish (Swing)" if swing_direction == "bear" else "Bullish (Internal)" if internal_direction == "bull" else "Bearish (Internal)" if internal_direction == "bear" else "Sideways"
    if signal != "WAIT":
        reason = f"{signal}: Liquidity→Sweep→Displacement→{sequence['event']['label']}→{zone['kind']}→Retracement→Liquidity Target."
    else:
        reason = "WAIT: urutan liquidity-first, retracement POI, lokasi dealing range, atau target belum lengkap."

    flow = flow_summary(bars, 20) if "v" in last else None
    # ma100 alias is retained only for old chart builders; it carries MA200 values.
    result.update(
        trend=trend, structure=structure, internal_structure=internal_direction,
        swing_structure=swing_direction, signal=signal, setup=setup,
        checklist=checklist, reason=reason,
        ma20=_r(ma20[-1], digits), ma50=_r(ma50[-1], digits),
        ma200=_r(ma200[-1], digits), ma100=_r(ma200[-1], digits),
        rsi=_r(rsi14[-1], 1), atr=_r(risk_atr, digits),
        atr14=_r(risk_atr, digits), atr200=_r(atr200[-1], digits),
        atr200_ready=len(bars) > config.STRUCTURE_ATR_LEN and raw_atr200[-1] is not None,
        structure_lengths={"internal": internal_len, "swing": swing_len},
        fvg_mode=config.FVG_MODE, premium_discount=premium_discount,
        dealing_range=_zl([dealing_range], digits)[0] if dealing_range else None,
        last_candle={
            "t": last["t"], "o": _r(last["o"], digits), "h": _r(last["h"], digits),
            "l": _r(last["l"], digits), "c": _r(last["c"], digits),
            "dir": "Bullish" if last["c"] >= last["o"] else "Bearish",
        },
        liquidity=_zl(smc["liquidity"], digits), events=_zl(smc["events"], digits),
        sweeps=_zl(smc["sweeps"], digits), fvg=_zl(smc["fvg"], digits),
        ob=_zl(smc["order_blocks"], digits), fvg_mit=_zl(smc["fvg_mit"], digits),
        ob_mit=_zl(smc["ob_mitigated"], digits), pd_zones=_zl(smc["pd_zones"], digits),
        strong_weak=_zl(smc["strong_weak"], digits), flow=flow,
        series={"times": [bar["t"] for bar in bars], "ma20": ma20, "ma50": ma50,
                "ma200": ma200, "ma100": ma200},
    )
    return result


def overall(tfs, book=None, trades=None):
    bias_tf = tfs.get(config.BIAS_TF) or {}
    setup_tf = tfs.get(config.SETUP_TF) or {}
    confirm_tf = tfs.get(config.CONFIRM_TF) or {}
    wanted = {"BUY": "bull", "SELL": "bear"}.get(setup_tf.get("signal"))
    reasons = []
    bias = bias_tf.get("swing_structure") or bias_tf.get("structure")
    reasons.append([bias is not None, f"HTF {config.BIAS_TF}: swing structure {bias or 'netral'}"])
    reasons.append([wanted is not None, f"Setup {config.SETUP_TF}: {setup_tf.get('reason', '-')}"])
    confirmation = confirm_tf.get("internal_structure") or confirm_tf.get("structure")
    reasons.append([wanted is not None and confirmation == wanted,
                    f"Konfirmasi {config.CONFIRM_TF}: internal structure {confirmation or 'belum ada BOS/CHoCH'}"])
    if wanted and bias in (None, wanted) and confirmation == wanted:
        return {"signal": setup_tf["signal"], "setup": setup_tf.get("setup"), "reasons": reasons,
                "summary": f"{setup_tf['signal']} valid: swing HTF → setup liquidity {config.SETUP_TF} → internal BOS/CHoCH {config.CONFIRM_TF}."}
    return {"signal": "WAIT", "setup": None, "pending": setup_tf.get("signal"), "reasons": reasons,
            "summary": "WAIT – swing HTF, setup liquidity, dan struktur internal belum selaras."}
