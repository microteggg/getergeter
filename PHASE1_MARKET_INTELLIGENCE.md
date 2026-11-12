# Phase 1 Market Intelligence v21 (included)

This package preserves the v21 live, non-blocking intelligence layer and adds Auto Fib v2 above it.

Included modules:

- `engine/intel/regime.py` — trend, expansion, range, compression, and transition from structure, efficiency, ATR percentile, and range expansion.
- `engine/intel/mtf.py` — D/H4/H1/M15 hierarchy; XAUUSD falls back to H4 when D is insufficient.
- `engine/intel/liqmap.py` — PDH/PDL and PWH/PWL price-level proxies with INTACT/SWEPT/BROKEN lifecycle.
- `engine/intel/fvg_quality.py` — explainable FVG score components.
- `engine/intel/confidence.py` — transparent context score with separate penalties; it is not represented as win probability.
- `engine/resample.py` — `n`, `expected_n`, `coverage`, `complete`, and `source_step` metadata.

Outputs:

- `store/intel/{SYMBOL}.json`
- `_site/data/intel_{SYMBOL}.json`
- `state.json → pairs[SYMBOL].intel`
- Dashboard Market Intelligence panel

Emergency switch: `INTEL_ENABLED=false`. Intelligence failures are non-blocking and do not stop legacy analysis, journaling, monitoring, or chart creation.
