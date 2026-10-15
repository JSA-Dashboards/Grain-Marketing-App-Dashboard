"""The page's data calls, answered in Python (the Streamlit stand-in for hosted/server/app/main.py).

Every call comes from the page as (op, args). The signed-in person is NEVER taken from the page: the
caller passes the user it got at sign-in, so a tampered page can't act as someone else. Access rules are
the shared ones in hosted/server/app/access.py (admin everything; broker only their own farms).

Each handler returns the same JSON the FastAPI routes returned, or raises Err(code, message).
"""
import base64
import json
import re
import time

from app import access, claude_api, config, store


class Err(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


_last_claude = {}


def _split(path):
    try:
        return store.split(path)
    except ValueError as e:
        raise Err('invalid_argument', str(e))


def _doc_json(r):
    return {'id': r['id'], 'exists': True, 'data': json.loads(r['data']), 'version': r['version']}


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            a[k] = _merge(a[k], v)
        else:
            a[k] = v
    return a


def me(u, a):
    return {'id': u['id'], 'name': u['name'], 'email': u['email'], 'role': u['role']}


def seq(u, a):
    return {'seq': store.current_seq()}


def doc_get(u, a):
    coll, did = _split(a['path'])
    r = store.get_doc(a['path'])
    if not r or not access.can_read(u, coll, r):
        return {'id': did, 'exists': False}          # hidden looks the same as missing
    return _doc_json(r)


def doc_put(u, a):
    path = a['path']
    coll, _ = _split(path)
    data = a.get('data')
    if not isinstance(data, dict):
        raise Err('invalid_argument', 'data must be an object')
    if a.get('merge'):
        old = store.get_doc(path)
        if not old:
            raise Err('invalid_argument', 'update needs an existing document')
        data = _merge(json.loads(old['data']), data)
    row = store.get_doc(path)
    if not access.can_write(u, coll, row):
        raise Err('permission_denied', "You can't change this.")
    broker = access.farm_broker(u, data) if coll == access.FARMS else None
    try:
        v = store.put_doc(path, data, broker_uid=broker, if_version=a.get('ifVersion'))
    except store.VersionConflict as e:
        raise Err('aborted', str(e))
    except ValueError as e:
        raise Err('invalid_argument', str(e))
    return {'version': v, 'seq': store.current_seq()}


def doc_delete(u, a):
    coll, _ = _split(a['path'])
    row = store.get_doc(a['path'])
    if row is None:
        return {'ok': True}
    if not access.can_write(u, coll, row):
        raise Err('permission_denied', "You can't remove this.")
    store.delete_doc(a['path'])
    return {'ok': True, 'seq': store.current_seq()}


def coll_get(u, a):
    """Documents changed since `since` (0 = all), plus every visible id so the page can drop deleted ones."""
    path = a['path']
    if len(path.strip('/').split('/')) % 2 == 0:
        raise Err('invalid_argument', 'a collection path has an odd number of segments')
    scope = access.collection_scope(u, path)
    s = store.current_seq()
    if scope is False:
        return {'docs': [], 'ids': [], 'seq': s}
    return {'docs': [_doc_json(r) for r in store.list_docs(path, int(a.get('since') or 0), scope)],
            'ids': store.list_ids(path, scope), 'seq': s}


def asset_put(u, a):
    ctype = a.get('type', '')
    if ctype not in claude_api.MEDIA:
        raise Err('invalid_argument', 'Photos must be JPEG, PNG, WebP or GIF.')
    try:
        blob = base64.b64decode(a.get('b64', ''), validate=True)
    except Exception:
        raise Err('invalid_argument', 'Photo is not readable.')
    if not blob or len(blob) > config.MAX_ASSET_BYTES_SF:
        raise Err('invalid_argument', 'Photo is empty or larger than 7 MB.')
    aid = store.add_asset(u['id'], ctype, blob)
    return {'id': aid, 'url': '/_blob/' + aid, 'sizeBytes': len(blob), 'contentType': ctype}


def asset_get(u, a):
    # ids are long random values that only appear inside farm records the person can already read
    aid = a.get('id', '')
    meta = store.get_asset(aid) if re.fullmatch(r'[0-9a-f]{32}', aid) else None
    if not meta:
        raise Err('not_found', 'Not found')
    return {'dataUrl': f"data:{meta['content_type']};base64," + base64.b64encode(store.get_asset_bytes(aid)).decode()}


def asset_delete(u, a):
    meta = store.get_asset(a.get('id', ''))
    if meta and (u['role'] == 'admin' or meta['owner_uid'] == u['id']):
        store.delete_asset(a['id'])
    return {'ok': True}


def claude(u, a):
    t = time.time()
    if _last_claude.get(u['id'], 0) > t - 3:
        raise Err('rate_limited', 'One request at a time. Try again in a moment.')
    _last_claude[u['id']] = t
    try:
        text, truncated = claude_api.ask(a.get('input', ''), a.get('images') or [])
    except claude_api.ClaudeError as e:
        raise Err(e.code, str(e))
    return {'text': text, 'truncated': truncated}


OPS = {'me': me, 'seq': seq, 'doc_get': doc_get, 'doc_put': doc_put, 'doc_delete': doc_delete, 'coll_get': coll_get,
       'asset_put': asset_put, 'asset_get': asset_get, 'asset_delete': asset_delete, 'claude': claude}


def handle(user, req):
    """One request {id, op, args} -> {id, ok, result} or {id, ok: False, code, message}."""
    try:
        fn = OPS.get(req.get('op'))
        if not fn:
            raise Err('invalid_argument', 'unknown request')
        return {'id': req['id'], 'ok': True, 'result': fn(user, req.get('args') or {})}
    except Err as e:
        return {'id': req.get('id'), 'ok': False, 'code': e.code, 'message': str(e)}
    except Exception as e:           # never leak internals to the page
        import logging
        logging.getLogger('grain').exception('request %s failed', req.get('op'))
        return {'id': req.get('id'), 'ok': False, 'code': 'unavailable', 'message': 'Something went wrong on the server. Try again.'}
