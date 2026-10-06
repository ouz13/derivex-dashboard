#!/usr/bin/env python3
"""
bench_stream.py — veri boru hattinin gercek kapasitesini olcer.

NEDEN BU DOSYA VAR
Proje dokumaninda "saniyede 10.000+ mesaj" senaryosu gecer ama bu iddia
hic test edilmedi. Elimizde gercek bir seanstan alinmis kayit var;
tahmin etmek yerine olculebilir.

NE OLCULUYOR
Boru hatti dort asamadan geciyor ve her birinin maliyeti ayri olculuyor;
toplam sayi tek basina nerede tikandigini soylemez:

    1. parse_frames    bayt tamponu  -> cerceve listesi
    2. parse_frame     cerceve       -> (tip, alanlar)
    3. extract_quote   alanlar       -> (bid, ask, miktarlar)
    4. apply_quote     kotasyon      -> sembol basina birlesik defter

Ayrica DASHBOARD'A POST maliyeti ayri olculuyor: bu asamalar saf CPU
isi, POST ise ag ve JSON islemesi. Darbogazin hangisinde oldugu ancak
ikisi ayri olculunce gorulur.

KULLANIM
    python3 bench_stream.py                        kayittan tam olcum
    python3 bench_stream.py --frames 20000         daha kisa
    python3 bench_stream.py --no-post              POST olcumunu atla
    python3 bench_stream.py --kayit /yol/veri.rtf
"""

import argparse
import os
import re
import statistics
import sys
import time

import bridge_stream as B

VARSAYILAN_KAYIT = os.path.join(os.path.expanduser("~"), "Desktop", "exampledata.rtf")

# Dokumandaki hedef (Ay 3): "saniyede 10.000+ mesaj"
HEDEF_MSG_SN = 10_000


def kayittan_cerceveler(yol, azami=None):
    """
    RTF kaydindan ham cerceveleri cikarir.

    Kayit RTF olarak saklandigi icin akisin '|' ayiricisi, RTF'in satir
    devam kacisi olan ters-bolu + satirsonu olarak yazilmis. Once o geri
    cevriliyor, sonra RTF basligi atiliyor.
    """
    with open(yol, encoding="utf-8", errors="replace") as f:
        ham = f.read()
    ham = ham.replace("\\\n", "|")
    # RTF kontrol sozcuklerini ve suslu parantezleri at
    ham = re.sub(r"\\[a-z]+-?\d*\s?", " ", ham)
    ham = ham.replace("{", " ").replace("}", " ")
    cerceveler = [c.strip() for c in ham.split("|")]
    cerceveler = [c for c in cerceveler if ";" in c and "=" in c]
    return cerceveler[:azami] if azami else cerceveler


def _sure(islev, yineleme=1):
    """islev'i calistirir, saniye cinsinden gecen sureyi doner."""
    t0 = time.perf_counter()
    for _ in range(yineleme):
        islev()
    return time.perf_counter() - t0


def olc_asamalar(cerceveler):
    """Her asamayi ayri ayri olcer ve saniyedeki mesaj sayisini doner."""
    n = len(cerceveler)
    sonuc = {}

    # 1) parse_frames — bayt tamponundan cerceve ayirma
    tampon = ("|".join(cerceveler) + "|").encode("utf-8")
    sure = _sure(lambda: B.parse_frames(tampon))
    sonuc["parse_frames"] = n / sure if sure > 0 else float("inf")

    # 2) parse_frame — cerceveden alan sozlugu
    sure = _sure(lambda: [B.parse_frame(c) for c in cerceveler])
    sonuc["parse_frame"] = n / sure if sure > 0 else float("inf")

    # Sonraki asamalar icin ayristirilmis hali hazirla
    ayristirilmis = [B.parse_frame(c) for c in cerceveler]
    kotasyonluk = [(t, a) for t, a in ayristirilmis if t in B.QUOTE_MSG_TYPES]

    # 3) extract_quote
    if kotasyonluk:
        sure = _sure(lambda: [B.extract_quote(t, a) for t, a in kotasyonluk])
        sonuc["extract_quote"] = len(kotasyonluk) / sure if sure > 0 else float("inf")

    # 4) apply_quote — sembol basina birlesik defter
    kotasyonlar = []
    for t, a in kotasyonluk:
        s = a.get("1", "")
        if not s:
            continue
        b, k, bs, ks = B.extract_quote(t, a)
        if b is None and k is None:
            continue
        kotasyonlar.append((s, b, k, bs, ks))

    if kotasyonlar:
        def defter_kos():
            defter = {}
            for s, b, k, bs, ks in kotasyonlar:
                B.apply_quote(defter, s, b, k, bs, ks)
        sure = _sure(defter_kos)
        sonuc["apply_quote"] = len(kotasyonlar) / sure if sure > 0 else float("inf")

    # 5) Uctan uca: gercek dongunun yaptigi isin tamami
    def uctan_uca():
        defter = {}
        for c in cerceveler:
            tip, alanlar = B.parse_frame(c)
            if tip not in B.QUOTE_MSG_TYPES:
                continue
            s = alanlar.get("1", "")
            if not s:
                continue
            b, k, bs, ks = B.extract_quote(tip, alanlar)
            if b is None and k is None:
                continue
            B.apply_quote(defter, s, b, k, bs, ks)
    sure = _sure(uctan_uca)
    sonuc["uctan_uca"] = n / sure if sure > 0 else float("inf")

    return sonuc, len(kotasyonluk), len(kotasyonlar)


BENCH_TICKER = "ZZ_BENCH"


def _bench_temizle():
    """
    Olcumun biraktigi sahte kaydi depodan siler.

    Tanilama araci canli duruma kalici iz birakmamali. Dashboard'in
    bellekteki kopyasinda kayit yeniden baslatmaya kadar kalir (spot
    silme ucu yok); adi ZZ_BENCH oldugu icin listelerin sonunda durur
    ve gercek bir ticker ile karistirilamaz.
    """
    try:
        import store
        d = store.depo()
        if d is None:
            return
        with d._kilit:
            d.conn.execute("DELETE FROM spot_tick WHERE ticker = ?", (BENCH_TICKER,))
            d.conn.execute("DELETE FROM spot_daily WHERE ticker = ?", (BENCH_TICKER,))
            d.conn.commit()
    except Exception:
        pass


def olc_post(adet=200):
    """
    Dashboard'a POST maliyeti.

    Asamalardan ayri olculuyor: onlar saf CPU, bu ag + JSON. Kopru her
    spot degisiminde bir POST attigi icin bu yol uygulamada asil sinir
    olabilir.

    Gercek POST yolunu olcmek icin gercekten POST atilir; sahte ticker
    sonra temizlenir.
    """
    try:
        import requests
    except ImportError:
        return None, "requests kurulu degil"

    try:
        requests.get(f"{B.FRONTEND_BASE_URL}/health", timeout=2).raise_for_status()
    except Exception as e:
        return None, f"dashboard erisilemiyor ({type(e).__name__}) — once baslatin"

    sureler = []
    for i in range(adet):
        t0 = time.perf_counter()
        try:
            B.post_spot_mid(BENCH_TICKER, 100.0 + (i % 50) * 0.01,
                            time.strftime("%H:%M:%S.000"))
        except Exception as e:
            _bench_temizle()
            return None, f"POST hatasi: {type(e).__name__}: {e}"
        sureler.append((time.perf_counter() - t0) * 1000.0)

    _bench_temizle()
    sureler.sort()
    return {
        "adet": adet,
        "ortalama_ms": statistics.mean(sureler),
        "ortanca_ms": statistics.median(sureler),
        "p95_ms": sureler[int(0.95 * len(sureler)) - 1],
        "post_sn": 1000.0 / statistics.mean(sureler),
    }, None


def _bicim(x):
    if x == float("inf"):
        return "       —"
    if x >= 1_000_000:
        return f"{x/1_000_000:7.1f}M"
    if x >= 1_000:
        return f"{x/1_000:7.1f}k"
    return f"{x:8.0f}"


def main():
    ap = argparse.ArgumentParser(description="Veri boru hatti kapasite olcumu")
    ap.add_argument("--kayit", default=VARSAYILAN_KAYIT, help="ornek kayit dosyasi")
    ap.add_argument("--frames", type=int, help="kullanilacak azami cerceve sayisi")
    ap.add_argument("--post", action="store_true",
                    help="POST yolunu da olc (calisan dashboard gerekir; "
                         "sahte bir ticker POST edilip temizlenir)")
    ap.add_argument("--post-adet", type=int, default=200, help="POST ornegi sayisi")
    a = ap.parse_args()

    if not os.path.isfile(a.kayit):
        print(f"[HATA] kayit bulunamadi: {a.kayit}")
        print("       --kayit ile yol verin")
        sys.exit(1)

    print("\n  VERI BORU HATTI KAPASITE OLCUMU")
    print("  " + "=" * 62)
    print(f"  Kayit: {a.kayit}")

    cerceveler = kayittan_cerceveler(a.kayit, a.frames)
    if not cerceveler:
        print("  [HATA] kayittan cerceve cikarilamadi")
        sys.exit(1)

    asamalar, kotasyonluk, uygulanan = olc_asamalar(cerceveler)

    print(f"  Cerceve: {len(cerceveler):,}  |  kotasyon tipi: {kotasyonluk:,}"
          f"  |  deftere islenen: {uygulanan:,}\n")

    print("  ASAMA BAZINDA (mesaj/saniye)")
    print("  " + "-" * 62)
    for ad, etiket in (("parse_frames", "1. parse_frames  (tampon -> cerceve)"),
                       ("parse_frame", "2. parse_frame   (cerceve -> alanlar)"),
                       ("extract_quote", "3. extract_quote (alanlar -> kotasyon)"),
                       ("apply_quote", "4. apply_quote   (defter birlestirme)")):
        if ad in asamalar:
            print(f"    {etiket:<42}{_bicim(asamalar[ad])}/sn")
    print("  " + "-" * 62)
    print(f"    {'UCTAN UCA (1+2+3+4)':<42}{_bicim(asamalar['uctan_uca'])}/sn")

    uctan = asamalar["uctan_uca"]
    print()
    if uctan >= HEDEF_MSG_SN:
        print(f"  Ayristirma tarafi {HEDEF_MSG_SN:,} msg/sn hedefini "
              f"{uctan/HEDEF_MSG_SN:.1f}x karsiliyor.")
    else:
        print(f"  Ayristirma tarafi hedefin ALTINDA: {uctan:,.0f} < {HEDEF_MSG_SN:,}")

    if a.post:
        print("\n  DASHBOARD'A POST")
        print("  " + "-" * 62)
        post, hata = olc_post(a.post_adet)
        if hata:
            print(f"    atlandi: {hata}")
        else:
            print(f"    ortanca gecikme   {post['ortanca_ms']:.2f} ms")
            print(f"    ortalama gecikme  {post['ortalama_ms']:.2f} ms")
            print(f"    p95 gecikme       {post['p95_ms']:.2f} ms")
            print(f"    verim             {post['post_sn']:,.0f} POST/sn")
            print()
            # Asil mesele burasi: kopru her spot DEGISIMINDE bir POST
            # atiyor, yani uygulamadaki tavan bu.
            if post["post_sn"] < uctan:
                print(f"    DARBOGAZ: POST yolu ayristirmadan "
                      f"{uctan/post['post_sn']:.0f}x yavas.")
                print(f"    Boru hattinin pratik tavani ~{post['post_sn']:,.0f} msg/sn,")
                print(f"    ayristirma kapasitesi {uctan:,.0f} msg/sn degil.")
                if post["post_sn"] < HEDEF_MSG_SN:
                    print(f"    Dokumandaki {HEDEF_MSG_SN:,} msg/sn hedefi bu haliyle "
                          f"KARSILANMIYOR.")
                    print(f"    Cozum yonu: spot basina tek POST yerine toplu gonderim.")
    else:
        print("\n  POST yolu olculmedi (--post ile olculur). Ayristirma")
        print("  kapasitesi tek basina pratik tavani GOSTERMEZ: kopru her spot")
        print("  degisiminde bir POST atiyor ve asil sinir orada.")
    print()


if __name__ == "__main__":
    main()
