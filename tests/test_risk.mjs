/**
 * Monte Carlo VaR cekirdeginin testleri.
 *
 *   node --test tests/
 *
 * Sunucu dosyasindaki risk kodu /risk-handler.js olarak servis edilir;
 * test onu gercek sunucudan cekip degerlendirir. Boylece test, tarayiciya
 * giden kodun ta kendisini sinar.
 *
 * Node 18+ gerekir, ek bagimlilik yoktur.
 */

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const KOK = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 8771;

let sunucu;
let kapsam = {};   // risk-handler.js'in calistirildigi sahte "window"

before(async () => {
  sunucu = spawn('node', ['_frontend_runtime.js'], {
    cwd: KOK,
    env: { ...process.env, PORT: String(PORT), DATA_MODE: '0' },
    stdio: 'ignore',
  });

  // sunucunun ayaga kalkmasini bekle
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/health`);
      if (r.ok || r.status === 404) break;
    } catch { /* henuz hazir degil */ }
    await new Promise((r) => setTimeout(r, 250));
  }

  const js = await (await fetch(`http://127.0.0.1:${PORT}/risk-handler.js`)).text();

  // Tarayici kodunu calistirmak icin asgari ortam: DOM'a dokunmayan
  // _riskSimulate ve yardimcilari icin bu kadari yeterli.
  const sahteBelge = { addEventListener() {}, getElementById: () => null };
  kapsam = { window: {}, document: sahteBelge, Math, Number, String, Array, isNaN, parseFloat, parseInt };
  kapsam.window = kapsam;
  const fn = new Function('window', 'document', 'alert', 'fetch', js + '\nreturn window;');
  kapsam = fn(kapsam.window, sahteBelge, () => {}, () => {});

  assert.equal(typeof kapsam._riskSimulate, 'function',
    '_riskSimulate servis edilen koda dahil degil');
});

after(() => { if (sunucu) sunucu.kill(); });

// --- yardimcilar -----------------------------------------------------------

const PORTFOY = [
  { underlying: 'THYAO', posType: 'call', strike: 280, spot: 281.5, qty: 100, dtm: 30 },
  { underlying: 'THYAO', posType: 'put', strike: 275, spot: 281.5, qty: -50, dtm: 30 },
  { underlying: 'GARAN', posType: 'call', strike: 130, spot: 126.9, qty: 200, dtm: 30 },
  { underlying: 'AKBNK', posType: 'put', strike: 66, spot: 68.9, qty: -100, dtm: 30 },
];

const VARSAYILAN = {
  nSims: 20000, dt: 1 / 365, mult: 100,
  volMap: { THYAO: 0.35, GARAN: 0.43, AKBNK: 0.47 },
  rateMap: { THYAO: 0.30, GARAN: 0.30, AKBNK: 0.30 },
};

function varDegeri(pnls, conf = 0.99) {
  const s = [...pnls].sort((a, b) => a - b);
  return s[Math.floor((1 - conf) * s.length)];
}

// Tekrarlanabilir normal uretec (Box-Muller, sabit tohumlu LCG)
function tohumluRnd(tohum = 12345) {
  let x = tohum;
  const u = () => ((x = (1103515245 * x + 12345) % 2147483648) / 2147483648) || 1e-9;
  return () => Math.sqrt(-2 * Math.log(u())) * Math.cos(2 * Math.PI * u());
}

// --- testler ---------------------------------------------------------------

test('ayni dayanaktaki pozisyonlar tek senaryo fiyatini paylasir', () => {
  // THYAO'da iki pozisyon var ama stMap'te tek THYAO girdisi olmali.
  // Sok pozisyon basina uretilseydi ayni hisse iki farkli fiyat gorurdu.
  const s = kapsam._riskSimulate(PORTFOY, { ...VARSAYILAN, nSims: 500, rho: 0.5 });
  assert.equal(Object.keys(s.stMap).length, 3,
    'dayanak sayisi 3 olmali (THYAO, GARAN, AKBNK)');
  assert.ok(s.stMap.THYAO, 'THYAO senaryosu yok');
});

test('korelasyon arttikca risk artar', () => {
  const sonuc = [0, 0.3, 0.6, 0.95].map((rho) => {
    const s = kapsam._riskSimulate(PORTFOY, { ...VARSAYILAN, rho, rnd: tohumluRnd(7) });
    return { rho, var: varDegeri(s.pnls) };
  });
  for (let i = 1; i < sonuc.length; i++) {
    assert.ok(sonuc[i].var < sonuc[i - 1].var,
      `rho=${sonuc[i].rho} icin VaR daha kotu olmaliydi: ` +
      `${sonuc[i].var.toFixed(0)} vs ${sonuc[i - 1].var.toFixed(0)}`);
  }
});

test('rho=1 ile tam korelasyon: cesitlendirme faydasi kalmaz', () => {
  const bagimsiz = kapsam._riskSimulate(PORTFOY, { ...VARSAYILAN, rho: 0, rnd: tohumluRnd(3) });
  const tamKorele = kapsam._riskSimulate(PORTFOY, { ...VARSAYILAN, rho: 0.99, rnd: tohumluRnd(3) });
  const oran = varDegeri(tamKorele.pnls) / varDegeri(bagimsiz.pnls);
  assert.ok(oran > 1.2,
    `tam korelasyonda VaR belirgin buyumeli, oran=${oran.toFixed(2)}`);
});

test('bos portfoy cokmeden sonuc doner', () => {
  const s = kapsam._riskSimulate([], { ...VARSAYILAN, nSims: 100, rho: 0.5 });
  assert.equal(s.curVal, 0);
  assert.equal(s.pnls.length, 100);
  assert.ok(s.pnls.every((x) => x === 0));
});

test('pozisyon yonu K/Z isaretini ters cevirir', () => {
  const uzun = [{ underlying: 'X', posType: 'call', strike: 100, spot: 100, qty: 100, dtm: 30 }];
  const kisa = [{ ...uzun[0], qty: -100 }];
  const opt = { ...VARSAYILAN, nSims: 4000, volMap: { X: 0.4 }, rateMap: { X: 0.3 }, rho: 0 };
  const a = kapsam._riskSimulate(uzun, { ...opt, rnd: tohumluRnd(11) });
  const b = kapsam._riskSimulate(kisa, { ...opt, rnd: tohumluRnd(11) });
  const ortA = a.pnls.reduce((x, y) => x + y, 0) / a.pnls.length;
  const ortB = b.pnls.reduce((x, y) => x + y, 0) / b.pnls.length;
  assert.ok(Math.abs(ortA + ortB) < Math.abs(ortA) * 1e-6 + 1e-6,
    'uzun ve kisa pozisyonun K/Z toplami sifir olmali');
});

test('simulasyon sayisi cikti uzunlugunu belirler', () => {
  for (const n of [100, 1000, 5000]) {
    const s = kapsam._riskSimulate(PORTFOY, { ...VARSAYILAN, nSims: n, rho: 0.5 });
    assert.equal(s.pnls.length, n);
  }
});

// --- stres testi -----------------------------------------------------------

test('stres testi her senaryo icin satir uretir', () => {
  const s = kapsam._riskStresTest(PORTFOY, { ...VARSAYILAN, nSims: 500, rho: 0.5, conf: 0.99 });
  assert.equal(s.length, kapsam.RISK_SENARYOLAR.length);
  for (const r of s) {
    for (const alan of ['ad', 'portfoyDegeri', 'anlikEtki', 'var']) {
      assert.ok(alan in r, `${alan} alani yok`);
    }
  }
});

test('baz senaryonun anlik etkisi sifirdir', () => {
  const s = kapsam._riskStresTest(PORTFOY, { ...VARSAYILAN, nSims: 300, rho: 0.5, conf: 0.99 });
  assert.equal(s[0].ad, 'Baz senaryo');
  assert.ok(Math.abs(s[0].anlikEtki) < 1e-6,
    `baz senaryoda sok olmamali, etki=${s[0].anlikEtki}`);
});

test('spot soku portfoy degerini delta yonunde degistirir', () => {
  const s = kapsam._riskStresTest(PORTFOY, { ...VARSAYILAN, nSims: 300, rho: 0.5, conf: 0.99 });
  const dus10 = s.find((x) => x.ad === 'Spot -%10');
  const dus20 = s.find((x) => x.ad === 'Spot -%20');
  const yuk10 = s.find((x) => x.ad === 'Spot +%10');
  // Portfoy net long delta: spot duserse deger azalir, yukselirse artar
  assert.ok(dus10.anlikEtki < 0, 'spot dustugunde deger artmis');
  assert.ok(yuk10.anlikEtki > 0, 'spot yukseldiginde deger azalmis');
  assert.ok(dus20.anlikEtki < dus10.anlikEtki,
    '-%20 soku -%10 dan daha agir olmali');
});

test('volatilite soku portfoy degerini degistirir', () => {
  const s = kapsam._riskStresTest(PORTFOY, { ...VARSAYILAN, nSims: 300, rho: 0.5, conf: 0.99 });
  const v15 = s.find((x) => x.ad === 'Volatilite +%50');
  const v2 = s.find((x) => x.ad === 'Volatilite x2');
  assert.ok(Math.abs(v15.anlikEtki) > 1e-6, 'vol soku degeri hic etkilememis');
  assert.ok(Math.abs(v2.anlikEtki) > Math.abs(v15.anlikEtki),
    'vol x2 etkisi +%50 den buyuk olmali');
});

test('kriz senaryosu en kotu VaR i uretir', () => {
  const s = kapsam._riskStresTest(PORTFOY, { ...VARSAYILAN, nSims: 3000, rho: 0.5, conf: 0.99 });
  const kriz = s.find((x) => x.ad.startsWith('Kriz'));
  const baz = s[0];
  assert.ok(kriz.var < baz.var,
    `kriz VaR'i baz senaryodan kotu olmali: ${kriz.var.toFixed(0)} vs ${baz.var.toFixed(0)}`);
  assert.equal(kriz.rho, 0.95, 'kriz senaryosu korelasyonu yukseltmeli');
});

test('senaryolar girdi portfoyunu degistirmez', () => {
  const kopya = JSON.parse(JSON.stringify(PORTFOY));
  kapsam._riskStresTest(PORTFOY, { ...VARSAYILAN, nSims: 200, rho: 0.5, conf: 0.99 });
  assert.deepEqual(PORTFOY, kopya, 'stres testi portfoyu yerinde degistirmis');
});

test('portfoy degeri pozisyon buyuklugu ile dogrusal olcekler', () => {
  const tek = kapsam._riskSimulate(PORTFOY, { ...VARSAYILAN, nSims: 10, rho: 0.5 });
  const cift = kapsam._riskSimulate(
    PORTFOY.map((p) => ({ ...p, qty: p.qty * 2 })),
    { ...VARSAYILAN, nSims: 10, rho: 0.5 });
  assert.ok(Math.abs(cift.curVal - tek.curVal * 2) < Math.abs(tek.curVal) * 1e-9 + 1e-9);
});
