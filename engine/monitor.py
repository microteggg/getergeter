"""Monitoring kejadian & setup v19 (OBSERVATION MODE – bukan sinyal BUY/SELL).

Dipindai pada MONITOR_TF (default M15), hanya candle tertutup, tanpa lookahead:
  STRUCTURE       BOS / CHoCH swing (iBOS/iCHoCH bila MONITOR_INTERNAL_STRUCTURE)
  SWEEP           sweep liquidity swing / EQH / EQL
  FVG_NEW         FVG baru terbentuk                         (default: hanya dicatat)
  OB_NEW          Order Block baru terbentuk                  (default: hanya dicatat)
  HTF_FIB         harga menyentuh level Fib utama (H4) 38.2 / 50 / 61.8 / 78.6 – sekali per siklus fib
  LOCAL_FIB       harga menyentuh Fib lokal M15 50 / 61.8 / 78.6 (default: hanya dicatat)
  FIB_INVALID     close menembus 100% Fib utama (H4)
  OB_FVG_OVERLAP  harga retest area overlap OB + FVG searah
  CONFLUENCE      retest POI (OB/FVG) M15 searah Fib H4 & berada di area H4 38.2–78.6, skor:
                    POI di H4 50–61.8 +2 (38.2–78.6 +1) · Fib M15 50–78.6 +1 · OB+FVG overlap +1
                    sweep searah <= MONITOR_SWEEP_WINDOW candle +1 · BOS/CHoCH searah terbaru +1   (maks 6)
Fib yang dipakai pada candle j = fib yang sudah diketahui pada close candle j-1 (tidak memakai candle j).
Kejadian disimpan permanen di store/monitor/{SYM}.json (tidak pernah diubah kecuali status notif).
"""
import json
import os
from bisect import bisect_left

import config
import fibo
from resample import TF_SEC

ICON = {"STRUCTURE": "🔄", "SWEEP": "💧", "FVG_NEW": "▫️", "OB_NEW": "▪️", "HTF_FIB": "📐", "LOCAL_FIB": "📏",
        "FIB_INVALID": "❌", "OB_FVG_OVERLAP": "🧱", "CONFLUENCE": "⭐", "HTF_POI": "🎯"}
NAME = {"STRUCTURE": "Struktur", "SWEEP": "Sweep liquidity", "FVG_NEW": "FVG baru", "OB_NEW": "Order Block baru",
        "HTF_FIB": "Fib utama", "LOCAL_FIB": "Fib lokal", "FIB_INVALID": "Fib invalid",
        "OB_FVG_OVERLAP": "OB + FVG overlap", "CONFLUENCE": "Confluence", "HTF_POI": "HTF POI"}
FIB_TOUCH = {"HTF_FIB": (0.382, 0.5, 0.618, 0.786), "LOCAL_FIB": (0.5, 0.618, 0.786)}
MAX_SCORE = 6


def _r(v, d):
    return None if v is None else round(v, d)


def _pct(r):
    return f"{r * 100:g}%"


def _ovl(a_bot, a_top, b_bot, b_top):
    return a_bot <= b_top and a_top >= b_bot


def _touch(bar, bot, top):
    return bar["l"] <= top and bar["h"] >= bot


class Monitor:
    def __init__(self, store_dir, sym):
        self.sym = sym
        self.path = os.path.join(store_dir, "monitor", f"{sym}.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None
        self.first_run = data is None
        self.events = (data or {}).get("events", {})
        self.new = []

    # ------------------------------------------------------------------ scan
    def scan(self, tfs, digits, now):
        tf = config.MONITOR_TF
        m = tfs.get(tf) or {}
        bars = m.get("_bars")
        if not m.get("enough") or not bars:
            return []
        smc, atr = m["_smc"], m["_atr14"]
        sec, d, n = TF_SEC[tf], digits, len(bars)
        start = max(2, n - config.MONITOR_LOOKBACK)
        loc_ser = m["_fib_series"](start - 1)

        P = tfs.get(config.FIB_PRIMARY_TF) or {}
        pbars, psec = P.get("_bars") or [], TF_SEC.get(config.FIB_PRIMARY_TF, 14400)
        pclose = [b["t"] + psec for b in pbars]
        p0 = max(0, fibo.index_at(pclose, bars[start - 1]["t"] + sec) - 1) if pbars else 0
        pser = P["_fib_series"](p0) if pbars else {}

        def htf(j):
            if not pbars:
                return None
            h = fibo.index_at(pclose, bars[j]["t"] + sec)
            return fibo.lite(pser.get(h), pbars, h) if h >= 0 else None

        def loc(j):
            return fibo.lite(loc_ser.get(j), bars, j)

        ev_at, sw_at = {}, {}
        for e in smc.get("events") or []:
            ev_at.setdefault(e["index"], []).append(e)
        for s in smc.get("sweeps") or []:
            sw_at.setdefault(s["index"], []).append(s)
        lo_c = start - 150
        obs = [z for z in (smc.get("order_blocks") or []) + (smc.get("ob_mitigated") or []) if z["created"] >= lo_c]
        fvgs = [z for z in (smc.get("fvg") or []) + (smc.get("fvg_mit") or []) if z["created"] >= lo_c]
        pois = [("OB", z) for z in obs] + [("FVG", z) for z in fvgs]
        ev_idx = sorted(ev_at)
        sw_idx = sorted(sw_at)

        def alive(z, j):
            return z["created"] < j and (z.get("end_i") is None or z["end_i"] >= j)

        # kedalaman fib yang sudah tercatat per siklus (agar tiap level hanya sekali)
        depth = {}
        for r in self.events.values():
            if r["type"] in FIB_TOUCH and r.get("fib_id"):
                depth[(r["type"], r["fib_id"])] = max(depth.get((r["type"], r["fib_id"]), 0), r.get("ratio", 0))

        def add(j, typ, dirn, title, why, **kw):
            rid = kw.pop("rid")
            quiet = kw.pop("quiet", False)
            if rid in self.events:
                return
            b = bars[j]
            close_t = b["t"] + sec
            rec = {"id": rid, "type": typ, "tf": tf, "dir": dirn, "t": b["t"], "close_t": close_t,
                   "price": _r(b["c"], d), "title": title, "why": why,
                   "src": "live" if close_t >= now - 2 * sec else "replay", "first_seen": now}
            rec.update({k: v for k, v in kw.items() if v is not None})
            h = htf(j)
            if h:
                rt = fibo.retrace_of(h["dir"], h["hi"], h["lo"], b["c"])
                rec["htf"] = {"tf": config.FIB_PRIMARY_TF, "dir": h["dir"], "scope": h["scope"],
                              "hi": _r(h["hi"], d), "lo": _r(h["lo"], d), "valid": h["valid"],
                              "retrace": None if rt is None else round(rt * 100, 1)}
            notify = (typ in config.MONITOR_NOTIFY and rec["src"] == "live" and not self.first_run and not quiet)
            if typ == "HTF_POI" and rec.get("stage") not in config.HTF_POI_STAGES:
                notify = False
            rec["notif"] = "QUEUED" if notify else ("OFF" if rec["src"] == "live" else "REPLAY")
            self.events[rid] = rec
            self.new.append(rec)

        times = [bb["t"] for bb in bars]
        conf_seen = {(bisect_left(times, r["t"]), r["dir"]) for r in self.events.values()
                     if r["type"] == "CONFLUENCE" and r["t"] >= bars[start]["t"]}
        for j in range(start, n):
            b, bp = bars[j], bars[j - 1]
            # ---- STRUCTURE ----
            for e in ev_at.get(j, []):
                if e["scope"] == "internal" and not config.MONITOR_INTERNAL_STRUCTURE:
                    continue
                bull = e["dir"] == "bull"
                add(j, "STRUCTURE", e["dir"], f"{'Bullish' if bull else 'Bearish'} {e['label']}",
                    [f"Close menembus {'swing high' if bull else 'swing low'} {_r(e['level'], d)}",
                     "Displacement: " + ("ya" if e.get("displacement") else "tidak"),
                     ("Karakter berubah (CHoCH)" if e["type"] == "CHoCH" else "Continuation (BOS)")],
                    rid=f"MN:STRUCT:{e['id']}", level=_r(e["level"], d), label=e["label"])
            # ---- SWEEP ----
            for s in sw_at.get(j, []):
                if s["scope"] != "swing" and not s.get("pool_label"):
                    continue
                add(j, "SWEEP", s["dir"], f"{s['side']} sweep" + (f" ({s['pool_label']})" if s.get("pool_label") else ""),
                    [f"Wick menembus {s['side']} {_r(s['level'], d)} lalu close kembali",
                     f"Ekstrem sweep {_r(s['price'], d)} · {s.get('pool_touches', 1)} sentuhan pool"],
                    rid=f"MN:SWEEP:{s['id']}", level=_r(s["level"], d))
            # ---- POI baru ----
            for kind, z in pois:
                if z["created"] != j:
                    continue
                tag = "FVG" if kind == "FVG" else ("sOB" if z.get("scope") == "swing" else "iOB")
                add(j, "FVG_NEW" if kind == "FVG" else "OB_NEW", z["dir"],
                    f"{'Bullish' if z['dir'] == 'bull' else 'Bearish'} {tag} terbentuk",
                    [f"Area {_r(z['bottom'], d)} – {_r(z['top'], d)}"],
                    rid=f"MN:NEW:{z['id']}", top=_r(z["top"], d), bottom=_r(z["bottom"], d), kind=tag)
            # ---- FIB touch (utama & lokal) ----
            for typ, fget, lab in (("HTF_FIB", htf, config.FIB_PRIMARY_TF), ("LOCAL_FIB", loc, tf)):
                f = fget(j - 1)
                if not f or not f["valid"]:
                    continue
                key = (typ, f["id"])
                hit = None
                for r in FIB_TOUCH[typ]:
                    lv = fibo.price_at(f["dir"], f["hi"], f["lo"], r)
                    if (f["dir"] == "bull" and b["l"] <= lv) or (f["dir"] == "bear" and b["h"] >= lv):
                        hit = (r, lv)
                if hit and hit[0] > depth.get(key, 0):
                    r, lv = hit
                    depth[key] = r
                    bull = f["dir"] == "bull"
                    zone = " – zona utama 50–61.8" if r in fibo.GOLD else ""
                    add(j, typ, f["dir"], f"{lab} Fib {_pct(r)} tersentuh{zone}",
                        [f"Fib {lab} {'bullish' if bull else 'bearish'} ({f['scope']}): "
                         f"100% {_r(f['lo'] if bull else f['hi'], d)} → 0% {_r(f['hi'] if bull else f['lo'], d)}",
                         f"Level {_pct(r)} = {_r(lv, d)} · {'low' if bull else 'high'} candle {_r(b['l'] if bull else b['h'], d)}",
                         "Retracement " + ("turun dari swing high" if bull else "naik dari swing low")],
                        rid=f"MN:{typ}:{f['id']}:{r}", fib_id=f["id"], ratio=r, level=_r(lv, d))
            # ---- FIB utama invalid ----
            fp, fc = htf(j - 1), htf(j)
            if fp and fc and fp["leg_id"] == fc["leg_id"] and fp["valid"] and not fc["valid"]:
                bull = fc["dir"] == "bull"
                add(j, "FIB_INVALID", fc["dir"], f"Fib {config.FIB_PRIMARY_TF} invalid",
                    [f"Close {'di bawah' if bull else 'di atas'} 100% "
                     f"({_r(fc['lo'] if bull else fc['hi'], d)}) – impuls batal, tunggu leg baru"],
                    rid=f"MN:FIBINV:{fc['leg_id']}")
            # ---- POI retest: overlap & confluence ----
            for kind, z in pois:
                if not alive(z, j) or z["created"] >= j - 1:
                    continue
                if not _touch(b, z["bottom"], z["top"]) or _touch(bp, z["bottom"], z["top"]):
                    continue
                dirn, bull = z["dir"], z["dir"] == "bull"
                tag = "FVG" if kind == "FVG" else ("sOB" if z.get("scope") == "swing" else "iOB")
                partner = None
                for k2, z2 in pois:
                    if k2 == kind or z2["dir"] != dirn or not alive(z2, j):
                        continue
                    if _ovl(z["bottom"], z["top"], z2["bottom"], z2["top"]):
                        partner = (k2, z2)
                        break
                if partner and kind == "OB":
                    z2 = partner[1]
                    bot, top = max(z["bottom"], z2["bottom"]), min(z["top"], z2["top"])
                    add(j, "OB_FVG_OVERLAP", dirn, f"Retest {'bullish' if bull else 'bearish'} OB + FVG",
                        [f"{tag} {_r(z['bottom'], d)}–{_r(z['top'], d)} ∩ FVG {_r(z2['bottom'], d)}–{_r(z2['top'], d)}",
                         f"Area overlap {_r(bot, d)} – {_r(top, d)}"],
                        rid=f"MN:OVL:{z['id']}:{z2['id']}", top=_r(top, d), bottom=_r(bot, d))
                # confluence: wajib searah Fib utama & POI di area H4 38.2–78.6
                h = htf(j - 1)
                if not h or not h["valid"] or h["dir"] != dirn:
                    continue
                g50, g618 = (fibo.price_at(dirn, h["hi"], h["lo"], r) for r in (0.5, 0.618))
                w382, w786 = (fibo.price_at(dirn, h["hi"], h["lo"], r) for r in (0.382, 0.786))
                pts, why = 0, []
                if _ovl(z["bottom"], z["top"], min(g50, g618), max(g50, g618)):
                    pts += 2
                    why.append([True, f"POI di zona {config.FIB_PRIMARY_TF} Fib 50–61.8 ({_r(min(g50, g618), d)}–{_r(max(g50, g618), d)})", 2])
                elif _ovl(z["bottom"], z["top"], min(w382, w786), max(w382, w786)):
                    pts += 1
                    why.append([True, f"POI di area {config.FIB_PRIMARY_TF} Fib 38.2–78.6", 1])
                else:
                    continue
                lf = loc(j - 1)
                if lf and lf["valid"] and lf["dir"] == dirn:
                    a50, a786 = (fibo.price_at(dirn, lf["hi"], lf["lo"], r) for r in (0.5, 0.786))
                    ok = _ovl(z["bottom"], z["top"], min(a50, a786), max(a50, a786))
                    pts += ok
                    why.append([ok, f"Fib {tf} searah, POI di 50–78.6" if ok else f"Fib {tf} searah, POI di luar 50–78.6", int(ok)])
                else:
                    why.append([False, f"Fib {tf} belum searah", 0])
                pts += bool(partner)
                why.append([bool(partner), "OB + FVG overlap" if partner else "Tanpa overlap OB/FVG", int(bool(partner))])
                x = bisect_left(sw_idx, j - config.MONITOR_SWEEP_WINDOW)
                sw = next((s for i in sw_idx[x:] if i <= j for s in sw_at[i] if s["dir"] == dirn), None)
                pts += bool(sw)
                why.append([bool(sw), f"{sw['side']} sweep @ {_r(sw['level'], d)}" if sw else
                            f"Belum ada sweep {'SSL' if bull else 'BSL'} ≤ {config.MONITOR_SWEEP_WINDOW} candle", int(bool(sw))])
                y = bisect_left(ev_idx, j + 1) - 1
                last_e = ev_at[ev_idx[y]][-1] if y >= 0 else None
                ok = bool(last_e and last_e["dir"] == dirn)
                pts += ok
                why.append([ok, f"Struktur terakhir {last_e['label']} {'bullish' if last_e['dir'] == 'bull' else 'bearish'}"
                            if last_e else "Belum ada struktur", int(ok)])
                if pts < config.MONITOR_CONFLUENCE_MIN or (j, dirn) in conf_seen:
                    continue
                conf_seen.add((j, dirn))
                add(j, "CONFLUENCE", dirn, f"{'Bullish' if bull else 'Bearish'} confluence {pts}/{MAX_SCORE} – retest {tag}",
                    [f"{'✅' if ok_ else '➖'} {t}" for ok_, t, _ in why],
                    rid=f"MN:CONF:{z['id']}:{h['id']}", top=_r(z["top"], d), bottom=_r(z["bottom"], d),
                    score=pts, max_score=MAX_SCORE, kind=tag + ("+FVG" if partner and kind == "OB" else "+OB" if partner else ""))
        # ---- v20: HTF POI (FVG/OB H4) -> CHoCH M15 searah -> BOS M15 (konfirmasi) ----
        if config.HTF_POI_ENABLED:
            self._htf_poi(tfs, bars, smc, atr, sec, d, add, htf, times)
        self.new.sort(key=lambda r: (r["close_t"], r["type"]))
        return self.new

    # ------------------------------------------------------------------ v20: HTF POI
    def _htf_poi(self, tfs, bars, smc, atr, sec, d, add, htf, times):
        """Harga M15 masuk POI H4 (FVG / OB) -> CHoCH/iCHoCH M15 searah POI -> BOS/iBOS M15 searah.

        Tanpa lookahead: zona H4 baru dipakai setelah candle H4 pembentuknya CLOSE; zona INVALID saat
        candle H4 CLOSE menembus sisi jauh (bukan wick). Tahap:
          POI        candle M15 pertama yang menyentuh zona H4 aktif
          CHOCH      CHoCH/iCHoCH M15 searah zona, asal leg-nya di/dekat zona, <= HTF_POI_WINDOW candle
                     sejak sentuhan terakhir                                   (setup reaksi HTF)
          CONFIRMED  BOS/iBOS M15 searah (atau CHoCH swing) setelah CHOCH, sebelum ada break berlawanan,
                     <= HTF_POI_CONFIRM_WINDOW candle
          INVALID    candle H4 close menembus sisi jauh zona (dinotif hanya bila tahap CHOCH pernah terjadi)
        ID kejadian deterministik (dari id zona / id event struktur) -> tidak ada notif ganda antar run.
        """
        htf_tf = config.HTF_POI_TF
        H = tfs.get(htf_tf) or {}
        hbars, hsmc = H.get("_bars"), H.get("_smc")
        if not hbars or not hsmc or not H.get("enough"):
            return
        hsec, n = TF_SEC.get(htf_tf, 14400), len(bars)
        hn = len(hbars)
        kinds = set(config.HTF_POI_KINDS)
        lo_c = hn - config.HTF_POI_LOOKBACK

        # ---- semua zona H4 (dua arah) + waktu diketahui & waktu invalid ----
        raw = [("FVG", z) for z in (hsmc.get("fvg") or []) + (hsmc.get("fvg_mit") or [])]
        raw += [("sOB" if z.get("scope") == "swing" else "iOB", z)
                for z in (hsmc.get("order_blocks") or []) + (hsmc.get("ob_mitigated") or [])]
        zones = []
        for tag, z in raw:
            if z["created"] < lo_c or z["top"] - z["bottom"] <= 1e-12:
                continue
            bull = z["dir"] == "bull"
            inv_t = None
            for k in range(z["created"] + 1, hn):
                c = hbars[k]["c"]
                if (bull and c < z["bottom"]) or (not bull and c > z["top"]):
                    inv_t = hbars[k]["t"] + hsec
                    break
            zones.append({"id": z["id"], "tag": tag, "dir": z["dir"], "top": z["top"], "bottom": z["bottom"],
                          "known": hbars[z["created"]]["t"] + hsec, "inv": inv_t,
                          "poi": tag.upper() in kinds})

        def alive(z, j):
            """Zona sudah diketahui sebelum candle M15 j dibuka & belum invalid pada close j."""
            return z["known"] <= bars[j]["t"] and (z["inv"] is None or z["inv"] > bars[j]["t"] + sec)

        # struktur swing H4 yang sudah diketahui (untuk label searah / counter-trend)
        hsw = [e for e in (hsmc.get("events") or []) if e["scope"] == "swing"]
        hsw_close = [hbars[e["index"]]["t"] + hsec for e in hsw]

        def h4_trend(j):
            x = bisect_left(hsw_close, bars[j]["t"] + sec + 1) - 1
            return hsw[x]["dir"] if x >= 0 else None

        m15_fvg = (smc.get("fvg") or []) + (smc.get("fvg_mit") or [])
        m15_ob = (smc.get("order_blocks") or []) + (smc.get("ob_mitigated") or [])

        def ctx(j, dirn, leg_from=None):
            """Konteks pesan: tren H4, target (POI H4 lawan + level Fib H4), POI M15 di leg."""
            bull, px = dirn == "bull", bars[j]["c"]
            tr = h4_trend(j)
            tg = []
            for z in zones:
                if z["dir"] == dirn or not alive(z, j):
                    continue
                edge = z["bottom"] if bull else z["top"]
                if (bull and edge > px) or (not bull and edge < px):
                    tg.append((abs(edge - px), f"{'Bear' if bull else 'Bull'} {z['tag']} {htf_tf}", edge))
            f = htf(j)
            if f and f["valid"]:
                for r in (0.0, 0.382, 0.5, 0.618, 0.786, 1.0):
                    lv = fibo.price_at(f["dir"], f["hi"], f["lo"], r)
                    if (bull and lv > px) or (not bull and lv < px):
                        tg.append((abs(lv - px), f"Fib {config.FIB_PRIMARY_TF} {r * 100:g}%", lv))
            tg.sort(key=lambda x: x[0])
            targets, seen = [], set()
            for _, lab, lv in tg:
                key = round(lv, d)
                if key in seen:
                    continue
                seen.add(key)
                targets.append([lab, _r(lv, d)])
                if len(targets) >= 4:
                    break
            poi = None
            if leg_from is not None:
                cand = [("FVG", z) for z in m15_fvg] + [("OB", z) for z in m15_ob]
                cand = [(k, z) for k, z in cand if z["dir"] == dirn and leg_from <= z["created"] <= j
                        and (z.get("end_i") is None or z["end_i"] > j)]
                if cand:
                    k, z = max(cand, key=lambda c: (c[1]["created"], c[0] == "FVG"))
                    poi = {"kind": k if k == "FVG" else ("sOB" if z.get("scope") == "swing" else "iOB"),
                           "top": _r(z["top"], d), "bottom": _r(z["bottom"], d)}
            return {"h4_trend": tr, "counter": tr is not None and tr != dirn, "targets": targets, "m15_poi": poi}

        def area(zs):
            return min(z["bottom"] for z in zs), max(z["top"] for z in zs), "+".join(sorted({z["tag"] for z in zs}))

        poi_hits, choch_hits, conf_hits, inv_hits = {}, {}, {}, []
        ev_at = {}
        for e in smc.get("events") or []:
            ev_at.setdefault(e["index"], []).append(e)

        for z in zones:
            if not z["poi"]:
                continue
            bull = z["dir"] == "bull"
            j0 = max(1, bisect_left(times, z["known"]))
            tap = last_touch = None
            act = None                       # (j_choch, event) yang menunggu konfirmasi
            had_choch = False
            for j in range(j0, n):
                b = bars[j]
                if z["inv"] is not None and b["t"] + sec >= z["inv"]:
                    inv_hits.append((j, z, had_choch))
                    break
                if b["l"] <= z["top"] and b["h"] >= z["bottom"]:
                    if tap is None:
                        tap = j
                        poi_hits.setdefault((j, z["dir"]), []).append(z)
                    last_touch = j
                for e in ev_at.get(j, []):
                    same = e["dir"] == z["dir"]
                    if act and j > act[0]:
                        if not same:
                            act = None                               # struktur berbalik -> setup gugur
                        elif j - act[0] <= config.HTF_POI_CONFIRM_WINDOW and (
                                e["type"] == "BOS" or (e["scope"] == "swing" and act[1]["scope"] == "internal")):
                            conf_hits.setdefault(e["id"], {"j": j, "e": e, "ch": act[1], "zones": []})["zones"].append(z)
                            act = None
                            continue
                    if not same or e["type"] != "CHoCH" or last_touch is None:
                        continue
                    if j - last_touch > config.HTF_POI_WINDOW:
                        continue
                    p = e.get("pivot_index")
                    if p is None or p >= j:
                        continue
                    prox = config.HTF_POI_PROX_ATR * (atr[j] if j < len(atr) and atr[j] else b["h"] - b["l"])
                    if bull:
                        ext = min(bars[x]["l"] for x in range(p, j + 1))
                        near = ext <= z["top"] + prox
                    else:
                        ext = max(bars[x]["h"] for x in range(p, j + 1))
                        near = ext >= z["bottom"] - prox
                    if not near:
                        continue
                    choch_hits.setdefault(e["id"], {"j": j, "e": e, "ext": ext, "zones": []})["zones"].append(z)
                    act, had_choch = (j, e), True

        name = lambda dr: "Bull" if dr == "bull" else "Bear"        # noqa: E731
        # ---- POI ----
        last_poi = {}
        for (j, dirn) in sorted(poi_hits):
            zs = poi_hits[(j, dirn)]
            bot, top, tags = area(zs)
            c = ctx(j, dirn)
            quiet = dirn in last_poi and j - last_poi[dirn] <= config.HTF_POI_COOLDOWN
            last_poi[dirn] = j
            why = [f"Harga M15 menyentuh {name(dirn)} {tags} {htf_tf} {_r(bot, d)} – {_r(top, d)}",
                   f"Struktur swing {htf_tf}: " + ({"bull": "bullish", "bear": "bearish"}.get(c["h4_trend"], "belum ada")
                                                    + (" → reaksi counter-trend" if c["counter"] else "")),
                   f"Menunggu CHoCH/iCHoCH {config.MONITOR_TF} {'bullish' if dirn == 'bull' else 'bearish'} dari area ini"]
            add(j, "HTF_POI", dirn, f"{htf_tf} POI · harga masuk {name(dirn)} {tags} {htf_tf}", why,
                rid=f"MN:HTFPOI:POI:{dirn}:{bars[j]['t']}", stage="POI", top=_r(top, d), bottom=_r(bot, d),
                kind=f"{tags} {htf_tf}", invalid_level=_r(bot if dirn == "bull" else top, d), quiet=quiet, **c)
        # ---- CHOCH ----
        for eid, h in sorted(choch_hits.items(), key=lambda x: x[1]["j"]):
            j, e, zs = h["j"], h["e"], h["zones"]
            dirn = e["dir"]
            bot, top, tags = area(zs)
            c = ctx(j, dirn, e.get("pivot_index"))
            why = [f"{name(dirn)} {tags} {htf_tf} aktif {_r(bot, d)} – {_r(top, d)}",
                   f"{e['label']} {'bullish' if dirn == 'bull' else 'bearish'} {config.MONITOR_TF} menembus {_r(e['level'], d)}"
                   + (" (displacement)" if e.get("displacement") else ""),
                   f"Asal leg {_r(h['ext'], d)} di/dekat POI {htf_tf}"]
            if c["m15_poi"]:
                mp = c["m15_poi"]
                why.append(f"{name(dirn)} {mp['kind']} {config.MONITOR_TF} di leg CHoCH {mp['bottom']} – {mp['top']} (area retest)")
            add(j, "HTF_POI", dirn, f"{htf_tf} POI + {e['label']} {config.MONITOR_TF} {'bullish' if dirn == 'bull' else 'bearish'}",
                why, rid=f"MN:HTFPOI:CHOCH:{eid}", stage="CHOCH", top=_r(top, d), bottom=_r(bot, d),
                kind=f"{tags} {htf_tf}", level=_r(e["level"], d), label=e["label"],
                leg_origin=_r(h["ext"], d), invalid_level=_r(bot if dirn == "bull" else top, d), **c)
        # ---- CONFIRMED ----
        for eid, h in sorted(conf_hits.items(), key=lambda x: x[1]["j"]):
            j, e, ch, zs = h["j"], h["e"], h["ch"], h["zones"]
            dirn = e["dir"]
            bot, top, tags = area(zs)
            c = ctx(j, dirn, ch.get("pivot_index"))
            why = [f"{name(dirn)} {tags} {htf_tf} {_r(bot, d)} – {_r(top, d)} masih valid",
                   f"{ch['label']} {config.MONITOR_TF} @ {_r(ch['level'], d)} → lanjut {e['label']} @ {_r(e['level'], d)}"]
            add(j, "HTF_POI", dirn, f"Struktur {config.MONITOR_TF} terkonfirmasi ({e['label']}) dari {htf_tf} POI",
                why, rid=f"MN:HTFPOI:CONF:{eid}", stage="CONFIRMED", top=_r(top, d), bottom=_r(bot, d),
                kind=f"{tags} {htf_tf}", level=_r(e["level"], d), label=e["label"],
                invalid_level=_r(bot if dirn == "bull" else top, d), **c)
        # ---- INVALID ----
        for j, z, had in inv_hits:
            bull = z["dir"] == "bull"
            add(j, "HTF_POI", z["dir"], f"{name(z['dir'])} {z['tag']} {htf_tf} invalid",
                [f"Candle {htf_tf} close {'di bawah' if bull else 'di atas'} {_r(z['bottom'] if bull else z['top'], d)} – POI batal"],
                rid=f"MN:HTFPOI:INV:{z['id']}", stage="INVALID", top=_r(z["top"], d), bottom=_r(z["bottom"], d),
                kind=f"{z['tag']} {htf_tf}", quiet=not had)

    # ------------------------------------------------------------------ status notif
    def set_notif(self, rid, status):
        if rid in self.events:
            self.events[rid]["notif"] = status

    def save(self):
        rows = sorted(self.events.values(), key=lambda r: r["close_t"])[-config.MONITOR_LIMIT:]
        self.events = {r["id"]: r for r in rows}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"events": self.events}, fh, separators=(",", ":"))

    def public(self, limit=1500):
        rows = sorted(self.events.values(), key=lambda r: (r["close_t"], r["type"]))[-limit:]
        return {"symbol": self.sym, "tf": config.MONITOR_TF, "events": rows}

    def summary(self, k=8):
        rows = sorted(self.events.values(), key=lambda r: (r["close_t"], r["type"]))
        cnt = {}
        for r in rows:
            cnt[r["type"]] = cnt.get(r["type"], 0) + 1
        return {"tf": config.MONITOR_TF, "total": len(rows), "by_type": cnt,
                "last": [{k2: r.get(k2) for k2 in ("type", "dir", "title", "close_t", "price", "score", "notif")}
                         for r in rows[-k:]]}


def message(sym, rec, digits, price_now):
    """Teks Telegram (HTML) satu kejadian monitoring."""
    from html import escape
    from messages import wib, f
    bull = rec["dir"] == "bull"
    if rec["type"] == "HTF_POI":
        return _message_htf(sym, rec, digits, price_now, escape, wib, f)
    arrow = "🟢" if bull else "🔴"
    lines = [f"{ICON.get(rec['type'], '•')} <b>MONITOR · {escape(NAME.get(rec['type'], rec['type']))}</b>",
             f"<b>{sym} • {rec['tf']}</b>  {arrow} {escape(rec['title'])}", ""]
    for w in rec.get("why") or []:
        lines.append(escape(w) if w[:1] in ("✅", "➖") else "• " + escape(w))
    if rec.get("bottom") is not None:
        lines.append(f"Area: {f(rec['bottom'], digits)} – {f(rec['top'], digits)}")
    h = rec.get("htf")
    if h and rec["type"] != "HTF_FIB":
        lines.append(f"Fib {h['tf']}: {'bullish' if h['dir'] == 'bull' else 'bearish'} "
                     f"{f(h['lo'], digits)}–{f(h['hi'], digits)} · retrace {h['retrace'] if h['retrace'] is not None else '-'}%"
                     + ("" if h["valid"] else " (invalid)"))
    lines += ["", f"Close candle: {f(rec['price'], digits)} · sekarang {f(price_now, digits)}",
              f"Candle: {wib(rec['t'], '%d/%m %H:%M')}",
              "", "📋 Kejadian dicatat untuk cek manual – <b>bukan sinyal entry</b>."]
    return "\n".join(lines)


def _message_htf(sym, rec, digits, price_now, escape, wib, f):
    """v20: pesan bertahap HTF POI (POI -> CHOCH -> CONFIRMED / INVALID)."""
    bull, st = rec["dir"] == "bull", rec.get("stage")
    side = "BULLISH" if bull else "BEARISH"
    head = {"POI": f"🟡 <b>HTF POI ALERT</b>",
            "CHOCH": f"🟠 <b>HTF POI + CHoCH – SETUP {side}</b>",
            "CONFIRMED": f"🟢 <b>STRUCTURE CONFIRMED – {side}</b>",
            "INVALID": f"❌ <b>HTF POI INVALID</b>"}.get(st, "🎯 <b>HTF POI</b>")
    lines = [head, f"<b>{sym} • {config.HTF_POI_TF} → {rec['tf']}</b>  {escape(rec['title'])}", ""]
    for w in rec.get("why") or []:
        lines.append("• " + escape(w))
    tr = rec.get("h4_trend")
    if st != "INVALID":
        lines.append("")
        lines.append(f"Swing {config.HTF_POI_TF}: " + ("🟢 bullish" if tr == "bull" else "🔴 bearish" if tr == "bear" else "⚪ belum ada")
                     + (" ⚠️ counter-trend (reaksi, bukan reversal HTF)" if rec.get("counter") else " ✅ searah"))
        h = rec.get("htf")
        if h:
            lines.append(f"Fib {h['tf']}: {'bullish' if h['dir'] == 'bull' else 'bearish'} {f(h['lo'], digits)}–{f(h['hi'], digits)}"
                         f" · retrace {h['retrace'] if h['retrace'] is not None else '-'}%" + ("" if h["valid"] else " (invalid)"))
        tg = rec.get("targets") or []
        if tg:
            lines.append("<b>" + ("Resistance / target" if bull else "Support / target") + "</b>")
            for i, (lab, lv) in enumerate(tg, 1):
                lines.append(f"{i}. {escape(lab)} → {f(lv, digits)}")
        if rec.get("invalid_level") is not None:
            lines.append(f"🛑 Invalid bila candle {config.HTF_POI_TF} close {'di bawah' if bull else 'di atas'} {f(rec['invalid_level'], digits)}")
        if st == "CHOCH" and rec.get("leg_origin") is not None:
            lines.append(f"Struktur {rec['tf']} gagal bila close {'di bawah' if bull else 'di atas'} asal leg {f(rec['leg_origin'], digits)}")
    nxt = {"POI": f"Tunggu CHoCH/iCHoCH {rec['tf']} {'bullish' if bull else 'bearish'} dari area ini.",
           "CHOCH": f"Tunggu retest POI {rec['tf']} lalu BOS/iBOS {'bullish' if bull else 'bearish'} lanjutan.",
           "CONFIRMED": "Struktur lanjutan terbentuk – cek manual area entry & target.",
           "INVALID": "Zona batal – abaikan setup dari area ini."}.get(st, "")
    lines += ["", f"Close candle: {f(rec['price'], digits)} · sekarang {f(price_now, digits)}",
              f"Candle: {wib(rec['t'], '%d/%m %H:%M')}", "", f"📋 {nxt} <b>Bukan sinyal entry.</b>"]
    return "\n".join(lines)
