"""Monitoring pasar v23; notif otomatis hanya pembentukan FVG/OB valid.

Dipindai pada MONITOR_TF (default M15), hanya candle tertutup, tanpa lookahead:
  STRUCTURE       BOS / CHoCH swing (iBOS/iCHoCH bila MONITOR_INTERNAL_STRUCTURE)
  SWEEP           sweep liquidity swing / EQH / EQL
  FVG_NEW         FVG baru terbentuk                         (default: hanya dicatat)
  OB_NEW          Order Block baru terbentuk                  (default: hanya dicatat)
  OB_FVG_OVERLAP  harga retest area overlap OB + FVG searah
  CONFLUENCE      retest POI M15: OB+FVG overlap +2 · POI kontekstual +1 · lokasi PD +1
                    sweep searah <= MONITOR_SWEEP_WINDOW candle +1 · struktur searah terbaru +1 (maks 6)
Kejadian disimpan permanen di store/monitor/{SYM}.json (tidak pernah diubah kecuali status notif).
"""
import json
import os
from bisect import bisect_left

import config
from resample import TF_SEC

ICON = {"STRUCTURE": "🔄", "SWEEP": "💧", "FVG_NEW": "▫️", "OB_NEW": "▪️",
        "OB_FVG_OVERLAP": "🧱", "CONFLUENCE": "⭐", "HTF_POI": "🎯"}
NAME = {"STRUCTURE": "Struktur", "SWEEP": "Sweep liquidity", "FVG_NEW": "FVG baru", "OB_NEW": "Order Block baru",
        "OB_FVG_OVERLAP": "OB + FVG overlap", "CONFLUENCE": "Confluence", "HTF_POI": "HTF POI"}
MAX_SCORE = 6


def _r(v, d):
    return None if v is None else round(v, d)


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

        ev_at, sw_at = {}, {}
        for e in smc.get("events") or []:
            ev_at.setdefault(e["index"], []).append(e)
        for s in smc.get("sweeps") or []:
            sw_at.setdefault(s["index"], []).append(s)
        seq_at = {}
        for s in smc.get("sequences") or []:
            seq_at.setdefault(s["event"]["index"], []).append(s)
        lo_c = start - 150
        obs = [z for z in (smc.get("order_blocks") or []) + (smc.get("ob_mitigated") or []) if z["created"] >= lo_c]
        fvgs = [z for z in (smc.get("fvg") or []) + (smc.get("fvg_mit") or []) if z["created"] >= lo_c]
        pois = [("OB", z) for z in obs] + [("FVG", z) for z in fvgs]
        ev_idx = sorted(ev_at)
        sw_idx = sorted(sw_at)

        def alive(z, j):
            return z["created"] < j and (z.get("end_i") is None or z["end_i"] >= j)

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
            # ---- POI valid baru ----
            # Validasi dikirim pada close BOS/MSS sequence (bukan saat gap/candle asal
            # belum punya konteks). FVG wajib mempunyai OB pada sequence yang sama.
            for seq in seq_at.get(j, []):
                ob, fvg = seq.get("ob"), seq.get("fvg")
                if ob:
                    tag = "sOB" if ob.get("scope") == "swing" else "iOB"
                    add(j, "OB_NEW", ob["dir"], f"{'Bullish' if ob['dir'] == 'bull' else 'Bearish'} {tag} valid",
                        [f"Area {_r(ob['bottom'], d)} – {_r(ob['top'], d)}",
                         f"Tervalidasi {seq['type']} close + sweep + displacement"],
                        rid=f"MN:VALID:OB:{seq['id']}:{ob['id']}", top=_r(ob["top"], d),
                        bottom=_r(ob["bottom"], d), kind=tag, sequence_id=seq["id"])
                if fvg and ob:
                    add(j, "FVG_NEW", fvg["dir"], f"{'Bullish' if fvg['dir'] == 'bull' else 'Bearish'} FVG valid",
                        [f"Area {_r(fvg['bottom'], d)} – {_r(fvg['top'], d)}",
                         f"Tidak berdiri sendiri: satu sequence dengan {('sOB' if ob.get('scope') == 'swing' else 'iOB')}",
                         f"Tervalidasi {seq['type']} close + sweep + displacement"],
                        rid=f"MN:VALID:FVG:{seq['id']}:{fvg['id']}", top=_r(fvg["top"], d),
                        bottom=_r(fvg["bottom"], d), kind="FVG+OB", sequence_id=seq["id"])
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
                # confluence struktural berbasis POI canonical
                pts, why = 0, []
                overlap_pts = 2 if partner else 0
                pts += overlap_pts
                why.append([bool(partner), "OB + FVG overlap" if partner else "Tanpa overlap OB/FVG", overlap_pts])
                contextual = bool(z.get("context"))
                pts += contextual
                why.append([contextual, "POI berasal dari sequence sweep → displacement → break" if contextual
                            else "POI tidak terikat sequence canonical", int(contextual)])
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
                disp = bool(last_e and last_e["dir"] == dirn and
                            (last_e.get("displacement") or last_e.get("leg_displacement")))
                pts += disp
                why.append([disp, "Break searah memiliki displacement" if disp else
                            "Break searah belum memiliki displacement", int(disp)])
                if pts < config.MONITOR_CONFLUENCE_MIN or (j, dirn) in conf_seen:
                    continue
                conf_seen.add((j, dirn))
                add(j, "CONFLUENCE", dirn, f"{'Bullish' if bull else 'Bearish'} confluence {pts}/{MAX_SCORE} – retest {tag}",
                    [f"{'✅' if ok_ else '➖'} {t}" for ok_, t, _ in why],
                    rid=f"MN:CONF:{z['id']}:{bars[j]['t']}", top=_r(z["top"], d), bottom=_r(z["bottom"], d),
                    score=pts, max_score=MAX_SCORE, kind=tag + ("+FVG" if partner and kind == "OB" else "+OB" if partner else ""))
        # ---- v20: HTF POI (FVG/OB H4) -> CHoCH M15 searah -> BOS M15 (konfirmasi) ----
        if config.HTF_POI_ENABLED:
            self._htf_poi(tfs, bars, smc, atr, sec, d, add, times)
        self.new.sort(key=lambda r: (r["close_t"], r["type"]))
        return self.new

    # ------------------------------------------------------------------ v20: HTF POI
    def _htf_poi(self, tfs, bars, smc, atr, sec, d, add, times):
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
            """Konteks pesan: tren H4, target POI H4 lawan, dan POI M15 di leg."""
            bull, px = dirn == "bull", bars[j]["c"]
            tr = h4_trend(j)
            tg = []
            for z in zones:
                if z["dir"] == dirn or not alive(z, j):
                    continue
                edge = z["bottom"] if bull else z["top"]
                if (bull and edge > px) or (not bull and edge < px):
                    tg.append((abs(edge - px), f"{'Bear' if bull else 'Bull'} {z['tag']} {htf_tf}", edge))
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
