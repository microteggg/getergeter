"""Tes koneksi RealMarketAPI, Binance, dan Telegram dengan diagnostik Chat ID aman."""
from datetime import datetime, timezone, timedelta
import requests
import config
from binance import Binance
from data_source import fetch_candles
from telegram_notify import chat_ids

WIB = timezone(timedelta(hours=7))
t = lambda x: datetime.fromtimestamp(x, WIB).strftime("%d/%m %H:%M WIB")
API = "https://api.telegram.org/bot{}/{}"


def masked(cid):
    s = str(cid)
    keep = min(4, len(s))
    return "*" * max(0, len(s) - keep) + s[-keep:]


def send_one(cid, text):
    try:
        r = requests.post(
            API.format(config.TELEGRAM_BOT_TOKEN, "sendMessage"),
            timeout=20,
            data={
                "chat_id": cid,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.ok and body.get("ok"):
            msg = body.get("result", {})
            return True, f"message_id={msg.get('message_id', '-')}"
        return False, f"HTTP {r.status_code}: {body.get('description') or r.text[:160]}"
    except requests.RequestException as e:
        return False, f"{e.__class__.__name__}: {e}"


hasil = []
print("== 1. RealMarketAPI (forex M5) ==")
for sym in config.FOREX_SYMBOLS:
    if not config.REALMARKET_API_KEY:
        print("GAGAL: REALMARKET_API_KEY belum diisi")
        hasil.append("❌ RealMarketAPI key belum diisi")
        break
    rows, err = fetch_candles(sym, "M5")
    if err:
        print(f"[{sym}] GAGAL: {err}")
        hasil.append(f"❌ {sym}: {err}")
        continue
    jam = (rows[-1]["t"] - rows[0]["t"]) / 3600
    aman = len(rows) >= 7
    print(f"[{sym}] OK: {len(rows)} candle M5 ({t(rows[0]['t'])} s/d {t(rows[-1]['t'])}, ±{jam:.1f} jam) -> {'AMAN' if aman else 'TIDAK CUKUP'} untuk 30 menit")
    hasil.append(f"{'✅' if aman else '⚠️'} {sym}: {len(rows)} candle M5")

print("\n== 2. Binance (crypto) ==")
bn = Binance()
for sym in config.CRYPTO_SYMBOLS:
    rows, err = bn.klines(sym, "M1", 1000)
    if err:
        print(f"[{sym}] GAGAL klines: {err}")
        hasil.append(f"❌ {sym}: {err[:80]}")
        continue
    dep, e2 = bn.depth(sym, config.DEPTH_LIMIT)
    tr, e3 = bn.agg_trades(sym, 1000)
    print(f"[{sym}] OK via {bn.host}: {len(rows)} candle M1 (s/d {t(rows[-1]['t'])}) | depth {'OK' if dep else 'GAGAL ' + str(e2)} | transaksi {'OK ' + str(len(tr)) if tr else 'GAGAL ' + str(e3)}")
    hasil.append(f"✅ {sym}: Binance OK ({bn.host.replace('https://', '')})")

print("\n== 3. Telegram ==")
if not config.TELEGRAM_BOT_TOKEN:
    raise SystemExit("GAGAL: TELEGRAM_BOT_TOKEN belum diisi")
try:
    r = requests.get(API.format(config.TELEGRAM_BOT_TOKEN, "getMe"), timeout=15)
    me = r.json() if r.content else {}
except (requests.RequestException, ValueError) as e:
    raise SystemExit(f"GAGAL getMe: {e}")
if not (r.ok and me.get("ok")):
    raise SystemExit(f"Token GAGAL: HTTP {r.status_code} {me.get('description') or r.text[:120]}")
bot = me.get("result", {})
print(f"Token OK | bot=@{bot.get('username', '-')} | bot_id={bot.get('id', '-')}")

ids = chat_ids()
if not ids:
    raise SystemExit("GAGAL: TELEGRAM_CHAT_ID kosong")
print(f"Chat tujuan terbaca: {len(ids)}")
for i, cid in enumerate(ids, 1):
    print(f"  {i}. {masked(cid)}")

stamp = datetime.now(WIB).strftime("%d/%m/%Y %H:%M:%S WIB")
text = "✅ <b>Tes koneksi Market Bot</b>\n" + f"Waktu: {stamp}\n\n" + "\n".join(hasil)
fail = []
for i, cid in enumerate(ids, 1):
    ok, info = send_one(cid, text)
    print(f"Kirim chat #{i} ({masked(cid)}): {'OK' if ok else 'GAGAL'} | {info}")
    if not ok:
        fail.append((masked(cid), info))

if fail:
    raise SystemExit("GAGAL: ada Chat ID yang tidak menerima pesan")
print(f"SELESAI: pesan tes diterima API Telegram untuk {len(ids)} Chat ID.")
