"""Multi-factor market-regime classification (descriptive, not predictive)."""

def _pct_rank(values, x):
    v = sorted(z for z in values if z is not None)
    return 0.5 if not v else sum(z <= x for z in v) / len(v)


def classify(analysis, lookback=100):
    bars, atr = analysis.get("_bars") or [], analysis.get("_atr14") or []
    if len(bars) < 20:
        return {"state": "DATA_INSUFFICIENT", "direction": "neutral", "score": 0, "why": ["Candle belum cukup"]}
    closes = [b["c"] for b in bars]
    w = min(lookback, len(bars) - 1)
    net, path = closes[-1] - closes[-1 - w], sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - w, len(closes)))
    er = abs(net) / path if path else 0.0
    av = atr[-1] if atr and atr[-1] else bars[-1]["h"] - bars[-1]["l"]
    ap = _pct_rank(atr[-lookback:], av)
    recent = bars[-20:]
    width = max(b["h"] for b in recent) - min(b["l"] for b in recent)
    prev = bars[-40:-20] or recent
    prev_width = max(b["h"] for b in prev) - min(b["l"] for b in prev)
    expand = width / max(prev_width, 1e-12)
    ev = (analysis.get("events") or [])[-1:] or []
    direction = ev[0]["dir"] if ev else ("bull" if net > 0 else "bear" if net < 0 else "neutral")
    choch = bool(ev and ev[0].get("type") == "CHoCH")
    if choch:
        state = "TRANSITION"
    elif er >= .38 and ap >= .65 and expand >= 1.15:
        state = "BULLISH EXPANSION" if direction == "bull" else "BEARISH EXPANSION"
    elif er >= .30:
        state = "BULLISH TREND" if direction == "bull" else "BEARISH TREND"
    elif ap <= .30 and expand <= .9:
        state = "COMPRESSION"
    else:
        state = "RANGE"
    return {"state": state, "direction": direction, "score": round(min(100, er * 160 + ap * 25), 1),
            "metrics": {"efficiency_ratio": round(er, 4), "atr_percentile": round(ap, 4), "range_expansion": round(expand, 4)},
            "why": [f"Directional efficiency {er:.2f}", f"ATR percentile {ap:.0%}", f"Range expansion {expand:.2f}x"]}
