#!/usr/bin/env python3
"""
correlation.py — varliklar arasi korelasyon matrisi.

NEDEN BU DOSYA VAR
Risk hesabi su ana kadar TEK BIR SKALER korelasyon katsayisina
dayaniyordu (arayuzde elle girilen rho, varsayilan 0.50) ve tek
faktorlu bir model kullaniyordu:

    z_i = sqrt(rho)*z_piyasa + sqrt(1-rho)*z_ozgu

Bu, THYAO-GARAN ile THYAO-HEKTS korelasyonunu AYNI kabul etmek demek.
Iki banka hissesi arasindaki bagintiyla bir banka ile bir kimya
sirketi arasindaki baginti ayni degil; VaR bu varsayima cok duyarli
oldugu icin sonuc sistematik olarak yaniliyordu. Depo gunluk
kapanislari biriktirdiginden artik gercek matris VERIDEN hesaplanabilir.

UC TEKNIK SORUN VE COZUMLERI
-----------------------------
1. HIZALAMA. Ticker'larin gozlem tarihleri ayni olmayabilir. Cift
   bazli (pairwise) hesap, her ciftte farkli ornek kullanacagi icin
   POZITIF YARI-TANIMLI OLMAYAN bir matris uretebilir. Burada ortak
   tarih kesisimi kullaniliyor (listwise): tek ornek, garantili PSD.

2. GURULTU. n gozlem, p varlik oldugunda ornek korelasyonu n/p kucuk
   oldukca guvenilmez olur; ozdegerler asiri yayilir ve matris tekile
   yaklasir. Cozum BUZULME (shrinkage): sabit-korelasyon hedefine
   dogru cekilir.

3. POZITIF TANIMLILIK. Monte Carlo icin Cholesky ayristirmasi gerekir
   ve bu yalnizca pozitif TANIMLI matriste calisir. Burada ozdeger
   kirpmaya gerek kalmiyor: PSD bir ornek matrisi, PD bir hedefe
   lambda>0 ile buzuldugunde sonuc PD olur. Yine de kor inanca
   birakilmiyor — Cholesky'nin kendisi sinama olarak kullanilip
   basarisizlikta lambda kademeli artiriliyor.

   (Ozdeger kirpma numpy'siz Jacobi donusu gerektirirdi; buzulme hem
   daha az kod hem istatistiksel olarak daha savunulabilir.)

Bagimlilik yoktur, yalnizca standart kutuphane.

KULLANIM
    python3 correlation.py                 hesapla ve dashboard'a gonder
    python3 correlation.py --show          ekrana yaz, gonderme
    python3 correlation.py --lambda 0.3    buzulme katsayisini sabitle
"""

import argparse
import json
import math
import os
from datetime import datetime, timezone

import store as S

# Korelasyon icin asgari ortak gozlem. Bunun altinda tahmin gurultuden
# ibarettir ve skaler rho'ya dusmek daha durusttur.
MIN_GOZLEM = int(os.environ.get("CORR_MIN_OBS", "60"))

# Azami varlik sayisi. p buyudukce n/p kuculur ve matris guvenilmez
# hale gelir; ayrica Cholesky O(p^3).
MAX_VARLIK = int(os.environ.get("CORR_MAX_ASSETS", "40"))


def _log_getiriler(kapanislar):
    """[(gun, kapanis)] -> {gun: log getiri}. Gun, getirinin BITTIGI gun."""
    out = {}
    onceki_gun, onceki = None, None
    for g, c in kapanislar:
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        if onceki is not None:
            out[g] = math.log(c / onceki)
        onceki_gun, onceki = g, c
    return out


def hizala(getiri_haritalari):
    """
    Ortak tarihlerde hizalanmis getiri matrisi.

    Cift bazli hesap yerine ortak kesisim kullaniliyor: her cift farkli
    ornek gorseydi matris pozitif yari-tanimli olmayabilirdi ve Cholesky
    patlardi.

    doner: (tickerlar, satirlar) — satirlar[t] = [getiri_1, ..., getiri_p]
    """
    tickerlar = sorted(getiri_haritalari)
    if not tickerlar:
        return [], []
    ortak = None
    for t in tickerlar:
        gunler = set(getiri_haritalari[t])
        ortak = gunler if ortak is None else (ortak & gunler)
    ortak = sorted(ortak or [])
    satirlar = [[getiri_haritalari[t][g] for t in tickerlar] for g in ortak]
    return tickerlar, satirlar


def ornek_korelasyon(satirlar):
    """Pearson korelasyon matrisi. Sabit seri (sigma=0) icin kosegen 1,
    disi 0 kabul edilir — tanimsiz bir degeri uydurmak yerine."""
    n = len(satirlar)
    if n < 2:
        return None
    p = len(satirlar[0])
    ort = [sum(r[j] for r in satirlar) / n for j in range(p)]
    sd = []
    for j in range(p):
        v = sum((r[j] - ort[j]) ** 2 for r in satirlar) / (n - 1)
        sd.append(math.sqrt(v))

    M = [[0.0] * p for _ in range(p)]
    for i in range(p):
        M[i][i] = 1.0
        for j in range(i + 1, p):
            if sd[i] <= 1e-15 or sd[j] <= 1e-15:
                M[i][j] = M[j][i] = 0.0
                continue
            kov = sum((r[i] - ort[i]) * (r[j] - ort[j]) for r in satirlar) / (n - 1)
            c = kov / (sd[i] * sd[j])
            c = max(-1.0, min(1.0, c))          # sayisal tasma kirpilir
            M[i][j] = M[j][i] = c
    return M


def ortalama_korelasyon(M):
    """Kosegen disi ortalama — buzulme hedefi ve skaler rho yedegi."""
    p = len(M)
    if p < 2:
        return 0.0
    toplam = sum(M[i][j] for i in range(p) for j in range(i + 1, p))
    return toplam / (p * (p - 1) / 2)


def buzult(M, lam):
    """
    Sabit-korelasyon hedefine dogru buzulme:
        S* = (1-lam)*S + lam*T,   T = ortalama korelasyonlu matris

    T pozitif tanimli ve S pozitif yari-tanimli oldugundan lam>0 icin
    sonuc pozitif TANIMLIDIR — Cholesky'nin ihtiyac duydugu sey bu.
    Hedefin birim matris degil sabit-korelasyon olmasi onemli: birim
    matrise buzulmek varliklar arasi bagintiyi sistematik olarak
    azaltir ve riski OLDUGUNDAN DUSUK gosterirdi.
    """
    p = len(M)
    ort = ortalama_korelasyon(M)
    out = [[0.0] * p for _ in range(p)]
    for i in range(p):
        for j in range(p):
            hedef = 1.0 if i == j else ort
            out[i][j] = (1.0 - lam) * M[i][j] + lam * hedef
    return out


def cholesky(M):
    """
    Alt ucgen L, L*L^T = M. Pozitif tanimli degilse None.

    Ayni zamanda pozitif tanimlilik SINAMASI olarak kullaniliyor:
    basarisizlik, buzulmenin yetersiz kaldigini gosterir.
    """
    p = len(M)
    L = [[0.0] * p for _ in range(p)]
    for i in range(p):
        for j in range(i + 1):
            toplam = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                x = M[i][i] - toplam
                if x <= 1e-12:
                    return None               # pozitif tanimli degil
                L[i][j] = math.sqrt(x)
            else:
                L[i][j] = (M[i][j] - toplam) / L[j][j]
    return L


def _varsayilan_lambda(n, p):
    """
    Gozlem/varlik oranina gore buzulme katsayisi.

    Ledoit-Wolf'un en iyi lambda'si veri bagimli ve hesabi uzun; burada
    aynı yonu izleyen basit bir kural kullaniliyor: n/p kuculdukce
    (ornek zayifladikca) hedefe daha cok yaslan. Tam Ledoit-Wolf bir
    iyilestirme olarak durmaktadir.
    """
    if p < 2 or n < 2:
        return 1.0
    oran = n / p
    if oran >= 20:
        return 0.05
    if oran >= 10:
        return 0.10
    if oran >= 5:
        return 0.20
    if oran >= 2:
        return 0.35
    return 0.50


def matris_hesapla(depo, tickerlar=None, lam=None, min_gozlem=None):
    """
    Depodaki gunluk kapanislardan korelasyon matrisi.

    doner: dict ya da yetersiz veride {"ok": False, "reason": ...}
    """
    min_gozlem = MIN_GOZLEM if min_gozlem is None else min_gozlem
    adaylar = tickerlar or depo.gunluk_kapanisi_olan_tickerlar(asgari_gun=2)
    if len(adaylar) < 2:
        return {"ok": False, "reason": f"{len(adaylar)} ticker — en az 2 gerekli"}

    haritalar = {}
    for t in adaylar:
        g = _log_getiriler(depo.gunluk_kapanislar(t))
        if len(g) >= min_gozlem:
            haritalar[t] = g
    if len(haritalar) < 2:
        return {"ok": False,
                "reason": f"{len(haritalar)} ticker'da {min_gozlem}+ getiri var — en az 2 gerekli"}

    # Varlik sayisi sinirlanir: p buyudukce n/p kuculur ve matris
    # guvenilmez hale gelir. En uzun gecmisi olanlar tutulur.
    if len(haritalar) > MAX_VARLIK:
        en_uzun = sorted(haritalar, key=lambda t: -len(haritalar[t]))[:MAX_VARLIK]
        haritalar = {t: haritalar[t] for t in en_uzun}

    tickerlar, satirlar = hizala(haritalar)
    n, p = len(satirlar), len(tickerlar)
    if n < min_gozlem:
        return {"ok": False,
                "reason": f"ortak tarih kesisimi {n} gun — en az {min_gozlem} gerekli"}

    ham = ornek_korelasyon(satirlar)
    if ham is None:
        return {"ok": False, "reason": "korelasyon hesaplanamadi"}

    secilen_lam = _varsayilan_lambda(n, p) if lam is None else float(lam)

    # Cholesky sinama olarak kullaniliyor: basarisizsa buzulme artirilir.
    # Teoride lam>0 yeterli ama sayisal kenar durumlar icin kor inanca
    # birakilmiyor.
    L = None
    denenen = []
    for aday_lam in [secilen_lam] + [0.2, 0.35, 0.5, 0.75, 0.95]:
        if aday_lam < secilen_lam:
            continue
        M = buzult(ham, aday_lam)
        L = cholesky(M)
        denenen.append(round(aday_lam, 3))
        if L is not None:
            secilen_lam = aday_lam
            break
    if L is None:
        return {"ok": False, "reason": "matris pozitif tanimli yapilamadi"}

    M = buzult(ham, secilen_lam)
    return {
        "ok": True,
        "tickers": tickerlar,
        "matrix": [[round(x, 6) for x in satir] for satir in M],
        "cholesky": [[round(x, 8) for x in satir] for satir in L],
        "observations": n,
        "assets": p,
        "shrinkage": round(secilen_lam, 4),
        "avg_correlation": round(ortalama_korelasyon(M), 4),
        "raw_avg_correlation": round(ortalama_korelasyon(ham), 4),
        "attempts": denenen,
        "data_mode": depo.data_mode,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _yazdir(r):
    if not r.get("ok"):
        print(f"  hesaplanamadi: {r['reason']}")
        return
    t = r["tickers"]
    print(f"\n  {r['assets']} varlik, {r['observations']} ortak gozlem")
    print(f"  buzulme lambda = {r['shrinkage']}   "
          f"ortalama korelasyon: ham {r['raw_avg_correlation']} -> {r['avg_correlation']}")
    gosterilecek = min(len(t), 10)
    print("\n  " + " " * 9 + "".join(f"{x[:6]:>8}" for x in t[:gosterilecek]))
    for i in range(gosterilecek):
        satir = "".join(f"{r['matrix'][i][j]:>8.2f}" for j in range(gosterilecek))
        print(f"  {t[i][:8]:<9}{satir}")
    if len(t) > gosterilecek:
        print(f"  ... ({len(t) - gosterilecek} varlik daha)")


def main():
    ap = argparse.ArgumentParser(description="Varliklar arasi korelasyon matrisi")
    ap.add_argument("--show", action="store_true", help="ekrana yaz, gonderme")
    ap.add_argument("--lambda", dest="lam", type=float, help="buzulme katsayisi (0-1)")
    ap.add_argument("--min-obs", type=int, help=f"asgari gozlem (varsayilan {MIN_GOZLEM})")
    ap.add_argument("--base-url", help="dashboard adresi")
    a = ap.parse_args()

    taban = a.base_url or os.environ.get("FRONTEND_BASE_URL", "http://127.0.0.1:5173")
    depo = S.Store()
    try:
        r = matris_hesapla(depo, lam=a.lam, min_gozlem=a.min_obs)
        _yazdir(r)
        if a.show:
            return
        if not r.get("ok"):
            print("  gonderilmedi.")
            return
        import requests
        requests.post(f"{taban}/api/correlation", json=r, timeout=5).raise_for_status()
        print(f"\n  -> {taban}/api/correlation")
        # Surumleme: hangi tarihte hangi matrisle risk olculdugu
        # sonradan sorulabilsin.
        depo.model_surum_yaz(
            "correlation", {"shrinkage": r["shrinkage"],
                            "avg_correlation": r["avg_correlation"],
                            "assets": r["assets"]},
            fit_quality=r["avg_correlation"],
            meta={"tickers": r["tickers"], "observations": r["observations"]})
    finally:
        depo.kapat()


if __name__ == "__main__":
    main()
