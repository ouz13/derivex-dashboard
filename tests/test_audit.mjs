/**
 * Denetim izi testleri.
 *
 *   node --test tests/test_audit.mjs
 *
 * Asıl soru şu: zincir, geçmişe yapılan bir müdahaleyi gerçekten yakalıyor mu?
 * Testlerin çoğu bunu farklı saldırı biçimleriyle sınar.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const { AuditTrail, GENESIS_HASH } = require(path.join(KOK, 'audit.js'));

function geciciIz() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'denetim-'));
  return { yol: path.join(d, 'audit.jsonl'), temizle: () => fs.rmSync(d, { recursive: true, force: true }) };
}

test('bos zincir gecerlidir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new AuditTrail(yol);
    assert.deepEqual(iz.verify(), { valid: true, recordCount: 0 });
    assert.equal(iz.lastHash, GENESIS_HASH);
  } finally { temizle(); }
});

test('eklenen records zinciri olusturur', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new AuditTrail(yol);
    const a = iz.append('fiyatlama', { ticker: 'THYAO', fiyat: 12.5 });
    const b = iz.append('var_kosusu', { var: -101029 });

    assert.equal(a.seq, 1);
    assert.equal(a.prevHash, GENESIS_HASH);
    assert.equal(b.seq, 2);
    assert.equal(b.prevHash, a.hash, 'ikinci kayit birincinin ozetine baglanmali');
    assert.equal(iz.verify().valid, true);
  } finally { temizle(); }
});

test('kayit diske yazilir ve yeniden okunur', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz1 = new AuditTrail(yol);
    iz1.append('fiyatlama', { x: 1 });
    iz1.append('fiyatlama', { x: 2 });

    const iz2 = new AuditTrail(yol);     // ayni dosyayi bastan yukle
    assert.equal(iz2.records.length, 2);
    assert.equal(iz2.verify().valid, true);
    assert.equal(iz2.lastHash, iz1.lastHash);
  } finally { temizle(); }
});

test('gecmis bir kaydin VERISI degistirilirse yakalanir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new AuditTrail(yol);
    iz.append('fiyatlama', { tutar: 100 });
    iz.append('fiyatlama', { tutar: 200 });
    iz.append('fiyatlama', { tutar: 300 });

    // Diskteki ilk kaydin tutarini degistir — klasik kurcalamadir
    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n');
    const k = JSON.parse(satirlar[0]);
    k.data.tutar = 999999;
    satirlar[0] = JSON.stringify(k);
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    const sonuc = new AuditTrail(yol).verify();
    assert.equal(sonuc.valid, false, 'degistirilen kayit yakalanmadi');
    assert.match(sonuc.error, /tampered|hash mismatch/);
  } finally { temizle(); }
});

test('hash de birlikte duzeltilirse zincir yine kopar', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new AuditTrail(yol);
    iz.append('fiyatlama', { tutar: 100 });
    iz.append('fiyatlama', { tutar: 200 });

    // Saldirgan hem veriyi hem o kaydin ozetini yeniden hesaplasin:
    // sonraki kaydin prevHash alani artik tutmaz.
    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n');
    const k = JSON.parse(satirlar[0]);
    k.data.tutar = 42;
    const { computeHash } = require(path.join(KOK, 'audit.js'));
    k.hash = computeHash(k);
    satirlar[0] = JSON.stringify(k);
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    const sonuc = new AuditTrail(yol).verify();
    assert.equal(sonuc.valid, false,
      'hash yeniden hesaplansa bile sonraki halka tutmamali');
    assert.match(sonuc.error, /broken chain/);
  } finally { temizle(); }
});

test('ortadan kayit silinirse yakalanir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new AuditTrail(yol);
    iz.append('a', { i: 1 }); iz.append('b', { i: 2 }); iz.append('c', { i: 3 });

    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n');
    satirlar.splice(1, 1);                      // ikinci kaydi sil
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    const sonuc = new AuditTrail(yol).verify();
    assert.equal(sonuc.valid, false, 'silinen kayit yakalanmadi');
  } finally { temizle(); }
});

test('kayitlarin sirasi degistirilirse yakalanir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new AuditTrail(yol);
    iz.append('a', { i: 1 }); iz.append('b', { i: 2 });

    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n').reverse();
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    assert.equal(new AuditTrail(yol).verify().valid, false);
  } finally { temizle(); }
});

test('ayni data ayni ozeti uretir, farkli data farkli', () => {
  const { computeHash } = require(path.join(KOK, 'audit.js'));
  const temel = { seq: 1, ts: '2026-10-05T00:00:00.000Z', type: 'x',
                  data: { a: 1 }, prevHash: GENESIS_HASH };
  assert.equal(computeHash(temel), computeHash({ ...temel }));
  assert.notEqual(computeHash(temel), computeHash({ ...temel, data: { a: 2 } }));
  assert.notEqual(computeHash(temel), computeHash({ ...temel, type: 'y' }));
});

test('recent(n) en yeni kayitlari dondurur', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new AuditTrail(yol);
    for (let i = 1; i <= 10; i++) iz.append('x', { i });
    const son3 = iz.recent(3);
    assert.equal(son3.length, 3);
    assert.equal(son3[2].data.i, 10);
  } finally { temizle(); }
});
