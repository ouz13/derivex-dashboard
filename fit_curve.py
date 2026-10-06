#!/usr/bin/env python3
"""
Vadeli islem oranlarindan NSS faiz egrisi uydurup dashboard'a gonderir.

Dashboard tek surecli bir Node sunucusu; NSS uydurma Python tarafinda
oldugu icin egri burada hesaplanip /api/yield-curve ucuna POST edilir.

    python3 fit_curve.py            tek sefer uydurup gonder
    python3 fit_curve.py --watch    periyodik olarak tekrarla
"""

import argparse
import os
import statistics
import time

import requests

import yield_curve as YC

FRONTEND = os.environ.get("FRONTEND_BASE_URL", "http://127.0.0.1:5173")


def gozlem_noktalari():
    """
    Tum ticker'larin vadeli oranlarindan vade basina MEDYAN alir.

    Tek bir hissenin oranini kullanmak gurultuye acik olurdu; medyan,
    tek tarafli kotasyon ya da bayat fiyat gibi aykiri degerlere dayanikli.
    """
    r = requests.get(f"{FRONTEND}/api/futures-rates?all=1", timeout=10)
    r.raise_for_status()
    veri = r.json().get("rates_by_ticker") or {}

    dtm_oranlar = {}
    for _, vadeler in veri.items():
        for _, kayit in (vadeler or {}).items():
            dtm = kayit.get("dtm")
            oran = kayit.get("rate")
            if dtm and oran is not None and dtm > 0 and -1 < oran < 5:
                dtm_oranlar.setdefault(dtm, []).append(float(oran))

    return sorted(
        (dtm / 365.0, statistics.median(v))
        for dtm, v in dtm_oranlar.items() if len(v) >= 3
    ), {d: len(v) for d, v in dtm_oranlar.items()}


def bir_tur(sessiz=False):
    noktalar, sayim = gozlem_noktalari()
    if len(noktalar) < 3:
        if not sessiz:
            print(f"[EGRI] yetersiz gozlem: {len(noktalar)} vade "
                  f"(en az 3 gerekli) — vade basina ticker: {sayim}")
        return None

    fit = YC.fit_nss(noktalar)
    if not fit:
        if not sessiz:
            print("[EGRI] uydurma basarisiz")
        return None

    # Egriyi gozlem araliginin cok disina uzatmak yaniltici: 2 ay araliga
    # yayilmis 3 noktadan 2 yillik oran uretmek, modelin soyleyemeyecegi bir
    # sey soyletmektir. Ornekleme en uzun gozlemin 1.5 katiyla sinirlanir.
    en_uzun_gun = max(t for t, _ in noktalar) * 365
    tavan = en_uzun_gun * 1.5
    adaylar = [7, 14, 30, 60, 90, 120, 180, 270, 365, 545, 730]
    vadeler = [g for g in adaylar if g <= tavan]
    if not vadeler:
        vadeler = [round(en_uzun_gun)]

    yuk = {
        "params": fit,
        "observed": [{"dtm": round(t * 365), "rate": y} for t, y in noktalar],
        "curve": YC.egri_noktalari(fit, vadeler),
        "gecerli_azami_gun": round(tavan),
        "en_uzun_gozlem_gun": round(en_uzun_gun),
    }
    try:
        requests.post(f"{FRONTEND}/api/yield-curve", json=yuk, timeout=5).raise_for_status()
    except Exception as e:
        print(f"[EGRI] gonderilemedi: {type(e).__name__}: {e}")
        return fit

    if not sessiz:
        not_ = " (tam belirlenmis: RMSE uyum kalitesi degil)" if fit.get("tam_belirlenmis") else ""
        print(f"[EGRI] {fit['model']} | {len(noktalar)} vade | "
              f"RMSE={fit['rmse']*100:.3f} puan{not_}")
        print(f"       b0={fit['b0']:.4f} b1={fit['b1']:.4f} "
              f"b2={fit['b2']:.4f} b3={fit['b3']:.4f} l1={fit['l1']} l2={fit['l2']}")
    return fit


def main():
    ap = argparse.ArgumentParser(description="NSS faiz egrisi uydurucu")
    ap.add_argument("--watch", action="store_true", help="periyodik calistir")
    ap.add_argument("--interval", type=float, default=60.0)
    a = ap.parse_args()

    print(f"[EGRI] hedef: {FRONTEND}")
    if not a.watch:
        bir_tur()
        return
    while True:
        try:
            bir_tur()
        except Exception as e:
            print(f"[EGRI] hata: {type(e).__name__}: {e}")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
