"""Persistent, bounded intelligence snapshots for live review and later calibration."""
from __future__ import annotations

import json
import os

import config
from .common import compact_snapshot


class SnapshotStore:
    def __init__(self, store_dir, symbol):
        self.symbol = symbol
        self.path = os.path.join(store_dir, "intel", f"{symbol}.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {"symbol": symbol, "latest": None, "history": []}

    def add(self, intel):
        self.data["latest"] = intel
        small = compact_snapshot(intel)
        history = self.data.setdefault("history", [])
        if small and (not history or history[-1].get("asof") != small.get("asof")):
            history.append(small)
        limit = int(getattr(config, "INTEL_HISTORY_LIMIT", 5000))
        self.data["history"] = history[-limit:]

    def save(self, public_path=None):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, separators=(",", ":"))
        if public_path:
            os.makedirs(os.path.dirname(public_path), exist_ok=True)
            with open(public_path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, separators=(",", ":"))
