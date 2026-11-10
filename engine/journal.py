"""Jurnal sinyal permanen (untuk cek ulang & backtest).

store/journal/{SYM}.json
  setups : setiap SETUP per TF (pertama kali valid) + hasilnya
  entries: setiap ENTRY top-down (BIAS→SETUP→CONFIRM) + hasilnya

Aturan:
- Record yang sudah tercatat TIDAK PERNAH diubah/dihapus (anti-repaint). Hanya kolom hasil
  (status, result_r, exit_t, tp1_hit, mfe_r, mae_r) yang diperbarui selama status masih OPEN.
- src = "live"   : tercatat saat engine berjalan (sinyal benar-benar terkirim pada waktunya)
  src = "replay" : direkonstruksi dari data historis (mis. saat pertama dipasang). Logika sama persis.
"""
import json
import os

import config
from analysis import outcome
from resample import TF_SEC

RESULT_KEYS = ("status", "result_r", "exit_t", "tp1_hit", "tp1_t", "mfe_r", "mae_r")


class Journal:
    def __init__(self, store_dir, sym):
        self.sym = sym
        self.path = os.path.join(store_dir, "journal", f"{sym}.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        self.setups = data.get("setups", {})
        self.entries = data.get("entries", {})
        self.closed_now = []          # entry yang baru selesai (TP2/SL) pada run ini

    def merge(self, kind, recs, now):
        """Tambahkan record baru. Return list record baru."""
        book = self.setups if kind == "setups" else self.entries
        new = []
        for r in recs:
            if r["id"] in book:
                continue
            sec = TF_SEC.get(r.get("tf"), 900)
            rec = {k: v for k, v in r.items() if not k.startswith("_")}
            rec.update(symbol=self.sym, first_seen=now, src="live" if r["close_t"] >= now - 2 * sec else "replay",
                       status="OPEN", result_r=None, exit_t=None, tp1_hit=False, tp1_t=None, mfe_r=0.0, mae_r=0.0)
            book[r["id"]] = rec
            new.append(rec)
        return new

    def update_outcomes(self, bars_by_tf, entry_tfs):
        """bars_by_tf: {tf: candle tertutup}. entry_tfs: urutan TF (halus→kasar) untuk menilai entry."""
        def pick(tfs, t):
            for tf in tfs:
                bars = bars_by_tf.get(tf) or []
                if bars and bars[0]["t"] <= t:
                    return bars
            return None

        def after(bars, t):
            lo, hi = 0, len(bars)
            while lo < hi:
                m = (lo + hi) // 2
                if bars[m]["t"] < t:
                    lo = m + 1
                else:
                    hi = m
            return bars[lo:]

        for r in self.setups.values():
            if r["status"] == "OPEN":
                bars = bars_by_tf.get(r["tf"]) or []
                if bars:
                    r.update(outcome(r, after(bars, r["close_t"])))
        for r in self.entries.values():
            if r["status"] == "OPEN":
                bars = pick(entry_tfs, r["close_t"])
                if bars:
                    r.update(outcome(r, after(bars, r["close_t"])))
                    if r["status"] != "OPEN":
                        self.closed_now.append(r)

    def trim(self):
        by_tf = {}
        for r in self.setups.values():
            by_tf.setdefault(r["tf"], []).append(r)
        keep = {}
        for tf, rows in by_tf.items():
            rows.sort(key=lambda r: r["close_t"])
            for r in rows[-config.JOURNAL_SETUP_LIMIT:]:
                keep[r["id"]] = r
        self.setups = keep

    def save(self):
        self.trim()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"setups": self.setups, "entries": self.entries}, fh, separators=(",", ":"))

    def public(self):
        return {"symbol": self.sym,
                "setups": sorted(self.setups.values(), key=lambda r: r["close_t"]),
                "entries": sorted(self.entries.values(), key=lambda r: r["close_t"])}


def stats(rows):
    done = [r for r in rows if r["status"] in ("TP2", "SL")]
    wins = [r for r in done if r["status"] == "TP2"]
    gross_w = sum(r["result_r"] for r in wins)
    gross_l = -sum(r["result_r"] for r in done if r["status"] == "SL")
    return {"total": len(rows), "closed": len(done), "open": len(rows) - len(done), "win": len(wins),
            "winrate": round(100 * len(wins) / len(done), 1) if done else None,
            "net_r": round(gross_w - gross_l, 2), "avg_r": round((gross_w - gross_l) / len(done), 2) if done else None,
            "pf": round(gross_w / gross_l, 2) if gross_l else None}
