"""Step 4: transparent FVG quality scoring (context score, not a probability)."""
from __future__ import annotations

from .common import clamp, price_session

WEIGHTS = {"mtf": 20, "liquidity": 15, "displacement": 20, "regime": 15,
           "freshness": 10, "location": 10, "session": 5, "data": 5}


def _grade(score):
    return "A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 60 else "D"


def _factor(name, score, maximum, reason):
    return {"name": name, "score": round(clamp(score, 0, maximum), 1), "max": maximum, "reason": reason}


def _active_leg(smc, bars, direction, created):
    events = [e for e in smc.get("events", []) if e.get("scope") == "swing" and e.get("dir") == direction and e.get("index", 0) <= created]
    if not events:
        return None
    e = events[-1]
    lo, hi = sorted((e.get("pivot_index", e["index"]), e["index"]))
    rows = bars[lo:hi + 1]
    if not rows:
        return None
    return {"bottom": min(b["l"] for b in rows), "top": max(b["h"] for b in rows), "event": e.get("id")}


def _mitigation(zone, bars):
    width = max(zone["top"] - zone["bottom"], 1e-12)
    rows = bars[zone["created"] + 1:]
    if not rows:
        return 0.0
    if zone["dir"] == "bull":
        low = min(b["l"] for b in rows)
        return clamp((zone["top"] - low) / width, 0, 1)
    high = max(b["h"] for b in rows)
    return clamp((high - zone["bottom"]) / width, 0, 1)


def _score_one(tf, zone, tfa, mtf, regime, data_score):
    bars, smc, atr = tfa.get("_bars") or [], tfa.get("_smc") or {}, tfa.get("_atr14") or []
    i = min(zone.get("created", 0), len(bars) - 1)
    mid_i = max(0, i - 1)
    bar = bars[mid_i]
    a = atr[mid_i] if atr and mid_i < len(atr) else max(bar["h"] - bar["l"], 1e-12)
    body = abs(bar["c"] - bar["o"])
    body_ratio = body / max(bar["h"] - bar["l"], 1e-12)
    impulse = body / max(a, 1e-12)
    disp_norm = clamp((impulse - 0.35) / 1.15, 0, 1) * 0.65 + clamp((body_ratio - 0.35) / 0.45, 0, 1) * 0.35
    f_disp = _factor("Displacement", 20 * disp_norm, 20,
                     f"Body {impulse:.2f} ATR; body/range {body_ratio:.0%} pada candle impuls.")

    direction = zone["dir"]
    directional = (mtf.get("bull_score") if direction == "bull" else mtf.get("bear_score")) or 0
    f_mtf = _factor("MTF alignment", 20 * directional / 100, 20,
                    f"Skor hierarchy {direction} {directional:.1f}/100.")

    sweeps = [s for s in smc.get("sweeps", []) if s.get("dir") == direction and 0 <= i - s.get("index", -9999) <= 12]
    if sweeps:
        latest = sweeps[-1]
        liq_score = 15 if latest.get("scope") == "swing" or latest.get("pool_label") else 11
        liq_reason = f"Sweep {latest.get('side')} {i - latest.get('index', i)} candle sebelum FVG."
    else:
        liq_score, liq_reason = 2, "Tidak ada sweep searah dalam 12 candle sebelum FVG."
    f_liq = _factor("Liquidity sweep", liq_score, 15, liq_reason)

    rdir, phase = regime.get("direction"), regime.get("phase")
    if rdir == direction and phase in ("TREND", "EXPANSION"):
        reg_score = 15 if phase == "EXPANSION" else 13
    elif phase == "RANGE":
        reg_score = 7
    elif phase == "COMPRESSION":
        reg_score = 4
    elif rdir and rdir != direction:
        reg_score = 0
    else:
        reg_score = 6
    f_reg = _factor("Regime compatibility", reg_score, 15,
                    f"Regime {regime.get('label', 'UNKNOWN')} vs FVG {direction}.")

    age = max(0, len(bars) - 1 - i)
    mitigation = _mitigation(zone, bars)
    age_score = 10 if age <= 5 else 8 if age <= 12 else 6 if age <= 30 else 3 if age <= 80 else 1
    freshness = age_score * (1 - 0.65 * mitigation)
    f_fresh = _factor("Freshness", freshness, 10,
                      f"Umur {age} candle; mitigasi wick {mitigation * 100:.0f}%.")

    leg = _active_leg(smc, bars, direction, i)
    center = (zone["top"] + zone["bottom"]) / 2
    if leg and leg["top"] > leg["bottom"]:
        pos = (center - leg["bottom"]) / (leg["top"] - leg["bottom"])
        favorable = 1 - pos if direction == "bull" else pos
        location = 10 * clamp((favorable - 0.35) / 0.45, 0, 1)
        loc_reason = f"Titik tengah FVG di {pos * 100:.0f}% active BOS/CHoCH leg."
    else:
        pos, location, loc_reason = None, 4, "Active structure leg belum tersedia; tidak diberi nilai penuh."
    f_loc = _factor("Active-leg location", location, 10, loc_reason)

    ses = price_session(zone.get("t", bar["t"]))
    ses_score = 5 if ses == "LONDON_NY_OVERLAP" else 4 if ses in ("LONDON", "NEW_YORK") else 3 if ses == "ASIA" else 1
    f_ses = _factor("Formation session", ses_score, 5,
                    f"FVG terbentuk pada {ses.replace('_', ' ').title()}; ini konteks, bukan Session Intelligence statistik.")
    f_data = _factor("Data integrity", 5 * data_score / 100, 5, f"Data-health score {data_score:.1f}/100.")
    factors = [f_mtf, f_liq, f_disp, f_reg, f_fresh, f_loc, f_ses, f_data]
    score = round(sum(f["score"] for f in factors), 1)
    return {
        "id": zone["id"], "tf": tf, "direction": direction, "top": zone["top"], "bottom": zone["bottom"],
        "created_t": zone.get("t"), "age_bars": age, "mitigation_pct": round(mitigation * 100, 1),
        "score": score, "grade": _grade(score), "factors": factors,
        "active_leg_position_pct": round(pos * 100, 1) if pos is not None else None,
        "status": zone.get("status", "active"),
        "description": "Skor konteks deterministik; bukan probabilitas menang dan belum dikalibrasi sebagai filter.",
    }


def build(tfs, mtf, regimes, data_score):
    rows = []
    for tf in mtf.get("used") or []:
        tfa = tfs.get(tf) or {}
        smc = tfa.get("_smc") or {}
        if not tfa.get("enough"):
            continue
        for zone in smc.get("fvg") or []:
            rows.append(_score_one(tf, zone, tfa, mtf, regimes.get(tf) or {}, data_score))
    rows.sort(key=lambda x: (x["score"], x["created_t"] or 0), reverse=True)
    best = rows[0] if rows else None
    return {"best": best, "active": rows[:12], "weights": WEIGHTS,
            "description": "FVG aktif dinilai dari alignment, sweep, displacement, regime, freshness, active leg, sesi, dan kualitas data."}
