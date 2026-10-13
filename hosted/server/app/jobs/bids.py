"""Nightly cash bids update -> market/bids (port of sql/sync-bids.ps1).

Latest snapshot per elevator/processor location over the last 7 days, with map coordinates;
keeps the two nearest delivery months per location and grain (and wheat class)."""
import re
from datetime import date, datetime, timezone

from .. import store
from .snow import query

MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12}
WHEATS = {'Wheat', 'Hard Red Spring', 'Hard Red Winter', 'Soft Red Winter', 'Soft White'}


def grain_code(g):
    return 'C' if g == 'Corn' else 'S' if g == 'Soybeans' else 'W' if g in WHEATS else None


def year_month(r, cur):
    """'Oct 2026', 'OCT 26', 'March 2027', "Nov '26" -> year*12+month; spot rows -> this month."""
    if int(r['IS_SPOT'] or 0) == 1:
        return cur
    m = re.search(r"([A-Za-z]{3,9})\.?\s*'?(\d{2,4})", str(r['DELIVERY_MONTH'] or ''))
    if not m:
        return None
    w = m.group(1).lower()
    mon = MONTHS.get(w[:4]) or MONTHS.get(w[:3])
    if not mon:
        return None
    y = int(m.group(2))
    return (y + 2000 if y < 100 else y) * 12 + mon


def run():
    rows = query("""
WITH s AS (
  SELECT ID, PROVIDER, LOCATION, TIMESTAMP FROM JSA.BASIS_TRACKER.SNAPSHOTS
  WHERE CREATED_AT >= DATEADD(day, -7, CURRENT_TIMESTAMP())
  QUALIFY ROW_NUMBER() OVER (PARTITION BY PROVIDER, LOCATION ORDER BY ID DESC) = 1)
SELECT s.PROVIDER, s.LOCATION, LEFT(s.TIMESTAMP, 10) AS D, m.STATE, m.LAT, m.LON,
       COALESCE(g.CANONICAL_GRAIN, r.GRAIN) AS GRAIN, g.WHEAT_CLASS, r.DELIVERY_MONTH, r.FUTURES_SYMBOL, r.BASIS_CENTS, r.IS_SPOT
FROM s
JOIN JSA.BASIS_TRACKER.SNAPSHOT_ROWS r ON r.SNAPSHOT_ID = s.ID
JOIN JSA.BASIS_TRACKER.LOCATION_META m ON m.PROVIDER = s.PROVIDER AND m.LOCATION = s.LOCATION
LEFT JOIN JSA.BASIS_TRACKER.GRAIN_MAP g ON g.RAW_GRAIN = r.GRAIN
WHERE m.LAT IS NOT NULL AND m.LON IS NOT NULL AND r.BASIS_CENTS IS NOT NULL AND r.FUTURES_SYMBOL IS NOT NULL""")
    if not rows:
        raise RuntimeError('No bid rows returned')
    t = date.today()
    cur = t.year * 12 + t.month
    loc_index, locs, by_key = {}, [], {}
    for r in rows:
        code = grain_code(str(r['GRAIN']))
        ym = year_month(r, cur) if code else None
        if not ym or ym < cur:
            continue
        lk = f"{r['PROVIDER']}|{r['LOCATION']}"
        if lk not in loc_index:
            loc_index[lk] = len(locs)
            locs.append([str(r['PROVIDER']), str(r['LOCATION']), str(r['STATE'] or ''), round(float(r['LAT']), 4), round(float(r['LON']), 4), str(r['D'])])
        cls = str(r['WHEAT_CLASS'] or '') if code == 'W' else ''
        by_key.setdefault((loc_index[lk], code, cls), []).append((ym, str(r['FUTURES_SYMBOL']), int(r['BASIS_CENTS'])))
    bids = []
    for (li, code, cls), items in by_key.items():
        seen = set()
        for ym, sym, basis in sorted(items):
            if ym in seen:
                continue
            seen.add(ym)
            y, mo = (ym - 1) // 12, ym - ((ym - 1) // 12) * 12
            bids.append([li, code, cls, f'{y}-{mo:02d}', sym, basis])
            if len(seen) >= 2:
                break
    doc = {'asOf': max(str(r['D']) for r in rows), 'syncedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'source': 'JSA.BASIS_TRACKER',
           # locs: [provider, location, state, lat, lon, bid date]; bids: [locIndex, grain C|S|W, wheat class, delivery YYYY-MM, futures symbol, basis cents]
           'locs': locs, 'bids': bids}
    store.put_doc('market/bids', doc)
    return f"bids asOf={doc['asOf']} locations={len(locs)} bids={len(bids)}"
