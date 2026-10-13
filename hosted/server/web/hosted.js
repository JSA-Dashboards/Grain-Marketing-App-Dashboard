/* Hosted Grain Marketing Desk: gives the app page the same `window.claude.use(name)` interface it has
   as a Claude app (db, user, sample, assets, downloads), backed by JSA's server. The page itself is
   unchanged. Live updates: the page checks a change counter every 15 seconds (and right after its own
   writes) and refetches only what changed. */
(function () {
  'use strict';
  const POLL_MS = 15000;

  async function api(method, url, body, raw) {
    const opt = { method, credentials: 'same-origin', headers: { 'x-grain-desk': '1' } };
    if (raw) { opt.body = raw; opt.headers['content-type'] = raw.type || 'application/octet-stream'; }
    else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers['content-type'] = 'application/json'; }
    const r = await fetch(url, opt);
    if (r.status === 401) { location.href = '/login'; throw { code: 'revoked', message: 'Signed out' }; }
    let j = null; try { j = await r.json(); } catch (_) {}
    if (!r.ok) {
      const code = (j && j.code) || (r.status === 429 ? 'rate_limited' : r.status >= 500 ? 'unavailable' : 'invalid_argument');
      throw { code, message: (j && (j.message || j.detail)) || r.statusText };
    }
    return j;
  }
  const q = s => encodeURIComponent(s);
  const freeze = o => Object.freeze(o);

  /* ---------- db ---------- */
  const subs = new Set();
  let lastSeq = -1, polling = false;
  const docSnap = (id, j) => freeze({ id, exists: !!(j && j.exists), data: () => (j && j.exists ? j.data : undefined), metadata: { fromCache: false, hasPendingWrites: false } });

  async function refresh(force) {
    if (polling) return; polling = true;
    try {
      const { seq } = await api('GET', '/api/seq');
      if (!force && seq === lastSeq) return;
      lastSeq = seq;
      await Promise.all([...subs].map(s => s.pull().catch(e => s.err && s.err(e))));
    } catch (_) { /* offline for a moment: try again next tick */ }
    finally { polling = false; }
  }
  setInterval(() => { if (!document.hidden) refresh(false); }, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(false); });
  const afterWrite = () => setTimeout(() => refresh(true), 50);

  function docRef(path) {
    const id = path.split('/').pop();
    return freeze({
      id, path,
      get: async () => docSnap(id, await api('GET', '/api/doc?path=' + q(path))),
      set: async data => { await api('PUT', '/api/doc?path=' + q(path), { data }); afterWrite(); },
      update: async data => { await api('PUT', '/api/doc?path=' + q(path), { data, merge: true }); afterWrite(); },
      delete: async () => { await api('DELETE', '/api/doc?path=' + q(path)); afterWrite(); },
      acquire: async () => ({ acquired: true }),
      collection: sub => collRef(path + '/' + sub),
      onSnapshot(next, err) {
        const s = { err, pull: async () => next(docSnap(id, await api('GET', '/api/doc?path=' + q(path)))) };
        subs.add(s); s.pull().catch(e => err && err(e));
        return () => subs.delete(s);
      },
    });
  }

  function collRef(path) {
    const query = {
      path,
      where: () => query, orderBy: () => query, limit: () => query,   // the app filters on its side
      doc: id => docRef(path + '/' + (id || Math.random().toString(36).slice(2, 12))),
      add: async data => { const r = query.doc(); await r.set(data); return r; },
      get: async () => { const j = await api('GET', '/api/collection?path=' + q(path)); return qsnap(j.docs.map(d => docSnap(d.id, d))); },
      onSnapshot(next, err) {
        const have = new Map(); let since = 0;
        const s = {
          err, pull: async () => {
            const j = await api('GET', '/api/collection?path=' + q(path) + '&since=' + since);
            const keep = new Set(j.ids);
            for (const k of [...have.keys()]) if (!keep.has(k)) have.delete(k);
            j.docs.forEach(d => have.set(d.id, docSnap(d.id, d)));
            since = j.seq;
            next(qsnap([...have.values()].sort((a, b) => a.id.localeCompare(b.id))));
          },
        };
        subs.add(s); s.pull().catch(e => err && err(e));
        return () => subs.delete(s);
      },
    };
    return freeze(query);
  }
  const qsnap = docs => freeze({ docs, size: docs.length, empty: !docs.length, docChanges: () => [], metadata: { fromCache: false, hasPendingWrites: false } });
  const db = freeze({ doc: docRef, collection: collRef });

  /* ---------- user ---------- */
  let meP = null;
  const getMe = () => meP || (meP = api('GET', '/api/me'));
  const user = freeze({
    id: async () => (await getMe()).id,
    role: async () => (await getMe()).role,
    isOwner: async () => ['admin', 'broker'].includes((await getMe()).role),   // works with many client farms
    canEdit: async () => (await getMe()).role === 'admin',
    can: async () => true,
    me: async () => { const m = await getMe(); return { id: m.id, name: m.name, email: m.email }; },
    profiles: async () => ({}),
    search: async () => [],
  });

  /* ---------- Claude (contract scanning, Intel drafts) ---------- */
  const toDataUrl = b => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(b); });
  async function ask(input, opts) {
    opts = opts || {};
    const images = await Promise.all((opts.images || []).map(toDataUrl));
    const r = await fetch('/api/claude', { method: 'POST', credentials: 'same-origin', signal: opts.signal,
      headers: { 'x-grain-desk': '1', 'content-type': 'application/json' }, body: JSON.stringify({ input, images }) })
      .catch(e => { throw e && e.name === 'AbortError' ? { code: 'cancelled', message: 'Cancelled' } : { code: 'unavailable', message: 'Network error' }; });
    if (r.status === 401) { location.href = '/login'; throw { code: 'revoked', message: 'Signed out' }; }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw { code: j.code || 'unavailable', message: j.message || 'Claude is unavailable right now.' };
    if (opts.onText) try { opts.onText({ text: j.text, delta: j.text }); } catch (_) {}
    return { text: j.text, truncated: !!j.truncated };
  }
  const sample = Object.assign((input, opts) => ask(input, opts), {
    json: async (input, opts) => {
      const { text } = await ask(input, opts);
      // the answer may come wrapped in a ```json fence or a sentence; parse from the first { or [
      const t = (text.match(/```(?:json)?\s*([\s\S]*?)```/) || [null, text])[1], i = t.search(/[[{]/);
      try { return JSON.parse((i >= 0 ? t.slice(i) : t).trim()); } catch (_) { throw { code: 'invalid_json', message: "Claude's answer wasn't valid JSON.", text }; }
    },
    limits: async () => ({ images: true }),
  });

  /* ---------- contract photos ---------- */
  const assets = freeze({
    upload: async (blob, o) => api('POST', '/api/assets', undefined, o && o.type && blob.type !== o.type ? new Blob([blob], { type: o.type }) : blob),
    delete: async id => { await api('DELETE', '/api/assets/' + q(id)); },
    list: async () => ({ assets: [], usage: {} }),
  });

  /* ---------- downloads (PDF reports, CSV, Snowflake export) ---------- */
  const downloads = freeze({
    save: async ({ filename, data }) => {
      const blob = data instanceof Blob ? data : new Blob([data]);
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      return { status: 'saved' };
    },
  });

  const caps = { db, user, sample, assets, downloads };
  window.claude = freeze({ hosted: true, use: async name => caps[name] || null });

  // a sign-out link beside the name in the app bar
  document.addEventListener('DOMContentLoaded', () => {
    const who = document.getElementById('who'); if (!who) return;
    const f = document.createElement('form'); f.method = 'post'; f.action = '/logout'; f.style.display = 'inline'; f.style.marginLeft = '10px';
    f.innerHTML = '<button type="submit" class="link" style="font-size:.85rem">Sign out</button>';
    who.after(f);
  });
})();
