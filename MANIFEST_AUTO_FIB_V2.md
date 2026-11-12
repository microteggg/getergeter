# Manifest — getergeter Auto Fib v2 v22

## New files

- `engine/fib_journal.py`
- `engine/intel/__init__.py`
- `engine/intel/regime.py`
- `engine/intel/mtf.py`
- `engine/intel/liqmap.py`
- `engine/intel/fvg_quality.py`
- `engine/intel/confidence.py`
- `tests/test_autofib_v2.py`
- `AUTO_FIB_V2.md`
- `PHASE1_MARKET_INTELLIGENCE.md`
- `VERIFICATION_AUTO_FIB_V2.md`
- `INSTALL_AUTO_FIB_V2.md`
- `AUTO_FIB_V2.patch`

## Revised integration files

- `engine/fibo.py` — complete v2 lifecycle state machine.
- `engine/analysis.py` — lifecycle history handoff.
- `engine/config.py` — v22 settings; internal fallback forced off.
- `engine/main.py` — Fib journal, Phase 1 intel, site/state outputs.
- `engine/monitor.py` — LOCKED/EXTENDED/REPLACED/INVALID events and descriptions.
- `engine/charts.py` — Telegram PNG lifecycle, extensions, previous Fib, clusters.
- `engine/messages.py` — Telegram text status and extension notation.
- `engine/resample.py` — data-integrity metadata.
- `docs/app.js` — dashboard chart and panels.
- `docs/index.html` — Market Intelligence panel slot and v22 marker.
- `.github/workflows/engine.yml` — variables and build-marker validation.
- `.gitignore` — local test output exclusions.

All remaining files are preserved from the supplied source snapshot to make the ZIP directly copyable to the repository root.
