"""SMC/ICT engine v4 – liquidity-first, zona visual vs POI kontekstual dipisah.

Alur model (gabungan materi Uncensored Trader, Smart Risk, Brain Titans, Lewis Kelly, dll):
  1. Struktur eksternal (BOS/CHoCH, strong/weak level, dealing range, premium/discount)
  2. Liquidity pool: swing eksternal/internal, EQH/EQL, PDH/PDL, Asian/London H/L
  3. Pool disentuh -> dibedakan:
       SWEEP = wick menembus, close kembali, candle berikut tidak close di luar  -> model REVERSAL
       RUN   = close menembus dengan displacement                               -> model CONTINUATION
  4. Displacement -> CISD (break range lokal) / MSS (break swing eksternal)
  5. PD Array:
       FVG valid  = gap 3 candle dengan candle tengah impulsif
       IFVG       = FVG yang ditembus close -> berbalik fungsi
       OB valid   = candle lawan terakhir sebelum kaki impuls yang (a) mematahkan struktur
                    dan (b) meninggalkan FVG/displacement
       Breaker    = OB yang ditembus close -> berbalik fungsi
  6. Inducement (IDM) di depan POI, lalu retrace ke POI
Semua zona digambar (lapisan visual). Hanya zona yang lahir dari sequence diberi context=True (★)
dan hanya zona ★ yang boleh dipakai analysis.py untuk entry.
Semua perhitungan memakai candle yang sudah close.
"""

MSS_WINDOW = 10          # jarak maksimal sweep -> candle displacement
OB_LOOKBACK = 6          # cari candle lawan sampai 6 candle sebelum titik asal impuls
MAX_CONTEXT = 3          # jumlah sequence terbaru yang zonanya diberi ★
PD_DAYS = 3              # PDH/PDL & sesi untuk 3 hari terakhir
SESSIONS = (("AS", 0, 7), ("LO", 7, 12))   # jam UTC: Asia 07-14 WIB, London 14-19 WIB
HIGH_VALUE = ("EXT", "EQH", "EQL", "PDH", "PDL", "ASH", "ASL", "LOH", "LOL")


# ------------------------------------------------------------------ util
def swings(bars, k=3):
    hs, ls = [], []
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(x["h"] for x in win): hs.append(i)
        if bars[i]["l"] == min(x["l"] for x in win): ls.append(i)
    return hs, ls


def _tf_sec(bars):
    if len(bars) < 3: return 3600
    ds = sorted(bars[i + 1]["t"] - bars[i]["t"] for i in range(len(bars) - 1))
    return max(ds[len(ds) // 2], 60)


def _bull(b): return b["c"] > b["o"]
def _bear(b): return b["c"] < b["o"]


def _disp(b, direction, atr, mult):
    """Candle displacement: searah, body >= 60% range dan body >= mult x ATR."""
    body = abs(b["c"] - b["o"]); rng = max(b["h"] - b["l"], 1e-12)
    ok_dir = _bull(b) if direction == "bull" else _bear(b)
    return ok_dir and body / rng >= 0.60 and body >= mult * max(atr, 1e-12)


# ------------------------------------------------------------------ struktur
def structure(bars, k):
    """BOS/CHoCH dari swing terkonfirmasi (tanpa lookahead) + protected (strong) level."""
    hs, ls = swings(bars, k)
    conf_h = {i + k: i for i in hs}; conf_l = {i + k: i for i in ls}
    last_h = last_l = None; trend = None; protected = None; events = []
    for j, b in enumerate(bars):
        if j in conf_h: last_h = conf_h[j]
        if j in conf_l: last_l = conf_l[j]
        if last_h is not None and b["c"] > bars[last_h]["h"]:
            origin = min(range(last_h, j + 1), key=lambda q: bars[q]["l"])
            events.append({"t": b["t"], "from_t": bars[last_h]["t"], "type": "CHoCH" if trend == "bear" else "BOS",
                           "dir": "bull", "level": bars[last_h]["h"], "index": j, "swing_i": last_h, "origin": origin})
            trend, protected, last_h = "bull", origin, None
        elif last_l is not None and b["c"] < bars[last_l]["l"]:
            origin = max(range(last_l, j + 1), key=lambda q: bars[q]["h"])
            events.append({"t": b["t"], "from_t": bars[last_l]["t"], "type": "CHoCH" if trend == "bull" else "BOS",
                           "dir": "bear", "level": bars[last_l]["l"], "index": j, "swing_i": last_l, "origin": origin})
            trend, protected, last_l = "bear", origin, None
    return {"trend": trend, "events": events, "protected": protected, "highs": hs, "lows": ls}


def dealing_range(bars, ext, price):
    """Range dari strong (protected) level ke weak level + premium/discount + OTE."""
    p, trend = ext["protected"], ext["trend"]
    if p is None or trend is None: return None
    if trend == "bull":
        lo = bars[p]["l"]; hi_i = max(range(p, len(bars)), key=lambda q: bars[q]["h"]); hi = bars[hi_i]["h"]
        t_lo, t_hi, strong, weak = bars[p]["t"], bars[hi_i]["t"], "low", "high"
    else:
        hi = bars[p]["h"]; lo_i = min(range(p, len(bars)), key=lambda q: bars[q]["l"]); lo = bars[lo_i]["l"]
        t_hi, t_lo, strong, weak = bars[p]["t"], bars[lo_i]["t"], "high", "low"
    if hi <= lo: return None
    eq = (hi + lo) / 2; span = hi - lo
    return {"dir": trend, "hi": hi, "lo": lo, "eq": eq, "t_hi": t_hi, "t_lo": t_lo, "strong": strong, "weak": weak,
            "pos": round((price - lo) / span, 3), "pd": "discount" if price < eq else "premium",
            "ote_bottom": hi - 0.79 * span if trend == "bull" else lo + 0.62 * span,
            "ote_top": hi - 0.62 * span if trend == "bull" else lo + 0.79 * span, "t": min(t_hi, t_lo)}


# ------------------------------------------------------------------ liquidity
def _taken(bars, side, level, start):
    for i in range(start, len(bars)):
        if (side == "BSL" and bars[i]["h"] > level) or (side == "SSL" and bars[i]["l"] < level):
            return i
    return None


def liquidity_pools(bars, k, tolerance, ext_highs=(), ext_lows=()):
    hs, ls = swings(bars, k); out = []
    eh, el = set(ext_highs), set(ext_lows)
    for side, ids, key, ext in (("BSL", hs, "h", eh), ("SSL", ls, "l", el)):
        for n, i in enumerate(ids):
            level = bars[i][key]; kind = "EXT" if i in ext else "INT"
            for j in ids[max(0, n - 6):n]:                      # equal highs / equal lows
                if abs(bars[j][key] - level) <= tolerance:
                    kind = "EQH" if side == "BSL" else "EQL"
                    level = max(level, bars[j][key]) if side == "BSL" else min(level, bars[j][key])
            out.append({"id": f"{side}:{bars[i]['t']}", "side": side, "level": level, "t": bars[i]["t"],
                        "kind": kind, "index": i, "start": i + k})
    tf = _tf_sec(bars)
    if tf < 86400 and bars:
        days = {}
        for i, b in enumerate(bars): days.setdefault(b["t"] // 86400, []).append(i)
        keys = sorted(days)
        for n, dk in enumerate(keys[:-1]):
            if n < len(keys) - 1 - PD_DAYS: continue
            ids = days[dk]; nxt = days[keys[n + 1]][0]
            hi_i = max(ids, key=lambda q: bars[q]["h"]); lo_i = min(ids, key=lambda q: bars[q]["l"])
            out.append({"id": f"PDH:{dk}", "side": "BSL", "level": bars[hi_i]["h"], "t": bars[hi_i]["t"], "kind": "PDH", "index": hi_i, "start": nxt})
            out.append({"id": f"PDL:{dk}", "side": "SSL", "level": bars[lo_i]["l"], "t": bars[lo_i]["t"], "kind": "PDL", "index": lo_i, "start": nxt})
        if tf <= 3600:
            for dk in keys[-PD_DAYS:]:
                for name, h0, h1 in SESSIONS:
                    ids = [q for q in days[dk] if h0 <= (bars[q]["t"] % 86400) // 3600 < h1]
                    if not ids: continue
                    close_t = dk * 86400 + h1 * 3600
                    end = next((q for q in range(ids[-1] + 1, len(bars)) if bars[q]["t"] >= close_t), None)
                    if end is None: continue                   # sesi belum selesai
                    hi_i = max(ids, key=lambda q: bars[q]["h"]); lo_i = min(ids, key=lambda q: bars[q]["l"])
                    out.append({"id": f"{name}H:{dk}", "side": "BSL", "level": bars[hi_i]["h"], "t": bars[hi_i]["t"], "kind": name + "H", "index": hi_i, "start": end})
                    out.append({"id": f"{name}L:{dk}", "side": "SSL", "level": bars[lo_i]["l"], "t": bars[lo_i]["t"], "kind": name + "L", "index": lo_i, "start": end})
    for p in out:
        ti = _taken(bars, p["side"], p["level"], p["start"])
        p["taken_t"] = bars[ti]["t"] if ti is not None else None
        p["resting"] = ti is None
        p["taken_i"] = ti
    return sorted(out, key=lambda x: x["start"])


def pool_reaction(bars, p, atr, run_atr):
    """Saat pool pertama kali disentuh: SWEEP (reversal) atau RUN (continuation)."""
    i = p.get("taken_i")
    if i is None: return None
    b, lv = bars[i], p["level"]
    base = {"t": b["t"], "from_t": p["t"], "side": p["side"], "kind": p["kind"], "level": lv, "index": i,
            "id": f"{p['id']}:{b['t']}"}
    if p["side"] == "SSL":
        closed_back = b["c"] >= lv
        nxt_fail = i + 1 < len(bars) and bars[i + 1]["c"] < lv
        if closed_back and not nxt_fail:
            return dict(base, type="SWEEP", dir="bull", price=b["l"])
        if not closed_back and _disp(b, "bear", atr, run_atr):
            return dict(base, type="RUN", dir="bear", price=b["l"])
    else:
        closed_back = b["c"] <= lv
        nxt_fail = i + 1 < len(bars) and bars[i + 1]["c"] > lv
        if closed_back and not nxt_fail:
            return dict(base, type="SWEEP", dir="bear", price=b["h"])
        if not closed_back and _disp(b, "bull", atr, run_atr):
            return dict(base, type="RUN", dir="bull", price=b["h"])
    return None


# ------------------------------------------------------------------ PD array
def find_fvgs(bars, atr, min_atr):
    """FVG valid: gap 3 candle, ukuran >= min_atr x ATR, candle tengah impulsif (body >= 50% range)."""
    out, mingap = [], min_atr * max(atr, 1e-12)
    for j in range(2, len(bars)):
        a, m, c = bars[j - 2], bars[j - 1], bars[j]
        body = abs(m["c"] - m["o"]); rng = max(m["h"] - m["l"], 1e-12)
        if body / rng < 0.5: continue
        if _bull(m) and c["l"] > a["h"] and c["l"] - a["h"] >= mingap:
            out.append({"kind": "FVG", "dir": "bull", "t": a["t"], "top": c["l"], "bottom": a["h"], "created": j, "origin": j - 2})
        elif _bear(m) and c["h"] < a["l"] and a["l"] - c["h"] >= mingap:
            out.append({"kind": "FVG", "dir": "bear", "t": a["t"], "top": a["l"], "bottom": c["h"], "created": j, "origin": j - 2})
    return out


def find_obs(bars, events, fvgs, atr, disp_atr):
    """OB valid = candle lawan terakhir sebelum kaki impuls yang mematahkan struktur
    DAN meninggalkan FVG atau candle displacement pada kaki tersebut."""
    out = {}
    fv_by_created = {}
    for f in fvgs: fv_by_created.setdefault(f["dir"], []).append(f["created"])
    for e in events:
        d, j, o = e["dir"], e["index"], e["origin"]
        leg_fvg = any(o < c <= j + 1 for c in fv_by_created.get(d, []))
        leg_disp = any(_disp(bars[q], d, atr, disp_atr) for q in range(o, j + 1))
        if not (leg_fvg or leg_disp): continue
        q = None
        for x in range(o, max(-1, o - OB_LOOKBACK), -1):           # candle lawan terakhir di/ sebelum titik asal
            if (_bear(bars[x]) if d == "bull" else _bull(bars[x])): q = x; break
        if q is None: q = o
        z = {"kind": "OB", "dir": d, "t": bars[q]["t"], "top": bars[q]["h"], "bottom": bars[q]["l"],
             "created": j, "origin": q, "break": "MSS" if e["type"] == "CHoCH" else "BOS"}
        out[(d, q)] = z
    return list(out.values())


def zone_state(bars, z):
    """Status zona.
    FVG/IFVG : fresh -> tapped -> ce (50%) -> filled (wick menembus penuh) | invalid (close menembus)
    OB/BB    : fresh -> tapped -> mitigated (close lewat 50%) | invalid (close menembus sisi jauh)
    FVG invalid -> IFVG, OB invalid -> Breaker (dibuat oleh pemanggil)."""
    top, bot, d, kind = z["top"], z["bottom"], z["dir"], z["kind"]
    mid = (top + bot) / 2
    st = {"status": "fresh", "touch_i": None, "end_i": None, "ce": False}
    for j in range(z["created"] + 1, len(bars)):
        b = bars[j]
        if d == "bull":
            if b["c"] < bot: st.update(status="invalid", end_i=j); break
            if b["l"] <= top and st["touch_i"] is None: st.update(touch_i=j, status="tapped")
            if b["l"] <= mid: st["ce"] = True
            if kind in ("FVG", "IFVG") and b["l"] <= bot: st.update(status="filled", end_i=j); break
            if kind in ("OB", "BB") and b["c"] < mid: st.update(status="mitigated", end_i=j); break
        else:
            if b["c"] > top: st.update(status="invalid", end_i=j); break
            if b["h"] >= bot and st["touch_i"] is None: st.update(touch_i=j, status="tapped")
            if b["h"] >= mid: st["ce"] = True
            if kind in ("FVG", "IFVG") and b["h"] >= top: st.update(status="filled", end_i=j); break
            if kind in ("OB", "BB") and b["c"] > mid: st.update(status="mitigated", end_i=j); break
    x = dict(z, mid=mid, status=st["status"], ce=st["ce"],
             touch_t=bars[st["touch_i"]]["t"] if st["touch_i"] is not None else None, touch_i=st["touch_i"])
    if st["end_i"] is not None:
        x["end_t"] = bars[st["end_i"]]["t"]; x["end_i"] = st["end_i"]
    return x


def _flip(bars, z, kind):
    """Zona invalid berbalik fungsi: FVG -> IFVG, OB -> Breaker."""
    j = z["end_i"]
    return {"kind": kind, "dir": "bear" if z["dir"] == "bull" else "bull", "t": bars[j]["t"], "top": z["top"],
            "bottom": z["bottom"], "created": j, "origin": j, "from_t": z["t"]}


def _inducement(bars, j, direction, zone_edge, k=2):
    """Swing internal pertama setelah break yang berada di antara harga dan POI."""
    for i in range(j + 1, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if direction == "bull" and bars[i]["l"] == min(x["l"] for x in win) and bars[i]["l"] > zone_edge:
            lvl = bars[i]["l"]; tk = _taken(bars, "SSL", lvl, i + k)
            return {"t": bars[i]["t"], "level": lvl, "dir": "bull", "taken": tk is not None,
                    "taken_t": bars[tk]["t"] if tk is not None else None}
        if direction == "bear" and bars[i]["h"] == max(x["h"] for x in win) and bars[i]["h"] < zone_edge:
            lvl = bars[i]["h"]; tk = _taken(bars, "BSL", lvl, i + k)
            return {"t": bars[i]["t"], "level": lvl, "dir": "bear", "taken": tk is not None,
                    "taken_t": bars[tk]["t"] if tk is not None else None}
    return None


# ------------------------------------------------------------------ model utama
def model(bars, atr_value, k=3, eq_tolerance=0.0, k_int=2, k_ext=5, disp_atr=0.8, strong_disp_atr=1.2,
          fvg_min_atr=0.10, run_atr=0.6, max_zones=3):
    empty = {"trend": None, "internal_trend": None, "dealing_range": None, "liquidity": [], "sweeps": [], "runs": [],
             "events": [], "internal_events": [], "fvg": [], "fvg_mit": [], "order_blocks": [], "ob_mitigated": [],
             "sequences": []}
    if len(bars) < 10: return empty
    A = max(atr_value, 1e-12); n = len(bars); price = bars[-1]["c"]
    ext = structure(bars, k_ext); itl = structure(bars, k_int)
    dr = dealing_range(bars, ext, price)
    pools = liquidity_pools(bars, k, eq_tolerance, ext["highs"], ext["lows"])

    # ---- PD array (lapisan visual lengkap)
    fvgs = [zone_state(bars, z) for z in find_fvgs(bars, A, fvg_min_atr)]
    obs = [zone_state(bars, z) for z in find_obs(bars, itl["events"] + ext["events"], fvgs, A, disp_atr)]
    ifvg = [zone_state(bars, _flip(bars, z, "IFVG")) for z in fvgs if z["status"] == "invalid"]
    bbs = [zone_state(bars, _flip(bars, z, "BB")) for z in obs if z["status"] == "invalid"]

    ext_hi = sorted((i + k_ext, i) for i in ext["highs"]); ext_lo = sorted((i + k_ext, i) for i in ext["lows"])

    def last_ext(conf, j):
        r = None
        for c, i in conf:
            if c <= j: r = i
            else: break
        return r

    def best_zone(zs, d, lo_i, hi_i):
        cand = [z for z in zs if z["dir"] == d and lo_i <= z["origin"] <= hi_i]
        return max(cand, key=lambda z: z["top"] - z["bottom"]) if cand else None

    reactions = [r for r in (pool_reaction(bars, p, A, run_atr) for p in pools) if r]
    sweeps = [r for r in reactions if r["type"] == "SWEEP"]
    runs = [r for r in reactions if r["type"] == "RUN"]
    seqs = {}

    # ---- model REVERSAL: sweep -> displacement -> CISD/MSS -> FVG/OB kontekstual
    for sw in sweeps:
        i, d = sw["index"], sw["dir"]
        for j in range(i, min(n, i + MSS_WINDOW)):
            if not _disp(bars[j], d, A, disp_atr): continue
            prior = bars[max(i - 8, 0):j]
            if not prior: continue
            cisd = max(x["h"] for x in prior) if d == "bull" else min(x["l"] for x in prior)
            if not (bars[j]["c"] > cisd if d == "bull" else bars[j]["c"] < cisd): continue
            xi = last_ext(ext_hi if d == "bull" else ext_lo, j)
            mss = xi is not None and (bars[j]["c"] > bars[xi]["h"] if d == "bull" else bars[j]["c"] < bars[xi]["l"])
            ev = {"t": bars[j]["t"], "from_t": bars[xi]["t"] if mss else sw["t"], "type": "MSS" if mss else "CISD",
                  "dir": d, "level": (bars[xi]["h"] if d == "bull" else bars[xi]["l"]) if mss else cisd,
                  "index": j, "displacement": True, "strong": _disp(bars[j], d, A, strong_disp_atr)}
            fz = best_zone(fvgs, d, i - 1, j + 1)
            oz = best_zone(obs, d, i - OB_LOOKBACK, j)
            if oz is None:                                       # OB dari candle lawan terakhir / candle sweep
                q = next((x for x in range(j - 1, max(-1, i - OB_LOOKBACK - 1), -1)
                          if (_bear(bars[x]) if d == "bull" else _bull(bars[x]))), i)
                oz = zone_state(bars, {"kind": "OB", "dir": d, "t": bars[q]["t"], "top": bars[q]["h"],
                                       "bottom": bars[q]["l"], "created": j, "origin": q, "break": ev["type"]})
                obs.append(oz)
            seqs[(ev["t"], d)] = {"id": sw["id"], "model": "REV", "dir": d, "sweep": sw, "event": ev,
                                  "anchor": sw["price"], "fvg": fz, "ob": oz, "origin_i": i}
            break

    # ---- model CONTINUATION: BOS eksternal searah tren / liquidity run + FVG di kaki impuls
    for e in ext["events"]:
        if e["type"] != "BOS": continue
        d, j, o = e["dir"], e["index"], e["origin"]
        run = next((r for r in runs if r["dir"] == d and o <= r["index"] <= j + 2), None)
        fz = best_zone(fvgs, d, o - 1, j + 1)
        if fz is None and run is None: continue
        if (e["t"], d) in seqs: continue
        oz = best_zone(obs, d, o - OB_LOOKBACK, j)
        if fz is None and oz is None: continue
        ev = dict(e, type="BOS", displacement=bool(fz), strong=any(_disp(bars[q], d, A, strong_disp_atr) for q in range(o, j + 1)))
        anchor = bars[o]["l"] if d == "bull" else bars[o]["h"]
        seqs[(e["t"], d)] = {"id": f"BOS:{e['t']}", "model": "CONT", "dir": d, "sweep": None, "run": run, "event": ev,
                             "anchor": anchor, "fvg": fz, "ob": oz, "origin_i": o}

    # ---- lengkapi sequence: leg, inducement, invalidasi; tandai zona kontekstual (★)
    out_seq = []
    for s in sorted(seqs.values(), key=lambda s: s["event"]["index"]):
        d, j, anc = s["dir"], s["event"]["index"], s["anchor"]
        if d == "bull": lo, hi = anc, max(b["h"] for b in bars[j:])
        else: hi, lo = anc, min(b["l"] for b in bars[j:])
        span = max(hi - lo, 1e-12)
        s["leg"] = {"lo": lo, "hi": hi, "eq": (lo + hi) / 2,
                    "ote_bottom": hi - 0.79 * span if d == "bull" else lo + 0.62 * span,
                    "ote_top": hi - 0.62 * span if d == "bull" else lo + 0.79 * span}
        poi = [z for z in (s["fvg"], s["ob"]) if z]
        if not poi: continue
        edge = max(z["top"] for z in poi) if d == "bull" else min(z["bottom"] for z in poi)
        s["inducement"] = _inducement(bars, j, d, edge)
        s["invalid"] = any((b["c"] < anc) if d == "bull" else (b["c"] > anc) for b in bars[j + 1:])
        f, o = s["fvg"], s["ob"]
        s["overlap"] = bool(f and o and f["bottom"] <= o["top"] and o["bottom"] <= f["top"])
        out_seq.append(s)
    # ★ hanya untuk sequence terbaru yang belum invalid (POI lama tidak lagi relevan)
    for s in [x for x in out_seq if not x["invalid"]][-MAX_CONTEXT:]:
        for z in (s["fvg"], s["ob"]):
            if z: z["context"] = True; z["model"] = s["model"]; z["seq_id"] = s["id"]

    # ---- pilih zona yang ditampilkan: semua ★ aktif + zona aktif terdekat dari harga
    active = lambda z: z["status"] in ("fresh", "tapped")

    def pick(zs):
        act = [z for z in zs if active(z)]
        ctx = [z for z in act if z.get("context")]
        rest = sorted([z for z in act if not z.get("context")], key=lambda z: min(abs(price - z["top"]), abs(price - z["bottom"])))
        bull = [z for z in rest if z["dir"] == "bull"][:max_zones]; bear = [z for z in rest if z["dir"] == "bear"][:max_zones]
        shown = sorted(ctx + bull + bear, key=lambda z: z["created"])
        mit = sorted([z for z in zs if not active(z)], key=lambda z: z.get("end_i") or 0)[-6:]
        return shown, mit

    fv_show, fv_mit = pick(fvgs + ifvg)
    ob_show, ob_mit = pick(obs + bbs)
    used = {s["sweep"]["id"] for s in out_seq if s.get("sweep")}
    shown_sweeps = [s for s in sweeps if s["id"] in used or s["kind"] in HIGH_VALUE]
    seq_events = [s["event"] for s in out_seq if s["model"] == "REV"]
    events = sorted(ext["events"][-12:] + seq_events[-8:], key=lambda e: e["index"])
    ints = [dict(e, type="i" + e["type"]) for e in itl["events"][-12:]]
    return {"trend": ext["trend"], "internal_trend": itl["trend"], "dealing_range": dr, "liquidity": pools,
            "sweeps": sorted(shown_sweeps, key=lambda s: s["index"])[-10:], "runs": runs[-6:],
            "events": events[-20:], "internal_events": ints,
            "fvg": fv_show, "fvg_mit": fv_mit, "order_blocks": ob_show, "ob_mitigated": ob_mit,
            "sequences": out_seq[-12:]}
