"""المليوني: جلب الأسعار الحقيقية وتسجيلها (يشتغل يوميًا من GitHub Actions بعد إغلاق السوق، بدون جهازك).

المصدر: Yahoo Finance (مجاني، بدون اشتراك):
  1) يحدّث شموع 15 دقيقة للأسهم وSPY في data/
  2) يطلع صفقات اليوم من الاستراتيجية (نفس كود almilyoni_weekly.py)
  3) لكل صفقة: يختار العقد (أقرب جمعة بعد 14 يوم+، وأي سترايك ولو بعيد عن السعر سعره بين 100$ و1000$؛
     يبدأ من أقرب سترايك للسعر، وإذا غالي أو رخيص يحسب التذبذب من سعر العقد الحقيقي ويقفز للسترايك المناسب)
     ويجيب سعره الحقيقي من صفقات العقد نفسه دقيقة بدقيقة: لحظة الدخول ولحظة الخروج
  4) يسجل في paper.json (والملخص الأسبوعي يقرأ منه)

قواعد الصدق: إذا العقد ما تداول خلال 10 دقائق من الدخول أو الخروج → الصفقة تنسجل "بدون سعر" (سيولة ضعيفة)،
وما نخترع رقم. شموع 1 دقيقة متوفرة في ياهو لآخر 30 يوم بس، فالتسجيل لازم ينعمل خلال شهر.

الاستخدام:
  python3 fetch_real.py                    # اليوم (توقيت نيويورك)
  python3 fetch_real.py --days 2026-10-27,2026-10-28
  python3 fetch_real.py --since 2026-10-13 # كل الأيام من تاريخ لين اليوم
"""
import json, time, math, argparse, urllib.request, urllib.error, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta, date
from types import SimpleNamespace
import almilyoni_weekly as m

ROOT = Path(__file__).resolve().parent
DATA, STATE = ROOT / 'data', ROOT / 'paper.json'
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'}
MAX_STRIKES = 15  # سترايكات لكل تاريخ انتهاء (قرار 14 نوفمبر: لازم يلقى عقد؛ أي سترايك ولو بعيد عن السعر، المهم 100–1000$ ويتحرك)
MAX_EXPIRIES = 3  # إذا ما لقى في التاريخ الأساسي يجرب التاريخ اللي بعده
VOL_MINUTES = 390  # كم دقيقة يغطي حجم تداول العقد المسجل (ياهو = اليوم كامل)
WIDE_MINUTES = lambda tr: 390
PREM_TGT_HI = 0.9 * (m.BUDGET - m.COMM)   # العقد غالي: نقفز لأقرب سترايك سعره المتوقع ~900$ (الأقرب للسعر يتحرك أكثر)
PREM_TGT_LO = 1.5 * m.PREM_MIN            # العقد أرخص من 100$: نقرّب للسعر لين ~150$
FILL_WIN = 600   # أقصى فرق (ثواني) بين وقت الدخول/الخروج وأقرب صفقة على العقد
NY = m.NY


# ───────── ياهو ─────────
def yget(sym, **params):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?' + urllib.parse.urlencode(params)
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                res = json.loads(r.read())['chart']['result']
            return res[0] if res else None
        except urllib.error.HTTPError as e:
            if e.code in (400, 404): return None
            time.sleep(4 * (k + 1))
        except Exception:
            time.sleep(4 * (k + 1))
    return None


def rth(t):
    d = datetime.fromtimestamp(t, NY); mm = d.hour * 60 + d.minute
    return d.weekday() < 5 and 570 <= mm < 960


def to_bars(res, step):
    if not res or not res.get('timestamp'): return []
    q = res['indicators']['quote'][0]; out = []
    for i, t in enumerate(res['timestamp']):
        v = [q[k][i] for k in ('open', 'high', 'low', 'close', 'volume')]
        if any(x is None for x in v) or t % step or not rth(t): continue
        out.append(dict(t=t, o=round(v[0], 4), h=round(v[1], 4), l=round(v[2], 4), c=round(v[3], 4), v=int(v[4])))
    return out


def day_span(day):
    lo = int(datetime(day.year, day.month, day.day, 9, 0, tzinfo=NY).timestamp())
    return lo, lo + 8 * 3600


def minute_bars(sym, day):
    lo, hi = day_span(day)
    return to_bars(yget(sym, interval='1m', period1=lo, period2=hi), 60)


# ───────── 1) تحديث شموع 15 دقيقة ─────────
def update_data():
    now = time.time(); info = {}
    for s in m.SYMBOLS:
        p = DATA / f'{s}-15m.json'
        new = [b for b in to_bars(yget(s, interval='15m', range='60d'), 900) if b['t'] + 900 <= now]
        if not new: info[s] = 'ما رجع بيانات'; continue
        old = json.loads(p.read_text()) if p.exists() else {'bars': [], 'symbol': s, 'interval': '15m'}
        ob = old['bars']
        if ob:   # نعيد آخر يوم مسجل (ممكن يكون ناقص) ونكمل من ياهو
            last_day = datetime.fromtimestamp(ob[-1]['t'], NY).date()
            ob = [b for b in ob if datetime.fromtimestamp(b['t'], NY).date() < last_day]
        last = ob[-1]['t'] if ob else 0
        merged = ob + [b for b in new if b['t'] > last]
        old.update(bars=merged, count=len(merged), updated=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        for k in ('has_more', 'next_date_to', 'hint'): old.pop(k, None)
        p.write_text(json.dumps(old, separators=(',', ':')))
        info[s] = f"{len(merged)} شمعة، آخرها {datetime.fromtimestamp(merged[-1]['t'], NY):%m-%d %H:%M}"
    return info


# ───────── 2/3) العقد وسعره الحقيقي ─────────
def next_friday(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d + timedelta(days=(4 - d.weekday()) % 7)


def occ(sym, exp, side, K):
    return f"{sym}{exp:%y%m%d}{side}{int(round(K * 1000)):08d}"


def first_after(bs, t, win=FILL_WIN):
    for b in bs:
        if t <= b['t'] <= t + win and b['v'] > 0: return b
    return None


def last_before(bs, t, win=FILL_WIN):
    c = [b for b in bs if t - win <= b['t'] <= t and b['v'] > 0]
    return c[-1] if c else None


def ema_last(vals, n=200):
    if len(vals) < n: return None
    e = sum(vals[:n]) / n
    for v in vals[n:]: e += (v - e) * 2 / (n + 1)
    return e


def exit_minute(tr, stock1m):
    """أول دقيقة لمس فيها السهم الهدف أو الوقف داخل شمعة الخروج (15د)."""
    if tr['why'] == 'نهاية الجلسة': return tr['t_exit'] + 840
    s = 1 if tr['side'] == 'C' else -1; lvl = tr['target'] if tr['why'] == 'هدف' else tr['stop']
    for b in stock1m:
        if tr['t_exit'] <= b['t'] < tr['t_exit'] + 900:
            if tr['why'] == 'هدف' and s * ((b['h'] if s == 1 else b['l']) - lvl) >= 0: return b['t']
            if tr['why'] == 'وقف' and s * ((b['l'] if s == 1 else b['h']) - lvl) <= 0: return b['t']
    return tr['t_exit']


def jump_strike(side, S, K, T, iv, inc, too_expensive):
    """أول سترايك سعره المتوقع (بتذبذب العقد الحقيقي) يدخل الميزانية: نبعد عن السعر إذا غالي، ونقرّب إذا رخيص."""
    d = (1 if side == 'C' else -1) * (1 if too_expensive else -1)
    k = K
    for _ in range(400):
        k = round(k + d * inc, 2)
        if k <= 0: return None
        v = m.bs_price(side, S, k, T, iv) * 100
        if (too_expensive and v <= PREM_TGT_HI) or (not too_expensive and v >= PREM_TGT_LO): return k
    return None


def expiry_list(min_expiry):
    """تواريخ الانتهاء بالترتيب: الأساسي أولًا، وإذا ما لقينا عقد نجرب اللي بعده."""
    e = next_friday(min_expiry)
    return [e, e + timedelta(days=7), e + timedelta(days=14)]


def wide_bars(sym, day):
    """شموع العقد من الدخول لين الإغلاق (ياهو يجيب اليوم كامل أصلًا)."""
    return minute_bars(sym, day)


def nearest_fill(bs, fe, tx):
    """أقرب صفقة حقيقية على العقد لوقت الخروج (بعده أو قبله، بعد الدخول)."""
    c = [b for b in bs if b['v'] > 0 and b['t'] > fe['t']]
    return min(c, key=lambda b: abs(b['t'] - tx)) if c else None


def price_trade(tr, day, stock1m, vix_chg):
    """قرار 14 نوفمبر: لازم يلقى عقد. يجرب لين MAX_STRIKES سترايك في كل تاريخ انتهاء (لين MAX_EXPIRIES تواريخ)،
    وإذا العقد ما تداول بالضبط وقت الخروج ياخذ أقرب صفقة حقيقية عليه (ويكتب كم دقيقة تبعد). ما نخترع سعر."""
    side, e = tr['side'], tr['entry']
    inc = 2.5 if e < 250 else 5.0
    step = inc if side == 'C' else -inc            # step = نبعد عن السعر (OTM)، -step = نقرّب
    q = dict(id=tr['id'], bucket='14')
    c1 = [b['c'] for b in stock1m if b['t'] < tr['t_entry']]
    c5 = [b['c'] for b in stock1m if b['t'] < tr['t_entry'] and (b['t'] // 60) % 5 == 4]
    e1, e5 = ema_last(c1), ema_last(c5, 200)
    if e1 and e5:
        q['ema200_ok'] = (e > e1 and e > e5) if side == 'C' else (e < e1 and e < e5)
    if vix_chg is not None: q['vix_chg_pct'] = vix_chg
    tx = exit_minute(tr, stock1m)
    for exp in expiry_list(tr['min_expiry'])[:MAX_EXPIRIES]:
        K0 = math.ceil(e / inc) * inc if side == 'C' else math.floor(e / inc) * inc
        queue, seen = [K0], set()
        while queue and len(seen) < MAX_STRIKES:
            K = queue.pop(0)
            if K <= 0 or K in seen: continue
            seen.add(K)
            sym = occ(tr['symbol'], exp, side, K)
            bs = minute_bars(sym, day)
            ref = (last_before(bs, tr['t_entry'], 6 * 3600) or first_after(bs, tr['t_entry'])) if bs else None
            if not ref:                     # السترايك ما تداول أو مو موجود: نجرب اللي بعده، وبعدين الأقرب للسعر
                queue += [round(K + step, 2), round(K - step, 2)]; continue
            if m.PREM_MIN <= ref['c'] * 100 and ref['c'] * 100 + m.COMM <= m.BUDGET:   # العقد من 100$ إلى 1000$
                fe = first_after(bs, tr['t_entry'])
                fx = (last_before(bs, tr['t_exit'] + 899, 900) if tr['why'] == 'نهاية الجلسة'
                      else first_after(bs, tx) or last_before(bs, tx, 300)) if fe else None
                note = ''; vb = bs; mins = VOL_MINUTES
                if fe and not fx:           # ما تداول بالضبط وقت الخروج: أقرب صفقة حقيقية عليه
                    vb = wide_bars(sym, day); mins = WIDE_MINUTES(tr)
                    fx = nearest_fill(vb, fe, tx)
                    if fx: note = f"سعر العقد الحقيقي (أقرب تداول، يبعد {round(abs(fx['t'] - tx) / 60)} دقيقة عن الخروج)"
                if fe and fx and fx['t'] >= fe['t']:
                    q.update(expiry=exp.isoformat(), strike=K, opt_symbol=sym, opt_day_vol=sum(b['v'] for b in vb),
                             opt_vol_minutes=mins, opt_entry=fe['c'], opt_exit=fx['c'], opt_entry_hi=fe['h'],
                             opt_exit_lo=fx['l'], opt_entry_t=fe['t'], opt_exit_t=fx['t'])
                    if note: q['price_note'] = note
                    for b in vb:            # مقارنة: خروج ±50% من سعر العقد قبل الخروج الأصلي
                        if fe['t'] < b['t'] < fx['t'] and b['v'] > 0:
                            if b['h'] >= 1.5 * fe['c']: q['exit50_usd'] = m._pnl(fe['c'], 1.5 * fe['c'])[0]; break
                            if b['l'] <= 0.5 * fe['c']: q['exit50_usd'] = m._pnl(fe['c'], 0.5 * fe['c'])[0]; break
                    return q
                # داخل الميزانية بس ما تداول وقت الدخول: نجرب الجيران (الأقرب للسعر أول، أكثر سيولة)
                queue = [round(K - step, 2), round(K + step, 2)] + queue; continue
            # السعر الحقيقي خارج 100–1000$: نحسب تذبذب العقد من سعره الحقيقي ونقفز مباشرة للسترايك المناسب
            too_exp = ref['c'] * 100 + m.COMM > m.BUDGET
            T = m.yearfrac(ref['t'], exp.isoformat())
            iv = m.implied_vol(ref['c'], e, K, T, side)
            nk = jump_strike(side, e, K, T, iv, inc, too_exp) if iv else None
            queue.insert(0, nk if nk else round(K + (step if too_exp else -step), 2))
    q['note'] = 'ما لقيت عقد ضمن الميزانية له تداول'   # المفروض ما يصير أبدًا بعد التوسيع
    return q


_VIX = None
def vix_change(day, t_entry):
    global _VIX
    if _VIX is None: _VIX = to_bars(yget('^VIX', interval='15m', range='60d'), 900)
    prev = [b for b in _VIX if datetime.fromtimestamp(b['t'], NY).date() < day]
    cur = [b for b in _VIX if b['t'] <= t_entry and datetime.fromtimestamp(b['t'], NY).date() == day]
    if not prev or not cur: return None
    return round(100 * (cur[-1]['c'] / prev[-1]['c'] - 1), 2)


def load_data():
    return {s: json.loads((DATA / f'{s}-15m.json').read_text())['bars'] for s in m.SYMBOLS if (DATA / f'{s}-15m.json').exists()}


def plan_for(data, day):
    out = []
    for n, t in enumerate(m.day_trades(data, day)):
        out.append(dict(t, id=f"{day}-{t['symbol']}-{n}",
                        min_expiry=(datetime.fromtimestamp(t['t_entry'], NY).date() + timedelta(days=m.DTE_MIN)).isoformat()))
    return out


def record_day(data, day):
    trades = plan_for(data, day)
    quotes = []; cache = {}
    for tr in trades:
        if tr['symbol'] not in cache: cache[tr['symbol']] = minute_bars(tr['symbol'], day)
        q = price_trade(tr, day, cache[tr['symbol']], vix_change(day, tr['t_entry']))
        quotes.append(q)
        print(' ', tr['id'], tr['side'], q.get('opt_symbol', '—'), q.get('opt_entry'), '→', q.get('opt_exit'), q.get('note', ''))
    qp = ROOT / '.quotes.json'; qp.write_text(json.dumps(quotes, ensure_ascii=False))
    m.cmd_close_day(SimpleNamespace(day=str(day), quotes=str(qp), state=str(STATE)), data)
    qp.unlink()
    return len(trades)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', default=''); ap.add_argument('--since', default=''); ap.add_argument('--no-update', action='store_true')
    a = ap.parse_args()
    if not a.no_update:
        for s, v in update_data().items(): print(s, v)
    today = datetime.now(NY).date()
    if a.since.strip():
        d = date.fromisoformat(a.since.strip()); days = []
        while d <= today:
            if d.weekday() < 5: days.append(d)
            d += timedelta(days=1)
    elif a.days.strip():
        days = [date.fromisoformat(x.strip()) for x in a.days.split(',') if x.strip()]
    else:
        days = [today] if today.weekday() < 5 else []
    data = load_data()
    for d in days:
        if (today - d).days > 29: print(d, 'أقدم من 30 يوم، ياهو ما عنده شموع دقيقة له'); continue
        print('==', d); record_day(data, d)


if __name__ == '__main__':
    main()
