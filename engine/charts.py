"""Chart PNG gaya Pine/TradingView untuk Telegram (/chartxauusd, /chartbtcusdt)."""
from datetime import datetime, timezone, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

WIB = timezone(timedelta(hours=7))
BG, FG, GRID, MUTED = "#131722", "#d1d4dc", "#1e222d", "#787b86"
UP, DN = "#26a69a", "#ef5350"
IOB_BULL, IOB_BEAR = "#3179f5", "#f77c80"
SOB_BULL, SOB_BEAR = "#1848cc", "#b22833"
FVG_BULL, FVG_BEAR = "#2962ff", "#ff9800"
SWEEP, EQ = "#ffeb3b", "#90a4ae"
ZONE_BUY, ZONE_SELL = "#2962ff", "#ff9800"
MA_COL = {"ma20": "#f7c548", "ma50": "#2962ff", "ma200": "#e040fb"}


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
    idx = {bar["t"]: i for i, bar in enumerate(bars)}
    t0, n = bars[0]["t"], len(bars)
    has_vol = "v" in bars[-1]
    if has_vol:
        fig, (ax, axv) = plt.subplots(2, 1, figsize=(12, 7), dpi=110, sharex=True,
                                      gridspec_kw={"height_ratios": [4.2, 1], "hspace": 0.02})
    else:
        fig, ax = plt.subplots(figsize=(12, 6.4), dpi=110)
        axv = None
    fig.patch.set_facecolor(BG)
    for axis in (ax, axv):
        if axis is not None:
            axis.set_facecolor(BG)

    def xpos(t):
        if t in idx:
            return idx[t]
        return 0 if t < t0 else n - 1

    # LuxAlgo-style narrow premium/equilibrium/discount visual bands.
    pd_color = {"premium": DN, "equilibrium": MUTED, "discount": UP}
    for zone in an.get("pd_zones", []):
        x0 = xpos(zone.get("t", t0))
        ax.add_patch(Rectangle((x0 - 0.4, zone["bottom"]), n + 3 - x0,
                               zone["top"] - zone["bottom"],
                               facecolor=pd_color.get(zone.get("kind"), MUTED), alpha=0.055,
                               edgecolor="none", zorder=0))
        ax.text(n + 2.8, (zone["top"] + zone["bottom"]) / 2, zone.get("name", ""),
                color=pd_color.get(zone.get("kind"), MUTED), fontsize=6, ha="right", va="center")

    for key, mitigated in (("ob", False), ("fvg", False), ("ob_mit", True), ("fvg_mit", True)):
        for zone in an.get(key, []):
            if zone.get("end_t") and zone["end_t"] < t0:
                continue
            x0 = xpos(zone["t"])
            x1 = xpos(zone["end_t"]) if zone.get("end_t") else n + 3
            if key.startswith("ob"):
                internal = zone.get("scope") == "internal"
                color = (IOB_BULL if zone["dir"] == "bull" else IOB_BEAR) if internal else (SOB_BULL if zone["dir"] == "bull" else SOB_BEAR)
                label = ("iOB" if internal else "sOB") if not mitigated else ""
                alpha = 0.14 if internal else 0.24
            else:
                color = FVG_BULL if zone["dir"] == "bull" else FVG_BEAR
                label = ("FVG★" if zone.get("context") else "FVG") if not mitigated else ""
                alpha = 0.20
            if mitigated:
                alpha *= 0.35
            ax.add_patch(Rectangle((x0 - 0.4, zone["bottom"]), x1 - x0 + 0.4,
                                   zone["top"] - zone["bottom"], facecolor=color,
                                   alpha=alpha, edgecolor=color, linewidth=0.7, zorder=1))
            if label:
                prefix = "Bull " if zone["dir"] == "bull" else "Bear "
                ax.text(x1 - 0.3, (zone["top"] + zone["bottom"]) / 2, prefix + label,
                        color=FG, fontsize=6.5, va="center", ha="right", zorder=5, clip_on=True)

    # v17: BUY AREA (biru) / SELL AREA (oranye) + label kecil BUY/SELL hanya saat CONFIRMED
    for z in an.get("zones", []):
        if z.get("stage") == "INVALID" or (z.get("end_t") and (z["end_t"] < t0 or not z.get("confirm"))):
            continue
        bull = z["dir"] == "bull"
        col = ZONE_BUY if bull else ZONE_SELL
        x0 = xpos(max(z["t"], t0))
        x1 = xpos(z["end_t"]) if z.get("end_t") else n + 3
        ax.add_patch(Rectangle((x0 - 0.4, z["bottom"]), x1 - x0 + 0.4, z["top"] - z["bottom"],
                               facecolor=col, alpha=0.16, edgecolor=col, linewidth=0.9,
                               linestyle="-" if z["stage"] == "CONFIRMED" else "--", zorder=1.5))
        ax.text(x1 - 0.3, z["top"], f"{'BUY' if bull else 'SELL'} {z['stage']} {z['score']}/10",
                color=col, fontsize=6.5, ha="right", va="bottom", weight="bold", zorder=6, clip_on=True)
        cf = z.get("confirm")
        if cf and cf["t"] in idx:
            ci = idx[cf["t"]]
            bar = bars[ci]
            ax.text(ci, bar["l"] if bull else bar["h"], "BUY" if bull else "SELL", color="white",
                    fontsize=6.5, ha="center", va="top" if bull else "bottom", weight="bold", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.2", fc=UP if bull else DN, ec="none"))

    series = an.get("series") or {}
    for key, color in MA_COL.items():
        values = series.get(key) or []
        xs, ys = [], []
        for t, value in zip(series.get("times") or [], values or []):
            if value is not None and t in idx:
                xs.append(idx[t])
                ys.append(value)
        if xs:
            ax.plot(xs, ys, color=color, linewidth=1.1, label=key.upper().replace("MA", "MA "), zorder=3)

    candle_width = 0.62
    for i, bar in enumerate(bars):
        color = UP if bar["c"] >= bar["o"] else DN
        alpha = 0.45 if bar.get("forming") else 1
        ax.vlines(i, bar["l"], bar["h"], color=color, linewidth=0.9, alpha=alpha, zorder=4)
        low, high = min(bar["o"], bar["c"]), max(bar["o"], bar["c"])
        ax.add_patch(Rectangle((i - candle_width / 2, low), candle_width,
                               max(high - low, 1e-12), color=color, alpha=alpha, zorder=4))
    if axv is not None:
        axv.bar(range(n), [bar.get("v", 0) for bar in bars], width=candle_width, alpha=0.55,
                color=[UP if bar["c"] >= bar["o"] else DN for bar in bars])

    for event in an.get("events", []):
        if event["t"] < t0:
            continue
        x0, x1 = xpos(max(event.get("from_t") or event["t"], t0)), xpos(event["t"])
        color = UP if event["dir"] == "bull" else DN
        internal = event.get("scope") == "internal"
        line_style = ":" if internal else "--" if event["type"] == "BOS" else "-"
        label = event.get("label") or (("i" if internal else "") + event["type"])
        ax.hlines(event["level"], x0, x1, colors=color, linestyles=line_style,
                  linewidth=0.9 if internal else 1.2, zorder=5)
        ax.text((x0 + x1) / 2, event["level"], label, color=color, fontsize=6.5 if internal else 7,
                ha="center", va="bottom" if event["dir"] == "bull" else "top", zorder=6, clip_on=True)

    for pool in an.get("liquidity", []):
        if (pool.get("touches", 1) < 2 and pool.get("scope") != "swing") or pool.get("last_t", pool["t"]) < t0:
            continue
        x0 = xpos(max(pool["t"], t0))
        ax.hlines(pool["level"], x0, n + 3, colors=EQ, linestyles=":", linewidth=0.65, zorder=2)
        ax.text(n + 2.8, pool["level"], pool.get("label") or pool["side"], color=EQ,
                fontsize=6, ha="right", va="center")

    for sweep in an.get("sweeps", []):
        if sweep["t"] < t0:
            continue
        x0, x1 = xpos(max(sweep["from_t"], t0)), xpos(sweep["t"])
        ax.hlines(sweep["level"], x0, x1, colors=SWEEP, linestyles=":", linewidth=0.9, zorder=5)
        ax.text(x1, sweep["price"], "Sweep", color=BG, fontsize=6, ha="center", clip_on=True,
                va="top" if sweep["dir"] == "bull" else "bottom", zorder=7,
                bbox=dict(boxstyle="round,pad=0.15", fc=SWEEP, ec="none"))

    setup = an.get("setup")
    if setup:
        for key, color in (("entry", FG), ("sl", DN), ("tp1", UP), ("tp2", UP)):
            ax.hlines(setup[key], n - 12, n + 3, colors=color, linestyles="--", linewidth=1, zorder=6)
            ax.text(n + 3.2, setup[key], f"{key.upper()} {setup[key]:.{digits}f}", color=color, fontsize=7.5, va="center")

    signal = an.get("signal")
    closed = [bar for bar in bars if not bar.get("forming")] or bars
    last = closed[-1]
    last_index = idx[last["t"]]
    risk_atr = an.get("atr14") or an.get("atr") or 0
    if signal == "BUY":
        ax.annotate("SETUP BUY", (last_index, last["l"]), xytext=(last_index, last["l"] - 2.5 * risk_atr),
                    color=UP, ha="center", fontsize=9, weight="bold",
                    arrowprops=dict(arrowstyle="-|>", color=UP), zorder=8)
    elif signal == "SELL":
        ax.annotate("SETUP SELL", (last_index, last["h"]), xytext=(last_index, last["h"] + 2.5 * risk_atr),
                    color=DN, ha="center", fontsize=9, weight="bold",
                    arrowprops=dict(arrowstyle="-|>", color=DN), zorder=8)

    price = bars[-1]["c"]
    price_color = UP if bars[-1]["c"] >= bars[-1]["o"] else DN
    ax.axhline(price, color=price_color, linestyle=":", linewidth=0.8, zorder=2)
    ax.text(1.0, price, f" {price:.{digits}f} ", transform=ax.get_yaxis_transform(), color="white",
            fontsize=8, va="center", ha="left", bbox=dict(boxstyle="square,pad=0.2", fc=price_color, ec="none"), zorder=9)

    latest_event = an["events"][-1] if an.get("events") else None
    rows = [
        (f"{symbol} {tf}", f"{price:.{digits}f}"),
        ("Swing", (an.get("swing_structure") or "-").upper()),
        ("Internal", (an.get("internal_structure") or "-").upper()),
        ("Event", f"{latest_event.get('label', latest_event['type'])} {'▲' if latest_event['dir'] == 'bull' else '▼'}" if latest_event else "-"),
        ("RSI(14)", str(an.get("rsi", "-"))),
        ("ATR14", f"{an['atr14']:.{digits}f}" if an.get("atr14") else "-"),
        ("ATR200", f"{an['atr200']:.{digits}f}" if an.get("atr200") else "-"),
    ]
    flow = an.get("flow")
    if flow:
        rows.append(("Buy vol 20", f"{flow['buy_pct']:.0f}%"))
    rows.append(("Lokasi", (an.get("premium_discount") or "-").replace("_", " ")))
    rows.append(("Setup", signal or "WAIT"))
    ax.add_patch(Rectangle((0.815, 0.985 - 0.042 * len(rows) - 0.01), 0.185,
                           0.042 * len(rows) + 0.015, transform=ax.transAxes,
                           facecolor="#1e222d", edgecolor="#2a2e39", alpha=0.95, zorder=9))
    y = 0.975
    for key, value in rows:
        color = UP if value.startswith(("BULL", "BUY", "BOS ▲", "iBOS ▲")) else DN if value.startswith(("BEAR", "SELL", "BOS ▼", "iBOS ▼")) else FG
        ax.text(0.822, y, key, transform=ax.transAxes, color=MUTED, fontsize=7.2, va="top", zorder=10)
        ax.text(0.995, y, value, transform=ax.transAxes, color=color, fontsize=7.2,
                va="top", ha="right", weight="bold", zorder=10)
        y -= 0.042

    bottom_axis = axv if axv is not None else ax
    step = max(1, n // 8)
    bottom_axis.set_xticks(range(0, n, step))
    bottom_axis.set_xticklabels([_fmt(bars[i]["t"], tf) for i in range(0, n, step)], fontsize=7.5, color=MUTED)
    for axis in (ax, axv):
        if axis is None:
            continue
        axis.tick_params(colors=MUTED, labelsize=7.5)
        axis.yaxis.tick_right()
        axis.grid(color=GRID, linewidth=0.6)
        for spine in axis.spines.values():
            spine.set_color(GRID)
    ax.set_xlim(-1, n + 14)
    low = min(bar["l"] for bar in bars)
    high = max(bar["h"] for bar in bars)
    if setup:
        low, high = min(low, setup["sl"], setup["tp2"]), max(high, setup["sl"], setup["tp2"])
    ax.set_ylim(low - (high - low) * 0.06, high + (high - low) * 0.42)
    if axv is not None:
        axv.text(0.005, 0.92, "Volume", transform=axv.transAxes, color=MUTED, fontsize=7, va="top")
    source = "Binance M1" if has_vol else "RealMarketAPI M5"
    ax.set_title(f"{symbol} · {tf} · {source} → {tf} · WIB", color=FG, fontsize=10, loc="left", pad=8)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper left", fontsize=7, facecolor=BG, labelcolor=FG, edgecolor=GRID)
    fig.subplots_adjust(left=0.02, right=0.93, top=0.95, bottom=0.06)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return True
