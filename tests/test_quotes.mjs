/**
 * Sube teklif akisi — durum makinesi testleri.
 *
 *   node --test tests/test_quotes.mjs
 *
 * Sayfa GOSTERIM amacli ama durum makinesi gercek: gecisler sunucuda
 * zorlanmali, arayuzde dugme gizlemek yeterli degil. Testlerin cogu
 * YETKISIZ gecisleri kovaliyor — ozellikle subenin kendi teklifini
 * onaylayamamasi (gorev ayriligi).
 */

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 8775;
const TABAN = `http://127.0.0.1:${PORT}`;
const KAYIT = path.join(KOK, 'quotes_demo.json');

let sunucu;
let yedek = null;

before(async () => {
  // Varsa gercek demo kayitlarini koru: test onlari ezmemeli.
  if (fs.existsSync(KAYIT)) yedek = fs.readFileSync(KAYIT, 'utf8');
  try { fs.unlinkSync(KAYIT); } catch { /* yoktu */ }

  sunucu = spawn('node', ['_frontend_runtime.js'], {
    cwd: KOK,
    env: { ...process.env, PORT: String(PORT), DATA_MODE: '0' },
    stdio: 'ignore',
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(`${TABAN}/health`)).ok) return; } catch { /* bekle */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('sunucu ayaga kalkmadi');
});

after(() => {
  if (sunucu) sunucu.kill();
  try { fs.unlinkSync(KAYIT); } catch { /* yoktu */ }
  if (yedek !== null) fs.writeFileSync(KAYIT, yedek, 'utf8');
});

const olustur = (ek = {}) => fetch(`${TABAN}/api/quotes`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ticker: 'THYAO', optionType: 'call', strike: 280,
                         expiry: '1026', qty: 100, price: 12.5, role: 'branch', ...ek }),
}).then((r) => r.json().then((j) => ({ durum: r.status, govde: j })));

const eylem = (id, action, role, note) => fetch(`${TABAN}/api/quotes/action`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ id, action, role, note }),
}).then((r) => r.json().then((j) => ({ durum: r.status, govde: j })));

// --- olusturma -------------------------------------------------------------

test('teklif olusturulur ve taslak baslar', async () => {
  const r = await olustur({ client: 'ACME' });
  assert.equal(r.durum, 200);
  assert.equal(r.govde.quote.status, 'draft');
  assert.equal(r.govde.quote.client, 'ACME');
  assert.equal(r.govde.quote.history.length, 1);
  assert.equal(r.govde.quote.history[0].action, 'created');
});

test('zorunlu alanlar eksikse 400', async () => {
  // null, undefined, '' ve [] hepsi Number() ile 0 olur; strike'i
  // olmayan bir teklifi strike=0 diye kaydetmek sessiz veri
  // bozulmasidir.
  assert.equal((await olustur({ ticker: '' })).durum, 400);
  assert.equal((await olustur({ qty: 'abc' })).durum, 400);
  for (const kotu of [null, undefined, '', [], {}]) {
    assert.equal((await olustur({ strike: kotu })).durum, 400,
                 `strike=${JSON.stringify(kotu)} kabul edilmemeliydi`);
    assert.equal((await olustur({ qty: kotu })).durum, 400,
                 `qty=${JSON.stringify(kotu)} kabul edilmemeliydi`);
  }
  // Sayi gibi gorunen metin kabul edilir
  assert.equal((await olustur({ strike: '280', qty: '100' })).durum, 200);
});

test('liste demo oldugunu bildirir', async () => {
  const d = await fetch(`${TABAN}/api/quotes`).then((r) => r.json());
  assert.equal(d.demo, true, 'tuketici bunun gosterim oldugunu bilmeli');
  assert.ok(Array.isArray(d.quotes));
});

// --- gecerli akis ----------------------------------------------------------

test('taslak -> iletildi -> onaylandi', async () => {
  const { govde: { quote } } = await olustur();
  const a = await eylem(quote.id, 'submit', 'branch');
  assert.equal(a.govde.quote.status, 'submitted');
  const b = await eylem(quote.id, 'approve', 'trading');
  assert.equal(b.govde.quote.status, 'approved');
  assert.deepEqual(b.govde.quote.history.map((h) => h.action),
                   ['created', 'submit', 'approve']);
});

test('red sonrasi gozden gecirilip tekrar iletilebilir', async () => {
  const { govde: { quote } } = await olustur();
  await eylem(quote.id, 'submit', 'branch');
  const r = await eylem(quote.id, 'reject', 'trading', 'spread too wide');
  assert.equal(r.govde.quote.status, 'rejected');
  // Red notu izde durmali
  assert.equal(r.govde.quote.history.at(-1).note, 'spread too wide');
  const v = await eylem(quote.id, 'revise', 'branch');
  assert.equal(v.govde.quote.status, 'draft');
  assert.equal((await eylem(quote.id, 'submit', 'branch')).govde.quote.status, 'submitted');
});

test('sube ilettigi teklifi geri cekebilir', async () => {
  const { govde: { quote } } = await olustur();
  await eylem(quote.id, 'submit', 'branch');
  assert.equal((await eylem(quote.id, 'withdraw', 'branch')).govde.quote.status, 'draft');
});

// --- yetkisiz gecisler -----------------------------------------------------

test('SUBE KENDI TEKLIFINI ONAYLAYAMAZ', async () => {
  // Gorev ayriligi: arayuzde dugmeyi gizlemek yetmez, sunucu
  // reddetmeli. Dogrudan API cagrisi bunu sinar.
  const { govde: { quote } } = await olustur();
  await eylem(quote.id, 'submit', 'branch');
  const r = await eylem(quote.id, 'approve', 'branch');
  assert.equal(r.durum, 403);
  assert.match(r.govde.error, /trading role/);
  // Durum degismemis olmali
  const d = await fetch(`${TABAN}/api/quotes`).then((x) => x.json());
  assert.equal(d.quotes.find((q) => q.id === quote.id).status, 'submitted');
});

test('sube reddedemez', async () => {
  const { govde: { quote } } = await olustur();
  await eylem(quote.id, 'submit', 'branch');
  assert.equal((await eylem(quote.id, 'reject', 'branch')).durum, 403);
});

test('trading teklif ILETEMEZ', async () => {
  const { govde: { quote } } = await olustur();
  assert.equal((await eylem(quote.id, 'submit', 'trading')).durum, 403);
});

test('trading geri cekemez', async () => {
  const { govde: { quote } } = await olustur();
  await eylem(quote.id, 'submit', 'branch');
  assert.equal((await eylem(quote.id, 'withdraw', 'trading')).durum, 403);
});

// --- gecersiz durum gecisleri ----------------------------------------------

test('taslak dogrudan onaylanamaz', async () => {
  const { govde: { quote } } = await olustur();
  const r = await eylem(quote.id, 'approve', 'trading');
  assert.equal(r.durum, 409);
  assert.match(r.govde.error, /state draft/);
});

test('ONAYLANMIS teklif DEGISTIRILEMEZ', async () => {
  // Uc durum: onaydan sonra geri donus yok.
  const { govde: { quote } } = await olustur();
  await eylem(quote.id, 'submit', 'branch');
  await eylem(quote.id, 'approve', 'trading');
  for (const [a, rol] of [['reject', 'trading'], ['withdraw', 'branch'],
                          ['revise', 'branch'], ['submit', 'branch']]) {
    assert.equal((await eylem(quote.id, a, rol)).durum, 409, `${a} gecmemeliydi`);
  }
});

test('iptal edilmis teklif de uc durumdur', async () => {
  const { govde: { quote } } = await olustur();
  assert.equal((await eylem(quote.id, 'cancel', 'branch')).govde.quote.status, 'cancelled');
  assert.equal((await eylem(quote.id, 'submit', 'branch')).durum, 409);
});

test('bilinmeyen eylem reddedilir', async () => {
  const { govde: { quote } } = await olustur();
  assert.equal((await eylem(quote.id, 'yoket', 'branch')).durum, 409);
  assert.equal((await eylem(quote.id, '', 'branch')).durum, 409);
});

test('olmayan teklif 404', async () => {
  assert.equal((await eylem('Q00000000', 'submit', 'branch')).durum, 404);
});

// --- iz --------------------------------------------------------------------

test('her gecis ize AKTOR ve zamanla yazilir', async () => {
  const { govde: { quote } } = await olustur();
  await eylem(quote.id, 'submit', 'branch');
  const r = await eylem(quote.id, 'reject', 'trading', 'price stale');
  const h = r.govde.quote.history;
  assert.equal(h.length, 3);
  assert.deepEqual(h.map((x) => x.by), ['branch', 'branch', 'trading']);
  h.forEach((x) => assert.ok(Date.parse(x.at), 'zaman damgasi gecersiz'));
});
