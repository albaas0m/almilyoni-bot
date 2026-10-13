import sys, json, math
sys.path.insert(0,'/home/claude/bot')
import almilyoni_weekly as m
from datetime import datetime, timezone, timedelta
D='./data/'
data={s:json.load(open(D+s+'-15m.json'))['bars'] for s in m.SYMBOLS}
pre={}
for s in m.SYMBOLS:
    bs=data[s]; pre[s]=(m.ut_zlsma(bs), m.session_info(bs))
spy=data['SPY']; s_end,s_vwap,s_ns=pre['SPY'][1]; spy_at={b['t']:j for j,b in enumerate(spy)}

def opt(e,px,iv,t0,t1,dte):
    d0=datetime.fromtimestamp(t0,m.NY)
    target=d0+timedelta(days=dte); days=(4-target.weekday())%7
    exp=(target+timedelta(days=days)).replace(hour=16,minute=0)
    T0=(exp-d0).total_seconds()/(365*86400)
    T1=max((exp-datetime.fromtimestamp(t1,m.NY)).total_seconds()/(365*86400),0)
    inc=1.0 if e<300 else 5.0; K=round(e/inc)*inc
    for _ in range(40):
        mid0=m.bs_call(e,K,T0,iv); ask=mid0+max(0.01*mid0,0.02)
        if ask*100+m.COMM<=m.BUDGET: break
        K+=inc
    else: return None
    mid1=m.bs_call(px,K,T1,iv); bid=max(mid1-max(0.01*mid1,0.02),0)
    cost=ask*100+m.COMM; got=bid*100-m.COMM
    return got-cost

def run(volx=1.2, spy_filter=True, late_volx=2.0, dtes=(14,21,30)):
    out=[]
    for sym in m.TRADE_SYMBOLS:
        bs=data[sym]; (z,sig,atrs),(end,vwap,ns)=pre[sym]; busy=-1
        for i,b in enumerate(bs):
            if sig[i]!=1 or i<210 or i<busy or end[i] or z[i] is None or z[i-3] is None: continue
            j=spy_at.get(b['t'])
            if j is None or j<200 or s_ns[j]<10 or ns[i]<10: continue
            if b['c']<=z[i] or b['c']<=vwap[i]: continue
            if spy_filter and spy[j]['c']<=s_vwap[j]: continue
            vavg=sum(bs[k]['v'] for k in range(i-20,i))/20
            if b['v']<volx*vavg: continue
            cny=datetime.fromtimestamp(b['t']+m.STEP,timezone.utc).astimezone(m.NY); mins=cny.hour*60+cny.minute-570
            if not (0<=mins<=m.LATE_MIN): continue
            if mins>m.WIN_MIN and b['v']<late_volx*vavg: continue
            a=atrs[i]; e=bs[i+1]['o']; tg=e+a; st=e-3*a; k=i+1; why='نهاية'
            while True:
                x=bs[k]
                if x['o']<=st: px=x['o'];why='وقف';break
                if x['o']>=tg: px=x['o'];why='هدف';break
                if x['l']<=st: px=st;why='وقف';break
                if x['h']>=tg: px=tg;why='هدف';break
                if end[k]: px=x['c'];break
                k+=1
            busy=k
            rets=[math.log(bs[q]['c']/bs[q-1]['c']) for q in range(max(1,i-520),i+1)]
            rv=math.sqrt(sum(r*r for r in rets)/len(rets))*math.sqrt(252*26)
            iv=max(rv*m.IV_MULT,0.12)
            r=dict(sym=sym,t=bs[i+1]['t'],why=why,pct=100*(px-e)/e-m.COST_PCT)
            for d in dtes: r[d]=opt(e,px,iv,bs[i+1]['t'],bs[k]['t'],d)
            out.append(r)
    return out

def rep(name,ts):
    n=len(ts)
    if not n: print(name,'0'); return
    days=len({datetime.fromtimestamp(t['t'],m.NY).date() for t in ts})
    w=sum(t['pct']>0 for t in ts)
    line=f"{name:34s} صفقات={n:3d} أيام={days:3d} فوز_السهم={100*w/n:4.1f}%"
    for d in (14,21,30):
        v=[t[d] for t in ts if t[d] is not None]
        if v: line+=f" | {d}ي: ربح={sum(v):7.0f}$ فوز={100*sum(x>0 for x in v)/len(v):4.1f}% أسوأ={min(v):5.0f}$"
    print(line)

b=data['SPY']
print('الفترة:',datetime.fromtimestamp(b[210]['t'],m.NY).date(),'→',datetime.fromtimestamp(b[-1]['t'],m.NY).date())
rep('الأساس (الحالي)',run())
rep('حجم 1.0× بدل 1.2×',run(volx=1.0))
rep('حجم 0.8×',run(volx=0.8))
rep('بدون فلتر SPY',run(spy_filter=False))
rep('متأخر 1.5× بدل 2×',run(late_volx=1.5))
rep('حجم 1.0× + متأخر 1.5×',run(volx=1.0,late_volx=1.5))
