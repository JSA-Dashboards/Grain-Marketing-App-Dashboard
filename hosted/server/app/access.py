"""Who may read and write which documents. Enforced here on the server, not in the page.

  admin   reads and writes everything (Megan and JSA staff).
  broker  reads shared market data; reads and writes only the farms assigned to them.
          A farm a broker creates is theirs; its broker name is always theirs.
Farmers (planned for the end of the year) would get their own role here.
"""
from . import store

MARKET = 'market'
FARMS = 'farms'


def can_read(user, coll, row):
    if user['role'] == 'admin':
        return True
    if coll == MARKET:
        return True
    if coll == FARMS:
        return row is not None and row['broker_uid'] == user['id']
    return False


def can_write(user, coll, row):
    if user['role'] == 'admin':
        return True
    if coll == FARMS:
        return row is None or row['broker_uid'] == user['id']   # a new farm, or one of theirs
    return False          # market data, Intel posts and program deadlines are admin-only


def collection_scope(user, coll):
    """None = every document; a user id = only that broker's farms; False = nothing."""
    if user['role'] == 'admin' or coll == MARKET:
        return None
    if coll == FARMS:
        return user['id']
    return False


def farm_broker(user, data):
    """Decide which broker a farm belongs to when it is saved.
    A broker's own saves always stay theirs (and carry their name). An admin assigns a farm by
    typing the broker's name in Farm details; it links to the broker account with that name."""
    if user['role'] == 'broker':
        data['broker'] = user['name']
        return user['id']
    b = store.broker_by_name(data.get('broker', ''))
    return b['id'] if b else None
