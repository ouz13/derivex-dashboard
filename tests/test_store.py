#!/usr/bin/env python3
"""
SQLite deposu testleri.

Asil sorular: veri yeniden baslatmadan sagliyor mu, mock ve canli gecmis
birbirine karisiyor mu, model surumleri gercekten birikiyor mu (ustune
yazilmiyor mu), ve depo yazamadiginda akis kiriliyor mu.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import store as S  # noqa: E402


class DepoTemel(unittest.TestCase):

    def setUp(self):
        self.dizin = tempfile.mkdtemp(prefix="depo-")
        self.yol = os.path.join(self.dizin, "test.db")
        self.d = S.Store(self.yol, data_mode="MOCK")

    def tearDown(self):
        self.d.kapat()
        shutil.rmtree(self.dizin, ignore_errors=True)


class TestSema(DepoTemel):

    def test_bos_depo_istatistigi(self):
        i = self.d.istatistik()
        self.assertEqual(i["spot_ticks"], 0)
        self.assertEqual(i["spot_days"], 0)
        self.assertEqual(i["model_versions"], 0)
        self.assertEqual(i["data_mode"], "MOCK")
        self.assertIsNone(i["write_error"])

    def test_wal_boyutu_ayri_raporlanir(self):
        """
        WAL gecicidir ve denetim noktasinda kuculur; kalici boyutla
        toplandiginda depo oldugundan buyuk gorunuyordu.
        """
        self.d.spot_kaydet("THYAO", 100.0)
        i = self.d.istatistik()
        self.assertIn("wal_bytes", i)
        self.assertGreater(i["db_bytes"], 0, "veritabani dosyasi bos gorunuyor")
        self.assertIsInstance(i["wal_bytes"], int)

    def test_yeniden_acilisla_veri_sagliyor(self):
        """Kalicilik iddiasinin kendisi: surec olunce veri kalmali."""
        self.d.spot_kaydet("THYAO", 281.5)
        self.d.kapat()

        yeni = S.Store(self.yol, data_mode="MOCK")
        try:
            self.assertEqual(yeni.istatistik()["spot_ticks"], 1)
            self.assertEqual(len(yeni.gunluk_kapanislar("THYAO")), 1)
        finally:
            yeni.kapat()
        self.d = S.Store(self.yol, data_mode="MOCK")   # tearDown icin

    def test_ayni_dosya_iki_kez_acilabilir(self):
        """Okuyucu (garch.py) yazan surecle ayni dosyayi acabilmeli."""
        self.d.spot_kaydet("THYAO", 100.0)
        okuyucu = S.Store(self.yol, data_mode="MOCK")
        try:
            self.assertEqual(len(okuyucu.gunluk_kapanislar("THYAO")), 1)
        finally:
            okuyucu.kapat()


class TestSpot(DepoTemel):

    def test_tick_ve_gunluk_bar_yazilir(self):
        self.d.spot_kaydet("THYAO", 281.5)
        i = self.d.istatistik()
        self.assertEqual(i["spot_ticks"], 1)
        self.assertEqual(i["spot_days"], 1)
        self.assertEqual(i["spot_tickers"], 1)

    def test_gunluk_bar_artimli_guncellenir(self):
        """Ayni gun icinde high/low genisler, close son degere kayar."""
        for f in (100.0, 105.0, 95.0, 102.0):
            self.d.spot_kaydet("THYAO", f, zorla=True)
        s = self.d.conn.execute(
            "SELECT open, high, low, close, n FROM spot_daily WHERE ticker='THYAO'"
        ).fetchone()
        self.assertEqual(s["open"], 100.0)
        self.assertEqual(s["high"], 105.0)
        self.assertEqual(s["low"], 95.0)
        self.assertEqual(s["close"], 102.0, "kapanis son degere kaymali")
        self.assertEqual(s["n"], 4)

    def test_ornekleme_yazimi_kisitlar(self):
        """
        Her tick'i yazmak gereksiz hacim uretir; ornekleme araligi icindeki
        ikinci yazim atlanmali.
        """
        self.assertTrue(self.d.spot_kaydet("THYAO", 100.0))
        self.assertFalse(self.d.spot_kaydet("THYAO", 101.0),
                         "ornekleme araliginda ikinci yazim gecmemeli")
        self.assertTrue(self.d.spot_kaydet("THYAO", 102.0, zorla=True),
                        "zorla=True ornekleme kapisini asmali")

    def test_ornekleme_ticker_basina(self):
        """Bir ticker'in yazilmasi diger ticker'i engellememeli."""
        self.assertTrue(self.d.spot_kaydet("THYAO", 100.0))
        self.assertTrue(self.d.spot_kaydet("GARAN", 126.0))

    def test_kapanislar_artan_sirada(self):
        self.d.spot_kaydet("THYAO", 100.0, zorla=True)
        self.d.conn.execute(
            "INSERT INTO spot_daily (d, ticker, open, high, low, close, n, data_mode) "
            "VALUES ('2026-01-01','THYAO',90,90,90,90,1,'MOCK')")
        self.d.conn.commit()
        gunler = [g for g, _ in self.d.gunluk_kapanislar("THYAO")]
        self.assertEqual(gunler, sorted(gunler), "kapanislar artan sirada donmeli")

    def test_ticker_buyuk_harfe_cevrilir(self):
        self.d.spot_kaydet("THYAO", 100.0)
        self.assertEqual(len(self.d.gunluk_kapanislar("thyao")), 1)

    def test_gun_sayisina_gore_ticker_secimi(self):
        """
        Gun SAYISI sayilmali, gun icindeki tick sayisi degil.

        spot_daily'nin "n" adli bir kolonu var; HAVING icinde COUNT(*)
        yerine takma ad kullanildiginda SQLite o kolonu cozuyor ve her bar
        icin n=1 oldugundan kosul daima yanlis cikiyordu. Sonuc: GARCH
        hicbir ticker gormuyordu. Bu testin varlik sebebi o.
        """
        # Tek gun, cok tick -> esik 2 gunken secilmemeli
        for f in (100.0, 101.0, 102.0):
            self.d.spot_kaydet("TEKGUN", f, zorla=True)
        # Iki ayri gun, gun basina tek tick -> secilmeli
        self.d.conn.executemany(
            "INSERT INTO spot_daily (d, ticker, open, high, low, close, n, data_mode) "
            "VALUES (?, 'IKIGUN', 100, 100, 100, 100, 1, 'MOCK')",
            [("2026-01-01",), ("2026-01-02",)])
        self.d.conn.commit()

        secilen = self.d.gunluk_kapanisi_olan_tickerlar(asgari_gun=2)
        self.assertIn("IKIGUN", secilen, "iki gunluk gecmis esigi gecmeli")
        self.assertNotIn("TEKGUN", secilen,
                         "tek gunluk gecmis, gun icinde 3 tick olsa da gecmemeli")


class TestModAyrimi(unittest.TestCase):
    """
    Mock ve canli gecmis AYNI dosyada bile birbirine karismamali.

    Uretilmis fiyatlardan uydurulmus bir volatilite, piyasadan
    uydurulmus gibi gorunemez; tersi de gecerli.
    """

    def setUp(self):
        self.dizin = tempfile.mkdtemp(prefix="depo-mod-")
        self.yol = os.path.join(self.dizin, "test.db")

    def tearDown(self):
        shutil.rmtree(self.dizin, ignore_errors=True)

    def test_mod_gecmisleri_ayrik(self):
        mock = S.Store(self.yol, data_mode="MOCK")
        mock.spot_kaydet("THYAO", 100.0)
        mock.model_surum_yaz("nss", {"b0": 0.3})
        mock.kapat()

        canli = S.Store(self.yol, data_mode="LIVE")
        try:
            self.assertEqual(canli.istatistik()["spot_ticks"], 0,
                             "MOCK tick'leri LIVE modda gorunmemeli")
            self.assertEqual(canli.gunluk_kapanislar("THYAO"), [])
            self.assertEqual(canli.model_surumleri(), [],
                             "MOCK model surumu LIVE modda gorunmemeli")
            canli.spot_kaydet("THYAO", 200.0)
            self.assertEqual(canli.istatistik()["spot_ticks"], 1)
        finally:
            canli.kapat()

        geri = S.Store(self.yol, data_mode="MOCK")
        try:
            self.assertEqual(geri.istatistik()["spot_ticks"], 1,
                             "LIVE yazimi MOCK gecmisini bozmamali")
            self.assertEqual(geri.gunluk_kapanislar("THYAO")[-1][1], 100.0)
        finally:
            geri.kapat()

    def test_snapshot_mod_basina_ayri(self):
        mock = S.Store(self.yol, data_mode="MOCK")
        mock.snapshot_yaz("spot", {"THYAO": 100.0})
        mock.kapat()
        canli = S.Store(self.yol, data_mode="LIVE")
        try:
            self.assertIsNone(canli.snapshot_oku("spot"))
        finally:
            canli.kapat()


class TestOranlarVeOpsiyonlar(DepoTemel):

    ORANLAR = {
        "THYAO": {
            "F_THYAO1026": {"rate": 0.295, "bid_rate": 0.28, "ask_rate": 0.31,
                            "fut_mid": 290.0, "spot_mid": 281.5, "dtm": 26},
        },
        "GARAN": {
            "F_GARAN1026": {"rate": 0.302, "fut_mid": 130.0, "spot_mid": 126.9, "dtm": 26},
        },
    }

    def test_oranlar_yazilir(self):
        self.assertTrue(self.d.oranlar_kaydet(self.ORANLAR, [{"code": "1026", "dtm": 26}]))
        self.assertEqual(self.d.istatistik()["futures_ticks"], 2)
        s = self.d.conn.execute(
            "SELECT rate, bid_rate, ask_rate, dtm FROM futures_rate_tick "
            "WHERE ticker='THYAO'").fetchone()
        self.assertAlmostEqual(s["rate"], 0.295)
        self.assertAlmostEqual(s["bid_rate"], 0.28)
        self.assertEqual(s["dtm"], 26)

    def test_bos_oran_yazimi_atlanir(self):
        self.assertFalse(self.d.oranlar_kaydet({}, []))
        self.assertFalse(self.d.oranlar_kaydet({"X": "bozuk"}, []))

    def test_opsiyonlar_yazilir(self):
        satirlar = [
            {"expiry": "1026", "strike": 280.0, "dtm": 26,
             "call_bid_price": 12.0, "call_ask_price": 12.5, "call_bid_iv": 0.41,
             "call_delta": 0.52, "put_bid_price": 9.0, "put_ask_price": 9.4},
            {"expiry": "1026", "strike": 290.0, "dtm": 26, "call_bid_price": 7.0},
        ]
        self.assertTrue(self.d.opsiyonlar_kaydet("THYAO", satirlar))
        self.assertEqual(self.d.istatistik()["option_quotes"], 2)

    def test_kullanim_fiyati_olmayan_satir_elenir(self):
        self.assertFalse(self.d.opsiyonlar_kaydet(
            "THYAO", [{"expiry": "1026"}, {"strike": 100.0}, "bozuk"]))


class TestSnapshot(DepoTemel):

    def test_gidis_donus(self):
        self.d.snapshot_yaz("spot", {"THYAO": 281.5, "GARAN": 126.9})
        s = self.d.snapshot_oku("spot")
        self.assertEqual(s["payload"]["THYAO"], 281.5)
        self.assertTrue(s["ts"])

    def test_ustune_yazilir(self):
        """Anlik goruntu TEK satirdir: gecmis tick tablolarinda tutulur."""
        self.d.snapshot_yaz("spot", {"THYAO": 100.0})
        self.d.snapshot_yaz("spot", {"THYAO": 200.0})
        self.assertEqual(self.d.snapshot_oku("spot")["payload"]["THYAO"], 200.0)
        self.assertEqual(
            self.d.conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0], 1)

    def test_olmayan_anahtar(self):
        self.assertIsNone(self.d.snapshot_oku("yok"))

    def test_bozuk_govde_cokmez(self):
        self.d.conn.execute(
            "INSERT INTO snapshot (key, data_mode, ts, payload) "
            "VALUES ('bozuk','MOCK','x','{gecersiz')")
        self.d.conn.commit()
        self.assertIsNone(self.d.snapshot_oku("bozuk"))


class TestModelSurumleme(DepoTemel):

    def test_surumler_birikir_ustune_yazilmaz(self):
        """
        Surumlemenin butun anlami bu: "hangi parametreyle fiyatlandi"
        sorusu ancak eski satirlar dururken cevaplanabilir.
        """
        self.d.model_surum_yaz("nss", {"b0": 0.28}, fit_quality=0.004)
        self.d.model_surum_yaz("nss", {"b0": 0.31}, fit_quality=0.002)
        self.d.model_surum_yaz("nss", {"b0": 0.33}, fit_quality=0.001)

        s = self.d.model_surumleri(model="nss")
        self.assertEqual(len(s), 3, "her uydurma yeni satir olmali")
        self.assertEqual([x["params"]["b0"] for x in s], [0.33, 0.31, 0.28],
                         "en yeni surum basta donmeli")
        self.assertEqual(s[0]["id"], max(x["id"] for x in s))

    def test_scope_ile_ayrisir(self):
        self.d.model_surum_yaz("garch11", {"alpha": 0.1}, scope="THYAO")
        self.d.model_surum_yaz("garch11", {"alpha": 0.2}, scope="GARAN")
        self.assertEqual(len(self.d.model_surumleri(model="garch11", scope="THYAO")), 1)
        self.assertEqual(len(self.d.model_surumleri(model="garch11")), 2)

    def test_model_ile_suzulur(self):
        self.d.model_surum_yaz("nss", {"b0": 0.3})
        self.d.model_surum_yaz("garch11", {"alpha": 0.1}, scope="THYAO")
        self.assertEqual(len(self.d.model_surumleri(model="nss")), 1)

    def test_meta_ve_kalite_saklanir(self):
        self.d.model_surum_yaz("nss", {"b0": 0.3}, fit_quality=0.0042,
                               meta={"observed": [{"dtm": 26}]})
        s = self.d.model_surumleri(model="nss")[0]
        self.assertAlmostEqual(s["fit_quality"], 0.0042)
        self.assertEqual(s["meta"]["observed"][0]["dtm"], 26)
        self.assertEqual(s["data_mode"], "MOCK")

    def test_limit_uygulanir(self):
        for i in range(10):
            self.d.model_surum_yaz("nss", {"b0": i / 100.0})
        self.assertEqual(len(self.d.model_surumleri(model="nss", limit=4)), 4)


class TestBudama(DepoTemel):

    def test_eski_tickler_silinir_gunluk_barlar_kalir(self):
        """
        Hacmi tick tablolari yapar, degeri gunluk seri tasir. Budama
        birini silip otekini korumali — yoksa 90 gun sonra GARCH'in
        girdisi de silinmis olurdu.
        """
        self.d.spot_kaydet("THYAO", 100.0)
        self.d.model_surum_yaz("nss", {"b0": 0.3})
        self.d.conn.execute(
            "INSERT INTO spot_tick (ts, ticker, mid, data_mode) "
            "VALUES ('2020-01-01T00:00:00.000+00:00','THYAO',50.0,'MOCK')")
        self.d.conn.commit()
        self.assertEqual(self.d.istatistik()["spot_ticks"], 2)

        self.d.buda(30)

        i = self.d.istatistik()
        self.assertEqual(i["spot_ticks"], 1, "yalnizca eski tick silinmeli")
        self.assertEqual(i["spot_days"], 1, "gunluk bar korunmali")
        self.assertEqual(i["model_versions"], 1, "model surumu korunmali")

    def test_sifir_veya_none_gun_islem_yapmaz(self):
        self.d.spot_kaydet("THYAO", 100.0)
        self.assertEqual(self.d.buda(0), {})
        self.assertEqual(self.d.buda(None), {})
        self.assertEqual(self.d.istatistik()["spot_ticks"], 1)


class TestHataDayanikliligi(DepoTemel):

    def test_yazma_hatasi_istisna_firlatmaz(self):
        """
        Depo bir yan kayittir. Baglanti kopsa bile akis surmeli:
        yazma yollari disariya istisna sizdirmamali.
        """
        self.d.conn.close()
        try:
            self.assertFalse(self.d.spot_kaydet("THYAO", 100.0))
            self.assertFalse(self.d.oranlar_kaydet(
                {"T": {"C": {"rate": 0.3}}}, []))
            self.assertFalse(self.d.opsiyonlar_kaydet(
                "T", [{"expiry": "1026", "strike": 100.0}]))
            self.assertFalse(self.d.snapshot_yaz("spot", {"T": 1.0}))
        except Exception as e:                       # noqa: BLE001
            self.fail(f"yazma hatasi disariya sizdi: {type(e).__name__}: {e}")
        self.assertIsNotNone(self.d.yazma_hatasi,
                             "hata yutulmamali, istatistikte gorunmeli")
        # tearDown'da ikinci kez kapanmasin diye yeniden ac
        self.d = S.Store(self.yol, data_mode="MOCK")

    def test_istatistik_yazma_hatasini_bildirir(self):
        self.d.yazma_hatasi = "OperationalError: disk I/O error"
        self.assertEqual(self.d.istatistik()["write_error"],
                         "OperationalError: disk I/O error")


class TestSayiDonusturme(unittest.TestCase):
    """_sayi: akistan gelen her turlu degeri guvenli sayiya cevirmeli."""

    def test_gecerli(self):
        self.assertEqual(S._sayi(1), 1.0)
        self.assertEqual(S._sayi("2.5"), 2.5)
        self.assertEqual(S._sayi(0), 0.0)
        self.assertEqual(S._sayi(-3.5), -3.5)

    def test_gecersiz_none_doner(self):
        for v in (None, "", "abc", [], {}, float("nan"),
                  float("inf"), float("-inf")):
            self.assertIsNone(S._sayi(v), f"{v!r} icin None beklenir")

    def test_bool_sayi_degildir(self):
        """True'yu 1.0 olarak yazmak sessiz veri bozulmasidir."""
        self.assertIsNone(S._sayi(True))
        self.assertIsNone(S._sayi(False))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTahminKaydi(DepoTemel):
    """
    Canli tahmin kaydi ve puanlama.

    backtest.py gecmis veri uzerinde tek seferlik calisir; bu tablo
    uretilen tahmini hedef tarihiyle saklar ve ufuk dolunca puanlar.
    """

    def test_kaydedilir_ve_bekleyen_sayilir(self):
        self.d.tahmin_kaydet("THYAO", "GARCH(1,1)", 30, 42.0, uretim_gunu="2026-09-01")
        o = self.d.tahmin_ozeti()
        self.assertEqual(o["pending"], 1)
        self.assertEqual(o["scored"], 0)
        self.assertEqual(o["next_due"], "2026-10-01", "hedef gun ufuk kadar ileride olmali")

    def test_ayni_gun_ayni_model_IKI_KEZ_yazilmaz(self):
        """
        garch.py 15 dakikada bir calisiyor. Her kosu yeni satir acsaydi
        gun icinde onlarca kopya olusur ve puanlama ayni tahmini
        defalarca sayardi.
        """
        for _ in range(5):
            self.d.tahmin_kaydet("THYAO", "GARCH(1,1)", 30, 42.0, uretim_gunu="2026-09-01")
        self.assertEqual(self.d.tahmin_ozeti()["pending"], 1)

    def test_farkli_ufuk_ve_model_ayri_kayit(self):
        self.d.tahmin_kaydet("THYAO", "GARCH(1,1)", 30, 42.0, uretim_gunu="2026-09-01")
        self.d.tahmin_kaydet("THYAO", "GARCH(1,1)", 15, 44.0, uretim_gunu="2026-09-01")
        self.d.tahmin_kaydet("THYAO", "EGARCH(1,1)", 30, 41.0, uretim_gunu="2026-09-01")
        self.d.tahmin_kaydet("GARAN", "GARCH(1,1)", 30, 39.0, uretim_gunu="2026-09-01")
        self.assertEqual(self.d.tahmin_ozeti()["pending"], 4)

    def test_yalnizca_ufku_DOLMUS_tahminler_puanlanacak_listede(self):
        from datetime import date, timedelta
        bugun = date(2026, 10, 6)
        # Ufku dolmus
        self.d.tahmin_kaydet("THYAO", "GARCH(1,1)", 30, 42.0,
                             uretim_gunu=(bugun - timedelta(days=60)).isoformat())
        # Ufku dolmamis
        self.d.tahmin_kaydet("GARAN", "GARCH(1,1)", 30, 40.0,
                             uretim_gunu=bugun.isoformat())
        bekleyen = self.d.puanlanacak_tahminler(bugun=bugun.isoformat())
        self.assertEqual(len(bekleyen), 1)
        self.assertEqual(bekleyen[0]["ticker"], "THYAO")

    def test_puanlama_skorlari_uretir(self):
        self.d.tahmin_kaydet("THYAO", "GARCH(1,1)", 30, 45.0, uretim_gunu="2026-08-01")
        self.d.tahmin_kaydet("THYAO", "Naive", 30, 55.0, uretim_gunu="2026-08-01")
        bekleyen = self.d.puanlanacak_tahminler(bugun="2026-10-06")
        for k in bekleyen:
            self.d.tahmin_puanla(k["id"], 40.0)     # gerceklesen %40

        s = self.d.tahmin_skorlari(ufuk=30)
        self.assertAlmostEqual(s["GARCH(1,1)"]["bias"], 5.0, places=6)
        self.assertAlmostEqual(s["Naive"]["bias"], 15.0, places=6)
        self.assertAlmostEqual(s["GARCH(1,1)"]["mae"], 5.0, places=6)
        self.assertEqual(self.d.tahmin_ozeti()["scored"], 2)
        self.assertEqual(self.d.tahmin_ozeti()["pending"], 0)

    def test_yanlilik_isareti_yonu_gosterir(self):
        """Yuksek tahmin pozitif, dusuk tahmin negatif yanlilik vermeli."""
        self.d.tahmin_kaydet("A", "M", 30, 60.0, uretim_gunu="2026-08-01")
        self.d.tahmin_kaydet("B", "M", 30, 20.0, uretim_gunu="2026-08-01")
        for k in self.d.puanlanacak_tahminler(bugun="2026-10-06"):
            self.d.tahmin_puanla(k["id"], 40.0)
        self.assertAlmostEqual(self.d.tahmin_skorlari(ufuk=30)["M"]["bias"], 0.0, places=6)

    def test_ufka_gore_suzulur(self):
        self.d.tahmin_kaydet("A", "M", 30, 50.0, uretim_gunu="2026-08-01")
        self.d.tahmin_kaydet("A", "M", 15, 45.0, uretim_gunu="2026-08-01")
        for k in self.d.puanlanacak_tahminler(bugun="2026-10-06"):
            self.d.tahmin_puanla(k["id"], 40.0)
        self.assertAlmostEqual(self.d.tahmin_skorlari(ufuk=30)["M"]["bias"], 10.0, places=6)
        self.assertAlmostEqual(self.d.tahmin_skorlari(ufuk=15)["M"]["bias"], 5.0, places=6)
        self.assertEqual(self.d.tahmin_skorlari()["M"]["n"], 2, "ufuksuz cagri hepsini almali")

    def test_puanlanmamis_tahmin_skora_girmez(self):
        self.d.tahmin_kaydet("A", "M", 30, 50.0, uretim_gunu="2026-08-01")
        self.assertEqual(self.d.tahmin_skorlari(), {})

    def test_mod_ayrimi_tahminlerde_de_gecerli(self):
        self.d.tahmin_kaydet("THYAO", "M", 30, 42.0, uretim_gunu="2026-08-01")
        canli = S.Store(self.yol, data_mode="LIVE")
        try:
            self.assertEqual(canli.tahmin_ozeti()["pending"], 0,
                             "MOCK tahmini LIVE modda gorunmemeli")
            self.assertEqual(canli.puanlanacak_tahminler(bugun="2026-10-06"), [])
        finally:
            canli.kapat()
