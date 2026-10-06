/**
 * auth.js — API anahtari dogrulama ve hiz siniri.
 *
 * NEDEN BU DOSYA VAR
 * /api/* uclarinin tamami aciksti: portfoy pozisyonlari, fiyatlama
 * kayitlari, denetim izi, hepsi. "Kurumsal veri servisi" iddiasiyla en
 * celisen eksik buydu; RUNBOOK'a "disa acik agda calistirmayin" yazmak
 * zorunda kalmistik.
 *
 * KAPSAM — NE YAPAR, NE YAPMAZ
 * Dokuman OAuth2 istiyor. Burada yapilan onun bir ALT KUMESI: API
 * anahtari + anahtar basina hiz siniri. Fark sunu degistirir: OAuth2
 * jeton suresi, yenileme ve yetki kapsami getirir; burada anahtar
 * suresizdir ve tum uclara erisir. Bu bilinerek boyle; tam OAuth2 ayri
 * bir is olarak duruyor.
 *
 * YEREL ISTEKLER MUAF (varsayilan)
 * Veri koprusu ve arayuz ayni makineden konusuyor. Tehdit modeli
 * "agdaki baska biri uclara vuruyor"; isletmecinin kendi makinesi
 * degil. AUTH_ALLOW_LOCAL=0 ile bu muafiyet kaldirilabilir.
 *
 * ANAHTAR TANIMLI DEGILSE kimlik dogrulama KAPALIDIR ve /health bunu
 * bildirir. Sessizce acik birakmak yerine gorunur birakmak:
 * gelistirme akisini bozmadan eksigi ortada tutuyor.
 */

'use strict';

const crypto = require('crypto');

// Kimlik dogrulamanin DISINDA kalan yollar. /health izlemenin
// calismasi icin acik: kapali olsaydi konteyner saglik kontrolu de
// anahtar tasimak zorunda kalirdi.
const MUAF_YOLLAR = new Set(['/health']);

/**
 * "ad1:anahtar1,ad2:anahtar2" bicimini cozer.
 *
 * Anahtari ada degil, anahtara gore indeksler: dogrulama sirasinda
 * aranan sey anahtarin kendisi.
 */
function anahtarlariCoz(metin) {
  const out = new Map();
  if (!metin || typeof metin !== 'string') return out;
  for (const parca of metin.split(',')) {
    const p = parca.trim();
    if (!p) continue;
    const i = p.indexOf(':');
    // Ad verilmemisse anahtarin kendisi ad olur; raporlarda bir sey
    // gorunsun diye.
    const ad = i === -1 ? 'unnamed' : p.slice(0, i).trim();
    const anahtar = i === -1 ? p : p.slice(i + 1).trim();
    if (anahtar.length >= 8) out.set(anahtar, ad || 'unnamed');
  }
  return out;
}

/** Istegin tasidigi anahtari cikarir. */
function istekAnahtari(req) {
  const h = (req && req.headers) || {};
  const x = h['x-api-key'];
  if (typeof x === 'string' && x.trim()) return x.trim();
  const a = h.authorization;
  if (typeof a === 'string') {
    const m = a.match(/^Bearer\s+(.+)$/i);
    if (m) return m[1].trim();
  }
  return null;
}

/**
 * Istek yerel mi?
 *
 * Yalnizca soketin uzak adresine bakilir. X-Forwarded-For'a
 * GUVENILMEZ: istemcinin gonderdigi bir basliktir ve guvenilen bir
 * vekil sunucu olmadan yerel gorunmek icin taklit edilebilir.
 */
function yerelMi(req) {
  const s = (req && req.socket) || {};
  const ip = s.remoteAddress || '';
  return ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1';
}

/** Zamanlama saldirisina kapali karsilastirma. */
function esitMi(a, b) {
  const A = Buffer.from(String(a));
  const B = Buffer.from(String(b));
  // timingSafeEqual esit uzunluk ister; uzunluk farki zaten bilgi
  // sizdirmaz cunku anahtar uzunlugu gizli degil.
  if (A.length !== B.length) return false;
  return crypto.timingSafeEqual(A, B);
}

function anahtariDogrula(anahtarlar, verilen) {
  if (!verilen) return null;
  for (const [gecerli, ad] of anahtarlar) {
    if (esitMi(gecerli, verilen)) return ad;
  }
  return null;
}

/**
 * Anahtar basina kayan pencere hiz siniri.
 *
 * Sabit pencere (dakika basi sayac) pencere sinirinda iki katina izin
 * verir: 59. saniyede limit kadar, 61. saniyede yine limit kadar.
 * Kayan pencere bunu engeller.
 */
class HizSiniri {
  constructor(limit, pencereMs = 60000) {
    this.limit = limit;
    this.pencereMs = pencereMs;
    this.kayitlar = new Map();   // kimlik -> [zaman damgalari]
  }

  /** doner: {izin, kalan, resetMs} */
  dene(kimlik, simdi = Date.now()) {
    if (!this.limit || this.limit <= 0) return { izin: true, kalan: Infinity, resetMs: 0 };
    const esik = simdi - this.pencereMs;
    let liste = this.kayitlar.get(kimlik);
    if (!liste) { liste = []; this.kayitlar.set(kimlik, liste); }
    // Pencere disina cikanlari at
    while (liste.length && liste[0] <= esik) liste.shift();

    if (liste.length >= this.limit) {
      return { izin: false, kalan: 0, resetMs: Math.max(0, liste[0] + this.pencereMs - simdi) };
    }
    liste.push(simdi);
    return { izin: true, kalan: this.limit - liste.length, resetMs: this.pencereMs };
  }

  /** Bellekte birikmesin diye bos kayitlari temizler. */
  temizle(simdi = Date.now()) {
    const esik = simdi - this.pencereMs;
    for (const [k, liste] of this.kayitlar) {
      while (liste.length && liste[0] <= esik) liste.shift();
      if (!liste.length) this.kayitlar.delete(k);
    }
  }
}

/**
 * Tek bir istek icin karar.
 *
 * ayarlar: {anahtarlar, yerelMuaf, korunanOnek, hizSiniri}
 * doner: {izin, durum, sebep, kimlik}
 */
function kontrolEt(req, yol, ayarlar) {
  const a = ayarlar || {};
  const anahtarlar = a.anahtarlar || new Map();
  const onek = a.korunanOnek || '/api/';

  // Anahtar tanimli degilse dogrulama kapalidir.
  if (anahtarlar.size === 0) {
    return { izin: true, durum: 200, sebep: 'auth disabled', kimlik: null };
  }
  if (!yol.startsWith(onek) || MUAF_YOLLAR.has(yol)) {
    return { izin: true, durum: 200, sebep: 'path not protected', kimlik: null };
  }

  const yerel = yerelMi(req);
  if (yerel && a.yerelMuaf !== false) {
    return { izin: true, durum: 200, sebep: 'local', kimlik: 'local' };
  }

  const verilen = istekAnahtari(req);
  if (!verilen) {
    return { izin: false, durum: 401, sebep: 'missing API key', kimlik: null };
  }
  const ad = anahtariDogrula(anahtarlar, verilen);
  if (!ad) {
    return { izin: false, durum: 403, sebep: 'invalid API key', kimlik: null };
  }

  if (a.hizSiniri) {
    const h = a.hizSiniri.dene(ad);
    if (!h.izin) {
      return {
        izin: false, durum: 429, kimlik: ad,
        sebep: 'rate limit exceeded',
        retryAfter: Math.ceil(h.resetMs / 1000),
      };
    }
    return { izin: true, durum: 200, sebep: 'ok', kimlik: ad, kalan: h.kalan };
  }

  return { izin: true, durum: 200, sebep: 'ok', kimlik: ad };
}

/** Kurulum icin guclu bir anahtar uretir. */
function anahtarUret() {
  return crypto.randomBytes(24).toString('base64url');
}

module.exports = {
  anahtarlariCoz,
  istekAnahtari,
  yerelMi,
  anahtariDogrula,
  kontrolEt,
  anahtarUret,
  HizSiniri,
  MUAF_YOLLAR,
};

// node auth.js --generate   -> kuruluma hazir anahtar
if (require.main === module && process.argv.includes('--generate')) {
  const k = anahtarUret();
  console.log(`API_KEYS="partner1:${k}"`);
  console.log('\n.env dosyasina ekleyin ya da ortam degiskeni olarak verin.');
  console.log('Birden fazla: API_KEYS="ad1:anahtar1,ad2:anahtar2"');
}
