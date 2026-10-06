/**
 * Değiştirilemez denetim izi.
 *
 * Her kayıt bir öncekinin özetini (hash) içerir. Zincirlendikleri için
 * geçmişteki bir kaydı değiştirmek ondan sonraki tüm özetleri bozar ve
 * doğrulama sırasında yakalanır. Kayıtların silinmesini engellemez —
 * engellediği, fark edilmeden değiştirilmesidir.
 *
 * Depolama JSONL: her satır bir kayıt, yalnızca sona eklenir.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const BASLANGIC_OZET = '0'.repeat(64);   // zincirin kökü

/**
 * Bir kaydın özetini hesaplar.
 *
 * Alanlar sabit bir sırayla birleştirilir: JSON.stringify anahtar sırasını
 * koruduğu için aynı veri her zaman aynı özeti üretir, ama sıraya güvenmek
 * kırılgan olurdu — bu yüzden alanlar tek tek ve açıkça yazılır.
 */
function ozetHesapla(kayit) {
  const girdi = [
    String(kayit.seq),
    kayit.ts,
    kayit.tip,
    JSON.stringify(kayit.veri === undefined ? null : kayit.veri),
    kayit.oncekiOzet,
  ].join('\u0000');
  return crypto.createHash('sha256').update(girdi, 'utf8').digest('hex');
}

class DenetimIzi {
  constructor(dosyaYolu) {
    this.dosya = dosyaYolu;
    this.kayitlar = [];
    this._yukle();
  }

  _yukle() {
    if (!fs.existsSync(this.dosya)) return;
    const satirlar = fs.readFileSync(this.dosya, 'utf8').split('\n');
    for (const s of satirlar) {
      const t = s.trim();
      if (!t) continue;
      try {
        this.kayitlar.push(JSON.parse(t));
      } catch {
        // Bozuk satır atlanır; dogrula() bunu zincir kopukluğu olarak bildirir.
      }
    }
  }

  get sonOzet() {
    return this.kayitlar.length
      ? this.kayitlar[this.kayitlar.length - 1].ozet
      : BASLANGIC_OZET;
  }

  /** Zincire yeni kayıt ekler ve eklenen kaydı döndürür. */
  ekle(tip, veri) {
    const kayit = {
      seq: this.kayitlar.length + 1,
      ts: new Date().toISOString(),
      tip: String(tip),
      veri: veri === undefined ? null : veri,
      oncekiOzet: this.sonOzet,
    };
    kayit.ozet = ozetHesapla(kayit);
    this.kayitlar.push(kayit);
    try {
      fs.appendFileSync(this.dosya, JSON.stringify(kayit) + '\n', 'utf8');
    } catch (e) {
      // Diske yazılamazsa bellekteki zincir ile dosya ayrışır; bunu
      // sessizce yutmak denetim izinin amacına aykırı olurdu.
      kayit.diskHatasi = String(e && e.message);
    }
    return kayit;
  }

  /**
   * Zinciri baştan sona doğrular.
   * Döndürür: { gecerli, kayitSayisi, hata? }
   */
  dogrula() {
    let beklenenOncekiOzet = BASLANGIC_OZET;
    for (let i = 0; i < this.kayitlar.length; i++) {
      const k = this.kayitlar[i];

      if (k.seq !== i + 1) {
        return { gecerli: false, kayitSayisi: this.kayitlar.length,
                 hata: `sira numarasi atlamis: ${i + 1} beklenirken ${k.seq}` };
      }
      if (k.oncekiOzet !== beklenenOncekiOzet) {
        return { gecerli: false, kayitSayisi: this.kayitlar.length,
                 hata: `zincir kopuk: kayit ${k.seq} onceki ozete baglanmiyor` };
      }
      if (ozetHesapla(k) !== k.ozet) {
        return { gecerli: false, kayitSayisi: this.kayitlar.length,
                 hata: `kayit ${k.seq} degistirilmis: ozet tutmuyor` };
      }
      beklenenOncekiOzet = k.ozet;
    }
    return { gecerli: true, kayitSayisi: this.kayitlar.length };
  }

  /** Son n kaydı döndürür (varsayılan 50). */
  son(n = 50) {
    return this.kayitlar.slice(-n);
  }
}

module.exports = { DenetimIzi, ozetHesapla, BASLANGIC_OZET };
