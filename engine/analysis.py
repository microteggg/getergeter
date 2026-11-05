"""Top-down SMC/ICT engine v4 (liquidity-first).

HTF  (BIAS_TF, H4)   : narrative = struktur eksternal + draw on liquidity + premium/discount
Setup (SETUP_TF, H1) : model REVERSAL (sweep -> MSS/CISD -> POI ★) atau CONTINUATION (BOS/run -> POI ★)
Trigger (CONFIRM_TF, M15): struktur internal searah setup
MA (SMA 20/50/100) & RSI hanya tampilan, TIDAK menentukan sinyal.
"""
import config
from indicators import ma, rsi, atr
from smc import model
from orderflow import flow_summary

MIN_BARS = 100
ANALYZE_BARS = 500
GRADE_RANK = {"A": 3, "B": 2, "C": 1}
HIGH_VALUE = ("EXT", "EQH", "EQL", "PDH", "PDL", "ASH", "ASL", "LOH", "LOL")
LIQ_NAME = {"EXT": "swing eksternal", "INT": "swing internal", "EQH": "equal highs", "EQL": "equal lows",
            "PDH": "previous day high", "PDL": "previous day low", "ASH": "Asian high", "ASL": "Asian low",
            "LOH": "London high", "LOL": "London low", "WEAK": "weak high/low"}
ZNAME = {"FVG": "FVG", "IFVG": "IFVG", "OB": "OB", "BB": "Breaker"}


def _cfg(name, default):
    return getattr(config, name, default)


def _r(x, d): return None if x is None else round(x, d)


def _zl(xs, d):
    out = []
    for z in xs or []:
        q = {k: v for k, v in z.items() if k not in ("created", "index", "start", "touch_i", "end_i", "origin", "taken_i", "swing_i", "origin_i")}
        for k in ("top", "bottom", "mid", "level", "price", "hi", "lo", "eq", "ote_top", "ote_bottom"):
            if q.get(k) is not None: q[k] = _r(q[k], d)
        out.append(q)
    return out


def _one(z, d): return _zl([z], d)[0] if z else None


def _empty(res):
    res.update(signal="WAIT", trend="Data belum cukup", structure=None, internal=None, setup=None, checklist=[],
               reason="Data belum cukup.", events=[], internal_events=[], sweeps=[], liquidity=[], fvg=[], ob=[],
               fvg_mit=[], ob_mit=[], dealing_range=None, leg=None, inducement=None, grade=None, quality=[],
               model=None, flow=None, series={"times": [], "ma20": [], "ma50": [], "ma100": [], "ema20": [], "ema50": [], "ema200": []})
    return res


def analyze_tf(tf, closed, price, d):
    need = 30 if tf == "D" else 20 if tf == "W" else MIN_BARS
    res = {"tf": tf, "bars": len(closed), "need": need, "enough": len(closed) >= need, "price": _r(price, d)}
    if len(closed) < min(need, 20): return _empty(res)
    bars = closed[-ANALYZE_BARS:]
    closes = [b["c"] for b in bars]
    m20 = ma(closes, _cfg("MA_FAST", 20)); m50 = ma(closes, _cfg("MA_MID", 50)); m100 = ma(closes, _cfg("MA_LONG", 100))
    rr = rsi(closes, _cfg("RSI_LEN", 14)); aa = atr(bars, _cfg("ATR_LEN", 14))
    A = aa[-1] or sum(b["h"] - b["l"] for b in bars[-14:]) / 14
    sm = model(bars, A,
               k=_cfg("SWING_LEN", 3),
               eq_tolerance=max(A * _cfg("EQUAL_LIQ_ATR", 0.10), 10 ** (-d)),
               k_int=_cfg("INTERNAL_SWING_LEN", 2), k_ext=_cfg("EXTERNAL_SWING_LEN", 5),
               disp_atr=_cfg("DISPLACEMENT_ATR", 0.8), strong_disp_atr=_cfg("STRONG_DISPLACEMENT_ATR", 1.2),
               fvg_min_atr=_cfg("FVG_MIN_ATR", 0.10), run_atr=_cfg("RUN_ATR", 0.6), max_zones=_cfg("MAX_ZONES", 3))
    last = bars[-1]; dr = sm["dealing_range"]

    # ---------- POI ★ yang masih layak (fresh/tapped, atau baru termitigasi di candle terakhir)
    def usable(z):
        return bool(z) and (z["status"] in ("fresh", "tapped") or z.get("end_t") == last["t"])

    seq = None
    for s in reversed(sm["sequences"]):
        if s["invalid"]: continue
        if not ((s["fvg"] or {}).get("context") or (s["ob"] or {}).get("context")): continue
        if usable(s["fvg"]) or usable(s["ob"]): seq = s; break
    direction = seq["dir"] if seq else None
    bull = direction == "bull"

    # retrace = candle terakhir menyentuh POI ★ dan close tidak menembus sisi jauh
    z = None
    if seq:
        for cand in sorted([x for x in (seq["fvg"], seq["ob"]) if usable(x)], key=lambda x: abs(price - x["mid"])):
            touch = last["l"] <= cand["top"] + 0.1 * A if bull else last["h"] >= cand["bottom"] - 0.1 * A
            hold = last["c"] >= cand["bottom"] if bull else last["c"] <= cand["top"]
            near = cand["bottom"] - 0.25 * A <= price <= cand["top"] + 0.25 * A
            if touch and hold and near: z = cand; break
    retrace = bool(z and last["t"] > seq["event"]["t"])

    # ---------- lokasi: discount (BUY) / premium (SELL) pada kaki setup, OTE sebagai bonus
    leg = seq["leg"] if seq else None
    in_disc = in_ote = False
    if leg:
        ref = z["mid"] if z else price
        in_disc = ref <= leg["eq"] if bull else ref >= leg["eq"]
        in_ote = leg["ote_bottom"] <= ref <= leg["ote_top"]

    # ---------- target liquidity: TP1 liquidity terdekat, TP2 liquidity bernilai tinggi / weak level
    tp1 = tp2 = None
    if direction:
        side = "BSL" if bull else "SSL"
        rest = sorted([p for p in sm["liquidity"] if p["resting"] and p["side"] == side and
                       (p["level"] > price if bull else p["level"] < price)], key=lambda p: abs(p["level"] - price))
        tp1 = rest[0] if rest else None
        tp2 = next((p for p in rest if p["kind"] in HIGH_VALUE), None)
        if tp2 is None and dr and dr["dir"] == direction:
            weak = dr["hi"] if bull else dr["lo"]
            if weak > price if bull else weak < price: tp2 = {"side": side, "level": weak, "kind": "WEAK"}
        tp2 = tp2 or tp1
        if tp1 and tp2 and abs(tp1["level"] - price) > abs(tp2["level"] - price): tp1 = tp2

    # ---------- skor kualitas (8 faktor)
    quality = []
    if seq:
        ev, idm = seq["event"], seq["inducement"]
        if seq["model"] == "REV":
            liq_ok = seq["sweep"]["kind"] in HIGH_VALUE
            liq_txt = f"Sweep {LIQ_NAME.get(seq['sweep']['kind'], seq['sweep']['kind'])}"
            struct_ok = ev["type"] == "MSS"; struct_txt = "MSS eksternal (bukan hanya CISD)"
            align_ok = sm["trend"] == direction or ev["type"] == "MSS"
        else:
            liq_ok = bool(seq.get("run")); liq_txt = "Liquidity run sebelum BOS" if liq_ok else "BOS tanpa liquidity run"
            struct_ok = True; struct_txt = "BOS eksternal searah tren"
            align_ok = sm["trend"] == direction
        quality = [
            [liq_ok, liq_txt],
            [bool(ev.get("strong") or seq.get("fvg")), "Displacement kuat / meninggalkan FVG"],
            [struct_ok, struct_txt],
            [bool(in_ote or in_disc), "POI di " + ("OTE 62–79%" if in_ote else "discount" if bull else "premium")],
            [bool(idm and idm["taken"]), "Inducement sudah diambil" if idm else "Inducement belum terbentuk"],
            [align_ok, f"Searah struktur eksternal {tf}"],
            [bool(z and (z["status"] == "fresh" or z.get("touch_t") == last["t"])), "POI first touch"],
            [bool(seq.get("overlap")), "OB + FVG overlap (confluence)"],
        ]
    score = sum(1 for q in quality if q[0])
    grade = None if not seq else "A" if score >= 6 else "B" if score >= 4 else "C"
    min_grade = str(_cfg("MIN_GRADE", "B")).upper()
    grade_ok = bool(grade and GRADE_RANK[grade] >= GRADE_RANK.get(min_grade, 2))

    mdl = None if not seq else ("Reversal" if seq["model"] == "REV" else "Continuation")
    trigger_txt = ("Sweep " + LIQ_NAME.get(seq["sweep"]["kind"], "")) if seq and seq["model"] == "REV" else \
                  ("BOS " + ("+ liquidity run" if seq.get("run") else "dengan displacement")) if seq else "Sweep / BOS"
    checklist = [
        [bool(seq), f"Model {mdl or '-'}: {trigger_txt}"],
        [bool(seq), f"Displacement + {seq['event']['type'] if seq else 'CISD/MSS/BOS'} terkonfirmasi"],
        [retrace, f"Retrace ke {ZNAME.get(z['kind'], z['kind']) + ' ★' if z else 'POI ★ (FVG/OB kontekstual)'}"],
        [bool(in_disc), f"Lokasi {'discount' if bull else 'premium' if direction else '-'} pada kaki setup"],
        [bool(tp2), "Target liquidity tersedia" + (f" ({tp2['kind']})" if tp2 else "")],
        [grade_ok, f"Kualitas setup {grade or '-'} ({score}/8, minimal {min_grade})"],
    ]

    signal, setup = "WAIT", None
    if seq and retrace and in_disc and tp2 and grade_ok:
        buf = _cfg("SL_BUFFER_ATR", 0.10) * A
        far = min(z["bottom"], seq["anchor"]) if bull else max(z["top"], seq["anchor"])
        sl = far - buf if bull else far + buf
        risk = abs(price - sl)
        rr_t = abs(tp2["level"] - price) / risk if risk else 0
        if rr_t >= _cfg("MIN_TARGET_RR", 1.5):
            signal = "BUY" if bull else "SELL"
            t1 = tp1["level"] if tp1 and abs(tp1["level"] - price) >= risk else price + (tp2["level"] - price) * 0.5
            setup = {"id": seq["id"], "model": mdl, "entry": _r(price, d), "sl": _r(sl, d), "tp1": _r(t1, d),
                     "tp2": _r(tp2["level"], d), "risk": _r(risk, d), "rr": round(rr_t, 2), "grade": grade,
                     "target": {"side": tp2["side"], "kind": tp2["kind"], "level": _r(tp2["level"], d)},
                     "zone": {"kind": ZNAME.get(z["kind"], z["kind"]), "top": _r(z["top"], d), "bottom": _r(z["bottom"], d),
                              "t": z["t"], "status": z["status"]}}
        else:
            checklist.append([False, f"RR ke target {rr_t:.2f} < {_cfg('MIN_TARGET_RR', 1.5)}"])

    trend = "Bullish" if sm["trend"] == "bull" else "Bearish" if sm["trend"] == "bear" else "Sideways"
    if signal != "WAIT":
        reason = f"{signal} {mdl} grade {grade}: {trigger_txt} → {seq['event']['type']} → {setup['zone']['kind']} ★ → target {tp2['kind']}."
    elif seq:
        reason = f"WAIT: setup {mdl} {grade} – menunggu retrace ke POI ★ / syarat lokasi & target."
    else:
        reason = "WAIT: belum ada sweep/BOS dengan displacement yang valid."
    fl = flow_summary(bars, 20) if "v" in last else None
    near_liq = sorted([p for p in sm["liquidity"] if p["resting"]], key=lambda p: abs(p["level"] - price))[:10]
    res.update(trend=trend, structure=sm["trend"], internal=sm["internal_trend"], setup_dir=direction, model=mdl,
               signal=signal, setup=setup, checklist=checklist, reason=reason, grade=grade, quality=quality,
               ma20=_r(m20[-1], d), ma50=_r(m50[-1], d), ma100=_r(m100[-1], d),
               ema20=_r(m20[-1], d), ema50=_r(m50[-1], d),                      # kompatibel pesan Telegram lama
               rsi=_r(rr[-1], 1), atr=_r(A, d), premium_discount=dr["pd"] if dr else None,
               last_candle={"t": last["t"], "o": _r(last["o"], d), "h": _r(last["h"], d), "l": _r(last["l"], d),
                            "c": _r(last["c"], d), "dir": "Bullish" if last["c"] >= last["o"] else "Bearish"},
               dealing_range=_one(dr, d),
               leg=_one(dict(leg, t=bars[seq["origin_i"]]["t"], dir=direction) if leg else None, d),
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
    st = s.get("setup") or {}
    grade, mdl = st.get("grade"), st.get("model")
    bias = b.get("structure"); dr = b.get("dealing_range") or {}
    loc = dr.get("pd")
    # narrative vs lokasi: continuation searah HTF boleh di premium/discount selama draw liquidity masih di depan
    loc_ok = (want is None or loc is None or (want == "bull" and loc == "discount") or (want == "bear" and loc == "premium")
              or (mdl == "Continuation" and bias == want))
    confirm = c.get("internal") or c.get("structure")
    bias_ok = want is not None and (bias == want or (bias is None and grade == "A") or (grade == "A" and mdl == "Reversal"))
    reasons = [
        [bias is not None, f"HTF {config.BIAS_TF}: struktur eksternal {bias or 'netral'}" + (f", harga di {loc}" if loc else "")],
        [want is not None, f"Setup {config.SETUP_TF}: {s.get('reason', '-')}"],
        [want is not None and confirm == want, f"Konfirmasi {config.CONFIRM_TF}: struktur internal {confirm or 'belum ada'}"],
    ]
    if want:
        reasons.append([loc_ok, f"Lokasi HTF {'discount' if want == 'bull' else 'premium'}" + (" ✓" if loc_ok else " – entry melawan lokasi HTF")])
    if want and bias_ok and confirm == want and loc_ok:
        return {"signal": s["signal"], "setup": s.get("setup"), "reasons": reasons,
                "summary": f"{s['signal']} valid ({mdl}, grade {grade}): HTF {config.BIAS_TF} → setup {config.SETUP_TF} → trigger {config.CONFIRM_TF}."}
    return {"signal": "WAIT", "setup": None, "pending": s.get("signal"), "reasons": reasons,
            "summary": "WAIT – top-down liquidity sequence belum selaras."}
