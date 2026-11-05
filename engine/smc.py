"""SMC/ICT v3.1 - interface compatible with analysis.py v3.
External/internal structure + liquidity/sweep + contextual and visual FVG/OB.
"""
SWEEP_CONFIRM_BARS=2
MSS_WINDOW=10
OB_LOOKBACK=8


def swings(bars,k=3):
    hs=[]; ls=[]
    for i in range(k,len(bars)-k):
        w=bars[i-k:i+k+1]
        if bars[i]['h']==max(x['h'] for x in w): hs.append(i)
        if bars[i]['l']==min(x['l'] for x in w): ls.append(i)
    return hs,ls


def structure(bars,k):
    hs,ls=swings(bars,k); trend=None; events=[]; protected=None
    for i in range(k,len(bars)):
        ph=[x for x in hs if x+k<=i]; pl=[x for x in ls if x+k<=i]
        hi=ph[-1] if ph else None; lo=pl[-1] if pl else None
        # jangan pecahkan swing yang sama berulang kali
        used_h={e.get('swing_i') for e in events if e['dir']=='bull'}
        used_l={e.get('swing_i') for e in events if e['dir']=='bear'}
        if hi is not None and hi not in used_h and bars[i]['c']>bars[hi]['h']:
            typ='CHoCH' if trend=='bear' else 'BOS'; trend='bull'
            q=min(range(hi,i+1),key=lambda x:bars[x]['l']); protected=q
            events.append({'t':bars[i]['t'],'from_t':bars[hi]['t'],'type':typ,'dir':'bull','level':bars[hi]['h'],'index':i,'swing_i':hi})
        elif lo is not None and lo not in used_l and bars[i]['c']<bars[lo]['l']:
            typ='CHoCH' if trend=='bull' else 'BOS'; trend='bear'
            q=max(range(lo,i+1),key=lambda x:bars[x]['h']); protected=q
            events.append({'t':bars[i]['t'],'from_t':bars[lo]['t'],'type':typ,'dir':'bear','level':bars[lo]['l'],'index':i,'swing_i':lo})
    return {'trend':trend,'events':events,'protected':protected,'highs':hs,'lows':ls}


def dealing_range(bars,ext,price):
    p=ext.get('protected'); direction=ext.get('trend')
    if p is None or not direction: return None
    if direction=='bull':
        lo=bars[p]['l']; hi_i=max(range(p,len(bars)),key=lambda q:bars[q]['h']); hi=bars[hi_i]['h']; tlo=bars[p]['t']; thi=bars[hi_i]['t']; strong='low'; weak='high'
    else:
        hi=bars[p]['h']; lo_i=min(range(p,len(bars)),key=lambda q:bars[q]['l']); lo=bars[lo_i]['l']; thi=bars[p]['t']; tlo=bars[lo_i]['t']; strong='high'; weak='low'
    if hi<=lo:return None
    eq=(hi+lo)/2; span=hi-lo
    return {'dir':direction,'hi':hi,'lo':lo,'eq':eq,'t_hi':thi,'t_lo':tlo,'strong':strong,'weak':weak,
            'pos':round((price-lo)/span,3),'pd':'discount' if price<eq else 'premium',
            'ote_bottom':hi-.79*span if direction=='bull' else lo+.62*span,
            'ote_top':hi-.62*span if direction=='bull' else lo+.79*span,'t':min(thi,tlo)}


def liquidity_pools(bars,k,tol,ext_h=(),ext_l=()):
    hs,ls=swings(bars,k); out=[]; eh=set(ext_h); el=set(ext_l)
    for side,ids,key,ex in (('BSL',hs,'h',eh),('SSL',ls,'l',el)):
        for n,i in enumerate(ids):
            lv=bars[i][key]; kind='EXT' if i in ex else 'INT'
            for j in ids[max(0,n-5):n]:
                if abs(bars[j][key]-lv)<=tol: kind='EQH' if side=='BSL' else 'EQL'
            out.append({'id':f'{side}:{bars[i]["t"]}','side':side,'level':lv,'t':bars[i]['t'],'kind':kind,'index':i,'start':i+k})
    for p in out:
        take=None
        for q in range(p['start'],len(bars)):
            if (p['side']=='BSL' and bars[q]['h']>p['level']) or (p['side']=='SSL' and bars[q]['l']<p['level']): take=q;break
        p['taken_t']=bars[take]['t'] if take is not None else None; p['resting']=take is None
    return sorted(out,key=lambda x:x['start'])


def _disp(b,d,a,m=.8):
    body=abs(b['c']-b['o']); rng=max(b['h']-b['l'],1e-12)
    return (b['c']>b['o'] if d=='bull' else b['c']<b['o']) and body/rng>=.60 and body>=m*max(a,1e-12)


def _sweep(bars,p):
    for i in range(p['start'],len(bars)):
        b=bars[i]; lv=p['level']
        if p['side']=='SSL':
            if b['c']<lv:return None
            if b['l']>=lv:continue
            d='bull'; px=b['l']
        else:
            if b['c']>lv:return None
            if b['h']<=lv:continue
            d='bear'; px=b['h']
        nxt=bars[i+1:i+1+SWEEP_CONFIRM_BARS]
        if any(x['c']<lv if d=='bull' else x['c']>lv for x in nxt):return None
        return {'id':f'SW:{p["id"]}:{b["t"]}','t':b['t'],'from_t':p['t'],'dir':d,'side':p['side'],'kind':p['kind'],'level':lv,'price':px,'index':i}
    return None


def _zone_state(bars,z,kind):
    mid=(z['top']+z['bottom'])/2; status='fresh'; touch=None; end=None
    for j in range(z['created']+1,len(bars)):
        b=bars[j]
        if z['dir']=='bull':
            if b['c']<z['bottom']:status='invalid';end=j;break
            if b['l']<=z['top'] and touch is None:touch=j;status='tapped'
            reached=b['l']<=mid if kind=='FVG' else b['c']<mid
        else:
            if b['c']>z['top']:status='invalid';end=j;break
            if b['h']>=z['bottom'] and touch is None:touch=j;status='tapped'
            reached=b['h']>=mid if kind=='FVG' else b['c']>mid
        if reached:status='mitigated';end=j;break
    x=dict(z,mid=mid,status=status,touch_t=bars[touch]['t'] if touch is not None else None)
    if end is not None:x['end_t']=bars[end]['t']
    return x


def _all_fvg(bars,a):
    out=[]; mingap=.05*a
    for j in range(2,len(bars)):
        if bars[j]['l']>bars[j-2]['h'] and bars[j]['l']-bars[j-2]['h']>=mingap:
            out.append({'dir':'bull','t':bars[j-1]['t'],'top':bars[j]['l'],'bottom':bars[j-2]['h'],'created':j,'kind':'FVG','context':False})
        elif bars[j]['h']<bars[j-2]['l'] and bars[j-2]['l']-bars[j]['h']>=mingap:
            out.append({'dir':'bear','t':bars[j-1]['t'],'top':bars[j-2]['l'],'bottom':bars[j]['h'],'created':j,'kind':'FVG','context':False})
    return out


def _all_ob(bars,a):
    out=[]
    for j in range(3,len(bars)):
        d='bull' if bars[j]['c']>bars[j]['o'] else 'bear'
        if not _disp(bars[j],d,a):continue
        prior=bars[max(0,j-6):j]
        broken=bars[j]['c']>max(x['h'] for x in prior) if d=='bull' else bars[j]['c']<min(x['l'] for x in prior)
        if not broken:continue
        for q in range(j-1,max(-1,j-OB_LOOKBACK),-1):
            opp=bars[q]['c']<bars[q]['o'] if d=='bull' else bars[q]['c']>bars[q]['o']
            if opp:
                out.append({'dir':d,'t':bars[q]['t'],'top':bars[q]['h'],'bottom':bars[q]['l'],'created':j,'kind':'OB','context':False});break
    u={}
    for z in out:u[(z['dir'],z['t'])]=z
    return list(u.values())


def _fvg_leg(bars,i,j,d):
    cand=[]
    for q in range(max(2,i),min(len(bars),j+2)):
        if d=='bull' and bars[q]['l']>bars[q-2]['h']:cand.append({'dir':d,'t':bars[q-1]['t'],'top':bars[q]['l'],'bottom':bars[q-2]['h'],'created':q,'kind':'FVG','context':True})
        if d=='bear' and bars[q]['h']<bars[q-2]['l']:cand.append({'dir':d,'t':bars[q-1]['t'],'top':bars[q-2]['l'],'bottom':bars[q]['h'],'created':q,'kind':'FVG','context':True})
    return max(cand,key=lambda z:z['top']-z['bottom']) if cand else None


def _ob_leg(bars,i,j,d):
    for q in range(j-1,max(-1,i-OB_LOOKBACK),-1):
        if (bars[q]['c']<bars[q]['o']) if d=='bull' else (bars[q]['c']>bars[q]['o']):
            return {'dir':d,'t':bars[q]['t'],'top':bars[q]['h'],'bottom':bars[q]['l'],'created':j,'kind':'OB','context':True}
    return {'dir':d,'t':bars[i]['t'],'top':bars[i]['h'],'bottom':bars[i]['l'],'created':j,'kind':'OB','context':True}


def model(bars,atr_value,k=3,eq_tolerance=0.0,k_int=2,k_ext=5,disp_atr=0.8,strong_disp_atr=1.2):
    """Signature ini sengaja menerima 8 argumen agar kompatibel dengan analysis.py v3."""
    if not bars:
        return {'trend':None,'internal_trend':None,'dealing_range':None,'liquidity':[],'sweeps':[],'events':[],'internal_events':[],'fvg':[],'fvg_mit':[],'order_blocks':[],'ob_mitigated':[],'sequences':[]}
    ext=structure(bars,k_ext); itl=structure(bars,k_int); dr=dealing_range(bars,ext,bars[-1]['c'])
    pools=liquidity_pools(bars,k,eq_tolerance,ext['highs'],ext['lows'])
    sweeps=[]; seqs=[]
    for p in pools:
        sw=_sweep(bars,p)
        if not sw:continue
        sweeps.append(sw); i=sw['index']; d=sw['dir']
        for j in range(i,min(len(bars),i+MSS_WINDOW)):
            if not _disp(bars[j],d,atr_value,disp_atr):continue
            prior=bars[max(0,i-8):j]
            if not prior:continue
            level=max(x['h'] for x in prior) if d=='bull' else min(x['l'] for x in prior)
            if not (bars[j]['c']>level if d=='bull' else bars[j]['c']<level):continue
            ev={'t':bars[j]['t'],'from_t':sw['t'],'type':'CISD/MSS','dir':d,'level':level,'sweep_id':sw['id'],'displacement':True,'strong':_disp(bars[j],d,atr_value,strong_disp_atr),'index':j}
            f=_fvg_leg(bars,i,j,d); ob=_ob_leg(bars,i,j,d)
            if f:f.update(sweep_id=sw['id'],event_t=ev['t'])
            ob.update(sweep_id=sw['id'],event_t=ev['t'])
            hi=max(x['h'] for x in bars[i:j+1]);lo=min(x['l'] for x in bars[i:j+1]);span=max(hi-lo,1e-12)
            leg={'lo':lo,'hi':hi,'eq':(lo+hi)/2,'ote_bottom':hi-.79*span if d=='bull' else lo+.62*span,'ote_top':hi-.62*span if d=='bull' else lo+.79*span}
            seqs.append({'id':sw['id'],'dir':d,'sweep':sw,'event':ev,'fvg':_zone_state(bars,f,'FVG') if f else None,'ob':_zone_state(bars,ob,'OB'),'leg':leg,'inducement':None,'invalid':False})
            break
    # dedupe: satu event memakai sweep terakhir
    uq={}
    for s in seqs:uq[(s['event']['t'],s['dir'])]=s
    seqs=sorted(uq.values(),key=lambda s:s['event']['index'])
    # visual layer: semua FVG/OB, bukan hanya POI contextual
    ctxf=[s['fvg'] for s in seqs if s['fvg']]; ctxo=[s['ob'] for s in seqs if s['ob']]
    vf=[_zone_state(bars,z,'FVG') for z in _all_fvg(bars,atr_value)]
    vo=[_zone_state(bars,z,'OB') for z in _all_ob(bars,atr_value)]
    tol=max(.02*atr_value,1e-9)
    def mark(z,ctx):
        for c in ctx:
            if c['dir']==z['dir'] and abs(c['top']-z['top'])<=tol and abs(c['bottom']-z['bottom'])<=tol:
                z['context']=True;z['sweep_id']=c.get('sweep_id');z['event_t']=c.get('event_t');break
        return z
    vf=[mark(z,ctxf) for z in vf];vo=[mark(z,ctxo) for z in vo]
    active=lambda z:z['status'] in ('fresh','tapped')
    used={s['id'] for s in seqs}; shown=[s for s in sweeps if s['id'] in used]
    events=ext['events'][-12:]+[s['event'] for s in seqs][-10:];events.sort(key=lambda e:e['index'])
    ints=[dict(e,type='i'+e['type']) for e in itl['events'][-10:]]
    return {'trend':ext['trend'],'internal_trend':itl['trend'],'dealing_range':dr,'liquidity':pools,
            'sweeps':shown[-12:],'events':events[-20:],'internal_events':ints,
            'fvg':[z for z in vf if active(z)][-12:],'fvg_mit':[z for z in vf if not active(z)][-12:],
            'order_blocks':[z for z in vo if active(z)][-12:],'ob_mitigated':[z for z in vo if not active(z)][-12:],
            'context_fvg':ctxf[-8:],'context_ob':ctxo[-8:],'sequences':seqs[-10:]}
