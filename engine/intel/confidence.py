"""Step 5: one transparent confidence score with components and penalties.

The score is an explainable context index.  It is intentionally not labelled as a
win probability, and in Phase 1 live mode it cannot gate legacy signals.
"""
from __future__ import annotations

from .common import clamp

WEIGHTS = {"regime": 20, "mtf": 25, "liquidity": 15, "fvg": 25, "location": 10, "data": 5}


def _component(name, value, maximum, reason):
    return {"name": name, "score": round(clamp(value, 0, maximum), 1), "max": maximum, "reason": reason}


def _liq_score(direction, liq):
    levels = liq.get("levels") or []
    if direction == "bull":
        swept_origin = any(x["side"] == "low" and x["status"] == "SWEPT" for x in levels)
        target = liq.get("nearest_above")
    else:
        swept_origin = any(x["side"] == "high" and x["status"] == "SWEPT" for x in levels)
        target = liq.get("nearest_below")
    score = (8 if swept_origin else 2) + (7 if target else 0)
    reason = ("Origin-side liquidity pernah swept; " if swept_origin else "Belum ada origin-side sweep; ") + \
             (f"target intact terdekat {target['type']}." if target else "target intact belum tersedia.")
    return score, reason


def _for_direction(direction, regime, mtf, liq, fvg, data):
    rdir, phase = regime.get("direction"), regime.get("phase")
    if rdir == direction:
        rv = 20 if phase == "EXPANSION" else 17 if phase == "TREND" else 12
    elif rdir is None:
        rv = 8 if phase == "RANGE" else 6
    else:
        rv = 1
    c_reg = _component("Market regime", rv, 20, f"{regime.get('label', 'UNKNOWN')} vs {direction}.")

    dscore = (mtf.get("bull_score") if direction == "bull" else mtf.get("bear_score")) or 0
    c_mtf = _component("MTF structure", 25 * dscore / 100, 25,
                       f"Hierarchy directional score {dscore:.1f}/100; konflik: {', '.join(mtf.get('conflicts') or []) or 'tidak ada'}.")

    lv, lr = _liq_score(direction, liq)
    c_liq = _component("Liquidity context", lv, 15, lr)

    candidates = [x for x in fvg.get("active") or [] if x.get("direction") == direction]
    best = candidates[0] if candidates else None
    fv = 25 * best["score"] / 100 if best else 0
    c_fvg = _component("FVG quality", fv, 25,
                       f"{best['tf']} {best['grade']} {best['score']}/100 ({best['id']})." if best else "Tidak ada FVG aktif searah.")

    loc_factor = next((x for x in (best or {}).get("factors", []) if x["name"] == "Active-leg location"), None)
    loc = (loc_factor["score"] if loc_factor else 0)
    c_loc = _component("Location", loc, 10, loc_factor["reason"] if loc_factor else "Lokasi active leg belum tersedia.")
    c_data = _component("Data integrity", 5 * data.get("score", 0) / 100, 5,
                        f"{data.get('status', 'UNKNOWN')} {data.get('score', 0)}/100.")
    components = [c_reg, c_mtf, c_liq, c_fvg, c_loc, c_data]

    penalties = []
    if mtf.get("conflicts"):
        penalties.append({"name": "TF conflict", "value": -min(8, 3 * len(mtf["conflicts"])),
                          "reason": "Satu atau lebih timeframe melawan bias terpilih."})
    if phase == "TRANSITION":
        penalties.append({"name": "Transition regime", "value": -8, "reason": "CHoCH/konflik membuat arah belum stabil."})
    if phase == "COMPRESSION":
        penalties.append({"name": "Compression", "value": -6, "reason": "Volatilitas menyusut; breakout belum terkonfirmasi."})
    if not best:
        penalties.append({"name": "No active FVG", "value": -8, "reason": "Tidak ada FVG aktif yang mendukung arah."})
    if data.get("status") == "POOR":
        penalties.append({"name": "Poor data", "value": -10, "reason": "Data-health rendah; interpretasi perlu hati-hati."})
    base = sum(x["score"] for x in components)
    total = round(clamp(base + sum(x["value"] for x in penalties)), 1)
    return {"direction": direction, "score": total, "base_score": round(base, 1),
            "components": components, "penalties": penalties, "fvg": best}


def build(regime, mtf, liq, fvg, data):
    bull = _for_direction("bull", regime, mtf, liq, fvg, data)
    bear = _for_direction("bear", regime, mtf, liq, fvg, data)
    lead, other = (bull, bear) if bull["score"] >= bear["score"] else (bear, bull)
    spread = lead["score"] - other["score"]
    if lead["score"] < 40:
        direction, action = None, "NO_EDGE"
    elif spread < 8:
        direction, action = None, "MIXED_WATCH"
    else:
        direction = lead["direction"]
        action = "BUY_WATCH" if direction == "bull" else "SELL_WATCH"
    score = lead["score"] if direction else max(lead["score"], other["score"])
    label = "HIGH" if score >= 80 else "GOOD" if score >= 65 else "MODERATE" if score >= 50 else "LOW"
    selected = lead
    return {
        "direction": direction, "action": action, "score": score, "label": label,
        "base_score": selected["base_score"], "components": selected["components"],
        "penalties": selected["penalties"], "bull": bull["score"], "bear": bear["score"],
        "spread": round(spread, 1), "blocking": False,
        "description": "Confidence adalah context score 0–100, bukan probabilitas menang; Phase 1 live tidak memblokir sinyal.",
    }
