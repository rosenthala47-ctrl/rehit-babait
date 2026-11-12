/* שליף — קורא קבצים: xlsx/xlsm, xls ישן (BIFF), CSV/TSV, וטבלאות HTML/XML ש"מתחפשות" לאקסל.
   ללא ספריות חיצוניות. הפלט: [{ name, grid }] — grid הוא מערך שורות, כל תא מחרוזת או מספר. */
(function (root) {
  'use strict';

  const utf8 = new TextDecoder('utf-8');

  function err(code) { const e = new Error(code); e.code = code; return e; }

  // ---------- כלי XML ----------
  const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
  function decodeXml(s) {
    if (s.indexOf('&') >= 0) {
      s = s.replace(/&(#x[0-9a-fA-F]+|#\d+|[a-z]+);/g, (m, e) => {
        if (e[0] === '#') {
          const n = e[1] === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
          return n > 0 && n < 0x110000 ? String.fromCodePoint(n) : '';
        }
        return ENT[e] !== undefined ? ENT[e] : m;
      });
    }
    if (s.indexOf('_x') >= 0) s = s.replace(/_x([0-9A-Fa-f]{4})_/g, (m, h) => {
      const n = parseInt(h, 16); return n < 32 ? ' ' : String.fromCharCode(n);
    });
    return s;
  }
  function clean(s) {
    if (/[\t\r\n\u001f]/.test(s)) s = s.replace(/[\t\r\n\u001f]+/g, ' ');
    return s.trim();
  }
  function attr(tag, name) {
    const m = new RegExp('(?:^|\\s)' + name + '\\s*=\\s*"([^"]*)"').exec(tag) ||
      new RegExp("(?:^|\\s)" + name + "\\s*=\\s*'([^']*)'").exec(tag);
    return m ? decodeXml(m[1]) : null;
  }
  function stripNs(xml, probe) {
    if (xml.indexOf('<' + probe) >= 0) return xml;
    return xml.replace(/<(\/?)[A-Za-z_][\w.-]*:/g, '<$1');
  }

  // ---------- ZIP ----------
  async function inflateRaw(u8) {
    const stream = new Blob([u8]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  function readZip(u8) {
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    let eocd = -1;
    for (let i = u8.length - 22, stop = Math.max(0, u8.length - 65600); i >= stop; i--) {
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw err('bad-zip');
    let count = dv.getUint16(eocd + 10, true), cd = dv.getUint32(eocd + 16, true);
    if ((cd === 0xFFFFFFFF || count === 0xFFFF) && eocd >= 20 && dv.getUint32(eocd - 20, true) === 0x07064b50) {
      const z = Number(dv.getBigUint64(eocd - 12, true));
      count = Number(dv.getBigUint64(z + 32, true));
      cd = Number(dv.getBigUint64(z + 48, true));
    }
    const entries = new Map();
    let p = cd;
    for (let i = 0; i < count && p + 46 <= u8.length; i++) {
      if (dv.getUint32(p, true) !== 0x02014b50) break;
      const method = dv.getUint16(p + 10, true);
      let csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
      const nlen = dv.getUint16(p + 28, true), elen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
      let lho = dv.getUint32(p + 42, true);
      const name = utf8.decode(u8.subarray(p + 46, p + 46 + nlen)).replace(/\\/g, '/');
      if (csize === 0xFFFFFFFF || usize === 0xFFFFFFFF || lho === 0xFFFFFFFF) {
        let x = p + 46 + nlen;
        const end = x + elen;
        while (x + 4 <= end) {
          const id = dv.getUint16(x, true), sz = dv.getUint16(x + 2, true);
          if (id === 1) {
            let y = x + 4;
            if (usize === 0xFFFFFFFF) { usize = Number(dv.getBigUint64(y, true)); y += 8; }
            if (csize === 0xFFFFFFFF) { csize = Number(dv.getBigUint64(y, true)); y += 8; }
            if (lho === 0xFFFFFFFF) { lho = Number(dv.getBigUint64(y, true)); }
            break;
          }
          x += 4 + sz;
        }
      }
      entries.set(name.toLowerCase(), { method, csize, lho });
      p += 46 + nlen + elen + clen;
    }
    return {
      has: (n) => entries.has(n.toLowerCase()),
      async read(n) {
        const e = entries.get(n.toLowerCase());
        if (!e) return null;
        const nl = dv.getUint16(e.lho + 26, true), el = dv.getUint16(e.lho + 28, true);
        const start = e.lho + 30 + nl + el;
        const data = u8.subarray(start, start + e.csize);
        if (e.method === 0) return data;
        if (e.method === 8) return inflateRaw(data);
        throw err('bad-zip');
      },
      async text(n) { const d = await this.read(n); return d ? utf8.decode(d) : null; },
    };
  }

  // ---------- תבניות מספר: תאריך / אחוז / ספרות אחרי הנקודה ----------
  const BUILTIN_DEC = { 1: 0, 2: 2, 3: 0, 4: 2, 5: 0, 6: 0, 7: 2, 8: 2, 37: 0, 38: 0, 39: 2, 40: 2, 41: 0, 42: 0, 43: 2, 44: 2 };
  function classifyFmt(id, code) {
    if ((id >= 14 && id <= 22) || (id >= 27 && id <= 36) || (id >= 45 && id <= 47) || (id >= 50 && id <= 58)) return { date: true, pct: false, dec: -1 };
    if (id === 9) return { date: false, pct: true, dec: 0 };
    if (id === 10) return { date: false, pct: true, dec: 2 };
    if (code == null) return { date: false, pct: false, dec: id in BUILTIN_DEC ? BUILTIN_DEC[id] : -1 };
    const c = code.replace(/"[^"]*"/g, '').replace(/\[[^\]]*\]/g, '').replace(/\\./g, '').replace(/_./g, '').split(';')[0];
    if (/^general$/i.test(c.trim()) || c.trim() === '@') return { date: false, pct: false, dec: -1 };
    if (/[ymdhs]/i.test(c) && !/[0#?]/.test(c.replace(/\.0+/g, ''))) return { date: true, pct: false, dec: -1 };
    const m = /\.([0#?]+)/.exec(c);
    const dec = m ? (m[1].match(/0/g) || []).length : (/[0#]/.test(c) ? 0 : -1);
    return { date: false, pct: c.indexOf('%') >= 0, dec };
  }
  const p2 = (n) => (n < 10 ? '0' + n : '' + n);
  function fmtSerial(serial, d1904) {
    if (!isFinite(serial)) return '';
    if (d1904) serial += 1462;
    const whole = Math.floor(serial);
    const frac = serial - whole;
    const mins = Math.round(frac * 1440);
    const time = p2(Math.floor(mins / 60) % 24) + ':' + p2(mins % 60);
    if (whole <= 0) return time;
    const d = new Date(Math.round((whole - 25569) * 86400000));
    const date = p2(d.getUTCDate()) + '/' + p2(d.getUTCMonth() + 1) + '/' + d.getUTCFullYear();
    return frac > 0.00001 && mins % 1440 ? date + ' ' + time : date;
  }
  function numCell(v, f, d1904) {
    if (f) {
      if (f.date) return fmtSerial(v, d1904);
      if (f.pct) return +(v * 100).toFixed(Math.max(f.dec, 0) + 2) + '%';
      if (f.dec > 0) return v.toFixed(f.dec);
    }
    return v;
  }

  // ---------- XLSX ----------
  function siText(inner) {
    if (inner.indexOf('<rPh') >= 0) inner = inner.replace(/<rPh[\s\S]*?<\/rPh>/g, '');
    let s = '', p = 0;
    for (;;) {
      const a = inner.indexOf('<t', p);
      if (a < 0) break;
      const c = inner.charCodeAt(a + 2);
      if (c !== 62 && c !== 32 && c !== 47) { p = a + 2; continue; }
      const gt = inner.indexOf('>', a);
      if (gt < 0) break;
      if (inner.charCodeAt(gt - 1) === 47) { p = gt + 1; continue; }
      const e = inner.indexOf('</t>', gt);
      if (e < 0) break;
      s += inner.slice(gt + 1, e);
      p = e + 4;
    }
    return clean(decodeXml(s));
  }
  function parseSST(xml) {
    xml = stripNs(xml, 'si');
    const out = [];
    let pos = 0;
    for (;;) {
      const a = xml.indexOf('<si', pos);
      if (a < 0) break;
      const c = xml.charCodeAt(a + 3);
      if (c !== 62 && c !== 32 && c !== 47) { pos = a + 3; continue; }
      const gt = xml.indexOf('>', a);
      if (xml.charCodeAt(gt - 1) === 47) { out.push(''); pos = gt + 1; continue; }
      const b = xml.indexOf('</si>', gt);
      if (b < 0) break;
      out.push(siText(xml.slice(gt + 1, b)));
      pos = b + 5;
    }
    return out;
  }
  function parseStyles(xml) {
    xml = stripNs(xml, 'cellXfs');
    const custom = new Map();
    const reF = /<numFmt\b[^>]*>/g;
    let m;
    while ((m = reF.exec(xml))) {
      const id = +attr(m[0], 'numFmtId');
      custom.set(id, attr(m[0], 'formatCode') || '');
    }
    const a = xml.indexOf('<cellXfs'), b = xml.indexOf('</cellXfs>');
    const xfs = [];
    if (a >= 0 && b > a) {
      const sec = xml.slice(a, b);
      const reX = /<xf\b[^>]*>/g;
      while ((m = reX.exec(sec))) {
        const id = +(attr(m[0], 'numFmtId') || 0);
        const f = classifyFmt(id, custom.has(id) ? custom.get(id) : null);
        xfs.push(f.date || f.pct || f.dec > 0 ? f : null);
      }
    }
    return xfs;
  }
  function parseSheetXml(xml, sst, xfs, d1904) {
    xml = stripNs(xml, 'sheetData');
    const grid = [];
    const start = xml.indexOf('<sheetData');
    if (start < 0) return grid;
    const re = /<row\b([^>]*)>|<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g;
    re.lastIndex = start;
    let r = -1, cNext = 0, row = null, m;
    while ((m = re.exec(xml))) {
      if (m[1] !== undefined) {
        const k = m[1].indexOf(' r="');
        r = k >= 0 ? parseInt(m[1].slice(k + 4), 10) - 1 : r + 1;
        row = null; cNext = 0;
        continue;
      }
      const inner = m[3];
      const at = m[2];
      let c = cNext;
      let k = at.indexOf(' r="');
      if (k >= 0) {
        c = 0;
        for (let i = k + 4; i < at.length; i++) {
          const ch = at.charCodeAt(i);
          if (ch >= 65 && ch <= 90) c = c * 26 + (ch - 64);
          else if (ch >= 97 && ch <= 122) c = c * 26 + (ch - 96);
          else break;
        }
        c -= 1;
      }
      cNext = c + 1;
      if (inner === undefined || r < 0 || c < 0 || c > 16383 || r > 1048575) continue;
      k = at.indexOf(' t="');
      const t = k >= 0 ? at.slice(k + 4, at.indexOf('"', k + 4)) : 'n';
      let val;
      if (t === 'inlineStr') {
        val = siText(inner);
      } else {
        const a = inner.indexOf('<v');
        if (a < 0) continue;
        const gt = inner.indexOf('>', a);
        if (inner.charCodeAt(gt - 1) === 47) continue;
        const e = inner.indexOf('</v>', gt);
        if (e < 0) continue;
        const v = inner.slice(gt + 1, e);
        if (t === 's') val = sst[+v];
        else if (t === 'n') {
          const num = +v;
          if (!isFinite(num)) continue;
          k = at.indexOf(' s="');
          const f = k >= 0 ? xfs[parseInt(at.slice(k + 4), 10)] : null;
          val = numCell(num, f, d1904);
        } else if (t === 'str') val = clean(decodeXml(v));
        else if (t === 'b') val = v === '1' ? 'TRUE' : 'FALSE';
        else if (t === 'd') val = v.slice(0, 10).split('-').reverse().join('/');
        else continue;
      }
      if (val === undefined || val === '') continue;
      if (!row) row = grid[r] || (grid[r] = []);
      row[c] = val;
    }
    return grid;
  }
  function resolvePath(base, target) {
    if (target[0] === '/') return target.slice(1);
    const parts = base.split('/'); parts.pop();
    for (const seg of target.split('/')) {
      if (seg === '..') parts.pop(); else if (seg !== '.') parts.push(seg);
    }
    return parts.join('/');
  }
  function parseRels(xml, basePath) {
    const out = [];
    if (!xml) return out;
    const re = /<(?:\w+:)?Relationship\b[^>]*>/g;
    let m;
    while ((m = re.exec(xml))) {
      const target = attr(m[0], 'Target');
      if (!target) continue;
      out.push({ id: attr(m[0], 'Id'), type: attr(m[0], 'Type') || '', path: resolvePath(basePath, target) });
    }
    return out;
  }
  async function readXlsx(u8) {
    const zip = readZip(u8);
    let wbPath = 'xl/workbook.xml';
    const rootRel = parseRels(await zip.text('_rels/.rels'), '').find((r) => /\/officeDocument$/.test(r.type));
    if (rootRel && zip.has(rootRel.path)) wbPath = rootRel.path;
    if (/\.bin$/i.test(wbPath)) throw err('xlsb');
    let wb = await zip.text(wbPath);
    if (!wb) {
      if (zip.has('content.xml')) throw err('ods');
      if (zip.has('xl/workbook.bin')) throw err('xlsb');
      throw err('not-excel');
    }
    wb = stripNs(wb, 'sheets');
    const slash = wbPath.lastIndexOf('/');
    const relsPath = wbPath.slice(0, slash + 1) + '_rels/' + wbPath.slice(slash + 1) + '.rels';
    const rels = parseRels(await zip.text(relsPath), wbPath);
    const relById = new Map(rels.map((r) => [r.id, r]));
    const d1904 = /<workbookPr\b[^>]*date1904="(1|true)"/.test(wb);
    const sstRel = rels.find((r) => /\/sharedStrings$/.test(r.type));
    const stRel = rels.find((r) => /\/styles$/.test(r.type));
    const sstXml = await zip.text(sstRel ? sstRel.path : 'xl/sharedStrings.xml');
    const stXml = await zip.text(stRel ? stRel.path : 'xl/styles.xml');
    const sst = sstXml ? parseSST(sstXml) : [];
    const xfs = stXml ? parseStyles(stXml) : [];
    const sheets = [];
    const re = /<sheet\b[^>]*>/g;
    let m, n = 0;
    while ((m = re.exec(wb))) {
      n++;
      const name = attr(m[0], 'name') || ('גיליון ' + n);
      const rid = (/\s[\w]+:id\s*=\s*"([^"]*)"/.exec(m[0]) || [])[1];
      const rel = relById.get(rid);
      const path = rel ? rel.path : 'xl/worksheets/sheet' + n + '.xml';
      if (rel && !/\/worksheet$/.test(rel.type)) continue; // גיליון תרשים וכד'
      const xml = await zip.text(path);
      if (!xml) continue;
      sheets.push({ name, grid: parseSheetXml(xml, sst, xfs, d1904) });
    }
    return sheets;
  }

  // ---------- XLS ישן (CFB + BIFF5/BIFF8) ----------
  function readCfbStream(u8, wanted) {
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    const ss = 1 << dv.getUint16(30, true), mss = 1 << dv.getUint16(32, true);
    const nFat = dv.getUint32(44, true), dirStart = dv.getUint32(48, true), cutoff = dv.getUint32(56, true);
    const miniFatStart = dv.getUint32(60, true);
    let difatSec = dv.getUint32(68, true), nDifat = dv.getUint32(72, true);
    const off = (s) => (s + 1) * ss;
    const fatSecs = [];
    for (let i = 0; i < 109 && fatSecs.length < nFat; i++) fatSecs.push(dv.getUint32(76 + i * 4, true));
    while (nDifat-- > 0 && difatSec < 0xFFFFFFFA && fatSecs.length < nFat) {
      const o = off(difatSec);
      for (let i = 0; i < ss / 4 - 1 && fatSecs.length < nFat; i++) fatSecs.push(dv.getUint32(o + i * 4, true));
      difatSec = dv.getUint32(o + ss - 4, true);
    }
    const perSec = ss / 4;
    const fat = new Uint32Array(fatSecs.length * perSec);
    fatSecs.forEach((s, i) => {
      const o = off(s);
      if (o + ss > u8.length) return;
      for (let j = 0; j < perSec; j++) fat[i * perSec + j] = dv.getUint32(o + j * 4, true);
    });
    const readChain = (start, size) => {
      const parts = [];
      let total = 0, s = start, guard = 0;
      while (s < 0xFFFFFFFA && s < fat.length && guard++ < fat.length + 1) {
        const o = off(s);
        if (o >= u8.length) break;
        parts.push(u8.subarray(o, Math.min(o + ss, u8.length)));
        total += ss;
        if (size != null && total >= size) break;
        s = fat[s];
      }
      const out = new Uint8Array(parts.reduce((a, p) => a + p.length, 0));
      let p = 0;
      for (const part of parts) { out.set(part, p); p += part.length; }
      return size != null ? out.subarray(0, Math.min(size, out.length)) : out;
    };
    const dir = readChain(dirStart, null);
    const ddv = new DataView(dir.buffer, dir.byteOffset, dir.byteLength);
    const utf16 = new TextDecoder('utf-16le');
    let rootEntry = null, found = null, encrypted = false;
    for (let o = 0; o + 128 <= dir.length; o += 128) {
      const nlen = ddv.getUint16(o + 64, true), type = dir[o + 66];
      if (!type || nlen < 2) continue;
      const name = utf16.decode(dir.subarray(o, o + nlen - 2)).toLowerCase();
      const e = { start: ddv.getUint32(o + 116, true), size: ddv.getUint32(o + 120, true) };
      if (type === 5) rootEntry = e;
      else if (name === 'encryptedpackage') encrypted = true;
      else if (type === 2 && wanted.includes(name) && (!found || name === 'workbook')) found = e;
    }
    if (encrypted) throw err('encrypted');
    if (!found) throw err('not-excel');
    if (found.size >= cutoff || !rootEntry) return readChain(found.start, found.size);
    // זרם קטן: יושב בתוך ה-mini stream
    const mini = readChain(rootEntry.start, rootEntry.size);
    const mfRaw = readChain(miniFatStart, null);
    const mf = new DataView(mfRaw.buffer, mfRaw.byteOffset, mfRaw.byteLength);
    const out = new Uint8Array(found.size);
    let s = found.start, p = 0, guard = 0;
    while (s < 0xFFFFFFFA && p < found.size && guard++ < 1e7) {
      const o = s * mss;
      out.set(mini.subarray(o, Math.min(o + mss, o + found.size - p)), p);
      p += mss;
      if (s * 4 + 4 > mfRaw.length) break;
      s = mf.getUint32(s * 4, true);
    }
    return out;
  }

  function readXls(u8) {
    const wb = readCfbStream(u8, ['workbook', 'book']);
    const dv = new DataView(wb.buffer, wb.byteOffset, wb.byteLength);
    const utf16 = new TextDecoder('utf-16le');
    let ansi = null;
    const ansiDec = (bytes) => {
      if (!ansi) { try { ansi = new TextDecoder('windows-' + codepage); } catch (e) { ansi = new TextDecoder('windows-1255'); } }
      return ansi.decode(bytes);
    };
    let codepage = 1255, biff8 = true, d1904 = false;
    const latin = (bytes) => { let s = ''; for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]); return s; };
    // מחרוזת BIFF8 בתוך רשומה אחת: [cch][flags][chars]
    const str8 = (o, lenBytes) => {
      const cch = lenBytes === 1 ? wb[o] : dv.getUint16(o, true);
      const fl = wb[o + lenBytes];
      let p = o + lenBytes + 1;
      if (fl & 8) p += 2;
      if (fl & 4) p += 4;
      return fl & 1 ? utf16.decode(wb.subarray(p, p + cch * 2)) : latin(wb.subarray(p, p + cch));
    };
    const str5 = (o, lenBytes) => {
      const cch = lenBytes === 1 ? wb[o] : dv.getUint16(o, true);
      return ansiDec(wb.subarray(o + lenBytes, o + lenBytes + cch));
    };

    const bounds = [], sst = [], xfFmt = [], fmtCode = new Map();
    // --- מעבר ראשון: הגדרות גלובליות ---
    let p = 0;
    while (p + 4 <= wb.length) {
      const id = dv.getUint16(p, true), len = dv.getUint16(p + 2, true), d = p + 4;
      if (id === 0x0809) { if (p === 0) biff8 = dv.getUint16(d, true) >= 0x0600; }
      else if (id === 0x002F) throw err('encrypted');
      else if (id === 0x0042) codepage = dv.getUint16(d, true);
      else if (id === 0x0022) d1904 = dv.getUint16(d, true) === 1;
      else if (id === 0x0085) {
        if (wb[d + 5] === 0) bounds.push({ pos: dv.getUint32(d, true), name: biff8 ? str8(d + 6, 1) : str5(d + 6, 1) });
      } else if (id === 0x041E) fmtCode.set(dv.getUint16(d, true), biff8 ? str8(d + 2, 2) : str5(d + 2, 1));
      else if (id === 0x00E0) xfFmt.push(dv.getUint16(d + 2, true));
      else if (id === 0x00FC) {
        // SST עם רשומות CONTINUE
        const segs = [[d, d + len]];
        let q = d + len;
        while (q + 4 <= wb.length && dv.getUint16(q, true) === 0x003C) {
          const l = dv.getUint16(q + 2, true);
          segs.push([q + 4, q + 4 + l]);
          q += 4 + l;
        }
        let si = 0, pos = segs[0][0] + 8, end = segs[0][1];
        const total = dv.getUint32(d + 4, true);
        const next = () => { si++; if (si >= segs.length) return false; pos = segs[si][0]; end = segs[si][1]; return true; };
        const byte = () => { if (pos >= end && !next()) return -1; return wb[pos++]; };
        const skip = (n) => { while (n > 0) { if (pos >= end && !next()) return; const k = Math.min(n, end - pos); pos += k; n -= k; } };
        for (let i = 0; i < total; i++) {
          if (pos >= end && !next()) break;
          const lo = byte(), hi = byte();
          if (hi < 0) break;
          let cch = lo | (hi << 8);
          let fl = byte();
          let runs = 0, ext = 0;
          if (fl & 8) runs = byte() | (byte() << 8);
          if (fl & 4) ext = (byte() | (byte() << 8) | (byte() << 16) | (byte() << 24)) >>> 0;
          let s = '';
          while (cch > 0) {
            if (pos >= end) { if (!next()) break; fl = (fl & ~1) | (wb[pos++] & 1); }
            if (fl & 1) {
              const k = Math.min(cch, (end - pos) >> 1);
              s += utf16.decode(wb.subarray(pos, pos + k * 2));
              pos += k * 2; cch -= k;
              if (k === 0) pos = end;
            } else {
              const k = Math.min(cch, end - pos);
              s += latin(wb.subarray(pos, pos + k));
              pos += k; cch -= k;
            }
          }
          skip(runs * 4 + ext);
          sst.push(clean(s));
        }
        p = q;
        continue;
      } else if (id === 0x000A) break;
      p += 4 + len;
    }
    const xfs = xfFmt.map((id) => {
      const f = classifyFmt(id, fmtCode.has(id) ? fmtCode.get(id) : null);
      return f.date || f.pct || f.dec > 0 ? f : null;
    });
    const rk = (o) => {
      const v = dv.getInt32(o, true);
      let n;
      if (v & 2) n = v >> 2;
      else { const b = new DataView(new ArrayBuffer(8)); b.setInt32(4, v & ~3, true); n = b.getFloat64(0, true); }
      return v & 1 ? n / 100 : n;
    };
    // --- גיליונות ---
    const sheets = [];
    for (const b of bounds) {
      const grid = [];
      const put = (r, c, val) => {
        if (val === undefined || val === '' || r > 1048575 || c > 16383) return;
        (grid[r] || (grid[r] = []))[c] = val;
      };
      const num = (r, c, xf, v) => { if (isFinite(v)) put(r, c, numCell(v, xfs[xf], d1904)); };
      let pending = null;
      p = b.pos;
      let depth = 0;
      while (p + 4 <= wb.length) {
        const id = dv.getUint16(p, true), len = dv.getUint16(p + 2, true), d = p + 4;
        if (id === 0x0809) depth++;
        else if (id === 0x000A) { if (--depth <= 0) break; }
        else if (depth === 1 && len >= 6) {
          const r = dv.getUint16(d, true), c = dv.getUint16(d + 2, true), xf = dv.getUint16(d + 4, true);
          if (id === 0x00FD) put(r, c, sst[dv.getUint32(d + 6, true)]);
          else if (id === 0x0203) num(r, c, xf, dv.getFloat64(d + 6, true));
          else if (id === 0x027E) num(r, c, xf, rk(d + 6));
          else if (id === 0x00BD) {
            const last = dv.getUint16(d + len - 2, true);
            for (let cc = c, o = d + 4; cc <= last && o + 6 <= d + len - 2; cc++, o += 6) num(r, cc, dv.getUint16(o, true), rk(o + 2));
          } else if (id === 0x0204 || id === 0x00D6) put(r, c, clean(biff8 ? str8(d + 6, 2) : str5(d + 6, 2)));
          else if (id === 0x0006) {
            if (dv.getUint16(d + 12, true) === 0xFFFF) {
              const t = wb[d + 6];
              if (t === 0) pending = [r, c];
              else if (t === 1) put(r, c, wb[d + 8] ? 'TRUE' : 'FALSE');
            } else num(r, c, xf, dv.getFloat64(d + 6, true));
          } else if (id === 0x0205) { if (!wb[d + 7]) put(r, c, wb[d + 6] ? 'TRUE' : 'FALSE'); }
        }
        if (id === 0x0207 && pending) { put(pending[0], pending[1], clean(biff8 ? str8(d, 2) : str5(d, 2))); pending = null; }
        p += 4 + len;
      }
      sheets.push({ name: b.name, grid });
    }
    return sheets;
  }

  // ---------- טקסט: CSV / TSV / HTML / XML 2003 ----------
  function decodeText(u8) {
    if (u8[0] === 0xEF && u8[1] === 0xBB && u8[2] === 0xBF) return utf8.decode(u8.subarray(3));
    if (u8[0] === 0xFF && u8[1] === 0xFE) return new TextDecoder('utf-16le').decode(u8.subarray(2));
    if (u8[0] === 0xFE && u8[1] === 0xFF) return new TextDecoder('utf-16be').decode(u8.subarray(2));
    try { return new TextDecoder('utf-8', { fatal: true }).decode(u8); }
    catch (e) { return new TextDecoder('windows-1255').decode(u8); }
  }
  function parseDelimited(text) {
    // זיהוי מפריד לפי השורות הראשונות
    const head = text.slice(0, 20000);
    let delim = ',', best = 0;
    for (const d of [',', ';', '\t', '|']) {
      let n = 0, inQ = false;
      for (let i = 0; i < head.length; i++) {
        const ch = head[i];
        if (ch === '"') inQ = !inQ; else if (ch === d && !inQ) n++;
      }
      if (n > best) { best = n; delim = d; }
    }
    const grid = [];
    let row = [], i = 0;
    const n = text.length, dc = delim.charCodeAt(0);
    while (i < n) {
      let val;
      if (text.charCodeAt(i) === 34) {
        let s = '';
        i++;
        for (;;) {
          const q = text.indexOf('"', i);
          if (q < 0) { s += text.slice(i); i = n; break; }
          s += text.slice(i, q);
          if (text.charCodeAt(q + 1) === 34) { s += '"'; i = q + 2; } else { i = q + 1; break; }
        }
        val = s;
        while (i < n) { const c = text.charCodeAt(i); if (c === dc || c === 10 || c === 13) break; i++; }
      } else {
        let j = i;
        while (j < n) { const c = text.charCodeAt(j); if (c === dc || c === 10 || c === 13) break; j++; }
        val = text.slice(i, j);
        i = j;
      }
      val = clean(val);
      if (val !== '') row[row.length] = val; else row.length++;
      const c = text.charCodeAt(i);
      if (c === dc) { i++; continue; }
      if (c === 13 && text.charCodeAt(i + 1) === 10) i++;
      i++;
      grid.push(row);
      row = [];
    }
    if (row.length) grid.push(row);
    return grid;
  }
  function parseHtmlTables(html) {
    const grid = [];
    const reRow = /<tr\b[\s\S]*?(?=<tr\b|<\/table|$)/gi;
    let m;
    while ((m = reRow.exec(html))) {
      const row = [];
      const reCell = /<t[dh]\b[^>]*>([\s\S]*?)(?=<t[dh]\b|<\/tr|<tr\b|<\/table|$)/gi;
      let c;
      while ((c = reCell.exec(m[0]))) {
        const v = clean(decodeXml(c[1].replace(/<[^>]*>/g, '')));
        if (v !== '') row[row.length] = v; else row.length++;
      }
      grid.push(row);
    }
    return grid;
  }
  function parseXml2003(xml) {
    const sheets = [];
    const reWs = /<(?:ss:)?Worksheet\b([^>]*)>([\s\S]*?)<\/(?:ss:)?Worksheet>/g;
    let w;
    while ((w = reWs.exec(xml))) {
      const grid = [];
      const reRow = /<(?:ss:)?Row\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:ss:)?Row>)/g;
      let r = -1, m;
      while ((m = reRow.exec(w[2]))) {
        const ri = attr(m[1], 'ss:Index');
        r = ri ? +ri - 1 : r + 1;
        if (!m[2]) continue;
        const row = [];
        const reCell = /<(?:ss:)?Cell\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:ss:)?Cell>)/g;
        let c = -1, x;
        while ((x = reCell.exec(m[2]))) {
          const ci = attr(x[1], 'ss:Index');
          c = ci ? +ci - 1 : c + 1;
          const dm = x[2] && /<(?:ss:)?Data\b([^>]*)>([\s\S]*?)<\/(?:ss:)?Data>/.exec(x[2]);
          if (!dm) continue;
          const raw = clean(decodeXml(dm[2].replace(/<[^>]*>/g, '')));
          if (raw === '') continue;
          const type = attr(dm[1], 'ss:Type');
          if (type === 'Number' && isFinite(+raw)) row[c] = +raw;
          else if (type === 'DateTime') row[c] = raw.slice(0, 10).split('-').reverse().join('/');
          else row[c] = raw;
        }
        grid[r] = row;
      }
      sheets.push({ name: attr(w[1], 'ss:Name') || 'גיליון', grid });
    }
    return sheets;
  }

  async function readAny(u8, fileName) {
    if (u8.length < 4) throw err('empty');
    if (u8[0] === 0x50 && u8[1] === 0x4B) return readXlsx(u8);
    if (u8[0] === 0xD0 && u8[1] === 0xCF && u8[2] === 0x11 && u8[3] === 0xE0) return readXls(u8);
    const text = decodeText(u8);
    const head = text.slice(0, 3000).toLowerCase();
    const base = String(fileName || '').replace(/\.[^.]+$/, '');
    if (head.indexOf('<worksheet') >= 0 || head.indexOf('<ss:worksheet') >= 0 || /urn:schemas-microsoft-com:office:spreadsheet/.test(head)) {
      const s = parseXml2003(text);
      if (s.length) return s;
    }
    if (/<(table|html|!doctype|tr)\b/.test(head)) return [{ name: base, grid: parseHtmlTables(text) }];
    return [{ name: base, grid: parseDelimited(text) }];
  }

  root.ShlifReader = { readAny };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.ShlifReader;
})(typeof self !== 'undefined' ? self : globalThis);
