"""Phase 1 v21 intelligence orchestrator."""
from . import confidence, fvg_quality, liqmap, mtf, regime


def _health(a):
    bars = a.get("_bars") or []
    if not bars: return {"n": 0, "expected_n": 0, "coverage": 0, "complete": False, "issues": ["no_data"]}
    tail = bars[-min(50, len(bars)):]
    cov = sum(float(b.get("coverage", 1.0)) for b in tail) / len(tail)
    dup = len({b["t"] for b in tail}) != len(tail)
    return {"n": len(bars), "expected_n": len(bars), "coverage": round(cov, 4),
            "complete": cov >= .90 and not dup, "issues": ["duplicate_timestamp"] if dup else ([] if cov >= .90 else ["incomplete_resample"])}


def build(symbol, tfs, now):
    regimes = {tf: regime.classify(a) for tf, a in tfs.items() if a.get("enough")}
    alignment = mtf.summarize(symbol, tfs, regimes)
    target_tf = "H4" if (tfs.get("H4") or {}).get("enough") else next(iter(regimes), None)
    a = tfs.get(target_tf) or {}; health = _health(a)
    fq = fvg_quality.best(a, regimes.get(target_tf, {}), alignment, health)
    conf = confidence.score(regimes.get(target_tf, {}), alignment, fq, health)
    return {"version": 21, "mode": "live", "updated_ts": now, "symbol": symbol, "primary_tf": target_tf,
            "regime": regimes, "mtf": alignment, "liquidity": liqmap.build(a), "fvg_quality": fq,
            "confidence": conf, "data_health": health,
            "glossary": {"TRANSITION": "CHoCH/character shift; direction not settled", "COMPRESSION": "low volatility/range contraction",
                         "price-level proxy": "OHLC-derived level, not proof of resting orders"}}
