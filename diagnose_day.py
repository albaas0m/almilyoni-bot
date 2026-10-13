import sys, json
sys.path.insert(0,'/home/claude/bot')
import almilyoni_weekly as m
from datetime import datetime, timezone
D='./data/'
data={s:json.load(open(D+s+'-15m.json'))['bars'] for s in m.SYMBOLS}
day=sys.argv[1]
spy=data['SPY']; s_end,s_vwap,s_ns=m.session_info(spy); spy_at={b['t']:j for j,b in enumerate(spy)}
for sym in m.TRADE_SYMBOLS:
    bs=data[sym]; z,sig,atrs=m.ut_zlsma(bs); end,vwap,ns=m.session_info(bs)
    print('==',sym)
    for i,b in enumerate(bs):
        ny=datetime.fromtimestamp(b['t'],timezone.utc).astimezone(m.NY)
        if ny.strftime('%Y-%m-%d')!=day: continue
        if sig[i]!=1: continue
        j=spy_at.get(b['t']); vavg=sum(bs[k]['v'] for k in range(i-20,i))/20
        cny=datetime.fromtimestamp(b['t']+m.STEP,timezone.utc).astimezone(m.NY); mins=cny.hour*60+cny.minute-570
        print(ny.strftime('%H:%M'),'BUY signal | c>zlsma',b['c']>z[i] if z[i] else None,'| c>vwap',b['c']>vwap[i],'| spy>vwap',spy[j]['c']>s_vwap[j] if j else None,'| vol x%.2f'%(b['v']/vavg),'| mins',mins)
