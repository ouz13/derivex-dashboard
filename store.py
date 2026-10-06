#!/usr/bin/env python3
"""
store.py — SQLite persistence layer.

Until now every observation lived in the dashboard's in-memory `serverState`
and vanished when the process stopped. Three things were impossible as a
result: a historical series, model parameter versioning, and a GARCH fit on
actual data. This module is the store those three stand on.

WHY SQLITE, AND WHY ON THE PYTHON SIDE
--------------------------------------
sqlite3 ships with every CPython, so there is no dependency to install and
nothing to run as a service. The store sits in Python rather than Node
because:

  * the data producer is already Python — bridge_stream.py sees every quote
    before the dashboard does;
  * `node:sqlite` is still flagged experimental and does not exist at all in
    Node 20, which is what CI and the Docker image run;
  * the consumers of history (GARCH, curve fitting) are Python too.

The dashboard therefore never opens the database. It receives what it needs
over the HTTP endpoints it already has, which keeps the Node side free of a
database dependency entirely.

WHERE THE WRITES COME FROM
--------------------------
bridge_stream.py's three POST helpers — post_spot_mid,
post_futures_rates_batch, post_options_chain — are the single choke point
through which both the live bridge and mock_feed.py publish. Hooking the
store there means both modes persist without either one knowing about it.

MOCK AND LIVE DATA ARE NEVER MIXED
----------------------------------
Every row carries a `data_mode` column and every read filters on it. A
volatility estimate fitted to generated prices must not be able to pass
itself off as one fitted to the market, and a database that is written in
both modes over its lifetime must not silently splice the two into one
series. This mirrors the audit trail, which stamps the mode into each
record for the same reason.

USAGE
-----
    python3 store.py --stats                 what is stored
    python3 store.py --restore               replay snapshot into dashboard
    python3 store.py --push-stats            send stats to dashboard panel
    python3 store.py --prune 90              drop ticks older than 90 days
    python3 store.py --versions nss          model parameter history
"""

import argparse
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

DB_PATH = os.environ.get("STORE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "derivex.db"))

# Kapatma anahtari: testler ve tek seferlik calistirmalar icin.
STORE_ENABLED = os.environ.get("STORE_ENABLED", "1") != "0"

# Veri modu etiketi. bridge_stream/mock_feed ile ayni DATA_MODE degiskenini
# okur, boylece dashboard'daki kirmizi mock serisiyle tutarli kalir.
def _mod_etiketi():
    return "MOCK" if os.environ.get("DATA_MODE", "0") == "0" else "LIVE"


# Ornekleme araliklari (saniye). Her tick'i yazmak gereksiz: opsiyon zinciri
# 26 ticker x 3 vade x 9 kullanim fiyati = ~700 satir, 3 saniyede bir yazilsa
# saatte 840 bin satir eder. Gunluk kapanis ve volatilite icin bu cozunurluk
# gereksiz, maliyeti ise gercek.
SPOT_SAMPLE_SEC = float(os.environ.get("STORE_SPOT_SAMPLE_SEC", "5"))
RATE_SAMPLE_SEC = float(os.environ.get("STORE_RATE_SAMPLE_SEC", "30"))
# Opsiyon zinciri en pahali tablo: her ornek ~700 satir. 60 saniyede bir
# yazildiginda gunde ~1 milyon satir (~100 MB) ediyordu. Gecmis serinin
# amaci volatilite ve model surumleme oldugu icin 5 dakikalik cozunurluk
# fazlasiyla yeterli; maliyet 5'te bire iniyor.
OPT_SAMPLE_SEC = float(os.environ.get("STORE_OPT_SAMPLE_SEC", "300"))

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Spot zaman serisi (orneklenmis).
CREATE TABLE IF NOT EXISTS spot_tick (
  ts        TEXT NOT NULL,
  ticker    TEXT NOT NULL,
  mid       REAL NOT NULL,
  data_mode TEXT NOT NULL,
  PRIMARY KEY (ticker, ts, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_spot_tick_ts ON spot_tick (ts);

-- Gunluk bar. Spot yazilirken artimli guncellenir; GARCH'in girdisi budur.
-- Ayri bir toplama isi calistirmak gerekmesin diye boyle.
CREATE TABLE IF NOT EXISTS spot_daily (
  d         TEXT NOT NULL,
  ticker    TEXT NOT NULL,
  open      REAL NOT NULL,
  high      REAL NOT NULL,
  low       REAL NOT NULL,
  close     REAL NOT NULL,
  n         INTEGER NOT NULL,
  data_mode TEXT NOT NULL,
  PRIMARY KEY (ticker, d, data_mode)
);

-- Vadeli ima edilen getiri zaman serisi.
CREATE TABLE IF NOT EXISTS futures_rate_tick (
  ts        TEXT NOT NULL,
  ticker    TEXT NOT NULL,
  code      TEXT NOT NULL,
  dtm       REAL,
  rate      REAL,
  bid_rate  REAL,
  ask_rate  REAL,
  fut_mid   REAL,
  spot_mid  REAL,
  data_mode TEXT NOT NULL,
  PRIMARY KEY (ticker, code, ts, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_fut_tick_ts ON futures_rate_tick (ts);

-- Opsiyon kotasyon anlik goruntusu (orneklenmis).
CREATE TABLE IF NOT EXISTS option_quote (
  ts          TEXT NOT NULL,
  ticker      TEXT NOT NULL,
  expiry      TEXT NOT NULL,
  strike      REAL NOT NULL,
  dtm         REAL,
  call_bid    REAL,
  call_ask    REAL,
  call_bid_iv REAL,
  call_ask_iv REAL,
  call_delta  REAL,
  put_bid     REAL,
  put_ask     REAL,
  put_bid_iv  REAL,
  put_ask_iv  REAL,
  put_delta   REAL,
  data_mode   TEXT NOT NULL,
  PRIMARY KEY (ticker, expiry, strike, ts, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_opt_quote_ts ON option_quote (ts);

-- Model parametresi surumleme. Uzerine yazilmaz: her uydurma yeni satir.
-- "Bu fiyatlama hangi parametrelerle uretildi" sorusunun cevabi burada.
CREATE TABLE IF NOT EXISTS model_version (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  model       TEXT NOT NULL,
  scope       TEXT NOT NULL DEFAULT '',
  params      TEXT NOT NULL,
  fit_quality REAL,
  meta        TEXT,
  data_mode   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_model_version_lookup
  ON model_version (model, scope, ts);

-- En son tam durum. Yeniden baslatmada arayuzu beslemek icin;
-- canli modda seans disinda hic tick gelmeyecegi icin bu sart.
CREATE TABLE IF NOT EXISTS snapshot (
  key       TEXT NOT NULL,
  data_mode TEXT NOT NULL,
  ts        TEXT NOT NULL,
  payload   TEXT NOT NULL,
  PRIMARY KEY (key, data_mode)
);
"""


def _simdi():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    """
    SQLite deposu.

    Yazma yollari akis dongusunun icinden cagrilir; bu yuzden hicbiri
    disariya istisna sizdirmaz. Depo bir yan kayittir, veri akisinin
    kendisi degil: disk dolsa ya da dosya kilitlense bile akis surmeli.
    """

    def __init__(self, yol=None, data_mode=None):
        self.yol = yol or DB_PATH
        self.data_mode = data_mode or _mod_etiketi()
        self._kilit = threading.Lock()
        self._son_yazim = {}          # ornekleme icin son yazim zamanlari
        self.yazma_hatasi = None
        self.conn = sqlite3.connect(self.yol, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL: okuyucu (garch.py, fit_curve.py) yazan akisi engellemesin.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        # Varsayilan 1000 sayfalik (~4 MB) esikte WAL dosyasi surekli o
        # boyutta duruyor ve veritabaninin kendisinden buyuk gorunuyordu.
        # Daha sik denetim noktasi, WAL'i ~2 MB'in altinda tutar.
        self.conn.execute("PRAGMA wal_autocheckpoint=512")
        self.conn.executescript(_SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),))
        self.conn.commit()

    # -- yardimcilar ------------------------------------------------------

    def kapat(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass

    def _ornekle(self, anahtar, aralik):
        """True donerse yazilacak. Sureci icin son yazim zamanini tutar."""
        simdi = time.time()
        son = self._son_yazim.get(anahtar)
        if son is not None and (simdi - son) < aralik:
            return False
        self._son_yazim[anahtar] = simdi
        return True

    def _yaz(self, islev):
        """Yazma sarmalayici: hata akisi bozmaz, ilk hata kaydedilir."""
        if not STORE_ENABLED:
            return False
        try:
            with self._kilit:
                islev()
                self.conn.commit()
            return True
        except Exception as e:                       # noqa: BLE001
            if self.yazma_hatasi is None:
                self.yazma_hatasi = f"{type(e).__name__}: {e}"
            return False

    # -- zaman serisi yazimi ---------------------------------------------

    def spot_kaydet(self, ticker, mid, zorla=False):
        if not zorla and not self._ornekle(("spot", ticker), SPOT_SAMPLE_SEC):
            return False
        ts = _simdi()
        gun = ts[:10]
        mid = float(mid)

        def islem():
            self.conn.execute(
                "INSERT OR REPLACE INTO spot_tick (ts, ticker, mid, data_mode) "
                "VALUES (?, ?, ?, ?)", (ts, ticker, mid, self.data_mode))
            # Gunluk bari ayni islemde guncelle. Mevcut satir varsa
            # high/low genisler, close son degere kayar.
            self.conn.execute(
                """
                INSERT INTO spot_daily (d, ticker, open, high, low, close, n, data_mode)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (ticker, d, data_mode) DO UPDATE SET
                  high  = MAX(spot_daily.high, excluded.close),
                  low   = MIN(spot_daily.low,  excluded.close),
                  close = excluded.close,
                  n     = spot_daily.n + 1
                """,
                (gun, ticker, mid, mid, mid, mid, self.data_mode))

        return self._yaz(islem)

    def oranlar_kaydet(self, rates_by_ticker, maturities=None):
        if not self._ornekle("rates", RATE_SAMPLE_SEC):
            return False
        ts = _simdi()
        dtm_map = {str(m.get("code")): m.get("dtm") for m in (maturities or [])}
        satirlar = []
        for ticker, kodlar in (rates_by_ticker or {}).items():
            if not isinstance(kodlar, dict):
                continue
            for kod, rec in kodlar.items():
                if not isinstance(rec, dict):
                    continue
                satirlar.append((
                    ts, str(ticker).upper(), str(kod).upper(),
                    _sayi(rec.get("dtm")) if rec.get("dtm") is not None else _sayi(dtm_map.get(str(kod))),
                    _sayi(rec.get("rate")), _sayi(rec.get("bid_rate")),
                    _sayi(rec.get("ask_rate")), _sayi(rec.get("fut_mid")),
                    _sayi(rec.get("spot_mid")), self.data_mode,
                ))
        if not satirlar:
            return False

        def islem():
            self.conn.executemany(
                "INSERT OR REPLACE INTO futures_rate_tick "
                "(ts, ticker, code, dtm, rate, bid_rate, ask_rate, fut_mid, spot_mid, data_mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", satirlar)

        return self._yaz(islem)

    def opsiyonlar_kaydet(self, ticker, satirlar):
        if not self._ornekle(("opt", ticker), OPT_SAMPLE_SEC):
            return False
        ts = _simdi()
        ticker = str(ticker).upper()
        kayitlar = []
        for r in (satirlar or []):
            if not isinstance(r, dict):
                continue
            vade, k = r.get("expiry"), _sayi(r.get("strike"))
            if not vade or k is None:
                continue
            kayitlar.append((
                ts, ticker, str(vade), k, _sayi(r.get("dtm")),
                _sayi(r.get("call_bid_price")), _sayi(r.get("call_ask_price")),
                _sayi(r.get("call_bid_iv")), _sayi(r.get("call_ask_iv")),
                _sayi(r.get("call_delta")),
                _sayi(r.get("put_bid_price")), _sayi(r.get("put_ask_price")),
                _sayi(r.get("put_bid_iv")), _sayi(r.get("put_ask_iv")),
                _sayi(r.get("put_delta")), self.data_mode,
            ))
        if not kayitlar:
            return False

        def islem():
            self.conn.executemany(
                "INSERT OR REPLACE INTO option_quote "
                "(ts, ticker, expiry, strike, dtm, call_bid, call_ask, call_bid_iv, "
                " call_ask_iv, call_delta, put_bid, put_ask, put_bid_iv, put_ask_iv, "
                " put_delta, data_mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kayitlar)

        return self._yaz(islem)

    # -- anlik goruntu ----------------------------------------------------

    def snapshot_yaz(self, anahtar, veri):
        """
        En son tam durumu saklar. Tick tablolarinin yerine gecmez; amaci
        yeniden baslatmada arayuzu tek POST ile doldurmak.
        """
        ts = _simdi()
        gövde = json.dumps(veri, separators=(",", ":"))

        def islem():
            self.conn.execute(
                "INSERT OR REPLACE INTO snapshot (key, data_mode, ts, payload) "
                "VALUES (?, ?, ?, ?)", (anahtar, self.data_mode, ts, gövde))

        return self._yaz(islem)

    def snapshot_oku(self, anahtar):
        s = self.conn.execute(
            "SELECT ts, payload FROM snapshot WHERE key = ? AND data_mode = ?",
            (anahtar, self.data_mode)).fetchone()
        if not s:
            return None
        try:
            return {"ts": s["ts"], "payload": json.loads(s["payload"])}
        except Exception:
            return None

    # -- model surumleme --------------------------------------------------

    def model_surum_yaz(self, model, params, scope="", fit_quality=None, meta=None):
        """
        Yeni bir parametre surumu ekler. Var olan satir guncellenmez:
        gecmise donuk "hangi parametreyle fiyatlandi" sorusu ancak
        eski surumler durursa cevaplanabilir.
        """
        ts = _simdi()

        def islem():
            self.conn.execute(
                "INSERT INTO model_version (ts, model, scope, params, fit_quality, meta, data_mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, model, scope,
                 json.dumps(params, separators=(",", ":")),
                 _sayi(fit_quality),
                 json.dumps(meta, separators=(",", ":")) if meta is not None else None,
                 self.data_mode))

        self._yaz(islem)
        return ts

    def model_surumleri(self, model=None, scope=None, limit=20):
        sorgu = ("SELECT id, ts, model, scope, params, fit_quality, meta, data_mode "
                 "FROM model_version WHERE data_mode = ?")
        arg = [self.data_mode]
        if model:
            sorgu += " AND model = ?"
            arg.append(model)
        if scope is not None:
            sorgu += " AND scope = ?"
            arg.append(scope)
        sorgu += " ORDER BY id DESC LIMIT ?"
        arg.append(int(limit))
        cikti = []
        for s in self.conn.execute(sorgu, arg):
            cikti.append({
                "id": s["id"], "ts": s["ts"], "model": s["model"],
                "scope": s["scope"], "fit_quality": s["fit_quality"],
                "data_mode": s["data_mode"],
                "params": _json_oku(s["params"]),
                "meta": _json_oku(s["meta"]),
            })
        return cikti

    # -- okuma ------------------------------------------------------------

    def gunluk_kapanislar(self, ticker, limit=1000):
        """GARCH ve gerceklesmis volatilite girdisi: (gun, kapanis) artan."""
        satirlar = self.conn.execute(
            "SELECT d, close FROM spot_daily "
            "WHERE ticker = ? AND data_mode = ? ORDER BY d DESC LIMIT ?",
            (str(ticker).upper(), self.data_mode, int(limit))).fetchall()
        return [(s["d"], s["close"]) for s in reversed(satirlar)]

    def gunluk_kapanisi_olan_tickerlar(self, asgari_gun=2):
        # HAVING icinde COUNT(*) acikca yazilir, takma ad kullanilmaz:
        # spot_daily'nin "n" adli bir kolonu var (gun icindeki tick sayisi)
        # ve takma ad kullanildiginda SQLite onu degil kolonu cozuyor.
        # Her bar icin n=1 oldugundan kosul daima yanlis cikiyor, yani
        # GARCH hicbir ticker gormuyordu.
        satirlar = self.conn.execute(
            "SELECT ticker, COUNT(*) AS gun_sayisi FROM spot_daily WHERE data_mode = ? "
            "GROUP BY ticker HAVING COUNT(*) >= ? ORDER BY ticker",
            (self.data_mode, int(asgari_gun))).fetchall()
        return [s["ticker"] for s in satirlar]

    def spot_serisi(self, ticker, limit=500):
        satirlar = self.conn.execute(
            "SELECT ts, mid FROM spot_tick WHERE ticker = ? AND data_mode = ? "
            "ORDER BY ts DESC LIMIT ?",
            (str(ticker).upper(), self.data_mode, int(limit))).fetchall()
        return [{"ts": s["ts"], "mid": s["mid"]} for s in reversed(satirlar)]

    def istatistik(self):
        """Depo icerigi — arayuzdeki Persistence panelini besler."""
        def tek(sql, *a):
            s = self.conn.execute(sql, a).fetchone()
            return s[0] if s and s[0] is not None else 0

        mod = self.data_mode
        aralik = self.conn.execute(
            "SELECT MIN(d) AS ilk, MAX(d) AS son FROM spot_daily WHERE data_mode = ?",
            (mod,)).fetchone()
        # Veritabani ve WAL ayri raporlanir: WAL gecicidir ve denetim
        # noktasinda kuculur, ikisini toplamak kalici boyutu oldugundan
        # buyuk gosteriyordu.
        def _boyut(ek=""):
            try:
                return os.path.getsize(self.yol + ek)
            except OSError:
                return 0

        return {
            "db_path": self.yol,
            "db_bytes": _boyut(),
            "wal_bytes": _boyut("-wal"),
            "data_mode": mod,
            "schema_version": SCHEMA_VERSION,
            "spot_ticks": tek("SELECT COUNT(*) FROM spot_tick WHERE data_mode = ?", mod),
            "spot_days": tek("SELECT COUNT(*) FROM spot_daily WHERE data_mode = ?", mod),
            "spot_tickers": tek("SELECT COUNT(DISTINCT ticker) FROM spot_daily WHERE data_mode = ?", mod),
            "futures_ticks": tek("SELECT COUNT(*) FROM futures_rate_tick WHERE data_mode = ?", mod),
            "option_quotes": tek("SELECT COUNT(*) FROM option_quote WHERE data_mode = ?", mod),
            "model_versions": tek("SELECT COUNT(*) FROM model_version WHERE data_mode = ?", mod),
            "first_day": aralik["ilk"] if aralik else None,
            "last_day": aralik["son"] if aralik else None,
            "write_error": self.yazma_hatasi,
            "ts": _simdi(),
        }

    def buda(self, gun):
        """
        gun gunden eski tick'leri siler. Gunluk barlar ve model surumleri
        korunur: hacmi yapan tick tablolari, deger tasiyan ise gunluk seri.
        """
        if gun is None or int(gun) <= 0:
            return {}
        esik = (datetime.now(timezone.utc).timestamp() - int(gun) * 86400)
        esik_ts = datetime.fromtimestamp(esik, timezone.utc).isoformat(timespec="milliseconds")
        silinen = {}
        with self._kilit:
            for tablo in ("spot_tick", "futures_rate_tick", "option_quote"):
                im = self.conn.execute(f"DELETE FROM {tablo} WHERE ts < ?", (esik_ts,))
                silinen[tablo] = im.rowcount
            self.conn.commit()
            self.conn.execute("VACUUM")
        return silinen


def _sayi(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _json_oku(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Surec genelinde tek ornek. bridge_stream.py POST yardimcilarindan cagirir;
# depo acilamazsa None doner ve akis depo olmadan surer.
# ---------------------------------------------------------------------------

_ornek = None
_ornek_denendi = False


def depo():
    global _ornek, _ornek_denendi
    if not STORE_ENABLED:
        return None
    if _ornek is None and not _ornek_denendi:
        _ornek_denendi = True
        try:
            _ornek = Store()
        except Exception as e:                       # noqa: BLE001
            print(f"[STORE] acilamadi, depolama devre disi: {type(e).__name__}: {e}", flush=True)
            _ornek = None
    return _ornek


# ---------------------------------------------------------------------------
# Komut satiri
# ---------------------------------------------------------------------------

def _restore(taban_url=None):
    """
    Snapshot'i dashboard'a geri yukler.

    Yeni bir uc eklenmedi: veriyi akisin kullandigi ayni POST uclarina
    gonderir. Boylece geri yukleme yolu, canli yolun kendisiyle ayni
    dogrulamadan gecer.
    """
    import bridge_stream as B

    if taban_url:
        B.FRONTEND_BASE_URL = taban_url
        B.FRONTEND_SPOT_ENDPOINT = f"{taban_url}/api/spot"
        B.FRONTEND_FUTURES_RATES_ENDPOINT = f"{taban_url}/api/futures-rates"
        B.FRONTEND_OPTIONS_CHAIN_ENDPOINT = f"{taban_url}/api/options-chain"

    d = Store()
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        sayac = {"spot": 0, "rates": 0, "options": 0, "curve": 0}

        s = d.snapshot_oku("spot")
        if s:
            for ticker, mid in (s["payload"] or {}).items():
                try:
                    B.post_spot_mid(ticker, mid, ts)
                    sayac["spot"] += 1
                except Exception:
                    pass

        s = d.snapshot_oku("rates")
        if s and isinstance(s["payload"], dict):
            try:
                B.post_futures_rates_batch(
                    s["payload"].get("rates_by_ticker") or {},
                    s["payload"].get("maturities") or [], ts)
                sayac["rates"] = 1
            except Exception:
                pass

        s = d.snapshot_oku("options")
        if s and isinstance(s["payload"], dict):
            for ticker, satirlar in s["payload"].items():
                try:
                    B.post_options_chain(ticker, satirlar, ts)
                    sayac["options"] += 1
                except Exception:
                    pass

        son = d.model_surumleri(model="nss", limit=1)
        if son:
            try:
                import requests
                requests.post(f"{B.FRONTEND_BASE_URL}/api/yield-curve",
                              json=son[0]["meta"] or {}, timeout=3)
                sayac["curve"] = 1
            except Exception:
                pass

        print(f"[STORE] geri yukleme: spot={sayac['spot']} oranlar={sayac['rates']} "
              f"opsiyon_ticker={sayac['options']} egri={sayac['curve']}")
        return sayac
    finally:
        d.kapat()


def _sync_models(taban_url=None):
    """
    Dashboard'daki son iyi kalibrasyonlari surum gecmisine alir.

    Volatilite kalibrasyonu tarayicida kosuyor ve sonucu Node'un
    /api/model-params ucunda duruyor. Surum TABLOSU ise burada. Bu islev
    ikisini bagliyor: ayni parametre seti iki kez yazilmiyor, yalnizca
    degisenler yeni surum olarak ekleniyor.
    """
    import requests
    taban = taban_url or os.environ.get("FRONTEND_BASE_URL", "http://127.0.0.1:5173")
    d = Store()
    try:
        r = requests.get(f"{taban}/api/model-params", timeout=5)
        r.raise_for_status()
        tumu = (r.json() or {}).get("params") or {}

        eklenen = 0
        for anahtar, kayit in tumu.items():
            if "|" not in anahtar or not isinstance(kayit, dict):
                continue
            model, kapsam = anahtar.split("|", 1)
            params = kayit.get("params")
            if not isinstance(params, dict):
                continue
            # Ayni parametreler zaten en son surumse tekrar yazma: her
            # senkronizasyonda yeni satir acmak gecmisi okunamaz hale
            # getirirdi.
            son = d.model_surumleri(model=model, scope=kapsam, limit=1)
            if son and son[0]["params"] == params:
                continue
            d.model_surum_yaz(model, params, scope=kapsam,
                              fit_quality=_sayi(kayit.get("rmse")),
                              meta={"points": kayit.get("points"),
                                    "calibrated_at": kayit.get("ts")})
            eklenen += 1
        print(f"[STORE] model senkronu: {eklenen} yeni surum / {len(tumu)} kalibrasyon")
        return eklenen
    finally:
        d.kapat()


def _push_stats(taban_url=None):
    """Depo istatistiklerini ve model surumlerini arayuz paneline gonderir."""
    import requests
    taban = taban_url or os.environ.get("FRONTEND_BASE_URL", "http://127.0.0.1:5173")
    d = Store()
    try:
        govde = d.istatistik()
        govde["versions"] = d.model_surumleri(limit=10)
        r = requests.post(f"{taban}/api/store-stats", json=govde, timeout=3)
        r.raise_for_status()
        return govde
    finally:
        d.kapat()


def main():
    ap = argparse.ArgumentParser(description="Derivex SQLite deposu")
    ap.add_argument("--stats", action="store_true", help="depo icerigini yaz")
    ap.add_argument("--restore", action="store_true", help="snapshot'i dashboard'a yukle")
    ap.add_argument("--push-stats", action="store_true", help="istatistikleri arayuze gonder")
    ap.add_argument("--sync-models", action="store_true",
                    help="dashboard'daki kalibrasyonlari surum gecmisine al")
    ap.add_argument("--prune", type=int, metavar="GUN", help="GUN gunden eski tick'leri sil")
    ap.add_argument("--versions", metavar="MODEL", help="model parametre gecmisi")
    ap.add_argument("--base-url", help="dashboard adresi (varsayilan FRONTEND_BASE_URL)")
    a = ap.parse_args()

    if a.restore:
        _restore(a.base_url)
        return
    if a.sync_models:
        _sync_models(a.base_url)
        return
    if a.push_stats:
        print(json.dumps(_push_stats(a.base_url), indent=2))
        return

    d = Store()
    try:
        if a.prune:
            print(json.dumps(d.buda(a.prune), indent=2))
        if a.versions:
            for s in d.model_surumleri(model=a.versions, limit=20):
                kalite = "-" if s["fit_quality"] is None else f"{s['fit_quality']:.6f}"
                print(f"#{s['id']:<5} {s['ts']}  {s['model']:<10} "
                      f"scope={s['scope'] or '-':<8} quality={kalite}  [{s['data_mode']}]")
        if a.stats or not (a.prune or a.versions):
            print(json.dumps(d.istatistik(), indent=2))
    finally:
        d.kapat()


if __name__ == "__main__":
    main()
