/**
 * health.js — servis sagligi degerlendirmesi.
 *
 * NEDEN BU DOSYA VAR
 * /health su ana kadar sabit bir {"ok":true} donuyordu: hicbir
 * bagimliligi kontrol etmiyordu. Sunucu ayakta ama veri hic akmiyorken
 * de "saglikli" diyordu — izleme acisindan bu YANLIS BIR SINYAL, cunku
 * tam da anlamasi gereken durumu kaciriyor.
 *
 * UC DURUM, IKI ESIK
 *   healthy    tum kontroller taze
 *   degraded   bir sey bayat ama sistem calisiyor (seans disi, yavas akis)
 *   unhealthy  calismayi engelleyen bir sey var (depo yazamiyor, hic veri yok)
 *
 * "degraded" ile "unhealthy" ayrimi onemli: seans disinda veri akmamasi
 * normaldir ve konteyneri yeniden baslatmayi gerektirmez. Bu yuzden
 * HTTP durumu 200 kalir, karar veren tarafa `status` alani birakilir.
 *
 * Hem Node'dan require edilir (sunucu + testler) hem tarayiciya servis
 * edilir (panel ayni esikleri kullansin diye).
 */

(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root._saglikDegerlendir = api.degerlendir;
    root._SAGLIK = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Esikler (saniye). Ortam degiskeniyle ezilebilir.
  //
  // IKI AYRI OLCU KULLANILIYOR, cunku kaynaklarin DOGAL TEMPOSU farkli:
  //   * Veri akisi saniyede bir yaziyor      -> TAZE_SN ile yargilanir
  //   * Depo raporu 60 sn'de bir gonderiliyor
  //     egri 300 sn'de bir uyduruluyor       -> BAYAT_SN ile yargilanir
  //
  // Periyodik isleri akis esigiyle olcmek, 5 dakikada bir calisan bir
  // isi her seferinde "bayat" gostermek olurdu.
  var TAZE_SN = 60;        // akis icin: bunun altinda taze
  var BAYAT_SN = 900;      // periyodik isler icin: bunun ustunde bayat

  function yasSn(ts, simdi) {
    if (!ts) return null;
    var t = Date.parse(ts);
    if (!isFinite(t)) return null;
    return Math.max(0, Math.round(((simdi || Date.now()) - t) / 1000));
  }

  function yasMetni(sn) {
    if (sn === null) return 'never';
    if (sn < 60) return sn + 's';
    if (sn < 3600) return Math.floor(sn / 60) + 'm';
    if (sn < 86400) return Math.floor(sn / 3600) + 'h';
    return Math.floor(sn / 86400) + 'd';
  }

  // Durumlarin siddet sirasi; genel durum en kotusu olur.
  var SIDDET = { healthy: 0, degraded: 1, unhealthy: 2 };

  function kotuOlan(a, b) {
    return SIDDET[b] > SIDDET[a] ? b : a;
  }

  /**
   * girdi:
   *   lastPostAt   {spot, futures, options, other} — ISO ya da null
   *   storeStats   store.py'nin gonderdigi ozet (ya da null)
   *   sayimlar     {spot, options, other} — kac dayanak var
   *   modelTs      son basarili kalibrasyon zamani (ya da null)
   *   egriTs       son faiz egrisi uydurma zamani (ya da null)
   *   mockMu       veri modu
   *   esikler      {taze, bayat}
   *   simdi        ms (test icin)
   *
   * doner: {status, checks:[{name,status,detail,age_s}], ts}
   */
  function degerlendir(girdi) {
    girdi = girdi || {};
    var esikler = girdi.esikler || {};
    var taze = typeof esikler.taze === 'number' ? esikler.taze : TAZE_SN;
    var bayat = typeof esikler.bayat === 'number' ? esikler.bayat : BAYAT_SN;
    var simdi = girdi.simdi || Date.now();
    var son = girdi.lastPostAt || {};
    var sayimlar = girdi.sayimlar || {};
    var checks = [];

    // --- Veri akisi: kaynak basina ---
    // "Hic veri gelmedi" ile "bayatladi" ayri seyler. Birincisi
    // kurulumun calismadigini, ikincisi akisin durdugunu gosterir.
    [['spot', 'Spot feed', sayimlar.spot],
     ['futures', 'Futures feed', sayimlar.futures],
     ['options', 'Option chains', sayimlar.options],
     ['other', 'Index/FX/commodity', sayimlar.other]].forEach(function (x) {
      var anahtar = x[0], ad = x[1], adet = x[2];
      var sn = yasSn(son[anahtar], simdi);
      var durum, detay;
      if (sn === null) {
        durum = 'unhealthy';
        detay = 'no data received since startup';
      } else if (sn <= taze) {
        durum = 'healthy';
        detay = yasMetni(sn) + ' ago' + (adet !== undefined ? ' · ' + adet + ' item(s)' : '');
      } else if (sn <= bayat) {
        durum = 'degraded';
        detay = 'stale — last update ' + yasMetni(sn) + ' ago';
      } else {
        durum = 'degraded';
        detay = 'very stale — last update ' + yasMetni(sn) + ' ago';
      }
      checks.push({ name: ad, status: durum, detail: detay, age_s: sn });
    });

    // --- Depo ---
    // Yazma hatasi gercekten engelleyici: gecmis birikmiyor demektir.
    var st = girdi.storeStats;
    if (!st) {
      checks.push({
        name: 'Store', status: 'degraded', age_s: null,
        detail: 'no report yet — run: python3 store.py --push-stats',
      });
    } else if (st.write_error) {
      checks.push({
        name: 'Store', status: 'unhealthy', age_s: yasSn(st.ts, simdi),
        detail: 'WRITE ERROR: ' + st.write_error,
      });
    } else {
      var raporYas = yasSn(st.received_at || st.ts, simdi);
      checks.push({
        name: 'Store', status: raporYas !== null && raporYas > bayat ? 'degraded' : 'healthy',
        age_s: raporYas,
        detail: (st.spot_days || 0) + ' daily bar(s), '
              + (st.model_versions || 0) + ' model version(s) · report '
              + yasMetni(raporYas) + ' old',
      });
    }

    // --- Model kalibrasyonu ---
    // Bayat kalibrasyon sistemi durdurmaz ama fiyatlar eski
    // parametrelerle uretiliyor demektir; gorulebilir olmali.
    var mSn = yasSn(girdi.modelTs, simdi);
    checks.push({
      name: 'Volatility calibration', age_s: mSn,
      status: mSn === null ? 'degraded' : (mSn > bayat ? 'degraded' : 'healthy'),
      detail: mSn === null ? 'never calibrated in this session'
                           : 'last good fit ' + yasMetni(mSn) + ' ago',
    });

    var eSn = yasSn(girdi.egriTs, simdi);
    checks.push({
      name: 'Yield curve', age_s: eSn,
      status: eSn === null ? 'degraded' : (eSn > bayat ? 'degraded' : 'healthy'),
      detail: eSn === null ? 'not fitted yet — run: python3 fit_curve.py'
                           : 'fitted ' + yasMetni(eSn) + ' ago',
    });

    var genel = checks.reduce(function (acc, c) { return kotuOlan(acc, c.status); }, 'healthy');

    return {
      status: genel,
      mode: girdi.mockMu ? 'MOCK' : 'LIVE',
      thresholds: { fresh_s: taze, stale_s: bayat },
      checks: checks,
      ts: new Date(simdi).toISOString(),
    };
  }

  return {
    degerlendir: degerlendir,
    yasMetni: yasMetni,
    TAZE_SN: TAZE_SN,
    BAYAT_SN: BAYAT_SN,
  };
}));
