"""Backfill otomatis: isi penyimpanan candle sampai kapasitas maksimal (config.STORE_LIMIT) – v16.1.

v16.1:
- Status backfill di dashboard sekarang JUJUR: "berjalan" / "tertunda (alasan + error asli + jadwal coba lagi)" /
  "berhenti (riwayat habis / budget)". Versi v16 menampilkan "berjalan" walaupun /history ditolak.
- Request /history dicoba dengan 2 format parameter (ApiKey/SymbolCode/TimeFrame dan apiKey/symbolCode/timeFrame).
- Bila ditolak (HTTP 4xx), dicoba lagi setelah BACKFILL_RETRY_HOURS (default 6 jam, dulu 7 hari).
- Paksa coba ulang segera: set Variable BACKFILL_RETRY=1 untuk satu run.

FOREX (XAUUSD, RealMarketAPI /history, memakai kuota – dibatasi BACKFILL_FX_BUDGET request):
  M5 jendela 3 hari · M15 jendela 10 hari · H1 jendela 5 minggu (-> H4 / D / W, sesi 17:00 New York).
CRYPTO (Binance, gratis): setiap TF native, 1000 candle/request mundur dengan endTime.
Candle yang sudah ada TIDAK ditimpa, kecuali candle baru lebih lengkap (n lebih besar).
"""
import time
from datetime import datetime, timedelta, timezone

import requests

import config
from resample import TF_SEC, resample
from session import session_start, week_start

FX_BUDGET = int(config._get("BACKFILL_FX_BUDGET", "150"))
MAX_SEC = int(config._get("BACKFILL_MAX_SEC", "240"))
SLEEP = float(config._get("BACKFILL_SLEEP", "0.3"))
RETRY_H = float(config._get("BACKFILL_RETRY_HOURS", "6"))
FORCE = config._get("BACKFILL_RETRY", "0") in ("1", "true", "yes")
DAY, WEEK = 86400, 7 * 86400
N_OF = {"M1": 1, "M5": 1, "M15": 3, "H1": 12, "H4": 48, "D": 288, "W": 2016}
FX_SPAN = {"M5": 3 * DAY, "M15": 10 * DAY, "H1": 5 * WEEK}
WIB = timezone(timedelta(hours=7))


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(js):
    from data_source import parse_rows
    return parse_rows(js)


def http_history(symbol, tf, start, end, page_size=1000, timeout=40):
    """GET /history. Return (rows, err, http_status). Mencoba 2 format nama parameter."""
    variants = (
        {"ApiKey": config.REALMARKET_API_KEY, "SymbolCode": symbol, "TimeFrame": tf},
        {"apiKey": config.REALMARKET_API_KEY, "symbolCode": symbol, "timeFrame": tf},
    )
    last = ("", 0)
    for base in variants:
        params = dict(base, startTime=_iso(start), endTime=_iso(end - 1), pageSize=page_size)
        try:
            r = requests.get(f"{config.FOREX_BASE_URL}/history", params=params, timeout=timeout)
        except requests.RequestException as e:
            return [], f"Koneksi gagal: {e.__class__.__name__}", 0
        if r.status_code == 200:
            try:
                rows = _parse(r.json())
            except ValueError:
                return [], "Respons /history bukan JSON", 200
            return [b for b in rows if start <= b["t"] < end], None, 200
        last = (f"HTTP {r.status_code}: {r.text[:160]}", r.status_code)
        if r.status_code not in (400, 404, 422):
            break                                  # 401/402/403/429/5xx: format lain tidak akan membantu
    return [], last[0], last[1]


def merge(store, rows):
    if not rows:
        return store, 0
    m = {b["t"]: b for b in store}
    changed = 0
    for b in rows:
        ex = m.get(b["t"])
        if ex is None or b.get("n", 0) > ex.get("n", 0):
            m[b["t"]] = b
            changed += 1
    return ([m[k] for k in sorted(m)] if changed else store), changed


class Backfill:
    def __init__(self, engine):
        self.e = engine
        self.t0 = time.time()
        self.st = engine.p.setdefault("backfill", {})
        self.st.setdefault("_fx_req", 0)
        if FORCE:
            self.st.pop("_fx_off_until", None)

    def time_left(self):
        return time.time() - self.t0 < MAX_SEC

    def state(self, sym, tf):
        return self.st.setdefault(sym, {}).setdefault(tf, {"done": False, "cur": None, "empty": 0, "req": 0})

    def _tasks(self, sym):
        return ["M5", "M15", "H1"] if config.market_of(sym) == "forex" else list(config.CRYPTO_TFS)

    def summary(self, sym, stores):
        """Pesan status untuk dashboard; None bila backfill selesai normal."""
        s = self.st.get(sym, {})
        counts = " · ".join(f"{tf} {len(stores[tf])}/{config.STORE_LIMIT[tf]}" for tf in config.tfs_of(sym))
        if config.market_of(sym) == "forex":
            off = self.st.get("_fx_off_until", 0)
            if off > self.e.now:
                nxt = datetime.fromtimestamp(off, WIB).strftime("%d/%m %H:%M WIB")
                return (f"Backfill {sym} TERTUNDA – endpoint /history ditolak RealMarketAPI "
                        f"({self.st.get('_fx_last_err', '-')}). Coba lagi otomatis ±{nxt}. Data: {counts}")
            if self.st["_fx_req"] >= FX_BUDGET and any(not s.get(tf, {}).get("done") or s[tf].get("why") == "budget"
                                                       for tf in self._tasks(sym)):
                return f"Backfill {sym} berhenti: budget {FX_BUDGET} request habis (naikkan BACKFILL_FX_BUDGET). Data: {counts}"
        if all(s.get(tf, {}).get("done") for tf in self._tasks(sym)):
            return None
        err = s.get("_last_err")
        return f"Backfill {sym} berjalan: {counts}" + (f" (kendala terakhir: {err})" if err else "")

    # ------------------------------------------------------------------ FOREX
    def forex(self, sym, stores, fetch_history):
        if not config.REALMARKET_API_KEY or self.st.get("_fx_off_until", 0) > self.e.now:
            return
        # main.py v16 mengoper data_source.fetch_history (1 format parameter) -> pakai http_history (2 format)
        if fetch_history is None or getattr(fetch_history, "__module__", "") == "data_source":
            fetch_history = http_history
        sym_st = self.st.setdefault(sym, {})
        for tf in ("M5", "M15", "H1"):
            s = self.state(sym, tf)
            if s["done"] and s.get("why") == "budget" and self.st["_fx_req"] < FX_BUDGET:
                s["done"] = False
            while not s["done"]:
                if self.st["_fx_req"] >= FX_BUDGET:
                    s["done"], s["why"] = True, "budget"
                    break
                if not self.time_left():
                    return
                if not self._fx_need(tf, stores):
                    s["done"], s["why"] = True, "penuh"
                    break
                end = self._fx_cursor(tf, s, stores)
                start = week_start(end - FX_SPAN[tf] + 7200) if tf == "H1" else session_start(end - FX_SPAN[tf] + 7200)
                if start >= end:
                    start = end - FX_SPAN[tf]
                self.e.count_req()
                self.st["_fx_req"] += 1
                s["req"] += 1
                rows, err, code = fetch_history(sym, tf, start, end)
                time.sleep(SLEEP)
                if err:
                    self.st["_fx_last_err"] = sym_st["_last_err"] = err[:120]
                    if 400 <= (code or 0) < 500 and code != 429:
                        self.st["_fx_off_until"] = self.e.now + int(RETRY_H * 3600)
                    return
                sym_st.pop("_last_err", None)
                self.st.pop("_fx_last_err", None)
                rows = [dict(b, n=N_OF[tf]) for b in rows if b["t"] + TF_SEC[tf] <= self.e.now]
                s["cur"] = start
                if not rows:
                    s["empty"] += 1
                    if s["empty"] >= 2:
                        s["done"], s["why"] = True, "riwayat habis"
                    continue
                s["empty"] = 0
                if tf == "H1":
                    if len(stores["H1"]) < config.STORE_LIMIT["H1"]:
                        stores["H1"], _ = merge(stores["H1"], rows)
                    for big in ("H4", "D", "W"):
                        stores[big], _ = merge(stores[big], resample(rows, big, "forex"))
                else:
                    stores[tf], _ = merge(stores[tf], rows)

    def _fx_need(self, tf, stores):
        if tf == "H1":
            return any(len(stores[x]) < config.STORE_LIMIT[x] for x in ("H1", "H4", "D", "W"))
        return len(stores[tf]) < config.STORE_LIMIT[tf]

    def _fx_cursor(self, tf, s, stores):
        if s["cur"]:
            return s["cur"]
        if tf == "H1":
            olds = [stores[x][0]["t"] for x in ("H1", "H4", "D", "W") if stores[x]]
            return week_start(min(olds) + WEEK) if olds else week_start(self.e.now)
        if stores[tf]:
            return min(session_start(stores[tf][0]["t"] + DAY), session_start(self.e.now))
        return session_start(self.e.now)

    # ------------------------------------------------------------------ CRYPTO
    def crypto(self, sym, stores):
        bn = self.e.bn
        sym_st = self.st.setdefault(sym, {})
        for tf in config.CRYPTO_TFS:
            s = self.state(sym, tf)
            lim = config.STORE_LIMIT[tf]
            while not s["done"]:
                if not self.time_left():
                    return
                need = lim - len(stores[tf])
                if need <= 0:
                    s["done"], s["why"] = True, "penuh"
                    break
                end_ms = (stores[tf][0]["t"] * 1000 - 1) if stores[tf] else None
                ask = min(1000, need + 2)
                rows, err = bn.klines(sym, tf, ask, None, end_ms)
                s["req"] += 1
                if err:
                    sym_st["_last_err"] = f"{tf}: {err}"[:120]
                    return
                sym_st.pop("_last_err", None)
                raw = len(rows)
                rows = [dict(b, n=N_OF[tf]) for b in rows if b["t"] + TF_SEC[tf] <= self.e.now]
                before = len(stores[tf])
                stores[tf], _ = merge(stores[tf], rows)
                if raw < ask or len(stores[tf]) == before:
                    s["done"], s["why"] = True, "riwayat habis"


def run(engine, sym, stores, fetch_history=None):
    """Dipanggil engine setelah load(). Mengubah `stores` in-place."""
    bf = engine.__dict__.setdefault("_backfill", Backfill(engine))
    if config.market_of(sym) == "forex":
        bf.forex(sym, stores, fetch_history)
    else:
        bf.crypto(sym, stores)
    msg = bf.summary(sym, stores)
    if msg:
        engine.warnings.append(msg)
