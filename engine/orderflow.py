"""Order flow crypto (TAMPILAN SAJA – tidak memengaruhi sinyal): delta/CVD dari kline (taker buy volume), order book, dan transaksi (aggTrades).
Catatan: aggTrade field m = True berarti buyer adalah maker -> transaksi agresif JUAL."""


def candle_flow(bars):
    """Tambahkan delta & cvd per candle (butuh v & bv dari Binance)."""
    cvd = 0.0
    out = []
    for b in bars:
        if "v" not in b:
            out.append(None)
            continue
        sell = b["v"] - b["bv"]
        d = b["bv"] - sell
        cvd += d
        out.append({"t": b["t"], "v": b["v"], "buy": b["bv"], "sell": sell, "delta": d, "cvd": cvd, "tc": b.get("tc", 0)})
    return out


def flow_summary(bars, n=20):
    """Ringkasan n candle tertutup terakhir."""
    rows = [x for x in candle_flow(bars[-n:]) if x]
    if not rows:
        return None
    buy = sum(r["buy"] for r in rows)
    sell = sum(r["sell"] for r in rows)
    tot = buy + sell
    vols = [b.get("v", 0) for b in bars[-60:]]
    avg = sum(vols[:-1]) / max(1, len(vols) - 1)
    last_v = bars[-1].get("v", 0)
    return {"bars": len(rows), "buy": buy, "sell": sell, "delta": buy - sell,
            "buy_pct": 100 * buy / tot if tot else 50.0,
            "last_delta": rows[-1]["delta"], "trades": sum(r["tc"] for r in rows),
            "vol_ratio": last_v / avg if avg else None}


def book_metrics(depth, price):
    if not depth or not depth.get("bids") or not depth.get("asks"):
        return None
    bids = [(float(p), float(q)) for p, q in depth["bids"]]
    asks = [(float(p), float(q)) for p, q in depth["asks"]]
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2

    def liq(side, pct):
        lim = mid * pct / 100
        return sum(p * q for p, q in side if abs(p - mid) <= lim)

    out = {"best_bid": bb, "best_ask": ba, "spread": ba - bb, "spread_bps": (ba - bb) / mid * 1e4, "mid": mid}
    for pct in (0.1, 0.5, 1.0):
        b, a = liq(bids, pct), liq(asks, pct)
        out[f"bid_{pct}"] = b
        out[f"ask_{pct}"] = a
        out[f"imb_{pct}"] = 100 * b / (b + a) if b + a else 50.0
    out["bid_total"] = sum(p * q for p, q in bids)
    out["ask_total"] = sum(p * q for p, q in asks)
    tot = out["bid_total"] + out["ask_total"]
    out["imb_all"] = 100 * out["bid_total"] / tot if tot else 50.0
    # wall diurutkan berdasarkan nilai (harga × qty), bukan qty saja
    out["bid_walls"] = [[p, q, p * q] for p, q in sorted(bids, key=lambda x: -x[0] * x[1])[:3]]
    out["ask_walls"] = [[p, q, p * q] for p, q in sorted(asks, key=lambda x: -x[0] * x[1])[:3]]
    out["bids"] = [[p, q] for p, q in bids[:20]]
    out["asks"] = [[p, q] for p, q in asks[:20]]
    out["levels"] = [len(bids), len(asks)]
    return out


def trade_metrics(trades, large_usd=0):
    if not trades:
        return None
    rows = []
    for t in trades:
        p, q = float(t["p"]), float(t["q"])
        rows.append({"t": int(t["T"]) // 1000, "ms": int(t["T"]), "p": p, "q": q, "usd": p * q,
                     "side": "SELL" if t.get("m") else "BUY"})
    buy = sum(r["q"] for r in rows if r["side"] == "BUY")
    sell = sum(r["q"] for r in rows if r["side"] == "SELL")
    usd = sorted(r["usd"] for r in rows)
    thr = large_usd or (usd[int(len(usd) * 0.98)] if usd else 0)
    large = [r for r in rows if r["usd"] >= thr]
    return {"count": len(rows), "from": rows[0]["t"], "to": rows[-1]["t"],
            "buy": buy, "sell": sell, "delta": buy - sell, "buy_pct": 100 * buy / (buy + sell) if buy + sell else 50.0,
            "large_threshold": thr,
            "large_buy": sum(1 for r in large if r["side"] == "BUY"),
            "large_sell": sum(1 for r in large if r["side"] == "SELL"),
            "large": sorted(large, key=lambda r: -r["ms"])[:15]}
