"""The Grain Marketing Desk page, embedded in Streamlit.

grain-desk.html runs unchanged inside an iframe. desk/shim.js (injected into the page) gives it the
`window.claude` interface; every call goes to Python through this component:

  page -> parent.__gdRpc(op, args) -> queued, sent as ONE trigger value {batch:[...]} (one at a time)
  Python (callback) -> desk.ops.handle(signed-in user, request) -> responses stored in session state
  Python -> component data {responses} -> the page's promises resolve

The signed-in person comes from session state set at sign-in, never from the page.
"""
import json
import pathlib
import time

import streamlit as st

from . import ops

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEY = 'gd_desk'


def _page_with_shim():
    page = (ROOT / 'grain-desk.html').read_text(encoding='utf-8')
    shim = '<script>' + (ROOT / 'desk' / 'shim.js').read_text(encoding='utf-8') + '</script>'
    i = page.find('<script>')      # the shim must run before the app's own script
    return page[:i] + shim + page[i:] if i >= 0 else shim + page


_JS = """
const PAGE = %s;
const states = new WeakMap();

function start(el, setTrigger) {
  const st = { queue: [], inflight: null, n: 0, timer: null, done: new Set(), setTrigger };
  const frame = document.createElement('iframe');
  frame.className = 'desk';
  frame.title = 'Grain Marketing Desk';
  frame.setAttribute('allow', 'clipboard-write; camera; fullscreen');
  frame.srcdoc = PAGE;
  el.appendChild(frame);

  function send(attempt) {
    const m = st.inflight;
    if (!m || m.bid !== st.bid) return;
    clearTimeout(st.retry);
    st.setTrigger('req', { bid: st.bid, attempt, batch: [...m.values()].map(b => ({ id: b.id, op: b.op, args: b.args })) });
    // Streamlit can drop a trigger set right after an update: ask again (the server answers a repeat from its stored result)
    st.retry = setTimeout(() => { if (st.inflight && st.inflight.bid === st.bid && attempt < 6) send(attempt + 1); }, 2500);
  }

  function pump() {
    if (st.inflight || !st.queue.length) return;
    const batch = st.queue.splice(0);
    st.inflight = new Map(batch.map(b => [b.id, b]));
    st.bid = st.inflight.bid = 'b' + Math.random().toString(36).slice(2, 12);
    clearTimeout(st.timer);
    st.timer = setTimeout(() => {          // the server never answered: fail these calls, carry on
      const m = st.inflight; st.inflight = null; clearTimeout(st.retry);
      if (m) m.forEach(b => b.reject({ code: 'unavailable', message: 'The server did not answer. Try again.' }));
      pump();
    }, 180000);
    send(0);
  }

  window.__gdRpc = (op, args) => new Promise((resolve, reject) => {
    st.queue.push({ id: 'r' + Math.random().toString(36).slice(2, 12), op, args, resolve, reject });
    pump();
  });
  window.__gdSignedOut = () => {
    document.cookie = 'gd_session=; Max-Age=0; Path=/; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
    location.reload();
  };
  st.pump = pump;
  states.set(el, st);
  return st;
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const st = states.get(parentElement) || start(parentElement, setTriggerValue);
  st.setTrigger = setTriggerValue;
  if (data && data.cookie) {
    document.cookie = 'gd_session=' + data.cookie.value + '; Max-Age=' + data.cookie.days * 86400 + '; Path=/; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
  }
  const rs = data && data.responses;
  if (rs && st.inflight) {
    const m = st.inflight;
    if ([...m.keys()].some(id => rs[id])) {
      st.inflight = null; clearTimeout(st.timer); clearTimeout(st.retry);
      m.forEach((b, id) => {
        const r = rs[id];
        if (!r) b.reject({ code: 'unavailable', message: 'No answer from the server.' });
        else b.resolve(r);
      });
      if (data.signedOut) window.__gdSignedOut();
    }
  }
  st.pump();   // anything queued while that batch was out goes now
}
"""

_CSS = """
.desk { display:block; width:100%; height:100vh; height:100dvh; border:0; background:#D6E5CF; }
"""

_DESK = st.components.v2.component('grain_desk', html='', css=_CSS, js=_JS % json.dumps(_page_with_shim()))


def _on_req():
    """Runs before the script body: answer the page's batch of calls, keep the answers for the next render."""
    req = st.session_state.get(KEY, {}).get('req') if isinstance(st.session_state.get(KEY), dict) else getattr(st.session_state.get(KEY), 'req', None)
    if req and st.session_state.get('gd_done') == req.get('bid'):
        return                      # a repeat of a batch already answered: keep the stored answer, don't run it twice
    user = st.session_state.get('gd_user')
    out = {}
    signed_out = False
    if req and user:
        # a deactivated person is cut off within two minutes, not at the end of their 30-day session
        from app import store
        if time.time() - st.session_state.get('gd_checked', 0) > 120:
            fresh = store.user_by_id(user['id'])
            st.session_state['gd_checked'] = time.time()
            if not fresh or not fresh['active']:
                user = None
            else:
                st.session_state['gd_user'] = user = fresh
        for r in req['batch']:
            if user is None:
                out[r['id']] = {'id': r['id'], 'ok': False, 'code': 'revoked', 'message': 'Signed out'}
                signed_out = True
            elif r.get('op') == 'logout':
                tok = st.session_state.pop('gd_token', None)
                if tok:
                    store.end_session(tok)
                st.session_state.pop('gd_user', None)
                out[r['id']] = {'id': r['id'], 'ok': True, 'result': {}}
                signed_out = True
            else:
                out[r['id']] = ops.handle(user, r)
    elif req:
        for r in req['batch']:
            out[r['id']] = {'id': r['id'], 'ok': False, 'code': 'revoked', 'message': 'Signed out'}
        signed_out = True
    st.session_state['gd_resp'] = {'responses': out, 'signedOut': signed_out}
    if req:
        st.session_state['gd_done'] = req.get('bid')


def render(cookie=None):
    """Mount the page. `cookie` = {'value': token, 'days': n} on the render right after sign-in."""
    resp = st.session_state.get('gd_resp', {})
    _DESK(key=KEY, data={'responses': resp.get('responses', {}), 'signedOut': resp.get('signedOut', False), 'cookie': cookie},
          on_req_change=_on_req, height='content', width='stretch')
