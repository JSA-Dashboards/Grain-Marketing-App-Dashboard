"""Snowflake storage for the Streamlit version of the Grain Marketing Desk (JSA.GRAIN_DESK).

Same functions as store.py (SQLite), so the page bridge, access rules and nightly jobs are shared.
Differences that matter:
  * deletes are soft (DELETED flag) so a poll can tell the page what disappeared;
  * the change counter is MAX(SEQ), and SEQ is the write's time in microseconds. (A Snowflake SEQUENCE is NOT
    used: it hands out numbers in blocks, so a later write can get a LOWER number than an earlier one, and a
    "highest number" poll then misses it.) A poll re-reads the 60 s before its mark, so a write that commits
    late is still picked up;
  * contract photos are stored in the ASSETS table (BINARY, 7 MB max) instead of on disk;
  * sign-in uses a 6-digit emailed code (LOGIN_CODES), 5 tries, then the code is dead.
Set STORE_BACKEND=snowflake; run sql/grain_desk_setup.sql once first.
"""
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timezone

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from . import config

_conn = None
_lock = threading.RLock()          # one statement at a time on the shared connection
_write_lock = threading.Lock()
_seq_cache = [0.0, 0]              # [fetched_at, value]
SEQ_TTL = 4.0
OVERLAP = 60_000_000          # change counters are microsecond timestamps: a poll re-reads the last 60 s before its mark
_last_seq = [0]


class VersionConflict(Exception):
    def __init__(self, current):
        super().__init__(f'document changed (now version {current})')
        self.current = current


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _private_key():
    pem = (config.SNOWFLAKE_PRIVATE_KEY or '').replace('\\n', '\n').encode()
    if not pem:
        with open(config.SNOWFLAKE_PRIVATE_KEY_PATH, 'rb') as f:
            pem = f.read()
    key = serialization.load_pem_private_key(pem, password=None)
    return key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _connect():
    return snowflake.connector.connect(
        account=config.SNOWFLAKE_ACCOUNT, user=config.SNOWFLAKE_USER, private_key=_private_key(),
        role=config.SNOWFLAKE_ROLE or None, warehouse=config.SNOWFLAKE_WAREHOUSE or None,
        database=config.SNOWFLAKE_DATABASE, schema=config.SNOWFLAKE_SCHEMA,
        login_timeout=60, network_timeout=60)


def sql(q, params=None):
    """Run one statement; returns rows as dicts with lower-case keys (like sqlite3.Row)."""
    global _conn
    with _lock:
        for attempt in (0, 1):
            try:
                if _conn is None or _conn.is_closed():
                    _conn = _connect()
                cur = _conn.cursor(snowflake.connector.DictCursor)
                try:
                    cur.execute(q, params)
                    rows = cur.fetchall() if cur.description else []
                finally:
                    cur.close()
                return [{k.lower(): v for k, v in r.items()} for r in rows]
            except (snowflake.connector.errors.OperationalError, snowflake.connector.errors.DatabaseError) as e:
                msg = str(e).lower()
                if attempt == 0 and ('expired' in msg or 'session' in msg or 'connection' in msg or 'closed' in msg):
                    try:
                        _conn.close()
                    except Exception:
                        pass
                    _conn = None
                    continue
                raise


def init():
    """The tables are created by sql/grain_desk_setup.sql (run once as the setup role). Just check we can read."""
    sql('SELECT 1 FROM DOCS LIMIT 1')


def split(path):
    parts = path.strip('/').split('/')
    if len(parts) < 2 or len(parts) % 2:
        raise ValueError('a document path has an even number of segments')
    return '/'.join(parts[:-1]), parts[-1]


def current_seq():
    t = time.time()
    if t - _seq_cache[0] > SEQ_TTL:
        r = sql('SELECT COALESCE(MAX(SEQ),0) AS S FROM DOCS')
        _seq_cache[:] = [t, int(r[0]['s'])]
    return _seq_cache[1]


def _next_seq():
    """Strictly increasing within this process; across processes (the app, the droplet jobs) it follows the clock."""
    n = max(int(time.time() * 1_000_000), _last_seq[0] + 1)
    _last_seq[0] = n
    return n


def _bump():
    _seq_cache[0] = 0.0


def _row(r):
    if r is None:
        return None
    d = r['data']
    r['data'] = d if isinstance(d, str) else json.dumps(d)
    return r


# ---------- documents ----------

def get_doc(path):
    rows = sql('SELECT * FROM DOCS WHERE PATH=%s AND NOT DELETED', (path,))
    return _row(rows[0]) if rows else None


def list_docs(coll, since=0, broker_uid=None):
    q, a = 'SELECT * FROM DOCS WHERE COLL=%s AND NOT DELETED AND SEQ>%s', [coll, max(0, since - OVERLAP) if since else 0]
    if broker_uid is not None:
        q += ' AND BROKER_UID=%s'
        a.append(broker_uid)
    return [_row(r) for r in sql(q + ' ORDER BY ID', a)]


def list_ids(coll, broker_uid=None):
    q, a = 'SELECT ID FROM DOCS WHERE COLL=%s AND NOT DELETED', [coll]
    if broker_uid is not None:
        q += ' AND BROKER_UID=%s'
        a.append(broker_uid)
    return [r['id'] for r in sql(q, a)]


def put_doc(path, data, broker_uid=None, if_version=None):
    """Create or replace a document. Returns the new version."""
    coll, did = split(path)
    body = json.dumps(data, separators=(',', ':'))
    if len(body) > config.MAX_DOC_BYTES:
        raise ValueError('document too large')
    with _write_lock:
        seq = _next_seq()
        old = sql('SELECT VERSION, DELETED FROM DOCS WHERE PATH=%s', (path,))
        cur_v = 0 if (not old or old[0]['deleted']) else int(old[0]['version'])
        if if_version is not None and cur_v != if_version:
            raise VersionConflict(cur_v)
        v = (int(old[0]['version']) if old else 0) + 1
        sql('MERGE INTO DOCS d USING (SELECT %s AS PATH) s ON d.PATH=s.PATH '
            'WHEN MATCHED THEN UPDATE SET DATA=%s, VERSION=%s, SEQ=%s, BROKER_UID=%s, DELETED=FALSE, UPDATED_AT=CURRENT_TIMESTAMP() '
            'WHEN NOT MATCHED THEN INSERT (PATH,COLL,ID,DATA,VERSION,SEQ,BROKER_UID,DELETED,UPDATED_AT) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE,CURRENT_TIMESTAMP())',
            (path, body, v, seq, broker_uid, path, coll, did, body, v, seq, broker_uid))
        _bump()
        return v


def delete_doc(path):
    with _write_lock:
        seq = _next_seq()
        sql('UPDATE DOCS SET DELETED=TRUE, SEQ=%s, UPDATED_AT=CURRENT_TIMESTAMP() WHERE PATH=%s AND NOT DELETED', (seq, path))
        _bump()


# ---------- users, sign-in codes, sessions ----------

def new_id(prefix):
    return prefix + secrets.token_urlsafe(9).replace('-', 'x').replace('_', 'y')


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def add_user(email, name, role):
    uid = new_id('u_')
    sql('INSERT INTO USERS(ID,EMAIL,NAME,ROLE,ACTIVE,CREATED_AT) VALUES(%s,%s,%s,%s,TRUE,CURRENT_TIMESTAMP())',
        (uid, email.strip().lower(), name.strip(), role))
    return uid


def set_user(uid, **fields):
    allowed = {k.upper(): v for k, v in fields.items() if k in ('name', 'role', 'active', 'email')}
    if not allowed:
        return
    if 'ACTIVE' in allowed:
        allowed['ACTIVE'] = bool(allowed['ACTIVE'])
    sql('UPDATE USERS SET ' + ', '.join(f'{k}=%s' for k in allowed) + ' WHERE ID=%s', (*allowed.values(), uid))


def _user(r):
    if r:
        r['active'] = 1 if r['active'] else 0
    return r


def user_by_email(email):
    r = sql('SELECT * FROM USERS WHERE LOWER(EMAIL)=LOWER(%s)', (email.strip(),))
    return _user(r[0]) if r else None


def user_by_id(uid):
    r = sql('SELECT * FROM USERS WHERE ID=%s', (uid,))
    return _user(r[0]) if r else None


def users():
    return [_user(r) for r in sql('SELECT * FROM USERS ORDER BY ROLE, NAME')]


def broker_by_name(name):
    if not name:
        return None
    r = sql("SELECT * FROM USERS WHERE ROLE='broker' AND ACTIVE AND LOWER(NAME)=LOWER(%s)", (name.strip(),))
    return _user(r[0]) if r else None


def new_login_code(uid):
    """A fresh 6-digit code (any earlier one for this person is voided)."""
    code = f'{secrets.randbelow(10**6):06d}'
    sql('DELETE FROM LOGIN_CODES WHERE USER_ID=%s OR EXPIRES<%s', (uid, time.time()))
    sql('INSERT INTO LOGIN_CODES(HASH,USER_ID,EXPIRES,TRIES) VALUES(%s,%s,%s,0)',
        (_hash(uid + ':' + code), uid, time.time() + config.LOGIN_CODE_MINUTES * 60))
    return code


def use_login_code(uid, code):
    """True once if the code is right and fresh; five wrong tries kill it."""
    rows = sql('SELECT * FROM LOGIN_CODES WHERE USER_ID=%s', (uid,))
    if not rows or rows[0]['expires'] < time.time() or rows[0]['tries'] >= 5:
        return False
    if rows[0]['hash'] != _hash(uid + ':' + (code or '').strip()):
        sql('UPDATE LOGIN_CODES SET TRIES=TRIES+1 WHERE USER_ID=%s', (uid,))
        return False
    sql('DELETE FROM LOGIN_CODES WHERE USER_ID=%s', (uid,))
    return True


def new_session(uid):
    t = secrets.token_urlsafe(32)
    sql('DELETE FROM SESSIONS WHERE EXPIRES<%s', (time.time(),))
    sql('INSERT INTO SESSIONS(HASH,USER_ID,EXPIRES) VALUES(%s,%s,%s)', (_hash(t), uid, time.time() + config.SESSION_DAYS * 86400))
    sql('UPDATE USERS SET LAST_LOGIN=CURRENT_TIMESTAMP() WHERE ID=%s', (uid,))
    return t


def session_user(t):
    if not t:
        return None
    r = sql('SELECT u.* FROM SESSIONS s JOIN USERS u ON u.ID=s.USER_ID WHERE s.HASH=%s AND s.EXPIRES>%s AND u.ACTIVE', (_hash(t), time.time()))
    return _user(r[0]) if r else None


def end_session(t):
    sql('DELETE FROM SESSIONS WHERE HASH=%s', (_hash(t),))


def end_user_sessions(uid):
    sql('DELETE FROM SESSIONS WHERE USER_ID=%s', (uid,))


# ---------- uploaded contract photos ----------

def add_asset(owner_uid, content_type, blob):
    if len(blob) > config.MAX_ASSET_BYTES_SF:
        raise ValueError('photo too large')
    aid = secrets.token_hex(16)
    sql('INSERT INTO ASSETS(ID,OWNER_UID,CONTENT_TYPE,SIZE,DATA,CREATED_AT) VALUES(%s,%s,%s,%s,%s,CURRENT_TIMESTAMP())',
        (aid, owner_uid, content_type, len(blob), bytes(blob)))
    return aid


def get_asset(aid):
    r = sql('SELECT ID,OWNER_UID,CONTENT_TYPE,SIZE FROM ASSETS WHERE ID=%s', (aid,))
    return r[0] if r else None


def get_asset_bytes(aid):
    r = sql('SELECT DATA FROM ASSETS WHERE ID=%s', (aid,))
    return bytes(r[0]['data']) if r else None


def delete_asset(aid):
    sql('DELETE FROM ASSETS WHERE ID=%s', (aid,))
