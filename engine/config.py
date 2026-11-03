"""Konfigurasi engine. Semua nilai dibaca dari environment / GitHub Secrets & Variables.
Bisa juga dari file .env (untuk tes di PC)."""
import os


def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _get(name, default=""):
    v = os.getenv(name, "")
    return v.strip() if v and v.strip() else default


def _bool(name, default):
    return _get(name, str(default)).lower() in ("1", "true", "yes", "y", "on")


# --- Kredensial (GitHub Secrets) ---
REALMARKET_API_KEY = _get("REALMARKET_API_KEY")
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")

# --- Data ---
BASE_URL = "https://api.realmarketapi.com/api/v1"
SYMBOLS = [s.strip().upper() for s in _get("SYMBOLS", "XAUUSD,EURUSD").split(",") if s.strip()]
SOURCE_TF = "M5"                        # satu-satunya timeframe yang diminta ke API
TIMEFRAMES = ["M5", "M15", "H1", "H4", "D", "W"]   # semua dibentuk dari M5
FETCH_INTERVAL_MIN = int(_get("FETCH_INTERVAL_MIN", "30"))   # hanya untuk tampilan "Next check"
MONTHLY_QUOTA = int(_get("MONTHLY_QUOTA", "5000"))
# Jam mulai candle harian (UTC). 22 = standar broker forex (≈ 05:00 WIB).
DAY_START_UTC_HOUR = int(_get("DAY_START_UTC_HOUR", "22"))
# Ambil H1 sekali di awal agar chart H1/H4/D/W langsung punya riwayat (2 request, sekali saja).
BOOTSTRAP_H1 = _bool("BOOTSTRAP_H1", True)
# Lewati fetch saat pasar forex tutup (Sabtu & Minggu) untuk hemat kuota.
SKIP_WEEKEND = _bool("SKIP_WEEKEND", True)

# Batas jumlah candle yang disimpan per timeframe
STORE_LIMIT = {"M5": 4100, "M15": 2000, "H1": 2000, "H4": 1500, "D": 1000, "W": 520}
CHART_BARS = {"M5": 150, "M15": 150, "H1": 150, "H4": 150, "D": 150, "W": 120}

# --- Strategi ---
BIAS_TF = _get("BIAS_TF", "H4")       # arah besar
SETUP_TF = _get("SETUP_TF", "H1")     # tempat mencari entry
CONFIRM_TF = _get("CONFIRM_TF", "M15")  # konfirmasi
EMA_FAST, EMA_SLOW, RSI_LEN, ATR_LEN = 20, 50, 14, 14
SWING_LEN = 2
RSI_BUY_MIN, RSI_BUY_MAX = 40, 70
RSI_SELL_MIN, RSI_SELL_MAX = 30, 60
TP1_R, TP2_R = 1.5, 3.0

# --- Alert Telegram ---
ALERT_SIGNAL = _bool("ALERT_SIGNAL", True)                 # BUY/SELL top-down
ALERT_EVENT_TFS = [s.strip().upper() for s in _get("ALERT_EVENT_TFS", "H1,H4").split(",") if s.strip()]
ALERT_WITH_CHART = _bool("ALERT_WITH_CHART", True)

DASHBOARD_URL = _get("DASHBOARD_URL", "")

DIGITS = {"XAUUSD": 2, "XAGUSD": 3, "USDJPY": 3}


def digits(symbol):
    return DIGITS.get(symbol, 5)
