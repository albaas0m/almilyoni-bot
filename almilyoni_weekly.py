"""المليوني v3 (30 أكتوبر 2026): كول وبوت + عكس الترند اليومي + 8 أسهم + ميزانية 1000$ + عقد جمعة 14 يوم+
+ فلتر العقد الغالي وفلتر السيولة + مقارنات (VIX، Dr Tip، ميزانيات 500/800/2×500، خروج ±50%).
v2.1: سعر العقد الحقيقي دخول وخروج من شموع OPRA.

الاستخدام:
  python3 almilyoni_weekly.py --data DIR                 # آخر أسبوع في البيانات
  python3 almilyoni_weekly.py --data DIR --week 2026-09-07
  python3 almilyoni_weekly.py --data DIR --all           # ملخص كل الفترة
  python3 almilyoni_weekly.py --data DIR --history results.json   # يضيف/يحدّث الأسبوع في ملف السجل
DIR فيه ملفات {SYMBOL}-15m.json من موصل TradingView (حقل bars: t,o,h,l,c,v)، ومنها SPY.

قواعد الاستراتيجية (لكل اتجاه، والبوت عكس الشرط تمامًا):
  CALL: UT Buy (ATR10، حساسية1) + إغلاق فوق ZLSMA32 + فوق VWAP + SPY فوق VWAP حقه + السهم تحت EMA50 اليومي (عكس الترند)
  PUT : UT Sell + إغلاق تحت ZLSMA32 + تحت VWAP + SPY تحت VWAP حقه + السهم فوق EMA50 اليومي
+ حجم ≥ 1.2× متوسط 20 شمعة + إغلاق الإشارة خلال أول 120 دقيقة (أو حتى 3:00 نيويورك إذا الحجم ≥ 2×). دخول افتتاح الشمعة التالية.
هدف 1 ATR، وقف 3 ATR (لمسهما معًا = وقف)، وإلا خروج آخر شمعة في الجلسة. صفقة وحدة لكل سهم في نفس الوقت.
EMA50 اليومي محسوب من إغلاقات الجلسات السابقة فقط (بدون نظر للمستقبل).

تقدير العقد (ليس سعرًا حقيقيًا): أقرب جمعة تبعد 14 يوم أو أكثر، سترايك ATM،
وإذا العقد أغلى من الميزانية (1000$) ينتقل لسترايك أبعد (OTM) لين يصير ضمنها.
العقد الغالي = القيمة الزمنية للسهم الواحد أكبر من متوسط مدى السهم اليومي (14 جلسة) → الصفقة تتجاهل.
السيولة الضعيفة = حجم تداول العقد في اليوم أقل من 100 عقد (لما يتوفر السعر الحقيقي) → الصفقة تتجاهل.
التسعير Black-Scholes بتذبذب = التذبذب الفعلي لآخر 20 جلسة × 0.81 (معايَر على أسعار أبيان، حد أدنى 12%)، سبريد 1% لكل جهة (حد أدنى 0.02$)، وعمولة 0.995$ لكل أمر.
"""
import json, math, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

TRADE_SYMBOLS = ['NVDA', 'AAPL', 'AMD', 'AVGO', 'TSLA', 'LRCX', 'TSM', 'ARM']   # الأسهم الحلال اللي لها عقود في أبيان (توسعة 24 أكتوبر)
SYMBOLS = TRADE_SYMBOLS + ['SPY']   # SPY للفلتر فقط (فوق VWAP)، ما يتداول
STEP = 900; NY = ZoneInfo('America/New_York'); RY = ZoneInfo('Asia/Riyadh')
TGT, STP, VOLX, WIN_MIN, COST_PCT = 1.0, 3.0, 1.2, 120, 0.03
LATE_MIN, VOLX_LATE = 330, 2.0   # بعد أول ساعتين وحتى 3:00 نيويورك: تنقبل الإشارة بس إذا الحجم ≥ 2× المتوسط (قرار المستخدم 10 أكتوبر 2026)
BUDGET, COMM, RATE = 1000.0, 0.995, 0.04   # 1000$ عقد واحد = الأساس (قرار 28 أكتوبر)
CMP_BUDGETS = (500.0, 800.0)                # للمقارنة فقط، و"2×500" = عقدين كل واحد ≤ 500$
DTE_MIN = 14                                # أقرب جمعة بعد 14 يوم أو أكثر
EMA_D, ADR_N = 50, 14                        # EMA50 اليومي (عكس الترند) ومتوسط المدى اليومي (فلتر العقد الغالي)
TV_MAX_ADR = 1.0                             # القيمة الزمنية للسهم ≤ 1× المدى اليومي، وإلا غالي
MIN_OPT_VOL = 100                            # أقل حجم تداول يومي للعقد (فلتر السيولة)
VIX_SPIKE = 5.0                              # مقارنة فقط: تجاهل CALL إذا VIX طالع ≥5%، وPUT إذا نازل ≥5%
DRTIP_START = 30                             # مقارنة فقط: Dr Tip ما يدخل قبل 10:00 نيويورك (30 دقيقة بعد الافتتاح)
IV_MULT = 0.81   # معايَر على أسعار أبيان الحقيقية (3 أكتوبر 2026): النموذج بـ1.15 كان أغلى من السوق بـ42%


# ───────── المؤشرات ─────────
def linreg(v, n=32):
    out = [None] * len(v); xm = (n - 1) / 2; den = sum((j - xm) ** 2 for j in range(n))
    for i in range(n - 1, len(v)):
        ys = v[i - n + 1:i + 1]
        if any(y is None for y in ys): continue
        m = sum(ys) / n; sl = sum((j - xm) * (y - m) for j, y in enumerate(ys)) / den
        out[i] = m + sl * xm
    return out


def ut_zlsma(bs):
    c = [b['c'] for b in bs]; f1 = linreg(c); f2 = linreg(f1)
    z = [2 * a - b if b is not None else None for a, b in zip(f1, f2)]
    tr = []; atr = None; stop = None; sig = []; atrs = []
    for i, b in enumerate(bs):
        prev = c[i - 1] if i else b['c']
        tr.append(max(b['h'] - b['l'], abs(b['h'] - prev), abs(b['l'] - prev)))
        if i == 9: atr = sum(tr) / 10
        elif i > 9: atr = (atr * 9 + tr[-1]) / 10
        atrs.append(atr); old = stop
        if atr is None: sig.append(0); continue
        p = old if old is not None else 0; x = b['c']
        if x > p and prev > p: stop = max(p, x - atr)
        elif x < p and prev < p: stop = min(p, x + atr)
        else: stop = x - atr if x > p else x + atr
        sig.append(1 if old is not None and x > stop and prev <= old else
                   -1 if old is not None and x < stop and prev >= old else 0)
    return z, sig, atrs


def session_info(bs):
    n = len(bs); end = [i + 1 == n or bs[i + 1]['t'] - bs[i]['t'] != STEP for i in range(n)]
    vwap = [0.0] * n; cpv = cv = 0
    for i, b in enumerate(bs):
        if i == 0 or end[i - 1]: cpv = cv = 0
        tp = (b['h'] + b['l'] + b['c']) / 3; cpv += tp * b['v']; cv += b['v']
        vwap[i] = cpv / cv if cv else b['c']
    closes = []; nsess = [0] * n
    for i in range(n):
        nsess[i] = len(closes)
        if end[i]: closes.append(bs[i]['c'])
    return end, vwap, nsess


def daily_stats(bs, end):
    """لكل شمعة: EMA50 اليومي وقيمة متوسط المدى اليومي (14) من الجلسات المكتملة قبلها فقط."""
    n = len(bs); ema_by = []; adr_by = []; ema = None; rng = []; hi = lo = None; cnt = 0
    for i, b in enumerate(bs):
        hi = b['h'] if hi is None else max(hi, b['h']); lo = b['l'] if lo is None else min(lo, b['l'])
        if end[i]:
            cnt += 1; c = b['c']
            ema = c if ema is None else ema + (c - ema) * 2 / (EMA_D + 1)
            ema_by.append(ema if cnt >= EMA_D else None)
            rng.append(hi - lo); adr_by.append(sum(rng[-ADR_N:]) / ADR_N if len(rng) >= ADR_N else None)
            hi = lo = None
    pe = [None] * n; pa = [None] * n; k = 0
    for i in range(n):
        if k: pe[i] = ema_by[k - 1]; pa[i] = adr_by[k - 1]
        if end[i]: k += 1
    return pe, pa


# ───────── الصفقات ─────────
def trades_for(data, volx=VOLX, late_volx=VOLX_LATE, spy_filter=True, trend='counter', sides=(1, -1),
               dte=DTE_MIN, budget=BUDGET, symbols=None):
    """trend: 'counter' = عكس الترند اليومي (الأساس)، 'with' = مع الترند، 'none' = بدون شرط EMA50.
    sides: 1 = CALL، -1 = PUT."""
    spy = data['SPY']; s_end, s_vwap, s_ns = session_info(spy)
    spy_at = {b['t']: j for j, b in enumerate(spy)}
    out = []
    for sym in (symbols or TRADE_SYMBOLS):
        if sym not in data: continue
        bs = data[sym]; z, sig, atrs = ut_zlsma(bs); end, vwap, ns = session_info(bs); n = len(bs)
        ema_d, adr = daily_stats(bs, end)
        busy = -1
        for i, b in enumerate(bs):
            s = sig[i]
            if s not in sides or i < 210 or i < busy or end[i] or z[i] is None or z[i - 3] is None or i + 1 >= n: continue
            j = spy_at.get(b['t'])
            if j is None or j < 200 or s_ns[j] < 10 or ns[i] < 10: continue      # تهيئة كافية
            if s * (b['c'] - z[i]) <= 0 or s * (b['c'] - vwap[i]) <= 0: continue
            if spy_filter and s * (spy[j]['c'] - s_vwap[j]) <= 0: continue
            if trend != 'none':
                if ema_d[i] is None: continue
                side_of_ema = s * (b['c'] - ema_d[i])     # موجب = مع الترند، سالب = عكس الترند
                if trend == 'counter' and side_of_ema >= 0: continue
                if trend == 'with' and side_of_ema <= 0: continue
            vavg = sum(bs[k]['v'] for k in range(i - 20, i)) / 20
            if b['v'] < volx * vavg: continue
            cny = datetime.fromtimestamp(b['t'] + STEP, timezone.utc).astimezone(NY)
            mins = cny.hour * 60 + cny.minute - 570
            if not (0 <= mins <= LATE_MIN): continue
            if mins > WIN_MIN and b['v'] < late_volx * vavg: continue   # إشارة متأخرة تحتاج حجم أقوى
            a = atrs[i]; e = bs[i + 1]['o']; tg = e + s * TGT * a; st = e - s * STP * a
            k = i + 1; why = 'نهاية الجلسة'
            while True:
                x = bs[k]
                adv, fav = (x['l'], x['h']) if s == 1 else (x['h'], x['l'])
                if s * (x['o'] - st) <= 0: px = x['o']; why = 'وقف'; break
                if s * (x['o'] - tg) >= 0: px = x['o']; why = 'هدف'; break
                if s * (adv - st) <= 0: px = st; why = 'وقف'; break
                if s * (fav - tg) >= 0: px = tg; why = 'هدف'; break
                if end[k] or k + 1 >= n: px = x['c']; break
                k += 1
            busy = k
            pnl = 100 * s * (px - e) / e - COST_PCT
            rets = [math.log(bs[q]['c'] / bs[q - 1]['c']) for q in range(max(1, i - 520), i + 1)]
            rv = math.sqrt(sum(r * r for r in rets) / len(rets)) * math.sqrt(252 * 26)
            iv = max(rv * IV_MULT, 0.12)
            opt = option_estimate(e, px, a, iv, bs[i + 1]['t'], bs[k]['t'], side=s, dte=dte, budget=budget, adr=adr[i])
            cmp_b = {}
            for bud in CMP_BUDGETS:
                o2 = option_estimate(e, px, a, iv, bs[i + 1]['t'], bs[k]['t'], side=s, dte=dte, budget=bud, adr=adr[i])
                cmp_b[f'{bud:.0f}'] = o2['opt_usd']
            o2 = option_estimate(e, px, a, iv, bs[i + 1]['t'], bs[k]['t'], side=s, dte=dte, budget=500.0, adr=adr[i])
            cmp_b['2x500'] = None if o2['opt_usd'] is None else 2 * o2['opt_usd']
            # ضغط شراء/بيع تقديري لليوم السابق من الشموع (ليس دفتر أوامر)
            q = i
            while q > 0 and not end[q - 1]: q -= 1          # بداية جلسة اليوم
            pe = q - 1; ps = pe
            while ps > 0 and not end[ps - 1]: ps -= 1
            up = sum(bs[r]['v'] for r in range(ps, pe + 1) if bs[r]['c'] >= bs[r]['o']); tot = sum(bs[r]['v'] for r in range(ps, pe + 1)) or 1
            out.append(dict(symbol=sym, side='C' if s == 1 else 'P', t_entry=bs[i + 1]['t'], t_exit=bs[k]['t'], entry=round(e, 2),
                            target=round(tg, 2), stop=round(st, 2), exit=round(px, 2), why=why, stock_pct=round(pnl, 3),
                            atr=round(a, 4), rv=round(rv, 4), ema50d=round(ema_d[i], 2) if ema_d[i] else None,
                            adr=round(adr[i], 3) if adr[i] else None, before10=mins < DRTIP_START,
                            prev_buy_vol_pct=round(100 * up / tot, 1), cmp_budgets=cmp_b, **opt))
    out.sort(key=lambda r: r['t_entry'])
    return out


# ───────── تقدير العقد ─────────
def _N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(S, K, T, v):
    if T <= 1e-6: return max(S - K, 0.0)
    d1 = (math.log(S / K) + (RATE + v * v / 2) * T) / (v * math.sqrt(T)); d2 = d1 - v * math.sqrt(T)
    return S * _N(d1) - K * math.exp(-RATE * T) * _N(d2)


def bs_put(S, K, T, v):
    if T <= 1e-6: return max(K - S, 0.0)
    return bs_call(S, K, T, v) - S + K * math.exp(-RATE * T)


def bs_price(side, S, K, T, v):
    return bs_call(S, K, T, v) if side in (1, 'C') else bs_put(S, K, T, v)


def intrinsic(side, S, K):
    return max(S - K, 0.0) if side in (1, 'C') else max(K - S, 0.0)


def expiry_for(t0, dte=DTE_MIN):
    """أقرب جمعة تبعد dte يوم أو أكثر."""
    d0 = datetime.fromtimestamp(t0, NY); tgt = d0.date() + timedelta(days=dte)
    tgt += timedelta(days=(4 - tgt.weekday()) % 7)
    return datetime(tgt.year, tgt.month, tgt.day, 16, 0, tzinfo=NY)


def option_estimate(e, px, atr, iv, t0, t1, side=1, dte=DTE_MIN, budget=BUDGET, adr=None):
    exp = expiry_for(t0, dte); d0 = datetime.fromtimestamp(t0, NY)
    T0 = (exp - d0).total_seconds() / (365 * 86400)
    T1 = max((exp - datetime.fromtimestamp(t1, NY)).total_seconds() / (365 * 86400), 0)
    inc = 1.0 if e < 300 else 5.0
    K = round(e / inc) * inc; step = inc if side == 1 else -inc     # كول يبعد لفوق، بوت يبعد لتحت
    for _ in range(40):
        mid0 = bs_price(side, e, K, T0, iv); ask = mid0 + max(0.01 * mid0, 0.02)
        if ask * 100 + COMM <= budget: break
        K += step
    else:
        return dict(strike=None, premium=None, opt_usd=None, opt_pct=None, expensive=None)
    tv = mid0 - intrinsic(side, e, K)
    mid1 = bs_price(side, px, K, T1, iv); bid = max(mid1 - max(0.01 * mid1, 0.02), 0)
    cost = ask * 100 + COMM; got = bid * 100 - COMM
    return dict(strike=K, expiry=exp.strftime('%Y-%m-%d'), premium=round(ask * 100, 0), time_value=round(tv, 2),
                expensive=bool(adr and tv > TV_MAX_ADR * adr),
                opt_usd=round(got - cost, 0), opt_pct=round(100 * (got - cost) / cost, 1))


# ───────── التقارير ─────────
def summary(ts):
    ts = [t for t in ts if not t.get('expensive')]          # العقد الغالي يتجاهل
    o = [t for t in ts if t['opt_usd'] is not None]
    return dict(n=len(ts), stock_win=round(100 * sum(t['stock_pct'] > 0 for t in ts) / len(ts), 1) if ts else 0,
                stock_total=round(sum(t['stock_pct'] for t in ts), 2),
                opt_n=len(o), opt_win=round(100 * sum(t['opt_usd'] > 0 for t in o) / len(o), 1) if o else 0,
                opt_usd=round(sum(t['opt_usd'] for t in o), 0), opt_avg_pct=round(sum(t['opt_pct'] for t in o) / len(o), 1) if o else 0)


def week_bounds(day):
    mon = day - timedelta(days=day.weekday())
    lo = int(datetime(mon.year, mon.month, mon.day, tzinfo=NY).timestamp())
    return mon, lo, lo + 7 * 86400


def fmt_week(mon, ts, last_t):
    s = summary(ts); L = [f'## أسبوع {mon} إلى {mon + timedelta(days=4)}']
    if not ts:
        L.append('ما فيه صفقات هذا الأسبوع (ما اكتملت الشروط).')
    else:
        v = 'نجح لأن الربح' if s['opt_usd'] > 0 else 'فشل لأن الخسارة'
        L.append(f"**العقود: {v} {s['opt_usd']:+.0f}$** (عقد واحد لكل صفقة، {s['opt_n']} صفقة، نجح منها {round(s['opt_n'] * s['opt_win'] / 100)}). "
                 f"حركة السهم: {s['stock_total']:+.2f}%.")
        L += ['', '| السهم | الدخول (الرياض) | سعر الدخول | الهدف | الوقف | الخروج | العقد | سعره | ربح العقد |',
              '|---|---|---:|---:|---:|---|---|---:|---:|']
        for t in ts:
            tm = datetime.fromtimestamp(t['t_entry'], RY).strftime('%a %d/%m %H:%M')
            c = f"{t['strike']:g}{t.get('side', 'C')} {t['expiry'][5:]}" if t['strike'] else 'أغلى من الميزانية'
            if t.get('expensive'): c += ' (غالي، متجاهل)'
            L.append(f"|{t['symbol']}|{tm}|{t['entry']}|{t['target']}|{t['stop']}|{t['exit']} ({t['why']})|{c}|"
                     f"{'' if t['premium'] is None else '%.0f$' % t['premium']}|"
                     f"{'' if t['opt_usd'] is None else '%+.0f$ (%+.0f%%)' % (t['opt_usd'], t['opt_pct'])}|")
    L += ['', f"_تقدير عقود بنموذج تسعير، مو أسعار حقيقية. ميزانية العقد ≤ {BUDGET:.0f}$. البيانات حتى "
          f"{datetime.fromtimestamp(last_t, RY):%Y-%m-%d %H:%M} الرياض._"]
    return '\n'.join(L), s


# ═════════ المحاكاة الحية (المرحلة 1) ═════════
# الحالة في ملف paper.json: {"briefs": {يوم: {...}}, "trades": [...], "days": {يوم: ملاحظة}}
def implied_vol(price, S, K, T, side='C'):
    lo, hi = 0.01, 5.0
    if price <= intrinsic(side, S, K) + 1e-4 or T <= 1e-6: return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(side, S, K, T, mid) > price: hi = mid
        else: lo = mid
    return (lo + hi) / 2


def yearfrac(t_from, exp_date):
    exp = datetime.fromisoformat(exp_date).replace(hour=16, minute=0, tzinfo=NY)
    return max((exp - datetime.fromtimestamp(t_from, NY)).total_seconds() / (365 * 86400), 0.0)


def load_state(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else dict(briefs={}, trades=[], days={})


def day_trades(data, day):
    lo = int(datetime(day.year, day.month, day.day, tzinfo=NY).timestamp()); hi = lo + 86400
    return [t for t in trades_for(data) if lo <= t['t_entry'] < hi]


def cmd_plan(a, data):
    """يطبع صفقات اليوم والعقد المطلوب تسعيره لكل وحدة (للخطوة التالية: جلب أسعار أبيان)."""
    day = date.fromisoformat(a.day); ts = day_trades(data, day)
    out = []
    for n, t in enumerate(ts):
        min_exp = (datetime.fromtimestamp(t['t_entry'], NY).date() + timedelta(days=DTE_MIN)).isoformat()
        u = lambda x: datetime.fromtimestamp(x, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        rule = ('أول جمعة انتهاء >= min_expiry، ثم ' +
                ('أصغر سترايك >= سعر الدخول، وإذا fmv×100 > 995 انتقل للسترايك الأعلى' if t['side'] == 'C' else
                 'أكبر سترايك <= سعر الدخول، وإذا fmv×100 > 995 انتقل للسترايك الأقل') +
                '. سجّل opt_day_vol (حجم العقد اليومي) لفلتر السيولة، وema200_ok (السعر مع EMA200 على 1د و5د) لمقارنة Dr Tip، وexit50_usd لو انضرب ±50% قبل الخروج')
        out.append(dict(id=f"{a.day}-{t['symbol']}-{n}", symbol=t['symbol'], side=t['side'], entry=t['entry'], target=t['target'], stop=t['stop'],
                        exit=t['exit'], why=t['why'], min_expiry=min_exp, adr=t['adr'], before10=t['before10'],
                        rule=rule,
                        entry_window=[u(t['t_entry']), u(t['t_entry'] + 240)],
                        exit_window=[u(t['t_exit']), u(t['t_exit'] + 840)]))
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_close_day(a, data):
    """يسجل صفقات اليوم بأسعار أبيان الحقيقية (عند الإغلاق) ويحسب ربح العقد المعايَر."""
    day = date.fromisoformat(a.day); ts = day_trades(data, day)
    quotes = {q['id']: q for q in json.loads(Path(a.quotes).read_text())} if a.quotes else {}
    st = load_state(a.state); brief = st['briefs'].get(a.day, {})
    flags = brief.get('flags', {}); caution = brief.get('market_caution', False)
    st['trades'] = [x for x in st['trades'] if x.get('day') != a.day]
    for n, t in enumerate(ts):
        tid = f"{a.day}-{t['symbol']}-{n}"; q = quotes.get(tid)
        rec = dict(id=tid, day=a.day, **{k: t[k] for k in ['symbol', 'side', 't_entry', 't_exit', 'entry', 'target', 'stop', 'exit', 'why',
                                                           'stock_pct', 'prev_buy_vol_pct', 'ema50d', 'adr', 'before10']},
                   news_skip=bool(flags.get(t['symbol'], {}).get('skip')), news_reason=flags.get(t['symbol'], {}).get('reason', ''),
                   market_caution=caution, model_opt_usd=t['opt_usd'])
        vix = brief.get('vix_chg_pct')     # تغيّر VIX عن إغلاق أمس (%) من دراسة ما قبل الافتتاح
        rec['vix_skip'] = None if vix is None else (vix >= VIX_SPIKE if t['side'] == 'C' else vix <= -VIX_SPIKE)
        if q and q.get('opt_entry') and q.get('opt_exit') is not None:
            apply_real(rec, q)
        elif q and q.get('fmv') and q.get('stock_close'):
            K = float(q['strike']); exp = q['expiry']
            t_close = int(datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY).timestamp())
            iv = implied_vol(float(q['fmv']), float(q['stock_close']), K, yearfrac(t_close, exp), t['side'])
            if iv:
                m0 = bs_price(t['side'], t['entry'], K, yearfrac(t['t_entry'], exp), iv); ask = m0 + max(0.01 * m0, 0.02)
                if t['why'] == 'نهاية الجلسة':
                    m1 = float(q['fmv']); src = 'سعر إغلاق أبيان'
                else:
                    m1 = bs_price(t['side'], t['exit'], K, yearfrac(t['t_exit'], exp), iv); src = 'نموذج بتذبذب أبيان الفعلي'
                bid = max(m1 - max(0.01 * m1, 0.02), 0)
                cost = ask * 100 + COMM; got = bid * 100 - COMM
                rec.update(strike=K, expiry=exp, iv=round(iv, 3), premium=round(ask * 100), close_fmv=float(q['fmv']),
                           opt_usd=round(got - cost), opt_pct=round(100 * (got - cost) / cost, 1), exit_price_source=src,
                           over_budget=ask * 100 + COMM > BUDGET)
        if 'opt_usd' not in rec:
            rec.update(strike=None, expiry=None, opt_usd=None, opt_pct=None, exit_price_source='ما توفر سعر أبيان')
        if q:
            if q.get('ema200_ok') is not None: rec['ema200_ok'] = bool(q['ema200_ok'])
            if q.get('exit50_usd') is not None: rec['exit50_usd'] = q['exit50_usd']
        apply_filters(rec)
        st['trades'].append(rec)
    st['days'][a.day] = dict(n=len(ts), updated=datetime.now(RY).strftime('%Y-%m-%d %H:%M'))
    st['trades'].sort(key=lambda x: x['t_entry'])
    Path(a.state).write_text(json.dumps(st, ensure_ascii=False, indent=1))
    print(json.dumps([{k: r[k] for k in ['id', 'symbol', 'why', 'stock_pct', 'opt_usd', 'news_skip']} for r in st['trades'] if r['day'] == a.day], ensure_ascii=False))


def apply_filters(rec):
    """فلاتر أساسية (تتجاهل الصفقة): عقد غالي، سيولة ضعيفة. وعلامات مقارنة فقط: Dr Tip."""
    skip = []
    if rec.get('premium') is not None and rec.get('strike') is not None and rec.get('adr'):
        tv = rec['premium'] / 100 - intrinsic(rec.get('side', 'C'), rec['entry'], rec['strike'])
        rec['time_value'] = round(tv, 2)
        if tv > TV_MAX_ADR * rec['adr']: skip.append('عقد غالي')
    if rec.get('opt_day_vol') is not None and float(rec['opt_day_vol']) < MIN_OPT_VOL: skip.append('سيولة ضعيفة')
    rec['skip'] = '، '.join(skip)
    rec['drtip_skip'] = bool(rec.get('before10')) or rec.get('ema200_ok') is False


def _pnl(e, x):
    cost = e * 100 + COMM; got = x * 100 - COMM
    return round(got - cost), round(100 * (got - cost) / cost, 1), cost


def apply_real(rec, q):
    """سعر العقد الحقيقي من شموع العقد نفسه (TradingView OPRA): لحظة الدخول ولحظة الخروج.
    أسوأ حالة: شراء بأعلى سعر في دقيقة/شمعة الدخول وبيع بأقل سعر في دقيقة/شمعة الخروج (بديل عن فرق العرض والطلب).
    alt14 (قديم): عقد 14 يوم+ للمقارنة. alts: نفس الصفقة بمدد عقود أخرى {"7":{...},"21":{...}} وbucket = مدة العقد الأساسي."""
    e, x = float(q['opt_entry']), float(q['opt_exit'])
    usd, pct, cost = _pnl(e, x)
    rec.update(strike=float(q['strike']), expiry=q['expiry'], premium=round(e * 100), exit_premium=round(x * 100),
               opt_usd=usd, opt_pct=pct, exit_price_source='سعر العقد الحقيقي (TradingView)', over_budget=cost > BUDGET)
    if q.get('opt_entry_hi') and q.get('opt_exit_lo') is not None:
        rec['opt_usd_worst'] = _pnl(float(q['opt_entry_hi']), float(q['opt_exit_lo']))[0]
    for k in ('fmv', 'opt_exit_t', 'opt_symbol', 'opt_day_vol'):
        if q.get(k) is not None: rec[{'fmv': 'close_fmv'}.get(k, k)] = q[k]
    a = q.get('alt14')
    if a and a.get('opt_entry') and a.get('opt_exit') is not None:
        au, ap, ac = _pnl(float(a['opt_entry']), float(a['opt_exit']))
        rec['alt14'] = dict(a, premium=round(float(a['opt_entry']) * 100), exit_premium=round(float(a['opt_exit']) * 100),
                            opt_usd=au, opt_pct=ap, over_budget=ac > BUDGET)
        if a.get('opt_entry_hi') and a.get('opt_exit_lo') is not None:
            rec['alt14']['opt_usd_worst'] = _pnl(float(a['opt_entry_hi']), float(a['opt_exit_lo']))[0]
    if q.get('bucket') is not None: rec['bucket'] = str(q['bucket'])
    for lab, a in (q.get('alts') or {}).items():
        if a and a.get('opt_entry') and a.get('opt_exit') is not None:
            au, ap, ac = _pnl(float(a['opt_entry']), float(a['opt_exit']))
            r = dict(a, premium=round(float(a['opt_entry']) * 100), exit_premium=round(float(a['opt_exit']) * 100),
                     opt_usd=au, opt_pct=ap, over_budget=ac > BUDGET)
            if a.get('opt_entry_hi') and a.get('opt_exit_lo') is not None:
                r['opt_usd_worst'] = _pnl(float(a['opt_entry_hi']), float(a['opt_exit_lo']))[0]
            rec.setdefault('alts', {})[str(lab)] = r


def buckets_of(t):
    """نتيجة نفس الصفقة بكل مدة عقد متوفرة: {المدة بالأيام: (ربح, أسوأ حالة)}.
    الصفقات القديمة (قبل 12 أكتوبر): العقد الأساسي أسبوعي = 7، و alt14 = 14."""
    out = {}
    if t.get('opt_usd') is None: return out
    w = lambda d: d.get('opt_usd_worst', d['opt_usd'])
    if t.get('alts') or t.get('bucket'):
        out[t.get('bucket', '14')] = (t['opt_usd'], w(t))
        for lab, a in (t.get('alts') or {}).items(): out[lab] = (a['opt_usd'], w(a))
    else:
        out['7'] = (t['opt_usd'], w(t))
        if t.get('alt14'): out['14'] = (t['alt14']['opt_usd'], w(t['alt14']))
    return out


def bucket_table(ts, title):
    agg = {}
    for t in ts:
        for lab, (u, wv) in buckets_of(t).items():
            g = agg.setdefault(lab, [0, 0, 0, 0]); g[0] += 1; g[1] += u; g[2] += wv; g[3] += u > 0
    if not agg: return []
    best = max(agg, key=lambda k: agg[k][1])
    L = ['', f'**{title}**', '| مدة العقد | الصفقات | نجح | الربح | أسوأ حالة |', '|---|---:|---:|---:|---:|']
    for lab in sorted(agg, key=lambda k: int(k)):
        n, u, wv, wn = agg[lab]
        L.append(f"|{lab} يوم{' ⭐' if lab == best else ''}|{n}|{wn}|{u:+.0f}$|{wv:+.0f}$|")
    return L


def cmd_reprice(a, _data):
    """يحدّث صفقات مسجلة بأسعار العقد الحقيقية: --quotes ملف [{id, strike, expiry, opt_entry, opt_exit, ...}]."""
    st = load_state(a.state); qs = {q['id']: q for q in json.loads(Path(a.quotes).read_text())}
    for r in st['trades']:
        q = qs.get(r['id'])
        if q and q.get('opt_entry') and q.get('opt_exit') is not None:
            q.setdefault('strike', r.get('strike')); q.setdefault('expiry', r.get('expiry'))
            for k in ('opt_usd_worst', 'alt14'): r.pop(k, None)
            r.setdefault('model_opt_usd', r.get('opt_usd')); apply_real(r, q); apply_filters(r)
            print(r['id'], r['premium'], '->', r['exit_premium'], f"{r['opt_usd']:+}$")
    Path(a.state).write_text(json.dumps(st, ensure_ascii=False, indent=1))


def cmd_brief(a, _data):
    st = load_state(a.state); b = json.loads(Path(a.brief).read_text())
    st['briefs'][b['day']] = b; Path(a.state).write_text(json.dumps(st, ensure_ascii=False, indent=1)); print('brief saved', b['day'])


def compare_lines(priced):
    """مقارنات فقط، ما تغيّر نتيجة البوت الأساسية."""
    if not priced: return []
    L = ['', '**مقارنات (للمعلومة فقط):**']
    def cost(t): return (t.get('premium') or 0) + COMM
    for bud in CMP_BUDGETS:
        ok = [t for t in priced if cost(t) <= bud]
        L.append(f"- ميزانية {bud:.0f}$: {sum(t['opt_usd'] for t in ok):+.0f}$ على {len(ok)} صفقة")
    ok = [t for t in priced if cost(t) <= 500]
    L.append(f"- عقدين × 500$: {2 * sum(t['opt_usd'] for t in ok):+.0f}$ على {len(ok)} صفقة")
    v = [t for t in priced if t.get('vix_skip') is not None]
    if v: L.append(f"- مع فلتر VIX: {sum(t['opt_usd'] for t in v if not t['vix_skip']):+.0f}$ على {sum(not t['vix_skip'] for t in v)} صفقة")
    d = [t for t in priced if not t.get('drtip_skip')]
    L.append(f"- مع فلاتر Dr Tip: {sum(t['opt_usd'] for t in d):+.0f}$ على {len(d)} صفقة")
    e = [t for t in priced if t.get('exit50_usd') is not None]
    if e: L.append(f"- خروج ±50% من العقد: {sum(t['exit50_usd'] for t in e):+.0f}$ على {len(e)} صفقة (الحالي {sum(t['opt_usd'] for t in e):+.0f}$)")
    return L


def cmd_paper_week(a, _data):
    st = load_state(a.state)
    day = date.fromisoformat(a.week) if a.week else datetime.now(NY).date()
    mon = day - timedelta(days=day.weekday()); fri = mon + timedelta(days=4)
    ts = [t for t in st['trades'] if str(mon) <= t['day'] <= str(fri)]
    priced = [t for t in ts if t.get('opt_usd') is not None and not t.get('skip')]
    skipped = [t for t in ts if t.get('skip')]
    tot = sum(t['opt_usd'] for t in priced); wins = sum(t['opt_usd'] > 0 for t in priced)
    L = [f'## المحاكاة الحية — أسبوع {mon} إلى {fri}']
    if not ts:
        L.append('ما فيه صفقات هذا الأسبوع (ما اكتملت الشروط).')
    else:
        v = 'نجح لأن الربح' if tot > 0 else 'فشل لأن الخسارة'
        nreal = sum('TradingView' in t.get('exit_price_source', '') for t in priced)
        L.append(f"**العقود: {v} {tot:+.0f}$** — {len(priced)} صفقة ({nreal} منها بسعر العقد الحقيقي دخول وخروج)، نجح منها {wins}.")
        wr = [t for t in priced if t.get('opt_usd_worst') is not None]
        if wr:
            L.append(f"أسوأ حالة (شراء بأعلى سعر وبيع بأقل سعر في دقيقة الدخول والخروج): {sum(t['opt_usd_worst'] for t in wr):+.0f}$ على {len(wr)} صفقة.")
        ob = [t['symbol'] for t in priced if t.get('over_budget')]
        if ob: L.append(f"تنبيه: عقود فوق الميزانية وقت الدخول: {', '.join(ob)}.")
        nf = [t for t in priced if not t['news_skip']]
        if len(nf) != len(priced):
            L.append(f"لو طبّقنا فلتر الأخبار (تجاهل الأسهم المعلَّمة): {sum(t['opt_usd'] for t in nf):+.0f}$ على {len(nf)} صفقة.")
        if skipped:
            L.append('متجاهلة: ' + '، '.join(f"{t['symbol']} ({t['skip']}"
                                              + (f"، كانت {t['opt_usd']:+.0f}$" if t.get('opt_usd') is not None else '') + ')' for t in skipped) + '.')
        L += compare_lines(priced)
        L += bucket_table(priced, 'مقارنة مدد العقود (نفس الصفقات) — هذا الأسبوع')
        L += ['', '| السهم | اليوم | الدخول | الخروج | العقد | العقد وقت الدخول | العقد وقت الخروج | ربح العقد | أسوأ حالة | باقي المدد | تنبيه الأخبار |',
              '|---|---|---:|---|---|---:|---:|---:|---:|---|---|']
        for t in ts:
            c = f"{t['strike']:g}{t.get('side', 'C')} {t['expiry'][5:]}" if t.get('strike') else '—'
            o = f"{t['opt_usd']:+.0f}$ ({t['opt_pct']:+.0f}%)" if t.get('opt_usd') is not None else 'بدون سعر'
            pe = f"{t['premium']:.0f}$" if t.get('premium') is not None else '—'
            px = f"{t['exit_premium']:.0f}$" if t.get('exit_premium') is not None else '(تقدير)'
            w = f"{t['opt_usd_worst']:+.0f}$" if t.get('opt_usd_worst') is not None else '—'
            bo = buckets_of(t); mainlab = t.get('bucket', '14') if (t.get('alts') or t.get('bucket')) else '7'
            al14 = ' / '.join(f"{k}ي: {bo[k][0]:+.0f}$" for k in sorted(bo, key=int) if k != mainlab) or '—'
            L.append(f"|{t['symbol']}|{t['day'][5:]}|{t['entry']}|{t['exit']} ({t['why']})|{c}|{pe}|{px}|{o}|{w}|{al14}|{t['news_reason'] or '—'}|")
    allp = [t for t in st['trades'] if t.get('opt_usd') is not None and not t.get('skip')]
    if allp:
        L += ['', f"منذ بداية المحاكاة: {sum(t['opt_usd'] for t in allp):+.0f}$ على {len(allp)} صفقة، نجح {sum(t['opt_usd'] > 0 for t in allp)}."]
        L += bucket_table(allp, 'مقارنة مدد العقود منذ البداية (⭐ = الأعلى ربح)')
    print('\n'.join(L))


SUB = {'plan': cmd_plan, 'close-day': cmd_close_day, 'reprice': cmd_reprice, 'brief': cmd_brief, 'paper-week': cmd_paper_week}


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in SUB:
        cmd = sys.argv.pop(1)
        ap = argparse.ArgumentParser()
        ap.add_argument('--data', default='.'); ap.add_argument('--state', default='paper.json')
        ap.add_argument('--day'); ap.add_argument('--quotes'); ap.add_argument('--brief'); ap.add_argument('--week')
        a = ap.parse_args(); D = Path(a.data)
        data = None
        if cmd in ('plan', 'close-day'):
            data = {s: json.loads((D / f'{s}-15m.json').read_text())['bars'] for s in SYMBOLS if (D / f'{s}-15m.json').exists()}
        SUB[cmd](a, data); return
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='.'); ap.add_argument('--week'); ap.add_argument('--all', action='store_true')
    ap.add_argument('--history'); ap.add_argument('--source', default='أسبوعي'); ap.add_argument('--seed', action='store_true')
    a = ap.parse_args(); D = Path(a.data)
    data = {s: json.loads((D / f'{s}-15m.json').read_text())['bars'] for s in SYMBOLS if (D / f'{s}-15m.json').exists()}
    for s, bs in data.items():
        assert all(bs[i]['t'] < bs[i + 1]['t'] for i in range(len(bs) - 1)), s
    ts = trades_for(data); last_t = max(bs[-1]['t'] for bs in data.values())
    if a.all:
        print(json.dumps(summary(ts), ensure_ascii=False)); return
    if a.seed and a.history:   # كل الأسابيع في البيانات كباك تيست
        h = []; mons = sorted({week_bounds(datetime.fromtimestamp(t['t_entry'], NY).date())[0] for t in ts})
        for mon in mons:
            _, lo, hi = week_bounds(mon); wk = [t for t in ts if lo <= t['t_entry'] < hi]
            h.append(dict(week=str(mon), source=a.source, **summary(wk), trades=wk))
        Path(a.history).write_text(json.dumps(h, ensure_ascii=False, indent=1)); print('weeks', len(h)); return
    day = date.fromisoformat(a.week) if a.week else datetime.fromtimestamp(last_t, NY).date()
    mon, lo, hi = week_bounds(day)
    wk = [t for t in ts if lo <= t['t_entry'] < hi]
    text, s = fmt_week(mon, wk, last_t)
    print(text)
    if a.history:
        p = Path(a.history); h = json.loads(p.read_text()) if p.exists() else []
        h = [x for x in h if x['week'] != str(mon)] + [dict(week=str(mon), source=a.source, **s, trades=wk)]
        h.sort(key=lambda x: x['week']); p.write_text(json.dumps(h, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
