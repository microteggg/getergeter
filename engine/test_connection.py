"""Tes koneksi manual: Telegram (utama) + Binance + RealMarketAPI.
Token TIDAK pernah dicetak. Exit code 1 bila Telegram gagal."""
import sys
from datetime import datetime, timezone

import requests

import config
from binance import Binance
from telegram_notify import chat_ids

API = "https://api.telegram.org/bot{}/{}"
ok_all = True


def line(t=""):
    print(t, flush=True)


def mask(v):
    v = v or ""
    return f"(kosong)" if not v else f"terisi, {len(v)} karakter" + (" ⚠️ ada spasi/enter di awal/akhir" if v != v.strip() else "")


line("=== 1. SECRET TERBACA? ===")
line(f"TELEGRAM_BOT_TOKEN : {mask(config.TELEGRAM_BOT_TOKEN)}")
line(f"TELEGRAM_CHAT_ID   : {mask(config.TELEGRAM_CHAT_ID)} -> {len(chat_ids())} chat id")
line(f"REALMARKET_API_KEY : {mask(config.REALMARKET_API_KEY)}")
tok = (config.TELEGRAM_BOT_TOKEN or "").strip()
if tok.lower().startswith("bot"):
    line("⚠️ Token diawali 'bot' – hapus awalan itu, isi hanya angka:huruf dari BotFather")
    tok = tok[3:]

line("\n=== 2. TELEGRAM ===")
tg_ok = False
if not tok or not chat_ids():
    line("❌ Token/Chat ID kosong di environment. Cek: secret ada DAN workflow memakai")
    line("   env: TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }} / TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}")
else:
    try:
        r = requests.get(API.format(tok, "getMe"), timeout=20).json()
        if r.get("ok"):
            line(f"✅ Token valid – bot @{r['result'].get('username')}")
            now = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")
            tg_ok = True
            for cid in chat_ids():
                r2 = requests.post(API.format(tok, "sendMessage"), timeout=20,
                                   data={"chat_id": cid, "text": f"✅ Tes koneksi GitHub Actions berhasil ({now})"}).json()
                if r2.get("ok"):
                    line(f"✅ Pesan terkirim ke chat {cid}")
                else:
                    tg_ok = False
                    d = r2.get("description", "")
                    line(f"❌ Gagal kirim ke chat {cid}: {r2.get('error_code')} {d}")
                    if "chat not found" in d:
                        line("   -> Chat ID salah, atau bot belum ditambahkan ke grup. Cek via /getUpdates.")
                    elif "initiate" in d or "blocked" in d:
                        line("   -> Buka bot di Telegram lalu tekan /start (atau unblock bot).")
        else:
            line(f"❌ Token ditolak: {r.get('error_code')} {r.get('description')} -> ambil ulang token di @BotFather")
    except requests.RequestException as e:
        line(f"❌ Tidak bisa menghubungi api.telegram.org: {e.__class__.__name__}")
ok_all &= tg_ok

line("\n=== 3. BINANCE ===")
bn = Binance()
rows, err = bn.klines("BTCUSDT", "M15", limit=3)
line(f"✅ BTCUSDT M15 OK via {bn.host}, close {rows[-1]['c']}" if rows else f"⚠️ Binance gagal: {err}")

line("\n=== 4. REALMARKETAPI (1 request kuota) ===")
if not config.REALMARKET_API_KEY:
    line("⚠️ REALMARKET_API_KEY kosong – dilewati")
else:
    from data_source import fetch_candles
    sym = (config.FOREX_SYMBOLS or ["XAUUSD"])[0]
    rows, err = fetch_candles(sym, "M5")
    line(f"✅ {sym} OK, close {rows[-1]['c']}" if rows else f"⚠️ RealMarketAPI gagal: {err}")

line("\n=== HASIL ===")
line("✅ Telegram BERFUNGSI" if tg_ok else "❌ Telegram BELUM berfungsi – lihat pesan di bagian 1/2")
sys.exit(0 if ok_all else 1)
