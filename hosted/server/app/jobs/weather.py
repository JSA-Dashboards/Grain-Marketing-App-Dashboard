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


def one(key, lat, lon):
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
    hourly = [{'t': x['startTime'], 'temp': x['temperature'], 'short': x['shortForecast'], 'wind': _mph(x['windSpeed']), 'pop': _pop(x)}
              for x in hr['properties']['periods'][:24]]
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
    store.put_doc('market/' + key, doc)
    return f"{key} ok place={doc['place']} rain7={rain} alerts={len(al)}"


def run():
    out = []
    for key, (lat, lon) in locations().items():
        try:
            out.append(one(key, lat, lon))
        except Exception as e:
            out.append(f'{key} FAILED {e}')
        time.sleep(0.3)          # be polite to the weather service
    return '\n'.join(out) or 'weather: no farm locations yet'
