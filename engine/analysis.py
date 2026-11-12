"""Analisis per-TF + keputusan top-down (definisi: LOGIC_SPEC.md).

PRINSIP v13: satu fungsi keputusan dipakai untuk LIVE dan RIWAYAT.
  - Setup TF dievaluasi pada SETIAP candle tertutup j (setup_at j) memakai hanya data <= j.
    Sinyal live = hasil pada candle tertutup terakhir. Riwayat = hasil pada semua candle.
  - Entry top-down dievaluasi pada SETIAP close candle CONFIRM_TF (decide_at T).
    Entry live = hasil pada close terakhir. Riwayat = semua T.
Dengan begitu riwayat yang tampil di dashboard = persis apa yang akan dikirim engine saat itu
(tanpa repaint), dan bisa dipakai untuk backtest.
MA/RSI hanya tampilan.
"""
from bisect import bisect_right

import config
from indicators import ma, rsi, atr
from resample import TF_SEC
from smc import model, premium_discount_at
from zones import build_zones
import fibo

_DROP = ("created", "index", "pivot_index", "last_index", "tol", "event_id", "hist", "start", "end_i")


def _r(v, d):
    return None if v is None else round(v, d)


def _zl(items, d):
    out = []
    for item in items:
        q = {k: v for k, v in item.items() if k not in _DROP}
        for key in ("top", "bottom", "mid", "level", "price", "width"):
            if q.get(key) is not None:
                q[key] = _r(q[key], d)
        if q.get("gap_atr200") is not None:
            q["gap_atr200"] = round(q["gap_atr200"], 3)
        out.append(q)
    return out


def _fill_atr(bars, values, n):
    """Wilder ATR bila sudah siap; sebelum itu rata-rata TR berjalan."""
    out, trs, s = [], [], 0.0
    for i, b in enumerate(bars):
        pc = bars[i - 1]["c"] if i else b["c"]
        tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        s += tr
        if len(trs) > n:
            s -= trs[-n - 1]
        out.append(values[i] if i < len(values) and values[i] is not None else s / min(len(trs), n))
    return out


def _retrace_index(zone, bars):
    """Index bar pertama yang KEMBALI menyentuh zona setelah harga sempat meninggalkannya."""
    left = False
    for j in range(zone["created"] + 1, len(bars)):
        b = bars[j]
        if b["l"] <= zone["top"] and b["h"] >= zone["bottom"]:
            if left:
                return j
        else:
            left = True
    return None


def _location_ok(direction, loc):
    return ((direction == "bull" and loc in ("discount", "extreme_discount")) or
            (direction == "bear" and loc in ("premium", "extreme_premium")))


def _pool_at(pool, j):
    """Level & jumlah sentuhan pool pada bar j (riwayat merge cluster)."""
    level, touches = None, 0
    for i, lv in pool["hist"]:
        if i > j:
            break
        level, touches = lv, touches + 1
    return level, touches


def _empty(result):
    result.update(signal="WAIT", trend="Data belum cukup", structure=None, internal_structure=None,
                  swing_structure=None, setup=None, checklist=[], reason="Data belum cukup.",
                  events=[], sweeps=[], liquidity=[], liquidity_done=[], fvg=[], ob=[], fvg_mit=[], ob_mit=[],
                  pd_zones=[], strong_weak=[], zones=[], dealing_range=None, atr=None, atr14=None,
                  atr200=None, atr200_ready=False, setups=[], fib=None, _on=[], _setups={}, _times=[], _closes=[],
                  series={"times": [], "ma20": [], "ma50": [], "ma200": []})
    return result


class _Scanner:
    """Evaluasi setup pada bar j memakai hanya informasi yang sudah diketahui pada bar j."""

    def __init__(self, tf, bars, smc, atr14, digits):
        self.tf, self.bars, self.smc, self.atr14, self.d = tf, bars, smc, atr14, digits
        n = len(bars)
        self.n = n
        self.k = smc["swing_len"]
        self.pools = smc["liquidity"] + smc["liquidity_done"]
        # kandidat (sequence, zona) beserta rentang bar di mana keduanya valid & zona sudah di-retrace
        self.best = {}
        for order, s in enumerate(smc["sequences"]):
            inv = s["invalid_i"] if s["invalid_i"] is not None else n
            for prio, z in ((0, s["fvg"]), (1, s["ob"])):          # FVG diprioritaskan, lalu OB
                if not z:
                    continue
                ri = _retrace_index(z, bars)
                if ri is None:
                    continue
                zend = z["end_i"] if z.get("end_i") is not None else n
                start, end = max(s["event"]["index"], ri), min(inv, zend)
                key = (order, -prio)
                for j in range(start, end):
                    c, a = bars[j]["c"], atr14[j] * config.POI_PROXIMITY_ATR
                    if z["bottom"] - a <= c <= z["top"] + a:
                        cur = self.best.get(j)
                        if cur is None or key > cur[0]:
                            self.best[j] = (key, s, z, ri)

    def _range_at(self, j):
        hs, ls, k = self.smc["s_highs"], self.smc["s_lows"], self.k
        a, b = bisect_right(hs, j - k) - 1, bisect_right(ls, j - k) - 1
        if a < 0 or b < 0:
            return None
        hi, lo = self.bars[hs[a]]["h"], self.bars[ls[b]]["l"]
        top, bottom = max(hi, lo), min(hi, lo)
        if top - bottom <= 1e-12:
            return None
        return {"top": top, "bottom": bottom, "width": top - bottom}

    def _target_at(self, j, direction, price):
        best = None
        side = "BSL" if direction == "bull" else "SSL"
        for p in self.pools:
            if p["side"] != side or p["start"] > j or (p.get("end_i") is not None and p["end_i"] <= j):
                continue
            level, touches = _pool_at(p, j)
            if level is None or (direction == "bull" and level <= price) or (direction == "bear" and level >= price):
                continue
            if best is None or abs(level - price) < abs(best[0] - price):
                best = (level, touches, p)
        return best

    def at(self, j):
        """Return (setup|None, checklist, loc)."""
        bars, d = self.bars, self.d
        price = bars[j]["c"]
        hit = self.best.get(j)
        seq = zone = None
        if hit:
            _, seq, zone, ri = hit
        else:   # untuk checklist: sequence valid terbaru (tanpa zona)
            for s in reversed(self.smc["sequences"]):
                if s["event"]["index"] <= j and (s["invalid_i"] is None or s["invalid_i"] > j):
                    seq = s
                    break
        direction = seq["dir"] if seq else None
        rng = self._range_at(j)
        loc = premium_discount_at(price, rng)
        loc_ok = _location_ok(direction, loc)
        tgt = self._target_at(j, direction, price) if direction else None
        rr, setup = None, None
        if seq and zone and loc_ok and tgt:
            bull = direction == "bull"
            a = self.atr14[j]
            extreme = seq["sweep"]["price"]
            sl = extreme - config.SL_BUFFER_ATR * a if bull else extreme + config.SL_BUFFER_ATR * a
            risk, tp2 = abs(price - sl), tgt[0]
            rr = abs(tp2 - price) / risk if risk else 0.0
            if risk > 0 and ((bull and sl < price < tp2) or (not bull and tp2 < price < sl)) and rr >= config.MIN_TARGET_RR:
                tp1 = price + (1 if bull else -1) * config.TP1_R * risk
                if (bull and tp1 >= tp2) or (not bull and tp1 <= tp2):
                    tp1 = price + (tp2 - price) * 0.5
                lab = ("EQH" if tgt[2]["side"] == "BSL" else "EQL") if tgt[1] >= 2 else tgt[2]["side"]
                setup = {
                    "id": f"{seq['id']}|{zone['id']}", "seq_id": seq["id"], "tf": self.tf, "dir": direction,
                    "signal": "BUY" if bull else "SELL", "t": bars[j]["t"], "close_t": bars[j]["t"] + TF_SEC[self.tf],
                    "model": f"{seq['sweep']['side']} sweep → {seq['type']} ({seq['scope']}) → {zone['kind']}",
                    "entry": _r(price, d), "sl": _r(sl, d), "tp1": _r(tp1, d), "tp2": _r(tp2, d),
                    "risk": _r(risk, d), "rr": round(rr, 2), "scope": seq["scope"],
                    "touch_t": bars[ri]["t"], "location": loc,
                    "sweep": {"side": seq["sweep"]["side"], "level": _r(seq["sweep"]["level"], d),
                              "price": _r(extreme, d), "t": seq["sweep"]["t"]},
                    "target": {"side": tgt[2]["side"], "label": lab, "level": _r(tp2, d)},
                    "zone": {"id": zone["id"], "kind": zone["kind"], "scope": zone.get("scope"),
                             "top": _r(zone["top"], d), "bottom": _r(zone["bottom"], d), "t": zone["t"]},
                }
        checklist = [
            [bool(seq), "Sweep liquidity → displacement → BOS/MSS (sequence belum invalid)"],
            [bool(zone), "Harga kembali ke FVG/OB kontekstual setelah sempat meninggalkannya"],
            [bool(loc_ok), f"Lokasi {loc or '-'} sesuai arah (bull = discount, bear = premium)"],
            [bool(tgt), "Liquidity lawan (BSL/SSL aktif) tersedia sebagai target"],
            [rr is not None and rr >= config.MIN_TARGET_RR,
             f"RR ke target {rr:.2f} ≥ {config.MIN_TARGET_RR}" if rr is not None else f"RR ≥ {config.MIN_TARGET_RR}"],
        ]
        return setup, checklist, loc, rng


def analyze_tf(tf, closed, price, digits):
    """closed = SEMUA candle tertutup tersimpan. price hanya untuk tampilan."""
    internal_len, swing_len = config.structure_lengths(tf)
    need = config.min_bars(tf)
    result = {"tf": tf, "bars": len(closed), "need": need, "enough": len(closed) >= need, "price": _r(price, digits)}
    if len(closed) < min(need, 20):
        return _empty(result)

    bars = closed
    closes = [b["c"] for b in bars]
    ma20, ma50, ma200 = ma(closes, config.MA_FAST), ma(closes, config.MA_MID), ma(closes, config.MA_LONG)
    rsi14 = rsi(closes, config.RSI_LEN)
    raw200 = atr(bars, config.STRUCTURE_ATR_LEN)
    atr14 = _fill_atr(bars, atr(bars, config.ATR_LEN), config.ATR_LEN)
    atr200 = _fill_atr(bars, raw200, config.STRUCTURE_ATR_LEN)

    smc = model(
        bars, atr14=atr14, atr200=atr200, internal_len=internal_len, swing_len=swing_len,
        eq_threshold=config.EQUAL_LIQ_ATR, displacement_atr=config.DISPLACEMENT_ATR,
        displacement_body_ratio=config.DISPLACEMENT_BODY_RATIO, fvg_mode=config.FVG_MODE,
        fvg_atr_threshold=config.FVG_ATR_THRESHOLD, fvg_auto_multiplier=config.FVG_AUTO_MULTIPLIER,
        fvg_min_body_ratio=config.FVG_MIN_BODY_RATIO, ob_lookback=config.OB_LOOKBACK,
        sequence_window=config.SEQUENCE_WINDOW)

    sc = _Scanner(tf, bars, smc, atr14, digits)
    zones = build_zones(tf, bars, smc, atr14, digits) if config.ZONE_ENABLED else []
    on, setups, first = [], {}, []
    for j in sorted(sc.best):
        s, _, _, _ = sc.at(j)
        if s:
            on.append([bars[j]["t"], s["id"]])
            if s["id"] not in setups:
                setups[s["id"]] = s
                first.append(s)
    last = len(bars) - 1
    setup, checklist, loc, drange = sc.at(last)
    signal = setup["signal"] if setup else "WAIT"

    sdir, idir = smc["swing_trend"], smc["internal_trend"]
    trend = ("Bullish (Swing)" if sdir == "bull" else "Bearish (Swing)" if sdir == "bear" else
             "Bullish (Internal)" if idir == "bull" else "Bearish (Internal)" if idir == "bear" else "Sideways")
    reason = (f"SETUP {signal}: {setup['model']} → retrace → target {setup['target']['label']}." if setup else
              "WAIT: sequence, retrace POI, lokasi dealing range, target, atau RR belum lengkap.")
    lb = bars[-1]
    # v19: Auto Fibonacci TF ini (anchor dari leg BOS/CHoCH, lihat fibo.py)
    fib_pub, fib_legs, fib_series = fibo.build(bars, smc, atr14, tf, digits, price)
    pools_active = [dict(p, label=p["label"]) for p in smc["liquidity"]]
    result.update(
        trend=trend, structure=sdir or idir, internal_structure=idir, swing_structure=sdir,
        signal=signal, setup=setup, checklist=checklist, reason=reason,
        ma20=_r(ma20[-1], digits), ma50=_r(ma50[-1], digits), ma200=_r(ma200[-1], digits),
        rsi=_r(rsi14[-1], 1), atr=_r(atr14[-1], digits), atr14=_r(atr14[-1], digits),
        atr200=_r(atr200[-1], digits), atr200_ready=raw200[-1] is not None,
        structure_lengths={"internal": internal_len, "swing": swing_len},
        fvg_mode=config.FVG_MODE, premium_discount=loc,
        dealing_range=_zl([dict(drange, mid=(drange["top"] + drange["bottom"]) / 2)], digits)[0] if drange else None,
        last_closed_t=lb["t"],
        last_candle={"t": lb["t"], "o": _r(lb["o"], digits), "h": _r(lb["h"], digits),
                     "l": _r(lb["l"], digits), "c": _r(lb["c"], digits),
                     "dir": "Bullish" if lb["c"] >= lb["o"] else "Bearish"},
        liquidity=_zl(pools_active, digits), liquidity_done=_zl(smc["liquidity_done"], digits),
        events=_zl(smc["events"], digits), sweeps=_zl(smc["sweeps"], digits),
        fvg=_zl(smc["fvg"], digits), ob=_zl(smc["order_blocks"], digits),
        fvg_mit=_zl(smc["fvg_mit"], digits), ob_mit=_zl(smc["ob_mitigated"], digits),
        pd_zones=_zl(smc["pd_zones"], digits), strong_weak=_zl(smc["strong_weak"], digits),
        setups=first, zones=zones, fib=fib_pub,
        # internal (tidak ditulis ke state.json): dipakai decide() & journal
        _on=on, _setups=setups, _times=[b["t"] for b in bars], _closes=[b["c"] for b in bars],
        _bars=bars, _smc=smc, _atr14=atr14, _fib_series=fib_series,
        series={"times": [b["t"] for b in bars], "ma20": ma20, "ma50": ma50, "ma200": ma200},
    )
    return result


# ====================================================================== top-down
class _Decider:
    def __init__(self, tfs, digits):
        self.d = digits
        self.b = tfs.get(config.BIAS_TF) or {}
        self.s = tfs.get(config.SETUP_TF) or {}
        self.c = tfs.get(config.CONFIRM_TF) or {}
        sb, ss, sc = TF_SEC[config.BIAS_TF], TF_SEC[config.SETUP_TF], TF_SEC[config.CONFIRM_TF]
        self.sc = sc
        self.need_b, self.need_c = config.min_bars(config.BIAS_TF), config.min_bars(config.CONFIRM_TF)
        self.b_close = [t + sb for t in self.b.get("_times", [])]
        bsw = [e for e in self.b.get("events", []) if e["scope"] == "swing"]
        self.b_ev = ([e["t"] + sb for e in bsw], [e["dir"] for e in bsw])
        self.s_times = self.s.get("_times", [])
        self.s_close = [t + ss for t in self.s_times]
        self.on = {t: sid for t, sid in self.s.get("_on", [])}
        self.setups = self.s.get("_setups", {})
        self.c_times, self.c_closes = self.c.get("_times", []), self.c.get("_closes", [])
        cin = [e for e in self.c.get("events", []) if e["scope"] == "internal"]
        self.c_in = ([e["t"] + sc for e in cin], [e["dir"] for e in cin])
        self.c_all = self.c.get("events", [])
        self.c_all_close = [e["t"] + sc for e in self.c_all]

    @staticmethod
    def _last(ev, T):
        i = bisect_right(ev[0], T) - 1
        return ev[1][i] if i >= 0 else None

    def at(self, k):
        """Keputusan pada close candle CONFIRM_TF ke-k. Return (entry|None, reasons, setup)."""
        T = self.c_times[k] + self.sc
        h = bisect_right(self.s_close, T) - 1
        sid = self.on.get(self.s_times[h]) if h >= 0 else None
        setup = self.setups.get(sid) if sid else None
        want = setup["dir"] if setup else None
        nb = bisect_right(self.b_close, T)
        bias = self._last(self.b_ev, T) if nb >= self.need_b else None
        bias_txt = bias or ("data belum cukup" if nb < self.need_b else "netral")
        idir = self._last(self.c_in, T) if k + 1 >= self.need_c else None
        trig = None
        if want and idir == want:
            i = bisect_right(self.c_all_close, T) - 1
            while i >= 0:
                e = self.c_all[i]
                if e["dir"] == want:
                    if e["t"] >= setup["touch_t"]:
                        trig = e
                    break
                i -= 1
        reasons = [
            [bias == want if want else bias is not None, f"Bias {config.BIAS_TF}: swing structure {bias_txt}"],
            [want is not None, f"Setup {config.SETUP_TF}: " + (f"{setup['signal']} – {setup['model']}" if setup else "belum ada setup valid")],
            [trig is not None,
             f"Trigger {config.CONFIRM_TF}: {trig['label']} {trig['dir']} @ {trig['level']}" if trig
             else f"Trigger {config.CONFIRM_TF}: belum ada BOS/CHoCH searah setelah harga menyentuh POI"],
        ]
        entry = None
        if want and bias == want and trig:
            bull = want == "bull"
            price = self.c_closes[k]
            sl, tp2 = setup["sl"], setup["tp2"]
            risk = abs(price - sl)
            rr = abs(tp2 - price) / risk if risk else 0
            ok = risk > 0 and ((bull and sl < price < tp2) or (not bull and tp2 < price < sl)) and rr >= config.MIN_TARGET_RR
            reasons.append([ok, f"RR pada harga trigger {rr:.2f} ≥ {config.MIN_TARGET_RR}"])
            if ok:
                tp1 = price + (1 if bull else -1) * config.TP1_R * risk
                if (bull and tp1 >= tp2) or (not bull and tp1 <= tp2):
                    tp1 = price + (tp2 - price) * 0.5
                d = self.d
                entry = dict(setup, id=setup["seq_id"], setup_id=setup["id"], setup_t=setup["t"],
                             t=self.c_times[k], close_t=T, tf=config.CONFIRM_TF,
                             entry=round(price, d), tp1=round(tp1, d), risk=round(risk, d),
                             rr=round(rr, 2), trigger={"t": trig["t"], "label": trig["label"], "level": trig["level"]})
        return entry, reasons, setup


def overall(tfs, digits=2):
    """Keputusan live (close CONFIRM_TF terakhir) + riwayat semua entry (scan seluruh close CONFIRM_TF)."""
    dc = _Decider(tfs, digits)
    n = len(dc.c_times)
    entries, seen = [], set()
    for k in range(n):
        if not dc.on:
            break
        e, _, _ = dc.at(k)
        if e and e["id"] not in seen:
            seen.add(e["id"])
            entries.append(e)
    if not n:
        return {"signal": "WAIT", "setup": None, "pending": None, "entries": entries,
                "reasons": [[False, f"Data {config.CONFIRM_TF} belum ada"]], "summary": "WAIT – data belum cukup."}
    e, reasons, setup = dc.at(n - 1)
    if e:
        return {"signal": e["signal"], "setup": e, "reasons": reasons, "pending": None, "entries": entries,
                "summary": f"{e['signal']} TERKONFIRMASI: bias {config.BIAS_TF} → setup {config.SETUP_TF} → trigger {config.CONFIRM_TF}."}
    return {"signal": "WAIT", "setup": None, "pending": setup["signal"] if setup else None, "reasons": reasons,
            "entries": entries,
            "summary": ("WAIT – setup " + setup["signal"] + " " + config.SETUP_TF + " ada, menunggu bias/trigger."
                        if setup else "WAIT – belum ada setup valid di " + config.SETUP_TF + ".")}


# ====================================================================== backtest
def outcome(rec, bars):
    """Hasil posisi penuh: TP2 (+RR) atau SL (−1R). Bila SL & TP di candle yang sama → SL (konservatif).
    bars = candle (urut waktu) yang DIBUKA pada/sesudah waktu keputusan."""
    bull = rec["dir"] == "bull"
    entry, sl, tp1, tp2 = rec["entry"], rec["sl"], rec["tp1"], rec["tp2"]
    risk = abs(entry - sl) or 1e-12
    out = {"status": "OPEN", "result_r": None, "exit_t": None, "tp1_hit": False, "tp1_t": None, "mfe_r": 0.0, "mae_r": 0.0}
    for b in bars:
        fav = ((b["h"] - entry) if bull else (entry - b["l"])) / risk
        adv = ((entry - b["l"]) if bull else (b["h"] - entry)) / risk
        out["mfe_r"], out["mae_r"] = round(max(out["mfe_r"], fav), 2), round(max(out["mae_r"], adv), 2)
        if (bull and b["l"] <= sl) or (not bull and b["h"] >= sl):
            out.update(status="SL", result_r=-1.0, exit_t=b["t"])
            return out
        if not out["tp1_hit"] and ((bull and b["h"] >= tp1) or (not bull and b["l"] <= tp1)):
            out.update(tp1_hit=True, tp1_t=b["t"])
        if (bull and b["h"] >= tp2) or (not bull and b["l"] <= tp2):
            out.update(status="TP2", result_r=round(abs(tp2 - entry) / risk, 2), exit_t=b["t"])
            return out
    return out
