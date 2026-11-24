"""اختبار محاكاة سجل تنبيهات TradingView (24 نوفمبر): python3 test_tvlog.py — بدون شبكة ولا مفاتيح، وما يكتب في live.json."""
import json, io, contextlib
from datetime import date
import live_abyan as L, tv

L.NTFY_KEY = 'KEY'; L.save = lambda st, msg, data=False: None
T0 = 1795532400                                  # 15:00Z 24 نوفمبر = شمعة AVGO الضايعة
EV = dict(bar_time='2026-11-24T14:45:00Z', fire_id=60705855152, fired_at='2026-11-24T15:00:00Z', symbol='NASDAQ:AVGO',
          message=json.dumps(dict(k='KEY', sym='AVGO', side=1, t=T0, c=307.97, atr=4.343358735949126)), webhook=dict(http_code=429))
ok = []
def check(name, cond): ok.append(cond); print(('✅' if cond else '❌'), name)
def run(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): r = fn(*a)
    return r, buf.getvalue()

# 1) إشارة حيّة من السجل (بعد الإغلاق بـ 40 ثانية) + منع التكرار
L.stock_px = lambda s: 308.0; L.pick_contract = lambda sym, side, S, now, price=None: (date(2026, 12, 7), 320.0, 6.5)
tv.call = lambda tool, args: dict(events=[EV], success=True)
st = {'trades': []}
(live, late), out = run(L.tvlog_signals, T0 + 40, st)
check('إشارة حيّة وحدة من السجل', len(live) == 1 and not late and live[0]['t_entry'] == T0 and live[0]['side'] == 'C' and live[0]['fire_id'] == 60705855152)
check('target/stop صح (2×/3× ATR)', abs(live[0]['target'] - (308 + 2 * 4.3434)) < .01 and abs(live[0]['stop'] - (308 - 3 * 4.3434)) < .01)
check('العداد tv_calls = 1', st['tv_calls']['n'] == 1)
run(L.enter, st, live[0], T0 + 40); L.mark_seen(st, T0 + 40, live[0]['sig'], live[0]['fire_id'])
check('الصفقة تسجلت بسعر أبيان لحظي', len(st['trades']) == 1 and st['trades'][0]['opt_entry'] == 6.5)
(live2, late2), _ = run(L.tvlog_signals, T0 + 940, st)
check('القراءة الجاية ما تكرر الإشارة (seen)', not live2 and not late2)
st2 = {'trades': [], 'seen': {}}; L.mark_seen(st2, T0 + 40, live[0]['sig'])        # ntfy سبق ودخلها (مفتاح sym-t فقط)
(live3, _), _ = run(L.tvlog_signals, T0 + 40, st2)
check('ما نكرر لو ntfy سبق (مفتاح sym-t)', not live3)
st3 = {'trades': [], 'seen': {'fire:60705855152': T0}}
(live4, _), _ = run(L.tvlog_signals, T0 + 40, st3)
check('ما نكرر بـ fire_id', not live4)

# 2) رمز غلط
bad = dict(EV, message=json.dumps(dict(k='XXX', sym='AVGO', side=1, t=T0, c=1, atr=1)))
tv.call = lambda tool, args: dict(events=[bad])
(lv, lt), out = run(L.tvlog_signals, T0 + 40, {'trades': []})
check('رمز غلط يُتجاهل مع تحذير', not lv and not lt and 'برمز غلط' in out)

# 3) إشارة فايتة (بعد 25 دقيقة) → أثر رجعي بأسعار OPRA
exp = date(2026, 12, 7)
import importlib; importlib.reload(L); L.NTFY_KEY = 'KEY'; L.save = lambda st, msg, data=False: None
L.expiries = lambda sym, d0: [exp]
OPT = {'OPRA:AVGO261207C310.0': [dict(t=T0 - 240, o=12.18, h=12.18, l=12.18, c=12.18, v=1), dict(t=T0, o=14.74, h=14.74, l=14.13, c=14.13, v=6)],
       'OPRA:AVGO261207C315.0': [dict(t=T0 + 180, o=9.4, h=9.4, l=9.4, c=9.4, v=2)]}                 # (بيانات اختبار، مو أسعار البوت)
STK = dict(t=[T0, T0 + 60, T0 + 120], o=[307.96, 308.1, 308.4], h=[308.5, 308.6, 309.0], l=[307.4, 307.9, 308.0], c=[308.1, 308.4, 308.9], v=[5, 5, 5])
def fake(tool, args):
    if tool == 'mcp-tv-get-alerts-log': return dict(events=[EV])
    if args['symbol'].startswith('OPRA:'):
        b = OPT.get(args['symbol'], []); return dict(bars=dict(t=[x['t'] for x in b], o=[x['o'] for x in b], h=[x['h'] for x in b], l=[x['l'] for x in b], c=[x['c'] for x in b], v=[x['v'] for x in b]))
    return dict(bars=STK)
tv.call = fake
st = {'trades': []}; now = T0 + 25 * 60
(live, late), out = run(L.tvlog_signals, now, st)
check('إشارة فايتة (مو حيّة)', not live and len(late) == 1)
r, out = run(L.retro_enter, st, late[0]['sig'], now, late[0]['fire_id'], late[0]['wh'])
t = st['trades'][-1] if st['trades'] else {}
print('   ', {k: t.get(k) for k in ('symbol', 'side', 'entry', 'strike', 'expiry', 'opt_entry', 'src')})
check('سُجلت بأثر رجعي بسعر العقد الحقيقي (C315 = 9.4$ ← 940$)', r == 'سُجلت' and t.get('opt_entry') == 9.4 and t.get('strike') == 315.0 and t.get('retro'))
check('سعر السهم = افتتاح دقيقة الإشارة 307.96 والمصدر مكتوب', t.get('entry') == 307.96 and 'OPRA' in t.get('src', ''))
check('عداد tv_calls يحسب الطلبات', st['tv_calls']['n'] >= 3)
(live, late), _ = run(L.tvlog_signals, now, {'trades': st['trades'], 'seen': {}})
r2, _ = run(L.retro_enter, {'trades': st['trades']}, late[0]['sig'], now)
check('ما تتكرر الصفقة الفايتة', r2 == 'موجودة')

# 4) فشل القراءة: تحذير واضح + إعادة بعد دقيقة (ما يسكت)
for exc in (RuntimeError('boom'), tv.QuotaError('حد'), SystemExit('التوكن انتهى')):
    def boom(tool, args, e=exc): raise e
    tv.call = boom; tl = dict(bar=T0 // 900, due=T0 + 35, tries=0, done=False); s5 = {'trades': []}
    _, out = run(L.tvlog_step, T0 + 40, s5, tl)
    check(f'فشل {type(exc).__name__}: تحذير + إعادة بعد دقيقة', '⚠️ فشل قراءة سجل' in out and tl['due'] == T0 + 100 and not tl['done'])
tl = dict(bar=T0 // 900, due=0, tries=2, done=False)
_, out = run(L.tvlog_step, T0 + 40, {'trades': []}, tl)
check('المحاولة الثالثة ❌ بارزة', '❌' in out)
print('\nالنتيجة:', 'نجح كل شي' if all(ok) else 'فيه فشل', f'({sum(ok)}/{len(ok)})'); raise SystemExit(0 if all(ok) else 1)
