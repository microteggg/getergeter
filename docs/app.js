/* Market Monitor – chart canvas gaya Pine/TradingView.
   Forex & crypto dibaca dari file JSON hasil engine (0 request ke RealMarketAPI).
   Crypto: tambahan data LIVE langsung dari WebSocket publik Binance di browser (order book, transaksi, candle M1). */
(function () {
  "use strict";
  const WIB = 7 * 3600;
  const TF_SEC = { M1: 60, M5: 300, M15: 900, H1: 3600, H4: 14400, D: 86400, W: 604800 };
  const WS_HOSTS = ["wss://data-stream.binance.vision/stream", "wss://stream.binance.com:9443/stream"];
  const C = {
    bg: "#131722", grid: "#1e222d", axis: "#787b86", fg: "#d1d4dc", up: "#26a69a", dn: "#ef5350",
    obBull: "rgba(8,153,129,.28)", obBear: "rgba(136,14,79,.40)", obBullL: "#089981", obBearL: "#c2185b",
    fvgBull: "rgba(41,98,255,.22)", fvgBear: "rgba(255,152,0,.22)", fvgBullL: "#2962ff", fvgBearL: "#ff9800",
    sweep: "#ffeb3b", ema20: "#f7c548", ema50: "#2962ff", ema200: "#e040fb", cvd: "#ffb74d",
  };
  const AXIS_W = 74, TIME_H = 24;
  const S = { state: null, pair: null, tf: "H1", data: null, idx: null, sp: 9, off: 8, hover: null, show: {},
    live: { ws: null, sym: null, ok: false, book: null, tape: [], buy: 0, sell: 0, since: 0, lastMin: null, tries: 0, host: 0 } };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fx = (v, d) => (v == null || !isFinite(v) ? "-" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const fq = (v) => (v == null ? "-" : v >= 1000 ? fx(v, 0) : v >= 1 ? fx(v, 3) : fx(v, 5));
  const usd = (x) => { if (x == null) return "-"; const a = Math.abs(x); return a >= 1e9 ? "$" + (x / 1e9).toFixed(2) + "B" : a >= 1e6 ? "$" + (x / 1e6).toFixed(2) + "M" : a >= 1e3 ? "$" + (x / 1e3).toFixed(1) + "K" : "$" + x.toFixed(0); };
  const short = (t) => String(t || "-").split(" (")[0];
  const sig = (s) => `<span class="sig ${esc(s || "WAIT")}">${esc(s || "WAIT")}</span>`;
  const cls = (t) => (t === "Bullish" || t === "bull" ? "up" : t === "Bearish" || t === "bear" ? "dn" : "");
  const P = () => S.state.pairs[S.pair];
  const isCrypto = () => P() && P().market === "crypto";
  function wib(t, mode) {
    if (!t) return "-";
    const d = new Date((t + WIB) * 1000), p = (n) => String(n).padStart(2, "0");
    const hm = p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()), dm = p(d.getUTCDate()) + "/" + p(d.getUTCMonth() + 1);
    if (mode === "d") return dm;
    if (mode === "s") return hm + ":" + p(d.getUTCSeconds());
    if (mode === "full") return dm + " " + hm + " WIB";
    return hm;
  }
  async function getJSON(path) {
    const r = await fetch(path + "?t=" + Date.now(), { cache: "no-store" });
    if (!r.ok) throw new Error(path + " HTTP " + r.status);
    return r.json();
  }
  let rafPending = false;
  function redraw() { if (rafPending) return; rafPending = true; requestAnimationFrame(() => { rafPending = false; draw(); }); }

  // ======================= CHART =======================
  const cv = $("cv"), ctx = cv.getContext("2d");
  let W = 0, H = 0, dpr = 1;
  function resize() {
    dpr = window.devicePixelRatio || 1; W = cv.clientWidth; H = cv.clientHeight;
    if (!W || !H) return;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); draw();
  }
  function idxOf(t) {
    const c = S.data.candles, n = c.length;
    if (!n) return 0;
    if (S.idx.has(t)) return S.idx.get(t);
    if (t < c[0].t) return -1;
    let lo = 0, hi = n - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (c[m].t < t) lo = m + 1; else hi = m; }
    return c[lo].t >= t ? lo : n - 1;
  }
  const plotW = () => W - AXIS_W;
  const xOf = (i) => plotW() - (S.data.candles.length - 1 - i + S.off) * S.sp - S.sp / 2;
  const iOfX = (x) => Math.round(S.data.candles.length - 1 + S.off - (plotW() - x - S.sp / 2) / S.sp);
  function layout() {
    const total = H - TIME_H, hasV = S.show.vol && S.data.candles.some((k) => k.v != null);
    const hasC = S.show.cvd && isCrypto() && hasV;
    const vh = hasV ? Math.round(total * 0.14) : 0, ch = hasC ? Math.round(total * 0.16) : 0;
    const mh = total - vh - ch;
    return { main: [0, mh], vol: hasV ? [mh, mh + vh] : null, cvd: hasC ? [mh + vh, total] : null, total };
  }
  function visRange() {
    const n = S.data.candles.length;
    return [Math.max(0, Math.floor(n - 1 + S.off - plotW() / S.sp) - 1), Math.min(n - 1, Math.ceil(n - 1 + S.off))];
  }
  function scale(lo, hi, top, bot, padPct) {
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-12) { hi += Math.abs(hi) * 1e-4 + 1e-9; lo -= Math.abs(lo) * 1e-4 + 1e-9; }
    const pad = (hi - lo) * (padPct || 0);
    lo -= pad; hi += pad;
    return { lo, hi, y: (p) => top + 6 + (hi - p) / (hi - lo) * (bot - top - 12), p: (y) => hi - (y - top - 6) / (bot - top - 12) * (hi - lo) };
  }
  function niceStep(range, target) {
    const raw = range / target, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  }
  function label(text, x, y, color, o = {}) {
    ctx.font = (o.bold ? "bold " : "") + (o.size || 11) + "px -apple-system,Segoe UI,Roboto,sans-serif";
    const w = ctx.measureText(text).width;
    const tx = o.align === "right" ? x - w : o.align === "center" ? x - w / 2 : x;
    if (o.bg) {
      const ph = (o.size || 11) + 6, ty = o.base === "top" ? y : o.base === "bottom" ? y - ph : y - ph / 2;
      ctx.fillStyle = o.bg; ctx.fillRect(tx - 4, ty, w + 8, ph);
      ctx.fillStyle = color; ctx.textBaseline = "middle"; ctx.fillText(text, tx, ty + ph / 2); return;
    }
    ctx.fillStyle = color; ctx.textBaseline = o.base || "middle"; ctx.fillText(text, tx, y);
  }
  function hline(y, x0, x1, color, dash, width) {
    ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = width || 1; ctx.setLineDash(dash || []);
    ctx.beginPath(); ctx.moveTo(x0, Math.round(y) + 0.5); ctx.lineTo(x1, Math.round(y) + 0.5); ctx.stroke(); ctx.restore();
  }
  function tri(x, y, dir, col) {
    ctx.fillStyle = col; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - 6, y + 10 * dir); ctx.lineTo(x + 6, y + 10 * dir); ctx.closePath(); ctx.fill();
  }

  function draw() {
    if (!W || !H) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    const D = S.data;
    if (!D || !D.candles.length) return;
    const d = D.digits, c = D.candles, n = c.length, pw = plotW(), L = layout();
    S.off = Math.max(-5, Math.min(S.off, n - 5));
    const [a, b] = visRange();
    let lo = Infinity, hi = -Infinity;
    for (let i = a; i <= b; i++) { lo = Math.min(lo, c[i].l); hi = Math.max(hi, c[i].h); }
    if (S.show.setup && D.setup) { lo = Math.min(lo, D.setup.sl, D.setup.tp2); hi = Math.max(hi, D.setup.sl, D.setup.tp2); }
    const Y = scale(lo, hi, L.main[0], L.main[1], 0.06);
    S.Y = Y; S.L = L;

    // grid harga
    const step = niceStep(Y.hi - Y.lo, Math.max(3, (L.main[1] - L.main[0]) / 60));
    for (let p = Math.ceil(Y.lo / step) * step; p <= Y.hi; p += step) { const y = Y.y(p); hline(y, 0, pw, C.grid); label(fx(p, d), pw + 6, y, C.axis); }
    // grid waktu
    const every = Math.max(1, Math.round(90 / S.sp));
    for (let i = a; i <= b; i++) {
      if (i % every) continue;
      const x = xOf(i); if (x < 0 || x > pw) continue;
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, L.total); ctx.stroke();
      const t = c[i].t;
      label(["D", "W"].includes(D.tf) ? wib(t, "d") : (wib(t) === "00:00" || D.tf === "H4" ? wib(t, "d") + " " : "") + wib(t), x, L.total + 12, C.axis, { align: "center" });
    }
    ctx.save(); ctx.beginPath(); ctx.rect(0, L.main[0], pw, L.main[1] - L.main[0]); ctx.clip();
    const zone = (z, fill, line, txt, mit) => {
      const x0 = xOf(idxOf(z.t)) - S.sp / 2, x1 = z.end_t ? xOf(idxOf(z.end_t)) + S.sp / 2 : pw;
      if (x1 < 0 || x0 > pw) return;
      const y0 = Y.y(z.top), y1 = Y.y(z.bottom);
      ctx.globalAlpha = mit ? 0.45 : 1; ctx.fillStyle = fill; ctx.fillRect(x0, y0, x1 - x0, Math.max(1, y1 - y0));
      ctx.strokeStyle = line; ctx.lineWidth = 1; ctx.strokeRect(Math.round(x0) + 0.5, Math.round(y0) + 0.5, x1 - x0, Math.max(1, y1 - y0));
      ctx.globalAlpha = 1;
      if (!mit && y1 - y0 >= 9) label(txt, Math.min(x1, pw) - 6, (y0 + y1) / 2, C.fg, { align: "right", size: 10 });
    };
    if (S.show.mit) {
      if (S.show.ob) (D.ob_mit || []).forEach((z) => zone(z, z.dir === "bull" ? C.obBull : C.obBear, z.dir === "bull" ? C.obBullL : C.obBearL, "", true));
      if (S.show.fvg) (D.fvg_mit || []).forEach((z) => zone(z, z.dir === "bull" ? C.fvgBull : C.fvgBear, z.dir === "bull" ? C.fvgBullL : C.fvgBearL, "", true));
    }
    if (S.show.ob) (D.ob || []).forEach((z) => zone(z, z.dir === "bull" ? C.obBull : C.obBear, z.dir === "bull" ? C.obBullL : C.obBearL, z.dir === "bull" ? "Bull OB" : "Bear OB"));
    if (S.show.fvg) (D.fvg || []).forEach((z) => zone(z, z.dir === "bull" ? C.fvgBull : C.fvgBear, z.dir === "bull" ? C.fvgBullL : C.fvgBearL, z.dir === "bull" ? "Bull FVG" : "Bear FVG"));
    if (S.show.ema) {
      [["ema200", C.ema200], ["ema50", C.ema50], ["ema20", C.ema20]].forEach(([k, col]) => {
        ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.beginPath(); let st = false;
        for (const [t, v] of D[k] || []) {
          const i = S.idx.get(t); if (i == null || i < a - 1 || i > b + 1) continue;
          const x = xOf(i), y = Y.y(v); if (!st) { ctx.moveTo(x, y); st = true; } else ctx.lineTo(x, y);
        }
        ctx.stroke();
      });
    }
    const bw = Math.max(1, Math.min(S.sp * 0.7, S.sp - 1));
    for (let i = a; i <= b; i++) {
      const k = c[i], x = xOf(i), col = k.c >= k.o ? C.up : C.dn;
      ctx.globalAlpha = k.forming ? 0.55 : 1; ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, Y.y(k.h)); ctx.lineTo(Math.round(x) + 0.5, Y.y(k.l)); ctx.stroke();
      const yo = Y.y(k.o), yc = Y.y(k.c);
      ctx.fillRect(Math.round(x - bw / 2), Math.min(yo, yc), Math.round(bw), Math.max(1, Math.abs(yc - yo)));
    }
    ctx.globalAlpha = 1;
    if (S.show.struct) {
      (D.events || []).forEach((e) => {
        const x0 = xOf(idxOf(e.from_t || e.t)), x1 = xOf(idxOf(e.t)); if (x1 < 0 || x0 > pw) return;
        const col = e.dir === "bull" ? C.up : C.dn, y = Y.y(e.level);
        hline(y, x0, x1, col, e.type === "CHoCH" ? [] : [5, 4], e.type === "CHoCH" ? 1.6 : 1.2);
        label(e.type, (x0 + x1) / 2, e.dir === "bull" ? y - 2 : y + 3, col, { align: "center", size: 10, bold: e.type === "CHoCH", base: e.dir === "bull" ? "bottom" : "top" });
      });
    }
    if (S.show.sweep) {
      (D.sweeps || []).forEach((s) => {
        const x0 = xOf(idxOf(s.from_t)), x1 = xOf(idxOf(s.t)); if (x1 < 0 || x0 > pw) return;
        hline(Y.y(s.level), x0, x1, C.sweep, [2, 3], 1);
        const y = Y.y(s.price);
        label("Sweep", x1, s.dir === "bull" ? y + 4 : y - 4, "#131722", { align: "center", size: 9, bg: C.sweep, base: s.dir === "bull" ? "top" : "bottom" });
      });
    }
    if (S.show.setup && D.setup) {
      const s = D.setup, x0 = xOf(n - 1) - 120;
      ctx.fillStyle = "rgba(38,166,154,.08)"; ctx.fillRect(x0, Y.y(Math.max(s.entry, s.tp2)), pw - x0, Math.abs(Y.y(s.tp2) - Y.y(s.entry)));
      ctx.fillStyle = "rgba(239,83,80,.08)"; ctx.fillRect(x0, Y.y(Math.max(s.entry, s.sl)), pw - x0, Math.abs(Y.y(s.sl) - Y.y(s.entry)));
      [["ENTRY", s.entry, C.fg], ["SL", s.sl, C.dn], ["TP1", s.tp1, C.up], ["TP2", s.tp2, C.up]].forEach(([t, v, col]) => {
        hline(Y.y(v), x0, pw, col, [6, 4], 1.2); label(t + " " + fx(v, d), x0, Y.y(v) - 2, col, { size: 10, bold: true, base: "bottom" });
      });
    }
    if (D.signal === "BUY" || D.signal === "SELL") {
      let li = n - 1; while (li > 0 && c[li].forming) li--;
      const k = c[li], x = xOf(li);
      if (D.signal === "BUY") { const y = Y.y(k.l) + 8; tri(x, y, 1, C.up); label("BUY", x, y + 12, C.up, { align: "center", bold: true, base: "top" }); }
      else { const y = Y.y(k.h) - 8; tri(x, y, -1, C.dn); label("SELL", x, y - 12, C.dn, { align: "center", bold: true, base: "bottom" }); }
    }
    const last = c[n - 1], pc = last.c >= last.o ? C.up : C.dn;
    hline(Y.y(last.c), 0, pw, pc, [1, 3], 1);
    ctx.restore();
    label(fx(last.c, d), pw + 2, Y.y(last.c), "#fff", { bg: pc, size: 11, bold: true });

    // ---- pane volume ----
    if (L.vol) {
      hline(L.vol[0], 0, W, C.grid);
      let vmax = 0; for (let i = a; i <= b; i++) vmax = Math.max(vmax, c[i].v || 0);
      const Vs = scale(0, vmax || 1, L.vol[0], L.vol[1], 0);
      for (let i = a; i <= b; i++) {
        const k = c[i], x = xOf(i);
        ctx.globalAlpha = k.forming ? 0.35 : 0.6; ctx.fillStyle = k.c >= k.o ? C.up : C.dn;
        const y = Vs.y(k.v || 0); ctx.fillRect(Math.round(x - bw / 2), y, Math.round(bw), Vs.y(0) - y);
      }
      ctx.globalAlpha = 1;
      label("Volume", 6, L.vol[0] + 9, C.axis, { size: 10 });
      label(fq(vmax), pw + 6, L.vol[0] + 10, C.axis, { size: 10 });
    }
    // ---- pane delta & CVD ----
    if (L.cvd) {
      hline(L.cvd[0], 0, W, C.grid);
      let cv0 = 0; const cvd = [], del = [];
      for (let i = 0; i < n; i++) { const dlt = c[i].v != null ? 2 * (c[i].bv || 0) - c[i].v : 0; cv0 += dlt; cvd.push(cv0); del.push(dlt); }
      let dm = 0, cl = Infinity, chh = -Infinity;
      for (let i = a; i <= b; i++) { dm = Math.max(dm, Math.abs(del[i])); cl = Math.min(cl, cvd[i]); chh = Math.max(chh, cvd[i]); }
      const Ds = scale(-dm || -1, dm || 1, L.cvd[0], L.cvd[1], 0), Cs = scale(cl, chh, L.cvd[0], L.cvd[1], 0.1);
      for (let i = a; i <= b; i++) {
        const x = xOf(i), y0 = Ds.y(0), y = Ds.y(del[i]);
        ctx.globalAlpha = 0.55; ctx.fillStyle = del[i] >= 0 ? C.up : C.dn;
        ctx.fillRect(Math.round(x - bw / 2), Math.min(y, y0), Math.round(bw), Math.abs(y - y0));
      }
      ctx.globalAlpha = 1; ctx.strokeStyle = C.cvd; ctx.lineWidth = 1.4; ctx.beginPath();
      for (let i = a; i <= b; i++) { const x = xOf(i), y = Cs.y(cvd[i]); if (i === a) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
      ctx.stroke();
      label("Delta (bar) · CVD (garis)", 6, L.cvd[0] + 9, C.axis, { size: 10 });
    }
    // ---- crosshair ----
    const hv = S.hover;
    if (hv && hv.x < pw && hv.y < L.total) {
      const i = Math.max(0, Math.min(n - 1, iOfX(hv.x))), x = xOf(i);
      ctx.save(); ctx.setLineDash([4, 4]); ctx.strokeStyle = "#9598a1"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, L.total); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, Math.round(hv.y) + 0.5); ctx.lineTo(pw, Math.round(hv.y) + 0.5); ctx.stroke(); ctx.restore();
      if (hv.y < L.main[1]) label(fx(Y.p(hv.y), d), pw + 2, hv.y, "#fff", { bg: "#363a45" });
      label(wib(c[i].t, "full").replace(" WIB", ""), x, L.total + 12, "#fff", { bg: "#363a45", align: "center" });
      legend(i);
    } else legend(n - 1);
  }

  let legendKey = "";
  function legend(i) {
    const D = S.data, k = D.candles[i], d = D.digits;
    if (!k) return;
    const key = D.symbol + D.tf + i + "|" + k.c + "|" + k.h + "|" + k.l + "|" + k.v + "|" + S.show.ema;
    if (key === legendKey) return;
    legendKey = key;
    const ch = k.c - k.o, pct = (ch / k.o) * 100;
    const ema = (key) => { const m = (D[key] || []).find((x) => x[0] === k.t); return m ? fx(m[1], d) : "-"; };
    const vol = k.v != null ? `<span class="o muted">Vol ${fq(k.v)} · Buy ${k.v ? Math.round(100 * (k.bv || 0) / k.v) : 0}% · Δ ${fq(2 * (k.bv || 0) - k.v)}</span>` : "";
    $("legend").innerHTML = `<div><b>${esc(D.symbol)}</b> · ${esc(D.tf)} · <span class="muted">${wib(k.t, "full")}${k.forming ? " (berjalan)" : ""}</span></div>
      <div class="${k.c >= k.o ? "up" : "dn"}"><span class="o">O ${fx(k.o, d)}</span><span class="o">H ${fx(k.h, d)}</span><span class="o">L ${fx(k.l, d)}</span><span class="o">C ${fx(k.c, d)}</span><span class="o">${ch >= 0 ? "+" : ""}${fx(ch, d)} (${fx(pct, 2)}%)</span></div>
      ${vol ? "<div>" + vol + "</div>" : ""}
      ${S.show.ema ? `<div><span class="o" style="color:${C.ema20}">EMA20 ${ema("ema20")}</span><span class="o" style="color:${C.ema50}">EMA50 ${ema("ema50")}</span><span class="o" style="color:${C.ema200}">EMA200 ${ema("ema200")}</span></div>` : ""}`;
  }

  // ======================= INTERAKSI (mouse + sentuh) =======================
  // Mouse : drag = geser, roda = zoom di posisi kursor, trackpad 2 jari = geser/zoom
  // Sentuh: 1 jari horizontal = geser (+ inersia), 1 jari vertikal = scroll halaman,
  //         2 jari = pinch zoom di titik tengah jari, tap = crosshair, double-tap = reset
  const ptrs = new Map();
  let drag = null, pinch = null, inertia = 0, tapT = 0, tapAt = null, crossLock = false;
  const SP_MIN = 1.5, SP_MAX = 40;
  const relX = (e) => e.clientX - cv.getBoundingClientRect().left;
  const relY = (e) => e.clientY - cv.getBoundingClientRect().top;
  function stopInertia() { if (inertia) cancelAnimationFrame(inertia); inertia = 0; }
  function zoomAt(f, x) {
    if (!S.data) return;
    const n = S.data.candles.length, pw = plotW();
    if (x == null || x > pw) x = Math.min(pw, xOf(n - 1));
    const fi = n - 1 + S.off - (pw - x - S.sp / 2) / S.sp;        // candle di bawah titik zoom
    S.sp = Math.max(SP_MIN, Math.min(SP_MAX, S.sp * f));
    S.off = fi - (n - 1) + (pw - x - S.sp / 2) / S.sp;              // jaga candle tetap di titik itu
    redraw();
  }
  function zoom(f) { zoomAt(f, null); }
  cv.addEventListener("pointerdown", (e) => {
    if (!S.data) return;
    stopInertia();
    cv.setPointerCapture(e.pointerId);
    ptrs.set(e.pointerId, { x: relX(e), y: relY(e) });
    if (ptrs.size === 1) {
      drag = { x0: relX(e), y0: relY(e), off: S.off, moved: false, touch: e.pointerType !== "mouse", t0: performance.now(), hist: [[performance.now(), relX(e)]] };
    } else if (ptrs.size === 2) {
      const [a, b] = [...ptrs.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mid: (a.x + b.x) / 2, sp: S.sp, off: S.off };
      drag = null; S.hover = null;
    }
  });
  cv.addEventListener("pointermove", (e) => {
    if (!S.data) return;
    const x = relX(e), y = relY(e);
    if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, { x, y });
    if (pinch && ptrs.size >= 2) {
      const [a, b] = [...ptrs.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1, mid = (a.x + b.x) / 2;
      S.sp = pinch.sp; S.off = pinch.off;
      zoomAt(d / pinch.d, pinch.mid);
      S.off += (mid - pinch.mid) / S.sp;                           // ikut geser saat pinch
      return;
    }
    if (drag) {
      const dx = x - drag.x0;
      if (!drag.moved && Math.abs(dx) > (drag.touch ? 6 : 2)) drag.moved = true;
      if (drag.moved) {
        S.off = drag.off + dx / S.sp;
        drag.hist.push([performance.now(), x]); if (drag.hist.length > 6) drag.hist.shift();
        if (drag.touch && !crossLock) S.hover = null;               // jangan gambar crosshair saat menggeser di HP
      }
      if (!drag.touch) S.hover = { x, y };
      redraw();
      return;
    }
    if (e.pointerType === "mouse") { S.hover = { x, y }; redraw(); }
  });
  function endPtr(e) {
    ptrs.delete(e.pointerId);
    if (pinch) { if (ptrs.size < 2) pinch = null; drag = null; return; }
    if (!drag) return;
    const d = drag; drag = null;
    if (e.type === "pointercancel") return;                        // browser mengambil alih (scroll vertikal)
    if (d.touch && !d.moved && performance.now() - d.t0 < 300) {   // TAP
      const now = performance.now(), x = relX(e), y = relY(e);
      if (now - tapT < 320 && tapAt && Math.hypot(x - tapAt.x, y - tapAt.y) < 30) {
        resetView(); S.hover = null; crossLock = false; tapT = 0;  // double-tap = reset
      } else {
        crossLock = !crossLock || (S.hover && Math.abs(S.hover.x - x) > 4);
        S.hover = crossLock ? { x, y } : null; tapT = now; tapAt = { x, y };
      }
      redraw(); return;
    }
    if (d.touch && d.moved && d.hist.length > 1) {                 // INERSIA
      const [t1, x1] = d.hist[d.hist.length - 1], [t0, x0] = d.hist[0];
      let v = (x1 - x0) / Math.max(1, t1 - t0);                    // px/ms
      if (performance.now() - t1 > 80) v = 0;
      let last = performance.now();
      const step = (now) => {
        const dt = now - last; last = now;
        S.off += (v * dt) / S.sp; v *= Math.pow(0.94, dt / 16);
        draw();
        inertia = Math.abs(v) > 0.02 ? requestAnimationFrame(step) : 0;
      };
      if (Math.abs(v) > 0.1) inertia = requestAnimationFrame(step);
    }
  }
  cv.addEventListener("pointerup", endPtr);
  cv.addEventListener("pointercancel", endPtr);
  cv.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse" && !drag) { S.hover = null; redraw(); } });
  cv.addEventListener("wheel", (e) => {
    if (!S.data) return;
    e.preventDefault(); stopInertia();
    if (e.ctrlKey) zoomAt(Math.exp(-e.deltaY * 0.01), relX(e));    // pinch trackpad
    else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) { S.off -= e.deltaX / S.sp; redraw(); } // geser trackpad
    else zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, relX(e));
  }, { passive: false });
  cv.addEventListener("contextmenu", (e) => e.preventDefault());
  $("zoom-in").addEventListener("click", () => zoom(1.25));
  $("zoom-out").addEventListener("click", () => zoom(0.8));
  $("zoom-reset").addEventListener("click", () => { stopInertia(); resetView(); S.hover = null; crossLock = false; redraw(); });
  function resetView() { S.sp = W < 640 ? 6 : 9; S.off = 8; }
  if (window.innerWidth < 640) {
    $("toggles").open = false;
    ["table", "mit", "cvd"].forEach((k) => { document.querySelector(`.toggles input[data-k="${k}"]`).checked = false; });
  }
  document.querySelectorAll(".toggles input").forEach((el) => {
    S.show[el.dataset.k] = el.checked;
    el.addEventListener("change", () => { S.show[el.dataset.k] = el.checked; $("pine-table").hidden = !S.show.table; redraw(); });
  });

  // ======================= LIVE BINANCE (crypto) =======================
  function bucket(t, tf) {
    const s = TF_SEC[tf];
    if (tf === "W") { const day = Math.floor(t / 86400); return (day - ((day + 3) % 7)) * 86400; }
    return t - (t % s);
  }
  function liveStop() {
    const L = S.live;
    if (L.ws) { L.ws.onclose = null; L.ws.close(); }
    Object.assign(L, { ws: null, sym: null, ok: false, book: null, tape: [], buy: 0, sell: 0, since: 0, lastMin: null });
  }
  function liveStart(sym) {
    const L = S.live;
    if (L.sym === sym && L.ws) return;
    liveStop(); L.sym = sym; L.since = Date.now() / 1000;
    const s = sym.toLowerCase();
    const url = WS_HOSTS[L.host % WS_HOSTS.length] + "?streams=" + [s + "@aggTrade", s + "@depth20@100ms", s + "@kline_1m"].join("/");
    let ws;
    try { ws = new WebSocket(url); } catch (e) { liveFail(); return; }
    L.ws = ws;
    ws.onopen = () => { L.ok = true; L.tries = 0; liveBadge(); };
    ws.onmessage = (ev) => { try { onLive(JSON.parse(ev.data)); } catch (e) { /* abaikan */ } };
    ws.onerror = () => { };
    ws.onclose = () => { L.ok = false; L.ws = null; liveFail(); };
    setTimeout(() => { if (L.ws === ws && !L.ok) { ws.onclose = null; ws.close(); L.ws = null; liveFail(); } }, 8000);
  }
  function liveFail() {
    const L = S.live; L.tries++; L.host++;
    liveBadge();
    if (isCrypto() && L.tries <= 6) { const sym = L.sym; L.sym = null; setTimeout(() => { if (isCrypto() && S.pair === sym) liveStart(sym); }, Math.min(30000, 2000 * L.tries)); }
    renderSide();
  }
  function liveBadge() {
    const b = $("live");
    if (!isCrypto()) { b.hidden = true; return; }
    b.hidden = false;
    b.className = "pill " + (S.live.ok ? "live" : "snap");
    b.textContent = S.live.ok ? "● LIVE Binance" : "◌ Snapshot (live tidak tersedia)";
  }
  let sideTimer = 0;
  function onLive(m) {
    const st = m.stream || "", d = m.data || {};
    if (st.endsWith("@aggTrade")) {
      const tr = { p: +d.p, q: +d.q, ms: d.T, side: d.m ? "SELL" : "BUY" };
      tr.usd = tr.p * tr.q;
      S.live.tape.unshift(tr); if (S.live.tape.length > 60) S.live.tape.length = 60;
      if (tr.side === "BUY") S.live.buy += tr.q; else S.live.sell += tr.q;
    } else if (st.includes("@depth")) {
      S.live.book = { bids: d.bids || d.b, asks: d.asks || d.a };
    } else if (st.endsWith("@kline_1m")) {
      liveCandle(d.k);
    }
    const now = Date.now();
    if (now - sideTimer > 300) { sideTimer = now; renderSide(); }
  }
  function liveCandle(k) {
    if (!S.data || S.data.symbol !== S.live.sym || !k) return;
    const D = S.data, t = Math.floor(k.t / 1000), bt = bucket(t, D.tf), c = D.candles;
    const v = +k.v, bv = +k.V, close = +k.c;
    const prev = S.live.lastMin;
    let dv = v, dbv = bv;
    if (prev && prev.t === t) { dv = v - prev.v; dbv = bv - prev.bv; }
    S.live.lastMin = { t, v, bv };
    let last = c[c.length - 1];
    if (!last) return;
    if (bt < last.t) return;
    if (bt > last.t) {
      last.forming = false;
      last = { t: bt, o: +k.o, h: +k.h, l: +k.l, c: close, v: 0, bv: 0, forming: true };
      c.push(last); S.idx.set(bt, c.length - 1);
      if (D.tf === "M1") { last.v = v; last.bv = bv; dv = dbv = 0; }
    }
    last.h = Math.max(last.h, +k.h); last.l = Math.min(last.l, +k.l); last.c = close;
    if (D.tf === "M1") { last.v = v; last.bv = bv; } else if (prev) { last.v = (last.v || 0) + Math.max(0, dv); last.bv = (last.bv || 0) + Math.max(0, dbv); }
    redraw();
  }

  // ---- panel samping: order book + transaksi ----
  function renderSide() {
    if (!isCrypto()) return;
    const p = P(), d = p.digits, L = S.live;
    const live = L.ok && L.book;
    const book = live ? { bids: L.book.bids.map((x) => [+x[0], +x[1]]), asks: L.book.asks.map((x) => [+x[0], +x[1]]) } : (p.book ? { bids: p.book.bids, asks: p.book.asks } : null);
    $("book-src").textContent = live ? "live 100ms" : p.book ? "snapshot " + wib(S.state.updated_ts) : "";
    if (book && book.bids.length && book.asks.length) {
      const rows = 12, asks = book.asks.slice(0, rows), bids = book.bids.slice(0, rows);
      const mx = Math.max(...asks.map((x) => x[1]), ...bids.map((x) => x[1]));
      const wallA = Math.max(...asks.map((x) => x[1])), wallB = Math.max(...bids.map((x) => x[1]));
      const row = (x, side, wall) => `<div class="lvl ${side}${x[1] === wall && x[1] > mx * 0.6 ? " wall" : ""}"><i style="width:${(100 * x[1] / mx).toFixed(1)}%"></i><span class="${side === "a" ? "dn" : "up"}">${fx(x[0], d)}</span><span>${fq(x[1])}</span><span class="muted">${usd(x[0] * x[1])}</span></div>`;
      const bb = book.bids[0][0], ba = book.asks[0][0], mid = (bb + ba) / 2;
      const sb = bids.reduce((s, x) => s + x[0] * x[1], 0), sa = asks.reduce((s, x) => s + x[0] * x[1], 0);
      $("book").innerHTML = `<div class="lvl muted"><span>Harga</span><span>Jumlah</span><span>Nilai</span></div>` +
        asks.slice().reverse().map((x) => row(x, "a", wallA)).join("") +
        `<div class="mid"><span>${fx(mid, d)}</span><span class="muted">spread ${fx(ba - bb, d)}</span><span class="${sb >= sa ? "up" : "dn"}">bid ${(100 * sb / (sb + sa)).toFixed(0)}%</span></div>` +
        bids.map((x) => row(x, "b", wallB)).join("");
    } else $("book").innerHTML = `<p class="muted">Order book belum tersedia.</p>`;
    const thr = p.trades ? p.trades.large_threshold : Infinity;
    const tape = L.ok && L.tape.length ? L.tape.slice(0, 40) : (p.trades ? p.trades.large : []);
    $("tape-src").textContent = L.ok && L.tape.length ? "live" : p.trades ? "transaksi besar (snapshot)" : "";
    $("tape").innerHTML = `<div class="tr muted"><span>Harga</span><span>Jumlah</span><span>Waktu</span></div>` + (tape.length ? tape.map((x) =>
      `<div class="tr${x.usd >= thr ? " whale" : ""}"><span class="${x.side === "BUY" ? "up" : "dn"}">${fx(x.p, d)}</span><span>${fq(x.q)}</span><span class="muted">${wib(Math.floor(x.ms / 1000), "s")}</span></div>`).join("") : `<p class="muted">Menunggu transaksi…</p>`);
    if (L.ok) flowPanel();
  }

  // ======================= PANEL =======================
  function pineTable() {
    const p = P(), a = p.tf[S.tf] || {}, d = p.digits, bias = p.tf[S.state.strategy.bias] || {};
    const e = (a.events || []).slice(-1)[0], sw = (a.sweeps || []).slice(-1)[0];
    const et = a.ema20 != null && a.ema50 != null ? (a.ema20 > a.ema50 ? ["NAIK", "up"] : ["TURUN", "dn"]) : ["-", ""];
    const rows = [
      ["Struktur", short(a.trend).toUpperCase(), cls(a.trend)],
      ["HTF " + S.state.strategy.bias, bias.enough ? short(bias.trend).toUpperCase() : "DATA KURANG", cls(bias.trend)],
      ["Event", e ? `${e.type} ${e.dir === "bull" ? "▲" : "▼"} ${fx(e.level, d)}` : "-", e ? cls(e.dir) : ""],
      ["Sweep", sw ? `${sw.dir === "bull" ? "Low" : "High"} ${fx(sw.level, d)}` : "-", sw ? "yl" : ""],
      ["EMA 20/50", et[0], et[1]],
      ["RSI(14)", a.rsi ?? "-", a.rsi > 70 ? "dn" : a.rsi < 30 ? "up" : ""],
      ["ATR(14)", fx(a.atr, d), ""],
    ];
    if (a.flow) rows.push(["Buy vol 20", a.flow.buy_pct.toFixed(0) + "%", a.flow.buy_pct >= 50 ? "up" : "dn"]);
    if (p.book) rows.push(["Book ±0,5%", "bid " + p.book["imb_0.5"].toFixed(0) + "%", p.book["imb_0.5"] >= 50 ? "up" : "dn"]);
    rows.push(["Signal " + S.tf, a.signal || "WAIT", a.signal === "BUY" ? "up" : a.signal === "SELL" ? "dn" : ""]);
    rows.push(["Keputusan", p.overall.signal, p.overall.signal === "BUY" ? "up" : p.overall.signal === "SELL" ? "dn" : ""]);
    $("pine-table").innerHTML = `<table><tr class="hd"><td>${esc(S.pair)} ${S.tf}</td><td>${fx(p.price, d)}</td></tr>` +
      rows.map(([k, v, c]) => `<tr><td>${esc(k)}</td><td class="${c}">${esc(v)}</td></tr>`).join("") + "</table>";
    $("pine-table").hidden = !S.show.table;
  }
  const checklist = (items) => `<ul class="check">${(items || []).map(([ok, t]) => `<li class="${ok === null ? "na" : ok ? "ok" : "no"}">${esc(t)}</li>`).join("")}</ul>`;
  const meter = (title, pct, left, right) => `<div class="meter"><div class="lab"><span>${esc(title)}</span><span><b class="up">${esc(left)}</b> · <b class="dn">${esc(right)}</b></span></div><div class="bar2"><b style="width:${Math.max(0, Math.min(100, pct)).toFixed(1)}%"></b><s></s></div></div>`;

  function flowPanel() {
    const p = P(), d = p.digits, el = $("p-flow");
    if (p.market !== "crypto") return statusInto(el);
    const a = p.tf[S.tf] || {}, fl = a.flow, bk = p.book, tr = p.trades, tk = p.ticker, L = S.live;
    let h = `<h3>Order Flow ${esc(S.pair)}</h3>`;
    if (tk) h += `<div class="kv"><div><small>24 jam</small><b class="${tk.change_pct >= 0 ? "up" : "dn"}">${tk.change_pct >= 0 ? "+" : ""}${tk.change_pct.toFixed(2)}%</b></div><div><small>Volume 24j</small><b>${usd(tk.quote_volume)}</b></div><div><small>Transaksi 24j</small><b>${fx(tk.trades, 0)}</b></div></div>`;
    if (L.ok && L.buy + L.sell > 0) h += meter(`Live sejak dibuka (${Math.round((Date.now() / 1000 - L.since) / 60)} mnt)`, 100 * L.buy / (L.buy + L.sell), "Beli " + fq(L.buy), "Jual " + fq(L.sell));
    if (fl) h += meter(`${S.tf} 20 candle · Δ ${fl.delta >= 0 ? "+" : ""}${fq(fl.delta)}`, fl.buy_pct, "Beli " + fl.buy_pct.toFixed(0) + "%", "Jual " + (100 - fl.buy_pct).toFixed(0) + "%");
    if (tr) h += meter(`${tr.count} transaksi terakhir`, tr.buy_pct, "Agresif beli " + tr.buy_pct.toFixed(0) + "%", (100 - tr.buy_pct).toFixed(0) + "%");
    if (bk) {
      h += meter(`Order book ±0,1% (${usd(bk["bid_0.1"] + bk["ask_0.1"])})`, bk["imb_0.1"], "Bid " + usd(bk["bid_0.1"]), "Ask " + usd(bk["ask_0.1"]));
      h += meter(`Order book ±0,5%`, bk["imb_0.5"], "Bid " + usd(bk["bid_0.5"]), "Ask " + usd(bk["ask_0.5"]));
      h += meter(`Order book ±1%`, bk["imb_1.0"], "Bid " + usd(bk["bid_1.0"]), "Ask " + usd(bk["ask_1.0"]));
      h += `<h4>Liquidity wall</h4><ul class="list">${bk.ask_walls.map((w) => `<li class="dn">Ask ${fx(w[0], d)} · ${fq(w[1])} (${usd(w[2])})</li>`).join("")}${bk.bid_walls.map((w) => `<li class="up">Bid ${fx(w[0], d)} · ${fq(w[1])} (${usd(w[2])})</li>`).join("")}</ul>
        <div class="muted">Spread ${fx(bk.spread, d)} (${bk.spread_bps.toFixed(2)} bps) · ${bk.levels[0]}/${bk.levels[1]} level</div>`;
    }
    if (tr) h += `<h4>Transaksi besar ≥ ${usd(tr.large_threshold)}: <span class="up">${tr.large_buy} beli</span> / <span class="dn">${tr.large_sell} jual</span></h4>`;
    if ((p.book_hist || []).length > 2) h += `<h4>Riwayat imbalance (bid ±0,5% <span style="color:${C.acc || "#2962ff"}">━</span>, agresif beli <span style="color:${C.cvd}">━</span>)</h4><canvas class="spark" id="spark"></canvas>`;
    el.innerHTML = h;
    const sp = $("spark");
    if (sp) spark(sp, p.book_hist);
  }
  function spark(el, hist) {
    const w = el.clientWidth, hgt = el.clientHeight, r = window.devicePixelRatio || 1, g = el.getContext("2d");
    el.width = w * r; el.height = hgt * r; g.setTransform(r, 0, 0, r, 0, 0);
    g.fillStyle = "#1e222d"; g.fillRect(0, 0, w, hgt);
    const y = (v) => hgt - 4 - (v / 100) * (hgt - 8);
    g.strokeStyle = "#434651"; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(0, y(50)); g.lineTo(w, y(50)); g.stroke(); g.setLineDash([]);
    const t0 = hist[0][0], t1 = hist[hist.length - 1][0] || t0 + 1;
    [[1, "#2962ff"], [3, C.cvd]].forEach(([k, col]) => {
      g.strokeStyle = col; g.lineWidth = 1.4; g.beginPath(); let st = false;
      hist.forEach((hh) => { if (hh[k] == null) return; const x = (hh[0] - t0) / (t1 - t0 || 1) * (w - 4) + 2; if (!st) { g.moveTo(x, y(hh[k])); st = true; } else g.lineTo(x, y(hh[k])); });
      g.stroke();
    });
  }

  function statusInto(el) {
    const st = S.state, fx_ = st.sources.forex, cr = st.sources.crypto;
    const ageMin = Math.round((Date.now() / 1000 - st.updated_ts) / 60);
    const late = ageMin > Math.min(fx_.interval_min || 30, cr.symbols.length ? cr.interval_min : 99) * 2 + 15;
    const pct = Math.min(100, (100 * fx_.req_month) / (fx_.quota || 5000));
    let w = "";
    Object.entries(st.errors || {}).forEach(([k, v]) => (w += `<div class="warn err">⚠️ ${esc(k)}: ${esc(v)}</div>`));
    (st.warnings || []).forEach((v) => (w += `<div class="warn">⚠️ ${esc(v)}</div>`));
    el.innerHTML = `<h3>System Status</h3><div class="scols"><div>
      <div class="srow"><span>Engine</span><span>${late ? "🔴 Terlambat" : "🟢 Running"}</span></div>
      <div class="srow"><span>Telegram</span><span>${st.telegram_ok ? "🟢 Connected" : "🟠 Kendala"}</span></div>
      <div class="srow"><span>Last Update</span><span>${wib(st.updated_ts, "full")}</span></div></div>
      ${fx_.symbols.length ? `<div><div class="srow"><span>RealMarketAPI (forex)</span><span>${fx_.market_closed ? "💤 Pasar tutup" : fx_.ok ? "🟢 Connected" : "🔴 Error"}</span></div>
      <div class="srow"><span>M5 tiap ${fx_.interval_min} mnt</span><span>terakhir ${wib(fx_.fetch_ts)} · next ±${wib(fx_.next_ts)}</span></div>
      <div class="muted" style="margin-top:6px">Kuota bulan ini: <b>${fx_.req_month} / ${fx_.quota}</b> (${pct.toFixed(1)}%)</div>
      <div class="bar"><div style="width:${pct}%;background:${pct > 85 ? "var(--dn)" : pct > 65 ? "var(--org)" : "var(--acc)"}"></div></div></div>` : ""}
      ${cr.symbols.length ? `<div><div class="srow"><span>Binance (crypto)</span><span>${cr.ok ? "🟢 Connected" : "🔴 Error"}</span></div>
      <div class="srow"><span>M1 tiap ${cr.interval_min} mnt</span><span>next ±${wib(cr.next_ts)}</span></div>
      <div class="srow"><span>Host</span><span>${esc((cr.host || "-").replace("https://", ""))}</span></div>
      <div class="srow"><span>Live browser</span><span>${S.live.ok ? "🟢 WebSocket" : "◌ snapshot"}</span></div></div>` : ""}
      </div>${w}`;
    $("age").textContent = (late ? "🔴 " : "🟢 ") + "Data " + ageMin + " mnt lalu";
  }

  function panels() {
    const st = S.state, p = P(), d = p.digits, ov = p.overall, s = ov.setup;
    document.body.classList.toggle("forex", p.market !== "crypto");
    $("p-decision").innerHTML = `<h3>Keputusan ${esc(S.pair)} (${st.strategy.bias} → ${st.strategy.setup} → ${st.strategy.confirm})</h3>
      <div class="big">${fx(p.price, d)} ${sig(ov.signal)}</div>
      <div class="muted">${p.market === "crypto" ? "Binance · candle M1" : "RealMarketAPI · candle M5"} terakhir: ${wib(p.last_ts, "full")}</div>
      ${s ? `<div class="kv"><div><small>Entry</small><b>${fx(s.entry, d)}</b></div><div><small>Stop Loss</small><b class="dn">${fx(s.sl, d)}</b></div><div><small>TP1</small><b class="up">${fx(s.tp1, d)}</b></div><div><small>TP2</small><b class="up">${fx(s.tp2, d)}</b></div></div>` : ""}
      ${checklist(ov.reasons)}<div class="reason">${esc(ov.summary)}</div>`;
    const a = p.tf[S.tf] || {};
    if (!a.ema20 && !(a.events || []).length) $("p-detail").innerHTML = `<h3>Detail ${esc(S.pair)} ${S.tf}</h3><p>${esc(a.reason || "Data belum cukup.")}</p>`;
    else {
      const lc = a.last_candle || {};
      const z = (arr, k) => (arr || []).slice(-3).map((x) => `<li class="${cls(x.dir)}">${x.dir === "bull" ? "Bull" : "Bear"} ${k} ${fx(x.bottom, d)} – ${fx(x.top, d)}</li>`).join("");
      $("p-detail").innerHTML = `<h3>Kondisi entry ${esc(S.pair)} ${S.tf} ${sig(a.signal)}</h3>
        <div class="muted">Candle close ${wib(lc.t, "full")}: O ${fx(lc.o, d)} · H ${fx(lc.h, d)} · L ${fx(lc.l, d)} · C ${fx(lc.c, d)} <b class="${cls(lc.dir)}">${esc(lc.dir || "")}</b></div>
        ${checklist(a.checklist)}<div class="reason">${esc(a.reason)}</div><ul class="list">${z(a.ob, "OB")}${z(a.fvg, "FVG")}</ul>`;
    }
    flowPanel();
    statusInto($("p-status"));
    $("mtf-title").textContent = "Multi-Timeframe " + S.pair;
    const tfs = ["W", "D", "H4", "H1", "M15", "M5"].concat(p.market === "crypto" ? ["M1"] : []);
    $("mtf").querySelector("tbody").innerHTML = tfs.map((tf) => {
      const x = p.tf[tf] || {}, cur = tf === S.tf ? "cur" : "";
      if (!x.enough && !x.ema20) return `<tr data-tf="${tf}" class="${cur}"><td><b>${tf}</b></td><td colspan="9" class="muted">Data belum cukup (${x.bars || 0}/${x.need || 60} candle) – terisi otomatis</td></tr>`;
      const e = (x.events || []).slice(-1)[0], sw = (x.sweeps || []).slice(-1)[0];
      return `<tr data-tf="${tf}" class="${cur}"><td><b>${tf}</b></td><td class="${cls(x.trend)}">${esc(short(x.trend))}</td>
        <td>${e ? `<span class="${cls(e.dir)}">${esc(e.type)} ${e.dir === "bull" ? "▲" : "▼"}</span> ${fx(e.level, d)}` : "-"}</td>
        <td>${sw ? `<span class="yl">${sw.dir === "bull" ? "Low" : "High"}</span> ${fx(sw.level, d)}` : "-"}</td>
        <td>${fx(x.ema20, d)}</td><td>${fx(x.ema50, d)}</td><td>${x.rsi ?? "-"}</td><td>${fx(x.atr, d)}</td>
        <td class="cr-col ${x.flow ? (x.flow.buy_pct >= 50 ? "up" : "dn") : ""}">${x.flow ? x.flow.buy_pct.toFixed(0) + "%" : "-"}</td><td>${sig(x.signal)}</td></tr>`;
    }).join("");
    $("mtf").querySelectorAll("tr[data-tf]").forEach((tr) => tr.addEventListener("click", () => selectTf(tr.dataset.tf)));
  }

  function tabs() {
    const pairs = S.state.pairs, groups = { forex: [], crypto: [] };
    Object.keys(pairs).forEach((s) => groups[pairs[s].market].push(s));
    let h = "";
    for (const [g, list] of Object.entries(groups)) {
      if (!list.length) continue;
      h += `<span class="mk">${g.toUpperCase()}</span>` + list.map((s) => `<button type="button" role="tab" aria-selected="${s === S.pair}" class="tab ${s === S.pair ? "on" : ""}" data-v="${esc(s)}">${esc(s)}</button>`).join("");
    }
    $("pair-tabs").innerHTML = h;
    const tfl = isCrypto() ? ["M1", "M5", "M15", "H1", "H4", "D", "W"] : ["M5", "M15", "H1", "H4", "D", "W"];
    $("tf-tabs").innerHTML = tfl.map((t) => `<button type="button" role="tab" aria-selected="${t === S.tf}" class="tab ${t === S.tf ? "on" : ""}" data-v="${t}">${t}</button>`).join("");
    $("pair-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => selectPair(b.dataset.v)));
    $("tf-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => selectTf(b.dataset.v)));
  }
  function selectPair(s) {
    S.pair = s;
    if (!isCrypto() && S.tf === "M1") S.tf = "M5";
    tabs(); pineTable(); panels(); loadChart(true);
    if (isCrypto()) liveStart(s); else liveStop();
    liveBadge(); renderSide();
  }
  function selectTf(t) { S.tf = t; tabs(); pineTable(); panels(); loadChart(true); }

  async function loadChart(reset) {
    const msg = $("chart-msg");
    try {
      const D = await getJSON("data/chart_" + S.pair + "_" + S.tf + ".json");
      S.data = D; S.idx = new Map(D.candles.map((k, i) => [k.t, i])); S.live.lastMin = null;
      if (reset) resetView();
      msg.hidden = !!D.candles.length;
      if (!D.candles.length) msg.textContent = "Belum ada candle untuk " + S.pair + " " + S.tf;
      redraw();
    } catch (e) {
      S.data = null; redraw(); msg.hidden = false; msg.textContent = "Chart belum tersedia (" + e.message + ")";
    }
  }
  async function load() {
    try { S.state = await getJSON("data/state.json"); }
    catch (e) { $("age").textContent = "Gagal memuat data"; $("chart-msg").hidden = false; $("chart-msg").textContent = "Data belum ada. Jalankan workflow Forex Engine dulu. (" + e.message + ")"; return; }
    const syms = Object.keys(S.state.pairs || {});
    if (!syms.length) { $("age").textContent = "Belum ada data"; return; }
    const first = !S.pair;
    if (!S.pair || !S.state.pairs[S.pair]) S.pair = syms[0];
    if (!isCrypto() && S.tf === "M1") S.tf = "M5";
    tabs(); pineTable(); panels(); loadChart(first);
    if (isCrypto()) liveStart(S.pair); else liveStop();
    liveBadge(); renderSide();
  }
  new ResizeObserver(resize).observe(cv);
  $("refresh").addEventListener("click", load);
  load();
  setInterval(load, 5 * 60 * 1000);
})();
