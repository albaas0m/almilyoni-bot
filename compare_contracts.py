"""مقارنة اختيار العقد القديم بالجديد على صفقات paper.json (24 نوفمبر 2026). python3 compare_contracts.py
محاكاة بنموذج: تذبذب كل صفقة ثابت (iv المحفوظ) لكل السترايكات والتواريخ، وما فيه بيانات سيولة تاريخية (الفلاتر الجديدة تشتغل بدلتا + نسبة ربح/خسارة بس).
"""
import json, math
from datetime import datetime, timedelta, date
import almilyoni_weekly as m, contract_select as cs

d = json.load(open('paper.json', encoding='utf-8')); rows = []


def fridays(t0):
    d0 = datetime.fromtimestamp(t0, m.NY).date(); out = []
    for i in range(7, 36):
        x = d0 + timedelta(days=i)
        if x.weekday() == 4: out.append(x)
    main = sorted([x for x in out if m.DTE_LO <= (x - d0).days <= m.DTE_HI], key=lambda x: abs((x - d0).days - m.DTE_MIN))
    return (main + [x for x in out if x not in main])[:3]


def modelled_pnl(c, side, S0, S1, hold_h):
    """ربح الصفقة بالنموذج لو خرج السهم عند S1 بعد hold_h ساعة (بنفس التذبذب)."""
    T2 = max(c['T'] - hold_h / (365 * 24), 1e-6); v = m.bs_price(side, S1, c['K'], T2, c['iv'])
    return (v - c['price']) * 100 - 2 * m.COMM


for t in d['trades']:
    if not (t.get('strike') and t.get('expiry') and t.get('target') and t.get('t_exit') and (t.get('iv') or t.get('premium'))): continue
    S, side = t['entry'], 'C' if t['target'] > t['entry'] else 'P'
    if not t.get('iv'):                                       # الصفقات القديمة بدون iv: نحسبه من سعر دخول العقد الحقيقي
        iv = m.implied_vol(t['premium'] / 100, S, t['strike'], m.yearfrac(t['t_entry'], t['expiry']), side)
        if not iv: continue
        t = dict(t, iv=iv)
    inc = 2.5 if S < 250 else 5.0; hold = max((t['t_exit'] - t['t_entry']) / 3600, 0.05)
    old_exp = date.fromisoformat(t['expiry']); T_of = lambda e: m.yearfrac(t['t_entry'], e.isoformat())
    old_p = m.bs_price(side, S, t['strike'], T_of(old_exp), t['iv'])
    c_old = cs.make_cand(old_exp, side, t['strike'], old_p, S, t['t_entry'])
    cands = []
    for e in fridays(t['t_entry']):
        for k in range(-14, 15):
            K = round(math.floor(S / inc) * inc + k * inc, 2)
            if K <= 0: continue
            p = m.bs_price(side, S, K, T_of(e), t['iv'])
            if p < 0.05: continue
            c = cs.make_cand(e, side, K, round(p, 2), S, t['t_entry'])
            if c: cands.append(c)
    # target/stop المحفوظين لكل صفقة (قواعدها وقتها)
    sel, why = cs.select(cands, side, S, 1.0, tgt_px=t['target'], stp_px=t['stop'])
    if not sel or not c_old: continue
    new = sel['c']; g0, l0, r0 = cs.score(c_old, side, S, 1.0, tgt_px=t['target'], stp_px=t['stop'])
    rows.append(dict(id=t['id'], sym=t['symbol'], why=t['why'], old=(t['strike'], t['expiry'], round(old_p * 100), round(abs(c_old['delta']), 2), round(r0, 2)),
                     new=(new['K'], new['exp'].isoformat(), round(new['price'] * 100), round(abs(new['delta']), 2), sel['ratio']), level=sel['level'],
                     pnl_old=modelled_pnl(c_old, side, S, t['exit'], hold), pnl_new=modelled_pnl(new, side, S, t['exit'], hold), real_old=t.get('opt_usd')))
n = len(rows); avg = lambda f: sum(f(r) for r in rows) / n
print(f'صفقات قديمة قارنتها: {n}')
for r in rows[:8]: print(r['id'], r['why'], '| قديم', r['old'], '| جديد', r['new'], r['level'], '| نموذج $', round(r['pnl_old']), '→', round(r['pnl_new']))
print(f"متوسط الدلتا: قديم {avg(lambda r: r['old'][3]):.2f} → جديد {avg(lambda r: r['new'][3]):.2f}")
print(f"متوسط نسبة ربح/خسارة (نموذج): قديم {avg(lambda r: r['old'][4]):.2f} → جديد {avg(lambda r: r['new'][4]):.2f}")
print(f"متوسط سعر العقد: قديم {avg(lambda r: r['old'][2]):.0f}$ → جديد {avg(lambda r: r['new'][2]):.0f}$")
print(f"دلتا القديم خارج 0.30–0.55: {sum(1 for r in rows if not .30 <= r['old'][3] <= .55)} من {n}؛ الجديد: {sum(1 for r in rows if not .30 <= r['new'][3] <= .55)}")
print(f"ربح النموذج الإجمالي: قديم {sum(r['pnl_old'] for r in rows):.0f}$ → جديد {sum(r['pnl_new'] for r in rows):.0f}$ | الفعلي للقديم (سعر حقيقي) {sum(r['real_old'] or 0 for r in rows):.0f}$")
print(f"مستويات الفلاتر:", {k: sum(1 for r in rows if r['level'] == k) for k in {r['level'] for r in rows}})
print(f"نسبة الصفقات اللي الجديد أحسن (نموذج): {sum(1 for r in rows if r['pnl_new'] > r['pnl_old'])}/{n}")
