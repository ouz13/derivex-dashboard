/**
 * ws.js — WebSocket (RFC 6455) sunucu tarafi, bagimliliksiz.
 *
 * NEDEN BU DOSYA VAR
 * Kurumsal tuketiciler veriyi HTTP ile YOKLAMAK zorundaydi: her guncelleme
 * icin yeni istek, aradaki degisiklikleri kacirma riski ve gereksiz yuk.
 * Dokuman (Ay 2) REST'in yaninda WebSocket istiyor.
 *
 * NEDEN ELLE YAZILDI
 * Projede hicbir calisma zamani bagimliligi yok; bunu tek bir ozellik icin
 * bozmak, kurulumu ve guvenlik yuzeyini kalicilastiracak bir maliyet.
 * El sikismasi ve cerceveleme toplamda ~150 satir.
 *
 * KAPSAM
 * Sunucu -> istemci yayin icin gereken kadari: metin cerceveleri, ping/pong,
 * duzgun kapanis. Desteklenmeyenler acikca reddediliyor (parcali cerceve,
 * ikili veri, uzanti/eklenti) — sessizce yanlis cozmek yerine.
 */

'use strict';

const crypto = require('crypto');

// RFC 6455'te sabitlenmis sihirli dize; el sikismasinin kendisi bu.
const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

const OPCODE = {
  DEVAM: 0x0, METIN: 0x1, IKILI: 0x2,
  KAPAT: 0x8, PING: 0x9, PONG: 0xA,
};

// Tek cercevede kabul edilen azami yuk. Istemciden gelen devasa bir
// uzunluk alani, bellek ayirmaya calisirken sunucuyu dusurebilirdi.
const AZAMI_YUK = 1 << 20;   // 1 MB

/** Sec-WebSocket-Accept degeri. */
function kabulAnahtari(istemciAnahtari) {
  return crypto.createHash('sha1').update(istemciAnahtari + GUID).digest('base64');
}

/**
 * Yukseltme istegi gecerli mi?
 * doner: {ok, anahtar} ya da {ok:false, sebep}
 */
function yukseltmeGecerliMi(req) {
  const h = (req && req.headers) || {};
  if (String(h.upgrade || '').toLowerCase() !== 'websocket') {
    return { ok: false, sebep: 'Upgrade header must be websocket' };
  }
  if (!String(h.connection || '').toLowerCase().includes('upgrade')) {
    return { ok: false, sebep: 'Connection header must include upgrade' };
  }
  if (String(h['sec-websocket-version'] || '') !== '13') {
    return { ok: false, sebep: 'only WebSocket version 13 is supported' };
  }
  const anahtar = h['sec-websocket-key'];
  if (!anahtar || Buffer.from(String(anahtar), 'base64').length !== 16) {
    return { ok: false, sebep: 'invalid Sec-WebSocket-Key' };
  }
  return { ok: true, anahtar: String(anahtar) };
}

/** El sikisma yanitini soketе yazar. */
function elSikismaYaniti(anahtar) {
  return [
    'HTTP/1.1 101 Switching Protocols',
    'Upgrade: websocket',
    'Connection: Upgrade',
    `Sec-WebSocket-Accept: ${kabulAnahtari(anahtar)}`,
    '', '',
  ].join('\r\n');
}

/** Yukseltmeyi reddeder (soket HTTP yanitiyla kapatilir). */
function reddet(socket, durum, sebep) {
  const govde = JSON.stringify({ ok: false, error: sebep });
  socket.end(
    `HTTP/1.1 ${durum} ${durum === 401 ? 'Unauthorized' : 'Bad Request'}\r\n`
    + 'Content-Type: application/json\r\n'
    + `Content-Length: ${Buffer.byteLength(govde)}\r\n`
    + 'Connection: close\r\n\r\n' + govde);
}

/**
 * Sunucu -> istemci cerceve.
 * Sunucu cerceveleri MASKELENMEZ (RFC 6455 §5.1).
 */
function cerceveKodla(veri, opcode = OPCODE.METIN) {
  const yuk = Buffer.isBuffer(veri) ? veri : Buffer.from(String(veri), 'utf8');
  const n = yuk.length;
  let bas;
  if (n < 126) {
    bas = Buffer.alloc(2);
    bas[1] = n;
  } else if (n < 65536) {
    bas = Buffer.alloc(4);
    bas[1] = 126;
    bas.writeUInt16BE(n, 2);
  } else {
    bas = Buffer.alloc(10);
    bas[1] = 127;
    // 64-bit uzunluk; ust 32 bit sifir (1 MB siniri zaten var)
    bas.writeUInt32BE(0, 2);
    bas.writeUInt32BE(n, 6);
  }
  bas[0] = 0x80 | opcode;        // FIN = 1
  return Buffer.concat([bas, yuk]);
}

/**
 * Artimli cerceve cozucu — YALNIZCA SUNUCU TARAFI.
 *
 * TCP parca sinirlarini korumaz: bir cerceve iki recv'e bolunebilir ya da
 * iki cerceve tek recv'de gelebilir. Bu yuzden tampon tutuluyor ve her
 * cagrida cikarilabilen TUM cerceveler donduruluyor.
 *
 * DIKKAT: Bu cozucu maskesiz cerceveleri REDDEDER, cunku RFC 6455 §5.1
 * istemciden gelen her cerceveyi maskeli olmaya zorlar. SUNUCUDAN gelen
 * cerceveler ise maskesizdir — yani bir ISTEMCI yazarken bu sinifi
 * kullanmayin, maskesiz cozen ayri bir okuyucu gerekir. (Gelistirme
 * sirasinda tam bu yanlisa dusuldugu icin yaziliyor.)
 */
class CerceveCozucu {
  constructor(azamiYuk = AZAMI_YUK) {
    this.tampon = Buffer.alloc(0);
    this.azamiYuk = azamiYuk;
  }

  /** doner: {cerceveler:[{opcode,veri}], hata} */
  ekle(parca) {
    this.tampon = Buffer.concat([this.tampon, parca]);
    const cerceveler = [];

    for (;;) {
      const b = this.tampon;
      if (b.length < 2) break;

      const fin = (b[0] & 0x80) !== 0;
      const rsv = b[0] & 0x70;
      const opcode = b[0] & 0x0f;
      const maskeli = (b[1] & 0x80) !== 0;
      let uzunluk = b[1] & 0x7f;
      let ofset = 2;

      // Uzantilar desteklenmiyor; RSV bitleri sifir olmali.
      if (rsv !== 0) return { cerceveler, hata: 'RSV bits must be zero' };
      // Istemci cerceveleri MASKELENMEK ZORUNDA (RFC 6455 §5.1).
      // Maskesiz kabul etmek, araya giren vekillerde onbellek
      // zehirlenmesine kapi acan bilinen bir hatadir.
      if (!maskeli) return { cerceveler, hata: 'client frames must be masked' };

      if (uzunluk === 126) {
        if (b.length < ofset + 2) break;
        uzunluk = b.readUInt16BE(ofset);
        ofset += 2;
      } else if (uzunluk === 127) {
        if (b.length < ofset + 8) break;
        const ust = b.readUInt32BE(ofset);
        const alt = b.readUInt32BE(ofset + 4);
        if (ust !== 0) return { cerceveler, hata: 'frame too large' };
        uzunluk = alt;
        ofset += 8;
      }

      if (uzunluk > this.azamiYuk) {
        return { cerceveler, hata: `frame exceeds ${this.azamiYuk} bytes` };
      }
      if (b.length < ofset + 4 + uzunluk) break;      // maske + yuk bekleniyor

      const maske = b.slice(ofset, ofset + 4);
      ofset += 4;
      const yuk = Buffer.allocUnsafe(uzunluk);
      for (let i = 0; i < uzunluk; i++) yuk[i] = b[ofset + i] ^ maske[i & 3];
      ofset += uzunluk;
      this.tampon = b.slice(ofset);

      // Parcali cerceve desteklenmiyor: sessizce yanlis birlestirmek
      // yerine acikca reddediliyor.
      if (!fin) return { cerceveler, hata: 'fragmented frames are not supported' };
      if (opcode === OPCODE.DEVAM) {
        return { cerceveler, hata: 'continuation frame without start' };
      }
      cerceveler.push({ opcode, veri: yuk });
    }
    return { cerceveler, hata: null };
  }
}

/** Kapanis cercevesi (kod + sebep). */
function kapanisCercevesi(kod = 1000, sebep = '') {
  const s = Buffer.from(String(sebep), 'utf8');
  const yuk = Buffer.alloc(2 + s.length);
  yuk.writeUInt16BE(kod, 0);
  s.copy(yuk, 2);
  return cerceveKodla(yuk, OPCODE.KAPAT);
}

module.exports = {
  GUID, OPCODE, AZAMI_YUK,
  kabulAnahtari,
  yukseltmeGecerliMi,
  elSikismaYaniti,
  reddet,
  cerceveKodla,
  kapanisCercevesi,
  CerceveCozucu,
};
