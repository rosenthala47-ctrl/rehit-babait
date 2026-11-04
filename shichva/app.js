/* ============================================================
   יומן השכבה — app.js
   Local-first. Optional keyless cloud sync so the whole grade
   shares one dataset from one link.
   ============================================================ */
'use strict';

/* ---------------- utils ---------------- */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
const uid = (p = '') => p + Math.random().toString(36).slice(2, 9) + Date.now().toString(36).slice(-4);
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const nowTs = () => Date.now();
const sleep = ms => new Promise(r => setTimeout(r, ms));

const HE_DAYS = ['ראשון','שני','שלישי','רביעי','חמישי','שישי','שבת'];
const z2 = n => String(n).padStart(2, '0');

function dateKey(d = new Date()) { return `${d.getFullYear()}-${z2(d.getMonth()+1)}-${z2(d.getDate())}`; }
function keyToDate(k) { const [y,m,d] = String(k).split('-').map(Number); return new Date(y, m-1, d); }
function heDate(k)      { const d = keyToDate(k); return `יום ${HE_DAYS[d.getDay()]}, ${d.getDate()}.${d.getMonth()+1}.${d.getFullYear()}`; }
function heDateShort(k) { const d = keyToDate(k); return `יום ${HE_DAYS[d.getDay()]} · ${d.getDate()}.${d.getMonth()+1}`; }
function hhmm(ms) {
  const t = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60);
  if (h > 0) return `${h} שעות ו-${m} דק׳`;
  if (m > 0) return `${m} דקות`;
  return 'פחות מדקה';
}
function pctColor(p) {
  if (p < 34) return 'var(--green)';
  if (p < 67) return 'var(--amber)';
  return 'var(--red)';
}

/* ---------------- param types ---------------- */
const PTYPES = {
  scale:   { label: 'סקאלה באחוזים (0–100)', icon: '📊', hint: 'סליידר. הסיכום מציג ממוצע באחוזים.' },
  counter: { label: 'מונה — כמה פעמים',       icon: '🔢', hint: 'כפתור +. הסיכום מציג את ממוצע הדיווחים.' },
  bool:    { label: 'כן / לא',                 icon: '✅', hint: 'הסיכום מציג את אחוז ה"כן".' },
  text:    { label: 'טקסט / ציטוט',            icon: '💬', hint: 'כל הציטוטים נאספים לסיכום היום.' },
  image:   { label: 'תמונה',                   icon: '📸', hint: 'כל התמונות נאספות לגלריה של היום.' },
};
const EMOJIS = ['📚','😴','💬','📸','6️⃣','🔥','😂','🤯','📝','🧪','🧮','🌍','🕍','🎨','⚽','💻','🎵','🗣️','⏰','🥱','🤓','💤','🎯','❓','⭐','🧠','📖','✏️'];

/* ---------------- storage keys ---------------- */
const LS = {
  core:   'shichva.core.v1',
  me:     'shichva.me.v1',
  cloud:  'shichva.cloud.v1',
  day:    dk  => `shichva.day.${dk}`,
  photo:  ref => `shichva.ph.${ref}`,
  phIdx:  'shichva.phidx',
  outbox: 'shichva.outbox.v1',
};

function lsGet(k, def = null) {
  try { const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); }
  catch (e) { return def; }
}
function lsSet(k, v) {
  try { localStorage.setItem(k, JSON.stringify(v)); return true; }
  catch (e) {
    // quota — evict cached remote photos and retry once
    if (evictPhotos(12)) {
      try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e2) {}
    }
    console.warn('localStorage full', e);
    return false;
  }
}
function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }

/* photo cache (bounded LRU) */
const PH_CAP = 24;
function phCache(ref, dataUrl) {
  const idx = lsGet(LS.phIdx, []).filter(r => r !== ref);
  idx.push(ref);
  while (idx.length > PH_CAP) lsDel(LS.photo(idx.shift()));
  lsSet(LS.phIdx, idx);
  lsSet(LS.photo(ref), dataUrl);
}
function phRead(ref) { return lsGet(LS.photo(ref), null); }
function evictPhotos(n = 8) {
  const idx = lsGet(LS.phIdx, []);
  if (!idx.length) return false;
  for (let i = 0; i < n && idx.length; i++) lsDel(LS.photo(idx.shift()));
  lsSet(LS.phIdx, idx);
  return true;
}

/* ---------------- state ---------------- */
let core = null;      // {v,name,lockHour,subjects[],params[],days{},updatedAt}
let me   = null;      // {id,name}
let days = {};        // in-memory cache: dateKey -> {date, entries:{}, updatedAt}
let draft = null;     // current lesson draft {subjectId, values:{}}
let viewSubject = null, viewDate = null;

function defaultCore() {
  const t = nowTs();
  return {
    v: 1,
    name: 'יומן השכבה',
    lockHour: 22,
    subjects: [],
    params: [
      { id: 'p_bore',  name: 'רמת שיעמום',        type: 'scale',   icon: '😴', min: 0, max: 100, lowLbl: 'מרתק', highLbl: 'מוות מרוב שיעמום', order: 0, updatedAt: t },
      { id: 'p_photo', name: 'תמונה מהשיעור',     type: 'image',   icon: '📸', order: 1, updatedAt: t },
      { id: 'p_quote', name: 'ציטוט',             type: 'text',    icon: '💬', order: 2, updatedAt: t },
      { id: 'p_67',    name: 'כמה פעמים אמרו 67', type: 'counter', icon: '6️⃣', agg: 'avg', order: 3, updatedAt: t },
    ],
    days: {},
    updatedAt: t,
  };
}
const SEED_SUBJECTS = [
  ['לשון', '📝'], ['הסטוריה', '🌍'], ['תנ״ך', '🕍'], ['מתמטיקה', '🧮'],
  ['אנגלית', '🗣️'], ['ספרות', '📖'], ['אזרחות', '⚖️'], ['ביולוגיה', '🧪'],
];

function saveCore()  { core.updatedAt = core.updatedAt || nowTs(); lsSet(LS.core, core); }
function saveMe()    { lsSet(LS.me, me); }
function saveDay(dk) { if (days[dk]) lsSet(LS.day(dk), days[dk]); }

const activeSubjects = () => (core.subjects || []).filter(s => !s.deleted).sort((a,b) => (a.order??0) - (b.order??0));
const activeParams   = () => (core.params   || []).filter(p => !p.deleted).sort((a,b) => (a.order??0) - (b.order??0));
const subjectById = id => (core.subjects || []).find(s => s.id === id);
const paramById   = id => (core.params   || []).find(p => p.id === id);

/* ---------------- day window ---------------- */
const lockHour  = () => core?.lockHour ?? 22;
const todayKey  = () => dateKey();
function isFillOpen() { return new Date().getHours() < lockHour(); }
function activeDayKey() { return isFillOpen() ? todayKey() : null; }
function msToLock() { const d = new Date(), t = new Date(d); t.setHours(lockHour(), 0, 0, 0); return t - d; }
function msToMidnight() { const d = new Date(), t = new Date(d); t.setHours(24, 0, 0, 0); return t - d; }

/* ============================================================
   CLOUD — keyless shared storage
   ============================================================ */
const BACKENDS = {
  jb: {
    label: 'jsonblob.com',
    async create(obj) {
      const r = await fetch('https://jsonblob.com/api/jsonBlob', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(obj),
      });
      if (!r.ok) throw new Error('create ' + r.status);
      // the blob id is the last path segment of Location (or the X-jsonblob header)
      const loc = r.headers.get('Location') || r.headers.get('X-jsonblob') || '';
      const id = String(loc).split('?')[0].replace(/\/+$/, '').split('/').pop();
      if (!id || id === 'jsonBlob' || !/^[A-Za-z0-9_-]+$/.test(id)) throw new Error('id-not-exposed');
      return 'jb:' + id;
    },
    async read(ref) {
      const r = await fetch('https://jsonblob.com/api/jsonBlob/' + ref.slice(3), {
        headers: { 'Accept': 'application/json' }, cache: 'no-store',
      });
      if (r.status === 404) return null;
      if (!r.ok) throw new Error('read ' + r.status);
      return r.json();
    },
    async write(ref, obj) {
      const r = await fetch('https://jsonblob.com/api/jsonBlob/' + ref.slice(3), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(obj),
      });
      if (!r.ok) throw new Error('write ' + r.status);
      return true;
    },
  },
  kv: {
    label: 'kvdb.io',
    async create(obj) {
      const r = await fetch('https://kvdb.io/', { method: 'POST', body: '' });
      if (!r.ok) throw new Error('bucket ' + r.status);
      const bucket = (await r.text()).trim();
      if (!bucket || /\s/.test(bucket)) throw new Error('bad-bucket');
      const key = uid('k');
      const ref = 'kv:' + bucket + '/' + key;
      await this.write(ref, obj);
      return ref;
    },
    async read(ref) {
      const r = await fetch('https://kvdb.io/' + ref.slice(3), { cache: 'no-store' });
      if (r.status === 404) return null;
      if (!r.ok) throw new Error('read ' + r.status);
      const t = await r.text();
      if (!t) return null;
      return JSON.parse(t);
    },
    async write(ref, obj) {
      const r = await fetch('https://kvdb.io/' + ref.slice(3), { method: 'PUT', body: JSON.stringify(obj) });
      if (!r.ok) throw new Error('write ' + r.status);
      return true;
    },
  },
};
const refBackend = ref => BACKENDS[String(ref || '').slice(0, 2)];

const Cloud = {
  coreRef: null,
  status: 'local',     // local | busy | ok | off
  lastErr: '',
  lastSync: 0,
  probing: false,
};

function cloudOn() { return !!Cloud.coreRef; }
function setStatus(s, err = '') {
  Cloud.status = s; Cloud.lastErr = err;
  const dot = $('#syncDot');
  if (dot) dot.className = 'sync-dot ' + (s === 'ok' ? 'ok' : s === 'busy' ? 'busy' : s === 'off' ? 'off' : '');
  if (typeof Nav !== 'undefined' && Nav.cur() === 'settings') renderSyncCard();
}

async function cloudRead(ref)      { const b = refBackend(ref); if (!b) throw new Error('bad-ref'); return b.read(ref); }
async function cloudWrite(ref, o)  { const b = refBackend(ref); if (!b) throw new Error('bad-ref'); return b.write(ref, o); }
async function cloudCreate(obj, hint) {
  const order = hint ? [hint, ...Object.keys(BACKENDS).filter(k => k !== hint)] : Object.keys(BACKENDS);
  let last = '';
  for (const k of order) {
    try { return await BACKENDS[k].create(obj); }
    catch (e) { last = k + ': ' + e.message; console.warn('backend', k, e); }
  }
  throw new Error(last || 'no-backend');
}

/* ---- merge helpers ---- */
function mergeList(a = [], b = []) {
  const m = new Map();
  for (const it of [...(a||[]), ...(b||[])]) {
    if (!it || !it.id) continue;
    const prev = m.get(it.id);
    if (!prev || (it.updatedAt || 0) >= (prev.updatedAt || 0)) m.set(it.id, it);
  }
  return Array.from(m.values()).sort((x, y) => (x.order ?? 0) - (y.order ?? 0));
}
function mergeDayRec(l, r) {
  if (!l) return r; if (!r) return l;
  const newer = (r.updatedAt || 0) >= (l.updatedAt || 0) ? r : l;
  const older = newer === r ? l : r;
  const out = { ...older, ...newer };
  // deterministic convergence if two devices created different blobs for the same day
  if (l.blob && r.blob && l.blob !== r.blob) out.blob = [l.blob, r.blob].sort()[0];
  out.closed = !!(l.closed || r.closed);
  out.summary = newer.summary || older.summary;
  return out;
}
function mergeCore(local, remote) {
  if (!remote || typeof remote !== 'object') return local;
  const newer = (remote.updatedAt || 0) > (local.updatedAt || 0) ? remote : local;
  const out = {
    v: 1,
    name: newer.name || local.name,
    lockHour: newer.lockHour ?? local.lockHour,
    subjects: mergeList(local.subjects, remote.subjects),
    params:   mergeList(local.params,   remote.params),
    days: {},
    updatedAt: Math.max(local.updatedAt || 0, remote.updatedAt || 0),
  };
  const keys = new Set([...Object.keys(local.days || {}), ...Object.keys(remote.days || {})]);
  for (const k of keys) out.days[k] = mergeDayRec((local.days || {})[k], (remote.days || {})[k]);
  return out;
}
function mergeEntries(a = {}, b = {}) {
  const out = { ...a };
  for (const [id, e] of Object.entries(b || {})) {
    const prev = out[id];
    if (!prev || (e.updatedAt || 0) >= (prev.updatedAt || 0)) out[id] = e;
  }
  return out;
}

/* ---- core push/pull ---- */
let pushTimer = null, pushing = false;
function schedulePushCore(delay = 700) {
  if (!cloudOn()) return;
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => pushCore().catch(() => {}), delay);
}
async function pushCore() {
  if (!cloudOn() || pushing) return;
  pushing = true; setStatus('busy');
  try {
    const remote = await cloudRead(Cloud.coreRef).catch(() => null);
    core = mergeCore(core, remote);
    core.updatedAt = nowTs();
    saveCore();
    await cloudWrite(Cloud.coreRef, core);
    Cloud.lastSync = nowTs(); setStatus('ok');
  } catch (e) {
    setStatus('off', e.message);
  } finally { pushing = false; }
}
async function pullCore() {
  if (!cloudOn()) return;
  setStatus('busy');
  try {
    const remote = await cloudRead(Cloud.coreRef);
    if (remote) { core = mergeCore(core, remote); saveCore(); }
    Cloud.lastSync = nowTs(); setStatus('ok');
  } catch (e) { setStatus('off', e.message); }
}

/* ---- day load / push ---- */
async function loadDay(dk, { remote = true } = {}) {
  if (!days[dk]) days[dk] = lsGet(LS.day(dk), { date: dk, entries: {}, updatedAt: 0 });
  const rec = core.days[dk];
  if (remote && cloudOn() && rec && rec.blob) {
    try {
      const r = await cloudRead(rec.blob);
      if (r && r.entries) {
        days[dk] = { date: dk, entries: mergeEntries(days[dk].entries, r.entries), updatedAt: Math.max(days[dk].updatedAt || 0, r.updatedAt || 0) };
        saveDay(dk);
      }
      Cloud.lastSync = nowTs(); setStatus('ok');
    } catch (e) { setStatus('off', e.message); }
  }
  return days[dk];
}

async function ensureDayRef(dk) {
  let rec = core.days[dk];
  if (rec && rec.blob) return rec.blob;
  const ref = await cloudCreate({ date: dk, entries: {}, updatedAt: nowTs() }, lsGet(LS.cloud, {}).backend);
  core.days[dk] = { ...(rec || {}), blob: ref, closed: false, updatedAt: nowTs() };
  core.updatedAt = nowTs(); saveCore();
  await pushCore();                       // write + merge-back detects a concurrent create
  return core.days[dk].blob || ref;
}

/* outbox: date keys that still have unsynced local entries */
const obGet = () => lsGet(LS.outbox, []);
const obAdd = dk => { const o = obGet(); if (!o.includes(dk)) { o.push(dk); lsSet(LS.outbox, o); } };
const obDel = dk => lsSet(LS.outbox, obGet().filter(d => d !== dk));

async function pushDay(dk) {
  if (!cloudOn()) return false;
  setStatus('busy');
  try {
    const ref = await ensureDayRef(dk);
    for (let attempt = 0; attempt < 3; attempt++) {
      const remote = await cloudRead(ref).catch(() => null);
      const merged = mergeEntries(remote?.entries || {}, days[dk].entries || {});
      days[dk] = { date: dk, entries: merged, updatedAt: nowTs() };
      saveDay(dk);
      try { await cloudWrite(ref, days[dk]); obDel(dk); Cloud.lastSync = nowTs(); setStatus('ok'); return true; }
      catch (e) { if (attempt === 2) throw e; await sleep(400 * (attempt + 1)); }
    }
  } catch (e) { obAdd(dk); setStatus('off', e.message); }
  return false;
}
async function flushOutbox() {
  if (!cloudOn()) return;
  for (const dk of obGet()) { await pushDay(dk); }
  await pushPendingPhotos();
}

/* ---- photos ---- */
async function uploadPhoto(dataUrl) {
  const ref = await cloudCreate({ img: dataUrl, at: nowTs() }, lsGet(LS.cloud, {}).backend);
  phCache(ref, dataUrl);
  return ref;
}
const PEND = 'shichva.pendph';
function pendAdd(localId, dataUrl, dk, entryId, paramId) {
  const p = lsGet(PEND, {});
  p[localId] = { dataUrl, dk, entryId, paramId, at: nowTs() };
  lsSet(PEND, p);
}
function pendDel(localId) { const p = lsGet(PEND, {}); delete p[localId]; lsSet(PEND, p); }
function pendRead(localId) { return (lsGet(PEND, {})[localId] || {}).dataUrl || null; }

async function pushPendingPhotos() {
  if (!cloudOn()) return;
  const p = lsGet(PEND, {});
  for (const [localId, rec] of Object.entries(p)) {
    try {
      const ref = await uploadPhoto(rec.dataUrl);
      const d = days[rec.dk] || lsGet(LS.day(rec.dk), null);
      if (d && d.entries[rec.entryId] && d.entries[rec.entryId].values[rec.paramId] === localId) {
        d.entries[rec.entryId].values[rec.paramId] = ref;
        d.entries[rec.entryId].updatedAt = nowTs();
        days[rec.dk] = d; saveDay(rec.dk);
        await pushDay(rec.dk);
      }
      pendDel(localId);
    } catch (e) { setStatus('off', e.message); return; }
  }
}

/* resolve a photo value (ref or local id) to a data URL */
const photoMem = new Map();
async function photoUrl(v) {
  if (!v) return null;
  if (typeof v === 'string' && v.startsWith('data:')) return v;
  if (String(v).startsWith('local:')) return pendRead(v);
  if (photoMem.has(v)) return photoMem.get(v);
  const cached = phRead(v);
  if (cached) { photoMem.set(v, cached); return cached; }
  try {
    const o = await cloudRead(v);
    const url = o && o.img ? o.img : null;
    if (url) { phCache(v, url); photoMem.set(v, url); }
    return url;
  } catch (e) { return null; }
}

/* ============================================================
   SUMMARY ENGINE
   ============================================================ */
function aggregate(p, list) {
  const pick = e => (e.values || {})[p.id];
  const have = list.filter(e => { const v = pick(e); return v !== undefined && v !== null && v !== ''; });
  const n = have.length;

  if (p.type === 'scale') {
    if (!n) return { n: 0 };
    const nums = have.map(e => Number(pick(e))).filter(v => !isNaN(v));
    if (!nums.length) return { n: 0 };
    const avg = nums.reduce((a, b) => a + b, 0) / nums.length;
    const min = p.min ?? 0, max = p.max ?? 100;
    return { n: nums.length, avg, pct: clamp(Math.round(((avg - min) / Math.max(1, max - min)) * 100), 0, 100),
             lo: Math.min(...nums), hi: Math.max(...nums) };
  }
  if (p.type === 'counter') {
    if (!n) return { n: 0 };
    const nums = have.map(e => Number(pick(e))).filter(v => !isNaN(v));
    if (!nums.length) return { n: 0 };
    const sum = nums.reduce((a, b) => a + b, 0);
    const avg = sum / nums.length;
    const agg = p.agg || 'avg';
    const value = agg === 'sum' ? sum : agg === 'max' ? Math.max(...nums) : Math.round(avg);
    return { n: nums.length, sum, avg, value, lo: Math.min(...nums), hi: Math.max(...nums) };
  }
  if (p.type === 'bool') {
    if (!n) return { n: 0 };
    const yes = have.filter(e => pick(e) === true || pick(e) === 'yes').length;
    return { n, yes, pct: Math.round((yes / n) * 100) };
  }
  if (p.type === 'text') {
    return { n, items: have.map(e => ({ text: pick(e), by: e.author, subjectId: e.subjectId })) };
  }
  if (p.type === 'image') {
    return { n, items: have.map(e => ({ v: pick(e), by: e.author, subjectId: e.subjectId })) };
  }
  return { n };
}

function computeSummary(dk, dayObj) {
  const entries = Object.values((dayObj && dayObj.entries) || {}).filter(e => e && !e.deleted);
  const subs = activeSubjects(), pars = activeParams();
  const bySub = {}, people = new Set();
  for (const e of entries) {
    if (e.authorId) people.add(e.authorId);
    (bySub[e.subjectId] = bySub[e.subjectId] || []).push(e);
  }
  const perSubject = {};
  for (const s of subs) {
    const list = bySub[s.id] || [];
    const pv = {};
    for (const p of pars) pv[p.id] = aggregate(p, list);
    perSubject[s.id] = { fills: list.length, people: new Set(list.map(e => e.authorId)).size, params: pv,
                         name: s.name, teacher: s.teacher || '', icon: s.icon || '📚' };
  }
  const totals = {};
  for (const p of pars) {
    const a = aggregate(p, entries);
    if (p.type === 'counter') {
      // each lesson's value estimates that lesson's real count → day total = sum over lessons
      let tot = 0, lessons = 0;
      for (const s of subs) {
        const v = perSubject[s.id].params[p.id];
        if (v && v.n) { tot += (v.value || 0); lessons++; }
      }
      a.dayTotal = tot; a.lessons = lessons;
    }
    totals[p.id] = a;
  }
  // snapshot of names so history stays readable even if a lesson is renamed/removed later
  const paramMeta = {};
  for (const p of pars) paramMeta[p.id] = { name: p.name, icon: p.icon, type: p.type, unit: p.unit || '', agg: p.agg || 'avg' };

  return { date: dk, entries: entries.length, participants: people.size, perSubject, totals, paramMeta, computedAt: nowTs() };
}

/* A frozen summary keeps its own snapshot of lesson/param names, so history stays
   readable even after a lesson is renamed, a teacher changes, or a param is deleted. */
function paramsOf(sum) {
  const meta = sum && sum.paramMeta;
  if (!meta || !Object.keys(meta).length) return activeParams();
  const cur = new Map(activeParams().map(p => [p.id, p]));
  const ord = activeParams().map(p => p.id);
  return Object.entries(meta).map(([id, m]) => {
    const c = cur.get(id) || {};
    return { ...c, id, name: m.name || c.name || 'פרמטר', icon: m.icon || c.icon || '\u2022',
             type: m.type || c.type, unit: m.unit || c.unit || '', agg: m.agg || c.agg || 'avg',
             min: c.min ?? 0, max: c.max ?? 100 };
  }).sort((a, b) => {
    const ia = ord.indexOf(a.id), ib = ord.indexOf(b.id);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
}
function subjectsOf(sum) {
  const keys = Object.keys((sum && sum.perSubject) || {});
  if (!keys.length) return activeSubjects();
  const ord = activeSubjects().map(s => s.id);
  return keys.map(id => {
    const ps = sum.perSubject[id] || {}, s = subjectById(id) || {};
    return { id, name: ps.name || s.name || 'שיעור', teacher: ps.teacher ?? s.teacher ?? '',
             icon: ps.icon || s.icon || '📚' };
  }).sort((a, b) => {
    const ia = ord.indexOf(a.id), ib = ord.indexOf(b.id);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
}

/* live summary for a day (uses current data), or the frozen one if the day is closed */
function summaryFor(dk) {
  const rec = core.days[dk];
  if (rec && rec.closed && rec.summary) return rec.summary;
  return computeSummary(dk, days[dk]);
}

/* close every day whose fill window has passed, freezing its summary */
async function closeDueDays() {
  const tk = todayKey();
  let changed = false;
  const keys = Object.keys(core.days || {});
  for (const dk of keys) {
    const rec = core.days[dk];
    if (!rec || rec.closed) continue;
    const due = dk < tk || (dk === tk && !isFillOpen());
    if (!due) continue;
    await loadDay(dk);                       // make sure we have the entries before freezing
    rec.summary = computeSummary(dk, days[dk]);
    rec.closed = true;
    rec.updatedAt = nowTs();
    changed = true;
  }
  if (changed) { core.updatedAt = nowTs(); saveCore(); schedulePushCore(200); }
  return changed;
}

/* ============================================================
   NAV
   ============================================================ */
const Nav = {
  stack: ['home'],
  cur() { return this.stack[this.stack.length - 1]; },
  go(s) { if (this.cur() !== s) this.stack.push(s); this.paint(); },
  replace(s) { this.stack[this.stack.length - 1] = s; this.paint(); },
  back() { if (this.stack.length > 1) this.stack.pop(); this.paint(); },
  home() { this.stack = ['home']; this.paint(); },
  paint() {
    const s = this.cur();
    const map = { home: '#scHome', lesson: '#scLesson', day: '#scDay', history: '#scHistory', settings: '#scSettings' };
    $$('.screen').forEach(el => el.classList.remove('on'));
    const el = $(map[s]); if (el) el.classList.add('on');
    $('#btnBack').style.display = this.stack.length > 1 ? 'grid' : 'none';
    $('#fabRow') && ($('#fabRow').style.display = s === 'home' ? 'flex' : 'none');
    window.scrollTo(0, 0);
  },
};

/* ============================================================
   TOAST / SHEET
   ============================================================ */
let toastT = null;
function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast on ' + kind;
  clearTimeout(toastT);
  toastT = setTimeout(() => { t.className = 'toast ' + kind; }, 2600);
}
function sheet(html) {
  $('#sheet').innerHTML = `<div class="sheet-grip"></div>` + html;
  $('#sheetWrap').classList.add('on');
}
function closeSheet() { $('#sheetWrap').classList.remove('on'); }

/* ============================================================
   RENDER — HEADER + HOME
   ============================================================ */
function renderHeader() {
  $('#brandName').textContent = core.name || 'יומן השכבה';
  const open = isFillOpen();
  $('#brandSub').textContent = cloudOn()
    ? (Cloud.status === 'ok' ? 'מחובר · משותף לשכבה' : Cloud.status === 'busy' ? 'מסנכרן…' : 'אין חיבור — נשמר במכשיר')
    : 'מקומי במכשיר הזה';
  $('#brandLogo').textContent = open ? '📓' : '🌙';
}

function renderDayBar() {
  const open = isFillOpen();
  const bar = $('#dayBar');
  bar.classList.toggle('locked', !open);
  $('#dayBarIc').textContent = open ? '☀️' : '🌙';
  $('#dayBarDate').textContent = heDate(todayKey());
  if (open) {
    $('#dayBarSub').innerHTML = `אפשר למלא עוד <strong>${esc(hhmm(msToLock()))}</strong> · עד ${z2(lockHour())}:00`;
  } else {
    $('#dayBarSub').innerHTML = `היום נסגר ב-${z2(lockHour())}:00 · <strong>הסיכום מוכן</strong> · יום חדש בעוד ${esc(hhmm(msToMidnight()))}`;
  }
}

function renderHome() {
  renderHeader(); renderDayBar();
  const subs = activeSubjects();
  const dk = todayKey();
  const sum = summaryFor(dk);
  const myId = me.id;
  const todayEntries = (days[dk] && days[dk].entries) || {};
  const firstScale = activeParams().find(p => p.type === 'scale');

  $('#lessonsCount').textContent = subs.length ? `${subs.length} שיעורים` : '';

  if (!subs.length) {
    $('#lessonsList').innerHTML = `
      <div class="empty">
        <div class="empty-ic">📚</div>
        <h3>אין עוד שיעורים</h3>
        <p>הוסיפו את השיעורים של השכבה ואת שם המורה של כל אחד, ואז אפשר להתחיל למלא.</p>
        <div class="btn-row" style="max-width:300px;margin:16px auto 0">
          <button class="btn primary" data-act="add-lesson">➕ הוסף שיעור</button>
        </div>
        <div class="btn-row" style="max-width:300px;margin:8px auto 0">
          <button class="btn ghost sm" data-act="seed" style="width:100%">או הוסף רשימת שיעורים נפוצה</button>
        </div>
      </div>`;
  } else {
    $('#lessonsList').innerHTML = subs.map(s => {
      const ps = sum.perSubject[s.id] || { fills: 0, params: {} };
      const mine = !!todayEntries[`${myId}:${s.id}`];
      let right = '<div class="lesson-pct" style="color:var(--dim)">—</div>';
      if (firstScale && ps.params[firstScale.id] && ps.params[firstScale.id].n) {
        const p = ps.params[firstScale.id].pct;
        right = `<div class="lesson-pct" style="color:${pctColor(p)}"><span class="rtl-num">${p}%</span></div>`;
      }
      return `
      <button class="lesson" data-act="open-lesson" data-id="${esc(s.id)}">
        <div class="lesson-ic">${esc(s.icon || '📚')}</div>
        <div class="lesson-main">
          <div class="lesson-name">${esc(s.name)} ${mine ? '<span class="badge-me">מילאתי</span>' : ''}</div>
          <div class="lesson-teacher">${s.teacher ? 'מורה: ' + esc(s.teacher) : 'בלי מורה'}</div>
        </div>
        <div class="lesson-right">
          ${right}
          <div class="lesson-fills">${ps.fills ? ps.fills + ' מילויים' : 'אף אחד עוד'}</div>
        </div>
        <div class="chev">‹</div>
      </button>`;
    }).join('');
  }

  // quick actions
  const acts = $('#homeActions');
  if (acts) {
    acts.innerHTML = subs.length
      ? `<button class="btn" data-act="add-lesson">➕ שיעור</button>
         <button class="btn" data-act="open-day" data-date="${esc(dk)}">📊 סיכום היום</button>`
      : '';
  }

  // today's running summary teaser
  const wrap = $('#todaySummaryWrap');
  if (!subs.length || !sum.entries) { wrap.innerHTML = ''; return; }
  wrap.innerHTML = `
    <div class="sec-head"><h2>${isFillOpen() ? 'הסיכום עד עכשיו' : 'סיכום היום'}</h2></div>
    ${summaryCardHTML(sum, { compact: true })}
    <div class="btn-row"><button class="btn" data-act="open-day" data-date="${esc(dk)}">הסיכום המלא של היום ›</button></div>`;
}

/* ============================================================
   RENDER — SUMMARY BLOCKS
   ============================================================ */
function summaryCardHTML(sum, { compact = false } = {}) {
  const pars = paramsOf(sum).filter(p => (sum.totals[p.id] || {}).n || p.type === 'counter');
  const rows = pars.map(p => {
    const t = sum.totals[p.id] || { n: 0 };
    let val = '—', meta = 'אין נתונים', bar = '';
    if (p.type === 'scale' && t.n) {
      val = `<span class="rtl-num" style="color:${pctColor(t.pct)}">${t.pct}%</span>`;
      meta = `ממוצע של ${t.n} דיווחים`;
      bar = `<div class="bar"><i style="width:${t.pct}%;background:${pctColor(t.pct)}"></i></div>`;
    } else if (p.type === 'counter' && (t.dayTotal || t.n)) {
      val = `<span class="rtl-num" style="color:var(--lime)">${t.dayTotal || 0}</span>`;
      meta = t.lessons ? `סך הכול ב-${t.lessons} שיעורים` : 'אין דיווחים';
    } else if (p.type === 'bool' && t.n) {
      val = `<span class="rtl-num" style="color:var(--cyan)">${t.pct}%</span>`;
      meta = `${t.yes} מתוך ${t.n} אמרו כן`;
      bar = `<div class="bar"><i style="width:${t.pct}%;background:var(--cyan)"></i></div>`;
    } else if (p.type === 'text') {
      val = `<span class="rtl-num" style="color:var(--pink)">${t.n || 0}</span>`;
      meta = (t.n || 0) === 1 ? 'ציטוט אחד' : 'ציטוטים';
    } else if (p.type === 'image') {
      val = `<span class="rtl-num" style="color:var(--cyan)">${t.n || 0}</span>`;
      meta = (t.n || 0) === 1 ? 'תמונה אחת' : 'תמונות';
    }
    return `<div class="agg-row">
      <div class="agg-ic">${esc(p.icon || '•')}</div>
      <div class="agg-main"><div class="agg-name">${esc(p.name)}</div><div class="agg-meta">${esc(meta)}</div>${bar}</div>
      <div class="agg-val">${val}</div>
    </div>`;
  }).join('');
  return `<div class="card">
    ${rows || '<div class="agg-meta">אין עוד נתונים להיום</div>'}
    ${compact ? `<div class="hint" style="margin-top:10px">${sum.participants || 0} אנשים מילאו · ${sum.entries || 0} מילויים</div>` : ''}
  </div>`;
}

/* per-lesson breakdown for one parameter */
function paramBreakdownHTML(sum, p) {
  const subs = subjectsOf(sum);
  const rows = subs.map(s => {
    const ps = sum.perSubject[s.id]; if (!ps) return null;
    const a = ps.params[p.id] || { n: 0 };
    if (!a.n) return null;
    let val = '', bar = '', meta = '';
    if (p.type === 'scale') {
      val = `<span class="rtl-num" style="color:${pctColor(a.pct)}">${a.pct}%</span>`;
      bar = `<div class="bar"><i style="width:${a.pct}%;background:${pctColor(a.pct)}"></i></div>`;
      meta = `${a.n} דיווחים · טווח ${a.lo}–${a.hi}`;
      return { v: a.pct, html: row(s, val, bar, meta) };
    }
    if (p.type === 'counter') {
      val = `<span class="rtl-num" style="color:var(--lime)">${a.value}</span>`;
      meta = a.n > 1 ? `ממוצע של ${a.n} דיווחים (${a.lo}–${a.hi})` : `דיווח אחד`;
      return { v: a.value, html: row(s, val, '', meta) };
    }
    if (p.type === 'bool') {
      val = `<span class="rtl-num" style="color:var(--cyan)">${a.pct}%</span>`;
      bar = `<div class="bar"><i style="width:${a.pct}%;background:var(--cyan)"></i></div>`;
      meta = `${a.yes} מתוך ${a.n}`;
      return { v: a.pct, html: row(s, val, bar, meta) };
    }
    val = `<span class="rtl-num" style="color:var(--pink)">${a.n}</span>`;
    return { v: a.n, html: row(s, val, '', p.type === 'text' ? 'ציטוטים' : 'תמונות') };
  }).filter(Boolean).sort((x, y) => y.v - x.v);

  function row(s, val, bar, meta) {
    return `<div class="agg-row">
      <div class="agg-ic">${esc(s.icon || '📚')}</div>
      <div class="agg-main">
        <div class="agg-name">${esc(s.name)}${s.teacher ? ` <span style="color:var(--dim);font-weight:400">· ${esc(s.teacher)}</span>` : ''}</div>
        <div class="agg-meta">${esc(meta)}</div>${bar}
      </div>
      <div class="agg-val">${val}</div>
    </div>`;
  }
  if (!rows.length) return '';
  return `<div class="sec-head"><h2>${esc(p.icon || '')} ${esc(p.name)}</h2></div>
          <div class="card">${rows.map(r => r.html).join('')}</div>`;
}

async function renderDay(dk) {
  viewDate = dk;
  const el = $('#scDay');
  const rec = core.days[dk] || {};
  el.innerHTML = `<div class="center-load"><span class="spin"></span> טוען את ${esc(heDateShort(dk))}…</div>`;
  if (!rec.closed) await loadDay(dk);
  const sum = summaryFor(dk);
  const pars = paramsOf(sum);
  const firstScale = pars.find(p => p.type === 'scale');
  const counter = pars.find(p => p.type === 'counter');
  const textP = pars.find(p => p.type === 'text');
  const imgP = pars.find(p => p.type === 'image');

  // highlights
  let topSub = null, topVal = -1;
  if (firstScale) for (const s of subjectsOf(sum)) {
    const a = (sum.perSubject[s.id] || {}).params?.[firstScale.id];
    if (a && a.n && a.pct > topVal) { topVal = a.pct; topSub = s; }
  }
  const quotes = (sum.totals[textP?.id] || {}).items || [];
  const photos = (sum.totals[imgP?.id] || {}).items || [];

  el.innerHTML = `
    <div class="daybar ${rec.closed ? 'locked' : ''}">
      <div class="daybar-ic">${rec.closed ? '✅' : '⏳'}</div>
      <div class="daybar-main">
        <div class="daybar-date">${esc(heDate(dk))}</div>
        <div class="daybar-sub">${rec.closed ? 'יום סגור · הסיכום נשמר' : 'היום עוד פתוח למילוי'} · <strong>${sum.participants || 0} אנשים</strong></div>
      </div>
    </div>

    ${firstScale && (sum.totals[firstScale.id] || {}).n ? `
      <div class="card">
        <div class="big-stat">
          <div class="bs-val"><span class="rtl-num">${sum.totals[firstScale.id].pct}%</span></div>
          <div class="bs-lbl">${esc(firstScale.name)} — ממוצע כל השכבה ביום הזה</div>
        </div>
      </div>` : ''}

    <div class="hl-grid">
      ${topSub ? `<div class="hl"><div class="hl-ic">${esc(topSub.icon || '📚')}</div><div class="hl-val"><span class="rtl-num">${topVal}%</span></div><div class="hl-lbl">הכי ${esc(firstScale.name)}<br>${esc(topSub.name)}${topSub.teacher ? ' · ' + esc(topSub.teacher) : ''}</div></div>` : ''}
      ${counter ? `<div class="hl"><div class="hl-ic">${esc(counter.icon || '🔢')}</div><div class="hl-val"><span class="rtl-num">${(sum.totals[counter.id] || {}).dayTotal || 0}</span></div><div class="hl-lbl">${esc(counter.name)}<br>בכל היום</div></div>` : ''}
      <div class="hl"><div class="hl-ic">💬</div><div class="hl-val"><span class="rtl-num">${quotes.length}</span></div><div class="hl-lbl">ציטוטים</div></div>
      <div class="hl"><div class="hl-ic">📸</div><div class="hl-val"><span class="rtl-num">${photos.length}</span></div><div class="hl-lbl">תמונות</div></div>
    </div>

    ${pars.map(p => paramBreakdownHTML(sum, p)).join('')}

    ${quotes.length ? `<div class="sec-head"><h2>💬 ציטוטי היום</h2></div><div class="card">
      ${quotes.map(q => `<div class="quote"><div class="quote-txt">"${esc(q.text)}"</div>
        <div class="quote-by">${esc((subjectById(q.subjectId) || {}).name || 'שיעור')}${q.by ? ' · העלה ' + esc(q.by) : ''}</div></div>`).join('')}
    </div>` : ''}

    ${photos.length ? `<div class="sec-head"><h2>📸 תמונות היום</h2></div><div class="card">
      <div class="gal" id="galDay">${photos.map((ph, i) => `<div class="gal-item" data-ph="${esc(String(ph.v))}" data-i="${i}"><span class="ld">…</span></div>`).join('')}</div>
    </div>` : ''}

    ${!sum.entries ? `<div class="empty"><div class="empty-ic">🫥</div><h3>אף אחד לא מילא ביום הזה</h3><p>אין נתונים לסכם.</p></div>` : ''}
  `;
  hydrateGallery('#galDay', photos);
}

async function hydrateGallery(sel, items) {
  const root = $(sel); if (!root) return;
  for (const node of $$('.gal-item', root)) {
    const i = Number(node.dataset.i);
    const url = await photoUrl(items[i].v);
    if (url) {
      node.innerHTML = `<img src="${url}" alt="תמונה מהשיעור" loading="lazy" />`;
      node.onclick = () => sheet(`<h3>תמונה מהשיעור</h3>
        <div class="sheet-sub">${esc((subjectById(items[i].subjectId) || {}).name || '')}${items[i].by ? ' · ' + esc(items[i].by) : ''}</div>
        <div class="photo-prev" style="margin-top:12px"><img src="${url}" alt="" /></div>
        <div class="btn-row"><button class="btn" data-act="close-sheet">סגור</button></div>`);
    } else {
      node.innerHTML = `<span class="ld">✕</span>`;
    }
  }
}

/* ============================================================
   RENDER — LESSON (fill form)
   ============================================================ */
function myEntryId(subjectId) { return `${me.id}:${subjectId}`; }

async function renderLesson(subjectId) {
  viewSubject = subjectId;
  const s = subjectById(subjectId);
  if (!s) { Nav.home(); return; }
  const dk = todayKey();
  const open = isFillOpen();

  await loadDay(dk);
  const existing = (days[dk].entries || {})[myEntryId(subjectId)];
  draft = { subjectId, values: { ...((existing && existing.values) || {}) } };

  $('#lessonHero').innerHTML = `
    <div class="lesson" style="padding:0;border:0">
      <div class="lesson-ic" style="width:52px;height:52px;font-size:26px">${esc(s.icon || '📚')}</div>
      <div class="lesson-main">
        <div class="lesson-name" style="font-size:19px;font-family:'Secular One',sans-serif">${esc(s.name)}</div>
        <div class="lesson-teacher">${s.teacher ? 'מורה: ' + esc(s.teacher) : 'בלי מורה'} · ${esc(heDateShort(dk))}</div>
      </div>
    </div>
    ${!open ? `<div class="note" style="margin-top:12px">🌙 <b>המילוי להיום נסגר</b> בשעה ${z2(lockHour())}:00. אפשר לראות את הסיכום, אבל לא לשנות. יום חדש מתחיל בחצות.</div>` : ''}`;

  $('#lessonMeState').textContent = existing ? 'עדכון המילוי שלי' : 'מילוי חדש';
  $('#btnSaveEntry').style.display = open ? '' : 'none';
  $('#btnSaveEntry').textContent = existing ? 'עדכן את המילוי שלי' : 'שמור את המילוי';

  renderParamsForm(open);
  renderLessonAgg(subjectId);
}

function renderParamsForm(open) {
  const pars = activeParams();
  if (!pars.length) {
    $('#paramsForm').innerHTML = `<div class="empty"><div class="empty-ic">🎛️</div><h3>אין פרמטרים</h3>
      <p>הוסיפו פרמטרים (שיעמום, ציטוט, תמונה…) בהגדרות.</p></div>`;
    return;
  }
  $('#paramsForm').innerHTML = pars.map(p => {
    const v = draft.values[p.id];
    let body = '';
    if (p.type === 'scale') {
      const min = p.min ?? 0, max = p.max ?? 100;
      const cur = v === undefined || v === '' ? Math.round((min + max) / 2) : Number(v);
      body = `<input class="slider" type="range" min="${min}" max="${max}" value="${cur}" data-pid="${esc(p.id)}" data-kind="scale" ${open ? '' : 'disabled'} />
        <div class="slider-ends"><span>${esc(p.lowLbl || min)}</span><span>${esc(p.highLbl || max)}</span></div>
        ${v === undefined || v === '' ? `<div class="hint">גררו כדי לדווח (ברירת מחדל: ${cur})</div>` : ''}`;
    } else if (p.type === 'counter') {
      const cur = Number(v || 0);
      body = `<div class="counter">
          <button class="cnt-btn" data-pid="${esc(p.id)}" data-kind="cnt" data-d="-1" ${open ? '' : 'disabled'}>−</button>
          <div class="cnt-val" data-cnt="${esc(p.id)}"><span class="rtl-num">${cur}</span></div>
          <button class="cnt-btn plus" data-pid="${esc(p.id)}" data-kind="cnt" data-d="1" ${open ? '' : 'disabled'}>+</button>
        </div><div class="cnt-tap">לחצו + בכל פעם שזה קורה</div>`;
    } else if (p.type === 'bool') {
      body = `<div class="yesno">
          <button class="btn ${v === true ? 'on-yes' : ''}" data-pid="${esc(p.id)}" data-kind="bool" data-v="1" ${open ? '' : 'disabled'}>כן</button>
          <button class="btn ${v === false ? 'on-no' : ''}" data-pid="${esc(p.id)}" data-kind="bool" data-v="0" ${open ? '' : 'disabled'}>לא</button>
        </div>`;
    } else if (p.type === 'text') {
      body = `<textarea class="inp" data-pid="${esc(p.id)}" data-kind="text" placeholder="${esc(p.ph || 'מה נאמר בשיעור?')}" maxlength="400" ${open ? '' : 'disabled'}>${esc(v || '')}</textarea>`;
    } else if (p.type === 'image') {
      body = v
        ? `<div class="photo-prev"><img id="pv_${esc(p.id)}" alt="" />${open ? `<button class="photo-x" data-pid="${esc(p.id)}" data-kind="imgdel">✕</button>` : ''}</div>`
        : (open
          ? `<button class="photo-zone" data-pid="${esc(p.id)}" data-kind="img"><div class="pz-ic">📸</div><p>צלמו או בחרו תמונה</p></button>`
          : `<div class="hint">לא הועלתה תמונה</div>`);
    }
    const valTag = p.type === 'scale'
      ? `<div class="pblock-val" data-sv="${esc(p.id)}"><span class="rtl-num">${v === undefined || v === '' ? '–' : v}${p.unit || '%'}</span></div>` : '';
    return `<div class="pblock">
      <div class="pblock-head"><span class="pblock-ic">${esc(p.icon || '•')}</span>
        <span class="pblock-name">${esc(p.name)}</span>${valTag}</div>
      ${body}
    </div>`;
  }).join('');

  // hydrate existing photo previews
  for (const p of pars) {
    if (p.type === 'image' && draft.values[p.id]) {
      photoUrl(draft.values[p.id]).then(u => { const el = $('#pv_' + p.id); if (el && u) el.src = u; });
    }
  }
}

function renderLessonAgg(subjectId) {
  const dk = todayKey();
  const sum = summaryFor(dk);
  const ps = sum.perSubject[subjectId];
  const pars = activeParams();
  if (!ps || !ps.fills) {
    $('#lessonAgg').innerHTML = `<div class="sec-head"><h2>מה השכבה מילאה</h2></div>
      <div class="card"><div class="agg-meta">אף אחד עוד לא מילא את השיעור הזה היום.</div></div>`;
    return;
  }
  const rows = pars.map(p => {
    const a = ps.params[p.id] || { n: 0 };
    if (!a.n) return '';
    let val = '', meta = '', bar = '';
    if (p.type === 'scale') { val = `<span class="rtl-num" style="color:${pctColor(a.pct)}">${a.pct}%</span>`; meta = `${a.n} דיווחים · טווח ${a.lo}–${a.hi}`; bar = `<div class="bar"><i style="width:${a.pct}%;background:${pctColor(a.pct)}"></i></div>`; }
    else if (p.type === 'counter') { val = `<span class="rtl-num" style="color:var(--lime)">${a.value}</span>`; meta = a.n > 1 ? `ממוצע של ${a.n} דיווחים (${a.lo}–${a.hi})` : 'דיווח אחד'; }
    else if (p.type === 'bool') { val = `<span class="rtl-num" style="color:var(--cyan)">${a.pct}%</span>`; meta = `${a.yes} מתוך ${a.n} אמרו כן`; bar = `<div class="bar"><i style="width:${a.pct}%;background:var(--cyan)"></i></div>`; }
    else { val = `<span class="rtl-num" style="color:var(--pink)">${a.n}</span>`; meta = p.type === 'text' ? 'ציטוטים' : 'תמונות'; }
    return `<div class="agg-row"><div class="agg-ic">${esc(p.icon || '•')}</div>
      <div class="agg-main"><div class="agg-name">${esc(p.name)}</div><div class="agg-meta">${esc(meta)}</div>${bar}</div>
      <div class="agg-val">${val}</div></div>`;
  }).join('');

  const textP = pars.find(p => p.type === 'text');
  const imgP = pars.find(p => p.type === 'image');
  const quotes = (textP && ps.params[textP.id] && ps.params[textP.id].items) || [];
  const photos = (imgP && ps.params[imgP.id] && ps.params[imgP.id].items) || [];

  $('#lessonAgg').innerHTML = `
    <div class="sec-head"><h2>מה השכבה מילאה</h2><span class="count">${ps.people} אנשים</span></div>
    <div class="card">${rows}</div>
    ${quotes.length ? `<div class="card">${quotes.map(q => `<div class="quote"><div class="quote-txt">"${esc(q.text)}"</div><div class="quote-by">${q.by ? 'העלה ' + esc(q.by) : ''}</div></div>`).join('')}</div>` : ''}
    ${photos.length ? `<div class="card"><div class="gal" id="galLesson">${photos.map((ph, i) => `<div class="gal-item" data-i="${i}"><span class="ld">…</span></div>`).join('')}</div></div>` : ''}`;
  if (photos.length) hydrateGallery('#galLesson', photos);
}

/* ---- save ---- */
async function saveEntry() {
  if (!isFillOpen()) { toast('המילוי להיום נסגר', 'bad'); return; }
  if (!draft) return;
  const dk = todayKey();
  const vals = { ...draft.values };

  // nothing filled?
  const filled = Object.entries(vals).some(([k, v]) => v !== undefined && v !== null && v !== '');
  if (!filled) { toast('לא מילאת כלום 🙃', 'bad'); return; }

  // photos: data URL -> upload (or queue locally)
  const btn = $('#btnSaveEntry');
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> שומר…';
  const eid = myEntryId(draft.subjectId);

  for (const p of activeParams()) {
    if (p.type !== 'image') continue;
    const v = vals[p.id];
    if (typeof v === 'string' && v.startsWith('data:')) {
      let done = false;
      if (cloudOn()) {
        try { vals[p.id] = await uploadPhoto(v); done = true; } catch (e) { console.warn('photo upload failed', e); }
      }
      if (!done) {
        const localId = 'local:' + uid();
        pendAdd(localId, v, dk, eid, p.id);
        vals[p.id] = localId;
      }
    }
  }

  await loadDay(dk, { remote: false });
  days[dk].entries[eid] = {
    id: eid, subjectId: draft.subjectId,
    authorId: me.id, author: me.name || 'אנונימי',
    values: vals, updatedAt: nowTs(),
    createdAt: (days[dk].entries[eid] && days[dk].entries[eid].createdAt) || nowTs(),
  };
  days[dk].updatedAt = nowTs();
  saveDay(dk);
  if (!core.days[dk]) { core.days[dk] = { closed: false, updatedAt: nowTs() }; core.updatedAt = nowTs(); }
  saveCore();
  obAdd(dk);

  btn.disabled = false;
  const ok = await pushDay(dk);
  toast(ok ? 'נשמר ושותף לשכבה ✅' : (cloudOn() ? 'נשמר במכשיר · יסונכרן כשיהיה חיבור' : 'נשמר במכשיר ✅'), ok ? 'good' : '');
  draft = null;
  Nav.back();
  renderHome();
}

/* ============================================================
   RENDER — HISTORY
   ============================================================ */
function renderHistory() {
  const keys = Object.keys(core.days || {}).sort().reverse();
  $('#histCount').textContent = keys.length ? `${keys.length} ימים` : '';
  if (!keys.length) {
    $('#histList').innerHTML = `<div class="empty"><div class="empty-ic">📅</div><h3>אין עוד היסטוריה</h3>
      <p>בכל יום שהשכבה ממלאת, הסיכום נשמר כאן בסוף היום.</p></div>`;
    return;
  }
  $('#histList').innerHTML = keys.map(dk => {
    const rec = core.days[dk] || {};
    const sum = rec.closed && rec.summary ? rec.summary : (days[dk] ? computeSummary(dk, days[dk]) : null);
    const dpars = sum ? paramsOf(sum) : activeParams();
    const firstScale = dpars.find(p => p.type === 'scale');
    const counter = dpars.find(p => p.type === 'counter');
    const pills = [];
    if (sum) {
      if (firstScale && (sum.totals[firstScale.id] || {}).n)
        pills.push(`<span class="pill" style="color:${pctColor(sum.totals[firstScale.id].pct)}">${esc(firstScale.icon || '')} ${sum.totals[firstScale.id].pct}%</span>`);
      if (counter && (sum.totals[counter.id] || {}).dayTotal)
        pills.push(`<span class="pill">${esc(counter.icon || '')} ${sum.totals[counter.id].dayTotal}</span>`);
      pills.push(`<span class="pill">👥 ${sum.participants || 0}</span>`);
    } else {
      pills.push(`<span class="pill">לא נטען</span>`);
    }
    return `<button class="hist-row" data-act="open-day" data-date="${esc(dk)}">
      <div class="hist-date">
        <div class="hist-d1">${esc(heDateShort(dk))} ${rec.closed ? '' : '<span class="badge-me">פתוח</span>'}</div>
        <div class="hist-d2">${sum ? (sum.entries || 0) + ' מילויים' : 'לחצו לטעינה'}</div>
      </div>
      <div class="hist-pills">${pills.join('')}</div>
      <div class="chev">‹</div>
    </button>`;
  }).join('');
}

/* ============================================================
   RENDER — SETTINGS
   ============================================================ */
function renderSettings() {
  $('#inpMyName').value = me.name || '';
  $('#inpGradeName').value = core.name || '';
  const sel = $('#inpLockHour');
  if (!sel.options.length) {
    sel.innerHTML = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${z2(h)}:00</option>`).join('');
  }
  sel.value = String(lockHour());

  const subs = activeSubjects();
  $('#setLessonsCount').textContent = subs.length ? `${subs.length}` : '';
  $('#setLessonsList').innerHTML = subs.length ? subs.map((s, i) => `
    <div class="mrow">
      <div class="mrow-ic">${esc(s.icon || '📚')}</div>
      <div class="mrow-main"><div class="mrow-t1">${esc(s.name)}</div>
        <div class="mrow-t2">${s.teacher ? 'מורה: ' + esc(s.teacher) : 'בלי מורה'}</div></div>
      <div class="mrow-act">
        <button class="mini-btn" data-act="mv-lesson" data-id="${esc(s.id)}" data-d="-1" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="mini-btn" data-act="mv-lesson" data-id="${esc(s.id)}" data-d="1" ${i === subs.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="mini-btn" data-act="edit-lesson" data-id="${esc(s.id)}">✏️</button>
        <button class="mini-btn" data-act="del-lesson" data-id="${esc(s.id)}">🗑️</button>
      </div>
    </div>`).join('') : `<div class="empty" style="padding:22px"><p>עוד לא הוספתם שיעורים.</p></div>`;

  const pars = activeParams();
  $('#setParamsCount').textContent = pars.length ? `${pars.length}` : '';
  $('#setParamsList').innerHTML = pars.length ? pars.map((p, i) => `
    <div class="mrow">
      <div class="mrow-ic">${esc(p.icon || '•')}</div>
      <div class="mrow-main"><div class="mrow-t1">${esc(p.name)}</div>
        <div class="mrow-t2">${esc((PTYPES[p.type] || {}).label || p.type)}</div></div>
      <div class="mrow-act">
        <button class="mini-btn" data-act="mv-param" data-id="${esc(p.id)}" data-d="-1" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="mini-btn" data-act="mv-param" data-id="${esc(p.id)}" data-d="1" ${i === pars.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="mini-btn" data-act="edit-param" data-id="${esc(p.id)}">✏️</button>
        <button class="mini-btn" data-act="del-param" data-id="${esc(p.id)}">🗑️</button>
      </div>
    </div>`).join('') : `<div class="empty" style="padding:22px"><p>אין פרמטרים.</p></div>`;

  renderSyncCard();
}

function renderSyncCard() {
  const card = $('#syncCard'); if (!card) return;
  if (!cloudOn()) {
    card.innerHTML = `
      <div class="mrow" style="padding:0 0 12px;border-bottom:1px solid var(--line)">
        <div class="mrow-ic">📱</div>
        <div class="mrow-main"><div class="mrow-t1">מצב מקומי</div>
          <div class="mrow-t2">הנתונים נשמרים רק במכשיר הזה</div></div>
      </div>
      <div class="note">כדי שכל השכבה תמלא לאותו מקום ותראה סיכום משותף, צריך ליצור <b>מרחב שכבה</b> פעם אחת. תקבלו קישור — וכל מי שייכנס דרכו ימלא לאותם נתונים.</div>
      <div class="btn-row"><button class="btn primary" data-act="create-space">🌐 צור מרחב שכבה ותן לי קישור</button></div>
      <div class="btn-row"><button class="btn ghost sm" data-act="join-space" style="width:100%">יש לי כבר קוד שכבה — חבר אותי</button></div>`;
    return;
  }
  const st = Cloud.status;
  const stTxt = st === 'ok' ? 'מחובר ומסונכרן' : st === 'busy' ? 'מסנכרן…' : st === 'off' ? 'אין חיבור כרגע' : 'מחובר';
  const stIc  = st === 'ok' ? '✅' : st === 'busy' ? '🔄' : st === 'off' ? '⚠️' : '🌐';
  const pend = obGet().length + Object.keys(lsGet(PEND, {})).length;
  card.innerHTML = `
    <div class="mrow" style="padding:0 0 12px;border-bottom:1px solid var(--line)">
      <div class="mrow-ic">${stIc}</div>
      <div class="mrow-main"><div class="mrow-t1">${esc(stTxt)}</div>
        <div class="mrow-t2">${(refBackend(Cloud.coreRef) || {}).label || '—'}${Cloud.lastSync ? ' · עודכן ' + new Date(Cloud.lastSync).toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' }) : ''}${pend ? ' · ' + pend + ' ממתינים' : ''}</div></div>
    </div>
    <div class="field" style="margin-top:12px"><label>הקישור לשיתוף בקבוצה</label>
      <div class="link-box" id="shareLink">${esc(shareUrl())}</div></div>
    <div class="btn-row">
      <button class="btn primary" data-act="share-link">📤 שתף</button>
      <button class="btn" data-act="copy-link">העתק</button>
    </div>
    <div class="btn-row"><button class="btn ghost sm" data-act="sync-now" style="width:100%">🔄 סנכרן עכשיו</button></div>
    ${st === 'off' ? `<div class="note">⚠️ <b>לא מצליח להתחבר</b> לאחסון המשותף (${esc(Cloud.lastErr || '')}). מה שמילאתם נשמר במכשיר ויישלח אוטומטית כשהחיבור יחזור.</div>` : ''}
    <div class="note">כל מי שיש לו הקישור יכול לראות ולמלא. אל תעלו דברים שלא מתאים שיראו.</div>`;
}

function shareUrl() {
  const base = location.origin + location.pathname;
  return base + '#g=' + String(Cloud.coreRef || '').replace(':', '.');
}

/* ============================================================
   IMAGE
   ============================================================ */
async function fileToDataUrl(file, maxDim = 620, quality = 0.56) {
  let bmp = null;
  try { bmp = await createImageBitmap(file, { imageOrientation: 'from-image' }); }
  catch (e) {
    bmp = await new Promise((res, rej) => {
      const img = new Image(); const u = URL.createObjectURL(file);
      img.onload = () => { URL.revokeObjectURL(u); res(img); };
      img.onerror = () => { URL.revokeObjectURL(u); rej(new Error('bad image')); };
      img.src = u;
    });
  }
  const w0 = bmp.width, h0 = bmp.height;
  const sc = Math.min(1, maxDim / Math.max(w0, h0));
  const w = Math.max(1, Math.round(w0 * sc)), h = Math.max(1, Math.round(h0 * sc));
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bmp, 0, 0, w, h);
  let q = quality, out = c.toDataURL('image/jpeg', q);
  while (out.length > 190000 && q > 0.25) { q -= 0.1; out = c.toDataURL('image/jpeg', q); }
  return out;
}

/* ============================================================
   SHEETS
   ============================================================ */
function emojiGrid(sel) {
  return `<div class="emoji-grid" id="emojiGrid">${EMOJIS.map(e =>
    `<button type="button" class="emoji-opt ${e === sel ? 'on' : ''}" data-emoji="${esc(e)}">${e}</button>`).join('')}</div>`;
}

function sheetLesson(id) {
  const s = id ? subjectById(id) : null;
  sheet(`
    <h3>${s ? 'עריכת שיעור' : 'שיעור חדש'}</h3>
    <div class="sheet-sub">שם השיעור ושם המורה שמלמד אותו.</div>
    <div class="field"><label for="slName">שם השיעור</label>
      <input class="inp" id="slName" placeholder="למשל: הסטוריה" maxlength="30" value="${esc(s ? s.name : '')}" /></div>
    <div class="field"><label for="slTeacher">שם המורה</label>
      <input class="inp" id="slTeacher" placeholder="למשל: גברת כהן" maxlength="30" value="${esc(s ? s.teacher || '' : '')}" /></div>
    <div class="field"><label>אייקון</label>${emojiGrid(s ? s.icon : '📚')}</div>
    <div class="btn-row">
      <button class="btn primary" data-act="save-lesson" data-id="${esc(id || '')}">${s ? 'שמור' : 'הוסף'}</button>
      <button class="btn ghost" data-act="close-sheet">ביטול</button>
    </div>`);
  setTimeout(() => $('#slName') && $('#slName').focus(), 120);
}

function sheetParam(id) {
  const p = id ? paramById(id) : null;
  const t = p ? p.type : 'scale';
  sheet(`
    <h3>${p ? 'עריכת פרמטר' : 'פרמטר חדש'}</h3>
    <div class="sheet-sub">פרמטר הוא דבר שממלאים בכל שיעור. אפשר להוסיף כמה שרוצים, מתי שרוצים.</div>
    <div class="field"><label for="spName">שם הפרמטר</label>
      <input class="inp" id="spName" placeholder="למשל: כמה פעמים אמרו 67" maxlength="40" value="${esc(p ? p.name : '')}" /></div>
    <div class="field"><label for="spType">סוג</label>
      <select class="inp" id="spType">${Object.entries(PTYPES).map(([k, v]) =>
        `<option value="${k}" ${k === t ? 'selected' : ''}>${esc(v.icon + ' ' + v.label)}</option>`).join('')}</select>
      <div class="hint" id="spHint">${esc(PTYPES[t].hint)}</div></div>
    <div id="spExtra"></div>
    <div class="field"><label>אייקון</label>${emojiGrid(p ? p.icon : '📊')}</div>
    ${p ? `<div class="note">שינוי הסוג עלול להציג מילויים קיימים בצורה מוזרה. השינוי לא מוחק נתונים.</div>` : ''}
    <div class="btn-row">
      <button class="btn primary" data-act="save-param" data-id="${esc(id || '')}">${p ? 'שמור' : 'הוסף'}</button>
      <button class="btn ghost" data-act="close-sheet">ביטול</button>
    </div>`);
  paramExtra(t, p);
  $('#spType').addEventListener('change', e => {
    $('#spHint').textContent = (PTYPES[e.target.value] || {}).hint || '';
    paramExtra(e.target.value, p);
  });
  setTimeout(() => $('#spName') && $('#spName').focus(), 120);
}

function paramExtra(type, p) {
  const box = $('#spExtra'); if (!box) return;
  if (type === 'scale') {
    box.innerHTML = `
      <div class="field"><label for="spLow">תיאור הקצה הנמוך</label>
        <input class="inp" id="spLow" placeholder="למשל: מרתק" maxlength="24" value="${esc(p && p.lowLbl || '')}" /></div>
      <div class="field"><label for="spHigh">תיאור הקצה הגבוה</label>
        <input class="inp" id="spHigh" placeholder="למשל: מוות מרוב שיעמום" maxlength="24" value="${esc(p && p.highLbl || '')}" /></div>`;
  } else if (type === 'counter') {
    const agg = (p && p.agg) || 'avg';
    box.innerHTML = `<div class="field"><label for="spAgg">איך לחשב את המספר של השיעור</label>
      <select class="inp" id="spAgg">
        <option value="avg" ${agg === 'avg' ? 'selected' : ''}>ממוצע הדיווחים (מומלץ)</option>
        <option value="max" ${agg === 'max' ? 'selected' : ''}>הדיווח הגבוה ביותר</option>
        <option value="sum" ${agg === 'sum' ? 'selected' : ''}>סכום כל הדיווחים</option>
      </select>
      <div class="hint">כולם סופרים את אותו שיעור, לכן ממוצע נותן את ההערכה הנכונה. "סכום" יכפיל את הספירה.</div></div>`;
  } else if (type === 'text') {
    box.innerHTML = `<div class="field"><label for="spPh">טקסט עזרה בתוך השדה</label>
      <input class="inp" id="spPh" placeholder="למשל: מה נאמר בשיעור?" maxlength="40" value="${esc(p && p.ph || '')}" /></div>`;
  } else box.innerHTML = '';
}

function sheetJoin() {
  sheet(`<h3>חיבור למרחב שכבה</h3>
    <div class="sheet-sub">הדביקו את הקישור שקיבלתם, או את הקוד.</div>
    <div class="field"><label for="jnCode">קישור או קוד</label>
      <input class="inp" id="jnCode" placeholder="https://… או jb.123456" dir="ltr" style="text-align:left" /></div>
    <div class="btn-row">
      <button class="btn primary" data-act="do-join">התחבר</button>
      <button class="btn ghost" data-act="close-sheet">ביטול</button>
    </div>`);
}

function sheetWelcome() {
  sheet(`<h3>ברוכים הבאים 📓</h3>
    <div class="sheet-sub">איך קוראים לך? השם מופיע ליד הציטוטים והתמונות שתעלה.</div>
    <div class="field"><label for="wcName">השם שלי</label>
      <input class="inp" id="wcName" placeholder="השם שלך" maxlength="24" /></div>
    <div class="btn-row"><button class="btn primary" data-act="save-welcome">יאללה נתחיל</button></div>
    <div class="hint" style="text-align:center;margin-top:10px">אפשר לשנות מתי שרוצים בהגדרות.</div>`);
  setTimeout(() => $('#wcName') && $('#wcName').focus(), 200);
}

/* ============================================================
   ACTIONS
   ============================================================ */
function reorder(list, id, d) {
  const arr = list.filter(x => !x.deleted).sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const i = arr.findIndex(x => x.id === id);
  const j = i + d;
  if (i < 0 || j < 0 || j >= arr.length) return;
  [arr[i], arr[j]] = [arr[j], arr[i]];
  arr.forEach((x, k) => { x.order = k; x.updatedAt = nowTs(); });
  core.updatedAt = nowTs(); saveCore(); schedulePushCore();
}

async function createSpace() {
  if (Cloud.probing) return;
  Cloud.probing = true;
  toast('יוצר מרחב שכבה…');
  setStatus('busy');
  try {
    core.updatedAt = nowTs();
    const ref = await cloudCreate(core);
    Cloud.coreRef = ref;
    lsSet(LS.cloud, { ref, backend: ref.slice(0, 2) });
    location.hash = 'g=' + ref.replace(':', '.');
    setStatus('ok');
    // push any local days up
    for (const dk of Object.keys(core.days || {})) { await loadDay(dk, { remote: false }); obAdd(dk); }
    await flushOutbox();
    renderSettings(); renderHeader();
    sheet(`<h3>המרחב מוכן 🎉</h3>
      <div class="sheet-sub">שלחו את הקישור הזה בקבוצה של השכבה. כל מי שייכנס דרכו ממלא לאותו מקום ורואה את אותו סיכום.</div>
      <div class="link-box" style="margin-top:12px">${esc(shareUrl())}</div>
      <div class="btn-row">
        <button class="btn primary" data-act="share-link">📤 שתף בוואטסאפ</button>
        <button class="btn" data-act="copy-link">העתק</button>
      </div>
      <div class="btn-row"><button class="btn ghost" data-act="close-sheet">סגור</button></div>`);
  } catch (e) {
    setStatus('local');
    Cloud.coreRef = null;
    sheet(`<h3>לא הצלחתי ליצור מרחב משותף 😕</h3>
      <div class="sheet-sub">שירותי האחסון החינמיים לא ענו (${esc(e.message)}).</div>
      <div class="note">האפליקציה ממשיכה לעבוד רגיל — הנתונים נשמרים במכשיר. אפשר לנסות שוב בעוד כמה דקות, או להשתמש ב"ייצוא גיבוי" כדי להעביר נתונים בין מכשירים.</div>
      <div class="btn-row">
        <button class="btn primary" data-act="create-space">נסה שוב</button>
        <button class="btn ghost" data-act="close-sheet">סגור</button>
      </div>`);
  } finally { Cloud.probing = false; }
}

async function joinSpace(raw) {
  let v = String(raw || '').trim();
  const m = v.match(/#g=([a-z]{2})[.:]([^\s&]+)/i) || v.match(/^([a-z]{2})[.:]([^\s&]+)$/i);
  if (!m) { toast('הקוד לא נראה תקין', 'bad'); return; }
  const ref = m[1].toLowerCase() + ':' + m[2];
  if (!refBackend(ref)) { toast('סוג קוד לא מוכר', 'bad'); return; }
  closeSheet();
  Cloud.coreRef = ref;
  lsSet(LS.cloud, { ref, backend: ref.slice(0, 2) });
  location.hash = 'g=' + ref.replace(':', '.');
  toast('מתחבר…');
  await pullCore();
  await closeDueDays();
  await loadDay(todayKey());
  renderAll();
  toast(Cloud.status === 'ok' ? 'מחובר לשכבה ✅' : 'לא הצלחתי להתחבר', Cloud.status === 'ok' ? 'good' : 'bad');
}

function doExport() {
  const payload = { core, me, days: {}, exportedAt: nowTs() };
  for (const dk of Object.keys(core.days || {})) {
    const d = days[dk] || lsGet(LS.day(dk), null);
    if (d) payload.days[dk] = d;
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `yoman-shichva-${todayKey()}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast('הגיבוי ירד ⬇️', 'good');
}

async function doImport(file) {
  try {
    const txt = await file.text();
    const p = JSON.parse(txt);
    if (!p || !p.core) throw new Error('קובץ לא תקין');
    core = mergeCore(core, p.core);
    for (const [dk, d] of Object.entries(p.days || {})) {
      await loadDay(dk, { remote: false });
      days[dk].entries = mergeEntries(days[dk].entries, d.entries || {});
      days[dk].updatedAt = nowTs();
      saveDay(dk); obAdd(dk);
      if (!core.days[dk]) core.days[dk] = { closed: false, updatedAt: nowTs() };
    }
    core.updatedAt = nowTs(); saveCore();
    await closeDueDays();
    if (cloudOn()) { await pushCore(); await flushOutbox(); }
    renderAll();
    toast('הייבוא הושלם ✅', 'good');
  } catch (e) { toast('שגיאה בייבוא: ' + e.message, 'bad'); }
}

/* ============================================================
   EVENTS
   ============================================================ */
let photoTargetPid = null;

document.addEventListener('click', async (ev) => {
  const el = ev.target.closest('[data-act], [data-emoji], [data-kind]');
  if (!el) return;

  // emoji picker
  if (el.dataset.emoji) {
    $$('.emoji-opt').forEach(b => b.classList.remove('on'));
    el.classList.add('on');
    return;
  }

  // form widgets
  const kind = el.dataset.kind;
  if (kind) {
    const pid = el.dataset.pid;
    if (kind === 'cnt') {
      const cur = Number(draft.values[pid] || 0);
      const next = clamp(cur + Number(el.dataset.d), 0, 9999);
      draft.values[pid] = next;
      const box = $(`[data-cnt="${pid}"]`);
      if (box) box.innerHTML = `<span class="rtl-num">${next}</span>`;
      return;
    }
    if (kind === 'bool') {
      draft.values[pid] = el.dataset.v === '1';
      renderParamsForm(true);
      return;
    }
    if (kind === 'img') { photoTargetPid = pid; $('#filePick').click(); return; }
    if (kind === 'imgdel') { delete draft.values[pid]; renderParamsForm(true); return; }
    return;
  }

  const act = el.dataset.act, id = el.dataset.id;
  switch (act) {
    case 'close-sheet': closeSheet(); break;

    case 'open-lesson': Nav.go('lesson'); await renderLesson(id); break;
    case 'open-day':    Nav.go('day');    await renderDay(el.dataset.date); break;

    case 'add-lesson':  sheetLesson(null); break;
    case 'edit-lesson': sheetLesson(id); break;
    case 'mv-lesson':   reorder(core.subjects, id, Number(el.dataset.d)); renderSettings(); break;
    case 'del-lesson': {
      const s = subjectById(id);
      if (!s || !confirm(`למחוק את "${s.name}"? המילויים מהעבר יישמרו בהיסטוריה.`)) break;
      s.deleted = true; s.updatedAt = nowTs(); core.updatedAt = nowTs(); saveCore(); schedulePushCore();
      renderSettings(); toast('השיעור הוסר');
      break;
    }
    case 'save-lesson': {
      const name = $('#slName').value.trim();
      if (!name) { toast('צריך שם לשיעור', 'bad'); break; }
      const teacher = $('#slTeacher').value.trim();
      const icon = ($('.emoji-opt.on') || {}).textContent || '📚';
      if (id) {
        const s = subjectById(id);
        Object.assign(s, { name, teacher, icon: icon.trim(), updatedAt: nowTs() });
      } else {
        core.subjects.push({ id: uid('s_'), name, teacher, icon: icon.trim(),
          order: activeSubjects().length, updatedAt: nowTs() });
      }
      core.updatedAt = nowTs(); saveCore(); schedulePushCore();
      closeSheet(); renderSettings(); renderHome();
      toast(id ? 'עודכן ✅' : 'השיעור נוסף ✅', 'good');
      break;
    }
    case 'seed': {
      SEED_SUBJECTS.forEach(([n, ic], i) => core.subjects.push({
        id: uid('s_'), name: n, teacher: '', icon: ic, order: i, updatedAt: nowTs() }));
      core.updatedAt = nowTs(); saveCore(); schedulePushCore();
      renderHome(); toast('נוספו שיעורים — ערכו ושמות מורים בהגדרות', 'good');
      break;
    }

    case 'add-param':  sheetParam(null); break;
    case 'edit-param': sheetParam(id); break;
    case 'mv-param':   reorder(core.params, id, Number(el.dataset.d)); renderSettings(); break;
    case 'del-param': {
      const p = paramById(id);
      if (!p || !confirm(`למחוק את הפרמטר "${p.name}"? נתוני העבר יישמרו בסיכומים שכבר נסגרו.`)) break;
      p.deleted = true; p.updatedAt = nowTs(); core.updatedAt = nowTs(); saveCore(); schedulePushCore();
      renderSettings(); toast('הפרמטר הוסר');
      break;
    }
    case 'save-param': {
      const name = $('#spName').value.trim();
      if (!name) { toast('צריך שם לפרמטר', 'bad'); break; }
      const type = $('#spType').value;
      const icon = (($('.emoji-opt.on') || {}).textContent || PTYPES[type].icon).trim();
      const o = { name, type, icon, updatedAt: nowTs() };
      if (type === 'scale') { o.min = 0; o.max = 100; o.unit = '%';
        o.lowLbl = ($('#spLow') || {}).value || ''; o.highLbl = ($('#spHigh') || {}).value || ''; }
      if (type === 'counter') o.agg = ($('#spAgg') || {}).value || 'avg';
      if (type === 'text') o.ph = ($('#spPh') || {}).value || '';
      if (id) Object.assign(paramById(id), o);
      else core.params.push({ id: uid('p_'), order: activeParams().length, ...o });
      core.updatedAt = nowTs(); saveCore(); schedulePushCore();
      closeSheet(); renderSettings(); renderHome();
      toast(id ? 'עודכן ✅' : 'הפרמטר נוסף ✅', 'good');
      break;
    }

    case 'create-space': closeSheet(); await createSpace(); break;
    case 'join-space':   sheetJoin(); break;
    case 'do-join':      await joinSpace($('#jnCode').value); break;
    case 'sync-now':
      toast('מסנכרן…');
      await pullCore(); await closeDueDays(); await loadDay(todayKey()); await flushOutbox();
      renderAll(); toast(Cloud.status === 'ok' ? 'מסונכרן ✅' : 'אין חיבור', Cloud.status === 'ok' ? 'good' : 'bad');
      break;
    case 'copy-link':
      try { await navigator.clipboard.writeText(shareUrl()); toast('הקישור הועתק 📋', 'good'); }
      catch (e) { toast('העתיקו ידנית מהתיבה', 'bad'); }
      break;
    case 'share-link': {
      const text = `📓 ${core.name || 'יומן השכבה'} — ממלאים את השיעורים של היום, ובעשר בלילה יוצא סיכום של כל השכבה:\n${shareUrl()}`;
      if (navigator.share) { try { await navigator.share({ title: core.name || 'יומן השכבה', text }); } catch (e) {} }
      else { try { await navigator.clipboard.writeText(text); toast('הועתק — הדביקו בקבוצה 📋', 'good'); } catch (e) { toast('העתיקו ידנית', 'bad'); } }
      break;
    }

    case 'save-welcome': {
      const n = $('#wcName').value.trim();
      me.name = n || 'אנונימי'; saveMe(); closeSheet(); renderAll();
      break;
    }
  }
});

/* form inputs */
document.addEventListener('input', (ev) => {
  const el = ev.target;
  const pid = el.dataset && el.dataset.pid;
  if (!pid || !draft) return;
  if (el.dataset.kind === 'scale') {
    draft.values[pid] = Number(el.value);
    const sv = $(`[data-sv="${pid}"]`);
    const p = paramById(pid);
    if (sv) sv.innerHTML = `<span class="rtl-num">${el.value}${(p && p.unit) || '%'}</span>`;
  } else if (el.dataset.kind === 'text') {
    draft.values[pid] = el.value;
  }
});

/* settings fields */
function wireSettings() {
  $('#inpMyName').addEventListener('change', e => { me.name = e.target.value.trim() || 'אנונימי'; saveMe(); toast('השם עודכן'); });
  $('#inpGradeName').addEventListener('change', e => {
    core.name = e.target.value.trim() || 'יומן השכבה';
    core.updatedAt = nowTs(); saveCore(); schedulePushCore(); renderHeader(); toast('שם השכבה עודכן');
  });
  $('#inpLockHour').addEventListener('change', async e => {
    core.lockHour = Number(e.target.value);
    core.updatedAt = nowTs(); saveCore(); schedulePushCore();
    await closeDueDays(); renderAll(); toast(`המילוי ייסגר ב-${z2(core.lockHour)}:00`);
  });
}

/* file pickers */
$('#filePick').addEventListener('change', async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  ev.target.value = '';
  if (!f || !photoTargetPid || !draft) return;
  toast('מעבד תמונה…');
  try {
    draft.values[photoTargetPid] = await fileToDataUrl(f);
    renderParamsForm(true);
    toast('התמונה נוספה · לא לשכוח לשמור', 'good');
  } catch (e) { toast('לא הצלחתי לקרוא את התמונה', 'bad'); }
  photoTargetPid = null;
});
$('#jsonPick').addEventListener('change', async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  ev.target.value = '';
  if (f) await doImport(f);
});

/* nav buttons */
$('#btnBack').addEventListener('click', () => { Nav.back(); if (Nav.cur() === 'home') renderHome(); });
$('#btnHistory').addEventListener('click', () => { Nav.go('history'); renderHistory(); });
$('#btnSettings').addEventListener('click', () => { Nav.go('settings'); renderSettings(); });
$('#btnSaveEntry').addEventListener('click', saveEntry);
$('#sheetBg').addEventListener('click', closeSheet);
$('#btnAddLesson').addEventListener('click', () => sheetLesson(null));
$('#btnAddParam').addEventListener('click', () => sheetParam(null));
$('#btnExport').addEventListener('click', doExport);
$('#btnImport').addEventListener('click', () => $('#jsonPick').click());
$('#btnReset').addEventListener('click', () => {
  if (!confirm('לאפס את האפליקציה במכשיר הזה? אם יש מרחב שכבה, הנתונים המשותפים יישארו בענן.')) return;
  for (const k of Object.keys(localStorage)) if (k.startsWith('shichva.')) localStorage.removeItem(k);
  location.hash = ''; location.reload();
});

/* ============================================================
   RENDER ALL / BOOT
   ============================================================ */
function renderAll() {
  renderHeader();
  const s = Nav.cur();
  if (s === 'home') renderHome();
  else if (s === 'history') renderHistory();
  else if (s === 'settings') renderSettings();
  else if (s === 'lesson' && viewSubject) renderLesson(viewSubject);
  else if (s === 'day' && viewDate) renderDay(viewDate);
  if (s !== 'home') renderHome();
}

function parseHash() {
  const m = String(location.hash || '').match(/g=([a-z]{2})[.:]([^&\s]+)/i);
  return m ? m[1].toLowerCase() + ':' + m[2] : null;
}

async function boot() {
  // identity
  me = lsGet(LS.me, null);
  if (!me || !me.id) { me = { id: uid('u_'), name: '' }; saveMe(); }

  // core
  core = lsGet(LS.core, null);
  if (!core || !core.params) core = defaultCore();
  if (!core.days) core.days = {};
  if (!Array.isArray(core.subjects)) core.subjects = [];
  saveCore();

  // cloud ref: URL wins, else remembered
  const fromHash = parseHash();
  const remembered = lsGet(LS.cloud, {});
  Cloud.coreRef = fromHash || remembered.ref || null;
  if (Cloud.coreRef) {
    lsSet(LS.cloud, { ref: Cloud.coreRef, backend: Cloud.coreRef.slice(0, 2) });
    if (!fromHash) location.hash = 'g=' + Cloud.coreRef.replace(':', '.');
  }

  wireSettings();
  renderHeader();
  Nav.paint();
  renderHome();

  if (cloudOn()) { await pullCore(); }
  await closeDueDays();
  await loadDay(todayKey());
  if (cloudOn()) flushOutbox();
  renderAll();

  if (!me.name) sheetWelcome();

  // timers
  setInterval(() => { if (Nav.cur() === 'home') renderDayBar(); }, 30000);
  setInterval(async () => {
    if (document.hidden || !cloudOn()) return;
    await pullCore(); await closeDueDays(); await loadDay(todayKey());
    if (Nav.cur() === 'home') renderHome();
  }, 90000);
  // roll over the day / close at lock time
  setInterval(async () => {
    const changed = await closeDueDays();
    if (changed) { renderAll(); toast('היום נסגר — הסיכום נשמר 📊'); }
  }, 60000);

  document.addEventListener('visibilitychange', async () => {
    if (document.hidden || !cloudOn()) return;
    await pullCore(); await closeDueDays(); await loadDay(todayKey()); await flushOutbox();
    if (Nav.cur() === 'home') renderHome();
  });
  window.addEventListener('online', () => { if (cloudOn()) flushOutbox(); });
  window.addEventListener('hashchange', () => {
    const r = parseHash();
    if (r && r !== Cloud.coreRef) location.reload();
  });
}

boot().catch(e => {
  console.error(e);
  document.body.insertAdjacentHTML('afterbegin',
    `<div style="padding:20px;color:#ff5f6d;font-family:sans-serif">שגיאה בטעינה: ${esc(e.message)}</div>`);
});
