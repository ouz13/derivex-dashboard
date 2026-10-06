/**
 * Servis sagligi testleri.
 *
 *   node --test tests/test_health.mjs
 *
 * Asil soru: /health, SORUN OLDUGUNDA sorunu soyluyor mu? Eski surum
 * sabit {"ok":true} donuyordu, yani tam da anlamasi gereken durumu
 * kaciriyordu. Testlerin cogu bozuk halleri kovaliyor.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const H = require(path.join(KOK, 'health.js'));

const SIMDI = Date.parse('2026-10-06T12:00:00Z');
const iso = (snOnce) => new Date(SIMDI - snOnce * 1000).toISOString();

function degerlendir(ek) {
  return H.degerlendir(Object.assign({
    simdi: SIMDI,
    lastPostAt: { spot: iso(2), futures: iso(2), options: iso(3), other: iso(4) },
    sayimlar: { spot: 47, futures: 47, options: 26, other: 9 },
    storeStats: { received_at: iso(10), spot_days: 716, model_versions: 12, write_error: null },
    modelTs: iso(30),
    egriTs: iso(120),
    mockMu: true,
  }, ek));
}

function bul(rapor, ad) {
  return rapor.checks.find((c) => c.name === ad);
}

// --- saglikli hal ----------------------------------------------------------

test('her sey tazeyken healthy', () => {
  const r = degerlendir();
  assert.equal(r.status, 'healthy');
  assert.ok(r.checks.length >= 7, 'kontrol sayisi az');
  assert.ok(r.checks.every((c) => c.status === 'healthy'), 'beklenmeyen bozuk kontrol');
});

test('veri modu raporda yer alir', () => {
  assert.equal(degerlendir({ mockMu: true }).mode, 'MOCK');
  assert.equal(degerlendir({ mockMu: false }).mode, 'LIVE');
});

test('esikler raporda bildirilir', () => {
  const r = degerlendir({ esikler: { taze: 30, bayat: 300 } });
  assert.deepEqual(r.thresholds, { fresh_s: 30, stale_s: 300 });
});

// --- bozuk haller ----------------------------------------------------------

test('HIC veri gelmediyse unhealthy — "bayat" ile ayni sey degil', () => {
  // Bu ayrim onemli: biri kurulumun hic calismadigini, oteki akisin
  // durdugunu gosterir ve mudahalesi farklidir.
  const r = degerlendir({ lastPostAt: { spot: null, futures: null, options: null, other: null } });
  assert.equal(r.status, 'unhealthy');
  assert.equal(bul(r, 'Spot feed').status, 'unhealthy');
  assert.match(bul(r, 'Spot feed').detail, /no data received/);
  assert.equal(bul(r, 'Spot feed').age_s, null);
});

test('akis bayatlayinca degraded olur, unhealthy DEGIL', () => {
  // Seans disinda veri akmamasi normaldir; konteyneri yeniden
  // baslatmayi gerektirmez.
  const r = degerlendir({ lastPostAt: { spot: iso(300), futures: iso(300), options: iso(300), other: iso(300) } });
  assert.equal(r.status, 'degraded');
  assert.equal(bul(r, 'Spot feed').status, 'degraded');
  assert.match(bul(r, 'Spot feed').detail, /stale/);
});

test('cok bayat akis ayri metin verir', () => {
  const r = degerlendir({ lastPostAt: { spot: iso(5000), futures: iso(2), options: iso(2), other: iso(2) } });
  assert.match(bul(r, 'Spot feed').detail, /very stale/);
});

test('depo yazma hatasi unhealthy yapar', () => {
  // Yazma hatasi gercekten engelleyici: gecmis birikmiyor demektir.
  const r = degerlendir({
    storeStats: { received_at: iso(5), write_error: 'OperationalError: disk I/O error' },
  });
  assert.equal(r.status, 'unhealthy');
  assert.equal(bul(r, 'Store').status, 'unhealthy');
  assert.match(bul(r, 'Store').detail, /disk I\/O error/);
});

test('depo raporu hic gelmediyse degraded ve ne yapilacagi yazar', () => {
  const r = degerlendir({ storeStats: null });
  assert.equal(bul(r, 'Store').status, 'degraded');
  assert.match(bul(r, 'Store').detail, /store\.py --push-stats/);
});

test('egri hic uydurulmadiysa komut onerilir', () => {
  const r = degerlendir({ egriTs: null });
  assert.equal(bul(r, 'Yield curve').status, 'degraded');
  assert.match(bul(r, 'Yield curve').detail, /fit_curve\.py/);
});

test('kalibrasyon hic yapilmadiysa degraded', () => {
  const r = degerlendir({ modelTs: null });
  assert.equal(bul(r, 'Volatility calibration').status, 'degraded');
});

// --- tempo farki -----------------------------------------------------------

test('periyodik isler akis esigiyle YARGILANMAZ', () => {
  // Egri 5 dakikada bir uyduruluyor; 2 dakikalik bir egri sagliklidir.
  // Akis esigi (60 sn) buna uygulansaydi her zaman bayat gorunurdu.
  const r = degerlendir({ egriTs: iso(240), modelTs: iso(240) });
  assert.equal(bul(r, 'Yield curve').status, 'healthy',
    '4 dakikalik egri saglikli sayilmali');
  assert.equal(bul(r, 'Volatility calibration').status, 'healthy');
  // Ayni yas akis icin bayattir
  const r2 = degerlendir({ lastPostAt: { spot: iso(240), futures: iso(2), options: iso(2), other: iso(2) } });
  assert.equal(bul(r2, 'Spot feed').status, 'degraded',
    '4 dakikalik akis bayat sayilmali');
});

test('periyodik is bayat esigini asinca degraded', () => {
  const r = degerlendir({ egriTs: iso(1200) });
  assert.equal(bul(r, 'Yield curve').status, 'degraded');
});

// --- genel durumun turetilmesi ---------------------------------------------

test('genel durum EN KOTU kontrolu yansitir', () => {
  const r = degerlendir({
    lastPostAt: { spot: iso(300), futures: iso(2), options: iso(2), other: iso(2) },
  });
  assert.equal(r.status, 'degraded', 'tek bozuk kontrol genel durumu dusurmeli');

  const r2 = degerlendir({
    lastPostAt: { spot: iso(300), futures: iso(2), options: iso(2), other: iso(2) },
    storeStats: { received_at: iso(5), write_error: 'bozuk' },
  });
  assert.equal(r2.status, 'unhealthy', 'unhealthy, degraded i ezmeli');
});

test('tek bir unhealthy genel durumu unhealthy yapar', () => {
  const r = degerlendir({ storeStats: { received_at: iso(5), write_error: 'x' } });
  assert.equal(r.status, 'unhealthy');
  // ... ama digerleri hala saglikli raporlanir
  assert.equal(bul(r, 'Spot feed').status, 'healthy');
});

// --- yas metni -------------------------------------------------------------

test('yas metni insan okur gibi', () => {
  assert.equal(H.yasMetni(null), 'never');
  assert.equal(H.yasMetni(45), '45s');
  assert.equal(H.yasMetni(90), '1m');
  assert.equal(H.yasMetni(7200), '2h');
  assert.equal(H.yasMetni(172800), '2d');
});

test('gecersiz zaman damgasi cokme yapmaz', () => {
  const r = degerlendir({ lastPostAt: { spot: 'abc', futures: iso(2), options: iso(2), other: iso(2) } });
  assert.equal(bul(r, 'Spot feed').status, 'unhealthy');
  assert.equal(bul(r, 'Spot feed').age_s, null);
});

test('bos girdiyle cokmez', () => {
  const r = H.degerlendir({});
  assert.ok(r.status);
  assert.ok(Array.isArray(r.checks));
});
