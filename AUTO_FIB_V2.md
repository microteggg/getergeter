# Auto Fib v2 — Lifecycle specification

## Status

- **FORMING** — structural swing leg exists, but its endpoint is not locked. The endpoint may follow a new extreme.
- **LOCKED** — endpoint pivot has been confirmed after `k` closed candles, pullback reached at least 23.6% by wick, and no candle closed through 100%.
- **EXTENDED** — a candle closed through old 1.0. Anchors stay frozen and extension targets are enabled.
- **REPLACED** — the old Fib became the single dashed “previous Fib” after a complete HL→HH or LH→LL continuation.
- **INVALID** — a candle closed through 100%. Wick-only sweeps do not invalidate it.

## Replacement gate

A same-direction Fib replaces the current Fib only when all conditions are complete:

1. the current Fib is LOCKED;
2. a post-lock HL (bull) or LH (bear) is recorded;
3. a candle **closes** beyond old 1.0;
4. the new HH/LL endpoint is confirmed after `k` closed candles;
5. the new leg pulls back at least 23.6%; and
6. the new leg has not closed through its own origin.

Until then, the old Fib remains primary and price beyond 1.0 is interpreted as extension.

## Levels

Retracement: 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%.

Approved extension notation: **1.236, 1.382, 1.618, 2.236, 2.618**. Here 1.0 is the impulse endpoint.

## Data and repaint controls

- Each timeframe runs an independent lifecycle.
- Only closed candles are processed.
- Endpoint confirmation is available at `pivot_index + k`; no future candle is read early.
- 1.0 break and 100% invalidation use close.
- Pullback depth uses closed-candle high/low, so wick depth counts.
- Internal Fib never replaces the primary swing Fib; `FIB_FALLBACK_INTERNAL` is forced off.

## Persistence and presentation

- Current Fib and exactly one previous Fib are included in chart JSON.
- Previous Fib is drawn thinner/dashed on dashboard and Telegram PNG charts.
- Full lifecycle history is merged idempotently into `store/fib/{SYMBOL}.json` and exported as `_site/data/fib_history_{SYMBOL}.json`.
- Overlap between active and previous golden zones, or an old Fib level inside the active golden zone, produces a `Fib cluster` annotation.
- Monitor events: `FIB_LOCKED`, `FIB_EXTENDED`, `FIB_REPLACED`, and `FIB_INVALID`, each with a human-readable reason.

## Configuration

```text
FIB_ENABLED=true
FIB_PRIMARY_TF=H4
FIB_MIN_ATR=1.0
FIB_LOCK_RETRACE=0.236
FIB_HISTORY_LIMIT=10000
```

`FIB_LOCK_RETRACE` is a decimal ratio, not a percentage string.
