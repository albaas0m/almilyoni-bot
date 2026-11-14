"""المليوني ↔ أبيان مباشرة (بدون Claude): البوت يسجل دخول OAuth مرة وحدة ويسأل أبيان عن سعر العقد بنفسه.

السر الوحيد: ABYAN_KEY في GitHub Secrets (نص عشوائي طويل يحطه أحمد بنفسه).
التوكن محفوظ مشفّر في abyan_token.enc (ما ينقرأ بدون ABYAN_KEY)، ويتجدد تلقائيًا.

  python3 abyan.py start                 # يسجل البوت عند أبيان ويطبع رابط الدخول
  python3 abyan.py finish --code "<الرابط اللي وصلت له بعد الدخول أو الكود>"
  python3 abyan.py price AMD 2026-11-27 put 495
"""
import os, sys, json, time, hmac, hashlib, base64, secrets, argparse, urllib.parse, urllib.request, urllib.error
from pathlib import Path

BASE = 'https://mcp.abyancapital.sa'
REDIRECT = 'http://localhost:8765/callback'
ROOT = Path(__file__).resolve().parent
CLIENT, TOKEN = ROOT / 'abyan_client.json', ROOT / 'abyan_token.enc'
MCP_URLS = (BASE + '/', BASE + '/mcp/')


def _key():
    k = os.environ.get('ABYAN_KEY', '')
    if len(k) < 16: sys.exit('ABYAN_KEY ناقص: أضفه في GitHub ← Settings ← Secrets and variables ← Actions')
    return hashlib.sha256(k.encode()).digest()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(_key()))


def _b64(b): return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _verifier(cid): return _b64(hmac.new(_key(), f'{cid}|pkce'.encode(), hashlib.sha256).digest())


def _post(url, data, form=True, headers=None):
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    h = {'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json', 'Accept': 'application/json',
         'User-Agent': 'almilyoni-bot/1'}
    h.update(headers or {})
    req = urllib.request.Request(url, body, h, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


# ───────── الدخول (مرة وحدة) ─────────
def start():
    st, _, b = _post(BASE + '/oauth/register', dict(client_name='almilyoni-bot', redirect_uris=[REDIRECT],
                     grant_types=['authorization_code', 'refresh_token'], response_types=['code'],
                     token_endpoint_auth_method='none', scope='trading'), form=False)
    if st >= 300: sys.exit(f'التسجيل عند أبيان رفض ({st}): {b[:300]}')
    cid = json.loads(b)['client_id']
    CLIENT.write_text(json.dumps(dict(client_id=cid, redirect_uri=REDIRECT), indent=1))
    ch = _b64(hashlib.sha256(_verifier(cid).encode()).digest())
    url = BASE + '/oauth/authorize?' + urllib.parse.urlencode(dict(response_type='code', client_id=cid, redirect_uri=REDIRECT,
          scope='trading', state=secrets.token_urlsafe(8), code_challenge=ch, code_challenge_method='S256', resource=BASE))
    print('رابط الدخول:\n' + url)


def _save(tok):
    tok['saved_at'] = int(time.time())
    TOKEN.write_bytes(_fernet().encrypt(json.dumps(tok).encode()))


def finish(code):
    if 'code=' in code: code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)['code'][0]
    cid = json.loads(CLIENT.read_text())['client_id']
    st, _, b = _post(BASE + '/oauth/token', dict(grant_type='authorization_code', code=code.strip(), redirect_uri=REDIRECT,
                     client_id=cid, code_verifier=_verifier(cid), resource=BASE))
    if st >= 300: sys.exit(f'أبيان رفض الكود ({st}): {b[:300]}')
    tok = json.loads(b); _save(tok)
    print('تم الربط ✅', 'refresh' if tok.get('refresh_token') else 'بدون refresh_token!', 'ينتهي بعد', tok.get('expires_in'), 'ثانية')


def access_token():
    tok = json.loads(_fernet().decrypt(TOKEN.read_bytes()))
    if time.time() < tok['saved_at'] + int(tok.get('expires_in') or 3600) - 120: return tok['access_token']
    if not tok.get('refresh_token'): sys.exit('التوكن انتهى وما فيه refresh: أعد الدخول (start ثم finish)')
    cid = json.loads(CLIENT.read_text())['client_id']
    st, _, b = _post(BASE + '/oauth/token', dict(grant_type='refresh_token', refresh_token=tok['refresh_token'], client_id=cid, resource=BASE))
    if st >= 300: sys.exit(f'تجديد التوكن فشل ({st}): {b[:300]} — أعد الدخول')
    new = json.loads(b); new.setdefault('refresh_token', tok['refresh_token']); _save(new)
    return new['access_token']


# ───────── MCP ─────────
def _parse(b):
    if b.lstrip().startswith('{'): return json.loads(b)
    for line in b.splitlines():                      # SSE
        if line.startswith('data:'):
            d = json.loads(line[5:]);
            if 'id' in d: return d
    raise ValueError(b[:300])


def call(tool, args):
    at = access_token()
    for url in MCP_URLS:
        h = {'Authorization': f'Bearer {at}', 'Accept': 'application/json, text/event-stream'}
        st, hd, b = _post(url, dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion='2025-06-18',
                          capabilities={}, clientInfo=dict(name='almilyoni-bot', version='1'))), form=False, headers=h)
        if st >= 300: last = (st, b[:200]); continue
        sid = next((v for k, v in hd.items() if k.lower() == 'mcp-session-id'), None)
        if sid: h['Mcp-Session-Id'] = sid
        _post(url, dict(jsonrpc='2.0', method='notifications/initialized'), form=False, headers=h)
        st, _, b = _post(url, dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name=tool, arguments=args)), form=False, headers=h)
        r = _parse(b)
        if 'error' in r: raise RuntimeError(r['error'])
        txt = ''.join(c.get('text', '') for c in r['result'].get('content', []))
        return r['result'].get('structuredContent') or json.loads(txt)
    raise RuntimeError(f'ما قدرت أتصل بأبيان: {last}')


def option_price(symbol, expiry, typ, strike):
    """سعر العقد من أبيان الحين (fmv). typ = call/put أو C/P."""
    typ = {'C': 'call', 'P': 'put'}.get(typ, typ)
    r = call('get_current_price', dict(symbol=symbol, asset_class='us_option', expiration_date=str(expiry),
                                       option_type=typ, strike_price=float(strike)))
    return float(r['fmv'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd'); ap.add_argument('rest', nargs='*'); ap.add_argument('--code', default='')
    a = ap.parse_args()
    if a.cmd == 'start': start()
    elif a.cmd == 'finish': finish(a.code)
    elif a.cmd == 'price':
        t0 = time.time(); p = option_price(*a.rest); print(f'{a.rest} = {p} ({time.time() - t0:.1f} ثانية)')
