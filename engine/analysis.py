"""Analisis per timeframe + keputusan top-down (BUY / SELL / WAIT) beserta alasannya."""
import config
from indicators import ema, rsi, atr
from smc import structure, fvgs

MIN_BARS = 60
MIN_BY_TF = {"D": 30, "W": 15}
ANALYZE_BARS = 400


def _r(x, d):
    return None if x is None else round(x, d)


def _zone_near(zones, price, a, direction):
    """Zona searah yang sedang disentuh / dekat harga (pullback)."""
    best = None
    for z in zones:
        if z["dir"] != direction:
            continue
        if direction == "bull":
            ok = z["bottom"] - 0.3 * a <= price <= z["top"] + 0.5 * a
            dist = max(0.0, price - z["top"])
        else:
            ok = z["bottom"] - 0.5 * a <= price <= z["top"] + 0.3 * a
            dist = max(0.0, z["bottom"] - price)
        if ok and (best is None or dist < best[0]):
            best = (dist, z)
    return best[1] if best else None


def analyze_tf(tf, closed, price, symbol):
    d = config.digits(symbol)
    need = MIN_BY_TF.get(tf, MIN_BARS)
    res = {"tf": tf, "bars": len(closed), "need": need, "enough": len(closed) >= need, "price": _r(price, d)}
    if len(closed) < min(need, 20):
        res.update(signal="WAIT", trend="Data belum cukup",
                   reason=f"Baru {len(closed)} candle {tf} tertutup (butuh ±{need}). Riwayat akan terisi otomatis seiring waktu.",
                   checklist=[], events=[], fvg=[], ob=[])
        return res
    bars = closed[-ANALYZE_BARS:]
    closes = [b["c"] for b in bars]
    e20, e50 = ema(closes, config.EMA_FAST), ema(closes, config.EMA_SLOW)
    r, a = rsi(closes, config.RSI_LEN), atr(bars, config.ATR_LEN)
    A = a[-1] or (sum(b["h"] - b["l"] for b in bars[-14:]) / 14)
    st = structure(bars, config.SWING_LEN)
    fv = fvgs(bars, min_size=0.1 * A)
    zones = [dict(z, kind="FVG") for z in fv] + [dict(z, kind="OB") for z in st["order_blocks"]]
    last = bars[-1]
    E20, E50, RSI = e20[-1], e50[-1], r[-1]

    s_trend = st["trend"]
    ref = E50 if E50 is not None else E20
    if s_trend == "bull" and (ref is None or last["c"] > ref):
        trend = "Bullish"
    elif s_trend == "bear" and (ref is None or last["c"] < ref):
        trend = "Bearish"
    elif s_trend is None:
        trend = "Sideways"
    else:
        trend = "Mixed (struktur " + ("bullish" if s_trend == "bull" else "bearish") + ", harga vs EMA50 berlawanan)"

    res.update({
        "trend": trend, "structure": s_trend,
        "ema20": _r(E20, d), "ema50": _r(E50, d), "rsi": _r(RSI, 1), "atr": _r(A, d),
        "last_candle": {"t": last["t"], "o": _r(last["o"], d), "h": _r(last["h"], d), "l": _r(last["l"], d),
                        "c": _r(last["c"], d), "dir": "Bullish" if last["c"] >= last["o"] else "Bearish"},
        "swing_high": _r(st["swing_high"], d), "swing_low": _r(st["swing_low"], d),
        "events": [{"t": e["t"], "type": e["type"], "dir": e["dir"], "level": _r(e["level"], d)} for e in st["events"][-12:]],
        "fvg": [{"t": z["t"], "dir": z["dir"], "top": _r(z["top"], d), "bottom": _r(z["bottom"], d)} for z in fv[-4:]],
        "ob": [{"t": z["t"], "dir": z["dir"], "top": _r(z["top"], d), "bottom": _r(z["bottom"], d)} for z in st["order_blocks"][-4:]],
        "series": {"ema20": e20, "ema50": e50, "times": [b["t"] for b in bars]},
    })

    # ---- Setup per timeframe ----
    checklist = []
    signal = "WAIT"
    setup = None
    if s_trend is None:
        reason = "Belum ada BOS/CHoCH yang jelas, struktur belum terbentuk."
    else:
        bull = s_trend == "bull"
        dname = "BUY" if bull else "SELL"
        ev = st["events"][-1]
        c1 = True
        checklist.append([c1, f"Struktur {'bullish' if bull else 'bearish'} ({ev['type']} {'naik' if bull else 'turun'} di {_r(ev['level'], d)})"])
        if E50 is not None and E20 is not None:
            c2 = (last["c"] > E50 and E20 > E50) if bull else (last["c"] < E50 and E20 < E50)
        else:
            c2 = False
        checklist.append([c2, f"Harga & EMA{config.EMA_FAST} {'di atas' if bull else 'di bawah'} EMA{config.EMA_SLOW}"])
        if RSI is None:
            c3 = False
        elif bull:
            c3 = config.RSI_BUY_MIN <= RSI <= config.RSI_BUY_MAX
        else:
            c3 = config.RSI_SELL_MIN <= RSI <= config.RSI_SELL_MAX
        rng = (config.RSI_BUY_MIN, config.RSI_BUY_MAX) if bull else (config.RSI_SELL_MIN, config.RSI_SELL_MAX)
        checklist.append([c3, f"RSI {_r(RSI, 1)} dalam rentang {rng[0]}–{rng[1]} (tidak jenuh)"])
        z = _zone_near(zones, price, A, s_trend)
        c4 = z is not None
        if z:
            checklist.append([True, f"Harga pullback ke {z['kind']} {'bullish' if bull else 'bearish'} {_r(z['bottom'], d)}–{_r(z['top'], d)}"])
        else:
            checklist.append([False, f"Harga belum pullback ke FVG/OB {'bullish' if bull else 'bearish'} yang aktif"])
        if c1 and c2 and c3 and c4:
            signal = dname
            if bull:
                sl = min(z["bottom"], price) - 0.3 * A
                sl = min(sl, price - 0.5 * A)
                R = price - sl
                tp1, tp2 = price + config.TP1_R * R, price + config.TP2_R * R
            else:
                sl = max(z["top"], price) + 0.3 * A
                sl = max(sl, price + 0.5 * A)
                R = sl - price
                tp1, tp2 = price - config.TP1_R * R, price - config.TP2_R * R
            setup = {"entry": _r(price, d), "sl": _r(sl, d), "tp1": _r(tp1, d), "tp2": _r(tp2, d),
                     "risk": _r(R, d), "zone": {"kind": z["kind"], "top": _r(z["top"], d), "bottom": _r(z["bottom"], d), "t": z["t"]}}
            reason = (f"{dname}: struktur {'bullish' if bull else 'bearish'}, searah EMA, RSI sehat, dan harga sedang retest "
                      f"{z['kind']} {_r(z['bottom'], d)}–{_r(z['top'], d)}.")
        else:
            gagal = [c[1] for c in checklist if not c[0]]
            reason = f"Arah condong {dname}, tetapi belum memenuhi: " + "; ".join(gagal) + "."
    res.update(signal=signal, setup=setup, checklist=checklist, reason=reason)
    return res


def _bias(an):
    if not an or not an.get("enough") or an.get("structure") is None:
        return None
    if an["trend"] == "Bullish":
        return "bull"
    if an["trend"] == "Bearish":
        return "bear"
    return "mixed"


def overall(tfs):
    """Keputusan akhir top-down: BIAS_TF (H4) -> SETUP_TF (H1) -> CONFIRM_TF (M15)."""
    b_tf, s_tf, c_tf = config.BIAS_TF, config.SETUP_TF, config.CONFIRM_TF
    bias_an, setup_an, conf_an = tfs.get(b_tf), tfs.get(s_tf), tfs.get(c_tf)
    reasons = []
    bias = _bias(bias_an)
    if not bias_an or not bias_an.get("enough"):
        reasons.append([None, f"{b_tf}: data belum cukup, bias {b_tf} diabaikan sementara"])
    elif bias is None:
        reasons.append([None, f"Bias {b_tf}: Sideways (belum ada BOS/CHoCH) – dianggap netral"])
    else:
        reasons.append([bias != "mixed", f"Bias {b_tf}: {bias_an['trend']}"])
    sig = setup_an.get("signal", "WAIT") if setup_an else "WAIT"
    if not setup_an or not setup_an.get("enough"):
        return {"signal": "WAIT", "setup": None, "reasons": reasons + [[False, f"{s_tf}: data belum cukup"]],
                "summary": f"Menunggu data {s_tf} cukup."}
    reasons.append([sig != "WAIT", f"Setup {s_tf}: {sig} – {setup_an['reason']}"])
    want = "bull" if sig == "BUY" else "bear" if sig == "SELL" else None
    conf_ok = conf_an is not None and conf_an.get("structure") == want if want else False
    if conf_an and conf_an.get("structure"):
        reasons.append([conf_ok if want else None, f"Konfirmasi {c_tf}: struktur {'bullish' if conf_an['structure']=='bull' else 'bearish'}"])
    else:
        reasons.append([False if want else None, f"Konfirmasi {c_tf}: struktur belum jelas"])
    if want is None:
        return {"signal": "WAIT", "setup": None, "reasons": reasons, "summary": f"WAIT – setup {s_tf} belum lengkap."}
    bias_ok = bias in (None, want)
    if bias_ok and conf_ok:
        return {"signal": sig, "setup": setup_an["setup"], "reasons": reasons,
                "summary": f"{sig} – {s_tf} setup valid, searah bias {b_tf}, dikonfirmasi {c_tf}."}
    why = []
    if not bias_ok:
        why.append(f"berlawanan dengan bias {b_tf}")
    if not conf_ok:
        why.append(f"menunggu konfirmasi struktur {c_tf}")
    return {"signal": "WAIT", "setup": None, "pending": sig, "reasons": reasons,
            "summary": f"WAIT – ada setup {sig} di {s_tf} tetapi " + " dan ".join(why) + "."}
