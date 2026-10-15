"""Admin page: who can sign in. Replaces the Docker version's `manage.py` command line."""
import re

import streamlit as st

from app import store

from . import branding


def page():
    branding.header('People', 'Who can sign in &middot; John Stewart &amp; Associates')
    u = st.session_state.get('gd_user')
    if not u or u['role'] != 'admin':
        st.error('Admins only.')
        return
    st.caption("A broker's farms link to their account by name: the name here must match the Broker field in Farm details.")

    rows = store.users()
    farms = {r['id']: len(store.list_ids('farms', r['id'])) for r in rows if r['role'] == 'broker'}
    st.dataframe(
        [{'Name': r['name'], 'Email': r['email'], 'Role': r['role'], 'Active': bool(r['active']),
          'Farms': str(farms.get(r['id'], '-')), 'Last sign-in': str(r['last_login'] or '')[:16]} for r in rows],
        hide_index=True, width='stretch')

    with st.form('add_user', clear_on_submit=True):
        st.subheader('Add a person')
        name = st.text_input('Full name')
        email = st.text_input('Work email').strip().lower()
        role = st.segmented_control('Role', ['broker', 'admin'], default='broker')
        if st.form_submit_button('Add', type='primary'):
            if not name.strip() or not re.fullmatch(r'[^@\s]+@jpsi\.com', email):
                st.error('Enter a name and a @jpsi.com email.')
            elif store.user_by_email(email):
                st.error('That email already has an account.')
            else:
                store.add_user(email, name, role or 'broker')
                st.success(f'Added {name}.')
                st.rerun()

    st.subheader('Turn someone on or off')
    pick = st.selectbox('Person', [r['email'] for r in rows], key='gd_admin_pick')
    target = next((r for r in rows if r['email'] == pick), None)
    if target:
        on = st.toggle('Can sign in', value=bool(target['active']), key=f"gd_on_{target['id']}")
        if on != bool(target['active']):
            if target['id'] == u['id'] and not on:
                st.error("You can't turn yourself off.")
            else:
                store.set_user(target['id'], active=on)
                if not on:
                    store.end_user_sessions(target['id'])
                st.rerun()
