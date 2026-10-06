/**
 * Izleme listesi testleri.
 *
 *   node --test tests/test_watchlist.mjs
 *
 * Iki sey onemli: depolama erisilemez oldugunda (gizli pencere, site
 * verisi engellenmis) liste CALISMAYA DEVAM ETMELI, ve bos bir listeyle
 * suzme yapildiginda tablo bosalmamali.
 */

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');

// Sahte localStorage — Node'da yok.
function sahteDepo() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    _m: m,
  };
}

globalThis.localStorage = sahteDepo();
const W = require(path.join(KOK, 'watchlist.js'));

beforeEach(() => {
  globalThis.localStorage = sahteDepo();
  W._sifirla();
});

// --- temel islemler --------------------------------------------------------

test('bos baslar', () => {
  assert.deepEqual(W.oku(), []);
  assert.equal(W.izleniyorMu('THYAO'), false);
});

test('ekle ve cikar', () => {
  W.ekle('THYAO');
  assert.deepEqual(W.oku(), ['THYAO']);
  assert.equal(W.izleniyorMu('THYAO'), true);
  W.cikar('THYAO');
  assert.deepEqual(W.oku(), []);
});

test('degistir yeni durumu doner', () => {
  assert.equal(W.degistir('GARAN'), true);
  assert.equal(W.izleniyorMu('GARAN'), true);
  assert.equal(W.degistir('GARAN'), false);
  assert.equal(W.izleniyorMu('GARAN'), false);
});

test('ticker buyuk harfe cevrilir ve bosluk kirpilir', () => {
  W.ekle('  thyao ');
  assert.deepEqual(W.oku(), ['THYAO']);
  assert.equal(W.izleniyorMu('thyao'), true);
});

test('ayni ticker iki kez eklenmez', () => {
  W.ekle('THYAO'); W.ekle('THYAO'); W.ekle('thyao');
  assert.deepEqual(W.oku(), ['THYAO']);
});

test('alfabetik sirali durur', () => {
  W.ekle('THYAO'); W.ekle('AKBNK'); W.ekle('GARAN');
  assert.deepEqual(W.oku(), ['AKBNK', 'GARAN', 'THYAO']);
});

test('bos ve gecersiz girdiler yok sayilir', () => {
  W.ekle(''); W.ekle('   '); W.ekle(null); W.ekle(undefined);
  assert.deepEqual(W.oku(), []);
  assert.equal(W.degistir(''), false);
});

test('temizle hepsini siler', () => {
  W.ekle('A'); W.ekle('B');
  W.temizle();
  assert.deepEqual(W.oku(), []);
});

// --- suzme -----------------------------------------------------------------

const SATIRLAR = [
  { Ticker: 'THYAO', v: 1 }, { Ticker: 'GARAN', v: 2 }, { Ticker: 'AKBNK', v: 3 },
];

test('liste BOSSA hicbir sey suzulmez', () => {
  // Bos listeyle tabloyu bosaltmak kullaniciya "veri yok" gibi gorunurdu.
  assert.equal(W.suz(SATIRLAR, 'Ticker').length, 3);
});

test('liste doluyken yalnizca izlenenler kalir', () => {
  W.ekle('THYAO'); W.ekle('AKBNK');
  const s = W.suz(SATIRLAR, 'Ticker');
  assert.deepEqual(s.map((r) => r.Ticker), ['THYAO', 'AKBNK']);
});

test('suzme girdi dizisini DEGISTIRMEZ', () => {
  W.ekle('THYAO');
  const kopya = SATIRLAR.slice();
  W.suz(SATIRLAR, 'Ticker');
  assert.deepEqual(SATIRLAR, kopya);
});

test('alan adi verilebilir', () => {
  W.ekle('XAUUSD');
  const s = W.suz([{ underlying: 'XAUUSD' }, { underlying: 'XU030' }], 'underlying');
  assert.equal(s.length, 1);
});

test('gecersiz girdi cokme yapmaz', () => {
  assert.deepEqual(W.suz(null, 'Ticker'), []);
  assert.deepEqual(W.suz(undefined), []);
  W.ekle('THYAO');
  assert.deepEqual(W.suz([null, undefined, {}], 'Ticker'), []);
});

// --- depolama dayanikliligi ------------------------------------------------

test('localStorage YOKKEN bile calisir', () => {
  // Gizli pencerede ya da site verisi engellendiginde erisim istisna
  // firlatir; bunun bir tabloyu cokertmesi sacma olur.
  const yedek = globalThis.localStorage;
  delete globalThis.localStorage;
  W._sifirla();
  try {
    assert.deepEqual(W.oku(), []);
    W.ekle('THYAO');
    assert.equal(W.izleniyorMu('THYAO'), true, 'depolama olmadan bellek yedegi calismali');
    assert.deepEqual(W.suz(SATIRLAR, 'Ticker').map((r) => r.Ticker), ['THYAO']);
  } finally {
    globalThis.localStorage = yedek;
    W._sifirla();
  }
});

test('setItem istisna atarsa liste yine calisir', () => {
  const d = sahteDepo();
  d.setItem = () => { throw new Error('QuotaExceededError'); };
  globalThis.localStorage = d;
  W._sifirla();
  assert.doesNotThrow(() => W.ekle('THYAO'));
  assert.equal(W.izleniyorMu('THYAO'), true);
});

test('bozuk depolama icerigi cokme yapmaz', () => {
  globalThis.localStorage = sahteDepo();
  globalThis.localStorage.setItem(W.ANAHTAR, '{bu json degil');
  W._sifirla();
  assert.deepEqual(W.oku(), []);
});

test('depolamada dizi olmayan deger yok sayilir', () => {
  globalThis.localStorage = sahteDepo();
  globalThis.localStorage.setItem(W.ANAHTAR, '{"a":1}');
  W._sifirla();
  assert.deepEqual(W.oku(), []);
});

test('elle kurcalanmis liste temizlenerek okunur', () => {
  globalThis.localStorage = sahteDepo();
  globalThis.localStorage.setItem(W.ANAHTAR, '["thyao","",null,"THYAO","garan"]');
  W._sifirla();
  assert.deepEqual(W.oku(), ['GARAN', 'THYAO']);
});
