"""Step 2: multi-timeframe structure hierarchy and conflict reporting."""
from __future__ import annotations

from .common import clamp, direction_label

WEIGHTS = {"D": 35.0, "H4": 30.0, "H1": 20.0, "M15": 15.0}


def _state(tf, a, reg):
    enough = bool(a.get("enough"))
    swing, internal = a.get("swing_structure"), a.get("internal_structure")
    if not enough:
        return {"tf": tf, "available": False, "direction": None, "conviction": 0.0,
                "structure": "DATA_KURANG", "regime": (reg or {}).get("label")}
    if swing and internal and swing == internal:
        direction, conviction, structure = swing, 1.0, "ALIGNED"
    elif swing and internal:
        direction, conviction, structure = swing, 0.58, "PULLBACK_CONFLICT"
    elif swing:
        direction, conviction, structure = swing, 0.78, "SWING_ONLY"
    elif internal:
        direction, conviction, structure = internal, 0.45, "INTERNAL_ONLY"
    else:
        direction, conviction, structure = None, 0.20, "NEUTRAL"
    rdir = (reg or {}).get("direction")
    if rdir and direction and rdir != direction:
        conviction *= 0.75
        structure += "_REGIME_CONFLICT"
    return {"tf": tf, "available": True, "direction": direction,
            "label": direction_label(direction), "conviction": round(conviction, 3),
            "structure": structure, "regime": (reg or {}).get("label")}


def select_timeframes(market, tfs):
    d_ready = bool((tfs.get("D") or {}).get("enough"))
    requested = ["D", "H4", "H1", "M15"] if market == "crypto" or d_ready else ["H4", "H1", "M15"]
    used = [tf for tf in requested if (tfs.get(tf) or {}).get("enough")]
    if not used:
        used = [tf for tf in ("H4", "H1", "M15") if tf in tfs]
    return requested, used, d_ready


def build(market, tfs, regimes):
    requested, used, d_ready = select_timeframes(market, tfs)
    states = []
    for tf in requested:
        states.append(_state(tf, tfs.get(tf) or {}, regimes.get(tf) or {}))
    active = [s for s in states if s["tf"] in used and s["available"]]
    total_w = sum(WEIGHTS[s["tf"]] for s in active) or 1.0

    bull = sum(WEIGHTS[s["tf"]] * (0.5 if s["direction"] is None else
                                   s["conviction"] if s["direction"] == "bull" else 0.0) for s in active) / total_w
    bear = sum(WEIGHTS[s["tf"]] * (0.5 if s["direction"] is None else
                                   s["conviction"] if s["direction"] == "bear" else 0.0) for s in active) / total_w
    if bull >= bear and bull >= 0.55:
        bias, chosen = "bull", bull
    elif bear > bull and bear >= 0.55:
        bias, chosen = "bear", bear
    else:
        bias, chosen = None, max(bull, bear)
    alignment = round(clamp(chosen * 100), 1)
    conflicts = [s["tf"] for s in active if bias and s["direction"] and s["direction"] != bias]
    label = "STRONG" if alignment >= 80 and not conflicts else "ALIGNED" if alignment >= 65 else "MIXED" if alignment >= 45 else "NEUTRAL"

    if market == "forex" and not d_ready:
        fallback = "D XAUUSD belum cukup; hierarchy aman memakai H4 → H1 → M15."
    elif market == "crypto":
        fallback = "Hierarchy crypto D → H4 → H1 → M15; TF yang belum cukup ditandai, bukan dipaksakan."
    else:
        fallback = "D XAUUSD sudah cukup dan ikut hierarchy D → H4 → H1 → M15."
    return {
        "bias": bias, "bias_label": direction_label(bias), "alignment": alignment, "label": label,
        "bull_score": round(bull * 100, 1), "bear_score": round(bear * 100, 1),
        "requested": requested, "used": used, "d_ready": d_ready, "states": states,
        "conflicts": conflicts, "description": fallback,
    }
