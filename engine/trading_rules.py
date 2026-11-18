"""Aturan trading final v23.

Modul murni/deterministik yang dipakai live dan replay:
- BOS/MSS harus sudah tercatat oleh SMC (break berbasis close, bukan wick).
- Setelah break, retest M15 harus terjadi dalam maksimum 8 candle.
- Entry hanya pada close candle konfirmasi retest.
- FVG tidak pernah menjadi konteks entry tunggal; OB canonical wajib ada.
- Manajemen 40% @ 1R, runner 60% @ 2R-3R, BE tertunda, protected swing,
  partial exit pada displacement lawan, dan full exit pada close yang mematahkan protected swing.
- Risk gate akun: 0,50% normal, maksimal 1% setup terbaik, -3% harian,
  dan berhenti setelah 3 loss berturut-turut.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone

_EPS = 1e-12


def overlap(a, b):
    """True bila dua zona harga beririsan."""
    return bool(a and b and a["bottom"] <= b["top"] and a["top"] >= b["bottom"])


def contextual_entry_zone(sequence):
    """Return zona entry canonical.

    OB canonical wajib ada. Bila FVG pada sequence yang sama beririsan dengan OB,
    area entry dipersempit ke overlap; FVG tanpa OB tidak pernah lolos.
    """
    ob, fvg = sequence.get("ob"), sequence.get("fvg")
    if not ob:
        return None
    z = dict(ob)
    z["kind"] = "OB"
    z["paired_fvg"] = False
    if overlap(ob, fvg):
        z["bottom"] = max(ob["bottom"], fvg["bottom"])
        z["top"] = min(ob["top"], fvg["top"])
        z["mid"] = (z["bottom"] + z["top"]) / 2
        z["kind"] = "OB+FVG"
        z["paired_fvg"] = True
        z["fvg_id"] = fvg["id"]
    return z


def _touch_level(bar, level, buffer_):
    return bar["l"] <= level + buffer_ and bar["h"] >= level - buffer_


def retest_confirmation(bars, events, k, direction, not_before_t, atr_values, max_bars=8, buffer_atr=0.10):
    """Cari BOS/MSS close-confirmed lalu retest dan candle konfirmasi.

    Event SMC sudah dibentuk hanya bila close menembus pivot. Retest harus terjadi
    setelah candle break dan entry berada pada close candle konfirmasi, paling lambat
    ``max_bars`` candle setelah break. Return None bila syarat tidak lengkap.
    """
    if not (0 <= k < len(bars)):
        return None
    candidates = [e for e in events if e.get("dir") == direction and e.get("index", -1) < k
                  and e.get("t", 0) >= not_before_t and 1 <= k - e.get("index", -999) <= max_bars]
    for e in reversed(candidates):
        ei, level = e["index"], e["level"]
        a = atr_values[k] if k < len(atr_values) and atr_values[k] else 0.0
        buf = max(0.0, a * buffer_atr)
        touched = [r for r in range(ei + 1, k + 1) if _touch_level(bars[r], level, buf)]
        if not touched:
            continue
        r = touched[-1]
        b = bars[k]
        if direction == "bull":
            body_ok = b["c"] > b["o"] and b["c"] > level
            follow_ok = k == r or b["c"] > bars[k - 1]["h"]
            invalid = min(x["l"] for x in bars[r:k + 1])
        else:
            body_ok = b["c"] < b["o"] and b["c"] < level
            follow_ok = k == r or b["c"] < bars[k - 1]["l"]
            invalid = max(x["h"] for x in bars[r:k + 1])
        if body_ok and follow_ok:
            return {"event": e, "retest_index": r, "confirm_index": k,
                    "bars_after_break": k - ei, "invalid_level": invalid}
    return None


def position_risk_pct(entry, base_pct=0.50, best_pct=1.00, best_min_rr=2.50):
    """0,50% normal; 1% hanya untuk setup terbaik (RR tinggi + overlap OB/FVG)."""
    zone = entry.get("zone") or {}
    best = bool(entry.get("rr", 0) >= best_min_rr and zone.get("paired_fvg") and
                entry.get("htf_aligned", False) and entry.get("retest_confirmed", False))
    return (best_pct if best else base_pct), best


def _day_key(ts, offset_hours):
    tz = timezone(timedelta(hours=offset_hours))
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def account_risk_snapshot(store_dir, now, daily_loss_pct=3.0, max_consecutive_losses=3, tz_offset=7,
                          default_risk_pct=0.50):
    """Hitung risk lock lintas semua pair dari jurnal persisten."""
    closed = []
    for path in glob.glob(os.path.join(store_dir, "journal", "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                rows = (json.load(fh) or {}).get("entries", {}).values()
        except (OSError, ValueError):
            continue
        for r in rows:
            status = r.get("status_pm", r.get("status", "OPEN"))
            result = r.get("result_pm_r", r.get("result_r"))
            exit_t = r.get("exit_t_pm", r.get("exit_t"))
            if status != "OPEN" and result is not None and exit_t is not None:
                closed.append({"exit_t": exit_t, "result_r": float(result),
                               "risk_pct": float(r.get("risk_pct", default_risk_pct)),
                               "symbol": r.get("symbol")})
    closed.sort(key=lambda x: x["exit_t"])
    today = _day_key(now, tz_offset)
    today_rows = [r for r in closed if _day_key(r["exit_t"], tz_offset) == today]
    net_pct = round(sum(r["result_r"] * r["risk_pct"] for r in today_rows), 3)
    consecutive = 0
    for r in reversed(today_rows):
        if r["result_r"] < 0:
            consecutive += 1
        else:
            break
    reasons = []
    if net_pct <= -abs(daily_loss_pct):
        reasons.append(f"batas kerugian harian {daily_loss_pct:g}% tercapai ({net_pct:+.2f}%)")
    if consecutive >= max_consecutive_losses:
        reasons.append(f"{consecutive} loss berturut-turut (batas {max_consecutive_losses})")
    return {"allowed": not reasons, "day": today, "daily_net_pct": net_pct,
            "daily_closed": len(today_rows), "consecutive_losses": consecutive,
            "reason": "; ".join(reasons) if reasons else None}


def _atr_series(bars, length=14):
    out, trs = [], []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["c"] if i else b["o"]
        tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        out.append(sum(trs[-length:]) / min(len(trs), length))
    return out


def _opposite_displacement(bar, direction, atr_value, atr_mult, body_ratio):
    rng = max(bar["h"] - bar["l"], _EPS)
    body = abs(bar["c"] - bar["o"])
    opposite = bar["c"] < bar["o"] if direction == "bull" else bar["c"] > bar["o"]
    return opposite and body >= atr_mult * max(atr_value, _EPS) and body / rng >= body_ratio


def managed_outcome(rec, bars, *, tp1_close_pct=0.40, opposite_close_pct=0.30,
                    be_trigger_r=1.50, protected_pivot_len=2, atr_len=14,
                    sl_buffer_atr=0.10, displacement_atr=0.80, displacement_body_ratio=0.60):
    """Simulasi aturan management final tanpa keluar hanya karena warna candle.

    Perubahan stop/protected swing baru berlaku mulai candle berikutnya agar tidak
    memakai urutan intrabar yang tidak diketahui.
    """
    bull = rec["dir"] == "bull"
    direction = rec["dir"]
    entry, initial_sl, tp1, tp2 = (float(rec[k]) for k in ("entry", "sl", "tp1", "tp2"))
    risk = abs(entry - initial_sl) or _EPS
    r2 = abs(tp2 - entry) / risk
    atrs = _atr_series(bars, atr_len) if bars else []
    remaining = 1.0
    realized = 0.0
    tp1_hit = False
    partial_exit = False
    be_active = False
    protected = None
    mfe = mae = 0.0
    out = {"status": "OPEN", "result_r": None, "exit_t": None,
           "status_pm": "OPEN", "result_pm_r": None, "exit_t_pm": None,
           "tp1_hit": False, "tp1_t": None, "ambig": False,
           "mfe_r": 0.0, "mae_r": 0.0, "be_active": False,
           "be_t": None, "protected_swing": None, "partial_exit_t": None,
           "partial_exit_r": None, "runner_pct": round(remaining * 100, 1), "ov": 3}

    def rr(price):
        return ((price - entry) if bull else (entry - price)) / risk

    for i, b in enumerate(bars):
        fav = ((b["h"] - entry) if bull else (entry - b["l"])) / risk
        adv = ((entry - b["l"]) if bull else (b["h"] - entry)) / risk
        hard_hit = b["l"] <= initial_sl if bull else b["h"] >= initial_sl
        be_hit = be_active and (b["l"] <= entry if bull else b["h"] >= entry)
        tp1_now = b["h"] >= tp1 if bull else b["l"] <= tp1
        tp2_now = b["h"] >= tp2 if bull else b["l"] <= tp2

        # Stop yang sudah aktif dari candle sebelumnya; ambigu dihitung konservatif.
        if hard_hit or be_hit:
            stop_price = entry if be_hit and not hard_hit else initial_sl
            out["ambig"] = bool(tp1_now or tp2_now)
            realized += remaining * rr(stop_price)
            out.update(status="BE" if stop_price == entry and tp1_hit else "SL",
                       result_r=round(realized, 2), exit_t=b["t"])
            remaining = 0.0
            break

        # Protected swing hanya pecah oleh candle close, bukan wick.
        if protected is not None and ((bull and b["c"] < protected) or (not bull and b["c"] > protected)):
            realized += remaining * rr(b["c"])
            out.update(status="PROTECTED_BREAK", result_r=round(realized, 2), exit_t=b["t"])
            remaining = 0.0
            break

        if not tp1_hit and tp1_now:
            close_pct = min(remaining, tp1_close_pct)
            realized += close_pct * 1.0
            remaining -= close_pct
            tp1_hit = True
            out.update(tp1_hit=True, tp1_t=b["t"])

        if tp2_now and remaining > 0:
            realized += remaining * r2
            remaining = 0.0
            out.update(status="TP2", result_r=round(realized, 2), exit_t=b["t"])
            break

        # Partial early exit hanya displacement lawan; candle beda warna biasa diabaikan.
        if tp1_hit and not partial_exit and remaining > 0 and _opposite_displacement(
                b, direction, atrs[i], displacement_atr, displacement_body_ratio):
            close_pct = min(remaining, opposite_close_pct)
            exit_r = rr(b["c"])
            realized += close_pct * exit_r
            remaining -= close_pct
            partial_exit = True
            out.update(partial_exit_t=b["t"], partial_exit_r=round(exit_r, 2))

        mfe, mae = max(mfe, fav), max(mae, adv)

        # Pivot baru diketahui setelah protected_pivot_len candle di kanannya selesai.
        p = i - protected_pivot_len
        if p >= protected_pivot_len:
            window = bars[p - protected_pivot_len:p + protected_pivot_len + 1]
            if bull and bars[p]["l"] == min(x["l"] for x in window):
                candidate = bars[p]["l"]
                if candidate > entry and (protected is None or candidate > protected):
                    protected = candidate
            elif not bull and bars[p]["h"] == max(x["h"] for x in window):
                candidate = bars[p]["h"]
                if candidate < entry and (protected is None or candidate < protected):
                    protected = candidate

        if tp1_hit and not be_active and (mfe >= be_trigger_r or protected is not None):
            be_active = True
            out.update(be_active=True, be_t=b["t"])
        if protected is not None:
            out["protected_swing"] = round(protected, 10)

    out["mfe_r"], out["mae_r"] = round(mfe, 2), round(mae, 2)
    out["runner_pct"] = round(remaining * 100, 1)
    if out["status"] == "OPEN":
        out["realized_r"] = round(realized, 2)
    out["status_pm"], out["result_pm_r"], out["exit_t_pm"] = out["status"], out["result_r"], out["exit_t"]
    return out
