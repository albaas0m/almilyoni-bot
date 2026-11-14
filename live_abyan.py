"""المليوني اللحظي (قرار 14 نوفمبر 2026): البوت يشتغل وقت السوق على GitHub بنفسه، ويجيب سعر العقد من أبيان مباشرة.

كل 15 دقيقة (بعد إغلاق الشمعة): يحدّث شموع الأسهم ويطلع إشارات الاستراتيجية نفسها (almilyoni_weekly.trades_for).
إشارة جديدة → يختار العقد ويسجل سعره من أبيان لحظة الدخول (fmv).
كل دقيقة: يشيك سعر السهم من أبيان على الهدف/الوقف، ولما يلمس (أو نهاية الجلسة) يسجل سعر العقد من أبيان لحظة الخروج.
كل شي ينحفظ في live.json، وتسجيل آخر اليوم (cmd_close_day) يقدّم أسعار أبيان اللحظية على أي مصدر ثاني.

  python3 live_abyan.py            # يشتغل لين نهاية الجلسة أو حد وقت الجوب (5:50 ساعة)
  python3 live_abyan.py --once     # جولة وحدة (اختبار)
"""
import json, math, time, argparse, subprocess, traceback
from pathlib import Path
from datetime import datetime, date, timedelta
import almilyoni_weekly as m
import fetch_real as f
import abyan
import tv

ROOT = Path(__file__).resolve().parent; LIVE = ROOT / 'live.json'; NY = m.NY
MAX_STRIKES, JOB_SEC = 15, 5 * 3600 + 50 * 60
T0 = time.time()


def log(*a): print(datetime.now(NY).strftime('%H:%M:%S'), *a, flush=True)


def load():
    return json.loads(LIVE.read_text()) if LIVE.exists() else {'trades': []}


def save(st, msg):
    LIVE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    try:   # نحفظ في الريبو على طول (عشان لو الجوب انقطع ما يضيع شي)
        subprocess.run('git add live.json abyan_token.enc tv_token.enc && git commit -qm "' + msg + '" && '
                       'git pull -q --rebase origin main && git push -q', shell=True, cwd=ROOT, timeout=90)
    except Exception as e: log('git:', e)


# ───────── أبيان ─────────
def stock_px(sym):
    return float(abyan.call('get_current_price', dict(symbol=sym))['fmv'])


def opt_px(sym, exp, side, K):
    try: return abyan.option_price(sym, exp.isoformat(), side, K)
    except Exception: return None          # العقد مو موجود أو ما له سعر


def expiries(sym, d0):
    r = abyan.call('browse_options_chain', dict(symbol=sym))
    exps = [date.fromisoformat(x) for x in r['expiration_dates']]
    key = lambda x: abs((x - d0).days - m.DTE_MIN)
    main = sorted([x for x in exps if m.DTE_LO <= (x - d0).days <= m.DTE_HI], key=key)
    return main + sorted([x for x in exps if 7 <= (x - d0).days <= 35 and x not in main], key=key)


def pick_contract(sym, side, S, now):
    """أي سترايك سعره من أبيان بين 100$ و1000$ (يقفز بالتذبذب المحسوب من سعر أبيان نفسه)."""
    inc = 2.5 if S < 250 else 5.0; step = inc if side == 'C' else -inc
    for exp in expiries(sym, datetime.fromtimestamp(now, NY).date())[:3]:
        K = math.ceil(S / inc) * inc if side == 'C' else math.floor(S / inc) * inc
        queue, seen = [K], set()
        while queue and len(seen) < MAX_STRIKES:
            K = queue.pop(0)
            if K <= 0 or K in seen: continue
            seen.add(K); p = opt_px(sym, exp, side, K)
            if not p: queue += [round(K + step, 2), round(K - step, 2)]; continue
            if m.PREM_MIN <= p * 100 and p * 100 + m.COMM <= m.BUDGET: return exp, K, p
            too_exp = p * 100 + m.COMM > m.BUDGET
            T = m.yearfrac(now, exp.isoformat()); iv = m.implied_vol(p, S, K, T, side)
            nk = f.jump_strike(side, S, K, T, iv, inc, too_exp) if iv else None
            queue.insert(0, nk if nk else round(K + (step if too_exp else -step), 2))
    return None


# ───────── الإشارات ─────────
def update_tv(now):
    """شموع 15د من حساب أحمد في TradingView (المكتملة بس)، تندمج مع ملفات data/."""
    for s in m.SYMBOLS:
        new = [b for b in tv.bars(s, '15m', 60) if b['t'] + 900 <= now]
        p = f.DATA / f'{s}-15m.json'; old = json.loads(p.read_text())
        first = new[0]['t'] if new else 1e12
        old['bars'] = [b for b in old['bars'] if b['t'] < first] + new
        old.update(count=len(old['bars']), source_note='TradingView (حساب أحمد) — لحظي')
        p.write_text(json.dumps(old, separators=(',', ':')))


def new_signals(now):
    try: update_tv(now)                               # المصدر الأساسي: حسابك في TradingView
    except Exception as e:
        log('TradingView ما رد، أستخدم ياهو هالمرة:', e); f.update_data()
    data = f.load_data()
    t_next = (int(now) // 900) * 900                  # الشمعة اللي بدت الحين = شمعة الدخول
    for s in m.SYMBOLS:                               # شمعة دخول مؤقتة بسعر أبيان الحين (الاستراتيجية تدخل افتتاح الشمعة التالية)
        bs = data.get(s)
        if not bs or bs[-1]['t'] + 900 != t_next: continue
        px = stock_px(s) if s in m.TRADE_SYMBOLS else bs[-1]['c']
        bs.append(dict(t=t_next, o=px, h=px, l=px, c=px, v=0))
    return [t for t in m.trades_for(data) if t['t_entry'] == t_next]


def enter(st, t, now):
    key = f"{t['symbol']}-{t['t_entry']}"
    if any(x['key'] == key for x in st['trades']): return
    if any(x['symbol'] == t['symbol'] and not x.get('t_exit') for x in st['trades']): return
    c = pick_contract(t['symbol'], t['side'], t['entry'], now)
    rec = dict(key=key, day=datetime.fromtimestamp(now, NY).date().isoformat(), symbol=t['symbol'], side=t['side'],
               t_entry=t['t_entry'], entry=t['entry'], target=t['target'], stop=t['stop'], atr=t['atr'])
    if c:
        exp, K, p = c
        rec.update(expiry=exp.isoformat(), strike=K, opt_entry=p, opt_entry_at=int(now), src='أبيان لحظي')
        log('دخول', t['symbol'], t['side'], K, exp, f'{p * 100:.0f}$')
    else:
        rec.update(note='ما لقيت عقد 100–1000$ في أبيان', t_exit=int(now), why='بدون عقد'); log('دخول بدون عقد', key)
    st['trades'].append(rec); save(st, f"لحظي: دخول {t['symbol']} {t['side']}")


def check_exits(st, now, close_all=False):
    for r in st['trades']:
        if r.get('t_exit') or 'strike' not in r: continue
        s = 1 if r['side'] == 'C' else -1; px = stock_px(r['symbol']); why = None
        if s * (px - r['stop']) <= 0: why = 'وقف'
        elif s * (px - r['target']) >= 0: why = 'هدف'
        elif close_all: why = 'نهاية الجلسة'
        if not why: continue
        p = opt_px(r['symbol'], date.fromisoformat(r['expiry']), r['side'], r['strike'])
        r.update(t_exit=int(now), exit=px, why=why, opt_exit=p)
        usd = None if p is None else round((p - r['opt_entry']) * 100 - 2 * m.COMM)
        r['opt_usd'] = usd
        log('خروج', r['symbol'], why, f'{p * 100:.0f}$' if p else '—', usd)
        save(st, f"لحظي: خروج {r['symbol']} ({why})")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--once', action='store_true'); ap.add_argument('--test', nargs=2)
    a = ap.parse_args()
    if a.test:   # اختبار: سعر السهم واختيار العقد من أبيان
        t0 = time.time(); update_tv(time.time())
        log('شموع TradingView: آخر شمعة', f.load_data()[a.test[0]][-1], f'({time.time() - t0:.1f} ثانية)')
        t0 = time.time(); S = stock_px(a.test[0]); c = pick_contract(a.test[0], a.test[1], S, time.time())
        log(a.test, 'السهم', S, '← العقد', c and (c[0].isoformat(), c[1], f'{c[2] * 100:.0f}$'), f'({time.time() - t0:.1f} ثانية)')
        save(load(), 'لحظي: اختبار'); return
    st = load(); last_bar = None
    while True:
        now = time.time(); d = datetime.fromtimestamp(now, NY); mins = d.hour * 60 + d.minute - 570
        if d.weekday() >= 5 or mins >= 392 or time.time() - T0 > JOB_SEC:
            log('خلص الوقت'); save(st, 'لحظي: نهاية الجوب'); break
        if mins < -2: time.sleep(60); continue
        try:
            bar = int(now) // 900
            if 0 <= mins <= m.LATE_MIN + 1 and bar != last_bar and now % 900 >= 25:   # 25 ثانية بعد إغلاق الشمعة
                last_bar = bar
                for t in new_signals(now): enter(st, t, now)
            check_exits(st, now, close_all=mins >= 389)                                # 15:59 يقفل الكل
        except Exception:
            log(traceback.format_exc()[-600:])
        if a.once: break
        if int(now) // 1800 != int(time.time() + 60) // 1800: save(st, 'لحظي: حفظ التوكن')   # كل نص ساعة
        time.sleep(max(5, 60 - time.time() % 60 + 2))


if __name__ == '__main__':
    main()
