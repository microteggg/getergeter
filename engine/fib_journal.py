"""Persistent immutable Auto Fib v2 lifecycle journal."""
import json
import os

import config


class FibJournal:
    def __init__(self, store_dir, symbol):
        self.symbol = symbol
        self.path = os.path.join(store_dir, "fib", f"{symbol}.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {"version": 2, "symbol": symbol, "events": [], "cycles": {}}
        self.data.setdefault("events", [])
        self.data.setdefault("cycles", {})

    def merge(self, tfs, now):
        known = {e["id"] for e in self.data["events"]}
        added = []
        for tf, analysis in tfs.items():
            for row in analysis.get("_fib_history") or []:
                event, fib = row["event"], row["fib"]
                eid = f"{tf}:{event['id']}"
                if eid in known:
                    continue
                rec = {**event, "id": eid, "tf": tf, "first_seen": now, "fib": fib}
                self.data["events"].append(rec)
                known.add(eid)
                added.append(rec)
                cycle = self.data["cycles"].setdefault(fib["id"], {
                    "id": fib["id"], "tf": tf, "dir": fib["dir"], "scope": "swing",
                    "anchor100": fib["anchor100"], "anchor0": fib["anchor0"], "states": []})
                if not cycle["states"] or cycle["states"][-1]["status"] != fib["status"]:
                    cycle["states"].append({"status": fib["status"], "t": event["t"], "event_id": eid})
        self.data["events"] = self.data["events"][-config.FIB_HISTORY_LIMIT:]
        self.data["updated_ts"] = now
        return added

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, separators=(",", ":"), ensure_ascii=False)

    def public(self):
        return {"version": 2, "symbol": self.symbol, "updated_ts": self.data.get("updated_ts"),
                "events": self.data["events"], "cycles": list(self.data["cycles"].values())}
