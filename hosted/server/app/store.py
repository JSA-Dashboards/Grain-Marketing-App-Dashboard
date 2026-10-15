"""SQLite storage for the hosted Grain Marketing Desk.

The app stores JSON documents at slash paths, exactly as it did as a Claude app
(farms/<id>, market/futures, market/wx_42.74_-93.75 ...). Keeping that shape means
the page code is unchanged and data moves over as a straight copy. A later move to
Snowflake or Postgres only has to replace this module.
"""
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_local = threading.local()
_write_lock = threading.Lock()

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS docs (
  path TEXT PRIMARY KEY,          -- e.g. farms/client-ab12cd34
  coll TEXT NOT NULL,             -- e.g. farms
  id TEXT NOT NULL,               -- e.g. client-ab12cd34
  data TEXT NOT NULL,             -- the JSON document
  version INTEGER NOT NULL,       -- bumps on every write (optimistic concurrency)
  seq INTEGER NOT NULL,           -- global change counter, for cheap polling
  broker_uid TEXT,                -- farms only: the broker who works this farm
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS docs_coll ON docs(coll, seq);
CREATE TABLE IF NOT EXISTS deleted (path TEXT PRIMARY KEY, coll TEXT NOT NULL, seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL COLLATE NOCASE, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','broker')), active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, last_login TEXT
);
CREATE TABLE IF NOT EXISTS login_tokens (hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, owner_uid TEXT NOT NULL, content_type TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def conn():
    c = getattr(_local, 'c', None)
    if c is None:
        os.makedirs(os.path.dirname(config.DB_PATH) or '.', exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=30000')
        _local.c = c
    return c


def init():
    conn().executescript(SCHEMA)
    os.makedirs(config.ASSET_DIR, exist_ok=True)


@contextmanager
def tx():
    """One write transaction at a time (SQLite allows a single writer)."""
    with _write_lock:
        c = conn()
        c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.execute('COMMIT')
        except BaseException:
            c.execute('ROLLBACK')
            raise


def _next_seq(c):
    row = c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()
    n = int(row['v']) + 1 if row else 1
    c.execute("INSERT INTO meta(k,v) VALUES('seq',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(n),))
    return n


def current_seq():
    row = conn().execute("SELECT v FROM meta WHERE k='seq'").fetchone()
    return int(row['v']) if row else 0


def split(path):
    parts = path.strip('/').split('/')
    if len(parts) < 2 or len(parts) % 2:
        raise ValueError('a document path has an even number of segments')
    return '/'.join(parts[:-1]), parts[-1]


# ---------- documents ----------

def get_doc(path):
    r = conn().execute('SELECT * FROM docs WHERE path=?', (path,)).fetchone()
    return r


def list_docs(coll, since=0, broker_uid=None):
    q, a = 'SELECT * FROM docs WHERE coll=? AND seq>?', [coll, since]
    if broker_uid is not None:
        q += ' AND broker_uid=?'
        a.append(broker_uid)
    return conn().execute(q + ' ORDER BY id', a).fetchall()


def list_ids(coll, broker_uid=None):
    q, a = 'SELECT id FROM docs WHERE coll=?', [coll]
    if broker_uid is not None:
        q += ' AND broker_uid=?'
        a.append(broker_uid)
    return [r['id'] for r in conn().execute(q, a).fetchall()]


def put_doc(path, data, broker_uid=None, if_version=None):
    """Create or replace a document. Returns the new version."""
    coll, did = split(path)
    body = json.dumps(data, separators=(',', ':'))
    if len(body) > config.MAX_DOC_BYTES:
        raise ValueError('document too large')
    with tx() as c:
        old = c.execute('SELECT version FROM docs WHERE path=?', (path,)).fetchone()
        if if_version is not None and (old['version'] if old else 0) != if_version:
            raise VersionConflict(old['version'] if old else 0)
        v = (old['version'] if old else 0) + 1
        s = _next_seq(c)
        c.execute('INSERT INTO docs(path,coll,id,data,version,seq,broker_uid,updated_at) VALUES(?,?,?,?,?,?,?,?) '
                  'ON CONFLICT(path) DO UPDATE SET data=excluded.data, version=excluded.version, seq=excluded.seq, '
                  'broker_uid=excluded.broker_uid, updated_at=excluded.updated_at',
                  (path, coll, did, body, v, s, broker_uid, now_iso()))
        c.execute('DELETE FROM deleted WHERE path=?', (path,))
        return v


def delete_doc(path):
    coll, _ = split(path)
    with tx() as c:
        if c.execute('DELETE FROM docs WHERE path=?', (path,)).rowcount:
            s = _next_seq(c)
            c.execute('INSERT INTO deleted(path,coll,seq) VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET seq=excluded.seq', (path, coll, s))


class VersionConflict(Exception):
    def __init__(self, current):
        super().__init__(f'document changed (now version {current})')
        self.current = current


# ---------- users, sign-in links, sessions ----------

def new_id(prefix):
    return prefix + secrets.token_urlsafe(9).replace('-', 'x').replace('_', 'y')


def add_user(email, name, role):
    with tx() as c:
        uid = new_id('u_')
        c.execute('INSERT INTO users(id,email,name,role,active,created_at) VALUES(?,?,?,?,1,?)',
                  (uid, email.strip().lower(), name.strip(), role, now_iso()))
        return uid


def set_user(uid, **fields):
    allowed = {k: v for k, v in fields.items() if k in ('name', 'role', 'active', 'email')}
    if not allowed:
        return
    with tx() as c:
        c.execute('UPDATE users SET ' + ', '.join(f'{k}=?' for k in allowed) + ' WHERE id=?', (*allowed.values(), uid))


def user_by_email(email):
    return conn().execute('SELECT * FROM users WHERE email=? COLLATE NOCASE', (email.strip(),)).fetchone()


def user_by_id(uid):
    return conn().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()


def users():
    return conn().execute('SELECT * FROM users ORDER BY role, name').fetchall()


def broker_by_name(name):
    if not name:
        return None
    return conn().execute("SELECT * FROM users WHERE role='broker' AND active=1 AND lower(name)=lower(?)", (name.strip(),)).fetchone()


def _hash(token):
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def new_login_token(uid):
    t = secrets.token_urlsafe(32)
    with tx() as c:
        c.execute('DELETE FROM login_tokens WHERE expires<?', (time.time(),))
        c.execute('INSERT INTO login_tokens(hash,user_id,expires) VALUES(?,?,?)', (_hash(t), uid, time.time() + config.LOGIN_LINK_MINUTES * 60))
    return t


def use_login_token(t):
    with tx() as c:
        r = c.execute('SELECT * FROM login_tokens WHERE hash=?', (_hash(t),)).fetchone()
        if not r or r['used'] or r['expires'] < time.time():
            return None
        c.execute('UPDATE login_tokens SET used=1 WHERE hash=?', (_hash(t),))
        return r['user_id']


_code_tries = {}


def new_login_code(uid):
    """6-digit sign-in code (the Streamlit version; reuses login_tokens). Any earlier one for this person is voided."""
    code = f'{secrets.randbelow(10**6):06d}'
    with tx() as c:
        c.execute('DELETE FROM login_tokens WHERE user_id=? OR expires<?', (uid, time.time()))
        c.execute('INSERT INTO login_tokens(hash,user_id,expires) VALUES(?,?,?)',
                  (_hash(uid + ':' + code), uid, time.time() + config.LOGIN_CODE_MINUTES * 60))
    _code_tries[uid] = 0
    return code


def use_login_code(uid, code):
    if _code_tries.get(uid, 0) >= 5:
        return False
    with tx() as c:
        r = c.execute('SELECT * FROM login_tokens WHERE hash=?', (_hash(uid + ':' + (code or '').strip()),)).fetchone()
        if not r or r['used'] or r['expires'] < time.time():
            _code_tries[uid] = _code_tries.get(uid, 0) + 1
            return False
        c.execute('DELETE FROM login_tokens WHERE user_id=?', (uid,))
        return True


def new_session(uid):
    t = secrets.token_urlsafe(32)
    with tx() as c:
        c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        c.execute('INSERT INTO sessions(hash,user_id,expires) VALUES(?,?,?)', (_hash(t), uid, time.time() + config.SESSION_DAYS * 86400))
        c.execute('UPDATE users SET last_login=? WHERE id=?', (now_iso(), uid))
    return t


def session_user(t):
    if not t:
        return None
    r = conn().execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>? AND u.active=1',
                       (_hash(t), time.time())).fetchone()
    return r


def end_session(t):
    with tx() as c:
        c.execute('DELETE FROM sessions WHERE hash=?', (_hash(t),))


def end_user_sessions(uid):
    with tx() as c:
        c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))


# ---------- uploaded contract photos ----------

def add_asset(owner_uid, content_type, blob):
    aid = secrets.token_hex(16)
    with open(os.path.join(config.ASSET_DIR, aid), 'wb') as f:
        f.write(blob)
    with tx() as c:
        c.execute('INSERT INTO assets(id,owner_uid,content_type,size,created_at) VALUES(?,?,?,?,?)',
                  (aid, owner_uid, content_type, len(blob), now_iso()))
    return aid


def get_asset(aid):
    return conn().execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()


def delete_asset(aid):
    with tx() as c:
        c.execute('DELETE FROM assets WHERE id=?', (aid,))
    try:
        os.remove(os.path.join(config.ASSET_DIR, aid))
    except FileNotFoundError:
        pass


# The Streamlit version keeps everything in Snowflake: same function names, so the page bridge,
# the access rules and the nightly jobs run unchanged.
if config.STORE_BACKEND == 'snowflake':
    from .store_sf import *                        # noqa: F401,F403,E402
    from .store_sf import VersionConflict, split   # noqa: F401,E402
