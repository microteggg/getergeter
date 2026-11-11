/* Market Monitor v19 – Auto Fibonacci (H4 utama tebal + Fib lokal tipis per TF), MODE MONITORING
   (sinyal BUY/SELL disembunyikan, panel Monitoring Events dari monitor_{PAIR}.json), preset tampilan Minimal,
   status Telegram per pesan (SENT / RETRYING / FAILED) + catatan run engine.
   Market Monitor v18 – + BUY AREA / SELL AREA (Sweep Reversal + CHoCH Retest)
   Market Monitor v15 – chart canvas gaya Pine/TradingView.
   XAUUSD & crypto top 10 dibaca dari file JSON hasil engine (0 request ke RealMarketAPI).
   Seluruh riwayat (struktur, zona, liquidity, setup, entry + hasil) selalu tampil untuk cek & backtest.
   Crypto: tambahan data LIVE langsung dari WebSocket publik Binance di browser (order book, transaksi, candle M1). */
(function () {
  "use strict";
  const WIB = 7 * 3600;
  const TF_SEC = { M1: 60, M5: 300, M15: 900, H1: 3600, H4: 14400, D: 86400, W: 604800 };
  const WS_HOSTS = ["wss://data-stream.binance.vision/stream", "wss://stream.binance.com:9443/stream"];
  const C = {
    bg: "#ffffff", grid: "#f0f3fa", axis: "#131722", fg: "#131722", up: "#26a69a", dn: "#000000", border: "#000000", red: "#e53935",
    iobBull: "rgba(49,121,245,.12)", iobBear: "rgba(247,124,128,.12)", iobBullL: "#3179f5", iobBearL: "#f77c80",
    sobBull: "rgba(24,72,204,.18)", sobBear: "rgba(178,40,51,.16)", sobBullL: "#1848cc", sobBearL: "#b22833",
    fvgBull: "rgba(41,98,255,.10)", fvgBear: "rgba(255,152,0,.14)", fvgBullL: "#2962ff", fvgBearL: "#f57c00",
    premium: "rgba(229,57,53,.045)", equilibrium: "rgba(120,123,134,.055)", discount: "rgba(38,166,154,.045)",
    zoneBuy: "rgba(41,98,255,.15)", zoneBuyL: "#2962ff", zoneSell: "rgba(255,152,0,.18)", zoneSellL: "#f57c00",
    fibMain: "#f59e0b", fibBand: "rgba(245,158,11,.10)", fibLocal: "#64748b", conf: "#7c3aed",
    sweep: "#f9a825", liquidity: "#90a4ae", win: "#26a69a", loss: "#e53935", open: "#2962ff", be: "#787b86", ma20: "#ff9800", ma50: "#2962ff", ma200: "#9c27b0", cvd: "#f57c00",
  };
  const AXIS_W = 74, TIME_H = 24;
  const S = { state: null, pair: null, tf: "M15", data: null, journal: null, mon: null, evFilter: "key", hlEv: null, histFilter: "entries", resMode: (localStorage.getItem("resMode") || "full"), idx: null, sp: 9, off: 8, hover: null, show: {}, ym: null,
    legendMini: localStorage.getItem("mm_legend") ? localStorage.getItem("mm_legend") === "mini" : window.innerWidth < 1000,
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
  const MONITOR = () => !!(S.state && S.state.mode === "monitor");
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
    // Field visual tambahan diambil dari state.json bila chart payload lama belum menyalinnya.
    const A = (P() && P().tf && P().tf[D.tf]) || {};
    const d = D.digits, c = D.candles, n = c.length, pw = plotW(), L = layout();
    S.off = Math.max(5 - (n - 1), Math.min(S.off, pw / S.sp - 5));
    const [a, b] = visRange();
    let lo = Infinity, hi = -Infinity;
    for (let i = a; i <= b; i++) { lo = Math.min(lo, c[i].l); hi = Math.max(hi, c[i].h); }
    if (S.show.setup && D.setup) { lo = Math.min(lo, D.setup.sl, D.setup.tp2); hi = Math.max(hi, D.setup.sl, D.setup.tp2); }
    let Y = scale(lo, hi, L.main[0], L.main[1], 0.06);
    if (S.ym) Y = scale(S.ym.mid - S.ym.half, S.ym.mid + S.ym.half, L.main[0], L.main[1], 0);   // skala harga manual
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
      ctx.globalAlpha = mit ? 0.3 : 1; ctx.fillStyle = fill; ctx.fillRect(x0, y0, x1 - x0, Math.max(1, y1 - y0));
      if (!mit) { ctx.strokeStyle = line; ctx.lineWidth = 1; ctx.strokeRect(Math.round(x0) + 0.5, Math.round(y0) + 0.5, x1 - x0, Math.max(1, y1 - y0)); }
      ctx.globalAlpha = 1;
      if (!mit && y1 - y0 >= 9) label(txt, Math.min(x1, pw) - 6, (y0 + y1) / 2, C.fg, { align: "right", size: 10 });
    };
    if (S.show.pd) (D.pd_zones || A.pd_zones || []).forEach((z) => {
      const fill = z.kind === "premium" ? C.premium : z.kind === "discount" ? C.discount : C.equilibrium;
      const x0 = xOf(idxOf(z.t)) - S.sp / 2, y0 = Y.y(z.top), y1 = Y.y(z.bottom);
      ctx.fillStyle = fill; ctx.fillRect(x0, y0, pw - x0, Math.max(1, y1 - y0));
      label(z.name || z.kind, pw - 6, (y0 + y1) / 2, z.kind === "premium" ? C.red : z.kind === "discount" ? C.up : "#787b86", { align: "right", size: 9 });
    });
    if (S.show.mit) {
      (D.ob_mit || []).filter((z) => (z.scope === "internal" ? S.show.iob : S.show.sob)).forEach((z) => {
        const internal = z.scope === "internal";
        zone(z, z.dir === "bull" ? (internal ? C.iobBull : C.sobBull) : (internal ? C.iobBear : C.sobBear),
          z.dir === "bull" ? (internal ? C.iobBullL : C.sobBullL) : (internal ? C.iobBearL : C.sobBearL), "", true);
      });
      if (S.show.fvg) (D.fvg_mit || []).forEach((z) => zone(z, z.dir === "bull" ? C.fvgBull : C.fvgBear, z.dir === "bull" ? C.fvgBullL : C.fvgBearL, "", true));
    }
    // v19: mode minimal – OB/FVG aktif hanya N terdekat harga per arah (zona lama tidak memenuhi chart)
    const near = (arr) => {
      if (!S.show.poiLimit) return arr || [];
      const px = c[n - 1].c, out = [];
      ["bull", "bear"].forEach((dr) => out.push(...(arr || []).filter((z) => z.dir === dr)
        .sort((p, q) => Math.min(Math.abs(px - p.top), Math.abs(px - p.bottom)) - Math.min(Math.abs(px - q.top), Math.abs(px - q.bottom))).slice(0, 3)));
      return out;
    };
    near((D.ob || []).filter((z) => (z.scope === "internal" ? S.show.iob : S.show.sob))).forEach((z) => {
      const internal = z.scope === "internal", tag = internal ? "iOB" : "sOB";
      zone(z, z.dir === "bull" ? (internal ? C.iobBull : C.sobBull) : (internal ? C.iobBear : C.sobBear),
        z.dir === "bull" ? (internal ? C.iobBullL : C.sobBullL) : (internal ? C.iobBearL : C.sobBearL),
        `${z.dir === "bull" ? "Bull" : "Bear"} ${tag} · ${z.status || "fresh"}`);
    });
    if (S.show.fvg) near(D.fvg).forEach((z) => zone(z, z.dir === "bull" ? C.fvgBull : C.fvgBear, z.dir === "bull" ? C.fvgBullL : C.fvgBearL,
      `${z.dir === "bull" ? "Bull" : "Bear"} FVG${z.context ? " ★" : ""} · ${z.filter_mode || ""}`));
    // ---- v18: BUY AREA (biru) / SELL AREA (oranye) – model Sweep Reversal / CHoCH Retest ----
    const ZST = { AREA: "AREA", SETUP: "SETUP", CONFIRMED: "CONFIRMED", INVALID: "invalid", EXPIRED: "expired" };
    const ZMD = { retest: " · Retest", sweep: " · Sweep" };
    const zonesArr = S.show.zone ? (D.zones || A.zones || []) : [];
    zonesArr.forEach((z) => {
      const dead = z.stage === "INVALID" || z.stage === "EXPIRED" || !!z.end_t;
      if (dead && !S.show.mit) return;
      const bull = z.dir === "bull";
      const x0 = xOf(idxOf(z.t)) - S.sp / 2, x1 = z.end_t ? xOf(idxOf(z.end_t)) + S.sp / 2 : pw;
      if (x1 < 0 || x0 > pw) return;
      const y0 = Y.y(z.top), y1 = Y.y(z.bottom), h = Math.max(1, y1 - y0);
      ctx.globalAlpha = dead ? 0.35 : 1;
      ctx.fillStyle = bull ? C.zoneBuy : C.zoneSell; ctx.fillRect(x0, y0, x1 - x0, h);
      ctx.save(); ctx.strokeStyle = bull ? C.zoneBuyL : C.zoneSellL; ctx.lineWidth = z.stage === "CONFIRMED" ? 1.6 : 1;
      ctx.setLineDash(z.stage === "CONFIRMED" ? [] : [5, 3]);
      ctx.strokeRect(Math.round(x0) + 0.5, Math.round(y0) + 0.5, x1 - x0, h); ctx.restore();
      ctx.globalAlpha = 1;
      if (!dead) label(`${bull ? "BUY" : "SELL"} ${ZST[z.stage] || z.stage} · ${z.score}/${z.max_score || 10}${ZMD[z.model] || ""}`,
        Math.max(x0, 0) + 5, y0 + 7, bull ? C.zoneBuyL : C.zoneSellL, { size: 9, bold: true });
    });
    // ---- v19: Auto Fibonacci – H4 utama (tebal oranye + zona 50–61.8) & Fib lokal TF aktif (tipis) ----
    const fibDraw = (fb, primary) => {
      if (!fb || !fb.levels) return;
      const col = primary ? C.fibMain : C.fibLocal;
      const xa = Math.max(0, xOf(idxOf(Math.min(fb.anchor100.t, fb.anchor0.t))) - S.sp / 2);
      if (xa > pw) return;
      const dead = fb.status === "invalid";
      ctx.save(); ctx.globalAlpha = dead ? 0.35 : 1;
      if (primary && fb.gold) { const y0 = Y.y(fb.gold[1]), y1 = Y.y(fb.gold[0]); ctx.fillStyle = C.fibBand; ctx.fillRect(xa, y0, pw - xa, Math.max(1, y1 - y0)); }
      const lab = [];
      fb.levels.forEach((lv) => {
        if (lv.r === 0.236) return;                                   // minimalis: 23.6 tidak digambar
        const y = Y.y(lv.price), key = lv.r === 0.5 || lv.r === 0.618;
        hline(y, xa, pw, col, primary ? [] : [4, 3], primary ? (key ? 1.5 : 1) : 0.8);
        const txt = `${fb.tf} ${+(lv.r * 100).toFixed(1)}%` + (primary ? ` (${fx(lv.price, D.digits)})` : "");
        if (primary) label(txt, Math.max(xa, 0) + 6, y - 7, col, { size: 10, bold: key, bg: "rgba(255,255,255,.75)" });
        else lab.push([txt, y]);
      });
      if (!primary) {
        const x0 = xOf(idxOf(fb.anchor100.t)), x1 = xOf(idxOf(fb.anchor0.t));
        ctx.save(); ctx.strokeStyle = col; ctx.setLineDash([2, 3]); ctx.lineWidth = 0.9; ctx.beginPath();
        ctx.moveTo(x0, Y.y(fb.anchor100.price)); ctx.lineTo(x1, Y.y(fb.anchor0.price)); ctx.stroke(); ctx.restore();
        const lx = x1 + 8 < pw - 70 ? x1 + 8 : pw - 6, la = x1 + 8 < pw - 70 ? "left" : "right";
        lab.forEach(([t, y]) => label(t, lx, y - 6, col, { size: 9, align: la }));
      }
      ctx.restore();
    };
    if (S.show.fibH4) fibDraw(D.fib_primary || (P() && P().fib_primary), true);
    if (S.show.fibL && D.fib_local) fibDraw(D.fib_local, false);
    // ---- v19: area confluence / OB+FVG overlap dari Monitoring Events ----
    if (S.show.conf && S.mon && D.tf === S.mon.tf) {
      S.mon.events.forEach((e) => {
        if ((e.type !== "CONFLUENCE" && e.type !== "OB_FVG_OVERLAP") || e.top == null) return;
        const i = S.idx.get(e.t); if (i == null || i < a - 30 || i > b) return;
        const x0 = xOf(i) - S.sp / 2, x1 = Math.min(pw, xOf(Math.min(n - 1, i + 12)) + S.sp / 2), y0 = Y.y(e.top), y1 = Y.y(e.bottom);
        const cf = e.type === "CONFLUENCE";
        ctx.save(); ctx.globalAlpha = S.hlEv === e.id ? 1 : 0.85;
        ctx.fillStyle = cf ? "rgba(124,58,237,.12)" : "rgba(100,116,139,.10)"; ctx.fillRect(x0, y0, x1 - x0, Math.max(1, y1 - y0));
        ctx.strokeStyle = cf ? C.conf : C.fibLocal; ctx.lineWidth = S.hlEv === e.id ? 2 : 1; ctx.setLineDash(cf ? [] : [3, 3]);
        ctx.strokeRect(Math.round(x0) + 0.5, Math.round(y0) + 0.5, x1 - x0, Math.max(1, y1 - y0)); ctx.restore();
        label(cf ? `★ ${e.score}/${e.max_score}` : "OB+FVG", x0 + 3, y0 - 6, cf ? C.conf : C.fibLocal, { size: 9, bold: cf });
      });
      if (S.hlEv) { const e = S.mon.events.find((x) => x.id === S.hlEv), i = e ? S.idx.get(e.t) : null;
        if (i != null) { ctx.save(); ctx.strokeStyle = C.conf; ctx.setLineDash([2, 2]); ctx.beginPath(); ctx.moveTo(xOf(i) + 0.5, 0); ctx.lineTo(xOf(i) + 0.5, L.main[1]); ctx.stroke(); ctx.restore(); } }
    }
    if (S.show.ma) {
      [["ma200", C.ma200], ["ma50", C.ma50], ["ma20", C.ma20]].forEach(([k, col]) => {
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
      const bx = Math.round(x - bw / 2) + 0.5, by = Math.round(Math.min(yo, yc)) + 0.5, bh = Math.max(1, Math.round(Math.abs(yc - yo)));
      ctx.strokeStyle = C.border;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, Y.y(k.h)); ctx.lineTo(Math.round(x) + 0.5, Y.y(k.l)); ctx.stroke();
      ctx.fillRect(bx, by, Math.max(1, Math.round(bw) - 1), bh);
      if (bw >= 3) ctx.strokeRect(bx, by, Math.max(1, Math.round(bw) - 1), bh);
    }
    ctx.globalAlpha = 1;
    // ---- v18: label kecil BUY/SELL hanya pada candle konfirmasi ----
    zonesArr.forEach((z) => {
      if (!z.confirm || ((z.stage === "INVALID" || z.end_t) && !S.show.mit)) return;
      const i = S.idx.get(z.confirm.t); if (i == null || i < a || i > b) return;
      const k = c[i], x = xOf(i), bull = z.dir === "bull";
      label(bull ? "BUY" : "SELL", x, bull ? Y.y(k.l) + 6 : Y.y(k.h) - 6, "#fff",
        { align: "center", size: 9, bold: true, bg: bull ? C.up : C.red, base: bull ? "top" : "bottom" });
    });
    if (S.show.struct) {
      (D.events || []).forEach((e) => {
        const x0 = xOf(idxOf(e.from_t || e.t)), x1 = xOf(idxOf(e.t)); if (x1 < 0 || x0 > pw) return;
        const col = e.dir === "bull" ? C.up : C.red, y = Y.y(e.level);
        const internal = e.scope === "internal", eventLabel = e.label || (internal ? "i" : "") + e.type;
        hline(y, x0, x1, col, internal ? [2, 3] : e.type === "CHoCH" ? [] : [5, 4], internal ? 0.9 : e.type === "CHoCH" ? 1.6 : 1.2);
        label(eventLabel, (x0 + x1) / 2, e.dir === "bull" ? y - 2 : y + 3, col, { align: "center", size: internal ? 9 : 10, bold: e.type === "CHoCH", base: e.dir === "bull" ? "bottom" : "top" });
      });
    }
    if (S.show.levels) {
      (D.liquidity || A.liquidity || []).filter((p) => (p.touches || 1) >= 2 || p.scope === "swing").forEach((p) => {
        const x0 = xOf(idxOf(p.t)); if (x0 > pw) return;
        hline(Y.y(p.level), x0, pw, C.liquidity, [2, 3], 0.8);
        label((p.label || p.side) + (p.scope === "swing" ? "" : " i"), pw - 5, Y.y(p.level), C.liquidity, { align: "right", size: 9 });
      });
      if (S.show.mit) (D.liquidity_done || []).filter((p) => (p.touches || 1) >= 2 || p.scope === "swing").forEach((p) => {
        const x0 = xOf(idxOf(p.t)), x1 = xOf(idxOf(p.end_t)); if (x1 < 0 || x0 > pw) return;
        ctx.globalAlpha = 0.55; hline(Y.y(p.level), x0, x1, p.status === "swept" ? C.sweep : C.liquidity, [1, 3], 0.8); ctx.globalAlpha = 1;
        if (x1 - x0 > 40) label((p.label || p.side) + " · " + p.status, x1 - 3, Y.y(p.level) - 1, C.liquidity, { align: "right", size: 8, base: "bottom" });
      });
      (D.strong_weak || A.strong_weak || []).forEach((p) => {
        const txt = `${p.strength === "strong" ? "Strong" : p.strength === "weak" ? "Weak" : "Neutral"} ${p.side === "high" ? "High" : "Low"}`;
        hline(Y.y(p.level), xOf(idxOf(p.t)), pw, p.strength === "strong" ? C.fg : C.liquidity, [6, 4], 0.8);
        label(txt, pw - 5, Y.y(p.level), p.strength === "strong" ? C.fg : C.liquidity, { align: "right", size: 8 });
      });
    }
    if (S.show.sweep) {
      (D.sweeps || []).forEach((s) => {
        const x0 = xOf(idxOf(s.from_t)), x1 = xOf(idxOf(s.t)); if (x1 < 0 || x0 > pw) return;
        hline(Y.y(s.level), x0, x1, C.sweep, [2, 3], 1);
        const y = Y.y(s.price);
        label("Sweep", x1, s.dir === "bull" ? y + 4 : y - 4, "#000", { align: "center", size: 9, bg: C.sweep, base: s.dir === "bull" ? "top" : "bottom" });
      });
    }
    if (S.show.setup && D.setup) {
      const s = D.setup, x0 = xOf(n - 1) - 120;
      ctx.fillStyle = "rgba(38,166,154,.08)"; ctx.fillRect(x0, Y.y(Math.max(s.entry, s.tp2)), pw - x0, Math.abs(Y.y(s.tp2) - Y.y(s.entry)));
      ctx.fillStyle = "rgba(239,83,80,.08)"; ctx.fillRect(x0, Y.y(Math.max(s.entry, s.sl)), pw - x0, Math.abs(Y.y(s.sl) - Y.y(s.entry)));
      [["ENTRY", s.entry, C.fg], ["SL", s.sl, C.red], ["TP1", s.tp1, C.up], ["TP2", s.tp2, C.up]].forEach(([t, v, col]) => {
        hline(Y.y(v), x0, pw, col, [6, 4], 1.2); label(t + " " + fx(v, d), x0, Y.y(v) - 2, col, { size: 10, bold: true, base: "bottom" });
      });
    }
    if (D.signal === "BUY" || D.signal === "SELL") {
      let li = n - 1; while (li > 0 && c[li].forming) li--;
      const k = c[li], x = xOf(li);
      if (D.signal === "BUY") { const y = Y.y(k.l) + 8; tri(x, y, 1, C.up); label("SETUP BUY", x, y + 12, C.up, { align: "center", bold: true, base: "top" }); }
      else { const y = Y.y(k.h) - 8; tri(x, y, -1, C.red); label("SETUP SELL", x, y - 12, C.red, { align: "center", bold: true, base: "bottom" }); }
    }
    if (S.show.hist && S.journal) drawHistory(D, Y, a, b, pw);
    const last = c[n - 1];
    hline(Y.y(last.c), 0, pw, "#131722", [1, 3], 1);
    ctx.restore();
    {
      const ly = Y.y(last.c), rem = last.t + (TF_SEC[D.tf] || 0) - Math.floor(Date.now() / 1000);
      const cd = rem > 0 ? (rem >= 3600 ? Math.floor(rem / 3600) + ":" : "") + String(Math.floor(rem % 3600 / 60)).padStart(2, "0") + ":" + String(rem % 60).padStart(2, "0") : "";
      ctx.fillStyle = "#000"; ctx.fillRect(pw, ly - 10, AXIS_W, cd ? 34 : 20);
      label(fx(last.c, d), pw + 5, ly, "#fff", { size: 11, bold: true });
      if (cd) label(cd, pw + 5, ly + 15, "#b2b5be", { size: 10 });
    }

    // ---- pane volume ----
    if (L.vol) {
      hline(L.vol[0], 0, W, C.grid);
      let vmax = 0; for (let i = a; i <= b; i++) vmax = Math.max(vmax, c[i].v || 0);
      const Vs = scale(0, vmax || 1, L.vol[0], L.vol[1], 0);
      for (let i = a; i <= b; i++) {
        const k = c[i], x = xOf(i);
        ctx.globalAlpha = k.forming ? 0.3 : 0.45; ctx.fillStyle = k.c >= k.o ? C.up : "#787b86";
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
        ctx.globalAlpha = 0.5; ctx.fillStyle = del[i] >= 0 ? C.up : C.red;
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
      ctx.save(); ctx.setLineDash([4, 4]); ctx.strokeStyle = "#787b86"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, L.total); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, Math.round(hv.y) + 0.5); ctx.lineTo(pw, Math.round(hv.y) + 0.5); ctx.stroke(); ctx.restore();
      if (hv.y < L.main[1]) label(fx(Y.p(hv.y), d), pw + 2, hv.y, "#fff", { bg: "#131722" });
      label(wib(c[i].t, "full").replace(" WIB", ""), x, L.total + 12, "#fff", { bg: "#131722", align: "center" });
      legend(i);
    } else legend(n - 1);
  }

  // ---- hasil sesuai mode: FULL (TP2/SL) atau PM (TP1 50% + break-even) ----
  function res(r) {
    if (S.resMode === "pm" && r.status_pm) return { st: r.status_pm, r: r.result_pm_r, exit: r.exit_t_pm };
    return { st: r.status, r: r.result_r, exit: r.exit_t };
  }
  const resCol = (x) => x.st === "OPEN" ? C.open : x.st === "BE" ? C.be : x.r > 0 ? C.win : C.loss;
  const resMark = (x) => x.st === "OPEN" ? "•" : x.st === "BE" ? "BE" : x.r > 0 ? "✓" : "✗";

  // ---- riwayat sinyal ----
  // Setup: WARNA = ARAH (hijau ▲ di bawah candle = BUY, merah ▼ di atas candle = SELL),
  //        huruf B/S + simbol hasil (✓ TP2, ✗ SL, BE, • open) dengan warna hasil.
  // Entry top-down: kotak posisi gaya TradingView + label hasil.
  function drawHistory(D, Y, a, b, pw) {
    const c = D.candles; if (!c.length || b < a) return;
    const t0 = c[Math.min(c.length - 1, Math.max(0, a))].t, t1 = c[Math.min(c.length - 1, Math.max(0, b))].t + (TF_SEC[D.tf] || 0);
    const J = S.journal, inView = (r) => (r.exit_t || r.close_t + 1e9) >= t0 && r.t <= t1;
    J.setups.filter((r) => r.tf === D.tf && inView(r)).forEach((r) => {
      const i = idxOf(r.t); if (i < 0) return;
      const x = xOf(i), k = c[i], bull = r.dir === "bull", xr = res(r);
      const y = bull ? Y.y(k.l) + 14 : Y.y(k.h) - 14;
      tri(x, y, bull ? 1 : -1, bull ? C.up : C.red);
      if (S.sp >= 5) label((bull ? "B" : "S") + " " + resMark(xr), x, bull ? y + 12 : y - 12, resCol(xr), { align: "center", size: 9, bold: true, base: bull ? "top" : "bottom" });
    });
    J.entries.filter(inView).forEach((r) => {
      const x_ = res(r), i0 = idxOf(r.close_t), i1 = r.exit_t ? idxOf(r.exit_t) : c.length - 1;
      if (i0 < 0) return;
      const x0 = xOf(i0), x1 = Math.max(x0 + 24, xOf(i1) + S.sp / 2), bull = r.dir === "bull";
      const yE = Y.y(r.entry), yS = Y.y(r.sl), yT = Y.y(r.tp2);
      ctx.fillStyle = "rgba(38,166,154,.13)"; ctx.fillRect(x0, Math.min(yE, yT), x1 - x0, Math.abs(yT - yE));
      ctx.fillStyle = "rgba(229,57,53,.13)"; ctx.fillRect(x0, Math.min(yE, yS), x1 - x0, Math.abs(yS - yE));
      hline(yE, x0, x1, C.fg, [], 1); hline(Y.y(r.tp1), x0, x1, C.win, [3, 3], 0.8);
      if (r.tp1_t != null) { const it = idxOf(r.tp1_t); if (it >= 0) { ctx.fillStyle = C.win; ctx.beginPath(); ctx.arc(xOf(it), Y.y(r.tp1), 3, 0, 7); ctx.fill(); } }
      const txt = `${r.signal} ${x_.st === "OPEN" ? "OPEN" : x_.st === "BE" ? "TP1+BE " + x_.r + "R" : (x_.r > 0 ? "+" : "") + x_.r + "R"}${r.ambig ? " ⚠" : ""}${r.src === "replay" ? " ·r" : ""}`;
      label(txt, x0 + 2, bull ? Math.max(yS, yE) + 3 : Math.min(yS, yE) - 3, "#fff", { size: 10, bold: true, bg: resCol(x_), base: bull ? "top" : "bottom" });
      if (S.hl === r.id) { ctx.strokeStyle = "#ff6d00"; ctx.lineWidth = 2; ctx.strokeRect(x0 - 3, Math.min(yS, yT) - 3, x1 - x0 + 6, Math.abs(yT - yS) + 6); }
    });
  }

  let legendKey = "";
  function legend(i) {
    const D = S.data, k = D.candles[i], d = D.digits;
    if (!k) return;
    const key = D.symbol + D.tf + i + "|" + k.c + "|" + k.h + "|" + k.l + "|" + k.v + "|" + S.show.ma + "|" + S.legendMini;
    if (key === legendKey) return;
    legendKey = key;
    const ch = k.c - k.o, pct = (ch / k.o) * 100;
    if (S.legendMini) {
      $("legend").innerHTML = `<div><b>${esc(D.symbol)}</b> · ${esc(D.tf)} <span class="o ${k.c >= k.o ? "up" : "dn"}">C ${fx(k.c, d)} (${ch >= 0 ? "+" : ""}${fx(pct, 2)}%)</span></div>`;
      return;
    }
    const mav = (key) => { const m = (D[key] || []).find((x) => x[0] === k.t); return m ? fx(m[1], d) : "-"; };
    const vol = k.v != null ? `<span class="o muted">Vol ${fq(k.v)} · Buy ${k.v ? Math.round(100 * (k.bv || 0) / k.v) : 0}% · Δ ${fq(2 * (k.bv || 0) - k.v)}</span>` : "";
    $("legend").innerHTML = `<div><b>${esc(D.symbol)}</b> · ${esc(D.tf)} · <span class="muted">${wib(k.t, "full")}${k.forming ? " (berjalan)" : ""}</span></div>
      <div class="${k.c >= k.o ? "up" : "dn"}"><span class="o">O ${fx(k.o, d)}</span><span class="o">H ${fx(k.h, d)}</span><span class="o">L ${fx(k.l, d)}</span><span class="o">C ${fx(k.c, d)}</span><span class="o">${ch >= 0 ? "+" : ""}${fx(ch, d)} (${fx(pct, 2)}%)</span></div>
      ${vol ? "<div>" + vol + "</div>" : ""}
      ${S.show.ma ? `<div><span class="o" style="color:${C.ma20}">MA20 ${mav("ma20")}</span><span class="o" style="color:${C.ma50}">MA50 ${mav("ma50")}</span><span class="o" style="color:${C.ma200}">MA200 ${mav("ma200")}</span></div>` : ""}`;
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
      drag = { x0: relX(e), y0: relY(e), off: S.off, ym: S.ym ? { ...S.ym } : null, moved: false, touch: e.pointerType !== "mouse", t0: performance.now(), hist: [[performance.now(), relX(e)]] };
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
      S.off -= (mid - pinch.mid) / S.sp;                           // ikut geser saat pinch
      return;
    }
    if (drag) {
      const dx = x - drag.x0, dy = y - drag.y0;
      if (!drag.moved && Math.hypot(dx, dy) > (drag.touch ? 6 : 2)) drag.moved = true;
      if (drag.moved) {
        S.off = drag.off - dx / S.sp;                                // chart ikut arah jari
        if (S.ym && drag.ym && S.L) {                                // skala manual: geser atas/bawah juga
          const ppx = (2 * drag.ym.half) / (S.L.main[1] - S.L.main[0] - 12);
          S.ym.mid = drag.ym.mid + dy * ppx;
        }
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
        S.off -= (v * dt) / S.sp; v *= Math.pow(0.94, dt / 16);
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
    else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) { S.off += e.deltaX / S.sp; redraw(); } // geser trackpad
    else zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, relX(e));
  }, { passive: false });
  cv.addEventListener("contextmenu", (e) => e.preventDefault());
  // ---- tombol hide/unhide info kiri atas ----
  function legendBtn() {
    $("legend-btn").textContent = S.legendMini ? "▸ Info" : "▾ Info";
    $("legend-btn").setAttribute("aria-expanded", String(!S.legendMini));
    $("legend").classList.toggle("mini", S.legendMini);
  }
  $("legend-btn").addEventListener("click", () => {
    S.legendMini = !S.legendMini; localStorage.setItem("mm_legend", S.legendMini ? "mini" : "full");
    legendKey = ""; legendBtn(); redraw();
  });
  legendBtn();

  // ---- sumbu harga kanan: tarik atas = candle membesar, tarik bawah = mengecil ----
  const ya = $("yaxis");
  function setYM(v) {
    S.ym = v;
    cv.style.touchAction = v ? "none" : "pan-y";                   // skala manual: chart bisa digeser vertikal
    $("yauto").hidden = !v;
  }
  function startYM() { if (!S.ym && S.Y) setYM({ mid: (S.Y.hi + S.Y.lo) / 2, half: (S.Y.hi - S.Y.lo) / 2 }); }
  let yd = null, yTap = 0;
  ya.addEventListener("pointerdown", (e) => {
    if (!S.data) return;
    e.preventDefault(); ya.setPointerCapture(e.pointerId); stopInertia();
    startYM();
    yd = { y0: e.clientY, half: S.ym ? S.ym.half : 0, moved: false, t0: performance.now() };
  });
  ya.addEventListener("pointermove", (e) => {
    if (!yd || !S.ym) return;
    const dy = e.clientY - yd.y0;
    if (Math.abs(dy) > 3) yd.moved = true;
    S.ym.half = Math.max(1e-9, yd.half * Math.exp(dy * 0.006));
    redraw();
  });
  ya.addEventListener("pointerup", () => {
    if (!yd) return;
    const tap = !yd.moved && performance.now() - yd.t0 < 300; yd = null;
    if (!tap) return;
    const now = performance.now();
    if (now - yTap < 350) { setYM(null); redraw(); yTap = 0; }      // ketuk 2x sumbu = AUTO
    else yTap = now;
  });
  ya.addEventListener("pointercancel", () => { yd = null; });
  ya.addEventListener("wheel", (e) => {
    if (!S.data) return; e.preventDefault(); startYM(); if (!S.ym) return;
    S.ym.half *= e.deltaY > 0 ? 1.1 : 1 / 1.1; redraw();
  }, { passive: false });
  ya.addEventListener("dblclick", () => { setYM(null); redraw(); });
  $("yauto").addEventListener("click", () => { setYM(null); redraw(); });

  $("zoom-in").addEventListener("click", () => zoom(1.25));
  $("zoom-out").addEventListener("click", () => zoom(0.8));
  $("zoom-reset").addEventListener("click", () => { stopInertia(); resetView(); S.hover = null; crossLock = false; redraw(); });
  function resetView() { S.sp = W < 640 ? 6 : 9; S.off = 8; setYM(null); }
  if (window.innerWidth < 1000) {
    $("toggles").open = false;
    ["table", "cvd"].forEach((k) => { document.querySelector(`.toggles input[data-k="${k}"]`).checked = false; });
  }
  // v19: pilihan layer disimpan di browser; preset Minimal = Fib H4 + Fib lokal + OB swing + FVG + confluence
  const SHOW_KEY = "mm_show_v19";
  const PRESET_MIN = { fibH4: 1, fibL: 1, conf: 1, sob: 1, fvg: 1, poiLimit: 1, vol: 1 };
  function applyShow(obj) {
    document.querySelectorAll(".toggles input").forEach((el) => { if (obj && el.dataset.k in obj) el.checked = !!obj[el.dataset.k]; S.show[el.dataset.k] = el.checked; });
    $("pine-table").hidden = !S.show.table;
  }
  function saveShow() { localStorage.setItem(SHOW_KEY, JSON.stringify(S.show)); }
  function preset(min) {
    const o = {}; document.querySelectorAll(".toggles input").forEach((el) => { o[el.dataset.k] = min ? !!PRESET_MIN[el.dataset.k] : !(MONITOR() && ["zone", "setup", "hist"].includes(el.dataset.k)); });
    if (min && window.innerWidth >= 1000) o.table = true;
    applyShow(o); saveShow(); redraw();
  }
  let savedShow = null; try { savedShow = JSON.parse(localStorage.getItem(SHOW_KEY) || "null"); } catch (e) { savedShow = null; }
  document.querySelectorAll(".toggles input").forEach((el) => {
    S.show[el.dataset.k] = el.checked;
    el.addEventListener("change", () => { S.show[el.dataset.k] = el.checked; saveShow(); $("pine-table").hidden = !S.show.table; redraw(); });
  });
  if (savedShow) applyShow(savedShow);
  $("preset-min").addEventListener("click", () => preset(true));
  $("preset-full").addEventListener("click", () => preset(false));

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
    const et = a.ma20 != null && a.ma50 != null ? (a.ma20 > a.ma50 ? ["NAIK", "up"] : ["TURUN", "dn"]) : ["-", ""];
    const rows = [
      ["Swing", (a.swing_structure || "-").toUpperCase(), cls(a.swing_structure)],
      ["Internal", (a.internal_structure || "-").toUpperCase(), cls(a.internal_structure)],
      ["HTF " + S.state.strategy.bias, bias.enough ? short(bias.trend).toUpperCase() : "DATA KURANG", cls(bias.trend)],
      ["Event", e ? `${e.label || e.type} ${e.dir === "bull" ? "▲" : "▼"} ${fx(e.level, d)}` : "-", e ? cls(e.dir) : ""],
      ["Sweep", sw ? `${sw.dir === "bull" ? "Low" : "High"} ${fx(sw.level, d)}` : "-", sw ? "yl" : ""],
      ["MA 20/50", et[0], et[1]],
      ["RSI(14)", a.rsi ?? "-", a.rsi > 70 ? "dn" : a.rsi < 30 ? "up" : ""],
      ["ATR14", fx(a.atr14 ?? a.atr, d), ""],
      ["ATR200", fx(a.atr200, d), ""],
    ];
    if (a.flow) rows.push(["Buy vol 20", a.flow.buy_pct.toFixed(0) + "%", a.flow.buy_pct >= 50 ? "up" : "dn"]);
    if (p.book) rows.push(["Book ±0,5%", "bid " + p.book["imb_0.5"].toFixed(0) + "%", p.book["imb_0.5"] >= 50 ? "up" : "dn"]);
    rows.push(["Lokasi", (a.premium_discount || "-").replace("_", " "), ""]);
    const fp = p.fib_primary, fl = a.fib;
    rows.push(["Fib " + (fp ? fp.tf : "H4"), fp ? `${fp.dir === "bull" ? "▲" : "▼"} ${fp.retrace ?? "-"}%` : "-", fp ? cls(fp.dir) : ""]);
    if (S.tf !== (fp && fp.tf)) rows.push(["Fib " + S.tf, fl ? `${fl.dir === "bull" ? "▲" : "▼"} ${fl.retrace ?? "-"}%` : "-", fl ? cls(fl.dir) : ""]);
    if (MONITOR()) {
      const le = p.monitor && p.monitor.last && p.monitor.last.slice(-1)[0];
      rows.push(["Mode", "MONITOR", ""]);
      if (le) rows.push(["Kejadian", le.title.slice(0, 26), cls(le.dir)]);
      $("pine-table").innerHTML = `<table><tr class="hd"><td>${esc(S.pair)} ${S.tf}</td><td>${fx(p.price, d)}</td></tr>` +
        rows.map(([k, v, c]) => `<tr><td>${esc(k)}</td><td class="${c}">${esc(v)}</td></tr>`).join("") + "</table>";
      $("pine-table").hidden = !S.show.table;
      return;
    }
    const zl = (a.zones || []).filter((z) => !z.end_t).slice(-1)[0];
    rows.push(["Zona", zl ? `${zl.side} ${zl.stage} ${zl.score}/10${zl.model === "retest" ? " R" : ""}` : "-", zl ? (zl.dir === "bull" ? "up" : "dn") : ""]);
    rows.push(["Setup " + S.tf, a.signal || "WAIT", a.signal === "BUY" ? "up" : a.signal === "SELL" ? "dn" : ""]);
    rows.push(["Keputusan", p.overall.signal, p.overall.signal === "BUY" ? "up" : p.overall.signal === "SELL" ? "dn" : ""]);
    $("pine-table").innerHTML = `<table><tr class="hd"><td>${esc(S.pair)} ${S.tf}</td><td>${fx(p.price, d)}</td></tr>` +
      rows.map(([k, v, c]) => `<tr><td>${esc(k)}</td><td class="${c}">${esc(v)}</td></tr>`).join("") + "</table>";
    $("pine-table").hidden = !S.show.table;
  }
  const checklist = (items) => `<ul class="check">${(items || []).map(([ok, t]) => `<li class="${ok === null ? "na" : ok ? "ok" : "no"}">${esc(t)}</li>`).join("")}</ul>`;
  const meter = (title, pct, left, right) => `<div class="meter"><div class="lab"><span>${esc(title)}</span><span><b class="up">${esc(left)}</b> · <b class="dn">${esc(right)}</b></span></div><div class="bar2"><b style="width:${Math.max(0, Math.min(100, pct)).toFixed(1)}%"></b><s></s></div></div>`;

  function zoneList(zs, d) {
    const act = (zs || []).filter((z) => !z.end_t).slice(-5).reverse();
    if (!act.length) return `<h4>Zona BUY/SELL</h4><p class="muted">Belum ada area aktif di ${esc(S.tf)}.</p>`;
    return `<h4>Zona BUY/SELL (skor maks 10)</h4><ul class="list">` + act.map((z) => {
      const why = (z.reasons || []).filter((r) => r[0]).map((r) => r[1]).join(" · ");
      const md = z.model_name ? ` · <i>${esc(z.model_name)}</i>` : "";
      const si = z.struct_invalid != null ? ` · struktur gagal ${z.dir === "bull" ? "&lt;" : "&gt;"} ${fx(z.struct_invalid, d)}` : "";
      return `<li class="${z.dir === "bull" ? "up" : "dn"}"><b>${z.side} ${esc(z.stage)}</b> ${z.score}/10${md} · ${esc(z.kind)} ${fx(z.bottom, d)} – ${fx(z.top, d)}
        <div class="muted">${esc(why)} · invalid bila close ${z.dir === "bull" ? "&lt;" : "&gt;"} ${fx(z.invalidation, d)}${si}</div></li>`;
    }).join("") + "</ul>";
  }
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
    g.fillStyle = "#f8f9fd"; g.fillRect(0, 0, w, hgt);
    const y = (v) => hgt - 4 - (v / 100) * (hgt - 8);
    g.strokeStyle = "#d1d4dc"; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(0, y(50)); g.lineTo(w, y(50)); g.stroke(); g.setLineDash([]);
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
    const late = !fx_.market_closed || cr.symbols.length ? ageMin > 20 : false;
    const pct = Math.min(100, (100 * fx_.req_month) / (fx_.quota || 5000));
    let w = "";
    Object.entries(st.errors || {}).forEach(([k, v]) => (w += `<div class="warn err">⚠️ ${esc(k)}: ${esc(v)}</div>`));
    (st.warnings || []).forEach((v) => (w += `<div class="warn">⚠️ ${esc(v)}</div>`));
    const tg = st.telegram || {}, run = st.run || {};
    const tgTxt = tg.configured === false ? "🔴 Token/Chat ID tidak terbaca engine"
      : tg.pending_retry ? `🟠 ${tg.pending_retry} pesan menunggu retry`
      : (tg.failed ? "🟠 Ada pesan gagal" : "🟢 Connected");
    const log = (tg.log || []).slice(-15).reverse();
    const logHtml = log.length ? `<h4>Notifikasi Telegram (status per pesan)</h4><div class="tbl-wrap ev-wrap"><table class="tbl"><thead><tr><th>Waktu</th><th>Status</th><th>Pesan</th><th>Percobaan</th><th>message_id / error</th></tr></thead><tbody>${log.map((x) =>
      `<tr><td>${wib(x.ts, "full").replace(" WIB", "")}</td><td><span class="nf ${esc(x.status)}">${esc(x.status)}</span></td><td>${esc(x.title)}<div class="muted">${esc(x.detail || "")}</div></td><td>${x.attempts}</td><td class="muted">${x.status === "SENT" ? esc((x.message_ids || []).join(", ") || "ok") : esc(x.error || "")}</td></tr>`).join("")}</tbody></table></div>` : "";
    el.innerHTML = `<h3>System Status</h3><div class="scols"><div>
      <div class="srow"><span>Engine</span><span>${late ? "🔴 Terlambat" : "🟢 Running"}</span></div>
      <div class="srow"><span>Telegram</span><span>${tgTxt}</span></div>
      <div class="srow"><span>Notif terakhir terkirim</span><span>${wib(tg.last_sent_ts, "full")}</span></div>
      <div class="srow"><span>Run engine</span><span>${esc(run.trigger || "-")} · ${run.duration_sec ?? "-"} dtk</span></div>
      <div class="srow"><span>Jeda antar run</span><span>${run.avg_gap_min != null ? "rata-rata " + run.avg_gap_min + " mnt" : "-"}${run.gap_min != null ? " · terakhir " + run.gap_min + " mnt" : ""}</span></div>
      <div class="srow"><span>Last Update</span><span>${wib(st.updated_ts, "full")}</span></div></div>
      ${fx_.symbols.length ? `<div><div class="srow"><span>RealMarketAPI (forex)</span><span>${fx_.market_closed ? "💤 Pasar tutup" : fx_.ok ? "🟢 Connected" : "🔴 Error"}</span></div>
      <div class="srow"><span>XAUUSD M5 tiap ${fx_.interval_min || "-"} mnt (normal ${fx_.fast_min}/${fx_.slow_min})</span><span>terakhir ${wib(fx_.fetch_ts)} · next ±${wib(fx_.next_ts)}</span></div>
      <div class="muted" style="margin-top:6px">Kuota bulan ini: <b>${fx_.req_month} / ${fx_.quota}</b> (${pct.toFixed(1)}%) · proyeksi akhir bulan ±${fx_.projected_month}${fx_.throttled ? " · ⚠️ diperlambat" : ""}</div>
      <div class="bar"><div style="width:${pct}%;background:${pct > 85 ? "var(--dn)" : pct > 65 ? "var(--org)" : "var(--acc)"}"></div></div></div>` : ""}
      ${cr.symbols.length ? `<div><div class="srow"><span>Binance (crypto)</span><span>${cr.ok ? "🟢 Connected" : "🔴 Error"}</span></div>
      <div class="srow"><span>${cr.symbols.length} pair · M1 tiap ${cr.interval_min} mnt</span><span>next ±${wib(cr.next_ts)}</span></div>
      <div class="srow"><span>Host</span><span>${esc((cr.host || "-").replace("https://", ""))}</span></div>
      <div class="srow"><span>Live browser</span><span>${S.live.ok ? "🟢 WebSocket" : "◌ snapshot"}</span></div></div>` : ""}
      </div>${w}${el.id === "p-status" ? logHtml : ""}`;
    $("age").textContent = (late ? "🔴 " : "🟢 ") + "Data " + ageMin + " mnt lalu";
  }

  function fibCard(fb, d, local) {
    if (!fb) return `<div class="fibcard${local ? " loc" : ""}"><div class="t">Fib ${local ? "lokal" : "utama"}</div><span class="muted">Belum ada impuls valid (BOS/CHoCH).</span></div>`;
    const bull = fb.dir === "bull", lv = (r) => (fb.levels.find((x) => x.r === r) || {}).price;
    return `<div class="fibcard${local ? " loc" : ""}"><div class="t">Fib ${esc(fb.tf)} ${local ? "lokal" : "utama"} · <span class="${cls(fb.dir)}">${bull ? "Bullish" : "Bearish"}</span> <span class="muted">(${esc(fb.scope)}${fb.status === "invalid" ? ", INVALID" : ""}${fb.extending ? ", 0% masih berjalan" : ""})</span></div>
      <div class="fibgrid"><span>100% (awal)</span><b>${fx(fb.anchor100.price, d)}</b><span>0% (ujung)</span><b>${fx(fb.anchor0.price, d)}</b>
      <span>38.2%</span><b>${fx(lv(0.382), d)}</b><span>78.6%</span><b>${fx(lv(0.786), d)}</b>
      <span>Zona 50–61.8</span><b class="g">${fx(fb.gold[0], d)} – ${fx(fb.gold[1], d)}</b><span>Retrace</span><b>${fb.retrace ?? "-"}% <span class="muted">${esc(fb.zone || "")}</span></b></div>
      <div class="muted">Anchor dari ${esc(fb.event.label)} ${wib(fb.event.t, "full")} · ${bull ? "100% = swing low → 0% = swing high" : "100% = swing high → 0% = swing low"}</div></div>`;
  }
  function panels() {
    const st = S.state, p = P(), d = p.digits, ov = p.overall, s = ov.setup;
    document.body.classList.toggle("forex", p.market !== "crypto");
    if (MONITOR()) {
      const fl = (p.tf[S.tf] || {}).fib, mtf = (st.monitor || {}).tf || "M15";
      const last = ((p.monitor || {}).last || []).slice(-5).reverse();
      $("p-decision").innerHTML = `<h3>Monitoring ${esc(S.pair)} · Auto Fibonacci</h3>
        <div class="big">${fx(p.price, d)} ${sig("MONITOR")}</div>
        <div class="muted">Sinyal BUY/SELL dinonaktifkan sementara – engine mencatat kejadian ${esc(mtf)} untuk dicek manual.</div>
        ${fibCard(p.fib_primary, d, false)}${S.tf !== (p.fib_primary || {}).tf ? fibCard(fl, d, true) : ""}
        ${last.length ? `<h4>Kejadian terakhir</h4><ul class="list">${last.map((e) => `<li class="${cls(e.dir)}">${wib(e.close_t)} ${esc(e.title)} <span class="nf ${esc(e.notif)}">${esc(e.notif || "")}</span></li>`).join("")}</ul>` : (st.monitor && st.monitor.symbols.includes(S.pair) ? "" : `<p class="muted">Monitoring kejadian aktif untuk: ${esc((st.monitor || {}).symbols || "-")}.</p>`)}`;
    } else
    $("p-decision").innerHTML = `<h3>Keputusan ${esc(S.pair)} (${st.strategy.bias} → ${st.strategy.setup} → ${st.strategy.confirm})</h3>
      <div class="big">${fx(p.price, d)} ${sig(ov.signal)}</div>
      <div class="muted">${p.market === "crypto" ? "Binance · candle M1" : "RealMarketAPI · candle M5"} terakhir: ${wib(p.last_ts, "full")}</div>
      ${s ? `<div class="kv"><div><small>Entry</small><b>${fx(s.entry, d)}</b></div><div><small>Stop Loss</small><b class="dn">${fx(s.sl, d)}</b></div><div><small>TP1</small><b class="up">${fx(s.tp1, d)}</b></div><div><small>TP2</small><b class="up">${fx(s.tp2, d)}</b></div></div>` : ""}
      ${checklist(ov.reasons)}<div class="reason">${esc(ov.summary)}</div>`;
    const a = p.tf[S.tf] || {};
    if (!a.ma20 && !(a.events || []).length) $("p-detail").innerHTML = `<h3>Detail ${esc(S.pair)} ${S.tf}</h3><p>${esc(a.reason || "Data belum cukup.")}</p>`;
    else {
      const lc = a.last_candle || {};
      const z = (arr, k) => (arr || []).slice(-4).map((x) => `<li class="${cls(x.dir)}">${x.dir === "bull" ? "Bull" : "Bear"} ${x.scope === "internal" ? "i" : x.scope === "swing" ? "s" : ""}${k}${x.context ? " ★" : ""} ${fx(x.bottom, d)} – ${fx(x.top, d)} · ${esc(x.status || x.filter_mode || "")}</li>`).join("");
      $("p-detail").innerHTML = `<h3>${MONITOR() ? "Detail" : "Kondisi entry"} ${esc(S.pair)} ${S.tf} ${MONITOR() ? "" : sig(a.signal)}</h3>
        <div class="muted">Candle close ${wib(lc.t, "full")}: O ${fx(lc.o, d)} · H ${fx(lc.h, d)} · L ${fx(lc.l, d)} · C ${fx(lc.c, d)} <b class="${cls(lc.dir)}">${esc(lc.dir || "")}</b></div>
        ${MONITOR() ? "" : checklist(a.checklist) + `<div class="reason">${esc(a.reason)}</div>`}<ul class="list">${z(a.ob, "OB")}${z(a.fvg, "FVG")}</ul>
        ${MONITOR() ? "" : zoneList(a.zones, d)}`;
    }
    flowPanel();
    statusInto($("p-status"));
    eventsPanel();
    historyPanel();
    $("mtf-title").textContent = "Multi-Timeframe " + S.pair;
    { const th = $("mtf").querySelectorAll("thead th"); if (th.length) th[th.length - 1].textContent = MONITOR() ? "Fib" : "Setup"; }
    const tfs = ["W", "D", "H4", "H1", "M15", "M5"].concat(p.market === "crypto" ? ["M1"] : []);
    $("mtf").querySelector("tbody").innerHTML = tfs.map((tf) => {
      const x = p.tf[tf] || {}, cur = tf === S.tf ? "cur" : "";
      if (!x.enough && !x.ma20) return `<tr data-tf="${tf}" class="${cur}"><td><b>${tf}</b></td><td colspan="9" class="muted">Data belum cukup (${x.bars || 0}/${x.need || 60} candle) – terisi otomatis</td></tr>`;
      const e = (x.events || []).slice(-1)[0], sw = (x.sweeps || []).slice(-1)[0];
      return `<tr data-tf="${tf}" class="${cur}"><td><b>${tf}</b></td><td class="${cls(x.trend)}">${esc(short(x.trend))}</td>
        <td>${e ? `<span class="${cls(e.dir)}">${esc(e.label || e.type)} ${e.dir === "bull" ? "▲" : "▼"}</span> ${fx(e.level, d)}` : "-"}</td>
        <td>${sw ? `<span class="yl">${sw.dir === "bull" ? "Low" : "High"}</span> ${fx(sw.level, d)}` : "-"}</td>
        <td>${fx(x.ma20, d)}</td><td>${fx(x.ma50, d)}</td><td>${x.rsi ?? "-"}</td><td>${fx(x.atr, d)}</td>
        <td class="cr-col ${x.flow ? (x.flow.buy_pct >= 50 ? "up" : "dn") : ""}">${x.flow ? x.flow.buy_pct.toFixed(0) + "%" : "-"}</td><td>${MONITOR() ? (x.fib ? `<span class="${cls(x.fib.dir)}">Fib ${x.fib.retrace ?? "-"}%</span>` : "-") : sig(x.signal)}</td></tr>`;
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
    S.pair = s; S.journal = null; S.hl = null; S.mon = null; S.hlEv = null; loadJournal(); loadMonitor();
    if (!isCrypto() && S.tf === "M1") S.tf = "M5";
    tabs(); pineTable(); panels(); loadChart(true);
    if (isCrypto()) liveStart(s); else liveStop();
    liveBadge(); renderSide();
  }
  function selectTf(t) { S.tf = t; tabs(); pineTable(); panels(); loadChart(true); }

  // chart v2: candle disimpan sebagai array kolom + MA sejajar candle (hemat ukuran, seluruh riwayat)
  function unpack(D) {
    if (D.v !== 2) return D;
    const cols = D.cols;
    D.candles = D.rows.map((r) => { const k = {}; cols.forEach((c, j) => { if (c === "forming") { if (r[j]) k.forming = true; } else k[c] = r[j]; }); return k; });
    ["ma20", "ma50", "ma200"].forEach((m) => { const arr = D[m] || []; D[m] = []; arr.forEach((v, i) => { if (v != null) D[m].push([D.candles[i].t, v]); }); });
    delete D.rows;
    return D;
  }
  async function loadJournal() {
    try { S.journal = await getJSON("data/journal_" + S.pair + ".json"); }
    catch (e) { S.journal = null; }
    historyPanel(); redraw();
  }

  // ======================= v19: MONITORING EVENTS =======================
  const EV_KEY = ["STRUCTURE", "SWEEP", "HTF_FIB", "FIB_INVALID", "OB_FVG_OVERLAP", "CONFLUENCE"];
  const EV_NAME = { STRUCTURE: "Struktur", SWEEP: "Sweep", FVG_NEW: "FVG baru", OB_NEW: "OB baru", HTF_FIB: "Fib utama", LOCAL_FIB: "Fib lokal", FIB_INVALID: "Fib invalid", OB_FVG_OVERLAP: "OB+FVG", CONFLUENCE: "Confluence" };
  async function loadMonitor() {
    const want = S.pair;
    try { const m = await getJSON("data/monitor_" + want + ".json"); if (S.pair === want) S.mon = m; }
    catch (e) { if (S.pair === want) S.mon = null; }
    eventsPanel(); redraw();
  }
  function eventsPanel() {
    const el = $("p-events"); if (!el || !S.state) return;
    const mon = S.mon, p = P(), d = p ? p.digits : 2, cfg = S.state.monitor || {};
    if (!mon) { el.innerHTML = `<h3>Monitoring Events ${esc(S.pair || "")}</h3><p class="muted">${(cfg.symbols || []).includes(S.pair) ? "Belum ada data kejadian (menunggu run engine)." : "Monitoring kejadian aktif untuk: " + esc((cfg.symbols || []).join(", ") || "-") + " (ubah lewat Variable MONITOR_SYMBOLS)."}</p>`; return; }
    const types = Object.keys(EV_NAME);
    const rows = mon.events.filter((e) => S.evFilter === "all" ? true : S.evFilter === "key" ? EV_KEY.includes(e.type) : e.type === S.evFilter).slice().reverse();
    const cnt = {}; mon.events.forEach((e) => (cnt[e.type] = (cnt[e.type] || 0) + 1));
    const sel = `<select id="ev-filter"><option value="key" ${S.evFilter === "key" ? "selected" : ""}>Penting (struktur, sweep, fib, OB+FVG, confluence)</option><option value="all" ${S.evFilter === "all" ? "selected" : ""}>Semua kejadian</option>${types.map((t) => `<option value="${t}" ${S.evFilter === t ? "selected" : ""}>${esc(EV_NAME[t])} (${cnt[t] || 0})</option>`).join("")}</select>`;
    const body = rows.slice(0, 400).map((e) => `<tr data-t="${e.t}" data-id="${esc(e.id)}" class="${S.hlEv === e.id ? "cur" : ""}"><td>${wib(e.close_t, "full").replace(" WIB", "")}</td>
      <td>${esc(EV_NAME[e.type] || e.type)}</td><td class="${cls(e.dir)}">${e.dir === "bull" ? "▲ Bull" : "▼ Bear"}</td><td><b>${esc(e.title)}</b></td><td>${fx(e.price, d)}</td>
      <td>${e.htf && e.htf.retrace != null ? `<span class="${cls(e.htf.dir)}">${e.htf.retrace}%</span>` : "-"}</td>
      <td class="why">${esc((e.why || []).join(" · "))}</td><td><span class="nf ${esc(e.notif)}">${esc(e.notif || "-")}</span></td></tr>`).join("");
    el.innerHTML = `<h3>Monitoring Events ${esc(S.pair)} · ${esc(mon.tf)}</h3>
      <div class="hist-bar">${sel}<button type="button" class="tbtn" id="ev-csv">⬇ CSV</button><span class="muted">Notif Telegram: ${esc((cfg.notify || []).join(", "))} · confluence ≥ ${cfg.confluence_min ?? "-"}/6</span></div>
      <div class="muted" style="margin-bottom:6px">Kejadian untuk <b>cek manual</b> – bukan sinyal entry. Fib dibaca dari candle sebelumnya (tanpa lookahead). Klik baris → chart ${esc(mon.tf)} lompat ke candle tsb. Status notif: SENT = diterima Telegram (ada message_id), RETRYING = dicoba ulang run berikutnya, FAILED = gagal, OFF = tipe ini tidak dinotifkan, REPLAY = tercatat dari data historis.</div>
      ${rows.length ? `<div class="tbl-wrap ev-wrap"><table class="tbl"><thead><tr><th>Waktu (WIB)</th><th>Tipe</th><th>Arah</th><th>Kejadian</th><th>Close</th><th>Retrace H4</th><th>Detail</th><th>Notif</th></tr></thead><tbody>${body}</tbody></table></div>` : `<p class="muted">Belum ada kejadian untuk filter ini.</p>`}`;
    $("ev-filter").addEventListener("change", (e) => { S.evFilter = e.target.value; eventsPanel(); });
    $("ev-csv").addEventListener("click", () => {
      const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
      const lines = [["waktu_wib", "tipe", "arah", "kejadian", "close", "retrace_h4", "detail", "notif"].join(",")].concat(rows.slice().reverse().map((e) => [wib(e.close_t, "full"), e.type, e.dir, e.title, e.price, e.htf ? e.htf.retrace : "", (e.why || []).join(" | "), e.notif].map(q).join(",")));
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv" }));
      a.download = `events_${S.pair}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    });
    el.querySelectorAll("tr[data-t]").forEach((tr) => tr.addEventListener("click", () => jumpEvent(tr.dataset.id, +tr.dataset.t)));
  }
  async function jumpEvent(id, t) {
    S.hlEv = id;
    const tf = (S.mon && S.mon.tf) || "M15";
    if (tf !== S.tf) { S.tf = tf; tabs(); pineTable(); panels(); await loadChart(true); }
    const i = Math.max(0, idxOf(t)), n = S.data.candles.length;
    S.off = plotW() / S.sp / 2 - (n - 1 - i);
    setYM(null); eventsPanel(); redraw();
    $("cv").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // ======================= RIWAYAT SINYAL & BACKTEST =======================
  function histRows() {
    const J = S.journal; if (!J) return [];
    if (S.histFilter === "entries") return J.entries.slice().reverse();
    return J.setups.filter((r) => r.tf === S.histFilter).reverse();
  }
  function statsOf(rows) {
    const R = rows.map((r) => ({ r, x: res(r) }));
    const done = R.filter((o) => o.x.st !== "OPEN" && o.x.r != null), win = done.filter((o) => o.x.r > 0);
    const gw = win.reduce((s, o) => s + o.x.r, 0), gl = -done.filter((o) => o.x.r < 0).reduce((s, o) => s + o.x.r, 0);
    let peak = 0, eq = 0, dd = 0, streak = 0, maxL = 0;
    done.slice().sort((p, q) => (p.x.exit || 0) - (q.x.exit || 0)).forEach((o) => { eq += o.x.r; peak = Math.max(peak, eq); dd = Math.max(dd, peak - eq); streak = o.x.r < 0 ? streak + 1 : 0; maxL = Math.max(maxL, streak); });
    return { total: rows.length, closed: done.length, open: rows.length - done.length, win: win.length,
      wr: done.length ? (100 * win.length / done.length).toFixed(1) : "-", net: (gw - gl).toFixed(2),
      avg: done.length ? ((gw - gl) / done.length).toFixed(2) : "-", pf: gl ? (gw / gl).toFixed(2) : "-", dd: dd.toFixed(2), maxL,
      tp1: done.length ? (100 * done.filter((o) => o.r.tp1_hit).length / done.length).toFixed(0) : "-",
      amb: rows.filter((r) => r.ambig).length, old: rows.filter((r) => r.ov !== 2).length };
  }
  function historyPanel() {
    const el = $("p-history"); if (!el || !S.state) return;
    const J = S.journal, p = P(), d = p ? p.digits : 2;
    const tfl = isCrypto() ? ["M1", "M5", "M15", "H1", "H4", "D", "W"] : ["M5", "M15", "H1", "H4", "D", "W"];
    if (S.histFilter !== "entries" && !tfl.includes(S.histFilter)) S.histFilter = "entries";
    const opts = [["entries", `Entry top-down (${S.state.strategy.bias}→${S.state.strategy.setup}→${S.state.strategy.confirm})`]].concat(tfl.map((t) => [t, "Setup " + t]));
    const rows = histRows(), st = statsOf(rows), pm = S.resMode === "pm";
    const sel = `<select id="hist-filter">${opts.map(([v, t]) => `<option value="${v}" ${v === S.histFilter ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
    const mode = `<select id="hist-mode"><option value="full" ${pm ? "" : "selected"}>Hasil: posisi penuh TP2 / SL</option><option value="pm" ${pm ? "selected" : ""}>Hasil: 50% di TP1 + SL ke entry (BE)</option></select>`;
    const note = pm ? "Mode BE: 50% ditutup di TP1, sisa SL dipindah ke entry. TP1 lalu balik = TP1+BE (untung kecil), bukan −1R."
                    : "Mode penuh: seluruh posisi ke TP2 atau SL (−1R). TP1✓ hanya penanda – TP1 tersentuh lalu balik ke SL tetap −1R.";
    const stTxt = (x) => x.st === "BE" ? "TP1 → BE" : x.st;
    const body = rows.slice(0, 500).map((r) => {
      const x = res(r);
      const out = x.st === "OPEN" ? `<span class="muted">OPEN</span>` : `<b class="${x.r > 0 ? "up" : x.r < 0 ? "dn" : ""}">${x.r > 0 ? "+" : ""}${x.r}R</b>`;
      const flag = (r.tp1_hit && x.st !== "BE" ? " · TP1✓" : "") + (r.ambig ? ` <span title="SL & TP tersentuh di candle yang sama – urutan tidak diketahui, dihitung terburuk">⚠</span>` : "");
      return `<tr data-id="${esc(r.id)}" data-t="${r.close_t}" data-tf="${esc(r.tf)}" class="${S.hl === r.id ? "cur" : ""}"><td>${wib(r.close_t, "full").replace(" WIB", "")}</td>
        <td>${sig(r.signal)}</td><td>${esc(r.tf)}</td><td>${fx(r.entry, d)}</td><td class="dn">${fx(r.sl, d)}</td><td class="up">${fx(r.tp1, d)}</td><td class="up">${fx(r.tp2, d)}</td>
        <td>${r.rr}</td><td>${esc(stTxt(x))}${flag}</td><td>${out}</td><td>${r.mfe_r ?? "-"} / ${r.mae_r ?? "-"}</td>
        <td class="muted">${esc(r.model)}</td><td class="muted">${r.src === "live" ? "live" : "replay"}</td></tr>`;
    }).join("");
    el.innerHTML = `<h3>Riwayat Sinyal & Backtest ${esc(S.pair || "")}</h3>
      <div class="hist-bar">${sel}${mode}<button type="button" class="tbtn" id="hist-csv">⬇ CSV</button></div>
      <div class="muted" style="margin-bottom:6px">${note} Tanda chart: <b class="up">▲ B</b> = setup BUY, <b class="dn">▼ S</b> = setup SELL; ✓ TP2 · ✗ SL · BE · • open. Candle ambigu (⚠) dihitung hasil terburuk. Klik baris → chart lompat ke sinyal.${st.old ? ` <b>${st.old} sinyal lama menunggu dihitung ulang pada run engine berikutnya.</b>` : ""}</div>
      <div class="kv hist-kv"><div><small>Sinyal</small><b>${st.total}</b></div><div><small>Selesai / open</small><b>${st.closed} / ${st.open}</b></div>
      <div><small>Winrate</small><b>${st.wr}%</b></div><div><small>Net</small><b class="${st.net >= 0 ? "up" : "dn"}">${st.net}R</b></div>
      <div><small>Rata-rata</small><b>${st.avg}R</b></div><div><small>Profit factor</small><b>${st.pf}</b></div>
      <div><small>Max drawdown</small><b class="dn">${st.dd}R</b></div><div><small>Kalah beruntun</small><b>${st.maxL}</b></div><div><small>TP1 tersentuh</small><b>${st.tp1}%</b></div><div><small>Ambigu ⚠</small><b>${st.amb}</b></div></div>
      ${rows.length ? `<div class="tbl-wrap hist-wrap"><table class="tbl"><thead><tr><th>Waktu (WIB)</th><th>Sinyal</th><th>TF</th><th>Entry</th><th>SL</th><th>TP1</th><th>TP2</th><th>RR</th><th>Status</th><th>Hasil</th><th>MFE/MAE R</th><th>Model</th><th>Sumber</th></tr></thead><tbody>${body}</tbody></table></div>`
        : `<p class="muted">${J ? "Belum ada sinyal untuk filter ini pada data tersimpan." : "Riwayat belum tersedia (menunggu run engine berikutnya)."}</p>`}`;
    $("hist-filter").addEventListener("change", (e) => { S.histFilter = e.target.value; historyPanel(); });
    $("hist-mode").addEventListener("change", (e) => { S.resMode = e.target.value; localStorage.setItem("resMode", S.resMode); historyPanel(); redraw(); });
    $("hist-csv").addEventListener("click", () => exportCsv(rows, d));
    el.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => jumpTo(tr.dataset.id, +tr.dataset.t, tr.dataset.tf)));
  }
  function exportCsv(rows, d) {
    const head = ["waktu_wib", "symbol", "signal", "tf", "entry", "sl", "tp1", "tp2", "rr", "status_full", "result_full_r", "status_be", "result_be_r", "tp1_hit", "ambigu", "mfe_r", "mae_r", "exit_wib", "model", "sumber"];
    const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
    const lines = [head.join(",")].concat(rows.slice().reverse().map((r) => [wib(r.close_t, "full"), r.symbol, r.signal, r.tf, r.entry, r.sl, r.tp1, r.tp2, r.rr, r.status, r.result_r, r.status_pm, r.result_pm_r, r.tp1_hit, r.ambig, r.mfe_r, r.mae_r, r.exit_t ? wib(r.exit_t, "full") : "", r.model, r.src].map(q).join(",")));
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `riwayat_${S.pair}_${S.histFilter}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  async function jumpTo(id, t, tf) {
    S.hl = id;
    const want = S.histFilter === "entries" ? (S.state.strategy.setup) : tf;
    if (want && want !== S.tf) { S.tf = want; tabs(); pineTable(); panels(); await loadChart(true); }
    const i = Math.max(0, idxOf(t)), n = S.data.candles.length;
    S.off = plotW() / S.sp / 2 - (n - 1 - i);
    setYM(null); historyPanel(); redraw();
    $("cv").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function loadChart(reset) {
    const msg = $("chart-msg");
    try {
      const D = unpack(await getJSON("data/chart_" + S.pair + "_" + S.tf + ".json"));
      S.data = D; S.idx = new Map(D.candles.map((k, i) => [k.t, i])); S.live.lastMin = null;
      if (reset) resetView();
      msg.hidden = !!D.candles.length;
      if (!D.candles.length) msg.textContent = "Belum ada candle untuk " + S.pair + " " + S.tf;
      redraw();
    } catch (e) {
      S.data = null; redraw(); msg.hidden = false; msg.textContent = "Chart belum tersedia (" + e.message + ")";
    }
  }
  // ======================= AUTO REFRESH =======================
  // state.json dicek tiap 45 detik (cache dilewati). Chart & panel hanya diperbarui bila
  // updated_ts berubah -> hemat & posisi zoom/geser chart tetap. Tab tersembunyi = jeda.
  // v10-lazy compatibility marker is kept for the existing GitHub Actions check.
  const APP_VERSION = "v19-fib-monitor";
  const POLL_MS = 45 * 1000;
  let lastTs = 0, polling = false;
  async function load(force) {
    if (polling) return;
    polling = true;
    try {
      let st;
      try { st = await getJSON("data/state.json"); }
      catch (e) {
        if (!S.state) { $("age").textContent = "Gagal memuat data"; $("chart-msg").hidden = false; $("chart-msg").textContent = "Data belum ada. Jalankan workflow Market Engine dulu. (" + e.message + ")"; }
        else $("age").textContent = "⚠️ Gagal cek data baru";
        return;
      }
      const changed = st.updated_ts !== lastTs;
      if (!changed && !force) { ageTick(); return; }
      lastTs = st.updated_ts; S.state = st;
      const syms = Object.keys(S.state.pairs || {});
      if (!syms.length) { $("age").textContent = "Belum ada data"; return; }
      const first = !S.pair;
      if (!S.pair || !S.state.pairs[S.pair]) S.pair = syms[0];
      if (!isCrypto() && S.tf === "M1") S.tf = "M5";
      if (first && !savedShow && MONITOR()) preset(true);       // v19: pertama kali buka -> tampilan minimal
      tabs(); pineTable(); panels(); await loadChart(first);   // reset zoom hanya saat pertama dibuka
      loadJournal(); loadMonitor();
      if (isCrypto()) liveStart(S.pair); else liveStop();
      liveBadge(); renderSide();
    } finally { polling = false; }
  }
  // label "x mnt lalu" diperbarui tiap 30 detik tanpa mengambil data
  function ageTick() {
    if (!S.state) return;
    const m = Math.round((Date.now() / 1000 - S.state.updated_ts) / 60);
    $("age").textContent = (m > 20 ? "🔴 " : "🟢 ") + "Data " + m + " mnt lalu";
    $("age").title = "Dashboard " + APP_VERSION + " · cek otomatis tiap " + POLL_MS / 1000 + " dtk";
  }
  new ResizeObserver(resize).observe(cv);
  $("refresh").addEventListener("click", () => load(true));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  window.addEventListener("online", () => load());
  load(true);
  setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
  setInterval(ageTick, 30 * 1000);
  setInterval(() => { if (!document.hidden && S.data) redraw(); }, 1000);   // hitung mundur candle
  console.log("Market Monitor", APP_VERSION);
})();
