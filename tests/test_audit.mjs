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
const { DenetimIzi, BASLANGIC_OZET } = require(path.join(KOK, 'audit.js'));

function geciciIz() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'denetim-'));
  return { yol: path.join(d, 'audit.jsonl'), temizle: () => fs.rmSync(d, { recursive: true, force: true }) };
}

test('bos zincir gecerlidir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new DenetimIzi(yol);
    assert.deepEqual(iz.dogrula(), { gecerli: true, kayitSayisi: 0 });
    assert.equal(iz.sonOzet, BASLANGIC_OZET);
  } finally { temizle(); }
});

test('eklenen kayitlar zinciri olusturur', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new DenetimIzi(yol);
    const a = iz.ekle('fiyatlama', { ticker: 'THYAO', fiyat: 12.5 });
    const b = iz.ekle('var_kosusu', { var: -101029 });

    assert.equal(a.seq, 1);
    assert.equal(a.oncekiOzet, BASLANGIC_OZET);
    assert.equal(b.seq, 2);
    assert.equal(b.oncekiOzet, a.ozet, 'ikinci kayit birincinin ozetine baglanmali');
    assert.equal(iz.dogrula().gecerli, true);
  } finally { temizle(); }
});

test('kayit diske yazilir ve yeniden okunur', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz1 = new DenetimIzi(yol);
    iz1.ekle('fiyatlama', { x: 1 });
    iz1.ekle('fiyatlama', { x: 2 });

    const iz2 = new DenetimIzi(yol);     // ayni dosyayi bastan yukle
    assert.equal(iz2.kayitlar.length, 2);
    assert.equal(iz2.dogrula().gecerli, true);
    assert.equal(iz2.sonOzet, iz1.sonOzet);
  } finally { temizle(); }
});

test('gecmis bir kaydin VERISI degistirilirse yakalanir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new DenetimIzi(yol);
    iz.ekle('fiyatlama', { tutar: 100 });
    iz.ekle('fiyatlama', { tutar: 200 });
    iz.ekle('fiyatlama', { tutar: 300 });

    // Diskteki ilk kaydin tutarini degistir — klasik kurcalamadir
    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n');
    const k = JSON.parse(satirlar[0]);
    k.veri.tutar = 999999;
    satirlar[0] = JSON.stringify(k);
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    const sonuc = new DenetimIzi(yol).dogrula();
    assert.equal(sonuc.gecerli, false, 'degistirilen kayit yakalanmadi');
    assert.match(sonuc.hata, /degistirilmis|ozet tutmuyor/);
  } finally { temizle(); }
});

test('ozet de birlikte duzeltilirse zincir yine kopar', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new DenetimIzi(yol);
    iz.ekle('fiyatlama', { tutar: 100 });
    iz.ekle('fiyatlama', { tutar: 200 });

    // Saldirgan hem veriyi hem o kaydin ozetini yeniden hesaplasin:
    // sonraki kaydin oncekiOzet alani artik tutmaz.
    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n');
    const k = JSON.parse(satirlar[0]);
    k.veri.tutar = 42;
    const { ozetHesapla } = require(path.join(KOK, 'audit.js'));
    k.ozet = ozetHesapla(k);
    satirlar[0] = JSON.stringify(k);
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    const sonuc = new DenetimIzi(yol).dogrula();
    assert.equal(sonuc.gecerli, false,
      'ozet yeniden hesaplansa bile sonraki halka tutmamali');
    assert.match(sonuc.hata, /zincir kopuk/);
  } finally { temizle(); }
});

test('ortadan kayit silinirse yakalanir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new DenetimIzi(yol);
    iz.ekle('a', { i: 1 }); iz.ekle('b', { i: 2 }); iz.ekle('c', { i: 3 });

    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n');
    satirlar.splice(1, 1);                      // ikinci kaydi sil
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    const sonuc = new DenetimIzi(yol).dogrula();
    assert.equal(sonuc.gecerli, false, 'silinen kayit yakalanmadi');
  } finally { temizle(); }
});

test('kayitlarin sirasi degistirilirse yakalanir', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new DenetimIzi(yol);
    iz.ekle('a', { i: 1 }); iz.ekle('b', { i: 2 });

    const satirlar = fs.readFileSync(yol, 'utf8').trim().split('\n').reverse();
    fs.writeFileSync(yol, satirlar.join('\n') + '\n');

    assert.equal(new DenetimIzi(yol).dogrula().gecerli, false);
  } finally { temizle(); }
});

test('ayni veri ayni ozeti uretir, farkli veri farkli', () => {
  const { ozetHesapla } = require(path.join(KOK, 'audit.js'));
  const temel = { seq: 1, ts: '2026-10-05T00:00:00.000Z', tip: 'x',
                  veri: { a: 1 }, oncekiOzet: BASLANGIC_OZET };
  assert.equal(ozetHesapla(temel), ozetHesapla({ ...temel }));
  assert.notEqual(ozetHesapla(temel), ozetHesapla({ ...temel, veri: { a: 2 } }));
  assert.notEqual(ozetHesapla(temel), ozetHesapla({ ...temel, tip: 'y' }));
});

test('son(n) en yeni kayitlari dondurur', () => {
  const { yol, temizle } = geciciIz();
  try {
    const iz = new DenetimIzi(yol);
    for (let i = 1; i <= 10; i++) iz.ekle('x', { i });
    const son3 = iz.son(3);
    assert.equal(son3.length, 3);
    assert.equal(son3[2].veri.i, 10);
  } finally { temizle(); }
});
