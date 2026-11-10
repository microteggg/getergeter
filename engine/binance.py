"""Binance Spot – data pasar publik (tanpa API key, tanpa kuota bulanan).
Host dicoba berurutan; host yang berhasil diingat agar run berikutnya langsung memakainya.
GitHub Actions berjalan di server AS -> api.binance.com bisa menolak (HTTP 451)."""
import requests

import config

INTERVAL = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h", "H4": "4h", "D": "1d", "W": "1w"}


class Binance:
    def __init__(self, preferred=None):
        hosts = list(config.BINANCE_HOSTS)
        if preferred in hosts:
            hosts.remove(preferred)
            hosts.insert(0, preferred)
        self.hosts = hosts
        self.host = None
        self.calls = 0
        self.weight = 0

    def _get(self, path, params, weight=1):
        last = "tidak ada host"
        for h in ([self.host] if self.host else []) + [x for x in self.hosts if x != self.host]:
            try:
                self.calls += 1
                r = requests.get(h + path, params=params, timeout=20)
            except requests.RequestException as e:
                last = f"{h}: {e.__class__.__name__}"
                continue
            if r.status_code == 200:
                self.host = h
                self.weight += weight
                return r.json(), None
            last = f"{h}: HTTP {r.status_code} {r.text[:100]}"
            if r.status_code in (400,) and "Invalid symbol" in r.text:
                return None, last
        return None, last

    def klines(self, symbol, tf, limit=1000, start_ms=None, end_ms=None):
        p = {"symbol": symbol, "interval": INTERVAL[tf], "limit": limit}
        if start_ms:
            p["startTime"] = start_ms
        if end_ms:
            p["endTime"] = end_ms          # backfill: ambil mundur dari candle tertua yang tersimpan
        data, err = self._get("/api/v3/klines", p, 2)
        if err:
            return [], err
        out = []
        for k in data:
            v, bv = float(k[5]), float(k[9])
            out.append({"t": int(k[0]) // 1000, "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]),
                        "v": v, "bv": bv, "qv": float(k[7]), "tc": int(k[8])})
        return out, None

    def depth(self, symbol, limit=100):
        w = 5 if limit <= 100 else 25 if limit <= 500 else 50
        return self._get("/api/v3/depth", {"symbol": symbol, "limit": limit}, w)

    def agg_trades(self, symbol, limit=1000):
        return self._get("/api/v3/aggTrades", {"symbol": symbol, "limit": limit}, 4)

    def ticker24(self, symbol):
        return self._get("/api/v3/ticker/24hr", {"symbol": symbol}, 2)
