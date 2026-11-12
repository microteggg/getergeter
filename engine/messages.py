"""Teks balasan command Telegram. Disimpan di state.json -> Worker cukup membaca (0 request API)."""
from datetime import datetime, timezone, timedelta
from html import escape

import config

WIB = timezone(timedelta(hours=7))
ICON = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "WAIT": "⏳ WAIT", "MONITOR": "👁 MONITOR"}
PAIR_ICON = {"XAUUSD": "🟡", "BTCUSDT": "🟠", "ETHUSDT": "🟣", "BNBUSDT": "🟨", "XRPUSDT": "⚫", "SOLUSDT": "🟪",
             "TRXUSDT": "🔺", "DOGEUSDT": "🐕", "ZECUSDT": "🛡", "LINKUSDT": "🔗", "ADAUSDT": "🔷"}


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
    tr = (tr or "")
    return "🟢" if tr.startswith("Bullish") else "🔴" if tr.startswith("Bearish") else "⚪"


def short(tr):
    return (tr or "-").split(" (")[0]


def mark(ok):
    return "✅" if ok else "➖" if ok is None else "❌"


def help_text(st):
    lines = ["🤖 <b>Daftar command</b>", "",
             "/status – cek sistem & kuota", "/price – harga terakhir",
             "/signal – " + ("sinyal" if config.SIGNALS_ON else "ringkasan monitoring + Auto Fibonacci"),
             "/events – kejadian/setup terakhir (mode monitoring)",
             "/lastupdate – data terakhir yang diambil", "/riwayat – statistik riwayat entry (backtest)", ""]
    for s in st["pairs"]:
        lines.append(f"/{s.lower()} – analisis {s}" + (" + order flow" if st["pairs"][s]["market"] == "crypto" else ""))
        lines.append(f"/chart{s.lower()} – chart {s} (contoh: /chart{s.lower()} h4)")
    lines += ["", "/dashboard – link dashboard", "/help – daftar command", "",
              "ℹ️ Jawaban diambil dari hasil analisis terakhir (tanpa request tambahan ke API)."]
    return "\n".join(lines)


def status_text(st):
    fx, cr = st["sources"]["forex"], st["sources"]["crypto"]
    lines = ["📡 <b>SYSTEM STATUS</b>", "🟢 Engine berjalan (GitHub Actions, cron 5 menit)", ""]
    if fx["symbols"]:
        lines += ["<b>Forex XAUUSD – RealMarketAPI</b>",
                  f"{'🟢' if fx['ok'] else '🔴'} {'Connected' if fx['ok'] else 'ERROR'}" + (" · 💤 pasar tutup" if fx.get("market_closed") else ""),
                  f"M5 tiap {fx.get('interval_min') or '-'} menit (normal {fx['fast_min']}/{fx['slow_min']}) · fetch terakhir {wib(fx.get('fetch_ts'), '%H:%M')}",
                  f"Next ±{wib(fx.get('next_ts'), '%H:%M')}",
                  f"Kuota {fx.get('req_month', 0)} / {fx['quota']} · proyeksi akhir bulan ±{fx.get('projected_month', '-')}", ""]
    if cr["symbols"]:
        lines += ["<b>Crypto – Binance Spot</b>",
                  f"{'🟢' if cr['ok'] else '🔴'} {'Connected' if cr['ok'] else 'ERROR'} ({escape(cr.get('host') or '-')})",
                  f"{len(cr['symbols'])} pair · M1 + order book + transaksi tiap {cr['interval_min']} menit · {wib(cr.get('fetch_ts'), '%H:%M')}", ""]
    tg = st.get("telegram") or {}
    if not tg.get("configured", True):
        lines.append("🔴 Telegram: token/chat id TIDAK terbaca oleh engine (notif otomatis tidak terkirim)")
    elif tg.get("pending_retry"):
        lines.append(f"🟠 Telegram: {tg['pending_retry']} pesan menunggu dikirim ulang · error: {escape(str(tg.get('last_error') or '-'))}")
    else:
        lines.append(f"🟢 Telegram Connected · notif terakhir terkirim {wib(tg.get('last_sent_ts'))}")
    if tg:
        lines.append(f"Run terakhir: {tg.get('sent', 0)} terkirim · {tg.get('failed', 0)} gagal")
        if tg.get("notify_types"):
            lines.append(f"Mode MONITORING · notif: {', '.join(tg['notify_types'])}")
        elif tg.get("zone_tfs"):
            lines.append(f"Notif zona: TF {', '.join(tg.get('zone_tfs') or [])} · tahap {', '.join(tg.get('zone_stages') or [])}")
    run = st.get("run") or {}
    if run:
        lines.append(f"Run engine: trigger {run.get('trigger')} · {run.get('duration_sec')} dtk · "
                     f"jeda rata-rata {run.get('avg_gap_min') or '-'} mnt")
    lines.append(f"Last Update: {wib(st.get('updated_ts'))}")
    for sym, err in (st.get("errors") or {}).items():
        lines.append(f"⚠️ {sym}: {escape(err)}")
    for w in st.get("warnings", []):
        lines.append(f"⚠️ {escape(w)}")
    return "\n".join(lines)


def price_text(st):
    lines = ["💰 <b>HARGA TERAKHIR</b>"]
    for sym, p in st["pairs"].items():
        extra = ""
        if p.get("ticker"):
            ch = p["ticker"]["change_pct"]
            extra = f"  {'🟢' if ch >= 0 else '🔴'} {ch:+.2f}% 24j"
        lines.append(f"{PAIR_ICON.get(sym, '•')} {sym}: <b>{f(p.get('price'), p['digits'])}</b>  ({wib(p.get('last_ts'), '%H:%M')}){extra}")
    lines.append(f"\n🕐 Update: {wib(st.get('updated_ts'))}")
    lines.append("XAUUSD = close M5 terakhir · Crypto = close M1 terakhir (real-time crypto ada di dashboard).")
    return "\n".join(lines)


def _setup_lines(s, d):
    return [f"Model: {escape(s['model'])} · RR {s['rr']}",
            f"Entry {f(s['entry'], d)} · SL {f(s['sl'], d)} · TP1 {f(s['tp1'], d)} · TP2 {f(s['tp2'], d)}"]


def fib_line(fb, d, prefix=""):
    if not fb:
        return f"{prefix}Fib: belum ada impuls valid"
    bull = fb["dir"] == "bull"
    g = fb.get("gold") or [None, None]
    status = str(fb.get("status") or "FORMING").upper()
    ext = ""
    if status == "EXTENDED":
        ext = " · EXT " + ", ".join(f"{x['notation']}={f(x['price'], d)}" for x in (fb.get("extensions") or []))
    prev = " · Fib sebelumnya tersimpan" if fb.get("previous") else ""
    cluster = " · " + ", ".join(z.get("label", "Fib cluster") for z in fb.get("clusters") or []) if fb.get("clusters") else ""
    return (f"{prefix}Fib {fb['tf']} {'🟢 bullish' if bull else '🔴 bearish'} [{status}] ({fb['scope']}): "
            f"100% {f(fb['anchor100']['price'], d)} → 1.0/0% {f(fb['anchor0']['price'], d)} · "
            f"retrace {fb.get('retrace') if fb.get('retrace') is not None else '-'}%"
            f" ({fb.get('zone') or '-'}) · zona 50–61.8 {f(g[0], d)}–{f(g[1], d)}{ext}{prev}{cluster}")


def monitor_text(st):
    lines = ["👁 <b>MONITORING (sinyal BUY/SELL nonaktif)</b>"]
    for sym, p in st["pairs"].items():
        d = p["digits"]
        mtf = (st.get("monitor") or {}).get("tf", "M15")
        loc = (p["tf"].get(mtf) or {}).get("fib")
        lines += ["", f"{PAIR_ICON.get(sym, '•')} <b>{sym}</b>  {f(p.get('price'), d)}",
                  fib_line(p.get("fib_primary"), d), fib_line(loc, d)]
        mon = p.get("monitor")
        if mon and mon.get("last"):
            r = mon["last"][-1]
            lines.append(f"Kejadian terakhir: {escape(r['title'])} ({wib(r['close_t'], '%d/%m %H:%M')})")
    return "\n".join(lines)


def events_text(st, k=12):
    lines = ["📋 <b>KEJADIAN TERAKHIR</b> (cek manual – bukan sinyal)"]
    any_ = False
    for sym, p in st["pairs"].items():
        mon = p.get("monitor")
        if not mon or not mon.get("last"):
            continue
        any_ = True
        lines += ["", f"<b>{sym} • {mon['tf']}</b> · total {mon['total']} kejadian"]
        for r in reversed(mon["last"][-k:]):
            arrow = "🟢" if r["dir"] == "bull" else "🔴"
            lines.append(f"{wib(r['close_t'], '%d/%m %H:%M')} {arrow} {escape(r['title'])} @ {f(r['price'], p['digits'])}"
                         f" · notif {r.get('notif', '-')}")
    if not any_:
        lines.append("Belum ada kejadian tercatat.")
    lines.append("\nDetail lengkap: dashboard → panel Monitoring Events.")
    return "\n".join(lines)


def signal_text(st):
    if not config.SIGNALS_ON:
        return monitor_text(st)
    lines = ["📊 <b>CURRENT SIGNAL</b>"]
    for sym, p in st["pairs"].items():
        d, ov = p["digits"], p.get("overall", {})
        b = p["tf"].get(config.BIAS_TF, {})
        h = p["tf"].get(config.SETUP_TF, {})
        lines += ["", f"{PAIR_ICON.get(sym, '•')} <b>{sym}</b>  {f(p.get('price'), d)}",
                  f"Bias {config.BIAS_TF}: {trend_icon(b.get('trend'))} {short(b.get('trend'))} · "
                  f"Setup {config.SETUP_TF}: {ICON[h.get('signal', 'WAIT')]}",
                  f"Keputusan: <b>{ICON[ov.get('signal', 'WAIT')]}</b>"]
        if ov.get("setup"):
            lines += _setup_lines(ov["setup"], d)
        lines.append(f"<i>{escape(ov.get('summary', ''))}</i>")
    return "\n".join(lines)


def pair_text(sym, p):
    d, ov = p["digits"], p.get("overall", {})
    lines = [f"{PAIR_ICON.get(sym, '•')} <b>{sym} – DETAIL ANALISIS</b>",
             f"Harga: <b>{f(p.get('price'), d)}</b>  ({wib(p.get('last_ts'), '%H:%M')})", "", "<b>Multi-timeframe</b>"]
    if p.get("ticker"):
        t = p["ticker"]
        lines.insert(2, f"24j: {t['change_pct']:+.2f}% · H {f(t['high'], d)} · L {f(t['low'], d)} · Vol {usd(t['quote_volume'])}")
    for tf in ["W", "D", "H4", "H1", "M15", "M5"] + (["M1"] if p["market"] == "crypto" else []):
        a = p["tf"].get(tf, {})
        if not a.get("enough"):
            lines.append(f"{tf:>3}: ⚪ data belum cukup ({a.get('bars', 0)}/{a.get('need', '-')} candle)")
            continue
        fb = a.get("fib")
        tail = (ICON.get(a.get("signal", "WAIT"), "") if config.SIGNALS_ON else
                f"Fib {fb['retrace']}%" if fb and fb.get("retrace") is not None else "Fib -")
        lines.append(f"{tf:>3}: {trend_icon(a.get('trend'))} {short(a.get('trend'))} | RSI {a.get('rsi')} | {tail}")
    a = p["tf"].get(config.SETUP_TF, {})
    if a.get("enough"):
        lc = a["last_candle"]
        lines += ["", f"<b>{config.SETUP_TF} detail</b>",
                  f"MA20 {f(a.get('ma20'), d)} · MA50 {f(a.get('ma50'), d)} · MA200 {f(a.get('ma200'), d)}",
                  f"RSI {a['rsi']} · ATR14 {f(a['atr14'], d)} · Lokasi {a.get('premium_discount') or '-'}",
                  f"Candle {wib(lc['t'], '%H:%M')}: O {f(lc['o'], d)} H {f(lc['h'], d)} L {f(lc['l'], d)} C {f(lc['c'], d)} – {lc['dir']}"]
        if a["events"]:
            e = a["events"][-1]
            lines.append(f"Struktur: {e['label']} {'bullish' if e['dir'] == 'bull' else 'bearish'} @ {f(e['level'], d)} ({wib(e['t'], '%d/%m %H:%M')})")
        if a.get("sweeps"):
            s = a["sweeps"][-1]
            lines.append(f"Sweep {s['side']} @ {f(s['level'], d)} ({wib(s['t'], '%d/%m %H:%M')})")
        for z in a.get("fvg", [])[-2:]:
            lines.append(f"FVG {'🟦' if z['dir'] == 'bull' else '🟧'} {f(z['bottom'], d)}–{f(z['top'], d)} · {z['status']}")
        for z in a.get("ob", [])[-2:]:
            lines.append(f"OB {'🟩' if z['dir'] == 'bull' else '🟥'} {f(z['bottom'], d)}–{f(z['top'], d)} · {z['status']}")
        if config.SIGNALS_ON:
            lines += ["", "<b>Kondisi setup</b>"] + [f"{mark(ok)} {escape(t)}" for ok, t in a.get("checklist", [])]
    zl = []
    for tf in (dict.fromkeys((config.CONFIRM_TF, config.SETUP_TF, config.BIAS_TF)) if config.SIGNALS_ON else []):
        for z in ((p["tf"].get(tf) or {}).get("zones") or [])[-3:]:
            ic = "🔵" if z["dir"] == "bull" else "🟠"
            md = f" · {z['model_name']}" if z.get("model_name") else ""
            zl.append(f"{ic} {tf} {z['side']} {z['stage']} {z['score']}/10{md} · {f(z['bottom'], d)}–{f(z['top'], d)}"
                      f" · invalid {f(z['invalidation'], d)}")
    if zl:
        lines += ["", "<b>Zona BUY/SELL aktif</b>"] + zl
    if p["market"] == "crypto":
        lines += ["", "<b>Order flow</b> (info, tidak memengaruhi sinyal)"]
        fl = a.get("flow")
        if fl:
            lines.append(f"{config.SETUP_TF} 20 candle: buy {fl['buy_pct']:.0f}% · delta {fl['delta']:+,.2f} · transaksi {fl['trades']:,}")
        bk = p.get("book")
        if bk:
            lines += [f"Order book ±0,5%: bid {usd(bk['bid_0.5'])} vs ask {usd(bk['ask_0.5'])} → bid {bk['imb_0.5']:.0f}%",
                      f"Bid wall: {f(bk['bid_walls'][0][0], d)} ({usd(bk['bid_walls'][0][2])}) · "
                      f"Ask wall: {f(bk['ask_walls'][0][0], d)} ({usd(bk['ask_walls'][0][2])})"]
    js = (p.get("journal") or {}).get("entries")
    if js:
        lines += ["", f"<b>Riwayat entry</b>: {js['total']} · selesai {js['closed']} · winrate "
                      f"{js['winrate'] if js['winrate'] is not None else '-'}% · net {js['net_r']:+}R"]
    lines += ["", "<b>Auto Fibonacci</b>", fib_line(p.get("fib_primary"), d),
              fib_line((p["tf"].get(config.MONITOR_TF) or {}).get("fib"), d)]
    if not config.SIGNALS_ON:
        lines += ["", "👁 <b>Mode monitoring</b> – sinyal BUY/SELL nonaktif. Lihat /events."]
        return "\n".join(lines)
    lines += ["", f"<b>Keputusan: {ICON[ov.get('signal', 'WAIT')]}</b>"] + [f"{mark(ok)} {escape(t)}" for ok, t in ov.get("reasons", [])]
    if ov.get("setup"):
        lines += _setup_lines(ov["setup"], d)
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


def history_text(st):
    lines = ["📒 <b>RIWAYAT ENTRY (BACKTEST)</b>",
             f"Model {config.BIAS_TF}→{config.SETUP_TF}→{config.CONFIRM_TF} · posisi penuh ke TP2 / SL (−1R)", ""]
    for sym, p in st["pairs"].items():
        js = (p.get("journal") or {}).get("entries") or {}
        if not js.get("total"):
            lines.append(f"{PAIR_ICON.get(sym, '•')} {sym}: belum ada entry")
            continue
        lines.append(f"{PAIR_ICON.get(sym, '•')} <b>{sym}</b>: {js['total']} entry · {js['closed']} selesai · WR "
                     f"{js['winrate'] if js['winrate'] is not None else '-'}% · net {js['net_r']:+}R · PF {js['pf'] or '-'}")
    lines.append("\nDetail tiap sinyal ada di dashboard (panel Riwayat Sinyal & Backtest).")
    return "\n".join(lines)


def build_all(st):
    m = {"help": help_text(st), "status": status_text(st), "price": price_text(st),
         "signal": signal_text(st), "lastupdate": lastupdate_text(st), "riwayat": history_text(st),
         "events": events_text(st)}
    for sym, p in st["pairs"].items():
        m["pair_" + sym] = pair_text(sym, p)
    url = config.DASHBOARD_URL
    m["dashboard"] = f"🌐 Dashboard: {url}" if url else "Dashboard URL belum diatur (Variable DASHBOARD_URL)."
    return m
