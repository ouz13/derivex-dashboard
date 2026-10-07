#!/usr/bin/env python3
"""
import_history.py testleri.

    python3 -m unittest discover tests -p "test_import_history.py"

Agirlik merkezi: SESSIZCE YANLIS aktarim. Bir aktarim aracinda en kotu
sonuc hata vermesi degil, hata vermeden yanlis veri yazmasidir —
ozellikle gun/ay sirasi ve mock/live etiketi.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import import_history as ih                                   # noqa: E402
import store as st                                            # noqa: E402


class TarihSirasiCikarimi(unittest.TestCase):
    """Gun/ay sirasi TAHMIN EDILMEMELI."""

    def test_iso_belirsizlik_yok(self):
        sira, _ = ih.sira_bul(["2016-01-04", "2016-01-05"])
        self.assertEqual(sira, "iso")

    def test_yyyymmdd_de_belirsiz_degil(self):
        sira, _ = ih.sira_bul(["20160104", "20160105"])
        self.assertEqual(sira, "iso")

    def test_gun_12den_buyukse_dmy(self):
        sira, aciklama = ih.sira_bul(["31/01/2016", "01/02/2016"])
        self.assertEqual(sira, "dmy")
        self.assertIn("day > 12", aciklama)

    def test_ay_konumu_12den_buyukse_mdy(self):
        sira, aciklama = ih.sira_bul(["01/31/2016", "02/01/2016"])
        self.assertEqual(sira, "mdy")
        self.assertIn("month > 12", aciklama)

    def test_BELIRSIZSE_None(self):
        # Hicbir satirda 12'den buyuk bilesen yok: DD/MM ile MM/DD ayirt
        # edilemez. Burada tahmin etmek, 10 yillik seriyi gun ve ayi ters
        # cevrilmis halde yuklemek demek — hicbir hata cikmaz, yalnizca
        # her getiri yanlis olur.
        sira, aciklama = ih.sira_bul(["03/04/2016", "04/05/2016"])
        self.assertIsNone(sira)
        self.assertIn("ambiguous", aciklama)

    def test_CELISKILIYSE_None(self):
        # Hem 31/01 hem 01/31 var: dosyanin kendisi tutarsiz.
        sira, aciklama = ih.sira_bul(["31/01/2016", "01/31/2016"])
        self.assertIsNone(sira)
        self.assertIn("conflicting", aciklama)

    def test_nokta_ve_tire_ayiricilar(self):
        self.assertEqual(ih.sira_bul(["31.01.2016"])[0], "dmy")
        self.assertEqual(ih.sira_bul(["31-01-2016"])[0], "dmy")


class TarihCozme(unittest.TestCase):

    def test_iso(self):
        self.assertEqual(ih.tarih_coz("2016-01-04", "iso"), "2016-01-04")

    def test_tek_haneli_ay_gun(self):
        self.assertEqual(ih.tarih_coz("2016-1-4", "iso"), "2016-01-04")

    def test_zaman_damgasindan_gun_alinir(self):
        self.assertEqual(ih.tarih_coz("2016-01-04T13:45:00Z", "iso"),
                         "2016-01-04")

    def test_dmy_ve_mdy_AYNI_metni_farkli_cozer(self):
        # Testin varlik sebebi: siranin gercekten etkisi oldugunu sabitlemek.
        self.assertEqual(ih.tarih_coz("03/04/2016", "dmy"), "2016-04-03")
        self.assertEqual(ih.tarih_coz("03/04/2016", "mdy"), "2016-03-04")

    def test_yyyymmdd(self):
        self.assertEqual(ih.tarih_coz("20160104", "iso"), "2016-01-04")

    def test_gecersiz_takvim_gunu(self):
        # 30 Subat gecerli bir metin ama gecerli bir gun degil.
        self.assertIsNone(ih.tarih_coz("2016-02-30", "iso"))
        self.assertIsNone(ih.tarih_coz("31/13/2016", "dmy"))

    def test_bos_ve_anlamsiz(self):
        for kotu in (None, "", "   ", "abc", "2016", "4 Ocak 2016"):
            self.assertIsNone(ih.tarih_coz(kotu, "iso"), repr(kotu))


class BarDogrulama(unittest.TestCase):

    DUN = (date.today() - timedelta(days=1)).isoformat()

    def coz(self, satirlar, **ek):
        return ih.barlari_dogrula(satirlar, "iso", **ek)

    def test_asgari_satir(self):
        barlar, red, _ = self.coz([{"date": "2024-01-02", "ticker": "THYAO",
                                    "close": "10.5"}])
        self.assertEqual(len(barlar), 1)
        gun, t, o, h, l, c = barlar[0]
        self.assertEqual((gun, t, c), ("2024-01-02", "THYAO", 10.5))
        # open/high/low verilmediyse close'dan doldurulur.
        self.assertEqual((o, h, l), (10.5, 10.5, 10.5))
        self.assertEqual(sum(red.values()), 0)

    def test_ohlc_korunur(self):
        barlar, _, _ = self.coz([{"date": "2024-01-02", "ticker": "X",
                                  "open": 10, "high": 12, "low": 9, "close": 11}])
        self.assertEqual(barlar[0][2:], (10.0, 12.0, 9.0, 11.0))

    def test_kapanis_yoksa_red(self):
        for kotu in (None, "", "abc", 0, -5, [], {}, True):
            _, red, _ = self.coz([{"date": "2024-01-02", "ticker": "X",
                                   "close": kotu}])
            self.assertEqual(sum(red.values()), 1, repr(kotu) + " kabul edildi")

    def test_ticker_yoksa_red(self):
        _, red, _ = self.coz([{"date": "2024-01-02", "close": 10}])
        self.assertIn("invalid or missing ticker", red)

    def test_sabit_ticker_kolonun_yerine_gecer(self):
        barlar, _, _ = self.coz([{"date": "2024-01-02", "close": 10}],
                                sabit_ticker="THYAO")
        self.assertEqual(barlar[0][1], "THYAO")

    def test_HIGH_LOWDAN_KUCUKSE_reddedilir_duzeltilmez(self):
        # Bozuk veriyi "duzeltmek" bozuklugu gizlemek olur.
        _, red, _ = self.coz([{"date": "2024-01-02", "ticker": "X",
                               "high": 9, "low": 12, "close": 10}])
        self.assertIn("high below low", red)

    def test_kapanis_aralik_disindaysa_red(self):
        _, red, _ = self.coz([{"date": "2024-01-02", "ticker": "X",
                               "high": 11, "low": 10, "close": 15}])
        self.assertIn("open/close outside high-low range", red)

    def test_gelecek_tarih_red(self):
        yarin = (date.today() + timedelta(days=1)).isoformat()
        _, red, _ = self.coz([{"date": yarin, "ticker": "X", "close": 10}])
        self.assertIn("date in the future", red)

    def test_BUGUNUN_BARI_varsayilan_atlanir(self):
        # Bugunun bari hala olusuyor; tek satirla ezmek gun ici biriken
        # gercek gozlemi silerdi.
        bugun = date.today().isoformat()
        barlar, red, _ = self.coz([{"date": bugun, "ticker": "X", "close": 10}])
        self.assertEqual(barlar, [])
        self.assertTrue(any("today" in k for k in red))

    def test_bugun_acikca_istenirse_alinir(self):
        bugun = date.today().isoformat()
        barlar, _, _ = self.coz([{"date": bugun, "ticker": "X", "close": 10}],
                                bugune_izin=True)
        self.assertEqual(len(barlar), 1)

    def test_cakisan_satirlarin_HEPSI_atilir(self):
        # "Son satir kazanir" demek dosyadaki siraya guvenmek olurdu.
        satirlar = [{"date": "2024-01-02", "ticker": "X", "close": 10},
                    {"date": "2024-01-02", "ticker": "X", "close": 11},
                    {"date": "2024-01-02", "ticker": "X", "close": 12},
                    {"date": "2024-01-03", "ticker": "X", "close": 13}]
        barlar, red, cakisma = self.coz(satirlar)
        self.assertEqual([b[0] for b in barlar], ["2024-01-03"])
        self.assertEqual(cakisma, 1, "uc satir tek cakisma sayilmali")
        self.assertIn("duplicate ticker/date (all dropped)", red)

    def test_ayni_gun_farkli_ticker_cakisma_degil(self):
        barlar, _, cakisma = self.coz(
            [{"date": "2024-01-02", "ticker": "A", "close": 10},
             {"date": "2024-01-02", "ticker": "B", "close": 11}])
        self.assertEqual(len(barlar), 2)
        self.assertEqual(cakisma, 0)

    def test_siralama_ticker_sonra_gun(self):
        barlar, _, _ = self.coz(
            [{"date": "2024-01-03", "ticker": "B", "close": 1},
             {"date": "2024-01-02", "ticker": "B", "close": 1},
             {"date": "2024-01-02", "ticker": "A", "close": 1}])
        self.assertEqual([(b[1], b[0]) for b in barlar],
                         [("A", "2024-01-02"), ("B", "2024-01-02"),
                          ("B", "2024-01-03")])


class BoslukRaporu(unittest.TestCase):

    def test_hafta_sonu_bosluk_SAYILMAZ(self):
        # 2024-01-05 Cuma, 2024-01-08 Pazartesi.
        b = ih.bosluklar(["2024-01-05", "2024-01-08"])
        self.assertEqual(b["missing_business_days"], 0)

    def test_eksik_is_gunu_sayilir(self):
        # 2024-01-02 Sali, 2024-01-04 Persembe -> 03 Carsamba eksik.
        b = ih.bosluklar(["2024-01-02", "2024-01-04"])
        self.assertEqual(b["missing_business_days"], 1)
        self.assertEqual(b["examples"], ["2024-01-03"])

    def test_en_uzun_kesinti(self):
        # Bir hafta tamamen eksik: 5 is gunu.
        gunler = ["2024-01-05"] + ["2024-01-15", "2024-01-16"]
        b = ih.bosluklar(gunler)
        self.assertGreaterEqual(b["longest_run"], 5)

    def test_tek_gun_ve_bos_liste(self):
        self.assertEqual(ih.bosluklar([])["missing_business_days"], 0)
        self.assertEqual(ih.bosluklar(["2024-01-02"])["missing_business_days"], 0)


class HaritaCozme(unittest.TestCase):

    def test_gecerli_eslem(self):
        self.assertEqual(ih._harita_coz(["Fiyat=close"]), {"Fiyat": "close"})

    def test_bicim_hatasi(self):
        with self.assertRaises(SystemExit):
            ih._harita_coz(["Fiyat"])

    def test_bilinmeyen_kanonik_ad(self):
        with self.assertRaises(SystemExit):
            ih._harita_coz(["Fiyat=yokboylebirsey"])


class DosyaOkuma(unittest.TestCase):

    def yaz(self, ad, icerik):
        yol = os.path.join(self.dizin.name, ad)
        with open(yol, "w", encoding="utf-8") as f:
            f.write(icerik)
        return yol

    def setUp(self):
        self.dizin = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dizin.cleanup()

    def test_csv_turkce_basliklar(self):
        yol = self.yaz("a.csv", "tarih,hisse,kapanis\n2024-01-02,THYAO,10\n")
        satirlar, bicim = ih.satirlari_oku(yol)
        self.assertEqual(bicim, "csv")
        self.assertEqual(satirlar[0]["ticker"], "THYAO")
        self.assertEqual(satirlar[0]["close"], "10")

    def test_csv_zorunlu_kolon_eksikse_ANLATARAK_durur(self):
        yol = self.yaz("a.csv", "ilgisiz,kolonlar\n1,2\n")
        with self.assertRaises(SystemExit) as c:
            ih.satirlari_oku(yol)
        mesaj = str(c.exception)
        self.assertIn("date", mesaj)
        self.assertIn("close", mesaj)
        self.assertIn("--map", mesaj)       # cikis yolu gosterilmeli

    def test_csv_acik_eslem_kurtarir(self):
        yol = self.yaz("a.csv", "gun,Fiyat\n2024-01-02,10\n")
        satirlar, _ = ih.satirlari_oku(yol, {"Fiyat": "close"})
        self.assertEqual(satirlar[0]["close"], "10")

    def test_json_liste(self):
        yol = self.yaz("a.json",
                       '[{"date":"2024-01-02","ticker":"X","close":10}]')
        satirlar, bicim = ih.satirlari_oku(yol)
        self.assertEqual(bicim, "json")
        self.assertEqual(satirlar[0]["ticker"], "X")

    def test_json_sarmalayici_anahtar(self):
        yol = self.yaz("a.json",
                       '{"rows":[{"date":"2024-01-02","ticker":"X","close":10}]}')
        satirlar, _ = ih.satirlari_oku(yol)
        self.assertEqual(len(satirlar), 1)

    def test_json_ticker_anahtarli_sozluk(self):
        # {"THYAO": [{...}]} bicimi: ticker anahtardan gelir.
        yol = self.yaz("a.json",
                       '{"THYAO":[{"date":"2024-01-02","close":10},'
                       '{"date":"2024-01-03","close":11}]}')
        satirlar, _ = ih.satirlari_oku(yol)
        self.assertEqual(len(satirlar), 2)
        self.assertEqual({s["ticker"] for s in satirlar}, {"THYAO"})

    def test_bos_dosya(self):
        yol = self.yaz("a.csv", "   \n")
        with self.assertRaises(SystemExit):
            ih.satirlari_oku(yol)

    def test_bozuk_json(self):
        yol = self.yaz("a.json", "{bozuk")
        with self.assertRaises(SystemExit):
            ih.satirlari_oku(yol)

    def test_uzantisiz_json_icerikten_anlasilir(self):
        yol = self.yaz("a.txt", '[{"date":"2024-01-02","ticker":"X","close":1}]')
        _, bicim = ih.satirlari_oku(yol)
        self.assertEqual(bicim, "json")


class DepoAktarimi(unittest.TestCase):
    """store.gunluk_barlari_aktar — asil yazim yolu."""

    def setUp(self):
        self.dizin = tempfile.TemporaryDirectory()
        self.yol = os.path.join(self.dizin.name, "t.db")
        self.depo = st.Store(yol=self.yol, data_mode="LIVE")

    def tearDown(self):
        self.depo.kapat()
        self.dizin.cleanup()

    @staticmethod
    def bar(gun, ticker="X", kapanis=10.0):
        return (gun, ticker, kapanis, kapanis, kapanis, kapanis)

    def test_yazim_ve_okuma(self):
        s = self.depo.gunluk_barlari_aktar(
            [self.bar("2024-01-02", kapanis=10), self.bar("2024-01-03", kapanis=11)])
        self.assertEqual(s["yazilan"], 2)
        self.assertEqual(self.depo.gunluk_kapanislar("X"),
                         [("2024-01-02", 10.0), ("2024-01-03", 11.0)])

    def test_IDEMPOTENT(self):
        barlar = [self.bar("2024-01-02"), self.bar("2024-01-03")]
        self.depo.gunluk_barlari_aktar(barlar)
        self.depo.gunluk_barlari_aktar(barlar)
        self.assertEqual(len(self.depo.gunluk_kapanislar("X")), 2)

    def test_aktarilan_bar_n_SIFIR_ile_isaretlenir(self):
        # Hangi barin olculdugu, hangisinin aktarildigi ayirt edilebilmeli.
        self.depo.gunluk_barlari_aktar([self.bar("2024-01-02")])
        n = self.depo.conn.execute(
            "SELECT n FROM spot_daily WHERE ticker='X'").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_TICKTEN_OLUSMUS_bar_EZILMEZ(self):
        # spot_kaydet ile gercek bir gozlem yazilir (n=1 olur), sonra ayni
        # gun icin aktarim denenir.
        self.depo.spot_kaydet("X", 99.0, zorla=True)
        gun = self.depo.conn.execute(
            "SELECT d FROM spot_daily WHERE ticker='X'").fetchone()["d"]
        s = self.depo.gunluk_barlari_aktar([self.bar(gun, kapanis=10.0)])
        self.assertEqual(s["yazilan"], 0)
        self.assertEqual(s["korunan"], 1)
        # Gercek gozlem yerinde durmali.
        kapanis = self.depo.conn.execute(
            "SELECT close FROM spot_daily WHERE ticker='X'").fetchone()["close"]
        self.assertEqual(kapanis, 99.0)

    def test_overwrite_acikca_istenirse_ezer(self):
        self.depo.spot_kaydet("X", 99.0, zorla=True)
        gun = self.depo.conn.execute(
            "SELECT d FROM spot_daily WHERE ticker='X'").fetchone()["d"]
        s = self.depo.gunluk_barlari_aktar([self.bar(gun, kapanis=10.0)],
                                           uzerine_yaz=True)
        self.assertEqual(s["yazilan"], 1)
        kapanis = self.depo.conn.execute(
            "SELECT close FROM spot_daily WHERE ticker='X'").fetchone()["close"]
        self.assertEqual(kapanis, 10.0)

    def test_aktarilan_bar_tekrar_aktarimda_SERBEST_yenilenir(self):
        # n=0 oldugu icin koruma kurali ona takilmaz; aktarim idempotent
        # kalirken duzeltme de mumkun olur.
        self.depo.gunluk_barlari_aktar([self.bar("2024-01-02", kapanis=10)])
        s = self.depo.gunluk_barlari_aktar([self.bar("2024-01-02", kapanis=12)])
        self.assertEqual(s["yazilan"], 1)
        self.assertEqual(s["korunan"], 0)
        self.assertEqual(self.depo.gunluk_kapanislar("X"), [("2024-01-02", 12.0)])

    def test_MOCK_ve_LIVE_karismaz(self):
        # Deponun tum tasarimi buna dayaniyor: aktarilan gercek veri,
        # uretilmis veriyle ayni seriye girmemeli.
        self.depo.gunluk_barlari_aktar([self.bar("2024-01-02", kapanis=10)])
        mock = st.Store(yol=self.yol, data_mode="MOCK")
        try:
            self.assertEqual(mock.gunluk_kapanislar("X"), [])
            mock.gunluk_barlari_aktar([self.bar("2024-01-02", kapanis=55)])
            self.assertEqual(mock.gunluk_kapanislar("X"), [("2024-01-02", 55.0)])
            # LIVE serisi etkilenmemis olmali.
            self.assertEqual(self.depo.gunluk_kapanislar("X"),
                             [("2024-01-02", 10.0)])
        finally:
            mock.kapat()

    def test_bos_girdi(self):
        s = self.depo.gunluk_barlari_aktar([])
        self.assertEqual(s, {"yazilan": 0, "korunan": 0, "korunan_ornekler": []})

    def test_cok_ticker_500_uzeri_dilimlenir(self):
        # Koruma sorgusu 500'luk dilimler halinde calisiyor; SQLite'in
        # degisken sayisi sinirina takilmamali.
        barlar = [self.bar("2024-01-02", ticker="T%04d" % i) for i in range(1200)]
        s = self.depo.gunluk_barlari_aktar(barlar)
        self.assertEqual(s["yazilan"], 1200)

    def test_gunluk_ozet(self):
        self.depo.gunluk_barlari_aktar(
            [self.bar("2024-01-02", "A"), self.bar("2024-01-03", "A"),
             self.bar("2024-01-02", "B")])
        ozet = {o["ticker"]: o for o in self.depo.gunluk_ozet()}
        self.assertEqual(ozet["A"]["days"], 2)
        self.assertEqual(ozet["A"]["first"], "2024-01-02")
        self.assertEqual(ozet["A"]["last"], "2024-01-03")
        self.assertEqual(ozet["A"]["imported"], 2)
        self.assertEqual(ozet["B"]["days"], 1)

    def test_gunler(self):
        self.depo.gunluk_barlari_aktar(
            [self.bar("2024-01-03"), self.bar("2024-01-02")])
        self.assertEqual(self.depo.gunler("X"), ["2024-01-02", "2024-01-03"])

    def test_GARCH_esigi_aktarimla_gecilir(self):
        # Aracin varlik sebebi: GARCH 60 getirinin altinda uyum yapmayi
        # reddediyor ve depo ileriye birikerek bu esige aylarca gelmiyor.
        gun = date(2024, 1, 1)
        barlar = []
        for i in range(80):
            while gun.weekday() >= 5:
                gun += timedelta(days=1)
            barlar.append(self.bar(gun.isoformat(), kapanis=100 + i * 0.1))
            gun += timedelta(days=1)
        self.depo.gunluk_barlari_aktar(barlar)
        self.assertIn("X", self.depo.gunluk_kapanisi_olan_tickerlar(
            asgari_gun=ih.GARCH_ASGARI_GETIRI + 1))


class SemaBeklentisi(unittest.TestCase):
    """Aktarim varsaydigi sema kisitina gercekten dayaniyor mu?"""

    def test_birincil_anahtar_ticker_gun_mod(self):
        # Idempotentlik tamamen bu kisita dayaniyor. Kisit degisirse
        # aktarim sessizce satir cogaltmaya baslardi.
        dizin = tempfile.TemporaryDirectory()
        try:
            depo = st.Store(yol=os.path.join(dizin.name, "t.db"),
                            data_mode="LIVE")
            try:
                bilgi = depo.conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name='spot_daily'"
                ).fetchone()["sql"]
                self.assertIn("PRIMARY KEY (ticker, d, data_mode)",
                              " ".join(bilgi.split()))
            finally:
                depo.kapat()
        finally:
            dizin.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
