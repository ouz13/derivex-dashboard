#!/usr/bin/env python3
"""
feed_schema.py testleri.

    python3 -m unittest tests.test_feed_schema -v

Testlerin agirlik merkezi su iki soru:

  1. Dort ayri tel bicimi GERCEKTEN ayni kanonik kayda mi cikiyor?
     Cikmazsa modulun tek varlik sebebi yok.
  2. Bozuk kayit SESSIZCE dusuyor mu?
     Sessiz dusme, boru hattinin boslugunu kaynaginkiyle karistirir.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feed_schema as fs  # noqa: E402


class KanonikEsdegerlik(unittest.TestCase):
    """Ayni kotasyon, dort bicim, tek sonuc."""

    # Hepsi THYAO 279.50 / 280.50
    ORNEKLER = {
        "idealdata": "YU;1=THYAO;6=279.50;9=280.50;7=100;10=250|",
        "fix": "8=FIX.4.4|55=THYAO|269=0|270=279.50|271=100|269=1|270=280.50|271=250|",
        "csv": "symbol,bid,ask\nTHYAO,279.50,280.50\n",
        "xml": '<quotes><quote symbol="THYAO" bid="279.50" ask="280.50"/></quotes>',
    }

    def test_dort_bicim_ayni_mid(self):
        hat = fs.varsayilan_hat()
        midler = set()
        for kaynak, ham in self.ORNEKLER.items():
            kayitlar = hat.besle(kaynak, ham)
            self.assertEqual(len(kayitlar), 1, kaynak + " tek kayit vermedi")
            k = kayitlar[0]
            self.assertEqual(k["ticker"], "THYAO", kaynak)
            self.assertAlmostEqual(k["bid"], 279.50, msg=kaynak)
            self.assertAlmostEqual(k["ask"], 280.50, msg=kaynak)
            midler.add(round(k["mid"], 6))
        self.assertEqual(midler, {280.0},
                         "bicimler farkli mid uretti: " + str(midler))

    def test_her_kayit_kaynagini_tasir(self):
        # source olmadan devretme (E5) imkansiz: devretme aninda hangi
        # fiyatin hangi kaynaktan geldigi ayirt edilemez.
        hat = fs.varsayilan_hat()
        for kaynak, ham in self.ORNEKLER.items():
            k = hat.besle(kaynak, ham)[0]
            self.assertEqual(k["source"], kaynak)

    def test_kanonik_alanlar_tam(self):
        hat = fs.varsayilan_hat()
        k = hat.besle("idealdata", self.ORNEKLER["idealdata"])[0]
        for alan in fs.SPOT_ALANLARI:
            self.assertIn(alan, k, "eksik kanonik alan: " + alan)


class MidTuretme(unittest.TestCase):

    def test_iki_tarafli(self):
        mid, flags = fs.mid_turet(100, 102)
        self.assertEqual(mid, 101.0)
        self.assertEqual(flags, [])

    def test_tek_tarafli_isaretlenir(self):
        for bid, ask in ((100, None), (None, 102)):
            mid, flags = fs.mid_turet(bid, ask)
            self.assertIn("one-sided", flags)
            self.assertEqual(mid, bid or ask)

    def test_defter_yoksa_son_islem(self):
        mid, flags = fs.mid_turet(None, None, 99.5)
        self.assertEqual(mid, 99.5)
        self.assertIn("no-book", flags)

    def test_hic_fiyat_yoksa_red(self):
        with self.assertRaises(fs.Red):
            fs.mid_turet(None, None, None)

    def test_capraz_defter_ATILMAZ_isaretlenir(self):
        # Gercek piyasada aninda olur. Atmak o anda fiyati tamamen
        # kaybetmek olurdu; ortasi hala iki kotasyonun arasindadir.
        mid, flags = fs.mid_turet(281, 279)
        self.assertEqual(mid, 280.0)
        self.assertIn("crossed", flags)

    def test_sifir_ve_negatif_fiyat_yok_sayilir(self):
        # 0 gecerli bir fiyat degil; bir kaynak "veri yok" yerine 0
        # gonderdiginde mid 0'a kaymamali.
        mid, flags = fs.mid_turet(0, 102)
        self.assertEqual(mid, 102.0)
        self.assertIn("one-sided", flags)
        with self.assertRaises(fs.Red):
            fs.mid_turet(0, -5)


class SayiDonusumu(unittest.TestCase):

    def test_bool_sayi_sayilmaz(self):
        # float(True) == 1.0 olurdu: True'yu 1 TL fiyat kabul etmek
        # sessiz veri bozulmasidir.
        self.assertIsNone(fs._sayi(True))
        self.assertIsNone(fs._sayi(False))

    def test_bos_ve_kapsayici_degerler(self):
        for kotu in (None, "", "   ", [], {}, "abc", float("nan"),
                     float("inf"), float("-inf")):
            self.assertIsNone(fs._sayi(kotu), repr(kotu) + " sayi sayildi")

    def test_virgullu_ondalik(self):
        # Turkce yerel ayarla kaydedilmis CSV'ler boyle geliyor.
        self.assertEqual(fs._sayi("279,50"), 279.5)

    def test_metin_sayi_kabul(self):
        self.assertEqual(fs._sayi(" 280.5 "), 280.5)


class TickerDogrulama(unittest.TestCase):

    def test_buyuk_harfe_cevrilir(self):
        k = fs.spot_kaydi("t", " thyao ", 100, 102)
        self.assertEqual(k["ticker"], "THYAO")

    def test_bos_ticker_red(self):
        for kotu in (None, "", "   "):
            with self.assertRaises(fs.Red):
                fs.spot_kaydi("t", kotu, 100, 102)

    def test_bosluklu_ticker_red(self):
        # Icinde bosluk ya da ayirici olan "sembol" neredeyse her zaman
        # yanlis alanin okundugunun isareti — satir kaymis demektir.
        for kotu in ("THY AO", "THYAO;6", "a" * 40, "THYAO\n"):
            if kotu.strip() == "THYAO":
                continue                   # strip sonrasi gecerli, ayri konu
            with self.assertRaises(fs.Red):
                fs.spot_kaydi("t", kotu, 100, 102)

    def test_SAF_SAYI_ticker_sayilmaz(self):
        # Kaymis bir CSV satirinda fiyat kolonu ticker kolonuna duser.
        # "280.50" yalnizca izinli karakterlerden olustugu icin en az bir
        # harf sarti olmasa gecerli bir sembol sayilir ve depoya "280.50"
        # adli bir enstruman olarak girerdi.
        for kotu in ("280.50", "0", "1026", "-5", "100.0"):
            with self.assertRaises(fs.Red):
                fs.spot_kaydi("t", kotu, 100, 102)


class OpsiyonKaydi(unittest.TestCase):

    def test_tam_kayit(self):
        k = fs.opsiyon_kaydi("t", "thyao", "1026", 280, "c", 12.4, 12.6)
        self.assertEqual(k["opt_type"], "C")
        self.assertEqual(k["strike"], 280.0)
        self.assertEqual(k["expiry"], "1026")
        self.assertEqual(k["mid"], 12.5)

    def test_call_put_uzun_yazim(self):
        self.assertEqual(
            fs.opsiyon_kaydi("t", "X", "1026", 1, "call", 1, 2)["opt_type"], "C")
        self.assertEqual(
            fs.opsiyon_kaydi("t", "X", "1026", 1, "PUT", 1, 2)["opt_type"], "P")

    def test_gecersiz_tip_red(self):
        for kotu in (None, "", "x", "1"):
            with self.assertRaises(fs.Red):
                fs.opsiyon_kaydi("t", "X", "1026", 1, kotu, 1, 2)

    def test_strike_sifir_ya_da_yoksa_red(self):
        # strike=0 bir opsiyon degil. Number(null)===0 tuzaginin bu
        # modüldeki karsiligi; tipi degil DEGERI kontrol ediyoruz.
        for kotu in (None, 0, "", "abc", [], {}, False):
            with self.assertRaises(fs.Red):
                fs.opsiyon_kaydi("t", "X", "1026", kotu, "C", 1, 2)

    def test_vade_yoksa_red(self):
        with self.assertRaises(fs.Red):
            fs.opsiyon_kaydi("t", "X", "", 280, "C", 1, 2)


class SembolAyristirma(unittest.TestCase):

    def test_opsiyon_gercek_bicim(self):
        # bridge_stream.OPTION_SYMBOL_RE ile ayni bicim.
        r = fs.ayristir_opsiyon_sembolu("O_THYAOE1026C280")
        self.assertEqual(r, {"ticker": "THYAO", "strike": 280.0,
                             "opt_type": "C", "expiry": "1026"})

    def test_opsiyon_ondalikli_strike(self):
        r = fs.ayristir_opsiyon_sembolu("O_THYAOE1026P280.5")
        self.assertEqual(r["strike"], 280.5)
        self.assertEqual(r["opt_type"], "P")

    def test_opsiyon_bozuk_sembol(self):
        for kotu in ("THYAO", "O_THYAO1026C280", "O_THYAOE26C280",
                     "O_THYAOE1026X280", "O_THYAOE1026C", ""):
            self.assertIsNone(fs.ayristir_opsiyon_sembolu(kotu), kotu)

    def test_vadeli_sembol(self):
        self.assertEqual(fs.ayristir_vadeli_sembolu("F_THYAO1026"),
                         {"ticker": "THYAO", "code": "1026"})

    def test_vadeli_ticker_rakam_icerebilir(self):
        # Vade kodu SONDAKI dort hane. Ticker rakam icerse bile bolme
        # dogru olmali; aksi halde ticker'in rakami vade koduna karisirdi.
        r = fs.ayristir_vadeli_sembolu("F_XU0301026")
        self.assertEqual(r, {"ticker": "XU030", "code": "1026"})

    def test_vadeli_bozuk(self):
        for kotu in ("F_THYAO", "THYAO1026", "F_1026", ""):
            self.assertIsNone(fs.ayristir_vadeli_sembolu(kotu), kotu)


class IdealDataAdaptoru(unittest.TestCase):

    def setUp(self):
        self.a = fs.IdealDataAdaptor()

    def test_WU_ve_YU_ayni_sonucu_verir(self):
        # Iki mesaj tipi ayni kotasyonu farkli etiketlerde tasiyor;
        # kanonik ciktinin ayni olmasi tam olarak bu modulun isi.
        wu = self.a.coz("WU;1=THYAO;108=279.50;109=280.50|")[0]
        yu = self.a.coz("YU;1=THYAO;6=279.50;9=280.50|")[0]
        self.assertEqual(wu["mid"], yu["mid"])
        self.assertEqual(wu["ticker"], yu["ticker"])

    def test_ikili_girdi_de_kabul(self):
        # bridge_stream.parse_frame zaten (tip, alanlar) uretiyor; tekrar
        # metne cevirmek gereksiz olurdu.
        k = self.a.coz(("YU", {"1": "THYAO", "6": "279.50", "9": "280.50"}))[0]
        self.assertEqual(k["mid"], 280.0)

    def test_miktarlar_okunur(self):
        k = self.a.coz("YU;1=THYAO;6=279.5;9=280.5;7=100;10=250|")[0]
        self.assertEqual(k["bid_size"], 100)
        self.assertEqual(k["ask_size"], 250)

    def test_DU_yalnizca_en_iyi_seviye(self):
        k = self.a.coz("DU;1=THYAO;100=0;101=B;3=279.5;4=100|")[0]
        self.assertEqual(k["bid"], 279.5)
        self.assertIn("one-sided", k["flags"])
        with self.assertRaises(fs.Red):
            # Seviye 1 derinliktir, en iyi kotasyon degil.
            self.a.coz("DU;1=THYAO;100=1;101=B;3=279.0;4=100|")

    def test_DU_bilinmeyen_taraf(self):
        with self.assertRaises(fs.Red):
            self.a.coz("DU;1=THYAO;100=0;101=Z;3=279.5;4=100|")

    def test_kotasyon_olmayan_mesaj_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("LOGIN;1=THYAO|")

    def test_sembolsuz_mesaj_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("YU;6=279.5;9=280.5|")

    def test_opsiyon_sembolu_opsiyon_kaydi_verir(self):
        k = self.a.coz("YU;1=O_THYAOE1026C280;6=12.4;9=12.6|")[0]
        self.assertEqual(k["type"], fs.OPSIYON)
        self.assertEqual(k["strike"], 280.0)

    def test_vadeli_sembolu_vadeli_kaydi_verir(self):
        k = self.a.coz("YU;1=F_THYAO1026;6=285;9=286|")[0]
        self.assertEqual(k["type"], fs.VADELI)
        self.assertEqual(k["code"], "1026")
        self.assertEqual(k["fut_mid"], 285.5)

    def test_cozulemeyen_opsiyon_sembolu_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("YU;1=O_BOZUK;6=1;9=2|")


class FixAdaptoru(unittest.TestCase):

    def setUp(self):
        self.a = fs.FixAdaptor()

    def test_SOH_ayirici(self):
        ham = "8=FIX.4.4\x0155=THYAO\x01269=0\x01270=279.5\x01269=1\x01270=280.5\x01"
        self.assertEqual(self.a.coz(ham)[0]["mid"], 280.0)

    def test_boru_ayirici(self):
        ham = "55=THYAO|269=0|270=279.5|269=1|270=280.5|"
        self.assertEqual(self.a.coz(ham)[0]["mid"], 280.0)

    def test_islem_girdisi_defter_yoksa_kullanilir(self):
        k = self.a.coz("55=THYAO|269=2|270=280.0|")[0]
        self.assertEqual(k["mid"], 280.0)
        self.assertIn("no-book", k["flags"])

    def test_sembol_etiketi_yoksa_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("269=0|270=279.5|")

    def test_girdi_yoksa_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("55=THYAO|")

    def test_etiket_yoksa_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("bos metin")

    def test_270_oncesinde_269_yoksa_yutulmaz(self):
        # Sirasiz mesajda fiyat sahipsiz kalir; sessizce bid sayilmamali.
        with self.assertRaises(fs.Red):
            self.a.coz("55=THYAO|270=279.5|")


class XmlAdaptoru(unittest.TestCase):

    def setUp(self):
        self.a = fs.XmlAdaptor()

    def test_oznitelik(self):
        k = self.a.coz('<quotes><quote symbol="THYAO" bid="279.5" ask="280.5"/></quotes>')
        self.assertEqual(k[0]["mid"], 280.0)

    def test_alt_oge(self):
        ham = ("<quotes><quote><symbol>THYAO</symbol><bid>279.5</bid>"
               "<ask>280.5</ask></quote></quotes>")
        self.assertEqual(self.a.coz(ham)[0]["mid"], 280.0)

    def test_cok_satir(self):
        ham = ('<quotes><quote symbol="A" bid="1" ask="2"/>'
               '<quote symbol="B" bid="3" ask="4"/></quotes>')
        self.assertEqual([x["ticker"] for x in self.a.coz(ham)], ["A", "B"])

    def test_kismi_basari_kalanlari_dusurur_hepsini_degil(self):
        ham = ('<quotes><quote symbol="A" bid="1" ask="2"/>'
               '<quote symbol="" bid="3" ask="4"/></quotes>')
        k = self.a.coz(ham)
        self.assertEqual(len(k), 1)
        self.assertEqual(k[0]["ticker"], "A")

    def test_bozuk_xml_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("<quotes><quote")

    def test_oge_yoksa_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("<quotes></quotes>")

    def test_tek_oge_kok_olabilir(self):
        k = self.a.coz('<quote symbol="THYAO" bid="279.5" ask="280.5"/>')
        self.assertEqual(len(k), 1)


class CsvAdaptoru(unittest.TestCase):

    def setUp(self):
        self.a = fs.CsvAdaptor()

    def test_turkce_basliklar(self):
        ham = "sembol,alis,satis\nTHYAO,279.5,280.5\n"
        self.assertEqual(self.a.coz(ham)[0]["mid"], 280.0)

    def test_buyuk_harf_ve_bosluklu_basliklar(self):
        ham = "Symbol , BID , ASK \nTHYAO,279.5,280.5\n"
        # Baslik icindeki bosluk sadelesirken yok sayilir.
        k = self.a.coz(ham)
        self.assertEqual(k[0]["ticker"], "THYAO")

    def test_bom_temizlenir(self):
        # Excel'den kaydedilen CSV basa BOM koyar; ilk baslik adi
        # bozulup eslesmezdi.
        ham = "﻿symbol,bid,ask\nTHYAO,279.5,280.5\n"
        self.assertEqual(self.a.coz(ham)[0]["ticker"], "THYAO")

    def test_kapanis_fiyati_defter_yoksa_kullanilir(self):
        k = self.a.coz("symbol,close\nTHYAO,280.0\n")[0]
        self.assertEqual(k["mid"], 280.0)
        self.assertIn("no-book", k["flags"])

    def test_bos_csv_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("")

    def test_tum_satirlar_bozuksa_red(self):
        with self.assertRaises(fs.Red):
            self.a.coz("symbol,bid\n,280\n")


class JsonAdaptoru(unittest.TestCase):

    def test_alan_haritasiyla_baglanir(self):
        # Ikinci kaynagin sozlesmesi geldiginde KOD degil harita yazilacak.
        a = fs.JsonAdaptor("partner", {"s": "ticker", "b": "bid", "a": "ask"})
        k = a.coz('{"s":"THYAO","b":279.5,"a":280.5}')
        self.assertEqual(k[0]["mid"], 280.0)
        self.assertEqual(k[0]["source"], "partner")

    def test_zaten_kanonik_kaynak_harita_gerektirmez(self):
        a = fs.JsonAdaptor("partner")
        k = a.coz({"ticker": "THYAO", "bid": 279.5, "ask": 280.5})
        self.assertEqual(k[0]["mid"], 280.0)

    def test_liste_ve_sarmalayici_anahtar(self):
        a = fs.JsonAdaptor("partner", liste_anahtari="data")
        k = a.coz({"data": [{"ticker": "A", "bid": 1, "ask": 2},
                            {"ticker": "B", "bid": 3, "ask": 4}]})
        self.assertEqual([x["ticker"] for x in k], ["A", "B"])

    def test_opsiyon_turu(self):
        a = fs.JsonAdaptor("partner", kayit_turu=fs.OPSIYON)
        k = a.coz({"ticker": "THYAO", "expiry": "1026", "strike": 280,
                   "opt_type": "C", "bid": 12.4, "ask": 12.6})
        self.assertEqual(k[0]["type"], fs.OPSIYON)

    def test_bozuk_json_red(self):
        with self.assertRaises(fs.Red):
            fs.JsonAdaptor("partner").coz("{bozuk")

    def test_nesne_olmayan_satir_red(self):
        with self.assertRaises(fs.Red):
            fs.JsonAdaptor("partner").coz("[1, 2, 3]")


class HatSayaclari(unittest.TestCase):

    def test_red_sessizce_dusmez_sayilir(self):
        hat = fs.varsayilan_hat()
        hat.besle("idealdata", "YU;1=THYAO;6=279.5;9=280.5|")
        hat.besle("idealdata", "YU;1=THYAO|")               # fiyat yok
        hat.besle("csv", "symbol,bid\n,280\n")              # ticker yok
        r = hat.rapor()
        self.assertEqual(r["accepted"], 1)
        self.assertEqual(r["rejected"], 2)
        self.assertIn("no usable price", r["reject_reasons"])

    def test_bilinmeyen_kaynak_red_olarak_sayilir(self):
        hat = fs.Hat()
        self.assertEqual(hat.besle("yok", "{}"), [])
        self.assertEqual(hat.rapor()["rejected"], 1)
        self.assertIn("unknown source: yok", hat.rapor()["reject_reasons"])

    def test_kaynak_basina_sayac(self):
        hat = fs.varsayilan_hat()
        hat.besle("idealdata", "YU;1=THYAO;6=279.5;9=280.5|")
        hat.besle("fix", "55=X|")
        r = hat.rapor()
        self.assertEqual(r["per_source"]["idealdata"], {"ok": 1, "red": 0})
        self.assertEqual(r["per_source"]["fix"], {"ok": 0, "red": 1})

    def test_adaptor_patlarsa_hat_durmaz(self):
        # Bir kaynagin beklenmeyen hatasi digerlerinin akisini kesmemeli.
        class Patlak(fs.Adaptor):
            ad = "patlak"

            def coz(self, ham):
                raise ZeroDivisionError("beklenmedik")

        hat = fs.varsayilan_hat().kaydet(Patlak())
        self.assertEqual(hat.besle("patlak", "x"), [])
        # Patlamadan sonra saglam kaynak hala calisiyor.
        self.assertEqual(len(hat.besle("idealdata", "YU;1=A;6=1;9=2|")), 1)
        self.assertIn("ZeroDivisionError: beklenmedik",
                      hat.rapor()["reject_reasons"])

    def test_son_kayit_zamani_izlenir(self):
        hat = fs.varsayilan_hat()
        hat.besle("idealdata", "YU;1=THYAO;6=279.5;9=280.5|")
        self.assertIn("idealdata", hat.rapor()["last_record_ts"])


class KaynakSecimi(unittest.TestCase):
    """E5'in karar mantigi — kaynagin kendisi degil."""

    def kur(self):
        return fs.KaynakSecici({"birincil": 0, "yedek": 1}, bayat_sn=30)

    def test_ikisi_de_tazeyse_birincil(self):
        s = self.kur()
        s.isaretle("birincil", 100)
        s.isaretle("yedek", 100)
        self.assertEqual(s.sec(100)[0], "birincil")

    def test_birincil_bayatlarsa_yedege_devreder(self):
        s = self.kur()
        s.isaretle("birincil", 100)
        s.isaretle("yedek", 195)
        aktif, sebep = s.sec(200)
        self.assertEqual(aktif, "yedek")
        self.assertIn("failover", sebep)

    def test_birincil_geri_gelince_OTOMATIK_doner(self):
        # Yedekte takilip kalmak, birincil duzelmis olsa bile daha kotu
        # veriyle devam etmek olurdu.
        s = self.kur()
        s.isaretle("yedek", 195)
        self.assertEqual(s.sec(200)[0], "yedek")
        s.isaretle("birincil", 299)
        self.assertEqual(s.sec(300)[0], "birincil")

    def test_hicbiri_taze_degilse_None(self):
        s = self.kur()
        s.isaretle("birincil", 100)
        aktif, sebep = s.sec(1000)
        self.assertIsNone(aktif)
        self.assertEqual(sebep, "no fresh source")

    def test_hic_gorulmemis_kaynak_secilmez(self):
        s = self.kur()
        self.assertIsNone(s.sec(100)[0])

    def test_gecis_YALNIZCA_degisimde_kaydedilir(self):
        # Her cagrida kayit atilsa gecmis saniyeler icinde dolar ve
        # gercek devretmeler gorunmez olurdu.
        s = self.kur()
        s.isaretle("birincil", 100)
        for t in (100, 101, 102, 103):
            s.sec(t)
        self.assertEqual(len(s.gecis_gecmisi), 1)
        self.assertEqual(s.gecis_gecmisi[0]["to"], "birincil")

    def test_gecis_gecmisi_nereden_nereye_tutar(self):
        s = self.kur()
        s.isaretle("birincil", 100)
        s.sec(100)
        s.isaretle("yedek", 195)
        s.sec(200)
        son = s.gecis_gecmisi[-1]
        self.assertEqual(son["from"], "birincil")
        self.assertEqual(son["to"], "yedek")


class BaslikEslemesi(unittest.TestCase):

    def test_esanlamlilar(self):
        e = fs.basliklari_esle(["Tarih", "HISSE", "kapanış"])
        self.assertEqual(set(e.values()), {"date", "ticker", "close"})

    def test_tanimsiz_baslik_atlanir(self):
        e = fs.basliklari_esle(["date", "ilgisiz_kolon"])
        self.assertNotIn("ilgisiz_kolon", e)

    def test_acik_eslem_tahmini_EZER(self):
        # Belirsiz basliklari olan dosyalar icin kacis yolu.
        e = fs.basliklari_esle(["price", "x"], ek={"x": "close"})
        self.assertEqual(e["x"], "close")

    def test_alt_tire_ve_bosluk_yok_sayilir(self):
        e = fs.basliklari_esle(["en_yuksek", " En Dusuk "])
        self.assertEqual(set(e.values()), {"high", "low"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
