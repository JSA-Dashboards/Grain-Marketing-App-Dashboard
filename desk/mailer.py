"""Sign-in code emails through Microsoft Graph (app-only OAuth), the same route JSA's Basis Tracker uses.

Needs four settings (Streamlit secrets): GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_SENDER
(a mailbox the app registration may send as; Mail.Send, Application permission).
With none of them set, send_code() returns False and the caller can show the code on screen when
GRAIN_DEV_SHOW_CODE is on (local testing only).
"""
import os
import time

import requests

_tok = {'v': None, 'exp': 0.0}


def configured():
    return all(os.environ.get(k) for k in ('GRAPH_TENANT_ID', 'GRAPH_CLIENT_ID', 'GRAPH_CLIENT_SECRET', 'GRAPH_SENDER'))


def _token():
    if _tok['v'] and _tok['exp'] > time.time() + 60:
        return _tok['v']
    r = requests.post(f"https://login.microsoftonline.com/{os.environ['GRAPH_TENANT_ID']}/oauth2/v2.0/token", timeout=20, data={
        'grant_type': 'client_credentials', 'client_id': os.environ['GRAPH_CLIENT_ID'],
        'client_secret': os.environ['GRAPH_CLIENT_SECRET'], 'scope': 'https://graph.microsoft.com/.default'})
    r.raise_for_status()
    j = r.json()
    _tok.update(v=j['access_token'], exp=time.time() + int(j.get('expires_in', 3000)))
    return _tok['v']


def send_code(to_email, name, code, minutes):
    """Email the code. Returns True if sent, False if Graph isn't configured. Raises if Graph refuses."""
    if not configured():
        return False
    html = (f"<p>Hi {name or 'there'},</p><p>Your Grain Marketing Desk sign-in code is:</p>"
            f"<p style='font-size:28px;letter-spacing:6px;font-weight:700'>{code}</p>"
            f"<p>It expires in {minutes} minutes. If you didn't ask to sign in, ignore this email.</p>")
    r = requests.post(f"https://graph.microsoft.com/v1.0/users/{os.environ['GRAPH_SENDER']}/sendMail", timeout=20,
                      headers={'Authorization': 'Bearer ' + _token()},
                      json={'message': {'subject': f'Your Grain Marketing Desk code: {code}',
                                        'body': {'contentType': 'HTML', 'content': html},
                                        'toRecipients': [{'emailAddress': {'address': to_email}}]},
                            'saveToSentItems': False})
    r.raise_for_status()
    return True
