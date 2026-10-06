#!/usr/bin/env python3
"""
Nelson-Siegel-Svensson faiz egrisi.

Gozlenen birkac vade noktasindan purussuz, turevlenebilir bir egri uydurur.
Elle girilen nokta nokta oranlarin yerini alir: ara vadeler artik dogrusal
interpolasyonla degil, modelin kendisinden okunur.

    y(t) = b0
         + b1 * (1 - e^(-t/l1)) / (t/l1)
         + b2 * [(1 - e^(-t/l1)) / (t/l1) - e^(-t/l1)]
         + b3 * [(1 - e^(-t/l2)) / (t/l2) - e^(-t/l2)]

Parametrelerin anlami:
    b0  uzun vade seviyesi (t -> sonsuz limiti)
    b1  kisa vade sapmasi; b0+b1 ani vade orani
    b2  birinci kambur (l1 cevresinde)
    b3  ikinci kambur (l2 cevresinde) — NSS'i Nelson-Siegel'den ayiran terim
    l1, l2  kamburların konumunu belirleyen sonum sabitleri (yil)

Uydurma yontemi: l1 ve l2 sabitlendiginde model beta'larda DOGRUSALDIR, yani
en kucuk kareler kapali formda cozulur. Bu yuzden yalnizca iki boyutlu bir
(l1, l2) taramasi yapilir; genel amacli bir nonlineer optimize edici ve
dolayisiyla dis bagimlilik gerekmez.

Bagimlilik yoktur, yalnizca standart kutuphane.
"""

import math

# Tarama izgarasi: Turk piyasasinda anlamli vadeler aylar-birkac yil
# araliginda oldugu icin kisa sonumlere agirlik verilir.
_LAMBDA_IZGARA = [0.08, 0.15, 0.25, 0.40, 0.60, 0.85, 1.20, 1.70,
                  2.40, 3.30, 4.50, 6.00, 8.00, 10.0]


def _yuk_terimi(t, lam):
    """(1 - e^(-t/lam)) / (t/lam);  t -> 0 limitinde 1."""
    if lam <= 0:
        return 0.0
    x = t / lam
    if x < 1e-8:
        return 1.0
    return (1.0 - math.exp(-x)) / x


def nss_oran(t, b0, b1, b2, b3, l1, l2):
    """Verilen vade (yil) icin modelin ongordugu orani dondurur."""
    if t is None or t < 0:
        return None
    if t == 0:
        return b0 + b1
    y1 = _yuk_terimi(t, l1)
    y2 = _yuk_terimi(t, l2)
    return (b0
            + b1 * y1
            + b2 * (y1 - math.exp(-t / l1))
            + b3 * (y2 - math.exp(-t / l2)))


def _tasarim_satiri(t, l1, l2):
    """Sabit l1, l2 icin bir gozlemin dogrusal katsayilari."""
    y1 = _yuk_terimi(t, l1)
    y2 = _yuk_terimi(t, l2)
    return [1.0, y1, y1 - math.exp(-t / l1), y2 - math.exp(-t / l2)]


def _coz(A, b):
    """
    Kucuk kare sistemi Gauss eliminasyonu ile cozer (kismi pivotlama).
    numpy yok; matris 4x4 oldugundan bu yeterli ve hizli.
    """
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for s in range(n):
        p = max(range(s, n), key=lambda r: abs(M[r][s]))
        if abs(M[p][s]) < 1e-12:
            return None                      # tekil: bu lambda ciftini atla
        M[s], M[p] = M[p], M[s]
        for r in range(s + 1, n):
            f = M[r][s] / M[s][s]
            for c in range(s, n + 1):
                M[r][c] -= f * M[s][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        toplam = M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))
        x[r] = toplam / M[r][r]
    return x


def fit_nss(noktalar, izgara=None):
    """
    noktalar: [(vade_yil, oran), ...]   oran ondalik (0.30 = %30)

    Dondurur: {b0, b1, b2, b3, l1, l2, rmse, nokta_sayisi, model} ya da
    yetersiz veri varsa None.

    Gozlem sayisina gore model kuculur:
      >= 5 nokta : NSS   (b0,b1,b2,b3 — iki kambur)
      3-4 nokta  : NS    (b0,b1,b2    — tek kambur, b3=0)
      < 3 nokta  : None

    Bu bir tercih degil zorunluluk: dort dogrusal parametreyi dort noktayla
    uydurmak tam belirlenmis bir sistemdir, RMSE sifir cikar ve uyum
    kalitesi hakkinda hicbir sey soylemez. Vadeli piyasada genelde yalnizca
    3 aktif vade bulundugu icin NS yolu pratikte asil kullanilan olur.
    """
    temiz = [(float(t), float(y)) for t, y in noktalar
             if t is not None and y is not None and t > 0 and math.isfinite(y)]
    if len(temiz) < 3:
        return None

    nss_mi = len(temiz) >= 5
    izgara = izgara or _LAMBDA_IZGARA
    en_iyi = None

    for i, l1 in enumerate(izgara):
        for l2 in (izgara[i + 1:] if nss_mi else [izgara[i]]):
            X = [_tasarim_satiri(t, l1, l2)[:4 if nss_mi else 3] for t, _ in temiz]
            Y = [y for _, y in temiz]

            n = 4 if nss_mi else 3
            # Normal denklemler: (X'X) b = X'Y
            XtX = [[sum(X[k][r] * X[k][c] for k in range(len(X)))
                    for c in range(n)] for r in range(n)]
            XtY = [sum(X[k][r] * Y[k] for k in range(len(X))) for r in range(n)]
            beta = _coz(XtX, XtY)
            if beta is None:
                continue
            if not nss_mi:
                beta = list(beta) + [0.0]      # b3 kapali

            sse = 0.0
            for (t, y) in temiz:
                tahmin = nss_oran(t, beta[0], beta[1], beta[2], beta[3], l1, l2)
                sse += (tahmin - y) ** 2
            if en_iyi is None or sse < en_iyi[0]:
                en_iyi = (sse, beta, l1, l2)

    if en_iyi is None:
        return None

    sse, beta, l1, l2 = en_iyi
    return {
        "b0": beta[0], "b1": beta[1], "b2": beta[2], "b3": beta[3],
        "l1": l1, "l2": l2,
        "rmse": math.sqrt(sse / len(temiz)),
        "nokta_sayisi": len(temiz),
        "model": "NSS" if nss_mi else "NS",
        # Parametre sayisi gozlem sayisina esit ya da fazlaysa uyum
        # "mukemmel" gorunur ama bu bilgi tasimaz.
        "tam_belirlenmis": len(temiz) <= (4 if nss_mi else 3),
    }


def egri_noktalari(fit, vadeler_gun):
    """Uydurulmus egriyi verilen gun vadelerinde orneklenmis hale getirir."""
    if not fit:
        return []
    return [{"dtm": g,
             "rate": nss_oran(g / 365.0, fit["b0"], fit["b1"], fit["b2"],
                              fit["b3"], fit["l1"], fit["l2"])}
            for g in vadeler_gun]


def dv01(fiyat_fonksiyonu, oran, *args, bp=1e-4):
    """
    1 baz puanlik paralel faiz kaymasina karsi fiyat degisimi.

    Analitik rho yerine sayisal fark kullanilir: boylece Black-Scholes
    disindaki yontemlerde (binom agaci gibi) de ayni tanim gecerli olur.
    """
    yukari = fiyat_fonksiyonu(oran + bp, *args)
    asagi = fiyat_fonksiyonu(oran - bp, *args)
    if yukari is None or asagi is None:
        return None
    return (yukari - asagi) / 2.0
