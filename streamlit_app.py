"""Grain Marketing Desk on Streamlit Community Cloud.

The same page as the Claude and Docker versions (grain-desk.html), signed in with a shared password plus an
emailed code, storing everything in Snowflake (JSA.GRAIN_DESK). See STREAMLIT.md.
"""
import os
import sys

import streamlit as st

# Streamlit secrets -> environment, so the shared server modules (hosted/server/app) read them like on the server
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)):
            os.environ.setdefault(_k, str(_v))
except Exception:
    pass
os.environ.setdefault('STORE_BACKEND', 'snowflake')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hosted', 'server'))

st.set_page_config(page_title='Grain Marketing Desk', page_icon=':material/grass:', layout='wide', initial_sidebar_state='collapsed')

from desk import admin, auth, component  # noqa: E402  (after the environment is set)


def desk_page():
    # the page needs the whole window: no Streamlit padding around it. Brokers also lose Streamlit's top bar;
    # admins keep it, because it holds the Desk | People menu (it overlays the top, so leave room for it)
    u = st.session_state.get('gd_user')
    admin = bool(u) and u['role'] == 'admin'      # u is a dict on Snowflake, a sqlite3.Row locally: index, don't .get
    css = ('.stMainBlockContainer{padding:3.75rem 0 0 0!important;max-width:100%!important}' if admin else
           '.stMainBlockContainer{padding:0!important;max-width:100%!important}header[data-testid="stHeader"]{display:none}')
    st.html(f'<style>{css}</style>')
    cookie = st.session_state.pop('gd_cookie', None)
    component.render(cookie)


def login_page():
    if auth.try_resume():
        st.rerun()
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        cookie = auth.login_screen()
    if cookie:
        st.session_state['gd_cookie'] = cookie
        st.rerun()


if auth.from_cookie():
    if st.session_state['gd_user']['role'] == 'admin':
        nav = st.navigation([st.Page(desk_page, title='Desk', icon=':material/grass:', default=True),
                             st.Page(admin.page, title='People', icon=':material/group:', url_path='people')], position='top')
    else:
        nav = st.navigation([st.Page(desk_page, title='Desk', default=True)], position='hidden')
    nav.run()
else:
    st.navigation([st.Page(login_page, title='Sign in', default=True)], position='hidden').run()
