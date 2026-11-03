"""Tes: berapa candle M5 yang dikirim RealMarketAPI per 1 request (memakai 1 request per pair)."""
from datetime import datetime, timezone, timedelta
import requests
import config
from data_source import fetch_candles
from telegram_notify import send_message

WIB = timezone(timedelta(hours=7))
t = lambda x: datetime.fromtimestamp(x, WIB).strftime("%d/%m %H:%M WIB")

print("== 1. RealMarketAPI (M5) ==")
if not config.REALMARKET_API_KEY:
    print("GAGAL: REALMARKET_API_KEY belum diisi")
hasil = []
for sym in config.SYMBOLS:
    rows, err = fetch_candles(sym, "M5")
    if err:
        print(f"[{sym}] GAGAL: {err}")
        hasil.append(f"❌ {sym}: {err}")
        continue
    jam = (rows[-1]["t"] - rows[0]["t"]) / 3600
    aman = len(rows) >= 7
    print(f"[{sym}] OK: {len(rows)} candle M5 | {t(rows[0]['t'])} s/d {t(rows[-1]['t'])} (±{jam:.1f} jam)")
    print(f"        {'AMAN' if aman else 'TIDAK CUKUP'} untuk fetch tiap {config.FETCH_INTERVAL_MIN} menit")
    hasil.append(f"{'✅' if aman else '⚠️'} {sym}: {len(rows)} candle M5 (±{jam:.1f} jam)")

print("\n== 2. Telegram token ==")
r = requests.get(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe", timeout=15)
print("Token OK" if r.ok else f"GAGAL: {r.text[:120]}")

print("\n== 3. Kirim pesan tes ==")
ok, err = send_message("✅ <b>Tes Bot v2 berhasil</b>\n" + "\n".join(hasil))
print("OK, cek Telegram Anda" if ok else f"GAGAL: {err}")
