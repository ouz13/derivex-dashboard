/**
 * WebSocket (RFC 6455) testleri.
 *
 *   node --test tests/test_ws.mjs
 *
 * Cerceveleme elle yazildigi icin testlerin cogu protokol ayrintilarini
 * kovaliyor: TCP parca sinirlari, maskeleme zorunlulugu, uzunluk
 * kodlamasinin uc esigi (7 bit / 16 bit / 64 bit).
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const W = require(path.join(KOK, 'ws.js'));

/** Istemci tarafi cerceve (maskeli) — testlerde gercek istemciyi taklit eder. */
function istemciCercevesi(veri, opcode = W.OPCODE.METIN) {
  const yuk = Buffer.isBuffer(veri) ? veri : Buffer.from(String(veri), 'utf8');
  const n = yuk.length;
  let bas;
  if (n < 126) { bas = Buffer.alloc(2); bas[1] = 0x80 | n; }
  else if (n < 65536) { bas = Buffer.alloc(4); bas[1] = 0x80 | 126; bas.writeUInt16BE(n, 2); }
  else {
    bas = Buffer.alloc(10); bas[1] = 0x80 | 127;
    bas.writeUInt32BE(0, 2); bas.writeUInt32BE(n, 6);
  }
  bas[0] = 0x80 | opcode;
  const maske = crypto.randomBytes(4);
  const m = Buffer.allocUnsafe(n);
  for (let i = 0; i < n; i++) m[i] = yuk[i] ^ maske[i & 3];
  return Buffer.concat([bas, maske, m]);
}

const istek = (ek = {}) => ({
  headers: {
    upgrade: 'websocket',
    connection: 'Upgrade',
    'sec-websocket-version': '13',
    'sec-websocket-key': crypto.randomBytes(16).toString('base64'),
    ...ek,
  },
  socket: { remoteAddress: '10.0.0.5' },
});

// --- el sikismasi ----------------------------------------------------------

test('kabul anahtari RFC ornegiyle ayni', () => {
  // RFC 6455 §1.3'teki ornek
  assert.equal(W.kabulAnahtari('dGhlIHNhbXBsZSBub25jZQ=='),
               's3pPLMBiTxaQ9kYGzzhZRbK+xOo=');
});

test('gecerli yukseltme kabul edilir', () => {
  const g = W.yukseltmeGecerliMi(istek());
  assert.equal(g.ok, true);
  assert.ok(g.anahtar);
});

test('Upgrade basligi yanlissa reddedilir', () => {
  assert.equal(W.yukseltmeGecerliMi(istek({ upgrade: 'h2c' })).ok, false);
});

test('Connection basligi eksikse reddedilir', () => {
  const r = istek(); delete r.headers.connection;
  assert.equal(W.yukseltmeGecerliMi(r).ok, false);
});

test('yalnizca surum 13 kabul edilir', () => {
  // Eski surumler farkli cerceveleme kullanir; sessizce kabul etmek
  // bozuk veri cozmeye yol acardi.
  for (const v of ['8', '7', '', '14']) {
    assert.equal(W.yukseltmeGecerliMi(istek({ 'sec-websocket-version': v })).ok, false, v);
  }
});

test('gecersiz Sec-WebSocket-Key reddedilir', () => {
  // 16 bayt base64 olmali
  assert.equal(W.yukseltmeGecerliMi(istek({ 'sec-websocket-key': 'kisa' })).ok, false);
  const r = istek(); delete r.headers['sec-websocket-key'];
  assert.equal(W.yukseltmeGecerliMi(r).ok, false);
});

test('el sikisma yaniti 101 ve dogru basliklari tasir', () => {
  const y = W.elSikismaYaniti('dGhlIHNhbXBsZSBub25jZQ==');
  assert.match(y, /^HTTP\/1\.1 101 Switching Protocols/);
  assert.match(y, /Upgrade: websocket/);
  assert.match(y, /Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK\+xOo=/);
  assert.ok(y.endsWith('\r\n\r\n'), 'basliklar bos satirla bitmeli');
});

// --- cerceve kodlama -------------------------------------------------------

test('kisa yuk 2 baytlik baslik kullanir', () => {
  const c = W.cerceveKodla('merhaba');
  assert.equal(c[0], 0x81, 'FIN + metin opcode');
  assert.equal(c[1], 7, 'maskesiz, uzunluk 7');
  assert.equal(c.slice(2).toString(), 'merhaba');
});

test('sunucu cerceveleri MASKELENMEZ', () => {
  // RFC 6455 §5.1: sunucudan gelen cerceveler maskelenmemeli.
  const c = W.cerceveKodla('x');
  assert.equal(c[1] & 0x80, 0, 'maske biti sifir olmali');
});

test('126-65535 arasi 16 bitlik uzunluk', () => {
  const c = W.cerceveKodla('a'.repeat(200));
  assert.equal(c[1], 126);
  assert.equal(c.readUInt16BE(2), 200);
  assert.equal(c.length, 4 + 200);
});

test('65536 ve ustu 64 bitlik uzunluk', () => {
  const c = W.cerceveKodla('a'.repeat(70000));
  assert.equal(c[1], 127);
  assert.equal(c.readUInt32BE(2), 0);
  assert.equal(c.readUInt32BE(6), 70000);
  assert.equal(c.length, 10 + 70000);
});

test('uzunluk esiklerinde sinir degerleri', () => {
  assert.equal(W.cerceveKodla('a'.repeat(125))[1], 125);
  assert.equal(W.cerceveKodla('a'.repeat(126))[1], 126);
  assert.equal(W.cerceveKodla('a'.repeat(65535))[1], 126);
  assert.equal(W.cerceveKodla('a'.repeat(65536))[1], 127);
});

test('kapanis cercevesi kod ve sebep tasir', () => {
  const c = W.kapanisCercevesi(1002, 'protokol');
  assert.equal(c[0] & 0x0f, W.OPCODE.KAPAT);
  assert.equal(c.readUInt16BE(2), 1002);
  assert.equal(c.slice(4).toString(), 'protokol');
});

// --- cerceve cozme ---------------------------------------------------------

test('tek cerceve cozulur ve maske kaldirilir', () => {
  const c = new W.CerceveCozucu();
  const { cerceveler, hata } = c.ekle(istemciCercevesi('selam'));
  assert.equal(hata, null);
  assert.equal(cerceveler.length, 1);
  assert.equal(cerceveler[0].veri.toString(), 'selam');
  assert.equal(cerceveler[0].opcode, W.OPCODE.METIN);
});

test('TEK recv icinde birden cok cerceve', () => {
  const c = new W.CerceveCozucu();
  const birlesik = Buffer.concat([
    istemciCercevesi('bir'), istemciCercevesi('iki'), istemciCercevesi('uc')]);
  const { cerceveler } = c.ekle(birlesik);
  assert.deepEqual(cerceveler.map((f) => f.veri.toString()), ['bir', 'iki', 'uc']);
});

test('BOLUNMUS cerceve parcalar birlesince cozulur', () => {
  // TCP parca sinirlarini korumaz; cerceve iki recv'e bolunebilir.
  const c = new W.CerceveCozucu();
  const tam = istemciCercevesi('bolunmus-mesaj');
  const kesme = 5;
  let r = c.ekle(tam.slice(0, kesme));
  assert.equal(r.cerceveler.length, 0, 'yarim cerceve dondurulmemeli');
  r = c.ekle(tam.slice(kesme));
  assert.equal(r.cerceveler.length, 1);
  assert.equal(r.cerceveler[0].veri.toString(), 'bolunmus-mesaj');
});

test('bayt bayt beslense de cozulur', () => {
  const c = new W.CerceveCozucu();
  const tam = istemciCercevesi('yavas');
  let toplam = [];
  for (const b of tam) {
    const r = c.ekle(Buffer.from([b]));
    toplam = toplam.concat(r.cerceveler);
  }
  assert.equal(toplam.length, 1);
  assert.equal(toplam[0].veri.toString(), 'yavas');
});

test('MASKESIZ istemci cercevesi REDDEDILIR', () => {
  // Maskesiz kabul etmek, araya giren vekillerde onbellek
  // zehirlenmesine kapi acan bilinen bir hatadir.
  const c = new W.CerceveCozucu();
  const { hata } = c.ekle(W.cerceveKodla('maskesiz'));   // sunucu bicimi
  assert.match(hata, /masked/);
});

test('RSV bitleri sifir degilse reddedilir', () => {
  const c = new W.CerceveCozucu();
  const f = istemciCercevesi('x');
  f[0] |= 0x40;                      // RSV1
  assert.match(c.ekle(f).hata, /RSV/);
});

test('PARCALI cerceve acikca reddedilir', () => {
  // Sessizce yanlis birlestirmek yerine reddediliyor.
  const c = new W.CerceveCozucu();
  const f = istemciCercevesi('yarim');
  f[0] &= 0x7f;                      // FIN = 0
  assert.match(c.ekle(f).hata, /fragmented/);
});

test('AZAMI YUKU asan cerceve reddedilir', () => {
  // Devasa bir uzunluk alani, bellek ayirmaya calisirken sunucuyu
  // dusurebilirdi.
  const c = new W.CerceveCozucu(100);
  const f = istemciCercevesi('a'.repeat(200));
  assert.match(c.ekle(f).hata, /exceeds/);
});

test('ping ve kapanis opcode lari tasinir', () => {
  const c = new W.CerceveCozucu();
  const { cerceveler } = c.ekle(Buffer.concat([
    istemciCercevesi('p', W.OPCODE.PING),
    istemciCercevesi('', W.OPCODE.KAPAT)]));
  assert.equal(cerceveler[0].opcode, W.OPCODE.PING);
  assert.equal(cerceveler[1].opcode, W.OPCODE.KAPAT);
});

test('bos yuk cozulur', () => {
  const c = new W.CerceveCozucu();
  const { cerceveler } = c.ekle(istemciCercevesi(''));
  assert.equal(cerceveler.length, 1);
  assert.equal(cerceveler[0].veri.length, 0);
});

test('buyuk yuk gidis-donus', () => {
  const c = new W.CerceveCozucu();
  const metin = 'x'.repeat(70000);
  const { cerceveler, hata } = c.ekle(istemciCercevesi(metin));
  assert.equal(hata, null);
  assert.equal(cerceveler[0].veri.toString(), metin);
});
