"""Analisis per timeframe + keputusan top-down (BUY / SELL / WAIT) beserta alasannya."""
import config
from indicators import ema, rsi, atr
from smc import structure, fvgs
from orderflow import flow_summary

MIN_BARS = 60
MIN_BY_TF = {"D": 30, "W": 15}
ANALYZE_BARS = 400


def _r(x, d):
    return None if x is None else round(x, d)


def _zone_near(zones, price, a, direction):
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


def _zl(lst, d):
    out = []
    for z in lst:
        x = {"t": z["t"], "dir": z["dir"], "top": _r(z["top"], d), "bottom": _r(z["bottom"], d)}
        if z.get("end_t"):
            x["end_t"] = z["end_t"]
        out.append(x)
    return out


def analyze_tf(tf, closed, price, d):
    need = MIN_BY_TF.get(tf, MIN_BARS)
    res = {"tf": tf, "bars": len(closed), "need": need, "enough": len(closed) >= need, "price": _r(price, d)}
    if len(closed) < min(need, 20):
        res.update(signal="WAIT", trend="Data belum cukup", structure=None, setup=None, checklist=[],
                   reason=f"Baru {len(closed)} candle {tf} tertutup (butuh ±{need}). Riwayat terisi otomatis.",
                   events=[], sweeps=[], fvg=[], ob=[], fvg_mit=[], ob_mit=[], flow=None,
                   series={"times": [], "ema20": [], "ema50": [], "ema200": []})
        return res
    bars = closed[-ANALYZE_BARS:]
    closes = [b["c"] for b in bars]
    e20, e50, e200 = ema(closes, config.EMA_FAST), ema(closes, config.EMA_SLOW), ema(closes, config.EMA_LONG)
    r, a = rsi(closes, config.RSI_LEN), atr(bars, config.ATR_LEN)
    A = a[-1] or (sum(b["h"] - b["l"] for b in bars[-14:]) / 14)
    st = structure(bars, config.SWING_LEN)
    fv, fv_mit = fvgs(bars, min_size=0.1 * A)
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
    fl = flow_summary(bars, 20) if "v" in last else None
    res.update({
        "trend": trend, "structure": s_trend,
        "ema20": _r(E20, d), "ema50": _r(E50, d), "ema200": _r(e200[-1], d), "rsi": _r(RSI, 1), "atr": _r(A, d),
        "last_candle": {"t": last["t"], "o": _r(last["o"], d), "h": _r(last["h"], d), "l": _r(last["l"], d),
                        "c": _r(last["c"], d), "dir": "Bullish" if last["c"] >= last["o"] else "Bearish"},
        "swing_high": _r(st["swing_high"], d), "swing_low": _r(st["swing_low"], d),
        "events": [dict(e, level=_r(e["level"], d)) for e in st["events"][-40:]],
        "sweeps": [dict(s, level=_r(s["level"], d), price=_r(s["price"], d)) for s in st["sweeps"][-15:]],
        "fvg": _zl(fv[-6:], d), "ob": _zl(st["order_blocks"][-6:], d),
        "fvg_mit": _zl(fv_mit[-6:], d), "ob_mit": _zl(st["ob_mitigated"][-6:], d),
        "flow": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in fl.items()} if fl else None,
        "series": {"times": [b["t"] for b in bars], "ema20": e20, "ema50": e50, "ema200": e200},
    })
    checklist, signal, setup = [], "WAIT", None
    if s_trend is None:
        reason = "Belum ada BOS/CHoCH yang jelas, struktur belum terbentuk."
    else:
        bull = s_trend == "bull"
        dname = "BUY" if bull else "SELL"
        ev = st["events"][-1]
        checklist.append([True, f"Struktur {'bullish' if bull else 'bearish'} ({ev['type']} {'naik' if bull else 'turun'} di {_r(ev['level'], d)})"])
        c2 = E50 is not None and E20 is not None and ((last["c"] > E50 and E20 > E50) if bull else (last["c"] < E50 and E20 < E50))
        checklist.append([c2, f"Harga & EMA{config.EMA_FAST} {'di atas' if bull else 'di bawah'} EMA{config.EMA_SLOW}"])
        lo, hi = (config.RSI_BUY_MIN, config.RSI_BUY_MAX) if bull else (config.RSI_SELL_MIN, config.RSI_SELL_MAX)
        c3 = RSI is not None and lo <= RSI <= hi
        checklist.append([c3, f"RSI {_r(RSI, 1)} dalam rentang {lo}–{hi} (tidak jenuh)"])
        z = _zone_near(zones, price, A, s_trend)
        checklist.append([bool(z), f"Harga pullback ke {z['kind']} {'bullish' if bull else 'bearish'} {_r(z['bottom'], d)}–{_r(z['top'], d)}"
                          if z else f"Harga belum pullback ke FVG/OB {'bullish' if bull else 'bearish'} yang aktif"])
        if c2 and c3 and z:
            signal = dname
            if bull:
                sl = min(min(z["bottom"], price) - 0.3 * A, price - 0.5 * A)
                R = price - sl
                tp1, tp2 = price + config.TP1_R * R, price + config.TP2_R * R
            else:
                sl = max(max(z["top"], price) + 0.3 * A, price + 0.5 * A)
                R = sl - price
                tp1, tp2 = price - config.TP1_R * R, price - config.TP2_R * R
            setup = {"entry": _r(price, d), "sl": _r(sl, d), "tp1": _r(tp1, d), "tp2": _r(tp2, d), "risk": _r(R, d),
                     "zone": {"kind": z["kind"], "top": _r(z["top"], d), "bottom": _r(z["bottom"], d), "t": z["t"]}}
            reason = f"{dname}: struktur searah, EMA searah, RSI sehat, harga retest {z['kind']} {_r(z['bottom'], d)}–{_r(z['top'], d)}."
        else:
            reason = f"Arah condong {dname}, tetapi belum memenuhi: " + "; ".join(c[1] for c in checklist if not c[0]) + "."
    res.update(signal=signal, setup=setup, checklist=checklist, reason=reason)
    return res


def _bias(an):
    if not an or not an.get("enough") or an.get("structure") is None:
        return None
    return {"Bullish": "bull", "Bearish": "bear"}.get(an["trend"], "mixed")


def overall(tfs, book=None, trades=None):
    """Top-down: bias H4 -> setup H1 -> konfirmasi M15 (+ order flow untuk crypto)."""
    b_tf, s_tf, c_tf = config.BIAS_TF, config.SETUP_TF, config.CONFIRM_TF
    bias_an, setup_an, conf_an = tfs.get(b_tf), tfs.get(s_tf), tfs.get(c_tf)
    reasons = []
    bias = _bias(bias_an)
    if not bias_an or not bias_an.get("enough"):
        reasons.append([None, f"{b_tf}: data belum cukup, bias {b_tf} diabaikan sementara"])
    elif bias is None:
        reasons.append([None, f"Bias {b_tf}: Sideways (belum ada BOS/CHoCH) – netral"])
    else:
        reasons.append([bias != "mixed", f"Bias {b_tf}: {bias_an['trend']}"])
    if not setup_an or not setup_an.get("enough"):
        return {"signal": "WAIT", "setup": None, "reasons": reasons + [[False, f"{s_tf}: data belum cukup"]],
                "summary": f"Menunggu data {s_tf} cukup."}
    sig = setup_an.get("signal", "WAIT")
    reasons.append([sig != "WAIT", f"Setup {s_tf}: {sig} – {setup_an['reason']}"])
    want = {"BUY": "bull", "SELL": "bear"}.get(sig)
    conf_ok = bool(want) and conf_an is not None and conf_an.get("structure") == want
    if conf_an and conf_an.get("structure"):
        reasons.append([conf_ok if want else None, f"Konfirmasi {c_tf}: struktur {'bullish' if conf_an['structure'] == 'bull' else 'bearish'}"])
    else:
        reasons.append([False if want else None, f"Konfirmasi {c_tf}: struktur belum jelas"])

    # ----- order flow (crypto) -----
    flow_ok = True
    fl = setup_an.get("flow")
    if fl or book or trades:
        votes = []
        if fl:
            v = fl["delta"] > 0
            votes.append(v)
            reasons.append([None if not want else (v if want == "bull" else not v),
                            f"Delta {s_tf} 20 candle: {'+' if fl['delta'] >= 0 else ''}{fl['delta']:.2f} (buy {fl['buy_pct']:.0f}%)"])
        if book:
            v = book["imb_0.5"] > 50
            votes.append(v)
            reasons.append([None if not want else (v if want == "bull" else not v),
                            f"Order book ±0,5%: bid {book['imb_0.5']:.0f}% / ask {100 - book['imb_0.5']:.0f}%"])
        if trades:
            v = trades["buy_pct"] > 50
            votes.append(v)
            reasons.append([None if not want else (v if want == "bull" else not v),
                            f"Transaksi terakhir: agresif beli {trades['buy_pct']:.0f}%"])
        if want and config.ORDERFLOW_FILTER and votes:
            agree = sum(1 for v in votes if (v if want == "bull" else not v))
            flow_ok = agree * 2 >= len(votes)
    if want is None:
        return {"signal": "WAIT", "setup": None, "reasons": reasons, "summary": f"WAIT – setup {s_tf} belum lengkap."}
    bias_ok = bias in (None, want)
    if bias_ok and conf_ok and flow_ok:
        extra = " dan order flow mendukung" if (fl or book) else ""
        return {"signal": sig, "setup": setup_an["setup"], "reasons": reasons,
                "summary": f"{sig} – {s_tf} setup valid, searah bias {b_tf}, dikonfirmasi {c_tf}{extra}."}
    why = []
    if not bias_ok:
        why.append(f"berlawanan dengan bias {b_tf}")
    if not conf_ok:
        why.append(f"menunggu konfirmasi struktur {c_tf}")
    if not flow_ok:
        why.append("order flow belum mendukung")
    return {"signal": "WAIT", "setup": None, "pending": sig, "reasons": reasons,
            "summary": f"WAIT – ada setup {sig} di {s_tf} tetapi " + " dan ".join(why) + "."}
