"""Transparent multi-timeframe structure alignment."""

def summarize(symbol, tfs, regimes):
    order = [tf for tf in (("D", "H4", "H1", "M15") if symbol != "XAUUSD" else ("D", "H4", "H1", "M15"))
             if (tfs.get(tf) or {}).get("enough")]
    if symbol == "XAUUSD" and not (tfs.get("D") or {}).get("enough"):
        order = [tf for tf in ("H4", "H1", "M15") if (tfs.get(tf) or {}).get("enough")]
    rows, bull, bear = [], 0.0, 0.0
    weights = {"D": 4, "H4": 3, "H1": 2, "M15": 1}
    total = sum(weights.get(tf, 1) for tf in order) or 1
    for tf in order:
        r = regimes.get(tf, {})
        d, w = r.get("direction", "neutral"), weights.get(tf, 1)
        bull += w if d == "bull" else 0; bear += w if d == "bear" else 0
        rows.append({"tf": tf, "direction": d, "regime": r.get("state"), "weight": w})
    bs, br = round(100 * bull / total, 1), round(100 * bear / total, 1)
    direction = "bull" if bs >= 65 else "bear" if br >= 65 else "mixed"
    return {"hierarchy": rows, "bull_score": bs, "bear_score": br, "alignment": max(bs, br), "direction": direction,
            "conflict": bs >= 25 and br >= 25, "top_tf": order[0] if order else None}
