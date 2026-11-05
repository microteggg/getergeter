"""Teks balasan semua command Telegram. Disimpan di state.json -> Worker cukup membaca (0 request API)."""
from datetime import datetime, timezone, timedelta
from html import escape

import config

WIB = timezone(timedelta(hours=7))
ICON = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "WAIT": "⏳ WAIT"}
PAIR_ICON = {"XAUUSD": "🟡", "EURUSD": "🔵", "BTCUSDT": "🟠", "ETHUSDT": "🟣"}


def wib(t, fmt="%d %b %H:%M"):
    return datetime.fromtimestamp(t, WIB).strftime(fmt) + " WIB" if t else "-"


def f(x, d):
    return "-" if x is None else f"{x:,.{d}f}"


def usd(x):
    if x is None:
        return "-"
    for div, s in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:.2f}{s}"
    return f"${x:.0f}"


def trend_icon(tr):
    return "🟢" if tr == "Bullish" else "🔴" if tr == "Bearish" else "⚪"


def short(tr):
    return (tr or "-").split(" (")[0]


def mark(ok):
    return "✅" if ok else "➖" if ok is None else "❌"


def help_text(st):
    lines = ["🤖 <b>Daftar command</b>", "",
             "/status – cek sistem hidup", "/price – harga terakhir", "/signal – sinyal semua pair",
             "/lastupdate – data terakhir yang diambil", ""]
    for s in st["pairs"]:
        lines.append(f"/{s.lower()} – analisis {s}" + (" + order flow" if st["pairs"][s]["market"] == "crypto" else ""))
        lines.append(f"/chart{s.lower()} – chart {s} (contoh: /chart{s.lower()} h4)")
    lines += ["", "/dashboard – link dashboard", "/help – daftar command", "",
              "ℹ️ Jawaban diambil dari hasil analisis terakhir (tanpa request tambahan ke API)."]
    return "\n".join(lines)


def status_text(st):
    fx, cr = st["sources"]["forex"], st["sources"]["crypto"]
    lines = ["📡 <b>SYSTEM STATUS</b>", "🟢 Engine berjalan (GitHub Actions)", ""]
    if fx["symbols"]:
        lines += [f"<b>Forex – RealMarketAPI</b>",
                  f"{'🟢' if fx['ok'] else '🔴'} {'Connected' if fx['ok'] else 'ERROR'}" + (" · 💤 pasar tutup" if fx.get("market_closed") else ""),
                  f"M5 tiap {config.FOREX_INTERVAL_MIN} menit · fetch terakhir {wib(fx.get('fetch_ts'), '%H:%M')}",
                  f"Next ±{wib(fx.get('next_ts'), '%H:%M')} · kuota {fx.get('req_month', 0)} / {config.MONTHLY_QUOTA}", ""]
    if cr["symbols"]:
        lines += [f"<b>Crypto – Binance Spot</b>",
                  f"{'🟢' if cr['ok'] else '🔴'} {'Connected' if cr['ok'] else 'ERROR'} ({escape(cr.get('host') or '-')})",
                  f"M1 + order book + transaksi tiap {config.CRYPTO_INTERVAL_MIN} menit · fetch terakhir {wib(cr.get('fetch_ts'), '%H:%M')}", ""]
    lines.append(f"{'🟢' if st.get('telegram_ok', True) else '🟠'} Telegram {'Connected' if st.get('telegram_ok', True) else 'ada kendala kirim'}")
    lines.append(f"Last Update: {wib(st.get('updated_ts'))}")
    for sym, err in (st.get("errors") or {}).items():
        lines.append(f"⚠️ {sym}: {escape(err)}")
    for w in st.get("warnings", []):
        lines.append(f"⚠️ {escape(w)}")
    return "\n".join(lines)


def price_text(st):
    lines = ["💰 <b>HARGA TERAKHIR</b>"]
    for sym, p in st["pairs"].items():
        d = p["digits"]
        extra = ""
        if p.get("ticker"):
            ch = p["ticker"]["change_pct"]
            extra = f"  {'🟢' if ch >= 0 else '🔴'} {ch:+.2f}% 24j"
        lines.append(f"{PAIR_ICON.get(sym, '•')} {sym}: <b>{f(p.get('price'), d)}</b>  ({wib(p.get('last_ts'), '%H:%M')}){extra}")
    lines.append(f"\n🕐 Update: {wib(st.get('updated_ts'))}")
    lines.append("Forex = close M5 terakhir · Crypto = close M1 terakhir (bukan tick real-time; real-time ada di dashboard).")
    return "\n".join(lines)


def signal_text(st):
    lines = ["📊 <b>CURRENT SIGNAL</b>"]
    for sym, p in st["pairs"].items():
        d = p["digits"]
        ov = p.get("overall", {})
        b = p["tf"].get(config.BIAS_TF, {})
        h = p["tf"].get(config.SETUP_TF, {})
        lines += ["", f"{PAIR_ICON.get(sym, '•')} <b>{sym}</b>  {f(p.get('price'), d)}",
                  f"Bias {config.BIAS_TF}: {trend_icon(b.get('trend'))} {short(b.get('trend'))} · Trend {config.SETUP_TF}: {trend_icon(h.get('trend'))} {short(h.get('trend'))}",
                  f"Signal: <b>{ICON[ov.get('signal', 'WAIT')]}</b>"]
        if p.get("book"):
            lines.append(f"Order book ±0,5%: bid {p['book']['imb_0.5']:.0f}% · Transaksi beli {p['trades']['buy_pct']:.0f}%" if p.get("trades") else "")
        s = ov.get("setup")
        if s:
            lines.append(f"Entry {f(s['entry'], d)} · SL {f(s['sl'], d)} · TP1 {f(s['tp1'], d)} · TP2 {f(s['tp2'], d)}")
        lines.append(f"<i>{escape(ov.get('summary', ''))}</i>")
    return "\n".join(x for x in lines if x is not None)


def pair_text(sym, p):
    d = p["digits"]
    ov = p.get("overall", {})
    tfs = ["W", "D", "H4", "H1", "M15", "M5"] + (["M1"] if p["market"] == "crypto" else [])
    lines = [f"{PAIR_ICON.get(sym, '•')} <b>{sym} – DETAIL ANALISIS</b>",
             f"Harga: <b>{f(p.get('price'), d)}</b>  ({wib(p.get('last_ts'), '%H:%M')})"]
    if p.get("ticker"):
        t = p["ticker"]
        lines.append(f"24j: {t['change_pct']:+.2f}% · H {f(t['high'], d)} · L {f(t['low'], d)} · Vol {usd(t['quote_volume'])}")
    lines += ["", "<b>Multi-timeframe</b>"]
    for tf in tfs:
        a = p["tf"].get(tf, {})
        if not a.get("enough"):
            lines.append(f"{tf:>3}: ⚪ data belum cukup ({a.get('bars', 0)} candle)")
            continue
        lines.append(f"{tf:>3}: {trend_icon(a.get('trend'))} {short(a.get('trend'))} | RSI {a.get('rsi')} | {ICON[a.get('signal', 'WAIT')]}")
    a = p["tf"].get(config.SETUP_TF, {})
    if a.get("enough"):
        lc = a["last_candle"]
        lines += ["", f"<b>{config.SETUP_TF} detail</b>",
                  f"MA20 {f(a.get('ma20'), d)} · MA50 {f(a.get('ma50'), d)} · MA100 {f(a.get('ma100'), d)}",
                  f"RSI {a['rsi']} · ATR {f(a['atr'], d)}",
                  f"Candle {wib(lc['t'], '%H:%M')}: O {f(lc['o'], d)} H {f(lc['h'], d)} L {f(lc['l'], d)} C {f(lc['c'], d)} – {lc['dir']}"]
        if a["events"]:
            e = a["events"][-1]
            lines.append(f"Struktur: {e['type']} {'bullish' if e['dir'] == 'bull' else 'bearish'} @ {f(e['level'], d)} ({wib(e['t'], '%d/%m %H:%M')})")
        if a.get("sweeps"):
            s = a["sweeps"][-1]
            lines.append(f"Sweep {'low' if s['dir'] == 'bull' else 'high'} @ {f(s['level'], d)} ({wib(s['t'], '%d/%m %H:%M')})")
        for z in a.get("fvg", [])[-2:]:
            lines.append(f"FVG {'🟦' if z['dir'] == 'bull' else '🟧'} {f(z['bottom'], d)}–{f(z['top'], d)}")
        for z in a.get("ob", [])[-2:]:
            lines.append(f"OB {'🟩' if z['dir'] == 'bull' else '🟥'} {f(z['bottom'], d)}–{f(z['top'], d)}")
        lines += ["", "<b>Kondisi entry</b>"] + [f"{mark(ok)} {escape(t)}" for ok, t in a.get("checklist", [])]
    if p["market"] == "crypto":
        lines += ["", "<b>Order flow</b>"]
        fl = a.get("flow")
        if fl:
            lines.append(f"{config.SETUP_TF} 20 candle: buy {fl['buy_pct']:.0f}% · delta {fl['delta']:+,.2f} · transaksi {fl['trades']:,}")
        bk = p.get("book")
        if bk:
            lines += [f"Spread {f(bk['spread'], d)} ({bk['spread_bps']:.2f} bps)",
                      f"Order book ±0,5%: bid {usd(bk['bid_0.5'])} vs ask {usd(bk['ask_0.5'])} → bid {bk['imb_0.5']:.0f}%",
                      f"Bid wall terbesar: {f(bk['bid_walls'][0][0], d)} ({usd(bk['bid_walls'][0][2])})",
                      f"Ask wall terbesar: {f(bk['ask_walls'][0][0], d)} ({usd(bk['ask_walls'][0][2])})"]
        tr = p.get("trades")
        if tr:
            lines.append(f"{tr['count']} transaksi terakhir: agresif beli {tr['buy_pct']:.0f}% · large ≥{usd(tr['large_threshold'])}: "
                         f"{tr['large_buy']} beli / {tr['large_sell']} jual")
    lines += ["", f"<b>Keputusan: {ICON[ov.get('signal', 'WAIT')]}</b>"] + [f"{mark(ok)} {escape(t)}" for ok, t in ov.get("reasons", [])]
    s = ov.get("setup")
    if s:
        lines += [f"Entry {f(s['entry'], d)} | SL {f(s['sl'], d)}", f"TP1 {f(s['tp1'], d)} | TP2 {f(s['tp2'], d)}"]
    lines.append(f"\n<i>{escape(ov.get('summary', ''))}</i>")
    return "\n".join(lines)


def lastupdate_text(st):
    lines = ["🕐 <b>LAST UPDATE</b>"]
    for sym, p in st["pairs"].items():
        base = "M1" if p["market"] == "crypto" else "M5"
        lines += ["", f"<b>{sym}</b> ({'Binance' if p['market'] == 'crypto' else 'RealMarketAPI'})",
                  f"Candle {base} terakhir: {wib(p.get('last_ts'))}",
                  f"Diterima pada fetch terakhir: {p.get('received', 0)} candle {base}",
                  "Tersimpan: " + " · ".join(f"{tf} {n}" for tf, n in p.get("counts", {}).items())]
    return "\n".join(lines)


def build_all(st):
    m = {"help": help_text(st), "status": status_text(st), "price": price_text(st),
         "signal": signal_text(st), "lastupdate": lastupdate_text(st)}
    for sym, p in st["pairs"].items():
        m["pair_" + sym] = pair_text(sym, p)
    url = config.DASHBOARD_URL
    m["dashboard"] = f"🌐 Dashboard: {url}" if url else "Dashboard URL belum diatur (Variable DASHBOARD_URL)."
    return m
