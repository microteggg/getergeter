"""Zone engine v18 – BUY AREA / SELL AREA berbasis FVG & OB (simetris bull/bear).

Dua model setup (jalur) untuk satu zona:

  A) SWEEP REVERSAL  : harga di zona -> sweep liquidity searah (SSL untuk BUY, BSL untuk SELL)
                       -> displacement / iCHoCH / CISD
  B) CHoCH RETEST    : CHoCH/iCHoCH (karakter baru, leg-nya ada displacement) -> harga RETEST
                       OB/FVG searah karakter baru di premium (SELL) / discount (BUY) leg CHoCH
                       -> rejection: displacement / iBOS / BOS / CISD searah -> lanjut trend baru
                       Sweep di model B hanya bonus skor, bukan syarat.

Siklus zona (hanya candle tertutup, tanpa lookahead):
  AREA       : harga menyentuh zona & skor >= ZONE_AREA_SCORE           -> "Potential BUY/SELL AREA"
  SETUP      : model A (sweep) atau model B (retest setelah CHoCH)      -> "BUY/SELL SETUP"
               & skor >= ZONE_SETUP_SCORE
  CONFIRMED  : dalam ZONE_CONFIRM_WINDOW candle setelah setup muncul displacement / iCHoCH /
               iBOS / BOS / CISD searah & skor >= ZONE_CONFIRM_SCORE (A) / ZONE_RETEST_CONFIRM_SCORE (B)
  INVALID    : candle CLOSE menembus sisi jauh zona (BUY: close < bottom, SELL: close > top)
  Model B juga gugur bila karakter berbalik (CHoCH berlawanan dengan scope >= scope CHoCH konteks).

Skor confluence (maks 10, sama untuk BUY & SELL):
  FVG +1 · OB (iOB/sOB) +1 · lokasi discount/premium +1 · liquidity dekat zona +1
  CHoCH karakter baru +2 · sweep +2 · displacement +2 · iCHoCH/CHoCH/CISD +2 (iBOS/BOS +1)
"""
from bisect import bisect_left

import config
from smc import is_displacement, premium_discount_at

MAX_SCORE = 10
MODEL_NAME = {"sweep": "Sweep Reversal", "retest": "CHoCH Retest"}


def _r(v, d):
    return None if v is None else round(v, d)


def _range_at(bars, s_highs, s_lows, k, j):
    """Dealing range swing yang sudah DIKETAHUI pada bar j (pivot terkonfirmasi k bar kemudian)."""
    a, b = bisect_left(s_highs, j - k + 1) - 1, bisect_left(s_lows, j - k + 1) - 1
    if a < 0 or b < 0:
        return None
    hi, lo = bars[s_highs[a]]["h"], bars[s_lows[b]]["l"]
    top, bottom = max(hi, lo), min(hi, lo)
    if top - bottom <= 1e-12:
        return None
    return {"top": top, "bottom": bottom, "width": top - bottom}


def _pool_level(pool, j):
    level = None
    for i, lv in pool.get("hist") or [(pool.get("start", 0), pool["level"])]:
        if i > j:
            break
        level = lv
    return level


def _cisd_level(bars, i, bull):
    """CISD: open candle pertama dari rangkaian candle berlawanan terakhir sampai bar i.
    BUY -> close di atas open rangkaian candle bearish terakhir; SELL -> kebalikannya."""
    opp = (lambda b: b["c"] < b["o"]) if bull else (lambda b: b["c"] > b["o"])
    j, lim = i, max(0, i - 20)
    while j >= lim and not opp(bars[j]):
        j -= 1
    if j < lim:
        return None
    while j - 1 >= lim and opp(bars[j - 1]):
        j -= 1
    return bars[j]["o"]


def _choch_contexts(bars, events, atr14):
    """CHoCH (internal & swing) + leg-nya + index kapan karakter itu gugur."""
    rank = {"internal": 0, "swing": 1}
    out = {"bull": [], "bear": []}
    for e in events:
        if e["type"] != "CHoCH":
            continue
        bull = e["dir"] == "bull"
        p, i = e["pivot_index"], e["index"]
        if i <= p:
            continue
        leg = range(p, i)
        # titik asal leg = ekstrem berlawanan sebelum break (sama dengan origin OB)
        o = min(leg, key=lambda j: bars[j]["l"]) if bull else max(leg, key=lambda j: bars[j]["h"])
        disp = any(is_displacement(bars[j], e["dir"], (atr14[j] if j < len(atr14) and atr14[j] else
                                                       bars[j]["h"] - bars[j]["l"]),
                                   config.DISPLACEMENT_ATR, config.DISPLACEMENT_BODY_RATIO)
                   for j in range(o, i + 1))
        kill = None
        for f in events:
            if f["index"] <= i or f["dir"] == e["dir"] or f["type"] != "CHoCH":
                continue
            if rank[f["scope"]] >= rank[e["scope"]]:
                kill = f["index"]
                break
        out[e["dir"]].append({"e": e, "index": i, "pivot": p, "origin_i": o,
                              "origin": bars[o]["l"] if bull else bars[o]["h"],
                              "disp": disp, "kill": kill})
    for d in out:
        out[d].sort(key=lambda c: c["index"])
    return out


def build_zones(tf, bars, smc, atr14, digits):
    n = len(bars)
    if n < 10:
        return []
    start_i = max(0, n - config.ZONE_LOOKBACK)
    k = smc.get("swing_len", 1)
    s_highs, s_lows = smc.get("s_highs", []), smc.get("s_lows", [])

    # ---- kandidat: FVG + OB internal (iOB) + OB swing (sOB); aktif & sudah mitigasi ----
    cands, seen = [], set()
    for z in (smc.get("fvg") or []) + (smc.get("fvg_mit") or []):
        if z.get("created", 0) >= start_i:
            cands.append(("FVG", z))
    obs = sorted((smc.get("order_blocks") or []) + (smc.get("ob_mitigated") or []),
                 key=lambda z: z.get("scope") != "swing")          # sOB didahulukan bila candle sama
    for z in obs:
        if z.get("created", 0) < start_i:
            continue
        key = (z["dir"], z["t"])
        if key in seen:
            continue
        seen.add(key)
        cands.append(("sOB" if z.get("scope") == "swing" else "iOB", z))
    cands.sort(key=lambda c: (c[1]["created"], c[0]))

    sweeps_at, events_at = {}, {}
    for s in smc.get("sweeps") or []:
        sweeps_at.setdefault(s["index"], []).append(s)
    events = smc.get("events") or []
    for e in events:
        events_at.setdefault(e["index"], []).append(e)
    pools = (smc.get("liquidity") or []) + (smc.get("liquidity_done") or [])
    chochs = _choch_contexts(bars, events, atr14) if config.ZONE_RETEST_ENABLED else {"bull": [], "bear": []}
    ch_idx = {d: [c["index"] for c in chochs[d]] for d in chochs}

    def atr(j):
        v = atr14[j] if j < len(atr14) else None
        return v if v else max(bars[j]["h"] - bars[j]["l"], 1e-12)

    def overlap_kind(zone, j):
        """Zona jenis lain (FVG <-> OB) searah yang sudah ada pada bar j dan overlap."""
        for kind, z in cands:
            if (kind == "FVG") == (zone["kind"] == "FVG") or z["dir"] != zone["dir"] or z["created"] > j:
                continue
            if z.get("end_i") is not None and z["end_i"] <= j:
                continue
            if z["bottom"] <= zone["top"] and z["top"] >= zone["bottom"]:
                return kind
        return None

    def liq_context(zone, j):
        bull = zone["dir"] == "bull"
        side, a = ("SSL" if bull else "BSL"), atr(j)
        for p in pools:
            if p["side"] != side or p.get("start", 0) > j or (p.get("end_i") is not None and p["end_i"] < j):
                continue
            lv = _pool_level(p, j)
            if lv is None:
                continue
            if bull and zone["bottom"] - a <= lv <= zone["top"]:
                return lv
            if not bull and zone["bottom"] <= lv <= zone["top"] + a:
                return lv
        return None

    def choch_ctx(d, created, j):
        """CHoCH searah terakhir sebelum bar j, zona terbentuk di leg/sesudahnya, karakter masih utuh."""
        lst = chochs[d]
        x = bisect_left(ch_idx[d], j) - 1
        while x >= 0:
            c = lst[x]
            if c["kill"] is not None and c["kill"] <= j:
                return None                     # karakter terbaru sudah gugur
            if c["origin_i"] <= created and (c["disp"] or not config.ZONE_RETEST_NEED_DISP):
                return c
            if c["origin_i"] > created:
                x -= 1                          # zona lebih tua dari leg ini -> cek CHoCH sebelumnya
                continue
            return None
        return None

    out = []
    for kind, src in cands:
        bull = src["dir"] == "bull"
        side = "BUY" if bull else "SELL"
        top, bottom, created = src["top"], src["bottom"], src["created"]
        if top - bottom <= 1e-12:
            continue
        zone = {"kind": kind, "dir": src["dir"], "top": top, "bottom": bottom}

        st = {"stage": "WATCH", "loc": None, "leg_ok": False, "liq": None, "ovl": None, "ctx": None,
              "setup": None, "confirm": None, "n_setup": 0}
        events_out = []

        def score_now(s=None, c=None):
            pts, why = 0, []
            ovl = st["ovl"]
            name = "Bull" if bull else "Bear"
            if kind == "FVG" or ovl == "FVG":
                pts += 1
                why.append([True, f"{name} FVG", 1])
            if kind != "FVG" or ovl in ("iOB", "sOB"):
                pts += 1
                why.append([True, f"{name} {kind if kind != 'FVG' else ovl}", 1])
            loc = st["loc"]
            loc_ok = ((bull and loc in ("discount", "extreme_discount")) or
                      (not bull and loc in ("premium", "extreme_premium")))
            if loc_ok or st["leg_ok"]:
                pts += 1
                txt = (f"Lokasi {(loc or '').replace('_', ' ')}" if loc_ok else
                       f"Lokasi {'discount' if bull else 'premium'} leg CHoCH (≥50%)")
                why.append([True, txt, 1])
            else:
                why.append([False, f"Lokasi {(loc or '-').replace('_', ' ')}", 0])
            if st["liq"] is not None:
                pts += 1
                why.append([True, f"Liquidity {'SSL' if bull else 'BSL'} dekat zona @ {_r(st['liq'], digits)}", 1])
            cx = st["ctx"]
            if cx:
                pts += 2
                why.append([True, f"{cx['e']['label']} {'bullish' if bull else 'bearish'} @ "
                                  f"{_r(cx['e']['level'], digits)} (karakter baru)", 2])
            if s and s.get("sweep"):
                sw = s["sweep"]
                pts += 2
                why.append([True, f"{sw['side']} sweep @ {_r(sw['level'], digits)} (ekstrem {_r(sw['price'], digits)})", 2])
            if s and s["model"] == "retest":
                why.append([True, f"Retest zona setelah {cx['e']['label'] if cx else 'CHoCH'}", 0])
            if c:
                if c.get("disp"):
                    pts += 2
                    why.append([True, f"Displacement {'bullish' if bull else 'bearish'}", 2])
                sc = c.get("struct")
                if sc:
                    p = 2 if sc["strong"] else 1
                    pts += p
                    why.append([True, f"{sc['label']} {'bullish' if bull else 'bearish'} @ {_r(sc['level'], digits)}", p])
            return min(pts, MAX_SCORE), why

        def push(stage, j, extra=None):
            sc, why = score_now(st["setup"], st["confirm"])
            ev = {"stage": stage, "t": bars[j]["t"], "score": sc, "price": _r(bars[j]["c"], digits),
                  "why": [[t, p] for ok, t, p in why if ok]}
            if st["setup"]:
                ev["model"] = st["setup"]["model"]
            if extra:
                ev.update(extra)
            events_out.append(ev)

        last_j = min(n - 1, created + config.ZONE_MAX_AGE)
        tap_i = inv_i = end_i = end_why = None
        j = created + 1
        while j < n:
            b = bars[j]
            if (bull and b["c"] < bottom) or (not bull and b["c"] > top):
                inv_i = j
                was = st["stage"]
                st["stage"] = "INVALID"
                if was != "WATCH":
                    push("INVALID", j)
                break
            touch = b["l"] <= top and b["h"] >= bottom
            # konteks CHoCH (model B) dievaluasi ulang tiap bar; gugur bila karakter berbalik
            if config.ZONE_RETEST_ENABLED and st["stage"] != "CONFIRMED":
                st["ctx"] = choch_ctx(src["dir"], created, j)
            if tap_i is None:
                if j > last_j:
                    break
                if not touch:
                    j += 1
                    continue
                tap_i, st["stage"] = j, "AREA"
                st["loc"] = premium_discount_at(b["c"], _range_at(bars, s_highs, s_lows, k, j))
                st["liq"] = liq_context(zone, j)
                st["ovl"] = overlap_kind(zone, j)
                if score_now()[0] >= config.ZONE_AREA_SCORE:
                    push("AREA", j)
            if st["stage"] == "CONFIRMED":
                if j - st["confirm"]["i"] > config.ZONE_MAX_AGE:
                    end_i, end_why = j, "selesai"
                    break
                j += 1
                continue
            if st["stage"] == "AREA" and st["setup"] is None and j - tap_i > config.ZONE_MAX_AGE:
                end_i, end_why, st["stage"] = j, "kedaluwarsa", "EXPIRED"
                break

            # ---- SETUP ----
            if st["setup"] is None and st["n_setup"] < config.ZONE_MAX_SETUPS:
                a = atr(j)
                prox = a * config.POI_PROXIMITY_ATR
                cand = None
                # model A: sweep searah di/dekat zona
                for s in sweeps_at.get(j, []):
                    if s["dir"] != src["dir"]:
                        continue
                    ok = (bottom - a * config.ZONE_SWEEP_ATR <= s["price"] <= top + prox) if bull else \
                         (bottom - prox <= s["price"] <= top + a * config.ZONE_SWEEP_ATR)
                    if ok:
                        cand = {"model": "sweep", "i": j,
                                "sweep": {"side": s["side"], "level": s["level"], "price": s["price"], "t": s["t"]}}
                        break
                # model B: retest zona setelah CHoCH, di premium/discount leg CHoCH
                cx = st["ctx"]
                if cand is None and cx and touch and j > cx["index"]:
                    seg = bars[cx["index"]:j + 1]
                    if bull:
                        ext = max(x["h"] for x in seg)
                        eq = (ext + cx["origin"]) / 2
                        leg_ok = b["l"] <= eq
                    else:
                        ext = min(x["l"] for x in seg)
                        eq = (ext + cx["origin"]) / 2
                        leg_ok = b["h"] >= eq
                    if leg_ok or not config.ZONE_RETEST_NEED_PD:
                        st["leg_ok"] = leg_ok
                        cand = {"model": "retest", "i": j, "sweep": None, "eq": eq, "origin": cx["origin"],
                                "choch": cx["e"]["label"], "choch_level": cx["e"]["level"]}
                if cand:
                    cand["cisd"] = _cisd_level(bars, j, bull)
                    if score_now(cand, None)[0] >= config.ZONE_SETUP_SCORE:
                        st["setup"], st["confirm"] = cand, {"disp": False, "struct": None, "by": []}
                        st["stage"] = "SETUP"
                        st["n_setup"] += 1
                        push("SETUP", j)
                    else:
                        st["leg_ok"] = False

            # ---- CONFIRMED (termasuk candle setup itu sendiri) ----
            if st["setup"] is not None and st["stage"] == "SETUP":
                su, cf = st["setup"], st["confirm"]
                if j - su["i"] > config.ZONE_CONFIRM_WINDOW or (su["model"] == "retest" and st["ctx"] is None):
                    events_out.append({"stage": "EXPIRED", "t": b["t"], "score": score_now(su, cf)[0],
                                       "price": _r(b["c"], digits), "model": su["model"]})
                    st["setup"], st["confirm"], st["stage"], st["leg_ok"] = None, None, "AREA", False
                    j += 1
                    continue
                if not cf["disp"] and is_displacement(b, src["dir"], atr(j), config.DISPLACEMENT_ATR,
                                                      config.DISPLACEMENT_BODY_RATIO):
                    cf["disp"] = True
                    cf["by"].append("Displacement")
                for e in events_at.get(j, []):
                    if e["dir"] != src["dir"]:
                        continue
                    strong = e["type"] == "CHoCH"
                    cur = cf["struct"]
                    if cur is None or (strong and not cur["strong"]):
                        cf["struct"] = {"label": e["label"], "level": e["level"], "strong": strong}
                        cf["by"].append(e["label"])
                cl = su.get("cisd")
                if cl is not None and j > su["i"] and (cf["struct"] is None or not cf["struct"]["strong"]):
                    if (bull and b["c"] > cl) or (not bull and b["c"] < cl):
                        cf["struct"] = {"label": "CISD", "level": cl, "strong": True}
                        cf["by"].append("CISD")
                if cf["disp"] or cf["struct"]:
                    need = config.ZONE_RETEST_CONFIRM_SCORE if su["model"] == "retest" else config.ZONE_CONFIRM_SCORE
                    if score_now(su, cf)[0] >= need:
                        st["stage"] = "CONFIRMED"
                        cf["t"], cf["i"], cf["price"] = b["t"], j, b["c"]
                        push("CONFIRMED", j, {"by": list(cf["by"])})
            j += 1

        if tap_i is None or not any(e["stage"] in ("AREA", "SETUP", "CONFIRMED") for e in events_out):
            continue
        su, cf = st["setup"], st["confirm"]
        last_model = next((e.get("model") for e in reversed(events_out) if e.get("model")), None)
        sc, why = score_now(su, cf)
        conf = cf if (cf and cf.get("t")) else None
        ovl = st["ovl"]
        rec = {
            "id": f"ZN:{src['dir']}:{kind}:{src['t']}", "dir": src["dir"], "side": side,
            "kind": kind + (f"+{ovl}" if ovl else ""), "src_id": src.get("id"),
            "t": src["t"], "created_t": bars[created]["t"], "tap_t": bars[tap_i]["t"],
            "top": _r(top, digits), "bottom": _r(bottom, digits),
            "stage": st["stage"], "score": sc, "max_score": MAX_SCORE, "reasons": why,
            "location": st["loc"], "model": last_model, "model_name": MODEL_NAME.get(last_model),
            "invalidation": _r(bottom if bull else top, digits),
            "struct_invalid": (_r(su["origin"], digits) if su and su["model"] == "retest" else None),
            "sweep": ({k2: (_r(v, digits) if isinstance(v, float) else v) for k2, v in su["sweep"].items()}
                      if su and su.get("sweep") else None),
            "confirm": ({"t": conf["t"], "price": _r(conf["price"], digits), "by": conf["by"]} if conf else None),
            "events": events_out,
        }
        if inv_i is not None:
            rec["end_t"] = rec["invalid_t"] = bars[inv_i]["t"]
        elif end_i is not None:
            rec["end_t"], rec["end_why"] = bars[end_i]["t"], end_why
        out.append(rec)
    out.sort(key=lambda z: z["tap_t"])
    return out
