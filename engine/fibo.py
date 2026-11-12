"""Auto Fibonacci v2 – deterministic lifecycle, closed candles only, no lookahead.

Lifecycle per timeframe:
  FORMING -> LOCKED -> EXTENDED -> REPLACED / INVALID

Rules:
* Only swing BOS/CHoCH legs can seed the primary Fib. Internal Fib never replaces it.
* FORMING endpoint may follow new extremes.
* LOCKED requires: endpoint pivot confirmed after ``k`` closed candles, at least 23.6%
  wick retracement, and no close through the 100% origin.
* 1.0 breakout and 100% invalidation use CLOSE, never wick.
* A same-direction Fib replaces the old one only after a complete HL->HH / LH->LL:
  close through old 1.0, new endpoint pivot confirmation, then >=23.6% pullback
  of the new leg. The last replaced/invalid Fib remains available as ``previous``.
"""
from bisect import bisect_right
from copy import deepcopy

import config

LEVELS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
EXTENSIONS = (1.236, 1.382, 1.618, 2.236, 2.618)
GOLD = (0.5, 0.618)
_EPS = 1e-12


def price_at(direction, hi, lo, r):
    """Retracement notation: 0 at impulse end, 1 at impulse origin."""
    return hi - r * (hi - lo) if direction == "bull" else lo + r * (hi - lo)


def extension_price(direction, hi, lo, x):
    """Extension notation: 1.0 at impulse end, values >1 beyond it."""
    return lo + x * (hi - lo) if direction == "bull" else hi - x * (hi - lo)


def retrace_of(direction, hi, lo, price):
    rng = hi - lo
    if rng <= _EPS:
        return None
    return (hi - price) / rng if direction == "bull" else (price - lo) / rng


def _wick_retrace(state, bar):
    p = bar["l"] if state["dir"] == "bull" else bar["h"]
    return retrace_of(state["dir"], state["hi"], state["lo"], p)


def _origin_price(state):
    return state["lo"] if state["dir"] == "bull" else state["hi"]


def _end_price(state):
    return state["hi"] if state["dir"] == "bull" else state["lo"]


def _invalid_close(state, bar):
    return bar["c"] < state["lo"] if state["dir"] == "bull" else bar["c"] > state["hi"]


def _break_1_close(state, bar):
    return bar["c"] > state["hi"] if state["dir"] == "bull" else bar["c"] < state["lo"]


def legs(bars, events, atr14, scope="swing"):
    """Seed legs from confirmed structural breaks. v2 intentionally ignores internal fallback."""
    out = []
    for e in events:
        if e.get("scope") != scope:
            continue
        p, i = e.get("pivot_index"), e.get("index")
        if p is None or i is None or not (0 <= p < i < len(bars)):
            continue
        bull = e.get("dir") == "bull"
        seg = range(p, i + 1)
        origin = min(seg, key=lambda j: (bars[j]["l"], j)) if bull else max(seg, key=lambda j: (bars[j]["h"], -j))
        ext = max(range(origin, i + 1), key=lambda j: (bars[j]["h"], -j)) if bull else min(
            range(origin, i + 1), key=lambda j: (bars[j]["l"], j))
        lo = bars[origin]["l"] if bull else bars[ext]["l"]
        hi = bars[ext]["h"] if bull else bars[origin]["h"]
        av = atr14[i] if i < len(atr14) and atr14[i] else bars[i]["h"] - bars[i]["l"]
        if hi - lo < config.FIB_MIN_ATR * max(av, _EPS):
            continue
        out.append({
            "seed_id": f"FIB2:{e['dir']}:{bars[origin]['t']}:{e['t']}", "dir": e["dir"],
            "i": i, "origin_i": origin, "ext_i": ext, "lo": lo, "hi": hi,
            "event": {"t": e["t"], "label": e["label"], "type": e["type"], "level": e["level"]},
        })
    return sorted(out, key=lambda x: (x["i"], x["origin_i"]))


def _new_state(seed, bars):
    return {
        "id": seed["seed_id"], "leg_id": seed["seed_id"], "dir": seed["dir"], "scope": "swing",
        "status": "FORMING", "origin_i": seed["origin_i"], "ext_i": seed["ext_i"],
        "lo": seed["lo"], "hi": seed["hi"], "start_i": seed["i"], "start_t": seed["event"]["t"],
        "event": deepcopy(seed["event"]), "lock_i": None, "extended_i": None, "invalid_i": None,
        "max_retrace": 0.0, "pull_i": None, "break_i": None, "candidate": None,
        "replaced_from": None, "events": [], "previous": None,
    }


def _event(state, typ, j, bars, description, **extra):
    ev = {"id": f"{typ}:{state['id']}:{bars[j]['t']}", "type": typ, "i": j, "t": bars[j]["t"],
          "tf_status": state["status"], "dir": state["dir"], "fib_id": state["id"],
          "description": description}
    ev.update(extra)
    state["events"].append(ev)


def _extend_forming(state, bar, j):
    if state["dir"] == "bull" and bar["h"] > state["hi"]:
        state["hi"], state["ext_i"], state["max_retrace"] = bar["h"], j, 0.0
    elif state["dir"] == "bear" and bar["l"] < state["lo"]:
        state["lo"], state["ext_i"], state["max_retrace"] = bar["l"], j, 0.0


def _strict_continuation(old, origin_price):
    return origin_price > old["lo"] if old["dir"] == "bull" else origin_price < old["hi"]


def _start_candidate(state, bars):
    if state.get("pull_i") is None:
        return
    oi = state["pull_i"]
    op = bars[oi]["l"] if state["dir"] == "bull" else bars[oi]["h"]
    if not _strict_continuation(state, op):
        return
    j = state["break_i"]
    if state["dir"] == "bull":
        ei = max(range(oi, j + 1), key=lambda x: (bars[x]["h"], -x))
        lo, hi = op, bars[ei]["h"]
    else:
        ei = min(range(oi, j + 1), key=lambda x: (bars[x]["l"], x))
        lo, hi = bars[ei]["l"], op
    state["candidate"] = {"origin_i": oi, "ext_i": ei, "lo": lo, "hi": hi, "max_retrace": 0.0}


def _advance_candidate(state, bars, j, k_confirm, min_retrace):
    c, b = state.get("candidate"), bars[j]
    if not c:
        return None
    if state["dir"] == "bull" and b["h"] > c["hi"]:
        c.update(hi=b["h"], ext_i=j, max_retrace=0.0)
    elif state["dir"] == "bear" and b["l"] < c["lo"]:
        c.update(lo=b["l"], ext_i=j, max_retrace=0.0)
    p = b["l"] if state["dir"] == "bull" else b["h"]
    rt = retrace_of(state["dir"], c["hi"], c["lo"], p)
    c["max_retrace"] = max(c["max_retrace"], rt or 0.0)
    origin_broken = b["c"] < c["lo"] if state["dir"] == "bull" else b["c"] > c["hi"]
    if origin_broken:
        state["candidate"] = None
        return None
    if j - c["ext_i"] < k_confirm or c["max_retrace"] + _EPS < min_retrace:
        return None
    seed = {"seed_id": f"FIB2:{state['dir']}:{bars[c['origin_i']]['t']}:{bars[c['ext_i']]['t']}",
            "dir": state["dir"], "i": state["break_i"], "origin_i": c["origin_i"], "ext_i": c["ext_i"],
            "lo": c["lo"], "hi": c["hi"],
            "event": {"t": bars[state["break_i"]]["t"], "label": "Continuation", "type": "BOS",
                      "level": _end_price(state)}}
    old = _archive(state)
    old["status"] = "REPLACED"
    new = _new_state(seed, bars)
    new.update(status="LOCKED", lock_i=j, max_retrace=c["max_retrace"], previous=old,
               replaced_from=state["id"])
    _event(new, "FIB_LOCKED", j, bars, "Pivot ujung leg baru terkonfirmasi dan retrace leg baru mencapai minimal 23,6%.")
    _event(new, "FIB_REPLACED", j, bars,
           "Fib lama diganti setelah pola continuation lengkap: HL→HH / LH→LL, close melewati 1.0, pivot baru terkonfirmasi, lalu retrace minimal 23,6%.",
           previous_id=state["id"])
    return new


def _archive(state):
    if not state:
        return None
    keep = ("id", "leg_id", "dir", "scope", "status", "origin_i", "ext_i", "lo", "hi", "start_i",
            "start_t", "event", "lock_i", "extended_i", "invalid_i", "max_retrace")
    return {k: deepcopy(state.get(k)) for k in keep}


def _advance(state, bars, j, k_confirm, min_retrace):
    b = bars[j]
    state["events"] = []
    if state["status"] == "FORMING":
        _extend_forming(state, b, j)
        if _invalid_close(state, b):
            state.update(status="INVALID", invalid_i=j)
            _event(state, "FIB_INVALID", j, bars, "Close menembus level 100%; struktur leg FORMING gagal.")
            return state
        rt = _wick_retrace(state, b)
        state["max_retrace"] = max(state["max_retrace"], rt or 0.0)
        if j - state["ext_i"] >= k_confirm and state["max_retrace"] + _EPS >= min_retrace:
            state.update(status="LOCKED", lock_i=j)
            _event(state, "FIB_LOCKED", j, bars,
                   "Pivot ujung impuls terkonfirmasi, retrace minimal 23,6% tercapai, dan belum ada close menembus 100%.")
        return state

    if state["status"] in ("LOCKED", "EXTENDED"):
        if _invalid_close(state, b):
            state.update(status="INVALID", invalid_i=j, candidate=None)
            _event(state, "FIB_INVALID", j, bars, "Close menembus 100%; struktur Fib gagal dan anchor dibekukan sebagai histori.")
            return state
        if state["status"] == "LOCKED":
            # Track the deepest post-lock pullback before the first 1.0 close breakout.
            if state["dir"] == "bull":
                if state["pull_i"] is None or b["l"] < bars[state["pull_i"]]["l"]:
                    state["pull_i"] = j
            elif state["pull_i"] is None or b["h"] > bars[state["pull_i"]]["h"]:
                state["pull_i"] = j
            if _break_1_close(state, b):
                state.update(status="EXTENDED", extended_i=j, break_i=j)
                _start_candidate(state, bars)
                _event(state, "FIB_EXTENDED", j, bars,
                       "Close melewati 1.0; anchor lama tetap dan target extension 1.236–2.618 diaktifkan.")
                return state
        else:
            repl = _advance_candidate(state, bars, j, k_confirm, min_retrace)
            if repl:
                return repl
    return state


def lifecycle_series(bars, seeds, k_confirm, start_i=0, min_retrace=0.236):
    """Return close-by-close snapshots; every snapshot uses only bars <= j."""
    seed_at = {}
    for s in seeds:
        seed_at.setdefault(s["i"], []).append(s)
    state, out = None, {}
    pending = None
    for j in range(len(bars)):
        # Initial Fib, or a new structural seed after invalidation. Invalid Fib remains visible until lock.
        if state is None:
            avail = seed_at.get(j) or []
            if avail:
                state = _new_state(avail[-1], bars)
        elif state["status"] == "INVALID" and pending is None:
            avail = seed_at.get(j) or []
            if avail:
                pending = _new_state(avail[-1], bars)
        if pending is not None:
            pending = _advance(pending, bars, j, k_confirm, min_retrace)
            if pending["status"] == "LOCKED":
                pending["previous"] = _archive(state)
                pending["replaced_from"] = state["id"]
                _event(pending, "FIB_REPLACED", j, bars, "Fib invalid diganti setelah leg struktural baru memenuhi syarat LOCKED.",
                       previous_id=state["id"])
                state, pending = pending, None
            elif pending["status"] == "INVALID":
                pending = None
        elif state is not None:
            state = _advance(state, bars, j, k_confirm, min_retrace)
            # A CHoCH/BOS can invalidate the old Fib and seed the opposite leg on the same close.
            # Capture that seed now; waiting for j+1 would lose it because seed events are point-in-time.
            if state["status"] == "INVALID" and seed_at.get(j):
                cand = seed_at[j][-1]
                if cand["seed_id"] != state["id"]:
                    pending = _advance(_new_state(cand, bars), bars, j, k_confirm, min_retrace)
        if state is not None and j >= start_i:
            snap = deepcopy(state)
            if pending is not None:
                snap["pending"] = deepcopy(pending)
            out[j] = snap
    return out


def lite(rec, bars=None, j=None):
    if not rec:
        return None
    return {"leg_id": rec["leg_id"], "id": rec["id"], "scope": "swing", "dir": rec["dir"],
            "hi": rec["hi"], "lo": rec["lo"], "origin_i": rec["origin_i"], "ext_i": rec["ext_i"],
            "valid": rec["status"] != "INVALID", "status": rec["status"], "invalid_i": rec.get("invalid_i"),
            "events": deepcopy(rec.get("events") or []), "previous": deepcopy(rec.get("previous"))}


def _zone_label(rt):
    if rt is None:
        return None
    return ("extension di atas 1.0" if rt < 0 else "0–38.2" if rt < 0.382 else "38.2–50" if rt < 0.5 else
            "50–61.8 (zona utama)" if rt <= 0.618 else "61.8–78.6" if rt <= 0.786 else
            "78.6–100" if rt <= 1 else "lewat 100%")


def _one_public(rec, bars, tf, digits, price):
    if not rec:
        return None
    bull, hi, lo = rec["dir"] == "bull", rec["hi"], rec["lo"]
    a100 = {"t": bars[rec["origin_i"]]["t"], "price": round(lo if bull else hi, digits)}
    a0 = {"t": bars[rec["ext_i"]]["t"], "price": round(hi if bull else lo, digits)}
    rt = retrace_of(rec["dir"], hi, lo, price) if price is not None else None
    return {"id": rec["id"], "leg_id": rec["leg_id"], "tf": tf, "scope": "swing", "dir": rec["dir"],
            "status": rec["status"], "anchor100": a100, "anchor0": a0, "start_t": rec["start_t"],
            "event": {**rec["event"], "level": round(rec["event"]["level"], digits)},
            "levels": [{"r": r, "notation": f"{r * 100:g}%", "price": round(price_at(rec["dir"], hi, lo, r), digits)} for r in LEVELS],
            "extensions": [{"x": x, "notation": f"{x:g}", "price": round(extension_price(rec["dir"], hi, lo, x), digits)} for x in EXTENSIONS],
            "gold": sorted(round(price_at(rec["dir"], hi, lo, r), digits) for r in GOLD),
            "range": round(hi - lo, digits), "retrace": None if rt is None else round(rt * 100, 1),
            "zone": _zone_label(rt), "locked_t": bars[rec["lock_i"]]["t"] if rec.get("lock_i") is not None else None,
            "extended_t": bars[rec["extended_i"]]["t"] if rec.get("extended_i") is not None else None,
            "invalid_t": bars[rec["invalid_i"]]["t"] if rec.get("invalid_i") is not None else None,
            "lifecycle_events": deepcopy(rec.get("events") or [])}


def _clusters(active, previous, digits):
    if not active or not previous:
        return []
    ag = sorted(active.get("gold") or [])
    pg = sorted(previous.get("gold") or [])
    out = []
    if len(ag) == 2 and len(pg) == 2:
        bot, top = max(ag[0], pg[0]), min(ag[1], pg[1])
        if bot <= top:
            out.append({"kind": "gold_overlap", "bottom": round(bot, digits), "top": round(top, digits),
                        "label": "Fib cluster 50–61.8"})
    if len(ag) == 2:
        for lv in (previous.get("levels") or []) + (previous.get("extensions") or []):
            p = lv["price"]
            if ag[0] <= p <= ag[1]:
                out.append({"kind": "level_overlap", "bottom": p, "top": p,
                            "label": f"Fib cluster {lv.get('notation', '')}".strip()})
    # deterministic dedupe
    seen, unique = set(), []
    for z in out:
        key = (z["kind"], z["bottom"], z["top"], z["label"])
        if key not in seen:
            seen.add(key); unique.append(z)
    return unique


def public(rec, bars, j, tf, digits, price, k_confirm=None):
    if not rec:
        return None
    cur = _one_public(rec, bars, tf, digits, price)
    prev = _one_public(rec.get("previous"), bars, tf, digits, price) if rec.get("previous") else None
    cur["previous"] = prev
    cur["clusters"] = _clusters(cur, prev, digits)
    if rec.get("pending"):
        cur["pending"] = _one_public(rec["pending"], bars, tf, digits, price)
    return cur


def build(bars, smc, atr14, tf, digits, price):
    if not config.FIB_ENABLED or len(bars) < 10:
        return None, {"swing": []}, (lambda start_i=0: {})
    swing_legs = legs(bars, smc.get("events") or [], atr14, "swing")
    k_confirm, _ = config.structure_lengths(tf)
    cache = lifecycle_series(bars, swing_legs, k_confirm, 0, config.FIB_LOCK_RETRACE)
    last = len(bars) - 1
    cur = cache.get(last)
    pub = public(cur, bars, last, tf, digits, price, k_confirm)
    if pub is not None:
        hist = []
        for j, snap in cache.items():
            for ev in snap.get("events") or []:
                at = _one_public(snap, bars, tf, digits, bars[j]["c"])
                hist.append({"id": ev["id"], "event": deepcopy(ev), "fib": at})
        pub["_history"] = hist
    return pub, {"swing": swing_legs}, (
        lambda start_i=0: {j: deepcopy(v) for j, v in cache.items() if j >= start_i})


def index_at(close_times, T):
    return bisect_right(close_times, T) - 1
