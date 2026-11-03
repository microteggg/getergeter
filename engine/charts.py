"""Gambar chart PNG untuk dikirim lewat Telegram (/chartxauusd)."""
from datetime import datetime, timezone, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

WIB = timezone(timedelta(hours=7))
BG, FG, GRID = "#0f172a", "#e2e8f0", "#1e293b"
UP, DN = "#22c55e", "#ef4444"


def _fmt(t, tf):
    dt = datetime.fromtimestamp(t, WIB)
    if tf in ("D", "W"):
        return dt.strftime("%d %b")
    if tf in ("H4", "H1"):
        return dt.strftime("%d/%m %H:%M")
    return dt.strftime("%H:%M")


def render(path, symbol, tf, bars, an, digits, n_show=90):
    bars = bars[-n_show:]
    if not bars:
        return False
    idx = {b["t"]: i for i, b in enumerate(bars)}
    fig, ax = plt.subplots(figsize=(11, 6), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    w = 0.6
    for i, b in enumerate(bars):
        col = UP if b["c"] >= b["o"] else DN
        ax.vlines(i, b["l"], b["h"], color=col, linewidth=1)
        lo, hi = min(b["o"], b["c"]), max(b["o"], b["c"])
        ax.add_patch(Rectangle((i - w / 2, lo), w, max(hi - lo, 1e-9), color=col, alpha=0.35 if b.get("forming") else 1))
    n = len(bars)
    ser = an.get("series") or {}
    times = ser.get("times") or []
    for key, col, lab in (("ema20", "#f59e0b", "EMA20"), ("ema50", "#38bdf8", "EMA50")):
        vals = ser.get(key) or []
        xs, ys = [], []
        for t, v in zip(times, vals):
            if v is not None and t in idx:
                xs.append(idx[t]); ys.append(v)
        if xs:
            ax.plot(xs, ys, color=col, linewidth=1.3, label=lab)
    # Zona FVG / OB
    for z, kind in [(z, "FVG") for z in an.get("fvg", [])] + [(z, "OB") for z in an.get("ob", [])]:
        x0 = idx.get(z["t"], 0)
        col = UP if z["dir"] == "bull" else DN
        ax.add_patch(Rectangle((x0, z["bottom"]), n - x0 + 1, z["top"] - z["bottom"], color=col,
                               alpha=0.12 if kind == "FVG" else 0.2, linewidth=0))
        ax.text(n + 0.5, (z["top"] + z["bottom"]) / 2, kind, color=col, fontsize=7, va="center")
    # BOS / CHoCH
    for e in an.get("events", []):
        if e["t"] in idx:
            i = idx[e["t"]]
            col = UP if e["dir"] == "bull" else DN
            ax.hlines(e["level"], max(0, i - 8), i, colors=col, linestyles="dotted", linewidth=1)
            ax.text(i, e["level"], e["type"], color=col, fontsize=7, ha="right",
                    va="bottom" if e["dir"] == "bull" else "top")
    # Entry / SL / TP
    st = an.get("setup")
    if st:
        for k, col in (("entry", "#e2e8f0"), ("sl", DN), ("tp1", UP), ("tp2", UP)):
            ax.axhline(st[k], color=col, linestyle="--", linewidth=1)
            ax.text(n + 0.5, st[k], f"{k.upper()} {st[k]:.{digits}f}", color=col, fontsize=8, va="center")
        sig = an.get("signal")
        last = bars[-1]
        if sig == "BUY":
            ax.annotate("BUY", (n - 1, last["l"]), xytext=(n - 1, last["l"] - 2 * (an.get("atr") or 0)),
                        color=UP, ha="center", arrowprops=dict(arrowstyle="->", color=UP), fontsize=10, weight="bold")
        elif sig == "SELL":
            ax.annotate("SELL", (n - 1, last["h"]), xytext=(n - 1, last["h"] + 2 * (an.get("atr") or 0)),
                        color=DN, ha="center", arrowprops=dict(arrowstyle="->", color=DN), fontsize=10, weight="bold")
    step = max(1, n // 8)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([_fmt(bars[i]["t"], tf) for i in range(0, n, step)], fontsize=8, color=FG)
    ax.tick_params(colors=FG, labelsize=8)
    ax.yaxis.tick_right()
    ax.grid(color=GRID, linewidth=0.6)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_xlim(-1, n + 8)
    title = f"{symbol} {tf}  |  {an.get('signal','WAIT')}  |  Trend: {an.get('trend','-')}  |  RSI {an.get('rsi','-')}"
    ax.set_title(title, color=FG, fontsize=11, loc="left", pad=10)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper left", fontsize=8, facecolor=BG, labelcolor=FG, edgecolor=GRID)
    fig.text(0.01, 0.01, "Data: RealMarketAPI M5 → dibentuk ulang | Waktu WIB", color="#64748b", fontsize=7)
    fig.tight_layout(pad=1.4)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return True
