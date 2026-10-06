#!/usr/bin/env python3
"""
backtest.py — kayan kokenli capraz dogrulama.

NEDEN BU DOSYA VAR
Artik dort tahmin yontemi var (Realized, EWMA, GARCH, EGARCH) ama
hangisinin DAHA IYI oldugunu bilmiyoruz. EGARCH'in asimetrik veride
ustun oldugunu sentetik seride gostermistik; gercek veride tutup
tutmadigi olculmedi. Kalibrasyon anindaki RMSE de bunu soylemez: o,
modelin gecmise ne kadar iyi UYDUGUNU olcer, ileriyi ne kadar iyi
TAHMIN ettigini degil.

YONTEM — KAYAN KOKEN
    [-------- egitim --------][-- ufuk --]
                              ^ koken
    Koken ileri kayar, her adimda model YALNIZCA kokene kadarki veriyle
    yeniden uydurulur ve sonraki `ufuk` gunun gerceklesmis volatilitesi
    tahmin edilir. Modelin gelecegi gormemesi bu yeniden uydurmaya
    bagli; tek sefer uydurup tum gecmisi test etmek sizinti olurdu.

SAF DAYANAK (naive baseline)
    "Son `ufuk` gunun gerceklesmis volatilitesi, sonraki `ufuk` gun de
    aynen surecek" varsayimi. Modeller bunu GECEMIYORSA karmasikligin
    bir karsiligi yok demektir — bu yuzden karsilastirmaya dahil.

KULLANIM
    python3 backtest.py                    depodaki tum ticker'lar
    python3 backtest.py --ticker THYAO     tek ticker
    python3 backtest.py --ufuk 15          tahmin ufku (gun)
    python3 backtest.py --koken 10         kac koken denensin
"""

import argparse
import json
import math
import os
from datetime import datetime, timezone

import garch as G
import store as S

# Her kokende yeniden uydurma gerektigi icin koken sayisi maliyeti
# dogrudan belirler. 12 koken x 2 model, ticker basina birkac saniye.
VARSAYILAN_KOKEN = 12
VARSAYILAN_UFUK = 30
# Egitim penceresi: GARCH esiginin altina dusmemeli, yoksa uydurucu
# zaten None doner ve koken bosa gider.
VARSAYILAN_MIN_EGITIM = 80

MODELLER = ("Naive", "EWMA", "GARCH(1,1)", "EGARCH(1,1)")

CIKTI_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "backtest_results.json")


def gerceklesen_vol(getiriler, bas, ufuk):
    """[bas, bas+ufuk) araligindaki yillik gerceklesmis volatilite."""
    dilim = getiriler[bas:bas + ufuk]
    if len(dilim) < max(2, ufuk // 2):
        return None                       # pencere yarisindan az doluysa guvenilmez
    return G.gerceklesmis_vol(dilim)


def koken_tahminleri(getiriler, koken, ufuk, min_getiri):
    """
    Tek bir kokende her modelin tahminini uretir.

    Model yalnizca [0, koken) verisiyle uydurulur; sonrasi gorulmez.
    """
    egitim = getiriler[:koken]
    out = {}

    # Saf dayanak: son `ufuk` gunun volatilitesi aynen surer
    out["Naive"] = G.gerceklesmis_vol(egitim, ufuk)

    out["EWMA"] = G.ewma_vol(egitim)

    f = G.fit_garch11(egitim, min_getiri)
    out["GARCH(1,1)"] = G.garch_tahmin(f, egitim, ufuk) if f else None

    ef = G.fit_egarch11(egitim, min_getiri)
    out["EGARCH(1,1)"] = (G.egarch_tahmin(ef, egitim, [ufuk]) or {}).get(ufuk) if ef else None

    return out


def dogrula(getiriler, ufuk=VARSAYILAN_UFUK, koken_sayisi=VARSAYILAN_KOKEN,
            min_egitim=VARSAYILAN_MIN_EGITIM, min_getiri=None):
    """
    Kayan kokenli capraz dogrulama.

    Dondurur: {model: {rmse, mae, n, bias}} ve kullanilan koken sayisi.
    Yetersiz veride None.
    """
    n = len(getiriler)
    # Son koken, ufuk kadar ileriyi dogrulayabilmeli
    son_koken = n - ufuk
    if son_koken <= min_egitim:
        return None, 0

    adim = max(1, (son_koken - min_egitim) // koken_sayisi)
    kokenler = list(range(min_egitim, son_koken + 1, adim))[:koken_sayisi]
    if not kokenler:
        return None, 0

    hatalar = {m: [] for m in MODELLER}
    kullanilan = 0

    for koken in kokenler:
        gercek = gerceklesen_vol(getiriler, koken, ufuk)
        if gercek is None:
            continue
        tahminler = koken_tahminleri(getiriler, koken, ufuk, min_getiri)
        if all(v is None for v in tahminler.values()):
            continue
        kullanilan += 1
        for m in MODELLER:
            t = tahminler.get(m)
            if t is not None:
                hatalar[m].append(t - gercek)

    sonuc = {}
    for m in MODELLER:
        h = hatalar[m]
        if not h:
            sonuc[m] = None
            continue
        sonuc[m] = {
            "rmse": math.sqrt(sum(x * x for x in h) / len(h)),
            "mae": sum(abs(x) for x in h) / len(h),
            # Yanlilik: pozitifse model sistematik olarak YUKSEK tahmin
            # ediyor. RMSE tek basina bunu gizler.
            "bias": sum(h) / len(h),
            "n": len(h),
        }
    return sonuc, kullanilan


def ticker_dogrula(depo, ticker, **kw):
    getiriler = G.log_getiriler(depo.gunluk_kapanislar(ticker))
    sonuc, koken = dogrula(getiriler, **kw)
    return {"ticker": ticker, "returns": len(getiriler),
            "origins": koken, "scores": sonuc}


def _en_iyi(skorlar, olcu="rmse"):
    """En dusuk hatali modelin adi."""
    uygun = [(m, s[olcu]) for m, s in (skorlar or {}).items() if s]
    return min(uygun, key=lambda x: x[1])[0] if uygun else None


def main():
    ap = argparse.ArgumentParser(description="Kayan kokenli capraz dogrulama")
    ap.add_argument("--ticker", help="tek ticker")
    ap.add_argument("--ufuk", type=int, default=VARSAYILAN_UFUK, help="tahmin ufku (gun)")
    ap.add_argument("--koken", type=int, default=VARSAYILAN_KOKEN, help="koken sayisi")
    ap.add_argument("--min-egitim", type=int, default=VARSAYILAN_MIN_EGITIM)
    ap.add_argument("--min-returns", type=int, help="GARCH esigi")
    ap.add_argument("--out", default=CIKTI_DOSYA)
    ap.add_argument("--no-version", action="store_true", help="depoya yazma")
    a = ap.parse_args()

    depo = S.Store()
    try:
        tickerlar = ([a.ticker.upper()] if a.ticker
                     else depo.gunluk_kapanisi_olan_tickerlar(asgari_gun=2))
        if not tickerlar:
            print("[BACKTEST] depoda gunluk kapanis yok")
            return

        kw = dict(ufuk=a.ufuk, koken_sayisi=a.koken,
                  min_egitim=a.min_egitim, min_getiri=a.min_returns)

        print(f"\n  CAPRAZ DOGRULAMA  (ufuk {a.ufuk} gun, en fazla {a.koken} koken)")
        print("  " + "=" * 66)

        tumu = {}
        atlanan = []
        for t in tickerlar:
            s = ticker_dogrula(depo, t, **kw)
            if not s["scores"] or not s["origins"]:
                atlanan.append((t, s["returns"]))
                continue
            tumu[t] = s

            print(f"\n  {t}   ({s['returns']} getiri, {s['origins']} koken)")
            print(f"    {'model':<14}{'RMSE':>9}{'MAE':>9}{'yanlilik':>11}")
            print("    " + "-" * 43)
            en_iyi = _en_iyi(s["scores"])
            for m in MODELLER:
                sk = s["scores"].get(m)
                if not sk:
                    print(f"    {m:<14}{'—':>9}{'—':>9}{'—':>11}")
                    continue
                isaret = "  <-" if m == en_iyi else ""
                print(f"    {m:<14}{sk['rmse']:>9.2f}{sk['mae']:>9.2f}"
                      f"{sk['bias']:>+11.2f}{isaret}")

        if atlanan:
            print(f"\n  Yetersiz gecmis nedeniyle atlanan: "
                  f"{', '.join(t for t, _ in atlanan[:8])}"
                  f"{' ...' if len(atlanan) > 8 else ''} ({len(atlanan)} ticker)")
            print(f"  Gereken: en az {a.min_egitim + a.ufuk + 1} getiri.")

        if not tumu:
            print("\n  Hicbir ticker dogrulanamadi — depo henuz yeterince dolu degil.")
            return

        # Genel ozet: kac ticker'da hangi model kazandi
        kazanan = {}
        for s in tumu.values():
            m = _en_iyi(s["scores"])
            if m:
                kazanan[m] = kazanan.get(m, 0) + 1
        print("\n  " + "=" * 66)
        print("  OZET — RMSE'ye gore kac ticker'da en iyi:")
        for m in MODELLER:
            print(f"    {m:<14}{kazanan.get(m, 0):>3} / {len(tumu)}")

        # Saf dayanak karsilastirmasi ayri yaziliyor: modellerin
        # karmasikliginin bir karsiligi var mi, asil soru bu.
        naive_kazanan = kazanan.get("Naive", 0)
        if naive_kazanan > len(tumu) / 2:
            print(f"\n  DIKKAT: ticker'larin cogunda SAF DAYANAK kazaniyor.")
            print(f"  Bu ufukta model karmasikligi karsiligini vermiyor.")

        govde = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_mode": depo.data_mode,
            "horizon_days": a.ufuk,
            "origins_requested": a.koken,
            "min_training": a.min_egitim,
            "tickers": tumu,
            "winners": kazanan,
        }
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(govde, f, indent=2)
        print(f"\n  -> {a.out}")

        if not a.no_version:
            # Skorlar da surumleniyor: hangi tarihte hangi modelin daha
            # iyi oldugu sonradan sorulabilsin.
            for t, s in tumu.items():
                depo.model_surum_yaz(
                    "backtest", s["scores"], scope=t,
                    fit_quality=(s["scores"].get(_en_iyi(s["scores"])) or {}).get("rmse"),
                    meta={"horizon_days": a.ufuk, "origins": s["origins"],
                          "best": _en_iyi(s["scores"])})
        print()
    finally:
        depo.kapat()


if __name__ == "__main__":
    main()
