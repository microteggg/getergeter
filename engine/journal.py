"""Jurnal sinyal permanen – v23 final rules.

store/journal/{SYM}.json
  setups : setiap SETUP per TF (pertama kali valid) + hasilnya
  entries: setiap ENTRY top-down (BIAS→SETUP→CONFIRM) + hasilnya

Aturan:
- Record yang sudah tercatat TIDAK PERNAH diubah/dihapus (anti-repaint). Hanya kolom hasil yang diperbarui.
- src = "live"   : tercatat saat engine berjalan
  src = "replay" : direkonstruksi dari data historis (logika sama).

Hasil memakai management final (ov = 3): TP1 1R menutup 40%, runner 60% ke 2R–3R; BE hanya setelah MFE 1,5R atau protected swing; partial exit hanya pada displacement lawan; full exit jika protected swing patah oleh candle close.
"""
import json
import os

import config
from resample import TF_SEC
from trading_rules import managed_outcome

OUTCOME_VERSION = 3


def outcome(rec, bars):
    return managed_outcome(
        rec, bars, tp1_close_pct=config.TP1_CLOSE_PCT,
        opposite_close_pct=config.OPPOSITE_DISPLACEMENT_CLOSE_PCT,
        be_trigger_r=config.BE_TRIGGER_R,
        protected_pivot_len=config.PROTECTED_SWING_PIVOT_LEN,
        atr_len=config.ATR_LEN, sl_buffer_atr=config.SL_BUFFER_ATR,
        displacement_atr=config.DISPLACEMENT_ATR,
        displacement_body_ratio=config.DISPLACEMENT_BODY_RATIO)


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
        self.closed_now = []          # entry yang baru selesai (management final) pada run ini

    def merge(self, kind, recs, now):
        """Tambahkan record baru. Return list record baru."""
        book = self.setups if kind == "setups" else self.entries
        new = []
        for r in recs:
            if r["id"] in book:
                continue
            sec = TF_SEC.get(r.get("tf"), 900)
            rec = {k: v for k, v in r.items() if not k.startswith("_")}
            rec.update(symbol=self.sym, first_seen=now, src="live" if r["close_t"] >= now - 2 * sec else "replay")
            rec.update(outcome(rec, []))
            book[r["id"]] = rec
            new.append(rec)
        return new

    @staticmethod
    def _needs(r):
        return r.get("ov") != OUTCOME_VERSION or r.get("status") == "OPEN" or r.get("status_pm") == "OPEN"

    def update_outcomes(self, bars_by_tf, entry_tfs):
        """bars_by_tf: {tf: candle tertutup}. entry_tfs: urutan TF (halus→kasar) untuk menilai entry.
        Record lama (ov < 3) dihitung ulang sekali bila candle-nya masih tersimpan."""
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
            if self._needs(r):
                bars = bars_by_tf.get(r["tf"]) or []
                if bars and bars[0]["t"] <= r["close_t"]:
                    r.update(outcome(r, after(bars, r["close_t"])))
        for r in self.entries.values():
            if self._needs(r):
                bars = pick(entry_tfs, r["close_t"])
                if bars:
                    was_open = r.get("status") == "OPEN"
                    r.update(outcome(r, after(bars, r["close_t"])))
                    if was_open and r["status"] != "OPEN":
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


def _stats(rows, status_key, result_key):
    done = [r for r in rows if r.get(status_key, "OPEN") != "OPEN" and r.get(result_key) is not None]
    wins = [r for r in done if r[result_key] > 0]
    gross_w = sum(r[result_key] for r in wins)
    gross_l = -sum(r[result_key] for r in done if r[result_key] < 0)
    return {"total": len(rows), "closed": len(done), "open": len(rows) - len(done), "win": len(wins),
            "winrate": round(100 * len(wins) / len(done), 1) if done else None,
            "net_r": round(gross_w - gross_l, 2), "avg_r": round((gross_w - gross_l) / len(done), 2) if done else None,
            "pf": round(gross_w / gross_l, 2) if gross_l else None}


def stats(rows):
    """Kunci level atas dipertahankan untuk kompatibilitas; 'pm' sekarang berisi management final v23."""
    out = _stats(rows, "status", "result_r")
    out["pm"] = _stats(rows, "status_pm", "result_pm_r")
    return out
