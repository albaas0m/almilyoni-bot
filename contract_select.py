"""اختيار العقد (24 نوفمبر 2026، قرار أحمد): بدل "أول عقد سعره 100–1000$ من ATM" صار نجمع كل المرشحين ونفاضل بينهم.

1) فلتر سيولة (إذا البيانات متوفرة): حجم اليوم ≥ 30، اهتمام مفتوح ≥ 500، سبريد ≤ 8% من الـmid.
2) فلتر دلتا: 0.30 ≤ |delta| ≤ 0.55 (دلتا بلاك-شولز من تذبذب العقد المحسوب من سعره نفسه).
3) الميزانية 100–1000$ سقف بس، مو معيار.
4) المفاضلة: نعيد تسعير كل مرشح ببلاك-شولز (نفس التذبذب) عند سعر الهدف وسعر الوقف مع خصم وقت الاحتفاظ،
   ونختار أعلى (صافي الربح عند الهدف ÷ صافي الخسارة عند الوقف) بعد نص السبريد والعمولة.
5) دايم نلقى عقد: لو ما نجح أحد، نخفف بالتدريج (السيولة أولاً ثم الدلتا لين 0.25 ثم الدلتا كلها) ونكتب أي فلتر تخفف.
بيانات السيولة ما تجي من أبيان (بس fmv)، فإذا غابت (None) ما نرفض بسببها ونكتب "غير متوفر".
"""
import math
import almilyoni_weekly as m

HOLD_H = 3.5                          # متوسط مدة الاحتفاظ في paper.json (3.7 ساعة، 33 صفقة)
DELTA_LO, DELTA_HI, DELTA_FLOOR = 0.30, 0.55, 0.25
LIQ_STRICT = dict(vol=30, oi=500, spread=0.08)
LIQ_LOOSE = dict(vol=10, oi=100, spread=0.15)
LEVELS = [('كل الفلاتر', LIQ_STRICT, DELTA_LO), ('سيولة مخففة', LIQ_LOOSE, DELTA_LO),
          ('بدون فلتر سيولة', None, DELTA_LO), ('دلتا ≥ 0.25 وبدون سيولة', None, DELTA_FLOOR), ('بدون فلتر دلتا وسيولة', None, 0.0)]


def bs_delta(side, S, K, T, v):
    if T <= 1e-6 or v <= 0: return (1.0 if S > K else 0.0) if side == 'C' else (-1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + (m.RATE + v * v / 2) * T) / (v * math.sqrt(T)); n = m._N(d1)
    return n if side == 'C' else n - 1


def make_cand(exp, side, K, price, S, now_ts, vol=None, oi=None, bid=None, ask=None):
    """مرشح: price = سعر العقد (fmv أو أول صفقة حقيقية). يرجع None إذا ما نقدر نحسب التذبذب."""
    T = m.yearfrac(now_ts, exp.isoformat()); iv = m.implied_vol(price, S, K, T, side)
    if not iv: return None
    sp = (ask - bid) / ((ask + bid) / 2) if bid and ask and ask >= bid and ask + bid > 0 else None
    return dict(exp=exp, K=K, price=price, T=T, iv=iv, delta=bs_delta(side, S, K, T, iv), vol=vol, oi=oi, bid=bid, ask=ask, spread=sp)


def score(c, side, S, a, hold_h=HOLD_H, tgt_px=None, stp_px=None):
    """(صافي الربح عند الهدف $، صافي الخسارة عند الوقف $ موجب، النسبة) بعد نص السبريد والعمولة."""
    sgn = 1 if side == 'C' else -1; half = (c['ask'] - c['bid']) / 2 if c.get('spread') is not None else 0.0
    T2 = max(c['T'] - hold_h / (365 * 24), 1e-6); entry = c['price'] + half
    vt = m.bs_price(side, tgt_px if tgt_px is not None else S + sgn * m.TGT * a, c['K'], T2, c['iv']) - half
    vs = max(m.bs_price(side, stp_px if stp_px is not None else S - sgn * m.STP * a, c['K'], T2, c['iv']) - half, 0.0)
    gain = (vt - entry) * 100 - 2 * m.COMM; loss = (entry - vs) * 100 + 2 * m.COMM
    return gain, loss, gain / loss


def in_budget(c): return c['price'] * 100 >= m.PREM_MIN and c['price'] * 100 + m.COMM <= m.BUDGET


def liq_ok(c, cfg):
    if cfg is None: return True
    if c.get('vol') is not None and c['vol'] < cfg['vol']: return False
    if c.get('oi') is not None and c['oi'] < cfg['oi']: return False
    if c.get('spread') is not None and c['spread'] > cfg['spread']: return False
    return True


def liq_known(c): return {k: c.get(k) is not None for k in ('vol', 'oi', 'spread')}


def select(cands, side, S, a, hold_h=HOLD_H, tgt_px=None, stp_px=None):
    """يرجع (المرشح الأفضل + حقول التسجيل) أو (None، سبب). يخفف الفلاتر بالتدريج ولا يرفض إلا إذا ما فيه ولا عقد ضمن الميزانية."""
    pool = [c for c in cands if in_budget(c)]
    if not pool: return None, 'ما فيه ولا عقد ضمن الميزانية (100–1000$) بين المرشحين'
    for n, (name, cfg, dlo) in enumerate(LEVELS):
        ok = [c for c in pool if liq_ok(c, cfg) and dlo <= abs(c['delta']) <= (DELTA_HI if dlo else 1.0)]
        if not ok: continue
        sc = [(score(c, side, S, a, hold_h, tgt_px, stp_px), c) for c in ok]; (g, l, r), best = max(sc, key=lambda x: x[0][2])
        relaxed = [] if n == 0 else [name]
        unknown = [k for k, v in liq_known(best).items() if not v]
        sp_txt = 'غير متوفر' if best['spread'] is None else f"{best['spread'] * 100:.1f}%"
        why = (f"نسبة ربح/خسارة {r:.2f} (+{g:.0f}$ عند الهدف، -{l:.0f}$ عند الوقف) · دلتا {abs(best['delta']):.2f} · "
               f"حجم {best['vol'] if best['vol'] is not None else 'غير متوفر'} · OI {best['oi'] if best['oi'] is not None else 'غير متوفر'} · "
               f"سبريد {sp_txt} · مستوى الفلاتر: {name} · "
               f"من {len(ok)} مرشح (و{len(pool)} ضمن الميزانية)")
        return dict(c=best, level=name, relaxed=relaxed, ratio=round(r, 2), gain=round(g), loss=round(l), why=why, unknown=unknown), ''
    return None, 'ما فيه مرشح'


def record_fields(sel):
    c = sel['c']
    return dict(delta=round(abs(c['delta']), 3), day_vol=c['vol'], oi=c['oi'], spread_pct=None if c['spread'] is None else round(c['spread'] * 100, 2),
                iv=round(c['iv'], 3), rr_ratio=sel['ratio'], rr_gain=sel['gain'], rr_loss=sel['loss'], filter_level=sel['level'],
                filters_relaxed=sel['relaxed'], liq_unknown=sel['unknown'], why_chosen=sel['why'])
