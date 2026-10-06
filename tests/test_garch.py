#!/usr/bin/env python3
"""
Gerceklesmis volatilite ve GARCH(1,1) testleri.

En onemlisi parametre geri kazanimi: bilinen parametrelerle uretilmis bir
seriye uydurma yapilip ayni parametrelerin geri bulunup bulunmadigi
sinaniyor. Bu olmadan "GARCH uyduruyoruz" demek, uydurucunun sacma
parametre donduruyor olmasini dislamaz.

Toleranslar olculerek konuldu (n=3000'de alpha/beta ~0.01 hassasiyetle
geri geliyor); yuvarlak sayilar tahmin edilerek degil.
"""

import math
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import garch as G  # noqa: E402
import store as S  # noqa: E402


def garch_serisi_uret(n, alpha, beta, s2_bar=0.0004, seed=7):
    """
    Bilinen parametrelerle GARCH(1,1) getiri serisi uretir.

    Ilk 500 gozlem atilir: ozyineleme kosulsuz varyanstan basladigi icin
    baslangic etkisi tasiniyor.
    """
    omega = s2_bar * (1.0 - alpha - beta)
    r = random.Random(seed)
    s2 = s2_bar
    out = []
    for _ in range(n + 500):
        e = r.gauss(0, 1) * math.sqrt(s2)
        out.append(e)
        s2 = omega + alpha * e * e + beta * s2
    return out[500:]


class TestLogGetiriler(unittest.TestCase):

    def test_n_fiyattan_n_eksi_bir_getiri(self):
        k = [("2026-01-0%d" % i, 100.0 + i) for i in range(1, 6)]
        self.assertEqual(len(G.log_getiriler(k)), 4)

    def test_deger_dogru(self):
        g = G.log_getiriler([("d1", 100.0), ("d2", 110.0)])
        self.assertAlmostEqual(g[0], math.log(1.10), places=12)

    def test_pozitif_olmayan_fiyat_atlanir(self):
        """Sifir ya da negatif fiyatta log tanimsiz; satir atlanmali."""
        k = [("d1", 100.0), ("d2", 0.0), ("d3", -5.0), ("d4", 110.0)]
        g = G.log_getiriler(k)
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0], math.log(1.10), places=12)

    def test_sayi_olmayan_atlanir(self):
        k = [("d1", 100.0), ("d2", None), ("d3", "abc"), ("d4", 110.0)]
        self.assertEqual(len(G.log_getiriler(k)), 1)

    def test_bos_girdi(self):
        self.assertEqual(G.log_getiriler([]), [])
        self.assertEqual(G.log_getiriler([("d1", 100.0)]), [])


class TestGerceklesmisVol(unittest.TestCase):

    def test_elle_hesapla_ayni(self):
        g = [0.01, -0.02, 0.015, -0.005, 0.008]
        n = len(g)
        ort = sum(g) / n
        beklenen = math.sqrt(sum((x - ort) ** 2 for x in g) / (n - 1) * 252) * 100
        self.assertAlmostEqual(G.gerceklesmis_vol(g), beklenen, places=10)

    def test_sabit_getiri_sifir_vol(self):
        self.assertAlmostEqual(G.gerceklesmis_vol([0.01] * 50), 0.0, places=10)

    def test_iki_gozlemden_azi_none(self):
        self.assertIsNone(G.gerceklesmis_vol([]))
        self.assertIsNone(G.gerceklesmis_vol([0.01]))

    def test_pencere_son_gozlemleri_alir(self):
        """Pencere serinin SONUNU almali: volatilite guncel olmali."""
        sakin = [0.001] * 100
        oynak = [0.05, -0.05] * 10
        v = G.gerceklesmis_vol(sakin + oynak, 20)
        self.assertGreater(v, G.gerceklesmis_vol(sakin, 20),
                           "son pencere oynak donemi yansitmali")

    def test_yillandirma_252(self):
        """Gunluk sapma biliniyorsa yillik deger sqrt(252) kati olmali."""
        g = [0.01, -0.01] * 100
        gunluk = G.gerceklesmis_vol(g) / 100 / math.sqrt(252)
        self.assertAlmostEqual(gunluk, 0.01, places=4)


class TestEwma(unittest.TestCase):

    def test_son_gozlemlere_duyarli(self):
        """
        EWMA'nin varlik sebebi bu: rejim degisiminde esit agirlikli
        gerceklesmis volatiliteden daha hizli tepki vermeli.
        """
        seri = [0.002] * 200 + [0.04, -0.04] * 15
        e = G.ewma_vol(seri)
        rv = G.gerceklesmis_vol(seri)
        self.assertGreater(e, rv, "EWMA son oynak donemi daha agir tartmali")

    def test_sabit_oynaklikta_rv_ye_yakin(self):
        r = random.Random(3)
        seri = [r.gauss(0, 0.012) for _ in range(800)]
        e, rv = G.ewma_vol(seri), G.gerceklesmis_vol(seri)
        self.assertLess(abs(e - rv) / rv, 0.35,
                        "sabit rejimde iki olcu birbirine yakin olmali")

    def test_yetersiz_veri_none(self):
        self.assertIsNone(G.ewma_vol([]))
        self.assertIsNone(G.ewma_vol([0.01]))


class TestFitGarch(unittest.TestCase):

    def test_esigin_altinda_none(self):
        """
        Yetersiz veride uydurma YAPILMAMALI. Makul gorunen bir sayi
        dondurmek, kullaniciya duragan dosyayi canli sanmaktan daha
        buyuk bir yanlis bilgi verir.
        """
        self.assertIsNone(G.fit_garch11([0.01] * 10, min_getiri=60))
        self.assertIsNone(G.fit_garch11(garch_serisi_uret(59, 0.08, 0.90),
                                        min_getiri=60))
        self.assertIsNotNone(G.fit_garch11(garch_serisi_uret(60, 0.08, 0.90),
                                           min_getiri=60))

    def test_parametreleri_geri_bulur(self):
        """
        Asil test: bilinen parametrelerle uretilmis seriden ayni
        parametreler geri gelmeli. Toleranslar n=3000 icin olculdu.
        """
        for alpha, beta in ((0.08, 0.90), (0.05, 0.93), (0.15, 0.80)):
            seri = garch_serisi_uret(3000, alpha, beta)
            f = G.fit_garch11(seri)
            self.assertIsNotNone(f)
            self.assertAlmostEqual(f["alpha"], alpha, delta=0.03,
                                   msg=f"alpha geri gelmedi (gercek {alpha})")
            self.assertAlmostEqual(f["beta"], beta, delta=0.03,
                                   msg=f"beta geri gelmedi (gercek {beta})")
            self.assertAlmostEqual(f["persistence"], alpha + beta, delta=0.02,
                                   msg="kaliciligi geri bulamadi")

    def test_kosulsuz_volatiliteyi_geri_bulur(self):
        s2_bar = 0.0004                        # ~%31.7 yillik
        beklenen = math.sqrt(s2_bar * 252) * 100
        f = G.fit_garch11(garch_serisi_uret(3000, 0.08, 0.90, s2_bar))
        self.assertAlmostEqual(f["uncond_vol_pct"], beklenen, delta=beklenen * 0.15)

    def test_her_zaman_duragan(self):
        """alpha+beta>=1 duragan olmayan modeldir; uydurucu oraya gitmemeli."""
        for seed in range(1, 6):
            f = G.fit_garch11(garch_serisi_uret(400, 0.10, 0.88, seed=seed))
            self.assertIsNotNone(f)
            self.assertLess(f["persistence"], 1.0, "duragan olmayan uyum dondu")
            self.assertGreater(f["alpha"], 0.0)
            self.assertGreater(f["beta"], 0.0)

    def test_omega_varyans_hedeflemesiyle_tutarli(self):
        f = G.fit_garch11(garch_serisi_uret(1000, 0.08, 0.90))
        self.assertAlmostEqual(
            f["omega"], f["sigma2_bar"] * (1 - f["alpha"] - f["beta"]), places=14)

    def test_uyum_en_iyi_log_olabilirligi_bulur(self):
        """Dondurulen parametreler, komsu noktalardan daha iyi olmali."""
        seri = garch_serisi_uret(1200, 0.08, 0.90)
        f = G.fit_garch11(seri)
        for da, db in ((0.05, 0.0), (-0.03, 0.0), (0.0, 0.05), (0.0, -0.05)):
            a, b = f["alpha"] + da, f["beta"] + db
            if a <= 0 or b <= 0 or a + b >= 0.9995:
                continue
            ll = G._log_olabilirlik(seri, a, b, f["sigma2_bar"])
            if ll is not None:
                self.assertGreaterEqual(f["loglik"], ll,
                                        f"a+{da}, b+{db} daha iyi cikti")

    def test_duragan_olmayan_bolge_none(self):
        self.assertIsNone(G._log_olabilirlik([0.01] * 50, 0.5, 0.5, 0.0004))

    def test_sifir_varyansli_seri_none(self):
        """Hic hareket etmemis fiyat serisinde GARCH tanimsiz."""
        self.assertIsNone(G.fit_garch11([0.0] * 200))


class TestTahmin(unittest.TestCase):

    def setUp(self):
        self.seri = garch_serisi_uret(2000, 0.08, 0.90)
        self.fit = G.fit_garch11(self.seri)

    def test_uzun_ufukta_kosulsuza_yakinsar(self):
        """
        Ufuk buyudukce tahmin kosulsuz volatiliteye gitmeli: ortalamaya
        donus modelin tanimi geregi.
        """
        kisa = G.garch_tahmin(self.fit, self.seri, 1)
        uzun = G.garch_tahmin(self.fit, self.seri, 2000)
        kosulsuz = self.fit["uncond_vol_pct"]
        self.assertLess(abs(uzun - kosulsuz), abs(kisa - kosulsuz) + 1e-9,
                        "uzun ufuk kosulsuza daha yakin olmali")
        self.assertAlmostEqual(uzun, kosulsuz, delta=kosulsuz * 0.08)

    def test_yuksek_oynaklik_sonrasi_ufukla_duser(self):
        """Son gozlemler oynaksa kisa ufuk tahmini uzun ufuktan yuksek olmali."""
        sokli = self.seri + [0.08, -0.08, 0.09]
        f = G.fit_garch11(sokli)
        t1 = G.garch_tahmin(f, sokli, 1)
        t180 = G.garch_tahmin(f, sokli, 180)
        self.assertGreater(t1, t180,
                           "sok sonrasi kisa vadeli tahmin daha yuksek olmali")

    def test_tum_ufuklar_pozitif(self):
        for h in (1, 15, 30, 60, 90, 180):
            v = G.garch_tahmin(self.fit, self.seri, h)
            self.assertIsNotNone(v)
            self.assertGreater(v, 0.0)

    def test_gecersiz_girdi_none(self):
        self.assertIsNone(G.garch_tahmin(None, self.seri, 30))
        self.assertIsNone(G.garch_tahmin(self.fit, [], 30))
        self.assertIsNone(G.garch_tahmin(self.fit, self.seri, 0))


def egarch_serisi_uret(n, alpha, gamma, beta, s2_bar=0.0004, seed=5):
    """Bilinen EGARCH parametreleriyle getiri serisi uretir."""
    r = random.Random(seed)
    log_s2_bar = math.log(s2_bar)
    omega = (1.0 - beta) * log_s2_bar
    log_s2 = log_s2_bar
    out = []
    for _ in range(n + 600):
        s2 = math.exp(log_s2)
        z = r.gauss(0, 1)
        out.append(z * math.sqrt(s2))
        log_s2 = omega + alpha * (abs(z) - G._E_ABS_Z) + gamma * z + beta * log_s2
    return out[600:]


class TestEgarch(unittest.TestCase):
    """
    EGARCH(1,1).

    Modelin varlik sebebi gamma terimi: getirinin ISARETINI tasiyor ve
    boylece dususlerin volatiliteyi yukselislerden daha cok artirmasini
    yakalayabiliyor. GARCH bunu yapisal olarak yapamaz (orada yalnizca
    r^2 var, isaret kareyle kayboluyor). Testlerin agirligi orada.
    """

    def test_esigin_altinda_none(self):
        self.assertIsNone(G.fit_egarch11([0.01] * 10, min_getiri=60))
        self.assertIsNotNone(G.fit_egarch11(egarch_serisi_uret(60, 0.15, -0.08, 0.95),
                                            min_getiri=60))

    def test_parametreleri_geri_bulur(self):
        """
        Toleranslar olculerek konuldu. gamma ve beta ~0.02 hassasiyetle
        geri geliyor; ALPHA ZAYIF TANIMLI — olabilirlik yuzeyi o yonde
        cok duz (bkz. test_alpha_zayif_tanimli), bu yuzden toleransi
        bilerek genis.
        """
        for alpha, gamma, beta in ((0.15, -0.08, 0.95), (0.20, -0.12, 0.92)):
            seri = egarch_serisi_uret(2000, alpha, gamma, beta)
            f = G.fit_egarch11(seri)
            self.assertIsNotNone(f)
            self.assertAlmostEqual(f["gamma"], gamma, delta=0.02,
                                   msg=f"gamma geri gelmedi (gercek {gamma})")
            self.assertAlmostEqual(f["beta"], beta, delta=0.02,
                                   msg=f"beta geri gelmedi (gercek {beta})")
            self.assertAlmostEqual(f["alpha"], alpha, delta=0.07,
                                   msg=f"alpha geri gelmedi (gercek {alpha})")

    def test_alpha_zayif_tanimli(self):
        """
        Uydurucunun buldugu nokta GERCEK parametrelerden daha yuksek
        olabilirlik veriyor. Yani alpha'daki sapma optimize edici hatasi
        degil, sonlu ornekte olabilirligin alpha yonunde duz olmasi.
        Bu test o yorumu sabitliyor.
        """
        gercek = (0.15, -0.08, 0.95)
        seri = egarch_serisi_uret(3000, *gercek)
        f = G.fit_egarch11(seri)
        ll_gercek = G._egarch_log_olabilirlik(
            seri, gercek[0], gercek[1], gercek[2], math.log(f["sigma2_bar"]))
        self.assertGreaterEqual(f["loglik"], ll_gercek,
                                "uydurma gercek parametrelerden kotu cikti")

    def test_asimetriyi_yakalar(self):
        """Negatif gamma ile uretilmis seride gamma negatif bulunmali."""
        f = G.fit_egarch11(egarch_serisi_uret(2000, 0.15, -0.15, 0.95))
        self.assertLess(f["gamma"], -0.05)
        self.assertIn("dusus", f["asimetri"])

    def test_ters_asimetriyi_de_yakalar(self):
        """Isaret sabitlenmemis olmali: pozitif gamma da bulunabilmeli."""
        f = G.fit_egarch11(egarch_serisi_uret(2000, 0.15, +0.12, 0.95, seed=47))
        self.assertGreater(f["gamma"], 0.05)
        self.assertIn("yukselis", f["asimetri"])

    def test_simetrik_seride_gamma_sifira_yakin(self):
        f = G.fit_egarch11(egarch_serisi_uret(2000, 0.15, 0.0, 0.95, seed=31))
        self.assertLess(abs(f["gamma"]), 0.05,
                        "simetrik seride asimetri uydurulmamali")

    def test_asimetrik_seride_GARCH_i_geceR(self):
        """
        Modelin eklenme gerekcesi: asimetrik veride GARCH'tan daha iyi
        uyum vermeli. Simetrik veride fark kapanmali — aksi halde
        EGARCH sadece fazladan parametreyle ezbere uyuyor olurdu.
        """
        asimetrik = egarch_serisi_uret(2000, 0.15, -0.15, 0.95)
        g = G.fit_garch11(asimetrik)
        e = G.fit_egarch11(asimetrik)
        asimetrik_fark = e["loglik"] - g["loglik"]
        self.assertGreater(asimetrik_fark, 10.0,
                           "asimetrik seride EGARCH belirgin ustun olmali")

        simetrik = egarch_serisi_uret(2000, 0.15, 0.0, 0.95, seed=31)
        g2 = G.fit_garch11(simetrik)
        e2 = G.fit_egarch11(simetrik)
        simetrik_fark = e2["loglik"] - g2["loglik"]
        self.assertLess(simetrik_fark, asimetrik_fark / 3,
                        "simetrik seride ustunluk belirgin sekilde azalmali")

    def test_duragan(self):
        for seed in (3, 9, 17):
            f = G.fit_egarch11(egarch_serisi_uret(600, 0.15, -0.08, 0.95, seed=seed))
            self.assertLess(abs(f["beta"]), 1.0, "duragan olmayan uyum dondu")

    def test_omega_hedeflemeyle_tutarli(self):
        f = G.fit_egarch11(egarch_serisi_uret(1000, 0.15, -0.08, 0.95))
        self.assertAlmostEqual(
            f["omega"],
            G._egarch_omega(f["alpha"], f["gamma"], f["beta"],
                            math.log(f["sigma2_bar"])),
            places=12)

    def test_jensen_duzeltmesi_uygulanir(self):
        """
        Naif hedefleme (omega = (1-beta)*log(s2_bar)) kosulsuz LOG-varyansi
        hedefler; ama E[exp(X)] != exp(E[X]), yani modelin ima ettigi
        VARYANS s2_bar'dan buyuk cikar ve tahminler sistematik yuksek olur.
        Duzeltilmis omega naif olandan KUCUK olmali.
        """
        log_s2_bar = math.log(0.0004)
        for alpha, gamma, beta in ((0.15, -0.08, 0.95), (0.25, -0.20, 0.97)):
            naif = (1 - beta) * log_s2_bar
            duzeltilmis = G._egarch_omega(alpha, gamma, beta, log_s2_bar)
            self.assertLess(duzeltilmis, naif,
                            "Jensen duzeltmesi omega'yi dusurmeli")

    def test_jensen_duzeltmesi_kosulsuz_varyansi_tutturur(self):
        """
        Duzeltmenin ISE YARADIGI olculuyor: uzun bir benzetimde ortalama
        varyans hedefe yakin cikmali. Duzeltme olmadan bu oran 1.09-1.15
        araligindaydi.
        """
        alpha, gamma, beta, s2_bar = 0.15, -0.08, 0.95, 0.0004
        omega = G._egarch_omega(alpha, gamma, beta, math.log(s2_bar))
        r = random.Random(1)
        log_s2 = math.log(s2_bar)
        toplam, sayi = 0.0, 0
        for i in range(60000):
            s2 = math.exp(log_s2)
            if i > 3000:                       # yakinsama suresi atlanir
                toplam += s2
                sayi += 1
            z = r.gauss(0, 1)
            log_s2 = omega + alpha * (abs(z) - G._E_ABS_Z) + gamma * z + beta * log_s2
        oran = (toplam / sayi) / s2_bar
        self.assertAlmostEqual(oran, 1.0, delta=0.05,
                               msg=f"kosulsuz varyans hedeften sapti: {oran:.3f}x")

    def test_alpha_sifira_sabitlenmez(self):
        """
        alpha=0 modeli sok BUYUKLUGUNE tamamen duyarsiz birakir —
        dejenere bir kose ve ince izgara oraya dusebiliyordu.
        """
        for seed in (5, 13, 29):
            f = G.fit_egarch11(egarch_serisi_uret(800, 0.15, -0.08, 0.95, seed=seed))
            self.assertGreaterEqual(f["alpha"], 0.01,
                                    "alpha dejenere kosede sabitlendi")

    def test_sifir_varyansli_seri_none(self):
        self.assertIsNone(G.fit_egarch11([0.0] * 200))


class TestEgarchTahmin(unittest.TestCase):

    def setUp(self):
        self.seri = egarch_serisi_uret(1500, 0.15, -0.10, 0.95)
        self.fit = G.fit_egarch11(self.seri)

    def test_tum_ufuklar_pozitif(self):
        t = G.egarch_tahmin(self.fit, self.seri, G.PENCERELER)
        self.assertEqual(sorted(t), sorted(G.PENCERELER))
        for h, v in t.items():
            self.assertGreater(v, 0.0, f"{h} gunluk tahmin pozitif degil")

    def test_tekrarlanabilir(self):
        """
        Tahmin benzetimle uretiliyor; tohum sabit oldugu icin ayni girdi
        ayni sonucu vermeli. Aksi halde ekran her yenilemede ziplardi.
        """
        a = G.egarch_tahmin(self.fit, self.seri, [30])
        b = G.egarch_tahmin(self.fit, self.seri, [30])
        self.assertEqual(a, b)

    def test_ufukla_kosulsuza_yaklasir(self):
        t = G.egarch_tahmin(self.fit, self.seri, [1, 2000])
        kosulsuz = self.fit["uncond_vol_pct"]
        self.assertLess(abs(t[2000] - kosulsuz), abs(t[1] - kosulsuz) + 1e-9,
                        "uzun ufuk kosulsuza daha yakin olmali")

    def test_gecersiz_girdi_bos(self):
        self.assertEqual(G.egarch_tahmin(None, self.seri, [30]), {})
        self.assertEqual(G.egarch_tahmin(self.fit, [], [30]), {})
        self.assertEqual(G.egarch_tahmin(self.fit, self.seri, []), {})
        self.assertEqual(G.egarch_tahmin(self.fit, self.seri, [0]), {})


class TestDepoEntegrasyonu(unittest.TestCase):

    def setUp(self):
        self.dizin = tempfile.mkdtemp(prefix="garch-")
        self.d = S.Store(os.path.join(self.dizin, "t.db"), data_mode="MOCK")

    def tearDown(self):
        self.d.kapat()
        shutil.rmtree(self.dizin, ignore_errors=True)

    def _gunluk_doldur(self, ticker, getiriler, baslangic=100.0):
        """Verilen getirilerden gunluk bar serisi yazar."""
        fiyat = baslangic
        satirlar = []
        for i, g in enumerate(getiriler):
            fiyat *= math.exp(g)
            gun = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
            satirlar.append((gun, ticker, fiyat, fiyat, fiyat, fiyat, 1, "MOCK"))
        self.d.conn.executemany(
            "INSERT OR REPLACE INTO spot_daily "
            "(d, ticker, open, high, low, close, n, data_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", satirlar)
        self.d.conn.commit()

    def test_yetersiz_gecmiste_durust_cikti(self):
        """
        Depo yeni dolarken dogal olarak bu durumdayiz. Tahmin uretilmemeli
        ve NEDEN uretilmedigi soylenmeli.
        """
        self._gunluk_doldur("THYAO", garch_serisi_uret(20, 0.08, 0.90))
        s = G.ticker_hesapla(self.d, "THYAO")
        self.assertIsNotNone(s["insufficient"])
        self.assertIn("en az", s["insufficient"])
        self.assertTrue(all(v is None for v in s["models"]["GARCH(1,1)"].values()),
                        "yetersiz veride GARCH hucreleri bos kalmali")
        self.assertNotIn("fit", s)

    def test_pencereden_kisa_seride_rv_bos(self):
        """30 gozlemle 180 gunluk gerceklesmis volatilite hesaplanamaz."""
        self._gunluk_doldur("THYAO", garch_serisi_uret(31, 0.08, 0.90))
        s = G.ticker_hesapla(self.d, "THYAO")
        rv = s["models"]["Realized"]
        self.assertIsNotNone(rv["15D (%)"])
        self.assertIsNotNone(rv["30D (%)"])
        self.assertIsNone(rv["90D (%)"], "elde olmayan pencere uretilmemeli")
        self.assertIsNone(rv["180D (%)"])

    def test_yeterli_gecmiste_tahmin_ve_surum(self):
        self._gunluk_doldur("THYAO", garch_serisi_uret(300, 0.08, 0.90))
        s = G.ticker_hesapla(self.d, "THYAO")
        self.assertIsNone(s["insufficient"])
        self.assertIn("fit", s)
        for p in G.PENCERELER:
            self.assertIsNotNone(s["models"]["GARCH(1,1)"][f"{p}D (%)"])
            self.assertGreater(s["models"]["GARCH(1,1)"][f"{p}D (%)"], 0)

    def test_egarch_cikti_setinde(self):
        """EGARCH, GARCH'in yaninda ayri bir model olarak gelmeli."""
        self._gunluk_doldur("THYAO", egarch_serisi_uret(300, 0.15, -0.10, 0.95))
        s = G.ticker_hesapla(self.d, "THYAO")
        self.assertIn("EGARCH(1,1)", s["models"])
        self.assertIn("efit", s)
        self.assertIn("gamma", s["efit"])
        for p in G.PENCERELER:
            self.assertIsNotNone(s["models"]["EGARCH(1,1)"][f"{p}D (%)"])

    def test_yetersiz_veride_egarch_de_bos(self):
        self._gunluk_doldur("THYAO", egarch_serisi_uret(20, 0.15, -0.10, 0.95))
        s = G.ticker_hesapla(self.d, "THYAO")
        self.assertTrue(all(v is None for v in s["models"]["EGARCH(1,1)"].values()))
        self.assertNotIn("efit", s)

    def test_egarch_surumu_ayri_yazilir(self):
        """
        Iki model ayri surum satiri acmali: biri yakinsayip digeri
        yakinsamadiginda hangisinin ne zaman uydurulduğu kaybolmamali.
        """
        self._gunluk_doldur("THYAO", egarch_serisi_uret(300, 0.15, -0.10, 0.95))
        govde = G.tumunu_hesapla(self.d)
        self.assertEqual(govde["fitted_egarch"], 1)
        egarch = self.d.model_surumleri(model="egarch11")
        garch = self.d.model_surumleri(model="garch11")
        self.assertEqual(len(egarch), 1)
        self.assertEqual(len(garch), 1)
        self.assertEqual(egarch[0]["scope"], "THYAO")
        self.assertIn("gamma", egarch[0]["params"])
        self.assertNotIn("gamma", garch[0]["params"])

    def test_tumunu_hesapla_surum_yazar(self):
        self._gunluk_doldur("THYAO", garch_serisi_uret(300, 0.08, 0.90))
        self._gunluk_doldur("GARAN", garch_serisi_uret(300, 0.10, 0.85, seed=11))
        govde = G.tumunu_hesapla(self.d)

        self.assertEqual(govde["tickers"], 2)
        self.assertEqual(govde["fitted"], 2)
        self.assertEqual(govde["data_mode"], "MOCK")
        self.assertIn("store", govde["lookbacks"])
        self.assertIn("THYAO", govde["lookbacks"]["store"])
        self.assertIn("GARCH(1,1)", govde["lookbacks"]["store"]["THYAO"])

        surumler = self.d.model_surumleri(model="garch11")
        self.assertEqual(len(surumler), 2, "her uyum bir surum yazmali")
        self.assertEqual({s["scope"] for s in surumler}, {"THYAO", "GARAN"})
        self.assertIsNotNone(surumler[0]["fit_quality"])

    def test_surum_yazmayi_kapatabilir(self):
        self._gunluk_doldur("THYAO", garch_serisi_uret(300, 0.08, 0.90))
        G.tumunu_hesapla(self.d, surum_yaz=False)
        self.assertEqual(self.d.model_surumleri(model="garch11"), [])

    def test_bos_depo(self):
        govde = G.tumunu_hesapla(self.d)
        self.assertEqual(govde["tickers"], 0)
        self.assertEqual(govde["fitted"], 0)
        self.assertEqual(govde["lookbacks"]["store"], {})

    def test_mock_gecmisi_canli_moda_sizmaz(self):
        """GARCH yalnizca kendi modunun gecmisini gormeli."""
        self._gunluk_doldur("THYAO", garch_serisi_uret(300, 0.08, 0.90))
        canli = S.Store(self.d.yol, data_mode="LIVE")
        try:
            govde = G.tumunu_hesapla(canli, surum_yaz=False)
            self.assertEqual(govde["tickers"], 0,
                             "MOCK gunluk barlari LIVE GARCH'ina girmemeli")
        finally:
            canli.kapat()


if __name__ == "__main__":
    unittest.main(verbosity=2)
