"""Liquidity-first top-down SMC engine. MA/RSI are display-only."""
import config
from indicators import ma, rsi, atr
from smc import model
from orderflow import flow_summary
MIN_BARS=100; ANALYZE_BARS=500

def _r(x,d): return None if x is None else round(x,d)
def _zl(xs,d):
    out=[]
    for z in xs:
        q={k:v for k,v in z.items() if k not in ("created","index")}
        for k in ("top","bottom","mid","level","price"):
            if k in q: q[k]=_r(q[k],d)
        out.append(q)
    return out

def _near(z,p,a): return z["bottom"]-0.15*a <= p <= z["top"]+0.15*a

def analyze_tf(tf, closed, price, d):
    need=30 if tf=="D" else 20 if tf=="W" else MIN_BARS
    res={"tf":tf,"bars":len(closed),"need":need,"enough":len(closed)>=need,"price":_r(price,d)}
    if len(closed)<min(need,20):
        res.update(signal="WAIT",trend="Data belum cukup",structure=None,setup=None,checklist=[],reason="Data belum cukup.",events=[],sweeps=[],liquidity=[],fvg=[],ob=[],fvg_mit=[],ob_mit=[],flow=None,series={"times":[],"ma20":[],"ma50":[],"ma100":[]})
        return res
    bars=closed[-ANALYZE_BARS:]; closes=[b["c"] for b in bars]
    m20,m50,m100=ma(closes,20),ma(closes,50),ma(closes,100); rr=rsi(closes,14); aa=atr(bars,14)
    A=aa[-1] or sum(b["h"]-b["l"] for b in bars[-14:])/14
    sm=model(bars,A,config.SWING_LEN,max(A*config.EQUAL_LIQ_ATR,10**(-d)))
    last=bars[-1]; seq=sm["sequences"][-1] if sm["sequences"] else None
    direction=seq["dir"] if seq else None
    zones=[]
    if seq:
        zones=[z for z in (seq.get("fvg"),seq.get("ob")) if z]
    z=next((x for x in zones if _near(x,price,A)),None)
    retrace=bool(z and last["t"]>z["t"])
    target=None
    if direction:
        candidates=[x for x in sm["liquidity"] if (x["side"]=="BSL" and direction=="bull" and x["level"]>price) or (x["side"]=="SSL" and direction=="bear" and x["level"]<price)]
        if candidates: target=min(candidates,key=lambda x:abs(x["level"]-price))
    premium_discount=None
    if sm["liquidity"]:
        hi=max(x["level"] for x in sm["liquidity"]); lo=min(x["level"] for x in sm["liquidity"]); mid=(hi+lo)/2
        premium_discount="discount" if price<=mid else "premium"
    location_ok=direction=="bull" and premium_discount=="discount" or direction=="bear" and premium_discount=="premium"
    checklist=[
      [bool(seq),"Liquidity sweep terhubung ke urutan setup"],
      [bool(seq and seq.get("event")),"Displacement + CISD/MSS terkonfirmasi"],
      [bool(z),"Harga retrace ke contextual FVG/OB"],
      [bool(location_ok),f"Lokasi {premium_discount or '-'} sesuai arah"],
      [bool(target),"Opposing liquidity tersedia sebagai target"]]
    signal="WAIT"; setup=None
    if seq and retrace and location_ok and target:
        signal="BUY" if direction=="bull" else "SELL"
        extreme=seq["sweep"]["price"]
        sl=extreme-(config.SL_BUFFER_ATR*A) if direction=="bull" else extreme+(config.SL_BUFFER_ATR*A)
        risk=abs(price-sl); tp2=target["level"]
        tp1=price+(tp2-price)*0.5
        rr_target=abs(tp2-price)/risk if risk else 0
        if rr_target>=config.MIN_TARGET_RR:
            setup={"id":seq["id"],"entry":_r(price,d),"sl":_r(sl,d),"tp1":_r(tp1,d),"tp2":_r(tp2,d),"risk":_r(risk,d),"rr":round(rr_target,2),"target":{"side":target["side"],"level":_r(target["level"],d)},"zone":{"kind":z["kind"],"top":_r(z["top"],d),"bottom":_r(z["bottom"],d),"t":z["t"]}}
        else: signal="WAIT"; checklist.append([False,f"RR ke liquidity target {rr_target:.2f} < {config.MIN_TARGET_RR}"])
    trend="Bullish" if direction=="bull" else "Bearish" if direction=="bear" else "Sideways"
    reason=(f"{signal}: Liquidity→Sweep→Displacement→CISD/MSS→{z['kind'] if z else 'POI'}→Retracement→Liquidity Target." if signal!="WAIT" else "WAIT: urutan SMC liquidity-first belum lengkap.")
    fl=flow_summary(bars,20) if "v" in last else None
    res.update(trend=trend,structure=direction,signal=signal,setup=setup,checklist=checklist,reason=reason,
      ma20=_r(m20[-1],d),ma50=_r(m50[-1],d),ma100=_r(m100[-1],d),rsi=_r(rr[-1],1),atr=_r(A,d),premium_discount=premium_discount,
      last_candle={"t":last["t"],"o":_r(last["o"],d),"h":_r(last["h"],d),"l":_r(last["l"],d),"c":_r(last["c"],d),"dir":"Bullish" if last["c"]>=last["o"] else "Bearish"},
      liquidity=_zl(sm["liquidity"],d),events=_zl(sm["events"],d),sweeps=_zl(sm["sweeps"],d),fvg=_zl(sm["fvg"],d),ob=_zl(sm["order_blocks"],d),fvg_mit=_zl(sm["fvg_mit"],d),ob_mit=_zl(sm["ob_mitigated"],d),flow=fl,
      series={"times":[b["t"] for b in bars],"ma20":m20,"ma50":m50,"ma100":m100})
    return res

def overall(tfs,book=None,trades=None):
    b=tfs.get(config.BIAS_TF) or {}; s=tfs.get(config.SETUP_TF) or {}; c=tfs.get(config.CONFIRM_TF) or {}
    want={"BUY":"bull","SELL":"bear"}.get(s.get("signal")); reasons=[]
    bias=b.get("structure"); reasons.append([bias is not None,f"HTF {config.BIAS_TF}: draw/narrative {bias or 'netral'}"])
    reasons.append([want is not None,f"Setup {config.SETUP_TF}: {s.get('reason','-')}"])
    confirm=c.get("structure"); reasons.append([want is not None and confirm==want,f"Konfirmasi {config.CONFIRM_TF}: {confirm or 'belum ada CISD/MSS'}"])
    if want and bias in (None,want) and confirm==want:
        return {"signal":s["signal"],"setup":s.get("setup"),"reasons":reasons,"summary":f"{s['signal']} valid: HTF narrative → setup liquidity {config.SETUP_TF} → CISD/MSS {config.CONFIRM_TF}."}
    return {"signal":"WAIT","setup":None,"pending":s.get("signal"),"reasons":reasons,"summary":"WAIT – top-down liquidity sequence belum selaras."}
