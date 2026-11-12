"""Explainable 0–100 FVG context score."""
def best(analysis, regime, mtf, data_health):
    zones = analysis.get("fvg") or []
    if not zones: return {"score": 0, "grade": "D", "zone": None, "factors": {"no_fvg": 0}}
    z = zones[-1]; direction = z.get("dir")
    factors = {
        "mtf_alignment": 20 if mtf.get("direction") == direction else 8 if mtf.get("direction") == "mixed" else 0,
        "liquidity_sweep": 15 if any(s.get("dir") == direction for s in (analysis.get("sweeps") or [])[-8:]) else 0,
        "displacement": 20 if any(e.get("dir") == direction and e.get("displacement") for e in (analysis.get("events") or [])[-5:]) else 7,
        "regime": 15 if regime.get("direction") == direction else 5,
        "freshness": 10 if z.get("status") == "active" else 4,
        "active_leg_location": 10 if analysis.get("premium_discount") in (("discount" if direction == "bull" else "premium"),) else 4,
        "formation_session": 5,
        "data_integrity": 5 if data_health.get("complete") else round(5 * data_health.get("coverage", 0), 1),
    }
    score = round(sum(factors.values()), 1)
    grade = "A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 55 else "D"
    return {"score": score, "grade": grade, "zone": z, "factors": factors}
