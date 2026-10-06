/**
 * watchlist.js — izleme listesi.
 *
 * Kullanici yildizladigi ticker'lari tarayicida tutar; sunucu tarafi
 * degisiklik gerekmez. Dokumanda Ay 4'te gecen "izleme listeleri".
 *
 * NEDEN AYRI DOSYA
 * Birden cok tabloda kullanilacak (Realized Vols, Other Assets) ve
 * suzme mantigi test edilebilir olmali. Depolama erisimi de tek yerde
 * toplanmali: localStorage gizli pencerede ya da site verisi
 * engellendiginde ISTISNA FIRLATIR, ve bunun bir tabloyu komple
 * cokertmesi sacma olur. Her erisim try/catch icinde ve depolama
 * calismasa bile liste bellekte calismaya devam eder.
 *
 * Hem Node'dan require edilir (testler) hem tarayiciya servis edilir.
 */

(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root._izleme = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var ANAHTAR = 'derivexWatchlist';

  // Depolama calismazsa buraya duser: oturum boyunca yine de calisir.
  var bellekYedek = null;

  function _depo() {
    try {
      if (typeof localStorage !== 'undefined') return localStorage;
    } catch (_) { /* erisim engellendi */ }
    return null;
  }

  function _normalize(t) {
    return String(t === null || t === undefined ? '' : t).trim().toUpperCase();
  }

  /** Izlenen ticker'lar, alfabetik. */
  function oku() {
    if (bellekYedek) return bellekYedek.slice();
    var d = _depo();
    if (!d) return [];
    try {
      var ham = d.getItem(ANAHTAR);
      if (!ham) return [];
      var liste = JSON.parse(ham);
      if (!Array.isArray(liste)) return [];
      // Yazarken temizleniyor ama elle kurcalanmis olabilir
      var temiz = [];
      for (var i = 0; i < liste.length; i++) {
        var t = _normalize(liste[i]);
        if (t && temiz.indexOf(t) === -1) temiz.push(t);
      }
      return temiz.sort();
    } catch (_) {
      return [];
    }
  }

  function _yaz(liste) {
    var temiz = [];
    for (var i = 0; i < liste.length; i++) {
      var t = _normalize(liste[i]);
      if (t && temiz.indexOf(t) === -1) temiz.push(t);
    }
    temiz.sort();
    bellekYedek = temiz.slice();
    var d = _depo();
    if (d) {
      try { d.setItem(ANAHTAR, JSON.stringify(temiz)); } catch (_) { /* kota/gizli pencere */ }
    }
    return temiz;
  }

  function izleniyorMu(ticker) {
    var t = _normalize(ticker);
    return !!t && oku().indexOf(t) !== -1;
  }

  function ekle(ticker) {
    var t = _normalize(ticker);
    if (!t) return oku();
    var l = oku();
    if (l.indexOf(t) === -1) l.push(t);
    return _yaz(l);
  }

  function cikar(ticker) {
    var t = _normalize(ticker);
    return _yaz(oku().filter(function (x) { return x !== t; }));
  }

  /** Yildizi ters cevirir; yeni durumu doner (true = izleniyor). */
  function degistir(ticker) {
    var t = _normalize(ticker);
    if (!t) return false;
    if (izleniyorMu(t)) { cikar(t); return false; }
    ekle(t);
    return true;
  }

  function temizle() {
    return _yaz([]);
  }

  /**
   * Satirlari izlenenlere gore suzer.
   *
   * Liste BOSSA hicbir sey suzulmez: bos bir listeyle tabloyu
   * bosaltmak, kullaniciya "veri yok" gibi gorunurdu.
   */
  function suz(satirlar, alan) {
    if (!Array.isArray(satirlar)) return [];
    var izlenen = oku();
    if (!izlenen.length) return satirlar.slice();
    var ad = alan || 'Ticker';
    return satirlar.filter(function (r) {
      return izlenen.indexOf(_normalize(r && r[ad])) !== -1;
    });
  }

  return {
    oku: oku,
    ekle: ekle,
    cikar: cikar,
    degistir: degistir,
    izleniyorMu: izleniyorMu,
    temizle: temizle,
    suz: suz,
    ANAHTAR: ANAHTAR,
    // Testlerin bellek yedegini sifirlayabilmesi icin
    _sifirla: function () { bellekYedek = null; },
  };
}));
