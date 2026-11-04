# Market Monitor v3 – Forex (RealMarketAPI) + Crypto (Binance) + Order Flow

## Arsitektur
```
FOREX  RealMarketAPI ── M5, fetch tiap ±30 menit (kuota ±2.100/bln dari 5.000) ──┐
                                                                                  ├─► Engine Python (GitHub Actions, tiap 10 menit)
CRYPTO Binance Spot  ── M1 + order book + transaksi tiap ±10 menit (tanpa key) ──┘      M1→M5→M15→H1→H4 (+D/W)
                                                                                         SMC + EMA/RSI/ATR + Order Flow
                                                                                               │
                                             ┌─────────────────────────────────────────────────┴───────────┐
                                     GitHub Pages: Dashboard                                Cloudflare Worker: command Telegram
                     + crypto LIVE langsung dari WebSocket Binance di browser       (hanya membaca hasil engine)
```
- GitHub Actions berjalan tiap 10 menit. **Forex hanya di-fetch bila sudah ±30 menit** sejak fetch terakhir, sehingga kuota RealMarketAPI sama seperti sebelumnya.
- Binance data publik tidak butuh API key dan tidak mengurangi kuota RealMarketAPI.
- Data **real-time** crypto (order book 100 ms, transaksi, candle M1 berjalan) tampil langsung di browser lewat WebSocket Binance, tanpa server 24 jam.

## Isi repo (upload semua ke halaman utama repo)
```
.github/workflows/engine.yml   jadwal tiap 10 menit + deploy dashboard
.github/workflows/test.yml     tes koneksi manual
engine/                        program Python (forex, crypto, analisis, alert)
docs/                          dashboard
worker/worker.js               kode Cloudflare Worker (ditempel manual di Cloudflare)
requirements.txt  .gitignore  PANDUAN.md
```
**Hapus dulu file workflow lama** (`.github/workflows/`) agar tidak ada dua jadwal yang berjalan.

## Setting GitHub
1. **Secrets**: `REALMARKET_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (`ID1,ID2` untuk 2 penerima).
2. **Variables** (opsional): `CRYPTO_SYMBOLS` = `BTCUSDT,ETHUSDT` (default). Isi `NONE` untuk mematikan crypto.
3. **Settings → Actions → General → Workflow permissions → Read and write → Save.**
4. **Settings → Pages → Source: GitHub Actions.**
5. **Actions → Tes Koneksi (manual) → Run workflow.** Periksa log:
   - forex: minimal 7 candle M5 per request
   - Binance: `OK via https://data-api.binance.vision` (atau host lain yang berhasil)
6. **Actions → Market Engine → Run workflow.** Dashboard: `https://USERNAME.github.io/NAMA-REPO/` → simpan sebagai Variable `DASHBOARD_URL`.

## Dashboard
- Tab **FOREX** (XAUUSD, EURUSD) dan **CRYPTO** (BTCUSDT, ETHUSDT). Crypto punya TF tambahan **M1**.
- Chart gaya TradingView: drag = geser, scroll/tombol = zoom, crosshair + legenda OHLC.
- SMC: BOS (putus-putus), CHoCH (tebal), Order Block, FVG, Sweep, zona termitigasi, EMA 20/50/200, Entry/SL/TP, BUY/SELL.
- Pane **Volume**; untuk crypto juga pane **Delta per candle + CVD**.
- Crypto (panel kanan): **Order Book** 12 level + spread + bid/ask %, liquidity wall disorot; **Transaksi (done)** berjalan, transaksi besar disorot kuning.
- Panel **Order Flow**: perubahan & volume 24 jam, tekanan beli/jual (live sejak halaman dibuka, 20 candle, 1.000 transaksi terakhir),
  imbalance order book ±0,1% / ±0,5% / ±1%, liquidity wall, jumlah transaksi besar, grafik riwayat imbalance.
- Badge **● LIVE Binance** = WebSocket tersambung. **◌ Snapshot** = live diblokir jaringan/ISP; dashboard tetap memakai snapshot engine (maks ±10 menit).

## Logika sinyal
Per TF: struktur (BOS/CHoCH) + EMA20/50 + RSI tidak jenuh + pullback ke FVG/OB aktif. SL di luar zona, TP1 1,5R, TP2 3R.
Keputusan: bias **H4** → setup **H1** → konfirmasi **M15**.
**Crypto tambahan:** minimal separuh dari 3 indikator order flow harus searah (delta H1 20 candle, order book ±0,5%, transaksi agresif).
Matikan dengan Variable `ORDERFLOW_FILTER=false`.

## Bot Telegram (Cloudflare Worker)
Tempel `worker/worker.js` → Variables: `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `SETUP_KEY` (Secret), `ALLOWED_USER_IDS`, `DATA_URL` (Text) → Deploy →
buka `https://NAMA-WORKER.NAMA-AKUN.workers.dev/setup?key=ISI_SETUP_KEY`.
Jika Worker versi lama sudah terpasang: cukup tempel kode baru, Deploy, lalu buka `/setup` sekali lagi agar menu command crypto muncul.

| Command | Fungsi |
|---|---|
| `/status` | Status RealMarketAPI, Binance, Telegram, kuota |
| `/price` `/signal` | Harga (crypto + perubahan 24j) dan sinyal semua pair |
| `/xauusd` `/eurusd` | Analisis forex |
| `/btcusdt` `/ethusdt` | Analisis crypto + order flow (delta, order book, wall, transaksi besar) |
| `/chartbtcusdt m1` dst. | Chart PNG (crypto ada pane volume) |
| `/lastupdate` `/dashboard` `/help` | Info data, link, daftar command |

## Variables opsional
| Name | Default | Fungsi |
|---|---|---|
| `SYMBOLS` | XAUUSD,EURUSD | Pair forex |
| `CRYPTO_SYMBOLS` | BTCUSDT,ETHUSDT | Pair crypto Binance Spot (`NONE` = matikan) |
| `ALERT_EVENT_TFS` | H1,H4 | Alert BOS/CHoCH |
| `ALERT_SIGNAL` | true | Alert BUY/SELL |
| `ALERT_WHALE` | false | Alert transaksi besar crypto |
| `LARGE_TRADE_USD` | 0 (otomatis persentil 98) | Batas "transaksi besar" dalam USD |
| `ORDERFLOW_FILTER` | true | Order flow wajib mendukung sinyal crypto |
| `SKIP_WEEKEND` | true | Forex dilewati Sabtu–Minggu (crypto tetap jalan) |

## Catatan
- Binance memblokir sebagian lokasi server (HTTP 451). Engine otomatis mencoba `data-api.binance.vision` → `api.binance.com` → `api.binance.us`.
- Jadwal GitHub bisa terlambat beberapa menit; 10 menit = 6 deploy Pages per jam.
- Order book & transaksi di Telegram/analisis adalah snapshot saat engine jalan; data detik demi detik hanya di dashboard (live).
- Simulasi offline: `cd engine && python simulate.py`
