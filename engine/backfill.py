"""Backfill otomatis: isi penyimpanan candle sampai kapasitas maksimal (config.STORE_LIMIT).

Berjalan otomatis di dalam engine, mundur dari candle TERTUA yang tersimpan, dan bisa dilanjutkan
pada run berikutnya (progres disimpan di private_state.json -> "backfill"). Setelah penuh, engine
kembali ke model geser biasa: candle baru masuk, candle paling lama terbuang.

FOREX (XAUUSD, RealMarketAPI /history, memakai kuota – dibatasi BACKFILL_FX_BUDGET request):
  M5  native  : jendela 3 hari  (≤ 864 candle/request)
  M15 native  : jendela 10 hari (≤ 960 candle/request)
  H1  native  : jendela 5 minggu (≤ 840 candle/request) -> juga membentuk H4 / D / W (sesi 17:00 New York)
  Jendela diselaraskan ke awal hari/minggu sesi, jadi setiap candle H4/D/W yang dibentuk selalu lengkap.
CRYPTO (Binance, gratis): setiap TF native, 1000 candle/request mundur dengan endTime.

Aturan gabung: candle yang sudah ada TIDAK ditimpa, kecuali candle baru lebih lengkap
(contoh: candle H4/D/W pertama yang dulu terbentuk sebagian dari bootstrap H1).
"""
import time

import config
from resample import TF_SEC, resample
from session import session_start, week_start

FX_BUDGET = int(config._get("BACKFILL_FX_BUDGET", "150"))     # total request RealMarketAPI untuk backfill
MAX_SEC = int(config._get("BACKFILL_MAX_SEC", "240"))         # batas waktu backfill per run (detik)
SLEEP = float(config._get("BACKFILL_SLEEP", "0.3"))          # jeda antar request RealMarketAPI
DAY, WEEK = 86400, 7 * 86400
N_OF = {"M1": 1, "M5": 1, "M15": 3, "H1": 12, "H4": 48, "D": 288, "W": 2016}   # isi n (satuan candle sumber)
FX_SPAN = {"M5": 3 * DAY, "M15": 10 * DAY, "H1": 5 * WEEK}


def merge(store, rows):
    """Tambah candle yang belum ada; ganti candle yang sudah ada hanya bila yang baru lebih lengkap (n lebih besar)."""
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


def _align_day(t):
    return session_start(t)


def _align_week(t):
    return week_start(t)


class Backfill:
    def __init__(self, engine):
        self.e = engine
        self.t0 = time.time()
        self.st = engine.p.setdefault("backfill", {})
        self.st.setdefault("_fx_req", 0)

    def time_left(self):
        return time.time() - self.t0 < MAX_SEC

    # ------------------------------------------------------------------ util
    def state(self, sym, tf):
        return self.st.setdefault(sym, {}).setdefault(tf, {"done": False, "cur": None, "empty": 0, "req": 0})

    def summary(self, sym, stores):
        s = self.st.get(sym, {})
        tfs = config.tfs_of(sym)
        if all(s.get(tf, {}).get("done") for tf in self._tasks(sym)):
            return None
        return f"Backfill {sym} berjalan: " + " · ".join(
            f"{tf} {len(stores[tf])}/{config.STORE_LIMIT[tf]}" for tf in tfs)

    def _tasks(self, sym):
        return ["M5", "M15", "H1"] if config.market_of(sym) == "forex" else list(config.CRYPTO_TFS)

    # ------------------------------------------------------------------ FOREX
    def forex(self, sym, stores, fetch_history):
        if not config.REALMARKET_API_KEY:
            return
        if self.st.get("_fx_off_until", 0) > self.e.now:
            return
        for tf in ("M5", "M15", "H1"):
            s = self.state(sym, tf)
            if s["done"] and s.get("why") == "budget" and self.st["_fx_req"] < FX_BUDGET:
                s["done"] = False                             # budget dinaikkan -> lanjutkan
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
                start = (_align_week(end - FX_SPAN[tf] + 7200) if tf == "H1"
                         else _align_day(end - FX_SPAN[tf] + 7200))
                if start >= end:
                    start = end - FX_SPAN[tf]
                self.e.count_req()
                self.st["_fx_req"] += 1
                s["req"] += 1
                rows, err, code = fetch_history(sym, tf, start, end)
                time.sleep(SLEEP)
                if err:
                    if code in (400, 401, 402, 403, 404, 405):
                        # endpoint/paket tidak mendukung -> jangan coba lagi selama 7 hari
                        self.st["_fx_off_until"] = self.e.now + 7 * DAY
                        self.e.warnings.append(f"Backfill {sym}: endpoint /history ditolak ({err}). Dicoba lagi 7 hari lagi.")
                    else:
                        self.e.warnings.append(f"Backfill {sym} {tf}: {err} – dilanjutkan run berikutnya.")
                    return
                rows = [dict(b, n=N_OF[tf]) for b in rows if b["t"] + TF_SEC[tf] <= self.e.now]
                s["cur"] = start
                if not rows:
                    s["empty"] += 1
                    if s["empty"] >= 2:                       # dua jendela kosong berturut-turut = riwayat habis
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
            # mulai dari minggu yang memuat candle tertua -> candle H4/D/W parsial di awal ikut dilengkapi
            return _align_week(min(olds) + WEEK) if olds else _align_week(self.e.now)
        if stores[tf]:
            return min(_align_day(stores[tf][0]["t"] + DAY), _align_day(self.e.now))
        return _align_day(self.e.now)

    # ------------------------------------------------------------------ CRYPTO
    def crypto(self, sym, stores):
        bn = self.e.bn
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
                    self.e.warnings.append(f"Backfill {sym} {tf}: {err} – dilanjutkan run berikutnya.")
                    return
                raw = len(rows)                                   # hitung sebelum candle berjalan dibuang
                rows = [dict(b, n=N_OF[tf]) for b in rows if b["t"] + TF_SEC[tf] <= self.e.now]
                before = len(stores[tf])
                stores[tf], _ = merge(stores[tf], rows)
                if raw < ask or len(stores[tf]) == before:        # sudah sampai awal listing pair
                    s["done"], s["why"] = True, "riwayat habis"


def run(engine, sym, stores, fetch_history):
    """Dipanggil engine setelah load(). Mengubah `stores` in-place."""
    bf = engine.__dict__.setdefault("_backfill", Backfill(engine))
    if config.market_of(sym) == "forex":
        bf.forex(sym, stores, fetch_history)
    else:
        bf.crypto(sym, stores)
    msg = bf.summary(sym, stores)
    if msg:
        engine.warnings.append(msg)
