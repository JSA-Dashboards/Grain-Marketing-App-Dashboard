"""Nightly futures update -> market/futures (port of sql/sync-futures.ps1).

Prices, previous close and a 20-day trend for CBOT corn/soybeans/wheat and KC wheat outrights,
the CME Feeder Cattle Index, and RMA crop insurance prices for corn and soybeans (projected =
February average of December corn / November soybean settles; harvest = October average)."""
from datetime import date, datetime, timezone

from .. import store
from .snow import query


def run():
    rows = query("""
WITH d AS (SELECT DISTINCT DATE FROM JSA.BASIS_TRACKER.FUTURES_PRICES
           WHERE LEFT(SYMBOL,2) IN ('ZC','ZS','ZW','KE') ORDER BY DATE DESC LIMIT 25)
SELECT DATE, SYMBOL, PRICE_CENTS FROM JSA.BASIS_TRACKER.FUTURES_PRICES
WHERE DATE IN (SELECT DATE FROM d) AND LEFT(SYMBOL,2) IN ('ZC','ZS','ZW','KE') AND LENGTH(SYMBOL) = 5
QUALIFY ROW_NUMBER() OVER (PARTITION BY DATE, SYMBOL ORDER BY CAPTURED_AT DESC) = 1""")
    if not rows:
        raise RuntimeError('No futures rows returned')
    dates = sorted({str(r['DATE']) for r in rows})
    as_of, prev_date = dates[-1], (dates[-2] if len(dates) > 1 else None)
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r['SYMBOL'], []).append(r)
    prices, prev, hist = {}, {}, {}
    for sym in sorted(by_sym):
        series = sorted(by_sym[sym], key=lambda r: str(r['DATE']))
        last = next((r for r in series if str(r['DATE']) == as_of), None)
        if not last:
            continue          # contract no longer trading
        prices[sym] = round(float(last['PRICE_CENTS']) / 100, 4)
        p = next((r for r in series if str(r['DATE']) == prev_date), None)
        if p:
            prev[sym] = round(float(p['PRICE_CENTS']) / 100, 4)
        hist[sym] = [round(float(r['PRICE_CENTS']) / 100, 4) for r in series[-20:]]

    fci = None
    f = query('SELECT REPORT_DATE, FCI_VALUE FROM JSA.CME_FEEDER_CATTLE.FCI_DAILY WHERE FCI_VALUE IS NOT NULL ORDER BY REPORT_DATE DESC LIMIT 20')
    if f:
        f = sorted(f, key=lambda r: str(r['REPORT_DATE']))
        fci = {'asOf': str(f[-1]['REPORT_DATE']), 'value': round(float(f[-1]['FCI_VALUE']), 3),
               'prev': round(float(f[-2]['FCI_VALUE']), 3) if len(f) > 1 else None,
               'hist': [round(float(r['FCI_VALUE']), 3) for r in f]}

    doc = {'asOf': as_of, 'prevDate': prev_date, 'syncedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'source': 'JSA.BASIS_TRACKER.FUTURES_PRICES', 'prices': prices, 'prev': prev, 'hist': hist, 'fci': fci, 'rma': rma()}
    store.put_doc('market/futures', doc)
    return f"futures asOf={as_of} contracts={len(prices)}"


def rma():
    today = date.today()
    yrs = range(today.year - 1, today.year + 2)
    syms = ','.join(f"'ZCZ{y % 100:02d}','ZSX{y % 100:02d}'" for y in yrs)
    rows = query(f"""
WITH s AS (SELECT DATE, SYMBOL, PRICE_CENTS FROM JSA.BASIS_TRACKER.FUTURES_PRICES WHERE SYMBOL IN ({syms})
           QUALIFY ROW_NUMBER() OVER (PARTITION BY DATE, SYMBOL ORDER BY CAPTURED_AT DESC) = 1)
SELECT SYMBOL, 2000 + RIGHT(SYMBOL,2)::INT AS YR,
  AVG(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-02', PRICE_CENTS, NULL)) / 100 AS PROJ,
  COUNT(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-02', 1, NULL)) AS PROJ_DAYS,
  AVG(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-10', PRICE_CENTS, NULL)) / 100 AS HARV,
  COUNT(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-10', 1, NULL)) AS HARV_DAYS
FROM s GROUP BY SYMBOL""")
    out, t = {}, today.isoformat()
    for r in rows:
        y = str(r['YR'])
        crop = 'corn' if r['SYMBOL'].startswith('ZC') else 'soybeans'
        out.setdefault(y, {})[crop] = {
            'symbol': r['SYMBOL'],
            'proj': round(float(r['PROJ']), 2) if r['PROJ'] is not None else None, 'projDays': int(r['PROJ_DAYS']), 'projFinal': t >= f'{y}-03-01',
            'harv': round(float(r['HARV']), 2) if r['HARV'] is not None else None, 'harvDays': int(r['HARV_DAYS']), 'harvFinal': t >= f'{y}-11-01'}
    return out
