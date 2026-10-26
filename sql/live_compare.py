# "Refresh" step 2: compare the live (Streamlit / Snowflake) farms with the Claude app's copies and say what to copy.
#   python sql/live_compare.py sync/fromapp      (expects sync/fromapp/live/... and sync/fromapp/claude/...)
# Prints names and counts only. NEW and CHANGED-IN-LIVE-ONLY are safe to write into the Claude app; anything also
# changed in the Claude app is listed for a person to decide, never overwritten.
import glob
import json
import os
import sys

base = sys.argv[1]
load = lambda p: json.load(open(p, encoding='utf-8'))
live = {os.path.basename(p)[:-5]: load(p) for p in glob.glob(os.path.join(base, 'live', 'farms', '*.json'))}
claude = {os.path.basename(p)[:-5]: ('farms', load(p)) for p in glob.glob(os.path.join(base, 'claude', 'farms', '*.json'))}
for p in glob.glob(os.path.join(base, 'claude', 'brokers', '*', 'farms', '*.json')):
    claude[os.path.basename(p)[:-5]] = (f'brokers/{p.split(os.sep)[-3]}/farms', load(p))
strip = lambda d: {k: v for k, v in d.items() if k != 'updatedAt'}
ids = lambda d, k: {x.get('id') for x in d.get(k, [])}
new, same, changed, conflict = [], [], [], []
for fid, L in sorted(live.items(), key=lambda x: (x[1].get('broker') or '', x[1].get('name') or '')):
    if fid not in claude:
        new.append((fid, f"{L.get('name')} [{L.get('broker')}] crops={len(L.get('crops', []))} contracts={len(L.get('sales', []))}"))
        continue
    coll, C = claude[fid]
    if strip(L) == strip(C):
        same.append(fid)
        continue
    only_c = ids(C, 'sales') - ids(L, 'sales')
    crops_c = ids(C, 'crops') - ids(L, 'crops')
    keys = sorted(k for k in set(L) | set(C) if k != 'updatedAt' and L.get(k) != C.get(k))
    line = (f"{L.get('name')} [{L.get('broker')}] in {coll}; contracts live {len(L.get('sales', []))} vs Claude "
            f"{len(C.get('sales', []))}; differs: {', '.join(keys)}")
    claude_newer = (C.get('updatedAt') or '') > (L.get('updatedAt') or '')
    (conflict if (only_c or crops_c or claude_newer) else changed).append((fid, coll, line))
only_claude = [f"{C.get('name')} [{coll}]" for fid, (coll, C) in claude.items() if fid not in live]
print('IDENTICAL:', len(same))
print('NEW IN LIVE (add to Claude):', len(new))
for fid, x in new:
    print('  +', fid, x)
print('CHANGED IN LIVE ONLY (safe to update Claude):', len(changed))
for fid, coll, x in changed:
    print('  ~', fid, x)
print('CHANGED IN CLAUDE TOO (needs a look):', len(conflict))
for fid, coll, x in conflict:
    print('  !', fid, x)
print('ONLY IN CLAUDE (kept as is):', only_claude)
L, C = os.path.join(base, 'live', 'market', 'deadlines.json'), os.path.join(base, 'claude', 'market', 'deadlines.json')
if os.path.exists(L) and os.path.exists(C):
    print('DEADLINES:', 'same' if load(L) == load(C) else 'differ')
