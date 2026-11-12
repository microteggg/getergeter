"""Step 3: auditable price-level liquidity map.

The name is conventional SMC vocabulary.  These OHLC-derived reference levels are not
claims about resting orders; true executable liquidity requires an order book.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from resample import bucket_start
from .common import LONDON, NY, UTC


def _state(level, side, after):
    swept_t = broken_t = None
    for b in after:
        wick = b["h"] > level if side == "high" else b["l"] < level
        close_break = b["c"] > level if side == "high" else b["c"] < level
        if close_break and broken_t is None:
            broken_t = b["t"]
            break
        if wick and swept_t is None:
            swept_t = b["t"]
    status = "BROKEN" if broken_t is not None else "SWEPT" if swept_t is not None else "INTACT"
    return status, broken_t or swept_t


def _period_groups(bars, tf, market):
    groups = {}
    for b in bars:
        groups.setdefault(bucket_start(b["t"], tf, market), []).append(b)
    return groups


def _previous_period_levels(bars, tf, market, prefix):
    groups = _period_groups(bars, tf, market)
    keys = sorted(groups)
    if len(keys) < 2:
        return []
    start = keys[-2]
    rows = groups[start]
    end = keys[-1]
    after = [b for b in bars if b["t"] >= end]
    high, low = max(b["h"] for b in rows), min(b["l"] for b in rows)
    out = []
    for typ, value, side in ((prefix + "H", high, "high"), (prefix + "L", low, "low")):
        status, event_t = _state(value, side, after)
        out.append({"type": typ, "side": side, "price": value, "period_start": start,
                    "period_end": end, "status": status, "event_t": event_t,
                    "description": f"{typ} {status.lower()}: ekstrem periode sebelumnya."})
    return out


def _session_window(now, tz, start_hour, end_hour, bars):
    local_now = datetime.fromtimestamp(now, UTC).astimezone(tz)
    candidates = []
    for days in range(0, 8):
        date = (local_now - timedelta(days=days)).date()
        start = datetime(date.year, date.month, date.day, start_hour, tzinfo=tz)
        end = datetime(date.year, date.month, date.day, end_hour, tzinfo=tz)
        if end <= start:
            end += timedelta(days=1)
        s, e = int(start.timestamp()), int(end.timestamp())
        rows = [b for b in bars if s <= b["t"] < e]
        if rows and e <= now:
            candidates.append((s, e, rows))
    return max(candidates, key=lambda x: x[0]) if candidates else None


def _session_levels(bars, now, name, tz, start_hour, end_hour):
    win = _session_window(now, tz, start_hour, end_hour, bars)
    if not win:
        return []
    start, end, rows = win
    after = [b for b in bars if b["t"] >= end]
    high, low = max(b["h"] for b in rows), min(b["l"] for b in rows)
    out = []
    for suffix, value, side in (("HIGH", high, "high"), ("LOW", low, "low")):
        typ = f"{name}_{suffix}"
        status, event_t = _state(value, side, after)
        out.append({"type": typ, "side": side, "price": value, "period_start": start,
                    "period_end": end, "status": status, "event_t": event_t,
                    "description": f"Ekstrem sesi {name.title()} terakhir yang sudah selesai."})
    return out


def _smc_levels(tfs, top_tf):
    a = tfs.get(top_tf) or {}
    pools = (a.get("liquidity") or [])[-10:]
    out = []
    for p in pools:
        typ = p.get("label") or ("SWING_HIGH" if p.get("side") == "BSL" else "SWING_LOW")
        out.append({"type": typ, "side": "high" if p.get("side") == "BSL" else "low",
                    "price": p.get("level"), "period_start": p.get("t"), "period_end": None,
                    "status": "INTACT", "event_t": None,
                    "description": "Pool pivot aktif dari canonical SMC engine."})
    return out


def build(market, tfs, top_tf, now, price, digits):
    base_tf = "M15" if (tfs.get("M15") or {}).get("_bars") else "H1"
    bars = (tfs.get(base_tf) or {}).get("_bars") or []
    levels = []
    if bars:
        levels += _previous_period_levels(bars, "D", market, "PD")
        levels += _previous_period_levels(bars, "W", market, "PW")
        levels += _session_levels(bars, now, "ASIA", UTC, 0, 8)
        levels += _session_levels(bars, now, "LONDON", LONDON, 8, 16)
        levels += _session_levels(bars, now, "NEW_YORK", NY, 8, 17)
    levels += _smc_levels(tfs, top_tf)

    atr = (tfs.get("H1") or {}).get("atr14") or (tfs.get(top_tf) or {}).get("atr14") or 0
    for lv in levels:
        lv["price"] = round(float(lv["price"]), digits)
        lv["distance"] = round(abs(lv["price"] - price), digits)
        lv["distance_atr"] = round(abs(lv["price"] - price) / atr, 2) if atr else None
    active = [x for x in levels if x["status"] == "INTACT"]
    above = min((x for x in active if x["price"] > price), key=lambda x: x["price"] - price, default=None)
    below = min((x for x in active if x["price"] < price), key=lambda x: price - x["price"], default=None)
    return {
        "levels": levels, "nearest_above": above, "nearest_below": below,
        "intact": sum(x["status"] == "INTACT" for x in levels),
        "swept": sum(x["status"] == "SWEPT" for x in levels),
        "broken": sum(x["status"] == "BROKEN" for x in levels),
        "description": "Level referensi berbasis OHLC/pivot, bukan ukuran resting order book atau jaminan reversal.",
    }
