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
(live, late, _f), out = run(L.tvlog_signals, T0 + 40, st)
check('إشارة حيّة وحدة من السجل', len(live) == 1 and not late and live[0]['t_entry'] == T0 and live[0]['side'] == 'C' and live[0]['fire_id'] == 60705855152)
check('target/stop صح (2×/3× ATR)', abs(live[0]['target'] - (308 + 2 * 4.3434)) < .01 and abs(live[0]['stop'] - (308 - 3 * 4.3434)) < .01)
check('العداد tv_calls = 1', st['tv_calls']['n'] == 1)
run(L.enter, st, live[0], T0 + 40); L.mark_seen(st, T0 + 40, live[0]['sig'], live[0]['fire_id'])
check('الصفقة تسجلت بسعر أبيان لحظي', len(st['trades']) == 1 and st['trades'][0]['opt_entry'] == 6.5)
(live2, late2, _f), _ = run(L.tvlog_signals, T0 + 940, st)
check('القراءة الجاية ما تكرر الإشارة (seen)', not live2 and not late2)
st2 = {'trades': [], 'seen': {}}; L.mark_seen(st2, T0 + 40, live[0]['sig'])        # ntfy سبق ودخلها (مفتاح sym-t فقط)
(live3, _, _f), _ = run(L.tvlog_signals, T0 + 40, st2)
check('ما نكرر لو ntfy سبق (مفتاح sym-t)', not live3)
st3 = {'trades': [], 'seen': {'fire:60705855152': T0}}
(live4, _, _f), _ = run(L.tvlog_signals, T0 + 40, st3)
check('ما نكرر بـ fire_id', not live4)

# 2) رمز غلط
bad = dict(EV, message=json.dumps(dict(k='XXX', sym='AVGO', side=1, t=T0, c=1, atr=1)))
tv.call = lambda tool, args: dict(events=[bad])
(lv, lt, _f), out = run(L.tvlog_signals, T0 + 40, {'trades': []})
check('رمز غلط يُتجاهل مع تحذير', not lv and not lt and 'برمز غلط' in out)

# 3) إشارة فايتة → أثر رجعي بأرقام حقيقية بس (صفقة قبل الإشارة لازم تنرفض)
import importlib; importlib.reload(L); L.NTFY_KEY = 'KEY'; L.save = lambda st, msg, data=False: None
exp = date(2026, 12, 7); L.expiries = lambda sym, d0: [exp]
from datetime import datetime, timezone
def ts(x): return int(datetime.strptime(x, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())
def bar(t, p, v): return dict(t=t, o=p, h=p, l=p, c=p, v=v)
OPT = {}                                                  # (بيانات اختبار، مو أسعار البوت)
STK = [dict(t=T0, o=307.96, h=308.5, l=307.4, c=308.1, v=5), dict(t=T0 + 60, o=308.1, h=308.6, l=307.9, c=308.4, v=5), dict(t=T0 + 120, o=308.4, h=309.0, l=308.0, c=308.9, v=5)]
reqs = []
def cols(bs): return dict(bars=dict(t=[x['t'] for x in bs], o=[x['o'] for x in bs], h=[x['h'] for x in bs], l=[x['l'] for x in bs], c=[x['c'] for x in bs], v=[x['v'] for x in bs]))
def fake(tool, args):
    if tool == 'mcp-tv-get-alerts-log': return dict(events=[EV])
    reqs.append(args['symbol']); lo, hi = ts(args['date_from']), ts(args['date_to'])
    src = OPT.get(args['symbol'], []) if args['symbol'].startswith('OPRA:') else STK
    return cols([b for b in src if lo <= b['t'] <= hi])
tv.call = fake
OPT.update({'OPRA:AVGO261207C310.0': [bar(T0 - 240, 8.0, 6)],                       # صفقة قبل الإشارة بـ 4 دقايق (في الميزانية) ← لازم تنرفض
            'OPRA:AVGO261207C305.0': [bar(T0 - 600, 9.0, 3), bar(T0 + 60, 9.5, 8)],  # حجم 11، أول صفقة بعد الإشارة 9.5
            'OPRA:AVGO261207C300.0': [bar(T0 + 120, 9.9, 20)],                      # الأعلى حجم
            'OPRA:AVGO261207C315.0': [bar(T0 + 400, 9.0, 50)]})                     # بعد 6 دقايق (برا النافذة) ← ينرفض
st = {'trades': []}; now = T0 + 25 * 60
(live, late, _f), out = run(L.tvlog_signals, now, st)
check('إشارة فايتة (مو حيّة)', not live and len(late) == 1)
r, out = run(L.retro_enter, st, late[0]['sig'], now, late[0]['fire_id'], late[0]['wh'])
t = st['trades'][-1] if st['trades'] else {}
print('   ', {k: t.get(k) for k in ('strike', 'expiry', 'entry', 'target', 'stop', 'opt_entry', 'opt_entry_at', 'opt_entry_vol', 'src')})
check('صفقة قبل الإشارة (C310 = 8.0$) انرفضت', t.get('strike') != 310.0 and t.get('opt_entry') != 8.0)
check('اختار الأعلى حجم داخل النافذة (C300، 9.9$، حجم 20)', r == 'سُجلت' and t.get('strike') == 300.0 and t.get('opt_entry') == 9.9 and t.get('opt_entry_vol') == 20)
check('opt_entry_at = وقت أول صفقة حقيقية بعد الإشارة', t.get('opt_entry_at') == T0 + 120 and t['opt_entry_at'] >= T0)
check('السهم = افتتاح نفس الدقيقة (308.4) والهدف/الوقف منها', t.get('entry') == 308.4 and abs(t['target'] - (308.4 + 2 * 4.343358735949126)) < .01 and abs(t['stop'] - (308.4 - 3 * 4.343358735949126)) < .01)
check('المصدر مكتوب (صفقة حقيقية بعد الإشارة)', 'بعد الإشارة' in t.get('src', '') and 'OPRA' in t['src'])
check('الطلبات ≤ 1 سهم + 8 عقود، والعداد يحسبها', len(reqs) <= 9 and st['tv_calls']['n'] == len(reqs) + 1)   # +1 قراءة السجل
r2, _ = run(L.retro_enter, {'trades': st['trades']}, late[0]['sig'], now)
check('ما تتكرر الصفقة الفايتة', r2 == 'موجودة')
# ما فيه ولا عقد عليه صفقة بعد الإشارة → بدون عقد + سبب، ولا رقم تقريبي
OPT.clear(); OPT.update({'OPRA:AVGO261207C310.0': [bar(T0 - 240, 8.0, 6)], 'OPRA:AVGO261207C305.0': [bar(T0 - 100, 8.5, 9)]}); reqs.clear()
st = {'trades': []}; r, out = run(L.retro_enter, st, late[0]['sig'], now); t = st['trades'][-1]
check('بدون عقد حقيقي: لا opt_entry والسبب مكتوب', t.get('why') == 'بدون عقد' and 'opt_entry' not in t and 'ما فيه صفقة خلال 5 دقايق' in t.get('note', '') and len(reqs) <= 9)
# سيولة ضعيفة (حجم < 5) تنرفض
OPT.clear(); OPT.update({'OPRA:AVGO261207C310.0': [bar(T0 + 30, 9.0, 2)]}); reqs.clear()
st = {'trades': []}; r, out = run(L.retro_enter, st, late[0]['sig'], now); t = st['trades'][-1]
check('عقد سيولته ضعيفة (حجم 2) ينرفض', 'opt_entry' not in t and 'سيولة ضعيفة' in t.get('note', ''))

# 4) فشل القراءة: تحذير واضح + إعادة بعد دقيقة (ما يسكت)
for exc in (RuntimeError('boom'), tv.QuotaError('حد'), SystemExit('التوكن انتهى')):
    def boom(tool, args, e=exc): raise e
    tv.call = boom; tl = dict(bar=T0 // 900, due=T0 + 35, tries=0, done=False); s5 = {'trades': []}
    _, out = run(L.tvlog_step, T0 + 40, s5, tl)
    check(f'فشل {type(exc).__name__}: تحذير + إعادة بعد دقيقة', '⚠️ فشل قراءة سجل' in out and tl['due'] == T0 + 100 and not tl['done'])
tl = dict(bar=T0 // 900, due=0, tries=2, done=False)
_, out = run(L.tvlog_step, T0 + 40, {'trades': []}, tl)
check('المحاولة الثالثة ❌ بارزة', '❌' in out)

# ───── تصحيحات مراجعة Gemini (24 نوفمبر) ─────
import importlib, urllib.request
importlib.reload(L); L.NTFY_KEY = 'KEY'; L.save = lambda st, msg, data=False: None; L.NTFY_TOPIC = 'x'
def alert(sym, t, k='KEY', **kw): return dict(k=k, sym=sym, side=1, t=t, c=1.0, atr=2.0, **kw)
def logev(fid, msg, fired='2026-11-24T15:00:00Z'): return dict(fire_id=fid, fired_at=fired, message=json.dumps(msg), webhook=dict(http_code=200))
NOW = T0 + 40
# (4) limit 100
seen_args = []
tv.call = lambda tool, args: (seen_args.append(args), dict(events=[]))[1]
run(L.tvlog_signals, NOW, {'trades': []}); check('(4) limit=100', seen_args[0] == {'days': 1, 'limit': 100})
# (2) فشل أبيان لإشارة وحدة: الباقي يمر، طلب سجل واحد، والفاشلة تنعاد بدون TradingView
calls = []; tv.call = lambda tool, args: (calls.append(1), dict(events=[logev(1, alert('NVDA', T0)), logev(2, alert('AMD', T0))]))[1]
def px(s):
    if s == 'NVDA': raise RuntimeError('أبيان طاح')
    return 100.0
L.stock_px = px; st = {'trades': []}
(live, late, failed), out = run(L.tvlog_signals, NOW, st)
check('(2) الباقي ما يسقط + الفاشلة مؤجلة + طلب سجل واحد', [t['symbol'] for t in live] == ['AMD'] and [P['sig']['symbol'] for P in failed] == ['NVDA'] and len(calls) == 1 and 'أعيد كل دقيقة' in out)
L.stock_px = lambda s: 100.0; L.pick_contract = lambda *a, **k: (date(2026, 12, 7), 100.0, 5.0)
tl = dict(bar=T0 // 900, due=0, tries=0, done=False, pending=failed)
run(L.tvlog_pending, NOW + 60, st, tl)
check('(2) tvlog_pending يدخلها بدون طلب TradingView جديد', len(calls) == 1 and [x['symbol'] for x in st['trades']] == ['NVDA'] and not tl['pending'])
# (3) تكرار داخل نفس الرد
tv.call = lambda tool, args: dict(events=[logev(1, alert('AMD', T0)), logev(2, alert('AMD', T0)), logev(1, alert('TSLA', T0))])
(live, _, _f), _ = run(L.tvlog_signals, NOW, {'trades': []})
check('(3) batch_seen في السجل (نفس sym-t ونفس fire_id)', [t['symbol'] for t in live] == ['AMD'])
lines = '\n'.join(json.dumps(dict(event='message', id=f'id{i}', message=json.dumps(alert('AMD', T0)))) for i in (1, 2))
class R:
    def __init__(s, b): s.b = b
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s): return s.b.encode()
urllib.request.urlopen = lambda req, timeout=0: R(lines)
st = {'trades': []}; out_ = L.webhook_signals(NOW, st)
check('(3) batch_seen في ntfy', len(out_) == 1 and st['ntfy']['last_id'] == 'id2')
# (1) ntfy: فشل أبيان للإشارة الثانية → الأولى ما تضيع وlast_id ما يتقدم عليها
lines = '\n'.join(json.dumps(dict(event='message', id=f'id{i}', message=json.dumps(alert(sy, T0)))) for i, sy in ((1, 'AMD'), (2, 'NVDA'), (3, 'TSLA')))
L.stock_px = px; st = {'trades': []}; out_, o = run(L.webhook_signals, NOW, st)
check('(1) الأولى تنرجع، last_id عند الأولى، ويوقف عند الفاشلة', [t['symbol'] for t in out_] == ['AMD'] and st['ntfy']['last_id'] == 'id1')
L.stock_px = lambda s: 100.0; L.mark_seen(st, NOW, dict(symbol='AMD', t=T0)); out_, _ = run(L.webhook_signals, NOW + 60, st)
check('(1) الدورة الجاية تكمل الفاشلة وما بعدها', [t['symbol'] for t in out_] == ['NVDA', 'TSLA'] and st['ntfy']['last_id'] == 'id3')
# (5) سقف طلبات التسعير
n = []; importlib.reload(L); L.expiries = lambda sym, d0: [date(2026, 12, 7), date(2026, 12, 11)]
r = L.pick_contract('AVGO', 'C', 300.0, T0, price=lambda *a: (n.append(1), None)[1], max_calls=8)
check('(5) max_calls=8 يوقف التسعير', r is None and len(n) == 8)
# (6) STALE_SEC=240
check('(6) STALE_SEC = 240', L.STALE_SEC == 240)
L.NTFY_KEY = 'KEY'
tv.call = lambda tool, args: dict(events=[logev(1, alert('AMD', T0))]); L.stock_px = lambda s: 100.0
(live, late, _f), _ = run(L.tvlog_signals, T0 + 300, {'trades': []}); check('(6) عمر 300ث → أثر رجعي مو حيّة', not live and len(late) == 1)
(live, late, _f), _ = run(L.tvlog_signals, T0 + 200, {'trades': []}); check('(6) عمر 200ث → حيّة', len(live) == 1 and not late)
# (7) ملي ثانية + مستقبل
sg = L.parse_alert(alert('AMD', T0 * 1000), 't', NOW); check('(7) t بالملي ثانية يتقسم', sg and sg['t'] == T0)
r, o = run(L.parse_alert, alert('AMD', T0 + 3600), 't', NOW); check('(7) مستقبل > 60ث يُرفض', r is None and 'المستقبل' in o)
check('(7) مستقبل ≤ 60ث مقبول', L.parse_alert(alert('AMD', T0 + 50), 't', NOW) is not None)
# (8) tv_bars بدون v
tv.call = lambda tool, args: dict(bars=dict(t=[1, 2], o=[1, 1], h=[1, 1], l=[1, 1], c=[1, 1]))
check('(8) tv_bars بدون v ما ينكسر', [b['v'] for b in L.tv_bars('X', 0, 9)] == [0, 0])
print('\nالنتيجة:', 'نجح كل شي' if all(ok) else 'فيه فشل', f'({sum(ok)}/{len(ok)})'); raise SystemExit(0 if all(ok) else 1)
