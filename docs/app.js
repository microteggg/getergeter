/* Forex Monitor dashboard – hanya membaca file JSON hasil engine (0 request ke RealMarketAPI). */
(function () {
  "use strict";
  const WIB = 7 * 3600;
  const TFS = ["W", "D", "H4", "H1", "M15", "M5"];
  const S = { state: null, pair: null, tf: "H1", chart: null, data: null };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fx = (v, d) => (v == null || isNaN(v) ? "-" : Number(v).toFixed(d));
  const wib = (t, withDate) => {
    if (!t) return "-";
    const d = new Date((t + WIB) * 1000);
    const p = (n) => String(n).padStart(2, "0");
    const hm = p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
    return withDate ? p(d.getUTCDate()) + "/" + p(d.getUTCMonth() + 1) + " " + hm + " WIB" : hm + " WIB";
  };
  const trendCls = (t) => (t === "Bullish" ? "up" : t === "Bearish" ? "dn" : "mut");
  const short = (t) => String(t || "-").split(" (")[0];
  const sig = (s) => `<span class="sig ${esc(s || "WAIT")}">${esc(s || "WAIT")}</span>`;

  async function getJSON(path) {
    const r = await fetch(path + "?t=" + Date.now(), { cache: "no-store" });
    if (!r.ok) throw new Error(path + " HTTP " + r.status);
    return r.json();
  }

  // ---------------- status & overview ----------------
  function renderStatus() {
    const st = S.state;
    const ageMin = Math.round((Date.now() / 1000 - st.updated_ts) / 60);
    const late = ageMin > st.interval_min * 2 + 20;
    $("age").textContent = "Data " + ageMin + " menit lalu";
    const rows = [
      ["Engine", late ? "🔴 Terlambat (" + ageMin + " mnt)" : "🟢 Running"],
      ["RealMarketAPI", st.market_closed ? "💤 Pasar tutup" : st.api_ok ? "🟢 Connected" : "🔴 Error"],
      ["Telegram", st.telegram_ok ? "🟢 Connected" : "🟠 Ada kendala kirim"],
      ["Last Update", wib(st.updated_ts, true)],
      ["Sumber data", "M5 tiap " + st.interval_min + " menit"],
      ["Next Check", "± " + wib(st.next_ts)],
      ["Strategi", st.strategy.bias + " bias → " + st.strategy.setup + " setup → " + st.strategy.confirm + " konfirmasi"],
    ];
    $("status").innerHTML = rows.map((r) => `<li><span>${esc(r[0])}</span><span>${esc(r[1])}</span></li>`).join("");
    const pct = Math.min(100, (100 * st.req_month) / (st.quota || 5000));
    $("quota-text").textContent = st.req_month + " / " + st.quota + " (" + pct.toFixed(1) + "%)";
    $("quota-bar").style.width = pct + "%";
    $("quota-bar").style.background = pct > 85 ? "var(--dn)" : pct > 65 ? "var(--wait)" : "var(--acc)";
    let w = "";
    Object.entries(st.errors || {}).forEach(([k, v]) => (w += `<div class="warn err">⚠️ ${esc(k)}: ${esc(v)}</div>`));
    (st.warnings || []).forEach((v) => (w += `<div class="warn">⚠️ ${esc(v)}</div>`));
    $("warnings").innerHTML = w;

    const tb = $("overview").querySelector("tbody");
    tb.innerHTML = Object.entries(st.pairs).map(([sym, p]) => {
      const d = digits(sym);
      const b = p.tf[st.strategy.bias] || {}, h = p.tf[st.strategy.setup] || {};
      return `<tr class="click" data-sym="${esc(sym)}" tabindex="0"><td><b>${esc(sym)}</b></td><td>${fx(p.price, d)}</td>
        <td class="${trendCls(b.trend)}">${esc(b.enough ? short(b.trend) : "data kurang")}</td>
        <td class="${trendCls(h.trend)}">${esc(short(h.trend))}</td><td>${sig(p.overall.signal)}</td></tr>`;
    }).join("");
    tb.querySelectorAll("tr").forEach((tr) => {
      const go = () => selectPair(tr.dataset.sym);
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
  }

  function digits(sym) { return sym === "XAUUSD" ? 2 : sym === "USDJPY" || sym === "XAGUSD" ? 3 : 5; }

  // ---------------- tabs ----------------
  function renderTabs() {
    const syms = Object.keys(S.state.pairs);
    $("pair-tabs").innerHTML = syms.map((s) => `<button type="button" role="tab" class="tab ${s === S.pair ? "on" : ""}" data-v="${esc(s)}">${esc(s)}</button>`).join("");
    $("tf-tabs").innerHTML = TFS.map((t) => `<button type="button" role="tab" class="tab ${t === S.tf ? "on" : ""}" data-v="${t}">${t}</button>`).join("");
    $("pair-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => selectPair(b.dataset.v)));
    $("tf-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => selectTf(b.dataset.v)));
  }
  function selectPair(s) { S.pair = s; renderTabs(); renderPair(); loadChart(); }
  function selectTf(t) { S.tf = t; renderTabs(); renderDetail(); loadChart(); }

  // ---------------- decision + detail + matrix ----------------
  function checklist(items) {
    return `<ul class="check">${(items || []).map(([ok, t]) => `<li class="${ok === null ? "na" : ok ? "ok" : "no"}">${esc(t)}</li>`).join("")}</ul>`;
  }
  function renderPair() {
    const p = S.state.pairs[S.pair], d = digits(S.pair), ov = p.overall, s = ov.setup;
    $("decision").innerHTML = `<h2>Keputusan ${esc(S.pair)}</h2>
      <div class="big">${fx(p.price, d)} &nbsp;${sig(ov.signal)}</div>
      <div class="mut">Candle M5 terakhir: ${wib(p.last_m5, true)}</div>
      ${s ? `<div class="kv"><div><small>Entry</small><b>${fx(s.entry, d)}</b></div><div><small>Stop Loss</small><b class="dn">${fx(s.sl, d)}</b></div>
        <div><small>TP1</small><b class="up">${fx(s.tp1, d)}</b></div><div><small>TP2</small><b class="up">${fx(s.tp2, d)}</b></div>
        <div><small>Zona</small><b>${esc(s.zone.kind)} ${fx(s.zone.bottom, d)}–${fx(s.zone.top, d)}</b></div></div>` : ""}
      <h2 style="margin-top:10px">Kenapa ${esc(ov.signal)}?</h2>${checklist(ov.reasons)}
      <div class="reason">${esc(ov.summary)}</div>`;
    $("matrix-title").textContent = "Multi-Timeframe " + S.pair;
    $("matrix").querySelector("tbody").innerHTML = TFS.map((tf) => {
      const a = p.tf[tf] || {};
      if (!a.enough && !a.ema20) return `<tr class="click" data-tf="${tf}"><td><b>${tf}</b></td><td colspan="7" class="mut">Data belum cukup (${a.bars || 0}/${a.need || 60} candle) – terisi otomatis seiring waktu</td></tr>`;
      const e = (a.events || []).slice(-1)[0];
      const lc = a.last_candle || {};
      return `<tr class="click" data-tf="${tf}" tabindex="0"><td><b>${tf}</b></td><td class="${trendCls(a.trend)}">${esc(short(a.trend))}</td>
        <td>${e ? `<span class="${e.dir === "bull" ? "up" : "dn"}">${esc(e.type)} ${e.dir === "bull" ? "▲" : "▼"}</span> ${fx(e.level, d)}` : "-"}</td>
        <td>${fx(a.ema20, d)}</td><td>${fx(a.ema50, d)}</td><td>${a.rsi ?? "-"}</td><td>${sig(a.signal)}</td>
        <td class="${lc.dir === "Bullish" ? "up" : "dn"}">${esc(lc.dir || "-")} ${wib(lc.t)}</td></tr>`;
    }).join("");
    $("matrix").querySelectorAll("tr[data-tf]").forEach((tr) => tr.addEventListener("click", () => selectTf(tr.dataset.tf)));
    renderDetail();
  }
  function renderDetail() {
    const p = S.state.pairs[S.pair], d = digits(S.pair), a = p.tf[S.tf] || {};
    if (!a.ema20 && !a.events) {
      $("tf-detail").innerHTML = `<h2>Detail ${esc(S.pair)} ${S.tf}</h2><p>${esc(a.reason || "Data belum cukup.")}</p>`;
      return;
    }
    const lc = a.last_candle || {};
    const ev = (a.events || []).slice(-4).reverse().map((e) => `<li class="${e.dir === "bull" ? "up" : "dn"}">${esc(e.type)} ${e.dir === "bull" ? "bullish" : "bearish"} @ ${fx(e.level, d)} <span class="mut">(${wib(e.t, true)})</span></li>`).join("");
    const zones = (arr, k) => (arr || []).map((z) => `<li class="${z.dir === "bull" ? "up" : "dn"}">${k} ${z.dir === "bull" ? "bullish" : "bearish"} ${fx(z.bottom, d)} – ${fx(z.top, d)}</li>`).join("");
    const s = a.setup;
    $("tf-detail").innerHTML = `<h2>Detail ${esc(S.pair)} ${S.tf} ${sig(a.signal)}</h2>
      <div class="kv">
        <div><small>Harga</small><b>${fx(a.price, d)}</b></div>
        <div><small>Trend</small><b class="${trendCls(a.trend)}">${esc(short(a.trend))}</b></div>
        <div><small>EMA20</small><b>${fx(a.ema20, d)}</b></div><div><small>EMA50</small><b>${fx(a.ema50, d)}</b></div>
        <div><small>RSI 14</small><b>${a.rsi ?? "-"}</b></div><div><small>ATR 14</small><b>${fx(a.atr, d)}</b></div>
        <div><small>Swing High</small><b>${fx(a.swing_high, d)}</b></div><div><small>Swing Low</small><b>${fx(a.swing_low, d)}</b></div>
      </div>
      <div class="mut">Candle terakhir close (${wib(lc.t, true)}): O ${fx(lc.o, d)} · H ${fx(lc.h, d)} · L ${fx(lc.l, d)} · C ${fx(lc.c, d)} – <b class="${lc.dir === "Bullish" ? "up" : "dn"}">${esc(lc.dir)}</b></div>
      <h2 style="margin-top:10px">Struktur & Zona</h2>
      <ul class="zones">${ev || "<li>Belum ada BOS/CHoCH</li>"}${zones(a.fvg, "FVG")}${zones(a.ob, "OB")}</ul>
      ${s ? `<div class="kv"><div><small>Entry</small><b>${fx(s.entry, d)}</b></div><div><small>SL</small><b class="dn">${fx(s.sl, d)}</b></div><div><small>TP1</small><b class="up">${fx(s.tp1, d)}</b></div><div><small>TP2</small><b class="up">${fx(s.tp2, d)}</b></div></div>` : ""}
      <h2 style="margin-top:10px">Kondisi Entry ${S.tf}</h2>${checklist(a.checklist)}
      <div class="reason">${esc(a.reason)}</div>`;
  }

  // ---------------- chart ----------------
  async function loadChart() {
    const key = S.pair + "_" + S.tf;
    try {
      S.data = await getJSON("data/chart_" + key + ".json");
      drawChart();
    } catch (e) {
      $("chart").innerHTML = `<p class="mut">Chart belum tersedia (${esc(e.message)}).</p>`;
    }
  }
  function drawChart() {
    const box = $("chart"), png = $("chart-png");
    if (typeof LightweightCharts === "undefined") {          // CDN gagal -> pakai gambar PNG dari engine
      box.hidden = true; png.hidden = false;
      png.src = "charts/" + S.pair + "_" + S.tf + ".png?t=" + Date.now();
      return;
    }
    box.hidden = false; png.hidden = true;
    if (S.chart) { S.chart.remove(); S.chart = null; }
    box.innerHTML = "";
    const dark = !window.matchMedia("(prefers-color-scheme: light)").matches;
    const D = S.data, d = D.digits;
    const chart = LightweightCharts.createChart(box, {
      autoSize: true,
      layout: { background: { type: "solid", color: dark ? "#111a2e" : "#ffffff" }, textColor: dark ? "#cbd5e1" : "#334155" },
      grid: { vertLines: { color: dark ? "#1b2540" : "#eef2f7" }, horzLines: { color: dark ? "#1b2540" : "#eef2f7" } },
      timeScale: { timeVisible: !["D", "W"].includes(S.tf), secondsVisible: false, rightOffset: 6 },
      rightPriceScale: { borderVisible: false },
    });
    S.chart = chart;
    const cs = chart.addCandlestickSeries({
      upColor: "#22c55e", downColor: "#ef4444", wickUpColor: "#22c55e", wickDownColor: "#ef4444", borderVisible: false,
      priceFormat: { type: "price", precision: d, minMove: Math.pow(10, -d) },
    });
    cs.setData(D.candles.map((b) => ({
      time: b.t + WIB, open: b.o, high: b.h, low: b.l, close: b.c,
      ...(b.forming ? { color: b.c >= b.o ? "rgba(34,197,94,.35)" : "rgba(239,68,68,.35)", wickColor: "#64748b" } : {}),
    })));
    const t0 = D.candles.length ? D.candles[0].t : 0;
    if ($("t-ema").checked) {
      [["ema20", "#f59e0b"], ["ema50", "#38bdf8"]].forEach(([k, c]) => {
        const ls = chart.addLineSeries({ color: c, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        ls.setData((D[k] || []).filter((x) => x[0] >= t0).map((x) => ({ time: x[0] + WIB, value: x[1] })));
      });
    }
    const L = LightweightCharts.LineStyle;
    const line = (price, color, title, style) => cs.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
    if ($("t-zone").checked) {
      const zs = [...(D.fvg || []).map((z) => ({ ...z, k: "FVG" })), ...(D.ob || []).map((z) => ({ ...z, k: "OB" }))];
      const last = D.candles.length ? D.candles[D.candles.length - 1].c : 0;
      zs.sort((a, b) => Math.abs((a.top + a.bottom) / 2 - last) - Math.abs((b.top + b.bottom) / 2 - last));
      zs.slice(0, 3).forEach((z) => {
        const c = z.dir === "bull" ? "rgba(34,197,94,.75)" : "rgba(239,68,68,.75)";
        line(z.top, c, z.k + (z.dir === "bull" ? "▲" : "▼"), L.Dotted);
        line(z.bottom, c, "", L.Dotted);
      });
    }
    if ($("t-setup").checked && D.setup) {
      line(D.setup.entry, "#e2e8f0", "ENTRY", L.Solid);
      line(D.setup.sl, "#ef4444", "SL", L.Dashed);
      line(D.setup.tp1, "#22c55e", "TP1", L.Dashed);
      line(D.setup.tp2, "#22c55e", "TP2", L.Dashed);
    }
    const marks = [];
    if ($("t-struct").checked) {
      (D.events || []).filter((e) => e.t >= t0).forEach((e) => marks.push({
        time: e.t + WIB, position: e.dir === "bull" ? "aboveBar" : "belowBar",
        color: e.dir === "bull" ? "#22c55e" : "#ef4444", shape: "circle", text: e.type,
      }));
    }
    if ((D.signal === "BUY" || D.signal === "SELL") && D.candles.length) {
      const lt = D.candles.filter((b) => !b.forming).slice(-1)[0] || D.candles[D.candles.length - 1];
      marks.push(D.signal === "BUY"
        ? { time: lt.t + WIB, position: "belowBar", color: "#22c55e", shape: "arrowUp", text: "BUY" }
        : { time: lt.t + WIB, position: "aboveBar", color: "#ef4444", shape: "arrowDown", text: "SELL" });
    }
    marks.sort((a, b) => a.time - b.time);
    cs.setMarkers(marks);
    chart.timeScale().fitContent();
  }

  // ---------------- load ----------------
  async function load() {
    try {
      S.state = await getJSON("data/state.json");
    } catch (e) {
      $("age").textContent = "Gagal memuat data";
      $("status").innerHTML = `<li><span>Error</span><span>${esc(e.message)}</span></li>`;
      return;
    }
    const syms = Object.keys(S.state.pairs);
    if (!syms.length) { $("age").textContent = "Belum ada data"; return; }
    if (!S.pair || !S.state.pairs[S.pair]) S.pair = syms[0];
    renderStatus(); renderTabs(); renderPair(); loadChart();
  }
  ["t-ema", "t-zone", "t-struct", "t-setup"].forEach((id) => $(id).addEventListener("change", () => S.data && drawChart()));
  $("refresh").addEventListener("click", load);
  load();
  setInterval(load, 5 * 60 * 1000);   // file JSON statis, aman di-refresh
})();
