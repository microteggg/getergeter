# Install / test

1. Extract the ZIP.
2. Copy the contents of `getergeter-autofib-v2-v22` to the repository root, preserving paths.
3. Commit and manually run **Market Engine** once.
4. Check:
   - dashboard Fib status labels and extension lines;
   - dashed previous Fib and Fib-cluster annotations;
   - Monitoring Events for FIB LOCKED/EXTENDED/REPLACED/INVALID;
   - Telegram chart PNG;
   - `store/fib/{SYMBOL}.json` on the data branch;
   - `_site/data/fib_history_{SYMBOL}.json` and `intel_{SYMBOL}.json`.

Suggested repository variables:

```text
FIB_ENABLED=true
FIB_PRIMARY_TF=H4
FIB_MIN_ATR=1.0
FIB_LOCK_RETRACE=0.236
FIB_HISTORY_LIMIT=10000
INTEL_ENABLED=true
INTEL_MODE=live
```

Rollback: restore the previous commit. `FIB_ENABLED=false` hides/disables Fib calculation; `INTEL_ENABLED=false` independently disables Market Intelligence.

Do not commit `.env`, Telegram tokens, API keys, runtime `_site/`, or local simulation folders.
