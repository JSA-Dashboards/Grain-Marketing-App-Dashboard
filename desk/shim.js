/* Runs INSIDE the page's iframe, before the app's own script. Gives the unchanged page the same
   `window.claude.use(name)` interface it has as a Claude app (db, user, sample, assets, downloads),
   answered by Python through parent.__gdRpc(op, args). Live updates: the page checks a change counter
   every 20 seconds (and right after its own writes) and refetches only what changed. */
(function () {
  'use strict';
  const POLL_MS = 20000;
  const rpc = (op, args) => parent.__gdRpc(op, args || {});
  async function api(op, args) {
    const r = await rpc(op, args);
    if (!r.ok) {
      if (r.code === 'revoked') { parent.__gdSignedOut && parent.__gdSignedOut(); }
      throw { code: r.code || 'unavailable', message: r.message || 'Request failed' };
    }
    return r.result;
  }
  const freeze = o => Object.freeze(o);

  /* ZIP code files ship with the Streamlit app as static files (the page fetches "zips/zips-<digit>.csv") */
  const realFetch = window.fetch.bind(window);
  window.fetch = (u, o) => (typeof u === 'string' && u.startsWith('zips/')) ? realFetch(parent.location.origin + '/app/static/' + u, o) : realFetch(u, o);

  /* ---------- db ---------- */
  const subs = new Set();
  let lastSeq = -1, polling = false;
  const docSnap = (id, j) => freeze({ id, exists: !!(j && j.exists), data: () => (j && j.exists ? j.data : undefined), metadata: { fromCache: false, hasPendingWrites: false } });

  async function refresh(force) {
    if (polling) return; polling = true;
    try {
      const { seq } = await api('seq');
      if (!force && seq === lastSeq) return;
      lastSeq = seq;
      await Promise.all([...subs].map(s => s.pull().catch(e => s.err && s.err(e))));
    } catch (_) { /* offline for a moment: try again next tick */ }
    finally { polling = false; }
  }
  setInterval(() => { if (!document.hidden && !parent.document.hidden) refresh(false); }, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(false); });
  const afterWrite = () => setTimeout(() => refresh(true), 50);

  function docRef(path) {
    const id = path.split('/').pop();
    return freeze({
      id, path,
      get: async () => docSnap(id, await api('doc_get', { path })),
      set: async data => { await api('doc_put', { path, data }); afterWrite(); },
      update: async data => { await api('doc_put', { path, data, merge: true }); afterWrite(); },
      delete: async () => { await api('doc_delete', { path }); afterWrite(); },
      acquire: async () => ({ acquired: true }),
      collection: sub => collRef(path + '/' + sub),
      onSnapshot(next, err) {
        const s = { err, pull: async () => next(docSnap(id, await api('doc_get', { path }))) };
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
      get: async () => { const j = await api('coll_get', { path }); return qsnap(j.docs.map(d => docSnap(d.id, d))); },
      onSnapshot(next, err) {
        const have = new Map(); let since = 0;
        const s = {
          err, pull: async () => {
            const j = await api('coll_get', { path, since });
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
  const getMe = () => meP || (meP = api('me'));
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
    if (opts.signal && opts.signal.aborted) throw { code: 'cancelled', message: 'Cancelled' };
    const j = await api('claude', { input, images });
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
  const blobToB64 = b => toDataUrl(b).then(u => u.slice(u.indexOf(',') + 1));
  const assets = freeze({
    upload: async (blob, o) => {
      const type = (o && o.type) || blob.type || 'image/jpeg';
      return api('asset_put', { type, b64: await blobToB64(blob) });
    },
    delete: async id => { await api('asset_delete', { id }); },
    list: async () => ({ assets: [], usage: {} }),
  });

  /* The page shows photos as <img src="/_blob/<id>">: swap each for the real image once it is fetched. */
  const photoCache = new Map();
  const photo = id => photoCache.get(id) || (photoCache.set(id, api('asset_get', { id }).then(r => r.dataUrl).catch(e => { photoCache.delete(id); throw e; })), photoCache.get(id));
  function fixPhotos(root) {
    root.querySelectorAll('img[src^="/_blob/"],a[href^="/_blob/"]').forEach(el => {
      const attr = el.tagName === 'IMG' ? 'src' : 'href', id = el.getAttribute(attr).slice(7);
      el.setAttribute(attr, 'data:,');
      photo(id).then(url => { if (el.tagName === 'A') { el.href = URL.createObjectURL(dataUrlToBlob(url)); } else { el.src = url; } }).catch(() => {});
    });
  }
  function dataUrlToBlob(u) {
    const [h, b] = u.split(','), mime = h.match(/:(.*?);/)[1], bin = atob(b), a = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    return new Blob([a], { type: mime });
  }
  new MutationObserver(() => fixPhotos(document)).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'href'] });

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

  // a sign-out button beside the name in the app bar
  document.addEventListener('DOMContentLoaded', () => {
    const who = document.getElementById('who'); if (!who) return;
    const f = document.createElement('span'); f.style.marginLeft = '10px';
    f.innerHTML = '<button type="button" class="link" style="font-size:.85rem">Sign out</button>';
    f.firstChild.onclick = () => api('logout').finally(() => parent.__gdSignedOut && parent.__gdSignedOut());
    who.after(f);
  });
})();
