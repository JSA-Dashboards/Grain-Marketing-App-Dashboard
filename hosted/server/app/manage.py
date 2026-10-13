"""Admin commands, run on the server (see hosted/README.md):

  python -m app.manage add-user  EMAIL "NAME" admin|broker
  python -m app.manage users
  python -m app.manage set-user  EMAIL [--name "NAME"] [--role admin|broker] [--active 0|1]
  python -m app.manage import    FOLDER      # farms and market data saved from the Claude app
  python -m app.manage relink                # re-match every farm to its broker by name

A broker's name must match the "Broker" written on their farms (Farm details) for those farms
to show up for them.
"""
import argparse
import json
import os
import sys

from . import access, store


def cmd_add_user(a):
    if store.user_by_email(a.email):
        sys.exit(f'{a.email} already has an account')
    store.add_user(a.email, a.name, a.role)
    print(f'added {a.role} {a.name} <{a.email}>')
    if a.role == 'broker':
        cmd_relink(a)


def cmd_users(_):
    for u in store.users():
        n = len(store.list_ids('farms', u['id'])) if u['role'] == 'broker' else '-'
        print(f"{u['role']:7} {'active' if u['active'] else 'OFF':6} {u['name']:28} {u['email']:36} farms={n} last={u['last_login'] or 'never'}")


def cmd_set_user(a):
    u = store.user_by_email(a.email) or sys.exit(f'no account for {a.email}')
    fields = {k: v for k, v in (('name', a.name), ('role', a.role), ('active', a.active)) if v is not None}
    store.set_user(u['id'], **fields)
    if a.active == 0:
        store.end_user_sessions(u['id'])
    print('updated', a.email)
    cmd_relink(a)


def cmd_relink(_):
    """Point every farm at the broker account whose name matches its Broker field."""
    n = 0
    for r in store.list_docs('farms'):
        data = json.loads(r['data'])
        b = store.broker_by_name(data.get('broker', ''))
        uid = b['id'] if b else None
        if uid != r['broker_uid']:
            store.put_doc(r['path'], data, broker_uid=uid)
            n += 1
    print(f'relinked {n} farm(s)')


def cmd_import(a):
    """Load JSON files saved from the Claude app (ArtifactData list with out_dir): <folder>/farms/*.json, <folder>/market/*.json."""
    n = 0
    for coll in ('farms', 'market'):
        d = os.path.join(a.folder, coll)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.json'):
                continue
            with open(os.path.join(d, fn), encoding='utf-8') as f:
                j = json.load(f)
            data = j.get('data', j) if isinstance(j, dict) else None
            if not isinstance(data, dict):
                continue
            did = j.get('id') or fn[:-5]
            b = store.broker_by_name(data.get('broker', '')) if coll == access.FARMS else None
            store.put_doc(f'{coll}/{did}', data, broker_uid=b['id'] if b else None)
            n += 1
    print(f'imported {n} document(s)')


def main():
    store.init()
    p = argparse.ArgumentParser(prog='python -m app.manage')
    sp = p.add_subparsers(dest='cmd', required=True)
    x = sp.add_parser('add-user'); x.add_argument('email'); x.add_argument('name'); x.add_argument('role', choices=['admin', 'broker']); x.set_defaults(fn=cmd_add_user)
    sp.add_parser('users').set_defaults(fn=cmd_users)
    x = sp.add_parser('set-user'); x.add_argument('email'); x.add_argument('--name'); x.add_argument('--role', choices=['admin', 'broker'])
    x.add_argument('--active', type=int, choices=[0, 1]); x.set_defaults(fn=cmd_set_user)
    x = sp.add_parser('import'); x.add_argument('folder'); x.set_defaults(fn=cmd_import)
    sp.add_parser('relink').set_defaults(fn=cmd_relink)
    a = p.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
