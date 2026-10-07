#!/usr/bin/env python3
"""
feed_schema.py — ortak veri semasi ve kaynak adaptorleri.

NEDEN BU DOSYA VAR
------------------
Boru hatti bugune kadar TEK bir kaynagin tel bicimine gomuluydu:
bridge_stream.py'nin ana dongusu IdealData'nin "TIP;1=SEMBOL;6=bid;9=ask|"
cercevelerini dogrudan okuyup ayni yerde is mantigini yuruttuyor. Bu
calisiyor, ama ikinci bir kaynak eklemek demek o donguyu bastan yazmak
demekti: her kaynagin alan adlari, fiyat olcegi ve sembol dilbilgisi farkli.

Bu modul araya kanonik bir kayit bicimi koyuyor. Adaptorler ham veriyi
kanonik bicime cevirir; hattin geri kalani YALNIZCA kanonik bicimi bilir.
Yeni bir kaynak eklemek artik bir adaptor yazmak ve kaydetmek.

KANONIK KAYIT NEYE BENZER
-------------------------
Spot:
    {source, ticker, ts, bid, ask, mid, bid_size, ask_size, flags}
Opsiyon:
    {source, ticker, ts, expiry, strike, opt_type, bid, ask, mid, flags}
Vadeli:
    {source, ticker, ts, code, fut_mid, spot_mid, dtm, flags}

Her kayit `source` tasir. Bu alan susleme degil: hangi fiyatin hangi
kaynaktan geldigi bilinmeden yedek kaynaga gecmek (E5) imkansizdir —
devretme aninda iki kaynagin degerleri karisir ve hangisine bakildigi
anlasilmaz.

KAYIT DUSURULUR, SESSIZCE DEGIL
-------------------------------
Dogrulamayi gecemeyen kayit atilir ama SAYILIR. `Hat.rapor()` her red
sebebini ayri tutar. Bozuk bir kaynak, boru hattini sessizce bosaltmak
yerine sayac olarak gorunur.

NE YAPMIYOR
-----------
Bu modul kaynak BAGLANTISI kurmaz ve veri CEKMEZ. Yalnizca bicim
cevirir. Ikinci bir gercek kaynagin sozlesmesi (uc nokta, kimlik
dogrulama, sembol listesi) hala dista: A1 ve E5 bu modulle kapanmaz,
yalnizca ucuzlar.

KULLANIM
--------
    import feed_schema as fs

    hat = fs.Hat()
    hat.kaydet(fs.IdealDataAdaptor())
    hat.kaydet(fs.JsonAdaptor("partner", {"symbol": "ticker", "b": "bid"}))

    for kayit in hat.besle("idealdata", ("YU", {"1": "THYAO", "6": "280", "9": "281"})):
        ...                      # kanonik spot kaydi

    python3 feed_schema.py --self-test      # adaptorleri ornek veriyle gosterir
"""

import argparse
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Kanonik alanlar
# ---------------------------------------------------------------------------

SPOT_ALANLARI = ("source", "ticker", "ts", "bid", "ask", "mid",
                 "bid_size", "ask_size", "flags")
OPSIYON_ALANLARI = ("source", "ticker", "ts", "expiry", "strike", "opt_type",
                    "bid", "ask", "mid", "flags")
VADELI_ALANLARI = ("source", "ticker", "ts", "code", "fut_mid", "spot_mid",
                   "dtm", "flags")

# Kayit turleri
SPOT = "spot"
OPSIYON = "option"
VADELI = "futures"


def _simdi():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sayi(v):
    """Sonlu float ya da None. bool kasten reddedilir: True -> 1.0 olurdu."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
        if not v:
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _pozitif(v):
    f = _sayi(v)
    return f if (f is not None and f > 0) else None


def _tamsayi(v):
    f = _sayi(v)
    if f is None or f < 0:
        return None
    return int(f)


def _ticker(v):
    if v is None:
        return None
    t = str(v).strip().upper()
    # Bosluk ve ayirici karakter iceren sembol kabul edilmez: boyle bir
    # deger neredeyse her zaman yanlis alanin okundugunun isaretidir.
    #
    # En az bir HARF sarti var: "280.50" yalnizca izinli karakterlerden
    # olustugu icin aksi halde gecerli bir sembol sayilirdi. Kaymis bir
    # CSV satirinda fiyat kolonu ticker kolonuna dusuyor ve o kayit
    # "280.50" adli bir enstruman olarak depoya giriyordu.
    if not t or not re.fullmatch(r"[A-Z0-9._-]{1,24}", t):
        return None
    if not re.search(r"[A-Z]", t):
        return None
    return t


class Red(Exception):
    """Kayit kanonik bicime cevrilemedi. Mesaj red SEBEBIDIR, sayilir."""


# ---------------------------------------------------------------------------
# Fiyat/mid turetme
#
# Kaynaklarin hepsi bid+ask vermiyor: bazisi yalnizca son islem fiyati
# (last) gonderiyor. Mid'i burada tek yerde turetiyoruz ki her adaptor
# kendi kuralini uydurmasin.
# ---------------------------------------------------------------------------

def mid_turet(bid, ask, son=None):
    """
    doner: (mid, flags)

    Kurallar:
      bid ve ask varsa        -> ortasi
      biri varsa              -> o deger, 'one-sided'
      ikisi de yoksa son varsa-> son, 'no-book'
      hicbiri yoksa           -> Red

    bid > ask (capraz defter) ATILMAZ. Gercek piyasada aninda olur ve
    ortasi hala iki kotasyonun arasindadir; isaretlenip gecirilir, cunku
    atmak o anda fiyati tamamen kaybetmek olurdu.
    """
    b, a, s = _pozitif(bid), _pozitif(ask), _pozitif(son)
    flags = []
    if b is not None and a is not None:
        if b > a:
            flags.append("crossed")
        return (b + a) / 2.0, flags
    if b is not None:
        return b, ["one-sided"]
    if a is not None:
        return a, ["one-sided"]
    if s is not None:
        return s, ["no-book"]
    raise Red("no usable price")


# ---------------------------------------------------------------------------
# Kanonik kurucular — her adaptor bunlardan birini cagirir.
# Dogrulama TEK yerde: adaptor sayisi arttikca kural kopyalanmasin.
# ---------------------------------------------------------------------------

def spot_kaydi(source, ticker, bid=None, ask=None, son=None, ts=None,
               bid_size=None, ask_size=None):
    t = _ticker(ticker)
    if t is None:
        raise Red("invalid ticker")
    mid, flags = mid_turet(bid, ask, son)
    return {
        "type": SPOT, "source": str(source), "ticker": t,
        "ts": ts or _simdi(),
        "bid": _pozitif(bid), "ask": _pozitif(ask), "mid": mid,
        "bid_size": _tamsayi(bid_size), "ask_size": _tamsayi(ask_size),
        "flags": flags,
    }


def opsiyon_kaydi(source, ticker, expiry, strike, opt_type,
                  bid=None, ask=None, son=None, ts=None):
    t = _ticker(ticker)
    if t is None:
        raise Red("invalid ticker")
    k = _pozitif(strike)
    if k is None:
        raise Red("invalid strike")
    # 'C'/'P' ya da 'call'/'put' — ilk harf yeterli.
    tip = str(opt_type or "").strip().upper()[:1]
    if tip not in ("C", "P"):
        raise Red("invalid option type")
    vade = str(expiry or "").strip()
    if not vade:
        raise Red("missing expiry")
    mid, flags = mid_turet(bid, ask, son)
    return {
        "type": OPSIYON, "source": str(source), "ticker": t,
        "ts": ts or _simdi(), "expiry": vade, "strike": k, "opt_type": tip,
        "bid": _pozitif(bid), "ask": _pozitif(ask), "mid": mid, "flags": flags,
    }


def vadeli_kaydi(source, ticker, code, fut_mid=None, spot_mid=None,
                 dtm=None, ts=None):
    t = _ticker(ticker)
    if t is None:
        raise Red("invalid ticker")
    f = _pozitif(fut_mid)
    if f is None:
        raise Red("no futures price")
    return {
        "type": VADELI, "source": str(source), "ticker": t,
        "ts": ts or _simdi(), "code": str(code or "").strip().upper(),
        "fut_mid": f, "spot_mid": _pozitif(spot_mid), "dtm": _sayi(dtm),
        "flags": [],
    }


# ---------------------------------------------------------------------------
# Adaptorler
# ---------------------------------------------------------------------------

class Adaptor:
    """
    Bir kaynak bicimini kanonik kayda cevirir.

    Alt sinif `ad` tanimlar ve `coz(ham)` yazar. `coz` ya kanonik kayit
    listesi doner ya da Red yukseltir. Kismi basari normaldir: tek bir
    yuk icinde bazi satirlar gecer, bazisi gecmez.
    """

    ad = "base"

    def coz(self, ham):                                   # pragma: no cover
        raise NotImplementedError

    # Yardimci: bir yuk icindeki satirlari tek tek cozup hatalari toplar.
    def _satirlar(self, satirlar, cevir):
        cikti, hatalar = [], []
        for s in satirlar:
            try:
                cikti.append(cevir(s))
            except Red as e:
                hatalar.append(str(e))
        if not cikti and hatalar:
            raise Red(hatalar[0])
        return cikti


class IdealDataAdaptor(Adaptor):
    """
    Mevcut kaynak: IdealData TCP akisi.

    Tel bicimi "TIP;alan=deger;...|" ve alan adlari FIX'e benzer sayisal
    etiketler. Konumlar bridge_stream.py'deki QUOTE_MSG_TYPES/extract_quote
    ile AYNI; burada kopyalanmasinin sebebi o donguyu simdilik degistirmeden
    ikinci bir tuketici kazanmak. (Birlestirme ayri bir is; o dongu canli
    akisin sicak yolu ve testleri ona bagli.)

    Sembol dilbilgisi:
        O_...  opsiyon      F_<TICKER><KOD>  vadeli      digeri  spot
    """

    ad = "idealdata"

    # mesaj tipi -> (bid alani, ask alani, bid miktari, ask miktari)
    KOTASYON = {
        "WU": ("108", "109", "110", "111"),
        "YU": ("6", "9", "7", "10"),
    }
    SEMBOL_ALANI = "1"

    def coz(self, ham):
        """
        ham: (msg_type, fields) ikilisi ya da ham cerceve metni.
        """
        if isinstance(ham, str):
            tip, alanlar = self._cerceve(ham)
        else:
            try:
                tip, alanlar = ham
            except (TypeError, ValueError):
                raise Red("unrecognised idealdata payload")
        if not isinstance(alanlar, dict):
            raise Red("unrecognised idealdata payload")

        sembol = str(alanlar.get(self.SEMBOL_ALANI, "")).strip()
        if not sembol:
            raise Red("missing symbol")

        if tip == "DU":
            bid, ask, bs, as_ = self._emir_defteri(alanlar)
        else:
            konum = self.KOTASYON.get(tip)
            if konum is None:
                raise Red("message type not a quote: " + str(tip))
            bid, ask, bs, as_ = (alanlar.get(konum[0]), alanlar.get(konum[1]),
                                 alanlar.get(konum[2]), alanlar.get(konum[3]))

        if sembol.startswith("O_"):
            op = ayristir_opsiyon_sembolu(sembol)
            if op is None:
                raise Red("unparsable option symbol: " + sembol)
            return [opsiyon_kaydi(self.ad, op["ticker"], op["expiry"],
                                  op["strike"], op["opt_type"], bid, ask)]
        if sembol.startswith("F_"):
            vd = ayristir_vadeli_sembolu(sembol)
            if vd is None:
                raise Red("unparsable futures symbol: " + sembol)
            mid, _ = mid_turet(bid, ask)
            return [vadeli_kaydi(self.ad, vd["ticker"], vd["code"], mid)]
        return [spot_kaydi(self.ad, sembol, bid, ask, bid_size=bs, ask_size=as_)]

    @staticmethod
    def _cerceve(raw):
        parcalar = str(raw).strip().strip("|").split(";")
        alanlar = {}
        for p in parcalar[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                alanlar[k] = v
        return (parcalar[0] if parcalar else ""), alanlar

    @staticmethod
    def _emir_defteri(alanlar):
        # Alan 100 derinlik seviyesi; yalnizca en iyi seviye (0) alinir.
        if str(alanlar.get("100")) != "0":
            raise Red("depth level ignored")
        taraf = alanlar.get("101")
        px, sz = alanlar.get("3"), alanlar.get("4")
        if taraf == "B":
            return px, None, sz, None
        if taraf == "A":
            return None, px, None, sz
        raise Red("unknown book side")


class JsonAdaptor(Adaptor):
    """
    Jenerik JSON kaynagi — alan adlari yapilandirmayla verilir.

    Ikinci kaynagin sozlesmesi henuz yok; bu adaptor o sozlesme geldiginde
    KOD yazmadan, yalnizca bir alan haritasiyla baglanabilecegini gosteriyor.

    harita: {kaynaktaki_ad: kanonik_ad}
      kanonik adlar: ticker, bid, ask, last, ts, bid_size, ask_size,
                     expiry, strike, opt_type
    """

    def __init__(self, ad, harita=None, kayit_turu=SPOT, liste_anahtari=None):
        self.ad = str(ad)
        self.harita = dict(harita or {})
        self.kayit_turu = kayit_turu
        self.liste_anahtari = liste_anahtari

    def _cevir(self, d):
        if not isinstance(d, dict):
            raise Red("row is not an object")
        out = {}
        for kaynak_ad, kanonik in self.harita.items():
            if kaynak_ad in d:
                out[kanonik] = d[kaynak_ad]
        # Haritalanmamis ama zaten kanonik adla gelen alanlar da alinir;
        # boylece hali hazirda uyumlu bir kaynak icin harita gerekmez.
        for k in ("ticker", "bid", "ask", "last", "ts", "bid_size",
                  "ask_size", "expiry", "strike", "opt_type"):
            out.setdefault(k, d.get(k))
        return out

    def coz(self, ham):
        if isinstance(ham, (str, bytes)):
            try:
                ham = json.loads(ham)
            except Exception:
                raise Red("invalid JSON")
        if isinstance(ham, dict) and self.liste_anahtari:
            ham = ham.get(self.liste_anahtari)
        satirlar = ham if isinstance(ham, list) else [ham]

        def cevir(s):
            f = self._cevir(s)
            if self.kayit_turu == OPSIYON:
                return opsiyon_kaydi(self.ad, f.get("ticker"), f.get("expiry"),
                                     f.get("strike"), f.get("opt_type"),
                                     f.get("bid"), f.get("ask"), f.get("last"),
                                     f.get("ts"))
            return spot_kaydi(self.ad, f.get("ticker"), f.get("bid"),
                              f.get("ask"), f.get("last"), f.get("ts"),
                              f.get("bid_size"), f.get("ask_size"))

        return self._satirlar(satirlar, cevir)


class FixAdaptor(Adaptor):
    """
    FIX 4.x tag=value — dokumanin adiyla andigi bicimlerden biri.

    Ayirici normalde SOH (0x01); bazi kopruler '|' kullanir, ikisi de
    kabul edilir. Kullanilan etiketler:
        55 Symbol   270 MDEntryPx   271 MDEntrySize   269 MDEntryType
        269: 0=bid, 1=ask, 2=trade
    Tek mesaj birden cok MDEntry tasiyabilir; etiketler sirayla gelir ve
    her 269 yeni bir girdi baslatir.
    """

    ad = "fix"

    def __init__(self, ad=None):
        if ad:
            self.ad = str(ad)

    def coz(self, ham):
        metin = ham.decode("latin-1") if isinstance(ham, bytes) else str(ham)
        ciftler = []
        for parca in re.split(r"[\x01|]", metin):
            if "=" in parca:
                k, v = parca.split("=", 1)
                ciftler.append((k.strip(), v.strip()))
        if not ciftler:
            raise Red("no FIX tags")

        sembol = None
        girdiler, mevcut = [], None
        for k, v in ciftler:
            if k == "55":
                sembol = v
            elif k == "269":
                mevcut = {"tip": v}
                girdiler.append(mevcut)
            elif k == "270" and mevcut is not None:
                mevcut["px"] = v
            elif k == "271" and mevcut is not None:
                mevcut["sz"] = v
        if sembol is None:
            raise Red("missing tag 55")
        if not girdiler:
            raise Red("no market data entries")

        bid = ask = son = bs = as_ = None
        for g in girdiler:
            if g["tip"] == "0":
                bid, bs = g.get("px"), g.get("sz")
            elif g["tip"] == "1":
                ask, as_ = g.get("px"), g.get("sz")
            elif g["tip"] == "2":
                son = g.get("px")
        return [spot_kaydi(self.ad, sembol, bid, ask, son,
                           bid_size=bs, ask_size=as_)]


class XmlAdaptor(Adaptor):
    """
    XML kaynagi. Her kotasyon bir ogedir; alanlar oznitelik ya da alt oge
    olarak gelebilir, ikisi de okunur.

        <quotes><quote symbol="THYAO" bid="280" ask="281"/></quotes>

    ET.fromstring kullaniliyor: harici varlik (XXE) cozmez, dis kaynaktan
    gelen XML icin onemli.
    """

    def __init__(self, ad="xml", oge="quote", harita=None):
        self.ad = str(ad)
        self.oge = oge
        self.harita = dict(harita or {"symbol": "ticker"})

    def coz(self, ham):
        try:
            kok = ET.fromstring(ham.strip() if isinstance(ham, str) else ham)
        except ET.ParseError as e:
            raise Red("invalid XML: " + str(e).split(":")[0])
        ogeler = kok.findall(".//" + self.oge)
        if kok.tag == self.oge:
            ogeler = [kok] + ogeler
        if not ogeler:
            raise Red("no <" + self.oge + "> elements")

        def cevir(el):
            d = dict(el.attrib)
            for alt in el:
                if alt.text is not None:
                    d.setdefault(alt.tag, alt.text.strip())
            for kaynak_ad, kanonik in self.harita.items():
                if kaynak_ad in d:
                    d.setdefault(kanonik, d[kaynak_ad])
            return spot_kaydi(self.ad, d.get("ticker"), d.get("bid"),
                              d.get("ask"), d.get("last"), d.get("ts"))

        return self._satirlar(ogeler, cevir)


class CsvAdaptor(Adaptor):
    """
    CSV kaynagi. Baslik adlari esnek eslenir (bkz. BASLIK_ESLERI), cunku
    elle hazirlanmis dosyalarda baslik adi her seferinde farkli oluyor.
    import_history.py de ayni eslemeyi kullanir.
    """

    ad = "csv"

    def __init__(self, ad=None, harita=None):
        if ad:
            self.ad = str(ad)
        self.harita = dict(harita or {})

    def coz(self, ham):
        if isinstance(ham, bytes):
            ham = ham.decode("utf-8-sig", errors="replace")
        okuyucu = csv.DictReader(io.StringIO(ham))
        if not okuyucu.fieldnames:
            raise Red("empty CSV")
        eslem = basliklari_esle(okuyucu.fieldnames, self.harita)

        def cevir(satir):
            d = {kanonik: satir.get(ham_ad) for ham_ad, kanonik in eslem.items()}
            return spot_kaydi(self.ad, d.get("ticker"), d.get("bid"),
                              d.get("ask"), d.get("close") or d.get("last"),
                              d.get("date") or d.get("ts"))

        return self._satirlar(list(okuyucu), cevir)


# ---------------------------------------------------------------------------
# Baslik eslemesi — CSV ve import_history.py ortak kullanir.
# ---------------------------------------------------------------------------

BASLIK_ESLERI = {
    "ticker": ("ticker", "symbol", "sembol", "hisse", "code", "kod", "instrument"),
    "date":   ("date", "tarih", "d", "day", "gun", "datetime", "timestamp"),
    "open":   ("open", "acilis", "o", "first"),
    "high":   ("high", "yuksek", "en_yuksek", "h", "max"),
    "low":    ("low", "dusuk", "en_dusuk", "l", "min"),
    "close":  ("close", "kapanis", "kapanış", "c", "last", "son", "price", "fiyat", "mid"),
    "bid":    ("bid", "alis", "alış", "b"),
    "ask":    ("ask", "satis", "satış", "a", "offer"),
    "ts":     ("ts", "time", "saat"),
}


def basliklari_esle(basliklar, ek=None):
    """
    doner: {ham_baslik: kanonik_ad}

    Buyuk/kucuk harf, bosluk ve alt tire farklari yok sayilir. `ek`
    acikca verilen eslemelerdir ve otomatik tahmini EZER — belirsiz
    basliklari olan dosyalar icin kacis yolu.
    """
    def sadeles(s):
        return re.sub(r"[^a-z0-9]", "", str(s).strip().lower())

    ters = {}
    for kanonik, adlar in BASLIK_ESLERI.items():
        for a in adlar:
            ters[sadeles(a)] = kanonik

    out = {}
    for h in (basliklar or []):
        kanonik = ters.get(sadeles(h))
        if kanonik:
            out[h] = kanonik
    for ham_ad, kanonik in (ek or {}).items():
        out[ham_ad] = kanonik
    return out


# ---------------------------------------------------------------------------
# Sembol ayristirma
# ---------------------------------------------------------------------------

# Bicimler bridge_stream.py'deki OPTION_SYMBOL_RE ve build_futures_symbol_map
# ile AYNI; uydurulmadi, mevcut kaynaktan alindi.
#
#   O_THYAOE1026C280     O_ + ticker + 'E' + MMYY + C|P + strike (ondalikli)
#   F_THYAO1026          F_ + ticker + MMYY
#
# Vade kodu MMYY'dir, YYMM degil: 1026 = Ekim 2026.
_OPSIYON_RE = re.compile(
    r"^O_(?P<ticker>[A-Z0-9]+)E(?P<expiry>\d{4})"
    r"(?P<tip>[CP])(?P<strike>\d+(?:\.\d+)?)$")

# Ticker tembel eslenir: vade kodu sondaki DORT hanedir, ticker'in kendisi
# rakam icerse bile (ornegin bir endeks kodu) dogru bolunur.
_VADELI_RE = re.compile(r"^F_(?P<ticker>[A-Z0-9]+?)(?P<code>\d{4})$")


def ayristir_opsiyon_sembolu(sembol):
    """
    doner: {ticker, strike, opt_type, expiry} ya da None.

    Strike sembolde gercek fiyat olceginde yaziyor (280 ya da 280.5),
    kurus olarak degil — bu yuzden olcekleme yapilmiyor. Kaynaga ozgu bir
    olcek olsaydi donusum TAM BURADA olurdu: kanonik kayda her zaman
    gercek fiyat olcegi girer, yoksa iki kaynagin strike'lari sessizce
    100 kat farkli olurdu.
    """
    m = _OPSIYON_RE.match(str(sembol).strip().upper())
    if not m:
        return None
    return {"ticker": m.group("ticker"), "strike": float(m.group("strike")),
            "opt_type": m.group("tip"), "expiry": m.group("expiry")}


def ayristir_vadeli_sembolu(sembol):
    m = _VADELI_RE.match(str(sembol).strip().upper())
    if not m:
        return None
    return {"ticker": m.group("ticker"), "code": m.group("code")}


# ---------------------------------------------------------------------------
# Hat — adaptor kaydi, cozme ve sayac
# ---------------------------------------------------------------------------

class Hat:
    """
    Kayitli adaptorlerin onunde duran cephe.

    Tuketici `besle(kaynak, ham)` cagirir ve kanonik kayit listesi alir.
    Hangi bicimin geldigini bilmesi gerekmez.
    """

    def __init__(self):
        self.adaptorler = {}
        self.sayac = {"ok": 0, "red": 0}
        self.red_sebepleri = {}
        self.kaynak_sayaci = {}
        self.son_kayit_ts = {}

    def kaydet(self, adaptor):
        self.adaptorler[adaptor.ad] = adaptor
        self.sayac.setdefault(adaptor.ad, 0)
        self.kaynak_sayaci.setdefault(adaptor.ad, {"ok": 0, "red": 0})
        return self

    def besle(self, kaynak, ham):
        a = self.adaptorler.get(kaynak)
        if a is None:
            self._red(kaynak, "unknown source: " + str(kaynak))
            return []
        try:
            kayitlar = a.coz(ham)
        except Red as e:
            self._red(kaynak, str(e))
            return []
        except Exception as e:                            # noqa: BLE001
            # Adaptor hatasi hattı durdurmaz; bir kaynagin bozuk yuku
            # digerlerinin akisini kesmemeli.
            self._red(kaynak, type(e).__name__ + ": " + str(e)[:80])
            return []
        self.sayac["ok"] += len(kayitlar)
        self.kaynak_sayaci[kaynak]["ok"] += len(kayitlar)
        if kayitlar:
            self.son_kayit_ts[kaynak] = kayitlar[-1].get("ts")
        return kayitlar

    def _red(self, kaynak, sebep):
        self.sayac["red"] += 1
        self.red_sebepleri[sebep] = self.red_sebepleri.get(sebep, 0) + 1
        if kaynak in self.kaynak_sayaci:
            self.kaynak_sayaci[kaynak]["red"] += 1

    def rapor(self):
        return {
            "sources": sorted(self.adaptorler),
            "accepted": self.sayac["ok"],
            "rejected": self.sayac["red"],
            "reject_reasons": dict(sorted(self.red_sebepleri.items(),
                                          key=lambda kv: -kv[1])),
            "per_source": {k: dict(v) for k, v in self.kaynak_sayaci.items()},
            "last_record_ts": dict(self.son_kayit_ts),
        }


class KaynakSecici:
    """
    Oncelikli kaynak secimi.

    BU E5 DEGIL. E5 (yedek API kaynagi) ikinci bir gercek kaynagin
    sozlesmesini gerektiriyor ve o hala yok. Burada yapilan sey yalnizca
    KARAR mantigi: birden cok kaynak varken hangisinin gecerli oldugunu,
    ne zaman devredilecegini ve ne zaman geri donulecegini belirlemek.
    Sozlesme geldiginde baglanacak yer bu sinif.

    Oncelik kucukten buyuge: 0 birincil.
    """

    def __init__(self, oncelikler, bayat_sn=60.0):
        # {ad: oncelik}
        self.oncelikler = dict(oncelikler or {})
        self.bayat_sn = float(bayat_sn)
        self.son_goruldu = {}
        self.aktif = None
        self.gecis_gecmisi = []

    def isaretle(self, kaynak, simdi=None):
        """Kaynaktan kayit geldigini bildirir."""
        self.son_goruldu[kaynak] = float(simdi) if simdi is not None else _mono()

    def sec(self, simdi=None):
        """
        doner: (aktif_kaynak|None, sebep)

        En yuksek oncelikli TAZE kaynak secilir. Birincil geri geldiginde
        otomatik dondurulur — yedekte takilip kalmak, birincil duzelmis
        olsa bile kotu veriyle devam etmek olurdu.
        """
        t = float(simdi) if simdi is not None else _mono()
        uygun = []
        for ad, onc in self.oncelikler.items():
            gorulen = self.son_goruldu.get(ad)
            if gorulen is None:
                continue
            if (t - gorulen) <= self.bayat_sn:
                uygun.append((onc, ad))
        if not uygun:
            yeni, sebep = None, "no fresh source"
        else:
            uygun.sort()
            yeni = uygun[0][1]
            sebep = ("primary" if self.oncelikler.get(yeni) == min(self.oncelikler.values())
                     else "failover to " + yeni)
        if yeni != self.aktif:
            self.gecis_gecmisi.append({"from": self.aktif, "to": yeni,
                                       "reason": sebep, "ts": _simdi()})
            self.aktif = yeni
        return yeni, sebep


def _mono():
    import time
    return time.monotonic()


# ---------------------------------------------------------------------------
# Varsayilan hat — kayitli adaptorlerle
# ---------------------------------------------------------------------------

def varsayilan_hat():
    return (Hat()
            .kaydet(IdealDataAdaptor())
            .kaydet(FixAdaptor())
            .kaydet(CsvAdaptor())
            .kaydet(XmlAdaptor()))


# ---------------------------------------------------------------------------
# CLI — adaptorlerin ayni kanonik ciktiyi verdigini gostermek icin.
# ---------------------------------------------------------------------------

_ORNEKLER = [
    ("idealdata", "YU;1=THYAO;6=279.50;9=280.50;7=100;10=250|"),
    ("fix", "8=FIX.4.4|55=THYAO|269=0|270=279.50|271=100|269=1|270=280.50|271=250|"),
    ("csv", "symbol,bid,ask\nTHYAO,279.50,280.50\n"),
    ("xml", '<quotes><quote symbol="THYAO" bid="279.50" ask="280.50"/></quotes>'),
]


def _self_test():
    hat = varsayilan_hat()
    print("Ayni kotasyon, dort ayri bicim -> ayni kanonik kayit:\n")
    midler = []
    for kaynak, ham in _ORNEKLER:
        kayitlar = hat.besle(kaynak, ham)
        for k in kayitlar:
            midler.append(k["mid"])
            print(f"  {kaynak:10s} ticker={k['ticker']:6s} bid={k['bid']} "
                  f"ask={k['ask']} mid={k['mid']} source={k['source']}")
    print()
    if midler and len(set(midler)) == 1:
        print(f"  -> dort kaynagin mid'i ayni: {midler[0]}")
    else:
        print(f"  -> UYUSMAZLIK: {midler}")

    print("\nOpsiyon sembolu:")
    k = hat.besle("idealdata", "YU;1=O_THYAOE1026C280;6=12.40;9=12.60|")
    for r in k:
        print(f"  {r['ticker']} {r['opt_type']} K={r['strike']} exp={r['expiry']} mid={r['mid']}")

    print("\nReddedilenler (sessizce dusmuyor, sayiliyor):")
    hat.besle("idealdata", "YU;1=THYAO|")                    # fiyat yok
    hat.besle("csv", "symbol,bid\n,280\n")                   # ticker yok
    hat.besle("bilinmeyen", "{}")                            # kayitli degil
    rapor = hat.rapor()
    print(f"  accepted={rapor['accepted']} rejected={rapor['rejected']}")
    for sebep, n in rapor["reject_reasons"].items():
        print(f"    {n}x {sebep}")

    print("\nKaynak secimi (E5'in karar mantigi):")
    sec = KaynakSecici({"idealdata": 0, "yedek": 1}, bayat_sn=30)
    sec.isaretle("idealdata", 100); sec.isaretle("yedek", 100)
    print("   t=100 ->", sec.sec(100))
    print("   t=200 (birincil bayat, yedek taze) ->",
          (sec.isaretle("yedek", 195), sec.sec(200))[1])
    print("   t=300 (birincil geri geldi) ->",
          (sec.isaretle("idealdata", 299), sec.sec(300))[1])


def main():
    ap = argparse.ArgumentParser(description="Ortak veri semasi ve adaptorler")
    ap.add_argument("--self-test", action="store_true",
                    help="adaptorleri ornek veriyle calistirip karsilastirir")
    a = ap.parse_args()
    if a.self_test:
        _self_test()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
