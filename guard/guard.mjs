#!/usr/bin/env node
// Guard universal untuk .megamemory/knowledge.db — dipakai semua repo.
// Runtime: node:sqlite (Node >=22). Megamemory sendiri adalah CLI Node, jadi Node pasti ada.
// Perilaku: forward-only (hanya node/edge/timeline BARU atau BERUBAH vs HEAD).
import { DatabaseSync } from 'node:sqlite';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync, statSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const DB = '.megamemory/knowledge.db';
const BLOCKLIST = '.megamemory/pii-blocklist.txt';
const EXAMPLE = '.megamemory/pii-blocklist.example.txt';
const CONFIG = '.megamemory/guard.json';
const DEFAULTS = {
  blockedTokens: [],
  piiFilePatterns: [],
  absolutePathTokens: ['/users/', '/home/', 'c:\\users\\'],
  detectPatterns: [],
};

// Tier A: pola deterministik, bekerja tanpa blocklist nama.
// phone sengaja konteks-sensitif: angka 14 digit polos sering bukan telepon.
const PATTERNS = {
  email: /[A-Za-z0-9._+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+/i,
  phone: /(?:\+62\s?8\d{2}[-\s]?\d{4}[-\s]?\d{3,4}\b|\b08\d{2}[-\s]\d{4}[-\s]\d{3,4}\b|\b(?:wa|whatsapp|telp|telepon|phone|nomor|no\.|hp|kontak|contact)\D{0,12}(?:\+?62|0)8\d{8,11}\b)/i,
  uuid: /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i,
  nik: /\b\d{16}\b/,
  namaLabel: /\bnama(?:\s+anak)?\s*:\s*[A-Za-z]+/i,
};

const temps = [];
function extract(spec) {
  try {
    const data = execFileSync('git', ['show', spec], { maxBuffer: 100 * 1024 * 1024 });
    const dir = mkdtempSync(path.join(tmpdir(), 'kg-guard-'));
    const file = path.join(dir, 'db');
    writeFileSync(file, data);
    temps.push(dir);
    return file;
  } catch {
    return null;
  }
}
function cleanup() {
  for (const dir of temps) {
    try { rmSync(dir, { recursive: true, force: true }); } catch {}
  }
}

function config() {
  try { return { ...DEFAULTS, ...JSON.parse(readFileSync(CONFIG, 'utf8')) }; }
  catch { return { ...DEFAULTS }; }
}
function terms() {
  const file = existsSync(BLOCKLIST) ? BLOCKLIST : EXAMPLE;
  try {
    return readFileSync(file, 'utf8').split(/\r?\n/)
      .map((s) => s.trim()).filter((s) => s && !s.startsWith('#'));
  } catch { return []; }
}
// Word-boundary: "Ayu" tidak boleh kena "bayu" atau "sayur".
function wordHit(hay, term) {
  const t = String(term || '').toLowerCase();
  if (!t) return false;
  const low = hay.toLowerCase();
  let i = low.indexOf(t);
  while (i !== -1) {
    const before = i === 0 ? '' : low[i - 1];
    const after = i + t.length >= low.length ? '' : low[i + t.length];
    if (!/[a-z0-9]/.test(before) && !/[a-z0-9]/.test(after)) return true;
    i = low.indexOf(t, i + 1);
  }
  return false;
}

function readDb(file) {
  const out = { nodes: new Map(), maxSeq: 0, timeline: [], edges: new Map() };
  if (!file || !existsSync(file)) return out;
  const db = new DatabaseSync(file);
  try {
    const nodeRows = db.prepare(
      "SELECT id,name,summary,why,file_refs,parent_id,coalesce(removed_at,'') removed_at FROM nodes"
    ).all();
    for (const r of nodeRows) {
      const fields = [r.id, r.name, r.summary, r.why, r.file_refs, r.parent_id]
        .map((x) => String(x ?? ''));
      const digest = createHash('sha256')
        .update(JSON.stringify([fields, r.removed_at])).digest('hex');
      out.nodes.set(String(r.id), { digest, fields });
    }
    const seqRow = db.prepare('SELECT coalesce(MAX(seq),0) m FROM timeline').get();
    out.maxSeq = Number(seqRow?.m ?? 0);
    for (const r of db.prepare('SELECT seq,params,result_summary FROM timeline').all()) {
      out.timeline.push([Number(r.seq), String(r.params ?? ''), String(r.result_summary ?? '')]);
    }
    for (const r of db.prepare('SELECT from_id,to_id,relation,description FROM edges').all()) {
      const key = [r.from_id, r.to_id, r.relation, r.description].join('\u0000');
      out.edges.set(key, String(r.description ?? ''));
    }
  } finally {
    db.close();
  }
  return out;
}

function findings(fields, cfg, checkSibling = false) {
  const hay = fields.map((x) => String(x ?? '')).join('\n');
  const low = hay.toLowerCase();
  const hits = [];
  for (const t of terms()) if (wordHit(low, t)) hits.push('pii:' + t);
  for (const t of cfg.absolutePathTokens || []) {
    if (low.includes(String(t).toLowerCase())) hits.push('path:' + t);
  }
  if (checkSibling) {
    for (const t of cfg.blockedTokens || []) {
      if (low.includes(String(t).toLowerCase())) hits.push('sibling:' + t);
    }
  }
  for (const name of cfg.detectPatterns || []) {
    const re = PATTERNS[name];
    if (re && re.test(hay)) hits.push('pattern:' + name);
  }
  return hits;
}

function check(headFile, stageFile, cfg = config()) {
  const head = readDb(headFile);
  const stage = readDb(stageFile);
  const problems = [];
  for (const [id, { digest, fields }] of stage.nodes) {
    const old = head.nodes.get(id);
    if (old && old.digest === digest) continue;
    const hits = new Set([
      ...findings(fields, cfg, false),
      ...findings([fields[0], fields[1], fields[5]], cfg, true),
    ]);
    for (const hit of hits) problems.push('node ' + id + ' [' + hit + ']');
  }
  for (const [seq, params, result] of stage.timeline) {
    if (seq <= head.maxSeq) continue;
    for (const hit of findings([params, result], cfg, false)) {
      problems.push('timeline ' + seq + ' [' + hit + ']');
    }
  }
  for (const [key, desc] of stage.edges) {
    if (head.edges.has(key)) continue;
    const [from, to] = key.split('\u0000');
    const hits = new Set([
      ...findings([from, to, desc], cfg, false),
      ...findings([from, to], cfg, true),
    ]);
    for (const hit of hits) problems.push('edge ' + from + '->' + to + ' [' + hit + ']');
  }
  return problems;
}

function git(...args) {
  try { return execFileSync('git', args, { encoding: 'utf8' }); }
  catch { return ''; }
}

// Cek byte mentah: baris yang di-DELETE menyisakan string di halaman freelist.
// Pemeriksaan baris logis saja tidak melihatnya; VACUUM diperlukan.
function rawFindings(file, cfg = config()) {
  if (!file || !existsSync(file)) return [];
  const raw = readFileSync(file).toString('latin1').toLowerCase();
  return (cfg.absolutePathTokens || DEFAULTS.absolutePathTokens)
    .map((t) => String(t).toLowerCase())
    .filter((t) => t && raw.includes(t))
    .map((t) => 'byte:' + t);
}

function stagedCheck() {
  const cfg = config();
  const names = git('diff', '--cached', '--name-only', '--diff-filter=ACM')
    .split(/\r?\n/).filter(Boolean);
  const badFiles = names.filter((n) => (cfg.piiFilePatterns || [])
    .some((p) => n.includes(p)));
  if (badFiles.length) {
    console.error('[guard] Ditolak: file data identitas:', badFiles.join(', '));
    return 1;
  }
  if (!names.includes(DB)) return 0;

  const wal = DB + '-wal';
  if (existsSync(wal) && statSync(wal).size > 0) {
    console.error('[guard] Ditolak: WAL belum di-checkpoint — perubahan graph tidak ikut commit.');
    console.error('  Jalankan: node .githooks/guard.mjs checkpoint ' + DB);
    return 1;
  }
  if (!existsSync(BLOCKLIST)) {
    console.error('[guard] Peringatan: ' + BLOCKLIST + ' belum ada — hanya placeholder.');
  }
  const head = extract('HEAD:' + DB);
  const stage = extract(':' + DB);
  if (!stage) {
    console.error('[guard] Gagal membaca blob staged ' + DB);
    return 1;
  }
  const raw = rawFindings(stage, cfg);
  if (raw.length) {
    console.error('[guard] Ditolak: residu path absolut di byte DB ' + DB + ' — jalankan VACUUM.');
    for (const p of raw.slice(0, 10)) console.error('  - ' + p);
    return 1;
  }
  const problems = check(head, stage, cfg);
  if (problems.length) {
    console.error('[guard] Ditolak: PII/path/konteks asing pada perubahan BARU di ' + DB + '.');
    for (const p of problems.slice(0, 20)) console.error('  - ' + p);
    if (problems.length > 20) console.error('  ... dan ' + (problems.length - 20) + ' lagi');
    return 1;
  }
  return 0;
}

function checkpoint(file) {
  const db = new DatabaseSync(file);
  try { db.exec('PRAGMA wal_checkpoint(TRUNCATE);'); }
  finally { db.close(); }
  console.log('ok');
}

function integrity(file) {
  let state = 'bad';
  try {
    const db = new DatabaseSync(file);
    state = String(db.prepare('PRAGMA integrity_check').get()?.integrity_check ?? '');
    db.close();
  } catch {}
  if (state === 'ok') return 'ok';
  let healed = false;
  for (const sidecar of [file + '-wal', file + '-shm']) {
    if (existsSync(sidecar)) {
      try { rmSync(sidecar, { force: true }); healed = true; } catch {}
    }
  }
  if (healed) {
    try {
      const db = new DatabaseSync(file);
      state = String(db.prepare('PRAGMA integrity_check').get()?.integrity_check ?? '');
      db.close();
    } catch {}
    if (state === 'ok') return 'healed';
  }
  return 'bad';
}

function conflicts(file) {
  try {
    const db = new DatabaseSync(file);
    const cols = db.prepare('PRAGMA table_info(nodes)').all().map((r) => r.name);
    const n = cols.includes('needs_merge')
      ? Number(db.prepare('SELECT count(*) c FROM nodes WHERE needs_merge=1').get().c) : 0;
    db.close();
    return n;
  } catch { return 0; }
}

function makeDb(file, nodes = [], timeline = [], edges = []) {
  try { rmSync(file, { force: true }); } catch {}
  const db = new DatabaseSync(file);
  db.exec('CREATE TABLE nodes(id TEXT PRIMARY KEY,name TEXT,summary TEXT,why TEXT,file_refs TEXT,parent_id TEXT,removed_at TEXT)');
  db.exec('CREATE TABLE timeline(seq INTEGER,params TEXT,result_summary TEXT)');
  db.exec('CREATE TABLE edges(from_id TEXT,to_id TEXT,relation TEXT,description TEXT)');
  for (const n of nodes) db.prepare('INSERT INTO nodes VALUES(?,?,?,?,?,?,?)').run(...n);
  for (const t of timeline) db.prepare('INSERT INTO timeline VALUES(?,?,?)').run(...t);
  for (const e of edges) db.prepare('INSERT INTO edges VALUES(?,?,?,?)').run(...e);
  db.close();
}

function selfTest() {
  let pass = 0;
  const ok = (name, cond) => {
    if (!cond) { console.error('FAIL: ' + name); process.exitCode = 1; }
    else { pass++; }
  };
  const dir = mkdtempSync(path.join(tmpdir(), 'kg-guard-test-'));
  const node = (id, summary, removed = '') => [id, id, summary, '', '', '', removed];
  const head = path.join(dir, 'head.db');
  const stage = path.join(dir, 'stage.db');
  const cfg = { ...DEFAULTS, blockedTokens: ['example-repo'] };

  ok('word boundary: bayu tidak kena Ayu', !wordHit('bayu', 'ayu'));
  ok('word boundary: "si ayu" kena', wordHit('si ayu', 'ayu'));
  ok('word boundary: hyphen sebagai batas', wordHit('sync-shenina-2026', 'shenina'));

  makeDb(head, [node('a', 'aman')]);
  makeDb(stage, [node('a', 'aman')]);
  ok('node tidak berubah → bersih', check(head, stage, cfg).length === 0);

  makeDb(stage, [node('a', 'aman'), node('b', 'path /Users/sample/x')]);
  ok('node baru dengan path absolut → ditolak', check(head, stage, cfg).length > 0);

  makeDb(stage, [node('a', 'aman'), node('b', 'aman')]);
  ok('node baru bersih → lolos', check(head, stage, cfg).length === 0);

  makeDb(head, [node('a', 'old /Users/sample/x')]);
  makeDb(stage, [node('a', 'old /Users/sample/x')]);
  ok('node lama dengan PII → tidak diulang', check(head, stage, cfg).length === 0);

  makeDb(stage, [node('a', 'aman'), node('b', '/Users/sample/x', '2026-11-24')]);
  ok('node soft-deleted baru dengan PII → ditolak', check(head, stage, cfg).length > 0);

  makeDb(stage, [node('a', 'aman')], [[1, 'path /Users/sample/x', '']]);
  ok('timeline baru dengan PII → ditolak', check(head, stage, cfg).length > 0);

  makeDb(stage, [node('a', 'aman')], [], [['example-repo', 'a', 'depends_on', '']]);
  ok('edge baru dengan repo asing → ditolak', check(head, stage, cfg).length > 0);

  const patCfg = { ...DEFAULTS, detectPatterns: ['email', 'phone', 'uuid', 'nik', 'namaLabel'] };
  makeDb(stage, [node('a', 'aman'), node('b', 'kontak a@b.com')]);
  ok('pattern email aktif → ditolak', check(head, stage, patCfg).length > 0);
  ok('pattern email nonaktif → lolos', check(head, stage, cfg).length === 0);
  makeDb(stage, [node('a', 'aman'), node('b', 'nomor WA 08123456789')]);
  ok('telepon dengan konteks → ditolak', check(head, stage, patCfg).length > 0);
  makeDb(stage, [node('a', 'aman'), node('b', 'id 08202611010000')]);
  ok('angka 14 digit tanpa konteks → lolos', check(head, stage, patCfg).length === 0);
  makeDb(stage, [node('a', 'aman'), node('b', 'child f74f04f0-d0ad-4b5c-8b93-6a1f87d3eca8')]);
  ok('pattern uuid → ditolak', check(head, stage, patCfg).length > 0);
  makeDb(stage, [node('a', 'aman'), node('b', 'nama: Uta')]);
  ok('label nama → ditolak', check(head, stage, patCfg).length > 0);
  makeDb(stage, [node('a', 'aman'), node('b', 'nik 1234567890123456')]);
  ok('pattern nik → ditolak', check(head, stage, patCfg).length > 0);

  makeDb(head, [node('a', 'aman')]);
  ok('DB bersih lolos cek byte', rawFindings(head, cfg).length === 0);
  makeDb(stage, [node('a', 'aman'), node('b', 'path /Users/sample/x')]);
  {
    const d = new DatabaseSync(stage);
    d.exec("DELETE FROM nodes WHERE id='b'");
    d.close();
  }
  ok('baris logis bersih setelah DELETE', check(head, stage, cfg).length === 0);
  ok('residu byte freelist terdeteksi', rawFindings(stage, cfg).length > 0);

  rmSync(dir, { recursive: true, force: true });
  console.log('SELF-TEST ' + (process.exitCode ? 'FAIL' : 'PASS') + ' — ' + pass + ' assertions');
}

const [cmd, arg] = process.argv.slice(2);
try {
  if (cmd === 'checkpoint') checkpoint(arg);
  else if (cmd === 'integrity') console.log(integrity(arg));
  else if (cmd === 'conflicts') console.log(conflicts(arg));
  else if (cmd === 'self-test') selfTest();
  else process.exitCode = stagedCheck();
} finally {
  cleanup();
}
