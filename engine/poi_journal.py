"""Persistent FVG/OB lifecycle journal used only for history and chart overlays.

Trading decisions keep using the canonical snapshot from ``smc.model``.  This
journal prevents an older zone from silently disappearing when its origin rolls
out of the finite candle store.  IDs are deterministic, merges are idempotent,
and an archived active zone is advanced with newly closed candles.
"""
import json
import os

import config

_EPS = 1e-12


def _terminal(z):
    return z.get("status") in ("filled", "invalid")


def _advance(z, bars, after_t):
    if _terminal(z):
        return
    bull = z.get("dir") == "bull"
    top, bottom = float(z["top"]), float(z["bottom"])
    mid = float(z.get("mid", (top + bottom) / 2))
    height = max(top - bottom, _EPS)
    depth = float(z.get("mitigation_pct") or 0) / 100
    for b in bars:
        if b["t"] <= after_t or b["t"] <= z.get("t", 0):
            continue
        overlap = b["l"] <= top and b["h"] >= bottom
        penetration = ((top - b["l"]) / height if bull else (b["h"] - bottom) / height)
        depth = max(depth, min(1.0, max(0.0, penetration)))
        if overlap and not z.get("touch_t"):
            z["touch_t"] = b["t"]
        if z.get("kind") == "FVG":
            if (bull and b["l"] <= bottom) or (not bull and b["h"] >= top):
                z.update(status="filled", filled_t=b["t"], end_t=b["t"], mitigation_pct=100.0)
                return
            z["status"] = "tapped" if z.get("touch_t") else "fresh"
        else:
            if not z.get("ce_t") and ((bull and b["l"] <= mid) or (not bull and b["h"] >= mid)):
                z["ce_t"] = b["t"]
            if not z.get("deep_t") and ((bull and b["l"] < bottom) or (not bull and b["h"] > top)):
                z["deep_t"] = b["t"]
            if (bull and b["c"] < bottom) or (not bull and b["c"] > top):
                z.update(status="invalid", end_t=b["t"], mitigation_pct=100.0)
                return
            z["status"] = ("deep" if z.get("deep_t") else "CE" if z.get("ce_t") else
                           "tapped" if z.get("touch_t") else "fresh")
        z["mitigation_pct"] = round(depth * 100, 1)


class PoiJournal:
    def __init__(self, store_dir, symbol):
        self.symbol = symbol
        self.path = os.path.join(store_dir, "poi", f"{symbol}.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {"version": 1, "symbol": symbol, "timeframes": {}}

    def merge(self, tfs, closed, now):
        new = 0
        for tf, analysis in tfs.items():
            section = self.data["timeframes"].setdefault(tf, {"checked_t": 0, "zones": {}})
            zones = section.setdefault("zones", {})
            previous_check = int(section.get("checked_t") or 0)
            incoming = ((analysis.get("fvg") or []) + (analysis.get("fvg_mit") or []) +
                        (analysis.get("ob") or []) + (analysis.get("ob_mit") or []))
            current_ids = set()
            for row in incoming:
                z = dict(row)
                zid = z.get("id")
                if not zid:
                    continue
                current_ids.add(zid)
                old = zones.get(zid)
                if old is None:
                    z["first_seen"] = now
                    zones[zid] = z
                    new += 1
                else:
                    first_seen = old.get("first_seen", now)
                    old.update(z)
                    old["first_seen"] = first_seen
            bars = closed.get(tf) or []
            for zid, z in zones.items():
                if zid not in current_ids:
                    _advance(z, bars, previous_check)
            if bars:
                section["checked_t"] = max(previous_check, bars[-1]["t"])
            active = [z for z in zones.values() if not _terminal(z)]
            done = sorted((z for z in zones.values() if _terminal(z)), key=lambda x: x.get("end_t") or x.get("t", 0))
            keep = active + done[-config.POI_HISTORY_LIMIT:]
            section["zones"] = {z["id"]: z for z in keep}
        self.data["updated_ts"] = now
        return new

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, separators=(",", ":"))

    def public(self):
        out = {"version": 1, "symbol": self.symbol, "updated_ts": self.data.get("updated_ts"), "tf": {}}
        for tf, section in self.data.get("timeframes", {}).items():
            rows = sorted(section.get("zones", {}).values(), key=lambda z: (z.get("t", 0), z.get("id", "")))
            out["tf"][tf] = {
                "fvg": [z for z in rows if z.get("kind") == "FVG" and z.get("status") != "filled"],
                "fvg_mit": [z for z in rows if z.get("kind") == "FVG" and z.get("status") == "filled"],
                "ob": [z for z in rows if z.get("kind") == "OB" and z.get("status") != "invalid"],
                "ob_mit": [z for z in rows if z.get("kind") == "OB" and z.get("status") == "invalid"],
            }
        return out
