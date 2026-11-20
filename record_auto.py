"""التسجيل اليومي الآلي (20 نوفمبر 2026) — بدون Claude، يجيب الملفات الناقصة من TradingView + أبيان بنفسه.

يكرر tv_record.run لكل يوم ناقص: يطلع قائمة NEED، يجيبها (get_ohlcv من TradingView، browse_options_chain من أبيان)،
ويحفظها في tv_cache/. كل ملف انحفظ ما ينطلب مرة ثانية. إذا خلص حد TradingView (TOOL_QUOTA_EXCEEDED، كولداون ~10 دقايق):
يحفظ اللي جابه وينتظر الكولداون ويكمل — ما يوقف كامل. ميزانية الطلبات محدودة عشان ما ياكل حد الحساب اليومي (500).

  python3 record_auto.py                    # كل الأيام الناقصة من آخر يوم مسجل
  python3 record_auto.py --days 2026-11-17,2026-11-18
"""
import json, time, argparse, subprocess
from datetime import datetime, date, timedelta
import almilyoni_weekly as m
import fetch_real as f
import tv_record as r
import tv, abyan

NY = m.NY; CALL_BUDGET = 120


def log(*a): print(datetime.now(NY).strftime('%H:%M:%S'), '[تسجيل]', *a, flush=True)


def missing_days(now=None):
    """أيام تداول (فيها شموع في data/) بعد آخر يوم مسجل في paper.json وانتهت جلستها."""
    now = now or datetime.now(NY); st = json.loads((r.ROOT / 'paper.json').read_text())
    done = set(st.get('days', {}))
    have = {datetime.fromtimestamp(b['t'], NY).date().isoformat() for b in json.loads((f.DATA / 'SPY-15m.json').read_text())['bars']}
    last = max(done) if done else '2000-01-01'
    out = []
    d = date.fromisoformat(last) + timedelta(days=1)
    while d <= now.date():
        closed = d < now.date() or now.hour >= 17     # بعد 5م نيويورك الجلسة خلصت والشموع محفوظة
        if d.weekday() < 5 and d.isoformat() not in done and closed and (d.isoformat() in have or d < now.date()):
            out.append(d)
        d += timedelta(days=1)
    return out


def fetch(need):
    """يجيب ملف NEED واحد ويحفظه. QuotaError يطلع لفوق."""
    name = need['file']; p = r.CACHE / name
    if need['tool'] == 'get_ohlcv':
        args = {k: v for k, v in need.items() if k not in ('file', 'tool')}
        res = tv.call(tv.OHLCV, args)
        p.write_text(json.dumps({'bars': res.get('bars', [])}))
    else:
        res = abyan.call('browse_options_chain', dict(symbol=need['symbol']))
        p.write_text(json.dumps({'expiration_dates': res['expiration_dates']}))


def commit(msg):
    try:
        subprocess.run('git add tv_cache data paper.json abyan_token.enc tv_token.enc && (git diff --cached --quiet || git commit -qm "' + msg + '") && '
                       'for i in 1 2 3; do git pull -q --rebase --autostash origin main && git push -q && exit 0; sleep 5; done; exit 1',
                       shell=True, cwd=r.ROOT, timeout=180, executable='/bin/bash')
    except Exception as e: log('git:', e)


def record_day(day, deadline=None):
    """True إذا انسجل اليوم. deadline = وقت (time.time) نوقف عنده وننتظر الجولة الجاية."""
    calls = 0
    r.CACHE.mkdir(exist_ok=True)
    while True:
        r.NEED.clear()
        try: res = r.run(day)
        except r.Missing: res = None
        if res is not None:
            log('انسجل', day, len(res), 'صفقة'); commit(f'تسجيل آلي {day}'); return True
        need = {json.dumps(x, sort_keys=True): x for x in r.NEED}.values()
        for x in need:
            if calls >= CALL_BUDGET: log('وصلت ميزانية الطلبات، أكمل بالجولة الجاية'); commit(f'حفظ اللي انجاب {day}'); return False
            waits = 0
            while True:
                if deadline and time.time() > deadline: log('خلص الوقت المسموح'); commit(f'حفظ اللي انجاب {day}'); return False
                try: fetch(x); calls += 1; break
                except tv.QuotaError as e:
                    waits += 1
                    if waits > 3: log('الحد يبدو يومي (3 كولداون متتالية)، أكمل بالجولة الجاية'); commit(f'حفظ اللي انجاب {day}'); return False
                    log('حد TradingView:', str(e)[:120], '— أنتظر الكولداون 11 دقيقة'); commit(f'حفظ اللي انجاب {day}'); time.sleep(11 * 60)
                except Exception as e:
                    log('فشل جلب', x['file'], ':', str(e)[:200]); return False


def run_missing(days=None, deadline=None):
    ok = True
    for d in (days or missing_days()):
        log('اليوم', d)
        if not record_day(d, deadline): ok = False; break      # الأقدم أول: ما نتخطى يوم
    return ok


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--days', default=''); a = ap.parse_args()
    ds = [date.fromisoformat(x) for x in a.days.split(',') if x] or None
    raise SystemExit(0 if run_missing(ds) else 1)
