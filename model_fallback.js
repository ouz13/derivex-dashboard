/**
 * model_fallback.js — model basarisizliginda ne yapilacagi.
 *
 * NEDEN BU DOSYA VAR
 * Kalibrasyon bir "ya calisir ya patlar" isi degil. Uc ayri sekilde
 * bozulabiliyor ve ucunun de davranisi farkli olmali:
 *
 *   1. Hic uyum yapilamaz   — yeterli kotasyon noktasi yok
 *   2. Uyum yapilir ama kotu — optimize edici bir sey dondurur, RMSE %40'tir
 *   3. Veri bayattir         — parametreler iyi ama dayandiklari fiyatlar eski
 *
 * Ikincisi en sinsisi: fitHeston/fitSvi asla null donmuyor (iki noktadan
 * azi disinda), her zaman izgaranin en iyi noktasini veriyor. Uyum
 * kalitesine bakilmadigi surece "kalibre edildi" yazisi, hicbir sey
 * anlatmayan bir parametre setini mesrulastiriyor.
 *
 * ZINCIR
 *   istenen model -> diger model -> bu kapsamin son iyi parametreleri
 *   -> diger modelin son iyileri -> tohum varsayilanlari
 *
 * Her basamak cagirana ADIYLA bildiriliyor; arayuz hangi basamakta
 * oldugunu yaziyor. Sessizce varsayilana dusmek, kullaniciya kalibre
 * edilmis bir model gosterdigini sanmasina yol acardi.
 *
 * Hem Node'dan require edilir (testler) hem tarayiciya servis edilir.
 */

(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root._modelFallback = api.secParametreler;
    root._modelVeriYasi = api.veriYasi;
    root._modelKabulEdilir = api.kabulEdilir;
    root._MODEL_FALLBACK = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Kabul edilebilir azami IV RMSE'si (ondalik; 0.05 = 5 volatilite puani).
  // Bunun ustundeki bir uyum teknik olarak "basarili" olsa da piyasayi
  // temsil etmiyordur ve kullanilmasi yanlis fiyat uretir.
  var AZAMI_RMSE = 0.05;

  // Veri tazeligi esikleri (saniye).
  var TAZE_SN = 60;
  var BAYAT_SN = 900;             // 15 dk

  var DIGER = { heston: 'svi', svi: 'heston' };

  function sayiMi(x) {
    return typeof x === 'number' && isFinite(x);
  }

  /**
   * Bir uyum kullanilabilir mi?
   * Reddetme sebebi acikca donuyor ki arayuz "neden varsayilana dustuk"
   * sorusunu cevaplayabilsin.
   */
  // NOT: sebep ve aciklama metinleri ARAYUZDE gorunur, bu yuzden
  // Ingilizce. Kod ici adlandirma Turkce kaliyor (dosyanin geri kalaniyla
  // tutarli olsun diye), ama kullaniciya giden her sey Ingilizce.
  function kabulEdilir(fit, azamiRmse) {
    var esik = sayiMi(azamiRmse) ? azamiRmse : AZAMI_RMSE;
    if (!fit || !fit.params) return { ok: false, sebep: 'no fit' };
    var p = fit.params;
    for (var k in p) {
      if (Object.prototype.hasOwnProperty.call(p, k) && !sayiMi(p[k])) {
        return { ok: false, sebep: 'non-numeric parameter: ' + k };
      }
    }
    if (!sayiMi(fit.rmseIv)) return { ok: false, sebep: 'RMSE unavailable' };
    if (fit.rmseIv > esik) {
      return {
        ok: false,
        sebep: 'RMSE ' + (fit.rmseIv * 100).toFixed(2) + ' pts exceeds '
             + (esik * 100).toFixed(2) + ' pts limit',
      };
    }
    return { ok: true };
  }

  /**
   * Fallback zincirini yurutur.
   *
   * girdi:
   *   istenen    'heston' | 'svi'
   *   uyumlar    { heston: {params,rmseIv}|null, svi: {...}|null }
   *   sonIyi     { heston: {params,rmse,ts}|null, svi: {...}|null }  (opsiyonel)
   *   varsayilan { heston: {...}, svi: {...} }
   *   azamiRmse  esik (opsiyonel)
   *
   * doner:
   *   { params, model, kaynak, rmse, guvenilir, aciklama, redSebepleri }
   *
   *   kaynak: 'kalibre' | 'alternatif' | 'onceki' | 'onceki-alternatif'
   *           | 'varsayilan'
   *   guvenilir: yalnizca 'kalibre' ve 'alternatif' icin true
   */
  function secParametreler(girdi) {
    girdi = girdi || {};
    var istenen = girdi.istenen === 'svi' ? 'svi' : 'heston';
    var uyumlar = girdi.uyumlar || {};
    var sonIyi = girdi.sonIyi || {};
    var varsayilan = girdi.varsayilan || {};
    var esik = girdi.azamiRmse;
    var diger = DIGER[istenen];
    var red = {};

    // 1) Istenen modelin taze uyumu
    var k = kabulEdilir(uyumlar[istenen], esik);
    if (k.ok) {
      return {
        params: uyumlar[istenen].params, model: istenen, kaynak: 'kalibre',
        rmse: uyumlar[istenen].rmseIv, guvenilir: true, redSebepleri: red,
        aciklama: 'calibrated',
      };
    }
    red[istenen] = k.sebep;

    // 2) Alternatif model — dokumanin istedigi "alternatif model" basamagi.
    //    Heston yakinsamazsa SVI genelde yakinsar; ikisi de ayni piyasa
    //    IV'lerine uyduruldugu icin cikti karsilastirilabilir kalir.
    var k2 = kabulEdilir(uyumlar[diger], esik);
    if (k2.ok) {
      return {
        params: uyumlar[diger].params, model: diger, kaynak: 'alternatif',
        rmse: uyumlar[diger].rmseIv, guvenilir: true, redSebepleri: red,
        aciklama: istenen + ' did not converge, used ' + diger,
      };
    }
    if (k2.sebep) red[diger] = k2.sebep;

    // 3) Bu kapsamin son iyi parametreleri — "onceki parametre setine donus".
    if (sonIyi[istenen] && sonIyi[istenen].params) {
      return {
        params: sonIyi[istenen].params, model: istenen, kaynak: 'onceki',
        rmse: sonIyi[istenen].rmse, guvenilir: false, redSebepleri: red,
        ts: sonIyi[istenen].ts,
        aciklama: 'using last good calibration',
      };
    }

    // 4) Alternatif modelin son iyileri
    if (sonIyi[diger] && sonIyi[diger].params) {
      return {
        params: sonIyi[diger].params, model: diger, kaynak: 'onceki-alternatif',
        rmse: sonIyi[diger].rmse, guvenilir: false, redSebepleri: red,
        ts: sonIyi[diger].ts,
        aciklama: 'using last good ' + diger + ' calibration',
      };
    }

    // 5) Tohum varsayilanlari — hicbir sey kalmadiginda. Bu bir kalibrasyon
    //    DEGILDIR ve oyle sunulmamali.
    return {
      params: varsayilan[istenen] || null, model: istenen, kaynak: 'varsayilan',
      rmse: null, guvenilir: false, redSebepleri: red,
      aciklama: 'not calibrated, seed parameters',
    };
  }

  /**
   * Veri yasi — "veri gecikmelerinde kontrollu degrade".
   *
   * Parametreler iyi olsa bile dayandiklari fiyatlar saatlerceyse ekranda
   * taze gibi durmamali. Veriyi gizlemek yerine yasini soyluyoruz:
   * seans disinda son kapanisi gormek mesru, onu canli sanmak degil.
   */
  function veriYasi(ts, simdi, esikler) {
    esikler = esikler || {};
    var taze = sayiMi(esikler.taze) ? esikler.taze : TAZE_SN;
    var bayat = sayiMi(esikler.bayat) ? esikler.bayat : BAYAT_SN;

    var t = (ts instanceof Date) ? ts.getTime() : Date.parse(ts);
    if (!isFinite(t)) {
      return { saniye: null, durum: 'bilinmiyor', metin: 'data age unknown' };
    }
    var simdiMs = (simdi instanceof Date) ? simdi.getTime()
                : (sayiMi(simdi) ? simdi : Date.now());
    var sn = Math.max(0, Math.round((simdiMs - t) / 1000));

    var durum = sn <= taze ? 'taze' : (sn <= bayat ? 'bayat' : 'cok-bayat');
    return { saniye: sn, durum: durum, metin: yasMetni(sn), ts: ts };
  }

  function yasMetni(sn) {
    if (sn < 60) return sn + 's old';
    if (sn < 3600) return Math.floor(sn / 60) + 'm old';
    if (sn < 86400) return Math.floor(sn / 3600) + 'h old';
    return Math.floor(sn / 86400) + 'd old';
  }

  return {
    secParametreler: secParametreler,
    kabulEdilir: kabulEdilir,
    veriYasi: veriYasi,
    AZAMI_RMSE: AZAMI_RMSE,
    TAZE_SN: TAZE_SN,
    BAYAT_SN: BAYAT_SN,
  };
}));
