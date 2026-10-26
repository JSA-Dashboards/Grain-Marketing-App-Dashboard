# "Refresh" step 1: split a Snowflake JSON result (PATH, DATA rows of JSA.GRAIN_DESK.DOCS, the Streamlit app's store)
# into one file per document: <out>/<collection>/<id>.json holding the document data only (the Claude app's shape).
#   python sql/live_split.py sync/fromapp/raw.json sync/fromapp/live
# sync/ is gitignored: these files are customer data; delete them after the refresh.
import json
import os
import sys

src, out = sys.argv[1], sys.argv[2]
rows = json.load(open(src, encoding='utf-8-sig'))
if rows and isinstance(rows[0], list):   # several statements -> list of result sets
    rows = [r for rs in rows for r in rs]
n = {}
for r in rows:
    coll, _, doc = r['PATH'].rpartition('/')
    d = os.path.join(out, *coll.split('/'))
    os.makedirs(d, exist_ok=True)
    json.dump(json.loads(r['DATA']), open(os.path.join(d, doc + '.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    n[coll] = n.get(coll, 0) + 1
print(n)
