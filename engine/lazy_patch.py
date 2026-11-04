"""Build-time installer for lazy candle loading.
Patches the checked-out main.py and docs assets before GitHub Pages is built.
Idempotent: safe to run on every workflow execution.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "engine" / "main.py"
APP = ROOT / "docs" / "app.js"
STYLE = ROOT / "docs" / "style.css"
INDEX = ROOT / "docs" / "index.html"
MARK = "v10-lazy"


def replace_once(text, old, new, label):
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Patch gagal ({label}): pola sumber tidak ditemukan")
    return text.replace(old, new, 1)


def patch_main():
    s = MAIN.read_text(encoding="utf-8")
    helper_anchor = "def forex_closed(now):\n"
    helper = '''def export_history(site, sym, tf, store_bars, chart_n, digits):
    """Tulis candle lama sebagai potongan JSON; part 1 adalah blok terdekat dengan chart utama."""
    hdir = os.path.join(site, "data", "history")
    os.makedirs(hdir, exist_ok=True)
    prefix = f"{sym}_{tf}_"
    for name in os.listdir(hdir):
        if name.startswith(prefix) and name.endswith(".json"):
            os.remove(os.path.join(hdir, name))
    size = max(1, int(chart_n))
    older = store_bars[:-size] if len(store_bars) > size else []
    end, part = len(older), 0
    while end > 0:
        start = max(0, end - size)
        part += 1
        jsave(os.path.join(hdir, f"{sym}_{tf}_{part}.json"), {
            "symbol": sym, "tf": tf, "part": part,
            "candles": [rnd(b, digits) for b in older[start:end]],
        })
        end = start
    return part

'''
    if "def export_history(" not in s:
        s = replace_once(s, helper_anchor, helper + helper_anchor, "helper history")

    old = '''            a = tfs[tf]
            jsave(os.path.join(self.site, "data", f"chart_{sym}_{tf}.json"),
                  {"symbol": sym, "market": market, "tf": tf, "digits": d, "candles": bars, **ema,
                   "events": a.get("events", []), "sweeps": a.get("sweeps", []), "fvg": a.get("fvg", []), "ob": a.get("ob", []),
                   "fvg_mit": a.get("fvg_mit", []), "ob_mit": a.get("ob_mit", []), "setup": a.get("setup"), "signal": a.get("signal")})'''
    new = '''            a = tfs[tf]
            history_parts = export_history(self.site, sym, tf, stores[tf], config.CHART_BARS[tf], d)
            jsave(os.path.join(self.site, "data", f"chart_{sym}_{tf}.json"),
                  {"symbol": sym, "market": market, "tf": tf, "digits": d, "candles": bars, **ema,
                   "events": a.get("events", []), "sweeps": a.get("sweeps", []), "fvg": a.get("fvg", []), "ob": a.get("ob", []),
                   "fvg_mit": a.get("fvg_mit", []), "ob_mit": a.get("ob_mit", []), "setup": a.get("setup"), "signal": a.get("signal"),
                   "history_parts": history_parts, "history_total": len(stores[tf])})'''
    if '"history_parts": history_parts' not in s:
        s = replace_once(s, old, new, "output chart")
    MAIN.write_text(s, encoding="utf-8")


def patch_app():
    s = APP.read_text(encoding="utf-8")
    old_state = '    live: { ws: null, sym: null, ok: false, book: null, tape: [], buy: 0, sell: 0, since: 0, lastMin: null, tries: 0, host: 0 } };'
    new_state = '    live: { ws: null, sym: null, ok: false, book: null, tape: [], buy: 0, sell: 0, since: 0, lastMin: null, tries: 0, host: 0 },\n    hist: { key: "", parts: 0, next: 1, loading: false, done: true } };'
    s = replace_once(s, old_state, new_state, "state history")

    old_draw = '    const [a, b] = visRange();\n    let lo = Infinity, hi = -Infinity;'
    new_draw = '    const [a, b] = visRange();\n    if (a < 30 && !S.hist.done && !S.hist.loading) setTimeout(loadOlder, 0);\n    let lo = Infinity, hi = -Infinity;'
    s = replace_once(s, old_draw, new_draw, "trigger history")

    old_load = '''  async function loadChart(reset) {
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
  }'''
    new_load = '''  async function loadChart(reset) {
    const msg = $("chart-msg"), key = S.pair + "_" + S.tf, Hs = S.hist;
    try {
      const D = await getJSON("data/chart_" + key + ".json");
      if (!reset && Hs.key === key && S.data && S.data.candles.length && D.candles.length) {
        const first = D.candles[0].t;
        const older = S.data.candles.filter((k) => k.t < first && !k.forming);
        if (older.length) D.candles = older.concat(D.candles);
      } else {
        Object.assign(Hs, { key, next: 1, loading: false });
      }
      Hs.parts = D.history_parts || 0;
      Hs.done = Hs.next > Hs.parts;
      S.data = D; S.idx = new Map(D.candles.map((k, i) => [k.t, i])); S.live.lastMin = null;
      if (reset) resetView();
      msg.hidden = !!D.candles.length;
      if (!D.candles.length) msg.textContent = "Belum ada candle untuk " + S.pair + " " + S.tf;
      redraw();
    } catch (e) {
      S.data = null; redraw(); msg.hidden = false; msg.textContent = "Chart belum tersedia (" + e.message + ")";
    }
  }
  async function loadOlder() {
    const Hs = S.hist;
    if (!S.data || Hs.loading || Hs.done) return;
    const key = Hs.key, part = Hs.next;
    Hs.loading = true; historyBadge("⏳ Memuat histori…");
    try {
      const R = await getJSON("data/history/" + key + "_" + part + ".json");
      if (Hs.key !== key || !S.data) return;
      const current = S.data.candles, first = current.length ? current[0].t : Infinity;
      const add = (R.candles || []).filter((k) => k.t < first);
      if (add.length) {
        S.data.candles = add.concat(current);
        S.idx = new Map(S.data.candles.map((k, i) => [k.t, i]));
      }
      Hs.next = part + 1; Hs.done = Hs.next > Hs.parts;
      historyBadge(Hs.done ? "✓ Awal histori tercapai (" + S.data.candles.length + " candle)" : "✓ +" + add.length + " candle", 1800);
      redraw();
    } catch (e) {
      if (Hs.key === key) { Hs.done = true; historyBadge("Histori tidak tersedia", 2500); }
    } finally {
      if (Hs.key === key) Hs.loading = false;
    }
  }
  let historyTimer = 0;
  function historyBadge(text, ms) {
    const el = $("history-badge");
    if (!el) return;
    el.textContent = text; el.hidden = false;
    clearTimeout(historyTimer);
    if (ms) historyTimer = setTimeout(() => { el.hidden = true; }, ms);
  }'''
    s = replace_once(s, old_load, new_load, "loadChart")
    s = s.replace('const APP_VERSION = "v9-light";', 'const APP_VERSION = "v10-lazy";')
    APP.write_text(s, encoding="utf-8")


def patch_index_style():
    s = INDEX.read_text(encoding="utf-8")
    s = s.replace('style.css?v=9', 'style.css?v=10lazy').replace('app.js?v=9', 'app.js?v=10lazy')
    anchor = '    <div id="chart-msg" class="chart-msg" hidden></div>'
    badge = anchor + '\n    <div id="history-badge" class="history-badge" hidden aria-live="polite"></div>'
    if 'id="history-badge"' not in s:
        s = replace_once(s, anchor, badge, "badge HTML")
    INDEX.write_text(s, encoding="utf-8")

    css = STYLE.read_text(encoding="utf-8")
    block = '''\n/* v10 lazy loading candle */
.history-badge{position:absolute;top:8px;left:50%;transform:translateX(-50%);z-index:6;padding:4px 10px;border-radius:12px;font-size:12px;background:rgba(19,23,34,.85);color:#fff;pointer-events:none;white-space:nowrap}
.history-badge[hidden]{display:none}\n'''
    if ".history-badge{" not in css:
        css += block
    STYLE.write_text(css, encoding="utf-8")


if __name__ == "__main__":
    patch_main()
    patch_app()
    patch_index_style()
    print("Lazy loading candle v10 diterapkan pada working tree build")
