"""The hosted Grain Marketing Desk: sign-in, the app page, and the data API it calls.

The page is the same grain-desk.html the Claude version runs. A small script (web/hosted.js)
gives it the same `window.claude.use(...)` interface, backed by the routes below, so the
page code does not need to know where it is running.
"""
import json
import logging
import os
import re
import time

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from . import access, claude_api, config, mail, store

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('grain')
app = FastAPI(title='Grain Marketing Desk', docs_url=None, redoc_url=None, openapi_url=None)
COOKIE = 'gd_session'
WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'web')


@app.on_event('startup')
def _startup():
    store.init()


# ---------- who is signed in ----------

def current_user(request: Request):
    u = store.session_user(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, 'Sign in again.')
    return u


def api_user(request: Request, u=Depends(current_user)):
    # a header the browser only sends from our own page: blocks cross-site form posts
    if request.method not in ('GET', 'HEAD') and request.headers.get('x-grain-desk') != '1':
        raise HTTPException(403, 'Missing request header.')
    return u


def user_json(u):
    return {'id': u['id'], 'name': u['name'], 'email': u['email'], 'role': u['role']}


# ---------- sign in by email link ----------

PAGE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Grain Marketing Desk · Sign in</title><style>
body{{margin:0;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:#D6E5CF;color:#17201A;display:grid;place-items:center;min-height:100vh;padding:16px;box-sizing:border-box}}
main{{background:#fff;border-radius:14px;padding:28px;max-width:380px;width:100%;box-shadow:0 8px 24px rgba(0,0,0,.08)}}
h1{{font-size:1.3rem;margin:0 0 6px}} p{{color:#5A675E;margin:0 0 16px}} label{{display:grid;gap:6px;font-weight:600;font-size:.9rem}}
input{{font:inherit;padding:10px 12px;border:1.5px solid #c9d3c9;border-radius:10px}} button{{margin-top:14px;width:100%;padding:11px;border:0;border-radius:10px;background:#1D6A4D;color:#fff;font:600 1rem system-ui}}
.note{{background:#EEF3EC;border-radius:10px;padding:12px;color:#17201A}}
.wrap{{max-width:640px;width:100%;display:grid;justify-items:center;gap:18px}} .disc{{font-size:.68rem;line-height:1.45;color:#5A675E;margin:0}}
</style></head><body><div class="wrap"><main><h1>Grain Marketing Desk</h1>{body}</main><p class="disc">{disc}</p></div></body></html>"""

# JSA's required disclaimer, same wording as the app (DISCLAIMER in grain-desk.html) and the JSA dashboards
DISCLAIMER = ("Trading commodity futures, options on futures, cash commodities, and over-the-counter derivative products involves "
              "substantial risk of loss and may not be suitable for all investors. This communication is provided for informational "
              "purposes only and does not constitute investment advice, a recommendation, or an offer or solicitation to buy or sell "
              "any futures, options, cash commodities, or derivative products. John Stewart &amp; Associates, Inc. does not accept "
              "orders to buy or sell any financial instruments via email. The information contained herein has been obtained from "
              "sources believed to be reliable; however, its accuracy and completeness are not guaranteed. Any opinions expressed are "
              "solely those of the author, are subject to change without notice, and should not be relied upon as a basis for "
              "investment decisions. Past performance is not indicative of future results. This message may contain confidential or "
              "proprietary information intended solely for the use of the designated recipient. "
              "&copy; John Stewart &amp; Associates, Inc. {year}")

_recent = {}


def _throttled(key, seconds=60):
    t = time.time()
    if _recent.get(key, 0) > t - seconds:
        return True
    _recent[key] = t
    return False


@app.get('/login', response_class=HTMLResponse)
def login_page(sent: int = 0, bad: int = 0):
    if sent:
        body = '<p class="note">Check your email for a sign-in link. It works once and expires in {} minutes.</p>'.format(config.LOGIN_LINK_MINUTES)
    else:
        msg = '<p class="note">That link has expired or was already used. Ask for a new one.</p>' if bad else '<p>Enter your email and we\'ll send you a sign-in link.</p>'
        body = msg + '<form method="post" action="/login"><label>Email<input name="email" type="email" required autocomplete="email" autofocus></label><button type="submit">Send sign-in link</button></form>'
    return PAGE.format(body=body, disc=DISCLAIMER.format(year=time.localtime().tm_year))


@app.post('/login')
def login_request(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    # same answer whether or not the address has an account, so the form can't be used to look up users
    if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) and not _throttled('mail:' + email):
        u = store.user_by_email(email)
        if u and u['active']:
            t = store.new_login_token(u['id'])
            try:
                mail.send_login_link(u['email'], u['name'], f'{config.PUBLIC_URL}/auth?t={t}')
            except Exception:
                log.exception('could not send the sign-in email to %s', email)
    return RedirectResponse('/login?sent=1', status_code=303)


@app.get('/auth')
def auth(t: str = ''):
    uid = store.use_login_token(t) if t else None
    if not uid:
        return RedirectResponse('/login?bad=1', status_code=303)
    r = RedirectResponse('/', status_code=303)
    r.set_cookie(COOKIE, store.new_session(uid), max_age=config.SESSION_DAYS * 86400, httponly=True,
                 secure=config.COOKIE_SECURE, samesite='lax')
    return r


@app.post('/logout')
def logout(request: Request):
    t = request.cookies.get(COOKIE)
    if t:
        store.end_session(t)
    r = RedirectResponse('/login', status_code=303)
    r.delete_cookie(COOKIE)
    return r


# ---------- the app page ----------

@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    if not store.session_user(request.cookies.get(COOKIE)):
        return RedirectResponse('/login', status_code=303)
    with open(config.APP_HTML, encoding='utf-8') as f:
        page = f.read()
    shim = '<script src="/hosted.js"></script>'
    # the hosted script must run before the app's own script
    i = page.find('<script>')
    page = page[:i] + shim + page[i:] if i >= 0 else shim + page
    return HTMLResponse('<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
                        '<style>[hidden]{display:none!important}</style></head><body>' + page + '</body></html>',
                        headers={'Cache-Control': 'no-store'})


@app.get('/hosted.js')
def hosted_js():
    return FileResponse(os.path.join(WEB, 'hosted.js'), media_type='text/javascript', headers={'Cache-Control': 'no-cache'})


# ---------- data API (the page's db) ----------

def _coll(path):
    try:
        return store.split(path)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _doc_json(r):
    return {'id': r['id'], 'exists': True, 'data': json.loads(r['data']), 'version': r['version']}


@app.get('/api/me')
def me(u=Depends(current_user)):
    return user_json(u)


@app.get('/api/seq')
def seq(u=Depends(current_user)):
    return {'seq': store.current_seq()}


@app.get('/api/doc')
def get_doc(path: str, u=Depends(current_user)):
    coll, did = _coll(path)
    r = store.get_doc(path)
    if not r or not access.can_read(u, coll, r):
        return {'id': did, 'exists': False}       # hidden looks the same as missing
    return _doc_json(r)


@app.put('/api/doc')
def put_doc(path: str, body: dict = Body(...), u=Depends(api_user)):
    coll, _ = _coll(path)
    data = body.get('data')
    if not isinstance(data, dict):
        raise HTTPException(400, 'data must be an object')
    if body.get('merge'):
        old = store.get_doc(path)
        if not old:
            raise HTTPException(400, 'update needs an existing document')
        data = _merge(json.loads(old['data']), data)
    row = store.get_doc(path)
    if not access.can_write(u, coll, row):
        raise HTTPException(403, "You can't change this.")
    broker = access.farm_broker(u, data) if coll == access.FARMS else None
    try:
        v = store.put_doc(path, data, broker_uid=broker, if_version=body.get('ifVersion'))
    except store.VersionConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {'version': v, 'seq': store.current_seq()}


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            a[k] = _merge(a[k], v)
        else:
            a[k] = v
    return a


@app.delete('/api/doc')
def delete_doc(path: str, u=Depends(api_user)):
    coll, _ = _coll(path)
    row = store.get_doc(path)
    if row is None:
        return {'ok': True}
    if not access.can_write(u, coll, row):
        raise HTTPException(403, "You can't remove this.")
    store.delete_doc(path)
    return {'ok': True, 'seq': store.current_seq()}


@app.get('/api/collection')
def get_collection(path: str, since: int = 0, u=Depends(current_user)):
    """Documents changed since `since` (0 = all), plus every visible id so the page can drop deleted ones."""
    parts = path.strip('/').split('/')
    if len(parts) % 2 == 0:
        raise HTTPException(400, 'a collection path has an odd number of segments')
    scope = access.collection_scope(u, path)
    if scope is False:
        return {'docs': [], 'ids': [], 'seq': store.current_seq()}
    s = store.current_seq()
    return {'docs': [_doc_json(r) for r in store.list_docs(path, since, scope)], 'ids': store.list_ids(path, scope), 'seq': s}


# ---------- contract photos ----------

@app.post('/api/assets')
async def upload_asset(request: Request, u=Depends(api_user)):
    ctype = request.headers.get('content-type', '').split(';')[0].strip()
    if ctype not in claude_api.MEDIA:
        raise HTTPException(400, 'Photos must be JPEG, PNG, WebP or GIF.')
    blob = await request.body()
    if not blob or len(blob) > config.MAX_ASSET_BYTES:
        raise HTTPException(400, 'Photo is empty or too large.')
    aid = store.add_asset(u['id'], ctype, blob)
    return {'id': aid, 'url': '/_blob/' + aid, 'sizeBytes': len(blob), 'contentType': ctype}


@app.get('/_blob/{aid}')
def get_asset(aid: str, u=Depends(current_user)):
    # photo ids are long random values that only appear inside farm records the person can already
    # read, so any signed-in person holding the id may view it (an admin's upload on a broker's farm, too)
    a = store.get_asset(aid) if re.fullmatch(r'[0-9a-f]{32}', aid) else None
    if not a:
        raise HTTPException(404, 'Not found')
    return FileResponse(os.path.join(config.ASSET_DIR, aid), media_type=a['content_type'], headers={'Cache-Control': 'private, max-age=86400'})


@app.delete('/api/assets/{aid}')
def delete_asset(aid: str, u=Depends(api_user)):
    a = store.get_asset(aid)
    if a and (u['role'] == 'admin' or a['owner_uid'] == u['id']):
        store.delete_asset(aid)
    return {'ok': True}


# ---------- Claude (contract scanning, Intel drafts) ----------

@app.post('/api/claude')
def ask_claude(body: dict = Body(...), u=Depends(api_user)):
    if _throttled('claude:' + u['id'], 3):
        return JSONResponse({'code': 'rate_limited', 'message': 'One request at a time. Try again in a moment.'}, status_code=429)
    try:
        text, truncated = claude_api.ask(body.get('input', ''), body.get('images') or [])
    except claude_api.ClaudeError as e:
        status = {'not_configured': 503, 'bad_request': 400, 'rate_limited': 429, 'refused': 422}.get(e.code, 502)
        return JSONResponse({'code': e.code, 'message': str(e)}, status_code=status)
    log.info('claude call by %s (%s)', u['email'], 'with image' if body.get('images') else 'text')
    return {'text': text, 'truncated': truncated}


@app.get('/healthz')
def healthz():
    store.current_seq()
    return {'ok': True}
