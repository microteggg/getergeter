"""Tes koneksi: RealMarketAPI (1 request/pair forex), Binance (tanpa kuota), Telegram."""
from datetime import datetime, timezone, timedelta
import requests
import config
from binance import Binance
from data_source import fetch_candles
from telegram_notify import send_message, chat_ids

WIB = timezone(timedelta(hours=7))
t = lambda x: datetime.fromtimestamp(x, WIB).strftime("%d/%m %H:%M WIB")
hasil = []

print("== 1. RealMarketAPI (forex M5) ==")
for sym in config.FOREX_SYMBOLS:
    if not config.REALMARKET_API_KEY:
        print("GAGAL: REALMARKET_API_KEY belum diisi"); break
    rows, err = fetch_candles(sym, "M5")
    if err:
        print(f"[{sym}] GAGAL: {err}"); hasil.append(f"❌ {sym}: {err}"); continue
    jam = (rows[-1]["t"] - rows[0]["t"]) / 3600
    print(f"[{sym}] OK: {len(rows)} candle M5 ({t(rows[0]['t'])} s/d {t(rows[-1]['t'])}, ±{jam:.1f} jam) "
          f"-> {'AMAN' if len(rows) >= 7 else 'TIDAK CUKUP'} untuk 30 menit")
    hasil.append(f"{'✅' if len(rows) >= 7 else '⚠️'} {sym}: {len(rows)} candle M5")

print("\n== 2. Binance (crypto) ==")
bn = Binance()
for sym in config.CRYPTO_SYMBOLS:
    rows, err = bn.klines(sym, "M1", 1000)
    if err:
        print(f"[{sym}] GAGAL klines: {err}"); hasil.append(f"❌ {sym}: {err[:80]}"); continue
    dep, e2 = bn.depth(sym, config.DEPTH_LIMIT)
    tr, e3 = bn.agg_trades(sym, 1000)
    print(f"[{sym}] OK via {bn.host}: {len(rows)} candle M1 (s/d {t(rows[-1]['t'])}) | depth "
          f"{'OK' if dep else 'GAGAL ' + str(e2)} | transaksi {'OK ' + str(len(tr)) if tr else 'GAGAL ' + str(e3)}")
    hasil.append(f"✅ {sym}: Binance OK ({bn.host.replace('https://', '')})")

print("\n== 3. Telegram ==")
r = requests.get(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe", timeout=15)
print("Token OK" if r.ok else f"Token GAGAL: {r.text[:120]}")
ok, err = send_message("✅ <b>Tes koneksi Market Bot</b>\n" + "\n".join(hasil))
print(f"Kirim ke {len(chat_ids())} chat ID: " + ("OK" if ok else f"GAGAL {err}"))
