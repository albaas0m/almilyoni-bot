"""المليوني اللحظي (قرار 14 نوفمبر 2026): البوت يشتغل وقت السوق على GitHub بنفسه، ويجيب سعر العقد من أبيان مباشرة.

كل 15 دقيقة (بعد إغلاق الشمعة): يحدّث شموع الأسهم ويطلع إشارات الاستراتيجية نفسها (almilyoni_weekly.trades_for).
إشارة جديدة → يختار العقد ويسجل سعره من أبيان لحظة الدخول (fmv).
كل دقيقة: يشيك سعر السهم من أبيان على الهدف/الوقف، ولما يلمس (أو نهاية الجلسة) يسجل سعر العقد من أبيان لحظة الخروج.
كل شي ينحفظ في live.json، وتسجيل آخر اليوم (cmd_close_day) يقدّم أسعار أبيان اللحظية على أي مصدر ثاني.

  python3 live_abyan.py            # يشتغل لين نهاية الجلسة أو حد وقت الجوب (5:50 ساعة)
  python3 live_abyan.py --once     # جولة وحدة (اختبار)
"""
import os, json, math, time, argparse, subprocess, traceback, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
import almilyoni_weekly as m
import fetch_real as f
import abyan
import tv

ROOT = Path(__file__).resolve().parent; LIVE = ROOT / 'live.json'; NY = m.NY; UTC = timezone.utc
MAX_STRIKES, JOB_SEC = 15, 5 * 3600 + 50 * 60
T0 = time.time()


# (20 نوفمبر، بعد مراجعة Gemini) عطل بورصة نيويورك وأيام الإغلاق المبكر (1:00 الظهر نيويورك)
NYSE_CLOSED = {'2026-11-26', '2026-12-25', '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31',
               '2027-06-18', '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24'}
NYSE_EARLY = {'2026-11-27', '2026-12-24', '2027-11-26'}


def closed_day(d): return d.weekday() >= 5 or d.date().isoformat() in NYSE_CLOSED


def close_min(d): return 210 if d.date().isoformat() in NYSE_EARLY else 390   # دقايق من 9:30 لين الإغلاق


def log(*a): print(datetime.now(NY).strftime('%H:%M:%S'), *a, flush=True)


def load():
    return json.loads(LIVE.read_text()) if LIVE.exists() else {'trades': []}


def save(st, msg, data=False):
    LIVE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    # (20 نوفمبر) كان git pull --rebase يفشل بسبب تعديلات data/ غير المحفوظة فما ينرفع شي أبداً: --autostash + إعادة محاولة + نسجل الفشل
    cmd = ('(git add tv_quota.json 2>/dev/null; true) && git add live.json abyan_token.enc tv_token.enc' + (' data' if data else '') + ' && (git diff --cached --quiet || git commit -qm "' + msg + '") && '
           'for i in 1 2 3; do git pull -q --rebase --autostash origin main && git push -q && exit 0; sleep 5; done; exit 1')
    try:   # نحفظ في الريبو على طول (عشان لو الجوب انقطع ما يضيع شي)
        r = subprocess.run(cmd, shell=True, cwd=ROOT, timeout=180, capture_output=True, text=True, executable='/bin/bash')
        if r.returncode: log('⚠️ فشل رفع الحفظ للريبو:', (r.stdout + r.stderr)[-300:])
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


import contract_select as cs
TV_LIQ_ON = (ROOT / 'tv_liquidity_on').exists()      # بيانات السيولة من TradingView (طلب واحد لكل دخول): مطفية افتراضياً (حد الطلبات)


def tv_liquidity(sym, side, cands, now, st):
    """حجم/OI/سبريد من TradingView (get_symbol_data_batch، طلب واحد). ما تشتغل إلا بملف tv_liquidity_on، وتفشل بهدوء (None) بدون ما تأكل الحد.
    (24 نوفمبر) أبيان ما يعطي إلا fmv، فهذا المصدر الوحيد للسيولة؛ شكل رد الأداة ما اتجرب لسا (الحد خلص) فالقراءة متساهلة."""
    if not TV_LIQ_ON or not cands: return {}
    try:
        tv_count(tv_budget(now, st))
        syms = {opra(sym, c['exp'], side, c['K']): c for c in cands[:50]}
        r = tv.call('mcp-tv-get-symbol-data-batch', dict(symbols=list(syms), columns=['volume', 'bid', 'ask', 'open_interest']))
        rows = next((r[k] for k in ('symbols', 'data', 'results', 'rows') if isinstance(r, dict) and isinstance(r.get(k), list)), r if isinstance(r, list) else [])
        for row in rows:
            c = syms.get(row.get('symbol') or row.get('s') or row.get('name'))
            if c:
                for k, src in (('vol', 'volume'), ('oi', 'open_interest'), ('bid', 'bid'), ('ask', 'ask')):
                    if row.get(src) is not None: c[k] = float(row[src])
                if c.get('bid') and c.get('ask') and c['ask'] >= c['bid']: c['spread'] = (c['ask'] - c['bid']) / ((c['ask'] + c['bid']) / 2)
    except (Exception, SystemExit) as e: log('⚠️ بيانات السيولة من TradingView ما انجابت (أكمل بدونها):', repr(e)[:160])
    return {}


def gather_live(sym, side, S, now, n_exp=3, per_exp=6):
    """كل المرشحين من سلسلة أبيان لأقرب 3 تواريخ: السترايكات الموجودة فعلاً، نختار اللي دلتاها المتوقعة (من تذبذب أقرب سترايك) بين 0.2 و0.62،
    ونسعّر كل واحد من أبيان (fmv). بدون TradingView."""
    typ = {'C': 'call', 'P': 'put'}[side]; out = []; d0 = datetime.fromtimestamp(now, NY).date()
    for exp in expiries(sym, d0)[:n_exp]:
        ks = sorted({float(c['strike_price']) for c in abyan.call('browse_options_chain', dict(symbol=sym, expiration_date=exp.isoformat(), type=typ, limit=250)).get('contracts', [])})
        if not ks: continue
        atm = min(ks, key=lambda k: abs(k - S)); p0 = opt_px(sym, exp, side, atm); c0 = cs.make_cand(exp, side, atm, p0, S, now) if p0 else None
        iv0 = c0['iv'] if c0 else 0.45; T = m.yearfrac(now, exp.isoformat())
        near = sorted((k for k in ks if 0.2 <= abs(cs.bs_delta(side, S, k, T, iv0)) <= 0.62 and k != atm), key=lambda k: abs(abs(cs.bs_delta(side, S, k, T, iv0)) - 0.42))[:per_exp]
        if c0: out.append(c0)
        for k in near:
            p = opt_px(sym, exp, side, k); c = cs.make_cand(exp, side, k, p, S, now) if p else None
            if c: out.append(c)
    return out


def choose_contract(sym, side, S, a, now, st=None):
    """(24 نوفمبر) المفاضلة الجديدة (contract_select) بدل أول عقد في الميزانية. يرجع sel (فيه c + حقول التسجيل) أو None."""
    cands = gather_live(sym, side, S, now)
    if st is not None: tv_liquidity(sym, side, cands, now, st)
    sel, why = cs.select(cands, side, S, a)
    if not sel: log('ما لقيت عقد:', sym, side, why, f'({len(cands)} مرشح)')
    return sel


# ───────── الإشارات من تنبيهات TradingView (21 نوفمبر) ─────────
# الاستراتيجية تنحسب على سيرفرات TradingView (pine/almilyoni_alerts.pine) والتنبيه يرسل الإشارة لـ ntfy.sh،
# (24 نوفمبر) ntfy رجّع HTTP 429 على تنبيه AVGO 15:00Z (عناوين TradingView مشتركة وntfy يحدد على الـIP) فضاعت الصفقة.
# الحل الجذري: سجل التنبيهات في حساب TradingView (mcp-tv-get-alerts-log) هو المصدر الأساسي — يسجل كل تنبيه حتى لو الـwebhook فشل —
# وntfy مصدر سريع إضافي (كل دقيقة). الاثنين يمرون على نفس الفك والتحقق، ومنع التكرار بمفتاح (sym,t) وfire_id محفوظ في st['seen'].
# يشتغل بملف webhook_on؛ ntfy يحتاج كمان سر NTFY_TOPIC.
NTFY_TOPIC = os.environ.get('NTFY_TOPIC', ''); NTFY_KEY = os.environ.get('NTFY_KEY', '')
SIGNAL_ALERTS = (ROOT / 'webhook_on').exists()
WEBHOOK = bool(NTFY_TOPIC) and SIGNAL_ALERTS
TVLOG_TOOL, TVLOG_DELAY, STALE_SEC, RETRO_MAX_CALLS = 'mcp-tv-get-alerts-log', 35, 240, 8
if SIGNAL_ALERTS:
    # (21 نوفمبر، طلب أحمد) الـ8 أسهم كلها. (24 نوفمبر) وSYMBOLS معها: التسجيل الليلي يحمّل شموع الـ8، وإلا AVGO/LRCX/TSM/ARM ما تتسجل أبداً
    m.TRADE_SYMBOLS = list(m.ALL_TRADE_SYMBOLS); m.SYMBOLS = m.TRADE_SYMBOLS + ['SPY']


def parse_alert(x, src, now=None):
    """يفك رسالة التنبيه {k,sym,side,t,c,atr} (نفس المنطق لـ ntfy وسجل TradingView) ويتحقق من NTFY_KEY.
    يرجع dict(symbol, side, t, atr, c) أو None (ويكتب السبب — ما نسكت)."""
    if not isinstance(x, dict): log(f'⚠️ [{src}] تنبيه مو مفهوم:', str(x)[:120]); return None
    if NTFY_KEY and x.get('k') != NTFY_KEY: log(f'⚠️ [{src}] تنبيه برمز غلط، تجاهلته'); return None
    sym = str(x.get('sym', '')).upper().split(':')[-1]; side = x.get('side')
    try: side = {1: 'C', -1: 'P'}.get(side, side)                 # alertcondition يرسل 1/-1 (1.0 نفس 1 في القاموس)
    except TypeError: pass
    if sym not in m.TRADE_SYMBOLS or side not in ('C', 'P'): return None
    try: t = float(x['t']); atr = float(x['atr'])
    except (KeyError, TypeError, ValueError): log(f'⚠️ [{src}] تنبيه ناقص t/atr:', str(x)[:120]); return None
    if t > 1e11: t /= 1000                                         # لو جاء بالملي ثانية
    if now is not None and t - now > 60: log(f'⚠️ [{src}] تنبيه من المستقبل ({t - now:.0f} ثانية)، تجاهلته:', sym); return None
    return dict(symbol=sym, side=side, t=int(t), atr=atr, c=x.get('c'))


def sig_key(sig): return f"{sig['symbol']}-{sig['t']}"


def is_seen(st, sig, fire_id=None):
    sn = st.setdefault('seen', {})
    return sig_key(sig) in sn or (fire_id is not None and f'fire:{fire_id}' in sn)


def mark_seen(st, now, sig, fire_id=None):
    sn = st.setdefault('seen', {}); sn[sig_key(sig)] = int(now)
    if fire_id is not None: sn[f'fire:{fire_id}'] = int(now)
    for k in [k for k, v in sn.items() if now - v > 3 * 86400]: del sn[k]


def build_signal(sig, now):
    """إشارة حيّة: ندخل بسعر أبيان الحين (نفس منطق الاستراتيجية: افتتاح الشمعة التالية)."""
    sgn = 1 if sig['side'] == 'C' else -1; a = sig['atr']; e = stock_px(sig['symbol'])
    return dict(symbol=sig['symbol'], side=sig['side'], t_entry=sig['t'], entry=round(e, 2), atr=a,
                target=round(e + sgn * m.TGT * a, 2), stop=round(e - sgn * m.STP * a, 2))


def webhook_signals(now, st):
    w = st.setdefault('ntfy', {}); since = w.get('last_id') or '1h'
    url = f'https://ntfy.sh/{urllib.parse.quote(NTFY_TOPIC)}/json?poll=1&since={urllib.parse.quote(str(since))}'
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'almilyoni-bot/1'}), timeout=20) as r:
        lines = r.read().decode().splitlines()
    out = []; batch = set()
    for ln in lines:
        try: ev = json.loads(ln)
        except ValueError: continue
        if ev.get('event') != 'message': continue
        try: x = json.loads(ev.get('message', ''))
        except ValueError: log('⚠️ تنبيه مو مفهوم:', ev.get('message', '')[:120]); w['last_id'] = ev['id']; continue
        sig = parse_alert(x, 'ntfy', now)
        if not sig or is_seen(st, sig) or sig_key(sig) in batch: w['last_id'] = ev['id']; continue      # seen = وصل من السجل قبل
        if now - sig['t'] > STALE_SEC: log('تنبيه قديم، تجاهلته (السجل/الأثر الرجعي يغطيه):', sig['symbol'], sig['side']); w['last_id'] = ev['id']; continue
        try: t = build_signal(sig, now)
        except Exception as e:                                 # فشل أبيان: نوقف عند هالإشارة ونعيد الدورة الجاية (last_id ما تقدم)، واللي قبلها ما يضيع
            log('⚠️ فشل سعر أبيان لإشارة ntfy، أعيد الدورة الجاية:', sig['symbol'], repr(e)[:150]); break
        batch.add(sig_key(sig)); w['last_id'] = ev['id']; t['sig'] = sig
        log('📩 إشارة من ntfy:', t['symbol'], t['side'], 'سعر أبيان', t['entry'])
        out.append(t)
    return out


# ───── سجل التنبيهات في TradingView (المصدر الأساسي) ─────
def tv_budget(now, st, need=1):
    """عداد tv_calls والميزانية اليومية (مشتركة مع new_signals): يرفع QuotaError إذا خلصت أو إذا الحارس المشترك مانع الطلب."""
    u = st.setdefault('tv_calls', {}); day = datetime.fromtimestamp(now, NY).date().isoformat()
    if u.get('day') != day: u.clear(); u.update(day=day, n=0)
    if now < tv.blocked_until(): raise tv.QuotaError(f"حد TradingView: محجوب لين {datetime.fromtimestamp(tv.blocked_until(), NY):%H:%M}")
    if u['n'] + need > TV_DAILY_BUDGET: raise tv.QuotaError(f"وصلت ميزانية البوت اللحظي ({u['n']} طلب اليوم)")
    return u


def tv_count(u, n=1): u['n'] = u.get('n', 0) + n               # نحسبه قبل الطلب: الفاشل يتحسب بعد


def tvlog_signals(now, st):
    """يقرا سجل التنبيهات (يوم واحد، 100 حدث) ويرجع (حيّة، فايتة، فاشلة-مؤقتاً): حيّة = أقدم من STALE_SEC ما تنطبق، فايتة = اليوم بس تأخرت (تتسجل بأثر رجعي)."""
    u = tv_budget(now, st); tv_count(u)
    r = tv.call(TVLOG_TOOL, {'days': 1, 'limit': 100})
    evs = r.get('events') if isinstance(r, dict) else None
    if not isinstance(evs, list): raise RuntimeError(f'رد سجل التنبيهات مو مفهوم: {str(r)[:200]}')
    st['tvlog'] = dict(last_ok=int(now), events=len(evs))
    today = datetime.fromtimestamp(now, NY).date(); live, late, failed = [], [], []; batch = set()
    for ev in sorted(evs, key=lambda e: e.get('fired_at') or ''):
        try: x = json.loads(ev.get('message') or '')
        except ValueError: log('⚠️ [سجل TradingView] رسالة مو JSON:', str(ev.get('message'))[:120]); continue
        sig = parse_alert(x, 'سجل TradingView', now)
        if not sig: continue
        fid = ev.get('fire_id')
        if is_seen(st, sig, fid) or sig_key(sig) in batch or (fid is not None and fid in batch): continue      # تكرار داخل نفس الرد
        batch.add(sig_key(sig)); fid is not None and batch.add(fid)
        age = now - sig['t']
        if age <= STALE_SEC:
            wh = (ev.get('webhook') or {}).get('http_code')
            try: t = build_signal(sig, now)
            except Exception as e:                                 # فشل أبيان لإشارة وحدة: ما نسقط الباقي ولا نعيد طلب السجل، تنعاد من tvlog_pending
                log('⚠️ فشل سعر أبيان لإشارة السجل، أعيد كل دقيقة بدون TradingView:', sig['symbol'], repr(e)[:150]); failed.append(dict(sig=sig, fire_id=fid, wh=wh)); continue
            t.update(sig=sig, fire_id=fid, wh=wh)
            log('📋 إشارة من سجل TradingView:', t['symbol'], t['side'], 'fire_id', fid, '| ntfy:', t['wh'], '| سعر أبيان', t['entry']); live.append(t)
        elif datetime.fromtimestamp(sig['t'], NY).date() == today:
            late.append(dict(sig=sig, fire_id=fid, wh=(ev.get('webhook') or {}).get('http_code')))
        else: mark_seen(st, now, sig, fid)
    return live, late, failed


def iso(t): return datetime.fromtimestamp(t, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def tv_bars(sym, t0, t1, n=500):
    r = tv.call(tv.OHLCV, dict(symbol=sym, interval='1m', date_from=iso(t0), date_to=iso(t1), format='columns', count=n))
    c = r.get('bars') or {}
    if isinstance(c, list): return [b for b in c if 't' in b]
    return [dict(t=c['t'][i], o=c['o'][i], h=c['h'][i], l=c['l'][i], c=c['c'][i], v=(c.get('v') or [0] * len(c['t']))[i]) for i in range(len(c.get('t', [])))]


def opra(sym, exp, side, K): return f"OPRA:{sym}{exp:%y%m%d}{side}{K:.1f}"      # نفس tv_record.tv_symbol


RETRO_WIN, RETRO_MIN_VOL = 300, 5      # نافذة الدخول بعد الإشارة (ثانية) · أقل حجم للعقد (من افتتاح الجلسة لين نهاية النافذة)


def retro_pick(sym, side, S, t0, now, st, a):
    """(24 نوفمبر) نفس منطق المفاضلة (contract_select) بس بأرقام حقيقية: المرشح لازم عليه صفقة حقيقية من وقت الإشارة لين 5 دقايق بعدها،
    وسعره = أول صفقة بعد الإشارة (ممنوع أي سعر قبلها). حجم الجلسة (من الافتتاح لين نهاية النافذة) ≥ RETRO_MIN_VOL شرط ثابت.
    ترتيب التجربة بالدلتا المتوقعة (تذبذب أول مرشح له صفقة)، وسقف RETRO_MAX_CALLS طلب. يرجع (sel أو None، سبب)."""
    d0 = datetime.fromtimestamp(t0, NY); open_t = int(d0.replace(hour=9, minute=30, second=0, microsecond=0).timestamp()); end = min(int(now), t0 + RETRO_WIN)
    typ = {'C': 'call', 'P': 'put'}[side]; calls, why, cands = 0, [], []
    for exp in expiries(sym, d0.date())[:3]:
        ks = sorted({float(c['strike_price']) for c in abyan.call('browse_options_chain', dict(symbol=sym, expiration_date=exp.isoformat(), type=typ, limit=250)).get('contracts', [])})
        if not ks: continue
        T = m.yearfrac(t0, exp.isoformat()); iv = 0.45; tried = set(); got = []
        order = lambda ivx: sorted(ks, key=lambda k: abs(abs(cs.bs_delta(side, S, k, T, ivx)) - 0.42))
        while calls < RETRO_MAX_CALLS:
            nxt = next((k for k in order(iv) if k not in tried), None)
            if nxt is None: break
            tried.add(nxt); calls += 1; tv_count(tv_budget(now, st))
            bs = tv_bars(opra(sym, exp, side, nxt), open_t, end, 200)
            vol = sum(b['v'] for b in bs); first = next((b for b in bs if t0 <= b['t'] <= end and b['v'] > 0), None)
            if not first: why.append(f'{exp} {nxt}: ما فيه صفقة خلال 5 دقايق من الإشارة'); continue
            c = cs.make_cand(exp, side, nxt, first['o'], S, t0, vol=vol)
            if not c: why.append(f'{exp} {nxt}: ما قدرت أحسب التذبذب'); continue
            iv = c['iv']                                              # التجربة الجاية بالدلتا المتوقعة من تذبذب حقيقي
            if vol < RETRO_MIN_VOL: why.append(f'{exp} {nxt}: سيولة ضعيفة (حجم {vol})'); continue
            c.update(t=first['t'], hi=first['h']); cands.append(c); got.append(c)
            if len(got) >= 3: break
        if cands: break
    sel, w2 = cs.select(cands, side, S, a)
    if not sel: why.append(w2 or 'ما فيه مرشح')
    return sel, '؛ '.join(why[-8:]) + (f' (وصلت سقف {RETRO_MAX_CALLS} طلبات)' if calls >= RETRO_MAX_CALLS else '')


def retro_enter(st, sig, now, fire_id=None, wh=None):
    """(24 نوفمبر) إشارة اليوم فاتتنا (ntfy فشل) وعدّى وقتها: نسجلها بأثر رجعي بأرقام حقيقية بس من TradingView:
    العقد (OPRA دقيقة) = أول صفقة حقيقية بعد الإشارة خلال 5 دقايق، والسهم = افتتاح نفس الدقيقة. ما نسجل أي سعر قبل الإشارة ولا تقريبي.
    ما لقينا عقد يحقق الشروط → نكتب السبب (بدون عقد). لو الصفقة خلصت قبل ما نلحقها يسجلها التسجيل الآلي بآخر اليوم."""
    sym, side, t0 = sig['symbol'], sig['side'], sig['t']; key = f"{sym}-{t0}"
    if any(x['key'] == key for x in st['trades']): return 'موجودة'
    if any(x['symbol'] == sym and not x.get('t_exit') for x in st['trades']): log('⚠️ إشارة فايتة', key, 'وفيه صفقة مفتوحة على نفس السهم، تجاهلتها'); return 'مفتوحة'
    sgn = 1 if side == 'C' else -1; a = sig['atr']; ex = tv.EXCH[sym]
    tv_count(tv_budget(now, st, 1 + RETRO_MAX_CALLS))
    sb = tv_bars(f'{ex}:{sym}', t0, int(now))
    first = next((b for b in sb if b['t'] >= t0), None)
    if not first: raise RuntimeError(f'ما فيه شموع دقيقة لـ {sym} من وقت الإشارة')
    def levels(e): return round(e + sgn * m.TGT * a, 2), round(e - sgn * m.STP * a, 2)
    def hit_from(t_in, e):
        target, stop = levels(e)
        return next(((b['t'], 'وقف' if sgn * (b['l' if sgn > 0 else 'h'] - stop) <= 0 else 'هدف') for b in sb if b['t'] >= t_in and
                     (sgn * (b['l' if sgn > 0 else 'h'] - stop) <= 0 or sgn * (b['h' if sgn > 0 else 'l'] - target) >= 0)), None)
    def missed(h):
        st.setdefault('missed', []).append(dict(key=key, symbol=sym, side=side, t_entry=t0, fire_id=fire_id, why=f'خلصت ({h[1]}) قبل ما نلحقها'))
        log(f'⚠️ إشارة {key} فاتتنا وخلصت ({h[1]}) — يسجلها التسجيل الآلي بآخر اليوم بأسعار العقد الحقيقية'); return 'خلصت'
    if hit_from(t0, first['o']): return missed(hit_from(t0, first['o']))
    sel, why = retro_pick(sym, side, first['o'], t0, now, st, a); c = sel and sel['c']
    rec = dict(key=key, day=datetime.fromtimestamp(t0, NY).date().isoformat(), symbol=sym, side=side, t_entry=t0, atr=a, retro=True, fire_id=fire_id, ntfy_http=wh)
    if c:
        sbar = next((b for b in sb if b['t'] == c['t']), None)             # السهم: افتتاح نفس دقيقة صفقة العقد
        if not sbar: raise RuntimeError(f"ما فيه شمعة سهم بدقيقة صفقة العقد ({c['t']})")
        e = sbar['o']; h = hit_from(c['t'], e)
        if h: return missed(h)
        target, stop = levels(e); d = c['t'] - t0
        rec.update(entry=round(e, 2), target=target, stop=stop, expiry=c['exp'].isoformat(), strike=c['K'], opt_entry=c['price'], opt_entry_hi=c['hi'], opt_entry_at=int(c['t']),
                   opt_entry_vol=c['vol'], src=f"سعر صفقة حقيقية على العقد (OPRA، TradingView) بعد الإشارة بـ {d // 60} دقيقة و{d % 60} ثانية؛ السهم: افتتاح نفس الدقيقة؛ حجم العقد {c['vol']}",
                   **cs.record_fields(sel))
        log('دخول بأثر رجعي', sym, side, c['K'], c['exp'], f"{c['price'] * 100:.0f}$", '|', sel['why'])
    else:
        target, stop = levels(first['o'])
        rec.update(entry=round(first['o'], 2), target=target, stop=stop, t_exit=int(now), why='بدون عقد',
                   note=f'ما لقيت عقد 100–1000$ عليه صفقة حقيقية خلال 5 دقايق من الإشارة وحجمه ≥ {RETRO_MIN_VOL}: {why}')
        log('دخول بأثر رجعي بدون عقد', key, why)
    st['trades'].append(rec); save(st, f'لحظي: دخول بأثر رجعي {sym} {side}'); return 'سُجلت'


def tvlog_step(now, st, tl):
    """قراءة السجل لشمعة وحدة: فشلت → تحذير واضح وإعادة بعد دقيقة (لين تنتهي الشمعة). ممنوع السكوت."""
    try:
        live, late, failed = tvlog_signals(now, st)
    except (Exception, SystemExit) as e:                      # SystemExit: فشل توكن TradingView — ما نقتل الجوب (ntfy وأبيان يكملون)
        tl['tries'] += 1; tl['due'] = now + 60
        log(f"{'❌' if tl['tries'] >= 3 else '⚠️'} فشل قراءة سجل تنبيهات TradingView (محاولة {tl['tries']}): {e!r:.300} — أعيد بعد دقيقة")
        if tl['due'] >= (tl['bar'] + 1) * 900: log('❌ ما قدرت أقرا سجل التنبيهات هالشمعة كلها: أي إشارة فايتة تنلحق بالقراءة الجاية (أثر رجعي) لو لسا اليوم')
        return
    tl['done'] = True; log(f"سجل التنبيهات: {st['tvlog']['events']} حدث، حيّة {len(live)}، فايتة {len(late)}، مؤجلة {len(failed)}")
    have = {sig_key(P['sig']) for P in tl.get('pending', [])}; tl['pending'] = tl.get('pending', []) + [P for P in failed if sig_key(P['sig']) not in have]
    for t in live:
        try: enter(st, t, now); mark_seen(st, now, t['sig'], t.get('fire_id'))
        except Exception: log('⚠️ فشل الدخول على إشارة السجل (أعيد بالقراءة الجاية):', traceback.format_exc()[-400:])
    tvlog_late(now, st, late)


def tvlog_pending(now, st, tl):
    """إشارات السجل اللي فشل سعر أبيان لها: نعيد كل دقيقة بدون طلب TradingView جديد؛ لو عدّى STALE_SEC تروح للأثر الرجعي."""
    left, late = [], []
    for P in tl.get('pending', []):
        sig = P['sig']
        if is_seen(st, sig, P['fire_id']): continue
        if now - sig['t'] > STALE_SEC: late.append(P); continue
        try:
            t = build_signal(sig, now); t.update(sig=sig, fire_id=P['fire_id'], wh=P['wh'])
            enter(st, t, now); mark_seen(st, now, sig, P['fire_id'])
        except Exception as e: log('⚠️ إعادة سعر أبيان فشلت:', sig['symbol'], repr(e)[:150]); left.append(P)
    tl['pending'] = left
    if late: tvlog_late(now, st, late)


def tvlog_late(now, st, late):
    for L in late:
        fails = st.setdefault('retro_fail', {}); k = sig_key(L['sig'])
        try:
            log('📋 إشارة فايتة من السجل:', k, 'fire_id', L['fire_id'], '| ntfy:', L['wh'], '— أسجلها بأثر رجعي'); res = retro_enter(st, L['sig'], now, L['fire_id'], L['wh'])
            mark_seen(st, now, L['sig'], L['fire_id']); log('نتيجة الأثر الرجعي', k, res)
        except (Exception, SystemExit) as e:
            fails[k] = fails.get(k, 0) + 1
            log(f'⚠️ فشل التسجيل بأثر رجعي ({fails[k]}/3) {k}: {e!r:.300}')
            if fails[k] >= 3: mark_seen(st, now, L['sig'], L['fire_id']); log(f'❌ تخليت عن {k} — يبقى للتسجيل الآلي بآخر اليوم')


# ───────── الإشارات ─────────
def update_tv(now, usage=None):
    """شموع 15د من حساب أحمد في TradingView (المكتملة بس)، تندمج مع ملفات data/."""
    for s in m.SYMBOLS:
        if usage is not None: usage['n'] = usage.get('n', 0) + 1     # نحسبه قبل الطلب: الفاشل يتحسب بعد
        new = [b for b in tv.bars(s, '15m', 130) if b['t'] + 900 <= now]
        p = f.DATA / f'{s}-15m.json'; old = json.loads(p.read_text())
        first = new[0]['t'] if new else 1e12
        old['bars'] = [b for b in old['bars'] if b['t'] < first] + new
        old.update(count=len(old['bars']), source_note='TradingView (حساب أحمد) — لحظي')
        p.write_text(json.dumps(old, separators=(',', ':')))


TV_BLOCK_UNTIL = 0.0      # كولداون حد TradingView (10 دقايق) — نوقف الطلبات ونعيد بعده
TV_FAILS = 0              # فشل حد متتالي: بعد 3 نعتبره الحد اليومي ونوقف ساعة بدل ما نكرر الطلب كل 11 دقيقة
TV_DAILY_BUDGET = 150     # حد البوت اللحظي لليوم (حد الحساب 500 يشاركه التسجيل الليلي، وكل طلب فاشل يتحسب)
def new_signals(now, st):
    global TV_BLOCK_UNTIL, TV_FAILS
    u = st.setdefault('tv_calls', {}); day = datetime.fromtimestamp(now, NY).date().isoformat()
    if u.get('day') != day: u.clear(); u.update(day=day, n=0)
    try:
        if now < TV_BLOCK_UNTIL: raise tv.QuotaError(f'كولداون TradingView لين {datetime.fromtimestamp(TV_BLOCK_UNTIL, NY):%H:%M}')
        if u['n'] + len(m.SYMBOLS) > TV_DAILY_BUDGET: raise tv.QuotaError(f"وصلت ميزانية البوت اللحظي ({u['n']} طلب اليوم)")
        update_tv(now, u); TV_FAILS = 0               # المصدر الأساسي: حسابك في TradingView (طلب واحد لكل سهم في الشمعة)
    except Exception as e:
        if isinstance(e, tv.QuotaError) and now >= TV_BLOCK_UNTIL:    # نكمل باللي عندنا ونعيد بعد الكولداون
            TV_FAILS += 1; TV_BLOCK_UNTIL = now + (60 if TV_FAILS >= 3 else 11) * 60
        log('TradingView ما رد، أستخدم ياهو هالمرة:', e)
        try:
            info = f.update_data(); bad = {k: v for k, v in info.items() if 'ما رجع' in v}
            if bad: log('⚠️ ياهو بعد ما رجّع شموع:', bad)
        except Exception as e2: log('⚠️ ياهو فشل:', e2)
    data = f.load_data()
    for s in m.SYMBOLS:
        lt = data[s][-1]['t'] if data.get(s) else 0
        if now - lt > 1500 + 900: log(f'⚠️ شموع {s} قديمة: آخر شمعة {datetime.fromtimestamp(lt, NY):%m-%d %H:%M} — ما فيه إشارة لهالسهم الجولة هذي')
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
    c = choose_contract(t['symbol'], t['side'], t['entry'], t['atr'], now, st)
    rec = dict(key=key, day=datetime.fromtimestamp(now, NY).date().isoformat(), symbol=t['symbol'], side=t['side'],
               t_entry=t['t_entry'], entry=t['entry'], target=t['target'], stop=t['stop'], atr=t['atr'])
    if c:
        x = c['c']
        rec.update(expiry=x['exp'].isoformat(), strike=x['K'], opt_entry=x['price'], opt_entry_at=int(now), src='أبيان لحظي', **cs.record_fields(c))
        log('دخول', t['symbol'], t['side'], x['K'], x['exp'], f"{x['price'] * 100:.0f}$", '|', c['why'])
    else:
        rec.update(note='ما لقيت ولا عقد ضمن 100–1000$ بين مرشحي أبيان (3 تواريخ)', t_exit=int(now), why='بدون عقد'); log('دخول بدون عقد', key)
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
        t0 = time.time(); S = stock_px(a.test[0]); c = choose_contract(a.test[0], a.test[1], S, 0.02 * S / 3, time.time(), load())
        log(a.test, 'السهم', S, '← العقد', c and (c['c']['exp'].isoformat(), c['c']['K'], f"{c['c']['price'] * 100:.0f}$", c['why']), f'({time.time() - t0:.1f} ثانية)')
        save(load(), 'لحظي: اختبار'); return
    st = load(); log('مصدر الإشارات:', ('سجل تنبيهات TradingView (أساسي)' + (' + ntfy (سريع)' if WEBHOOK else '')) if SIGNAL_ALERTS else 'طلب الشموع من TradingView'); last_bar = None; closed = None; last_rec = 0.0; last_keep = 0.0; tl = dict(bar=None, due=0.0, tries=0, done=False)
    while True:
        now = time.time(); d = datetime.fromtimestamp(now, NY); mins = d.hour * 60 + d.minute - 570; cm = close_min(d)
        if time.time() - T0 > JOB_SEC:
            # (19 نوفمبر) مواعيد GitHub تتأخر ساعات، فالبوت ما يعتمد عليها: كل جوب يشغّل اللي بعده على طول،
            # فيكون فيه دايم جوب ينتظر الافتتاح أو يراقب السوق
            (ROOT / '.again').write_text('1'); log('حد الجوب: جوب ثاني بيكمل'); save(st, 'لحظي: نهاية الجوب'); break
        if now - last_keep > 20 * 3600:  # (20 نوفمبر، بعد مراجعة Gemini) تجديد دخول TradingView وأبيان مرة كل 20 ساعة بس
            last_keep = now
            try: tv.access_token(); abyan.access_token(); save(st, 'لحظي: تجديد الدخول')
            except BaseException as e: log('⚠️ تجديد الدخول فشل:', e)
        if closed_day(d) or mins >= cm + 2 or mins < -2:
            done_today = closed_day(d) or mins >= cm + 2          # بعد الإغلاق أو ويكند أو عطلة
            if not closed_day(d) and mins >= cm + 2 and closed != d.date():     # السوق سكر: نحفظ شموع اليوم للتسجيل
                closed = d.date(); save(st, 'لحظي: إغلاق السوق', data=True); log('السوق سكر')
            # (21 نوفمبر) التسجيل الآلي بعد الإغلاق بس — قبل الافتتاح كان ياكل حد TradingView ويقفله طول وقت السوق
            if done_today:
                try:
                    import record_auto
                    record_auto.run_missing(deadline=T0 + JOB_SEC - 600); st = load()
                except Exception: log('التسجيل الآلي:', traceback.format_exc()[-400:])
            if done_today:
                # (20 نوفمبر، طلب أحمد) البوت يشتغل بس وقت السوق: مهمة مجدولة تشغّله 8:58 نيويورك (قبل الافتتاح بنص ساعة)،
                # وبعد الإغلاق والتسجيل يقفل بدون ما يشغّل جوب ثاني — ما يصير شغال 24 ساعة على GitHub
                save(st, 'لحظي: نهاية اليوم'); log('خلص اليوم، البوت يقفل لين بكرة'); break
            time.sleep(60); continue
        try:
            bar = int(now) // 900
            if SIGNAL_ALERTS:
                if 0 <= mins <= m.LATE_MIN + 16:
                    if WEBHOOK:                                                        # كل دقيقة: ntfy (مصدر سريع إضافي، بدون TradingView)
                        try:
                            for t in webhook_signals(now, st): enter(st, t, now); mark_seen(st, now, t['sig'])
                        except Exception as e: log('⚠️ قراءة ntfy فشلت (السجل يغطي):', repr(e)[:200])
                    if tl['bar'] != bar: tl.update(bar=bar, due=bar * 900 + TVLOG_DELAY, tries=0, done=False)     # السجل: مرة بعد إغلاق كل شمعة بـ 35 ثانية
                    if not tl['done'] and now >= tl['due']: tvlog_step(now, st, tl)
                    if tl.get('pending'): tvlog_pending(now, st, tl)
            elif 0 <= mins <= m.LATE_MIN + 1 and bar != last_bar and now % 900 >= 25:   # 25 ثانية بعد إغلاق الشمعة
                last_bar = bar
                for t in new_signals(now, st): enter(st, t, now)
            check_exits(st, now, close_all=mins >= cm - 1)                             # دقيقة قبل الإغلاق يقفل الكل
        except Exception:
            log(traceback.format_exc()[-600:])
        if a.once: break
        if int(now) // 1800 != int(time.time() + 60) // 1800: save(st, 'لحظي: حفظ التوكن')   # كل نص ساعة
        nxt = time.time() + max(5, 60 - time.time() % 60 + 2)
        if SIGNAL_ALERTS:     # نصحى بالضبط بعد إغلاق الشمعة بـ 35 ثانية (وعند موعد إعادة المحاولة) مو بس على رأس الدقيقة
            cand = [c for c in ((tl['due'] if not tl['done'] and tl['bar'] is not None else 0), (int(time.time()) // 900 + 1) * 900 + TVLOG_DELAY) if c > time.time() + 1]
            nxt = min([nxt] + cand)
        time.sleep(max(1, nxt - time.time()))


if __name__ == '__main__':
    try:
        main()
    except SystemExit as e:
        # (20 نوفمبر، بعد مراجعة Gemini) فشل الدخول كان يقتل الجوب بصمت ويوقف السلسلة.
        # invalid_grant = الدخول مات فعلاً: نوقف بخطأ (يوصلك إيميل فشل من GitHub) وما نعيد، وبعد ما تسجل دخول (finish) البوت يرجع يشتغل لحاله.
        # غيره (عطل مؤقت في السيرفر/الشبكة): ننتظر 5 دقايق ونخلي الجوب اللي بعده يكمل.
        msg = str(e.code or '')
        if e.code not in (None, 0):
            if 'invalid_grant' in msg or 'أعد الدخول' in msg and '(400)' in msg:
                log('❌ الدخول انتهى:', msg, '— سجّل دخول من جديد (start ثم finish) والبوت يرجع لحاله')
            else:
                log('⚠️ عطل مؤقت:', msg, '— أعيد بعد 5 دقايق'); time.sleep(300); (ROOT / '.again').write_text('1')
        raise
