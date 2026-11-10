"""Zone engine v17 – BUY AREA / SELL AREA berbasis FVG & iOB (simetris bull/bear).

Siklus satu zona (hanya candle tertutup, tanpa lookahead):

  WATCH      : Bull/Bear FVG atau Bull/Bear iOB terbentuk (belum disentuh, tidak digambar sebagai area)
  AREA       : harga kembali menyentuh zona & skor >= ZONE_AREA_SCORE -> "Potential BUY/SELL AREA"
  SETUP      : sweep liquidity searah zona (SSL untuk BUY, BSL untuk SELL) di/dekat zona
               dan skor >= ZONE_SETUP_SCORE                     -> "BUY/SELL SETUP"
  CONFIRMED  : dalam ZONE_CONFIRM_WINDOW candle setelah sweep muncul displacement
               ATAU iCHoCH/CHoCH/iBOS/BOS ATAU CISD searah, dan skor >= ZONE_CONFIRM_SCORE
                                                                -> "BUY/SELL CONFIRMED"
  INVALID    : ada candle CLOSE menembus sisi jauh zona (BUY: close < bottom, SELL: close > top)

Skor confluence (maks 10, sama untuk BUY dan SELL):
  FVG +1 · iOB +1 (zona sendiri atau overlap zona jenis lain searah) · lokasi discount/premium +1
  liquidity context (SSL/BSL aktif di/dekat zona saat disentuh) +1 · sweep +2 · displacement +2
  iCHoCH/CHoCH/CISD +2 (iBOS/BOS saja = +1)

Bila konfirmasi tidak datang dalam jendela, setup kedaluwarsa dan zona kembali ke AREA
(sweep berikutnya bisa membuat setup baru). Setelah CONFIRMED zona hanya dipantau sampai invalid.
"""
from bisect import bisect_right

import config
from smc import is_displacement, premium_discount_at

MAX_SCORE = 10


def _r(v, d):
    return None if v is None else round(v, d)


def _range_at(bars, s_highs, s_lows, k, j):
    """Dealing range swing yang sudah DIKETAHUI pada bar j (pivot terkonfirmasi k bar kemudian)."""
    a, b = bisect_right(s_highs, j - k) - 1, bisect_right(s_lows, j - k) - 1
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


def _cisd_level(bars, sweep_i, bull):
    """CISD: open candle pertama dari rangkaian candle berlawanan terakhir sampai candle sweep.
    BUY  -> close di atas open rangkaian candle bearish terakhir.
    SELL -> close di bawah open rangkaian candle bullish terakhir."""
    opp = (lambda b: b["c"] < b["o"]) if bull else (lambda b: b["c"] > b["o"])
    j = sweep_i
    lim = max(0, sweep_i - 20)
    while j >= lim and not opp(bars[j]):
        j -= 1
    if j < lim:
        return None
    while j - 1 >= lim and opp(bars[j - 1]):
        j -= 1
    return bars[j]["o"]


def build_zones(tf, bars, smc, atr14, digits):
    n = len(bars)
    if n < 10:
        return []
    start_i = max(0, n - config.ZONE_LOOKBACK)
    k = smc.get("swing_len", 1)
    s_highs, s_lows = smc.get("s_highs", []), smc.get("s_lows", [])

    # ---- kandidat: semua FVG + OB internal (aktif & yang sudah mitigasi, untuk riwayat) ----
    cands = []
    for z in (smc.get("fvg") or []) + (smc.get("fvg_mit") or []):
        if z.get("created", 0) >= start_i:
            cands.append(("FVG", z))
    for z in (smc.get("order_blocks") or []) + (smc.get("ob_mitigated") or []):
        if z.get("scope") == "internal" and z.get("created", 0) >= start_i:
            cands.append(("iOB", z))
    cands.sort(key=lambda c: (c[1]["created"], c[0]))

    # ---- indeks event per bar ----
    sweeps_at, events_at = {}, {}
    for s in smc.get("sweeps") or []:
        sweeps_at.setdefault(s["index"], []).append(s)
    for e in smc.get("events") or []:
        events_at.setdefault(e["index"], []).append(e)
    pools = (smc.get("liquidity") or []) + (smc.get("liquidity_done") or [])

    def atr(j):
        v = atr14[j] if j < len(atr14) else None
        return v if v else max(bars[j]["h"] - bars[j]["l"], 1e-12)

    def overlap_bonus(zone, j):
        """Zona jenis lain (FVG <-> iOB) searah yang sudah ada pada bar j dan overlap -> +1."""
        other = "iOB" if zone["kind"] == "FVG" else "FVG"
        for kind, z in cands:
            if kind != other or z["dir"] != zone["dir"] or z["created"] > j:
                continue
            if z.get("end_i") is not None and z["end_i"] <= j and kind == "iOB":
                continue
            if z["bottom"] <= zone["top"] and z["top"] >= zone["bottom"]:
                return True
        return False

    def liq_context(zone, j):
        bull = zone["dir"] == "bull"
        side = "SSL" if bull else "BSL"
        a = atr(j)
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

    out = []
    for kind, src in cands:
        bull = src["dir"] == "bull"
        side = "BUY" if bull else "SELL"
        zone = {"kind": kind, "dir": src["dir"], "top": src["top"], "bottom": src["bottom"]}
        created = src["created"]
        top, bottom = src["top"], src["bottom"]
        if top - bottom <= 1e-12:
            continue

        stage, tap_i, inv_i = "WATCH", None, None
        loc, liq_lv, ovl = None, None, False
        setup, confirm = None, None
        events = []

        def score_now(s=None, c=None):
            pts, why = 0, []
            if kind == "FVG" or ovl:
                pts += 1
                why.append([True, f"{'Bull' if bull else 'Bear'} FVG", 1])
            if kind == "iOB" or ovl:
                pts += 1
                why.append([True, f"{'Bull' if bull else 'Bear'} iOB", 1])
            loc_ok = (bull and loc in ("discount", "extreme_discount")) or (not bull and loc in ("premium", "extreme_premium"))
            if loc_ok:
                pts += 1
            why.append([loc_ok, f"Lokasi {(loc or '-').replace('_', ' ')}", 1 if loc_ok else 0])
            if liq_lv is not None:
                pts += 1
                why.append([True, f"Liquidity {'SSL' if bull else 'BSL'} dekat zona @ {_r(liq_lv, digits)}", 1])
            if s:
                pts += 2
                why.append([True, f"{s['side']} sweep @ {_r(s['level'], digits)} (ekstrem {_r(s['price'], digits)})", 2])
            if c:
                if c.get("disp"):
                    pts += 2
                    why.append([True, f"Displacement {'bullish' if bull else 'bearish'}", 2])
                st = c.get("struct")
                if st:
                    p = 2 if st["strong"] else 1
                    pts += p
                    why.append([True, f"{st['label']} {'bullish' if bull else 'bearish'} @ {_r(st['level'], digits)}", p])
            return min(pts, MAX_SCORE), why

        def push(st, j, extra=None):
            sc, why = score_now(setup, confirm)
            ev = {"stage": st, "t": bars[j]["t"], "score": sc, "price": _r(bars[j]["c"], digits),
                  "why": [[t, p] for ok, t, p in why if ok]}
            if extra:
                ev.update(extra)
            events.append(ev)
            return sc, why

        last_j = min(n - 1, created + config.ZONE_MAX_AGE)
        end_i, end_why = None, None
        j = created + 1
        while j < n:
            b = bars[j]
            # 1) invalid: close menembus sisi jauh
            if (bull and b["c"] < bottom) or (not bull and b["c"] > top):
                inv_i = j
                stage_before = stage
                stage = "INVALID"
                if stage_before != "WATCH":
                    push("INVALID", j)
                break
            touch = b["l"] <= top and b["h"] >= bottom
            if tap_i is None:
                if j > last_j:
                    break                      # kedaluwarsa tanpa pernah disentuh
                if not touch:
                    j += 1
                    continue
                tap_i, stage = j, "AREA"
                rng = _range_at(bars, s_highs, s_lows, k, j)
                loc = premium_discount_at(b["c"], rng)
                liq_lv = liq_context(zone, j)
                ovl = overlap_bonus(zone, j)
                area_sc, _ = score_now()
                if area_sc >= config.ZONE_AREA_SCORE:
                    push("AREA", j)
            if stage == "CONFIRMED":
                if j - confirm["i"] > config.ZONE_MAX_AGE:
                    end_i, end_why = j, "selesai"        # sudah lama terkonfirmasi -> berhenti digambar
                    break
                j += 1
                continue
            if stage == "AREA" and setup is None and j - tap_i > config.ZONE_MAX_AGE:
                end_i, end_why, stage = j, "kedaluwarsa", "EXPIRED"
                break
            # 2) setup: sweep searah di/dekat zona
            if setup is None:
                a = atr(j)
                prox = a * config.POI_PROXIMITY_ATR
                for s in sweeps_at.get(j, []):
                    if s["dir"] != src["dir"]:
                        continue
                    ok = (bottom - a * config.ZONE_SWEEP_ATR <= s["price"] <= top + prox) if bull else \
                         (bottom - prox <= s["price"] <= top + a * config.ZONE_SWEEP_ATR)
                    if not ok:
                        continue
                    cand = {"side": s["side"], "level": s["level"], "price": s["price"], "t": s["t"], "i": j,
                            "cisd": _cisd_level(bars, j, bull)}
                    sc, _ = score_now(cand, None)
                    if sc >= config.ZONE_SETUP_SCORE:
                        setup, confirm = cand, {"disp": False, "struct": None, "by": []}
                        stage = "SETUP"
                        push("SETUP", j, {"sweep_t": s["t"]})
                        break
            # 3) konfirmasi (termasuk candle sweep itu sendiri)
            if setup is not None and stage == "SETUP":
                if j - setup["i"] > config.ZONE_CONFIRM_WINDOW:
                    events.append({"stage": "EXPIRED", "t": b["t"], "score": score_now(setup, confirm)[0],
                                   "price": _r(b["c"], digits)})
                    setup, confirm, stage = None, None, "AREA"
                    j += 1
                    continue
                aj = atr(j)
                if not confirm["disp"] and is_displacement(b, src["dir"], aj, config.DISPLACEMENT_ATR,
                                                           config.DISPLACEMENT_BODY_RATIO):
                    confirm["disp"] = True
                    confirm["by"].append("Displacement")
                for e in events_at.get(j, []):
                    if e["dir"] != src["dir"]:
                        continue
                    strong = e["type"] == "CHoCH"
                    cur = confirm["struct"]
                    if cur is None or (strong and not cur["strong"]):
                        confirm["struct"] = {"label": e["label"], "level": e["level"], "strong": strong, "t": e["t"]}
                        confirm["by"].append(e["label"])
                cl = setup.get("cisd")
                if cl is not None and (confirm["struct"] is None or not confirm["struct"]["strong"]):
                    if (bull and b["c"] > cl) or (not bull and b["c"] < cl):
                        confirm["struct"] = {"label": "CISD", "level": cl, "strong": True, "t": b["t"]}
                        confirm["by"].append("CISD")
                if confirm["disp"] or confirm["struct"]:
                    sc, _ = score_now(setup, confirm)
                    if sc >= config.ZONE_CONFIRM_SCORE:
                        stage = "CONFIRMED"
                        confirm["t"], confirm["i"], confirm["price"] = b["t"], j, b["c"]
                        push("CONFIRMED", j, {"by": list(confirm["by"])})
            j += 1

        if tap_i is None:
            continue                            # tidak pernah disentuh -> tidak ditampilkan
        if not any(e["stage"] in ("AREA", "SETUP", "CONFIRMED") for e in events):
            continue                            # confluence terlalu lemah & tidak pernah sweep -> bukan area
        sc, why = score_now(setup, confirm)
        conf = confirm if (confirm and confirm.get("t")) else None
        inv_level = bottom if bull else top
        zid = f"ZN:{src['dir']}:{kind}:{src['t']}"
        rec = {
            "id": zid, "dir": src["dir"], "side": side, "kind": kind + ("+" + ("iOB" if kind == "FVG" else "FVG") if ovl else ""),
            "src_id": src.get("id"), "t": src["t"], "created_t": bars[created]["t"],
            "tap_t": bars[tap_i]["t"], "top": _r(top, digits), "bottom": _r(bottom, digits),
            "stage": stage, "score": sc, "max_score": MAX_SCORE,
            "reasons": why, "location": loc,
            "invalidation": _r(inv_level, digits),
            "sweep": ({"side": setup["side"], "level": _r(setup["level"], digits), "price": _r(setup["price"], digits),
                       "t": setup["t"]} if setup else None),
            "confirm": ({"t": conf["t"], "price": _r(conf["price"], digits), "by": conf["by"]} if conf else None),
            "events": events,
        }
        if inv_i is not None:
            rec["end_t"] = bars[inv_i]["t"]
            rec["invalid_t"] = bars[inv_i]["t"]
        elif end_i is not None:
            rec["end_t"] = bars[end_i]["t"]
            rec["end_why"] = end_why
        out.append(rec)
    out.sort(key=lambda z: z["tap_t"])
    return out
