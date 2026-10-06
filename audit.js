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

const GENESIS_HASH = '0'.repeat(64);   // zincirin kökü

/**
 * Bir kaydın özetini hesaplar.
 *
 * Alanlar sabit bir sırayla birleştirilir: JSON.stringify anahtar sırasını
 * koruduğu için aynı data her zaman aynı özeti üretir, ama sıraya güvenmek
 * kırılgan olurdu — bu yüzden alanlar tek tek ve açıkça yazılır.
 */
function computeHash(record) {
  const input = [
    String(record.seq),
    record.ts,
    record.type,
    JSON.stringify(record.data === undefined ? null : record.data),
    record.prevHash,
  ].join('\u0000');
  return crypto.createHash('sha256').update(input, 'utf8').digest('hex');
}

class AuditTrail {
  constructor(filePath) {
    this.file = filePath;
    this.records = [];
    this._load();
  }

  _load() {
    if (!fs.existsSync(this.file)) return;
    const lines = fs.readFileSync(this.file, 'utf8').split('\n');
    for (const s of lines) {
      const t = s.trim();
      if (!t) continue;
      try {
        this.records.push(JSON.parse(t));
      } catch {
        // Bozuk satır atlanır; verify() bunu zincir kopukluğu olarak bildirir.
      }
    }
  }

  get lastHash() {
    return this.records.length
      ? this.records[this.records.length - 1].hash
      : GENESIS_HASH;
  }

  /** Zincire yeni kayıt ekler ve eklenen kaydı döndürür. */
  append(type, data) {
    const record = {
      seq: this.records.length + 1,
      ts: new Date().toISOString(),
      type: String(type),
      data: data === undefined ? null : data,
      prevHash: this.lastHash,
    };
    record.hash = computeHash(record);
    this.records.push(record);
    try {
      fs.appendFileSync(this.file, JSON.stringify(record) + '\n', 'utf8');
    } catch (e) {
      // Diske yazılamazsa bellekteki zincir ile file ayrışır; bunu
      // sessizce yutmak denetim izinin amacına aykırı olurdu.
      record.diskError = String(e && e.message);
    }
    return record;
  }

  /**
   * Zinciri baştan sona doğrular.
   * Döndürür: { valid, recordCount, error? }
   */
  verify() {
    let expectedPrevHash = GENESIS_HASH;
    for (let i = 0; i < this.records.length; i++) {
      const k = this.records[i];

      if (k.seq !== i + 1) {
        return { valid: false, recordCount: this.records.length,
                 error: `sequence gap: expected ${i + 1}, found ${k.seq}` };
      }
      if (k.prevHash !== expectedPrevHash) {
        return { valid: false, recordCount: this.records.length,
                 error: `broken chain: record ${k.seq} does not link to the previous hash` };
      }
      if (computeHash(k) !== k.hash) {
        return { valid: false, recordCount: this.records.length,
                 error: `record ${k.seq} tampered: hash mismatch` };
      }
      expectedPrevHash = k.hash;
    }
    return { valid: true, recordCount: this.records.length };
  }

  /** Son n kaydı döndürür (varsayılan 50). */
  recent(n = 50) {
    return this.records.slice(-n);
  }
}

module.exports = { AuditTrail, computeHash, GENESIS_HASH };
