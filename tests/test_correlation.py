#!/usr/bin/env python3
"""
Korelasyon matrisi testleri.

Iki sey kritik:
  1. Matris POZITIF TANIMLI olmali — degilse Cholesky patlar ve risk
     simulasyonu hic calismaz. Ornek matrisi gurultuyle kolayca
     tekillesir, bu yuzden buzulmenin gercekten kurtardigi sinaniyor.
  2. Hizalama ortak tarih kesisimi uzerinden olmali. Cift bazli hesap
     her ciftte farkli ornek kullanir ve pozitif yari-tanimli olmayan
     matris uretebilir.
"""

import math
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import correlation as C  # noqa: E402
import store as S        # noqa: E402


def korelasyonlu_seri(n, hedef, seed=11):
    """Verilen korelasyon matrisine sahip seri uretir."""
    L = C.cholesky(hedef)
    r = random.Random(seed)
    p = len(hedef)
    out = []
    for _ in range(n):
        e = [r.gauss(0, 1) for _ in range(p)]
        out.append([sum(L[i][k] * e[k] for k in range(i + 1)) for i in range(p)])
    return out


class TestCholesky(unittest.TestCase):

    def test_birim_matris(self):
        L = C.cholesky([[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(L, [[1.0, 0.0], [0.0, 1.0]])

    def test_L_carpi_LT_matrisi_verir(self):
        M = [[1.0, 0.6, 0.3], [0.6, 1.0, 0.5], [0.3, 0.5, 1.0]]
        L = C.cholesky(M)
        self.assertIsNotNone(L)
        for i in range(3):
            for j in range(3):
                geri = sum(L[i][k] * L[j][k] for k in range(3))
                self.assertAlmostEqual(geri, M[i][j], places=10,
                                       msg=f"({i},{j}) geri carpim tutmuyor")

    def test_pozitif_tanimli_olmayan_None(self):
        # Korelasyon 1'den buyuk olamaz; bu matris pozitif tanimli degil
        self.assertIsNone(C.cholesky([[1.0, 1.5], [1.5, 1.0]]))
        # Tekil matris
        self.assertIsNone(C.cholesky([[1.0, 1.0], [1.0, 1.0]]))

    def test_alt_ucgen(self):
        L = C.cholesky([[1.0, 0.5], [0.5, 1.0]])
        self.assertEqual(L[0][1], 0.0, "ust ucgen sifir olmali")


class TestOrneklemKorelasyonu(unittest.TestCase):

    def test_bilinen_korelasyonu_geri_bulur(self):
        """
        Tek cekilis yerine birkac tohumun ORTALAMASI sinaniyor.
        n=2000'de bir korelasyon tahmininin standart hatasi ~0.022;
        tek cekilisi dar bir toleransa sokmak, tahmin ediciyi degil
        tohum sansini olcerdi.
        """
        hedef = [[1.0, 0.8, 0.1], [0.8, 1.0, 0.1], [0.1, 0.1, 1.0]]
        ab, ac = [], []
        for seed in range(1, 9):
            M = C.ornek_korelasyon(korelasyonlu_seri(2000, hedef, seed=seed))
            ab.append(M[0][1])
            ac.append(M[0][2])
        self.assertAlmostEqual(sum(ab) / len(ab), 0.8, delta=0.02)
        self.assertAlmostEqual(sum(ac) / len(ac), 0.1, delta=0.02)
        # Yuksek ve dusuk cift her cekiliste ayrismis olmali
        for a, c in zip(ab, ac):
            self.assertGreater(a, c + 0.4)

    def test_kosegen_bir_ve_simetrik(self):
        M = C.ornek_korelasyon(korelasyonlu_seri(300,
            [[1.0, 0.5, 0.2], [0.5, 1.0, 0.3], [0.2, 0.3, 1.0]]))
        for i in range(3):
            self.assertAlmostEqual(M[i][i], 1.0, places=12)
            for j in range(3):
                self.assertAlmostEqual(M[i][j], M[j][i], places=12)

    def test_sabit_seri_cokme_yapmaz(self):
        """Hic hareket etmemis fiyatta korelasyon tanimsizdir; uydurmak
        yerine 0 kabul edilir ve kosegen 1 kalir."""
        satirlar = [[r, 5.0] for r in (0.1, -0.2, 0.3, 0.05, -0.1)]
        M = C.ornek_korelasyon(satirlar)
        self.assertEqual(M[0][1], 0.0)
        self.assertEqual(M[1][1], 1.0)

    def test_iki_gozlemden_azi_None(self):
        self.assertIsNone(C.ornek_korelasyon([]))
        self.assertIsNone(C.ornek_korelasyon([[1.0, 2.0]]))

    def test_deger_araliginda_kalir(self):
        M = C.ornek_korelasyon(korelasyonlu_seri(500,
            [[1.0, 0.95], [0.95, 1.0]]))
        self.assertLessEqual(M[0][1], 1.0)
        self.assertGreaterEqual(M[0][1], -1.0)


class TestBuzulme(unittest.TestCase):

    def test_TEKIL_matrisi_kurtarir(self):
        """
        Asil mesele bu: p >= n oldugunda ornek matrisi tekildir ve
        Cholesky patlar. Buzulme olmadan risk simulasyonu hic
        calismazdi.
        """
        r = random.Random(5)
        satirlar = [[r.gauss(0, 1) for _ in range(10)] for _ in range(8)]
        M = C.ornek_korelasyon(satirlar)
        self.assertIsNone(C.cholesky(M), "ham matris tekil olmaliydi")
        for lam in (0.05, 0.2, 0.5):
            self.assertIsNotNone(C.cholesky(C.buzult(M, lam)),
                                 f"lambda={lam} ile kurtarilmaliydi")

    def test_ortalama_korelasyonu_KORUR(self):
        """
        Hedef sabit-korelasyon matrisi oldugu icin buzulme ciftleri
        ortalamaya ceker ama ORTALAMAYI degistirmez. Birim matrise
        buzulmek ortalamayi dusurur ve riski oldugundan dusuk
        gosterirdi.
        """
        M = C.ornek_korelasyon(korelasyonlu_seri(400,
            [[1.0, 0.8, 0.1], [0.8, 1.0, 0.1], [0.1, 0.1, 1.0]]))
        once = C.ortalama_korelasyon(M)
        for lam in (0.1, 0.5, 0.9):
            sonra = C.ortalama_korelasyon(C.buzult(M, lam))
            self.assertAlmostEqual(sonra, once, places=10,
                                   msg=f"lambda={lam} ortalamayi degistirdi")

    def test_ciftleri_ortalamaya_ceker(self):
        M = [[1.0, 0.9, 0.1], [0.9, 1.0, 0.1], [0.1, 0.1, 1.0]]
        ort = C.ortalama_korelasyon(M)
        B = C.buzult(M, 0.5)
        # Yuksek cift asagi, dusuk cift yukari gelmeli
        self.assertLess(B[0][1], M[0][1])
        self.assertGreater(B[0][2], M[0][2])
        self.assertTrue(min(M[0][2], ort) <= B[0][2] <= max(M[0][2], ort))

    def test_lambda_sifir_degistirmez(self):
        M = [[1.0, 0.7], [0.7, 1.0]]
        self.assertAlmostEqual(C.buzult(M, 0.0)[0][1], 0.7, places=12)

    def test_lambda_bir_tamamen_hedefe_goturur(self):
        M = [[1.0, 0.9, 0.1], [0.9, 1.0, 0.1], [0.1, 0.1, 1.0]]
        ort = C.ortalama_korelasyon(M)
        B = C.buzult(M, 1.0)
        self.assertAlmostEqual(B[0][1], ort, places=12)
        self.assertAlmostEqual(B[0][2], ort, places=12)
        self.assertAlmostEqual(B[0][0], 1.0, places=12)

    def test_kosegen_bir_kalir(self):
        M = C.ornek_korelasyon(korelasyonlu_seri(200,
            [[1.0, 0.6], [0.6, 1.0]]))
        for lam in (0.0, 0.3, 1.0):
            B = C.buzult(M, lam)
            for i in range(len(B)):
                self.assertAlmostEqual(B[i][i], 1.0, places=12)

    def test_varsayilan_lambda_oranla_artar(self):
        """Ornek zayifladikca (n/p kuculdukce) hedefe daha cok yaslan."""
        bol = C._varsayilan_lambda(500, 5)
        orta = C._varsayilan_lambda(100, 15)
        az = C._varsayilan_lambda(20, 20)
        self.assertLess(bol, orta)
        self.assertLess(orta, az)


class TestHizalama(unittest.TestCase):

    def test_ortak_tarih_kesisimi_kullanilir(self):
        """
        Cift bazli hesap her ciftte farkli ornek kullanirdi ve pozitif
        yari-tanimli olmayan matris uretebilirdi.
        """
        h = {
            "A": {"2026-01-01": 0.01, "2026-01-02": 0.02, "2026-01-03": 0.03},
            "B": {"2026-01-02": 0.02, "2026-01-03": 0.01, "2026-01-04": 0.04},
        }
        t, satirlar = C.hizala(h)
        self.assertEqual(t, ["A", "B"])
        self.assertEqual(len(satirlar), 2, "yalnizca 01-02 ve 01-03 ortak")

    def test_kesisim_yoksa_bos(self):
        h = {"A": {"2026-01-01": 0.01}, "B": {"2026-02-01": 0.02}}
        _, satirlar = C.hizala(h)
        self.assertEqual(satirlar, [])

    def test_bos_girdi(self):
        self.assertEqual(C.hizala({}), ([], []))

    def test_satirlar_ticker_sirasiyla(self):
        h = {"Z": {"g": 0.1}, "A": {"g": 0.2}}
        t, satirlar = C.hizala(h)
        self.assertEqual(t, ["A", "Z"], "tickerlar alfabetik olmali")
        self.assertEqual(satirlar[0], [0.2, 0.1], "degerler ticker sirasini izlemeli")


class TestDepoEntegrasyonu(unittest.TestCase):

    def setUp(self):
        self.dizin = tempfile.mkdtemp(prefix="kor-")
        self.d = S.Store(os.path.join(self.dizin, "t.db"), data_mode="MOCK")

    def tearDown(self):
        self.d.kapat()
        shutil.rmtree(self.dizin, ignore_errors=True)

    def _doldur(self, ticker, getiriler, baslangic_gun=0):
        fiyat, satir = 100.0, []
        for i, g in enumerate(getiriler):
            fiyat *= math.exp(g)
            gun_no = baslangic_gun + i
            satir.append((f"2026-{1 + gun_no // 28:02d}-{1 + gun_no % 28:02d}",
                          ticker, fiyat, fiyat, fiyat, fiyat, 1, "MOCK"))
        self.d.conn.executemany(
            "INSERT OR REPLACE INTO spot_daily "
            "(d,ticker,open,high,low,close,n,data_mode) VALUES (?,?,?,?,?,?,?,?)", satir)
        self.d.conn.commit()

    def test_depodan_hesaplanir_ve_cholesky_calisir(self):
        hedef = [[1.0, 0.75, 0.15], [0.75, 1.0, 0.15], [0.15, 0.15, 1.0]]
        seri = korelasyonlu_seri(200, hedef)
        for j, t in enumerate(("AAA", "BBB", "CCC")):
            self._doldur(t, [s[j] * 0.02 for s in seri])

        r = C.matris_hesapla(self.d, min_gozlem=60)
        self.assertTrue(r["ok"], r.get("reason"))
        self.assertEqual(r["tickers"], ["AAA", "BBB", "CCC"])
        self.assertEqual(len(r["matrix"]), 3)
        self.assertIsNotNone(C.cholesky(r["matrix"]),
                             "dondurulen matris pozitif tanimli olmali")
        # AAA-BBB bagintisi AAA-CCC'den belirgin yuksek olmali
        self.assertGreater(r["matrix"][0][1], r["matrix"][0][2] + 0.2)

    def test_dondurulen_cholesky_matrisi_geri_verir(self):
        seri = korelasyonlu_seri(150, [[1.0, 0.6], [0.6, 1.0]])
        for j, t in enumerate(("AAA", "BBB")):
            self._doldur(t, [s[j] * 0.02 for s in seri])
        r = C.matris_hesapla(self.d, min_gozlem=60)
        M, L = r["matrix"], r["cholesky"]
        for i in range(len(M)):
            for j in range(len(M)):
                geri = sum(L[i][k] * L[j][k] for k in range(len(M)))
                self.assertAlmostEqual(geri, M[i][j], places=5)

    def test_yetersiz_gecmiste_durust_cikti(self):
        self._doldur("AAA", [0.01] * 20)
        self._doldur("BBB", [0.02] * 20)
        r = C.matris_hesapla(self.d, min_gozlem=60)
        self.assertFalse(r["ok"])
        self.assertIn("60", r["reason"])

    def test_tek_ticker_yetersiz(self):
        self._doldur("AAA", [0.01 * i for i in range(100)])
        r = C.matris_hesapla(self.d, min_gozlem=10)
        self.assertFalse(r["ok"])
        self.assertIn("2", r["reason"])

    def test_bos_depo(self):
        r = C.matris_hesapla(self.d)
        self.assertFalse(r["ok"])

    def test_ortak_kesisim_kisa_ise_reddedilir(self):
        """Iki ticker uzun gecmise sahip olsa da ORTAK gunleri azsa
        tahmin guvenilmez; sessizce uretmek yerine reddedilmeli."""
        seri = korelasyonlu_seri(200, [[1.0, 0.5], [0.5, 1.0]])
        self._doldur("AAA", [s[0] * 0.02 for s in seri], baslangic_gun=0)
        self._doldur("BBB", [s[1] * 0.02 for s in seri], baslangic_gun=190)
        r = C.matris_hesapla(self.d, min_gozlem=60)
        self.assertFalse(r["ok"])
        self.assertIn("kesisim", r["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
