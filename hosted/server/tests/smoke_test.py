"""End-to-end check of the hosted server against a throwaway database (nothing real is touched).

  hosted\\.venv\\Scripts\\python hosted\\server\\tests\\smoke_test.py

Signs in as an admin and as a broker (reading the sign-in link from the log, as when no email
server is set up), then checks what each can see and change, photo upload, and the page itself.
"""
import json
import os
import re
import sys
import tempfile
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
tmp = tempfile.mkdtemp(prefix='grain-test-')
os.environ.update(DATA_DIR=tmp, APP_HTML=os.path.join(ROOT, 'grain-desk.html'), PUBLIC_URL='http://testserver', SMTP_HOST='')
sys.path.insert(0, os.path.join(ROOT, 'hosted', 'server'))

from fastapi.testclient import TestClient  # noqa: E402
from app import main, store  # noqa: E402

links = []


class Grab(logging.Handler):
    def emit(self, rec):
        m = re.search(r'(http://testserver/auth\?t=\S+)', rec.getMessage())
        if m:
            links.append(m.group(1))


logging.getLogger('grain.mail').addHandler(Grab())
store.init()
store.add_user('megan@example.com', 'Megan', 'admin')
store.add_user('tyler@example.com', 'Tyler', 'broker')
store.add_user('ann@example.com', 'Ann', 'broker')

ok = fail = 0


def check(name, cond, detail=''):
    global ok, fail
    if cond:
        ok += 1
        print('  ok   ', name)
    else:
        fail += 1
        print('  FAIL ', name, detail)


def sign_in(email):
    c = TestClient(main.app)
    main._recent.clear()
    r = c.post('/login', data={'email': email}, follow_redirects=False)
    assert r.status_code == 303, r.text
    t = links[-1].split('t=')[1]
    r = c.get('/auth?t=' + t, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/', r.text
    c.headers['x-grain-desk'] = '1'
    return c, t


H = {'x-grain-desk': '1'}
with TestClient(main.app) as anon:
    print('sign-in')
    check('page asks to sign in', anon.get('/', follow_redirects=False).headers.get('location') == '/login')
    check('API refuses without sign-in', anon.get('/api/me').status_code == 401)
    n = len(links)
    anon.post('/login', data={'email': 'nobody@example.com'}, follow_redirects=False)
    check('unknown email gets no link (same reply)', len(links) == n)

megan, mt = sign_in('megan@example.com')
tyler, tt = sign_in('tyler@example.com')
ann, _ = sign_in('ann@example.com')
check('a sign-in link works only once', TestClient(main.app).get('/auth?t=' + tt, follow_redirects=False).headers['location'] == '/login?bad=1')
check('admin role', megan.get('/api/me').json()['role'] == 'admin')
check('broker role', tyler.get('/api/me').json()['role'] == 'broker')

print('the app page')
page = megan.get('/')
check('page served with the hosted script first', page.status_code == 200 and page.text.find('/hosted.js') < page.text.find('<script>'))
check('hosted.js served', megan.get('/hosted.js').status_code == 200)

print('farms and brokers')
r = megan.put('/api/doc?path=farms/f-tyler', json={'data': {'name': 'Johnson Grain', 'broker': 'tyler'}})
check('admin saves a farm for Tyler (name match ignores case)', r.status_code == 200, r.text)
megan.put('/api/doc?path=farms/f-megan', json={'data': {'name': 'Megan Farm', 'broker': ''}})
ids = lambda c: sorted(d['id'] for d in c.get('/api/collection?path=farms').json()['docs'])
check('admin sees every farm', ids(megan) == ['f-megan', 'f-tyler'], ids(megan))
check('Tyler sees only his farm', ids(tyler) == ['f-tyler'], ids(tyler))
check('Ann sees none', ids(ann) == [], ids(ann))
check("Ann can't read Tyler's farm", ann.get('/api/doc?path=farms/f-tyler').json()['exists'] is False)
check("Ann can't change Tyler's farm", ann.put('/api/doc?path=farms/f-tyler', json={'data': {'name': 'x'}}).status_code == 403)
check("Ann can't delete Tyler's farm", ann.delete('/api/doc?path=farms/f-tyler').status_code == 403)
r = tyler.put('/api/doc?path=farms/f-new', json={'data': {'name': 'New Farm', 'broker': 'Ann'}})
check('Tyler adds a farm', r.status_code == 200, r.text)
d = tyler.get('/api/doc?path=farms/f-new').json()
check('the new farm is his, whatever broker was typed', d['exists'] and d['data']['broker'] == 'Tyler', d)
check("Ann still can't see it", ann.get('/api/doc?path=farms/f-new').json()['exists'] is False)
v = d['version']
r = tyler.put('/api/doc?path=farms/f-new', json={'data': {'acres': 10}, 'merge': True})
check('merge update keeps other fields', r.status_code == 200 and tyler.get('/api/doc?path=farms/f-new').json()['data']['name'] == 'New Farm')
r = tyler.put('/api/doc?path=farms/f-new', json={'data': {'name': 'stale'}, 'ifVersion': v})
check('a stale write is refused', r.status_code == 409, r.status_code)
seq = tyler.get('/api/seq').json()['seq']
megan.put('/api/doc?path=farms/f-tyler', json={'data': {'name': 'Johnson Grain LLC', 'broker': 'Tyler'}})
ch = tyler.get(f'/api/collection?path=farms&since={seq}').json()
check('polling returns only what changed', [x['id'] for x in ch['docs']] == ['f-tyler'] and sorted(ch['ids']) == ['f-new', 'f-tyler'], ch)
megan.put('/api/doc?path=farms/f-tyler', json={'data': {'name': 'Johnson Grain LLC', 'broker': 'Ann'}})
check('admin reassigning to Ann moves the farm', ids(ann) == ['f-tyler'] and ids(tyler) == ['f-new'], (ids(ann), ids(tyler)))
check("Tyler can't delete Ann's farm now", tyler.delete('/api/doc?path=farms/f-tyler').status_code == 403)
check('Tyler deletes his own farm', tyler.delete('/api/doc?path=farms/f-new').status_code == 200 and ids(tyler) == [])

print('shared market data')
check('admin writes market data', megan.put('/api/doc?path=market/intel', json={'data': {'posts': [{'title': 'Hi'}]}}).status_code == 200)
check('broker reads it', tyler.get('/api/doc?path=market/intel').json()['data']['posts'][0]['title'] == 'Hi')
check("broker can't write Intel", tyler.put('/api/doc?path=market/intel', json={'data': {'posts': []}}).status_code == 403)
check("broker can't write program deadlines", tyler.put('/api/doc?path=market/deadlines', json={'data': {}}).status_code == 403)
check('a write without the page header is refused', TestClient(main.app, cookies=dict(tyler.cookies)).put('/api/doc?path=farms/z', json={'data': {}}).status_code == 403)
check('bad paths are refused', megan.get('/api/doc?path=farms').status_code == 400)

print('contract photos')
png = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000') + b'\x00' * 20
r = tyler.post('/api/assets', content=png, headers={'content-type': 'image/png', **H})
check('broker uploads a photo', r.status_code == 200, r.text)
aid = r.json()['id']
check('photo can be viewed', tyler.get('/_blob/' + aid).content == png)
check('a non-image upload is refused', tyler.post('/api/assets', content=b'x', headers={'content-type': 'text/html', **H}).status_code == 400)
check('photo deleted', tyler.delete('/api/assets/' + aid).status_code == 200 and tyler.get('/_blob/' + aid).status_code == 404)

print('Claude (scanning)')
r = tyler.post('/api/claude', json={'input': 'hi'})
check('without an API key, a clear message instead of a crash', r.status_code == 503 and r.json()['code'] == 'not_configured', r.text)

print('admin tools')
from app import manage  # noqa: E402
megan.put('/api/doc?path=farms/f-x', json={'data': {'name': 'X', 'broker': 'Zed'}})
store.add_user('zed@example.com', 'Zed', 'broker')
manage.cmd_relink(None)
zed, _ = sign_in('zed@example.com')
check('a new broker picks up farms already named for them', ids(zed) == ['f-x'], ids(zed))
store.set_user(store.user_by_email('zed@example.com')['id'], active=0)
check('a turned-off broker is signed out', zed.get('/api/me').status_code == 401)

print('sign out')
megan.post('/logout', follow_redirects=False)
check('signed out', megan.get('/api/me').status_code == 401)

print(f'\n{ok} passed, {fail} failed  (test data in {tmp})')
sys.exit(1 if fail else 0)
