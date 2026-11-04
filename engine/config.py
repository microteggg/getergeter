"""Konfigurasi. Nilai dibaca dari GitHub Secrets/Variables (atau file .env untuk tes di PC)."""
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


# ---------- Kredensial (GitHub Secrets) ----------
REALMARKET_API_KEY = _get("REALMARKET_API_KEY")
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")          # boleh beberapa: 123,456

# ---------- FOREX (RealMarketAPI) ----------
FOREX_SYMBOLS = _list("SYMBOLS", "XAUUSD,EURUSD")
FOREX_BASE_URL = "https://api.realmarketapi.com/api/v1"
FOREX_SOURCE_TF = "M5"
FOREX_TFS = ["M5", "M15", "H1", "H4", "D", "W"]
FOREX_INTERVAL_MIN = int(_get("FOREX_INTERVAL_MIN", "30"))   # tetap 30 menit (hemat kuota)
FOREX_DAY_START_UTC = int(_get("DAY_START_UTC_HOUR", "22"))  # 22 UTC = 05:00 WIB
MONTHLY_QUOTA = int(_get("MONTHLY_QUOTA", "5000"))
BOOTSTRAP_H1 = _bool("BOOTSTRAP_H1", True)
SKIP_WEEKEND = _bool("SKIP_WEEKEND", True)

# ---------- CRYPTO (Binance Spot, data publik tanpa API key) ----------
CRYPTO_SYMBOLS = [s for s in _list("CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT") if s != "NONE"]
CRYPTO_TFS = ["M1", "M5", "M15", "H1", "H4", "D", "W"]
CRYPTO_INTERVAL_MIN = int(_get("CRYPTO_INTERVAL_MIN", "10"))
CRYPTO_DAY_START_UTC = 0                                     # candle D/W Binance mulai 00:00 UTC
BINANCE_HOSTS = [h.strip() for h in _get(
    "BINANCE_HOSTS", "https://data-api.binance.vision,https://api.binance.com,https://api.binance.us").split(",") if h.strip()]
DEPTH_LIMIT = int(_get("DEPTH_LIMIT", "100"))
LARGE_TRADE_USD = float(_get("LARGE_TRADE_USD", "0"))        # 0 = otomatis (persentil 98)
ORDERFLOW_FILTER = _bool("ORDERFLOW_FILTER", True)

# ---------- Penyimpanan & chart ----------
STORE_LIMIT = {"M1": 4400, "M5": 4100, "M15": 2000, "H1": 2000, "H4": 1500, "D": 1000, "W": 520}
CHART_BARS = {"M1": 300, "M5": 300, "M15": 300, "H1": 300, "H4": 300, "D": 250, "W": 150}

# ---------- Strategi ----------
BIAS_TF = _get("BIAS_TF", "H4")
SETUP_TF = _get("SETUP_TF", "H1")
CONFIRM_TF = _get("CONFIRM_TF", "M15")
EMA_FAST, EMA_SLOW, EMA_LONG, RSI_LEN, ATR_LEN = 20, 50, 200, 14, 14
SWING_LEN = 2
RSI_BUY_MIN, RSI_BUY_MAX = 40, 70
RSI_SELL_MIN, RSI_SELL_MAX = 30, 60
TP1_R, TP2_R = 1.5, 3.0

# ---------- Alert Telegram ----------
ALERT_SIGNAL = _bool("ALERT_SIGNAL", True)
ALERT_EVENT_TFS = _list("ALERT_EVENT_TFS", "H1,H4")
ALERT_WITH_CHART = _bool("ALERT_WITH_CHART", True)
ALERT_WHALE = _bool("ALERT_WHALE", False)

DASHBOARD_URL = _get("DASHBOARD_URL", "")

DIGITS = {"XAUUSD": 2, "XAGUSD": 3, "USDJPY": 3, "EURUSD": 5}


def market_of(symbol):
    return "crypto" if symbol in CRYPTO_SYMBOLS else "forex"


def digits(symbol, price=None):
    if symbol in DIGITS:
        return DIGITS[symbol]
    if market_of(symbol) == "forex":
        return 5
    if price is None:
        return 2
    p = abs(price)
    return 2 if p >= 100 else 3 if p >= 10 else 4 if p >= 1 else 5 if p >= 0.01 else 7
