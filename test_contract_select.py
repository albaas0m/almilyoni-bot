"""اختبار اختيار العقد (contract_select + gather_live): python3 test_contract_select.py — بدون شبكة."""
from datetime import date, datetime
import almilyoni_weekly as m, contract_select as cs, tv
import live_abyan as L
ok = []
def check(name, cond): cond = bool(cond); ok.append(cond); print(('✅' if cond else '❌'), name)
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=m.NY).timestamp(); EXP = date(2026, 12, 8); S, A = 300.0, 3.0
def cand(K, iv=0.40, **kw):
    T = m.yearfrac(NOW, EXP.isoformat()); p = round(m.bs_price('C', S, K, T, iv), 2)
    return cs.make_cand(EXP, 'C', K, p, S, NOW, **kw)
def pick(cands): return cs.select(cands, 'C', S, A)

# دلتا بلاك-شولز منطقية
c = cand(300.0); check('دلتا ATM ≈ 0.5', 0.48 < c['delta'] < 0.60)
check('دلتا بعيد 20% < 0.05', abs(cand(360.0)['delta']) < 0.05)
# فلتر السيولة الصارم يفضّل السائل داخل نطاق الدلتا
liq = cand(305.0, vol=200, oi=3000, bid=7.0, ask=7.2); dead = cand(300.0, vol=3, oi=20, bid=6.0, ask=9.0); far = cand(340.0, vol=500, oi=5000, bid=0.5, ask=0.55)
sel, _ = pick([dead, liq, far]); check('يختار السائل داخل الدلتا (305) ويتجاهل الميت (300) والبعيد (340)', sel and sel['c']['K'] == 305.0 and sel['level'] == 'كل الفلاتر' and not sel['relaxed'])
# التخفيف: السيولة أولاً
sel, _ = pick([dead, cand(305.0, vol=15, oi=150, bid=7.0, ask=7.9)])
check('ما فيه سائل صارم ← يخفف السيولة ويكتب ذلك', sel and sel['level'] == 'سيولة مخففة' and sel['relaxed'] == ['سيولة مخففة'] and sel['c']['K'] == 305.0)
sel, _ = pick([dead]); check('كلها ميتة ← بدون فلتر سيولة (والدلتا باقية)', sel and sel['level'] == 'بدون فلتر سيولة' and sel['relaxed'])
# ثم الدلتا لين 0.25
c27 = cand(315.0, iv=0.40); d27 = abs(c27['delta']); sel, _ = pick([c27])
check(f'دلتا {d27:.2f} (0.25–0.30) ← يخفف الدلتا لين 0.25' if 0.25 <= d27 < 0.30 else f'دلتا {d27:.2f} خارج نطاق الاختبار', 0.25 <= d27 < 0.30 and sel and sel['level'] == 'دلتا ≥ 0.25 وبدون سيولة')
# دايم يلقى عقد: لو الدلتا كلها برا حتى 0.25 يختار الأفضل بدون فلاتر (ويسجل)
sel, why = pick([cand(345.0, iv=0.60)]); check('دايم يلقى عقد ضمن الميزانية (بدون فلتر دلتا وسيولة)', sel and sel['level'] == 'بدون فلتر دلتا وسيولة' and sel['relaxed'])
# الميزانية سقف: ولا عقد ضمن 100–1000$ ← None بسبب واضح
big = cand(250.0); sel, why = pick([big]); check('فوق الميزانية يُرفض بسبب واضح', sel is None and 'الميزانية' in why and big['price'] * 100 > 1000)
# بيانات السيولة الناقصة ما ترفض وتتسجل "غير متوفر"
sel, _ = pick([cand(305.0)]); f = cs.record_fields(sel)
check('سيولة غير متوفرة: ما نرفض ونسجل غير متوفر', sel['level'] == 'كل الفلاتر' and f['liq_unknown'] == ['vol', 'oi', 'spread'] and 'غير متوفر' in f['why_chosen'])
check('حقول التسجيل كاملة (delta، حجم، OI، سبريد، سبب)', all(k in f for k in ('delta', 'day_vol', 'oi', 'spread_pct', 'why_chosen', 'rr_ratio', 'filter_level', 'filters_relaxed')))
# السبريد ينزّل النسبة
a = cs.score(cand(305.0, bid=7.0, ask=7.2), 'C', S, A); b = cs.score(cand(305.0), 'C', S, A); w = cs.score(cand(305.0, bid=6.0, ask=8.2), 'C', S, A)
check('السبريد الواسع ينزّل نسبة الربح/الخسارة', w[2] < a[2] < b[2] + 0.2 and w[0] < b[0])
# المفاضلة بالنسبة: بين مرشحين سائلين بنفس الدلتا يختار الأعلى نسبة (تذبذب أرخص)
cheap = cand(305.0, iv=0.30, vol=100, oi=1000); rich = cand(305.0, iv=0.60, vol=100, oi=1000)
sel, _ = pick([rich, cheap]); check('يختار الأعلى (ربح÷خسارة)', sel['c'] is (cheap if cs.score(cheap, 'C', S, A)[2] >= cs.score(rich, 'C', S, A)[2] else rich))
# gather_live: سلسلة أبيان + تسعير fmv لأقرب 3 تواريخ، بدون TradingView
exps = [date(2026, 12, 4), date(2026, 12, 7), date(2026, 12, 11), date(2026, 12, 18)]; L.expiries = lambda sym, d0: exps
KS = [float(k) for k in range(280, 331, 5)]; calls = []
L.abyan.call = lambda tool, args: (calls.append(args['expiration_date']), dict(contracts=[dict(strike_price=k) for k in KS]))[1]
def px(sym, exp, side, K): T = m.yearfrac(NOW, exp.isoformat()); return round(m.bs_price(side, S, K, T, 0.40), 2)
L.opt_px = px
def nope(*a, **k): raise AssertionError('ما يجوز طلب TradingView')
tv.call = nope
cands = L.gather_live('AVGO', 'C', S, NOW)
check('يجمع من أقرب 3 تواريخ بس بدون TradingView', {c['exp'] for c in cands} == set(exps[:3]) and sorted(set(calls)) == [e.isoformat() for e in exps[:3]])
check('كل مرشح فيه دلتا وتذبذب وسعر، وعددهم معقول (≤ 7 لكل تاريخ)', all(c['delta'] and c['iv'] and c['price'] for c in cands) and 6 <= len(cands) <= 21)
L.choose_contract.__globals__['gather_live'] = L.gather_live
sel = L.choose_contract('AVGO', 'C', S, A, NOW, {'trades': []})
check('choose_contract يرجع عقد داخل نطاق الدلتا من المرشحين', sel and 0.25 <= abs(sel['c']['delta']) <= 0.55 and sel['ratio'] is not None)
print('\nالنتيجة:', 'نجح كل شي' if all(ok) else 'فيه فشل', f'({sum(ok)}/{len(ok)})'); raise SystemExit(0 if all(ok) else 1)
