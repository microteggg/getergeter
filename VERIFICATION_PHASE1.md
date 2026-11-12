# Verification report — Phase 1 v21

Tanggal build: 2026-11-12

## Hasil

- Python compile: **PASS**
- Dashboard JavaScript syntax (`node --check`): **PASS**
- GitHub Actions YAML parse: **PASS**
- Unit tests: **5/5 PASS**
- Unified patch (`git apply --check`): **PASS**
- Offline integration simulation: **PASS**, dua run berurutan, XAUUSD + BTCUSDT
- JSON strict serialization (`allow_nan=False`): **PASS**
- Snapshot persistence: **PASS**, history bertambah satu record per run
- Kill switch `INTEL_ENABLED=false`: **PASS**
- Semantic regression: legacy overall decision, per-TF signal, candle counts, dan journal (setelah field intel diabaikan) identik dengan baseline: **PASS**

## Snapshot simulation terakhir

| Pair | Top TF | Regime | Confidence | Data health |
|---|---|---|---:|---:|
| XAUUSD | H4 | RANGE | 53.5 | 98.7 |
| BTCUSDT | H4* | BULLISH_TREND | 76.8 | 99.2 |

`*` Dataset simulasi hanya 36 D candle sehingga D belum memenuhi minimum; fallback H4 adalah perilaku yang disengaja. Feed Binance produksi dengan D cukup memakai D → H4 → H1 → M15.

Angka simulasi hanya membuktikan jalur kode dan schema; tidak membuktikan edge/profitability atau optimalitas threshold.

## Batas pengujian

- Tidak ada panggilan RealMarketAPI/Binance/Telegram produksi; semuanya di-mock oleh `simulate.py`.
- Kerapian visual divalidasi melalui syntax/schema/responsive CSS, belum melalui screenshot browser produksi.
- Bobot perlu dinilai setelah journal live cukup, memakai expectancy per score bucket dan out-of-sample review.
