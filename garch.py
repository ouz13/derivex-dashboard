#!/usr/bin/env python3
"""
garch.py — gerceklesmis volatilite ve GARCH(1,1) tahmini, depodaki
gunluk kapanislardan.

NEDEN BU DOSYA VAR
------------------
Arayuzdeki Fcst sutunu bugune kadar `realized_forecasts_all_models.json`
adli DURAGAN bir dosyadan okunuyordu: bir kez uretilmis, veri aktikca
guncellenmeyen sayilar. Depo (store.py) gunluk kapanislari tutmaya
basladigi andan itibaren tahmin gercekten hesaplanabilir hale geliyor.

UC MODEL
--------
  Realized   kapanis-kapanis ornek standart sapmasi, yillik
  EWMA       RiskMetrics, lambda=0.94 — son gozlemlere ustel agirlik
  GARCH(1,1) sigma2_t = omega + alpha*r2_{t-1} + beta*sigma2_{t-1}

GARCH NASIL UYDURULUYOR
-----------------------
Varyans hedeflemesi kullaniliyor: omega = sigma2_bar * (1 - alpha - beta).
Boylece serbest parametre ikiye duser (alpha, beta) ve kisit kumesi
basitlesir. Iki boyutta once kaba, sonra yerel ince izgara taramasi
yapiliyor. Bu bir hiz tercihi degil saglamlik tercihi: genel amacli bir
optimize edici olmadan (numpy/scipy yok) Nelder-Mead elde yazilabilirdi
ama yakinsamadigi durumlarda sessizce sacma parametre dondururdu.
Izgara taramasi her zaman ayni sonucu verir ve sinirlari bellidir.

YETERSIZ VERIDE TAHMIN URETILMEZ
--------------------------------
60 gunluk getiriden az veri varken GARCH uydurmak anlamsizdir; bu durumda
model None doner ve neden None dondugu cagirana soylenir. Depo yeni
doldugunda dogal olarak bu durumda olunur — eksikligi makul gorunen bir
sayiyla doldurmak, kullaniciya duragan dosyayi canli sanmasindan daha
buyuk bir yanlis bilgi verir.

KULLANIM
--------
    python3 garch.py                    tum tickerlar, dosyaya yaz
    python3 garch.py --ticker THYAO     tek ticker, ekrana yaz
    python3 garch.py --min-returns 30   esigi dusur (kucuk gecmis icin)
"""

import argparse
import json
import math
import os
import random
from datetime import datetime, timezone

import store as S

ISGUNU = 252                 # yillandirma carpani
EWMA_LAMBDA = 0.94           # RiskMetrics
MIN_RETURNS = int(os.environ.get("GARCH_MIN_RETURNS", "60"))
PENCERELER = [15, 30, 60, 90, 180]

CIKTI_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "garch_forecasts_store.json")


# ---------------------------------------------------------------------------
# Getiriler
# ---------------------------------------------------------------------------

def log_getiriler(kapanislar):
    """[(gun, kapanis)] -> [log getiri]. Gecersiz/sifir fiyatlar atlanir."""
    r = []
    onceki = None
    for _, c in kapanislar:
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        if onceki is not None:
            r.append(math.log(c / onceki))
        onceki = c
    return r


def gerceklesmis_vol(getiriler, pencere=None):
    """
    Yillik gerceklesmis volatilite (yuzde).

    Ortalama sifir varsayilmaz: kisa pencerelerde ornek ortalamasi
    volatiliteyi gorunur sekilde sapitabiliyor.
    """
    r = getiriler[-pencere:] if pencere else getiriler
    n = len(r)
    if n < 2:
        return None
    ort = sum(r) / n
    kare = sum((x - ort) ** 2 for x in r) / (n - 1)
    return math.sqrt(kare * ISGUNU) * 100.0


def ewma_vol(getiriler, lam=EWMA_LAMBDA):
    """RiskMetrics EWMA: sigma2_t = lam*sigma2_{t-1} + (1-lam)*r2_{t-1}."""
    if len(getiriler) < 2:
        return None
    s2 = sum(x * x for x in getiriler[:min(20, len(getiriler))]) / min(20, len(getiriler))
    for x in getiriler:
        s2 = lam * s2 + (1 - lam) * x * x
    return math.sqrt(s2 * ISGUNU) * 100.0


# ---------------------------------------------------------------------------
# GARCH(1,1)
# ---------------------------------------------------------------------------

def _log_olabilirlik(getiriler, alpha, beta, s2_bar):
    """
    Gaussian log-olabilirlik. Varyans hedeflemesi geregi
    omega = s2_bar * (1 - alpha - beta).
    """
    kalici = 1.0 - alpha - beta
    if kalici <= 1e-6:
        return None                      # durağan olmayan bolge
    omega = s2_bar * kalici
    if omega <= 0:
        return None
    s2 = s2_bar                          # baslangic: kosulsuz varyans
    toplam = 0.0
    sabit = math.log(2.0 * math.pi)
    for r in getiriler:
        if s2 <= 1e-18:
            return None
        toplam += -0.5 * (sabit + math.log(s2) + (r * r) / s2)
        s2 = omega + alpha * r * r + beta * s2
    return toplam


def fit_garch11(getiriler, min_getiri=None):
    """
    GARCH(1,1) uydurur.

    Dondurur: {omega, alpha, beta, persistence, sigma2_bar, loglik,
               n, uncond_vol_pct} ya da yetersiz veride None.
    """
    min_getiri = MIN_RETURNS if min_getiri is None else min_getiri
    n = len(getiriler)
    if n < min_getiri:
        return None
    s2_bar = sum(x * x for x in getiriler) / n
    if s2_bar <= 0:
        return None

    def tara(a_araligi, b_araligi):
        en_iyi = None
        for a in a_araligi:
            for b in b_araligi:
                if a <= 0 or b <= 0 or a + b >= 0.9995:
                    continue
                ll = _log_olabilirlik(getiriler, a, b, s2_bar)
                if ll is None:
                    continue
                if en_iyi is None or ll > en_iyi[0]:
                    en_iyi = (ll, a, b)
        return en_iyi

    # Kaba tarama: finansal gunluk serilerde alpha genelde 0.02-0.20,
    # beta 0.75-0.97 araliginda cikar; izgara bunu kapsayacak kadar genis.
    kaba = tara([i / 100.0 for i in range(1, 31)],
                [i / 100.0 for i in range(50, 99)])
    if kaba is None:
        return None

    # Yerel ince tarama: kaba izgaranin bir adimlik komsulugunda 0.002 adim.
    _, a0, b0 = kaba
    ince_a = [a0 + k * 0.002 for k in range(-5, 6)]
    ince_b = [b0 + k * 0.002 for k in range(-5, 6)]
    ince = tara([a for a in ince_a if a > 0], [b for b in ince_b if b > 0])
    ll, alpha, beta = ince if ince is not None else kaba

    kalici = alpha + beta
    return {
        "omega": s2_bar * (1.0 - kalici),
        "alpha": alpha,
        "beta": beta,
        "persistence": kalici,
        "sigma2_bar": s2_bar,
        "loglik": ll,
        "n": n,
        "uncond_vol_pct": math.sqrt(s2_bar * ISGUNU) * 100.0,
    }


def garch_tahmin(fit, getiriler, ufuk):
    """
    ufuk gun icin ortalama volatilite tahmini (yillik yuzde).

    Tek gunluk tahmin degil, ufuk boyunca ortalama varyansin karekoku
    kullanilir: 30 gunluk bir tahmin, 30 gun sonraki gunun degil o 30
    gunun volatilitesidir.
    """
    if not fit or not getiriler or ufuk < 1:
        return None
    omega, alpha, beta = fit["omega"], fit["alpha"], fit["beta"]
    s2_bar, kalici = fit["sigma2_bar"], fit["persistence"]

    # Ornek sonundaki kosullu varyansa kadar ozyinele
    s2 = s2_bar
    for r in getiriler:
        s2 = omega + alpha * r * r + beta * s2

    # h adim ileri: s2_{t+h} = s2_bar + kalici^(h-1) * (s2_{t+1} - s2_bar)
    toplam = 0.0
    for h in range(1, ufuk + 1):
        toplam += s2_bar + (kalici ** (h - 1)) * (s2 - s2_bar)
    return math.sqrt((toplam / ufuk) * ISGUNU) * 100.0


# ---------------------------------------------------------------------------
# EGARCH(1,1)
#
#   log(sigma2_t) = omega + alpha*(|z| - E|z|) + gamma*z + beta*log(sigma2_{t-1})
#   z = r_{t-1} / sigma_{t-1}
#
# GARCH'tan IKI FARKI VAR:
#
#   1. ASIMETRI. gamma terimi getirinin ISARETINI tasiyor. Hisse
#      serilerinde dususlerin volatiliteyi yukselislerden daha cok
#      artirdigi bilinen bir etki (kaldirac etkisi) ve GARCH bunu
#      yapisal olarak yakalayamaz: orada yalnizca r^2 var, isaret
#      bilgisi kareyle birlikte kayboluyor. gamma'nin negatif cikmasi
#      beklenir.
#
#   2. LOG UZAYI. Varyans log olarak modellendigi icin pozitifligi
#      kendiliginden saglaniyor; omega, alpha, gamma uzerinde isaret
#      kisiti gerekmiyor. Duraganlik icin yalnizca |beta| < 1 yeter.
#
# Hedefleme: kosulsuz log-varyans omega/(1-beta) oldugundan
# omega = (1-beta) * log(sigma2_bar) secilip serbest parametre uce
# dusuruluyor (alpha, gamma, beta).
# ---------------------------------------------------------------------------

# Standart normal icin E|z|
_E_ABS_Z = math.sqrt(2.0 / math.pi)

# Cok adimli tahmin benzetiminde kullanilan yol sayisi. EGARCH'in
# |z| terimi yuzunden h adim ileri varyansin kapali formu yok; bu
# yuzden benzetim yapiliyor. Tohum sabit, yani sonuc tekrarlanabilir.
EGARCH_YOL = 1000
EGARCH_TOHUM = 20261006


# |z|'nin varyansi (standart normal): 1 - 2/pi
_VAR_ABS_Z = 1.0 - 2.0 / math.pi


def _egarch_omega(alpha, gamma, beta, log_s2_bar):
    """
    Hedeflenen omega — JENSEN DUZELTMELI.

    Naif hedefleme omega = (1-beta)*log(s2_bar) kosulsuz LOG-varyansi
    log(s2_bar)'a esitler. Ama E[exp(X)] != exp(E[X]): modelin ima
    ettigi kosulsuz VARYANS bu durumda s2_bar'dan buyuk cikiyor.
    Olculdu: parametre araligina gore 1.09x - 1.15x, yani volatilitede
    %4-7 yukari sapma. Tahminler sistematik olarak yuksek olurdu.

    Durgun log-varyansin varyansi:
        Var[log s2] = (alpha^2 * Var|z| + gamma^2) / (1 - beta^2)
    ve E[s2] = exp(E[log s2] + Var/2). E[s2] = s2_bar istendigi icin
    E[log s2] = log(s2_bar) - Var/2 secilir.

    Duzeltme sonrasi olculen sapma tipik parametrelerde %1'in altinda.
    Cok yuksek kaliciliktaki (beta~0.97, alpha~0.25) kosede ~%8 artik
    sapma kaliyor: |z| durgun durumda tam normal dagilmadigi icin
    yuksek mertebe terimler devreye giriyor. Tam duzeltme kapali formda
    yok; bu kose nadir ve sapma yon olarak bilindigi icin boyle birakildi.
    """
    var_log_s2 = (alpha * alpha * _VAR_ABS_Z + gamma * gamma) / (1.0 - beta * beta)
    return (1.0 - beta) * (log_s2_bar - 0.5 * var_log_s2)


def _egarch_log_olabilirlik(getiriler, alpha, gamma, beta, log_s2_bar):
    """Gaussian log-olabilirlik; omega hedeflemeyle sabitlenir."""
    if abs(beta) >= 0.9995:
        return None                       # duragan olmayan bolge
    omega = _egarch_omega(alpha, gamma, beta, log_s2_bar)
    log_s2 = log_s2_bar
    toplam = 0.0
    sabit = math.log(2.0 * math.pi)
    for r in getiriler:
        if log_s2 < -60.0 or log_s2 > 20.0:
            return None                   # sayisal tasma
        s2 = math.exp(log_s2)
        toplam += -0.5 * (sabit + log_s2 + (r * r) / s2)
        z = r / math.sqrt(s2)
        log_s2 = (omega + alpha * (abs(z) - _E_ABS_Z)
                  + gamma * z + beta * log_s2)
    return toplam


def fit_egarch11(getiriler, min_getiri=None):
    """
    EGARCH(1,1) uydurur.

    Dondurur: {omega, alpha, gamma, beta, sigma2_bar, loglik, n,
               uncond_vol_pct, asimetri} ya da yetersiz veride None.

    `asimetri` gamma'nin isaretini yorumlar: negatifse dususler
    volatiliteyi yukselislerden daha cok artiriyor demektir.
    """
    min_getiri = MIN_RETURNS if min_getiri is None else min_getiri
    n = len(getiriler)
    if n < min_getiri:
        return None
    s2_bar = sum(x * x for x in getiriler) / n
    if s2_bar <= 0:
        return None
    log_s2_bar = math.log(s2_bar)

    def tara(a_ara, g_ara, b_ara):
        en_iyi = None
        for a in a_ara:
            for g in g_ara:
                for b in b_ara:
                    ll = _egarch_log_olabilirlik(getiriler, a, g, b, log_s2_bar)
                    if ll is None:
                        continue
                    if en_iyi is None or ll > en_iyi[0]:
                        en_iyi = (ll, a, g, b)
        return en_iyi

    # Kaba tarama. alpha pozitif (buyukluk etkisi), gamma genelde
    # negatif (kaldirac), beta 1'e yakin (volatilite kalicidir).
    kaba = tara([0.01 + 0.04 * i for i in range(10)],          # 0.01 .. 0.37
                [-0.30 + 0.05 * i for i in range(9)],          # -0.30 .. 0.10
                [0.80 + 0.02 * i for i in range(10)])          # 0.80 .. 0.98
    if kaba is None:
        return None

    # Ince taramada alpha pozitif tutuluyor. alpha=0 modeli sok
    # BUYUKLUGUNE tamamen duyarsiz birakir — yalnizca isarete tepki
    # veren dejenere bir kose; kaba izgaranin alt ucunda bulunabiliyordu.
    _, a0, g0, b0 = kaba
    ince = tara([max(0.01, a0 + k * 0.01) for k in range(-2, 3)],
                [g0 + k * 0.0125 for k in range(-2, 3)],
                [b0 + k * 0.005 for k in range(-2, 3)])
    ll, alpha, gamma, beta = ince if ince is not None else kaba

    return {
        "omega": _egarch_omega(alpha, gamma, beta, log_s2_bar),
        "alpha": alpha,
        "gamma": gamma,
        "beta": beta,
        "sigma2_bar": s2_bar,
        "loglik": ll,
        "n": n,
        "uncond_vol_pct": math.sqrt(s2_bar * ISGUNU) * 100.0,
        "asimetri": ("dususlerde daha yuksek volatilite" if gamma < -1e-6
                     else ("yukselislerde daha yuksek volatilite" if gamma > 1e-6
                           else "simetrik")),
    }


def _egarch_son_log_varyans(fit, getiriler):
    """Ornek sonundaki kosullu log-varyansa kadar ozyinele."""
    omega, alpha, gamma, beta = fit["omega"], fit["alpha"], fit["gamma"], fit["beta"]
    log_s2 = math.log(fit["sigma2_bar"])
    for r in getiriler:
        s2 = math.exp(log_s2)
        z = r / math.sqrt(s2)
        log_s2 = omega + alpha * (abs(z) - _E_ABS_Z) + gamma * z + beta * log_s2
    return log_s2


def egarch_tahmin(fit, getiriler, ufuklar):
    """
    Verilen ufuklar icin ortalama volatilite tahmini (yillik yuzde).

    EGARCH'ta |z| terimi yuzunden h adim ileri varyansin kapali formu
    yok, bu yuzden ozyineleme benzetiliyor. Tum ufuklar TEK benzetimde
    hesaplaniyor: en uzun ufka kadar yurunup yol boyunca her kontrol
    noktasindaki kumulatif varyans toplaniyor.

    ufuklar: [15, 30, ...] -> {15: yillik_yuzde, ...}
    """
    if not fit or not getiriler or not ufuklar:
        return {}
    ufuklar = sorted(set(int(h) for h in ufuklar if int(h) >= 1))
    if not ufuklar:
        return {}
    azami = ufuklar[-1]

    omega, alpha, gamma, beta = fit["omega"], fit["alpha"], fit["gamma"], fit["beta"]
    baslangic = _egarch_son_log_varyans(fit, getiriler)

    rng = random.Random(EGARCH_TOHUM)
    # kontrol[h] = tum yollarda h adima kadar biriken varyans toplami
    birikim = {h: 0.0 for h in ufuklar}

    for _ in range(EGARCH_YOL):
        log_s2 = baslangic
        kum = 0.0
        sonraki = 0
        for adim in range(1, azami + 1):
            s2 = math.exp(log_s2)
            kum += s2
            if sonraki < len(ufuklar) and adim == ufuklar[sonraki]:
                birikim[ufuklar[sonraki]] += kum / adim
                sonraki += 1
            z = rng.gauss(0.0, 1.0)        # ileri dogru sok cekiliyor
            log_s2 = omega + alpha * (abs(z) - _E_ABS_Z) + gamma * z + beta * log_s2
            if log_s2 < -60.0 or log_s2 > 20.0:
                break
    return {h: math.sqrt((birikim[h] / EGARCH_YOL) * ISGUNU) * 100.0
            for h in ufuklar}


# ---------------------------------------------------------------------------
# Ticker bazinda hesap
# ---------------------------------------------------------------------------

def ticker_hesapla(depo, ticker, min_getiri=None):
    kapanislar = depo.gunluk_kapanislar(ticker)
    getiriler = log_getiriler(kapanislar)
    cikti = {
        "ticker": ticker,
        "days": len(kapanislar),
        "returns": len(getiriler),
        "models": {},
        "insufficient": None,
    }

    rv = {}
    for p in PENCERELER:
        # Pencereden az gozlem varsa o hucre bos kalir; eldeki kisa
        # seriyi 180 gunluk volatilite gibi sunmak yanlis olur.
        rv[f"{p}D (%)"] = _yuvarla(gerceklesmis_vol(getiriler, p)) if len(getiriler) >= p else None
    cikti["models"]["Realized"] = rv

    e = ewma_vol(getiriler)
    cikti["models"]["EWMA"] = {f"{p}D (%)": _yuvarla(e) for p in PENCERELER} if e is not None else \
                              {f"{p}D (%)": None for p in PENCERELER}

    fit = fit_garch11(getiriler, min_getiri)
    if fit is None:
        esik = MIN_RETURNS if min_getiri is None else min_getiri
        cikti["insufficient"] = (f"{len(getiriler)} getiri var, GARCH(1,1) icin "
                                 f"en az {esik} gerekiyor")
        cikti["models"]["GARCH(1,1)"] = {f"{p}D (%)": None for p in PENCERELER}
    else:
        cikti["models"]["GARCH(1,1)"] = {
            f"{p}D (%)": _yuvarla(garch_tahmin(fit, getiriler, p)) for p in PENCERELER
        }
        cikti["fit"] = {k: fit[k] for k in
                        ("omega", "alpha", "beta", "persistence", "loglik", "n", "uncond_vol_pct")}

    # EGARCH ayri uydurulur: GARCH yakinsasa da bu yakinsamayabilir
    # (ya da tersi), biri digerinin varligina baglanmamali.
    efit = fit_egarch11(getiriler, min_getiri)
    if efit is None:
        cikti["models"]["EGARCH(1,1)"] = {f"{p}D (%)": None for p in PENCERELER}
    else:
        tahmin = egarch_tahmin(efit, getiriler, PENCERELER)
        cikti["models"]["EGARCH(1,1)"] = {
            f"{p}D (%)": _yuvarla(tahmin.get(p)) for p in PENCERELER
        }
        cikti["efit"] = {k: efit[k] for k in
                         ("omega", "alpha", "gamma", "beta", "loglik", "n",
                          "uncond_vol_pct", "asimetri")}
    return cikti


def _yuvarla(x, basamak=2):
    return None if x is None else round(x, basamak)


def tumunu_hesapla(depo=None, min_getiri=None, surum_yaz=True):
    """
    Depodaki her ticker icin hesaplar, Node'un bekledigi sekli uretir ve
    GARCH uyumlari model_version tablosuna surum olarak yazilir.
    """
    kendi = depo is None
    depo = depo or S.Store()
    try:
        tickerlar = depo.gunluk_kapanisi_olan_tickerlar(asgari_gun=2)
        lookback, gozlem, uyumlar = {}, {}, 0
        egarch_uyumlari = 0
        yetersiz = {}
        for t in tickerlar:
            s = ticker_hesapla(depo, t, min_getiri)
            lookback[t] = s["models"]
            gozlem[t] = s["returns"]
            if s.get("insufficient"):
                yetersiz[t] = s["insufficient"]
            if s.get("fit"):
                uyumlar += 1
                if surum_yaz:
                    depo.model_surum_yaz(
                        "garch11", s["fit"], scope=t,
                        fit_quality=s["fit"]["loglik"],
                        meta={"returns": s["returns"], "days": s["days"]})
            if s.get("efit"):
                egarch_uyumlari += 1
                if surum_yaz:
                    depo.model_surum_yaz(
                        "egarch11", s["efit"], scope=t,
                        fit_quality=s["efit"]["loglik"],
                        meta={"returns": s["returns"], "days": s["days"]})
        return {
            "source": "store",
            "data_mode": depo.data_mode,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "min_returns": MIN_RETURNS if min_getiri is None else min_getiri,
            "fitted": uyumlar,
            "fitted_egarch": egarch_uyumlari,
            "tickers": len(tickerlar),
            "observations": gozlem,
            "insufficient": yetersiz,
            "lookbacks": {"store": lookback},
        }
    finally:
        if kendi:
            depo.kapat()


def main():
    ap = argparse.ArgumentParser(description="Depodan GARCH/RV tahmini")
    ap.add_argument("--ticker", help="tek ticker hesapla ve yaz")
    ap.add_argument("--min-returns", type=int, help=f"GARCH esigi (varsayilan {MIN_RETURNS})")
    ap.add_argument("--out", default=CIKTI_DOSYA, help="cikti dosyasi")
    ap.add_argument("--no-version", action="store_true", help="model surumu yazma")
    a = ap.parse_args()

    depo = S.Store()
    try:
        if a.ticker:
            print(json.dumps(ticker_hesapla(depo, a.ticker.upper(), a.min_returns), indent=2))
            return

        govde = tumunu_hesapla(depo, a.min_returns, surum_yaz=not a.no_version)
        if not govde["tickers"]:
            i = depo.istatistik()
            if i["spot_days"]:
                print(f"[GARCH] {i['spot_days']} gunluk bar var ama hicbir ticker'da "
                      f"2 ayri gun yok — getiri hesaplanamiyor")
            else:
                print("[GARCH] depoda gunluk kapanis yok — once veri akisi calismali")
            return

        # Hicbir ticker uyum saglamadiysa dosya yazilmaz: bos bir "canli"
        # tahmin dosyasi, arayuzde duragan dosyanin yerini alip Fcst
        # sutununu bosaltirdi.
        if govde["fitted"] == 0:
            ornek = next(iter(govde["insufficient"].values()), "yeterli gecmis yok")
            print(f"[GARCH] hicbir ticker uyum saglamadi — {ornek}")
            print(f"[GARCH] {govde['tickers']} ticker, gunluk kapanis birikiyor; "
                  f"dosya yazilmadi (duragan tahminler korunuyor)")
            return

        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(govde, f, indent=2)
        print(f"[GARCH] {govde['fitted']}/{govde['tickers']} ticker uyumlandi "
              f"({govde['data_mode']}) -> {a.out}")
    finally:
        depo.kapat()


if __name__ == "__main__":
    main()
