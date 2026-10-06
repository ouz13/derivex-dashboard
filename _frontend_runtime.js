
const http = require('http');
const fs = require('fs');
const path = require('path');
const { AuditTrail } = require('./audit.js');

// .env dosyasini ortama yukler (mevcut degerleri ezmez).
// Kimlik bilgileri kodda gomulu tutulmaz.
(function loadEnv() {
  const envPath = path.join(__dirname, '.env');
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    if (process.env[key] !== undefined) continue;
    process.env[key] = trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
  }
})();

const tickers = [
  'AEFES','AKBNK','AKSEN','ALARK','ARCLK','ASELS','ASTOR','BIMAS','BRSAN','CIMSA',
  'DOAS','DOHOL','EKGYO','ENKAI','ENJSA','EREGL','FROTO','GARAN','GUBRF','HALKB',
  'HEKTS','ISCTR','KCHOL','TRMET','TRALT','KRDMD','MGROS','ODAS','OYAKC',
  'PETKM','PGSUS','SAHOL','SASA','SISE','SOKM','TAVHL','TCELL','THYAO','TKFEN',
  'TOASO','TSKB','TTKOM','TUPRS','ULKER','VAKBN','VESTL','YKBNK'
];

const optionTickers = [
  'AKBNK','ALARK','ARCLK','ASELS','BIMAS','EKGYO','ENKAI','EREGL','FROTO','GARAN',
  'HALKB','ISCTR','KCHOL','KRDMD','PETKM','PGSUS','SAHOL','SISE','TAVHL','TCELL',
  'THYAO','TOASO','TTKOM','TUPRS','VAKBN','YKBNK'
];

const realizedVolTickers = [
  'AEFES', 'AKBNK', 'ASELS', 'ASTOR', 'BIMAS',
  'EKGYO', 'ENKAI', 'EREGL', 'FROTO',
  'GARAN', 'GUBRF', 'ISCTR', 'KCHOL',
  'KRDMD', 'MGROS', 'PETKM',
  'PGSUS', 'SAHOL', 'SASA',
  'SISE', 'TAVHL', 'TCELL', 'THYAO', 'TOASO',
  'TRALT', 'TTKOM', 'TUPRS', 'ULKER', 'VAKBN',
  'YKBNK'
];

const realizedVolWindows = [15, 30, 60, 90, 180];
const realizedVolApiKey = process.env.IDEALDATA_API_KEY || 'b606d2c2-379d-46b5-8561-139039f0bd74+5vkySYZIcPTEgjvWI3wUZJcPzTsEJpYr2ncn2rCNycw4PWDRyhir8e69XPe8OM9ieyaB2DnJvSLuCZqhWww';
const realizedVolCache = { generatedAtMs: 0, payload: null, inFlight: null };
const realizedVolCacheTtlMs = 5 * 60 * 1000;

const optionColumnDefs = [
  { key: 'call_bid_size', label: 'C Bid Sz' },
  { key: 'call_bid_price', label: 'C Bid Px' },
  { key: 'call_ask_price', label: 'C Ask Px' },
  { key: 'call_ask_size', label: 'C Ask Sz' },
  { key: 'call_bid_iv', label: 'C Bid IV' },
  { key: 'call_ask_iv', label: 'C Ask IV' },
  { key: 'call_delta', label: 'C Δ' },
  { key: 'call_gamma', label: 'C Γ' },
  { key: 'call_vega', label: 'C Vega' },
  { key: 'call_theta', label: 'C Θ' },
  { key: 'call_rho', label: 'C Rho' },
  { key: 'call_dv01', label: 'C DV01' },
  { key: 'expiry', label: 'Expiry' },
  { key: 'strike', label: 'Strike' },
  { key: 'dtm', label: 'DTM' },
  { key: 'rate', label: 'Rate' },
  { key: 'spot_mid', label: 'Spot' },
  { key: 'put_dv01', label: 'P DV01' },
  { key: 'put_rho', label: 'P Rho' },
  { key: 'put_theta', label: 'P Θ' },
  { key: 'put_vega', label: 'P Vega' },
  { key: 'put_gamma', label: 'P Γ' },
  { key: 'put_delta', label: 'P Δ' },
  { key: 'put_bid_iv', label: 'P Bid IV' },
  { key: 'put_ask_iv', label: 'P Ask IV' },
  { key: 'put_bid_size', label: 'P Bid Sz' },
  { key: 'put_bid_price', label: 'P Bid Px' },
  { key: 'put_ask_price', label: 'P Ask Px' },
  { key: 'put_ask_size', label: 'P Ask Sz' }
];

// ---------------------------------------------------------------------------
// VERI KAYNAGI ANAHTARI
//   0 = MOCK  : uretilmis ornek veri (IdealData erisimi gerekmez)
//   1 = CANLI : gercek IdealData akisi ve REST ucu
//
// Mock moddayken arayuzde kirmizi bir uyari serididir gorunur. Bu kasitlidir:
// uretilmis sayilarin canli piyasa verisi sanilmasi onlenir.
// Ortam degiskeniyle de ezilebilir:  DATA_MODE=1 node _frontend_runtime.js
// ---------------------------------------------------------------------------
const DATA_MODE = process.env.DATA_MODE !== undefined ? Number(process.env.DATA_MODE) : 0;
const MOCK_MODE = DATA_MODE === 0;

// Degistirilemez denetim izi: fiyatlama ve risk koşuları zincirlenmiş
// kayıtlara yazılır, geçmişe müdahale doğrulamada yakalanır.
const auditTrail = new AuditTrail(path.join(__dirname, 'audit-log.jsonl'));

const serverState = {
  spotByTicker: {},
  futuresRatesByTicker: {},
  futuresMeta: [],
  optionsChainByTicker: {},
  dividendsByTicker: {},
  yieldCurve: null,        // NSS uydurma sonucu (fit_curve.py gonderir)
  pricerLog: [],
  // SQLite deposunun durumu ve model parametre surumleri. Depoyu Python
  // tarafi tutuyor (store.py); Node veritabanini hic acmaz, yalnizca
  // store.py'nin gonderdigi ozeti onbellekte tasir. Boylece Node 20'de
  // bulunmayan node:sqlite'a bagimlilik olusmuyor.
  storeStats: null
};

const DIVIDENDS_FILE = path.join(__dirname, 'dividends.json');
try {
  if (fs.existsSync(DIVIDENDS_FILE)) {
    serverState.dividendsByTicker = JSON.parse(fs.readFileSync(DIVIDENDS_FILE, 'utf8'));
  }
} catch (_) {}

const PRICER_LOG_FILE = path.join(__dirname, 'pricer_log.json');
try {
  if (fs.existsSync(PRICER_LOG_FILE)) {
    serverState.pricerLog = JSON.parse(fs.readFileSync(PRICER_LOG_FILE, 'utf8'));
    if (!Array.isArray(serverState.pricerLog)) serverState.pricerLog = [];
  }
} catch (_) {}

function formatSpot(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(3) : '-';
}

function formatRatePct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '-';
}

function formatTickerHref(ticker) {
  return `/market/options?ticker=${encodeURIComponent(ticker)}`;
}

function navPill(label, href, active) {
  return `<a class="pill ${active ? 'active' : ''}" href="${href}">${label}</a>`;
}

function appLayout({ mainTab, marketTab, toolsTab, contentHtml, breadcrumb }) {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Derivex Dashboard</title>
  <script src="/xlsx.js"></script>
  <script src="/risk-handler.js"></script>
  <style>
    :root {
      --bg: #f4f5f7;
      --card: #f7f8fa;
      --line: #d7dbe1;
      --text: #0f1728;
      --muted: #667085;
      --pill: #0f1728;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font-family: "Segoe UI", Arial, sans-serif; font-size: 14px; }
    .app { width: 100%; padding: 10px 16px 20px 16px; }
    .top-title { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: #0f1728; }
    .title-wrap { display: flex; align-items: center; gap: 10px; }
    .title { font-size: 28px; font-weight: 800; line-height: 1; }
    .help-btn { border: 1px solid #cfd5de; background: #f3f5f8; color: #0f1728; border-radius: 999px; padding: 6px 12px; font-size: 12px; font-weight: 900; cursor: pointer; }
    .help-btn:hover { background: #e8edf4; }
    .help-btn:focus-visible { outline: 2px solid #111827; outline-offset: 1px; }
    .crumb { margin-left: 14px; color: var(--muted); font-size: 13px; margin-bottom: 12px; }
    .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .pill { border: 1px solid #ced3dc; background: #f1f3f7; color: #101828; border-radius: 999px; padding: 8px 16px; font-weight: 800; text-decoration: none; }
    .pill.active { background: var(--pill); color: #fff; border-color: var(--pill); }
    .group { display: flex; align-items: center; gap: 8px; border: 1px solid #d5dae3; background: #f4f6f9; border-radius: 999px; padding: 4px; margin-left: 4px; flex-wrap: wrap; }
    .card { margin-top: 14px; border: 1px solid #d8dde5; border-radius: 16px; background: var(--card); padding: 14px 14px 8px 14px; width: 100%; }
    .card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }
    .section-title { font-size: 21px; font-weight: 800; margin: 0 0 4px 0; }
    .section-sub { font-size: 13px; color: var(--muted); margin: 0; }
    .filter { min-width: 300px; border: 1px solid #cfd5de; background: #f3f5f8; border-radius: 12px; padding: 9px 12px; font-size: 14px; }
    .table-wrap { width: 100%; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; margin-top: 4px; }
    th { text-align: left; color: #52607a; font-weight: 800; padding: 10px 8px; border-bottom: 1px solid #cfd5de; white-space: nowrap; }
    td { padding: 10px 8px; border-bottom: 1px solid #e1e5eb; white-space: nowrap; }
    td.ticker { font-weight: 900; color: #0f172a; }
    .dtm-chip { display: inline-block; margin-left: 6px; padding: 2px 6px; border-radius: 999px; border: 1px solid #d5dae3; background: #eef2f7; color: #46556f; font-size: 11px; font-weight: 700; }
    th.yield-subhead { text-align: center; font-size: 11px; color: #8a94a6; font-weight: 700; padding-top: 4px; padding-bottom: 8px; }
    tr.hidden { display: none; }
    .placeholder { font-size: 14px; color: #475467; padding: 2px 0 6px 0; }
    .ticker-tabs { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
    .ticker-tab { border: 1px solid #d9dee7; background: #f7f8fb; color: #0f172a; border-radius: 999px; padding: 8px 14px; font-weight: 800; font-size: 13px; text-decoration: none; }
    .ticker-tab.active { background: #0f1728; border-color: #0f1728; color: #fff; }
    .options-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
    .options-actions { display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap; margin-left: auto; }
    .action-btn { border: 1px solid #d9dee7; background: #ffffff; color: #0f172a; border-radius: 999px; padding: 8px 14px; font-size: 13px; font-weight: 800; cursor: pointer; }
    .options-table { min-width: 1500px; }
    .options-table th, .options-table td { font-size: 12px; }
    .options-table tbody tr[data-option-row="true"]:hover { background: #e8ebf1; }
    .options-empty { padding: 18px 14px; color: #667085; font-size: 14px; border-bottom: none; }
    .options-table tr.expiry-separator td { padding: 8px 0 6px 0; border-bottom: none; background: transparent; }
    .expiry-sep-wrap { display: flex; align-items: center; gap: 10px; }
    .expiry-sep-line { flex: 1; height: 4px; border-radius: 999px; background: linear-gradient(90deg, #111827 0%, #374151 50%, #9ca3af 100%); }
    .expiry-toggle-btn { display: inline-flex; align-items: center; gap: 8px; font-size: 11px; font-weight: 900; letter-spacing: 0.04em; text-transform: uppercase; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 999px; padding: 3px 10px; background: #f8fafc; cursor: pointer; }
    .expiry-toggle-btn:hover { background: #eef2f7; }
    .expiry-toggle-btn:focus-visible { outline: 2px solid #111827; outline-offset: 2px; }
    .expiry-toggle-icon { font-size: 10px; line-height: 1; min-width: 10px; text-align: center; }
    .exp-chip { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eef2f7; border: 1px solid #d6dde8; color: #1f2937; font-weight: 800; font-size: 11px; }
    .sort-header-btn { display: inline-flex; align-items: center; gap: 4px; padding: 0; border: none; background: transparent; color: inherit; font: inherit; font-weight: 800; cursor: pointer; }
    .sort-header-btn:hover { color: #0f1728; }
    .sort-header-btn.active { color: #0f1728; }
    .sort-header-btn:focus-visible { outline: 2px solid #111827; outline-offset: 2px; border-radius: 4px; }
    .sort-indicator { min-width: 10px; text-align: center; color: #64748b; }
    .sort-header-btn.active .sort-indicator { color: #0f1728; }
    .tools-note { color: #475467; font-size: 14px; margin: 0 0 10px 0; }
    .fair-rate-wrap { display: flex; align-items: center; gap: 8px; }
    .fair-rate-label { font-size: 13px; font-weight: 800; color: #475467; white-space: nowrap; }
    .maturity-head { display: flex; flex-direction: column; gap: 6px; }
    .maturity-fair-label { font-size: 10px; color: #64748b; font-weight: 800; letter-spacing: 0.02em; }
    .fair-rate-input-wrap { position: relative; display: inline-flex; align-items: center; }
    .fair-rate-input { border: 1px solid #cfd5de; background: #f3f5f8; color: #0f1728; border-radius: 10px; padding: 6px 24px 6px 10px; font-size: 12px; font-weight: 800; width: 92px; }
    .fair-rate-input:focus-visible { outline: 2px solid #111827; outline-offset: 1px; }
    .fair-rate-clear-btn {
      position: absolute;
      right: 8px;
      top: 50%;
      transform: translateY(-50%);
      width: 14px;
      height: 14px;
      border: none;
      border-radius: 999px;
      padding: 0;
      margin: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #d5dbe4;
      color: #22324a;
      font-size: 10px;
      font-weight: 900;
      line-height: 1;
      cursor: pointer;
      visibility: hidden;
    }
    .fair-rate-clear-btn:hover { background: #c3ccd8; }
    .fair-rate-clear-btn:focus-visible { outline: 2px solid #111827; outline-offset: 1px; }
    .yield-cell.rate-above { color: #16a34a; font-weight: 900; }
    .yield-cell.rate-below { color: #dc2626; font-weight: 900; }
    .yield-cell.maturity-danger { background: rgba(220, 38, 38, 0.08) !important; }
    .yield-cell.maturity-safe { background: rgba(22, 163, 74, 0.09) !important; }
    .yield-cell.div-adjusted { outline: 1.5px solid #f59e0b; outline-offset: -1px; }
    .pricer-grid { display: grid; grid-template-columns: repeat(6, minmax(160px, 1fr)); gap: 10px; }
    .pricer-grid.results { margin-top: 8px; }
    .field-label { font-size: 12px; color: #52607a; font-weight: 800; margin: 0 0 5px 0; }
    .field-label-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .manual-toggle { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: #52607a; font-weight: 800; white-space: nowrap; }
    .manual-toggle input { width: 13px; height: 13px; margin: 0; accent-color: #0f1728; }
    .field-input, .field-output { width: 100%; border: 1px solid #cfd5de; background: #f3f5f8; color: #0f1728; border-radius: 12px; padding: 10px 12px; font-size: 16px; font-weight: 800; min-height: 42px; }
    .field-input:focus-visible { outline: 2px solid #111827; outline-offset: 1px; }
    .field-output { background: #f9fafb; }
    .field-editable { background: rgba(243, 245, 248, 0.58); }
    .field-readonly { background: rgba(207, 213, 222, 0.92); color: #243046; }
    .field-output.emph { background: #e9f4ef; }
    .pricer-top { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    .toggle-btn { border: 1px solid #0f1728; background: #0f1728; color: #fff; border-radius: 14px; padding: 9px 14px; font-size: 16px; font-weight: 900; cursor: pointer; }
    .toggle-btn.off { background: #f3f5f8; color: #0f1728; }
    .pricer-log-wrap { margin-top: 12px; }
    .pricer-log-head { display: flex; justify-content: flex-start; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
    .pricer-log-right { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-left: auto; }
    .pricer-log-controls { display: inline-flex; align-items: center; gap: 8px; }
    .pricer-broker-input { border: 1px solid #cfd5de; background: #f3f5f8; color: #0f1728; border-radius: 10px; padding: 7px 10px; font-size: 13px; font-weight: 800; width: 120px; }
    .pricer-log-table { min-width: 1320px; }
    .pricer-log-table th, .pricer-log-table td { font-size: 12px; }
    .rv-top-note { margin: 0; color: #52607a; font-size: 13px; }
    .rv-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
    .rv-controls { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .rv-select { border: 1px solid #cfd5de; background: #f3f5f8; color: #0f1728; border-radius: 10px; padding: 7px 10px; font-size: 12px; font-weight: 800; min-width: 120px; }
    .rv-updated { color: #52607a; font-size: 12px; font-weight: 700; }
    .rv-status { color: #52607a; font-size: 12px; margin-bottom: 8px; }
    .rv-table { min-width: 720px; }
    .rv-table th, .rv-table td { font-size: 12px; }
    .rv-table td:first-child, .rv-table th:first-child { position: sticky; left: 0; background: #f7f8fa; z-index: 2; }
    .rv-table th:first-child { z-index: 3; }
    .rv-forecasts-hidden .rv-fcst-col { display: none; }
    .rv-ticker { font-weight: 900; color: #0f172a; }
    .rv-value { display: inline-flex; align-items: center; justify-content: center; min-width: 64px; padding: 4px 8px; border-radius: 999px; border: 1px solid #d6dde8; font-weight: 900; }
    .rv-empty { color: #98a2b3; font-weight: 800; }
    .rv-table tr:hover td { background: #edf1f7; }
    .tool-tabs { display: flex; gap: 8px; border: 1px solid #d5dae3; background: #f4f6f9; border-radius: 999px; padding: 4px; }
    .vc-status { color: #52607a; font-size: 12px; margin-bottom: 8px; }
    .vc-chart-wrap { border: 1px solid #d5dae3; border-radius: 12px; background: #fff; padding: 10px; margin-bottom: 10px; overflow-x: auto; }
    .vc-chart { width: 100%; min-width: 640px; height: 280px; display: block; }
    .vc-legend { display: flex; gap: 14px; align-items: center; margin-bottom: 8px; font-size: 12px; color: #475467; flex-wrap: wrap; }
    .vc-dot { width: 10px; height: 10px; border-radius: 999px; display: inline-block; margin-right: 5px; }
    .vc-dot.market { background: #0f1728; }
    .vc-dot.heston { background: #dc2626; }
    .vc-table { min-width: 980px; }
    .vc-table th, .vc-table td { font-size: 12px; }
    .help-modal-overlay { position: fixed; inset: 0; background: rgba(15, 23, 40, 0.55); display: none; align-items: center; justify-content: center; z-index: 9999; padding: 18px; }
    .help-modal-overlay.open { display: flex; }
    .help-modal-card { position: relative; background: #fff; border: 1px solid #d8dde5; border-radius: 14px; padding: 10px; box-shadow: 0 20px 40px rgba(10, 20, 40, 0.28); max-width: min(92vw, 760px); max-height: 90vh; overflow: auto; }
    .help-modal-image { display: block; width: 100%; height: auto; border-radius: 8px; }
    .help-modal-close { position: absolute; top: 8px; right: 8px; border: 1px solid #cfd5de; background: #f3f5f8; color: #0f1728; border-radius: 999px; width: 26px; height: 26px; font-size: 14px; font-weight: 900; cursor: pointer; }
    .help-modal-close:hover { background: #e8edf4; }
    .pricer-section { margin-top: 16px; }
    .pricer-section-label { font-size: 10px; font-weight: 900; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 8px; padding-bottom: 5px; border-bottom: 1px solid #e2e8f0; }
    .pricer-section .pricer-grid { margin-top: 0; }
    .prc-d1d2-row { display: grid; grid-template-columns: repeat(6, minmax(160px, 1fr)); gap: 10px; margin-top: 8px; }
    .prc-results-section { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 14px; padding: 14px; }
    .prc-results-layout { display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
    .prc-price-cell { flex: 0 0 auto; min-width: 180px; }
    .prc-price-big { font-size: 28px !important; font-weight: 900 !important; background: #f0fdf4 !important; border: 2px solid #16a34a !important; color: #15803d !important; min-height: 60px !important; text-align: center; }
    .prc-greeks-grid { flex: 1; display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); gap: 10px; }
    @media (max-width: 1200px) { .pricer-grid { grid-template-columns: repeat(3, minmax(160px, 1fr)); } .prc-greeks-grid { grid-template-columns: repeat(3, minmax(140px, 1fr)); } .prc-d1d2-row { grid-template-columns: repeat(3, minmax(160px, 1fr)); } }
    @media (max-width: 760px) { .pricer-grid { grid-template-columns: repeat(2, minmax(140px, 1fr)); } .prc-greeks-grid { grid-template-columns: repeat(2, minmax(140px, 1fr)); } .prc-d1d2-row { grid-template-columns: repeat(2, minmax(140px, 1fr)); } }
  </style>
</head>
<body>
  <div class="app">
    ${MOCK_MODE ? `<div data-mock-banner style="background:#b42318;color:#fff;padding:7px 14px;margin:-10px -16px 10px -16px;
         font-weight:700;font-size:13px;display:flex;gap:10px;align-items:center;">
      <span style="font-size:15px;">&#9888;</span>
      MOCK DATA — the figures on this screen are generated, not market data.
      <span style="font-weight:400;opacity:.85;">For live data: DATA_MODE=1</span>
    </div>` : ''}
    <div class="top-title">
      <span class="dot"></span>
      <div class="title-wrap">
        <div class="title">Derivex Dashboard</div>
        <button type="button" class="help-btn" id="helpBtn">Help</button>
        <button type="button" class="help-btn" id="transferDataBtn" onclick="openTransferDataModal()" style="margin-left:6px;">Transfer Data</button>
      </div>
    </div>
    <div class="crumb">${breadcrumb}</div>
    <div class="row">
      ${navPill('Market', '/market/futures', mainTab === 'market')}
      ${navPill('Tools', '/tools', mainTab === 'tools')}
      ${mainTab === 'market' ? `<div class="group">${navPill('Options', '/market/options', marketTab === 'options')}${navPill('Warrants', '/market/warrants', marketTab === 'warrants')}${navPill('Futures', '/market/futures', marketTab === 'futures')}${navPill('Summary', '/market/summary', marketTab === 'summary')}</div>` : ''}
      ${mainTab === 'tools' ? `<div class="tool-tabs">${navPill('Dividends', '/tools/dividends', toolsTab === 'dividends')}${navPill('Discount Rate', '/tools/discount', toolsTab === 'discount')}${navPill('Pricer', '/tools/pricer', toolsTab === 'pricer')}${navPill('Realized Vols', '/tools/realized-vols', toolsTab === 'realized-vols')}${navPill('Volatility Curve', '/tools/volatility-curve', toolsTab === 'volatility-curve')}${navPill('Risk', '/tools/risk', toolsTab === 'risk')}</div>` : ''}
    </div>
    ${contentHtml}
  </div>

  <script>
    const HELP_IMAGE_SRC = 'data:image/png;base64,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';
    const input = document.getElementById('tickerFilter');
    const body = document.getElementById('futuresBody');
    const count = document.getElementById('visibleCount');
    const optionsBody = document.getElementById('optionsBody');
    const optionsSelectedTicker = document.getElementById('optionsSelectedTicker');
    const optionsResetSortBtn = document.getElementById('optionsResetSortBtn');
    const optionsExpandAllBtn = document.getElementById('optionsExpandAllBtn');
    const optionsCollapseAllBtn = document.getElementById('optionsCollapseAllBtn');
    const helpBtn = document.getElementById('helpBtn');

    if (helpBtn) {
      const modal = document.createElement('div');
      modal.className = 'help-modal-overlay';
      modal.id = 'helpModal';
      modal.innerHTML = '<div class="help-modal-card" role="dialog" aria-modal="true" aria-label="Help image"><button type="button" class="help-modal-close" id="helpModalClose" aria-label="Close">x</button><img class="help-modal-image" alt="Help" src="' + HELP_IMAGE_SRC + '" /></div>';
      document.body.appendChild(modal);

      const closeModal = () => {
        modal.classList.remove('open');
      };

      helpBtn.addEventListener('click', () => {
        const pwd = window.prompt('Enter password');
        if (pwd === '111') {
          modal.classList.add('open');
        }
      });

      const closeBtn = document.getElementById('helpModalClose');
      if (closeBtn) closeBtn.addEventListener('click', closeModal);
      modal.addEventListener('click', (event) => {
        if (event.target === modal) closeModal();
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeModal();
      });
    }

    // --- Transfer Data Modal (multi-file upload) ---
    (function() {
      var overlay = document.createElement('div');
      overlay.id = 'transferDataOverlay';
      overlay.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;align-items:center;justify-content:center;';
      overlay.innerHTML = [
        '<div style="background:#fff;border-radius:12px;padding:28px 32px;min-width:380px;max-width:500px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.18);position:relative;">',
        '  <button id="transferDataClose" style="position:absolute;top:10px;right:12px;background:none;border:none;font-size:18px;cursor:pointer;color:#64748b;">&times;</button>',
        '  <h2 style="margin:0 0 6px;font-size:18px;font-weight:700;color:#111827;">Transfer Data</h2>',
        '  <p style="margin:0 0 20px;font-size:13px;color:#6b7280;">Select one or more files to upload and save to the server.</p>',
        '  <div id="tdDropZone" style="border:2px dashed #d1d5db;border-radius:8px;padding:24px 16px;text-align:center;cursor:pointer;transition:border-color 0.15s;margin-bottom:12px;">',
        '    <div style="font-size:32px;margin-bottom:8px;">&#128196;</div>',
        '    <div id="tdFileName" style="font-size:13px;color:#374151;font-weight:600;">Click or drag files here</div>',
        '    <div style="font-size:11px;color:#9ca3af;margin-top:4px;">.xlsx, .xlsm, .xls, .py or .txt</div>',
        '    <input id="tdFileInput" type="file" accept=".xlsx,.xlsm,.xls,.py,.txt" multiple style="display:none;">',
        '  </div>',
        '  <div id="tdFileList" style="margin-bottom:12px;max-height:140px;overflow-y:auto;"></div>',
        '  <button id="tdUploadBtn" style="width:100%;padding:10px;background:#111827;color:#fff;border:none;border-radius:7px;font-size:14px;font-weight:600;cursor:pointer;opacity:0.4;pointer-events:none;">Upload to Server</button>',
        '  <div id="tdStatus" style="margin-top:12px;font-size:12px;color:#6b7280;min-height:18px;text-align:center;"></div>',
        '</div>'
      ].join('');
      document.body.appendChild(overlay);

      function closeTransfer() { overlay.style.display = 'none'; }
      document.getElementById('transferDataClose').addEventListener('click', closeTransfer);
      overlay.addEventListener('click', function(e) { if (e.target === overlay) closeTransfer(); });

      var selectedFiles = [];
      var ALLOWED = ['xlsx','xlsm','xls','py','txt'];

      function renderFileList() {
        var listEl = document.getElementById('tdFileList');
        if (selectedFiles.length === 0) { listEl.innerHTML = ''; return; }
        listEl.innerHTML = selectedFiles.map(function(f, i) {
          var ext = f.name.split('.').pop().toLowerCase();
          var valid = ALLOWED.indexOf(ext) !== -1;
          var color = valid ? '#374151' : '#dc2626';
          return '<div style="display:flex;align-items:center;justify-content:space-between;padding:5px 8px;background:#f9fafb;border-radius:5px;margin-bottom:4px;font-size:12px;">'
            + '<span style="color:' + color + ';font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:320px;" title="' + f.name + '">' + f.name + ' <span style="font-weight:400;color:#9ca3af;">(' + (f.size/1024).toFixed(1) + ' KB)</span></span>'
            + '<button data-idx="' + i + '" style="background:none;border:none;color:#9ca3af;cursor:pointer;font-size:14px;padding:0 2px;line-height:1;" title="Remove">&times;</button>'
            + '</div>';
        }).join('');
        listEl.querySelectorAll('button[data-idx]').forEach(function(btn) {
          btn.addEventListener('click', function() {
            selectedFiles.splice(parseInt(btn.getAttribute('data-idx')), 1);
            renderFileList();
            updateUploadBtn();
          });
        });
      }

      function updateUploadBtn() {
        var valid = selectedFiles.filter(function(f) {
          return ALLOWED.indexOf(f.name.split('.').pop().toLowerCase()) !== -1;
        });
        var btn = document.getElementById('tdUploadBtn');
        if (valid.length > 0) {
          btn.style.opacity = '1'; btn.style.pointerEvents = 'auto';
          document.getElementById('tdFileName').textContent = valid.length + ' file' + (valid.length > 1 ? 's' : '') + ' selected';
        } else {
          btn.style.opacity = '0.4'; btn.style.pointerEvents = 'none';
          document.getElementById('tdFileName').textContent = 'Click or drag files here';
        }
        document.getElementById('tdStatus').textContent = '';
      }

      function addFiles(fileList) {
        var invalid = [];
        Array.from(fileList).forEach(function(f) {
          var ext = f.name.split('.').pop().toLowerCase();
          if (ALLOWED.indexOf(ext) === -1) { invalid.push(f.name); return; }
          // avoid duplicates by name
          if (!selectedFiles.some(function(s) { return s.name === f.name; })) {
            selectedFiles.push(f);
          }
        });
        renderFileList();
        updateUploadBtn();
        if (invalid.length > 0) {
          document.getElementById('tdStatus').style.color = '#dc2626';
          document.getElementById('tdStatus').textContent = 'Skipped (unsupported): ' + invalid.join(', ');
        }
      }

      var dropZone = document.getElementById('tdDropZone');
      var fileInput = document.getElementById('tdFileInput');

      dropZone.addEventListener('click', function() { fileInput.click(); });
      fileInput.addEventListener('change', function() { if (fileInput.files.length) addFiles(fileInput.files); fileInput.value = ''; });

      dropZone.addEventListener('dragover', function(e) { e.preventDefault(); dropZone.style.borderColor = '#111827'; });
      dropZone.addEventListener('dragleave', function() { dropZone.style.borderColor = '#d1d5db'; });
      dropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        dropZone.style.borderColor = '#d1d5db';
        if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
      });

      document.getElementById('tdUploadBtn').addEventListener('click', async function() {
        var valid = selectedFiles.filter(function(f) {
          return ALLOWED.indexOf(f.name.split('.').pop().toLowerCase()) !== -1;
        });
        if (valid.length === 0) return;
        var st = document.getElementById('tdStatus');
        st.style.color = '#6b7280';
        st.textContent = 'Uploading ' + valid.length + ' file' + (valid.length > 1 ? 's' : '') + '...';
        var saved = [], errors = [];
        for (var i = 0; i < valid.length; i++) {
          try {
            var fd = new FormData();
            fd.append('file', valid[i], valid[i].name);
            var r = await fetch('/api/upload-excel', { method: 'POST', body: fd });
            var json = await r.json();
            if (json.ok) { saved.push(json.filename); }
            else { errors.push(valid[i].name + ': ' + (json.error || 'failed')); }
          } catch(e) {
            errors.push(valid[i].name + ': ' + e.message);
          }
        }
        if (errors.length === 0) {
          st.style.color = '#16a34a';
          st.textContent = 'Saved: ' + saved.join(', ');
        } else if (saved.length > 0) {
          st.style.color = '#d97706';
          st.textContent = 'Saved: ' + saved.join(', ') + ' | Errors: ' + errors.join('; ');
        } else {
          st.style.color = '#dc2626';
          st.textContent = 'Errors: ' + errors.join('; ');
        }
      });

      window.openTransferDataModal = function() {
        selectedFiles = [];
        document.getElementById('tdFileName').textContent = 'Click or drag files here';
        document.getElementById('tdFileList').innerHTML = '';
        document.getElementById('tdStatus').textContent = '';
        var btn = document.getElementById('tdUploadBtn');
        btn.style.opacity = '0.4';
        btn.style.pointerEvents = 'none';
        document.getElementById('tdFileInput').value = '';
        overlay.style.display = 'flex';
      };
    })();

    window.__collapsedExpiriesByTicker = window.__collapsedExpiriesByTicker || {};
    window.__optionsExpiriesByTicker = window.__optionsExpiriesByTicker || {};
    window.__optionsSortByTicker = window.__optionsSortByTicker || {};

    // Dividends injected server-side (shared across all users, persisted to disk)
    window.__dividendsByTicker = ${JSON.stringify(serverState.dividendsByTicker || {})};

    // Pre-load discount factors from localStorage
    try {
      const _discRaw = localStorage.getItem('discountFactors');
      window.__discountFactors = _discRaw ? JSON.parse(_discRaw) : {};
    } catch (_) {
      window.__discountFactors = {};
    }

    function formatSpotClient(value) {
      const n = Number(value);
      return Number.isFinite(n) ? n.toFixed(3) : '-';
    }

    function formatRatePctClient(value) {
      const n = Number(value);
      return Number.isFinite(n) ? (n * 100).toFixed(1) + '%' : '-';
    }

    function contractExpiryDateClient(code) {
      const m = /^(\d{2})(\d{2})$/.exec(String(code || ''));
      if (!m) return null;
      const month = parseInt(m[1], 10);
      const year = 2000 + parseInt(m[2], 10);
      if (month < 1 || month > 12) return null;
      // UTC-normalized last day of that month — divEntry.date is an ISO
      // "YYYY-MM-DD" string, which Date() parses as UTC midnight, so this
      // must also be UTC or a dividend on the exact last day of the month
      // can appear to fall after expiry purely from timezone skew.
      return new Date(Date.UTC(year, month, 0));
    }

    function dividendAdjustedRateClient(rec, divEntry, code) {
      const base = rec && typeof rec === 'object' ? rec.rate : null;
      if (!divEntry || !divEntry.amount || !divEntry.date) return { rate: base, adjusted: false };
      const D = parseFloat(divEntry.amount);
      if (!Number.isFinite(D) || D <= 0) return { rate: base, adjusted: false };
      const divDate = new Date(divEntry.date);
      const expDate = contractExpiryDateClient(code);
      if (!expDate || isNaN(divDate.getTime())) return { rate: base, adjusted: false };
      if (divDate > expDate) return { rate: base, adjusted: false };
      if (!rec || rec.fut_mid === null || rec.spot_mid === null || !rec.dtm || rec.spot_mid === 0) {
        return { rate: base, adjusted: true };
      }
      const adjRate = ((rec.fut_mid + D) / rec.spot_mid - 1) * (365 / rec.dtm);
      return { rate: adjRate, adjusted: true };
    }

    function _normCdf(x) {
      const sign = x < 0 ? -1 : 1;
      const ax = Math.abs(x) / Math.sqrt(2);
      const t = 1 / (1 + 0.3275911 * ax);
      const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741, a4 = -1.453152027, a5 = 1.061405429;
      const erf = 1 - (((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t) * Math.exp(-ax * ax);
      return 0.5 * (1 + sign * erf);
    }

    function _fmtOut(v, dp = 4) {
      if (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v)) return '-';
      return Number(v).toFixed(dp);
    }

    // Cox-Ross-Rubinstein binom agaci.
    // Black-Scholes'un veremedigi tek sey erken kullanim hakkidir; agac her
    // dugumde "simdi kullan" ile "bekle" arasinda secim yapabildigi icin
    // Amerikan tipi opsiyonlari da fiyatlar.
    function _binomPrice(isCall, S, K, r, sigma, T, steps, american) {
      steps = steps || 200;
      if (!(S > 0) || !(K > 0) || !(sigma > 0) || !(T > 0)) return null;
      const dt = T / steps;
      const u = Math.exp(sigma * Math.sqrt(dt));
      const d = 1 / u;
      const disk = Math.exp(-r * dt);
      const p = (Math.exp(r * dt) - d) / (u - d);
      if (!(p > 0 && p < 1)) return null;   // risksiz olasilik aralik disi
      const ic = (s) => (isCall ? Math.max(s - K, 0) : Math.max(K - s, 0));

      const v = new Array(steps + 1);
      for (let i = 0; i <= steps; i++) v[i] = ic(S * Math.pow(u, steps - i) * Math.pow(d, i));
      for (let adim = steps - 1; adim >= 0; adim--) {
        for (let i = 0; i <= adim; i++) {
          let devam = disk * (p * v[i] + (1 - p) * v[i + 1]);
          if (american) devam = Math.max(devam, ic(S * Math.pow(u, adim - i) * Math.pow(d, i)));
          v[i] = devam;
        }
      }
      return v[0];
    }

    function _bsCore(isCall, S, K, r, sigma, T) {
      if (!(S > 0) || !(K > 0) || !(sigma > 0) || !(T > 0)) return null;
      const sqrtT = Math.sqrt(T);
      const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
      const d2 = d1 - sigma * sqrtT;
      const Nd1 = _normCdf(d1);
      const Nd2 = _normCdf(d2);
      const Nmd1 = _normCdf(-d1);
      const Nmd2 = _normCdf(-d2);
      const df = Math.exp(-r * T);
      const price = isCall ? (S * Nd1 - K * df * Nd2) : (K * df * Nmd2 - S * Nmd1);
      const delta = isCall ? Nd1 : (Nd1 - 1);
      const gamma = Math.exp(-0.5 * d1 * d1) / (S * sigma * sqrtT * Math.sqrt(2 * Math.PI));
      const vega = S * Math.exp(-0.5 * d1 * d1) * sqrtT / Math.sqrt(2 * Math.PI) / 100;
      const thetaYear = isCall
        ? (-S * Math.exp(-0.5 * d1 * d1) * sigma / (2 * sqrtT * Math.sqrt(2 * Math.PI)) - r * K * df * Nd2)
        : (-S * Math.exp(-0.5 * d1 * d1) * sigma / (2 * sqrtT * Math.sqrt(2 * Math.PI)) + r * K * df * Nmd2);
      const thetaDay = thetaYear / 365;
      const rho = (isCall ? (K * T * df * Nd2) : (-K * T * df * Nmd2)) / 100;
      // Girdiler de dondurulur: cagiran taraf ayni opsiyonu baska bir
      // yontemle (binom agaci) fiyatlayabilsin diye.
      return { d1, d2, price, delta, gamma, vega, thetaDay, rho,
               isCall, S, K, r, sigma, T };
    }

    function initOptionPricer() {
      const root = document.getElementById('toolsPricerCard');
      if (!root) return;
      const byId = (id) => document.getElementById(id);
      const symbolEl = byId('prcSymbol');
      const typeEl = byId('prcType');
      const spotEl = byId('prcSpot');
      const spotManualEl = byId('prcSpotManual');
      const strikeEl = byId('prcStrike');
      const daysEl = byId('prcDays');
      const dateEl = byId('prcDate');
      const rateEl = byId('prcRate');
      const rateManualEl = byId('prcRateManual');
      const volEl = byId('prcVol');
      const divEl = byId('prcDiv');
      const toggleEl = byId('prcDivToggle');
      const outPvDiv = byId('prcPvDiv');
      const outAdjSpot = byId('prcAdjSpot');
      const outT = byId('prcT');
      const outD1 = byId('prcD1');
      const outD2 = byId('prcD2');
      const outPrice = byId('prcPrice');
      const outDelta = byId('prcDelta');
      const outGamma = byId('prcGamma');
      const outVega = byId('prcVega');
      const outTheta = byId('prcTheta');
      const outRho = byId('prcRho');
      const outBinEu = byId('prcBinEu');
      const outBinAm = byId('prcBinAm');
      const outBinDiff = byId('prcBinDiff');
      const outEarlyEx = byId('prcEarlyEx');
      const brokerEl = byId('prcBroker');
      const qtyEl = byId('prcQty');
      const sideEl = byId('prcSide');
      const logBtn = byId('prcLogBtn');
      const copyBtn = byId('prcCopyBtn');
      const clearLogBtn = byId('prcClearLogBtn');
      const logBody = byId('prcLogBody');
      const PRICER_LOG_STORAGE_KEY = 'pricerLogEntries';
      const state = window.__pricerState = window.__pricerState || { dividendOn: true };

      function _esc(s) {
        return String(s || '')
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      function _readValue(el) {
        if (!el) return '-';
        const v = (el.value || '').trim();
        return v || '-';
      }

      // BIST-like tick ladder used for keyboard/spinner stepping on Spot and Strike.
      const priceTickBands = [
        { min: 0.01, max: 19.99, step: 0.01 },
        { min: 20.00, max: 49.99, step: 0.02 },
        { min: 50.00, max: 99.99, step: 0.05 },
        { min: 100.00, max: 249.99, step: 0.10 },
        { min: 250.00, max: 499.99, step: 0.25 },
        { min: 500.00, max: 999.99, step: 0.50 },
        { min: 1000.00, max: 2499.99, step: 1.00 },
        { min: 2500.00, max: Number.POSITIVE_INFINITY, step: 2.50 },
      ];
      const tickEps = 1e-9;

      function bandLastValue(band) {
        if (!Number.isFinite(band.max)) return null;
        const k = Math.floor(((band.max - band.min) / band.step) + tickEps);
        return band.min + (k * band.step);
      }

      function getTickBand(price) {
        const p = Number(price);
        if (!Number.isFinite(p)) return priceTickBands[0];
        for (let i = 0; i < priceTickBands.length; i++) {
          const b = priceTickBands[i];
          if (p + tickEps >= b.min && p <= b.max + tickEps) return b;
          if (p < b.min) return b;
        }
        return priceTickBands[priceTickBands.length - 1];
      }

      function getTickStep(price) {
        return getTickBand(price).step;
      }

      function nextTickPrice(currentPrice, dir) {
        const pRaw = Number(currentPrice);
        const p = Number.isFinite(pRaw) ? pRaw : priceTickBands[0].min;

        if (dir > 0) {
          for (let i = 0; i < priceTickBands.length; i++) {
            const b = priceTickBands[i];
            if (p < b.min - tickEps) return b.min;
            if (p <= b.max + tickEps) {
              const k = Math.floor(((p - b.min) / b.step) + tickEps);
              const candidate = b.min + ((k + 1) * b.step);
              if (candidate <= b.max + tickEps) return candidate;
              const nextBand = priceTickBands[i + 1];
              return nextBand ? nextBand.min : candidate;
            }
          }
          return p + getTickStep(p);
        }

        if (p <= priceTickBands[0].min + tickEps) return priceTickBands[0].min;
        for (let i = 0; i < priceTickBands.length; i++) {
          const b = priceTickBands[i];
          if (p <= b.max + tickEps) {
            if (p <= b.min + tickEps) {
              const prevBand = priceTickBands[i - 1];
              if (!prevBand) return priceTickBands[0].min;
              const prevLast = bandLastValue(prevBand);
              return prevLast !== null ? prevLast : prevBand.min;
            }
            const k = Math.ceil(((p - b.min) / b.step) - tickEps);
            const candidate = b.min + ((k - 1) * b.step);
            if (candidate >= b.min - tickEps) return candidate;
            const prevBand = priceTickBands[i - 1];
            if (!prevBand) return priceTickBands[0].min;
            const prevLast = bandLastValue(prevBand);
            return prevLast !== null ? prevLast : prevBand.min;
          }
        }
        return Math.max(priceTickBands[0].min, p - getTickStep(p));
      }

      function updatePriceStepFor(el) {
        if (!el) return;
        const n = Number(el.value);
        el.step = String(getTickStep(Number.isFinite(n) ? n : priceTickBands[0].min));
      }

      function updatePriceStepInputs() {
        updatePriceStepFor(spotEl);
        updatePriceStepFor(strikeEl);
      }

      function bindTickLadderArrows(el) {
        if (!el) return;
        el.addEventListener('focus', () => {
          updatePriceStepFor(el);
        });
        el.addEventListener('input', () => {
          updatePriceStepFor(el);
        });
        el.addEventListener('change', () => {
          updatePriceStepFor(el);
        });
        el.addEventListener('keydown', (event) => {
          if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
          event.preventDefault();
          const curr = Number(el.value);
          const base = Number.isFinite(curr) ? curr : priceTickBands[0].min;
          const dir = event.key === 'ArrowUp' ? 1 : -1;
          const next = nextTickPrice(base, dir);
          const normalized = Math.max(priceTickBands[0].min, Number(next.toFixed(4)));
          el.value = normalized.toFixed(4);
          updatePriceStepFor(el);
          recalc();
        });
      }

      function updateToggleUi() {
        toggleEl.textContent = state.dividendOn ? 'Dividend: ON' : 'Dividend: OFF';
        toggleEl.classList.toggle('off', !state.dividendOn);
      }

      function syncDateDays() {
        if (window.__pricerSkipSyncDate) {
          window.__pricerSkipSyncDate = false;
          return;
        }
        if (!dateEl || !daysEl) return;
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const dt = dateEl.value ? new Date(dateEl.value + 'T00:00:00') : null;
        if (dt && Number.isFinite(dt.getTime())) {
          const diffMs = dt.getTime() - today.getTime();
          const days = Math.max(0, Math.round(diffMs / 86400000));
          daysEl.value = String(days);
        }
      }

      function recalc() {
        syncDateDays();
        const isCall = (typeEl.value || 'Call') === 'Call';
        const S = Number(spotEl.value);
        const K = Number(strikeEl.value);
        const days = Number(daysEl.value);
        const r = Number(rateEl.value) / 100;
        const sigma = Number(volEl.value) / 100;
        const D = Number(divEl.value);
        const T = Number.isFinite(days) && days >= 0 ? (days / 365) : 0;

        // Get interpolated discount rate from monthly term structure (Discount Factor tab)
        function getDiscountRate(T_years) {
          var factors = window.__discountFactors || {};
          var T_months = T_years * 12;
          if (T_months <= 0) return Number(factors['1'] || 0) / 100;
          if (T_months >= 12) return Number(factors['12'] || 0) / 100;
          var lo = Math.floor(T_months);
          var hi = Math.ceil(T_months);
          if (lo === 0) lo = 1;
          if (lo === hi) return Number(factors[String(lo)] || 0) / 100;
          var rLo = Number(factors[String(lo)] || 0) / 100;
          var rHi = Number(factors[String(hi)] || 0) / 100;
          var frac = T_months - lo;
          return rLo + frac * (rHi - rLo);
        }
        var r_div = getDiscountRate(T);
        const pvDiv = (state.dividendOn && Number.isFinite(D) && D > 0 && T > 0 && Number.isFinite(r_div))
          ? (D * Math.exp(-r_div * T))
          : 0;
        const Sadj = Number.isFinite(S) ? (S - pvDiv) : NaN;
        outPvDiv.value = _fmtOut(pvDiv, 4);
        outAdjSpot.value = _fmtOut(Sadj, 4);
        outT.value = _fmtOut(T, 6);
        const core = _bsCore(isCall, Sadj, K, r, sigma, T);
        if (!core) {
          outD1.value = '-'; outD2.value = '-'; outPrice.value = '-';
          outDelta.value = '-'; outGamma.value = '-'; outVega.value = '-'; outTheta.value = '-'; outRho.value = '-';
          if (outBinEu) { outBinEu.value = '-'; outBinAm.value = '-'; outBinDiff.value = '-'; outEarlyEx.value = '-'; }
          return;
        }
        outD1.value = _fmtOut(core.d1, 4);
        outD2.value = _fmtOut(core.d2, 4);
        outPrice.value = _fmtOut(core.price, 4);
        outDelta.value = _fmtOut(core.delta, 4);
        outGamma.value = _fmtOut(core.gamma, 6);
        outVega.value = _fmtOut(core.vega, 4);
        outTheta.value = _fmtOut(core.thetaDay, 6);
        outRho.value = _fmtOut(core.rho, 4);

        // Yontemler arasi tutarlilik: ayni opsiyonu binom agaciyla da fiyatla.
        // Avrupa agaci BS'ye yakinsamali; sapma buyukse girdi ya da model
        // tarafinda bir sorun var demektir.
        if (outBinEu) {
          const bEu = _binomPrice(core.isCall, core.S, core.K, core.r, core.sigma, core.T, 300, false);
          const bAm = _binomPrice(core.isCall, core.S, core.K, core.r, core.sigma, core.T, 300, true);
          outBinEu.value = _fmtOut(bEu, 4);
          outBinAm.value = _fmtOut(bAm, 4);
          if (bEu !== null && core.price > 1e-12) {
            const sapma = (bEu - core.price) / core.price * 100;
            outBinDiff.value = (sapma >= 0 ? '+' : '') + sapma.toFixed(3) + '%';
          } else {
            outBinDiff.value = '-';
          }
          outEarlyEx.value = (bAm !== null && bEu !== null) ? _fmtOut(bAm - bEu, 4) : '-';
        }
      }

      [typeEl, spotEl, strikeEl, dateEl, rateEl, volEl, divEl].forEach((el) => {
        if (!el) return;
        el.addEventListener('input', () => {
          recalc();
        });
        el.addEventListener('change', () => {
          recalc();
        });
      });

      bindTickLadderArrows(spotEl);
      bindTickLadderArrows(strikeEl);

      toggleEl.addEventListener('click', () => {
        state.dividendOn = !state.dividendOn;
        updateToggleUi();
        recalc();
      });

      function applySpotValue(v) {
        const n = Number(v);
        if (!Number.isFinite(n)) return false;
        spotEl.value = n.toFixed(4);
        updatePriceStepFor(spotEl);
        recalc();
        return true;
      }

      function isSpotManual() {
        return !!(spotManualEl && spotManualEl.checked);
      }

      function isRateManual() {
        return !!(rateManualEl && rateManualEl.checked);
      }

      function fetchAndFillSpot(ticker) {
        if (isSpotManual()) {
          recalc();
          return;
        }
        if (!ticker) return;
        fetch('/api/spot?ticker=' + encodeURIComponent(ticker))
          .then((r) => r.json())
          .then((data) => {
            const spotFromApi = data && data.data ? data.data.spot_mid : null;
            if (spotFromApi !== null && spotFromApi !== undefined && applySpotValue(spotFromApi)) {
              return null;
            }
            return fetch('/api/options-chain?ticker=' + encodeURIComponent(ticker)).then((r) => r.json());
          })
          .then((chainData) => {
            if (!chainData) return;
            const rows = Array.isArray(chainData.options) ? chainData.options : [];
            const rowWithSpot = rows.find((row) => row && row.spot_mid !== null && row.spot_mid !== undefined);
            if (rowWithSpot) {
              applySpotValue(rowWithSpot.spot_mid);
            }
          })
          .catch(() => {});
      }

      function maturityCodeFromDate(dateValue) {
        if (!dateValue) return null;
        const dt = new Date(dateValue + 'T00:00:00');
        if (!Number.isFinite(dt.getTime())) return null;
        const mm = String(dt.getMonth() + 1).padStart(2, '0');
        const yy = String(dt.getFullYear()).slice(-2);
        return mm + yy;
      }

      function fetchAndFillRate(ticker, dateValue) {
        if (isRateManual()) {
          recalc();
          return;
        }
        const code = maturityCodeFromDate(dateValue);
        if (!ticker || !code) {
          recalc();
          return;
        }

        fetch('/api/futures-rates?ticker=' + encodeURIComponent(ticker))
          .then((r) => r.json())
          .then((data) => {
            const rates = data && data.rates ? data.rates : null;
            const rawRate = rates && Object.prototype.hasOwnProperty.call(rates, code) ? rates[code] : null;
            const n = Number(rawRate);
            if (Number.isFinite(n)) {
              if (document.activeElement !== rateEl) {
                rateEl.value = (n * 100).toFixed(2);
              }
              recalc();
              return null;
            }
            return fetch('/api/options-chain?ticker=' + encodeURIComponent(ticker)).then((r) => r.json());
          })
          .then((chainData) => {
            if (!chainData) return;
            const rows = Array.isArray(chainData.options) ? chainData.options : [];
            const exact = rows.find((row) => row && String(row.expiry || '') === code && Number.isFinite(Number(row.rate)));
            if (exact) {
              if (document.activeElement !== rateEl) {
                rateEl.value = (Number(exact.rate) * 100).toFixed(2);
              }
            } else {
              if (document.activeElement !== rateEl && !(rateEl.value || '').trim()) {
                rateEl.value = '';
              }
            }
            recalc();
          })
          .catch(() => {});
      }

      function refreshPricerMarketInputs() {
        const ticker = symbolEl ? symbolEl.value : '';
        const dateValue = dateEl ? dateEl.value : '';
        fetchAndFillSpot(ticker);
        fetchAndFillRate(ticker, dateValue);
        // Auto-fill dividend from Dividends tab storage
        if (divEl) {
          const divData = window.__dividendsByTicker || {};
          const entry = divData[ticker];
          if (entry && entry.amount !== undefined && entry.amount !== '') {
            divEl.value = entry.amount;
          } else {
            divEl.value = 0;
          }
          recalc();
        }
      }

      if (symbolEl) {
        symbolEl.addEventListener('change', () => {
          refreshPricerMarketInputs();
        });
      }

      if (dateEl) {
        dateEl.addEventListener('change', () => {
          fetchAndFillRate(symbolEl ? symbolEl.value : '', dateEl.value);
        });
        dateEl.addEventListener('input', () => {
          fetchAndFillRate(symbolEl ? symbolEl.value : '', dateEl.value);
        });
      }

      if (spotManualEl) {
        spotManualEl.addEventListener('change', () => {
          if (!spotManualEl.checked) {
            fetchAndFillSpot(symbolEl ? symbolEl.value : '');
          } else {
            recalc();
          }
        });
      }

      if (rateManualEl) {
        rateManualEl.addEventListener('change', () => {
          if (!rateManualEl.checked) {
            fetchAndFillRate(symbolEl ? symbolEl.value : '', dateEl ? dateEl.value : '');
          } else {
            recalc();
          }
        });
      }

      function showCopyToast(text) {
        let toast = document.getElementById('prcCopyToast');
        if (!toast) {
          toast = document.createElement('div');
          toast.id = 'prcCopyToast';
          toast.style.cssText = 'position:fixed;bottom:32px;left:50%;transform:translateX(-50%);background:#1e293b;color:#f0fdf4;padding:10px 22px;border-radius:8px;font-size:14px;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,0.4);z-index:9999;pointer-events:none;transition:opacity 0.3s;opacity:0;';
          document.body.appendChild(toast);
        }
        toast.textContent = text;
        toast.style.opacity = '1';
        clearTimeout(toast._hideTimer);
        toast._hideTimer = setTimeout(() => { toast.style.opacity = '0'; }, 2000);
      }

      if (copyBtn) {
        copyBtn.addEventListener('click', () => {
          const symbol  = (symbolEl  ? symbolEl.value  : '').trim();
          const type    = (typeEl    ? typeEl.value    : '').trim().toLowerCase();
          const strike  = (strikeEl  ? strikeEl.value  : '').trim();
          const priceRaw = (outPrice ? outPrice.value  : '').trim();
          const delta   = (outDelta  ? outDelta.value  : '').trim();
          const spot    = (spotEl    ? spotEl.value    : '').trim();
          const qtyRaw  = (qtyEl     ? qtyEl.value     : '').trim();
          const side    = (sideEl    ? sideEl.value    : 'mid').trim();
          const dateVal = (dateEl    ? dateEl.value    : '').trim();

          // 2 decimal places for price
          let price = priceRaw;
          if (priceRaw && priceRaw !== '-') {
            const n = parseFloat(priceRaw);
            if (Number.isFinite(n)) price = n.toFixed(2);
          }

          // 2 decimal places for spot
          let spot2 = spot;
          if (spot && spot !== '-') {
            const n = parseFloat(spot);
            if (Number.isFinite(n)) spot2 = n.toFixed(2);
          }

          // Auto-suffix qty with c/p based on type if no letter already present
          let qty = qtyRaw;
          if (qty && !/[a-zA-Z]/.test(qty)) {
            qty = qty + (type === 'put' ? 'p' : 'c');
          }

          let dateLabel = '';
          if (dateVal) {
            try {
              const d = new Date(dateVal + 'T00:00:00');
              const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
              dateLabel = d.getDate() + '-' + months[d.getMonth()];
            } catch (_) {}
          }

          let deltaLabel = '';
          if (delta && delta !== '-') {
            const dNum = parseFloat(delta);
            if (Number.isFinite(dNum)) deltaLabel = '(' + Math.round(Math.abs(dNum) * 100) + 'd)';
          }

          const parts = [symbol, type, strike];
          if (dateLabel) parts.push(dateLabel);
          if (qty) parts.push(qty);
          parts.push(side);
          if (price) parts.push(price);
          if (spot2 || deltaLabel) parts.push('vs ' + spot2 + deltaLabel);
          const text = parts.join(' ');

          function onCopied() { showCopyToast('✓ Copied: ' + text); }
          function onFail()   { showCopyToast('✗ Copy failed'); }

          if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            navigator.clipboard.writeText(text).then(onCopied).catch(() => {
              try {
                const ta = document.createElement('textarea');
                ta.value = text; ta.style.cssText = 'position:fixed;opacity:0;top:0;left:0;';
                document.body.appendChild(ta); ta.focus(); ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                onCopied();
              } catch (_) { onFail(); }
            });
          } else {
            try {
              const ta = document.createElement('textarea');
              ta.value = text; ta.style.cssText = 'position:fixed;opacity:0;top:0;left:0;';
              document.body.appendChild(ta); ta.focus(); ta.select();
              document.execCommand('copy');
              document.body.removeChild(ta);
              onCopied();
            } catch (_) { onFail(); }
          }
        });
      }

      function fillPricerFromEntry(e) {
        if (symbolEl && e.symbol && e.symbol !== '-') { symbolEl.value = e.symbol; symbolEl.dispatchEvent(new Event('change')); }
        if (typeEl   && e.type   && e.type   !== '-') typeEl.value = e.type;
        if (strikeEl && e.strike && e.strike !== '-') strikeEl.value = e.strike;
        if (dateEl   && e.date   && e.date   !== '-') {
          dateEl.value = e.date;
          dateEl.dispatchEvent(new Event('change'));
          window.__pricerSkipSyncDate = true;
        }
        if (volEl    && e.vol    && e.vol    !== '-') volEl.value = e.vol;
        if (divEl    && e.div    && e.div    !== '-') divEl.value = e.div;
        if (spotEl   && e.spot   && e.spot   !== '-') {
          spotEl.value = e.spot;
          if (spotManualEl) spotManualEl.checked = true;
        }
        if (rateEl   && e.rate   && e.rate   !== '-') {
          rateEl.value = e.rate;
          if (rateManualEl) rateManualEl.checked = true;
        }
        recalc();
        window.scrollTo({ top: 0, behavior: 'smooth' });
        showCopyToast('✓ Loaded into pricer');
      }

      function renderLogEntry(e, prepend) {
        const dataAttr = " data-entry='" + JSON.stringify(e).replace(/'/g, '&#39;') + "'";
        const rowHtml = '<tr class="pricer-log-row" style="cursor:pointer" title="Click to load into pricer"' + dataAttr + '>'\n          + '<td>' + _esc(e.stamp) + '</td>'\n          + '<td>' + _esc(e.broker) + '</td>'\n          + '<td>' + _esc(e.symbol) + '</td>'\n          + '<td>' + _esc(e.type) + '</td>'\n          + '<td>' + _esc(e.spot) + '</td>'\n          + '<td>' + _esc(e.strike) + '</td>'\n          + '<td>' + _esc(e.date) + '</td>'\n          + '<td>' + _esc(e.dtm) + '</td>'\n          + '<td>' + _esc(e.rate) + '</td>'\n          + '<td>' + _esc(e.vol) + '</td>'\n          + '<td>' + _esc(e.div) + '</td>'\n          + '<td>' + _esc(e.price) + '</td>'\n          + '<td>' + _esc(e.delta) + '</td>'\n          + '<td>' + _esc(e.gamma) + '</td>'\n          + '<td>' + _esc(e.vega) + '</td>'\n          + '<td>' + _esc(e.theta) + '</td>'\n          + '<td>' + _esc(e.rho) + '</td>'\n          + '</tr>';
        if (logBody) logBody.insertAdjacentHTML(prepend ? 'afterbegin' : 'beforeend', rowHtml);
      }

      if (logBody) {
        const renderEmptyLogRow = () => {
          logBody.innerHTML = '<tr><td class="options-empty" colspan="17">No logs yet.</td></tr>';
        };

        logBody.addEventListener('click', (ev) => {
          const row = ev.target.closest('tr[data-entry]');
          if (!row) return;
          try { fillPricerFromEntry(JSON.parse(row.getAttribute('data-entry'))); } catch (_) {}
        });

        if (clearLogBtn) {
          clearLogBtn.addEventListener('click', () => {
            fetch('/api/pricer-log', { method: 'DELETE' }).catch(() => {});
            renderEmptyLogRow();
          });
        }

        if (logBtn) {
          logBtn.addEventListener('click', () => {
            const now = new Date();
            const hh = String(now.getHours()).padStart(2, '0');
            const mm = String(now.getMinutes()).padStart(2, '0');
            const ss = String(now.getSeconds()).padStart(2, '0');
            const entry = {
              stamp: hh + ':' + mm + ':' + ss,
              broker: _readValue(brokerEl), symbol: _readValue(symbolEl), type: _readValue(typeEl),
              spot: _readValue(spotEl), strike: _readValue(strikeEl), date: _readValue(dateEl),
              dtm: _readValue(daysEl), rate: _readValue(rateEl), vol: _readValue(volEl),
              div: _readValue(divEl), price: _readValue(outPrice), delta: _readValue(outDelta),
              gamma: _readValue(outGamma), vega: _readValue(outVega), theta: _readValue(outTheta),
              rho: _readValue(outRho)
            };
            const first = logBody.firstElementChild;
            if (first && first.querySelector && first.querySelector('.options-empty')) logBody.innerHTML = '';
            renderLogEntry(entry, true);
            fetch('/api/pricer-log', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(entry)
            }).catch(() => {});
            if (brokerEl) brokerEl.value = '';
          });
        }

        // Load persisted log from server on mount
        fetch('/api/pricer-log').then((r) => r.json()).then((saved) => {
          if (Array.isArray(saved) && saved.length > 0) {
            logBody.innerHTML = '';
            saved.forEach((e) => renderLogEntry(e, false));
          }
        }).catch(() => {});
      }

      refreshPricerMarketInputs();
      setInterval(() => {
        refreshPricerMarketInputs();
      }, 2000);

      // Apply pending option data if navigated from options table
        let pending = null;
        try {
          const rawPending = sessionStorage.getItem('pricerPendingData');
          if (rawPending) pending = JSON.parse(rawPending);
        } catch (_) {
          pending = null;
        }
        const urlPending = new URLSearchParams(window.location.search || '');
        if (!pending) {
          const pSymbol = (urlPending.get('symbol') || '').trim();
          const pType = (urlPending.get('type') || '').trim();
          const pStrike = (urlPending.get('strike') || '').trim();
          const pDate = (urlPending.get('date') || '').trim();
          const pDtm = (urlPending.get('dtm') || '').trim();
          const pRate = (urlPending.get('rate') || '').trim();
          const pVol = (urlPending.get('vol') || '').trim();
          if (pSymbol || pType || pStrike || pDate || pDtm || pRate || pVol) {
            pending = { symbol: pSymbol, type: pType, strike: pStrike, date: pDate, dtm: pDtm, rate: pRate, vol: pVol };
          }
        }
        if (pending) {
          if (pending.symbol && symbolEl) symbolEl.value = String(pending.symbol).toUpperCase();
          if (pending.type && typeEl) typeEl.value = pending.type;
          if (pending.strike && strikeEl) strikeEl.value = pending.strike;
          if (pending.date && dateEl) dateEl.value = pending.date;
          if (pending.dtm && daysEl) daysEl.value = pending.dtm;
          if (pending.rate && rateEl) rateEl.value = pending.rate;
          if (pending.vol && volEl) volEl.value = pending.vol;
          try { sessionStorage.removeItem('pricerPendingData'); } catch (_) {}
          if (pending.symbol) fetchAndFillSpot(pending.symbol);
          if (pending.dtm) window.__pricerSkipSyncDate = true;
        }

      updatePriceStepInputs();
      updateToggleUi();
      recalc();
    }

    function initRealizedVols() {
      const root = document.getElementById('toolsRealizedVolsCard');
      if (!root) return;

      const refreshBtn = document.getElementById('rvRefreshBtn');
      const resetSortBtn = document.getElementById('rvResetSortBtn');
      const lookbackEl = document.getElementById('rvLookbackSelect');
      const modelEl = document.getElementById('rvModelSelect');
      const updatedEl = document.getElementById('rvUpdatedAt');
      const statusEl = document.getElementById('rvStatus');
      const bodyEl = document.getElementById('rvTableBody');

      if (!bodyEl) return;

      // RV sort state management
      window.__rvSortState = window.__rvSortState || { column: 'ticker', direction: 'asc' };

      function setRvSort(column, direction) {
        window.__rvSortState = { column, direction };
        syncRvSortHeaders();
      }

      function toggleRvSort(column) {
        const current = window.__rvSortState || { column: 'ticker', direction: 'asc' };
        if (current.column === column) {
          // Cycle: asc -> desc -> asc
          const newDir = current.direction === 'asc' ? 'desc' : 'asc';
          setRvSort(column, newDir);
        } else {
          // New column, default to asc
          setRvSort(column, 'asc');
        }
      }

      function resetRvSort() {
        setRvSort('ticker', 'asc');
      }

      function syncRvSortHeaders() {
        const state = window.__rvSortState || { column: 'ticker', direction: 'asc' };
        document.querySelectorAll('[data-rv-sort]').forEach((button) => {
          const key = String(button.getAttribute('data-rv-sort') || '');
          const active = state.column === key;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
          const indicator = button.querySelector('.sort-indicator');
          if (indicator) {
            indicator.innerHTML = active
              ? (state.direction === 'desc' ? '&#8595;' : '&#8593;')
              : '&#8597;';
          }
        });
      }

      function esc(s) {
        return String(s || '')
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      function fmtTs(iso) {
        if (!iso) return '-';
        const d = new Date(iso);
        if (!Number.isFinite(d.getTime())) return '-';
        return d.toLocaleString();
      }

      function parseRvNumber(v) {
        if (v === null || v === undefined || v === '') return null;
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
      }

      function normalizeForecastBundle(raw) {
        const empty = {
          mode: 'none',
          lookbacks: {},
          lookbackKeys: [],
          models: [],
          horizons: ['15D (%)', '30D (%)', '60D (%)', '90D (%)', '180D (%)'],
          legacy: {},
        };

        if (!raw || typeof raw !== 'object') return empty;

        if (raw.lookbacks && typeof raw.lookbacks === 'object') {
          const lookbackKeys = Object.keys(raw.lookbacks);
          const modelsFromPayload = Array.isArray(raw.models) ? raw.models.filter((m) => String(m || '').trim()) : [];
          let discoveredModels = modelsFromPayload.slice();

          if (discoveredModels.length === 0) {
            for (const lb of lookbackKeys) {
              const lbMap = raw.lookbacks[lb] || {};
              for (const ticker of Object.keys(lbMap)) {
                const tickerModels = lbMap[ticker] && typeof lbMap[ticker] === 'object' ? Object.keys(lbMap[ticker]) : [];
                if (tickerModels.length > 0) {
                  discoveredModels = tickerModels;
                  break;
                }
              }
              if (discoveredModels.length > 0) break;
            }
          }

          return {
            mode: 'multi',
            lookbacks: raw.lookbacks,
            lookbackKeys,
            models: discoveredModels,
            horizons: Array.isArray(raw.horizons) && raw.horizons.length > 0 ? raw.horizons : empty.horizons,
            legacy: {},
          };
        }

        return {
          mode: 'single',
          lookbacks: {},
          lookbackKeys: [],
          models: ['GARCH(1,1)'],
          horizons: empty.horizons,
          legacy: raw,
        };
      }

      function forecastForTicker(bundle, ticker) {
        if (!bundle || typeof bundle !== 'object') return {};
        const tickerKey = String(ticker || '').toUpperCase();

        if (bundle.mode === 'multi') {
          const selectedLookback = lookbackEl && lookbackEl.value ? lookbackEl.value : (bundle.lookbackKeys[0] || '');
          const selectedModel = modelEl && modelEl.value ? modelEl.value : (bundle.models[0] || '');
          const lbMap = bundle.lookbacks[selectedLookback] || {};
          const tMap = lbMap[tickerKey] || lbMap[ticker] || null;
          if (!tMap || typeof tMap !== 'object') return {};

          if (selectedModel && tMap[selectedModel] && typeof tMap[selectedModel] === 'object') {
            return tMap[selectedModel];
          }

          const firstModel = Object.keys(tMap)[0];
          return firstModel ? (tMap[firstModel] || {}) : {};
        }

        if (bundle.mode === 'single') {
          const legacy = bundle.legacy || {};
          return legacy[tickerKey] || legacy[ticker] || {};
        }

        return {};
      }

      function ensureSelectOptions(selectEl, values, storageKey) {
        if (!selectEl) return '';
        const cleaned = Array.isArray(values)
          ? values.map((v) => String(v || '').trim()).filter((v) => v)
          : [];

        if (cleaned.length === 0) {
          selectEl.innerHTML = '<option value="">-</option>';
          selectEl.disabled = true;
          return '';
        }

        const previous = selectEl.value || localStorage.getItem(storageKey) || '';
        selectEl.innerHTML = cleaned.map((v) => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
        selectEl.disabled = false;
        selectEl.value = cleaned.includes(previous) ? previous : cleaned[0];
        try { localStorage.setItem(storageKey, selectEl.value); } catch (_) {}
        return selectEl.value;
      }

      function renderEmpty(message) {
        bodyEl.innerHTML = '<tr><td class="options-empty" colspan="11">' + esc(message) + '</td></tr>';
      }

      function valueBadgeHtml(rawValue, min, max) {
        const v = parseRvNumber(rawValue);
        if (v === null) return '<span class="rv-empty">-</span>';

        const spread = Number.isFinite(max - min) && (max - min) > 0 ? (max - min) : 1;
        const ratio = Math.max(0, Math.min(1, (v - min) / spread));
        const hue = Math.round(155 - (ratio * 130));
        const bg = 'hsl(' + hue + ' 72% 91%)';
        const border = 'hsl(' + hue + ' 48% 70%)';
        const text = 'hsl(' + hue + ' 55% 23%)';
        return '<span class="rv-value" style="background:' + bg + '; border-color:' + border + '; color:' + text + '">' + v.toFixed(2) + '%</span>';
      }

      function renderTable(rows, forecastBundle) {
        if (!Array.isArray(rows) || rows.length === 0) {
          renderEmpty('No realized volatility rows available.');
          return;
        }

        // Sort rows based on current sort state
        const state = window.__rvSortState || { column: 'ticker', direction: 'asc' };
        const sortedRows = [...rows].sort((a, b) => {
          let aVal, bVal;

          if (state.column === 'ticker') {
            aVal = String(a.Ticker || '').toUpperCase();
            bVal = String(b.Ticker || '').toUpperCase();
            const cmp = aVal.localeCompare(bVal);
            return state.direction === 'asc' ? cmp : -cmp;
          }

          // For numeric columns (15D, 30D, 60D, 90D, 180D)
          const colMap = { '15D': '15D RV', '30D': '30D RV', '60D': '60D RV', '90D': '90D RV', '180D': '180D RV' };
          const colName = colMap[state.column] || state.column;
          aVal = parseRvNumber(a[colName]);
          bVal = parseRvNumber(b[colName]);

          // Handle nulls: treat as -Infinity for sorting (they go to the end in asc, beginning in desc)
          const aMissing = aVal === null;
          const bMissing = bVal === null;
          if (aMissing && bMissing) return 0;
          if (aMissing) return state.direction === 'asc' ? 1 : -1;
          if (bMissing) return state.direction === 'asc' ? -1 : 1;

          const cmp = aVal - bVal;
          return state.direction === 'asc' ? cmp : -cmp;
        });

        const metrics = ['15D RV', '30D RV', '60D RV', '90D RV', '180D RV'];
        const fcstMetrics = ['15D (%)', '30D (%)', '60D (%)', '90D (%)', '180D (%)'];

        bodyEl.innerHTML = sortedRows.map((row) => {
          const ticker = esc(row.Ticker || '-');
          
          // Calculate min/max for this row across all 5 windows
          const rowValues = metrics.map((key) => parseRvNumber(row[key])).filter((n) => n !== null);
          const rowMin = rowValues.length ? Math.min(...rowValues) : 0;
          const rowMax = rowValues.length ? Math.max(...rowValues) : 1;
          
          // Get forecast data for this ticker if available
          const forecastRow = forecastForTicker(forecastBundle, ticker);
          
          let cellsHtml = '';
          for (let i = 0; i < metrics.length; i++) {
            const rvKey = metrics[i];
            const fcstKey = fcstMetrics[i];
            const rvCell = '<td>' + valueBadgeHtml(row[rvKey], rowMin, rowMax) + '</td>';
            const fcstVal = forecastRow[fcstKey];
            const fcstCell = '<td class="rv-fcst-col">' + valueBadgeHtml(fcstVal, rowMin, rowMax) + '</td>';
            cellsHtml += rvCell + fcstCell;
          }
          
          return '<tr><td class="rv-ticker">' + ticker + '</td>' + cellsHtml + '</tr>';
        }).join('');
      }

      async function load(forceRefresh) {
        if (statusEl) statusEl.textContent = 'Loading realized vols...';
        if (refreshBtn) refreshBtn.disabled = true;

        try {
          const qs = forceRefresh ? '?refresh=1' : '';
          const resp = await fetch('/api/realized-vols' + qs);
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const payload = await resp.json();
          if (!payload || !payload.ok) throw new Error(payload && payload.error ? payload.error : 'Unknown API error');

          // Store rows for sorting/re-rendering
          window.__rvCurrentRows = payload.rows || [];
          window.__rvForecastBundle = normalizeForecastBundle(payload.forecasts || {});

          if (window.__rvForecastBundle.mode === 'multi') {
            ensureSelectOptions(lookbackEl, window.__rvForecastBundle.lookbackKeys, 'rvSelectedLookback');
            ensureSelectOptions(modelEl, window.__rvForecastBundle.models, 'rvSelectedModel');
          } else {
            ensureSelectOptions(lookbackEl, ['Default'], 'rvSelectedLookback');
            ensureSelectOptions(modelEl, window.__rvForecastBundle.models.length ? window.__rvForecastBundle.models : ['GARCH(1,1)'], 'rvSelectedModel');
          }
          
          renderTable(window.__rvCurrentRows, window.__rvForecastBundle);
          syncRvSortHeaders();
          if (updatedEl) updatedEl.textContent = fmtTs(payload.generated_at || payload.generated_at_iso || null);
          if (statusEl) {
            const sourceLabel = payload.stale ? 'Showing cached data.' : 'Live data loaded.';
            const selectedLb = lookbackEl && lookbackEl.value ? lookbackEl.value : '-';
            const selectedModel = modelEl && modelEl.value ? modelEl.value : '-';
            statusEl.textContent = sourceLabel + ' Tickers: ' + String(Array.isArray(payload.rows) ? payload.rows.length : 0) + '. Forecast: ' + selectedLb + ' / ' + selectedModel;
          }
        } catch (err) {
          renderEmpty('Failed to load realized vols.');
          if (statusEl) statusEl.textContent = 'Load error: ' + esc(err && err.message ? err.message : 'unknown error');
        } finally {
          if (refreshBtn) refreshBtn.disabled = false;
        }
      }

      const toggleForecastBtn = document.getElementById('rvToggleForecastBtn');

      if (toggleForecastBtn) {
        // Restore persisted state
        const _fcstHidden = localStorage.getItem('rvForecastsHidden') === '1';
        if (_fcstHidden) {
          root.classList.add('rv-forecasts-hidden');
          toggleForecastBtn.textContent = 'Show Forecasts';
        }
        toggleForecastBtn.addEventListener('click', () => {
          const hidden = root.classList.toggle('rv-forecasts-hidden');
          toggleForecastBtn.textContent = hidden ? 'Show Forecasts' : 'Hide Forecasts';
          try { localStorage.setItem('rvForecastsHidden', hidden ? '1' : '0'); } catch(_) {}
        });
      }

      if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
          load(true);
        });
      }

      if (resetSortBtn) {
        resetSortBtn.addEventListener('click', () => {
          resetRvSort();
          // Re-render the current rows with the new sort order
          if (window.__rvCurrentRows) {
            renderTable(window.__rvCurrentRows, window.__rvForecastBundle);
          }
        });
      }

      if (lookbackEl) {
        lookbackEl.addEventListener('change', () => {
          try { localStorage.setItem('rvSelectedLookback', lookbackEl.value || ''); } catch (_) {}
          if (window.__rvCurrentRows) renderTable(window.__rvCurrentRows, window.__rvForecastBundle);
          if (statusEl) {
            const selectedModel = modelEl && modelEl.value ? modelEl.value : '-';
            statusEl.textContent = 'Forecast: ' + (lookbackEl.value || '-') + ' / ' + selectedModel;
          }
        });
      }

      if (modelEl) {
        modelEl.addEventListener('change', () => {
          try { localStorage.setItem('rvSelectedModel', modelEl.value || ''); } catch (_) {}
          if (window.__rvCurrentRows) renderTable(window.__rvCurrentRows, window.__rvForecastBundle);
          if (statusEl) {
            const selectedLb = lookbackEl && lookbackEl.value ? lookbackEl.value : '-';
            statusEl.textContent = 'Forecast: ' + selectedLb + ' / ' + (modelEl.value || '-');
          }
        });
      }

      // Add click handlers for sort header buttons
      document.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-rv-sort]');
        if (!btn || !root.contains(btn)) return;
        const column = String(btn.getAttribute('data-rv-sort') || '');
        if (!column) return;
        toggleRvSort(column);
        // Re-render the current rows with the new sort order
        if (window.__rvCurrentRows) {
          renderTable(window.__rvCurrentRows, window.__rvForecastBundle);
        }
      });

      load(false);
      syncRvSortHeaders();
    }

    function initVolatilityCurve() {
      const root = document.getElementById('toolsVolCurveCard');
      if (!root) return;

      const refreshBtn = document.getElementById('vcRefreshBtn');
      const modelEl = document.getElementById('vcModelSelect');
      const statusEl = document.getElementById('vcStatus');
      const bodyEl = document.getElementById('vcTableBody');
      const svgEl = document.getElementById('vcSvg');
      const legendModelTextEl = document.getElementById('vcLegendModelText');
      const callModelHeadEl = document.getElementById('vcCallModelHead');
      const putModelHeadEl = document.getElementById('vcPutModelHead');

      const TARGET_TICKER = 'THYAO';
      // Vade sabit kodlanmaz: zincirde gelen en yakin (en kucuk DTM) vade
      // secilir. Sabit bir kod, vade gectiginde ekrani kalici olarak bosaltir.
      function enYakinVade(rows) {
        let secili = null, enKucuk = Infinity;
        for (const r of rows) {
          const d = Number(r && r.dtm);
          const e = String((r && r.expiry) || '');
          if (!e || !Number.isFinite(d) || d < 0) continue;
          if (d < enKucuk) { enKucuk = d; secili = e; }
        }
        return secili;
      }

      function esc(s) {
        return String(s || '')
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      function fmt(v, dp) {
        const n = Number(v);
        return Number.isFinite(n) ? n.toFixed(dp) : '-';
      }

      function mid(a, b) {
        const x = Number(a);
        const y = Number(b);
        const hasX = Number.isFinite(x);
        const hasY = Number.isFinite(y);
        if (hasX && hasY) return (x + y) / 2;
        if (hasX) return x;
        if (hasY) return y;
        return null;
      }

      function normCdf(x) {
        const sign = x < 0 ? -1 : 1;
        const ax = Math.abs(x) / Math.sqrt(2);
        const t = 1 / (1 + 0.3275911 * ax);
        const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741, a4 = -1.453152027, a5 = 1.061405429;
        const erf = 1 - (((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t) * Math.exp(-ax * ax);
        return 0.5 * (1 + sign * erf);
      }

      function bsPrice(type, S, K, r, T, vol) {
        if (!(S > 0) || !(K > 0) || !(T > 0) || !(vol > 0)) return null;
        const sqrtT = Math.sqrt(T);
        const d1 = (Math.log(S / K) + (r + 0.5 * vol * vol) * T) / (vol * sqrtT);
        const d2 = d1 - vol * sqrtT;
        const df = Math.exp(-r * T);
        if (type === 'C') return S * normCdf(d1) - K * df * normCdf(d2);
        if (type === 'P') return K * df * normCdf(-d2) - S * normCdf(-d1);
        return null;
      }

      function impliedVol(type, marketPx, S, K, r, T) {
        if (!(marketPx > 0) || !(S > 0) || !(K > 0) || !(T > 0)) return null;
        let lo = 1e-6;
        let hi = 5.0;
        let pLo = bsPrice(type, S, K, r, T, lo);
        let pHi = bsPrice(type, S, K, r, T, hi);
        if (!Number.isFinite(pLo) || !Number.isFinite(pHi)) return null;
        if (marketPx <= pLo) return lo;
        if (marketPx >= pHi) return hi;
        for (let i = 0; i < 90; i += 1) {
          const midVol = 0.5 * (lo + hi);
          const pm = bsPrice(type, S, K, r, T, midVol);
          if (!Number.isFinite(pm)) return null;
          if (Math.abs(pm - marketPx) < 1e-8) return midVol;
          if (pm > marketPx) hi = midVol;
          else lo = midVol;
        }
        return 0.5 * (lo + hi);
      }

      function clamp(v, lo, hi) {
        return Math.max(lo, Math.min(hi, v));
      }

      function makeRng(seed) {
        let s = (seed | 0) || 1;
        return () => {
          s ^= s << 13;
          s ^= s >> 17;
          s ^= s << 5;
          return (s >>> 0) / 4294967296;
        };
      }

      function c(re, im) { return { re, im }; }
      function cAdd(a, b) { return c(a.re + b.re, a.im + b.im); }
      function cSub(a, b) { return c(a.re - b.re, a.im - b.im); }
      function cMul(a, b) { return c(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re); }
      function cDiv(a, b) {
        const den = b.re * b.re + b.im * b.im;
        return c((a.re * b.re + a.im * b.im) / den, (a.im * b.re - a.re * b.im) / den);
      }
      function cScale(a, k) { return c(a.re * k, a.im * k); }
      function cExp(a) {
        const er = Math.exp(a.re);
        return c(er * Math.cos(a.im), er * Math.sin(a.im));
      }
      function cLog(a) {
        return c(Math.log(Math.hypot(a.re, a.im)), Math.atan2(a.im, a.re));
      }
      function cSqrt(a) {
        const m = Math.hypot(a.re, a.im);
        const re = Math.sqrt((m + a.re) / 2);
        const im = (a.im >= 0 ? 1 : -1) * Math.sqrt(Math.max(0, (m - a.re) / 2));
        return c(re, im);
      }

      function hestonCf(u, S, r, T, p) {
        const i = c(0, 1);
        const iu = cMul(i, u);
        const sigma2 = p.sigma * p.sigma;
        const a = p.kappa * p.theta;

        const beta = cSub(c(p.kappa, 0), cScale(cMul(c(p.rho * p.sigma, 0), iu), 1));
        const uu = cMul(u, u);
        const dInside = cAdd(cMul(beta, beta), cScale(cAdd(uu, iu), sigma2));
        const d = cSqrt(dInside);

        const gNum = cSub(beta, d);
        const gDen = cAdd(beta, d);
        const g = cDiv(gNum, gDen);

        const minusdT = cScale(d, -T);
        const expMinusdT = cExp(minusdT);

        const one = c(1, 0);
        const oneMinusGExp = cSub(one, cMul(g, expMinusdT));
        const oneMinusG = cSub(one, g);
        const logTerm = cLog(cDiv(oneMinusGExp, oneMinusG));

        const C1 = cScale(cMul(iu, c(Math.log(S), 0)), 1);
        const C2 = cMul(iu, c(r * T, 0));
        const C3 = cScale(cSub(cMul(gNum, c(T, 0)), cScale(logTerm, 2)), a / sigma2);
        const C = cAdd(cAdd(C1, C2), C3);

        const Dnum = cMul(gNum, cSub(one, expMinusdT));
        const Dden = cScale(oneMinusGExp, sigma2);
        const D = cDiv(Dnum, Dden);

        return cExp(cAdd(C, cScale(D, p.v0)));
      }

      function simpsonIntegral(fn, a, b, nEven) {
        const n = Math.max(40, nEven + (nEven % 2));
        const h = (b - a) / n;
        let sum = fn(a) + fn(b);
        for (let k = 1; k < n; k += 1) {
          const x = a + k * h;
          sum += (k % 2 === 0 ? 2 : 4) * fn(x);
        }
        return (h / 3) * sum;
      }

      function hestonProb(j, S, K, r, T, p, nInt) {
        const i = c(0, 1);
        const phiMinusI = hestonCf(c(0, -1), S, r, T, p);
        const lnK = Math.log(K);

        const integrand = (uReal) => {
          const u = c(uReal, 0);
          const eTerm = cExp(c(0, -uReal * lnK));
          const phi = j === 1
            ? cDiv(hestonCf(cSub(u, i), S, r, T, p), phiMinusI)
            : hestonCf(u, S, r, T, p);
          const denom = cMul(i, u);
          const value = cDiv(cMul(eTerm, phi), denom);
          return value.re;
        };

        const integral = simpsonIntegral(integrand, 1e-4, 120, nInt || 240);
        return 0.5 + integral / Math.PI;
      }

      function hestonCallPrice(S, K, r, T, p, nInt) {
        if (!(S > 0) || !(K > 0) || !(T > 0)) return null;
        const p1 = hestonProb(1, S, K, r, T, p, nInt);
        const p2 = hestonProb(2, S, K, r, T, p, nInt);
        const price = S * p1 - K * Math.exp(-r * T) * p2;
        return Number.isFinite(price) ? Math.max(0, price) : null;
      }

      // Calibrate Heston (Q-measure) parameters against market implied vols.
      // calibPoints: array of { S, K, r, T, mktIv } where mktIv is in decimal (e.g. 0.45 = 45%).
      // Uses coordinate descent from multiple seeds; fast integration (nInt=60) during search,
      // returning the best-fit { kappa, theta, sigma, rho, v0 }.
      function fitHeston(calibPoints) {
        if (!calibPoints || calibPoints.length < 2) return null;

        const BOUNDS = {
          kappa: [0.2,  7.0],
          theta: [0.005, 0.6],
          sigma: [0.05,  2.0],
          rho:   [-0.98, -0.02],
          v0:    [0.005, 0.8],
        };
        const KEYS = ['kappa', 'theta', 'sigma', 'rho', 'v0'];

        // Mean squared IV error; uses reduced integration (nInt=60) for speed.
        function loss(p) {
          let sum = 0; let count = 0;
          for (const pt of calibPoints) {
            const px = hestonCallPrice(pt.S, pt.K, pt.r, pt.T, p, 60);
            if (!Number.isFinite(px) || px <= 0) { sum += 1; count += 1; continue; }
            const iv = impliedVol('C', px, pt.S, pt.K, pt.r, pt.T);
            if (!Number.isFinite(iv) || iv <= 0) { sum += 1; count += 1; continue; }
            const diff = iv - pt.mktIv;
            sum += diff * diff;
            count += 1;
          }
          return count > 0 ? sum / count : 1e9;
        }

        // Fixed seeds cover different regions of the parameter space.
        const seeds = [
          { kappa: 1.5, theta: 0.08, sigma: 0.40, rho: -0.50, v0: 0.08 },
          { kappa: 2.5, theta: 0.12, sigma: 0.60, rho: -0.65, v0: 0.12 },
          { kappa: 0.5, theta: 0.20, sigma: 1.00, rho: -0.30, v0: 0.20 },
          { kappa: 4.0, theta: 0.05, sigma: 0.80, rho: -0.80, v0: 0.06 },
        ];

        // Estimate a data-driven v0 seed from median market variance.
        const mktVars = calibPoints.map((pt) => pt.mktIv * pt.mktIv).filter(Number.isFinite);
        if (mktVars.length) {
          const mktVars_sorted = mktVars.slice().sort((a, b) => a - b);
          const medVar = mktVars_sorted[Math.floor(mktVars_sorted.length / 2)];
          seeds.push({ kappa: 2.0, theta: medVar, sigma: 0.50, rho: -0.55, v0: medVar });
        }

        // Add random seeds.
        const rand = makeRng(20260429);
        for (let i = 0; i < 6; i += 1) {
          const p = {};
          for (const k of KEYS) { const [lo, hi] = BOUNDS[k]; p[k] = lo + rand() * (hi - lo); }
          seeds.push(p);
        }

        let bestP = seeds[0]; let bestLoss = 1e18;

        for (const seed of seeds) {
          let p = { ...seed };
          // Clamp seed into bounds.
          for (const k of KEYS) { const [lo, hi] = BOUNDS[k]; p[k] = clamp(p[k], lo, hi); }
          let curLoss = loss(p);
          let step = 0.45;

          for (let iter = 0; iter < 55; iter += 1) {
            for (const key of KEYS) {
              const [lo, hi] = BOUNDS[key];
              const delta = step * (hi - lo);
              let bestVal = p[key]; let bestL = curLoss;
              for (const dir of [-1, 1]) {
                const candidate = clamp(p[key] + dir * delta, lo, hi);
                const p2 = { ...p, [key]: candidate };
                const l2 = loss(p2);
                if (l2 < bestL) { bestL = l2; bestVal = candidate; }
              }
              p[key] = bestVal; curLoss = bestL;
            }
            step *= 0.87;
          }

          if (curLoss < bestLoss) { bestLoss = curLoss; bestP = { ...p }; }
        }

        return { params: bestP, rmseIv: Math.sqrt(bestLoss) };
      }

      // SVI total variance model:
      // w(k) = a + b * (rho*(k-m) + sqrt((k-m)^2 + sigma^2))
      // iv = sqrt(w / T), where k = ln(K/F).
      function sviTotalVariance(k, p) {
        const x = k - p.m;
        const core = p.rho * x + Math.sqrt(x * x + p.sigma * p.sigma);
        return p.a + p.b * core;
      }

      function fitSvi(calibPoints) {
        if (!calibPoints || calibPoints.length < 2) return null;

        const BOUNDS = {
          a: [-0.05, 2.0],
          b: [0.001, 3.0],
          rho: [-0.999, 0.999],
          m: [-2.0, 2.0],
          sigma: [0.005, 2.0],
        };
        const KEYS = ['a', 'b', 'rho', 'm', 'sigma'];

        const transformed = calibPoints.map((pt) => {
          const F = pt.S * Math.exp(pt.r * pt.T);
          const k = Math.log(pt.K / F);
          const wMkt = pt.mktIv * pt.mktIv * pt.T;
          return { ...pt, k, wMkt };
        }).filter((x) => Number.isFinite(x.k) && Number.isFinite(x.wMkt) && x.wMkt > 0);

        if (transformed.length < 2) return null;

        function loss(p) {
          let sum = 0; let count = 0;
          for (const pt of transformed) {
            const w = sviTotalVariance(pt.k, p);
            if (!Number.isFinite(w) || w <= 0) {
              sum += 1;
              count += 1;
              continue;
            }
            const iv = Math.sqrt(w / pt.T);
            const diff = iv - pt.mktIv;
            sum += diff * diff;
            count += 1;
          }
          return count > 0 ? sum / count : 1e9;
        }

        // ATM-ish seed from median total variance.
        const wVals = transformed.map((x) => x.wMkt).sort((a, b) => a - b);
        const medW = wVals[Math.floor(wVals.length / 2)] || 0.05;
        const seeds = [
          { a: medW * 0.5, b: 0.2, rho: -0.4, m: 0.0, sigma: 0.2 },
          { a: medW * 0.7, b: 0.3, rho: -0.6, m: 0.0, sigma: 0.3 },
          { a: medW * 0.4, b: 0.4, rho: -0.2, m: 0.1, sigma: 0.15 },
        ];

        const rand = makeRng(20260430);
        for (let i = 0; i < 6; i += 1) {
          const p = {};
          for (const k of KEYS) {
            const [lo, hi] = BOUNDS[k];
            p[k] = lo + rand() * (hi - lo);
          }
          seeds.push(p);
        }

        let bestP = seeds[0];
        let bestLoss = 1e18;

        for (const seed of seeds) {
          const p = { ...seed };
          for (const k of KEYS) {
            const [lo, hi] = BOUNDS[k];
            p[k] = clamp(p[k], lo, hi);
          }
          let curLoss = loss(p);
          let step = 0.45;

          for (let iter = 0; iter < 55; iter += 1) {
            for (const key of KEYS) {
              const [lo, hi] = BOUNDS[key];
              const delta = step * (hi - lo);
              let bestVal = p[key];
              let bestL = curLoss;
              for (const dir of [-1, 1]) {
                const candidate = clamp(p[key] + dir * delta, lo, hi);
                const p2 = { ...p, [key]: candidate };
                const l2 = loss(p2);
                if (l2 < bestL) {
                  bestL = l2;
                  bestVal = candidate;
                }
              }
              p[key] = bestVal;
              curLoss = bestL;
            }
            step *= 0.87;
          }

          if (curLoss < bestLoss) {
            bestLoss = curLoss;
            bestP = { ...p };
          }
        }

        return { params: bestP, rmseIv: Math.sqrt(bestLoss) };
      }

      function sviCallPrice(S, K, r, T, sviParams) {
        const F = S * Math.exp(r * T);
        const k = Math.log(K / F);
        const w = sviTotalVariance(k, sviParams);
        if (!Number.isFinite(w) || w <= 0) return null;
        const iv = Math.sqrt(w / T);
        return bsPrice('C', S, K, r, T, iv);
      }

      function renderCurveSvg(pointsMarket, pointsModel, modelName) {
        if (!svgEl) return;
        const allPoints = [...pointsMarket, ...pointsModel].filter((p) => Number.isFinite(p.k) && Number.isFinite(p.iv));
        if (allPoints.length < 2) {
          svgEl.innerHTML = '<text x="12" y="26" fill="#64748b" font-size="12">Not enough points to render curve.</text>';
          return;
        }

        const w = 960;
        const h = 260;
        const pad = { l: 56, r: 18, t: 18, b: 34 };
        const minK = Math.min(...allPoints.map((p) => p.k));
        const maxK = Math.max(...allPoints.map((p) => p.k));
        const minIv = Math.min(...allPoints.map((p) => p.iv));
        const maxIv = Math.max(...allPoints.map((p) => p.iv));
        const ivLo = Math.max(0, minIv - 3);
        const ivHi = maxIv + 3;
        const x = (k) => pad.l + ((k - minK) / Math.max(1e-9, maxK - minK)) * (w - pad.l - pad.r);
        const y = (iv) => h - pad.b - ((iv - ivLo) / Math.max(1e-9, ivHi - ivLo)) * (h - pad.t - pad.b);
        const path = (pts) => pts
          .filter((p) => Number.isFinite(p.k) && Number.isFinite(p.iv))
          .sort((a, b) => a.k - b.k)
          .map((p, idx) => (idx === 0 ? 'M' : 'L') + x(p.k).toFixed(2) + ' ' + y(p.iv).toFixed(2))
          .join(' ');

        const xTicks = 6;
        const yTicks = 5;
        const xGrid = Array.from({ length: xTicks + 1 }, (_, t) => {
          const kk = minK + (t / xTicks) * (maxK - minK);
          const xx = x(kk);
          return '<line x1="' + xx.toFixed(2) + '" y1="' + pad.t + '" x2="' + xx.toFixed(2) + '" y2="' + (h - pad.b) + '" stroke="#eef2f7"/>'
            + '<text x="' + xx.toFixed(2) + '" y="' + (h - 10) + '" text-anchor="middle" fill="#64748b" font-size="11">' + kk.toFixed(0) + '</text>';
        }).join('');
        const yGrid = Array.from({ length: yTicks + 1 }, (_, t) => {
          const vv = ivLo + (t / yTicks) * (ivHi - ivLo);
          const yy = y(vv);
          return '<line x1="' + pad.l + '" y1="' + yy.toFixed(2) + '" x2="' + (w - pad.r) + '" y2="' + yy.toFixed(2) + '" stroke="#eef2f7"/>'
            + '<text x="' + (pad.l - 8) + '" y="' + (yy + 4).toFixed(2) + '" text-anchor="end" fill="#64748b" font-size="11">' + vv.toFixed(1) + '%</text>';
        }).join('');

        svgEl.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
        svgEl.innerHTML = ''
          + '<rect x="0" y="0" width="' + w + '" height="' + h + '" fill="#ffffff"/>'
          + xGrid + yGrid
          + '<line x1="' + pad.l + '" y1="' + (h - pad.b) + '" x2="' + (w - pad.r) + '" y2="' + (h - pad.b) + '" stroke="#cfd5de"/>'
          + '<line x1="' + pad.l + '" y1="' + pad.t + '" x2="' + pad.l + '" y2="' + (h - pad.b) + '" stroke="#cfd5de"/>'
          + '<path d="' + path(pointsMarket) + '" fill="none" stroke="#0f1728" stroke-width="2"/>'
            + '<path d="' + path(pointsModel) + '" fill="none" stroke="#dc2626" stroke-width="2"/>'
          + '<text x="' + (w / 2) + '" y="' + (h - 4) + '" text-anchor="middle" fill="#334155" font-size="11">Strike</text>'
            + '<text x="12" y="' + (h / 2) + '" fill="#334155" font-size="11" transform="rotate(-90 12 ' + (h / 2) + ')">Implied Vol (%) - ' + esc(modelName || 'Model') + '</text>';
      }

      function rowMarketIv(row, type, px, S, K, r, T) {
        const bidIv = Number(type === 'C' ? row?.call_bid_iv : row?.put_bid_iv);
        const askIv = Number(type === 'C' ? row?.call_ask_iv : row?.put_ask_iv);
        const vals = [bidIv, askIv].filter((v) => Number.isFinite(v) && v > 0);
        if (vals.length) return vals.reduce((a, b) => a + b, 0) / vals.length;
        return impliedVol(type, px, S, K, r, T);
      }

      async function loadVolCurve() {
        const selectedModel = modelEl ? String(modelEl.value || 'heston').toLowerCase() : 'heston';
        const modelLabel = selectedModel === 'svi' ? 'SVI' : 'Heston';
        if (legendModelTextEl) legendModelTextEl.textContent = modelLabel + ' IV';
        if (callModelHeadEl) callModelHeadEl.textContent = 'Call ' + modelLabel + ' IV';
        if (putModelHeadEl) putModelHeadEl.textContent = 'Put ' + modelLabel + ' IV';

        if (statusEl) statusEl.textContent = 'Loading THYAO chain...';
        if (refreshBtn) refreshBtn.disabled = true;
        try {
          const resp = await fetch('/api/options-chain?ticker=' + TARGET_TICKER);
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const data = await resp.json();
          const rawRows = Array.isArray(data && data.options) ? data.options : [];
          const vade = enYakinVade(rawRows);
          const rows = rawRows.filter((r) => String(r?.expiry || '') === vade)
            .sort((a, b) => Number(a?.strike || 0) - Number(b?.strike || 0));

          if (!rows.length) {
            if (bodyEl) bodyEl.innerHTML = '<tr><td class="options-empty" colspan="9">No THYAO option rows available.</td></tr>';
            renderCurveSvg([], [], modelLabel);
            if (statusEl) statusEl.textContent = 'No THYAO rows yet. Keep the data source running.';
            return;
          }

          // --- Step 1: collect market IVs for calibration ---
          const calibPoints = [];
          rows.forEach((row) => {
            const K = Number(row?.strike);
            const S = Number(row?.spot_mid);
            const r = Number(row?.rate);
            const dtm = Number(row?.dtm);
            const T = Number.isFinite(dtm) && dtm > 0 ? dtm / 365 : null;
            if (!(Number.isFinite(K) && Number.isFinite(S) && Number.isFinite(r) && Number.isFinite(T) && T > 0)) return;
            const cMid = mid(row?.call_bid_price, row?.call_ask_price);
            const iv = Number.isFinite(cMid) ? rowMarketIv(row, 'C', cMid, S, K, r, T) : null;
            if (Number.isFinite(iv) && iv > 0) calibPoints.push({ S, K, r, T, mktIv: iv });
          });

          // --- Step 2: calibrate selected model against market IVs ---
          if (statusEl) statusEl.textContent = 'Calibrating ' + modelLabel + ' to ' + calibPoints.length + ' market IV points...';
          let hestonFit = null;
          let sviFit = null;
          if (calibPoints.length >= 2) {
            if (selectedModel === 'svi') {
              sviFit = fitSvi(calibPoints);
            } else {
              hestonFit = fitHeston(calibPoints);
            }
          }
          const HESTON = hestonFit
            ? hestonFit.params
            : { kappa: 2.0, theta: 0.10, sigma: 0.50, rho: -0.50, v0: 0.10 };
          const SVI = sviFit
            ? sviFit.params
            : { a: 0.02, b: 0.2, rho: -0.4, m: 0.0, sigma: 0.2 };

          // --- Step 3: build output rows using calibrated params ---
          const outRows = [];
          const marketPoints = [];
          const modelPoints = [];

          rows.forEach((row) => {
            const K = Number(row?.strike);
            const S = Number(row?.spot_mid);
            const r = Number(row?.rate);
            const dtm = Number(row?.dtm);
            const T = Number.isFinite(dtm) && dtm > 0 ? dtm / 365 : null;
            if (!(Number.isFinite(K) && Number.isFinite(S) && Number.isFinite(r) && Number.isFinite(T) && T > 0)) return;

            const cMid = mid(row?.call_bid_price, row?.call_ask_price);
            const pMid = mid(row?.put_bid_price, row?.put_ask_price);

            const cModelPx = selectedModel === 'svi'
              ? sviCallPrice(S, K, r, T, SVI)
              : hestonCallPrice(S, K, r, T, HESTON);
            const pModelPx = Number.isFinite(cModelPx) ? (cModelPx - S + K * Math.exp(-r * T)) : null;

            const cMktIv = Number.isFinite(cMid) ? rowMarketIv(row, 'C', cMid, S, K, r, T) : null;
            const pMktIv = Number.isFinite(pMid) ? rowMarketIv(row, 'P', pMid, S, K, r, T) : null;
            const cHesIv = Number.isFinite(cModelPx) ? impliedVol('C', cModelPx, S, K, r, T) : null;
            const pHesIv = Number.isFinite(pModelPx) ? impliedVol('P', Math.max(1e-8, pModelPx), S, K, r, T) : null;

            const mktIvPct = Number.isFinite(cMktIv) ? cMktIv * 100 : (Number.isFinite(pMktIv) ? pMktIv * 100 : null);
            const hesIvPct = Number.isFinite(cHesIv) ? cHesIv * 100 : (Number.isFinite(pHesIv) ? pHesIv * 100 : null);
            if (Number.isFinite(mktIvPct)) marketPoints.push({ k: K, iv: mktIvPct });
            if (Number.isFinite(hesIvPct)) modelPoints.push({ k: K, iv: hesIvPct });

            outRows.push({ K, cMid, cMktIv, cHesIv, pMid, pMktIv, pHesIv, dtm, r });
          });

          if (bodyEl) {
            if (!outRows.length) {
              bodyEl.innerHTML = '<tr><td class="options-empty" colspan="9">Rows are present but missing spot/rate/dtm inputs.</td></tr>';
            } else {
              bodyEl.innerHTML = outRows.map((x) => {
                return '<tr>'
                  + '<td><b>' + esc(fmt(x.K, 2)) + '</b></td>'
                  + '<td>' + esc(fmt(x.cMid, 4)) + '</td>'
                  + '<td>' + esc(Number.isFinite(x.cMktIv) ? fmt(x.cMktIv * 100, 2) + '%' : '-') + '</td>'
                  + '<td>' + esc(Number.isFinite(x.cHesIv) ? fmt(x.cHesIv * 100, 2) + '%' : '-') + '</td>'
                  + '<td>' + esc(fmt(x.pMid, 4)) + '</td>'
                  + '<td>' + esc(Number.isFinite(x.pMktIv) ? fmt(x.pMktIv * 100, 2) + '%' : '-') + '</td>'
                  + '<td>' + esc(Number.isFinite(x.pHesIv) ? fmt(x.pHesIv * 100, 2) + '%' : '-') + '</td>'
                  + '<td>' + esc(String(Math.round(x.dtm))) + '</td>'
                  + '<td>' + esc(fmt(x.r * 100, 2) + '%') + '</td>'
                  + '</tr>';
              }).join('');
            }
          }

          renderCurveSvg(marketPoints, modelPoints, modelLabel);
          if (statusEl) {
            if (selectedModel === 'svi') {
              const rmseStr = sviFit ? ' | IV RMSE: ' + (sviFit.rmseIv * 100).toFixed(2) + '%' : ' (fallback params)';
              statusEl.textContent = 'Calibrated SVI to ' + calibPoints.length + ' IV points' + rmseStr
                + ' | a=' + SVI.a.toFixed(3)
                + ' b=' + SVI.b.toFixed(3)
                + ' rho=' + SVI.rho.toFixed(2)
                + ' m=' + SVI.m.toFixed(3)
                + ' sigma=' + SVI.sigma.toFixed(3);
            } else {
              const rmseStr = hestonFit ? ' | IV RMSE: ' + (hestonFit.rmseIv * 100).toFixed(2) + '%' : ' (fallback params)';
              statusEl.textContent = 'Calibrated Heston to ' + calibPoints.length + ' IV points' + rmseStr
                + ' | kappa=' + HESTON.kappa.toFixed(2)
                + ' theta=' + HESTON.theta.toFixed(3)
                + ' sigma=' + HESTON.sigma.toFixed(2)
                + ' rho=' + HESTON.rho.toFixed(2)
                + ' v0=' + HESTON.v0.toFixed(3);
            }
          }
        } catch (err) {
          if (bodyEl) bodyEl.innerHTML = '<tr><td class="options-empty" colspan="9">Failed to load volatility curve data.</td></tr>';
          renderCurveSvg([], [], modelEl && String(modelEl.value || '').toLowerCase() === 'svi' ? 'SVI' : 'Heston');
          if (statusEl) statusEl.textContent = 'Load error: ' + esc(err && err.message ? err.message : 'unknown');
        } finally {
          if (refreshBtn) refreshBtn.disabled = false;
        }
      }

      if (refreshBtn) refreshBtn.addEventListener('click', loadVolCurve);
      if (modelEl) modelEl.addEventListener('change', loadVolCurve);
      loadVolCurve();
      setInterval(loadVolCurve, 10000);
    }

    function getCollapsedExpiries(ticker) {
      const key = String(ticker || '').trim().toUpperCase();
      if (!window.__collapsedExpiriesByTicker[key]) {
        window.__collapsedExpiriesByTicker[key] = {};
      }
      return window.__collapsedExpiriesByTicker[key];
    }

    function getOptionsSortState(ticker) {
      const key = String(ticker || '').trim().toUpperCase();
      return window.__optionsSortByTicker[key] || null;
    }

    function setOptionsSortState(ticker, sortState) {
      const key = String(ticker || '').trim().toUpperCase();
      if (!key) return;
      if (!sortState || !sortState.key || !sortState.dir) {
        delete window.__optionsSortByTicker[key];
        return;
      }
      window.__optionsSortByTicker[key] = sortState;
    }

    function getOptionComparableValue(row, key) {
      if (key === 'expiry') {
        return row?.expiry ? String(row.expiry) : null;
      }
      const value = row ? row[key] : null;
      if (value === null || value === undefined || value === '') {
        return null;
      }
      const num = Number(value);
      return Number.isFinite(num) ? num : String(value);
    }

    function compareOptionValues(aValue, bValue, dir) {
      const multiplier = dir === 'desc' ? -1 : 1;
      const aMissing = aValue === null || aValue === undefined;
      const bMissing = bValue === null || bValue === undefined;
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;
      if (bMissing) return -1;
      if (typeof aValue === 'number' && typeof bValue === 'number') {
        return (aValue - bValue) * multiplier;
      }
      return String(aValue).localeCompare(String(bValue)) * multiplier;
    }

    function buildOptionGroups(options, sortState) {
      const grouped = new Map();
      options.forEach((row, index) => {
        const expiry = row && row.expiry ? String(row.expiry) : '';
        if (!expiry) return;
        const normalizedRow = { ...row, __default_index: index };
        if (!grouped.has(expiry)) {
          grouped.set(expiry, { expiry, groupIndex: index, rows: [] });
        }
        grouped.get(expiry).rows.push(normalizedRow);
      });

      const groups = Array.from(grouped.values());
      if (sortState && sortState.key === 'expiry') {
        groups.sort((a, b) => {
          const cmp = compareOptionValues(a.expiry, b.expiry, sortState.dir);
          return cmp !== 0 ? cmp : a.groupIndex - b.groupIndex;
        });
      } else {
        groups.sort((a, b) => a.groupIndex - b.groupIndex);
      }

      groups.forEach((group) => {
        if (sortState && sortState.key && sortState.key !== 'expiry') {
          group.rows.sort((a, b) => {
            const cmp = compareOptionValues(
              getOptionComparableValue(a, sortState.key),
              getOptionComparableValue(b, sortState.key),
              sortState.dir,
            );
            return cmp !== 0 ? cmp : a.__default_index - b.__default_index;
          });
        } else {
          group.rows.sort((a, b) => a.__default_index - b.__default_index);
        }
      });

      return groups;
    }

    function initDiscount() {
      var root = document.getElementById('toolsDiscountCard');
      if (!root) return;
      nssGoster();
      depoGoster();
      var DISC_STORAGE_KEY = 'discountFactors';
      function loadFactors() {
        try { var r = localStorage.getItem(DISC_STORAGE_KEY); return r ? JSON.parse(r) : {}; } catch(_) { return {}; }
      }
      function saveFactors(data) {
        try { localStorage.setItem(DISC_STORAGE_KEY, JSON.stringify(data)); } catch(_) {}
      }
      var stored = loadFactors();
      root.querySelectorAll('.disc-rate-input').forEach(function(input) {
        var m = input.getAttribute('data-month');
        if (stored[m] !== undefined) input.value = stored[m];
      });
      function persistAll() {
        var data = {};
        root.querySelectorAll('.disc-rate-input').forEach(function(input) {
          var m = input.getAttribute('data-month');
          var v = (input.value || '').trim();
          if (v !== '') data[m] = v;
        });
        saveFactors(data);
        window.__discountFactors = data;
      }
      window.__discountFactors = stored;
      root.querySelectorAll('.disc-rate-input').forEach(function(input) {
        input.addEventListener('input', persistAll);
        input.addEventListener('change', persistAll);
      });
    }

    // Uydurulmus NSS egrisini gosterir. Egri fit_curve.py tarafindan
    // hesaplanip /api/yield-curve ucuna gonderilir; burada yalnizca okunur.
    function nssGoster() {
      var durum = document.getElementById('nssStatus');
      var govde = document.getElementById('nssBody');
      if (!durum) return;
      fetch('/api/yield-curve').then(function (r) { return r.json(); }).then(function (d) {
        var c = d && d.curve;
        if (!c || !c.params) {
          durum.textContent = 'no fitted curve yet — run: python3 fit_curve.py';
          if (govde) govde.style.display = 'none';
          return;
        }
        var p = c.params;
        var adi = p.model === 'NSS' ? 'Nelson-Siegel-Svensson' : 'Nelson-Siegel';
        var metin = adi + ' \u00b7 ' + p.nokta_sayisi + ' observed tenors \u00b7 fitted '
                  + String(c.ts || '').slice(11, 19);
        if (c.en_uzun_gozlem_gun) {
          metin += ' \u00b7 valid to ~' + c.gecerli_azami_gun + 'd'
                 + ' (longest observed ' + c.en_uzun_gozlem_gun + 'd)';
        }
        if (p.tam_belirlenmis) {
          // Parametre sayisi gozlem sayisina esit: uyum zorunlu olarak tam
          // cikar, RMSE kalite olcusu degildir. Bunu gizlemek yaniltici olur.
          metin += ' \u00b7 exactly determined (RMSE is not a fit-quality measure)';
          durum.style.color = '#b45309';
        } else {
          metin += ' \u00b7 RMSE ' + (p.rmse * 100).toFixed(3) + ' pts';
          durum.style.color = '#64748b';
        }
        durum.textContent = metin;
        document.getElementById('nssParams').textContent =
          'b0=' + p.b0.toFixed(4) + '  b1=' + p.b1.toFixed(4)
          + '  b2=' + p.b2.toFixed(4) + '  b3=' + p.b3.toFixed(4)
          + '  lambda1=' + p.l1 + '  lambda2=' + p.l2;

        var bas = '<th style="padding:4px 10px;text-align:left;">DTM</th>';
        var sat = '<td style="padding:4px 10px;font-weight:600;">Rate</td>';
        (c.curve || []).forEach(function (pt) {
          bas += '<th style="padding:4px 10px;text-align:right;">' + pt.dtm + 'd</th>';
          sat += '<td style="padding:4px 10px;text-align:right;">'
               + (pt.rate * 100).toFixed(2) + '%</td>';
        });
        document.getElementById('nssHead').innerHTML = bas;
        document.getElementById('nssRow').innerHTML = sat;
        if (govde) govde.style.display = '';
      }).catch(function () { durum.textContent = 'unreachable'; });
    }

    // SQLite deposunun durumu ve model parametre surumleri.
    // Veritabanini Python tarafi tutar; buraya store.py --push-stats'in
    // gonderdigi ozet gelir. Ozet hic gelmediyse bunu gizlemek yerine
    // "nasil doldurulur" yazilir.
    function depoGoster() {
      var durum = document.getElementById('depoStatus');
      var govde = document.getElementById('depoBody');
      if (!durum) return;
      fetch('/api/store-stats').then(function (r) { return r.json(); }).then(function (d) {
        var s = d && d.stats;
        if (!s) {
          durum.textContent = 'no store report yet — run: python3 store.py --push-stats';
          durum.style.color = '#64748b';
          if (govde) govde.style.display = 'none';
          return;
        }
        // WAL gecicidir, denetim noktasinda kuculur: kalici boyutla
        // toplanmasi depoyu oldugundan buyuk gosteriyordu.
        var mb = (Number(s.db_bytes || 0) / 1048576).toFixed(2);
        var wal = (Number(s.wal_bytes || 0) / 1048576).toFixed(2);
        var gun = (s.first_day && s.last_day)
          ? (s.first_day === s.last_day ? s.first_day : s.first_day + ' → ' + s.last_day)
          : 'no daily bars yet';
        durum.textContent = s.spot_days + ' daily bar(s) over ' + s.spot_tickers
          + ' ticker(s) · ' + gun + ' · ' + mb + ' MB (+' + wal
          + ' MB WAL) · mode ' + s.data_mode;
        durum.style.color = s.write_error ? '#dc2626' : '#64748b';
        if (s.write_error) durum.textContent += ' · WRITE ERROR: ' + s.write_error;

        document.getElementById('depoCounts').textContent =
          'spot_ticks=' + s.spot_ticks + '  futures_ticks=' + s.futures_ticks
          + '  option_quotes=' + s.option_quotes + '  model_versions=' + s.model_versions
          + '  db=' + s.db_path;

        var satirlar = (s.versions || []).map(function (v) {
          var q = (v.fit_quality === null || v.fit_quality === undefined)
            ? '-' : Number(v.fit_quality).toFixed(6);
          return '<tr>'
            + '<td style="padding:3px 10px;">#' + v.id + '</td>'
            + '<td style="padding:3px 10px;">' + String(v.ts || '').replace('T', ' ').slice(0, 19) + '</td>'
            + '<td style="padding:3px 10px;font-weight:600;">' + v.model + '</td>'
            + '<td style="padding:3px 10px;">' + (v.scope || '—') + '</td>'
            + '<td style="padding:3px 10px;text-align:right;">' + q + '</td>'
            + '<td style="padding:3px 10px;">' + v.data_mode + '</td>'
            + '</tr>';
        }).join('');
        document.getElementById('depoVersions').innerHTML = satirlar
          || '<tr><td colspan="6" style="padding:6px 10px;color:#64748b;">no model versions recorded yet</td></tr>';
        if (govde) govde.style.display = '';
      }).catch(function () { durum.textContent = 'unreachable'; });
    }
    window.depoYenile = depoGoster;

    function initDividends() {
      const root = document.getElementById('toolsDividendsCard');
      if (!root) return;

      function saveDividends(data) {
        fetch('/api/dividends', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        }).then(() => {
          window.__dividendsByTicker = data;
          refreshSpotAndRates();
        }).catch(() => {});
      }

      // Populate inputs from server-injected data
      const stored = window.__dividendsByTicker || {};
      root.querySelectorAll('.div-amount-input').forEach((input) => {
        const ticker = input.getAttribute('data-div-ticker');
        const entry = stored[ticker];
        if (entry && (entry.amount !== undefined && entry.amount !== null && entry.amount !== '')) {
          input.value = entry.amount;
        }
      });
      root.querySelectorAll('.div-date-input').forEach((input) => {
        const ticker = input.getAttribute('data-div-ticker');
        const entry = stored[ticker];
        if (entry && entry.date) {
          input.value = entry.date;
        }
      });

      function persistAll() {
        const data = {};
        root.querySelectorAll('tr[data-div-ticker]').forEach((row) => {
          const ticker = row.getAttribute('data-div-ticker');
          const amtInput = row.querySelector('.div-amount-input');
          const dateInput = row.querySelector('.div-date-input');
          const amt = (amtInput ? amtInput.value : '').trim();
          const dt = (dateInput ? dateInput.value : '').trim();
          if (amt || dt) {
            data[ticker] = { amount: amt, date: dt };
          }
        });
        saveDividends(data);
        window.__dividendsByTicker = data;
      }

      // Initialize global cache
      window.__dividendsByTicker = stored;

      root.querySelectorAll('.div-amount-input, .div-date-input').forEach((input) => {
        input.addEventListener('input', persistAll);
        input.addEventListener('change', persistAll);
      });

      root.querySelectorAll('.div-clear-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          const ticker = btn.getAttribute('data-div-ticker');
          const row = root.querySelector('tr[data-div-ticker="' + ticker + '"]');
          if (row) {
            const amt = row.querySelector('.div-amount-input');
            const dt = row.querySelector('.div-date-input');
            if (amt) amt.value = '';
            if (dt) dt.value = '';
          }
          persistAll();
        });
      });

      const clearAllBtn = document.getElementById('divClearAllBtn');
      if (clearAllBtn) {
        clearAllBtn.addEventListener('click', () => {
          root.querySelectorAll('.div-amount-input, .div-date-input').forEach((el) => { el.value = ''; });
          persistAll();
        });
      }
    }

    function syncOptionSortHeaders() {
      if (!optionsSelectedTicker) return;
      const ticker = (optionsSelectedTicker.getAttribute('data-ticker') || '').trim().toUpperCase();
      const sortState = getOptionsSortState(ticker);
      document.querySelectorAll('[data-option-sort]').forEach((button) => {
        const key = String(button.getAttribute('data-option-sort') || '');
        const active = !!sortState && sortState.key === key;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
        const indicator = button.querySelector('.sort-indicator');
        if (indicator) {
          indicator.innerHTML = active
            ? (sortState.dir === 'desc' ? '&#8595;' : '&#8593;')
            : '&#8597;';
        }
      });
    }

    function refreshSpotAndRates() {
      fetch('/api/spot?all=1').then((r) => r.json()).then((spotData) => {
        const spotMap = (spotData && spotData.spotByTicker) ? spotData.spotByTicker : {};
        document.querySelectorAll('.spot-cell').forEach((cell) => {
          const t = cell.getAttribute('data-ticker') || '';
          const val = spotMap[t] && typeof spotMap[t].spot_mid !== 'undefined' ? spotMap[t].spot_mid : null;
          cell.textContent = formatSpotClient(val);
        });
      }).catch(() => {});

      Promise.all([
        fetch('/api/futures-rates?all=1').then((r) => r.json()),
        fetch('/api/dividends').then((r) => r.json()),
      ]).then(([rateData, divData]) => {
        if (divData && typeof divData === 'object') {
          window.__dividendsByTicker = divData;
        }
        const rateMap = (rateData && rateData.rates_by_ticker) ? rateData.rates_by_ticker : {};
        const fairByCode = {};
        document.querySelectorAll('.maturity-fair-input').forEach((inputEl) => {
          const code = (inputEl.getAttribute('data-code') || '').trim();
          const n = Number(inputEl.value);
          if (code && Number.isFinite(n) && n !== 0) {
            fairByCode[code] = n;
          }
        });
        document.querySelectorAll('.yield-cell').forEach((cell) => {
          const t = cell.getAttribute('data-ticker') || '';
          const code = cell.getAttribute('data-code') || '';
          const side = cell.getAttribute('data-side') || 'bid';
          const r = rateMap[t] && Object.prototype.hasOwnProperty.call(rateMap[t], code) ? rateMap[t][code] : null;
          // Use server-computed dividend-adjusted rate for this side (bid
          // yield from futures bid vs spot bid, ask yield from futures ask
          // vs spot ask) when available.
          const rateField = side === 'ask' ? 'adj_ask_rate' : 'adj_bid_rate';
          const adjustedField = side === 'ask' ? 'adj_ask_adjusted' : 'adj_bid_adjusted';
          const fallbackField = side === 'ask' ? 'ask_rate' : 'bid_rate';
          const adjRate = r && r[rateField] !== undefined ? r[rateField] : (r ? r[fallbackField] : null);
          const isAdjusted = r && r[adjustedField] === true;
          cell.textContent = formatRatePctClient(adjRate);
          cell.classList.remove('rate-above', 'rate-below', 'maturity-danger', 'maturity-safe', 'div-adjusted');
          if (isAdjusted) cell.classList.add('div-adjusted');
          const rPct = adjRate !== null && adjRate !== undefined ? Number(adjRate) * 100 : NaN;
          const fairPct = Object.prototype.hasOwnProperty.call(fairByCode, code) ? fairByCode[code] : NaN;
          if (!Number.isNaN(rPct) && !Number.isNaN(fairPct)) {
            cell.classList.add(rPct > fairPct ? 'rate-above' : 'rate-below');
            if (rPct < (fairPct / 2)) {
              cell.classList.add('maturity-danger');
            } else if (rPct >= (fairPct + 10)) {
              cell.classList.add('maturity-safe');
            }
          }
        });
      }).catch(() => {});
    }

    function expiryLabel(expiryCode) {
      const raw = String(expiryCode || '').trim();
      const digitsOnly = raw.replace(/\D/g, '');
      const code = digitsOnly.length >= 4 ? digitsOnly.slice(0, 4) : digitsOnly.padStart(4, '0');
      const m = /^(\d{2})(\d{2})$/.exec(code);
      if (!m) return raw || '-';
      const mm = Number(m[1]);
      const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      return monthNames[mm - 1] || raw || '-';
    }

    function optionRowHtml(row, hidden, ticker) {
      const fmt = (v, dp) => {
        if (v === null || v === undefined) return '-';
        const n = Number(v);
        if (!Number.isFinite(n) || n === 0) return '-';
        return n.toFixed(dp);
      };
      const fmtSize = (v) => {
        if (v === null || v === undefined) return '-';
        const n = Number(v);
        if (!Number.isFinite(n) || n === 0) return '-';
        return String(n);
      };
      const exp = row?.expiry ? expiryLabel(row.expiry) : '-';
      const str = row?.strike !== null && row?.strike !== undefined ? String(row.strike) : '-';
      const dtm = row?.dtm !== null && row?.dtm !== undefined ? String(row.dtm) : '-';
      const rate = row?.rate !== null && row?.rate !== undefined ? (row.rate * 100).toFixed(1) + '%' : '-';
      const spot = fmt(row?.spot_mid, 3);
      const cbs = fmtSize(row?.call_bid_size);
      const cbp = fmt(row?.call_bid_price, 4);
      const cap = fmt(row?.call_ask_price, 4);
      const cas = fmtSize(row?.call_ask_size);
      const cbi = row?.call_bid_iv !== null && row?.call_bid_iv !== undefined ? (row.call_bid_iv * 100).toFixed(2) + '%' : '-';
      const cai = row?.call_ask_iv !== null && row?.call_ask_iv !== undefined ? (row.call_ask_iv * 100).toFixed(2) + '%' : '-';
      const cdt = row?.call_delta !== null && row?.call_delta !== undefined ? fmt(row.call_delta, 4) : '-';
      const pdt = row?.put_delta !== null && row?.put_delta !== undefined ? fmt(row.put_delta, 4) : '-';
      // Gamma ve theta buyukluk olarak delta'dan kucuk; 5 hane okunakli kaliyor.
      const gre = (v, n) => (v !== null && v !== undefined && Number.isFinite(Number(v)) ? fmt(v, n) : '-');
      const cgm = gre(row?.call_gamma, 5), pgm = gre(row?.put_gamma, 5);
      const cvg = gre(row?.call_vega, 4),  pvg = gre(row?.put_vega, 4);
      const cth = gre(row?.call_theta, 4), pth = gre(row?.put_theta, 4);
      const crh = gre(row?.call_rho, 4),   prh = gre(row?.put_rho, 4);
      const cdv = gre(row?.call_dv01, 6),  pdv = gre(row?.put_dv01, 6);
      const pbi = row?.put_bid_iv !== null && row?.put_bid_iv !== undefined ? (row.put_bid_iv * 100).toFixed(2) + '%' : '-';
      const pai = row?.put_ask_iv !== null && row?.put_ask_iv !== undefined ? (row.put_ask_iv * 100).toFixed(2) + '%' : '-';
      const pbs = fmtSize(row?.put_bid_size);
      const pbp = fmt(row?.put_bid_price, 4);
      const pap = fmt(row?.put_ask_price, 4);
      const pas = fmtSize(row?.put_ask_size);
      const callBidIvNum = Number(row?.call_bid_iv);
      const callAskIvNum = Number(row?.call_ask_iv);
      const putBidIvNum = Number(row?.put_bid_iv);
      const putAskIvNum = Number(row?.put_ask_iv);
      const callIvValues = [callBidIvNum, callAskIvNum].filter((v) => Number.isFinite(v) && v > 0);
      const putIvValues = [putBidIvNum, putAskIvNum].filter((v) => Number.isFinite(v) && v > 0);
      const callIvAttr = callIvValues.length ? String(callIvValues.reduce((acc, v) => acc + v, 0) / callIvValues.length) : '';
      const putIvAttr = putIvValues.length ? String(putIvValues.reduce((acc, v) => acc + v, 0) / putIvValues.length) : '';
      const hiddenAttr = hidden ? ' class="hidden"' : '';
      const dtmAttr = row?.dtm !== null && row?.dtm !== undefined ? String(row.dtm) : '';
      const rateAttr = row?.rate !== null && row?.rate !== undefined ? String(row.rate) : '';
      const maturityAttr = row?.maturity_date || row?.maturity || row?.expiry_date || row?.date || '';
      const callBidIvAttr = Number.isFinite(callBidIvNum) && callBidIvNum > 0 ? String(callBidIvNum) : '';
      const callAskIvAttr = Number.isFinite(callAskIvNum) && callAskIvNum > 0 ? String(callAskIvNum) : '';
      const putBidIvAttr = Number.isFinite(putBidIvNum) && putBidIvNum > 0 ? String(putBidIvNum) : '';
      const putAskIvAttr = Number.isFinite(putAskIvNum) && putAskIvNum > 0 ? String(putAskIvNum) : '';
      const rowAttrs = ' data-option-row="true" data-ticker="' + (ticker || '') + '" data-expiry="' + (row?.expiry || '') + '" data-strike="' + (row?.strike || '') + '" data-dtm="' + dtmAttr + '" data-rate="' + rateAttr + '" data-maturity="' + maturityAttr + '" data-call-iv="' + callIvAttr + '" data-put-iv="' + putIvAttr + '" data-call-bid-iv="' + callBidIvAttr + '" data-call-ask-iv="' + callAskIvAttr + '" data-put-bid-iv="' + putBidIvAttr + '" data-put-ask-iv="' + putAskIvAttr + '" style="cursor: pointer;"';
      return '<tr' + hiddenAttr + rowAttrs + '><td>' + cbs + '</td><td>' + cbp + '</td><td>' + cap + '</td><td>' + cas + '</td><td>' + cbi + '</td><td>' + cai + '</td><td>' + cdt + '</td>'
        + '<td>' + cgm + '</td><td>' + cvg + '</td><td>' + cth + '</td><td>' + crh + '</td><td>' + cdv + '</td>'
        + '<td><span class="exp-chip">' + exp + '</span></td><td>' + str + '</td><td>' + dtm + '</td><td>' + rate + '</td><td>' + spot + '</td>'
        + '<td>' + pdv + '</td><td>' + prh + '</td><td>' + pth + '</td><td>' + pvg + '</td><td>' + pgm + '</td>'
        + '<td>' + pdt + '</td><td>' + pbi + '</td><td>' + pai + '</td><td>' + pbs + '</td><td>' + pbp + '</td><td>' + pap + '</td><td>' + pas + '</td></tr>';
    }

    function optionSeparatorHtml(expiryCode, collapsed) {
      const label = expiryLabel(expiryCode);
      const safeExpiry = String(expiryCode || '');
      const icon = collapsed ? '&#9654;' : '&#9660;';
      return '<tr class="expiry-separator"><td colspan="19"><div class="expiry-sep-wrap"><button type="button" class="expiry-toggle-btn" data-expiry-toggle="' + safeExpiry + '" aria-expanded="' + (!collapsed) + '"><span class="expiry-toggle-icon">' + icon + '</span><span>' + label + '</span></button><span class="expiry-sep-line"></span></div></td></tr>';
    }

    function refreshOptionsChain() {
      if (!optionsBody || !optionsSelectedTicker) return;
      const ticker = (optionsSelectedTicker.getAttribute('data-ticker') || '').trim().toUpperCase();
      if (!ticker) return;

      fetch('/api/options-chain?ticker=' + encodeURIComponent(ticker))
        .then((r) => r.json())
        .then((data) => {
          const options = Array.isArray(data && data.options) ? data.options : [];
          const collapsedExpiries = getCollapsedExpiries(ticker);
          const sortState = getOptionsSortState(ticker);
          const groups = buildOptionGroups(options, sortState);
          window.__optionsExpiriesByTicker[ticker] = groups.map((group) => group.expiry);
          syncOptionSortHeaders();

          if (groups.length === 0) {
            optionsBody.innerHTML = '<tr><td class="options-empty" colspan="19">No rows.</td></tr>';
            return;
          }

          const htmlParts = [];
          groups.forEach((group) => {
            const collapsed = !!collapsedExpiries[group.expiry];
            htmlParts.push(optionSeparatorHtml(group.expiry, collapsed));
            group.rows.forEach((row) => {
              htmlParts.push(optionRowHtml(row, collapsed, ticker));
            });
          });
          optionsBody.innerHTML = htmlParts.join('');
        })
        .catch(() => {});
    }

    if (!window.__optionsControlsBound) {
      document.addEventListener('click', (event) => {
        const clickTarget = event.target instanceof Element
          ? event.target
          : (event.target && event.target.parentElement ? event.target.parentElement : null);
        if (!clickTarget) return;

        // Handle option row click - populate pricer and navigate
        const optionRow = clickTarget.closest('[data-option-row="true"]');
        if (optionRow) {
          const ticker = (optionRow.getAttribute('data-ticker') || '').trim().toUpperCase();
          const expiryCode = (optionRow.getAttribute('data-expiry') || '').trim();
          const strike = (optionRow.getAttribute('data-strike') || '').trim();
          const dtmRaw = (optionRow.getAttribute('data-dtm') || '').trim();
          const rateRaw = (optionRow.getAttribute('data-rate') || '').trim();
          const maturityRaw = (optionRow.getAttribute('data-maturity') || '').trim();
          const callIvRaw = (optionRow.getAttribute('data-call-iv') || '').trim();
          const putIvRaw = (optionRow.getAttribute('data-put-iv') || '').trim();

          if (ticker && expiryCode && strike) {
            // Extract call/put by analyzing which side was clicked
            const cells = Array.from(optionRow.querySelectorAll('td'));
            let clickedIndex = -1;
            for (let i = 0; i < cells.length; i++) {
              if (cells[i].contains(clickTarget)) {
                clickedIndex = i;
                break;
              }
            }

            // Column layout: C Bid Sz, C Bid Px, C Ask Px, C Ask Sz, C Bid IV, C Ask IV, C Delta, Expiry, Strike, DTM, Rate, Spot, P Delta, P Bid IV, P Ask IV, P Bid Sz, P Bid Px, P Ask Px, P Ask Sz
            // Determine if call (columns 0-6) or put (columns 12-18)
            const optionType = clickedIndex >= 0 && clickedIndex <= 6 ? 'Call' : (clickedIndex >= 12 ? 'Put' : 'Call');

            // Convert expiry code (MMYY) to date
            const digitsOnly = String(expiryCode).replace(/\D/g, '');
            const code = digitsOnly.length >= 4 ? digitsOnly.slice(0, 4) : '';
            const m = /^(\d{2})(\d{2})$/.exec(code);
            let dateStr = '';
            if (/^\d{4}-\d{2}-\d{2}$/.test(maturityRaw)) {
              dateStr = maturityRaw;
            }
            if (!dateStr && m) {
              const month = Number(m[1]);
              const year = 2000 + Number(m[2]);
              if (month >= 1 && month <= 12) {
                const lastDay = new Date(year, month, 0).getDate();
                dateStr = year + '-' + String(month).padStart(2, '0') + '-' + String(lastDay).padStart(2, '0');
              }
            }
            if (!dateStr) {
              const dtmNumFallback = Number(dtmRaw);
              if (Number.isFinite(dtmNumFallback)) {
                const today = new Date();
                today.setHours(0, 0, 0, 0);
                const maturityDt = new Date(today.getTime() + Math.max(0, Math.round(dtmNumFallback)) * 86400000);
                dateStr = maturityDt.getFullYear() + '-' + String(maturityDt.getMonth() + 1).padStart(2, '0') + '-' + String(maturityDt.getDate()).padStart(2, '0');
              }
            }

            // Determine bid vs ask based on clicked column
            // Layout: 0=CBidSz,1=CBidPx,2=CAskPx,3=CAskSz,4=CBidIV,5=CAskIV,6=CDelta,7=Expiry,8=Strike,9=DTM,10=Rate,11=Spot,12=PDelta,13=PBidIV,14=PAskIV,15=PBidSz,16=PBidPx,17=PAskPx,18=PAskSz
            const isBidCol = [0, 1, 4, 13, 15, 16].includes(clickedIndex);
            const isAskCol = [2, 3, 5, 14, 17, 18].includes(clickedIndex);
            const callBidIvRaw = (optionRow.getAttribute('data-call-bid-iv') || '').trim();
            const callAskIvRaw = (optionRow.getAttribute('data-call-ask-iv') || '').trim();
            const putBidIvRaw = (optionRow.getAttribute('data-put-bid-iv') || '').trim();
            const putAskIvRaw = (optionRow.getAttribute('data-put-ask-iv') || '').trim();

            // Persist across full page navigation and keep URL fallback
            const dtmNum = Number(dtmRaw);
            const rateNum = Number(rateRaw);
            const callIvNum = Number(callIvRaw);
            const putIvNum = Number(putIvRaw);
            // Pick bid or ask IV when column is unambiguous, else fall back to mid
            let selectedIvNum;
            if (optionType === 'Call') {
              if (isBidCol) selectedIvNum = Number(callBidIvRaw);
              else if (isAskCol) selectedIvNum = Number(callAskIvRaw);
              else selectedIvNum = callIvNum;
            } else {
              if (isBidCol) selectedIvNum = Number(putBidIvRaw);
              else if (isAskCol) selectedIvNum = Number(putAskIvRaw);
              else selectedIvNum = putIvNum;
            }
            const fallbackIvNum = Number.isFinite(selectedIvNum) && selectedIvNum > 0
              ? selectedIvNum
              : (Number.isFinite(callIvNum) ? callIvNum : putIvNum);
            const pending = {
              symbol: ticker,
              type: optionType,
              strike: strike,
              date: dateStr,
              dtm: Number.isFinite(dtmNum) ? String(Math.max(0, Math.round(dtmNum))) : '',
              rate: Number.isFinite(rateNum) ? (rateNum * 100).toFixed(2) : '',
              vol: Number.isFinite(fallbackIvNum) ? (fallbackIvNum * 100).toFixed(2) : '',
            };
            try { sessionStorage.setItem('pricerPendingData', JSON.stringify(pending)); } catch (_) {}
            const qs = new URLSearchParams(pending).toString();
            window.location.href = '/tools/pricer?' + qs;
          }
          return;
        }

        const sortBtn = clickTarget.closest('[data-option-sort]');
        if (sortBtn && optionsSelectedTicker) {
          const ticker = (optionsSelectedTicker.getAttribute('data-ticker') || '').trim().toUpperCase();
          const key = String(sortBtn.getAttribute('data-option-sort') || '').trim();
          if (ticker && key) {
            const current = getOptionsSortState(ticker);
            const next = current && current.key === key
              ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
              : { key, dir: 'asc' };
            setOptionsSortState(ticker, next);
            syncOptionSortHeaders();
            refreshOptionsChain();
          }
          return;
        }

        const toggleBtn = clickTarget.closest('[data-expiry-toggle]');
        if (toggleBtn && optionsSelectedTicker) {
          const ticker = (optionsSelectedTicker.getAttribute('data-ticker') || '').trim().toUpperCase();
          const expiry = String(toggleBtn.getAttribute('data-expiry-toggle') || '').trim();
          if (ticker && expiry) {
            const collapsedExpiries = getCollapsedExpiries(ticker);
            collapsedExpiries[expiry] = !collapsedExpiries[expiry];
            refreshOptionsChain();
          }
          return;
        }

        if (clickTarget.closest('#optionsResetSortBtn') && optionsSelectedTicker) {
          const ticker = (optionsSelectedTicker.getAttribute('data-ticker') || '').trim().toUpperCase();
          if (ticker) {
            setOptionsSortState(ticker, null);
            syncOptionSortHeaders();
            refreshOptionsChain();
          }
          return;
        }

        if (clickTarget.closest('#optionsExpandAllBtn') && optionsSelectedTicker) {
          const ticker = (optionsSelectedTicker.getAttribute('data-ticker') || '').trim().toUpperCase();
          if (ticker) {
            window.__collapsedExpiriesByTicker[ticker] = {};
            refreshOptionsChain();
          }
          return;
        }

        if (clickTarget.closest('#optionsCollapseAllBtn') && optionsSelectedTicker) {
          const ticker = (optionsSelectedTicker.getAttribute('data-ticker') || '').trim().toUpperCase();
          if (ticker) {
            const expiries = window.__optionsExpiriesByTicker[ticker] || [];
            const collapsedExpiries = getCollapsedExpiries(ticker);
            expiries.forEach((expiry) => {
              collapsedExpiries[expiry] = true;
            });
            refreshOptionsChain();
          }
        }
      });
      window.__optionsControlsBound = true;
    }

    if (input && body && count) {
      const rows = Array.from(body.querySelectorAll('tr'));
      function applyFilter() {
        const q = (input.value || '').trim().toUpperCase();
        let visible = 0;
        rows.forEach((row) => {
          const ticker = (row.getAttribute('data-ticker') || '').toUpperCase();
          const show = q.length === 0 || ticker.startsWith(q);
          row.classList.toggle('hidden', !show);
          if (show) visible += 1;
        });
        count.textContent = String(visible);
      }
      input.addEventListener('input', applyFilter);
      applyFilter();
    }

    syncOptionSortHeaders();
    initOptionPricer();
    initRealizedVols();
    initVolatilityCurve();
    initDividends();
    initDiscount();
    initRisk();

    function initRisk() {
      // Canli modda portfoy yalnizca XLSX ile iceri aktarilir
      // (riskHandleImport, /risk-handler.js).
      //
      // Mock modda sunucu ornek bir portfoy sunar; uc canli modda 404
      // dondugu icin istemci tarafinda ayrica bayrak tasimaya gerek yok.
      fetch('/api/mock-portfolio')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          var poz = d && d.ok && Array.isArray(d.positions) ? d.positions : [];
          if (!poz.length) return;

          var tbody = document.getElementById('riskPortfolioBody');
          if (!tbody) return;
          window._riskPortfolio = poz;
          tbody.innerHTML = '';
          poz.forEach(function (p, i) {
            var tr = document.createElement('tr');
            tr.style.background = i % 2 === 0 ? '#f8fafc' : '#fff';
            var renk = p.qty >= 0 ? '#16a34a' : '#dc2626';
            var h = 'padding:4px 10px;';
            tr.innerHTML =
              '<td style="' + h + 'text-align:left">' + p.underlying + '</td>' +
              '<td style="' + h + 'text-align:left;text-transform:capitalize">' + p.posType + '</td>' +
              '<td style="' + h + 'text-align:right">' + p.strike.toFixed(2) + '</td>' +
              '<td style="' + h + 'text-align:left">' + p.expiry + '</td>' +
              '<td style="' + h + 'text-align:right">' + p.dtm + '</td>' +
              '<td style="' + h + 'text-align:right">' + p.spot.toFixed(3) + '</td>' +
              '<td style="' + h + 'text-align:right;font-weight:600;color:' + renk + '">' + p.qty.toFixed(0) + '</td>' +
              '<td style="' + h + 'text-align:right">' + p.delta.toFixed(4) + '</td>';
            tbody.appendChild(tr);
          });

          var wrap = document.getElementById('riskPortfolioWrap');
          var mc = document.getElementById('riskMcPanel');
          if (wrap) wrap.style.display = '';
          if (mc) mc.style.display = '';
          var st = document.getElementById('riskStatus');
          if (st) {
            st.textContent = poz.length + ' sample position(s) loaded (MOCK DATA) — '
              + 'adjust parameters and click Run VaR Simulation.';
          }
        })
        .catch(function () { /* canli mod ya da uc kapali: sessiz gec */ });

      denetimDurumGoster();
    }

    // Denetim izinin kayit sayisini ve butunluk durumunu gosterir.
    function denetimDurumGoster() {
      var el = document.getElementById('denetimDurum');
      if (!el) return;
      fetch('/api/audit/verify')
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.valid) {
            el.textContent = d.recordCount + ' record(s) · chain intact';
            el.style.color = '#16a34a';
          } else {
            el.textContent = (d && d.error) ? ('TAMPERED — ' + d.error) : 'could not verify';
            el.style.color = '#dc2626';
          }
        })
        .catch(function () { el.textContent = 'unreachable'; el.style.color = '#dc2626'; });
    }
    window.denetimDogrula = denetimDurumGoster;

    const FAIR_RATE_STORAGE_KEY = 'futuresFairRateByCode';
    const fairRateInputs = Array.from(document.querySelectorAll('.maturity-fair-input'));
    const fairRateClearButtons = Array.from(document.querySelectorAll('.maturity-fair-clear-btn'));

    function syncFairRateClearButtons() {
      fairRateClearButtons.forEach((btn) => {
        const code = (btn.getAttribute('data-code') || '').trim();
        const inputEl = document.querySelector('.maturity-fair-input[data-code="' + code + '"]');
        if (!inputEl) return;
        btn.style.visibility = (inputEl.value || '').trim() ? 'visible' : 'hidden';
      });
    }

    function persistFairRatesByCode() {
      const payload = {};
      fairRateInputs.forEach((inputEl) => {
        const code = (inputEl.getAttribute('data-code') || '').trim();
        const raw = (inputEl.value || '').trim();
        const n = Number(raw);
        if (code && raw && Number.isFinite(n)) {
          payload[code] = raw;
        }
      });
      try {
        if (Object.keys(payload).length === 0) {
          localStorage.removeItem(FAIR_RATE_STORAGE_KEY);
        } else {
          localStorage.setItem(FAIR_RATE_STORAGE_KEY, JSON.stringify(payload));
        }
      } catch (_) {}
    }

    if (fairRateInputs.length > 0) {
      try {
        const savedRaw = localStorage.getItem(FAIR_RATE_STORAGE_KEY);
        const savedObj = savedRaw ? JSON.parse(savedRaw) : null;
        if (savedObj && typeof savedObj === 'object') {
          fairRateInputs.forEach((inputEl) => {
            const code = (inputEl.getAttribute('data-code') || '').trim();
            const savedVal = code ? savedObj[code] : null;
            if ((savedVal || '').toString().trim()) {
              inputEl.value = String(savedVal);
            }
          });
        }
      } catch (_) {}

      syncFairRateClearButtons();

      fairRateInputs.forEach((inputEl) => {
        inputEl.addEventListener('input', () => {
          persistFairRatesByCode();
          syncFairRateClearButtons();
          refreshSpotAndRates();
        });
        inputEl.addEventListener('change', () => {
          persistFairRatesByCode();
          syncFairRateClearButtons();
          refreshSpotAndRates();
        });
      });
    }

    fairRateClearButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const code = (btn.getAttribute('data-code') || '').trim();
        const inputEl = document.querySelector('.maturity-fair-input[data-code="' + code + '"]');
        if (!inputEl) return;
        inputEl.value = '';
        persistFairRatesByCode();
        syncFairRateClearButtons();
        refreshSpotAndRates();
      });
    });

    refreshSpotAndRates();
    refreshOptionsChain();
    setInterval(() => {
      refreshSpotAndRates();
      refreshOptionsChain();
    }, 2000);
    // Background tabs get their setInterval throttled by the browser (down
    // to ~once/minute in some cases), so a tab left open in the background
    // can look frozen. Force an immediate refresh whenever the tab regains
    // focus/visibility so it's never stale when you look back at it.
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        refreshSpotAndRates();
        refreshOptionsChain();
      }
    });
    window.addEventListener('focus', () => {
      refreshSpotAndRates();
      refreshOptionsChain();
    });
  </script>
</body>
</html>`;
}

function contractExpiryDateServer(code) {
  const m = /^(\d{2})(\d{2})$/.exec(String(code || ''));
  if (!m) return null;
  const month = parseInt(m[1], 10);
  const year = 2000 + parseInt(m[2], 10);
  if (month < 1 || month > 12) return null;
  // UTC-normalized last day of that month — divEntry.date is an ISO
  // "YYYY-MM-DD" string, which Date() parses as UTC midnight, so this
  // must also be UTC or a dividend on the exact last day of the month
  // can appear to fall after expiry purely from timezone skew.
  return new Date(Date.UTC(year, month, 0));
}

function _dividendAdjustSideServer(futVal, spotVal, dtm, D, baseRate) {
  if (futVal === null || futVal === undefined || spotVal === null || spotVal === undefined || !dtm || spotVal === 0) {
    return { rate: baseRate, adjusted: true };
  }
  const adjRate = ((futVal + D) / spotVal - 1) * (365 / dtm);
  return { rate: adjRate, adjusted: true };
}

function dividendAdjustedRateServer(rec, divEntry, code) {
  const baseMid = rec && typeof rec === 'object' ? rec.rate : null;
  const baseBid = rec && typeof rec === 'object' ? rec.bid_rate : null;
  const baseAsk = rec && typeof rec === 'object' ? rec.ask_rate : null;
  const unadjusted = {
    rate: baseMid, adjusted: false,
    bidRate: baseBid, bidAdjusted: false,
    askRate: baseAsk, askAdjusted: false,
  };
  if (!divEntry || !divEntry.amount || !divEntry.date) return unadjusted;
  const D = parseFloat(divEntry.amount);
  if (!Number.isFinite(D) || D <= 0) return unadjusted;
  const divDate = new Date(divEntry.date);
  const expDate = contractExpiryDateServer(code);
  if (!expDate || isNaN(divDate.getTime())) return unadjusted;
  if (divDate > expDate) return unadjusted;

  const dtm = rec ? rec.dtm : null;
  const mid = _dividendAdjustSideServer(rec ? rec.fut_mid : null, rec ? rec.spot_mid : null, dtm, D, baseMid);
  const bid = _dividendAdjustSideServer(rec ? rec.fut_bid : null, rec ? rec.spot_bid : null, dtm, D, baseBid);
  const ask = _dividendAdjustSideServer(rec ? rec.fut_ask : null, rec ? rec.spot_ask : null, dtm, D, baseAsk);
  return {
    rate: mid.rate, adjusted: mid.adjusted,
    bidRate: bid.rate, bidAdjusted: bid.adjusted,
    askRate: ask.rate, askAdjusted: ask.adjusted,
  };
}

function futuresContent(state) {
  const maturities = Array.isArray(state.futuresMeta) && state.futuresMeta.length >= 3
    ? state.futuresMeta.slice(0, 3)
    : [{ label: 'Apr', code: '0426', dtm: '-' }, { label: 'May', code: '0526', dtm: '-' }, { label: 'Jun', code: '0626', dtm: '-' }];

  const rows = tickers.map((t) => {
    const spot = formatSpot(state.spotByTicker[t] && state.spotByTicker[t].spot_mid);
    const divEntry = state.dividendsByTicker ? state.dividendsByTicker[t] : null;
    const cellsHtml = maturities.map((m) => {
      const raw = state.futuresRatesByTicker[t] ? state.futuresRatesByTicker[t][m.code] : null;
      const adj = dividendAdjustedRateServer(raw, divEntry, m.code);
      return `
        <td class="yield-cell${adj.bidAdjusted ? ' div-adjusted' : ''}" data-ticker="${t}" data-code="${m.code}" data-side="bid">${formatRatePct(adj.bidRate)}</td>
        <td class="yield-cell${adj.askAdjusted ? ' div-adjusted' : ''}" data-ticker="${t}" data-code="${m.code}" data-side="ask">${formatRatePct(adj.askRate)}</td>`;
    }).join('');

    return `
      <tr data-ticker="${t}">
        <td class="ticker">${t}</td>
        <td class="spot-cell" data-ticker="${t}">${spot}</td>${cellsHtml}
      </tr>`;
  }).join('');

  const maturityHeadCells = maturities.map((m) => `
              <th colspan="2">
                <div class="maturity-head">
                  <div class="fair-rate-input-wrap">
                    <input class="fair-rate-input maturity-fair-input" data-code="${m.code}" type="number" step="1" placeholder="Fair %" />
                    <button class="fair-rate-clear-btn maturity-fair-clear-btn" data-code="${m.code}" type="button" aria-label="Clear ${m.label} fair rate">x</button>
                  </div>
                  <div>${m.label} Yield <span class="dtm-chip">DTM ${m.dtm}</span></div>
                </div>
              </th>`).join('');

  const maturitySubHeadCells = maturities.map(() => `
              <th class="yield-subhead">Bid</th><th class="yield-subhead">Ask</th>`).join('');

  return `
    <div class="card">
      <div class="card-head">
        <div>
          <h2 class="section-title">Futures Rates</h2>
          <p class="section-sub">Showing <b id="visibleCount">${tickers.length}</b> tickers</p>
        </div>
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
          <input id="tickerFilter" class="filter" placeholder="Filter tickers by prefix..." />
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th rowspan="2">Ticker</th>
              <th rowspan="2">Spot Mid</th>${maturityHeadCells}
            </tr>
            <tr>${maturitySubHeadCells}</tr>
          </thead>
          <tbody id="futuresBody">${rows}</tbody>
        </table>
      </div>
    </div>`;
}

function placeholderPage(title, note) {
  return `
    <div class="card">
      <div class="card-head">
        <div>
          <h2 class="section-title">${title}</h2>
          <p class="placeholder">${note}</p>
        </div>
      </div>
    </div>`;
}

function optionsContent(state, selectedTicker) {
  const activeTicker = optionTickers.includes(selectedTicker) ? selectedTicker : 'THYAO';
  const chainRows = Array.isArray(state.optionsChainByTicker[activeTicker]) ? state.optionsChainByTicker[activeTicker] : [];

  const tickerTabs = optionTickers.map((ticker) =>
    `<a class="ticker-tab ${ticker === activeTicker ? 'active' : ''}" href="${formatTickerHref(ticker)}">${ticker}</a>`
  ).join('');

  const tableHead = optionColumnDefs.map(({ key, label }) => (
    `<th><button class="sort-header-btn" type="button" data-option-sort="${key}" aria-pressed="false"><span>${label}</span><span class="sort-indicator">&#8597;</span></button></th>`
  )).join('');
  const rowsHtml = chainRows.length === 0
    ? '<tr><td class="options-empty" colspan="19">No rows.</td></tr>'
    : '<tr><td class="options-empty" colspan="19">Loading latest rows...</td></tr>';

  return `
    <div class="card">
      <div class="ticker-tabs">${tickerTabs}</div>
      <div class="options-toolbar">
        <div class="placeholder">Selected ticker: <b id="optionsSelectedTicker" data-ticker="${activeTicker}">${activeTicker}</b></div>
        <div class="options-actions">
          <button class="action-btn" type="button" id="optionsResetSortBtn">Reset Sort</button>
          <button class="action-btn" type="button" id="optionsExpandAllBtn">Expand All</button>
          <button class="action-btn" type="button" id="optionsCollapseAllBtn">Collapse All</button>
        </div>
      </div>
      <div class="table-wrap">
        <table class="options-table">
          <thead><tr>${tableHead}</tr></thead>
          <tbody id="optionsBody">${rowsHtml}</tbody>
        </table>
      </div>
    </div>`;
}

function toolsContent(toolsTab) {
  if (toolsTab === 'discount') {
    var _monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var _now = new Date();
    var months = Array.from({length: 12}, function(_, i) {
      var d = new Date(_now.getFullYear(), _now.getMonth() + 1 + i, 1);
      return _monthNames[d.getMonth()] + ' ' + String(d.getFullYear()).slice(-2);
    });
    var discRows = months.map(function(label, i) {
      var m = String(i + 1);
      return '<tr><td class="div-ticker-cell">' + label + '</td>'
        + '<td><input class="disc-rate-input field-input field-editable" data-month="' + m + '" type="number" step="0.01" min="0" placeholder="0.00" /></td>'
        + '<td class="div-ticker-cell">%</td></tr>';
    }).join('');
    return '<div class="card" id="toolsDiscountCard">'
      + '<div class="card-head"><div>'
      + '<h2 class="section-title">Discount Factors</h2>'
      + '<p class="rv-top-note">Enter annualized discount rates (%) per month tenor. Used in the Options Pricer to compute PV of dividend via linear interpolation.</p>'
      + '</div></div>'
      + '<div id="nssPanel" style="margin:0 0 14px 0;padding:10px 12px;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc;">'
      +   '<div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">'
      +     'Fitted Yield Curve</div>'
      +   '<div id="nssStatus" style="font-size:12px;color:#64748b;">loading…</div>'
      +   '<div id="nssBody" style="display:none;margin-top:8px;">'
      +     '<div id="nssParams" style="font-size:12px;color:#334155;margin-bottom:8px;font-family:monospace;"></div>'
      +     '<div style="overflow-x:auto;"><table style="border-collapse:collapse;font-size:12px;">'
      +       '<thead><tr id="nssHead" style="background:#0f1728;color:#fff;"></tr></thead>'
      +       '<tbody><tr id="nssRow"></tr></tbody>'
      +     '</table></div>'
      +   '</div>'
      + '</div>'
      + '<div id="depoPanel" style="margin:0 0 14px 0;padding:10px 12px;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc;">'
      +   '<div style="display:flex;gap:10px;align-items:center;margin-bottom:6px;">'
      +     '<span style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;">'
      +       'Persistence &amp; Model Versions</span>'
      +     '<button class="action-btn" type="button" onclick="depoYenile()" style="padding:2px 9px;font-size:11px;">Refresh</button>'
      +   '</div>'
      +   '<div id="depoStatus" style="font-size:12px;color:#64748b;">loading…</div>'
      +   '<div id="depoBody" style="display:none;margin-top:8px;">'
      +     '<div id="depoCounts" style="font-size:11px;color:#334155;margin-bottom:8px;font-family:monospace;word-break:break-all;"></div>'
      +     '<div style="overflow-x:auto;"><table style="border-collapse:collapse;font-size:11px;width:100%;">'
      +       '<thead><tr style="background:#0f1728;color:#fff;">'
      +         '<th style="padding:4px 10px;text-align:left;">Ver</th>'
      +         '<th style="padding:4px 10px;text-align:left;">Fitted At (UTC)</th>'
      +         '<th style="padding:4px 10px;text-align:left;">Model</th>'
      +         '<th style="padding:4px 10px;text-align:left;">Scope</th>'
      +         '<th style="padding:4px 10px;text-align:right;">Fit Quality</th>'
      +         '<th style="padding:4px 10px;text-align:left;">Mode</th>'
      +       '</tr></thead>'
      +       '<tbody id="depoVersions"></tbody>'
      +     '</table></div>'
      +   '</div>'
      + '</div>'
      + '<div class="table-wrap"><table class="rv-table"><thead><tr>'
      + '<th>Tenor</th><th>Rate (%)</th><th></th>'
      + '</tr></thead><tbody>' + discRows + '</tbody></table></div></div>';
  }

  if (toolsTab === 'dividends') {
    const allDivTickers = [...new Set([...optionTickers, ...realizedVolTickers])].sort();
    const rows = allDivTickers.map((t) =>
      `<tr data-div-ticker="${t}">
        <td class="div-ticker-cell">${t}</td>
        <td><input class="div-amount-input field-input field-editable" data-div-ticker="${t}" type="number" step="0.0001" min="0" placeholder="0.0000" /></td>
        <td><input class="div-date-input field-input field-editable" data-div-ticker="${t}" type="date" /></td>
        <td><button class="div-clear-btn action-btn" data-div-ticker="${t}" type="button">Clear</button></td>
      </tr>`
    ).join('');
    return `
      <div class="card" id="toolsDividendsCard">
        <div class="card-head">
          <div>
            <h2 class="section-title">Dividends</h2>
            <p class="rv-top-note">Set cash dividend amounts and ex-dividend dates per ticker. Values are used automatically in the Options Pricer.</p>
          </div>
          <button class="action-btn" type="button" id="divClearAllBtn">Clear All</button>
        </div>
        <div class="table-wrap">
          <table class="rv-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Dividend (cash)</th>
                <th>Ex-Date</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="divTableBody">${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  if (toolsTab === 'realized-vols') {
    return `
      <div class="card" id="toolsRealizedVolsCard">
        <div class="card-head">
          <div>
            <h2 class="section-title">Realized Vols</h2>
            <p class="rv-top-note">Annualized realized vol table from the realized_volatility workflow (mean-subtracted returns).</p>
          </div>
        </div>
        <div class="rv-toolbar">
          <div class="rv-updated">Last updated: <b id="rvUpdatedAt">-</b></div>
          <div class="rv-controls">
            <select id="rvLookbackSelect" class="rv-select" aria-label="Select lookback period">
              <option value="Default">Default</option>
            </select>
            <select id="rvModelSelect" class="rv-select" aria-label="Select forecast model">
              <option value="GARCH(1,1)">GARCH(1,1)</option>
            </select>
            <button class="action-btn" type="button" id="rvToggleForecastBtn">Hide Forecasts</button>
            <button class="action-btn" type="button" id="rvResetSortBtn">Reset Sort</button>
            <button class="action-btn" type="button" id="rvRefreshBtn">Refresh</button>
          </div>
        </div>
        <div class="rv-status" id="rvStatus">Waiting for data...</div>
        <div class="table-wrap">
          <table class="rv-table">
            <thead>
              <tr>
                <th><button class="sort-header-btn" type="button" data-rv-sort="ticker" aria-pressed="false"><span>Ticker</span><span class="sort-indicator">&#8597;</span></button></th>
                <th><button class="sort-header-btn" type="button" data-rv-sort="15D" aria-pressed="false"><span>15D RV</span><span class="sort-indicator">&#8597;</span></button></th>
                <th class="rv-fcst-col">15D Fcst</th>
                <th><button class="sort-header-btn" type="button" data-rv-sort="30D" aria-pressed="false"><span>30D RV</span><span class="sort-indicator">&#8597;</span></button></th>
                <th class="rv-fcst-col">30D Fcst</th>
                <th><button class="sort-header-btn" type="button" data-rv-sort="60D" aria-pressed="false"><span>60D RV</span><span class="sort-indicator">&#8597;</span></button></th>
                <th class="rv-fcst-col">60D Fcst</th>
                <th><button class="sort-header-btn" type="button" data-rv-sort="90D" aria-pressed="false"><span>90D RV</span><span class="sort-indicator">&#8597;</span></button></th>
                <th class="rv-fcst-col">90D Fcst</th>
                <th><button class="sort-header-btn" type="button" data-rv-sort="180D" aria-pressed="false"><span>180D RV</span><span class="sort-indicator">&#8597;</span></button></th>
                <th class="rv-fcst-col">180D Fcst</th>
              </tr>
            </thead>
            <tbody id="rvTableBody"><tr><td class="options-empty" colspan="11">Loading realized vols...</td></tr></tbody>
          </table>
        </div>
      </div>`;
  }

  if (toolsTab === 'risk') {
    return `
      <div class="card" id="toolsRiskCard">
        <div class="card-head">
          <div>
            <h2 class="section-title">Risk — Portfolio</h2>
            <p class="rv-top-note">Import your portfolio from an XLSX file to view positions.</p>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <label for="riskImportFile" class="action-btn" style="background:#475569;cursor:pointer;margin:0;">&#8593; Import XLSX</label>
            <input type="file" id="riskImportFile" accept=".xlsx" style="position:absolute;left:-9999px;opacity:0;width:1px;height:1px;" onchange="riskHandleImport(this)" />
          </div>
        </div>
        <div id="riskPortfolioWrap" style="display:none;margin-top:14px;">
          <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Imported Portfolio</div>
          <div style="overflow-x:auto;">
            <table id="riskPortfolioTable" style="width:100%;border-collapse:collapse;font-size:12px;">
              <thead>
                <tr style="background:#e2e8f0;">
                  <th style="padding:5px 10px;text-align:left;">Underlying</th>
                  <th style="padding:5px 10px;text-align:left;">Type</th>
                  <th style="padding:5px 10px;text-align:right;">Strike</th>
                  <th style="padding:5px 10px;text-align:left;">Expiry</th>
                  <th style="padding:5px 10px;text-align:right;">DTM</th>
                  <th style="padding:5px 10px;text-align:right;">Spot</th>
                  <th style="padding:5px 10px;text-align:right;">Net Qty</th>
                  <th style="padding:5px 10px;text-align:right;">Delta</th>
                </tr>
              </thead>
              <tbody id="riskPortfolioBody"></tbody>
            </table>
          </div>
        </div>
        <div id="riskMcPanel" style="display:none;margin-top:16px;border-top:1px solid #e2e8f0;padding-top:14px;">
          <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;">Monte Carlo VaR Parameters</div>
          <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;">
            <div><div class="field-label">Holding Period (days)</div><input class="field-input field-editable" id="riskMcHold" type="number" value="1" min="1" step="1" style="width:100px;" /></div>
            <div><div class="field-label">Simulations</div><select class="field-input field-editable" id="riskMcSims" style="width:110px;"><option value="10000" selected>10,000</option><option value="50000">50,000</option><option value="100000">100,000</option></select></div>
            <div><div class="field-label">Confidence Level</div><select class="field-input field-editable" id="riskMcConf" style="width:80px;"><option value="0.95">95%</option><option value="0.99" selected>99%</option></select></div>
            <div><div class="field-label">Vol Window</div><select class="field-input field-editable" id="riskMcVolWin" style="width:90px;"><option value="15">15D RV</option><option value="30" selected>30D RV</option><option value="60">60D RV</option><option value="90">90D RV</option></select></div>
            <div><div class="field-label">Contract Multiplier</div><input class="field-input field-editable" id="riskMcMult" type="number" value="100" min="1" step="1" style="width:90px;" /></div>
            <div><div class="field-label" title="Average correlation across underlyings. 0 = independent, 1 = perfectly correlated.">Correlation (&rho;)</div><input class="field-input field-editable" id="riskMcRho" type="number" value="0.50" min="0" max="0.99" step="0.05" style="width:95px;" /></div>
            <button class="action-btn" type="button" onclick="riskRunMC()" style="align-self:flex-end;">Run VaR Simulation</button>
            <button class="action-btn" type="button" onclick="riskRunStres()" style="align-self:flex-end;">Stress Test</button>
            <button class="action-btn" type="button" onclick="riskRaporIndir()" style="align-self:flex-end;">Download Report (CSV)</button>
          </div>
          <div style="margin-top:14px;border-top:1px solid #e2e8f0;padding-top:10px;display:flex;gap:12px;align-items:center;font-size:12px;">
            <span style="font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;">Audit Trail</span>
            <span id="denetimDurum" style="color:#64748b;">loading…</span>
            <button class="action-btn" type="button" onclick="denetimDogrula()" style="padding:3px 10px;font-size:11px;">Verify Integrity</button>
          </div>
          <div id="riskStresWrap" style="display:none;margin-top:14px;">
            <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">Stress Test — Scenario Analysis</div>
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
              <thead>
                <tr style="background:#0f1728;color:#fff;">
                  <th style="padding:5px 10px;text-align:left;">Scenario</th>
                  <th style="padding:5px 10px;text-align:right;">Portfolio Value</th>
                  <th style="padding:5px 10px;text-align:right;">Immediate Impact</th>
                  <th style="padding:5px 10px;text-align:right;">Impact %</th>
                  <th style="padding:5px 10px;text-align:right;">VaR</th>
                </tr>
              </thead>
              <tbody id="riskStresBody"></tbody>
            </table>
          </div>
          <div id="riskMcResults" style="display:none;margin-top:14px;">
            <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;">
              <div><div class="field-label">Portfolio Value</div><input class="field-output field-readonly emph" id="riskMcCurVal" readonly style="width:140px;" /></div>
              <div><div class="field-label">VaR (abs loss)</div><input class="field-output field-readonly emph" id="riskMcVarAbs" readonly style="width:140px;" /></div>
              <div><div class="field-label">VaR (%)</div><input class="field-output field-readonly emph" id="riskMcVarPct" readonly style="width:90px;" /></div>
              <div><div class="field-label">CVaR (ES)</div><input class="field-output field-readonly" id="riskMcCvar" readonly style="width:140px;" /></div>
              <div><div class="field-label">Mean P&amp;L</div><input class="field-output field-readonly" id="riskMcMean" readonly style="width:140px;" /></div>
              <div><div class="field-label">Std Dev P&amp;L</div><input class="field-output field-readonly" id="riskMcStd" readonly style="width:140px;" /></div>
            </div>
            <div style="display:flex;gap:16px;align-items:flex-start;">
              <div style="flex:1;min-width:0;overflow-x:auto;">
                <svg id="riskMcSvg" style="display:block;background:#0f172a;border-radius:8px;width:100%;height:auto;"></svg>
              </div>
              <div id="riskMcLegend" style="flex-shrink:0;width:220px;background:#0f172a;border-radius:8px;padding:14px;"></div>
            </div>
            <div id="riskMcDebugTable" style="margin-top:4px;overflow-x:auto;"></div>
          </div>
        </div>
        <div class="rv-status" id="riskStatus" style="margin:8px 0;">Import an XLSX file to load your portfolio.</div>
      </div>`;
  }

  if (toolsTab === 'volatility-curve') {
    return `
      <div class="card" id="toolsVolCurveCard">
        <div class="card-head">
          <div>
            <h2 class="section-title">Volatility Curve (Heston)</h2>
            <p class="rv-top-note">Example view fixed to THYAO, nearest maturity. Market chain rows are compared against Heston-implied vols.</p>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <label for="vcModelSelect" style="font-size:12px; color:#475569;">Model</label>
            <select id="vcModelSelect" class="rv-select" aria-label="Select volatility model">
              <option value="heston">Heston</option>
              <option value="svi">SVI</option>
            </select>
            <button class="action-btn" type="button" id="vcRefreshBtn">Refresh</button>
          </div>
        </div>
        <div class="vc-status" id="vcStatus">Waiting for THYAO market data...</div>
        <div class="vc-legend">
          <span><span class="vc-dot market"></span>Market IV</span>
          <span><span class="vc-dot heston"></span><span id="vcLegendModelText">Heston IV</span></span>
        </div>
        <div class="vc-chart-wrap">
          <svg id="vcSvg" class="vc-chart" viewBox="0 0 960 260" role="img" aria-label="Volatility curve chart"></svg>
        </div>
        <div class="table-wrap">
          <table class="vc-table">
            <thead>
              <tr>
                <th>Strike</th>
                <th>Call Mid</th>
                <th>Call Mkt IV</th>
                <th id="vcCallModelHead">Call Heston IV</th>
                <th>Put Mid</th>
                <th>Put Mkt IV</th>
                <th id="vcPutModelHead">Put Heston IV</th>
                <th>DTM</th>
                <th>Rate</th>
              </tr>
            </thead>
            <tbody id="vcTableBody"><tr><td class="options-empty" colspan="9">Loading...</td></tr></tbody>
          </table>
        </div>
      </div>`;
  }

  const symbolOptions = optionTickers.map((t) => `<option value="${t}" ${t === 'THYAO' ? 'selected' : ''}>${t}</option>`).join('');
  return `
    <div class="card" id="toolsPricerCard">
      <div class="pricer-top">
        <div>
          <h2 class="section-title">Options Pricer (Black-Scholes)</h2>
          <p class="tools-note">Dividend is read as cash amount D. Pricing uses PV(D)=D*exp(-rT), then S*=S-PV(D).</p>
        </div>
        <button type="button" class="toggle-btn" id="prcDivToggle">Dividend: ON</button>
      </div>
      <div class="pricer-section">
        <div class="pricer-section-label">Contract</div>
        <div class="pricer-grid">
          <div><div class="field-label">Symbol</div><select class="field-input field-editable" id="prcSymbol">${symbolOptions}</select></div>
          <div><div class="field-label">Type</div><select class="field-input field-editable" id="prcType"><option>Call</option><option>Put</option></select></div>
          <div>
            <div class="field-label field-label-row"><span>Spot (S)</span><label class="manual-toggle"><input id="prcSpotManual" type="checkbox" />Set Manual</label></div>
            <input class="field-input field-editable" id="prcSpot" type="number" step="0.0001" placeholder="e.g. 312.40" />
          </div>
          <div><div class="field-label">Strike (K)</div><input class="field-input field-editable" id="prcStrike" type="number" step="0.0001" placeholder="e.g. 300" /></div>
          <div><div class="field-label">Maturity Date</div><input class="field-input field-editable" id="prcDate" type="date" /></div>
          <div><div class="field-label">Days to Maturity</div><input class="field-output field-readonly" id="prcDays" readonly /></div>
        </div>
      </div>
      <div class="pricer-section">
        <div class="pricer-section-label">Parameters &amp; Computed Inputs</div>
        <div class="pricer-grid">
          <div>
            <div class="field-label field-label-row"><span>Rate r (annual %)</span><label class="manual-toggle"><input id="prcRateManual" type="checkbox" />Set Manual</label></div>
            <input class="field-input field-editable" id="prcRate" type="number" step="0.01" />
          </div>
          <div><div class="field-label">Volatility (annual %)</div><input class="field-input field-editable" id="prcVol" type="number" step="0.01" value="60" /></div>
          <div style="display:none"><div class="field-label">Dividend D (cash)</div><input class="field-input field-editable" id="prcDiv" type="number" step="0.0001" value="0" /></div>
          <div><div class="field-label">Dividend</div><input class="field-output field-readonly emph" id="prcPvDiv" readonly /></div>
          <div><div class="field-label">Adjusted Spot (S*)</div><input class="field-output field-readonly emph" id="prcAdjSpot" readonly /></div>
          <div style="display:none"><div class="field-label">T (years)</div><input class="field-output field-readonly" id="prcT" readonly /></div>
        </div>
        <div class="prc-d1d2-row" style="display:none">
          <div><div class="field-label">d1</div><input class="field-output field-readonly" id="prcD1" readonly /></div>
          <div><div class="field-label">d2</div><input class="field-output field-readonly" id="prcD2" readonly /></div>
        </div>
      </div>
      <div class="pricer-section prc-results-section">
        <div class="pricer-section-label">Results</div>
        <div class="prc-results-layout">
          <div class="prc-price-cell">
            <div class="field-label">Price</div>
            <input class="field-output field-readonly prc-price-big" id="prcPrice" readonly />
          </div>
          <div class="prc-greeks-grid">
            <div><div class="field-label">Delta</div><input class="field-output field-readonly" id="prcDelta" readonly /></div>
            <div><div class="field-label">Gamma</div><input class="field-output field-readonly" id="prcGamma" readonly /></div>
            <div><div class="field-label">Vega (per 1% vol)</div><input class="field-output field-readonly" id="prcVega" readonly /></div>
            <div><div class="field-label">Theta (per day)</div><input class="field-output field-readonly" id="prcTheta" readonly /></div>
            <div><div class="field-label">Rho</div><input class="field-output field-readonly" id="prcRho" readonly /></div>
          </div>
          <div style="margin-top:14px;border-top:1px solid #e2e8f0;padding-top:12px;">
            <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">
              Method Comparison (Binomial Tree, 300 steps)
            </div>
            <div style="display:flex;gap:12px;flex-wrap:wrap;">
              <div><div class="field-label">Binomial (European)</div><input class="field-output field-readonly" id="prcBinEu" readonly /></div>
              <div><div class="field-label">Binomial (American)</div><input class="field-output field-readonly" id="prcBinAm" readonly /></div>
              <div><div class="field-label" title="Deviation between binomial European and Black-Scholes. A large gap points to a model or parameter problem.">Deviation from BS</div><input class="field-output field-readonly" id="prcBinDiff" readonly /></div>
              <div><div class="field-label" title="Difference between American and European: the value of the early exercise right.">Early Exercise Premium</div><input class="field-output field-readonly" id="prcEarlyEx" readonly /></div>
            </div>
          </div>
        </div>
      </div>
      <div class="pricer-log-wrap">
        <div class="pricer-log-head">
          <button class="action-btn" type="button" id="prcClearLogBtn">Clear Logs</button>
          <div class="pricer-log-right">
            <div class="pricer-log-controls">
              <input class="pricer-broker-input" id="prcBroker" type="text" placeholder="Broker:" />
              <input class="pricer-broker-input" id="prcQty" type="text" placeholder="Qty e.g. 1000c" style="width:100px" />
              <select class="pricer-broker-input" id="prcSide" style="width:80px"><option value="bid">bid</option><option value="ask">ask</option><option value="offer">offer</option><option value="mid">mid</option></select>
            </div>
            <button class="action-btn" type="button" id="prcCopyBtn" title="Copy to clipboard">&#128203; Copy</button>
            <button class="action-btn" type="button" id="prcLogBtn">Log Priced Option</button>
          </div>
        </div>
        <div class="table-wrap">
          <table class="pricer-log-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Broker</th>
                <th>Symbol</th>
                <th>Type</th>
                <th>Spot</th>
                <th>Strike</th>
                <th>Maturity Date</th>
                <th>DTM</th>
                <th>Rate %</th>
                <th>Vol %</th>
                <th>Dividend</th>
                <th>Price</th>
                <th>Delta</th>
                <th>Gamma</th>
                <th>Vega</th>
                <th>Theta</th>
                <th>Rho</th>
              </tr>
            </thead>
            <tbody id="prcLogBody"><tr><td class="options-empty" colspan="17">No logs yet.</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>`;
}

function renderRoute(url, state) {
  const pathname = url.pathname;
  const tickerParam = url.searchParams.get('ticker') || 'THYAO';
  const toolsTab = pathname === '/tools/dividends'
    ? 'dividends'
    : pathname === '/tools/discount'
      ? 'discount'
      : pathname === '/tools/realized-vols'
        ? 'realized-vols'
        : pathname === '/tools/volatility-curve'
          ? 'volatility-curve'
          : pathname === '/tools/risk'
            ? 'risk'
            : 'pricer';

  if (pathname === '/market/futures') {
    return appLayout({
      mainTab: 'market',
      marketTab: 'futures',
      toolsTab: null,
      breadcrumb: 'Main: <b>MARKET</b> - Market: <b>FUTURES</b>',
      contentHtml: futuresContent(state),
    });
  }

  if (pathname === '/market/options') {
    return appLayout({
      mainTab: 'market',
      marketTab: 'options',
      toolsTab: null,
      breadcrumb: 'Main: <b>MARKET</b> - Market: <b>OPTIONS</b>',
      contentHtml: optionsContent(state, tickerParam),
    });
  }

  if (pathname === '/market/warrants') {
    return appLayout({
      mainTab: 'market',
      marketTab: 'warrants',
      toolsTab: null,
      breadcrumb: 'Main: <b>MARKET</b> - Market: <b>WARRANTS</b>',
      contentHtml: placeholderPage('Warrants', 'Content will be added later.'),
    });
  }

  if (pathname === '/market/summary') {
    return appLayout({
      mainTab: 'market',
      marketTab: 'summary',
      toolsTab: null,
      breadcrumb: 'Main: <b>MARKET</b> - Market: <b>SUMMARY</b>',
      contentHtml: placeholderPage('Summary', 'Content will be added later.'),
    });
  }

  if (pathname === '/tools' || pathname === '/tools/pricer' || pathname === '/tools/dividends' || pathname === '/tools/discount' || pathname === '/tools/realized-vols' || pathname === '/tools/volatility-curve' || pathname === '/tools/risk') {
    const toolsLabel = toolsTab === 'pricer'
      ? 'Tools: <b>PRICER</b>'
      : toolsTab === 'dividends'
        ? 'Tools: <b>DIVIDENDS</b>'
        : toolsTab === 'discount'
          ? 'Tools: <b>DISCOUNT</b>'
          : toolsTab === 'realized-vols'
            ? 'Tools: <b>REALIZED VOLS</b>'
            : toolsTab === 'risk'
            ? 'Tools: <b>RISK</b>'
            : 'Tools: <b>VOLATILITY CURVE</b>';
    return appLayout({
      mainTab: 'tools',
      marketTab: null,
      toolsTab,
      breadcrumb: `Main: <b>TOOLS</b> - ${toolsLabel}`,
      contentHtml: toolsContent(toolsTab),
    });
  }

  return appLayout({
    mainTab: 'market',
    marketTab: 'futures',
    toolsTab: null,
    breadcrumb: 'Main: <b>MARKET</b> - Market: <b>FUTURES</b>',
    contentHtml: futuresContent(state),
  });
}

function parseChartDateMs(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    return value > 1e10 ? value : value * 1000;
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric)) {
    return numeric > 1e10 ? numeric : numeric * 1000;
  }
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeCloseSeries(records) {
  const rows = [];
  records.forEach((item) => {
    if (item === null || item === undefined) return;
    let dateValue = null;
    let closeValue = null;

    if (Array.isArray(item)) {
      dateValue = item[0];
      closeValue = item.length > 4 ? item[4] : item[item.length - 1];
    } else if (typeof item === 'object') {
      const keys = Object.keys(item);
      const dateKey = keys.find((k) => ['date', 'tarih', 't', 'd', 'datetime', 'time'].includes(String(k).toLowerCase()));
      const closeKey = keys.find((k) => ['close', 'c', 'kapanis', 'last', 'price'].includes(String(k).toLowerCase()));
      dateValue = dateKey ? item[dateKey] : null;
      closeValue = closeKey ? item[closeKey] : null;
      if (closeValue === null || closeValue === undefined) {
        const numericKeys = keys.filter((k) => Number.isFinite(Number(item[k])));
        if (numericKeys.length > 0) {
          closeValue = item[numericKeys[Math.min(4, numericKeys.length - 1)]];
        }
      }
    }

    const ts = parseChartDateMs(dateValue);
    const close = Number(closeValue);
    if (!Number.isFinite(ts) || !Number.isFinite(close)) return;
    rows.push({ ts, close });
  });

  rows.sort((a, b) => a.ts - b.ts);
  return rows;
}

function parseChartPayload(raw) {
  const source = String(raw || '').trim();
  if (!source) return [];

  const attempts = [
    () => JSON.parse(source),
    () => {
      const fixed = source.startsWith('[')
        ? source.replace(/([\}\]])\s*([\{\[])/g, '$1,$2')
        : '[' + source.replace(/([\}\]])\s*([\{\[])/g, '$1,$2') + ']';
      return JSON.parse(fixed);
    },
  ];

  for (const fn of attempts) {
    try {
      const parsed = fn();
      if (Array.isArray(parsed)) return parsed;
      if (parsed && typeof parsed === 'object') return [parsed];
    } catch (_) {
      // Keep trying fallback parse strategies.
    }
  }
  return [];
}

async function fetchCloseSeriesForRv(ticker, barCount = 300) {
  const fetchUrl = 'https://servisapi.idealdata.com.tr/api/Chart/Chart1'
    + '?Sembol=' + encodeURIComponent(ticker)
    + '&Periyot=G'
    + '&BarCount=' + encodeURIComponent(String(barCount))
    + '&CurrencyCode=TRY'
    + '&api_key=' + encodeURIComponent(realizedVolApiKey);

  const response = await fetch(fetchUrl);
  const raw = await response.text();
  const parsed = parseChartPayload(raw);
  return normalizeCloseSeries(parsed);
}

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchCloseSeriesForRvWithRetry(ticker, barCount = 300, maxAttempts = 3) {
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const series = await fetchCloseSeriesForRv(ticker, barCount);
      if (Array.isArray(series) && series.length > 0) {
        return series;
      }
      lastError = new Error('empty series');
    } catch (err) {
      lastError = err;
    }
    if (attempt < maxAttempts) {
      await sleepMs(220 * attempt);
    }
  }
  throw lastError || new Error('failed to fetch series');
}

function computeRealizedVolPercent(prices, endMs, windowDays) {
  if (!Array.isArray(prices) || prices.length === 0) return null;
  const startMs = endMs - ((windowDays - 1) * 24 * 60 * 60 * 1000);

  const beforeWindow = prices.filter((p) => p.ts < startMs);
  const inWindow = prices.filter((p) => p.ts >= startMs && p.ts <= endMs);
  if (inWindow.length < 1) return null;

  const combined = beforeWindow.length > 0
    ? [beforeWindow[beforeWindow.length - 1], ...inWindow]
    : inWindow.slice();

  const returns = [];
  for (let i = 1; i < combined.length; i++) {
    const prev = combined[i - 1];
    const curr = combined[i];
    if (!(prev.close > 0) || !(curr.close > 0)) continue;
    if (curr.ts < startMs || curr.ts > endMs) continue;
    returns.push(Math.log(curr.close / prev.close));
  }

  const n = returns.length;
  if (n < 1) return null;

  // Matches realized_volatility.ipynb's realized_volatility() exactly:
  // zero-mean RMS of log returns (not a demeaned sample std), n divisor
  // (not n-1), annualized by sqrt(252). RV = sqrt(sum(r^2)/n) * sqrt(252).
  const sqSum = returns.reduce((acc, v) => acc + (v * v), 0);
  const rv = Math.sqrt(sqSum / n);
  const annualized = rv * Math.sqrt(252) * 100;
  return Number.isFinite(annualized) ? Number(annualized.toFixed(2)) : null;
}

// Mock modda gerceklesmis volatilite tablosunu uretir.
// Ticker adindan tureyen sabit bir tohum kullanilir: ayni ticker her
// calistirmada ayni degerleri alir, tablo arastirma sirasinda zipliamaz.
// Fcst sutunu tahmin dosyasindan okunur; mock RV degerleri de ayni dosyadaki
// tahminlerin etrafinda uretilir ki tablo kendi icinde tutarli olsun
// (aksi halde RV %20 iken tahmin %46 gibi anlamsiz ciftler cikiyor).
// Tahmin yukunu TEK yerden kurar: hem mock RV uretimi hem Fcst sutunu
// bunu kullanir. Ayri ayri dosya secmeleri, ikisinin farkli kaynaklara
// dusup tabloda RV %20 / Fcst %46 gibi tutarsiz ciftler uretmesine yol
// aciyordu.
//
// DURAGAN DOSYA ILE DEPODAN HESAPLANAN TAHMIN NASIL BIRARADA DURUYOR
// Arayuzde zaten bir "lookback" secici var ve secenekleri dogrudan
// lookbacks anahtarlarindan uretiliyor. Depodan hesaplanan tahmin bu
// yuzden duragan dosyanin UZERINE YAZILMIYOR, yanina EK BIR SECENEK
// olarak ekleniyor ("Store"). Boylece:
//   * kullanici hangi tahmini gordugunu secerek biliyor;
//   * depo henuz 3 ticker kapsiyorken 46 satirin Fcst sutunu bosalmiyor.
// Depo duragan dosya kadar ticker kapsadiginda "Store" varsayilan olur.
const STORE_LOOKBACK_ADI = 'Store';

function storeForecastDosyasi() {
  const p = path.join(__dirname, 'garch_forecasts_store.json');
  try {
    if (!fs.existsSync(p)) return null;
    const d = JSON.parse(fs.readFileSync(p, 'utf8'));
    if (!d || typeof d !== 'object' || !d.lookbacks) return null;
    // Dosya veri modunu tasir. MOCK depodan uretilmis bir tahmin canli
    // bir ekrana, ya da tersi, asla girmemeli.
    const beklenen = MOCK_MODE ? 'MOCK' : 'LIVE';
    if (d.data_mode && d.data_mode !== beklenen) return null;
    if (!d.fitted) return null;
    const lb = d.lookbacks.store || d.lookbacks[STORE_LOOKBACK_ADI];
    if (!lb || !Object.keys(lb).length) return null;
    return { data: d, lookback: lb, path: p };
  } catch (_) {
    return null;                       // bozuk dosya duragan yolu bozmamali
  }
}

function duraganForecastDosyasi() {
  const adaylar = [
    path.join(__dirname, 'realized_forecasts_all_models.json'),
    path.join(__dirname, 'garch_forecasts_all_models.json'),
    path.join(__dirname, '..', 'HUD', 'iv-ui', 'public', 'realized_forecasts_all_models.json'),
    path.join(__dirname, 'garch_forecasts.json'),
  ];
  for (const p of adaylar) {
    try {
      if (!fs.existsSync(p)) continue;
      const d = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (d && typeof d === 'object') return { data: d, path: p };
    } catch (_) { /* bozuk dosyayi atla */ }
  }
  return null;
}

function forecastPayload() {
  const duragan = duraganForecastDosyasi();
  const depo = storeForecastDosyasi();

  if (!depo) {
    return {
      data: duragan ? duragan.data : {},
      path: duragan ? duragan.path : null,
      source: duragan ? 'static' : 'none',
      storeTickers: 0,
    };
  }

  const depoSayi = Object.keys(depo.lookback).length;
  if (!duragan || !duragan.data.lookbacks) {
    // Duragan dosya yok (ya da eski semada): depo tek kaynak.
    return { data: depo.data, path: depo.path, source: 'store', storeTickers: depoSayi };
  }

  const duraganLb = duragan.data.lookbacks;
  const duraganSayi = Math.max(
    0, ...Object.values(duraganLb).map((m) => Object.keys(m || {}).length));
  // Depo duragan dosya kadar ticker kapsiyorsa varsayilan olsun; aksi
  // halde ek secenek olarak kalsin ki sparse depo tabloyu bosaltmasin.
  const depoVarsayilan = depoSayi >= duraganSayi;

  const birlesik = depoVarsayilan
    ? { [STORE_LOOKBACK_ADI]: depo.lookback, ...duraganLb }
    : { ...duraganLb, [STORE_LOOKBACK_ADI]: depo.lookback };

  return {
    data: { ...duragan.data, lookbacks: birlesik },
    path: depo.path,
    source: depoVarsayilan ? 'store' : 'mixed',
    storeTickers: depoSayi,
    staticTickers: duraganSayi,
  };
}

function mockTahminTabani() {
  try {
    const { data: d } = forecastPayload();
    if (!d || !d.lookbacks) return {};
    // Arayuzun VARSAYILAN olarak sececegi lookback ile ayni olani kullan
    // (secici bundle.lookbackKeys[0]'i aliyor). Baska bir lookback
    // secilirse mock RV ile Fcst sutunu yine ayrisirdi.
    const lookback = Object.values(d.lookbacks)[0];
    if (!lookback) return {};
    const out = {};
    for (const [ticker, modeller] of Object.entries(lookback)) {
      out[ticker] = modeller['GARCH(1,1)'] || Object.values(modeller)[0] || {};
    }
    return out;
  } catch (_) {
    return {};
  }
}

function buildMockRealizedVolTable() {
  const windows = realizedVolWindows;
  const tahmin = mockTahminTabani();
  const rows = realizedVolTickers.map((ticker) => {
    let seed = 0;
    for (let i = 0; i < ticker.length; i++) seed = (seed * 31 + ticker.charCodeAt(i)) % 9973;
    const tahminSatiri = tahmin[ticker] || {};
    const row = { Ticker: ticker };
    windows.forEach((w, i) => {
      const t = Number(tahminSatiri[`${w}D (%)`]);
      // Tahmin varsa onun etrafinda, yoksa tickera sabit makul bir seviyede
      const merkez = Number.isFinite(t) ? t : 22 + (seed % 2600) / 100;
      const sapma = (((seed * (i + 5)) % 300) - 150) / 100;   // ±%1.5
      row[`${w}D RV`] = Math.round((merkez + sapma) * 100) / 100;
    });
    return row;
  });
  rows.sort((a, b) => String(a.Ticker).localeCompare(String(b.Ticker)));
  // Sekil canli moddakiyle ayni olmali: cagiran taraf rows/generated_at bekliyor.
  return {
    generated_at: new Date().toISOString(),
    rows,
    windows: realizedVolWindows.slice(),
  };
}

async function buildRealizedVolTable() {
  if (MOCK_MODE) return buildMockRealizedVolTable();

  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();

  const rows = [];
  for (const ticker of realizedVolTickers) {
    try {
      // Sequential fetch avoids upstream API throttling seen with high parallelism.
      const prices = await fetchCloseSeriesForRvWithRetry(ticker, 300, 3);
      const eligible = prices.filter((p) => p.ts < todayStart);
      const endPoint = eligible.length > 0 ? eligible[eligible.length - 1] : null;
      if (!endPoint) {
        rows.push({
          Ticker: ticker,
          '15D RV': null,
          '30D RV': null,
          '60D RV': null,
          '90D RV': null,
          '180D RV': null,
        });
        continue;
      }

      const row = { Ticker: ticker };
      realizedVolWindows.forEach((w) => {
        row[`${w}D RV`] = computeRealizedVolPercent(prices, endPoint.ts, w);
      });
      rows.push(row);
    } catch (_) {
      rows.push({
        Ticker: ticker,
        '15D RV': null,
        '30D RV': null,
        '60D RV': null,
        '90D RV': null,
        '180D RV': null,
      });
    }
    await sleepMs(60);
  }

  rows.sort((a, b) => String(a.Ticker).localeCompare(String(b.Ticker)));
  return {
    generated_at: new Date().toISOString(),
    rows,
    windows: realizedVolWindows.slice(),
  };
}

async function getRealizedVolTable(forceRefresh = false) {
  const nowMs = Date.now();
  const hasFreshCache = realizedVolCache.payload && (nowMs - realizedVolCache.generatedAtMs) < realizedVolCacheTtlMs;
  if (!forceRefresh && hasFreshCache) {
    return realizedVolCache.payload;
  }

  if (realizedVolCache.inFlight) {
    return realizedVolCache.inFlight;
  }

  realizedVolCache.inFlight = buildRealizedVolTable()
    .then((payload) => {
      // Load forecast payload with preference for multi-model/lookback schema.
      let forecasts = {};
      let forecastSource = { source: 'none', storeTickers: 0 };
      try {
        const secili = forecastPayload();
        const forecastData = secili.data;
        forecastSource = secili;

        if (forecastData && typeof forecastData === 'object' && forecastData.lookbacks && typeof forecastData.lookbacks === 'object') {
          forecasts = forecastData;
        } else if (forecastData && typeof forecastData === 'object') {
          // Legacy schema: { TICKER: {"15D (%)": ...} }
          Object.keys(forecastData).forEach((ticker) => {
            forecasts[String(ticker || '').toUpperCase()] = forecastData[ticker];
          });
        }
      } catch (err) {
        console.error('Warning: could not load forecast payload:', err.message);
      }

      payload.forecasts = forecasts;
      // Kaynagi arayuze tasi: depodan hesaplanmis bir tahminle duragan
      // bir dosyadan okunmus tahmin ayirt edilebilir olmali.
      payload.forecast_source = forecastSource.source;
      payload.forecast_store_tickers = forecastSource.storeTickers || 0;
      realizedVolCache.payload = payload;
      realizedVolCache.generatedAtMs = Date.now();
      return payload;
    })
    .finally(() => {
      realizedVolCache.inFlight = null;
    });

  return realizedVolCache.inFlight;
}

const xlsxBundlePath = path.join(__dirname, 'node_modules', 'xlsx', 'dist', 'xlsx.full.min.js');

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');

  if (url.pathname === '/xlsx.js') {
    try {
      const data = fs.readFileSync(xlsxBundlePath);
      res.writeHead(200, { 'Content-Type': 'application/javascript; charset=utf-8', 'Cache-Control': 'public, max-age=86400' });
      res.end(data);
    } catch(e) {
      res.writeHead(404); res.end('xlsx.js not found');
    }
    return;
  }

  if (url.pathname === '/risk-handler.js') {
    const js = [
      'window._riskPortfolio = [];',
      '',
      '// --- Helpers ---',
      'function _riskFindCol(cols, patterns) {',
      '  for (var pi=0; pi<patterns.length; pi++) {',
      '    var p=patterns[pi].toLowerCase();',
      '    for (var ci=0; ci<cols.length; ci++) { if (cols[ci].toLowerCase()===p) return cols[ci]; }',
      '  }',
      '  for (var pi=0; pi<patterns.length; pi++) {',
      '    var p=patterns[pi].toLowerCase();',
      '    for (var ci=0; ci<cols.length; ci++) { if (cols[ci].toLowerCase().indexOf(p)!==-1) return cols[ci]; }',
      '  }',
      '  return null;',
      '}',
      'function _riskNormCdf(x) {',
      '  var sign=x<0?-1:1, ax=Math.abs(x)/Math.SQRT2;',
      '  var t=1/(1+0.3275911*ax);',
      '  var erf=1-(((((1.061405429*t-1.453152027)*t+1.421413741)*t-0.284496736)*t+0.254829592)*t)*Math.exp(-ax*ax);',
      '  return 0.5*(1+sign*erf);',
      '}',
      'function _riskBsPrice(isCall,S,K,r,sigma,T) {',
      '  if(!(S>0)||!(K>0)||!(sigma>0)) return 0;',
      '  if(!(T>0)) return isCall ? Math.max(0,S-K) : Math.max(0,K-S);',
      '  var d1=(Math.log(S/K)+(r+0.5*sigma*sigma)*T)/(sigma*Math.sqrt(T));',
      '  var d2=d1-sigma*Math.sqrt(T);',
      '  return isCall ? S*_riskNormCdf(d1)-K*Math.exp(-r*T)*_riskNormCdf(d2)',
      '                : K*Math.exp(-r*T)*_riskNormCdf(-d2)-S*_riskNormCdf(-d1);',
      '}',
      'function _riskRandNorm() {',
      '  var u=0,v=0;',
      '  while(u===0) u=Math.random();',
      '  while(v===0) v=Math.random();',
      '  return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);',
      '}',
      'function _riskDrawHist(svgEl, pnls, varVal, conf, nSims, hold) {',
      '  var W=760, H=240, padL=72, padR=20, padT=28, padB=42, nBins=60;',
      '  var mn=pnls[0], mx=pnls[pnls.length-1];',
      '  if (mn===mx) { mx=mn+1; }',
      '  var binW=(mx-mn)/nBins, bins=new Array(nBins).fill(0);',
      '  for (var i=0;i<pnls.length;i++) { var bi=Math.floor((pnls[i]-mn)/binW); if(bi>=nBins)bi=nBins-1; bins[bi]++; }',
      '  var maxC=Math.max.apply(null,bins);',
      '  var cW=W-padL-padR, cH=H-padT-padB;',
      '  var xS=function(v){return padL+((v-mn)/(mx-mn))*cW;};',
      '  var yS=function(c){return padT+cH-(c/maxC)*cH;};',
      '  var html="";',
      '  // bars',
      '  for (var i=0;i<nBins;i++) {',
      '    var x0=padL+(i/nBins)*cW, x1=padL+((i+1)/nBins)*cW, y=yS(bins[i]);',
      '    var loss=mn+i*binW<0;',
      '    html+=\'<rect x="\'+x0.toFixed(1)+\'" y="\'+y.toFixed(1)+\'" width="\'+Math.max(0,(x1-x0-1)).toFixed(1)+\'" height="\'+(padT+cH-y).toFixed(1)+\'" fill="\'+(loss?"#ef4444":"#22c55e")+\'" opacity="0.85" rx="1"/>\';',
      '  }',
      '  // zero line',
      '  if (mn<0&&mx>0) {',
      '    var zx=xS(0);',
      '    html+=\'<line x1="\'+zx.toFixed(1)+\'" y1="\'+(padT-4)+\'" x2="\'+zx.toFixed(1)+\'" y2="\'+(padT+cH)+\'" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4,3"/>\';',
      '    html+=\'<text x="\'+zx.toFixed(1)+\'" y="\'+(padT-8)+\'" fill="#94a3b8" font-size="10" text-anchor="middle">0</text>\';',
      '  }',
      '  // VaR line',
      '  var vx=xS(varVal);',
      '  html+=\'<line x1="\'+vx.toFixed(1)+\'" y1="\'+(padT-4)+\'" x2="\'+vx.toFixed(1)+\'" y2="\'+(padT+cH)+\'" stroke="#facc15" stroke-width="2" stroke-dasharray="5,3"/>\';',
      '  html+=\'<text x="\'+((vx-4).toFixed(1))+\'" y="\'+(padT-8)+\'" fill="#facc15" font-size="10.5" font-weight="bold" text-anchor="end">VaR (\'+(conf*100).toFixed(0)+\'%) = \'+varVal.toFixed(2)+\'</text>\';',
      '  // x-axis line',
      '  html+=\'<line x1="\'+padL+\'" y1="\'+(padT+cH)+\'" x2="\'+(padL+cW)+\'" y2="\'+(padT+cH)+\'" stroke="#475569" stroke-width="1"/>\';',
      '  // y-axis line',
      '  html+=\'<line x1="\'+padL+\'" y1="\'+padT+\'" x2="\'+padL+\'" y2="\'+(padT+cH)+\'" stroke="#475569" stroke-width="1"/>\';',
      '  // x-axis ticks and labels',
      '  for (var i=0;i<=5;i++) {',
      '    var v=mn+(mx-mn)*(i/5), x=xS(v);',
      '    html+=\'<line x1="\'+x.toFixed(1)+\'" y1="\'+(padT+cH)+\'" x2="\'+x.toFixed(1)+\'" y2="\'+(padT+cH+5)+\'" stroke="#475569" stroke-width="1"/>\';',
      '    html+=\'<text x="\'+x.toFixed(1)+\'" y="\'+(padT+cH+17)+\'" fill="#94a3b8" font-size="10" text-anchor="middle">\'+v.toFixed(0)+\'</text>\';',
      '  }',
      '  // y-axis ticks and labels (frequency as %)',
      '  for (var i=0;i<=4;i++) {',
      '    var cnt=maxC*(i/4), y=yS(cnt);',
      '    html+=\'<line x1="\'+(padL-4)+\'" y1="\'+y.toFixed(1)+\'" x2="\'+padL+\'" y2="\'+y.toFixed(1)+\'" stroke="#475569" stroke-width="1"/>\';',
      '    html+=\'<text x="\'+(padL-7)+\'" y="\'+((y+4).toFixed(1))+\'" fill="#94a3b8" font-size="10" text-anchor="end">\'+(i*25)+\'%</text>\';',
      '  }',
      '  // x-axis title',
      '  html+=\'<text x="\'+((padL+cW/2)).toFixed(1)+\'" y="\'+(H-4)+\'" fill="#64748b" font-size="11" text-anchor="middle">Portfolio P&amp;L (per simulation path)</text>\';',
      '  // y-axis title (rotated)',
      '  html+=\'<text transform="rotate(-90,12,\'+(padT+cH/2)+\')" x="12" y="\'+(padT+cH/2)+\'" fill="#64748b" font-size="11" text-anchor="middle">Frequency</text>\';',
      '  // shaded loss tail',
      '  var tailX1=xS(mn), tailX2=vx;',
      '  html+=\'<rect x="\'+tailX1.toFixed(1)+\'" y="\'+padT+\'" width="\'+Math.max(0,tailX2-tailX1).toFixed(1)+\'" height="\'+cH+\'" fill="#facc15" opacity="0.06" rx="2"/>\';',
      '  // chart title',
      '  html+=\'<text x="\'+((padL+cW/2)).toFixed(1)+\'" y="15" fill="#e2e8f0" font-size="12" font-weight="bold" text-anchor="middle">P&amp;L Distribution — \'+nSims.toLocaleString()+\' paths, hold \'+hold+\'d</text>\';',
      '  svgEl.setAttribute("viewBox","0 0 "+W+" "+H);',
      '  svgEl.style.width="100%"; svgEl.style.height="auto";',
      '  svgEl.innerHTML=html;',
      '}',
      '',
      '// --- Import handler ---',
      'window.riskHandleImport = function(input) {',
      '  var file = input.files && input.files[0];',
      '  if (!file) return;',
      '  var reader = new FileReader();',
      '  reader.onload = function(ev) {',
      '    try {',
      '      if (typeof XLSX === "undefined") { alert("SheetJS not loaded. Refresh page."); return; }',
      '      var wb = XLSX.read(ev.target.result, { type: "array", cellDates: true });',
      '      var ws = wb.Sheets[wb.SheetNames[0]];',
      '      var data = XLSX.utils.sheet_to_json(ws, { defval: "" });',
      '      if (!data.length) { alert("Sheet is empty."); return; }',
      '      var cols = Object.keys(data[0]);',
      '      var cUnderlying = _riskFindCol(cols, ["Underlying","underlying","ticker","symbol"]);',
      '      var cType      = _riskFindCol(cols, ["P.optionType","optionType","option_type","type","put_call","putcall"]);',
      '      var cQty       = _riskFindCol(cols, ["Tot.netQty","netQty","net_qty","qty","quantity","position"]);',
      '      var cSpot      = _riskFindCol(cols, ["ulSpot","ul_spot","spot","underlying_price","underlyingprice"]);',
      '      var cStrike    = _riskFindCol(cols, ["P.strike","strike","strike_price","strikeprice"]);',
      '      var cExpiry    = _riskFindCol(cols, ["P.expiry","expiry","expiration","maturity","exp_date"]);',
      '      var cDelta     = _riskFindCol(cols, ["delta","Delta","P.delta","opt_delta","greeks_delta"]);',
      '      var missing = [];',
      '      if (!cUnderlying) missing.push("Underlying");',
      '      if (!cType)       missing.push("P.optionType");',
      '      if (!cQty)        missing.push("Tot.netQty");',
      '      if (!cSpot)       missing.push("ulSpot");',
      '      if (!cStrike)     missing.push("P.strike");',
      '      if (!cExpiry)     missing.push("P.expiry");',
      '      if (missing.length) {',
      '        alert("Could not find columns: " + missing.join(", ") + "\\nAvailable: " + cols.join(", "));',
      '        return;',
      '      }',
      '      var today = new Date(); today.setHours(0,0,0,0);',
      '      window._riskPortfolio = [];',
      '      var tbody = document.getElementById("riskPortfolioBody");',
      '      var wrap  = document.getElementById("riskPortfolioWrap");',
      '      var mcPanel = document.getElementById("riskMcPanel");',
      '      if (tbody) tbody.innerHTML = "";',
      '      var skipped=0, skipReasons={};',
      '      for (var i=0; i<data.length; i++) {',
      '        var row=data[i];',
      '        var u=String(row[cUnderlying]||"").trim();',
      '        var t=String(row[cType]||"").trim().toLowerCase();',
      '        var pt=(t==="put"||t==="p")?"put":(t==="call"||t==="c")?"call":null;',
      '        var qty=parseFloat(row[cQty]), spot=parseFloat(row[cSpot]);',
      '        var strike=parseFloat(row[cStrike]);',
      '        var delta=cDelta?parseFloat(row[cDelta]):NaN;',
      '        var expDate=null, rawExp=row[cExpiry];',
      '        if (rawExp instanceof Date && !isNaN(rawExp)) expDate=rawExp;',
      '        else if (rawExp) expDate=new Date(rawExp);',
      '        var dtm=(expDate&&!isNaN(expDate))?Math.max(0,Math.round((expDate-today)/86400000)):null;',
      '        var reason=!u?"no underlying":!pt?("bad type=["+t+"]"):isNaN(qty)?"bad qty":isNaN(spot)?"bad spot":isNaN(strike)?"bad strike":dtm===null?("bad expiry=["+rawExp+"]"):null;',
      '        if (reason) { skipped++; skipReasons[reason]=(skipReasons[reason]||0)+1; continue; }',
      '        var pos={underlying:u,posType:pt,qty:qty,spot:spot,strike:strike,delta:delta,dtm:dtm,',
      '                 expiry:expDate?expDate.toISOString().slice(0,10):""};',
      '        window._riskPortfolio.push(pos);',
      '        if (tbody) {',
      '          var tr=document.createElement("tr");',
      '          tr.style.background=i%2===0?"#f8fafc":"#fff";',
      '          var qtyColor=qty>=0?"#16a34a":"#dc2626";',
      '          var deltaStr=isNaN(delta)?"-":delta.toFixed(4);',
      '          tr.innerHTML=',
      '            "<td style=\\"padding:4px 10px;text-align:left\\">"+u+"</td>"+',
      '            "<td style=\\"padding:4px 10px;text-align:left;text-transform:capitalize\\">"+pt+"</td>"+',
      '            "<td style=\\"padding:4px 10px;text-align:right\\">"+strike.toFixed(2)+"</td>"+',
      '            "<td style=\\"padding:4px 10px;text-align:left\\">"+pos.expiry+"</td>"+',
      '            "<td style=\\"padding:4px 10px;text-align:right\\">"+dtm+"</td>"+',
      '            "<td style=\\"padding:4px 10px;text-align:right\\">"+spot.toFixed(3)+"</td>"+',
      '            "<td style=\\"padding:4px 10px;text-align:right;font-weight:600;color:"+qtyColor+"\\">"+qty.toFixed(0)+"</td>"+',
      '            "<td style=\\"padding:4px 10px;text-align:right\\">"+deltaStr+"</td>";',
      '          tbody.appendChild(tr);',
      '        }',
      '      }',
      '      var hasPositions = window._riskPortfolio.length > 0;',
      '      if (wrap) wrap.style.display = hasPositions ? "" : "none";',
      '      if (mcPanel) mcPanel.style.display = hasPositions ? "" : "none";',
      '      var statusEl=document.getElementById("riskStatus");',
      '      var skipDetail=Object.keys(skipReasons).map(function(k){return k+"x"+skipReasons[k];}).join(", ");',
      '      var msg=window._riskPortfolio.length+" position(s) imported"+(skipped?" ("+skipped+" skipped: "+skipDetail+")":"")+" — adjust parameters and click Run VaR Simulation.";',
      '      if (statusEl) statusEl.textContent=msg;',
      '      if (skipped&&!hasPositions) alert("All rows skipped!\\n"+msg);',
      '    } catch(err) { alert("Parse error: "+err.message); }',
      '    input.value="";',
      '  };',
      '  reader.readAsArrayBuffer(file);',
      '};',
      '',
      '// --- MC Simulation ---',
      '// --- Saf simulasyon cekirdegi -------------------------------------',
      '// DOM ve ag erisimi yoktur; girdi/cikti tamamen parametrelerle gecer.',
      '// Bu ayrim test edilebilirlik icindir: korelasyon ve ayni-dayanak',
      '// tutarliligi dogrudan bu fonksiyon uzerinden dogrulanabiliyor.',
      '// Dondurur: { pnls, stMap, curVal }',
      'window._riskSimulate = function(portfolio, opts) {',
      '  opts = opts || {};',
      '  var nSims = opts.nSims || 10000;',
      '  var dt    = opts.dt;',
      '  var mult  = opts.mult != null ? opts.mult : 100;',
      '  var rho   = Math.max(0, Math.min(0.99, opts.rho != null ? opts.rho : 0.5));',
      '  var rateMap = opts.rateMap || {}, volMap = opts.volMap || {};',
      '  var vR = opts.varsayilanR != null ? opts.varsayilanR : 0.40;',
      '  var vV = opts.varsayilanVol != null ? opts.varsayilanVol : 0.30;',
      '  var rnd = opts.rnd || _riskRandNorm;',
      '',
      '  function oranOf(tk){ return rateMap[tk]!=null?rateMap[tk]:vR; }',
      '  function volOf(tk){ return volMap[tk]||vV; }',
      '',
      '  var curVal=0;',
      '  for (var i=0;i<portfolio.length;i++) {',
      '    var pos=portfolio[i];',
      '    var tk=String(pos.underlying||"").toUpperCase();',
      '    var T0=pos.dtm/365;',
      '    curVal+=_riskBsPrice(pos.posType==="call",pos.spot,pos.strike,oranOf(tk),volOf(tk),T0)*pos.qty*mult;',
      '  }',
      '',
      '  // Dayanak listesi: sok POZISYON basina degil DAYANAK basina uretilir,',
      '  // boylece ayni hissedeki iki pozisyon tek senaryoda ayni fiyati gorur.',
      '  var dayanaklar=[], gorulen={};',
      '  for (var i=0;i<portfolio.length;i++) {',
      '    var u=String(portfolio[i].underlying||"").toUpperCase();',
      '    if (!gorulen[u]) {',
      '      gorulen[u]=1;',
      '      dayanaklar.push({ tk:u, spot:portfolio[i].spot, r:oranOf(u), sigma:volOf(u) });',
      '    }',
      '  }',
      '',
      '  // Tek faktorlu korelasyon:  z_i = sqrt(rho)*z_piyasa + sqrt(1-rho)*z_ozgu',
      '  var wMkt=Math.sqrt(rho), wIdio=Math.sqrt(1-rho);',
      '  var pnls=new Array(nSims), stMap={}, senaryoST={};',
      '  for (var s=0;s<nSims;s++) {',
      '    var portPnl=0;',
      '    var zMkt=rnd();',
      '    for (var d=0;d<dayanaklar.length;d++) {',
      '      var dv=dayanaklar[d];',
      '      var z=wMkt*zMkt+wIdio*rnd();',
      '      var STd=dv.spot*Math.exp((dv.r-0.5*dv.sigma*dv.sigma)*dt+dv.sigma*Math.sqrt(dt)*z);',
      '      senaryoST[dv.tk]=STd;',
      '      if (!stMap[dv.tk]) stMap[dv.tk]={spot:dv.spot,r:dv.r,sigma:dv.sigma,minST:STd,maxST:STd};',
      '      else { if(STd<stMap[dv.tk].minST) stMap[dv.tk].minST=STd; if(STd>stMap[dv.tk].maxST) stMap[dv.tk].maxST=STd; }',
      '    }',
      '    for (var i=0;i<portfolio.length;i++) {',
      '      var pos=portfolio[i];',
      '      var tk=String(pos.underlying||"").toUpperCase();',
      '      var r=oranOf(tk), sigma=volOf(tk);',
      '      var T0=pos.dtm/365, Th=Math.max(T0-dt,0);',
      '      var isCall=pos.posType==="call";',
      '      var curP=_riskBsPrice(isCall,pos.spot,pos.strike,r,sigma,T0);',
      '      var futP=_riskBsPrice(isCall,senaryoST[tk],pos.strike,r,sigma,Th);',
      '      portPnl+=(futP-curP)*pos.qty*mult;',
      '    }',
      '    pnls[s]=portPnl;',
      '  }',
      '  return { pnls: pnls, stMap: stMap, curVal: curVal };',
      '};',
      '',
      '// --- Stres testi / senaryo analizi ---------------------------------',
      '// Her senaryo portfoyu saptirilmis piyasa kosullarinda yeniden',
      '// degerler. Iki ayri sey raporlanir:',
      '//   anlikEtki : sokun kendisinden dogan K/Z (dagilim degil, tek sayi)',
      '//   var       : sok sonrasi durumda hesaplanan VaR',
      '// Tek bir VaR sayisi "ne olursa ne olur" sorusunu cevaplamaz; bu',
      '// katman onu tamamlar.',
      'window.RISK_SENARYOLAR = [',
      '  { ad: "Baseline",            spotSok: 0,     volCarpan: 1.0, rho: null },',
      '  { ad: "Spot -10%",           spotSok: -0.10, volCarpan: 1.0, rho: null },',
      '  { ad: "Spot -20%",           spotSok: -0.20, volCarpan: 1.0, rho: null },',
      '  { ad: "Spot +10%",           spotSok:  0.10, volCarpan: 1.0, rho: null },',
      '  { ad: "Volatility +50%",     spotSok: 0,     volCarpan: 1.5, rho: null },',
      '  { ad: "Volatility x2",       spotSok: 0,     volCarpan: 2.0, rho: null },',
      '  { ad: "Crisis: -20%, vol x2", spotSok: -0.20, volCarpan: 2.0, rho: 0.95 }',
      '];',
      '',
      'window._riskStresTest = function(portfolio, opts, senaryolar) {',
      '  senaryolar = senaryolar || window.RISK_SENARYOLAR;',
      '  var baz = window._riskSimulate(portfolio, opts);',
      '  var sonuclar = [];',
      '  for (var i=0;i<senaryolar.length;i++) {',
      '    var sen = senaryolar[i];',
      '',
      '    // Spot soku: pozisyonlarin dayanak fiyati topluca kaydirilir.',
      '    var sokluPortfoy = portfolio.map(function(p){',
      '      return Object.assign({}, p, { spot: p.spot * (1 + sen.spotSok) });',
      '    });',
      '',
      '    // Volatilite soku: tum dayanaklarin oynakligi carpanla olceklenir.',
      '    var sokluVol = {};',
      '    Object.keys(opts.volMap||{}).forEach(function(k){',
      '      sokluVol[k] = opts.volMap[k] * sen.volCarpan;',
      '    });',
      '',
      '    var senOpts = Object.assign({}, opts, {',
      '      volMap: sokluVol,',
      '      rho: (sen.rho !== null && sen.rho !== undefined) ? sen.rho : opts.rho,',
      '      varsayilanVol: (opts.varsayilanVol||0.30) * sen.volCarpan',
      '    });',
      '    var sim = window._riskSimulate(sokluPortfoy, senOpts);',
      '',
      '    var sirali = sim.pnls.slice().sort(function(a,b){return a-b;});',
      '    var idx = Math.floor((1-(opts.conf||0.99))*sirali.length);',
      '    sonuclar.push({',
      '      ad: sen.ad,',
      '      portfoyDegeri: sim.curVal,',
      '      anlikEtki: sim.curVal - baz.curVal,',
      '      anlikEtkiYuzde: baz.curVal !== 0 ? (sim.curVal-baz.curVal)/Math.abs(baz.curVal)*100 : null,',
      '      var: sirali[idx],',
      '      rho: senOpts.rho',
      '    });',
      '  }',
      '  return sonuclar;',
      '};',
      '',
      '// Piyasa verisini cekip (vol ve faiz haritalari) geri cagiriyi calistirir.',
      '// Hem VaR hem stres testi ayni girdileri kullanir.',
      'window._riskPiyasaVerisi = function(volWin, sonra) {',
      '  Promise.all([',
      '    fetch("/api/realized-vols").then(function(r){return r.json();}).catch(function(){return {};}),',
      '    fetch("/api/futures-rates?all=1").then(function(r){return r.json();}).catch(function(){return {};})',
      '  ]).then(function(res) {',
      '    var rvData=res[0], rateData=res[1], volMap={}, rateMap={};',
      '    if (rvData&&rvData.rows) {',
      '      for (var i=0;i<rvData.rows.length;i++) {',
      '        var row=rvData.rows[i];',
      '        var v=parseFloat(row[volWin]);',
      '        if (!isNaN(v)&&v>0) volMap[String(row.Ticker||"").toUpperCase()]=v/100;',
      '      }',
      '    }',
      '    if (rateData&&rateData.rates_by_ticker) {',
      '      Object.keys(rateData.rates_by_ticker).forEach(function(tk) {',
      '        var codes=rateData.rates_by_ticker[tk];',
      '        var fc=Object.keys(codes)[0];',
      '        if (fc) { var rec=codes[fc]; var rv=rec.adj_rate!==undefined?rec.adj_rate:rec.rate;',
      '                  if(rv!=null) rateMap[tk.toUpperCase()]=Number(rv); }',
      '      });',
      '    }',
      '    sonra(volMap, rateMap);',
      '  });',
      '};',
      '',
      '// Denetim izine kayit dusurur. Basarisizligi akisi bozmamali: izin',
      '// tutulamamasi risk hesabini durdurmaz, ama sessizce de gecmez.',
      'window._denetimYaz = function(tip, veri) {',
      '  return fetch("/api/audit", {',
      '    method: "POST",',
      '    headers: { "Content-Type": "application/json" },',
      '    body: JSON.stringify({ tip: tip, veri: veri })',
      '  }).then(function(r){ return r.json(); })',
      '    .catch(function(e){ console.warn("denetim izine yazilamadi:", e); return null; });',
      '};',
      '',
      '// --- Risk raporu ----------------------------------------------------',
      '// CSV secildi: XLSX kutuphanesi node_modules a bagli ve taze bir',
      '// klonda bulunmayabiliyor; rapor uretimi bagimliliga takilmamali.',
      '// CSV her tabloda dogrudan aciliyor.',
      'window._csvKacis = function(v) {',
      '  if (v === null || v === undefined) return "";',
      '  var s = String(v);',
      '  var t = String.fromCharCode(34);   // cift tirnak: kacis zinciri olusmasin',
      '  if (s.indexOf(";") === -1 && s.indexOf(t) === -1 && s.indexOf("\\n") === -1) return s;',
      '  return t + s.split(t).join(t + t) + t;',
      '};',
      '',
      '// Saf fonksiyon: veri alir, CSV metni dondurur. Test edilebilir.',
      'window._riskRaporCsv = function(veri) {',
      '  var sat = [];',
      '  var ekle = function(dizi) { sat.push(dizi.map(window._csvKacis).join(";")); };',
      '',
      '  ekle(["Derivex Risk Report"]);',
      '  ekle(["Generated", veri.tarih || ""]);',
      '  ekle(["Data mode", veri.mockMu ? "MOCK (generated data)" : "LIVE"]);',
      '  sat.push("");',
      '',
      '  ekle(["PARAMETERS"]);',
      '  ekle(["Holding period (days)", veri.hold]);',
      '  ekle(["Simulations", veri.nSims]);',
      '  ekle(["Confidence level", (veri.conf*100).toFixed(0) + "%"]);',
      '  ekle(["Vol window", veri.volWin]);',
      '  ekle(["Contract multiplier", veri.mult]);',
      '  ekle(["Correlation (rho)", veri.rho]);',
      '  sat.push("");',
      '',
      '  if (veri.portfoy && veri.portfoy.length) {',
      '    ekle(["PORTFOLIO"]);',
      '    ekle(["Underlying","Type","Strike","Expiry","DTM","Spot","Qty","Delta"]);',
      '    veri.portfoy.forEach(function(p) {',
      '      ekle([p.underlying, p.posType, p.strike, p.expiry, p.dtm, p.spot, p.qty, p.delta]);',
      '    });',
      '    sat.push("");',
      '  }',
      '',
      '  if (veri.var) {',
      '    ekle(["VaR RESULTS"]);',
      '    ekle(["Metric","Value"]);',
      '    ekle(["Portfolio value", veri.var.portfoyDegeri]);',
      '    ekle(["VaR (absolute)", veri.var.varAbs]);',
      '    ekle(["VaR (%)", veri.var.varPct]);',
      '    ekle(["CVaR (ES)", veri.var.cvar]);',
      '    ekle(["Mean P&L", veri.var.mean]);',
      '    ekle(["Std dev P&L", veri.var.std]);',
      '    sat.push("");',
      '  }',
      '',
      '  if (veri.stres && veri.stres.length) {',
      '    ekle(["STRESS TEST"]);',
      '    ekle(["Scenario","Portfolio value","Immediate impact","Impact %","VaR","Rho"]);',
      '    veri.stres.forEach(function(r) {',
      '      ekle([r.ad, r.portfoyDegeri, r.anlikEtki,',
      '            r.anlikEtkiYuzde === null ? "" : r.anlikEtkiYuzde, r.var, r.rho]);',
      '    });',
      '  }',
      '',
      '  return sat.join(String.fromCharCode(10));',
      '};',
      '',
      'window.riskRaporIndir = function() {',
      '  var portfoy = window._riskPortfolio || [];',
      '  if (!portfoy.length) { alert("Load a portfolio first."); return; }',
      '  var deger = function(id) { var e=document.getElementById(id); return e ? e.value : ""; };',
      '  var sayi  = function(id) { var v=parseFloat(deger(id)); return isNaN(v) ? null : v; };',
      '',
      '  var veri = {',
      '    tarih: new Date().toLocaleString("tr-TR"),',
      '    mockMu: !!document.querySelector("[data-mock-banner]"),',
      '    hold: deger("riskMcHold"), nSims: deger("riskMcSims"),',
      '    conf: parseFloat(deger("riskMcConf")) || 0.99,',
      '    volWin: deger("riskMcVolWin") + "D RV",',
      '    mult: deger("riskMcMult"), rho: deger("riskMcRho"),',
      '    portfoy: portfoy,',
      '    var: (deger("riskMcVarAbs") ? {',
      '      portfoyDegeri: sayi("riskMcCurVal"), varAbs: sayi("riskMcVarAbs"),',
      '      varPct: deger("riskMcVarPct"), cvar: sayi("riskMcCvar"),',
      '      mean: sayi("riskMcMean"), std: sayi("riskMcStd")',
      '    } : null),',
      '    stres: window._sonStresSonucu || null',
      '  };',
      '',
      '  window._denetimYaz("rapor_indirildi", {',
      '    pozisyon: portfoy.length, modMock: veri.mockMu',
      '  });',
      '  var csv = "\ufeff" + window._riskRaporCsv(veri);   // BOM: Excel UTF-8 icin',
      '  var blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });',
      '  var a = document.createElement("a");',
      '  a.href = URL.createObjectURL(blob);',
      '  a.download = "derivex-risk-raporu-" + new Date().toISOString().slice(0,19).replace(/[:T]/g,"-") + ".csv";',
      '  document.body.appendChild(a); a.click();',
      '  setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 1000);',
      '};',
      '',
      'window.riskRunStres = function() {',
      '  var portfolio=window._riskPortfolio||[];',
      '  if (!portfolio.length) { alert("Load a portfolio first."); return; }',
      '  var hold   = parseInt((document.getElementById("riskMcHold")||{value:1}).value,10)||1;',
      '  var nSims  = parseInt((document.getElementById("riskMcSims")||{value:10000}).value,10)||10000;',
      '  var conf   = parseFloat((document.getElementById("riskMcConf")||{value:0.99}).value)||0.99;',
      '  var volWin = ((document.getElementById("riskMcVolWin")||{value:"30"}).value)+"D RV";',
      '  var mult   = parseFloat((document.getElementById("riskMcMult")||{value:100}).value)||100;',
      '  var rhoIn  = parseFloat((document.getElementById("riskMcRho")||{value:0.5}).value);',
      '  var rho    = isNaN(rhoIn) ? 0.5 : Math.max(0, Math.min(0.99, rhoIn));',
      '  var statusEl=document.getElementById("riskStatus");',
      '  if (statusEl) statusEl.textContent="Running stress scenarios...";',
      '',
      '  window._riskPiyasaVerisi(volWin, function(volMap, rateMap) {',
      '    setTimeout(function() {',
      '      var sonuc = window._riskStresTest(portfolio, {',
      '        nSims:nSims, dt:hold/365, mult:mult, rho:rho, conf:conf,',
      '        volMap:volMap, rateMap:rateMap',
      '      });',
      '      window._sonStresSonucu = sonuc;   // rapor bu sonucu kullanir',
      '      window._denetimYaz("stres_testi", {',
      '        senaryo: sonuc.length, pozisyon: portfolio.length, rho: rho,',
      '        enKotuVar: Math.min.apply(null, sonuc.map(function(r){ return r.var; }))',
      '      });',
      '      var tb=document.getElementById("riskStresBody");',
      '      if (tb) {',
      '        tb.innerHTML="";',
      '        sonuc.forEach(function(r,i) {',
      '          var tr=document.createElement("tr");',
      '          tr.style.background = i%2===0 ? "#f8fafc" : "#fff";',
      '          var etkiRenk = r.anlikEtki >= 0 ? "#16a34a" : "#dc2626";',
      '          var h="padding:4px 10px;";',
      '          var f=function(x){ return (x==null||isNaN(x)) ? "-" : x.toLocaleString("tr-TR",{maximumFractionDigits:2}); };',
      '          tr.innerHTML=',
      '            "<td style=\'"+h+"text-align:left;font-weight:"+(i===0?"700":"400")+"\'>"+r.ad+"</td>"+',
      '            "<td style=\'"+h+"text-align:right\'>"+f(r.portfoyDegeri)+"</td>"+',
      '            "<td style=\'"+h+"text-align:right;color:"+etkiRenk+"\'>"+f(r.anlikEtki)+"</td>"+',
      '            "<td style=\'"+h+"text-align:right;color:"+etkiRenk+"\'>"+(r.anlikEtkiYuzde==null?"-":r.anlikEtkiYuzde.toFixed(2)+"%")+"</td>"+',
      '            "<td style=\'"+h+"text-align:right;color:#dc2626\'>"+f(r.var)+"</td>";',
      '          tb.appendChild(tr);',
      '        });',
      '      }',
      '      var w=document.getElementById("riskStresWrap");',
      '      if (w) w.style.display="";',
      '      if (statusEl) statusEl.textContent="Stress test done. "+sonuc.length+" scenarios, "+nSims.toLocaleString()+" paths.";',
      '    }, 20);',
      '  });',
      '};',
      '',
      'window.riskRunMC = function() {',
      '  var portfolio=window._riskPortfolio||[];',
      '  if (!portfolio.length) { alert("No portfolio loaded. Import an XLSX file first."); return; }',
      '  var hold   = parseInt((document.getElementById("riskMcHold")||{value:1}).value,10)||1;',
      '  var nSims  = parseInt((document.getElementById("riskMcSims")||{value:10000}).value,10)||10000;',
      '  var conf   = parseFloat((document.getElementById("riskMcConf")||{value:0.99}).value)||0.99;',
      '  var volWin = ((document.getElementById("riskMcVolWin")||{value:"30"}).value)+"D RV";',
      '  var mult   = parseFloat((document.getElementById("riskMcMult")||{value:100}).value)||100;',
      '  var rhoIn  = parseFloat((document.getElementById("riskMcRho")||{value:0.5}).value);',
      '  var rho    = isNaN(rhoIn) ? 0.5 : Math.max(0, Math.min(0.99, rhoIn));',
      '  var statusEl=document.getElementById("riskStatus");',
      '  if (statusEl) statusEl.textContent="Fetching market data...";',
      '  Promise.all([',
      '    fetch("/api/realized-vols").then(function(r){return r.json();}).catch(function(){return {};}),',
      '    fetch("/api/futures-rates?all=1").then(function(r){return r.json();}).catch(function(){return {};})',
      '  ]).then(function(res) {',
      '    var rvData=res[0], rateData=res[1];',
      '    var volMap={}, rateMap={};',
      '    if (rvData&&rvData.rows) {',
      '      for (var i=0;i<rvData.rows.length;i++) {',
      '        var row=rvData.rows[i];',
      '        var tk=String(row.Ticker||"").toUpperCase();',
      '        var v=parseFloat(row[volWin]);',
      '        if (!isNaN(v)&&v>0) volMap[tk]=v/100;',
      '      }',
      '    }',
      '    if (rateData&&rateData.rates_by_ticker) {',
      '      Object.keys(rateData.rates_by_ticker).forEach(function(tk) {',
      '        var codes=rateData.rates_by_ticker[tk];',
      '        var fc=Object.keys(codes)[0];',
      '        if (fc) { var rec=codes[fc]; var rv=rec.adj_rate!==undefined?rec.adj_rate:rec.rate; if(rv!=null) rateMap[tk.toUpperCase()]=Number(rv); }',
      '      });',
      '    }',
      '    if (statusEl) statusEl.textContent="Running "+nSims.toLocaleString()+" simulations...";',
      '    setTimeout(function() {',
      '      try {',
      '        var dt=hold/365;',
      '        var sim=window._riskSimulate(portfolio,{',
      '          nSims:nSims, dt:dt, mult:mult, rho:rho,',
      '          rateMap:rateMap, volMap:volMap',
      '        });',
      '        var pnls=sim.pnls, stMap=sim.stMap, curVal=sim.curVal;',
      '        window._denetimYaz("var_kosusu", {',
      '          pozisyon: portfolio.length, nSims: nSims, conf: conf,',
      '          hold: hold, rho: rho, volWin: volWin, portfoyDegeri: curVal',
      '        });',
      '        pnls.sort(function(a,b){return a-b;});',
      '        var varIdx=Math.floor((1-conf)*nSims);',
      '        var varVal=pnls[varIdx];',
      '        var cvarSum=0; for(var i=0;i<=varIdx;i++) cvarSum+=pnls[i]; var cvarVal=cvarSum/(varIdx+1);',
      '        var mean=0; for(var i=0;i<nSims;i++) mean+=pnls[i]; mean/=nSims;',
      '        var vari=0; for(var i=0;i<nSims;i++) vari+=(pnls[i]-mean)*(pnls[i]-mean); var std=Math.sqrt(vari/nSims);',
      '        var resEl=document.getElementById("riskMcResults"); if(resEl) resEl.style.display="";',
      '        var se=function(id,v){var e=document.getElementById(id);if(e)e.value=v;};',
      '        se("riskMcCurVal",curVal.toFixed(2));',
      '        se("riskMcVarAbs",varVal.toFixed(2));',
      '        se("riskMcVarPct",curVal!==0?((varVal/Math.abs(curVal))*100).toFixed(2)+"%":"-");',
      '        se("riskMcCvar",cvarVal.toFixed(2));',
      '        se("riskMcMean",mean.toFixed(2));',
      '        se("riskMcStd",std.toFixed(2));',
      '        var svgEl=document.getElementById("riskMcSvg"); if(svgEl) _riskDrawHist(svgEl,pnls,varVal,conf,nSims,hold);',
      '        var legendEl=document.getElementById("riskMcLegend");',
      '        if(legendEl){',
      '          var lossCount=0; for(var i=0;i<pnls.length;i++){if(pnls[i]<0)lossCount++;}',
      '          var lossPct=(lossCount/nSims*100).toFixed(1);',
      '          legendEl.innerHTML=',
      '            \'<div style="font-size:13px;font-weight:700;color:#e2e8f0;margin-bottom:10px;">How to read</div>\'+',
      '            \'<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;"><span style="display:inline-block;width:14px;height:14px;background:#ef4444;border-radius:2px;flex-shrink:0"></span><span style="font-size:12px;color:#94a3b8;">Loss scenarios (P&amp;L &lt; 0)</span></div>\'+',
      '            \'<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;"><span style="display:inline-block;width:14px;height:14px;background:#22c55e;border-radius:2px;flex-shrink:0"></span><span style="font-size:12px;color:#94a3b8;">Profit scenarios (P&amp;L &gt; 0)</span></div>\'+',
      '            \'<div style="display:flex;align-items:center;gap:6px;margin-bottom:12px;"><span style="display:inline-block;width:14px;height:3px;background:#facc15;flex-shrink:0"></span><span style="font-size:12px;color:#94a3b8;">VaR threshold</span></div>\'+',
      '            \'<div style="background:#1e293b;border-radius:6px;padding:10px;font-size:11.5px;color:#94a3b8;line-height:1.6;">\'+',
      '            \'<b style="color:#e2e8f0;">VaR (\'+(conf*100).toFixed(0)+\'%)</b> means: in the worst \'+(((1-conf)*100).toFixed(0))+\'% of scenarios, the loss exceeds <b style="color:#facc15;">\'+Math.abs(varVal).toFixed(2)+\'</b>.<br><br>\'+',
      '            \'<b style="color:#e2e8f0;">CVaR</b> is the average loss in those tail scenarios.<br><br>\'+',
      '            \'Paths with a loss: <b style="color:#ef4444;">\'+lossPct+\'%</b> of \'+nSims.toLocaleString()+\' simulations.\'+',
      '            \'</div>\';',
      '        }',
      '        if(statusEl) statusEl.textContent="Done. "+nSims.toLocaleString()+" paths | Conf: "+(conf*100).toFixed(0)+"% | Hold: "+hold+"d | Vol window: "+volWin;',
      '        var debugEl=document.getElementById("riskMcDebugTable");',
      '        if(debugEl){',
      '          var tks=Object.keys(stMap);',
      '          var rows=tks.map(function(tk){',
      '            var d=stMap[tk];',
      '            var chgMin=((d.minST/d.spot-1)*100).toFixed(2);',
      '            var chgMax=((d.maxST/d.spot-1)*100).toFixed(2);',
      '            return \'<tr><td>\'+tk+\'</td><td>\'+d.spot.toFixed(3)+\'</td><td>\'+(d.r*100).toFixed(1)+\'%</td><td>\'+(d.sigma*100).toFixed(1)+\'%</td><td style="color:#ef4444">\'+d.minST.toFixed(3)+\' (\'+chgMin+\'%)</td><td style="color:#22c55e">\'+d.maxST.toFixed(3)+\' (+\'+chgMax+\'%)</td></tr>\';',
      '          }).join("");',
      '          debugEl.innerHTML=\'<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:12px;"><thead><tr style="background:#1e293b;color:#94a3b8;"><th style="padding:5px 8px;text-align:left">Underlying</th><th style="padding:5px 8px;text-align:right">Spot</th><th style="padding:5px 8px;text-align:right">Rate (r)</th><th style="padding:5px 8px;text-align:right">Vol (σ)</th><th style="padding:5px 8px;text-align:right">Min ST</th><th style="padding:5px 8px;text-align:right">Max ST</th></tr></thead><tbody>\'+rows+\'</tbody></table>\';',
      '        }',
      '      } catch(err) { if(statusEl) statusEl.textContent="Sim error: "+err.message; alert("Sim error: "+err.message); }',
      '    }, 20);',
      '  }).catch(function(err){ if(statusEl) statusEl.textContent="Fetch error: "+err.message; });',
      '};',
      '',
      'document.addEventListener("change", function(e) {',
      '  if (e.target && e.target.id === "riskImportFile") window.riskHandleImport(e.target);',
      '});'
    ].join('\n');
    res.writeHead(200, { 'Content-Type': 'application/javascript; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(js);
    return;
  }

  if (url.pathname === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: true, page: 'market-shell' }));
    return;
  }

  if (url.pathname === '/api/spot' && req.method === 'GET') {
    const ticker = (url.searchParams.get('ticker') || '').trim().toUpperCase();
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    if (url.searchParams.get('all') === '1') {
      res.end(JSON.stringify({ ok: true, spotByTicker: serverState.spotByTicker }));
      return;
    }
    res.end(JSON.stringify({ ok: true, ticker, data: ticker ? (serverState.spotByTicker[ticker] || null) : null }));
    return;
  }

  if (url.pathname === '/api/spot' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 1_000_000) req.destroy();
    });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        const ticker = String(payload.ticker || '').toUpperCase();
        const spotMid = Number(payload.spot_mid);
        if (!ticker || !Number.isFinite(spotMid)) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'ticker and numeric spot_mid are required' }));
          return;
        }
        serverState.spotByTicker[ticker] = { spot_mid: spotMid, ts: payload.ts || new Date().toISOString() };
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, stored: serverState.spotByTicker[ticker] }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json body' }));
      }
    });
    return;
  }

  if (url.pathname === '/api/futures-rates' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    if (url.searchParams.get('all') === '1') {
      // Build dividend-adjusted rates server-side so clients never need to recalculate
      const adjustedByTicker = {};
      for (const [tkr, codes] of Object.entries(serverState.futuresRatesByTicker)) {
        adjustedByTicker[tkr] = {};
        const divEntry = (serverState.dividendsByTicker || {})[tkr] || null;
        for (const [code, rec] of Object.entries(codes)) {
          const adj = dividendAdjustedRateServer(rec, divEntry, code);
          adjustedByTicker[tkr][code] = {
            ...rec,
            adj_rate: adj.rate, adj_adjusted: adj.adjusted,
            adj_bid_rate: adj.bidRate, adj_bid_adjusted: adj.bidAdjusted,
            adj_ask_rate: adj.askRate, adj_ask_adjusted: adj.askAdjusted,
          };
        }
      }
      res.end(JSON.stringify({ ok: true, rates_by_ticker: adjustedByTicker, maturities: serverState.futuresMeta }));
      return;
    }
    const ticker = (url.searchParams.get('ticker') || '').trim().toUpperCase();
    res.end(JSON.stringify({ ok: true, ticker, rates: ticker ? (serverState.futuresRatesByTicker[ticker] || null) : null }));
    return;
  }

  if (url.pathname === '/api/futures-rates' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 2_000_000) req.destroy();
    });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        const ratesByTicker = payload.rates_by_ticker || {};
        const maturities = Array.isArray(payload.maturities) ? payload.maturities : [];
        const normalized = {};

        for (const [tickerRaw, rateObj] of Object.entries(ratesByTicker)) {
          const ticker = String(tickerRaw || '').toUpperCase();
          if (!ticker || typeof rateObj !== 'object' || rateObj === null) continue;
          normalized[ticker] = {};

          for (const [codeRaw, rec] of Object.entries(rateObj)) {
            const code = String(codeRaw || '').toUpperCase();
            if (!code) continue;

            const numOrNull = (v) => (v !== null && v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null);

            if (rec !== null && typeof rec === 'object' && Object.prototype.hasOwnProperty.call(rec, 'rate')) {
              normalized[ticker][code] = {
                rate: numOrNull(rec.rate),
                fut_mid: numOrNull(rec.fut_mid),
                spot_mid: numOrNull(rec.spot_mid),
                dtm: numOrNull(rec.dtm),
                // Bid/ask sides — bid yield from futures bid vs spot bid,
                // ask yield from futures ask vs spot ask (kept separate
                // from mid, which options IV/delta still uses).
                spot_bid: numOrNull(rec.spot_bid),
                spot_ask: numOrNull(rec.spot_ask),
                fut_bid: numOrNull(rec.fut_bid),
                fut_ask: numOrNull(rec.fut_ask),
                bid_rate: numOrNull(rec.bid_rate),
                ask_rate: numOrNull(rec.ask_rate),
              };
            } else {
              normalized[ticker][code] = {
                rate: numOrNull(rec),
                fut_mid: null,
                spot_mid: null,
                dtm: null,
                spot_bid: null,
                spot_ask: null,
                fut_bid: null,
                fut_ask: null,
                bid_rate: null,
                ask_rate: null,
              };
            }
          }
        }

        serverState.futuresRatesByTicker = normalized;
        serverState.futuresMeta = maturities;

        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, tickers: Object.keys(normalized).length }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json body' }));
      }
    });
    return;
  }

  if (url.pathname === '/api/options-chain' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    if (url.searchParams.get('all') === '1') {
      res.end(JSON.stringify({ ok: true, options_by_ticker: serverState.optionsChainByTicker }));
      return;
    }
    const ticker = (url.searchParams.get('ticker') || '').trim().toUpperCase();
    res.end(JSON.stringify({ ok: true, ticker, options: ticker ? (serverState.optionsChainByTicker[ticker] || []) : [] }));
    return;
  }

  if (url.pathname === '/api/options-chain' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 2_000_000) req.destroy();
    });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        const ticker = String(payload.ticker || '').trim().toUpperCase();
        const options = Array.isArray(payload.options) ? payload.options : [];

        if (!ticker) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'ticker is required' }));
          return;
        }

        const normalized = options.map((row) => {
          const expiryRaw = row && row.expiry !== undefined && row.expiry !== null ? String(row.expiry) : '';
          const expiryDigits = expiryRaw.replace(/\D/g, '');
          const expiry = expiryDigits ? (expiryDigits.length >= 4 ? expiryDigits.slice(0, 4) : expiryDigits.padStart(4, '0')) : null;
          const strikeRaw = row && row.strike !== undefined ? row.strike : null;
          const strikeNum = Number(strikeRaw);
          const toNum = (v) => typeof v === 'number' ? v : (typeof v === 'string' ? Number(v) : null);
          return {
            expiry,
            strike: Number.isFinite(strikeNum) ? strikeNum : null,
            dtm: toNum(row?.dtm),
            rate: toNum(row?.rate),
            spot_mid: toNum(row?.spot_mid),
            call_bid_size: toNum(row?.call_bid_size),
            call_bid_price: toNum(row?.call_bid_price),
            call_ask_price: toNum(row?.call_ask_price),
            call_ask_size: toNum(row?.call_ask_size),
            call_bid_iv: toNum(row?.call_bid_iv),
            call_ask_iv: toNum(row?.call_ask_iv),
            call_delta: toNum(row?.call_delta),
            call_gamma: toNum(row?.call_gamma),
            call_vega: toNum(row?.call_vega),
            call_theta: toNum(row?.call_theta),
            call_rho: toNum(row?.call_rho),
            call_dv01: toNum(row?.call_dv01),
            put_delta: toNum(row?.put_delta),
            put_gamma: toNum(row?.put_gamma),
            put_vega: toNum(row?.put_vega),
            put_theta: toNum(row?.put_theta),
            put_rho: toNum(row?.put_rho),
            put_dv01: toNum(row?.put_dv01),
            put_bid_iv: toNum(row?.put_bid_iv),
            put_ask_iv: toNum(row?.put_ask_iv),
            put_bid_size: toNum(row?.put_bid_size),
            put_bid_price: toNum(row?.put_bid_price),
            put_ask_price: toNum(row?.put_ask_price),
            put_ask_size: toNum(row?.put_ask_size),
          };
        }).filter((row) => row.expiry && row.strike !== null);

        normalized.sort((a, b) => {
          if (a.expiry !== b.expiry) return a.expiry.localeCompare(b.expiry);
          return a.strike - b.strike;
        });

        serverState.optionsChainByTicker[ticker] = normalized;

        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, ticker, count: normalized.length }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json body' }));
      }
    });
    return;
  }

  if (url.pathname === '/api/pricer-log' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(serverState.pricerLog || []));
    return;
  }

  if (url.pathname === '/api/pricer-log' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; if (body.length > 500_000) req.destroy(); });
    req.on('end', () => {
      try {
        const entry = JSON.parse(body || '{}');
        serverState.pricerLog = serverState.pricerLog || [];
        serverState.pricerLog.unshift(entry);
        if (serverState.pricerLog.length > 500) serverState.pricerLog = serverState.pricerLog.slice(0, 500);
        try { fs.writeFileSync(PRICER_LOG_FILE, JSON.stringify(serverState.pricerLog, null, 2), 'utf8'); } catch (_) {}
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json' }));
      }
    });
    return;
  }

  if (url.pathname === '/api/pricer-log' && req.method === 'DELETE') {
    serverState.pricerLog = [];
    try { fs.writeFileSync(PRICER_LOG_FILE, '[]', 'utf8'); } catch (_) {}
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // Mock modda Risk sekmesi icin ornek portfoy. Guncel spot fiyatlardan
  // kuruldugu icin kullanim fiyatlari ve delta'lar tutarli cikar.
  // Canli modda bu uc kapalidir: portfoy XLSX ile iceri aktarilir.
  if (url.pathname === '/api/mock-portfolio' && req.method === 'GET') {
    if (!MOCK_MODE) {
      res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'yalnizca mock modda' }));
      return;
    }
    const vade = (serverState.futuresMeta || []).find((m) => Number(m.dtm) > 5)
              || (serverState.futuresMeta || [])[0];
    const tanim = [
      { ticker: 'THYAO', posType: 'call', qty: 100, moneyness: 1.00 },
      { ticker: 'THYAO', posType: 'put', qty: -50, moneyness: 0.98 },
      { ticker: 'GARAN', posType: 'call', qty: 200, moneyness: 1.02 },
      { ticker: 'AKBNK', posType: 'put', qty: -100, moneyness: 0.96 },
      { ticker: 'ASELS', posType: 'call', qty: 50, moneyness: 1.05 },
    ];
    const pozisyonlar = [];
    for (const t of tanim) {
      const spot = Number((serverState.spotByTicker[t.ticker] || {}).spot_mid);
      if (!Number.isFinite(spot) || spot <= 0 || !vade) continue;
      const strike = Math.round(spot * t.moneyness * 100) / 100;
      // Kaba delta yaklasimi: para-basi 0.5, parada/disinda kayar
      const d = 0.5 + (spot - strike) / spot * 4;
      const delta = t.posType === 'call'
        ? Math.max(0.02, Math.min(0.98, d))
        : -Math.max(0.02, Math.min(0.98, 1 - d));
      pozisyonlar.push({
        underlying: t.ticker, posType: t.posType, qty: t.qty,
        spot: Math.round(spot * 1000) / 1000, strike,
        delta: Math.round(delta * 10000) / 10000,
        dtm: Number(vade.dtm), expiry: String(vade.code || ''),
      });
    }
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: true, positions: pozisyonlar }));
    return;
  }

  if (url.pathname === '/api/yield-curve' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: true, curve: serverState.yieldCurve }));
    return;
  }

  if (url.pathname === '/api/yield-curve' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; if (body.length > 200_000) req.destroy(); });
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        serverState.yieldCurve = { ...p, ts: new Date().toISOString() };
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, points: (p.curve || []).length }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json' }));
      }
    });
    return;
  }

  // Depo durumu. Veritabanini Python tarafi tutar; bu uc yalnizca
  // store.py --push-stats'in gonderdigi ozeti saklayip arayuze verir.
  if (url.pathname === '/api/store-stats' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: true, stats: serverState.storeStats }));
    return;
  }

  if (url.pathname === '/api/store-stats' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; if (body.length > 500_000) req.destroy(); });
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        serverState.storeStats = { ...p, received_at: new Date().toISOString() };
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json' }));
      }
    });
    return;
  }

  if (url.pathname === '/api/audit' && req.method === 'GET') {
    const n = Math.min(500, Math.max(1, Number(url.searchParams.get('limit')) || 50));
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({
      ok: true,
      total: auditTrail.records.length,
      verification: auditTrail.verify(),
      records: auditTrail.recent(n),
    }));
    return;
  }

  if (url.pathname === '/api/audit/verify' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: true, ...auditTrail.verify() }));
    return;
  }

  if (url.pathname === '/api/audit' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; if (body.length > 200_000) req.destroy(); });
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        if (!p.tip) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'type field is required' }));
          return;
        }
        // Veri modu kayda gömülür: üretilmiş veriyle yapılan bir koşunun
        // sonradan canlı sanılmaması için.
        const record = auditTrail.append(p.tip, { ...(p.veri || {}), mod: MOCK_MODE ? 'MOCK' : 'CANLI' });
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, seq: record.seq, hash: record.hash }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json' }));
      }
    });
    return;
  }

  if (url.pathname === '/api/dividends' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(serverState.dividendsByTicker || {}));
    return;
  }

  if (url.pathname === '/api/dividends' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 100_000) req.destroy();
    });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        if (typeof payload !== 'object' || Array.isArray(payload)) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'expected object' }));
          return;
        }
        serverState.dividendsByTicker = payload;
        try { fs.writeFileSync(DIVIDENDS_FILE, JSON.stringify(payload, null, 2), 'utf8'); } catch (_) {}
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true }));
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json' }));
      }
    });
    return;
  }

  if (url.pathname === '/api/realized-vols' && req.method === 'GET') {
    const forceRefresh = url.searchParams.get('refresh') === '1';
    try {
      const payload = await getRealizedVolTable(forceRefresh);
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: true, stale: false, ...payload }));
    } catch (err) {
      if (realizedVolCache.payload) {
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: true,
          stale: true,
          error: 'Failed to refresh realized vols, returning cached data.',
          ...realizedVolCache.payload,
        }));
      } else {
        res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: err && err.message ? err.message : 'realized vols failed' }));
      }
    }
    return;
  }

  if (url.pathname === '/api/upload-excel' && req.method === 'POST') {
    const ct = req.headers['content-type'] || '';
    const boundaryMatch = ct.match(/boundary=([^;]+)/i);
    if (!boundaryMatch) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Missing multipart boundary' }));
      return;
    }
    const boundary = boundaryMatch[1].trim();
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        const body = Buffer.concat(chunks);
        const sep = Buffer.from('--' + boundary);
        const parts = [];
        let start = 0;
        while (start < body.length) {
          const idx = body.indexOf(sep, start);
          if (idx === -1) break;
          const partStart = idx + sep.length;
          if (body[partStart] === 0x2D && body[partStart + 1] === 0x2D) break; // trailing --
          const nextIdx = body.indexOf(sep, partStart);
          const partEnd = nextIdx === -1 ? body.length : nextIdx;
          parts.push(body.slice(partStart, partEnd));
          start = partEnd;
        }
        let savedFile = null;
        for (const part of parts) {
          const headerEnd = part.indexOf(Buffer.from('\r\n\r\n'));
          if (headerEnd === -1) continue;
          const headerStr = part.slice(0, headerEnd).toString('utf8');
          const fileData = part.slice(headerEnd + 4);
          // strip trailing CRLF
          const trimmed = fileData[fileData.length - 2] === 0x0D && fileData[fileData.length - 1] === 0x0A
            ? fileData.slice(0, fileData.length - 2) : fileData;
          const fnMatch = headerStr.match(/filename="([^"]+)"/i);
          if (!fnMatch) continue;
          const rawName = path.basename(fnMatch[1]);
          const ext = path.extname(rawName).toLowerCase();
          if (ext !== '.xlsx' && ext !== '.xlsm' && ext !== '.xls' && ext !== '.py' && ext !== '.txt') {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ ok: false, error: 'Only .xlsx, .xlsm, .xls, .py and .txt files are allowed' }));
            return;
          }
          const savePath = path.join(__dirname, rawName);
          require('fs').writeFileSync(savePath, trimmed);
          savedFile = rawName;
          break;
        }
        if (!savedFile) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'No valid file part found' }));
          return;
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, filename: savedFile }));
      } catch (err) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: err.message }));
      }
    });
    return;
  }

  const html = renderRoute(url, serverState);
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(html);
});

server.listen(process.env.PORT || 5173, process.env.HOST || '127.0.0.1', () => {
  const bindHost = process.env.HOST || '127.0.0.1';
  const port = process.env.PORT || 5173;
  const publicHost = process.env.PUBLIC_HOST || bindHost;
  console.log(`Frontend shell ready: http://${publicHost}:${port} (bind ${bindHost})`);

  // Warm the realized-vol table right away instead of waiting for the
  // first /tools/realized-vols visit (which would otherwise pay the full
  // ~30-ticker sequential fetch cost on someone's page load). Then keep it
  // refreshed on the same cadence as its cache TTL so it never goes stale
  // while the dashboard is running.
  refreshRealizedVolsInBackground('startup');
  setInterval(() => refreshRealizedVolsInBackground('scheduled'), realizedVolCacheTtlMs);
});

function refreshRealizedVolsInBackground(reason) {
  getRealizedVolTable(true)
    .then((payload) => {
      const rowCount = payload && Array.isArray(payload.rows) ? payload.rows.length : 0;
      console.log(`[realized-vols] refreshed (${reason}): ${rowCount} tickers, generated_at=${payload && payload.generated_at}`);
    })
    .catch((err) => {
      console.error(`[realized-vols] background refresh failed (${reason}):`, err && err.message ? err.message : err);
    });
}

