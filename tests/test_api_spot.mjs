/**
 * /api/spot — toplu ve tekil gonderim testleri.
 *
 *   node --test tests/test_api_spot.mjs
 *
 * Toplu bicim, olculmus bir darbogazi kaldirmak icin eklendi: kopru her
 * spot degisiminde ayri bir HTTP istegi atiyordu ve boru hattinin
 * pratik tavani ~2.400 msg/sn'de kilitleniyordu (bkz. bench_stream.py).
 *
 * Testin asil isi, toplu bicim eklenirken TEKIL bicimin bozulmadigini
 * ve kismi bozuk yuklerin sessizce yanlis veri yazmadigini sinamak.
 */

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 8773;
const TABAN = `http://127.0.0.1:${PORT}`;

let sunucu;

before(async () => {
  sunucu = spawn('node', ['_frontend_runtime.js'], {
    cwd: KOK,
    env: { ...process.env, PORT: String(PORT), DATA_MODE: '0' },
    stdio: 'ignore',
  });
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`${TABAN}/health`);
      if (r.ok) return;
    } catch { /* henuz hazir degil */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('sunucu ayaga kalkmadi');
});

after(() => { if (sunucu) sunucu.kill(); });

const gonder = (govde) => fetch(`${TABAN}/api/spot`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(govde),
}).then((r) => r.json().then((j) => ({ durum: r.status, govde: j })));

const oku = (ticker) => fetch(`${TABAN}/api/spot?ticker=${ticker}`).then((r) => r.json());

// --- tekil bicim bozulmadi mi ----------------------------------------------

test('tekil bicim calismaya devam ediyor', async () => {
  const r = await gonder({ ticker: 'THYAO', spot_mid: 281.5, ts: '10:00:00.000' });
  assert.equal(r.durum, 200);
  assert.equal(r.govde.ok, true);
  const d = await oku('THYAO');
  assert.equal(d.data.spot_mid, 281.5);
});

test('tekil bicimde eksik alan 400 doner', async () => {
  assert.equal((await gonder({ ticker: 'THYAO' })).durum, 400);
  assert.equal((await gonder({ spot_mid: 1 })).durum, 400);
  assert.equal((await gonder({ ticker: 'X', spot_mid: 'abc' })).durum, 400);
});

// --- toplu bicim -----------------------------------------------------------

test('toplu bicim birden cok ticker yazar', async () => {
  const r = await gonder({ spots: { GARAN: 126.9, AKBNK: 69.15, ASELS: 355.5 },
                           ts: '10:01:00.000' });
  assert.equal(r.durum, 200);
  assert.equal(r.govde.stored, 3);
  for (const [t, v] of [['GARAN', 126.9], ['AKBNK', 69.15], ['ASELS', 355.5]]) {
    assert.equal((await oku(t)).data.spot_mid, v, `${t} yazilmamis`);
  }
});

test('toplu bicim ticker adini buyuk harfe cevirir', async () => {
  await gonder({ spots: { tuprs: 392.7 } });
  assert.equal((await oku('TUPRS')).data.spot_mid, 392.7);
});

test('toplu bicimde bozuk girdiler ATLANIR, gecerliler yazilir', async () => {
  // Kismi bozuk bir yuk yuzunden gecerli fiyatlari dusurmek de,
  // bozuk degeri sessizce yazmak da yanlis olur.
  // null, undefined, '' ve [] hepsi Number() ile 0 olur. Fiyati
  // olmayan bir ticker'i 0 diye yazmak sessiz veri bozulmasidir:
  // ima edilen getiri spot'a boldugu icin sonuc sacmalar.
  const r = await gonder({ spots: {
    EREGL: 36.25, SISE: 37.9,
    BOZUK: 'abc', BOS: null, BOSMETIN: '', BOSDIZI: [], NESNE: {},
  } });
  assert.equal(r.durum, 200);
  assert.equal(r.govde.stored, 2, 'yalnizca gecerli iki kayit yazilmali');
  assert.equal((await oku('EREGL')).data.spot_mid, 36.25);
  assert.equal((await oku('SISE')).data.spot_mid, 37.9);
  for (const t of ['BOZUK', 'BOS', 'BOSMETIN', 'BOSDIZI', 'NESNE']) {
    assert.equal((await oku(t)).data, null, `${t} yazilmamaliydi`);
  }
});

test('sayi gibi gorunen metin kabul edilir', async () => {
  // Akistan gelen degerler metin olabiliyor; bunlari reddetmek
  // gercek fiyatlari dusurmek olurdu.
  const r = await gonder({ spots: { KCHOL: '210.39' } });
  assert.equal(r.govde.stored, 1);
  assert.equal((await oku('KCHOL')).data.spot_mid, 210.39);
});

test('toplu gonderim tek zaman damgasi paylasir', async () => {
  await gonder({ spots: { TCELL: 97.6, TTKOM: 52.4 }, ts: '11:22:33.444' });
  const a = await oku('TCELL');
  const b = await oku('TTKOM');
  assert.equal(a.data.ts, '11:22:33.444');
  assert.equal(b.data.ts, a.data.ts);
});

test('bos toplu yuk 200 doner, sifir kayit yazar', async () => {
  const r = await gonder({ spots: {} });
  assert.equal(r.durum, 200);
  assert.equal(r.govde.stored, 0);
});

test('spots dizi olarak gelirse tekil yola duser ve 400 verir', async () => {
  // Dizi gecerli bir toplu bicim degil; sessizce yok sayilmamali.
  assert.equal((await gonder({ spots: [{ ticker: 'X', spot_mid: 1 }] })).durum, 400);
});

// --- saglik ucuyla baglanti ------------------------------------------------

test('toplu gonderim saglik kontrolunu tazeler', async () => {
  await gonder({ spots: { THYAO: 282.0, GARAN: 127.0 } });
  const s = await fetch(`${TABAN}/health`).then((r) => r.json());
  const spot = s.checks.find((c) => c.name === 'Spot feed');
  assert.equal(spot.status, 'healthy');
  assert.ok(spot.age_s !== null && spot.age_s < 60, 'toplu POST yasi guncellememis');
});
