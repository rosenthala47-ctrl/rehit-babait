/* שליף — הדף הראשי: ניהול קבצים, פיזור שאילתות למנועי החיפוש, והצגת התשובה. */
(() => {
  'use strict';
  const T = window.ShlifText, DB = window.ShlifDB, Demo = window.ShlifDemo;
  const $ = (id) => document.getElementById(id);
  const EXT = /\.(xlsx|xlsm|xls|xlsb|ods|csv|tsv)$/i;
  const nf = new Intl.NumberFormat('he-IL', { maximumFractionDigits: 2 });
  const nf2 = new Intl.NumberFormat('he-IL', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmt = (n) => (Number.isInteger(n) ? nf.format(n) : nf2.format(n));
  // שם קובץ או נתיב: כיוון לפי התוכן, כדי שעברית עם סיומת באנגלית תוצג בסדר הנכון
  const bd = (s) => `<bdi dir="${/[\u0590-\u05FF]/.test(s) ? 'rtl' : 'ltr'}">${esc(s)}</bdi>`;
  const filesTxt = (n) => (n === 1 ? 'קובץ אחד' : fmt(n) + ' קבצים');
  const rowsTxt = (n) => (n === 1 ? 'שורה אחת' : fmt(n) + ' שורות');
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const state = {
    workers: [],
    queue: [], jobs: new Map(), nextJob: 1,
    files: new Map(),     // path -> { path, name, size, lastModified, rows, sheets, error, status, shard, keys }
    catalog: new Map(),   // "shard:tableId" -> טבלה
    cat: null,            // כותרות ומושגים שקיימים בקבצים (לפענוח שאילתות)
    mode: 'empty',        // empty | demo | real
    batch: { total: 0, done: 0, rows: 0, t0: 0 },
    dirHandle: null,
    cacheOk: true,
    search: null, seq: 0, limit: 50, only: null,
  };

  // ---------- מנועי חיפוש ----------
  function startWorkers() {
    const n = Math.max(2, Math.min(6, (navigator.hardwareConcurrency || 4) - 1));
    for (let i = 0; i < n; i++) {
      const w = new Worker('engine-worker.js');
      const slot = { w, i, busy: false };
      w.onmessage = (ev) => { state.engineOk = true; onWorkerMessage(slot, ev.data); };
      w.onerror = (e) => {
        console.error('worker', e.message);
        if (state.engineOk) return;
        // קורה כשפותחים את הקובץ ישירות מהדיסק (file://)
        $('progress').hidden = true;
        $('statText').textContent = 'לא נטען';
        drawNothing('מנוע החיפוש לא עלה', 'פתחו את שליף דרך כתובת האתר ולא כקובץ מהמחשב.');
      };
      state.workers.push(slot);
    }
  }

  function enqueue(job) {
    job.id = state.nextJob++;
    state.jobs.set(job.id, job);
    state.queue.push(job);
    if (!state.batch.total || state.batch.done >= state.batch.total) state.batch = { total: 0, done: 0, rows: 0, t0: performance.now() };
    state.batch.total++;
  }
  function pump() {
    for (const slot of state.workers) {
      if (slot.busy || !state.queue.length) continue;
      const job = state.queue.shift();
      slot.busy = true;
      job.shard = slot.i;
      const f = state.files.get(job.path);
      if (f) { f.status = 'working'; f.shard = slot.i; }
      if (job.type === 'parse') sendFile(slot, job);
      else slot.w.postMessage(job);
    }
    drawProgress();
  }

  // קריאת הקובץ בדף עצמו והעברת הבייטים למנוע. כמה דרכים, כי דפדפני טלפון לא תמיד מסכימים לראשונה.
  function readBytes(file) {
    const viaReader = () => new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => rej(fr.error || new Error('FileReader'));
      fr.readAsArrayBuffer(file);
    });
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    return file.arrayBuffer()
      .catch(() => viaReader())
      .catch(() => wait(400).then(() => new Response(file).arrayBuffer()))
      .catch(() => wait(1200).then(viaReader));
  }
  async function sendFile(slot, job) {
    const file = job.file;
    job.file = null;
    try {
      const buf = await readBytes(file);
      slot.w.postMessage(Object.assign({}, job, { buf }), [buf]);
    } catch (e) {
      const detail = String((e && e.name) || 'Error') + ': ' + String((e && e.message) || e).slice(0, 140);
      onWorkerMessage(slot, {
        type: 'fileDone', job: job.id, path: job.path, tables: [], ms: 0, cached: false,
        meta: { path: job.path, name: job.name, size: job.size, lastModified: job.lastModified, rows: 0, sheets: 0, error: 'הדפדפן לא הצליח לקרוא את הקובץ מהמכשיר', detail },
      });
    }
  }

  function onWorkerMessage(slot, m) {
    if (m.type === 'fileDone') {
      slot.busy = false;
      const job = state.jobs.get(m.job);
      state.jobs.delete(m.job);
      const f = state.files.get(m.path);
      if (job && f && f.job === job.id) {
        state.batch.done++;
        if (m.meta.error === 'missing-cache') {
          state.files.delete(m.path);
          DB.del(['files', 'meta'], m.path).catch(() => {});
        } else {
          f.status = m.meta.error ? 'error' : 'done';
          f.error = m.meta.error;
          f.detail = m.meta.detail || null;
          f.rows = m.meta.rows; f.sheets = m.meta.sheets;
          f.shard = slot.i;
          f.keys = [];
          for (const t of m.tables) {
            const key = slot.i + ':' + t.id;
            f.keys.push(key);
            state.catalog.set(key, Object.assign(t, { path: m.path, name: f.name }));
          }
          state.batch.rows += m.meta.rows;
          state.cat = null;
        }
      } else if (m.tables.length) {
        // הקובץ הוסר או הוחלף בזמן שנקרא
        slot.w.postMessage({ type: 'remove', path: m.path, ids: m.tables.map((t) => t.id) });
      }
      pump();
      if (!state.queue.length && !state.workers.some((s) => s.busy)) batchFinished();
      else scheduleRefresh();
    } else if (m.type === 'result') {
      onSearchReply(slot, m);
    } else if (m.type === 'cacheFail') {
      if (state.cacheOk) { state.cacheOk = false; toast('אין מספיק מקום לשמור את הקבצים בדפדפן — הם יישארו עד שתסגרו את הדף'); }
    }
  }

  let refreshTimer = 0;
  function scheduleRefresh() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(() => { refreshTimer = 0; drawStatus(); if ($('q').value.trim()) runSearch(); }, 1500);
  }

  function batchFinished() {
    drawProgress();
    drawStatus();
    drawChips();
    if ($('sources').open) drawFiles();
    const b = state.batch;
    const errors = [...state.files.values()].filter((f) => f.status === 'error').length;
    if (state.mode === 'real' && b.total > 1) {
      const secs = ((performance.now() - b.t0) / 1000).toFixed(1);
      toast(`נקראו ${filesTxt(b.total)}, ${rowsTxt(b.rows)}, ב-${secs} שניות` + (errors ? ` · ${fmt(errors)} לא נקראו` : ''));
    } else if (state.mode === 'real' && b.total === 1) {
      toast(errors ? 'הקובץ לא נקרא — הסיבה מופיעה ברשימת הקבצים' : `הקובץ נקרא: ${rowsTxt(b.rows)}`);
    }
    if (state.mode === 'demo' && !$('q').value) $('q').value = DEMO_QUERIES[0];
    runSearch();
  }

  // ---------- קטלוג ----------
  function getCat() {
    if (state.cat) return state.cat;
    const headerTokens = new Set(), concepts = new Set(), headerCount = new Map();
    for (const t of state.catalog.values()) {
      t.header.forEach((h, c) => {
        if (/^עמודה [A-Z]+$/.test(h)) return;
        for (const tok of T.tokenize(h)) {
          headerTokens.add(tok);
          const cs = T.CONCEPT_BY_WORD.get(tok);
          if (cs) for (const id of cs) concepts.add(id);
        }
        if (t.kinds[c] !== 'text' || !/שם|תיאור|name/i.test(h)) headerCount.set(h, (headerCount.get(h) || 0) + 1);
      });
    }
    state.cat = { headerTokens, concepts, headerCount };
    return state.cat;
  }

  // ---------- הוספה / הסרה של קבצים ----------
  function resetEngines() {
    for (const s of state.workers) s.w.postMessage({ type: 'reset' });
    // עבודות שעדיין בדרך יתעלמו (אין להן רשומת קובץ)
    state.queue.length = 0;
    state.files.clear(); state.catalog.clear(); state.cat = null;
    state.only = null;
    state.batch = { total: 0, done: 0, rows: 0, t0: 0 };
  }

  function addFiles(list) {
    const good = list.filter((x) => EXT.test(x.file.name) && !/^(~\$|\.)/.test(x.file.name));
    if (!good.length) { toast(list.length ? 'לא נמצאו קבצי אקסל (xlsx, xls, csv)' : 'לא נבחרו קבצים'); return; }
    if (state.mode !== 'real') {
      resetEngines();
      state.mode = 'real';
      $('q').value = '';
      $('out').innerHTML = '';
    }
    let added = 0, same = 0;
    for (const { file, path } of good) {
      const old = state.files.get(path);
      if (old && old.size === file.size && old.lastModified === file.lastModified && old.status !== 'error') { same++; continue; }
      if (old) dropFile(path, false);
      const f = { path, name: file.name, size: file.size, lastModified: file.lastModified, rows: 0, sheets: 0, error: null, status: 'queued', shard: -1, keys: [] };
      const job = { type: 'parse', path, name: file.name, size: file.size, lastModified: file.lastModified, file, cache: state.cacheOk };
      enqueue(job);
      f.job = job.id;
      state.files.set(path, f);
      added++;
    }
    if (!added) toast(same === 1 ? 'הקובץ כבר נטען ולא השתנה' : same ? `כל ${fmt(same)} הקבצים כבר נטענו ולא השתנו` : 'אין קבצים חדשים');
    else if (same) toast(`${fmt(added)} קבצים חדשים או מעודכנים · ${fmt(same)} ללא שינוי`);
    try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {}); } catch (e) { /* לא חובה */ }
    drawConnect(); drawStatus(); pump();
  }

  function dropFile(path, redraw) {
    const f = state.files.get(path);
    if (!f) return;
    if (f.shard >= 0) state.workers[f.shard].w.postMessage({ type: 'remove', path });
    for (const k of f.keys) state.catalog.delete(k);
    state.files.delete(path);
    const before = state.queue.length;
    state.queue = state.queue.filter((j) => j.path !== path);
    state.batch.total -= before - state.queue.length;
    if (f.status === 'working') state.batch.total--;
    state.cat = null;
    if (state.mode === 'real') DB.del(['files', 'meta'], path).catch(() => {});
    if (redraw) { drawStatus(); drawFiles(); drawChips(); runSearch(); }
  }

  function loadDemo() {
    resetEngines();
    state.mode = 'demo';
    const small = (navigator.deviceMemory && navigator.deviceMemory <= 4) || /Mobi|Android/i.test(navigator.userAgent);
    for (const spec of Demo.list({ branches: small ? 4 : 12 })) {
      const f = { path: spec.path, name: spec.name, size: 0, lastModified: 0, rows: 0, sheets: 0, error: null, status: 'queued', shard: -1, keys: [] };
      const job = { type: 'demo', path: spec.path, name: spec.name, size: 0, lastModified: 0, spec };
      enqueue(job);
      f.job = job.id;
      state.files.set(spec.path, f);
    }
    drawConnect(); drawStatus(); pump();
  }

  async function restore() {
    let metas = [];
    try { metas = await DB.getAll('meta'); } catch (e) { state.cacheOk = false; }
    try { state.dirHandle = (await DB.get('kv', 'dir')) || null; } catch (e) { /* אין */ }
    if (!metas.length) { loadDemo(); return; }
    state.mode = 'real';
    for (const m of metas) {
      const f = { path: m.path, name: m.name, size: m.size, lastModified: m.lastModified, rows: 0, sheets: 0, error: m.error, status: 'queued', shard: -1, keys: [] };
      if (m.error) { f.status = 'error'; state.files.set(m.path, f); continue; }
      const job = { type: 'load', path: m.path, name: m.name, size: m.size, lastModified: m.lastModified };
      enqueue(job);
      f.job = job.id;
      state.files.set(m.path, f);
    }
    drawConnect(); drawStatus(); pump();
  }

  async function clearAll() {
    resetEngines();
    state.mode = 'empty';
    state.dirHandle = null;
    try { await DB.clear(['files', 'meta', 'kv']); } catch (e) { /* אין אחסון */ }
    $('q').value = '';
    $('out').innerHTML = '';
    drawConnect(); drawStatus(); drawChips(); drawFiles(); drawProgress();
  }

  // --- בחירת קבצים ותיקיות ---
  async function walkHandle(dir, prefix, out) {
    for await (const [name, h] of dir.entries()) {
      if (h.kind === 'file') { if (EXT.test(name)) out.push({ file: await h.getFile(), path: prefix + name }); }
      else if (h.kind === 'directory' && name[0] !== '.') await walkHandle(h, prefix + name + '/', out);
    }
  }
  async function pickDir() {
    if (window.showDirectoryPicker) {
      try {
        const h = await window.showDirectoryPicker({ mode: 'read' });
        toast('סורק את התיקייה…');
        const out = [];
        await walkHandle(h, h.name + '/', out);
        state.dirHandle = h;
        DB.put('kv', 'dir', h).catch(() => {});
        addFiles(out);
        return;
      } catch (e) {
        if (e && e.name === 'AbortError') return;
      }
    }
    openPicker('pickDir');
  }
  function openPicker(id) {
    const input = $(id);
    const busy = state.queue.some((j) => j.type === 'parse') || state.workers.some((s) => s.busy);
    if (!busy) input.value = ''; // מאפשר לבחור שוב את אותם קבצים
    input.click();
  }
  async function refreshDir() {
    const h = state.dirHandle;
    if (!h) return;
    try {
      if ((await h.queryPermission({ mode: 'read' })) !== 'granted' && (await h.requestPermission({ mode: 'read' })) !== 'granted') return;
      toast('בודק מה השתנה בתיקייה…');
      const out = [];
      await walkHandle(h, h.name + '/', out);
      const seen = new Set(out.map((x) => x.path));
      let removed = 0;
      for (const p of [...state.files.keys()]) if (p.startsWith(h.name + '/') && !seen.has(p)) { dropFile(p, false); removed++; }
      addFiles(out);
      if (removed) { toast(`${fmt(removed)} קבצים שנמחקו מהתיקייה הוסרו`); drawStatus(); drawFiles(); runSearch(); }
    } catch (e) {
      toast('לא הצלחנו לגשת לתיקייה — בחרו אותה מחדש');
    }
  }
  function fromInput(input) {
    // לא מנקים את השדה כאן: בחלק מהטלפונים ניקוי מיידי מבטל את הרשאת הקריאה לקבצים שנבחרו
    const out = [...input.files].map((file) => ({ file, path: file.webkitRelativePath || file.name }));
    addFiles(out);
  }
  function readEntry(entry, prefix, out) {
    return new Promise((res) => {
      if (entry.isFile) {
        entry.file((file) => { out.push({ file, path: prefix + file.name }); res(); }, () => res());
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const all = [];
        const next = () => reader.readEntries(async (batch) => {
          if (!batch.length) { for (const e of all) await readEntry(e, prefix + entry.name + '/', out); res(); return; }
          all.push(...batch); next();
        }, () => res());
        next();
      } else res();
    });
  }
  async function onDrop(ev) {
    ev.preventDefault();
    $('veil').hidden = true;
    const items = ev.dataTransfer && ev.dataTransfer.items;
    const out = [];
    if (items && items.length && items[0].webkitGetAsEntry) {
      const entries = [...items].map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
      for (const e of entries) await readEntry(e, '', out);
    } else if (ev.dataTransfer) {
      for (const file of ev.dataTransfer.files) out.push({ file, path: file.name });
    }
    addFiles(out);
  }

  // ---------- חיפוש ----------
  const DEMO_QUERIES = [
    'נייר טואלט, רך וטוב, כמה מלאי',
    'נייר טואלט רך וטוב תלת שכבתי 32 גלילים, חיפה, מלאי',
    'טלפון של דני כהן',
    'שמפו לילדים, מחיר',
    'כמה לקוחות בחיפה',
  ];

  function runSearch(keepLimit) {
    const text = $('q').value.trim();
    if (!keepLimit) state.limit = 50;
    const seq = ++state.seq;
    if (!text || !state.catalog.size) { state.search = null; if (!text) $('out').innerHTML = ''; return; }
    const parsed = T.parseQuery(text, getCat());
    if (!parsed.groups.length) { state.search = null; drawNothing('כתבו מה לחפש', 'למשל שם מוצר ומותג, ואחריהם הפרט שרוצים לדעת.'); return; }
    state.search = { seq, text, parsed, groups: parsed.groups, loose: null, loosed: false, strict: true, round: 1, replies: [], t0: performance.now(), dropped: [], loosened: [] };
    sendSearch();
  }
  function sendSearch() {
    const S = state.search;
    S.replies = [];
    for (const s of state.workers) {
      s.w.postMessage({ type: 'search', seq: S.seq, round: S.round, groups: S.groups, targets: S.parsed.targets, loose: S.loose, strict: S.strict, limit: state.limit, only: state.only });
    }
  }
  function onSearchReply(slot, m) {
    const S = state.search;
    if (!S || m.seq !== S.seq || m.round !== S.round) return;
    m.shard = slot.i;
    S.replies.push(m);
    if (S.replies.length < state.workers.length) return;
    const G = S.groups.length;
    const hits = new Array(G).fill(0);
    for (const r of S.replies) for (let g = 0; g < G; g++) hits[g] += r.hits[g] || 0;
    if (!S.loosed && hits.some((h) => !h)) {
      S.loosed = true;
      // מילים שלא נמצאו: מילות-רמז נזרקות, ובשאר מנסים התחלות מילה ושגיאות הקלדה
      const keep = [], loose = [];
      S.groups.forEach((g, i) => {
        if (!hits[i] && g.soft) { S.dropped.push(g.q); return; }
        keep.push(g); loose.push(!hits[i]);
        if (!hits[i]) S.loosened.push(g.q);
      });
      if (!keep.length) { drawNothing('לא נמצאו תוצאות', 'נסו מילה אחרת או בדקו שהקובץ הנכון נטען.'); return; }
      // מילות-רמז שנזרקו משנות את מיקום המילים — מעדכנים את ההפניות ביניהן
      const map = new Map(keep.map((g, i) => [S.groups.indexOf(g), i]));
      for (const g of keep) if (g.buddies) { g.buddies = g.buddies.map((b) => map.get(b)).filter((b) => b !== undefined); if (!g.buddies.length) delete g.buddies; }
      S.groups = keep; S.loose = loose; S.round++;
      sendSearch();
      return;
    }
    if (S.strict && S.replies.some((r) => r.strictDropped) && !S.replies.some((r) => r.count)) {
      S.strict = false; S.round++;
      sendSearch();
      return;
    }
    S.hits = hits;
    drawResults();
  }

  // ---------- תצוגה ----------
  function toast(text) {
    const el = $('toast');
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { el.hidden = true; }, 4200);
  }

  function drawStatus() {
    let rows = 0, files = 0;
    for (const f of state.files.values()) { if (f.status === 'done') { files++; rows += f.rows; } }
    $('statText').textContent = state.files.size ? `${filesTxt(files)} · ${rowsTxt(rows)}` : 'אין קבצים';
  }
  function drawProgress() {
    const b = state.batch;
    const active = b.total > 0 && b.done < b.total;
    $('progress').hidden = !active;
    if (!active) return;
    $('progressBar').style.width = (100 * b.done / b.total).toFixed(1) + '%';
    const verb = state.mode === 'demo' ? 'מכין נתוני דוגמה' : 'קורא קבצים';
    $('progressText').textContent = `${verb}… ${fmt(b.done)} מתוך ${fmt(b.total)} · ${fmt(b.rows)} שורות. אפשר כבר לחפש.`;
  }
  function drawConnect() {
    const el = $('connect');
    el.hidden = state.mode === 'real';
    $('btnDemo').hidden = state.mode !== 'empty';
    const privacy = '<small>הקבצים נקראים במחשב שלכם בלבד ולא נשלחים לשום שרת.</small>';
    $('connectText').innerHTML = state.mode === 'demo'
      ? 'אלה <strong>נתוני דוגמה</strong> של רשת סופרים דמיונית. כדי לחפש בקבצים שלכם, גררו לכאן תיקייה או בחרו אותה.' + privacy
      : 'גררו לכאן תיקייה עם קבצי האקסל שלכם, או בחרו אותה.' + privacy;
    $('dlgRefresh').hidden = !state.dirHandle;
  }
  function drawChips() {
    const el = $('chips');
    if (state.mode === 'demo') {
      el.innerHTML = '<span>נסו:</span>' + DEMO_QUERIES.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join('');
      return;
    }
    if (state.mode !== 'real' || !state.catalog.size) { el.innerHTML = ''; return; }
    const top = [...getCat().headerCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
    el.innerHTML = top.length
      ? '<span>אפשר לשאול על:</span>' + top.map(([h]) => `<button type="button" class="chip" data-add="${esc(h)}">${esc(h)}</button>`).join('')
      : '';
  }

  function drawNothing(title, text) {
    $('out').innerHTML = `<div class="empty"><strong>${esc(title)}</strong>${esc(text)}</div>`;
  }

  // סימון מילות החיפוש בתוך תא
  function makeHighlighter(S) {
    const exact = new Set(), prefixes = [];
    S.groups.forEach((g, i) => {
      for (const k of g.vars.keys()) exact.add(k);
      if (S.loose && S.loose[i]) prefixes.push(g.q);
    });
    const re = /[\p{L}\p{N}"'׳״`]+/gu;
    return (text) => {
      let out = '', last = 0, m;
      re.lastIndex = 0;
      while ((m = re.exec(text))) {
        const toks = T.tokenize(m[0]);
        const hit = toks.some((t) => exact.has(t) || prefixes.some((p) => t.startsWith(p)));
        if (!hit) continue;
        out += esc(text.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
        last = m.index + m[0].length;
      }
      return out + esc(text.slice(last));
    };
  }

  const shortName = (name) => name.replace(/\.[^.]+$/, '');
  const addrOf = (tb, r, c) => T.colLetter(tb.firstCol + c) + (tb.firstRow + r);

  function drawResults() {
    const S = state.search;
    const G = S.groups.length;
    const best = Math.max(0, ...S.replies.map((r) => r.best));
    const used = S.replies.filter((r) => r.best === best && best > 0);
    if (!used.length) {
      const missing = S.loosened.length ? `המילה "${S.loosened[0]}" לא מופיעה באף קובץ.` : 'נסו מילה אחרת או פחות מילים.';
      drawNothing('לא נמצאו תוצאות', missing);
      return;
    }
    const targets = S.parsed.targets;
    let total = 0;
    const perFile = new Map(); // path -> { count, sums[] }
    const tAgg = targets.map(() => ({ sum: 0, n: 0, min: Infinity, max: -Infinity, filled: 0, headers: new Map() }));
    const rows = [];
    for (const rep of used) {
      total += rep.count;
      for (const a of rep.agg) {
        const tb = state.catalog.get(rep.shard + ':' + a.t);
        if (!tb) continue;
        let pf = perFile.get(tb.path);
        if (!pf) perFile.set(tb.path, pf = { name: tb.name, count: 0, sum: 0, n: 0 });
        pf.count += a.count;
        a.aggs.forEach((x, i) => {
          if (a.tcols[i] < 0) return;
          const t = tAgg[i];
          t.sum += x.sum; t.n += x.n; t.filled += x.filled;
          if (x.min < t.min) t.min = x.min;
          if (x.max > t.max) t.max = x.max;
          const h = tb.header[a.tcols[i]];
          t.headers.set(h, (t.headers.get(h) || 0) + a.count);
          if (i === 0) { pf.sum += x.sum; pf.n += x.n; }
        });
      }
      for (const r of rep.rows) { r.tb = state.catalog.get(rep.shard + ':' + r.t); if (r.tb) rows.push(r); }
    }
    rows.sort((a, b) => b.score - a.score || (a.tb.path < b.tb.path ? -1 : a.tb.path > b.tb.path ? 1 : a.r - b.r));
    const shown = rows.slice(0, state.limit);
    const hl = makeHighlighter(S);
    const nFiles = perFile.size;
    const where = total === 1 ? '' : rowsTxt(total) + (nFiles > 1 ? ` ב-${fmt(nFiles)} קבצים` : '');
    let html = '';

    // --- הערות ---
    const notes = [];
    if (best < G) notes.push(`לא נמצאה שורה שמכילה את כל ${G} המילים. מוצגות שורות שמתאימות ל-${best} מהן.`);
    const fuzzy = S.loosened.filter((q, i) => S.hits && S.hits[S.groups.findIndex((g) => g.q === q)] > 0);
    if (fuzzy.length) notes.push(`"${fuzzy.join('", "')}" לא נמצא בדיוק כך — מוצגות מילים דומות.`);
    for (const n of notes) html += `<div class="note">${esc(n)}</div>`;

    // --- התא הנבחר: התשובה ---
    const t0 = tAgg[0];
    const hasTarget = targets.length && t0 && t0.filled > 0;
    const headerLabel = (t, tg) => {
      if (!t.headers.size) return tg.word;
      const top = [...t.headers.entries()].sort((a, b) => b[1] - a[1]);
      return top.length === 1 ? top[0][0] : top[0][0] + ' / ' + top[1][0];
    };
    const aggMode = (tg, t) => {
      if (!(t.n > 0 && t.n >= t.filled * 0.8)) return 'text';
      const cid = tg.concepts[0];
      const mode = cid ? T.CONCEPTS.get(cid).agg : 'sum';
      return mode === 'none' ? 'text' : mode;
    };
    html += '<section class="cell">';
    if (total === 1 && shown.length) {
      const r = shown[0];
      const title = r.cells[r.tb.titleCol] || r.cells.find(Boolean) || '';
      if (hasTarget && r.tcols[0] >= 0 && r.cells[r.tcols[0]]) {
        const c = r.tcols[0];
        html += `<div class="label"><span>${esc(r.tb.header[c])}</span><span class="addr">${addrOf(r.tb, r.r, c)}</span></div>`;
        html += `<div class="value${T.parseNum(r.cells[c]) === T.parseNum(r.cells[c]) ? '' : ' text'}"><bdi>${esc(r.cells[c])}</bdi></div>`;
        html += `<div class="sub"><strong>${hl(title)}</strong> · ${bd(shortName(r.tb.name))}</div>`;
        const extra = targets.slice(1).map((tg, i) => {
          const cc = r.tcols[i + 1];
          return cc >= 0 && r.cells[cc] ? `<span>${esc(r.tb.header[cc])}: <b><bdi>${esc(r.cells[cc])}</bdi></b></span>` : '';
        }).join('');
        if (extra) html += `<div class="extra">${extra}</div>`;
      } else {
        html += `<div class="label"><span>נמצאה שורה אחת</span><span class="addr">${addrOf(r.tb, r.r, 0)}</span></div>`;
        html += `<div class="value text">${hl(title)}</div>`;
        html += `<div class="sub">${bd(shortName(r.tb.name))}</div>`;
      }
    } else if (hasTarget) {
      const mode = aggMode(targets[0], t0);
      const label = headerLabel(t0, targets[0]);
      if (mode === 'sum') {
        html += `<div class="label"><span>${esc(label)} · סה״כ</span></div><div class="value">${fmt(t0.sum)}</div>`;
      } else if (mode === 'range') {
        const v = t0.min === t0.max ? fmt(t0.min) : `<bdi>${fmt(t0.min)}</bdi> עד <bdi>${fmt(t0.max)}</bdi>`;
        html += `<div class="label"><span>${esc(label)}${t0.min === t0.max ? '' : ' · טווח'}</span></div><div class="value">${v}</div>`;
      } else {
        html += `<div class="label"><span>נמצאו</span></div><div class="value text">${where}</div>`;
      }
      html += mode === 'text'
        ? `<div class="sub">${esc(label)} מופיע ליד כל שורה ברשימה.</div>`
        : `<div class="sub">מתוך ${where}${targets[0].implicit ? ' · לפי עמודת המלאי' : ''}</div>`;
      const extra = targets.slice(1).map((tg, i) => {
        const t = tAgg[i + 1];
        if (!t.filled) return '';
        const m = aggMode(tg, t), lb = esc(headerLabel(t, tg));
        if (m === 'sum') return `<span>${lb} סה״כ: <b>${fmt(t.sum)}</b></span>`;
        if (m === 'range') return `<span>${lb}: <b>${fmt(t.min)}–${fmt(t.max)}</b></span>`;
        return '';
      }).join('');
      if (extra) html += `<div class="extra">${extra}</div>`;
    } else {
      html += `<div class="label"><span>נמצאו</span></div><div class="value text">${where}</div>`;
      if (targets.length && !targets[0].implicit) html += `<div class="sub">לא מצאנו עמודת "${esc(targets[0].word)}" בשורות האלה.</div>`;
    }
    html += '</section>';

    // --- פירוט לפי קובץ ---
    if (nFiles > 1 || state.only) {
      const mode = hasTarget ? aggMode(targets[0], t0) : 'text';
      const list = [...perFile.entries()].sort((a, b) => (mode === 'sum' ? b[1].sum - a[1].sum : b[1].count - a[1].count));
      const top = list.slice(0, 12);
      html += '<div class="breakdown"><h3>' + (state.only ? 'מסונן לקובץ אחד' : 'לפי קובץ — לחצו כדי לצמצם') + '</h3><div class="chips">';
      if (state.only) html += `<button type="button" class="chip on" data-only="">${bd(shortName(top[0] ? top[0][1].name : ''))} ✕</button>`;
      else {
        for (const [path, pf] of top) {
          html += `<button type="button" class="chip" data-only="${esc(path)}" title="${esc(path)}">${bd(shortName(pf.name))}<b>${mode === 'sum' ? fmt(pf.sum) : fmt(pf.count)}</b></button>`;
        }
        if (list.length > top.length) html += `<span>ועוד ${fmt(list.length - top.length)} קבצים</span>`;
      }
      html += '</div></div>';
    }

    // --- השורות ---
    const ms = Math.round(performance.now() - S.t0);
    let allRows = 0;
    for (const f of state.files.values()) allRows += f.rows;
    html += `<div class="results-head"><span>${total === 1 ? 'השורה שנמצאה' : `השורות המתאימות ביותר (${fmt(shown.length)} מתוך ${fmt(total)})`}</span><span>נסרקו ${rowsTxt(allRows)} ב-${fmt(ms)} אלפיות שנייה</span></div>`;
    html += '<div class="hits">';
    shown.forEach((r, idx) => {
      const tb = r.tb;
      const tset = new Set(r.tcols.filter((c) => c >= 0));
      const title = r.cells[tb.titleCol] || r.cells.find(Boolean) || '';
      const order = [...r.mc.filter((c) => c !== tb.titleCol && !tset.has(c))];
      for (let c = 0; c < r.cells.length && order.length < 4; c++) {
        if (c === tb.titleCol || tset.has(c) || order.includes(c) || !r.cells[c] || tb.kinds[c] === 'date' || tb.kinds[c] === 'id') continue;
        order.push(c);
      }
      const fields = order.slice(0, 5).map((c) => `<span>${esc(tb.header[c])}: <em>${hl(r.cells[c])}</em></span>`).join('');
      let tv = '';
      r.tcols.forEach((c) => {
        if (c < 0 || !r.cells[c]) return;
        const zero = T.parseNum(r.cells[c]) === 0;
        tv += `<small>${esc(tb.header[c])}</small><b${zero ? ' class="zero"' : ''}><bdi>${esc(r.cells[c])}</bdi></b>`;
      });
      html += `<div class="hit"><button type="button" data-row="${idx}" aria-expanded="false">` +
        `<span class="title">${hl(title)}</span>` +
        (tv ? `<span class="tv">${tv}</span>` : '') +
        (fields ? `<span class="fields">${fields}</span>` : '') +
        `<span class="src">${bd(tb.path)} › ${esc(tb.sheet)} › שורה ${fmt(tb.firstRow + r.r)}</span>` +
        '</button></div>';
    });
    html += '</div>';
    if (total > shown.length && shown.length < 500) html += '<button type="button" class="btn more" id="moreBtn">הצגת עוד שורות</button>';
    $('out').innerHTML = html;
    state.shown = shown;
    state.hl = hl;
  }

  function toggleDetail(btn) {
    const host = btn.parentElement;
    const open = host.querySelector('.detail');
    if (open) { open.remove(); btn.setAttribute('aria-expanded', 'false'); return; }
    const r = state.shown[+btn.dataset.row];
    if (!r) return;
    const tset = new Set(r.tcols.filter((c) => c >= 0));
    let html = '<dl class="detail">';
    r.tb.header.forEach((h, c) => {
      const v = r.cells[c];
      if (!v) return;
      html += `<dt><span class="mono">${addrOf(r.tb, r.r, c)}</span>${esc(h)}</dt><dd${tset.has(c) ? ' class="t"' : ''}>${state.hl(v)}</dd>`;
    });
    host.insertAdjacentHTML('beforeend', html + '</dl>');
    btn.setAttribute('aria-expanded', 'true');
  }

  function drawFiles() {
    const filter = T.norm($('fileFilter').value.trim());
    const all = [...state.files.values()].sort((a, b) => (a.path < b.path ? -1 : 1));
    const list = filter ? all.filter((f) => T.norm(f.path).includes(filter)) : all;
    const MAX = 300;
    let html = '';
    for (const f of list.slice(0, MAX)) {
      const dir = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/')) : '';
      const count = f.status === 'done' ? rowsTxt(f.rows) : f.status === 'error' ? '' : 'בתור…';
      html += `<div class="file"><div><div class="n">${bd(f.name)}</div>${dir ? `<div class="p">${bd(dir)}</div>` : ''}</div>` +
        `<span class="c">${count}</span><button type="button" data-del="${esc(f.path)}" title="הסרה" aria-label="הסרת הקובץ">×</button>` +
        (f.error ? `<div class="e">${esc(f.error)}${f.detail ? ` <span class="mono">(${esc(f.detail)})</span>` : ''}</div>` : '') + '</div>';
    }
    if (!list.length) html = `<div class="empty">${all.length ? 'אין קובץ בשם הזה' : 'עוד לא נטענו קבצים'}</div>`;
    else if (list.length > MAX) html += `<div class="empty">ועוד ${fmt(list.length - MAX)} קבצים — סננו לפי שם כדי לראות אותם</div>`;
    $('fileList').innerHTML = html;
    const errors = all.filter((f) => f.status === 'error').length;
    $('dlgSummary').textContent = (state.mode === 'demo' ? 'נתוני דוגמה · ' : '') + filesTxt(all.length) + (errors ? ` · ${fmt(errors)} לא נקראו` : '') +
      (state.mode === 'real' ? (state.cacheOk ? ' · שמורים בדפדפן הזה בלבד' : ' · לא נשמרים') : '');
    $('dlgRefresh').hidden = !state.dirHandle;
    $('dlgClear').textContent = 'מחיקת כל הנתונים';
    $('dlgClear').dataset.armed = '';
  }

  // ---------- אירועים ----------
  function bind() {
    let typing = 0;
    $('q').addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => { state.only = null; runSearch(); }, 160); });
    $('searchForm').addEventListener('submit', (e) => { e.preventDefault(); clearTimeout(typing); state.only = null; runSearch(); });
    $('chips').addEventListener('click', (e) => {
      const b = e.target.closest('.chip');
      if (!b) return;
      const q = $('q');
      if (b.dataset.q) q.value = b.dataset.q;
      else if (b.dataset.add) q.value = q.value.trim() ? q.value.trim().replace(/[,\s]+$/, '') + ', ' + b.dataset.add : b.dataset.add + ' ';
      state.only = null;
      runSearch();
      if (b.dataset.add) q.focus();
    });
    $('out').addEventListener('click', (e) => {
      const chip = e.target.closest('[data-only]');
      if (chip) { state.only = chip.dataset.only || null; runSearch(); return; }
      if (e.target.closest('#moreBtn')) { state.limit += 100; runSearch(true); return; }
      const row = e.target.closest('[data-row]');
      if (row) toggleDetail(row);
    });
    $('btnDir').addEventListener('click', pickDir);
    $('dlgDir').addEventListener('click', pickDir);
    $('btnFiles').addEventListener('click', () => openPicker('pickFiles'));
    $('dlgFiles').addEventListener('click', () => openPicker('pickFiles'));
    $('btnDemo').addEventListener('click', loadDemo);
    $('dlgRefresh').addEventListener('click', refreshDir);
    $('pickFiles').addEventListener('change', (e) => fromInput(e.target));
    $('pickDir').addEventListener('change', (e) => fromInput(e.target));
    $('sourcesBtn').addEventListener('click', () => { $('fileFilter').value = ''; drawFiles(); $('sources').showModal(); });
    $('dlgClose').addEventListener('click', () => $('sources').close());
    $('sources').addEventListener('click', (e) => { if (e.target === $('sources')) $('sources').close(); });
    $('fileFilter').addEventListener('input', drawFiles);
    $('fileList').addEventListener('click', (e) => { const b = e.target.closest('[data-del]'); if (b) dropFile(b.dataset.del, true); });
    $('dlgClear').addEventListener('click', (e) => {
      const b = e.currentTarget;
      if (!b.dataset.armed) { b.dataset.armed = '1'; b.textContent = 'בטוח? לחצו שוב למחיקה'; return; }
      clearAll();
    });
    let depth = 0;
    window.addEventListener('dragenter', (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { depth++; $('veil').hidden = false; } });
    window.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; $('veil').hidden = true; } });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => { depth = 0; onDrop(e); });
  }

  bind();
  startWorkers();
  drawStatus();
  restore();
  window.__shlif = state; // לבדיקות
})();
