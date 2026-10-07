/**
 * Model fallback testleri.
 *
 *   node --test tests/test_model_fallback.mjs
 *
 * Asil sorular: kotu bir uyum "basarili" sayiliyor mu, zincir dogru
 * sirayla iniyor mu, ve hangi basamakta oldugumuz cagirana dogru
 * bildiriliyor mu. Sessizce varsayilana dusmek en tehlikeli davranis
 * oldugu icin testlerin cogu onu kovaliyor.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const MF = require(path.join(KOK, 'model_fallback.js'));

const TOHUM = {
  heston: { kappa: 2.0, theta: 0.10, sigma: 0.50, rho: -0.50, v0: 0.10 },
  svi: { a: 0.02, b: 0.2, rho: -0.4, m: 0.0, sigma: 0.2 },
};

const iyiHeston = { params: { kappa: 1.5, theta: 0.08, sigma: 0.4, rho: -0.5, v0: 0.08 }, rmseIv: 0.008 };
const iyiSvi = { params: { a: 0.03, b: 0.25, rho: -0.45, m: 0.01, sigma: 0.18 }, rmseIv: 0.012 };
const kotuHeston = { params: { kappa: 1.5, theta: 0.08, sigma: 0.4, rho: -0.5, v0: 0.08 }, rmseIv: 0.42 };

function sec(ek) {
  return MF.secParametreler(Object.assign({ istenen: 'heston', varsayilan: TOHUM }, ek));
}

// --- kabulEdilir -----------------------------------------------------------

test('iyi uyum kabul edilir', () => {
  assert.equal(MF.kabulEdilir(iyiHeston).ok, true);
});

test('RMSE esigin ustundeyse REDDEDILIR', () => {
  // Esas mesele bu: fitHeston/fitSvi kotu uyumda da sonuc donduruyor.
  // Kaliteye bakilmazsa %42 RMSE'li bir set "kalibre edildi" diye sunulur.
  const s = MF.kabulEdilir(kotuHeston);
  assert.equal(s.ok, false);
  assert.match(s.sebep, /RMSE/);
});

test('esik disaridan verilebilir', () => {
  assert.equal(MF.kabulEdilir({ params: { a: 1 }, rmseIv: 0.03 }, 0.02).ok, false);
  assert.equal(MF.kabulEdilir({ params: { a: 1 }, rmseIv: 0.03 }, 0.10).ok, true);
});

test('uyum yoksa reddedilir', () => {
  assert.equal(MF.kabulEdilir(null).ok, false);
  assert.equal(MF.kabulEdilir({}).ok, false);
});

test('NaN parametre reddedilir', () => {
  const s = MF.kabulEdilir({ params: { kappa: NaN, theta: 0.1 }, rmseIv: 0.01 });
  assert.equal(s.ok, false);
  assert.match(s.sebep, /kappa/);
});

test('RMSE hesaplanamadiysa reddedilir', () => {
  assert.equal(MF.kabulEdilir({ params: { kappa: 1 }, rmseIv: NaN }).ok, false);
  assert.equal(MF.kabulEdilir({ params: { kappa: 1 } }).ok, false);
});

// --- zincir sirasi ---------------------------------------------------------

test('1. basamak: istenen model yakinsarsa o kullanilir', () => {
  const r = sec({ uyumlar: { heston: iyiHeston, svi: iyiSvi } });
  assert.equal(r.kaynak, 'kalibre');
  assert.equal(r.model, 'heston');
  assert.equal(r.guvenilir, true);
  assert.deepEqual(r.params, iyiHeston.params);
});

test('2. basamak: istenen yakinsamazsa ALTERNATIF modele gecilir', () => {
  const r = sec({ uyumlar: { heston: kotuHeston, svi: iyiSvi } });
  assert.equal(r.kaynak, 'alternatif');
  assert.equal(r.model, 'svi', 'alternatif model SVI olmaliydi');
  assert.equal(r.guvenilir, true, 'taze bir uyum hala guvenilirdir');
  assert.deepEqual(r.params, iyiSvi.params);
});

test('3. basamak: iki model de yakinsamazsa ONCEKI parametrelere donulur', () => {
  const onceki = { params: { kappa: 3, theta: 0.2, sigma: 0.6, rho: -0.3, v0: 0.2 },
                   rmse: 0.01, ts: '2026-10-06T08:00:00.000Z' };
  const r = sec({
    uyumlar: { heston: kotuHeston, svi: { params: { a: 1 }, rmseIv: 0.9 } },
    sonIyi: { heston: onceki },
  });
  assert.equal(r.kaynak, 'onceki');
  assert.equal(r.model, 'heston');
  assert.equal(r.guvenilir, false, 'eski parametre taze uyum degildir');
  assert.deepEqual(r.params, onceki.params);
  assert.equal(r.ts, onceki.ts, 'ne kadar eski oldugu cagirana bildirilmeli');
});

test('4. basamak: istenen modelin gecmisi yoksa ALTERNATIFIN gecmisine bakilir', () => {
  const onceki = { params: { a: 0.05, b: 0.3, rho: -0.5, m: 0, sigma: 0.2 },
                   rmse: 0.02, ts: '2026-10-06T07:00:00.000Z' };
  const r = sec({
    uyumlar: { heston: kotuHeston, svi: null },
    sonIyi: { svi: onceki },
  });
  assert.equal(r.kaynak, 'onceki-alternatif');
  assert.equal(r.model, 'svi');
  assert.equal(r.guvenilir, false);
});

test('5. basamak: hicbir sey yoksa tohum parametreleri, GUVENILIR DEGIL', () => {
  const r = sec({ uyumlar: {} });
  assert.equal(r.kaynak, 'varsayilan');
  assert.equal(r.guvenilir, false,
    'tohum parametreleri kalibrasyon degildir ve oyle sunulamaz');
  assert.deepEqual(r.params, TOHUM.heston);
});

test('onceki parametreler taze uyumun ONUNE gecmez', () => {
  const r = sec({
    uyumlar: { heston: iyiHeston, svi: iyiSvi },
    sonIyi: { heston: { params: { kappa: 99 }, rmse: 0.001 } },
  });
  assert.equal(r.kaynak, 'kalibre');
  assert.equal(r.params.kappa, iyiHeston.params.kappa);
});

test('SVI istendiginde alternatif Heston olur', () => {
  const r = MF.secParametreler({
    istenen: 'svi', varsayilan: TOHUM,
    uyumlar: { svi: { params: { a: 1 }, rmseIv: 0.8 }, heston: iyiHeston },
  });
  assert.equal(r.kaynak, 'alternatif');
  assert.equal(r.model, 'heston');
});

// --- seffaflik -------------------------------------------------------------

test('red sebepleri cagirana bildirilir', () => {
  const r = sec({ uyumlar: { heston: kotuHeston, svi: { params: { a: 1 }, rmseIv: 0.9 } } });
  assert.ok(r.redSebepleri.heston, 'heston red sebebi eksik');
  assert.ok(r.redSebepleri.svi, 'svi red sebebi eksik');
  assert.match(r.redSebepleri.heston, /RMSE/);
});

test('her basamak bir aciklama tasir', () => {
  for (const girdi of [
    { uyumlar: { heston: iyiHeston } },
    { uyumlar: { heston: kotuHeston, svi: iyiSvi } },
    { uyumlar: {}, sonIyi: { heston: { params: { kappa: 1 }, rmse: 0.01 } } },
    { uyumlar: {} },
  ]) {
    const r = sec(girdi);
    assert.ok(r.aciklama && r.aciklama.length > 0, 'aciklama bos: ' + r.kaynak);
  }
});

test('kullaniciya giden metinler INGILIZCE', () => {
  // sebep ve aciklama arayuzdeki durum satirina basiliyor. GUI'nin
  // tamami Ingilizce oldugu icin bu metinlerde Turkce kelime olmamali;
  // ilk surumde "uyum yok" ekrana sizmisti.
  const turkce = /\b(uyum|yok|kullanildi|yakinsamadi|basarili|puan|esik|parametreleri|kalibrasyon)\b/i;

  const metinler = [];
  for (const girdi of [
    { uyumlar: { heston: iyiHeston } },
    { uyumlar: { heston: kotuHeston, svi: iyiSvi } },
    { uyumlar: { heston: kotuHeston, svi: { params: { a: 1 }, rmseIv: 0.9 } },
      sonIyi: { heston: { params: { kappa: 1 }, rmse: 0.01 } } },
    { uyumlar: {}, sonIyi: { svi: { params: { a: 1 }, rmse: 0.01 } } },
    { uyumlar: {} },
    { uyumlar: { heston: { params: { kappa: NaN }, rmseIv: 0.01 } } },
    { uyumlar: { heston: { params: { kappa: 1 }, rmseIv: NaN } } },
  ]) {
    const r = sec(girdi);
    metinler.push(r.aciklama);
    for (const m of Object.keys(r.redSebepleri || {})) metinler.push(r.redSebepleri[m]);
  }

  for (const m of metinler) {
    assert.ok(!turkce.test(m), `Turkce metin arayuze sizmis: "${m}"`);
  }
});

test('yalnizca taze uyumlar guvenilir isaretlenir', () => {
  assert.equal(sec({ uyumlar: { heston: iyiHeston } }).guvenilir, true);
  assert.equal(sec({ uyumlar: { heston: kotuHeston, svi: iyiSvi } }).guvenilir, true);
  assert.equal(sec({ uyumlar: {}, sonIyi: { heston: { params: {}, rmse: 0 } } }).guvenilir, false);
  assert.equal(sec({ uyumlar: {} }).guvenilir, false);
});

// --- veri yasi -------------------------------------------------------------

test('taze veri taze isaretlenir', () => {
  const simdi = Date.parse('2026-10-06T12:00:00Z');
  const y = MF.veriYasi('2026-10-06T11:59:30Z', simdi);
  assert.equal(y.durum, 'taze');
  assert.equal(y.saniye, 30);
});

test('bayat ve cok bayat ayrisir', () => {
  const simdi = Date.parse('2026-10-06T12:00:00Z');
  assert.equal(MF.veriYasi('2026-10-06T11:55:00Z', simdi).durum, 'bayat');
  assert.equal(MF.veriYasi('2026-10-06T09:00:00Z', simdi).durum, 'cok-bayat');
});

test('yas metni insan okur gibi', () => {
  const simdi = Date.parse('2026-10-06T12:00:00Z');
  assert.equal(MF.veriYasi('2026-10-06T11:59:15Z', simdi).metin, '45s old');
  assert.equal(MF.veriYasi('2026-10-06T11:30:00Z', simdi).metin, '30m old');
  assert.equal(MF.veriYasi('2026-10-06T09:00:00Z', simdi).metin, '3h old');
  assert.equal(MF.veriYasi('2026-10-04T12:00:00Z', simdi).metin, '2d old');
});

test('zaman damgasi yoksa bilinmiyor denir, taze denmez', () => {
  for (const v of [null, undefined, '', 'abc']) {
    const y = MF.veriYasi(v);
    assert.equal(y.durum, 'bilinmiyor');
    assert.equal(y.saniye, null);
  }
});

test('gelecege ait damga negatif yas uretmez', () => {
  const simdi = Date.parse('2026-10-06T12:00:00Z');
  assert.equal(MF.veriYasi('2026-10-06T12:05:00Z', simdi).saniye, 0);
});

test('esikler disaridan verilebilir', () => {
  const simdi = Date.parse('2026-10-06T12:00:00Z');
  const y = MF.veriYasi('2026-10-06T11:59:50Z', simdi, { taze: 5, bayat: 20 });
  assert.equal(y.durum, 'bayat', '10sn, 5sn esigine gore bayat olmali');
});

// --- volatilite yuzeyi: dilim kabul kararlari ------------------------------

test('moneyness izgarasi para-basini icerir ve sinirlarda biter', () => {
  const g = MF.yuzeyIzgarasi(0.85, 1.15, 0.025);
  assert.equal(g[0], 0.85);
  assert.equal(g[g.length - 1], 1.15, 'kayan nokta birikimi son adimi dusurmemeli');
  assert.ok(g.includes(1), 'para-basi izgarada olmali');
  // Adimlar duzgun
  for (let i = 1; i < g.length; i++) {
    assert.ok(Math.abs((g[i] - g[i - 1]) - 0.025) < 1e-9, `adim ${i} bozuk`);
  }
});

test('izgara varsayilanlarla da calisir', () => {
  const g = MF.yuzeyIzgarasi();
  assert.ok(g.length > 5);
  assert.ok(g.includes(1));
});

test('IKI NOKTADAN AZ dilim yuzeye girmez', () => {
  // Iki noktadan az ile kalibrasyon yapilamaz.
  assert.equal(MF.dilimKabulu(0, true).kabul, false);
  assert.equal(MF.dilimKabulu(1, true).kabul, false);
  assert.match(MF.dilimKabulu(1, true).sebep, /usable quote/);
  assert.equal(MF.dilimKabulu(2, true).kabul, true);
});

test('KALIBRE EDILEMEYEN dilim yuzeye girmez', () => {
  // Tohum parametreleriyle cizilmis bir dilim, gercek kalibrasyondan
  // ayirt edilemezdi; komsulardan doldurmak da yanlis olurdu.
  const k = MF.dilimKabulu(9, false);
  assert.equal(k.kabul, false);
  assert.equal(k.sebep, 'not calibrated');
});

test('nokta sayisi yetersizse sebep KALIBRASYON degil nokta eksikligi', () => {
  // Siralamanin onemi: 1 nokta hem az hem kalibre edilemez, ama
  // kullaniciya soylenecek sey "yeterli kotasyon yok".
  assert.match(MF.dilimKabulu(1, false).sebep, /usable quote/);
});

test('zayif belirlenmislik parametre sayisina gore', () => {
  // Hem Heston hem SVI bes parametreli: 10 gozlemin altinda zayif.
  assert.equal(MF.zayifBelirlenmisMi(9), true);
  assert.equal(MF.zayifBelirlenmisMi(10), false);
  assert.equal(MF.zayifBelirlenmisMi(20), false);
  assert.equal(MF.zayifBelirlenmisMi(0), true);
  // Parametre sayisi disaridan verilebilir
  assert.equal(MF.zayifBelirlenmisMi(9, 3), false);
  assert.equal(MF.zayifBelirlenmisMi(5, 3), true);
});

test('zayif belirlenmislik kabulu ENGELLEMEZ', () => {
  // Dilim yuzeye girer ama uyarilir: gizlemek yerine isaretlemek.
  assert.equal(MF.dilimKabulu(9, true).kabul, true);
  assert.equal(MF.zayifBelirlenmisMi(9), true);
});
