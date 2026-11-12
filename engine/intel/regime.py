"""Step 1: deterministic, explainable market-regime classification."""
from __future__ import annotations

from statistics import median

import config
from .common import clamp, efficiency_ratio, percentile_rank, rolling_true_ranges, robust_ratio


def _latest_direction(tfa):
    swing = tfa.get("swing_structure")
    internal = tfa.get("internal_structure")
    if swing and swing == internal:
        return swing, 1.0
    if swing:
        return swing, 0.72 if not internal else 0.55
    if internal:
        return internal, 0.42
    return None, 0.0


def classify(tf, tfa, data_score=100.0):
    bars = tfa.get("_bars") or []
    atr = tfa.get("_atr14") or []
    if not tfa.get("enough") or len(bars) < 30:
        return {
            "tf": tf, "label": "INSUFFICIENT_DATA", "direction": None, "phase": "UNKNOWN",
            "strength": 0, "metrics": {}, "reasons": [f"Data {tf} belum cukup untuk klasifikasi stabil."],
            "description": "Tidak diklasifikasikan; engine lama tetap berjalan.",
        }

    closes = [b["c"] for b in bars]
    tr = rolling_true_ranges(bars)
    er = efficiency_ratio(closes, int(getattr(config, "INTEL_REGIME_LOOKBACK", 20)))
    hist = atr[-120:] if atr else tr[-120:]
    current_atr = atr[-1] if atr else tr[-1]
    vol_pct = percentile_rank(hist, current_atr)
    atr_ratio = robust_ratio(current_atr, median(hist) if hist else current_atr)
    recent_range = sum(tr[-8:]) / min(8, len(tr))
    prior = tr[-38:-8] or tr[:-8] or tr
    prior_range = sum(prior) / len(prior)
    expansion = robust_ratio(recent_range, prior_range)

    direction, structure_quality = _latest_direction(tfa)
    recent_events = (tfa.get("events") or [])[-5:]
    recent_choch = any(e.get("type") == "CHoCH" for e in recent_events[-2:])
    directional_disp = sum(1 for e in recent_events[-4:] if e.get("dir") == direction and e.get("displacement"))
    opposing = bool(direction and any(e.get("dir") != direction for e in recent_events[-2:]))

    compression = vol_pct <= 0.30 and expansion <= 0.86 and er <= 0.32
    expanding = (vol_pct >= 0.65 and expansion >= 1.12) or (directional_disp and expansion >= 1.02)
    trending = bool(direction) and structure_quality >= 0.55 and er >= 0.25 and not opposing
    transition = recent_choch or opposing or (tfa.get("swing_structure") and tfa.get("internal_structure") and
                                               tfa["swing_structure"] != tfa["internal_structure"])

    if compression:
        label, phase, out_dir = "COMPRESSION", "COMPRESSION", direction
    elif expanding and trending:
        label = ("BULLISH" if direction == "bull" else "BEARISH") + "_EXPANSION"
        phase, out_dir = "EXPANSION", direction
    elif transition:
        label, phase, out_dir = "TRANSITION", "TRANSITION", direction
    elif trending:
        label = ("BULLISH" if direction == "bull" else "BEARISH") + "_TREND"
        phase, out_dir = "TREND", direction
    else:
        label, phase, out_dir = "RANGE", "RANGE", None

    trend_component = 40 * structure_quality + 30 * min(1.0, er / 0.45) + 15 * min(1.0, directional_disp / 2)
    if phase == "EXPANSION":
        trend_component += 15 * min(1.0, max(0.0, expansion - 1.0) / 0.35)
    elif phase == "COMPRESSION":
        trend_component = 45 + 35 * (1 - vol_pct) + 20 * max(0.0, 1 - expansion)
    elif phase == "RANGE":
        trend_component = 45 + 35 * (1 - er) + 20 * (1 - abs(vol_pct - 0.5))
    elif phase == "TRANSITION":
        trend_component = 35 + 20 * structure_quality + 15 * er
    strength = round(clamp(trend_component * (0.7 + 0.3 * data_score / 100)), 1)

    reasons = [
        f"Struktur swing/internal: {tfa.get('swing_structure') or '-'} / {tfa.get('internal_structure') or '-'}.",
        f"Directional efficiency {er:.2f}; makin dekat 1 berarti lintasan makin searah.",
        f"ATR percentile {vol_pct * 100:.0f}% dan ATR/median {atr_ratio:.2f}x.",
        f"Range 8 candle vs baseline: {expansion:.2f}x.",
    ]
    if recent_choch:
        reasons.append("CHoCH baru meningkatkan risiko fase transisi.")
    description = {
        "COMPRESSION": "Volatilitas dan rentang menyusut; breakout belum dianggap terkonfirmasi.",
        "EXPANSION": "Range dan volatilitas melebar bersama struktur searah; continuation mendapat konteks lebih kuat.",
        "TRANSITION": "Struktur internal/swing berubah atau berkonflik; arah belum stabil.",
        "TREND": "Struktur dan efisiensi harga mendukung pergerakan directional.",
        "RANGE": "Perjalanan harga tidak cukup efisien/directional; sweep tepi range lebih relevan daripada mengejar breakout.",
    }[phase]
    return {
        "tf": tf, "label": label, "direction": out_dir, "phase": phase, "strength": strength,
        "volatility": "HIGH" if vol_pct >= 0.75 else "LOW" if vol_pct <= 0.25 else "NORMAL",
        "metrics": {"efficiency": round(er, 3), "atr_percentile": round(vol_pct * 100, 1),
                    "atr_ratio": round(atr_ratio, 3), "range_expansion": round(expansion, 3),
                    "directional_displacement": directional_disp},
        "reasons": reasons, "description": description,
    }


def build(tfs, top_tf, data_score=100.0):
    primary = classify(top_tf, tfs.get(top_tf) or {}, data_score)
    per_tf = {}
    for tf in ("D", "H4", "H1", "M15"):
        if tf in tfs:
            per_tf[tf] = classify(tf, tfs[tf], data_score)
    primary["per_tf"] = per_tf
    return primary
