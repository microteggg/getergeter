"""Jurnal sinyal permanen (untuk cek ulang & backtest) – v14.

store/journal/{SYM}.json
  setups : setiap SETUP per TF (pertama kali valid) + hasilnya
  entries: setiap ENTRY top-down (BIAS→SETUP→CONFIRM) + hasilnya

Aturan:
- Record yang sudah tercatat TIDAK PERNAH diubah/dihapus (anti-repaint). Hanya kolom hasil yang diperbarui.
- src = "live"   : tercatat saat engine berjalan
  src = "replay" : direkonstruksi dari data historis (logika sama).

Hasil dihitung dengan DUA mode manajemen posisi (ov = 2):
  FULL : seluruh posisi ke TP2 atau SL (−1R). TP1 hanya penanda.
  PM   : 50% ditutup di TP1, sisa SL dipindah ke entry (break-even), sisa ke TP2.
         Hasil = 0.5×R(TP1) + 0.5×(R(TP2) atau 0).  Status: TP2 / BE / SL / OPEN.
Candle ambigu (SL dan TP tersentuh di candle yang sama, urutan tidak diketahui) → dihitung yang terburuk
dan ditandai ambig = True. MFE/MAE dihitung sampai posisi FULL keluar dan konsisten dengan status.
"""
import json
import os

import config
from resample import TF_SEC

OUTCOME_VERSION = 2


def outcome(rec, bars):
    """bars = candle (urut waktu) yang DIBUKA pada/sesudah waktu keputusan (close_t)."""
    bull = rec["dir"] == "bull"
    entry, sl, tp1, tp2 = rec["entry"], rec["sl"], rec["tp1"], rec["tp2"]
    risk = abs(entry - sl) or 1e-12
    r1, r2 = abs(tp1 - entry) / risk, abs(tp2 - entry) / risk
    o = {"status": "OPEN", "result_r": None, "exit_t": None,
         "status_pm": "OPEN", "result_pm_r": None, "exit_t_pm": None,
         "tp1_hit": False, "tp1_t": None, "ambig": False, "mfe_r": 0.0, "mae_r": 0.0, "ov": OUTCOME_VERSION}
    full_done = pm_done = False
    mfe = mae = 0.0
    for b in bars:
        fav = ((b["h"] - entry) if bull else (entry - b["l"])) / risk
        adv = ((entry - b["l"]) if bull else (b["h"] - entry)) / risk
        hit_sl = b["l"] <= sl if bull else b["h"] >= sl
        hit_t1 = b["h"] >= tp1 if bull else b["l"] <= tp1
        hit_t2 = b["h"] >= tp2 if bull else b["l"] <= tp2
        hit_be = b["l"] <= entry if bull else b["h"] >= entry

        if not o["tp1_hit"]:
            if hit_sl:                       # SL sebelum TP1 (bila TP1 juga kena di candle ini → ambigu, dihitung SL)
                o["ambig"] = hit_t1
                mae = max(mae, 1.0)
                o.update(status="SL", result_r=-1.0, exit_t=b["t"], status_pm="SL", result_pm_r=-1.0, exit_t_pm=b["t"])
                full_done = pm_done = True
                break
            mfe, mae = max(mfe, fav), max(mae, adv)
            if hit_t1:
                o.update(tp1_hit=True, tp1_t=b["t"])
                if hit_t2:
                    mfe = max(mfe, r2)
                    o.update(status="TP2", result_r=round(r2, 2), exit_t=b["t"],
                             status_pm="TP2", result_pm_r=round(0.5 * r1 + 0.5 * r2, 2), exit_t_pm=b["t"])
                    full_done = pm_done = True
                    break
            continue

        # ---- setelah TP1 tersentuh ----
        if not full_done:
            if hit_sl:
                o["ambig"] = o["ambig"] or hit_t2
                mae = max(mae, 1.0)
                o.update(status="SL", result_r=-1.0, exit_t=b["t"])
                full_done = True
            elif hit_t2:
                mfe = max(mfe, r2)
                o.update(status="TP2", result_r=round(r2, 2), exit_t=b["t"])
                full_done = True
            else:
                mfe, mae = max(mfe, fav), max(mae, adv)
        if not pm_done:
            if hit_be:                       # BE & TP2 di candle yang sama → dihitung BE (konservatif)
                o["ambig"] = o["ambig"] or hit_t2
                o.update(status_pm="BE", result_pm_r=round(0.5 * r1, 2), exit_t_pm=b["t"])
                pm_done = True
            elif hit_t2:
                o.update(status_pm="TP2", result_pm_r=round(0.5 * r1 + 0.5 * r2, 2), exit_t_pm=b["t"])
                pm_done = True
        if full_done and pm_done:
            break
    o["mfe_r"], o["mae_r"] = round(mfe, 2), round(mae, 2)
    return o


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
        self.closed_now = []          # entry yang baru selesai (FULL: TP2/SL) pada run ini

    def merge(self, kind, recs, now, intel=None):
        """Tambahkan record baru. Return list record baru."""
        book = self.setups if kind == "setups" else self.entries
        new = []
        for r in recs:
            if r["id"] in book:
                continue
            sec = TF_SEC.get(r.get("tf"), 900)
            rec = {k: v for k, v in r.items() if not k.startswith("_")}
            src = "live" if r["close_t"] >= now - 2 * sec else "replay"
            rec.update(symbol=self.sym, first_seen=now, src=src)
            # Phase 1: immutable context as it was when this record first appeared.
            # Replay records are not stamped with today's context: reconstructing a
            # historical score requires a dedicated candle-by-candle replay.
            if intel is not None and src == "live":
                rec["intel"] = intel
            rec.update(outcome(rec, []))
            book[r["id"]] = rec
            new.append(rec)
        return new

    @staticmethod
    def _needs(r):
        return r.get("ov") != OUTCOME_VERSION or r.get("status") == "OPEN" or r.get("status_pm") == "OPEN"

    def update_outcomes(self, bars_by_tf, entry_tfs):
        """bars_by_tf: {tf: candle tertutup}. entry_tfs: urutan TF (halus→kasar) untuk menilai entry.
        Record lama (ov < 2) dihitung ulang sekali bila candle-nya masih tersimpan."""
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
    """Kunci level atas = mode FULL (kompatibel v13); 'pm' = mode TP1 50% + break-even."""
    out = _stats(rows, "status", "result_r")
    out["pm"] = _stats(rows, "status_pm", "result_pm_r")
    return out
