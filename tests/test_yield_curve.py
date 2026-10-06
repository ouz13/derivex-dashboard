#!/usr/bin/env python3
"""Nelson-Siegel-Svensson egri uydurma ve DV01 testleri."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import yield_curve as YC  # noqa: E402
import bridge_stream as B  # noqa: E402


class TestNssFormulu(unittest.TestCase):

    P = dict(b0=0.28, b1=0.05, b2=-0.03, b3=0.02, l1=0.5, l2=3.0)

    def test_uzun_vade_b0_a_yakinsar(self):
        """Yakinsama O(1/t); sabit tolerans yerine farkin kucultugu sinanir."""
        onceki = None
        for t in (10.0, 50.0, 200.0, 1000.0):
            fark = abs(YC.nss_oran(t, **self.P) - self.P["b0"])
            if onceki is not None:
                self.assertLess(fark, onceki, f"t={t} icin fark buyumus")
            onceki = fark
        self.assertLess(onceki, 1e-4, "cok uzak vadede hala b0'a yakinsamadi")

    def test_sifir_vade_b0_arti_b1(self):
        self.assertAlmostEqual(YC.nss_oran(0, **self.P),
                               self.P["b0"] + self.P["b1"], places=10)

    def test_kucuk_vadede_limit_surekli(self):
        """t -> 0 limitinde kopukluk olmamali."""
        a = YC.nss_oran(1e-9, **self.P)
        b = YC.nss_oran(0, **self.P)
        self.assertAlmostEqual(a, b, places=6)

    def test_yuk_terimi_limitleri(self):
        self.assertAlmostEqual(YC._yuk_terimi(1e-12, 0.5), 1.0, places=9)
        self.assertLess(YC._yuk_terimi(50.0, 0.5), 0.02)

    def test_negatif_vade_none(self):
        self.assertIsNone(YC.nss_oran(-1, **self.P))


class TestFit(unittest.TestCase):

    def test_kendi_uretimini_geri_bulur(self):
        """Modelden uretilen noktalar neredeyse sifir hatayla uydurulmali."""
        gercek = dict(b0=0.30, b1=0.04, b2=-0.02, b3=0.015, l1=0.6, l2=4.5)
        vadeler = [0.08, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0]
        noktalar = [(t, YC.nss_oran(t, **gercek)) for t in vadeler]

        fit = YC.fit_nss(noktalar)
        self.assertIsNotNone(fit)
        self.assertLess(fit["rmse"], 0.002,
                        f"kendi uretimine uyduramadi, rmse={fit['rmse']:.5f}")
        for t in vadeler:
            self.assertAlmostEqual(
                YC.nss_oran(t, fit["b0"], fit["b1"], fit["b2"], fit["b3"],
                            fit["l1"], fit["l2"]),
                YC.nss_oran(t, **gercek), places=2)

    def test_duz_egri(self):
        noktalar = [(t, 0.30) for t in (0.1, 0.5, 1.0, 2.0, 5.0)]
        fit = YC.fit_nss(noktalar)
        self.assertIsNotNone(fit)
        self.assertLess(fit["rmse"], 1e-6)
        for t in (0.2, 1.5, 8.0):
            self.assertAlmostEqual(
                YC.nss_oran(t, fit["b0"], fit["b1"], fit["b2"], fit["b3"],
                            fit["l1"], fit["l2"]), 0.30, places=4)

    def test_uc_noktadan_azi_none(self):
        self.assertIsNone(YC.fit_nss([(0.5, 0.3), (1.0, 0.31)]))

    def test_az_noktada_NS_e_duser(self):
        """3-4 nokta ile NSS degil NS uydurulmali (b3 kapali)."""
        fit = YC.fit_nss([(26/365, 0.285), (55/365, 0.295), (88/365, 0.302)])
        self.assertIsNotNone(fit)
        self.assertEqual(fit["model"], "NS")
        self.assertEqual(fit["b3"], 0.0, "NS modunda dorduncu terim kapali olmali")
        self.assertTrue(fit["tam_belirlenmis"])

    def test_cok_noktada_NSS_kullanilir(self):
        fit = YC.fit_nss([(t, 0.28 + 0.01 * t) for t in
                          (0.1, 0.3, 0.6, 1.0, 2.0, 5.0)])
        self.assertEqual(fit["model"], "NSS")
        self.assertFalse(fit["tam_belirlenmis"])

    def test_tam_belirlenmis_rmse_yaniltici(self):
        """Nokta sayisi parametre sayisina esitse RMSE~0 cikar; bu uyum
        kalitesi degildir ve bayrakla isaretlenmelidir."""
        fit = YC.fit_nss([(0.1, 0.30), (0.5, 0.35), (1.0, 0.25)])
        self.assertLess(fit["rmse"], 1e-9)
        self.assertTrue(fit["tam_belirlenmis"],
                        "sifira yakin RMSE tam belirlenmislik bayragi tasimali")

    def test_gecersiz_noktalar_elenir(self):
        noktalar = [(0.1, 0.30), (0.5, 0.31), (None, 0.3), (1.0, None),
                    (-1.0, 0.3), (2.0, 0.33), (5.0, 0.34)]
        fit = YC.fit_nss(noktalar)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["nokta_sayisi"], 4)   # 3 gecersiz nokta elenir

    def test_gercekci_vadeli_egrisi(self):
        """Dashboard'in uretebilecegi tipte veriyle makul uyum."""
        noktalar = [(27/365, 0.285), (58/365, 0.295), (89/365, 0.302),
                    (180/365, 0.311), (365/365, 0.318)]
        fit = YC.fit_nss(noktalar)
        self.assertIsNotNone(fit)
        self.assertLess(fit["rmse"], 0.01, "gercekci egriye uyum zayif")
        # Ara vade, komsu gozlemler arasinda kalmali
        ara = YC.nss_oran(45/365, fit["b0"], fit["b1"], fit["b2"], fit["b3"],
                          fit["l1"], fit["l2"])
        self.assertTrue(0.27 < ara < 0.32, f"ara vade sacma: {ara}")

    def test_egri_noktalari_cikti_sekli(self):
        fit = YC.fit_nss([(0.1, 0.30), (0.5, 0.31), (1.0, 0.32), (2.0, 0.33)])
        ornek = YC.egri_noktalari(fit, [30, 60, 90])
        self.assertEqual([p["dtm"] for p in ornek], [30, 60, 90])
        self.assertTrue(all(p["rate"] is not None for p in ornek))
        self.assertEqual(YC.egri_noktalari(None, [30]), [])


class TestDv01(unittest.TestCase):
    """DV01: 1 baz puanlik faiz kaymasina karsi fiyat degisimi."""

    S, K, T, V = 281.5, 280.0, 0.25, 0.40

    def _bs(self, tip):
        return lambda r: B._bs_price(tip, self.S, self.K, r, self.T, self.V)

    def test_call_pozitif_put_negatif(self):
        self.assertGreater(YC.dv01(lambda r: self._bs("C")(r), 0.30), 0)
        self.assertLess(YC.dv01(lambda r: self._bs("P")(r), 0.30), 0)

    def test_rho_ile_tutarli(self):
        """DV01, rho'nun yuzde biri olmali (rho 1 puan, DV01 1 baz puan)."""
        for tip in ("C", "P"):
            d = YC.dv01(lambda r, t=tip: B._bs_price(t, self.S, self.K, r, self.T, self.V), 0.30)
            rho = B._bs_rho(tip, self.S, self.K, 0.30, self.T, self.V)
            self.assertAlmostEqual(d, rho / 100.0, places=9,
                                   msg=f"{tip}: DV01 ile rho tutarsiz")

    def test_binom_ile_de_calisir(self):
        """Sayisal tanim, Black-Scholes disindaki yontemde de gecerli."""
        d = YC.dv01(lambda r: B._binomial_price("C", self.S, self.K, r, self.T, self.V, 200), 0.30)
        self.assertIsNotNone(d)
        self.assertGreater(d, 0)

    def test_fiyatlanamayan_girdi_none(self):
        self.assertIsNone(YC.dv01(lambda r: B._bs_price("C", self.S, self.K, r, 0.0, self.V), 0.30))


if __name__ == "__main__":
    unittest.main(verbosity=2)
