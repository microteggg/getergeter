"""Chart PNG gaya Pine/TradingView untuk Telegram (/chartxauusd, /chartbtcusdt)."""
from datetime import datetime, timezone, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

WIB = timezone(timedelta(hours=7))
BG, FG, GRID, MUTED = "#131722", "#d1d4dc", "#1e222d", "#787b86"
UP, DN = "#26a69a", "#ef5350"
OB_BULL, OB_BEAR = "#089981", "#880e4f"
FVG_BULL, FVG_BEAR = "#2962ff", "#ff9800"
SWEEP = "#ffeb3b"
MA_COL = {"ma20": "#f7c548", "ma50": "#2962ff", "ma100": "#e040fb"}


def _fmt(t, tf):
    dt = datetime.fromtimestamp(t, WIB)
    if tf in ("D", "W"):
        return dt.strftime("%d %b")
    if tf in ("H4", "H1"):
        return dt.strftime("%d/%m %H:%M")
    return dt.strftime("%H:%M")


def render(path, symbol, tf, bars, an, digits, n_show=100):
    bars = bars[-n_show:]
    if not bars:
        return False
    idx = {b["t"]: i for i, b in enumerate(bars)}
    t0, n = bars[0]["t"], len(bars)
    has_vol = "v" in bars[-1]
    if has_vol:
        fig, (ax, axv) = plt.subplots(2, 1, figsize=(12, 7), dpi=110, sharex=True,
                                      gridspec_kw={"height_ratios": [4.2, 1], "hspace": 0.02})
    else:
        fig, ax = plt.subplots(figsize=(12, 6.4), dpi=110)
        axv = None
    fig.patch.set_facecolor(BG)
    for a in (ax, axv):
        if a is not None:
            a.set_facecolor(BG)

    def xpos(t):
        if t in idx:
            return idx[t]
        return 0 if t < t0 else n - 1

    for key, cb, cs, lab, alpha in (("ob", OB_BULL, OB_BEAR, "OB", 0.28), ("fvg", FVG_BULL, FVG_BEAR, "FVG", 0.22),
                                    ("ob_mit", OB_BULL, OB_BEAR, "", 0.10), ("fvg_mit", FVG_BULL, FVG_BEAR, "", 0.08)):
        for z in an.get(key, []):
            if z.get("end_t") and z["end_t"] < t0:
                continue
            x0 = xpos(z["t"])
            x1 = xpos(z["end_t"]) if z.get("end_t") else n + 3
            col = cb if z["dir"] == "bull" else cs
            ax.add_patch(Rectangle((x0 - 0.4, z["bottom"]), x1 - x0 + 0.4, z["top"] - z["bottom"],
                                   facecolor=col, alpha=alpha, edgecolor=col, linewidth=0.6, zorder=1))
            if lab:
                ax.text(x1 - 0.3, (z["top"] + z["bottom"]) / 2, ("Bull " if z["dir"] == "bull" else "Bear ") + lab,
                        color=FG, fontsize=6.5, va="center", ha="right", zorder=5, clip_on=True)
    ser = an.get("series") or {}
    for key, col in MA_COL.items():
        xs, ys = [], []
        for t, v in zip(ser.get("times") or [], ser.get(key) or []):
            if v is not None and t in idx:
                xs.append(idx[t]); ys.append(v)
        if xs:
            ax.plot(xs, ys, color=col, linewidth=1.1, label=key.upper().replace("MA", "MA "), zorder=3)
    w = 0.62
    for i, b in enumerate(bars):
        col = UP if b["c"] >= b["o"] else DN
        al = 0.45 if b.get("forming") else 1
        ax.vlines(i, b["l"], b["h"], color=col, linewidth=0.9, alpha=al, zorder=4)
        lo, hi = min(b["o"], b["c"]), max(b["o"], b["c"])
        ax.add_patch(Rectangle((i - w / 2, lo), w, max(hi - lo, 1e-12), color=col, alpha=al, zorder=4))
    if axv is not None:
        axv.bar(range(n), [b.get("v", 0) for b in bars], width=w, alpha=0.55,
                color=[UP if b["c"] >= b["o"] else DN for b in bars])
    for e in an.get("events", []):
        if e["t"] < t0:
            continue
        x0, x1 = xpos(max(e.get("from_t") or e["t"], t0)), xpos(e["t"])
        col = UP if e["dir"] == "bull" else DN
        ax.hlines(e["level"], x0, x1, colors=col, linestyles="-" if e["type"] == "CHoCH" else "--", linewidth=1.1, zorder=5)
        ax.text((x0 + x1) / 2, e["level"], e["type"], color=col, fontsize=7, ha="center",
                va="bottom" if e["dir"] == "bull" else "top", zorder=6, clip_on=True)
    for s in an.get("sweeps", []):
        if s["t"] < t0:
            continue
        x0, x1 = xpos(max(s["from_t"], t0)), xpos(s["t"])
        ax.hlines(s["level"], x0, x1, colors=SWEEP, linestyles=":", linewidth=0.9, zorder=5)
        ax.text(x1, s["price"], "Sweep", color=BG, fontsize=6, ha="center", clip_on=True,
                va="top" if s["dir"] == "bull" else "bottom", zorder=7, bbox=dict(boxstyle="round,pad=0.15", fc=SWEEP, ec="none"))
    st = an.get("setup")
    if st:
        for k, col in (("entry", FG), ("sl", DN), ("tp1", UP), ("tp2", UP)):
            ax.hlines(st[k], n - 12, n + 3, colors=col, linestyles="--", linewidth=1, zorder=6)
            ax.text(n + 3.2, st[k], f"{k.upper()} {st[k]:.{digits}f}", color=col, fontsize=7.5, va="center")
    sig = an.get("signal")
    closed = [b for b in bars if not b.get("forming")] or bars
    last = closed[-1]
    li = idx[last["t"]]
    A = an.get("atr") or 0
    if sig == "BUY":
        ax.annotate("BUY", (li, last["l"]), xytext=(li, last["l"] - 2.5 * A), color=UP, ha="center", fontsize=9,
                    weight="bold", arrowprops=dict(arrowstyle="-|>", color=UP), zorder=8)
    elif sig == "SELL":
        ax.annotate("SELL", (li, last["h"]), xytext=(li, last["h"] + 2.5 * A), color=DN, ha="center", fontsize=9,
                    weight="bold", arrowprops=dict(arrowstyle="-|>", color=DN), zorder=8)
    p = bars[-1]["c"]
    pc = UP if bars[-1]["c"] >= bars[-1]["o"] else DN
    ax.axhline(p, color=pc, linestyle=":", linewidth=0.8, zorder=2)
    ax.text(1.0, p, f" {p:.{digits}f} ", transform=ax.get_yaxis_transform(), color="white", fontsize=8,
            va="center", ha="left", bbox=dict(boxstyle="square,pad=0.2", fc=pc, ec="none"), zorder=9)
    ev = an["events"][-1] if an.get("events") else None
    rows = [(f"{symbol} {tf}", f"{p:.{digits}f}"),
            ("Struktur", (an.get("trend") or "-").split(" (")[0].upper()),
            ("Event", f"{ev['type']} {'▲' if ev['dir'] == 'bull' else '▼'}" if ev else "-"),
            ("RSI(14)", str(an.get("rsi", "-"))),
            ("ATR(14)", f"{an['atr']:.{digits}f}" if an.get("atr") else "-")]
    fl = an.get("flow")
    if fl:
        rows.append(("Buy vol 20", f"{fl['buy_pct']:.0f}%"))
    rows.append(("Signal", sig or "WAIT"))
    ax.add_patch(Rectangle((0.828, 0.985 - 0.045 * len(rows) - 0.01), 0.172, 0.045 * len(rows) + 0.015,
                           transform=ax.transAxes, facecolor="#1e222d", edgecolor="#2a2e39", alpha=0.95, zorder=9))
    y = 0.975
    for k, v in rows:
        col = UP if v.startswith(("BULL", "BUY", "BOS ▲", "CHoCH ▲")) else DN if v.startswith(("BEAR", "SELL", "BOS ▼", "CHoCH ▼")) else FG
        ax.text(0.835, y, k, transform=ax.transAxes, color=MUTED, fontsize=7.5, va="top", zorder=10)
        ax.text(0.995, y, v, transform=ax.transAxes, color=col, fontsize=7.5, va="top", ha="right", weight="bold", zorder=10)
        y -= 0.045
    bottom = axv if axv is not None else ax
    step = max(1, n // 8)
    bottom.set_xticks(range(0, n, step))
    bottom.set_xticklabels([_fmt(bars[i]["t"], tf) for i in range(0, n, step)], fontsize=7.5, color=MUTED)
    for a in (ax, axv):
        if a is None:
            continue
        a.tick_params(colors=MUTED, labelsize=7.5)
        a.yaxis.tick_right()
        a.grid(color=GRID, linewidth=0.6)
        for s in a.spines.values():
            s.set_color(GRID)
    ax.set_xlim(-1, n + 14)
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    if st:
        lo, hi = min(lo, st["sl"], st["tp2"]), max(hi, st["sl"], st["tp2"])
    ax.set_ylim(lo - (hi - lo) * 0.06, hi + (hi - lo) * 0.42)
    if axv is not None:
        axv.text(0.005, 0.92, "Volume", transform=axv.transAxes, color=MUTED, fontsize=7, va="top")
    src = "Binance M1" if has_vol else "RealMarketAPI M5"
    ax.set_title(f"{symbol} · {tf} · {src} → {tf} · WIB", color=FG, fontsize=10, loc="left", pad=8)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper left", fontsize=7, facecolor=BG, labelcolor=FG, edgecolor=GRID)
    fig.subplots_adjust(left=0.02, right=0.93, top=0.95, bottom=0.06)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return True
