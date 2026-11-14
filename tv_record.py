"""المليوني: التسجيل اليومي من TradingView (حساب أحمد) + أبيان — بدل ياهو (قرار 31 أكتوبر 2026).

ما يتصل بالنت بنفسه. مهمة Claude المجدولة تجيب البيانات بأدوات MCP وتحطها في tv_cache/، وهذا السكربت يقول وش ناقص.
  python3 tv_record.py --day 2026-11-02
    → يطبع NEED: [...] (ملفات لازم تنجاب) أو DONE (انسجلت الصفقات في paper.json)

أسماء الملفات في tv_cache/:
  <SYM>-15m.json        شموع 15د من get_ohlcv (NASDAQ:NVDA ...، وCBOE:VIX باسم VIX)
  <SYM>-1m-<unix>.json  نوافذ دقيقة حول الدخول والخروج من get_ohlcv (السهم أو العقد OPRA:...)
  <SYM>-expiries.json   قائمة تواريخ الانتهاء من أبيان browse_options_chain
كل ملف = نتيجة الأداة كما هي (فيها "bars" أو "expiration_dates").
"""
import json, argparse
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
import almilyoni_weekly as m
import fetch_real as f

ROOT = Path(__file__).resolve().parent; CACHE = ROOT / 'tv_cache'; NY = m.NY
EXCH = {'NVDA': 'NASDAQ', 'AAPL': 'NASDAQ', 'AMD': 'NASDAQ', 'AVGO': 'NASDAQ', 'TSLA': 'NASDAQ', 'LRCX': 'NASDAQ',
        'ARM': 'NASDAQ', 'TSM': 'NYSE', 'SPY': 'AMEX'}
NEED = []


class Missing(Exception): pass


SPEC = {}
def load(name):
    p = CACHE / name
    if not p.exists():
        NEED.append(SPEC.get(name, name)); raise Missing(name)
    return json.loads(p.read_text())


def tv_symbol(occ):          # NVDA261113C00220000 → OPRA:NVDA261113C220.0
    root, rest = occ[:-15], occ[-15:]
    k = int(rest[7:]) / 1000
    return f"OPRA:{root}{rest[:7]}{k:.1f}"


def bars_of(js, step):
    b = js.get('bars', [])
    if isinstance(b, dict):   # format=columns
        js = dict(bars=[dict(t=b['t'][i], o=b['o'][i], h=b['h'][i], l=b['l'][i], c=b['c'][i], v=b['v'][i]) for i in range(len(b['t']))])
    return [dict(t=b['t'], o=b['o'], h=b['h'], l=b['l'], c=b['c'], v=int(b.get('v') or 0))
            for b in js.get('bars', []) if b['t'] % step == 0 and f.rth(b['t'])]


def _iso(t): return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def minute_bars(sym, day):
    """شموع دقيقة بس حول الدخول والخروج (نافذتين صغار) عشان نوفر — مو اليوم كامل."""
    tr = CUR['tr']; out = []; miss = False
    wins = ((tr['t_entry'], tr['t_entry'] + 600), (tr['t_exit'], tr['t_exit'] + 1500))
    for a, b in (wins if ':' in sym else wins[1:]):   # السهم: نافذة الخروج بس
        name = f"{sym}-1m-{a}.json"; p = CACHE / name
        if miss: raise Missing(sym)
        if ':' in sym and a == wins[1][0]:       # العقد: نافذة الخروج بس إذا الدخول فيه تداول وسعره من 100 إلى 1000$
            ok = [x for x in out if x['v'] > 0]
            if not ok or not (m.PREM_MIN <= ok[0]['c'] * 100 <= m.BUDGET - m.COMM): return out
        if not p.exists():
            NEED.append(dict(file=name, tool='get_ohlcv', symbol=sym if ':' in sym else f"{EXCH[sym]}:{sym}",
                             interval='1m', date_from=_iso(a), date_to=_iso(b), format='columns', count=60)); miss = True; continue
        out += bars_of(json.loads(p.read_text()), 60)
    if miss: raise Missing(sym)
    return sorted({b['t']: b for b in out}.values(), key=lambda b: b['t'])


CUR = {}
def expiry_list(min_expiry):  # تواريخ أبيان مرتبة من الأقرب لـ14 يوم (10–21 أول، وإذا ما فيه نوسع 7–35)
    exps = [date.fromisoformat(x) for x in load(f"{CUR['sym']}-expiries.json")['expiration_dates']]
    d0 = CUR['day']; key = lambda x: abs((x - d0).days - m.DTE_MIN)
    main = sorted([x for x in exps if m.DTE_LO <= (x - d0).days <= m.DTE_HI], key=key)
    extra = sorted([x for x in exps if 7 <= (x - d0).days <= 35 and x not in main], key=key)
    if not main + extra: raise RuntimeError(f"ما فيه عقد في أبيان مدته 7–35 يوم لـ {CUR['sym']}")
    return main + extra


def wide_bars(sym, day):
    """العقد ما تداول بالضبط وقت الخروج: نجيب شموعه من الدخول لين الإغلاق ونختار أقرب صفقة حقيقية."""
    tr = CUR['tr']; occ = tv_symbol(sym) if len(sym) > 12 else sym
    a = tr['t_entry']; d = datetime.fromtimestamp(a, NY); b = int(d.replace(hour=16, minute=0, second=0).timestamp())
    name = f"{occ}-1m-wide-{a}.json"; p = CACHE / name
    if not p.exists():
        NEED.append(dict(file=name, tool='get_ohlcv', symbol=occ, interval='1m', date_from=_iso(a), date_to=_iso(b),
                         format='columns', count=400)); raise Missing(name)
    return bars_of(json.loads(p.read_text()), 60)


def vix_change(day, t_entry):
    vx = bars_of(load('VIX-15m.json'), 900)
    prev = [b for b in vx if datetime.fromtimestamp(b['t'], NY).date() < day]
    cur = [b for b in vx if b['t'] <= t_entry and datetime.fromtimestamp(b['t'], NY).date() == day]
    return round(100 * (cur[-1]['c'] / prev[-1]['c'] - 1), 2) if prev and cur else None


def merge_15m():
    miss = [s for s in m.SYMBOLS if not (CACHE / f"{s}-15m.json").exists()]
    for s in miss:
        NEED.append(dict(file=f"{s}-15m.json", tool='get_ohlcv', symbol=f"{EXCH[s]}:{s}", interval='15m', count=30, format='columns'))
    if miss: raise Missing(miss)
    for s in m.SYMBOLS:
        new = bars_of(load(f"{s}-15m.json"), 900)
        p = f.DATA / f'{s}-15m.json'; old = json.loads(p.read_text())
        first = new[0]['t'] if new else 1e12
        merged = [b for b in old['bars'] if b['t'] < first] + new
        old.update(bars=merged, count=len(merged), updated=datetime.now().isoformat(timespec='seconds'),
                   source_note='TradingView (حساب أحمد)')
        p.write_text(json.dumps(old, separators=(',', ':')))


def run(day, merge=True):
    if merge: merge_15m()
    data = f.load_data(); trades = f.plan_for(data, day); quotes = []
    for tr in trades:
        CUR.update(sym=tr['symbol'], day=day, tr=tr)
        SPEC[f"{tr['symbol']}-expiries.json"] = dict(file=f"{tr['symbol']}-expiries.json", tool='abyan browse_options_chain', symbol=tr['symbol'])
        SPEC['VIX-15m.json'] = dict(file='VIX-15m.json', tool='get_ohlcv', symbol='CBOE:VIX', interval='15m', count=60, format='columns')
        try:
            q = f.price_trade(tr, day, minute_bars(tr['symbol'], day), vix_change(day, tr['t_entry']))
        except Missing:
            continue
        q.pop('exit50_usd', None)        # يحتاج مسار العقد كامل، ما نجيبه توفيرًا
        quotes.append(q)
    if NEED: return None
    qp = ROOT / '.quotes.json'; qp.write_text(json.dumps(quotes, ensure_ascii=False))
    m.cmd_close_day(argparse.Namespace(day=str(day), quotes=str(qp), state=str(f.STATE)), data); qp.unlink()
    return [(q['id'], q.get('opt_symbol'), q.get('opt_entry'), q.get('opt_exit'), q.get('note', '')) for q in quotes]


f.minute_bars = minute_bars; f.expiry_list = expiry_list; f.wide_bars = wide_bars; f.vix_change = vix_change
f.VOL_MINUTES = 35                      # نافذتين: 10 دقائق عند الدخول + 25 عند الخروج
f.WIDE_MINUTES = lambda tr: (int(datetime.fromtimestamp(tr['t_entry'], NY).replace(hour=16, minute=0, second=0).timestamp()) - tr['t_entry']) // 60
f.occ = lambda sym, exp, side, K: f"{sym}{exp:%y%m%d}{side}{int(round(K * 1000)):08d}"
def _opt_bars(sym, day):     # العقود: الملف باسم OPRA
    return minute_bars(tv_symbol(sym) if len(sym) > 12 else sym, day)
f.minute_bars = _opt_bars


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--day', default=datetime.now(NY).date().isoformat())
    ap.add_argument('--no-merge', action='store_true'); a = ap.parse_args(); day = date.fromisoformat(a.day)
    try:
        res = run(day, not a.no_merge)
    except Missing:
        res = None
    if res is None:
        u = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in NEED}
        print('NEED (جيبها واحفظ نتيجة كل أداة كما هي في tv_cache/<file> ثم أعد التشغيل):')
        for x in sorted(u): print(x)
    else:
        print('DONE', len(res), 'صفقة'); [print(' ', *r) for r in res]
