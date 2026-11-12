"""Phase 1 Market Intelligence.

This package is deliberately read-only with respect to the legacy decision engine: it
observes closed candles and existing SMC output, then emits explainable context.  In
``live`` mode the context is published to the dashboard, Telegram and new journal
records, but it never accepts/rejects a legacy signal.
"""

from .pipeline import build_intelligence, telegram_suffix
from .store import SnapshotStore

__all__ = ["build_intelligence", "telegram_suffix", "SnapshotStore"]
