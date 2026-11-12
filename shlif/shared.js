/* שליף — קוד משותף לדף ולמנועי החיפוש (Web Workers).
   נרמול טקסט עברי, פירוק למילים, הטיות, הבנת שאילתה ואחסון מקומי. */
(function (root) {
  'use strict';

  const SEP = '\u001f'; // מפריד בין תאים בתוך שורה שמורה

  // ---------- נרמול ופירוק למילים ----------
  const FINAL = { 'ך': 'כ', 'ם': 'מ', 'ן': 'נ', 'ף': 'פ', 'ץ': 'צ' };
  const RE_NIQQUD = /[֑-ׇ]/g;
  const RE_FINAL = /[ךםןףץ]/g;
  const RE_QUOTE = /["'`׳״‘’“”]/g;
  const RE_TOK = /[\p{L}\p{N}]+/gu;
  const RE_DIGITS = /^\d+$/;

  function norm(s) {
    return String(s).toLowerCase()
      .replace(RE_NIQQUD, '')
      .replace(RE_QUOTE, '')
      .replace(RE_FINAL, (c) => FINAL[c]);
  }

  // מילים לאינדקס: המילה עצמה, חלקי אות/ספרה ("500מל" -> 500, מל), ומספר מחובר ("050-1234567")
  function tokenize(s) {
    const n = norm(s);
    const m = n.match(RE_TOK);
    if (!m) return [];
    const out = [];
    for (let i = 0; i < m.length; i++) {
      const t = m[i];
      out.push(t);
      if (/\d/.test(t) && /\D/.test(t)) {
        const parts = t.match(/\d+|\D+/g);
        for (const p of parts) if (RE_DIGITS.test(p) || p.length >= 2) out.push(p);
      }
    }
    if (m.length > 1 && /\d[-./]\d/.test(n)) {
      const runs = n.match(/\d+(?:[-./]\d+)+/g);
      if (runs) for (const r of runs) {
        const j = r.replace(/\D/g, '');
        if (j.length >= 7 && j.length <= 16) out.push(j);
      }
    }
    return out;
  }

  // ---------- הטיות (צד השאילתה בלבד) ----------
  const RE_HEB = /^[א-ת]+$/;
  const PREFIXES = 'הובלמשכ';

  // מחזיר Map: צורה -> משקל (1 = המילה כפי שנכתבה)
  function variants(q) {
    const out = new Map();
    const add = (t, w) => { if (t.length >= 2 && (out.get(t) || 0) < w) out.set(t, w); };
    out.set(q, 1);
    if (RE_HEB.test(q) && q.length >= 2) {
      const bases = [[q, 1]];
      const c0 = q[0];
      if ((c0 === 'ה' && q.length >= 4) || (PREFIXES.includes(c0) && q.length >= 5)) bases.push([q.slice(1), 0.88]);
      if (q.length >= 6 && 'בלמשכו'.includes(c0) && q[1] === 'ה') bases.push([q.slice(2), 0.8]);
      for (const [b, w] of bases) {
        add(b, w);
        const v = w * 0.86;
        if (b.length >= 3) { add(b + 'ימ', v); add(b + 'ות', v); }
        if (b.length >= 4) add(b + 'י', v);
        if (b.length >= 5 && (b.endsWith('ימ') || b.endsWith('ות'))) {
          add(b.slice(0, -2), v); add(b.slice(0, -2) + 'ה', v);
        }
        if (b.length >= 5 && b.endsWith('י')) { add(b.slice(0, -1), v); add(b.slice(0, -1) + 'ימ', v); }
        if (b.length >= 3 && b.endsWith('ה')) { add(b.slice(0, -1) + 'ות', v); add(b.slice(0, -1) + 'ת', v); }
        if (b.length >= 4 && b.endsWith('ת')) add(b.slice(0, -1) + 'ה', v);
      }
      for (const [b, w] of bases) {
        add('ה' + b, w * 0.86);
        if (b.length >= 4) for (const p of 'ובלמשכ') add(p + b, w * 0.84);
      }
    } else if (/^[a-z]+$/.test(q) && q.length >= 3) {
      add(q + 's', 0.86); add(q + 'es', 0.84);
      if (q.endsWith('s') && q.length >= 4) add(q.slice(0, -1), 0.86);
    }
    return out;
  }

  // מרחק עריכה חסום (לתיקון שגיאות הקלדה)
  function editWithin(a, b, max) {
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > max) return -1;
    let prev = new Array(lb + 1), cur = new Array(lb + 1);
    for (let j = 0; j <= lb; j++) prev[j] = j;
    for (let i = 1; i <= la; i++) {
      cur[0] = i;
      let rowMin = i;
      const ca = a.charCodeAt(i - 1);
      for (let j = 1; j <= lb; j++) {
        const cost = ca === b.charCodeAt(j - 1) ? 0 : 1;
        let v = prev[j - 1] + cost;
        if (prev[j] + 1 < v) v = prev[j] + 1;
        if (cur[j - 1] + 1 < v) v = cur[j - 1] + 1;
        cur[j] = v;
        if (v < rowMin) rowMin = v;
      }
      if (rowMin > max) return -1;
      const t = prev; prev = cur; cur = t;
    }
    return prev[lb] <= max ? prev[lb] : -1;
  }

  // ---------- מושגים: מילים נרדפות לכותרות עמודות ----------
  // kind: attr = פרט שמבקשים לדעת (מלאי, מחיר, טלפון); entity = במה מדובר (מותג, לקוח, קטגוריה);
  //       key = מזהה שמחפשים לפיו (מק"ט, ברקוד, ת"ז)
  // agg:  sum = מסכמים כמה שורות; range = מציגים טווח; none = מציגים ערכים
  const CONCEPT_DEFS = [
    { id: 'stock', kind: 'attr', agg: 'sum', words: ['מלאי', 'כמות', 'יתרה', 'יחידות', 'stock', 'qty', 'quantity', 'inventory', 'onhand'] },
    { id: 'price', kind: 'attr', agg: 'range', words: ['מחיר', 'עלות', 'מחירון', 'price', 'cost'] },
    { id: 'debt', kind: 'attr', agg: 'sum', words: ['חוב', 'יתרה', 'balance', 'debt'] },
    { id: 'total', kind: 'attr', agg: 'sum', words: ['סכום', 'סהכ', 'הכנסות', 'מכירות', 'total', 'amount', 'sales', 'revenue'] },
    { id: 'points', kind: 'attr', agg: 'sum', words: ['נקודות', 'points'] },
    { id: 'phone', kind: 'attr', agg: 'none', words: ['טלפון', 'נייד', 'פלאפון', 'סלולרי', 'טל', 'phone', 'mobile', 'tel', 'cell'] },
    { id: 'email', kind: 'attr', agg: 'none', words: ['מייל', 'אימייל', 'אימיל', 'דואל', 'email', 'mail'] },
    { id: 'address', kind: 'attr', agg: 'none', words: ['כתובת', 'רחוב', 'address', 'street'] },
    { id: 'code', kind: 'key', agg: 'none', words: ['מקט', 'ברקוד', 'קוד', 'sku', 'barcode', 'code'] },
    { id: 'id', kind: 'key', agg: 'none', words: ['תז', 'זהות', 'חפ', 'id'] },
    { id: 'date', kind: 'attr', agg: 'none', words: ['תאריך', 'תוקף', 'date', 'expiry'] },
    { id: 'status', kind: 'attr', agg: 'none', words: ['סטטוס', 'מצב', 'status'] },
    { id: 'location', kind: 'attr', agg: 'none', words: ['מיקום', 'מדף', 'מחסן', 'מעבר', 'location', 'shelf', 'warehouse', 'aisle'] },
    { id: 'city', kind: 'entity', agg: 'none', words: ['עיר', 'יישוב', 'ישוב', 'city', 'town'] },
    { id: 'name', kind: 'entity', agg: 'none', words: ['שם', 'תיאור', 'מוצר', 'פריט', 'לקוח', 'name', 'description', 'product', 'item', 'customer'] },
    { id: 'brand', kind: 'entity', agg: 'none', words: ['מותג', 'חברה', 'יצרן', 'ספק', 'brand', 'manufacturer', 'vendor', 'supplier', 'company'] },
    { id: 'category', kind: 'entity', agg: 'none', words: ['קטגוריה', 'מחלקה', 'סוג', 'category', 'department', 'type'] },
    { id: 'branch', kind: 'entity', agg: 'none', words: ['סניף', 'חנות', 'branch', 'store'] },
  ];
  const CONCEPTS = new Map();
  const CONCEPT_BY_WORD = new Map();
  for (const d of CONCEPT_DEFS) {
    const c = { id: d.id, kind: d.kind, agg: d.agg, words: d.words.map(norm) };
    CONCEPTS.set(c.id, c);
    for (const w of c.words) {
      if (!CONCEPT_BY_WORD.has(w)) CONCEPT_BY_WORD.set(w, []);
      CONCEPT_BY_WORD.get(w).push(c.id);
    }
  }

  const QWORDS = new Set(['כמה', 'מה', 'מהו', 'מהי', 'מי', 'איפה', 'היכן', 'מתי', 'איזה', 'איזו', 'אילו',
    'how', 'what', 'where', 'who', 'which', 'when'].map(norm));
  const STOP = new Set(['יש', 'אין', 'לי', 'לנו', 'שלי', 'שלנו', 'את', 'עם', 'על', 'אם', 'או', 'גם', 'רק', 'כל',
    'נשאר', 'נשארו', 'נשארה', 'נותר', 'נותרו', 'אני', 'אנחנו', 'רוצה', 'רוצים', 'צריך', 'לדעת', 'לראות',
    'תראה', 'תראי', 'הצג', 'תציג', 'תן', 'תני', 'חפש', 'מצא', 'תמצא', 'זה', 'זו', 'זאת', 'הוא', 'היא', 'הם',
    'האם', 'בבקשה', 'עוד', 'כרגע', 'עכשיו', 'ב', 'ל', 'מ', 'ה', 'ו',
    'the', 'a', 'an', 'is', 'are', 'in', 'for', 'show', 'find', 'me', 'many', 'much', 'do', 'we', 'have',
    'left', 'please', 'i', 'my'].map(norm));
  const MAX_GROUPS = 12;

  // ---------- הבנת שאילתה ----------
  // cat = { headerTokens: Set, concepts: Set } — מה שקיים בפועל בכותרות הקבצים
  // מחזיר { groups: [{q, vars, soft, nameOnly, hint}], targets: [{word, vars, concepts, implicit}] }
  function parseQuery(text, cat) {
    const segs = String(text || '').split(/[,;:?!\n،]+/);
    const groups = [], targets = [], plain = [];
    let howMany = false, segNo = 0;
    for (const seg of segs) {
      segNo++;
      const words = norm(seg).match(RE_TOK) || [];
      const items = [];
      let prevQ = false;
      for (const w of words) {
        if (QWORDS.has(w)) { prevQ = true; if (w === 'כמה' || w === 'how') howMany = true; continue; }
        if (w === 'של' || w === 'of') {
          if (items.length && items[items.length - 1].header) items[items.length - 1].ofNext = true;
          continue;
        }
        if (STOP.has(w)) continue;
        const vars = variants(w);
        let direct = false;
        const concepts = [];
        if (!RE_DIGITS.test(w)) {
          for (const k of vars.keys()) {
            if (cat.headerTokens.has(k)) direct = true;
            const cs = CONCEPT_BY_WORD.get(k);
            if (cs) for (const c of cs) if (cat.concepts.has(c) && !concepts.includes(c)) concepts.push(c);
          }
        }
        items.push({ w, vars, header: direct || concepts.length > 0, concepts, prevQ, ofNext: false });
        prevQ = false;
      }
      const hasValue = items.some((it) => !it.header);
      let pendingHint = null;
      items.forEach((it, i) => {
        plain.push(it);
        if (!it.header) {
          groups.push({ q: it.w, vars: it.vars, soft: false, nameOnly: false, hint: pendingHint, seg: segNo });
          return;
        }
        const known = it.concepts.length > 0;
        const next = items[i + 1];
        // כותרת לא מוכרת ואחריה מספר ("הזמנה 50002", "עובד 1002") = מחפשים לפיה, לא שואלים עליה
        const isAttr = known ? it.concepts.some((c) => CONCEPTS.get(c).kind === 'attr') : !(next && /\d/.test(next.w));
        const trailing = items.slice(i + 1).every((x) => x.header);
        // "כמה מלאי", "מחיר במבה", "טלפון של דני", "דני כהן טלפון" = הפרט המבוקש;
        // "חברת איקס", "כמה לקוחות בחיפה", "מק"ט A-100" = במה מדובר
        const asTarget = !hasValue || it.ofNext || trailing || (isAttr && (it.prevQ || i === 0));
        if (asTarget) {
          targets.push({ word: it.w, vars: it.vars, concepts: it.concepts, implicit: false });
          // מילה שהיא כותרת רק באחד הקבצים יכולה להיות גם ערך — מסננים לפיה אם היא קיימת בנתונים
          if (!known) groups.push({ q: it.w, vars: it.vars, soft: true, nameOnly: false, hint: null });
          pendingHint = null;
        } else {
          // "חברת איקס", "לקוחות חיפה": רמז לעמודה, ומסנן לפי שם הקובץ אם יש כזה
          pendingHint = it.concepts.length ? it.concepts : null;
          groups.push({ q: it.w, vars: it.vars, soft: true, nameOnly: known, hint: null });
        }
      });
    }
    // שאילתה שכולה מילות כותרת ("מלאי") — מחפשים אותה כטקסט רגיל
    if (!groups.some((g) => !g.soft)) {
      if (!plain.length) return { groups: [], targets: [], howMany };
      const g2 = plain.map((it) => ({ q: it.w, vars: it.vars, soft: false, nameOnly: false, hint: null }));
      return { groups: g2.slice(0, MAX_GROUPS), targets: [], howMany };
    }
    // מספר בשאילתה ("32 גלילים", "500 מ"ל") צריך להופיע באותו תא עם המילה שלידו,
    // כדי ש-32 לא יתאים בטעות למדף A-32 או לכמות 32
    const cut = groups.slice(0, MAX_GROUPS);
    cut.forEach((g, i) => {
      if (!RE_DIGITS.test(g.q) || g.soft) return;
      const b = [i - 1, i + 1].filter((j) => cut[j] && cut[j].seg === g.seg && !cut[j].soft && !RE_DIGITS.test(cut[j].q));
      if (b.length) g.buddies = b;
    });
    if (howMany && !targets.length && cat.concepts.has('stock')) {
      targets.push({ word: 'מלאי', vars: variants(norm('מלאי')), concepts: ['stock'], implicit: true });
    }
    return { groups: groups.slice(0, MAX_GROUPS), targets: targets.slice(0, 3), howMany };
  }

  // ---------- מספרים ותאים ----------
  const RE_PLAIN_NUM = /^-?\d+(\.\d+)?$/;
  function parseNum(s) {
    if (!s) return NaN;
    if (RE_PLAIN_NUM.test(s)) return +s;
    const t = s.replace(/[₪$€£,\s%]|ש"?ח|שח/g, '');
    return RE_PLAIN_NUM.test(t) ? +t : NaN;
  }
  function isNumLike(v) {
    if (typeof v === 'number') return true;
    if (v.length > 1 && v.charCodeAt(0) === 48 && v.charCodeAt(1) !== 46) return false; // אפס מוביל = מזהה
    return /^-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$/.test(v);
  }
  function isDateLike(v) {
    return typeof v === 'string' && (/^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}(\s|$)/.test(v) || /^\d{4}-\d{2}-\d{2}/.test(v));
  }
  function colLetter(i) {
    let s = '';
    for (i = i + 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s;
    return s;
  }

  // ---------- אחסון מקומי (IndexedDB) — הנתונים נשארים בדפדפן ----------
  let dbPromise = null;
  const DB = {
    open() {
      if (!dbPromise) {
        dbPromise = new Promise((res, rej) => {
          let rq;
          try { rq = indexedDB.open('shlif', 1); } catch (e) { rej(e); return; }
          rq.onupgradeneeded = () => {
            const d = rq.result;
            for (const s of ['files', 'meta', 'kv']) if (!d.objectStoreNames.contains(s)) d.createObjectStore(s);
          };
          rq.onsuccess = () => res(rq.result);
          rq.onerror = () => rej(rq.error);
          rq.onblocked = () => rej(new Error('blocked'));
        });
      }
      return dbPromise;
    },
    async run(stores, mode, fn) {
      const d = await DB.open();
      return new Promise((res, rej) => {
        const tx = d.transaction(stores, mode);
        let out;
        tx.oncomplete = () => res(out);
        tx.onerror = () => rej(tx.error);
        tx.onabort = () => rej(tx.error || new Error('abort'));
        out = fn(tx);
      });
    },
    async get(store, key) {
      let rq;
      await DB.run([store], 'readonly', (tx) => { rq = tx.objectStore(store).get(key); });
      return rq.result;
    },
    async getAll(store) {
      let rq;
      await DB.run([store], 'readonly', (tx) => { rq = tx.objectStore(store).getAll(); });
      return rq.result || [];
    },
    put(store, key, val) { return DB.run([store], 'readwrite', (tx) => { tx.objectStore(store).put(val, key); }); },
    del(stores, key) { return DB.run(stores, 'readwrite', (tx) => { for (const s of stores) tx.objectStore(s).delete(key); }); },
    clear(stores) { return DB.run(stores, 'readwrite', (tx) => { for (const s of stores) tx.objectStore(s).clear(); }); },
  };

  const api = {
    SEP, norm, tokenize, variants, editWithin, parseQuery, parseNum, isNumLike, isDateLike, colLetter,
    CONCEPTS, CONCEPT_BY_WORD, MAX_GROUPS,
  };
  root.ShlifText = api;
  root.ShlifDB = DB;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : globalThis);
