"""Sesi pasar & penjadwalan fetch berbasis kuota.

- Hari trading dimulai 17:00 New York (DST otomatis) -> sama dengan TradingView/OANDA.
- Pasar tutup Jumat 17:00 NY s/d Minggu 17:00 NY.
- Interval fetch: 5 menit di sesi aktif, 10 menit di luar sesi, lalu dinaikkan otomatis
  bila sisa kuota bulan ini tidak cukup (quota governor).
"""
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config

NY = ZoneInfo(config.SESSION_TZ)
STEP_MIN = 15


def session_start(t):
    """Timestamp UTC awal hari trading (17:00 NY) yang memuat t."""
    dt = datetime.fromtimestamp(t, NY)
    anchor = dt.replace(hour=config.SESSION_OPEN_HOUR, minute=0, second=0, microsecond=0)
    if dt < anchor:
        anchor = (anchor - timedelta(days=1)).replace(hour=config.SESSION_OPEN_HOUR)
    return int(anchor.timestamp())


def week_start(t):
    """Awal minggu trading = Minggu 17:00 NY."""
    s = session_start(t)
    dt = datetime.fromtimestamp(s, NY)
    back = (dt.weekday() - 6) % 7               # Minggu = 6
    start = (dt - timedelta(days=back)).replace(hour=config.SESSION_OPEN_HOUR, minute=0)
    return int(start.timestamp())


def market_closed(t):
    dt = datetime.fromtimestamp(t, NY)
    wd, h = dt.weekday(), dt.hour
    return wd == 5 or (wd == 4 and h >= config.SESSION_OPEN_HOUR) or (wd == 6 and h < config.SESSION_OPEN_HOUR)


def in_fast_session(t):
    a, b = config.FAST_SESSION_NY
    dt = datetime.fromtimestamp(t, NY)
    return dt.weekday() < 5 and a <= dt.hour < b


def month_bounds(t):
    dt = datetime.fromtimestamp(t, timezone.utc)
    nxt = datetime(dt.year + (dt.month == 12), dt.month % 12 + 1, 1, tzinfo=timezone.utc)
    return int(nxt.timestamp())


def _open_minutes(now, end):
    fast = slow = 0
    t = now
    while t < end:
        if not market_closed(t):
            if in_fast_session(t):
                fast += STEP_MIN
            else:
                slow += STEP_MIN
        t += STEP_MIN * 60
    return fast, slow


def plan(now, used, per_fetch=1):
    """Hitung interval fetch yang aman untuk sisa bulan ini."""
    end = month_bounds(now)
    fast, slow = _open_minutes(now, end)
    remaining = config.MONTHLY_QUOTA - config.QUOTA_RESERVE - used
    base = config.FAST_INTERVAL_MIN if in_fast_session(now) else config.SLOW_INTERVAL_MIN
    want = (fast / config.FAST_INTERVAL_MIN + slow / config.SLOW_INTERVAL_MIN) * per_fetch
    if remaining <= 0:
        return {"interval_min": None, "base_min": base, "remaining": remaining,
                "projected": used, "throttled": True, "open_min_left": fast + slow}
    budget = math.ceil((fast + slow) * per_fetch / remaining) if fast + slow else base
    factor = max(1.0, want / remaining)        # >1 berarti jadwal normal melebihi kuota
    interval = max(base, math.ceil(base * factor), budget if factor > 1 else base)
    return {"interval_min": interval, "base_min": base, "remaining": remaining,
            "projected": int(used + min(want, remaining)), "throttled": interval > base,
            "open_min_left": fast + slow}
