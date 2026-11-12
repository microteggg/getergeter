# Phase 1 Market Intelligence v21 — mode LIVE, non-blocking

Paket ini menambahkan langkah 0–6 di atas canonical SMC engine v20. Ia **tidak mengganti definisi BOS/CHoCH, FVG, OB, signal, outcome, atau journal lama**. `INTEL_MODE=live` berarti konteks tampil di dashboard, ditambahkan ke notifikasi Telegram, dan disimpan bersama record live baru. Ia tidak membuang sinyal.

> **Peringatan metodologis:** seluruh angka adalah *context score* deterministik, bukan probabilitas menang. Bobot adalah baseline yang dapat diaudit dan harus dikalibrasi dari journal live/out-of-sample; bukan hasil optimasi palsu pada data simulasi.

## Arsitektur

```text
closed OHLC + canonical smc.model()
  ├─ Step 0 data_quality.py + metadata resample
  ├─ Step 1 regime.py
  ├─ Step 2 mtf.py
  ├─ Step 3 liqmap.py
  ├─ Step 4 fvg_quality.py
  └─ Step 5 confidence.py
         ↓
  Step 6 state.json + intel_{SYMBOL}.json + dashboard + Telegram + journal snapshot
```

Semua modul dipanggil dari `main.Engine.analyze()` sesudah analisis v20 selesai. Exception pada intelligence ditangkap per pair; engine lama tetap berjalan.

## Step 0 — Candle/resample integrity

`resample.py` sekarang menambahkan:

- `n`: jumlah unit candle sumber yang benar-benar membentuk bucket;
- `expected_n`: unit yang seharusnya ada;
- `coverage`: `min(1, n / expected_n)`;
- `complete`: `coverage == 1`;
- `source_step`: median jarak waktu candle sumber.

Unit `n` tetap konsisten saat H1 bootstrap (`n=12`) diubah menjadi H4 (`expected_n=48`). Forex W memakai lima hari perdagangan. Holiday/provider gap tetap terlihat sebagai coverage rendah dan tidak diisi dengan OHLC sintetis.

`intel/data_quality.py` memantau source gap, duplicate timestamp, usia candle, dan incomplete resample. Pada Phase 1, defect memberi penalty confidence tetapi **tidak menghapus candle dan tidak memblokir signal**.

Arti status:

- `HEALTHY` ≥ 90;
- `DEGRADED` 70–89.9;
- `POOR` < 70.

## Step 1 — Market Regime

Regime dipisahkan menjadi **arah** dan **fase**, bukan ditentukan satu indikator:

1. arah swing/internal structure dari canonical SMC;
2. Kaufman-style efficiency ratio:
   `abs(close[t] - close[t-n]) / sum(abs(close[i] - close[i-1]))`;
3. percentile ATR pada history sampai 120 bar;
4. ATR sekarang / median ATR history;
5. average true range 8 bar / baseline sebelumnya;
6. recent CHoCH, opposing structure, dan displacement.

Klasifikasi:

| State | Arti operasional |
|---|---|
| `BULLISH_TREND` / `BEARISH_TREND` | Struktur searah dan perjalanan harga cukup efisien. |
| `BULLISH_EXPANSION` / `BEARISH_EXPANSION` | Trend searah disertai pelebaran range/volatilitas. |
| `RANGE` | Efisiensi directional lemah; jangan mengejar breakout tanpa konfirmasi. |
| `COMPRESSION` | ATR percentile, range ratio, dan efficiency rendah; breakout belum dikonfirmasi. |
| `TRANSITION` | Recent CHoCH atau swing/internal conflict; arah belum stabil. |
| `INSUFFICIENT_DATA` | Tidak cukup bar untuk klasifikasi yang layak. |

ATR dipakai sebagai ukuran volatilitas, bukan arah. Ini mengikuti karakter ATR: expanding ATR berarti range membesar, sedangkan low ATR terkait quiet/sideways periods. Threshold regime adalah baseline yang ditampilkan bersama metrik mentah agar bisa direvisi.

## Step 2 — MTF Structure

Bobot awal:

| TF | Bobot |
|---|---:|
| D | 35 |
| H4 | 30 |
| H1 | 20 |
| M15 | 15 |

Conviction per TF berasal dari kondisi struktur:

- swing = internal: 1.00;
- swing saja: 0.78;
- swing berlawanan internal (pullback/conflict): 0.58;
- internal saja: 0.45;
- netral: 0.20.

Regime conflict mengurangi conviction 25%. Output menampilkan bull score, bear score, alignment, TF conflict, dan state tiap TF.

Hierarchy:

- XAUUSD: `H4 → H1 → M15`; D otomatis ditambahkan hanya setelah `config.min_bars("D")` tercapai.
- Crypto: meminta `D → H4 → H1 → M15`; TF yang belum cukup ditandai, tidak dipaksakan.

## Step 3 — Liquidity Map

Level yang dihitung:

- PDH/PDL;
- PWH/PWL;
- Asia 00:00–08:00 UTC;
- London 08:00–16:00 `Europe/London` (DST-aware);
- New York 08:00–17:00 `America/New_York` (DST-aware);
- active EQH/EQL dan swing pool dari canonical SMC.

Lifecycle level:

- `INTACT`: belum ada wick/close melewati level setelah periode selesai;
- `SWEPT`: wick melewati level lalu close kembali;
- `BROKEN`: candle close melewati level.

**Batasan penting:** ini adalah *OHLC-derived price-level proxy*. Ia tidak membuktikan ada resting orders. Likuiditas eksekusi sesungguhnya memerlukan limit-order-book (bid/ask, depth, spread, size). Karena itu dashboard menulis caveat tersebut secara eksplisit.

## Step 4 — FVG Quality (0–100)

Hanya FVG aktif dari canonical `smc.model()` yang dinilai.

| Faktor | Maks | Definisi ringkas |
|---|---:|---|
| MTF alignment | 20 | directional score hierarchy untuk arah FVG |
| Liquidity sweep | 15 | sweep searah ≤12 candle sebelum FVG |
| Displacement | 20 | body/ATR + body/range candle impuls |
| Regime compatibility | 15 | cocok/tidak dengan arah dan fase regime |
| Freshness | 10 | umur + persentase mitigasi wick |
| Active-leg location | 10 | posisi midpoint FVG pada leg BOS/CHoCH aktif |
| Formation session | 5 | konteks waktu; bukan Session Intelligence statistik |
| Data integrity | 5 | data-health score |

Grade: A+ ≥90, A ≥80, B ≥70, C ≥60, D <60. FVG lama/termitigasi tidak hilang secara diam-diam; freshness score turun dan alasan ditampilkan.

Perbaikan utama dibanding premium/discount lama: lokasi FVG dihitung terhadap **leg struktur aktif yang menghasilkan BOS/CHoCH**, bukan sekadar swing high dan swing low terakhir yang mungkin tidak satu leg.

## Step 5 — Transparent Confidence

Satu skor menggantikan kebutuhan membuat skor kelima yang tidak dapat dijelaskan.

| Komponen | Maks |
|---|---:|
| Market regime | 20 |
| MTF structure | 25 |
| Liquidity context | 15 |
| Best aligned FVG | 25 |
| Active-leg location | 10 |
| Data integrity | 5 |

Penalty terpisah dan terlihat: TF conflict, transition, compression, no active FVG, dan poor data. Engine menghitung sisi bull dan bear; jika spread <8, action menjadi `MIXED_WATCH`. Output action: `BUY_WATCH`, `SELL_WATCH`, `MIXED_WATCH`, atau `NO_EDGE`.

Label HIGH/GOOD/MODERATE/LOW **bukan** persentase peluang menang. Tidak ada threshold yang menolak signal pada Phase 1.

## Step 6 — Live integration

### Dashboard

Panel **Market Intelligence** menampilkan:

- regime + kekuatan dan alasan;
- MTF bias/alignment/conflict;
- transparent confidence breakdown + penalties;
- FVG terbaik dan rincian seluruh faktor;
- hierarchy table;
- liquidity map + lifecycle;
- data-health dan glossary arti state.

### Telegram

Notifikasi monitor/signal yang memang sudah akan dikirim mendapat suffix: regime, MTF alignment, best FVG, dan context score. Phase 1 tidak membuat spam notification baru setiap lima menit.

### Journal

Record `src=live` baru mendapat immutable `intel` snapshot. Record replay tidak ditempeli konteks hari ini; historical intelligence hanya boleh dibuat melalui candle-by-candle replay agar tidak look-ahead/mislabel.

### Persistence

- `store/intel/{SYMBOL}.json`: latest full snapshot + bounded compact history;
- `_site/data/intel_{SYMBOL}.json`: salinan publik untuk audit;
- `state.json → pairs[SYMBOL].intel`: snapshot dashboard.

## Konfigurasi

```text
INTEL_ENABLED=true       # kill switch
INTEL_MODE=live          # live | shadow
INTEL_HISTORY_LIMIT=5000
INTEL_REGIME_LOOKBACK=20
```

- `live`: dashboard + Telegram + journal;
- `shadow`: dashboard/persistence tetap ada, tetapi suffix Telegram dimatikan;
- `INTEL_ENABLED=false`: modul tidak dipanggil dan legacy decisions/journal kembali seperti v20.

`gate` sengaja tidak disediakan pada paket ini agar threshold yang belum dikalibrasi tidak dapat memblokir setup.

## Validasi paket

Dilakukan pada source terlampir:

1. Python compile seluruh engine;
2. `node --check docs/app.js`;
3. workflow YAML parse;
4. lima unit test untuk resample, hierarchy, regime, dan non-blocking confidence;
5. offline simulation XAUUSD + BTCUSDT;
6. semantic regression: legacy `overall`, per-TF signal, counts, dan journal identik setelah field intel diabaikan;
7. simulation `INTEL_ENABLED=false`: legacy decisions dan journal identik dengan baseline.

Simulasi membuktikan integrasi/runtime, **bukan** profitability atau optimalitas bobot. Tahap berikutnya adalah observasi live, lalu evaluasi per score bucket terhadap expectancy/out-of-sample.

## Referensi desain

- pandas `DataFrame.resample` menjelaskan kontrol bucket/label/closed untuk time-series aggregation: https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.resample.html
- Fidelity, *Average True Range*: ATR mengukur volatilitas dan memasukkan gap; expanding ATR berarti range membesar dan ATR tidak directional: https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr
- QuantConnect, *Kaufman Efficiency Ratio*: referensi indikator untuk rasio efisiensi arah/path: https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators/kaufman-efficiency-ratio
- CME Group, *Liquidity Tool Methodology*: liquidity order-book dibangun dari bid/ask orders, quantities, depth, spread, dan cost-to-trade. Ini alasan liquidity map OHLC diberi label proxy, bukan order-book liquidity: https://www.cmegroup.com/education/articles-and-reports/understanding-the-cme-liquidity-tool-methodology
- OANDA, *Hours of operation*: sesi forex global dan perubahan DST mendukung penggunaan timezone-aware New York/London: https://www.oanda.com/bvi-en/cfds/hours-of-operation/
