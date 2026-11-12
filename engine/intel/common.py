"""Small dependency-free helpers shared by the intelligence modules."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from statistics import median
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


def clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def safe(value, default=0.0):
    try:
        value = float(value)
        return value if isfinite(value) else default
    except (TypeError, ValueError):
        return default


def percentile_rank(values, value):
    """Inclusive empirical percentile in [0, 1]; no distributional assumption."""
    clean = [safe(v, None) for v in values]
    clean = [v for v in clean if v is not None]
    if not clean:
        return 0.5
    return sum(v <= value for v in clean) / len(clean)


def rolling_true_ranges(bars):
    out = []
    for i, bar in enumerate(bars):
        prev = bars[i - 1]["c"] if i else bar["c"]
        out.append(max(bar["h"] - bar["l"], abs(bar["h"] - prev), abs(bar["l"] - prev)))
    return out


def wilder(values, period=14):
    """Wilder moving average with a running warm-up, matching the existing engine."""
    out, acc = [], None
    for i, value in enumerate(values):
        if i + 1 < period:
            out.append(sum(values[: i + 1]) / (i + 1))
        elif i + 1 == period:
            acc = sum(values[:period]) / period
            out.append(acc)
        else:
            acc = (acc * (period - 1) + value) / period
            out.append(acc)
    return out


def efficiency_ratio(closes, lookback=20):
    """Kaufman-style directional efficiency: net travel / total path, [0, 1]."""
    if len(closes) < 3:
        return 0.0
    n = min(lookback, len(closes) - 1)
    window = closes[-(n + 1):]
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    return abs(window[-1] - window[0]) / path if path > 1e-12 else 0.0


def robust_ratio(value, baseline):
    return safe(value) / max(safe(baseline), 1e-12)


def direction_label(direction):
    return "Bullish" if direction == "bull" else "Bearish" if direction == "bear" else "Netral"


def iso(ts):
    return datetime.fromtimestamp(int(ts), UTC).isoformat().replace("+00:00", "Z")


def price_session(ts):
    """Human review label only; Phase 2 will own statistical session intelligence.

    Sessions overlap by design.  The priority communicates the most active overlap
    first and uses DST-aware London/New York local clocks.
    """
    dt = datetime.fromtimestamp(ts, UTC)
    lh = dt.astimezone(LONDON).hour
    nh = dt.astimezone(NY).hour
    london = 8 <= lh < 16
    new_york = 8 <= nh < 17
    if london and new_york:
        return "LONDON_NY_OVERLAP"
    if new_york:
        return "NEW_YORK"
    if london:
        return "LONDON"
    if 0 <= dt.hour < 8:
        return "ASIA"
    return "OFF_SESSION"


def compact_snapshot(intel):
    """Immutable journal-safe subset; intentionally excludes large level arrays."""
    if not intel:
        return None
    conf = intel.get("confidence") or {}
    reg = intel.get("regime") or {}
    mtf = intel.get("mtf") or {}
    fvg = intel.get("fvg_quality") or {}
    data = intel.get("data_quality") or {}
    return {
        "version": intel.get("version"), "mode": intel.get("mode"), "asof": intel.get("asof"),
        "regime": reg.get("label"), "regime_score": reg.get("strength"),
        "mtf_bias": mtf.get("bias"), "mtf_alignment": mtf.get("alignment"),
        "confidence_direction": conf.get("direction"), "confidence_score": conf.get("score"),
        "confidence_label": conf.get("label"), "confidence_components": conf.get("components"),
        "fvg_id": (fvg.get("best") or {}).get("id"), "fvg_grade": (fvg.get("best") or {}).get("grade"),
        "fvg_score": (fvg.get("best") or {}).get("score"), "data_score": data.get("score"),
    }
