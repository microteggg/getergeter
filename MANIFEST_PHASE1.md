# Manifest paket Phase 1 v21

## File baru

- `engine/intel/__init__.py`
- `engine/intel/common.py`
- `engine/intel/data_quality.py`
- `engine/intel/regime.py`
- `engine/intel/mtf.py`
- `engine/intel/liqmap.py`
- `engine/intel/fvg_quality.py`
- `engine/intel/confidence.py`
- `engine/intel/pipeline.py`
- `engine/intel/store.py`
- `tests/test_phase1_intel.py`
- `PHASE1_MARKET_INTELLIGENCE.md`
- `VERIFICATION_PHASE1.md`
- `PHASE1_v21.patch`
- `MANIFEST_PHASE1.md`

## File yang direvisi

- `engine/resample.py` — metadata coverage/complete untuk candle hasil resample.
- `engine/config.py` — `INTEL_ENABLED`, `INTEL_MODE`, history limit, regime lookback.
- `engine/main.py` — orkestrasi, persistence, state, Telegram, fault isolation.
- `engine/journal.py` — immutable intel snapshot hanya untuk record live baru.
- `docs/index.html` — container panel dan versi v21.
- `docs/app.js` — panel Market Intelligence lengkap.
- `docs/style.css` — layout responsif panel intel.
- `.github/workflows/engine.yml` — variable live/kill-switch dan build marker v21.

## File existing yang dipertahankan

Seluruh file program lain dari source terlampir ikut ada di ZIP agar paket dapat langsung mengganti repository. Tidak ada credential produksi di paket.

## Cara commit

1. Ekstrak ZIP.
2. Salin isi folder ke root repository (pertahankan struktur folder).
3. Pastikan GitHub variable `INTEL_ENABLED` kosong/`true` dan `INTEL_MODE` kosong/`live`.
4. Commit dan jalankan workflow manual sekali.
5. Cek panel Market Intelligence, `_site/data/intel_{SYMBOL}.json`, status Telegram, dan journal record live berikutnya.
6. Jika ada masalah, set `INTEL_ENABLED=false`; legacy decision engine tetap berjalan.
