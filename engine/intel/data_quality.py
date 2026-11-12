"""Step 0: observable candle and resample integrity.

No legacy candle is deleted and no signal is blocked.  The module reports defects and
adds a confidence penalty, which makes live review possible without silently changing
v20 behaviour.
"""
from __future__ import annotations

import config
from resample import TF_SEC
from .common import clamp


def _closed(rows, tf, now):
    sec = TF_SEC[tf]
    return [b for b in rows if b.get("t", 0) + sec <= now]


def _source_gaps(rows, step, market):
    gaps = []
    for left, right in zip(rows, rows[1:]):
        delta = right["t"] - left["t"]
        if delta <= step:
            continue
        # Forex weekend closure is expected; shorter discontinuities are not.
        if market == "forex" and delta >= 40 * 3600:
            continue
        gaps.append({"from": left["t"], "to": right["t"], "missing": max(0, delta // step - 1)})
    return gaps


def summarize(stores, market, now):
    source_tf = "M1" if market == "crypto" else "M5"
    step = TF_SEC[source_tf]
    source = sorted(stores.get(source_tf) or [], key=lambda b: b["t"])
    closed_source = _closed(source, source_tf, now)
    times = [b["t"] for b in closed_source]
    duplicate = len(times) - len(set(times))
    gaps = _source_gaps(closed_source[-2000:], step, market)

    tf_rows = {}
    incomplete_total = complete_total = 0
    for tf, rows in stores.items():
        if tf not in TF_SEC:
            continue
        recent = _closed(rows, tf, now)[-120:]
        measured = [b for b in recent if b.get("expected_n")]
        incomplete = [b for b in measured if not b.get("complete", True)]
        coverage = (sum(float(b.get("coverage", 1.0)) for b in measured) / len(measured)) if measured else 1.0
        tf_rows[tf] = {
            "closed": len(recent), "measured": len(measured), "incomplete": len(incomplete),
            "coverage_pct": round(100 * coverage, 1),
            "last_incomplete_t": incomplete[-1]["t"] if incomplete else None,
        }
        incomplete_total += len(incomplete)
        complete_total += len(measured)

    latest_t = closed_source[-1]["t"] if closed_source else None
    age = max(0, now - (latest_t + step)) if latest_t else None
    expected_run_gap = max(3 * step, 20 * 60)
    stale = age is None or age > expected_run_gap
    incomplete_ratio = incomplete_total / complete_total if complete_total else 0.0
    score = 100.0
    score -= min(30.0, sum(g["missing"] for g in gaps) * 4.0)
    score -= min(15.0, duplicate * 5.0)
    score -= min(35.0, incomplete_ratio * 100.0)
    if stale:
        score -= 20.0
    score = round(clamp(score), 1)
    status = "HEALTHY" if score >= 90 else "DEGRADED" if score >= 70 else "POOR"

    notes = []
    if duplicate:
        notes.append(f"{duplicate} timestamp candle sumber duplikat.")
    if gaps:
        notes.append(f"{len(gaps)} gap sumber terdeteksi pada 2.000 candle terakhir.")
    if incomplete_total:
        notes.append(f"{incomplete_total}/{complete_total} candle resample terukur tidak lengkap.")
    if stale:
        notes.append("Candle sumber terakhir lebih tua dari batas pemantauan.")
    if not notes:
        notes.append("Tidak ada gap, duplikat, atau resample tidak lengkap pada jendela pemantauan.")

    return {
        "status": status, "score": score, "source_tf": source_tf, "last_source_t": latest_t,
        "age_sec": age, "duplicates": duplicate, "gap_count": len(gaps),
        "missing_source_bars": sum(g["missing"] for g in gaps), "recent_gaps": gaps[-5:],
        "resample_incomplete": incomplete_total, "resample_measured": complete_total,
        "timeframes": tf_rows, "notes": notes,
        "blocking": False,
        "description": "Observasi integritas data; Phase 1 live tidak memblokir sinyal lama.",
    }
