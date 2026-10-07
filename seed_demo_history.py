#!/usr/bin/env python3
"""
seed_demo_history.py — demo icin gecmis seri uretir.

NEDEN BU DOSYA VAR
------------------
Depo ileriye birikiyor. Temiz bir klonda ilk gun yalnizca o gunun
barlari var ve gecmise dayanan her sey bos kaliyor:

    GARCH / EGARCH tahmini   60 getiri gerekiyor, 0 var
    Korelasyon matrisi       en az 2 ortak gun gerekiyor, 1 var
    Gerceklesmis volatilite  ayni sebep

Yani panonun yarisi, "henuz veri yok" diyerek dogru davraniyor ama demo
gostermeye uygun olmuyor. Bu betik o bosluğu doldurur.

URETILMIS VERI, OLCULMUS DEGIL
------------------------------
Buradan cikan seri UYDURMADIR. Depoya MOCK etiketiyle yazilir, yani
sistemin geri kalani onu zaten uretilmis veri olarak isler ve canli
seriyle asla karismaz. Pano da ustteki kirmizi bantla bunu soyluyor.
Gercek piyasa verisi icin import_history.py kullanilir.

IKI TUZAK VAR, IKISI DE BURADA COZULU
-------------------------------------
1. Seri BUGUNKU fiyattan geriye yurutuluyor. Rastgele bir yerden
   baslayip ileri gitmek, dunku kapanisla bugunku spot arasinda yapay
   bir sicrama birakiyor — ve o tek sahte getiri, volatilite
   tahminini tamamen bozuyor. (Bu hata bir kez yapildi: EGARCH %82
   gosterdi, sebebi tohumun temmuzda bitip canli akisin bugunu
   yazmasiydi.)
2. BUGUN yazilmiyor. Bugunun bari gun ici tick'lerden olusuyor; uzerine
   yazmak gercek gozlemi silerdi. store.gunluk_barlari_aktar zaten
   tick'ten olusmus barlari korur, betik de bugune hic dokunmaz.

KULLANIM
--------
    python3 seed_demo_history.py                # 250 is gunu
    python3 seed_demo_history.py --gun 500
    python3 seed_demo_history.py --temizle      # urettigini geri al
"""

import argparse
import math
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store as st                                            # noqa: E402

# Gunluk oynaklik. BIST hisseleri icin yillik ~%35 kabaca gunluk %2,2
# eder (0.35 / sqrt(252)). Demo sayilarinin inandirici gorunmesi icin
# yeterli; bir tahmin degil, bir sahne dekoru.
GUNLUK_VOL = 0.022

# Piyasa faktorunun payi. Korelasyon matrisinin anlamli gorunmesi icin
# gerekli: tamamen bagimsiz yuruyusler uretilirse matris sifir cikar ve
# ekranda tek duze bir kare gorunur.
PIYASA_AGIRLIGI = 0.55


def is_gunleri(bitis, adet):
    """bitis gununden GERIYE dogru `adet` is gunu (bitis dahil degil)."""
    gunler = []
    g = bitis - timedelta(days=1)
    while len(gunler) < adet:
        if g.weekday() < 5:
            gunler.append(g)
        g -= timedelta(days=1)
    return list(reversed(gunler))


def seri_uret(son_fiyat, gunler, betalar_rng, piyasa_getirileri):
    """
    Bugunku fiyattan GERIYE yurutur.

    p[t-1] = p[t] * exp(-r[t])  — boylece serinin son noktasi gercek
    spot'a tam oturur ve dun ile bugun arasinda yapay sicrama olmaz.
    """
    beta = betalar_rng.uniform(0.6, 1.4)
    fiyatlar = [None] * len(gunler)
    p = son_fiyat
    for i in range(len(gunler) - 1, -1, -1):
        fiyatlar[i] = p
        ozel = betalar_rng.gauss(0.0, GUNLUK_VOL * math.sqrt(1 - PIYASA_AGIRLIGI))
        r = beta * piyasa_getirileri[i] + ozel
        p = p / math.exp(r)
        if p <= 0.01:                     # sayisal guvenlik
            p = son_fiyat
    return fiyatlar


def main(argv=None):
    ap = argparse.ArgumentParser(description="Demo icin uretilmis gecmis seri")
    ap.add_argument("--gun", type=int, default=250,
                    help="uretilecek is gunu sayisi (varsayilan 250 ~ 1 yil)")
    ap.add_argument("--tohum", type=int, default=20261007,
                    help="rastgelelik tohumu; ayni tohum ayni seriyi verir")
    ap.add_argument("--db", help="veritabani yolu")
    ap.add_argument("--temizle", action="store_true",
                    help="uretilmis barlari sil (n=0 olanlar), olculmuslere dokunma")
    a = ap.parse_args(argv)

    if not st.STORE_ENABLED:
        print("hata: STORE_ENABLED=0 — depo kapali", file=sys.stderr)
        return 1

    # Demo verisi her zaman MOCK. Uretilmis seriyi LIVE etiketiyle
    # yazmak, sistemin tum mock/canli ayrimini bosa cikarirdi.
    depo = st.Store(yol=a.db, data_mode="MOCK")
    try:
        if a.temizle:
            with depo._kilit:
                im = depo.conn.execute(
                    "DELETE FROM spot_daily WHERE data_mode = 'MOCK' AND n = 0")
                depo.conn.commit()
            print(f"silinen uretilmis bar: {im.rowcount}")
            print("olculmus barlar (n > 0) korundu.")
            return 0

        # Bugunun barlari = tickerlarin guncel fiyatlari. Seri bunlara
        # baglanacak.
        bugun = date.today().isoformat()
        satirlar = depo.conn.execute(
            "SELECT ticker, close FROM spot_daily "
            "WHERE data_mode = 'MOCK' AND d = ? ORDER BY ticker", (bugun,)
        ).fetchall()
        if not satirlar:
            print("hata: bugune ait bar yok — once panoyu calistirip veri", file=sys.stderr)
            print("      akmasini bekleyin (python3 start.py, ~10 saniye).", file=sys.stderr)
            return 1

        gunler = is_gunleri(date.today(), a.gun)
        rng = random.Random(a.tohum)
        # Ortak piyasa faktoru — tum tickerlar icin ayni.
        piyasa = [rng.gauss(0.0, GUNLUK_VOL * math.sqrt(PIYASA_AGIRLIGI))
                  for _ in gunler]

        barlar = []
        for s in satirlar:
            fiyatlar = seri_uret(float(s["close"]), gunler, rng, piyasa)
            for g, p in zip(gunler, fiyatlar):
                # Gun ici aralik: kapanisin etrafinda kucuk bir bant.
                yuksek = p * (1 + abs(rng.gauss(0, 0.004)))
                dusuk = p * (1 - abs(rng.gauss(0, 0.004)))
                acilis = min(max(p * (1 + rng.gauss(0, 0.003)), dusuk), yuksek)
                barlar.append((g.isoformat(), s["ticker"],
                               round(acilis, 4), round(yuksek, 4),
                               round(dusuk, 4), round(p, 4)))

        sonuc = depo.gunluk_barlari_aktar(barlar)
        print(f"uretilen  : {len(satirlar)} ticker x {len(gunler)} is gunu")
        print(f"yazilan   : {sonuc['yazilan']} bar  (etiket MOCK)")
        if sonuc["korunan"]:
            print(f"korunan   : {sonuc['korunan']} olculmus bar")
        print(f"tarih     : {gunler[0]} .. {gunler[-1]}  (bugun yazilmadi)")
        print()
        print("Simdi calistirin:")
        print("  python3 garch.py          # volatilite tahminleri")
        print("  python3 correlation.py    # korelasyon matrisi")
        print()
        print("Bu seri UYDURMADIR ve depoya MOCK olarak yazildi.")
    finally:
        depo.kapat()
    return 0


if __name__ == "__main__":
    sys.exit(main())
