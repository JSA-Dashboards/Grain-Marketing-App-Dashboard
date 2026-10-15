"""JSA look for the Streamlit-owned screens (sign-in, People), matching the CFTC COT dashboard."""
import streamlit as st

LOGO_URL = 'https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png'
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@500;600&family=Source+Sans+3:wght@400;600;700&display=swap');
html, body, p, span, div, label, button, td, th {font-family:'Source Sans 3','Source Sans Pro',sans-serif}
.dash-header {display:flex;align-items:center;gap:18px;padding-bottom:10px;border-bottom:1px solid #d7e2dc;margin-bottom:14px}
.dash-header img {height:40px}
.dash-header .t b {font-family:'EB Garamond',Georgia,serif;font-size:1.7rem;color:#32373c;font-weight:600}
.dash-header .t div {font-size:.85rem;color:#5f7267}
@media (max-width:640px) {.dash-header img {display:none}}
div[data-testid="stForm"] {background:#fff;border:1px solid #d7e2dc;border-top:3px solid #5e7164;border-radius:10px}
</style>
"""


def header(title, subtitle):
    st.html(_CSS + f'<div class="dash-header"><img src="{LOGO_URL}" alt="John Stewart &amp; Associates">'
                   f'<div class="t"><b>{title}</b><div>{subtitle}</div></div></div>')
