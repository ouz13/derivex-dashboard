#!/usr/bin/env python3
"""
bridge_stream.py icindeki fiyatlama ve akis ayristirma mantigini test eder.

    python3 -m unittest discover tests -v
    python3 tests/test_bridge.py

Standart kutuphane disinda bagimlilik yoktur.
"""

import math
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bridge_stream as B  # noqa: E402


# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------

class TestBlackScholes(unittest.TestCase):
    """Bilinen referans degerlere ve teorik ozdeşliklere karsi dogrular."""

    def test_atm_call_bilinen_deger(self):
        # S=100, K=100, r=0, T=1, sigma=0.20 -> yaklasik 7.9656
        fiyat = B._bs_price("C", 100.0, 100.0, 0.0, 1.0, 0.20)
        self.assertAlmostEqual(fiyat, 7.9656, places=3)

    def test_put_call_paritesi(self):
        """C - P = S - K*e^(-rT) her parametre setinde saglanmali."""
        for S, K, r, T, v in [(100, 95, 0.30, 0.5, 0.35),
                              (281.5, 300, 0.30, 0.08, 0.42),
                              (42.0, 42.0, 0.15, 2.0, 0.25)]:
            c = B._bs_price("C", S, K, r, T, v)
            p = B._bs_price("P", S, K, r, T, v)
            self.assertAlmostEqual(c - p, S - K * math.exp(-r * T), places=6,
                                   msg=f"parite bozuldu: S={S} K={K}")

    def test_fiyat_icsel_degerin_altina_inmez(self):
        c = B._bs_price("C", 150.0, 100.0, 0.30, 0.25, 0.30)
        self.assertGreaterEqual(c, 150.0 - 100.0 * math.exp(-0.30 * 0.25) - 1e-9)

    def test_vol_arttikca_fiyat_artar(self):
        onceki = None
        for v in (0.10, 0.20, 0.40, 0.80):
            f = B._bs_price("C", 100.0, 100.0, 0.30, 0.5, v)
            if onceki is not None:
                self.assertGreater(f, onceki, "volatilite arttigi halde fiyat artmadi")
            onceki = f

    def test_gecersiz_girdiler_none_doner(self):
        self.assertIsNone(B._bs_price("C", 100, 100, 0.3, 0.0, 0.2))   # T=0
        self.assertIsNone(B._bs_price("C", 100, 100, 0.3, 1.0, 0.0))   # sigma=0
        self.assertIsNone(B._bs_price("C", -1, 100, 0.3, 1.0, 0.2))    # negatif spot
        self.assertIsNone(B._bs_price("X", 100, 100, 0.3, 1.0, 0.2))   # bilinmeyen tip


class TestDelta(unittest.TestCase):

    def test_delta_sinirlari(self):
        """Call delta (0,1), put delta (-1,0) araliginda olmali."""
        for K in (50.0, 100.0, 200.0):
            dc = B._bs_delta("C", 100.0, K, 0.30, 0.5, 0.35)
            dp = B._bs_delta("P", 100.0, K, 0.30, 0.5, 0.35)
            self.assertTrue(0.0 < dc < 1.0, f"call delta araligi disi: {dc}")
            self.assertTrue(-1.0 < dp < 0.0, f"put delta araligi disi: {dp}")

    def test_delta_paritesi(self):
        """delta_call - delta_put = 1 (temettusuz)."""
        dc = B._bs_delta("C", 120.0, 100.0, 0.30, 0.75, 0.40)
        dp = B._bs_delta("P", 120.0, 100.0, 0.30, 0.75, 0.40)
        self.assertAlmostEqual(dc - dp, 1.0, places=6)


class TestGreeks(unittest.TestCase):
    """
    Her Greek, fiyat fonksiyonunun sayisal turevine karsi dogrulanir.
    Formul hatasi bu sekilde yakalanir; isaret/aralik kontrolleri tek
    basina yetmez.
    """

    S, K, R, T, V = 281.5, 280.0, 0.30, 0.25, 0.40

    def test_gamma_delta_turevi(self):
        h = 0.01
        for tip in ("C", "P"):
            dust = B._bs_delta(tip, self.S - h, self.K, self.R, self.T, self.V)
            yuks = B._bs_delta(tip, self.S + h, self.K, self.R, self.T, self.V)
            sayisal = (yuks - dust) / (2 * h)
            analitik = B._bs_gamma(self.S, self.K, self.R, self.T, self.V)
            self.assertAlmostEqual(analitik, sayisal, places=6,
                                   msg=f"{tip}: gamma delta turevine uymuyor")

    def test_vega_fiyatin_vol_turevi(self):
        h = 1e-5
        for tip in ("C", "P"):
            dust = B._bs_price(tip, self.S, self.K, self.R, self.T, self.V - h)
            yuks = B._bs_price(tip, self.S, self.K, self.R, self.T, self.V + h)
            sayisal = (yuks - dust) / (2 * h) / 100.0   # vega %1 basina
            analitik = B._bs_vega(self.S, self.K, self.R, self.T, self.V)
            self.assertAlmostEqual(analitik, sayisal, places=6,
                                   msg=f"{tip}: vega fiyat turevine uymuyor")

    def test_theta_zaman_turevi(self):
        h = 1e-6
        for tip in ("C", "P"):
            # Zaman ilerledikce vade azalir: theta = -dFiyat/dT
            ileri = B._bs_price(tip, self.S, self.K, self.R, self.T - h, self.V)
            geri = B._bs_price(tip, self.S, self.K, self.R, self.T + h, self.V)
            sayisal = (ileri - geri) / (2 * h) / 365.0   # gunluk
            analitik = B._bs_theta(tip, self.S, self.K, self.R, self.T, self.V)
            self.assertAlmostEqual(analitik, sayisal, places=5,
                                   msg=f"{tip}: theta zaman turevine uymuyor")

    def test_rho_faiz_turevi(self):
        h = 1e-6
        for tip in ("C", "P"):
            dust = B._bs_price(tip, self.S, self.K, self.R - h, self.T, self.V)
            yuks = B._bs_price(tip, self.S, self.K, self.R + h, self.T, self.V)
            sayisal = (yuks - dust) / (2 * h) / 100.0
            analitik = B._bs_rho(tip, self.S, self.K, self.R, self.T, self.V)
            self.assertAlmostEqual(analitik, sayisal, places=5,
                                   msg=f"{tip}: rho faiz turevine uymuyor")

    def test_isaretler(self):
        self.assertGreater(B._bs_gamma(self.S, self.K, self.R, self.T, self.V), 0)
        self.assertGreater(B._bs_vega(self.S, self.K, self.R, self.T, self.V), 0)
        self.assertGreater(B._bs_rho("C", self.S, self.K, self.R, self.T, self.V), 0)
        self.assertLess(B._bs_rho("P", self.S, self.K, self.R, self.T, self.V), 0)
        self.assertLess(B._bs_theta("C", self.S, self.K, self.R, self.T, self.V), 0)

    def test_gamma_ve_vega_call_put_ayni(self):
        """Put-call paritesi geregi gamma ve vega iki tipte de esittir."""
        g = B._bs_gamma(self.S, self.K, self.R, self.T, self.V)
        v = B._bs_vega(self.S, self.K, self.R, self.T, self.V)
        self.assertIsNotNone(g)
        self.assertIsNotNone(v)

    def test_gamma_para_basinda_en_yuksek(self):
        ortadaki = B._bs_gamma(100.0, 100.0, 0.30, 0.25, 0.40)
        for K in (60.0, 80.0, 125.0, 160.0):
            self.assertGreater(ortadaki, B._bs_gamma(100.0, K, 0.30, 0.25, 0.40),
                               f"K={K} icin gamma ATM'den buyuk cikti")

    def test_gecersiz_girdiler_none(self):
        for f in (lambda: B._bs_gamma(100, 100, 0.3, 0.0, 0.2),
                  lambda: B._bs_vega(100, 100, 0.3, 1.0, 0.0),
                  lambda: B._bs_theta("C", -1, 100, 0.3, 1.0, 0.2),
                  lambda: B._bs_rho("X", 100, 100, 0.3, 1.0, 0.2)):
            self.assertIsNone(f())


class TestImpliedVol(unittest.TestCase):

    def test_tur_gidis(self):
        """Fiyattan geri hesaplanan volatilite baslangictakini vermeli."""
        for v in (0.15, 0.30, 0.55, 0.85):
            fiyat = B._bs_price("C", 281.5, 280.0, 0.30, 0.09, v)
            geri = B._implied_vol_bisect("C", fiyat, 281.5, 280.0, 0.30, 0.09)
            self.assertIsNotNone(geri, f"IV hesaplanamadi (v={v})")
            self.assertAlmostEqual(geri, v, places=4)

    def test_put_tur_gidis(self):
        fiyat = B._bs_price("P", 68.9, 70.0, 0.30, 0.08, 0.47)
        geri = B._implied_vol_bisect("P", fiyat, 68.9, 70.0, 0.30, 0.08)
        self.assertAlmostEqual(geri, 0.47, places=4)

    def test_sacma_fiyat_none_doner(self):
        # Spot'tan pahali call arbitraja acik; IV bulunamamali
        self.assertIsNone(B._implied_vol_bisect("C", 500.0, 100.0, 100.0, 0.30, 0.5))


# ---------------------------------------------------------------------------
# Vadeli getiri
# ---------------------------------------------------------------------------

class TestBinomial(unittest.TestCase):
    """Binom agaci ve iki yontem arasindaki tutarlilik."""

    S, K, R, T, V = 100.0, 100.0, 0.30, 0.5, 0.40

    def test_avrupa_black_scholes_e_yakinsar(self):
        """Adim sayisi arttikca binom fiyati BS'ye yaklasmali."""
        bs = B._bs_price("C", self.S, self.K, self.R, self.T, self.V)
        onceki_hata = None
        for adim in (10, 50, 200, 800):
            b = B._binomial_price("C", self.S, self.K, self.R, self.T, self.V, adim)
            hata = abs(b - bs)
            if onceki_hata is not None:
                self.assertLess(hata, onceki_hata * 1.5,
                                f"adim={adim} hatayi azaltmadi")
            onceki_hata = hata
        self.assertLess(onceki_hata, 0.02, "800 adimda BS'ye yeterince yakinsamadi")

    def test_avrupa_put_da_yakinsar(self):
        bs = B._bs_price("P", 95.0, 100.0, 0.30, 0.75, 0.35)
        b = B._binomial_price("P", 95.0, 100.0, 0.30, 0.75, 0.35, 800)
        self.assertAlmostEqual(b, bs, delta=0.02)

    def test_temettusuz_amerikan_call_avrupa_ile_ayni(self):
        """Temettu yoksa Amerikan call'u erken kullanmak optimal degildir."""
        avr = B._binomial_price("C", self.S, self.K, self.R, self.T, self.V, 300, american=False)
        ame = B._binomial_price("C", self.S, self.K, self.R, self.T, self.V, 300, american=True)
        self.assertAlmostEqual(ame, avr, places=8)

    def test_amerikan_put_avrupadan_ucuz_olamaz(self):
        for K in (80.0, 100.0, 130.0):
            avr = B._binomial_price("P", 100.0, K, 0.30, 1.0, 0.35, 300, american=False)
            ame = B._binomial_price("P", 100.0, K, 0.30, 1.0, 0.35, 300, american=True)
            self.assertGreaterEqual(ame, avr - 1e-9,
                                    f"K={K}: Amerikan put Avrupadan ucuz cikti")

    def test_derin_icsel_amerikan_put_erken_kullanim_primi_tasir(self):
        """Faiz yuksekken derin ITM put'u erken kullanmak degerlidir."""
        avr = B._binomial_price("P", 50.0, 100.0, 0.30, 1.0, 0.25, 300, american=False)
        ame = B._binomial_price("P", 50.0, 100.0, 0.30, 1.0, 0.25, 300, american=True)
        self.assertGreater(ame - avr, 0.5, "erken kullanim primi beklenenden kucuk")

    def test_amerikan_put_icsel_degerin_altina_inmez(self):
        ame = B._binomial_price("P", 50.0, 100.0, 0.30, 1.0, 0.25, 300, american=True)
        self.assertGreaterEqual(ame, 100.0 - 50.0 - 1e-9)

    def test_karsilastirma_ciktisi(self):
        k = B._fiyat_karsilastir("C", self.S, self.K, self.R, self.T, self.V, 400)
        self.assertIsNotNone(k)
        for alan in ("black_scholes", "binom_avrupa", "binom_amerikan",
                     "fark", "fark_yuzde", "erken_kullanim_primi"):
            self.assertIn(alan, k)
        self.assertLess(abs(k["fark_yuzde"]), 0.5,
                        "iki yontem arasindaki sapma %0.5'i asmamali")

    def test_gecersiz_girdiler_none(self):
        self.assertIsNone(B._binomial_price("C", 100, 100, 0.3, 0.0, 0.2))
        self.assertIsNone(B._binomial_price("C", 100, 100, 0.3, 1.0, 0.0))
        self.assertIsNone(B._binomial_price("X", 100, 100, 0.3, 1.0, 0.2))
        self.assertIsNone(B._binomial_price("C", 100, 100, 0.3, 1.0, 0.2, steps=0))


class TestYield(unittest.TestCase):

    def test_bilinen_deger(self):
        """100 -> 110, 365 gun = %10 yillik."""
        self.assertAlmostEqual(B.calc_annualized_yield(100.0, 110.0, 365), 0.10, places=9)

    def test_yarim_yil_iki_katina_ciker(self):
        y365 = B.calc_annualized_yield(100.0, 110.0, 365)
        y182 = B.calc_annualized_yield(100.0, 110.0, 182.5)
        self.assertAlmostEqual(y182, y365 * 2, places=9)

    def test_gecersiz_girdiler(self):
        self.assertIsNone(B.calc_annualized_yield(None, 110.0, 30))
        self.assertIsNone(B.calc_annualized_yield(100.0, None, 30))
        self.assertIsNone(B.calc_annualized_yield(0.0, 110.0, 30))
        self.assertIsNone(B.calc_annualized_yield(100.0, 110.0, 0))


# ---------------------------------------------------------------------------
# Sembol ayristirma
# ---------------------------------------------------------------------------

class TestOptionSymbol(unittest.TestCase):

    def test_gercek_semboller(self):
        p = B._parse_option_symbol("O_KCHOLE1026C210.00")
        self.assertIsNotNone(p)
        self.assertEqual(p["ticker"], "KCHOL")
        self.assertEqual(p["expiry"], "1026")
        self.assertEqual(p["option_type"], "C")
        self.assertAlmostEqual(p["strike"], 210.00)

        p = B._parse_option_symbol("O_THYAOE0926P295.50")
        self.assertEqual(p["option_type"], "P")
        self.assertAlmostEqual(p["strike"], 295.50)

    def test_hedef_disi_ticker_elenir(self):
        self.assertIsNone(B._parse_option_symbol("O_YOKBOYLEE1026C10.00"))

    def test_bozuk_semboller(self):
        for s in ("", "THYAO", "F_THYAO1026", "O_THYAO1026C295.00", "O_"):
            self.assertIsNone(B._parse_option_symbol(s), f"yanlis kabul: {s}")


# ---------------------------------------------------------------------------
# Akis ayristirma — 19 numarali hatanin akrabasi burada yasiyordu
# ---------------------------------------------------------------------------

class TestFrameParsing(unittest.TestCase):

    def test_parse_frames_ayirir_ve_artani_saklar(self):
        tampon = b"YU;1=THYAO;6=281.0|DU;1=F_THYAO1026;3=290.0|YARIM;1=AK"
        frames, kalan = B.parse_frames(tampon)
        self.assertEqual(len(frames), 2)
        self.assertEqual(kalan, b"YARIM;1=AK")

    def test_parse_frames_ayirici_ile_biterse_artan_bos(self):
        frames, kalan = B.parse_frames(b"A;1=X|B;1=Y|")
        self.assertEqual(frames, ["A;1=X", "B;1=Y"])
        self.assertEqual(kalan, b"")

    def test_parse_frames_ayirici_yoksa_hepsi_artan(self):
        frames, kalan = B.parse_frames(b"YARIM;1=AK")
        self.assertEqual(frames, [])
        self.assertEqual(kalan, b"YARIM;1=AK")

    def test_parse_frames_bos_cerceveleri_atlar(self):
        frames, _ = B.parse_frames(b"A;1=X||  |B;1=Y|")
        self.assertEqual(frames, ["A;1=X", "B;1=Y"])

    def test_parse_frames_dogrusal_olcekler(self):
        """
        Onceki surum her cerceve icin tamponun kalanini kopyaliyordu,
        yani karesel idi. Normalde gorunmuyordu cunku tampon 4 KB'lik
        parcalarla buyur; ama akis hizlanip dongu geri kaldiginda tampon
        buyur ve maliyet TAM DA zaten geride kalmisken patlar.

        Girdi 4 katina cikarken sure 4 kat civari artmali. Karesel bir
        surumde ~16 kat olurdu; esik ikisinin arasinda ve olcum
        gurultusune yer birakacak kadar genis.
        """
        import time

        def sure(adet):
            tampon = b"|".join(b"YU;1=THYAO;6=281.0;9=281.5" for _ in range(adet)) + b"|"
            t0 = time.perf_counter()
            B.parse_frames(tampon)
            return time.perf_counter() - t0

        kucuk = min(sure(5000) for _ in range(3))
        buyuk = min(sure(20000) for _ in range(3))
        # Cok kisa surelerde bolme gurultulu olur; taban sure uygula
        if kucuk < 1e-4:
            kucuk = 1e-4
        self.assertLess(buyuk / kucuk, 8.0,
                        f"parse_frames dogrusal olceklenmemis "
                        f"(4x girdi -> {buyuk/kucuk:.1f}x sure)")

    def test_parse_frame_tip_ve_alanlar(self):
        tip, alanlar = B.parse_frame("YU;1=THYAO;6=281.00;9=281.50")
        self.assertEqual(tip, "YU")
        self.assertEqual(alanlar["1"], "THYAO")
        self.assertEqual(alanlar["9"], "281.50")

    def test_esittir_icermeyen_parcalar_atlanir(self):
        tip, alanlar = B.parse_frame("TU;1=AKBNK;BOZUK;3=68.9")
        self.assertEqual(alanlar, {"1": "AKBNK", "3": "68.9"})


class TestExtractQuote(unittest.TestCase):
    """
    Uc mesaj tipi de kotasyon tasir. Yalnizca DU islenirse bazi semboller
    hic fiyatlanmiyordu; bu testler o gerilemeyi yakalar.
    """

    def test_WU_108_109(self):
        bid, ask, bs, as_ = B.extract_quote("WU", {"1": "TUPRS", "108": "392.71",
                                                   "109": "411.92", "110": "100", "111": "200"})
        self.assertAlmostEqual(bid, 392.71)
        self.assertAlmostEqual(ask, 411.92)
        self.assertAlmostEqual(bs, 100)
        self.assertAlmostEqual(as_, 200)

    def test_YU_6_9(self):
        bid, ask, _, _ = B.extract_quote("YU", {"1": "SISE", "6": "37.93", "9": "39.62"})
        self.assertAlmostEqual(bid, 37.93)
        self.assertAlmostEqual(ask, 39.62)

    def test_DU_yalnizca_en_iyi_seviye(self):
        en_iyi = {"1": "F_TOASO0926", "3": "282.60", "4": "24", "101": "B", "100": "0"}
        derin = {"1": "F_TOASO0926", "3": "282.50", "4": "2", "101": "B", "100": "7"}
        bid, ask, _, _ = B.extract_quote("DU", en_iyi)
        self.assertAlmostEqual(bid, 282.60)
        self.assertIsNone(ask)
        self.assertEqual(B.extract_quote("DU", derin), (None, None, None, None))

    def test_DU_satis_tarafi(self):
        bid, ask, _, _ = B.extract_quote("DU", {"3": "100.5", "101": "A", "100": "0"})
        self.assertIsNone(bid)
        self.assertAlmostEqual(ask, 100.5)

    def test_kotasyon_tasimayan_tipler(self):
        for tip in ("TU", "VG", "OG", "DT"):
            self.assertEqual(B.extract_quote(tip, {"1": "X", "3": "1"}),
                             (None, None, None, None))

    def test_uc_tip_de_kotasyon_tipi_listesinde(self):
        self.assertEqual(set(B.QUOTE_MSG_TYPES), {"YU", "WU", "DU"})


class TestApplyQuote(unittest.TestCase):
    """Mesajlar kismi guncellemedir: eksik taraf korunmali."""

    def test_kismi_guncelleme_diger_tarafi_korur(self):
        defter = {}
        B.apply_quote(defter, "THYAO", 281.0, None)
        B.apply_quote(defter, "THYAO", None, 281.5)
        self.assertAlmostEqual(defter["THYAO"]["B"], 281.0)
        self.assertAlmostEqual(defter["THYAO"]["A"], 281.5)

    def test_degisiklik_bayragi(self):
        defter = {}
        self.assertTrue(B.apply_quote(defter, "AKBNK", 68.9, 69.1))
        self.assertFalse(B.apply_quote(defter, "AKBNK", 68.9, 69.1),
                         "ayni fiyat degisiklik saymamali")
        self.assertTrue(B.apply_quote(defter, "AKBNK", 69.0, None))

    def test_mid_hesabi(self):
        defter = {}
        B.apply_quote(defter, "X", 100.0, 102.0)
        self.assertAlmostEqual(B._mid_from_bid_ask(defter["X"]), 101.0)

    def test_tek_tarafli_kotasyon_mid_vermez(self):
        defter = {}
        B.apply_quote(defter, "ASTOR", None, 238.97)
        self.assertIsNone(B._mid_from_bid_ask(defter["ASTOR"]),
                          "tek tarafli kotasyondan orta fiyat uretilmemeli")


# ---------------------------------------------------------------------------
# Vade takvimi
# ---------------------------------------------------------------------------

class TestMaturities(unittest.TestCase):

    def test_vadeler_artan_dtm(self):
        vadeler = B.get_active_maturities()
        self.assertGreaterEqual(len(vadeler), 2)
        dtm = [m["dtm"] for m in vadeler]
        self.assertEqual(dtm, sorted(dtm), "vadeler DTM'e gore sirali degil")

    def test_vade_kodu_bicimi(self):
        for m in B.get_active_maturities():
            self.assertRegex(m["code"], r"^\d{4}$")

    def test_sembol_haritasi(self):
        _, harita = B.build_futures_symbol_map()
        self.assertTrue(all(s.startswith("F_") for s in harita))
        ornek = next(iter(harita.values()))
        self.assertIn("underlying", ornek)
        self.assertIn("dtm", ornek)
        self.assertEqual(ornek["asset_class"], "equity")


class TestHisseDisiVarliklar(unittest.TestCase):
    """
    Endeks / doviz / emtia vadelileri.

    Bunlar akista zaten geliyordu ama TARGET_TICKERS hisse ile sinirli
    oldugu icin atiliyordu.
    """

    TARIH = datetime(2026, 9, 15)

    def test_aday_kumesi_alti_ay_kapsar(self):
        """
        Vade dongusu varliga gore degisiyor (altinda cift ay, hissede
        ardisik ay). Dongu tahmin etmek yerine genis aday uretiliyor;
        kopru zaten filtreledigi icin eslesmeyenin maliyeti yok.
        """
        vadeler, harita = B.build_other_symbol_map(self.TARIH)
        self.assertEqual(len(vadeler), B.OTHER_MATURITY_MONTHS)
        kodlar = [v["code"] for v in vadeler]
        # Eylul 2026'dan itibaren alti ay
        self.assertEqual(kodlar, ["0926", "1026", "1126", "1226", "0127", "0227"])
        self.assertEqual(len(harita), len(B.OTHER_UNDERLYINGS) * B.OTHER_MATURITY_MONTHS)

    def test_gercek_kayittaki_semboller_kapsaniyor(self):
        """
        Ornek kayitta gorulen hisse disi vadeli sembollerin TAMAMI aday
        kumesine dusmeli; aksi halde veri yine sessizce atilirdi.
        """
        _, harita = B.build_other_symbol_map(self.TARIH)
        kayittakiler = [
            "F_XAUUSD0227", "F_XAUUSD1026", "F_XAUUSD1226",
            "F_XAGUSD0227", "F_XAGUSD1026", "F_XAGUSD1226",
            "F_XAUTRYM0227", "F_XAUTRYM1026", "F_XAUTRYM1226",
            "F_XU0300227", "F_XU0301026", "F_XU0301226",
            "F_X10XB0227", "F_XPTUSD1026", "F_XPTUSD1226",
            "F_XPDUSD1026", "F_XPDUSD1226",
            "F_CNHTRY1026", "F_CNHTRY1226",
            "F_USDTRY0926", "F_USDTRY1026",
        ]
        for s in kayittakiler:
            self.assertIn(s, harita, f"{s} aday kumesinde yok, veri atilir")

    def test_varlik_sinifi_etiketleniyor(self):
        _, harita = B.build_other_symbol_map(self.TARIH)
        self.assertEqual(harita["F_XAUUSD1026"]["asset_class"], "commodity")
        self.assertEqual(harita["F_XU0301026"]["asset_class"], "index")
        self.assertEqual(harita["F_USDTRY1026"]["asset_class"], "fx")

    def test_hisse_haritasiyla_cakismaz(self):
        """Iki harita ayri tutuluyor; ayni sembol iki yerde islenmemeli."""
        _, hisse = B.build_futures_symbol_map(self.TARIH)
        _, diger = B.build_other_symbol_map(self.TARIH)
        self.assertEqual(set(hisse) & set(diger), set())

    def test_anlik_goruntu_dayanaga_gore_gruplar(self):
        _, harita = B.build_other_symbol_map(self.TARIH)
        mid = {"F_XAUUSD1026": 4015.0, "F_XAUUSD1226": 4080.0, "F_USDTRY1026": 41.9}
        bid = {"F_XAUUSD1026": 4010.0, "F_XAUUSD1226": 4075.0, "F_USDTRY1026": 41.8}
        ask = {"F_XAUUSD1026": 4020.0, "F_XAUUSD1226": 4085.0, "F_USDTRY1026": 42.0}

        anlik = B.build_other_snapshot(harita, mid, bid, ask)
        self.assertEqual(set(anlik), {"XAUUSD", "USDTRY"})
        self.assertEqual(anlik["XAUUSD"]["asset_class"], "commodity")
        self.assertEqual(len(anlik["XAUUSD"]["maturities"]), 2)

        # Vadeler DTM'e gore sirali olmali
        dtm = [m["dtm"] for m in anlik["XAUUSD"]["maturities"]]
        self.assertEqual(dtm, sorted(dtm))

    def test_kotasyonu_olmayan_sembol_girmez(self):
        """Fiyati gelmemis aday sembol tabloda bos satir uretmemeli."""
        _, harita = B.build_other_symbol_map(self.TARIH)
        anlik = B.build_other_snapshot(harita, {}, {}, {})
        self.assertEqual(anlik, {})

    def test_ima_edilen_getiri_URETILMEZ(self):
        """
        Bu dayanaklarin spot kotasyonu akista yok; getiri fut/spot'a
        dayandigi icin hesaplanamaz. Uydurulmus bir getiri alanini
        sizdirmadigimiz burada sabitleniyor.
        """
        _, harita = B.build_other_symbol_map(self.TARIH)
        anlik = B.build_other_snapshot(
            harita, {"F_XAUUSD1026": 4015.0}, {"F_XAUUSD1026": 4010.0},
            {"F_XAUUSD1026": 4020.0})
        vade = anlik["XAUUSD"]["maturities"][0]
        self.assertEqual(set(vade), {"code", "label", "dtm", "bid", "ask", "mid"})
        for yasak in ("rate", "yield", "adj_rate", "spot_mid"):
            self.assertNotIn(yasak, vade)


if __name__ == "__main__":
    unittest.main(verbosity=2)
