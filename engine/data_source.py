"""RealMarketAPI (forex). HANYA file ini yang memakai kuota RealMarketAPI (1 panggilan = 1 request).
/candle  = candle terbaru (dipakai tiap run)
/history = riwayat per rentang waktu (dipakai backfill, maks 1000 candle per request)."""
from datetime import datetime, timezone
import requests

import config


def _num(d, *keys):
    for k in keys:
        if k in d and d[k] is not None:
            return float(d[k])
    raise KeyError(keys[0])


def _time(d):
    for k in ("openTime", "OpenTime", "time", "Time", "datetime"):
        if k in d and d[k]:
            v = d[k]
            if isinstance(v, (int, float)):
                return int(v / 1000 if v > 1e11 else v)
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
    raise KeyError("openTime")


def parse_rows(payload):
    rows = payload
    if isinstance(payload, dict):
        for k in ("data", "Data", "candles", "result", "items"):
            if isinstance(payload.get(k), list):
                rows = payload[k]
                break
        else:
            rows = [payload]
    out = {}
    for r in rows or []:
        try:
            t = _time(r)
            out[t] = {"t": t, "o": _num(r, "openPrice", "OpenPrice", "open", "Open"),
                      "h": _num(r, "highPrice", "HighPrice", "high", "High"),
                      "l": _num(r, "lowPrice", "LowPrice", "low", "Low"),
                      "c": _num(r, "closePrice", "ClosePrice", "close", "Close")}
        except (KeyError, ValueError, TypeError):
            continue
    return [out[k] for k in sorted(out)]


def fetch_candles(symbol, timeframe, timeout=25):
    try:
        r = requests.get(f"{config.FOREX_BASE_URL}/candle", timeout=timeout,
                         params={"apiKey": config.REALMARKET_API_KEY, "symbolCode": symbol, "timeFrame": timeframe})
    except requests.RequestException as e:
        return [], f"Koneksi gagal: {e.__class__.__name__}"
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}: {r.text[:160]}"
    try:
        rows = parse_rows(r.json())
    except ValueError:
        return [], "Respons bukan JSON"
    if not rows:
        return [], "Respons kosong / format tidak dikenali"
    return rows, None


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_history(symbol, timeframe, start, end, page_size=1000, timeout=40):
    """Candle dengan start <= openTime < end. Return (rows, err, http_status)."""
    try:
        r = requests.get(f"{config.FOREX_BASE_URL}/history", timeout=timeout,
                         params={"ApiKey": config.REALMARKET_API_KEY, "SymbolCode": symbol, "TimeFrame": timeframe,
                                 "startTime": _iso(start), "endTime": _iso(end - 1), "pageSize": page_size})
    except requests.RequestException as e:
        return [], f"Koneksi gagal: {e.__class__.__name__}", 0
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}: {r.text[:160]}", r.status_code
    try:
        rows = parse_rows(r.json())
    except ValueError:
        return [], "Respons bukan JSON", 200
    return [b for b in rows if start <= b["t"] < end], None, 200
