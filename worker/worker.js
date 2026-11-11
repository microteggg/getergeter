/**
 * Cloudflare Worker – penjawab command Telegram (gratis, tanpa kartu kredit).
 * Worker TIDAK memanggil RealMarketAPI / Binance; hanya membaca state.json & chart PNG di GitHub Pages.
 *
 * Variables and Secrets:
 *   TELEGRAM_BOT_TOKEN (Secret)  token BotFather
 *   WEBHOOK_SECRET     (Secret)  teks acak huruf/angka
 *   SETUP_KEY          (Secret)  teks acak untuk membuka /setup
 *   ALLOWED_USER_IDS   (Text)    ID Telegram yang boleh akses, pisahkan koma: 123456789,987654321
 *   DATA_URL           (Text)    URL GitHub Pages, mis. https://microteggg.github.io/alertcuan
 *
 * v19 – WATCHDOG ENGINE (Cron Trigger Cloudflare, gratis & jauh lebih tepat waktu dari cron GitHub):
 *   Tambahkan Cron Trigger tiap 5 menit (ekspresi lihat PANDUAN_v19.md) di Worker (Settings → Triggers → Cron Triggers), lalu isi:
 *   GH_TOKEN           (Secret)  Fine-grained PAT GitHub: repo ini saja, permission "Actions: Read and write"
 *   GH_REPO            (Text)    owner/repo, mis. microteggg/getergeter
 *   GH_WORKFLOW        (Text)    opsional, default engine.yml
 *   DISPATCH_AFTER_MIN (Text)    opsional, default 6 – picu engine bila data lebih tua dari N menit
 *   ALERT_CHAT_ID      (Text)    opsional, chat penerima peringatan engine mati (default: ID pertama ALLOWED_USER_IDS)
 *   Alur tiap 5 menit: baca umur state.json -> bila >= DISPATCH_AFTER_MIN dan tidak ada run yang sedang
 *   berjalan/antre -> workflow_dispatch engine.yml (workflow yang ter-disable otomatis di-enable dulu).
 *   Bila umur data >= 30 menit -> kirim peringatan Telegram (maks ±1x per jam).
 */
const TG = (env, m) => `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${m}`;
const TFS = ["M1", "M5", "M15", "H1", "H4", "D", "W"];
const base = (env) => (env.DATA_URL || "").replace(/\/+$/, "");

async function tg(env, method, body) {
  const r = await fetch(TG(env, method), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}
const reply = (env, chatId, text) =>
  tg(env, "sendMessage", { chat_id: chatId, text: String(text || "-").slice(0, 4000), parse_mode: "HTML", disable_web_page_preview: true });

async function loadState(env) {
  const r = await fetch(`${base(env)}/data/state.json?t=${Date.now()}`, { cf: { cacheTtl: 0 } });
  if (!r.ok) throw new Error("state.json HTTP " + r.status);
  return r.json();
}
function ageText(st) {
  const min = Math.round((Date.now() / 1000 - st.updated_ts) / 60);
  const late = min > 20;
  return `\n${late ? "🔴" : "🟢"} Umur data: ${min} menit${late ? " – engine terlambat, cek GitHub Actions" : ""}`;
}

async function handle(env, msg) {
  const chatId = msg.chat.id;
  const allowed = (env.ALLOWED_USER_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!allowed.includes(String(msg.from && msg.from.id))) {
    return reply(env, chatId, `⛔ Anda tidak memiliki akses ke bot ini.\nUser ID Anda: <code>${msg.from ? msg.from.id : "-"}</code>`);
  }
  const parts = msg.text.trim().split(/\s+/);
  const cmd = parts[0].split("@")[0].toLowerCase();
  const arg = (parts[1] || "").toUpperCase();
  let st;
  try { st = await loadState(env); } catch (e) { return reply(env, chatId, "⚠️ Data dashboard belum bisa dibaca: " + e.message); }
  const M = st.messages || {};
  const syms = st.symbols || Object.keys(st.pairs || {});

  if (cmd === "/start" || cmd === "/help") return reply(env, chatId, M.help);
  if (cmd === "/status") return reply(env, chatId, M.status + ageText(st));
  if (cmd === "/price") return reply(env, chatId, M.price);
  if (cmd === "/signal") return reply(env, chatId, M.signal);
  if (cmd === "/lastupdate") return reply(env, chatId, M.lastupdate + ageText(st));
  if (cmd === "/riwayat" || cmd === "/history") return reply(env, chatId, M.riwayat || "Riwayat belum tersedia.");
  if (cmd === "/events" || cmd === "/kejadian") return reply(env, chatId, M.events || "Belum ada kejadian tercatat.");
  if (cmd === "/dashboard") return reply(env, chatId, `🌐 Dashboard: ${base(env)}/`);
  for (const s of syms) {
    const low = s.toLowerCase();
    if (cmd === "/" + low) return reply(env, chatId, M["pair_" + s] || "Data belum ada.");
    if (cmd === "/chart" + low) {
      const p = (st.pairs || {})[s] || {};
      let tf = TFS.includes(arg) ? arg : "H1";
      if (tf === "M1" && p.market !== "crypto") tf = "M5";
      const a = (p.tf || {})[tf] || {};
      const caption = `<b>${s} ${tf}</b> | Setup ${a.signal || "WAIT"} · Keputusan ${(p.overall || {}).signal || "WAIT"} | ${String(a.trend || "-").split(" (")[0]} | RSI ${a.rsi ?? "-"}\n` +
        `Update: ${new Date((st.updated_ts + 7 * 3600) * 1000).toISOString().slice(11, 16)} WIB\nTF lain: /chart${low} ${p.market === "crypto" ? "m1 · " : ""}m15 · h4 · d · w`;
      const res = await tg(env, "sendPhoto", { chat_id: chatId, photo: `${base(env)}/charts/${s}_${tf}.png?v=${st.updated_ts}`, caption, parse_mode: "HTML" });
      if (!res.ok) return reply(env, chatId, `⚠️ Chart ${s} ${tf} belum tersedia (${res.description || "error"}).`);
      return res;
    }
  }
  return reply(env, chatId, "Command tidak dikenal. Ketik /help");
}

async function setup(env, host) {
  const hook = await tg(env, "setWebhook", { url: `https://${host}/telegram`, secret_token: env.WEBHOOK_SECRET, allowed_updates: ["message"], drop_pending_updates: true });
  let st = { symbols: ["XAUUSD", "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "TRXUSDT", "DOGEUSDT", "ZECUSDT", "LINKUSDT", "ADAUSDT"] };
  try { st = await loadState(env); } catch (e) { /* default */ }
  const commands = [
    { command: "status", description: "Cek sistem hidup" },
    { command: "price", description: "Harga terakhir" },
    { command: "signal", description: "Ringkasan monitoring + Auto Fibonacci" },
    { command: "events", description: "Kejadian/setup terakhir (monitoring)" },
  ];
  for (const s of st.symbols || []) {
    commands.push({ command: s.toLowerCase(), description: `Analisis ${s}` });
    commands.push({ command: "chart" + s.toLowerCase(), description: `Chart ${s} (tambah: m15/h4/d/w)` });
  }
  commands.push({ command: "lastupdate", description: "Data terakhir yang diambil" }, { command: "riwayat", description: "Statistik riwayat entry (backtest)" }, { command: "dashboard", description: "Link dashboard" }, { command: "help", description: "Daftar command" });
  return { webhook: hook, commands: await tg(env, "setMyCommands", { commands }) };
}

// ======================= v19: WATCHDOG ENGINE =======================
async function gh(env, path, method = "GET", body) {
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}${path}`, {
    method,
    headers: { authorization: `Bearer ${env.GH_TOKEN}`, accept: "application/vnd.github+json",
      "user-agent": "market-engine-watchdog", "x-github-api-version": "2022-11-28", "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  let js = null; try { js = await r.json(); } catch (e) { js = null; }
  return { status: r.status, ok: r.ok, js };
}
async function watchdog(env) {
  const out = { age_min: null, dispatched: false, reason: "" };
  let st = null;
  try { st = await loadState(env); out.age_min = Math.round((Date.now() / 1000 - st.updated_ts) / 60); }
  catch (e) { out.reason = "state.json tidak terbaca: " + e.message; }
  const wf = env.GH_WORKFLOW || "engine.yml", need = Number(env.DISPATCH_AFTER_MIN || 6);
  if (env.GH_TOKEN && env.GH_REPO && (out.age_min === null || out.age_min >= need)) {
    const runs = await gh(env, `/actions/workflows/${wf}/runs?per_page=5`);
    const busy = runs.ok && (runs.js.workflow_runs || []).some((r) => ["queued", "in_progress", "waiting", "pending", "requested"].includes(r.status));
    if (busy) out.reason = "engine sedang berjalan/antre";
    else {
      let d = await gh(env, `/actions/workflows/${wf}/dispatches`, "POST", { ref: "main" });
      if (!d.ok) {                                               // workflow ter-disable (mis. 60 hari tanpa aktivitas)
        await gh(env, `/actions/workflows/${wf}/enable`, "PUT");
        d = await gh(env, `/actions/workflows/${wf}/dispatches`, "POST", { ref: "main" });
      }
      out.dispatched = d.ok;
      out.reason = d.ok ? `dipicu (data ${out.age_min ?? "?"} mnt)` : `dispatch gagal HTTP ${d.status} ${(d.js && d.js.message) || ""}`;
    }
  } else if (!env.GH_TOKEN || !env.GH_REPO) out.reason = "GH_TOKEN / GH_REPO belum diisi";
  else out.reason = "engine normal";
  // peringatan engine mati: umur >= 30 mnt, dikirim saat menit jam 0-4 (±1x per jam, tanpa storage)
  const chat = env.ALERT_CHAT_ID || (env.ALLOWED_USER_IDS || "").split(",")[0].trim();
  if (chat && env.TELEGRAM_BOT_TOKEN && out.age_min !== null && out.age_min >= 30 && new Date().getUTCMinutes() < 5) {
    await reply(env, chat, `🔴 <b>Engine terlambat</b>\nData dashboard terakhir ${out.age_min} menit lalu.\nWatchdog: ${out.reason}.\nCek GitHub → Actions → Market Engine.`);
  }
  return out;
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(watchdog(env));
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/watchdog") {             // tes manual: /watchdog?key=SETUP_KEY
      if (!env.SETUP_KEY || url.searchParams.get("key") !== env.SETUP_KEY) return new Response("forbidden", { status: 403 });
      return new Response(JSON.stringify(await watchdog(env), null, 2), { headers: { "content-type": "application/json" } });
    }
    if (request.method === "GET" && url.pathname === "/setup") {
      if (!env.SETUP_KEY || url.searchParams.get("key") !== env.SETUP_KEY) return new Response("forbidden", { status: 403 });
      return new Response(JSON.stringify(await setup(env, url.host), null, 2), { headers: { "content-type": "application/json" } });
    }
    if (request.method === "POST" && url.pathname === "/telegram") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) return new Response("forbidden", { status: 403 });
      const update = await request.json().catch(() => ({}));
      const msg = update.message;
      if (msg && typeof msg.text === "string" && msg.text.startsWith("/")) {
        try { await handle(env, msg); } catch (e) { /* jangan sampai Telegram mengulang terus */ }
      }
      return new Response("ok");
    }
    return new Response("Market bot worker aktif");
  },
};
