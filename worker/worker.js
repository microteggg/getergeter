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
    { command: "signal", description: "Sinyal semua pair" },
  ];
  for (const s of st.symbols || []) {
    commands.push({ command: s.toLowerCase(), description: `Analisis ${s}` });
    commands.push({ command: "chart" + s.toLowerCase(), description: `Chart ${s} (tambah: m15/h4/d/w)` });
  }
  commands.push({ command: "lastupdate", description: "Data terakhir yang diambil" }, { command: "riwayat", description: "Statistik riwayat entry (backtest)" }, { command: "dashboard", description: "Link dashboard" }, { command: "help", description: "Daftar command" });
  return { webhook: hook, commands: await tg(env, "setMyCommands", { commands }) };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
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
