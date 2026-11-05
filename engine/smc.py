"""Liquidity-first SMC/ICT model (v3):
Liquidity -> Sweep (valid) -> Displacement -> CISD/MSS -> contextual FVG/OB -> status zona.

Perbaikan v3:
- Sweep hanya valid bila level belum pernah ditembus close sebelumnya, wick menembus lalu close
  kembali, dan 2 candle berikutnya tidak close melewati level (bukan breakout yang berlanjut).
- FVG dicari di seluruh kaki displacement (pola 3 candle di sekitar candle displacement).
- Order Block = candle berlawanan terakhir sebelum kaki displacement (termasuk candle sebelum sweep);
  bila tidak ada, candle sweep itu sendiri dipakai sebagai OB.
- Status zona: fresh -> tapped (wick masuk) -> mitigated (FVG: harga mencapai 50%/CE,
  OB: close melewati 50%) -> invalid (close menembus sisi jauh zona).
"""

SWEEP_CONFIRM_BARS = 2      # candle setelah sweep yang tidak boleh close melewati level
MSS_WINDOW = 10             # maksimal jarak candle sweep -> displacement
OB_LOOKBACK = 5             # cari OB sampai 5 candle sebelum sweep


def swings(bars, k=3):
    hs, ls = [], []
    for i in range(k, len(bars) - k):
        if bars[i]["h"] == max(x["h"] for x in bars[i-k:i+k+1]): hs.append(i)
        if bars[i]["l"] == min(x["l"] for x in bars[i-k:i+k+1]): ls.append(i)
    return hs, ls


def liquidity_pools(bars, k=3, tolerance=0.0):
    hs, ls = swings(bars, k); out = []
    for side, ids, key in (("BSL", hs, "h"), ("SSL", ls, "l")):
        for n, i in enumerate(ids):
            level = bars[i][key]; touches = [i]
            for j in ids[n+1:]:
                if abs(bars[j][key] - level) <= tolerance:
                    touches.append(j)
            out.append({"id": f"{side}:{bars[i]['t']}", "side": side, "level": level,
                        "t": bars[i]["t"], "touches": len(touches), "index": i})
    return sorted(out, key=lambda x: x["index"])


def _displacement(bars, i, direction, atr_value, body_mult=0.8):
    b = bars[i]; body = abs(b["c"] - b["o"]); rng = max(b["h"] - b["l"], 1e-12)
    directional = b["c"] > b["o"] if direction == "bull" else b["c"] < b["o"]
    return directional and body / rng >= 0.60 and body >= body_mult * max(atr_value, 1e-12)


def _find_sweep(bars, p, k):
    """Sweep pertama yang valid untuk pool p, atau None. Berhenti bila level sudah ditembus close."""
    level, side = p["level"], p["side"]
    for i in range(p["index"] + k, len(bars)):
        b = bars[i]
        if side == "SSL":
            if b["c"] < level: return None                      # breakout nyata, pool habis
            if not (b["l"] < level): continue
            direction = "bull"
        else:
            if b["c"] > level: return None
            if not (b["h"] > level): continue
            direction = "bear"
        nxt = bars[i+1:i+1+SWEEP_CONFIRM_BARS]
        failed = any((x["c"] < level) if direction == "bull" else (x["c"] > level) for x in nxt)
        if failed: return None                                   # wick lalu lanjut tembus = bukan sweep
        return {"id": f"SW:{p['id']}:{b['t']}", "t": b["t"], "from_t": p["t"], "dir": direction,
                "side": side, "level": level, "price": b["l"] if direction == "bull" else b["h"],
                "index": i, "confirmed": len(nxt) >= SWEEP_CONFIRM_BARS}
    return None


def _fvg_in_leg(bars, i, j, direction):
    """FVG 3-candle yang terbentuk di kaki displacement (dari sweep i sampai candle setelah j)."""
    best = None
    for m in range(max(i + 1, 1), min(len(bars) - 1, j + 2)):   # m = candle tengah
        a, c = bars[m-1], bars[m+1]
        if direction == "bull" and c["l"] > a["h"]:
            z = {"dir": "bull", "t": bars[m]["t"], "top": c["l"], "bottom": a["h"], "created": m + 1}
        elif direction == "bear" and c["h"] < a["l"]:
            z = {"dir": "bear", "t": bars[m]["t"], "top": a["l"], "bottom": c["h"], "created": m + 1}
        else:
            continue
        if best is None or (z["top"] - z["bottom"]) > (best["top"] - best["bottom"]):
            best = z                                             # ambil gap terbesar di kaki impuls
    return best


def _order_block(bars, i, j, direction):
    for q in range(j - 1, max(-1, i - OB_LOOKBACK - 1), -1):
        b = bars[q]
        opposing = b["c"] < b["o"] if direction == "bull" else b["c"] > b["o"]
        if opposing:
            return q
    return i                                                     # fallback: candle sweep


def _state(bars, z, kind):
    """Tentukan status zona setelah dibuat: fresh / tapped / mitigated / invalid."""
    mid = (z["top"] + z["bottom"]) / 2
    status, touch, end = "fresh", None, None
    for j in range(z["created"] + 1, len(bars)):
        b = bars[j]
        if z["dir"] == "bull":
            if b["c"] < z["bottom"]: status, end = "invalid", j; break
            if b["l"] <= z["top"] and touch is None: touch, status = j, "tapped"
            reached = b["l"] <= mid if kind == "FVG" else b["c"] < mid
        else:
            if b["c"] > z["top"]: status, end = "invalid", j; break
            if b["h"] >= z["bottom"] and touch is None: touch, status = j, "tapped"
            reached = b["h"] >= mid if kind == "FVG" else b["c"] > mid
        if reached:
            status, end = "mitigated", j; break
    x = dict(z, mid=mid, status=status, touch_t=bars[touch]["t"] if touch is not None else None)
    if end is not None: x["end_t"] = bars[end]["t"]
    return x


def model(bars, atr_value, k=3, eq_tolerance=0.0):
    pools = liquidity_pools(bars, k, eq_tolerance)
    sweeps, events, fvgs, obs, sequences = [], [], [], [], []
    trend = None
    for p in pools:
        sw = _find_sweep(bars, p, k)
        if not sw: continue
        sweeps.append(sw)
        i, direction = sw["index"], sw["dir"]
        for j in range(i, min(len(bars), i + MSS_WINDOW)):
            if not _displacement(bars, j, direction, atr_value): continue
            prior = bars[max(i - 8, 0):j]
            if not prior: continue
            break_level = max(x["h"] for x in prior) if direction == "bull" else min(x["l"] for x in prior)
            broken = bars[j]["c"] > break_level if direction == "bull" else bars[j]["c"] < break_level
            if not broken: continue
            ev = {"t": bars[j]["t"], "from_t": sw["t"], "type": "CISD/MSS", "dir": direction,
                  "level": break_level, "sweep_id": sw["id"], "displacement": True, "index": j}
            events.append(ev); trend = direction
            meta = {"context": True, "sweep_id": sw["id"], "event_t": ev["t"]}
            z = _fvg_in_leg(bars, i, j, direction)
            if z:
                z.update(meta, kind="FVG"); z = _state(bars, z, "FVG"); fvgs.append(z)
            q = _order_block(bars, i, j, direction)
            ob = {"dir": direction, "t": bars[q]["t"], "top": bars[q]["h"], "bottom": bars[q]["l"],
                  "created": j, "kind": "OB", **meta}
            ob = _state(bars, ob, "OB"); obs.append(ob)
            sequences.append({"id": sw["id"], "dir": direction, "sweep": sw, "event": ev, "fvg": z, "ob": ob})
            break
    # Beberapa sweep bisa memicu MSS yang sama -> simpan satu saja (sweep terakhir sebelum MSS).
    seen = {}
    for sq in sorted(sequences, key=lambda s: s["sweep"]["index"]):
        seen[(sq["event"]["t"], sq["dir"])] = sq
    sequences = sorted(seen.values(), key=lambda s: s["event"]["index"])
    events = [sq["event"] for sq in sequences]
    fvgs = [sq["fvg"] for sq in sequences if sq["fvg"]]
    obs = [sq["ob"] for sq in sequences]
    sweeps.sort(key=lambda s: s["index"])
    active = lambda z: z["status"] in ("fresh", "tapped")
    af = [z for z in fvgs if active(z)]; mf = [z for z in fvgs if not active(z)]
    ao = [z for z in obs if active(z)]; mo = [z for z in obs if not active(z)]
    used = {s["id"] for s in sequences}
    shown_sweeps = [s for s in sweeps if s["id"] in used] or sweeps
    return {"trend": trend, "liquidity": pools[-20:], "sweeps": shown_sweeps[-12:], "all_sweeps": sweeps[-20:],
            "events": events[-20:], "fvg": af[-6:], "fvg_mit": mf[-8:], "order_blocks": ao[-6:],
            "ob_mitigated": mo[-8:], "sequences": sequences[-10:]}
