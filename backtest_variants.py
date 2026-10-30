"""مقارنة تعديلات الشروط على الشموع التاريخية (أرباح العقود تقديرية بنموذج تسعير، الحقيقي في paper.json).
الاستخدام: python3 backtest_variants.py [مجلد_البيانات]"""
import sys, json
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parent))
import almilyoni_weekly as m

D = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'data')
data = {s: json.loads((D / f'{s}-15m.json').read_text())['bars'] for s in m.SYMBOLS if (D / f'{s}-15m.json').exists()}
print('الأسهم المتوفرة:', ', '.join(s for s in m.TRADE_SYMBOLS if s in data),
      '| الناقصة:', ', '.join(s for s in m.TRADE_SYMBOLS if s not in data) or '—')


def rep(name, ts):
    base = [t for t in ts if not t.get('expensive')]
    o = [t for t in base if t['opt_usd'] is not None]
    if not o: print(f'{name:32s} صفقات=0'); return
    w = sum(t['opt_usd'] > 0 for t in o); gw = sum(t['opt_usd'] for t in o if t['opt_usd'] > 0); gl = -sum(t['opt_usd'] for t in o if t['opt_usd'] < 0)
    eq = pk = dd = 0
    for t in o: eq += t['opt_usd']; pk = max(pk, eq); dd = max(dd, pk - eq)
    nc = sum(t['side'] == 'C' for t in o)
    print(f"{name:32s} صفقات={len(o):3d} (كول {nc} / بوت {len(o) - nc}) فوز={100 * w / len(o):4.1f}% "
          f"صافي={sum(t['opt_usd'] for t in o):+7.0f}$ PF={gw / gl if gl else float('inf'):4.2f} أكبر تراجع={dd:5.0f}$ "
          f"متجاهلة(غالي)={len(ts) - len(base)}")


b = data['SPY']
print('الفترة:', datetime.fromtimestamp(b[210]['t'], m.NY).date(), '→', datetime.fromtimestamp(b[-1]['t'], m.NY).date())
rep('الأساس v3 (عكس الترند، كول+بوت)', m.trades_for(data))
rep('كول بس', m.trades_for(data, sides=(1,)))
rep('بوت بس', m.trades_for(data, sides=(-1,)))
rep('مع الترند', m.trades_for(data, trend='with'))
rep('بدون شرط EMA50', m.trades_for(data, trend='none'))
rep('بدون فلتر SPY', m.trades_for(data, spy_filter=False))
rep('حجم 1.0× بدل 1.2×', m.trades_for(data, volx=1.0))
rep('عقد 21 يوم+', m.trades_for(data, dte=21))
rep('ميزانية 500$', m.trades_for(data, budget=500.0))
