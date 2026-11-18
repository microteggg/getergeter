"""Explainable 0–100 score for every active canonical FVG."""


def _grade(score):
    return "A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 60 else "D"


def _freshness(zone):
    """Age and wick-mitigation score; filled FVG never enters the active list."""
    age = max(0, int(zone.get("age") or 0))
    mitigation = max(0.0, min(100.0, float(zone.get("mitigation_pct") or 0)))
    age_score = 5 if age <= 12 else 4 if age <= 36 else 3 if age <= 100 else 1
    depth_score = 5 if mitigation == 0 else 4 if mitigation < 25 else 3 if mitigation < 50 else 1
    return age_score + depth_score


def _score(zone, analysis, regime, mtf, data_health):
    direction, formed = zone.get("dir"), zone.get("t", 0)
    sweeps = [s for s in (analysis.get("sweeps") or []) if s.get("dir") == direction and s.get("t", 0) <= formed]
    events = [e for e in (analysis.get("events") or []) if e.get("dir") == direction and e.get("t", 0) <= formed]
    factors = {
        "mtf_alignment": 20 if mtf.get("direction") == direction else 8 if mtf.get("direction") == "mixed" else 0,
        "liquidity_sweep": 15 if sweeps and sweeps[-1] in (analysis.get("sweeps") or [])[-12:] else 0,
        "displacement": 20 if zone.get("context") or any(e.get("displacement") or e.get("leg_displacement") for e in events[-5:]) else 7,
        "regime": 15 if regime.get("direction") == direction else 5 if not regime.get("direction") else 0,
        "freshness": _freshness(zone),
        "active_leg_location": 10 if analysis.get("premium_discount") == ("discount" if direction == "bull" else "premium") else 4,
        "formation_session": 5,
        "data_integrity": 5 if data_health.get("complete") else round(5 * data_health.get("coverage", 0), 1),
    }
    score = round(sum(factors.values()), 1)
    return {"id": zone.get("id"), "dir": direction, "score": score, "grade": _grade(score),
            "status": zone.get("status"), "age": zone.get("age"),
            "mitigation_pct": zone.get("mitigation_pct"), "zone": zone, "factors": factors}


def best(analysis, regime, mtf, data_health):
    zones = analysis.get("fvg") or []
    if not zones:
        return {"score": 0, "grade": "D", "zone": None, "factors": {"no_fvg": 0}, "candidates": []}
    candidates = [_score(z, analysis, regime, mtf, data_health) for z in zones]
    candidates.sort(key=lambda x: (x["score"], x.get("status") == "fresh", -(x.get("age") or 0)), reverse=True)
    top = dict(candidates[0])
    top["candidates"] = [{k: c.get(k) for k in ("id", "dir", "score", "grade", "status", "age", "mitigation_pct")}
                         for c in candidates]
    return top
