"""Top-down SMC/ICT engine v3 (liquidity-first).

HTF (H4)  : external structure, dealing range, premium/discount, draw on liquidity
Setup (H1): sweep -> displacement -> CISD/MSS -> FVG/OB -> inducement -> retrace -> target liquidity
Trigger (M15): struktur internal searah setup
MA (SMA 20/50/100) & RSI hanya untuk tampilan, tidak menentukan sinyal.
"""
import config
from indicators import ma, rsi, atr
from smc import model
from orderflow import flow_summary

MIN_BARS = 100; ANALYZE_BARS = 500
GRADE_RANK = {"A": 3, "B": 2, "C": 1}
LIQ_NAME = {"EXT": "Swing eksternal", "INT": "Swing internal", "EQH": "Equal highs", "EQL": "Equal lows",
            "PDH": "Previous day high", "PDL": "Previous day low", "ASH": "Asian high", "ASL": "Asian low",
            "LOH": "London high", "LOL": "London low"}
HIGH_VALUE = ("EXT", "EQH", "EQL", "PDH", "PDL", "ASH", "ASL", "LOH", "LOL")


def _r(x, d): return None if x is None else round(x, d)


def _zl(xs, d):
    out = []
    for z in xs or []:
        q = {k: v for k, v in z.items() if k not in ("created", "index", "start", "touch_i")}
        for k in ("top", "bottom", "mid", "level", "price", "hi", "lo", "eq", "ote_top", "ote_bottom"):
            if q.get(k) is not None: q[k] = _r(q[k], d)
        out.append(q)
    return out


def _one(z, d): return _zl([z], d)[0] if z else None
def _near(z, p, a): return z["bottom"] - 0.15 * a <= p <= z["top"] + 0.15 * a


def _empty(res):
    res.update(signal="WAIT", trend="Data belum cukup", structure=None, internal=None, setup=None, checklist=[],
               reason="Data belum cukup.", events=[], internal_events=[], sweeps=[], liquidity=[], fvg=[], ob=[],
               fvg_mit=[], ob_mit=[], dealing_range=None, leg=None, inducement=None, grade=None, quality=[],
               flow=None, series={"times": [], "ma20": [], "ma50": [], "ma100": []})
    return res


def analyze_tf(tf, closed, price, d):
    need = 30 if tf == "D" else 20 if tf == "W" else MIN_BARS
    res = {"tf": tf, "bars": len(closed), "need": need, "enough": len(closed) >= need, "price": _r(price, d)}
    if len(closed) < min(need, 20): return _empty(res)
    bars = closed[-ANALYZE_BARS:]; closes = [b["c"] for b in bars]
    m20, m50, m100 = ma(closes, config.MA_FAST), ma(closes, config.MA_MID), ma(closes, config.MA_LONG)
    rr = rsi(closes, config.RSI_LEN); aa = atr(bars, config.ATR_LEN)
    A = aa[-1] or sum(b["h"] - b["l"] for b in bars[-14:]) / 14
    sm = model(bars, A, config.SWING_LEN, max(A * config.EQUAL_LIQ_ATR, 10 ** (-d)),
               config.INTERNAL_SWING_LEN, config.EXTERNAL_SWING_LEN, config.DISPLACEMENT_ATR, config.STRONG_DISPLACEMENT_ATR)
    last = bars[-1]; dr = sm["dealing_range"]
    seqs = [s for s in sm["sequences"] if not s["invalid"]]
    seq = seqs[-1] if seqs else None
    direction = seq["dir"] if seq else None
    bull = direction == "bull"

    # ---------- POI yang masih layak (fresh/tapped, atau baru termitigasi di candle terakhir)
    def usable(z):
        return z and (z["status"] in ("fresh", "tapped") or (z["status"] == "mitigated" and z.get("end_t") == last["t"]))
    zones = [z for z in ((seq or {}).get("fvg"), (seq or {}).get("ob")) if usable(z)]
    z = next((x for x in zones if _near(x, price, A)), None)
    retrace = bool(z and last["t"] > seq["event"]["t"])

    # ---------- lokasi: discount (buy) / premium (sell) pada kaki setup
    leg = seq["leg"] if seq else None
    in_ote = in_disc = False
    if leg:
        in_disc = price <= leg["eq"] if bull else price >= leg["eq"]
        if z: in_disc = in_disc or ((z["mid"] <= leg["eq"]) if bull else (z["mid"] >= leg["eq"]))
        in_ote = leg["ote_bottom"] <= price <= leg["ote_top"]

    # ---------- target liquidity: TP1 internal terdekat, TP2 liquidity eksternal / weak level
    tp1 = tp2 = None
    if direction:
        side = "BSL" if bull else "SSL"
        rest = [p for p in sm["liquidity"] if p["resting"] and p["side"] == side and (p["level"] > price if bull else p["level"] < price)]
        rest.sort(key=lambda p: abs(p["level"] - price))
        tp1 = rest[0] if rest else None
        tp2 = next((p for p in rest if p["kind"] in HIGH_VALUE), None)
        if tp2 is None and dr and dr["dir"] == direction:      # fallback: weak high/low dealing range
            weak = dr["hi"] if bull else dr["lo"]
            if weak > price if bull else weak < price: tp2 = {"side": side, "level": weak, "kind": "WEAK"}
        if tp2 is None: tp2 = tp1
        if tp1 and tp2 and abs(tp1["level"] - price) > abs(tp2["level"] - price): tp1 = tp2

    # ---------- skor kualitas setup
    quality = []
    if seq:
        sw, ev, idm = seq["sweep"], seq["event"], seq["inducement"]
        aligned = sm["trend"] == direction or ev["type"] == "MSS"
        quality = [
            [sw["kind"] in HIGH_VALUE, f"Sweep {LIQ_NAME.get(sw['kind'], sw['kind'])}"],
            [bool(ev.get("strong") or seq.get("fvg")), "Displacement kuat / meninggalkan FVG"],
            [ev["type"] == "MSS", "MSS eksternal (bukan hanya CISD internal)"],
            [bool(in_ote or in_disc), "POI di " + ("OTE" if in_ote else "discount" if bull else "premium")],
            [bool(idm and idm["taken"]), "Inducement sudah diambil" if idm else "Inducement belum terbentuk"],
            [aligned, "Searah struktur eksternal " + tf],
            [bool(z and z["status"] == "fresh" or (z and z.get("touch_t") == last["t"])), "POI first touch"],
        ]
    score = sum(1 for q in quality if q[0])
    grade = None if not seq else "A" if score >= 5 else "B" if score >= 3 else "C"
    grade_ok = bool(grade and GRADE_RANK[grade] >= GRADE_RANK.get(config.MIN_GRADE, 2))

    pdl = dr["pd"] if dr else "-"
    checklist = [
        [bool(seq), f"Sweep liquidity valid{(' (' + LIQ_NAME.get(seq['sweep']['kind'], '') + ')') if seq else ''}"],
        [bool(seq), f"Displacement + {seq['event']['type'] if seq else 'CISD/MSS'} terkonfirmasi"],
        [retrace, f"Retrace ke {z['kind'] if z else 'FVG/OB'} kontekstual"],
        [bool(in_disc), f"Lokasi {'discount' if bull else 'premium' if direction else '-'} pada kaki setup"],
        [bool(tp2), "Target liquidity tersedia" + (f" ({tp2['kind']})" if tp2 else "")],
        [grade_ok, f"Kualitas setup {grade or '-'} ({score}/7)"],
    ]
    signal, setup = "WAIT", None
    if seq and retrace and in_disc and tp2 and grade_ok:
        extreme = seq["sweep"]["price"]
        sl = extreme - config.SL_BUFFER_ATR * A if bull else extreme + config.SL_BUFFER_ATR * A
        risk = abs(price - sl)
        rr_t = abs(tp2["level"] - price) / risk if risk else 0
        if rr_t >= config.MIN_TARGET_RR:
            signal = "BUY" if bull else "SELL"
            t1 = tp1["level"] if tp1 and abs(tp1["level"] - price) >= risk else price + (tp2["level"] - price) * 0.5
            setup = {"id": seq["id"], "entry": _r(price, d), "sl": _r(sl, d), "tp1": _r(t1, d), "tp2": _r(tp2["level"], d),
                     "risk": _r(risk, d), "rr": round(rr_t, 2), "grade": grade,
                     "target": {"side": tp2["side"], "kind": tp2["kind"], "level": _r(tp2["level"], d)},
                     "zone": {"kind": z["kind"], "top": _r(z["top"], d), "bottom": _r(z["bottom"], d), "t": z["t"], "status": z["status"]}}
        else:
            checklist.append([False, f"RR ke target {rr_t:.2f} < {config.MIN_TARGET_RR}"])

    trend = "Bullish" if sm["trend"] == "bull" else "Bearish" if sm["trend"] == "bear" else "Sideways"
    reason = (f"{signal} grade {grade}: Sweep {seq['sweep']['kind']} → {seq['event']['type']} → {z['kind']} → target {tp2['kind']}."
              if signal != "WAIT" else
              f"WAIT: setup {grade or '-'} – urutan liquidity→sweep→MSS→POI belum lengkap." if seq else
              "WAIT: belum ada sweep + displacement yang valid.")
    fl = flow_summary(bars, 20) if "v" in last else None
    near_liq = sorted([p for p in sm["liquidity"] if p["resting"]], key=lambda p: abs(p["level"] - price))[:10]
    res.update(trend=trend, structure=sm["trend"], internal=sm["internal_trend"], setup_dir=direction,
               signal=signal, setup=setup, checklist=checklist, reason=reason, grade=grade, quality=quality,
               ma20=_r(m20[-1], d), ma50=_r(m50[-1], d), ma100=_r(m100[-1], d), rsi=_r(rr[-1], 1), atr=_r(A, d),
               premium_discount=pdl,
               last_candle={"t": last["t"], "o": _r(last["o"], d), "h": _r(last["h"], d), "l": _r(last["l"], d), "c": _r(last["c"], d),
                            "dir": "Bullish" if last["c"] >= last["o"] else "Bearish"},
               dealing_range=_one(dr, d), leg=_one(dict(leg, t=seq["sweep"]["t"], dir=direction) if leg else None, d),
               inducement=_one(seq["inducement"] if seq else None, d),
               liquidity=_zl(near_liq, d), events=_zl(sm["events"], d), internal_events=_zl(sm["internal_events"], d),
               sweeps=_zl(sm["sweeps"], d), fvg=_zl(sm["fvg"], d), ob=_zl(sm["order_blocks"], d),
               fvg_mit=_zl(sm["fvg_mit"], d), ob_mit=_zl(sm["ob_mitigated"], d), flow=fl,
               series={"times": [b["t"] for b in bars], "ma20": m20, "ma50": m50, "ma100": m100,
                       "ema20": m20, "ema50": m50, "ema200": m100})
    return res


def overall(tfs, book=None, trades=None):
    b = tfs.get(config.BIAS_TF) or {}; s = tfs.get(config.SETUP_TF) or {}; c = tfs.get(config.CONFIRM_TF) or {}
    want = {"BUY": "bull", "SELL": "bear"}.get(s.get("signal"))
    bias = b.get("structure"); dr = b.get("dealing_range") or {}
    loc = dr.get("pd")
    loc_ok = want is None or loc is None or (want == "bull" and loc == "discount") or (want == "bear" and loc == "premium")
    confirm = c.get("internal") or c.get("structure")
    grade = (s.get("setup") or {}).get("grade")
    bias_ok = want is not None and (bias == want or (bias is None and grade == "A"))
    reasons = [
        [bias is not None, f"HTF {config.BIAS_TF}: struktur eksternal {bias or 'netral'}" + (f", harga di {loc}" if loc else "")],
        [want is not None, f"Setup {config.SETUP_TF}: {s.get('reason', '-')}"],
        [want is not None and confirm == want, f"Konfirmasi {config.CONFIRM_TF}: struktur internal {confirm or 'belum ada'}"],
    ]
    if want: reasons.append([loc_ok, f"Lokasi HTF {'discount' if want == 'bull' else 'premium'} " + ("✓" if loc_ok else "(entry melawan lokasi HTF)")])
    if want and bias_ok and confirm == want and (loc_ok or grade == "A"):
        return {"signal": s["signal"], "setup": s.get("setup"), "reasons": reasons,
                "summary": f"{s['signal']} valid (grade {grade}): HTF {config.BIAS_TF} → setup {config.SETUP_TF} → trigger {config.CONFIRM_TF}."}
    return {"signal": "WAIT", "setup": None, "pending": s.get("signal"), "reasons": reasons,
            "summary": "WAIT – top-down liquidity sequence belum selaras."}
