"""Morning weather update -> market/wx_<lat>_<lon> per farm location (port of sql/sync-weather.ps1).

Reads only each farm's bidHome lat/lon, then pulls the National Weather Service forecast
(api.weather.gov: free, public; only the ZIP-center coordinates are sent)."""
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from .. import store

HEADERS = {'User-Agent': 'GrainMarketingDesk/1.0 (farm weather)', 'Accept': 'application/geo+json'}


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def _mph(s):
    m = re.search(r'(\d+)\s*(?:to\s*(\d+))?\s*mph', s or '')
    return int(m.group(2) or m.group(1)) if m else None


def _pop(p):
    v = (p.get('probabilityOfPrecipitation') or {}).get('value')
    return int(v) if v is not None else 0


# Soil: Iowa State University Soil Moisture Network (Iowa Environmental Mesonet, public). The nearest field station
# within 75 miles gives 4-inch soil temperature (now + 10 days of daily avg/low/high); moisture at 12/24/50 inches comes
# from the nearest station with sane moisture readings. Same rules as sql/sync-weather.ps1.
IEM = 'https://mesonet.agron.iastate.edu'


def _miles(a1, o1, a2, o2):
    from math import asin, cos, radians, sin, sqrt
    dl, dn = radians(a2 - a1), radians(o2 - o1)
    h = sin(dl / 2) ** 2 + cos(radians(a1)) * cos(radians(a2)) * sin(dn / 2) ** 2
    return 3958.8 * 2 * asin(sqrt(h))


def _num(v):
    try:
        return round(float(v), 1) if v not in (None, '', 'M') else None
    except ValueError:
        return None


def _vwc(v):
    x = _num(v)
    return x if x is not None and 5 <= x <= 60 else None       # 1% or 70% is a sensor fault


def soil_stations():
    """Today's reporting field stations (no vineyards/orchards/horticulture plots, no 4-inch reading 12°F off the middle)."""
    try:
        cur = requests.get(f'{IEM}/api/1/currents.json?network=ISUSM', timeout=60).json()['data']
    except Exception:
        return [], {}
    ok = [c for c in cur if c.get('lat') is not None and c.get('c1tmpf') is not None and not re.search('Vineyard|Orchard|Horticulture', c.get('name') or '')]
    t4 = sorted(float(c['c1tmpf']) for c in ok)
    mid = t4[len(t4) // 2] if t4 else 0
    return [c for c in ok if abs(float(c['c1tmpf']) - mid) <= 12], {c['station']: c for c in cur}


def _daily(sid):
    sts = (datetime.now(timezone.utc) - timedelta(days=11)).strftime('%Y-%m-%dT00:00Z')
    ets = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%dT00:00Z')
    txt = requests.get(f'{IEM}/cgi-bin/request/isusm.py', timeout=60, params={'mode': 'daily', 'station': sid, 'sts': sts, 'ets': ets,
                       'vars': 'soil04t,soil04tn,soil04tx,soil12vwc,soil24vwc,soil50vwc', 'format': 'comma', 'missing': 'M'}).text
    rows = {}
    for line in txt.splitlines()[1:]:
        f = line.strip().split(',')
        if len(f) >= 8:
            rows[f[1]] = f
    return rows


def soil_for(lat, lon, stations, now):
    if not stations:
        return None
    near = lambda lst: min(((c, _miles(lat, lon, c['lat'], c['lon'])) for c in lst), key=lambda t: t[1], default=(None, 1e9))
    ts, tmi = near(stations)
    if not ts or tmi > 75:
        return None
    ms, mmi = near([c for c in stations if _vwc(c.get('c2smv')) is not None])
    if mmi > 75:
        ms = None
    tr = _daily(ts['station'])
    mr = (tr if ms['station'] == ts['station'] else _daily(ms['station'])) if ms else {}
    days = []
    for d in sorted(tr):
        f, m = tr[d], mr.get(d)
        days.append({'d': d, 't4': _num(f[2]), 't4n': _num(f[3]), 't4x': _num(f[4]),
                     'v12': _vwc(m[5]) if m else None, 'v24': _vwc(m[6]) if m else None, 'v50': _vwc(m[7]) if m else None})
    c, cm = now.get(ts['station']), ms
    return {'station': ts['station'], 'name': ts['name'], 'miles': round(tmi),
            'mStation': cm['station'] if cm else None, 'mName': cm['name'] if cm else None, 'mMiles': round(mmi) if cm else None,
            'source': 'Iowa State University Soil Moisture Network (Iowa Environmental Mesonet)',
            'now': {'valid': c.get('utc_valid') if c else None, 't4': _num(c.get('c1tmpf')) if c else None, 't12': _num(c.get('c2tmpf')) if c else None,
                    'v12': _vwc(cm.get('c2smv')) if cm else None, 'v24': _vwc(cm.get('c3smv')) if cm else None, 'v50': _vwc(cm.get('c4smv')) if cm else None},
            'days': days}


def locations():
    seen = {}
    for r in store.list_docs('farms'):
        h = (json.loads(r['data']) or {}).get('bidHome') or {}
        if h.get('lat') is None or h.get('lon') is None:
            continue
        lat, lon = float(h['lat']), float(h['lon'])
        key = f'wx_{lat:.2f}_{lon:.2f}'          # same key the app builds with toFixed(2)
        seen.setdefault(key, (lat, lon))
    return seen


def one(key, lat, lon, soil_ctx=([], {})):
    p = _get(f'https://api.weather.gov/points/{lat},{lon}')['properties']
    fc, hr = _get(p['forecast']), _get(p['forecastHourly'])
    grid = alerts = None
    try:
        grid = _get(p['forecastGridData'])
    except Exception:
        pass
    try:
        alerts = _get(f'https://api.weather.gov/alerts/active?point={lat},{lon}')
    except Exception:
        pass
    periods = [{'name': x['name'], 'start': x['startTime'], 'day': bool(x['isDaytime']), 'temp': x['temperature'], 'pop': _pop(x),
                'wind': x['windSpeed'], 'windMax': _mph(x['windSpeed']), 'dir': x['windDirection'], 'short': x['shortForecast'],
                'detail': x['detailedForecast']} for x in fc['properties']['periods']]
    # 72 hours, with relative humidity (%) and dew point (°F) for the bin-fan outlook
    hourly = [{'t': x['startTime'], 'temp': x['temperature'], 'short': x['shortForecast'], 'wind': _mph(x['windSpeed']), 'pop': _pop(x),
               'rh': (x.get('relativeHumidity') or {}).get('value'),
               'dew': round((x.get('dewpoint') or {}).get('value') * 9 / 5 + 32) if (x.get('dewpoint') or {}).get('value') is not None else None}
              for x in hr['properties']['periods'][:72]]
    rain = None
    qp = grid and grid['properties'].get('quantitativePrecipitation')
    if qp:
        until, mm = datetime.now(timezone.utc) + timedelta(days=7), 0.0
        for v in qp['values']:
            start = datetime.fromisoformat(v['validTime'].split('/')[0].replace('Z', '+00:00'))
            if start < until and v['value'] is not None:
                mm += float(v['value'])
        rain = round(mm / 25.4, 2)
    al = [{'event': a['properties']['event'], 'headline': a['properties']['headline'], 'severity': a['properties']['severity'],
           'ends': a['properties']['ends']} for a in (alerts or {}).get('features', [])]
    rl = p['relativeLocation']['properties']
    doc = {'place': f"{rl['city']}, {rl['state']}", 'lat': lat, 'lon': lon, 'office': p['gridId'],
           'asOf': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'updated': fc['properties'].get('updated'),
           'periods': periods, 'hourly': hourly, 'rain7': rain, 'alerts': al}
    try:
        doc['soil'] = soil_for(lat, lon, *soil_ctx)
    except Exception:
        doc['soil'] = None
    store.put_doc('market/' + key, doc)
    return f"{key} ok place={doc['place']} rain7={rain} alerts={len(al)}"


def run():
    out, ctx = [], soil_stations()
    for key, (lat, lon) in locations().items():
        try:
            out.append(one(key, lat, lon, ctx))
        except Exception as e:
            out.append(f'{key} FAILED {e}')
        time.sleep(0.3)          # be polite to the weather service
    return '\n'.join(out) or 'weather: no farm locations yet'
