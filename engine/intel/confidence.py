"""Context score; deliberately not a win-probability claim."""
def score(regime, mtf, fvg, data_health):
    components = {"regime": min(25, regime.get("score", 0) * .25), "mtf": mtf.get("alignment", 0) * .30,
                  "fvg": fvg.get("score", 0) * .30, "data_health": data_health.get("coverage", 0) * 15}
    penalties = {"tf_conflict": 12 if mtf.get("conflict") else 0,
                 "transition": 8 if regime.get("state") == "TRANSITION" else 0,
                 "compression": 6 if regime.get("state") == "COMPRESSION" else 0,
                 "no_fvg": 10 if not fvg.get("zone") else 0,
                 "poor_data": 15 if not data_health.get("complete") else 0}
    value = round(max(0, min(100, sum(components.values()) - sum(penalties.values()))), 1)
    d = mtf.get("direction")
    decision = ("BUY_WATCH" if d == "bull" and value >= 55 else "SELL_WATCH" if d == "bear" and value >= 55
                else "MIXED_WATCH" if value >= 45 else "NO_EDGE")
    return {"value": value, "label": "context score (not win probability)", "decision": decision,
            "components": {k: round(v, 1) for k, v in components.items()}, "penalties": penalties}
