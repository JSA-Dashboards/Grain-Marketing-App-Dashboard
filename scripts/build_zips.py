"""Build static/zips/zips-<first digit>.csv (zip,lat,lon) from the Census 2024 ZCTA Gazetteer (public domain).

The page's ZIP lookup (cash bid distances) fetches these files. Run once and commit the output:
    python scripts/build_zips.py
Source: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_zcta_national.zip
"""
import csv
import io
import os
import urllib.request
import zipfile

URL = 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_zcta_national.zip'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'zips')


def main():
    raw = urllib.request.urlopen(URL, timeout=60).read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode('utf-8')
    rows = csv.reader(io.StringIO(text), delimiter='\t')
    head = [h.strip() for h in next(rows)]
    zi, la, lo = head.index('GEOID'), head.index('INTPTLAT'), head.index('INTPTLONG')
    by = {}
    for r in rows:
        z = r[zi].strip()
        by.setdefault(z[0], []).append(f'{z},{float(r[la]):.5f},{float(r[lo]):.5f}')
    os.makedirs(OUT, exist_ok=True)
    for d, lines in by.items():
        with open(os.path.join(OUT, f'zips-{d}.csv'), 'w', newline='\n') as f:
            f.write('\n'.join(sorted(lines)) + '\n')
    print(f'{sum(map(len, by.values()))} ZIP codes written to {OUT}')


if __name__ == '__main__':
    main()
