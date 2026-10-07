/**
 * API kimlik dogrulama ve hiz siniri testleri.
 *
 *   node --test tests/test_auth.mjs
 *
 * Bu dosyadaki testlerin cogu ATLATMA yollarini kovaliyor: gecersiz
 * anahtarin gecmesi, X-Forwarded-For ile yerel gorunme, hiz sinirinin
 * pencere sinirinda iki kati trafige izin vermesi.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const A = require(path.join(KOK, 'auth.js'));

const ANAHTAR = 'cokgizlianahtar12345678';
const anahtarlar = A.anahtarlariCoz(`partner1:${ANAHTAR},partner2:bambaskabiranahtar99`);

function istek({ key, bearer, ip = '10.0.0.5', basliklar = {} } = {}) {
  const h = { ...basliklar };
  if (key) h['x-api-key'] = key;
  if (bearer) h.authorization = 'Bearer ' + bearer;
  return { headers: h, socket: { remoteAddress: ip } };
}

const ayar = (ek = {}) => ({ anahtarlar, yerelMuaf: true, ...ek });

// --- anahtar cozme ---------------------------------------------------------

test('ad:anahtar bicimi cozulur', () => {
  const m = A.anahtarlariCoz('a:anahtarbir123,b:anahtariki456');
  assert.equal(m.size, 2);
  assert.equal(m.get('anahtarbir123'), 'a');
  assert.equal(m.get('anahtariki456'), 'b');
});

test('adsiz anahtar da kabul edilir', () => {
  const m = A.anahtarlariCoz('sadeceanahtar123');
  assert.equal(m.get('sadeceanahtar123'), 'unnamed');
});

test('KISA anahtarlar reddedilir', () => {
  // 8 karakterden kisa bir anahtar kaba kuvvete acik; sessizce kabul
  // edilmesi guvenlik yanilsamasi yaratirdi.
  assert.equal(A.anahtarlariCoz('a:kisa').size, 0);
  assert.equal(A.anahtarlariCoz('a:1234567').size, 0);
  assert.equal(A.anahtarlariCoz('a:12345678').size, 1);
});

test('bos ve bozuk girdi cokme yapmaz', () => {
  for (const v of ['', null, undefined, ',,,', '  ']) {
    assert.equal(A.anahtarlariCoz(v).size, 0);
  }
});

// --- anahtar cikarma -------------------------------------------------------

test('X-API-Key basligindan okunur', () => {
  assert.equal(A.istekAnahtari(istek({ key: 'abc' })), 'abc');
});

test('Bearer basligindan okunur', () => {
  assert.equal(A.istekAnahtari(istek({ bearer: 'abc' })), 'abc');
  assert.equal(A.istekAnahtari({ headers: { authorization: 'bearer xyz' }, socket: {} }), 'xyz');
});

test('anahtar yoksa null', () => {
  assert.equal(A.istekAnahtari(istek()), null);
  assert.equal(A.istekAnahtari({ headers: { authorization: 'Basic abc' }, socket: {} }), null);
  assert.equal(A.istekAnahtari({}), null);
});

// --- yerellik --------------------------------------------------------------

test('127.0.0.1 ve ::1 yerel sayilir', () => {
  for (const ip of ['127.0.0.1', '::1', '::ffff:127.0.0.1']) {
    assert.equal(A.yerelMi({ socket: { remoteAddress: ip } }), true, ip);
  }
});

test('X-Forwarded-For ile yerel gorunulemez', () => {
  // Istemcinin gonderdigi bir basliktir; guvenilen bir vekil sunucu
  // olmadan ona bakmak dogrudan atlatma yolu acardi.
  const r = {
    headers: { 'x-forwarded-for': '127.0.0.1', 'x-real-ip': '127.0.0.1' },
    socket: { remoteAddress: '10.0.0.5' },
  };
  assert.equal(A.yerelMi(r), false, 'baslik taklidiyle yerel gorunulemez');
});

// --- karar -----------------------------------------------------------------

test('anahtar TANIMLI DEGILSE dogrulama kapalidir', () => {
  const k = A.kontrolEt(istek(), '/api/spot', { anahtarlar: new Map() });
  assert.equal(k.izin, true);
  assert.match(k.sebep, /disabled/);
});

test('korunmayan yollar anahtarsiz gecer', () => {
  for (const yol of ['/', '/health', '/market/futures', '/watchlist.js']) {
    assert.equal(A.kontrolEt(istek(), yol, ayar()).izin, true, yol);
  }
});

test('/health anahtar tanimliyken bile acik', () => {
  // Kapali olsaydi konteyner saglik kontrolu de anahtar tasimak
  // zorunda kalirdi.
  assert.equal(A.kontrolEt(istek(), '/health', ayar()).izin, true);
});

test('uzak istek anahtarsiz 401', () => {
  const k = A.kontrolEt(istek(), '/api/spot', ayar());
  assert.equal(k.izin, false);
  assert.equal(k.durum, 401);
});

test('GECERSIZ anahtar 403', () => {
  const k = A.kontrolEt(istek({ key: 'yanlisanahtar12345' }), '/api/spot', ayar());
  assert.equal(k.izin, false);
  assert.equal(k.durum, 403);
});

test('gecerli anahtar gecer ve kimligi doner', () => {
  const k = A.kontrolEt(istek({ key: ANAHTAR }), '/api/spot', ayar());
  assert.equal(k.izin, true);
  assert.equal(k.kimlik, 'partner1');
});

test('Bearer ile de gecer', () => {
  assert.equal(A.kontrolEt(istek({ bearer: ANAHTAR }), '/api/spot', ayar()).kimlik, 'partner1');
});

test('yerel istek anahtarsiz gecer (varsayilan)', () => {
  const k = A.kontrolEt(istek({ ip: '127.0.0.1' }), '/api/spot', ayar());
  assert.equal(k.izin, true);
  assert.equal(k.kimlik, 'local');
});

test('yerel muafiyet KAPATILABILIR', () => {
  const k = A.kontrolEt(istek({ ip: '127.0.0.1' }), '/api/spot',
                        ayar({ yerelMuaf: false }));
  assert.equal(k.izin, false);
  assert.equal(k.durum, 401);
});

test('anahtarin bir kismini vermek yetmez', () => {
  const k = A.kontrolEt(istek({ key: ANAHTAR.slice(0, 10) }), '/api/spot', ayar());
  assert.equal(k.izin, false);
});

// --- hiz siniri ------------------------------------------------------------

test('limit asilinca 429 ve Retry-After', () => {
  const hs = new A.HizSiniri(3);
  const a = ayar({ hizSiniri: hs });
  for (let i = 0; i < 3; i++) {
    assert.equal(A.kontrolEt(istek({ key: ANAHTAR }), '/api/spot', a).izin, true, `${i}. istek`);
  }
  const k = A.kontrolEt(istek({ key: ANAHTAR }), '/api/spot', a);
  assert.equal(k.izin, false);
  assert.equal(k.durum, 429);
  assert.ok(k.retryAfter >= 0);
});

test('hiz siniri ANAHTAR BASINA', () => {
  const hs = new A.HizSiniri(2);
  const a = ayar({ hizSiniri: hs });
  A.kontrolEt(istek({ key: ANAHTAR }), '/api/spot', a);
  A.kontrolEt(istek({ key: ANAHTAR }), '/api/spot', a);
  // partner1 doldu; partner2 etkilenmemeli
  assert.equal(A.kontrolEt(istek({ key: ANAHTAR }), '/api/spot', a).izin, false);
  assert.equal(A.kontrolEt(istek({ key: 'bambaskabiranahtar99' }), '/api/spot', a).izin, true);
});

test('pencere KAYAR, sabit degil', () => {
  // Sabit pencerede 59. saniyede limit kadar, 61. saniyede yine limit
  // kadar istek gecerdi — yani sinirin iki kati. Kayan pencere bunu
  // engeller.
  const hs = new A.HizSiniri(2, 1000);
  const t0 = 10_000;
  assert.equal(hs.dene('k', t0).izin, true);
  assert.equal(hs.dene('k', t0 + 900).izin, true);
  assert.equal(hs.dene('k', t0 + 950).izin, false, 'pencere icinde ucuncu gecmemeli');
  // Ilk kayit pencereden cikinca yer acilir
  assert.equal(hs.dene('k', t0 + 1100).izin, true);
  // Ama ikincisi hala pencerede
  assert.equal(hs.dene('k', t0 + 1150).izin, false);
});

test('limit 0 ise sinir yok', () => {
  const hs = new A.HizSiniri(0);
  for (let i = 0; i < 100; i++) assert.equal(hs.dene('k').izin, true);
});

test('temizle bos kayitlari dusurur', () => {
  const hs = new A.HizSiniri(5, 1000);
  hs.dene('k', 1000);
  assert.equal(hs.kayitlar.size, 1);
  hs.temizle(3000);
  assert.equal(hs.kayitlar.size, 0, 'bellekte birikmemeli');
});

// --- anahtar uretimi -------------------------------------------------------

test('uretilen anahtar yeterince uzun ve her seferinde farkli', () => {
  const a = A.anahtarUret();
  const b = A.anahtarUret();
  assert.ok(a.length >= 24, 'anahtar kisa');
  assert.notEqual(a, b);
  // Uretilen anahtar kendi cozucusunden gecmeli
  assert.equal(A.anahtarlariCoz(`x:${a}`).size, 1);
});

// --- oturum cerezi (sayfa korumasi) ----------------------------------------

const SIR = 'test-oturum-sirri-123456';

test('uretilen cerez dogrulanir ve kimligi tasir', () => {
  const c = A.cerezUret('partner1', SIR);
  assert.equal(A.cerezDogrula(c, SIR, 3600), 'partner1');
});

test('BASKA sir ile dogrulanmaz', () => {
  // Imzasiz ya da sahte imzali bir cerez, icerigi degistirip baskasi
  // gibi gorunmeye izin verirdi.
  const c = A.cerezUret('partner1', SIR);
  assert.equal(A.cerezDogrula(c, 'baska-sir-98765432', 3600), null);
});

test('KURCALANAN cerez reddedilir', () => {
  const c = A.cerezUret('partner1', SIR);
  const i = c.lastIndexOf('.');
  // Govdeyi degistir, imzayi oldugu gibi birak
  const sahte = 'yonetici|' + Date.now() + c.slice(i);
  assert.equal(A.cerezDogrula(sahte, SIR, 3600), null);
});

test('SURESI DOLMUS cerez reddedilir', () => {
  // 13 saat once verilmis bir cerez, 12 saatlik omurle gecersiz olmali.
  const eski = Date.now() - 13 * 3600 * 1000;
  const c = A.cerezUret('partner1', SIR, eski);
  assert.equal(A.cerezDogrula(c, SIR, 12 * 3600), null, 'omur asilinca gecersiz olmali');
  // Ayni cerez daha uzun omurle hala gecerli
  assert.equal(A.cerezDogrula(c, SIR, 24 * 3600), 'partner1');
});

test('bozuk cerez degerleri cokme yapmaz', () => {
  for (const v of ['', null, undefined, 'nokta-yok', 'a.b', '|.imza', 'ad|abc.imza']) {
    assert.equal(A.cerezDogrula(v, SIR, 3600), null, String(v));
  }
});

test('cerez basliktan cikarilir', () => {
  const r = { headers: { cookie: 'other=1; derivex_session=abc.def; x=2' }, socket: {} };
  assert.equal(A.istekCerezi(r), 'abc.def');
  assert.equal(A.istekCerezi({ headers: {}, socket: {} }), null);
});

// --- sayfa korumasi --------------------------------------------------------

test('sayfalariKoru KAPALIYKEN sayfalar acik', () => {
  const k = A.kontrolEt(istek(), '/market/futures', ayar());
  assert.equal(k.izin, true);
});

test('sayfalariKoru ACIKKEN sayfalar da anahtar ister', () => {
  const k = A.kontrolEt(istek(), '/market/futures', ayar({ sayfalariKoru: true }));
  assert.equal(k.izin, false);
  assert.equal(k.durum, 401);
});

test('sayfa korumasi acikken /health yine muaf', () => {
  // Kapali olsaydi konteyner saglik kontrolu de anahtar tasimak
  // zorunda kalirdi.
  assert.equal(A.kontrolEt(istek(), '/health', ayar({ sayfalariKoru: true })).izin, true);
});

test('gecerli oturum cerezi sayfayi acar', () => {
  const c = A.cerezUret('partner1', SIR);
  const r = istek({ basliklar: { cookie: `derivex_session=${c}` } });
  const k = A.kontrolEt(r, '/market/futures',
                        ayar({ sayfalariKoru: true, cerezSir: SIR, cerezOmurSn: 3600 }));
  assert.equal(k.izin, true);
  assert.equal(k.sebep, 'session');
  assert.equal(k.kimlik, 'partner1');
});

test('gecersiz cerez anahtar yoluna duser', () => {
  const r = istek({ basliklar: { cookie: 'derivex_session=sahte.imza' } });
  const k = A.kontrolEt(r, '/api/spot',
                        ayar({ cerezSir: SIR, cerezOmurSn: 3600 }));
  assert.equal(k.izin, false);
  assert.equal(k.durum, 401, 'cerez gecersizse anahtar sorulmali');
});

test('cerez API ucunda da gecerli', () => {
  const c = A.cerezUret('partner1', SIR);
  const r = istek({ basliklar: { cookie: `derivex_session=${c}` } });
  assert.equal(A.kontrolEt(r, '/api/spot', ayar({ cerezSir: SIR, cerezOmurSn: 3600 })).izin, true);
});
