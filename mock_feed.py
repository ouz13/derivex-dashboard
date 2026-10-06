#!/usr/bin/env python3
"""
mock_feed.py — uretilmis ornek veriyi dashboard'a besler.

IdealData erisimi olmadan arayuzu dolu gormek icin kullanilir
(DATA_MODE=0). Canli veri icin bunun yerine bridge_stream.py calisir.

Uretilen veri bridge_stream.py'nin kendi kurucularindan gecer; yani
dashboard'a giden JSON'un sekli canli moddakiyle BIREBIR aynidir.
Boylece mock modda calisan bir ekran, canli moda gecince de calisir.

    python3 mock_feed.py
    python3 mock_feed.py --once        tek tur besleyip cik
"""

import argparse
import math
import random
import time
from datetime import datetime

import bridge_stream as B

# Gercek bir BIST seansindan alinmis referans seviyeleri. Mock fiyatlar
# bunlarin etrafinda yurur, boylece tablolar inandirici gorunur.
TEMEL_FIYAT = {
    "AEFES": 17.64, "AKBNK": 69.15, "AKSEN": 66.29, "ALARK": 102.40,
    "ARCLK": 83.31, "ASELS": 355.56, "ASTOR": 238.97, "BIMAS": 416.75,
    "BRSAN": 621.06, "CIMSA": 41.84, "DOAS": 148.03, "DOHOL": 19.56,
    "EKGYO": 19.00, "ENJSA": 104.32, "ENKAI": 84.25, "EREGL": 36.25,
    "FROTO": 73.29, "GARAN": 126.94, "GUBRF": 433.00, "HALKB": 43.48,
    "HEKTS": 2.09, "ISCTR": 12.68, "KCHOL": 210.39, "KRDMD": 46.24,
    "MGROS": 514.00, "ODAS": 6.70, "OYAKC": 19.81, "PETKM": 20.50,
    "PGSUS": 141.72, "SAHOL": 85.60, "SASA": 1.94, "SISE": 37.93,
    "SOKM": 48.20, "TAVHL": 264.58, "TCELL": 97.65, "THYAO": 281.57,
    "TKFEN": 264.06, "TOASO": 279.75, "TRALT": 46.12, "TRMET": 131.19,
    "TSKB": 9.90, "TTKOM": 52.44, "TUPRS": 392.69, "ULKER": 84.82,
    "VAKBN": 33.28, "VESTL": 19.23, "YKBNK": 34.81,
}

# _frontend_runtime.js icindeki optionTickers listesiyle ayni olmali;
# aksi halde arayuzde secilebilen ama verisi olmayan ticker'lar olusur.
OPSIYON_TICKERS = [
    "AKBNK", "ALARK", "ARCLK", "ASELS", "BIMAS", "EKGYO", "ENKAI", "EREGL",
    "FROTO", "GARAN", "HALKB", "ISCTR", "KCHOL", "KRDMD", "PETKM", "PGSUS",
    "SAHOL", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO", "TTKOM", "TUPRS",
    "VAKBN", "YKBNK",
]

# Hisse disi dayanaklarin referans seviyeleri (endeks / doviz / emtia).
# Gercek piyasa buyukluk mertebelerine yakin secildi ki tablo inandirici
# gorunsun; bunlarin spot kotasyonu canli akista da yok, bu yuzden mock
# tarafta da yalnizca vadeli fiyat uretiliyor.
DIGER_TEMEL = {
    "XU030":   (11_250.0, "index"),
    "X10XB":   (18_400.0, "index"),
    "USDTRY":  (41.85, "fx"),
    "CNHTRY":  (5.78, "fx"),
    "XAUUSD":  (4_015.0, "commodity"),
    "XAGUSD":  (48.20, "commodity"),
    "XAUTRYM": (5_320.0, "commodity"),
    "XPTUSD":  (1_585.0, "commodity"),
    "XPDUSD":  (1_430.0, "commodity"),
}

YILLIK_FAIZ = 0.30          # vadeli fiyatlarin ima edecegi getiri
SPOT_SPREAD = 0.0015        # spot alis-satis araligi (%0.15)
FUT_SPREAD = 0.0045         # vadeli spread daha genis; bid/ask getirilerinin
                            # ayrismasi bu farktan dogar
OPT_SPREAD = 0.025          # opsiyon spread'i prim uzerinden (%2.5)
ADIM_ORANI = 0.0008         # tur basina fiyat yurumesi


def yuvarla(x, basamak=2):
    return round(x + 1e-12, basamak)


def sabit_tohum(metin):
    """
    Metinden sabit bir sayi uretir.

    Python'un yerlesik hash()'i surec basina rastgelelestirilir; onunla
    uretilen volatilite her calistirmada degisir ve tablolar ziplar.
    """
    t = 0
    for ch in metin:
        t = (t * 31 + ord(ch)) % 999983
    return t


def spot_uret(durum):
    """Her tur fiyatlari kucuk bir rassal adimla yurutur."""
    for t, taban in TEMEL_FIYAT.items():
        onceki = durum.get(t, taban)
        yeni = onceki * (1 + random.gauss(0, ADIM_ORANI))
        # referanstan %6'dan fazla uzaklasmasin
        yeni = max(taban * 0.94, min(taban * 1.06, yeni))
        durum[t] = yeni
    return durum


def taraflar(mid, oran=SPOT_SPREAD):
    yari = mid * oran / 2
    return yuvarla(mid - yari), yuvarla(mid + yari)


def kullanim_fiyatlari(spot, adet=9):
    """Spot etrafinda makul araliklarla kullanim fiyati uretir."""
    if spot >= 300:   adim = 10
    elif spot >= 100: adim = 5
    elif spot >= 30:  adim = 2
    elif spot >= 10:  adim = 1
    else:             adim = 0.5
    orta = round(spot / adim) * adim
    return [yuvarla(orta + (i - adet // 2) * adim) for i in range(adet)]


def opsiyon_uret(ticker, spot, maturities):
    """
    Opsiyon kayitlarini _parse_option_symbol'un dondurdugu sekilde uretir;
    fiyatlar Black-Scholes'tan gelir, boylece zincir kendi icinde tutarlidir
    ve geri hesaplanan zimni volatilite anlamli cikar.
    """
    out = {}
    for m in maturities:
        dtm = m["dtm"]
        if dtm <= 0:
            continue
        t = dtm / 365.0
        taban_vol = 0.30 + (sabit_tohum(ticker) % 20) / 100.0   # %30-50, tickera sabit
        for k in kullanim_fiyatlari(spot):
            # Volatilite gulumsemesi: para-disi kullanim fiyatlarinda IV yukselir,
            # negatif carpiklik ile dusuk strike tarafi daha pahalidir. Duz bir
            # volatilite kullanilirsa Volatilite Egrisi sekmesi duz cizgi gosterir.
            moneyness = k / spot - 1.0
            vol = taban_vol + 0.45 * moneyness * moneyness - 0.12 * moneyness
            for tip in ("C", "P"):
                teorik = B._bs_price(tip, spot, k, YILLIK_FAIZ, t, vol)
                if teorik is None or teorik < 0.01:
                    continue
                # Spread hem oransal hem asgari kurus bazli: ucuz opsiyonlarda
                # salt oransal spread zimni volatiliteyi asiri genisletiyor.
                yari = max(teorik * OPT_SPREAD / 2, 0.01)
                bid, ask = yuvarla(teorik - yari), yuvarla(teorik + yari)
                if ask <= bid:
                    ask = yuvarla(bid + 0.01)
                sembol = f"O_{ticker}E{m['code']}{tip}{k:.2f}"
                out[sembol] = {
                    "ticker": ticker, "expiry": m["code"], "option_type": tip,
                    "strike": float(k), "bid_price": bid, "ask_price": ask,
                    "bid_size": random.randint(10, 400),
                    "ask_size": random.randint(10, 400),
                }
    return out


def bir_tur(maturities, fut_map, durum, sessiz=False):
    spot_uret(durum)

    spot_mid, spot_bid, spot_ask = {}, {}, {}
    for t, mid in durum.items():
        b, a = taraflar(mid)
        spot_mid[t], spot_bid[t], spot_ask[t] = yuvarla(mid), b, a

    fut_mid, fut_bid, fut_ask = {}, {}, {}
    for sembol, meta in fut_map.items():
        s = spot_mid.get(meta["underlying"])
        if s is None:
            continue
        # Hedeflenen yillik getiriyi ima eden vadeli fiyat
        f = s * (1 + YILLIK_FAIZ * meta["dtm"] / 365.0) * (1 + random.gauss(0, 0.0012))
        b, a = taraflar(f, FUT_SPREAD)
        fut_mid[sembol], fut_bid[sembol], fut_ask[sembol] = yuvarla(f), b, a

    rates = B._build_rates_snapshot(
        maturities, fut_map, spot_mid, fut_mid,
        spot_bid=spot_bid, spot_ask=spot_ask, fut_bid=fut_bid, fut_ask=fut_ask)

    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    # Toplu gonderim: canli kopru de ayni yolu kullaniyor, mock'un
    # ondan farkli davranmasi icin sebep yok.
    spot_n = 0
    try:
        B.post_spot_batch(spot_mid, ts)
        spot_n = len(spot_mid)
    except Exception:
        pass
    try:
        B.post_futures_rates_batch(rates, maturities, ts)
    except Exception:
        pass

    opt_n = 0
    for t in OPSIYON_TICKERS:
        s = spot_mid.get(t)
        if s is None:
            continue
        kayitlar = opsiyon_uret(t, s, maturities)
        zincir = B._build_enriched_options(kayitlar, t, maturities, spot_mid, rates)
        if zincir:
            try:
                B.post_options_chain(t, zincir, ts)
                opt_n += len(zincir)
            except Exception:
                pass

    # Hisse disi vadeliler (endeks / doviz / emtia). Canli tarafta
    # oldugu gibi burada da ima edilen getiri uretilmiyor: spot yok.
    diger = {}
    for dayanak, (taban, sinif) in DIGER_TEMEL.items():
        seviye = durum.setdefault("_d_" + dayanak, taban)
        seviye *= (1 + random.gauss(0, ADIM_ORANI))
        seviye = max(taban * 0.94, min(taban * 1.06, seviye))
        durum["_d_" + dayanak] = seviye
        basamak = 2 if seviye >= 100 else 4
        vadeler = []
        for m in maturities:
            if m["dtm"] <= 0:
                continue
            f = seviye * (1 + YILLIK_FAIZ * m["dtm"] / 365.0) * (1 + random.gauss(0, 0.0012))
            yari = f * FUT_SPREAD / 2
            vadeler.append({
                "code": m["code"], "label": m["label"], "dtm": m["dtm"],
                "bid": yuvarla(f - yari, basamak),
                "ask": yuvarla(f + yari, basamak),
                "mid": yuvarla(f, basamak),
            })
        if vadeler:
            diger[dayanak] = {"underlying": dayanak, "asset_class": sinif,
                              "maturities": vadeler}
    if diger:
        try:
            B.post_other_assets(diger, ts)
        except Exception:
            pass

    # Tur sonunda anlik goruntuyu zorla yaz: aralikli yazim tek turluk
    # calistirmada (--once) yarim dolu bir goruntu birakirdi.
    try:
        B._snapshot_bastir(zorla=True)
    except Exception:
        pass

    if not sessiz:
        print(f"[MOCK] {ts}  spot={spot_n}  vadeli={len(fut_mid)}  "
              f"opsiyon_satir={opt_n}  diger_varlik={len(diger)}", flush=True)
    return spot_n, len(fut_mid), opt_n


def main():
    ap = argparse.ArgumentParser(description="Dashboard'a uretilmis veri besler")
    ap.add_argument("--once", action="store_true", help="tek tur besleyip cik")
    ap.add_argument("--interval", type=float, default=3.0, help="tur araligi (sn)")
    a = ap.parse_args()

    random.seed(42)
    maturities, fut_map = B.build_futures_symbol_map()

    print("[MOCK] Uretilmis veri besleniyor — bu veri piyasa verisi DEGILDIR.")
    print(f"[MOCK] Hedef: {B.FRONTEND_BASE_URL}")
    vadeler = ", ".join("{}(DTM={})".format(m["code"], m["dtm"]) for m in maturities)
    print("[MOCK] Vadeler: " + vadeler)

    durum = {}
    if a.once:
        bir_tur(maturities, fut_map, durum)
        return
    while True:
        bir_tur(maturities, fut_map, durum)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
