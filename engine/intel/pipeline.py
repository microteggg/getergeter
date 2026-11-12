"""Orchestration for Phase 1 steps 0–5."""
from __future__ import annotations

import config
from . import confidence, data_quality, fvg_quality, liqmap, mtf, regime

VERSION = 1

GLOSSARY = {
    "BULLISH_TREND": "Struktur directional bullish dengan perjalanan harga relatif efisien.",
    "BEARISH_TREND": "Struktur directional bearish dengan perjalanan harga relatif efisien.",
    "BULLISH_EXPANSION": "Trend bullish disertai pelebaran range/volatilitas.",
    "BEARISH_EXPANSION": "Trend bearish disertai pelebaran range/volatilitas.",
    "RANGE": "Arah tidak cukup efisien; harga lebih banyak berotasi.",
    "COMPRESSION": "ATR percentile dan range jangka pendek menyusut.",
    "TRANSITION": "CHoCH atau konflik swing/internal membuat karakter belum stabil.",
    "INTACT": "Level belum dilampaui wick maupun close setelah periode pembentuknya selesai.",
    "SWEPT": "Wick melewati level lalu close kembali; bukan jaminan reversal.",
    "BROKEN": "Candle close melewati level referensi.",
}


def build_intelligence(sym, market, stores, tfs, now, price, digits):
    data = data_quality.summarize(stores, market, now)
    requested, used, d_ready = mtf.select_timeframes(market, tfs)
    top_tf = used[0] if used else ("H4" if market == "forex" else "D")
    reg = regime.build(tfs, top_tf, data["score"])
    hierarchy = mtf.build(market, tfs, reg.get("per_tf") or {})
    liquidity = liqmap.build(market, tfs, top_tf, now, price, digits)
    fvg = fvg_quality.build(tfs, hierarchy, reg.get("per_tf") or {}, data["score"])
    conf = confidence.build(reg, hierarchy, liquidity, fvg, data)
    return {
        "version": VERSION, "enabled": True, "mode": config.INTEL_MODE, "blocking": False,
        "symbol": sym, "market": market, "asof": now, "top_tf": top_tf,
        "data_quality": data, "regime": reg, "mtf": hierarchy,
        "liquidity": liquidity, "fvg_quality": fvg, "confidence": conf,
        "glossary": GLOSSARY,
        "notice": "Decision-support context only. Scores are deterministic and explainable, not calibrated win probabilities.",
    }


def telegram_suffix(intel):
    if not intel or config.INTEL_MODE != "live":
        return ""
    reg, mtf_, conf = intel["regime"], intel["mtf"], intel["confidence"]
    best = (intel.get("fvg_quality") or {}).get("best")
    direction = "BUY" if conf.get("direction") == "bull" else "SELL" if conf.get("direction") == "bear" else "MIXED"
    fvg_line = (f"\nFVG terbaik: <b>{best['tf']} {best['grade']} {best['score']}/100</b>" if best else
                "\nFVG terbaik: belum ada FVG aktif")
    return ("\n\n🧠 <b>MARKET INTELLIGENCE · LIVE</b>"
            f"\nRegime: <b>{reg.get('label')}</b> ({reg.get('strength')}/100)"
            f"\nMTF: <b>{mtf_.get('bias_label')}</b> · alignment {mtf_.get('alignment')}/100"
            f"{fvg_line}"
            f"\nContext: <b>{direction} {conf.get('score')}/100 · {conf.get('label')}</b>"
            "\n<i>Context score, bukan probabilitas; tidak memblokir sinyal.</i>")
