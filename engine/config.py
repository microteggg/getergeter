"""Konfigurasi Market Monitor – Forex XAUUSD (RealMarketAPI) + Crypto top 10 (Binance) – SMC canonical v6.

Semua definisi logika trading ada di LOGIC_SPEC.md. File ini hanya berisi parameter.
Nilai dibaca dari GitHub Secrets/Variables atau file .env untuk tes lokal.
"""
import os


def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _get(name, default=""):
    v = os.getenv(name, "")
    return v.strip() if v and v.strip() else default


def _bool(name, default):
    return _get(name, str(default)).lower() in ("1", "true", "yes", "y", "on")


def _list(name, default):
    return [s.strip().upper() for s in _get(name, default).split(",") if s.strip()]


def _range(name, default):
    a, b = _get(name, default).split("-")
    return int(a), int(b)


# ---------- Kredensial (GitHub Secrets) ----------
REALMARKET_API_KEY = _get("REALMARKET_API_KEY")
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")

# ---------- FOREX: XAUUSD saja (RealMarketAPI, kuota 5.000 request/bulan) ----------
# Sengaja membaca FOREX_SYMBOLS (bukan SYMBOLS lama yang mungkin masih berisi EURUSD -> kuota dobel).
FOREX_SYMBOLS = [s for s in _list("FOREX_SYMBOLS", "XAUUSD") if s != "NONE"]
SYMBOLS = FOREX_SYMBOLS                                   # alias lama
FOREX_BASE_URL = "https://api.realmarketapi.com/api/v1"
FOREX_SOURCE_TF = SOURCE_TF = "M5"
FOREX_TFS = TFS = ["M5", "M15", "H1", "H4", "D", "W"]
BOOTSTRAP_H1 = _bool("BOOTSTRAP_H1", True)
SKIP_WEEKEND = _bool("SKIP_WEEKEND", True)
SESSION_TZ = "America/New_York"                          # hari forex mulai 17:00 New York (DST otomatis)
SESSION_OPEN_HOUR = 17
MONTHLY_QUOTA = int(_get("MONTHLY_QUOTA", "5000"))
QUOTA_RESERVE = int(_get("QUOTA_RESERVE", "150"))
FAST_INTERVAL_MIN = int(_get("FAST_INTERVAL_MIN", "5"))  # sesi aktif
SLOW_INTERVAL_MIN = int(_get("SLOW_INTERVAL_MIN", "10")) # di luar sesi aktif
FAST_SESSION_NY = _range("FAST_SESSION_NY", "7-12")      # jam New York (London/NY overlap)
FETCH_TOLERANCE_SEC = int(_get("FETCH_TOLERANCE_SEC", "60"))

# ---------- CRYPTO: Binance Spot (publik, tanpa kuota bulanan) ----------
# Top 10 market cap non-stablecoin yang tersedia di Binance spot (Nov 2026). Ubah lewat Variable CRYPTO_SYMBOLS.
CRYPTO_SYMBOLS = [s for s in _list(
    "CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT,TRXUSDT,DOGEUSDT,ZECUSDT,LINKUSDT,ADAUSDT") if s != "NONE"]
CRYPTO_TFS = ["M1", "M5", "M15", "H1", "H4", "D", "W"]
CRYPTO_INTERVAL_MIN = 5                                   # ikut cron (tiap run)
BINANCE_HOSTS = [h.strip() for h in _get(
    "BINANCE_HOSTS", "https://data-api.binance.vision,https://api.binance.com,https://api.binance.us").split(",") if h.strip()]
DEPTH_LIMIT = int(_get("DEPTH_LIMIT", "100"))
LARGE_TRADE_USD = float(_get("LARGE_TRADE_USD", "0"))

# ---------- Penyimpanan ----------
# Analisis memakai SELURUH candle tersimpan; chart menampilkan seluruhnya juga (tidak dipotong 300).
STORE_LIMIT = {"M1": 4400, "M5": 6000, "M15": 4000, "H1": 4000, "H4": 3000, "D": 2000, "W": 1000}
CHART_BARS = dict(STORE_LIMIT)
JOURNAL_SETUP_LIMIT = int(_get("JOURNAL_SETUP_LIMIT", "3000"))   # per TF; entry tidak dibatasi
ENGINE_VERSION = "v19"

# ---------- Strategi top-down ----------
BIAS_TF = _get("BIAS_TF", "H4")
SETUP_TF = _get("SETUP_TF", "H1")
CONFIRM_TF = _get("CONFIRM_TF", "M15")

# Indikator tampilan saja (tidak memengaruhi sinyal). MA = SMA.
MA_FAST, MA_MID, MA_LONG = 20, 50, 200
RSI_LEN = int(_get("RSI_LEN", "14"))
ATR_LEN = int(_get("ATR_LEN", "14"))                    # risiko: displacement, SL, proximity
STRUCTURE_ATR_LEN = int(_get("STRUCTURE_ATR_LEN", "200"))  # struktur: EQH/EQL, FVG

# Panjang pivot (internal, swing) per TF.
TF_STRUCTURE_LENGTHS = {
    "M1": (3, 10),
    "M5": (3, 12),
    "M15": (3, 16),
    "H1": (4, 24),
    "H4": (5, 30),
    "D": (5, 40),
    "W": (3, 20),
}

MIN_BARS = {"M1": 100, "M5": 100, "M15": 100, "H1": 100, "H4": 60, "D": 30, "W": 20}


def structure_lengths(tf):
    tf = str(tf).upper()
    internal, swing = TF_STRUCTURE_LENGTHS.get(tf, (4, 24))
    internal = int(_get(f"INTERNAL_SWING_LEN_{tf}", str(internal)))
    swing = int(_get(f"EXTERNAL_SWING_LEN_{tf}", str(swing)))
    return max(1, internal), max(internal + 1, swing)


def min_bars(tf):
    """Jumlah candle tertutup minimum agar TF dianggap 'enough' (swing pivot bisa terkonfirmasi 2x)."""
    _, swing = structure_lengths(tf)
    return max(MIN_BARS.get(tf, 100), swing * 2 + 5)


# ---------- Parameter SMC (definisi di LOGIC_SPEC.md) ----------
EQUAL_LIQ_ATR = float(_get("EQUAL_LIQ_ATR", "0.10"))
DISPLACEMENT_ATR = float(_get("DISPLACEMENT_ATR", "0.80"))
DISPLACEMENT_BODY_RATIO = float(_get("DISPLACEMENT_BODY_RATIO", "0.60"))
OB_LOOKBACK = int(_get("OB_LOOKBACK", "5"))
SEQUENCE_WINDOW = int(_get("SEQUENCE_WINDOW", "12"))
MAX_VISUAL_ZONES = int(_get("MAX_VISUAL_ZONES", "12"))

FVG_MODE = _get("FVG_MODE", "HYBRID").upper()
if FVG_MODE not in ("ATR", "AUTO", "HYBRID"):
    FVG_MODE = "HYBRID"
FVG_ATR_THRESHOLD = float(_get("FVG_ATR_THRESHOLD", "0.10"))
FVG_AUTO_MULTIPLIER = float(_get("FVG_AUTO_MULTIPLIER", "2.00"))
FVG_MIN_BODY_RATIO = float(_get("FVG_MIN_BODY_RATIO", "0.50"))

POI_PROXIMITY_ATR = float(_get("POI_PROXIMITY_ATR", "0.15"))
SL_BUFFER_ATR = float(_get("SL_BUFFER_ATR", "0.10"))
MIN_TARGET_RR = float(_get("MIN_TARGET_RR", "1.50"))
TP1_R = float(_get("TP1_R", "1.0"))                     # TP1 = entry ± TP1_R × risk; TP2 = liquidity

# ---------- Zone engine v18: BUY AREA / SELL AREA (definisi di zones.py) ----------
ZONE_ENABLED = _bool("ZONE_ENABLED", True)
ZONE_LOOKBACK = int(_get("ZONE_LOOKBACK", "1000"))          # hanya FVG/OB yang terbentuk di N candle terakhir
ZONE_MAX_AGE = int(_get("ZONE_MAX_AGE", "300"))            # candle: belum disentuh / AREA tanpa setup / setelah confirm
ZONE_SWEEP_ATR = float(_get("ZONE_SWEEP_ATR", "1.0"))      # ekstrem sweep boleh lewat zona maks N x ATR14
ZONE_CONFIRM_WINDOW = int(_get("ZONE_CONFIRM_WINDOW", str(SEQUENCE_WINDOW)))
ZONE_MAX_SETUPS = int(_get("ZONE_MAX_SETUPS", "3"))        # maks setup ulang per zona (setelah setup kedaluwarsa)
ZONE_AREA_SCORE = int(_get("ZONE_AREA_SCORE", "3"))        # skor minimum tampil/notif AREA
ZONE_SETUP_SCORE = int(_get("ZONE_SETUP_SCORE", "4"))      # skor minimum tahap SETUP (maks 10)
ZONE_CONFIRM_SCORE = int(_get("ZONE_CONFIRM_SCORE", "7"))  # skor minimum CONFIRMED model Sweep Reversal
# Model CHoCH Retest: CHoCH -> retest OB/FVG searah karakter baru -> rejection
ZONE_RETEST_ENABLED = _bool("ZONE_RETEST_ENABLED", True)
ZONE_RETEST_NEED_DISP = _bool("ZONE_RETEST_NEED_DISP", True)    # leg CHoCH wajib punya displacement
ZONE_RETEST_NEED_PD = _bool("ZONE_RETEST_NEED_PD", True)        # retest wajib di premium(SELL)/discount(BUY) leg CHoCH
ZONE_RETEST_CONFIRM_SCORE = int(_get("ZONE_RETEST_CONFIRM_SCORE", "6"))
ZONE_ALERT_TFS = _list("ZONE_ALERT_TFS", "M15,H1,H4")
ZONE_ALERT_STAGES = _list("ZONE_ALERT_STAGES", "AREA,SETUP,CONFIRMED")   # tambah INVALID bila perlu

# ---------- v19: MODE ----------
# monitor (default) : BUY/SELL, entry, hasil entry, dan notif zona BUY/SELL TIDAK dikirim & tidak ditampilkan.
#                     Engine hanya mencatat + mengirim KEJADIAN/SETUP (monitor.py) untuk dicek manual.
#                     Journal/backtest lama tetap dihitung & disimpan (tidak dihapus).
# signal            : perilaku lama v18 (sinyal BUY/SELL aktif kembali).
SIGNAL_MODE = _get("SIGNAL_MODE", "monitor").lower()
if SIGNAL_MODE not in ("monitor", "signal"):
    SIGNAL_MODE = "monitor"
SIGNALS_ON = SIGNAL_MODE == "signal"

# ---------- v19: Auto Fibonacci (fibo.py) ----------
FIB_ENABLED = _bool("FIB_ENABLED", True)
FIB_PRIMARY_TF = _get("FIB_PRIMARY_TF", "H4").upper()        # Fib utama (tebal) – tampil di semua TF
FIB_MIN_ATR = float(_get("FIB_MIN_ATR", "1.0"))              # leg impuls minimal N x ATR14 TF tsb
FIB_FALLBACK_INTERNAL = _bool("FIB_FALLBACK_INTERNAL", True) # fib swing invalid -> pakai leg internal valid

# ---------- v19: Monitoring kejadian/setup (monitor.py) ----------
MONITOR_SYMBOLS = _list("MONITOR_SYMBOLS", "XAUUSD")         # pair yang dicatat & dinotifkan
MONITOR_TF = _get("MONITOR_TF", "M15").upper()
MONITOR_LOOKBACK = int(_get("MONITOR_LOOKBACK", "300"))      # candle MONITOR_TF yang dipindai tiap run
MONITOR_LIMIT = int(_get("MONITOR_LIMIT", "5000"))           # riwayat kejadian disimpan per pair
MONITOR_SWEEP_WINDOW = int(_get("MONITOR_SWEEP_WINDOW", "12"))   # sweep dihitung confluence bila <= N candle
MONITOR_CONFLUENCE_MIN = int(_get("MONITOR_CONFLUENCE_MIN", "4"))  # skor minimum (maks 6)
MONITOR_INTERNAL_STRUCTURE = _bool("MONITOR_INTERNAL_STRUCTURE", False)  # iBOS/iCHoCH ikut dicatat
# Tipe kejadian yang dikirim ke Telegram (lainnya hanya dicatat di dashboard):
#   STRUCTURE, SWEEP, FVG_NEW, OB_NEW, HTF_FIB, LOCAL_FIB, FIB_INVALID, OB_FVG_OVERLAP, CONFLUENCE
MONITOR_NOTIFY = _list("MONITOR_NOTIFY", "STRUCTURE,SWEEP,HTF_FIB,OB_FVG_OVERLAP,CONFLUENCE")
TELEGRAM_RETRY = int(_get("TELEGRAM_RETRY", "3"))            # pesan gagal dicoba ulang di run berikutnya
TELEGRAM_RETRY_MAX_AGE_MIN = int(_get("TELEGRAM_RETRY_MAX_AGE_MIN", "60"))

# ---------- Alert Telegram ----------
ALERT_TFS = _list("ALERT_TFS", "M15,H1,H4")             # TF untuk STRUCTURE ALERT
ALERT_INTERNAL_STRUCTURE = _bool("ALERT_INTERNAL_STRUCTURE", False)
ALERT_WITH_CHART = _bool("ALERT_WITH_CHART", True)
DASHBOARD_URL = _get("DASHBOARD_URL", "")

DIGITS = {"XAUUSD": 2, "XAGUSD": 3, "USDJPY": 3, "EURUSD": 5}


def market_of(symbol):
    return "crypto" if symbol in CRYPTO_SYMBOLS else "forex"


def tfs_of(symbol):
    return CRYPTO_TFS if market_of(symbol) == "crypto" else FOREX_TFS


def digits(symbol, price=None):
    if symbol in DIGITS:
        return DIGITS[symbol]
    if market_of(symbol) == "forex":
        return 5
    if price is None:
        return 2
    p = abs(price)
    return 2 if p >= 100 else 3 if p >= 10 else 4 if p >= 1 else 5 if p >= 0.01 else 7
