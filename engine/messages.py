"""Menyusun teks balasan untuk semua command Telegram.
Teks disimpan di state.json sehingga Cloudflare Worker cukup membaca file (0 request ke RealMarketAPI)."""
from datetime import datetime, timezone, timedelta
from html import escape

import config

WIB = timezone(timedelta(hours=7))
ICON = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "WAIT": "⏳ WAIT"}
PAIR_ICON = {"XAUUSD": "🟡", "EURUSD": "🔵"}


def wib(t, fmt="%d %b %H:%M"):
    return datetime.fromtimestamp(t, WIB).strftime(fmt) + " WIB" if t else "-"


def f(x, d):
    return "-" if x is None else f"{x:.{d}f}"


def trend_icon(tr):
    return "🟢" if tr == "Bullish" else "🔴" if tr == "Bearish" else "⚪"


def help_text(symbols):
    lines = ["🤖 <b>Daftar command</b>", "",
             "/status – cek sistem hidup", "/price – harga terakhir", "/signal – sinyal semua pair",
             "/lastupdate – candle terakhir yang berhasil diambil"]
    for s in symbols:
        lines.append(f"/{s.lower()} – detail analisis {s}")
        lines.append(f"/chart{s.lower()} – chart {s} (H1). Bisa: /chart{s.lower()} h4")
    lines += ["/dashboard – link dashboard", "/help – daftar command", "",
              "ℹ️ Semua jawaban diambil dari hasil analisis terakhir (tanpa request baru ke RealMarketAPI)."]
    return "\n".join(lines)


def status_text(st):
    api_ok = st.get("api_ok")
    tg_ok = st.get("telegram_ok", True)
    lines = ["📡 <b>SYSTEM STATUS</b>",
             "🟢 Engine berjalan (GitHub Actions, tiap ±30 menit)",
             f"{'🟢' if api_ok else '🔴'} RealMarketAPI {'Connected' if api_ok else 'ERROR'}",
             f"{'🟢' if tg_ok else '🟠'} Telegram {'Connected' if tg_ok else 'ada kendala kirim'}",
             "", f"Last Update: {wib(st.get('updated_ts'), '%H:%M')} ({wib(st.get('updated_ts'), '%d %b')[:6]})",
             f"Sumber data: M5 diambil tiap {config.FETCH_INTERVAL_MIN} menit",
             "Timeframe: M5 · M15 · H1 · H4 · D · W (dibentuk dari M5)",
             f"Next Check: ±{wib(st.get('next_ts'), '%H:%M')}",
             f"Request API bulan ini: {st.get('req_month', 0)} / {config.MONTHLY_QUOTA}"]
    if st.get("market_closed"):
        lines.append("💤 Pasar forex tutup (akhir pekan) – fetch dilewati untuk hemat kuota")
    for sym, err in (st.get("errors") or {}).items():
        lines.append(f"⚠️ {sym}: {escape(err)}")
    for w in st.get("warnings", []):
        lines.append(f"⚠️ {escape(w)}")
    return "\n".join(lines)


def price_text(st):
    lines = ["💰 <b>HARGA TERAKHIR</b>"]
    for sym, p in st["pairs"].items():
        d = config.digits(sym)
        lines.append(f"{PAIR_ICON.get(sym, '•')} {sym}: <b>{f(p.get('price'), d)}</b>  ({wib(p.get('last_m5'), '%H:%M')})")
    lines.append(f"\n🕐 Data terakhir: {wib(st.get('updated_ts'))}")
    lines.append("Harga = close candle M5 terakhir (bukan tick real-time).")
    return "\n".join(lines)


def signal_text(st):
    lines = ["📊 <b>CURRENT SIGNAL</b>"]
    for sym, p in st["pairs"].items():
        d = config.digits(sym)
        ov = p.get("overall", {})
        h1 = p["tf"].get(config.SETUP_TF, {})
        lines += ["", f"{PAIR_ICON.get(sym, '•')} <b>{sym}</b>  {f(p.get('price'), d)}",
                  f"Bias {config.BIAS_TF}: {trend_icon(p['tf'].get(config.BIAS_TF, {}).get('trend'))} {p['tf'].get(config.BIAS_TF, {}).get('trend', '-')}",
                  f"Trend {config.SETUP_TF}: {trend_icon(h1.get('trend'))} {h1.get('trend', '-')}",
                  f"Signal: <b>{ICON[ov.get('signal', 'WAIT')]}</b>"]
        s = ov.get("setup")
        if s:
            lines += [f"Entry: {f(s['entry'], d)}", f"SL: {f(s['sl'], d)}", f"TP1: {f(s['tp1'], d)} | TP2: {f(s['tp2'], d)}"]
        lines.append(f"<i>{escape(ov.get('summary', ''))}</i>")
    return "\n".join(lines)


def pair_text(sym, p):
    d = config.digits(sym)
    ov = p.get("overall", {})
    lines = [f"{PAIR_ICON.get(sym, '•')} <b>{sym} – DETAIL ANALISIS</b>",
             f"Harga: <b>{f(p.get('price'), d)}</b>  ({wib(p.get('last_m5'), '%H:%M')})", "",
             "<b>Multi-timeframe</b>"]
    for tf in ["W", "D", "H4", "H1", "M15", "M5"]:
        a = p["tf"].get(tf, {})
        if not a.get("enough"):
            lines.append(f"{tf:>3}: ⚪ data belum cukup ({a.get('bars', 0)} candle)")
            continue
        lines.append(f"{tf:>3}: {trend_icon(a.get('trend'))} {a.get('trend', '-').split(' (')[0]} | RSI {a.get('rsi')} | {ICON[a.get('signal', 'WAIT')]}")
    a = p["tf"].get(config.SETUP_TF, {})
    if a.get("enough"):
        lc = a["last_candle"]
        lines += ["", f"<b>{config.SETUP_TF} detail</b>",
                  f"EMA{config.EMA_FAST}: {f(a['ema20'], d)} | EMA{config.EMA_SLOW}: {f(a['ema50'], d)}",
                  f"RSI: {a['rsi']} | ATR: {f(a['atr'], d)}",
                  f"Candle terakhir ({wib(lc['t'], '%H:%M')}): O {f(lc['o'], d)} H {f(lc['h'], d)} L {f(lc['l'], d)} C {f(lc['c'], d)} – {lc['dir']}"]
        if a["events"]:
            e = a["events"][-1]
            lines.append(f"Struktur: {e['type']} {'bullish' if e['dir'] == 'bull' else 'bearish'} @ {f(e['level'], d)} ({wib(e['t'], '%d/%m %H:%M')})")
        for z in a.get("fvg", [])[-2:]:
            lines.append(f"FVG {'🟢' if z['dir'] == 'bull' else '🔴'} {f(z['bottom'], d)}–{f(z['top'], d)}")
        for z in a.get("ob", [])[-2:]:
            lines.append(f"OB {'🟢' if z['dir'] == 'bull' else '🔴'} {f(z['bottom'], d)}–{f(z['top'], d)}")
        lines += ["", "<b>Kondisi entry</b>"]
        for ok, txt in a.get("checklist", []):
            lines.append(f"{'✅' if ok else '❌'} {escape(txt)}")
    lines += ["", f"<b>Keputusan: {ICON[ov.get('signal', 'WAIT')]}</b>"]
    for ok, txt in ov.get("reasons", []):
        lines.append(f"{'✅' if ok else '➖' if ok is None else '❌'} {escape(txt)}")
    s = ov.get("setup")
    if s:
        lines += [f"Entry {f(s['entry'], d)} | SL {f(s['sl'], d)}", f"TP1 {f(s['tp1'], d)} | TP2 {f(s['tp2'], d)}"]
    lines.append(f"\n<i>{escape(ov.get('summary', ''))}</i>")
    return "\n".join(lines)


def lastupdate_text(st):
    lines = ["🕐 <b>LAST UPDATE</b>"]
    for sym, p in st["pairs"].items():
        lines += ["", f"<b>{sym}</b>",
                  f"Candle M5 terakhir: {wib(p.get('last_m5'))}",
                  f"Diterima pada fetch terakhir: {p.get('received', 0)} candle M5",
                  f"Candle {config.SETUP_TF} close terakhir: {wib(p['tf'].get(config.SETUP_TF, {}).get('last_candle', {}).get('t'))}",
                  "Jumlah candle tersimpan: " + " · ".join(f"{tf} {p.get('counts', {}).get(tf, 0)}" for tf in config.TIMEFRAMES)]
    lines.append(f"\nFetch terakhir: {wib(st.get('fetch_ts'))}")
    return "\n".join(lines)


def build_all(st):
    m = {"help": help_text(list(st["pairs"].keys())), "status": status_text(st), "price": price_text(st),
         "signal": signal_text(st), "lastupdate": lastupdate_text(st)}
    for sym, p in st["pairs"].items():
        m["pair_" + sym] = pair_text(sym, p)
    url = config.DASHBOARD_URL or st.get("dashboard_url", "")
    m["dashboard"] = f"🌐 Dashboard: {url}" if url else "Dashboard URL belum diatur (Variable DASHBOARD_URL)."
    return m
