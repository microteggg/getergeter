# Verification — Auto Fib v2

## Automated checks

- Python compile: PASS
- JavaScript syntax (`docs/app.js`, `worker/worker.js`): PASS
- GitHub workflow YAML parse: PASS
- Deterministic unit tests: 8/8 PASS
- Offline engine simulation: PASS (XAUUSD + BTCUSDT, 14 sequential runs)
- Strict JSON parse of all generated site, Fib-journal, and intel files: PASS
- Persistent Fib journal idempotence: PASS

## Unit-test coverage

1. LOCKED requires endpoint confirmation and ≥23.6% retracement.
2. Wick above 1.0 does not trigger EXTENDED; close above 1.0 does.
3. Replacement waits for complete HL→HH and new-leg confirmation/retracement.
4. Replaced Fib is retained as the previous Fib.
5. Close through 100% triggers INVALID.
6. Approved extension levels calculate correctly.
7. Prefix-invariance test confirms no lookahead.
8. Cluster overlap and persistent history deduplication work.

## Offline simulation observations

- Engine completed sequential runs without aborting.
- Both symbols emitted Market Intelligence snapshots.
- BTCUSDT produced a current LOCKED primary Fib and persisted lifecycle history.
- XAUUSD had no current primary Fib at the final simulated close, while historical Fib events remained persisted. This is valid behavior: an invalid Fib stays historical and a new primary is not fabricated before another leg satisfies LOCKED.

## Still requires live verification

- Browser appearance at deployed viewport sizes.
- Telegram delivery with real credentials and production chart captions.
- Real-feed threshold calibration and market profitability.
- Long-running storage growth on the data branch.
