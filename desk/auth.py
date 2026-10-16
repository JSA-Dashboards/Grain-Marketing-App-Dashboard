"""Sign-in for the Streamlit version: shared app password -> work email -> 6-digit emailed code.

  1. The app is public, so everything sits behind a shared password (APP_PASSWORD secret).
  2. A person must then have an active account (added by an admin on the Admin page) and prove they own the
     mailbox by entering the code we send to it.
  3. A successful sign-in sets a 30-day session cookie, so a refresh or a new day doesn't repeat all this.

Who someone is (and so which farms they may see) comes only from the account behind the code or cookie.
"""
import hmac
import os
import re
import time

import streamlit as st

from app import config, store

from . import branding, component, mailer

COOKIE = 'gd_session'
_sent = {}      # email -> time of the last code, in this server process (one code a minute per address)


def _dev_show_code():
    return os.environ.get('GRAIN_DEV_SHOW_CODE') == '1'


def from_cookie():
    """Sign in from a valid session cookie. Returns True when signed in."""
    if st.session_state.get('gd_user'):
        return True
    tok = st.context.cookies.get(COOKIE)
    tok = tok if isinstance(tok, str) else None
    u = store.session_user(tok) if tok else None
    if u:
        st.session_state.update(gd_user=u, gd_token=tok, gd_checked=time.time())
        return True
    if tok:
        st.session_state['gd_resume_bad'] = True    # a cookie for a session that has ended: wipe it
    return False


def try_resume():
    """Sign in from the session saved in the browser's localStorage (the cookie can fail to reach the server on
    Streamlit Cloud). Shows a brief loading state until the browser answers. Returns True once signed in."""
    ss = st.session_state
    # always mounted on the sign-in screen: it also wipes a dead token (signed out, expired or revoked)
    r = component.resume(clear=ss.get('gd_resume_bad', False))
    if ss.get('gd_resume_done'):
        return False
    if r is None:                       # the browser has not answered yet
        st.caption('Loading...')
        if st.button('Continue to sign in', type='tertiary'):
            ss['gd_resume_done'] = True
            st.rerun()
        st.stop()
    tok = r.get('tok') or ''
    ss['gd_resume_done'] = True
    u = store.session_user(tok) if tok else None
    if u:
        ss.update(gd_user=u, gd_token=tok, gd_checked=time.time())
        return True
    if tok:
        ss['gd_resume_bad'] = True      # an old or revoked token: draw the component again so the browser drops it now
        st.rerun()
    return False


def _gate_ok():
    pw = os.environ.get('APP_PASSWORD', '')
    return not pw or st.session_state.get('gd_gate')


def login_screen():
    """Draw the sign-in steps. Returns a {'value','days'} cookie dict the moment sign-in completes, else None."""
    branding.header('Grain Marketing Desk', 'Broker grain marketing &middot; John Stewart &amp; Associates')
    ss = st.session_state

    if not _gate_ok():
        with st.form('gate'):
            pw = st.text_input('Access password', type='password')
            if st.form_submit_button('Continue', type='primary'):
                time.sleep(1)       # slows guessing
                if hmac.compare_digest(pw.encode(), os.environ.get('APP_PASSWORD', '').encode()):
                    ss['gd_gate'] = True
                    st.rerun()
                st.error('That password is not right.')
        return None

    if not ss.get('gd_pending'):
        with st.form('email'):
            email = st.text_input('Work email', placeholder='name@jpsi.com').strip().lower()
            if st.form_submit_button('Email me a code', type='primary'):
                _request_code(email)
        return None

    pend = ss['gd_pending']
    st.info(f"We sent a 6-digit code to {pend['email']}. It expires in {config.LOGIN_CODE_MINUTES} minutes.")
    if pend.get('shown'):
        st.warning(f"Test mode, no email is configured. Your code is {pend['shown']}.")
    with st.form('code'):
        code = st.text_input('Code', max_chars=6)
        ok = st.form_submit_button('Sign in', type='primary')
    if st.button('Use a different email', type='tertiary'):
        ss.pop('gd_pending')
        st.rerun()
    if ok:
        time.sleep(0.5)
        if store.use_login_code(pend['uid'], code):
            u = store.user_by_id(pend['uid'])
            tok = store.new_session(u['id'])
            ss.pop('gd_pending')
            ss.update(gd_user=u, gd_token=tok, gd_checked=time.time())
            return {'value': tok, 'days': config.SESSION_DAYS}
        st.error('That code is not right or has expired.')
    return None


def _request_code(email):
    ss = st.session_state
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        st.error('Enter your work email address.')
        return
    # the same message whether or not the address has an account, so the form can't be used to look people up
    ok_msg = None
    u = store.user_by_email(email)
    if u and u['active'] and _sent.get(email, 0) < time.time() - 60:
        _sent[email] = time.time()
        code = store.new_login_code(u['id'])
        try:
            sent = mailer.send_code(u['email'], u['name'], code, config.LOGIN_CODE_MINUTES)
        except Exception:
            import logging
            logging.getLogger('grain').exception('could not send the sign-in code to %s', email)
            st.error("We couldn't send the email. Try again in a minute, or ask Megan.")
            return
        ok_msg = code if (not sent and _dev_show_code()) else None
        ss['gd_pending'] = {'uid': u['id'], 'email': u['email'], 'shown': ok_msg}
    else:
        ss['gd_pending'] = {'uid': 'none', 'email': email, 'shown': None}
    st.rerun()
