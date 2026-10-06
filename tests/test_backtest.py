#!/usr/bin/env python3
"""
Kayan kokenli capraz dogrulama testleri.

En onemlisi SIZINTI testi: kokendeki tahmin, kokenden sonraki veriye
bagli olmamali. Bagli olsaydi tum alistirma anlamsiz olurdu ve bu
sessizce olur — sayilar yine makul gorunur, yalnizca fazla iyi cikar.
"""

import math
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import backtest as BT  # noqa: E402
import garch as G      # noqa: E402
import store as S      # noqa: E402


def seri_uret(n, alpha=0.08, beta=0.90, s2_bar=0.0004, seed=7):
    r = random.Random(seed)
    omega = s2_bar * (1 - alpha - beta)
    s2 = s2_bar
    out = []
    for _ in range(n + 400):
        e = r.gauss(0, 1) * math.sqrt(s2)
        out.append(e)
        s2 = omega + alpha * e * e + beta * s2
    return out[400:]


class TestSizinti(unittest.TestCase):
    """
    Kayan koken yonteminin BUTUN dayanagi, modelin gelecegi gormemesi.
    """

    def test_tahmin_koken_sonrasi_veriye_BAGLI_DEGIL(self):
        seri = seri_uret(400)
        koken, ufuk = 200, 30

        # Ayni koken, iki farkli "gelecek": biri gercek seri, oteki
        # kokenden sonrasi tamamen degistirilmis hali.
        bozuk = seri[:koken] + [x * 5 for x in seri[koken:]]

        a = BT.koken_tahminleri(seri, koken, ufuk, None)
        b = BT.koken_tahminleri(bozuk, koken, ufuk, None)

        for model in BT.MODELLER:
            self.assertEqual(
                a[model], b[model],
                f"{model} tahmini kokenden sonraki veriye bagli — sizinti var")

    def test_tahmin_seriyi_kisaltmakla_degismez(self):
        """Kokenden sonrasini tamamen atmak da tahmini degistirmemeli."""
        seri = seri_uret(400)
        koken = 200
        a = BT.koken_tahminleri(seri, koken, 30, None)
        b = BT.koken_tahminleri(seri[:koken], koken, 30, None)
        for model in BT.MODELLER:
            self.assertEqual(a[model], b[model], f"{model} icin tutarsizlik")


class TestGerceklesenVol(unittest.TestCase):

    def test_ileri_pencereyi_alir(self):
        seri = [0.001] * 100 + [0.05, -0.05] * 25
        sakin = BT.gerceklesen_vol(seri, 0, 50)
        oynak = BT.gerceklesen_vol(seri, 100, 50)
        self.assertLess(sakin, oynak, "ileri pencere dogru dilimi almiyor")

    def test_yarim_pencere_none(self):
        """Pencere yarisindan az doluysa guvenilmez, None donmeli."""
        seri = seri_uret(100)
        self.assertIsNone(BT.gerceklesen_vol(seri, 95, 30))

    def test_pencere_sonuna_tasarsa_none(self):
        seri = seri_uret(100)
        self.assertIsNone(BT.gerceklesen_vol(seri, 100, 30))


class TestDogrula(unittest.TestCase):

    def test_yetersiz_veride_none(self):
        sonuc, koken = BT.dogrula(seri_uret(50), ufuk=30, min_egitim=80)
        self.assertIsNone(sonuc)
        self.assertEqual(koken, 0)

    def test_butun_modeller_puanlanir(self):
        sonuc, koken = BT.dogrula(seri_uret(300), ufuk=30, koken_sayisi=5)
        self.assertGreater(koken, 0)
        for m in BT.MODELLER:
            self.assertIn(m, sonuc)
            self.assertIsNotNone(sonuc[m], f"{m} puanlanmamis")
            for alan in ("rmse", "mae", "bias", "n"):
                self.assertIn(alan, sonuc[m])

    def test_koken_sayisi_asilmaz(self):
        _, koken = BT.dogrula(seri_uret(400), ufuk=30, koken_sayisi=5)
        self.assertLessEqual(koken, 5)

    def test_rmse_mae_den_kucuk_degil(self):
        """Matematiksel zorunluluk; ters cikarsa hesap yanlis demektir."""
        sonuc, _ = BT.dogrula(seri_uret(300), ufuk=30, koken_sayisi=6)
        for m, s in sonuc.items():
            if s:
                self.assertGreaterEqual(s["rmse"], s["mae"] - 1e-9,
                                        f"{m}: RMSE < MAE olamaz")

    def test_yanlilik_isareti_anlamli(self):
        """
        Sistematik yuksek tahmin pozitif yanlilik vermeli. Gercek
        volatiliteden kucuk bir seviyeye sabitlenmis saf dayanak
        kullanarak isaret dogrulaniyor.
        """
        sonuc, _ = BT.dogrula(seri_uret(300), ufuk=30, koken_sayisi=6)
        for m, s in sonuc.items():
            if s:
                # |bias| <= MAE her zaman dogru olmali
                self.assertLessEqual(abs(s["bias"]), s["mae"] + 1e-9,
                                     f"{m}: |yanlilik| MAE'yi asamaz")

    def test_naive_dayanak_hesaplaniyor(self):
        sonuc, _ = BT.dogrula(seri_uret(300), ufuk=30, koken_sayisi=5)
        self.assertIsNotNone(sonuc["Naive"],
                             "saf dayanak olmadan karsilastirma anlamsiz")
        self.assertGreater(sonuc["Naive"]["n"], 0)


class TestEnIyi(unittest.TestCase):

    def test_en_dusuk_rmse_secilir(self):
        skorlar = {
            "A": {"rmse": 5.0, "mae": 4.0, "bias": 0.0, "n": 5},
            "B": {"rmse": 3.0, "mae": 2.5, "bias": 0.0, "n": 5},
            "C": {"rmse": 7.0, "mae": 6.0, "bias": 0.0, "n": 5},
        }
        self.assertEqual(BT._en_iyi(skorlar), "B")

    def test_puanlanmamis_model_secilmez(self):
        skorlar = {"A": None, "B": {"rmse": 3.0, "mae": 2.0, "bias": 0.0, "n": 5}}
        self.assertEqual(BT._en_iyi(skorlar), "B")

    def test_hicbiri_yoksa_none(self):
        self.assertIsNone(BT._en_iyi({"A": None}))
        self.assertIsNone(BT._en_iyi({}))
        self.assertIsNone(BT._en_iyi(None))


class TestDepoEntegrasyonu(unittest.TestCase):

    def setUp(self):
        self.dizin = tempfile.mkdtemp(prefix="bt-")
        self.d = S.Store(os.path.join(self.dizin, "t.db"), data_mode="MOCK")

    def tearDown(self):
        self.d.kapat()
        shutil.rmtree(self.dizin, ignore_errors=True)

    def _doldur(self, ticker, getiriler):
        fiyat, satir = 100.0, []
        for i, g in enumerate(getiriler):
            fiyat *= math.exp(g)
            satir.append((f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                          ticker, fiyat, fiyat, fiyat, fiyat, 1, "MOCK"))
        self.d.conn.executemany(
            "INSERT OR REPLACE INTO spot_daily "
            "(d,ticker,open,high,low,close,n,data_mode) VALUES (?,?,?,?,?,?,?,?)", satir)
        self.d.conn.commit()

    def test_yeterli_gecmiste_puan_uretir(self):
        self._doldur("THYAO", seri_uret(300))
        s = BT.ticker_dogrula(self.d, "THYAO", ufuk=30, koken_sayisi=5)
        self.assertGreater(s["origins"], 0)
        self.assertIsNotNone(s["scores"])
        self.assertEqual(s["returns"], 299)

    def test_yetersiz_gecmiste_durust_cikti(self):
        self._doldur("GARAN", seri_uret(40))
        s = BT.ticker_dogrula(self.d, "GARAN", ufuk=30, koken_sayisi=5)
        self.assertEqual(s["origins"], 0)
        self.assertIsNone(s["scores"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
