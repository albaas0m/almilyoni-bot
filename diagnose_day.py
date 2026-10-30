"""يوضح ليش انفتحت أو ما انفتحت صفقة في يوم معيّن. الاستخدام: python3 diagnose_day.py YYYY-MM-DD [مجلد_البيانات]"""
import sys, json
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent))
import almilyoni_weekly as m

day = sys.argv[1]
D = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parent / 'data')
data = {s: json.loads((D / f'{s}-15m.json').read_text())['bars'] for s in m.SYMBOLS if (D / f'{s}-15m.json').exists()}
spy = data['SPY']; s_end, s_vwap, s_ns = m.session_info(spy); spy_at = {b['t']: j for j, b in enumerate(spy)}
for sym in m.TRADE_SYMBOLS:
    if sym not in data: print('==', sym, 'ما فيه بيانات'); continue
    bs = data[sym]; z, sig, atrs = m.ut_zlsma(bs); end, vwap, ns = m.session_info(bs); ema_d, adr = m.daily_stats(bs, end)
    print('==', sym)
    for i, b in enumerate(bs):
        ny = datetime.fromtimestamp(b['t'], timezone.utc).astimezone(m.NY)
        if ny.strftime('%Y-%m-%d') != day or sig[i] == 0: continue
        s = sig[i]; j = spy_at.get(b['t']); vavg = sum(bs[k]['v'] for k in range(i - 20, i)) / 20
        cny = datetime.fromtimestamp(b['t'] + m.STEP, timezone.utc).astimezone(m.NY); mins = cny.hour * 60 + cny.minute - 570
        print(ny.strftime('%H:%M'), 'CALL' if s == 1 else 'PUT', '| zlsma', s * (b['c'] - z[i]) > 0 if z[i] else None,
              '| vwap', s * (b['c'] - vwap[i]) > 0, '| spy', s * (spy[j]['c'] - s_vwap[j]) > 0 if j is not None else None,
              '| عكس الترند', (s * (b['c'] - ema_d[i]) < 0) if ema_d[i] else None,
              '| حجم x%.2f' % (b['v'] / vavg), '| دقيقة', mins)
