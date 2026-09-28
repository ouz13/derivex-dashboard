#!/usr/bin/env python3
"""
IdealData Probe — veri kaynagi kesif scripti
===========================================

AMAC
----
IP izni olan makinede calistirilip, Derivex Dashboard'un ihtiyac duydugu
veri sozlesmesini (data contract) cikarmak icin ornek veri toplar.

- Bolum A : REST  servisapi.idealdata.com.tr/api/Chart/Chart1  (tarihsel OHLCV -> realized vol)
- Bolum B : TCP   ssdata1.idealdata.com.tr:9443                (canli akis -> spot/futures/opsiyon)

CIKTI
-----
Tek bir dosya: idealdata_probe_<zaman>.json
Bu dosyayi geri gonderin. Kullanici adi / sifre / api_key ciktiya YAZILMAZ,
yazilmis olsa bile maskelenir.

KULLANIM
--------
    python3 idealdata_probe.py                  # varsayilan: 25 saniye akis dinler
    python3 idealdata_probe.py --seconds 40
    python3 idealdata_probe.py --skip-stream    # sadece REST testi
    python3 idealdata_probe.py --skip-rest      # sadece akis testi

Bagimlilik yok, sadece Python standart kutuphanesi. Python 3.8+.

KIMLIK BILGILERI
----------------
Script icine gomulu; hicbir dosyaya veya ayara ihtiyac duymaz, tek basina
herhangi bir makinede calisir. Istenirse ortam degiskeniyle ezilebilir:
    STREAM_USERNAME, STREAM_PASSWORD, IDEALDATA_API_KEY
Uretilen JSON ciktisinda sifre ve api_key maskelenir.
"""

import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Baglanti bilgileri — .env dosyasindan ya da ortam degiskeninden okunur.
# Kodda gomulu kimlik bilgisi tutulmaz.
# --------------------------------------------------------------------------

def _env_yukle(dosya=".env"):
    """Script'in yanindaki .env dosyasini ortama yukler (mevcut degerleri ezmez)."""
    yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), dosya)
    if not os.path.isfile(yol):
        return
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir or satir.startswith("#") or "=" not in satir:
                continue
            k, v = satir.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_env_yukle()

STREAM_HOST = os.environ.get("STREAM_HOST", "ssdata1.idealdata.com.tr")
STREAM_PORT = int(os.environ.get("STREAM_PORT", "9443"))
STREAM_USERNAME = os.environ.get("STREAM_USERNAME", "")
STREAM_PASSWORD = os.environ.get("STREAM_PASSWORD", "")

REST_BASE = "https://servisapi.idealdata.com.tr/api/Chart/Chart1"
REST_API_KEY = os.environ.get("IDEALDATA_API_KEY", "")

HB_INTERVAL = 15.0          # heartbeat araligi (bridge_stream.py ile ayni)
SOCK_TIMEOUT = 0.5

# Ciktiyi sinirlamak icin (akis saniyede ~11k mesaj uretebiliyor)
MAX_RAW_FRAMES = 1500       # ham haliyle saklanacak toplam frame
MAX_SAMPLES_PER_TYPE = 20   # her mesaj tipi icin ornek frame
MAX_VALUES_PER_FIELD = 4    # her alan icin ornek deger
MAX_SYMBOLS = 5000          # toplanacak farkli sembol sayisi

REST_TEST_SYMBOLS = ["THYAO", "GARAN", "AKBNK"]

_SECRETS = []               # ciktidan maskelenecek degerler


# --------------------------------------------------------------------------
# Kimlik bilgisi kesfi
# --------------------------------------------------------------------------

def register_secrets():
    """Sifre ve api_key'i ciktidan maskelenecekler listesine ekler."""
    for v in (STREAM_PASSWORD, REST_API_KEY):
        if v:
            _SECRETS.append(v)


def scrub(obj):
    """Ciktidaki sifre/api_key degerlerini maskeler."""
    if isinstance(obj, str):
        for s in _SECRETS:
            if s and s in obj:
                obj = obj.replace(s, "***MASKELENDI***")
        return obj
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


# --------------------------------------------------------------------------
# BOLUM A — REST: tarihsel OHLCV (realized volatility kaynagi)
# --------------------------------------------------------------------------

_SSL_FALLBACK_USED = [False]


def http_get(url, timeout=15):
    """
    HTTP GET -> (status, body).

    macOS'ta Python kendi sertifika deposunu kullanir ve bu depo cogu kurulumda
    bostur; bu durumda dogrulama basarisiz olur ama sunucu aslinda erisilebilirdir.
    urllib, SSL hatasini URLError icine sardigi icin dogrudan ssl.SSLError
    yakalanamaz — bu yuzden hatanin icerigine bakip dogrulamasiz baglantiyla
    bir kez daha deniyoruz.
    """
    def _do(ctx):
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    try:
        return _do(ssl.create_default_context())
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e.reason):
            raise
        _SSL_FALLBACK_USED[0] = True
        return _do(ssl._create_unverified_context())


def probe_rest(api_key, bar_count=10):
    out = {"endpoint": REST_BASE, "api_key_bulundu": bool(api_key), "sonuclar": []}
    if not api_key:
        out["hata"] = "api_key bulunamadi"
        return out

    for sym in REST_TEST_SYMBOLS:
        url = (f"{REST_BASE}?Sembol={sym}&Periyot=G&BarCount={bar_count}"
               f"&CurrencyCode=TRY&api_key={api_key}")
        rec = {"sembol": sym}
        t0 = time.time()
        try:
            rec["http_status"], body = http_get(url)
        except Exception as e:
            rec["hata"] = f"{type(e).__name__}: {e}"
            out["sonuclar"].append(rec)
            continue

        rec["sure_sn"] = round(time.time() - t0, 3)
        rec["uzunluk"] = len(body)
        rec["ham_ilk_600"] = body[:600]

        # Not: yanit "bracket'siz JSON obje listesi" olabiliyor -> [ ] ile sarmayi dene
        parsed, how = None, None
        for attempt, text in (("duz", body), ("koseli_parantezle_sarildi", "[" + body + "]")):
            try:
                parsed = json.loads(text)
                how = attempt
                break
            except Exception:
                continue

        rec["json_parse"] = how or "BASARISIZ"

        # Yanit tek bir bracket'siz obje olabilir; bu da gecerli veridir.
        # Hata zarfi ("message" alani tasir) bunun disinda tutulur.
        if isinstance(parsed, dict) and "message" not in parsed:
            parsed = [parsed]

        if isinstance(parsed, list) and parsed:
            rec["kayit_sayisi"] = len(parsed)
            first = parsed[0]
            if isinstance(first, dict):
                rec["alanlar"] = sorted(first.keys())
                rec["ornek_kayit_ilk"] = first
                rec["ornek_kayit_son"] = parsed[-1]
        elif isinstance(parsed, dict):
            rec["govde_tipi"] = "obje"
            rec["govde"] = parsed

        out["sonuclar"].append(rec)

    if _SSL_FALLBACK_USED[0]:
        out["ssl_notu"] = ("yerel Python sertifika deposu eksik oldugundan dogrulama "
                           "devre disi birakilarak baglanildi (sunucu erisilebilir)")

    return out


# --------------------------------------------------------------------------
# BOLUM B — TCP akis
# --------------------------------------------------------------------------

def parse_frames(buffer: bytes):
    """Tamponu '|' ayracina gore frame'lere boler, artani geri dondurur."""
    frames = []
    while b"|" in buffer:
        idx = buffer.index(b"|")
        raw = buffer[:idx].decode("utf-8", errors="replace").strip()
        buffer = buffer[idx + 1:]
        if raw:
            frames.append(raw)
    return frames, buffer


def classify_symbol(sym: str) -> str:
    """Sembolu kabaca siniflandirir. Amac kesin kural degil, deseni gormek."""
    if not sym:
        return "bos"
    s = sym.upper()
    if s.startswith("O_"):
        return "opsiyon(O_ onekli)"
    if s.startswith("F_"):
        return "vadeli(F_ onekli)"
    if re.fullmatch(r"[A-Z]{3,6}", s):
        return "spot(duz harf)"
    if re.search(r"[CP]\d", s):
        return "opsiyon(call/put deseni)"
    if re.search(r"\d{4,}", s):
        return "vadeli/turev(tarih deseni)"
    return "siniflandirilamadi"


def probe_stream(user, pwd, seconds, raw_path=None, raw_budget_mb=5.0):
    out = {"host": STREAM_HOST, "port": STREAM_PORT, "dinleme_sn": seconds}
    if not (user and pwd):
        out["hata"] = "kimlik bilgisi bulunamadi"
        return out

    # --- baglan + login ---
    try:
        sock = socket.create_connection((STREAM_HOST, STREAM_PORT), timeout=10)
        sock.settimeout(SOCK_TIMEOUT)
    except Exception as e:
        out["hata"] = f"baglanti kurulamadi ({type(e).__name__})"
        return out

    sock.sendall(f"Login;{user};{pwd}|".encode("utf-8"))

    # Login yanitini oku. Kisa tutuluyor: akis saniyede ~11k mesaj uretebildigi
    # icin uzun bir bekleme, sayim baslamadan once tampona buyuk bir yigin doldurur.
    login_buf = b""
    deadline = time.time() + 1.5
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            login_buf += chunk
        except socket.timeout:
            pass
    login_resp = login_buf.decode("utf-8", errors="replace")
    out["login_yaniti"] = login_resp[:500]

    low = login_resp.lower()
    if "zaten bagli" in low or "already connected" in low:
        out["hata"] = "login reddedildi: kullanici zaten bagli (baska oturumu kapatin)"
        sock.close()
        return out

    # --- topla ---
    type_counts = Counter()
    field_counts = defaultdict(Counter)              # tip -> alan -> adet
    field_values = defaultdict(lambda: defaultdict(list))  # tip -> alan -> ornek degerler
    type_samples = defaultdict(list)                 # tip -> ham frame ornekleri
    raw_frames = []
    symbols = Counter()
    malformed = []

    buf = login_buf if b"|" in login_buf else b""
    total = 0
    started = time.time()
    last_hb = started

    # Ham frame'ler dogrudan diske yazilir (bellekte biriktirilmez), boylece
    # akis hizi yuksek olsa da bellek sabit kalir ve buyuk ornek toplanabilir.
    raw_fh = None
    raw_bytes = 0
    raw_written = 0
    raw_budget = int(raw_budget_mb * 1024 * 1024)
    if raw_path:
        raw_fh = open(raw_path, "w", encoding="utf-8")
        raw_fh.write(
            f"# IdealData ham akis ornegi\n"
            f"# kaynak   : {STREAM_HOST}:{STREAM_PORT}\n"
            f"# tarih    : {datetime.now().isoformat(timespec='seconds')}\n"
            f"# bicim    : her satir bir frame; orijinalde '|' ile ayrilir\n"
            f"#            frame icerigi -> TIP;alan=deger;alan=deger\n"
            f"# not      : satir sonlari okunabilirlik icin eklenmistir\n"
            f"#{'-' * 60}\n"
        )

    try:
        while time.time() - started < seconds:
            now = time.time()
            if now - last_hb >= HB_INTERVAL:
                try:
                    sock.sendall(f"HB;{user};{pwd}|".encode("utf-8"))
                    last_hb = now
                except Exception:
                    pass

            try:
                chunk = sock.recv(65536)
                if not chunk:
                    out["not"] = "sunucu baglantiyi kapatti"
                    break
                buf += chunk
            except socket.timeout:
                pass          # veri gelmedi; tamponda bekleyen frame'ler yine islenmeli
            except Exception as e:
                out["not"] = f"okuma hatasi: {type(e).__name__}: {e}"
                break

            # Ayristirma her turda calisir. 'continue' ile atlanirsa login yanitiyla
            # birlikte tampona dusen frame'ler hic islenmeden kaybolur.
            frames, buf = parse_frames(buf)
            for raw in frames:
                total += 1
                parts = raw.split(";")
                mtype = parts[0] if parts else "?"
                type_counts[mtype] += 1

                if len(raw_frames) < MAX_RAW_FRAMES:
                    raw_frames.append(raw)

                if raw_fh is not None and raw_bytes < raw_budget:
                    # Login yaniti da frame olarak ayrisabilir; sifre sizmasin.
                    satir = scrub(raw) + "\n"
                    raw_fh.write(satir)
                    raw_bytes += len(satir)
                    raw_written += 1
                if len(type_samples[mtype]) < MAX_SAMPLES_PER_TYPE:
                    type_samples[mtype].append(raw)

                has_kv = False
                for p in parts[1:]:
                    if "=" not in p:
                        continue
                    has_kv = True
                    k, v = p.split("=", 1)
                    field_counts[mtype][k] += 1
                    vals = field_values[mtype][k]
                    if len(vals) < MAX_VALUES_PER_FIELD and v not in vals:
                        vals.append(v)
                    # alan "1" sembol kabul ediliyor (bridge_stream.py ile uyumlu)
                    if k == "1" and len(symbols) < MAX_SYMBOLS:
                        symbols[v] += 1

                if not has_kv and len(malformed) < 25:
                    malformed.append(raw)
    finally:
        try:
            sock.close()
        except Exception:
            pass
        if raw_fh is not None:
            raw_fh.close()

    elapsed = time.time() - started

    # --- sembol siniflandirmasi ---
    buckets = defaultdict(list)
    for sym, cnt in symbols.most_common():
        buckets[classify_symbol(sym)].append(sym)

    out.update({
        "toplam_frame": total,
        "gecen_sure_sn": round(elapsed, 1),
        "saniyede_mesaj": int(total / max(elapsed, 1)),
        "mesaj_tipi_dagilimi": dict(type_counts.most_common()),
        "alan_haritasi": {
            mtype: {
                k: {"adet": c, "ornek_degerler": field_values[mtype][k]}
                for k, c in field_counts[mtype].most_common()
            }
            for mtype in field_counts
        },
        "tip_basina_ham_ornekler": {k: v for k, v in type_samples.items()},
        "sembol_sayisi": len(symbols),
        "sembol_siniflari": {k: {"adet": len(v), "ornekler": v[:40]} for k, v in buckets.items()},
        "en_aktif_semboller": symbols.most_common(60),
        "kv_icermeyen_frameler": malformed,
        "ham_frame_ornegi": raw_frames,
        "ham_txt_dosyasi": os.path.abspath(raw_path) if raw_path else None,
        "ham_txt_frame_sayisi": raw_written,
        "ham_txt_bayt": raw_bytes,
        "ham_txt_kesildi": bool(raw_fh is not None and raw_bytes >= raw_budget),
    })
    return out


# --------------------------------------------------------------------------

W = 60


LABEL_W = 34


def line_start(label):
    """Etiketi ve noktalari yazar, satiri acik birakir (sonuc bekleniyor)."""
    dots = "." * max(3, LABEL_W - len(label))
    print(f"{label} {dots} ", end="", flush=True)


def line_end(value):
    """Acik birakilan satiri sonuclandirir."""
    print(value)


def line(label, value):
    """Tek seferde tam satir."""
    line_start(label)
    line_end(value)


def rest_ozet(rest):
    """REST sonucunu (durum, tek_satir, detay_satirlari) olarak dondurur."""
    if rest is None:
        return None, "atlandi", []
    if rest.get("hata"):
        return False, "HATA", [rest["hata"]]

    sonuc = rest["sonuclar"]
    basarili = [r for r in sonuc if r.get("kayit_sayisi")]
    if basarili:
        r = basarili[0]
        det = [f"{len(basarili)}/{len(sonuc)} sembolden veri geldi, "
               f"sembol basina {r['kayit_sayisi']} bar",
               f"alanlar: {', '.join(r.get('alanlar', []))}"]
        return True, f"VERI GELDI ({len(basarili)}/{len(sonuc)} sembol)", det

    if any(r.get("http_status") == 401 for r in sonuc):
        return False, "YETKI YOK (401)", ["bu makinenin IP'si IdealData'da tanimli degil"]

    hatalar = [r.get("hata") or f"HTTP {r.get('http_status')}" for r in sonuc]
    return False, "VERI YOK", hatalar[:3]


def akis_ozet(st):
    """Akis sonucunu (durum, tek_satir, detay_satirlari) olarak dondurur."""
    if st is None:
        return None, "atlandi", []
    if st.get("hata"):
        return False, "BAGLANTI YOK", [st["hata"]]

    n = st.get("toplam_frame", 0)
    if not n:
        return False, "MESAJ GELMEDI", [
            "baglanti kuruldu ama hic veri akmadi",
            f"login yaniti: {str(st.get('login_yaniti'))[:70] or '(bos)'}",
        ]

    tipler = ", ".join(f"{k}={v}" for k, v in list(st["mesaj_tipi_dagilimi"].items())[:6])
    siniflar = ", ".join(f"{k.split('(')[0]} {v['adet']}"
                         for k, v in st["sembol_siniflari"].items())
    det = [
        f"{n:,} mesaj / {st['gecen_sure_sn']} sn  (~{st['saniyede_mesaj']:,} msg/sn)".replace(",", "."),
        f"farkli sembol : {st['sembol_sayisi']:,}".replace(",", "."),
        f"mesaj tipleri : {tipler}",
        f"sembol turleri: {siniflar}",
    ]
    if st.get("ham_txt_frame_sayisi"):
        kesik = " (ust sinira ulasildi)" if st.get("ham_txt_kesildi") else ""
        adet = f"{st['ham_txt_frame_sayisi']:,}".replace(",", ".")
        mb = st["ham_txt_bayt"] / 1024 / 1024
        det.append(f"ham .txt'ye yazilan: {adet} frame, {mb:.1f} MB{kesik}")
    return True, f"VERI GELDI ({n:,} mesaj)".replace(",", "."), det


def main():
    ap = argparse.ArgumentParser(description="IdealData veri kaynagi kesif scripti")
    ap.add_argument("--seconds", type=int, default=25, help="akis dinleme suresi (varsayilan 25)")
    ap.add_argument("--skip-rest", action="store_true", help="REST testini atla")
    ap.add_argument("--skip-stream", action="store_true", help="akis testini atla")
    ap.add_argument("--out", default=None, help="cikti dosyasi yolu")
    ap.add_argument("--raw-mb", type=float, default=5.0,
                    help="ham akis .txt dosyasi icin ust sinir, MB (varsayilan 5)")
    args = ap.parse_args()

    register_secrets()
    user, pwd, api_key = STREAM_USERNAME, STREAM_PASSWORD, REST_API_KEY

    eksik = [ad for ad, d in (("STREAM_USERNAME", user), ("STREAM_PASSWORD", pwd),
                              ("IDEALDATA_API_KEY", api_key)) if not d]
    if eksik:
        print(f"\n  HATA: kimlik bilgisi eksik -> {', '.join(eksik)}")
        print("  .env.example dosyasini .env olarak kopyalayip doldurun.\n")
        sys.exit(1)

    print()
    print("  IdealData Probe — Derivex veri kesfi")
    print("  " + "-" * (W - 2))

    report = {
        "olusturulma": datetime.now(timezone.utc).isoformat(),
        "makine": {"python": sys.version.split()[0], "platform": sys.platform},
    }

    # 1) disa acik IP — IdealData'nin gordugu adres (allowlist icin gerekli)
    try:
        _, ip = http_get("https://api.ipify.org", timeout=8)
        report["disa_acik_ip"] = ip.strip()
    except Exception as e:
        report["disa_acik_ip"] = f"belirlenemedi ({type(e).__name__})"
    line("  Bu makinenin IP'si", report["disa_acik_ip"])

    # 2) REST
    line_start("  REST  (tarihsel veri)")
    if args.skip_rest:
        report["bolum_a_rest"] = None
    else:
        report["bolum_a_rest"] = probe_rest(api_key)
    r_ok, r_tek, r_det = rest_ozet(report.get("bolum_a_rest"))
    line_end(r_tek)

    # 3) Akis
    damga = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = args.out or f"idealdata_probe_{damga}.json"
    raw_path = os.path.splitext(path)[0] + "_akis_ham.txt"

    line_start(f"  AKIS  (canli veri, {args.seconds} sn)")
    if args.skip_stream:
        report["bolum_b_akis"] = None
    else:
        report["bolum_b_akis"] = probe_stream(
            user, pwd, args.seconds, raw_path=raw_path, raw_budget_mb=args.raw_mb)
    s_ok, s_tek, s_det = akis_ozet(report.get("bolum_b_akis"))
    line_end(s_tek)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scrub(report), f, ensure_ascii=False, indent=2)

    # --- tek satirlik karar ---
    calisan = [x for x in (r_ok, s_ok) if x is not None]
    if calisan and all(calisan):
        karar = "VERI ALINDI"
    elif any(calisan):
        karar = "KISMEN ALINDI"
    else:
        karar = "VERI ALINAMADI"

    print()
    print("  " + "=" * (W - 2))
    print(f"   SONUC:  {karar}")
    print("  " + "=" * (W - 2))

    for baslik, det in (("REST", r_det), ("AKIS", s_det)):
        if det:
            print(f"   {baslik}:")
            for d in det:
                print(f"     - {d}")

    size_kb = os.path.getsize(path) / 1024
    print()
    if karar == "VERI ALINAMADI":
        print(f"   Cikti kaydedildi ama icinde ornek veri yok:")
        print(f"   {os.path.abspath(path)}")
        print()
        print(f"   Bu makinenin IP'si ({report['disa_acik_ip']}) IdealData'da tanimli degil")
        print("   gorunuyor. Yetki tanimlandiktan sonra tekrar calistirin.")
    else:
        print("   GERI GONDERILECEK DOSYALAR:")
        print(f"   1) {os.path.abspath(path)}")
        print(f"      ({size_kb:.0f} KB — ozet, alan haritasi, sembol listesi)")
        st = report.get("bolum_b_akis") or {}
        ham = st.get("ham_txt_dosyasi")
        if ham and os.path.isfile(ham):
            ham_mb = os.path.getsize(ham) / 1024 / 1024
            print(f"   2) {ham}")
            print(f"      ({ham_mb:.1f} MB — ham akis verisi, uzerinde calisilacak olan)")
        print()
        print("   Sifre ve api_key cikti icinde maskelenmistir.")
    print()


if __name__ == "__main__":
    main()
