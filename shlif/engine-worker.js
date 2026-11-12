/* שליף — מנוע חיפוש (Web Worker). כל Worker מחזיק חלק מהקבצים: קורא, מאנדקס ועונה לשאילתות.
   הדף מפזר את השאילתה לכל ה-Workers ומאחד את התשובות. */
'use strict';
importScripts('shared.js', 'xlsx-reader.js');

const T = self.ShlifText, DB = self.ShlifDB, Reader = self.ShlifReader;
const SEP = T.SEP;

let rows = [];               // כל השורות: תאים מחוברים ב-SEP
let tables = [];             // טבלה = גיליון בקובץ
const postings = new Map();  // מילה -> מספרי שורות (ממוין)
let vocab = null;            // אוצר מילים ממוין (נבנה בעת הצורך)
let W = new Uint8Array(0);   // משקלי התאמה לכל שורה ולכל מילת חיפוש
const tokCache = new Map();
let cacheBroken = false;

// ---------- בניית טבלה מתוך גיליון גולמי ----------
function fmtNum(v) { return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6); }
function isText(v) { return typeof v === 'string' && !T.isNumLike(v) && !T.isDateLike(v); }

function extractTable(grid) {
  // השורות הלא-ריקות הראשונות — מהן מזהים את שורת הכותרת (מדלגים על שורות כותרת-על/לוגו)
  const first = [];
  for (let r = 0; r < grid.length && first.length < 40; r++) {
    const row = grid[r];
    if (!row) continue;
    let filled = 0, text = 0;
    for (let c = 0; c < row.length; c++) {
      const v = row[c];
      if (v === undefined || v === '') continue;
      filled++;
      if (isText(v)) text++;
    }
    if (filled) first.push({ r, filled, text });
  }
  if (!first.length) return null;
  const sorted = first.map((x) => x.filled).sort((a, b) => a - b);
  const typical = sorted[Math.floor(sorted.length / 2)];
  const need = typical <= 1 ? 1 : Math.max(2, Math.ceil(typical * 0.6));
  let h = -1;
  for (let i = 0; i < Math.min(first.length - 1, 25); i++) {
    const x = first[i];
    if (x.filled >= need && x.text / x.filled >= 0.8) { h = i; break; }
  }
  const headerRow = h >= 0 ? first[h].r : -1;
  const dataStart = h >= 0 ? headerRow + 1 : first[0].r;
  let last = grid.length - 1;
  while (last >= dataStart && !(grid[last] && grid[last].some((v) => v !== undefined && v !== ''))) last--;
  if (last < dataStart) return null;

  // גבולות עמודות
  let minC = Infinity, maxC = -1;
  const scan = (row) => {
    if (!row) return;
    for (let c = 0; c < row.length; c++) {
      const v = row[c];
      if (v === undefined || v === '') continue;
      if (c < minC) minC = c;
      if (c > maxC) maxC = c;
    }
  };
  if (headerRow >= 0) scan(grid[headerRow]);
  for (let r = dataStart; r <= last; r++) scan(grid[r]);
  if (maxC < 0) return null;
  maxC = Math.min(maxC, minC + 255);
  const nC = maxC - minC + 1;

  const header = [];
  for (let c = 0; c < nC; c++) {
    const v = headerRow >= 0 ? grid[headerRow][c + minC] : undefined;
    header.push(v !== undefined && v !== '' ? String(typeof v === 'number' ? fmtNum(v) : v) : 'עמודה ' + T.colLetter(c + minC));
  }
  const filled = new Uint32Array(nC), nNum = new Uint32Array(nC), nDate = new Uint32Array(nC), nonInt = new Uint32Array(nC);
  const sample = header.map(() => new Set());
  const sampleN = new Uint32Array(nC);
  const out = new Array(last - dataStart + 1);
  const cells = new Array(nC);
  let nonEmpty = 0;
  for (let r = dataStart; r <= last; r++) {
    const row = grid[r];
    if (!row) { out[r - dataStart] = ''; continue; }
    let any = false;
    for (let c = 0; c < nC; c++) {
      let v = row[c + minC];
      if (v === undefined || v === '') { cells[c] = ''; continue; }
      any = true;
      filled[c]++;
      if (typeof v === 'number') { nNum[c]++; if (!Number.isInteger(v)) nonInt[c]++; v = fmtNum(v); }
      else if (T.isNumLike(v)) { nNum[c]++; if (v.indexOf('.') >= 0) nonInt[c]++; }
      else if (T.isDateLike(v)) nDate[c]++;
      if (sampleN[c] < 400) { sampleN[c]++; sample[c].add(v); }
      cells[c] = v;
    }
    out[r - dataStart] = any ? cells.join(SEP) : '';
    if (any) nonEmpty++;
  }
  if (!nonEmpty) return null;

  // סוג עמודה: text / num (כמות, מחיר — לא לאינדקס) / id (מספר מזהה — לאינדקס) / date
  const kinds = [];
  for (let c = 0; c < nC; c++) {
    let k = 'text';
    if (filled[c]) {
      if (nDate[c] >= filled[c] * 0.6) k = 'date';
      else if (nNum[c] >= filled[c] * 0.8) {
        k = 'num';
        const ht = T.tokenize(header[c]);
        const idHeader = ht.some((t) => t === 'מספר' || t === 'מס' || t === 'no' || t === 'num' || t === 'number' ||
          (T.CONCEPT_BY_WORD.get(t) || []).some((id) => id === 'code' || id === 'id' || id === 'phone'));
        // מספרים שלמים שכמעט לא חוזרים על עצמם הם מזהים (מספר הזמנה, ברקוד) ולא כמויות
        let unique = sample[c].size >= sampleN[c] * 0.9;
        if (unique && sampleN[c] < 20) for (const v of sample[c]) if (String(v).length < 4) { unique = false; break; }
        if (!nonInt[c] && (idHeader || unique)) k = 'id';
      }
    }
    kinds.push(k);
  }
  return { header, kinds, rows: out, firstRow: dataStart + 1, firstCol: minC };
}

// ---------- אינדקס ----------
function cellTokens(v) {
  const c0 = v.charCodeAt(0);
  if (c0 >= 48 && c0 <= 57 && v.length <= 20 && /^\d+$/.test(v)) return [v];
  let t = tokCache.get(v);
  if (t === undefined) {
    t = T.tokenize(v);
    if (tokCache.size > 80000) tokCache.clear();
    tokCache.set(v, t);
  }
  return t;
}

const GENERIC_SHEET = /^(sheet|גיליון|גליון|דף|worksheet)\s*\d*$/i;

function addTable(rec, sh) {
  const start = rows.length;
  const kinds = sh.kinds;
  const nC = sh.header.length;
  const skip = kinds.map((k) => k === 'num' || k === 'date');
  const src = sh.rows;
  for (let i = 0; i < src.length; i++) {
    const s = src[i];
    rows.push(s);
    if (!s) continue;
    const rid = start + i;
    const cells = s.split(SEP);
    for (let c = 0; c < nC; c++) {
      const v = cells[c];
      if (!v || skip[c]) continue;
      const toks = cellTokens(v);
      for (let k = 0; k < toks.length; k++) {
        const t = toks[k];
        const p = postings.get(t);
        if (p === undefined) postings.set(t, [rid]);
        else if (p[p.length - 1] !== rid) p.push(rid);
      }
    }
  }
  vocab = null;
  const headerToks = sh.header.map((h) => T.tokenize(h));
  // עמודת "כותרת" של השורה: שם מוצר / שם לקוח / תיאור
  let titleCol = -1, bestScore = 0;
  const nameWords = T.CONCEPTS.get('name').words;
  for (let c = 0; c < nC; c++) {
    if (kinds[c] !== 'text') continue;
    let sc = 1 - c * 0.01;
    for (const t of headerToks[c]) { const i = nameWords.indexOf(t); if (i >= 0) sc = Math.max(sc, 10 - i - c * 0.01); }
    if (sc > bestScore) { bestScore = sc; titleCol = c; }
  }
  if (titleCol < 0) titleCol = 0;
  const nameSrc = rec.path.replace(/\.[^./]+$/, '') + (GENERIC_SHEET.test(sh.name) ? '' : ' ' + sh.name);
  const tb = {
    id: tables.length, path: rec.path, sheet: sh.name, header: sh.header, headerToks, kinds,
    start, end: rows.length, firstRow: sh.firstRow, firstCol: sh.firstCol, titleCol,
    nameToks: new Set(T.tokenize(nameSrc)), deleted: false, targetCache: new Map(),
  };
  tables.push(tb);
  return { id: tb.id, sheet: tb.sheet, header: tb.header, kinds, rows: src.length, firstRow: tb.firstRow, firstCol: tb.firstCol, titleCol };
}

function indexRecord(rec) {
  const out = [];
  for (const sh of rec.sheets) out.push(addTable(rec, sh));
  return out;
}

function removePath(path, ids) {
  for (const tb of tables) {
    if (tb.path !== path || tb.deleted || (ids && !ids.includes(tb.id))) continue;
    tb.deleted = true;
    for (let r = tb.start; r < tb.end; r++) rows[r] = '';
  }
}

function resetAll() {
  rows = []; tables = []; postings.clear(); vocab = null; tokCache.clear();
}

// ---------- קריאת קובץ ----------
const ERR_TEXT = {
  'encrypted': 'הקובץ מוגן בסיסמה — שמרו עותק בלי סיסמה',
  'ods': 'קובץ ODS — שמרו אותו כ-xlsx',
  'xlsb': 'קובץ xlsb — שמרו אותו כ-xlsx',
  'not-excel': 'זה לא נראה כמו קובץ אקסל',
  'bad-zip': 'הקובץ פגום ולא נפתח',
  'empty': 'הקובץ ריק',
};

async function buildRecord(file, path) {
  const u8 = new Uint8Array(await file.arrayBuffer());
  const sheets = await Reader.readAny(u8, file.name);
  const rec = { path, name: file.name, size: file.size, lastModified: file.lastModified, sheets: [] };
  for (const sh of sheets) {
    const tb = extractTable(sh.grid);
    sh.grid = null;
    if (tb) rec.sheets.push({ name: sh.name, header: tb.header, kinds: tb.kinds, rows: tb.rows, firstRow: tb.firstRow, firstCol: tb.firstCol });
  }
  return rec;
}

async function cachePut(rec, meta) {
  if (cacheBroken) return;
  try {
    await DB.run(['files', 'meta'], 'readwrite', (tx) => {
      tx.objectStore('files').put(rec, rec.path);
      tx.objectStore('meta').put(meta, rec.path);
    });
  } catch (e) {
    cacheBroken = true;
    postMessage({ type: 'cacheFail' });
  }
}

function fileDone(job, rec, error, t0, cached) {
  let tablesOut = [], nRows = 0;
  if (rec) {
    tablesOut = indexRecord(rec);
    for (const t of tablesOut) nRows += t.rows;
  }
  const meta = { path: job.path, name: job.name, size: job.size, lastModified: job.lastModified, rows: nRows, sheets: tablesOut.length, error: error || null };
  postMessage({ type: 'fileDone', job: job.id, path: job.path, tables: tablesOut, meta, ms: Math.round(performance.now() - t0), cached: !!cached });
  return meta;
}

async function handleParse(job) {
  const t0 = performance.now();
  let rec = null, error = null;
  try {
    rec = await buildRecord(job.file, job.path);
    if (!rec.sheets.length) { rec = null; error = 'לא נמצאו נתונים בקובץ'; }
  } catch (e) {
    error = ERR_TEXT[e && e.code] || 'לא הצלחנו לקרוא את הקובץ';
    if (!(e && e.code)) console.warn('shlif: ' + job.path + ': ' + (e && e.stack || e));
  }
  const meta = fileDone(job, rec, error, t0, false);
  if (rec && job.cache !== false) cachePut(rec, meta);
}

async function handleLoad(job) {
  const t0 = performance.now();
  let rec = null, error = null;
  try { rec = await DB.get('files', job.path); } catch (e) { rec = null; }
  if (!rec) error = 'missing-cache';
  fileDone(job, rec, error, t0, true);
}

function handleDemo(job) {
  const t0 = performance.now();
  if (!self.ShlifDemo) importScripts('demo-data.js');
  const g = self.ShlifDemo.grid(job.spec);
  const tb = extractTable(g.grid);
  const rec = { path: job.path, name: job.name, size: 0, lastModified: 0, sheets: [{ name: g.sheet, header: tb.header, kinds: tb.kinds, rows: tb.rows, firstRow: tb.firstRow, firstCol: tb.firstCol }] };
  fileDone(job, rec, null, t0, false);
}

// ---------- חיפוש ----------
function ensureVocab() {
  if (!vocab) vocab = Array.from(postings.keys()).sort();
}
function lowerBound(arr, x) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < x) lo = mid + 1; else hi = mid; }
  return lo;
}

// הרחבת מילת חיפוש למילים שקיימות בפועל באינדקס: הטיות, ובמצב "רופף" גם התחלות מילה ושגיאות הקלדה
function expand(g, loose) {
  const exp = new Map();
  for (const [t, w] of g.vars) exp.set(t, w);
  if (loose) {
    const q = g.q;
    ensureVocab();
    const digits = /^\d+$/.test(q);
    if (q.length >= (digits ? 4 : 2)) {
      let i = lowerBound(vocab, q), n = 0;
      while (i < vocab.length && n < 500) {
        const t = vocab[i++];
        if (!t.startsWith(q)) break;
        n++;
        if (!exp.has(t)) exp.set(t, 0.5 + 0.25 * q.length / t.length);
      }
    }
    if (q.length >= 4 && !/\d/.test(q)) {
      const max = q.length >= 8 ? 2 : 1;
      for (let i = 0; i < vocab.length; i++) {
        const t = vocab[i];
        const dl = t.length - q.length;
        if (dl > max || dl < -max || exp.has(t) || /\d/.test(t)) continue;
        const d = T.editWithin(q, t, max);
        if (d > 0) exp.set(t, d === 1 ? 0.45 : 0.35);
      }
    }
  }
  return exp;
}

function resolveTarget(tb, tg, key) {
  let col = tb.targetCache.get(key);
  if (col !== undefined) return col;
  col = -1;
  let best = 0;
  for (let c = 0; c < tb.header.length; c++) {
    let sc = 0;
    const ht = tb.headerToks[c];
    for (const t of ht) {
      const w = tg.vars.get(t);
      if (w) sc = Math.max(sc, 20 + w - ht.length * 0.01);
    }
    if (!sc) {
      for (const cid of tg.concepts) {
        const words = T.CONCEPTS.get(cid).words;
        for (const t of ht) { const i = words.indexOf(t); if (i >= 0) sc = Math.max(sc, 10 - i * 0.1); }
      }
    }
    if (sc && tb.kinds[c] === 'num') sc += 0.05;
    if (sc > best) { best = sc; col = c; }
  }
  tb.targetCache.set(key, col);
  return col;
}

function search(msg) {
  const t0 = performance.now();
  const groups = msg.groups, G = groups.length, targets = msg.targets || [];
  const nRows = rows.length;
  const reply = { type: 'result', seq: msg.seq, round: msg.round, levels: new Array(G + 1).fill(0), hits: new Array(G).fill(0), best: 0, count: 0, rows: [], agg: [] };
  if (!G || !nRows) return reply;
  if (W.length < nRows * G) W = new Uint8Array(Math.ceil(nRows * G * 1.25));
  else W.fill(0, 0, nRows * G);

  const live = tables.filter((tb) => !tb.deleted && (!msg.only || tb.path === msg.only));
  const exps = [], nameW = [];
  for (let g = 0; g < G; g++) {
    const grp = groups[g];
    const exp = expand(grp, msg.loose && msg.loose[g]);
    exps.push(exp);
    if (!grp.nameOnly) {
      for (const [t, w] of exp) {
        const p = postings.get(t);
        if (!p) continue;
        const wv = Math.max(1, Math.round(w * 200));
        for (let i = 0; i < p.length; i++) { const k = p[i] * G + g; if (W[k] < wv) W[k] = wv; }
      }
    }
    // התאמה לשם הקובץ / התיקייה / הגיליון חלה על כל שורות הטבלה
    const nm = new Map();
    for (const tb of live) {
      let best = 0;
      for (const t of tb.nameToks) { const w = exp.get(t); if (w > best) best = w; }
      if (!best) continue;
      nm.set(tb.id, best * 0.6);
      const wv = Math.max(1, Math.round(best * 0.6 * 200));
      for (let r = tb.start; r < tb.end; r++) { const k = r * G + g; if (rows[r] && W[k] < wv) W[k] = wv; }
    }
    nameW.push(nm);
  }

  // מעבר 1: כמה מילים התאימו בכל שורה
  const levels = reply.levels, hits = reply.hits;
  for (const tb of live) {
    for (let r = tb.start, k = tb.start * G; r < tb.end; r++) {
      let c = 0;
      for (let g = 0; g < G; g++, k++) if (W[k]) { c++; hits[g]++; }
      if (c) levels[c]++;
    }
  }
  let best = 0;
  for (let l = G; l >= 1; l--) if (levels[l]) { best = l; break; }
  reply.best = best;
  if (!best || (msg.minLevel && best < msg.minLevel)) return reply;

  // מעבר 2: השורות ברמה הטובה ביותר + סיכומים לעמודות המבוקשות
  const candR = [], candS = [];
  const strict = [];
  if (msg.strict) for (let g = 0; g < G; g++) if (groups[g].buddies) strict.push(g);
  reply.strictDropped = 0;
  const tKeys = targets.map((tg) => tg.word + '|' + tg.concepts.join(','));
  for (const tb of live) {
    const tcols = targets.map((tg, i) => resolveTarget(tb, tg, tKeys[i]));
    const aggs = tcols.map(() => ({ sum: 0, n: 0, min: Infinity, max: -Infinity, filled: 0 }));
    const anyT = tcols.some((c) => c >= 0);
    let count = 0;
    for (let r = tb.start, k = tb.start * G; r < tb.end; r++, k += G) {
      let c = 0, s = 0;
      for (let g = 0; g < G; g++) { const w = W[k + g]; if (w) { c++; s += w; } }
      if (c !== best) continue;
      let cells = null;
      if (strict.length) {
        cells = rows[r].split(SEP);
        let ok = true;
        for (const g of strict) {
          if (!W[k + g] || nameW[g].has(tb.id)) continue;
          let found = false;
          for (let x = 0; x < cells.length && !found; x++) {
            const v = cells[x];
            if (!v || tb.kinds[x] === 'num' || tb.kinds[x] === 'date') continue;
            const toks = cellTokens(v);
            let hasNum = false;
            for (let y = 0; y < toks.length; y++) if (exps[g].has(toks[y])) { hasNum = true; break; }
            if (!hasNum) continue;
            for (const b of groups[g].buddies) {
              for (let y = 0; y < toks.length; y++) if (exps[b].has(toks[y])) { found = true; break; }
              if (found) break;
            }
          }
          if (!found) { ok = false; break; }
        }
        if (!ok) { reply.strictDropped++; continue; }
      }
      count++;
      candR.push(r); candS.push(s);
      if (anyT) {
        if (!cells) cells = rows[r].split(SEP);
        for (let i = 0; i < tcols.length; i++) {
          if (tcols[i] < 0) continue;
          const v = cells[tcols[i]];
          if (!v) continue;
          const a = aggs[i];
          a.filled++;
          const n = T.parseNum(v);
          if (n === n) { a.n++; a.sum += n; if (n < a.min) a.min = n; if (n > a.max) a.max = n; }
        }
      }
    }
    if (count) { reply.count += count; reply.agg.push({ t: tb.id, count, tcols, aggs }); }
  }

  // בחירת המועמדים הטובים לדירוג מדויק
  const CAP = 3000;
  let pick;
  if (candR.length <= CAP) pick = candR.map((_, i) => i);
  else {
    const hist = new Uint32Array(200 * G + 2);
    for (let i = 0; i < candS.length; i++) hist[candS[i]]++;
    let thr = hist.length - 1, acc = 0;
    while (thr > 0 && acc + hist[thr] <= CAP) { acc += hist[thr]; thr--; }
    pick = [];
    let room = CAP - acc;
    for (let i = 0; i < candS.length; i++) {
      if (candS[i] > thr) pick.push(i);
      else if (candS[i] === thr && room > 0) { pick.push(i); room--; }
    }
  }

  const scored = [];
  let ti = 0;
  const liveSorted = live; // כבר ממוין לפי start
  for (const i of pick) {
    const r = candR[i];
    while (liveSorted[ti].end <= r) ti++;
    const tb = liveSorted[ti];
    const cells = rows[r].split(SEP);
    const gBest = new Float32Array(G), gCol = new Int16Array(G).fill(-1);
    const mc = [];
    let cov = 0;
    for (let c = 0; c < cells.length; c++) {
      const v = cells[c];
      if (!v || tb.kinds[c] === 'num' || tb.kinds[c] === 'date') continue;
      const toks = cellTokens(v);
      let inCell = 0;
      for (let g = 0; g < G; g++) {
        if (groups[g].nameOnly) continue;
        const exp = exps[g];
        let b = 0;
        for (let k = 0; k < toks.length; k++) { const w = exp.get(toks[k]); if (w > b) b = w; }
        if (b) {
          inCell++;
          if (b > gBest[g]) { gBest[g] = b; gCol[g] = c; }
        }
      }
      if (inCell) {
        mc.push(c);
        const cv = (inCell / G) * (inCell / Math.max(toks.length, inCell));
        if (cv > cov) cov = cv;
      }
    }
    let score = 0.6 * cov;
    for (let g = 0; g < G; g++) {
      const nw = nameW[g].get(tb.id) || 0;
      score += Math.max(gBest[g], nw);
      if (gCol[g] >= 0) {
        if (gCol[g] === tb.titleCol) score += 0.15;
        const hint = groups[g].hint;
        if (hint) {
          const ht = tb.headerToks[gCol[g]];
          if (hint.some((cid) => ht.some((t) => T.CONCEPTS.get(cid).words.includes(t)))) score += 0.4;
        }
      }
    }
    const tcols = targets.map((tg, x) => resolveTarget(tb, tg, tKeys[x]));
    if (tcols.some((c) => c >= 0 && cells[c])) score += 0.3;
    scored.push({ score, t: tb.id, r: r - tb.start, cells, mc, tcols });
  }
  scored.sort((a, b) => b.score - a.score || a.t - b.t || a.r - b.r);
  reply.rows = scored.slice(0, msg.limit || 50);
  reply.ms = Math.round((performance.now() - t0) * 10) / 10;
  return reply;
}

// ---------- הודעות ----------
self.onmessage = (ev) => {
  const m = ev.data;
  switch (m.type) {
    case 'parse': handleParse(m); break;
    case 'load': handleLoad(m); break;
    case 'demo': handleDemo(m); break;
    case 'search': postMessage(search(m)); break;
    case 'remove': removePath(m.path, m.ids); break;
    case 'reset': resetAll(); postMessage({ type: 'resetDone' }); break;
  }
};
