"""Daily/weekly/session OHLC liquidity proxies with explicit lifecycle."""
from datetime import datetime, timezone


def _status(level, side, bars, born):
    later = [b for b in bars if b["t"] > born]
    for b in later:
        if side == "high" and b["c"] > level or side == "low" and b["c"] < level:
            return "BROKEN"
        if side == "high" and b["h"] > level or side == "low" and b["l"] < level:
            return "SWEPT"
    return "INTACT"


def build(analysis):
    bars = analysis.get("_bars") or []
    if not bars: return []
    groups = {}
    for b in bars:
        dt = datetime.fromtimestamp(b["t"], timezone.utc)
        groups.setdefault((dt.year, dt.timetuple().tm_yday), []).append(b)
    days = sorted(groups)
    levels = []
    if len(days) >= 2:
        prev = groups[days[-2]]; born = prev[-1]["t"]
        for name, level, side in (("PDH", max(b["h"] for b in prev), "high"), ("PDL", min(b["l"] for b in prev), "low")):
            levels.append({"name": name, "price": level, "kind": "price-level proxy", "status": _status(level, side, bars, born)})
    recent = bars[-min(len(bars), 7 * 24):]
    if recent:
        born = recent[0]["t"]
        for name, level, side in (("PWH", max(b["h"] for b in recent), "high"), ("PWL", min(b["l"] for b in recent), "low")):
            levels.append({"name": name, "price": level, "kind": "price-level proxy", "status": _status(level, side, bars, born)})
    return levels
