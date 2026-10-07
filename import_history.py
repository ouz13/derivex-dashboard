#!/usr/bin/env python3
"""
import_history.py — tarihsel gunluk fiyat aktarimi.

NEDEN BU DOSYA VAR
------------------
Depo yalnizca ILERIYE birikiyor: ilk calistirildigi gunden bugune. Yani
dokumanin istedigi 10 yillik tarihsel seri, kod ne kadar dogru olursa
olsun hicbir zaman kendiliginden olusmayacak. GARCH 60 getirinin altinda
uyum yapmayi reddediyor, yani modeller de bekliyor.

Veriyi bu arac GETIRMEZ. IdealData IP yetkisi hala yok ve gecmis veri
disaridan gelecek. Bu arac, veri geldigi an yuklenecek BORUYU kuruyor:
bir CSV ya da JSON dosyasini dogrulayip gunluk bar tablosuna yaziyor.

AKTARIM NEYE DIKKAT EDER
------------------------
1. MOCK/LIVE etiketi acikca istenir, varsayilani YOKTUR. Deponun tum
   tasarimi uretilmis fiyatlarin piyasa fiyati gibi gorunmemesine
   dayaniyor; bir aktarimda bu etiketi tahmin etmek o korumayi tek
   hamlede bosa cikarirdi.
2. Gun/ay sirasi TAHMIN EDILMEZ. 03/04/2016 hem 3 Nisan hem 4 Mart
   olabilir. Dosyadan cikarilabiliyorsa cikarilir, cikarilamiyorsa
   aktarim durur ve --date-format istenir. 10 yillik bir seriyi gun ve
   ayi ters cevrilmis halde yuklemek, hicbir hata vermeden her
   volatilite sayisini bozardi.
3. Tutarsiz bar REDDEDILIR, duzeltilmez. high < low olan bir satir
   bozuk veridir; "duzeltmek" bozuklugu gizlemek olur.
4. Bugunun bari varsayilan olarak ATLANIR. O bar hala olusuyor; tek
   satirla ezmek gun ici biriken gercek gozlemi silerdi.
5. Yeniden calistirmak guvenlidir (idempotent).

KULLANIM
--------
    python3 import_history.py gecmis.csv --data-mode LIVE
    python3 import_history.py gecmis.csv --data-mode LIVE --dry-run
    python3 import_history.py gecmis.json --data-mode MOCK --report-gaps
    python3 import_history.py veri.csv --data-mode LIVE --map "Fiyat=close"
    python3 import_history.py veri.csv --data-mode LIVE --ticker THYAO

Dosya bicimi — baslik adlari esnek (tarih/date, kapanis/close, ...):

    date,ticker,close
    2016-01-04,THYAO,7.42

open/high/low verilmezse close'dan doldurulur ve bar o gun icin tek
noktaya iner; gerceklesmis volatilite gun ici araligi degil kapanis
getirilerini kullandigi icin bu kabul edilebilir, ama bar "yuksek/dusuk"
bilgisi tasimaz.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feed_schema as fs                                      # noqa: E402

# garch.py'nin esigi. Aktarim sonrasi hangi tickerin model esigini
# gectigini soylemek, "yukledim ama ne oldu" sorusunu ortadan kaldiriyor.
GARCH_ASGARI_GETIRI = int(os.environ.get("GARCH_MIN_RETURNS", "60"))


# ---------------------------------------------------------------------------
# Tarih ayristirma
# ---------------------------------------------------------------------------

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_YYYYMMDD = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_AYIRICILI = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})")


def sira_bul(ham_tarihler):
    """
    Gun/ay sirasini dosyadan cikarmaya calisir.

    doner: ('dmy'|'mdy'|'iso'|None, aciklama)

    'iso'  -> tum tarihler YYYY-MM-DD ya da YYYYMMDD, belirsizlik yok
    'dmy'  -> en az bir satirda ilk bilesen > 12
    'mdy'  -> en az bir satirda ikinci bilesen > 12
    None   -> belirsiz ya da celiskili; cagiran durmali

    Bu is TAHMINE birakilamaz. Gun ve ayi ters cevrilmis bir seri
    yuklenirse hicbir hata cikmaz: tarihler gecerli, fiyatlar gecerli,
    yalnizca siralama ve dolayisiyla her getiri yanlis olur.
    """
    ayirici_var = False
    ilk_buyuk = ikinci_buyuk = 0
    for h in ham_tarihler:
        s = str(h).strip()
        if _ISO.match(s) or _YYYYMMDD.match(s):
            continue
        m = _AYIRICILI.match(s)
        if not m:
            continue
        ayirici_var = True
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            ilk_buyuk += 1
        if b > 12:
            ikinci_buyuk += 1

    if not ayirici_var:
        return "iso", "all dates unambiguous (ISO)"
    if ilk_buyuk and ikinci_buyuk:
        return None, (f"conflicting date order: {ilk_buyuk} row(s) imply "
                      f"day-first, {ikinci_buyuk} imply month-first")
    if ilk_buyuk:
        return "dmy", f"day-first inferred from {ilk_buyuk} row(s) with day > 12"
    if ikinci_buyuk:
        return "mdy", f"month-first inferred from {ikinci_buyuk} row(s) with month > 12"
    return None, ("ambiguous date order: no row has a component above 12, "
                  "so DD/MM and MM/DD cannot be told apart")


def tarih_coz(ham, sira):
    """'YYYY-MM-DD' ya da None."""
    s = str(ham or "").strip()
    if not s:
        return None
    # Zaman damgasi ise gun kismini al.
    m = _ISO.match(s)
    if m:
        y, ay, g = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _YYYYMMDD.match(s)
        if m:
            y, ay, g = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = _AYIRICILI.match(s)
            if not m:
                return None
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if sira == "mdy":
                ay, g = a, b
            else:
                g, ay = a, b
    try:
        return date(y, ay, g).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Dosya okuma
# ---------------------------------------------------------------------------

def satirlari_oku(yol, ek_harita=None):
    """
    doner: (satir_sozlukleri, bicim)

    Satirlar kanonik ANAHTARLARA cevrilmis halde doner (ticker, date,
    open, high, low, close); degerler hala ham metin.
    """
    with open(yol, "r", encoding="utf-8-sig", errors="replace") as f:
        icerik = f.read()
    if not icerik.strip():
        raise SystemExit("hata: dosya bos: " + yol)

    uzanti = os.path.splitext(yol)[1].lower()
    ilk = icerik.lstrip()[:1]
    if uzanti == ".json" or ilk in ("[", "{"):
        return _json_satirlari(icerik, ek_harita), "json"
    return _csv_satirlari(icerik, ek_harita), "csv"


def _json_satirlari(icerik, ek_harita):
    try:
        veri = json.loads(icerik)
    except Exception as e:
        raise SystemExit("hata: gecersiz JSON: " + str(e))
    if isinstance(veri, dict):
        # {"rows": [...]} ya da {"THYAO": [...]} ikisi de kabul.
        for anahtar in ("rows", "data", "bars", "history"):
            if isinstance(veri.get(anahtar), list):
                veri = veri[anahtar]
                break
        else:
            duz = []
            for ticker, liste in veri.items():
                if isinstance(liste, list):
                    for s in liste:
                        if isinstance(s, dict):
                            s = dict(s)
                            s.setdefault("ticker", ticker)
                            duz.append(s)
            veri = duz
    if not isinstance(veri, list):
        raise SystemExit("hata: JSON bir satir listesi icermiyor")

    cikti = []
    for s in veri:
        if not isinstance(s, dict):
            continue
        eslem = fs.basliklari_esle(list(s.keys()), ek_harita)
        cikti.append({kanonik: s.get(ham) for ham, kanonik in eslem.items()})
    return cikti


def _csv_satirlari(icerik, ek_harita):
    import csv
    import io
    okuyucu = csv.DictReader(io.StringIO(icerik))
    if not okuyucu.fieldnames:
        raise SystemExit("hata: CSV basligi okunamadi")
    eslem = fs.basliklari_esle(okuyucu.fieldnames, ek_harita)
    eksik = {"date", "close"} - set(eslem.values())
    if eksik:
        raise SystemExit(
            "hata: zorunlu kolon(lar) bulunamadi: " + ", ".join(sorted(eksik))
            + "\n       okunan basliklar: " + ", ".join(okuyucu.fieldnames)
            + "\n       --map ile elle eslenebilir, ornek: --map \"Fiyat=close\"")
    return [{kanonik: s.get(ham) for ham, kanonik in eslem.items()}
            for s in okuyucu]


# ---------------------------------------------------------------------------
# Dogrulama
# ---------------------------------------------------------------------------

def barlari_dogrula(satirlar, sira, sabit_ticker=None, bugune_izin=False,
                    bugun=None):
    """
    doner: (barlar, red_sayaci, cakisma_sayisi)

    barlar: [(gun, ticker, open, high, low, close)]
    """
    bugun = bugun or date.today()
    kabul = {}
    red = Counter()
    cakisan = set()

    for s in satirlar:
        t = fs._ticker(sabit_ticker or s.get("ticker"))
        if t is None:
            red["invalid or missing ticker"] += 1
            continue

        gun = tarih_coz(s.get("date") or s.get("ts"), sira)
        if gun is None:
            red["invalid or missing date"] += 1
            continue
        if gun > bugun.isoformat():
            red["date in the future"] += 1
            continue
        if not bugune_izin and gun == bugun.isoformat():
            # Bugunun bari hala olusuyor; tek satirla ezmek gun ici
            # biriken gercek gozlemi silerdi.
            red["today's bar skipped (--allow-today to include)"] += 1
            continue

        kapanis = fs._pozitif(s.get("close"))
        if kapanis is None:
            red["missing or non-positive close"] += 1
            continue

        acilis = fs._pozitif(s.get("open")) or kapanis
        yuksek = fs._pozitif(s.get("high")) or max(acilis, kapanis)
        dusuk = fs._pozitif(s.get("low")) or min(acilis, kapanis)

        # Tutarsiz bar duzeltilmez, reddedilir: high<low bozuk veridir ve
        # "duzeltmek" bozuklugu gizlemek olur.
        if yuksek < dusuk:
            red["high below low"] += 1
            continue
        if not (dusuk <= acilis <= yuksek) or not (dusuk <= kapanis <= yuksek):
            red["open/close outside high-low range"] += 1
            continue

        anahtar = (t, gun)
        if anahtar in kabul:
            # Ayni ticker/gun birden fazla satirda: hangisinin dogru
            # oldugu bilinemez, hepsi atilir. "Son satir kazanir" demek,
            # dosyadaki siraya guvenmek olurdu.
            cakisan.add(anahtar)
            continue
        kabul[anahtar] = (gun, t, acilis, yuksek, dusuk, kapanis)

    cakisma = len(cakisan)
    if cakisma:
        red["duplicate ticker/date (all dropped)"] += cakisma
        for anahtar in cakisan:
            kabul.pop(anahtar, None)

    barlar = list(kabul.values())
    barlar.sort(key=lambda b: (b[1], b[0]))
    return barlar, red, cakisma


# ---------------------------------------------------------------------------
# Bosluk raporu
# ---------------------------------------------------------------------------

def bosluklar(gunler):
    """
    Kayitli gunler arasindaki eksik IS gunlerini sayar.

    Resmi tatiller de "eksik" gorunur; bu arac tatil takvimi bilmiyor.
    Dolayisiyla bu sayi bir UST SINIR, hata degil: Turkiye'de yilda
    ~15 is gunu resmi tatil, yani 10 yilda ~150 bosluk NORMALDIR.
    """
    if len(gunler) < 2:
        return {"missing_business_days": 0, "longest_run": 0, "examples": []}
    var = set(gunler)
    ilk = date.fromisoformat(min(gunler))
    son = date.fromisoformat(max(gunler))
    eksik = []
    g = ilk
    while g <= son:
        if g.weekday() < 5 and g.isoformat() not in var:
            eksik.append(g.isoformat())
        g += timedelta(days=1)

    # En uzun kesintisiz bosluk — tek tatil gunleriyle veri kaybini
    # ayirt etmeye yariyor.
    en_uzun = 0
    sayac = 0
    onceki = None
    for e in eksik:
        d = date.fromisoformat(e)
        if onceki is not None and (d - onceki).days <= 3:
            sayac += 1
        else:
            sayac = 1
        en_uzun = max(en_uzun, sayac)
        onceki = d

    return {"missing_business_days": len(eksik), "longest_run": en_uzun,
            "examples": eksik[:5]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _harita_coz(ciftler):
    out = {}
    for c in (ciftler or []):
        if "=" not in c:
            raise SystemExit("hata: --map bicimi 'kolon=kanonik' olmali: " + c)
        ham, kanonik = c.split("=", 1)
        if kanonik not in fs.BASLIK_ESLERI:
            raise SystemExit("hata: bilinmeyen kanonik ad: " + kanonik
                             + "\n       gecerli: " + ", ".join(sorted(fs.BASLIK_ESLERI)))
        out[ham] = kanonik
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Tarihsel gunluk fiyat aktarimi (CSV/JSON -> spot_daily)")
    ap.add_argument("dosya", help="CSV ya da JSON dosyasi")
    ap.add_argument("--data-mode", required=True, choices=["LIVE", "MOCK"],
                    help="ZORUNLU. Aktarilan verinin etiketi. Gercek piyasa "
                         "verisi LIVE, uretilmis veri MOCK. Varsayilani yok: "
                         "yanlis etiket, uretilmis fiyatlarin piyasa fiyati "
                         "gibi gorunmesine yol acar.")
    ap.add_argument("--dry-run", action="store_true",
                    help="dogrula ve raporla, yazma")
    ap.add_argument("--ticker", help="dosyada ticker kolonu yoksa hepsi buna yazilir")
    ap.add_argument("--date-format", choices=["dmy", "mdy", "iso"],
                    help="gun/ay sirasi dosyadan cikarilamazsa")
    ap.add_argument("--map", action="append", metavar="KOLON=ALAN",
                    help="baslik eslemesini elle ver (tekrarlanabilir)")
    ap.add_argument("--overwrite", action="store_true",
                    help="gun ici tick'ten olusmus barlari da ez")
    ap.add_argument("--allow-today", action="store_true",
                    help="bugunun barini da aktar (olusmakta olan bar ezilir)")
    ap.add_argument("--report-gaps", action="store_true",
                    help="aktarim sonrasi eksik is gunu raporu")
    ap.add_argument("--db", help="veritabani yolu (varsayilan: derivex.db)")
    a = ap.parse_args(argv)

    if not os.path.exists(a.dosya):
        raise SystemExit("hata: dosya yok: " + a.dosya)

    ek = _harita_coz(a.map)
    satirlar, bicim = satirlari_oku(a.dosya, ek)
    if not satirlar:
        raise SystemExit("hata: dosyada satir yok")

    # --- gun/ay sirasi ---
    ham_tarihler = [s.get("date") or s.get("ts") for s in satirlar]
    if a.date_format:
        sira, aciklama = a.date_format, "given on the command line"
    else:
        sira, aciklama = sira_bul(ham_tarihler)
        if sira is None:
            print("hata: tarih sirasi belirlenemedi — " + aciklama, file=sys.stderr)
            print("      --date-format dmy  (03/04/2016 = 3 Nisan)", file=sys.stderr)
            print("      --date-format mdy  (03/04/2016 = 4 Mart)", file=sys.stderr)
            print("      Yanlis secim 10 yillik seriyi sessizce bozar: tarihler",
                  file=sys.stderr)
            print("      gecerli kalir, yalnizca her getiri yanlis olur.",
                  file=sys.stderr)
            return 2

    print(f"dosya      : {a.dosya}  ({bicim}, {len(satirlar)} satir)")
    print(f"tarih sirasi: {sira}  ({aciklama})")
    print(f"etiket     : {a.data_mode}")

    barlar, red, _ = barlari_dogrula(satirlar, sira, a.ticker, a.allow_today)

    print(f"\ndogrulama  : {len(barlar)} kabul, {sum(red.values())} red")
    for sebep, n in red.most_common():
        print(f"             {n:6d}x {sebep}")
    if not barlar:
        print("\nyazilacak bar yok.")
        return 1

    tickerlar = sorted({b[1] for b in barlar})
    print(f"\nticker     : {len(tickerlar)} ({', '.join(tickerlar[:8])}"
          f"{' ...' if len(tickerlar) > 8 else ''})")
    print(f"tarih      : {min(b[0] for b in barlar)} .. {max(b[0] for b in barlar)}")

    if a.dry_run:
        print("\n--dry-run: hicbir sey yazilmadi.")
        _esik_raporu(barlar)
        return 0

    # --- yazim ---
    import store as st
    if not st.STORE_ENABLED:
        # _yaz bu durumda sessizce False donuyor; "0 bar yazildi" diye
        # raporlamak sebebi gizlerdi.
        print("\nhata: STORE_ENABLED=0 — depo yazimi kapali, aktarim yapilamaz.",
              file=sys.stderr)
        return 1
    depo = st.Store(yol=a.db, data_mode=a.data_mode)
    try:
        sonuc = depo.gunluk_barlari_aktar(barlar, uzerine_yaz=a.overwrite)
        print(f"\nyazilan    : {sonuc['yazilan']} bar")
        if sonuc.get("hata"):
            print(f"HATA       : {sonuc['hata']}", file=sys.stderr)
            return 1
        if sonuc["korunan"]:
            print(f"korunan    : {sonuc['korunan']} bar (gun ici tick'ten "
                  f"olusmus, --overwrite ile ezilir)")
            for t, g in sonuc["korunan_ornekler"]:
                print(f"             {t} {g}")

        ozet = depo.gunluk_ozet()
        if ozet:
            print("\ndepodaki durum (" + a.data_mode + "):")
            for o in ozet:
                isaret = "OK " if o["days"] - 1 >= GARCH_ASGARI_GETIRI else "AZ "
                print(f"  {isaret}{o['ticker']:8s} {o['days']:6d} gun  "
                      f"{o['first']} .. {o['last']}  (aktarilan {o['imported']})")
            az = [o["ticker"] for o in ozet
                  if o["days"] - 1 < GARCH_ASGARI_GETIRI]
            print(f"\n  OK = GARCH esigi gecildi ({GARCH_ASGARI_GETIRI} getiri)")
            if az:
                print(f"  AZ = esigin altinda, model uyumu REDDEDILIR: "
                      f"{', '.join(az[:10])}")

        if a.report_gaps:
            print("\nbosluk raporu (resmi tatiller de eksik gorunur):")
            for t in tickerlar:
                b = bosluklar(depo.gunler(t))
                if b["missing_business_days"]:
                    print(f"  {t:8s} {b['missing_business_days']:5d} is gunu eksik, "
                          f"en uzun kesinti {b['longest_run']} gun"
                          f"   ilk: {', '.join(b['examples'][:3])}")
                else:
                    print(f"  {t:8s} eksik is gunu yok")
            print("  Turkiye'de yilda ~15 is gunu resmi tatil; 10 yilda ~150")
            print("  bosluk NORMALDIR. Uzun kesintiler veri kaybi isaretidir.")
    finally:
        depo.kapat()
    return 0


def _esik_raporu(barlar):
    sayim = defaultdict(int)
    for b in barlar:
        sayim[b[1]] += 1
    az = [t for t, n in sayim.items() if n - 1 < GARCH_ASGARI_GETIRI]
    print(f"\nGARCH esigi ({GARCH_ASGARI_GETIRI} getiri):")
    print(f"  gecen : {len(sayim) - len(az)}/{len(sayim)} ticker")
    if az:
        print(f"  altta : {', '.join(sorted(az)[:10])}")


if __name__ == "__main__":
    sys.exit(main())
