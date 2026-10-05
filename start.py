#!/usr/bin/env python3
"""
start.py — Derivex Dashboard'u tek komutla ayaga kaldirir.

    python3 start.py

Sirayla: on kontroller -> dashboard sunucusu -> veri koprusu.
Durdurmak icin Ctrl+C (ikisi de birlikte kapanir).

Secenekler:
    --port 5173        dashboard portu
    --no-check         acilis veri erisim testini atla
    --check-only       yalnizca erisim testini calistir, sunucu baslatma
"""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

KOK = os.path.dirname(os.path.abspath(__file__))
# Varsayilan port ortam degiskeninden okunur: konteynerde PORT verildiginde
# start.py'nin onu yok sayip 5173'te acmasi, EXPOSE edilen port ile uyusmazlik
# yaratiyordu. --port bayragi yine her seyin ustundedir.
try:
    VARSAYILAN_PORT = int(os.environ.get("PORT", "5173"))
except ValueError:
    VARSAYILAN_PORT = 5173

# Veri kaynagi anahtari:  0 = MOCK (uretilmis veri),  1 = CANLI (IdealData)
# _frontend_runtime.js de ayni degiskeni okur.
DATA_MODE = int(os.environ.get("DATA_MODE", "0"))
MOCK_MODE = DATA_MODE == 0

_surecler = []


# ---------------------------------------------------------------- yardimcilar

def yaz(durum, mesaj):
    isaret = {"ok": "  [OK]  ", "hata": "  [!!]  ", "bilgi": "  ...   "}[durum]
    print(f"{isaret}{mesaj}", flush=True)


def baslik(m):
    print(f"\n  {m}\n  " + "-" * 56, flush=True)


def port_dolu(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def kapat_hepsi(*_):
    print("\n  kapatiliyor...", flush=True)
    for ad, p in _surecler:
        if p.poll() is None:
            p.terminate()
    for ad, p in _surecler:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    print("  durduruldu.\n", flush=True)
    sys.exit(0)


# ---------------------------------------------------------------- 1) kontrol

def on_kontrol(port):
    baslik("1/4  On kontroller")
    tamam = True

    if sys.version_info < (3, 8):
        yaz("hata", f"Python 3.8+ gerekli (mevcut {sys.version.split()[0]})")
        tamam = False
    else:
        yaz("ok", f"Python {sys.version.split()[0]}")

    node = shutil.which("node")
    if not node:
        yaz("hata", "node bulunamadi — Node.js 14+ kurun: https://nodejs.org")
        tamam = False
    else:
        try:
            v = subprocess.run([node, "--version"], capture_output=True, text=True,
                               timeout=10).stdout.strip()
            yaz("ok", f"Node {v}")
        except Exception:
            yaz("ok", "Node bulundu")

    try:
        import requests  # noqa: F401
        yaz("ok", "requests paketi")
    except ImportError:
        yaz("hata", "requests yok — kurun:  pip3 install requests")
        tamam = False

    for f in ("_frontend_runtime.js", "mock_feed.py" if MOCK_MODE else "bridge_stream.py"):
        if os.path.isfile(os.path.join(KOK, f)):
            yaz("ok", f)
        else:
            yaz("hata", f"{f} bulunamadi — paket eksik")
            tamam = False

    if port_dolu(port):
        yaz("hata", f"port {port} kullanimda — baska bir dashboard acik olabilir")
        yaz("bilgi", f"kapatin ya da:  python3 start.py --port {port + 1}")
        tamam = False
    else:
        yaz("ok", f"port {port} bos")

    return tamam


# ---------------------------------------------------------------- 2) veri testi

def veri_testi():
    baslik("2/4  Veri erisimi")
    probe = os.path.join(KOK, "idealdata_probe.py")
    if not os.path.isfile(probe):
        yaz("bilgi", "idealdata_probe.py yok, test atlandi")
        return True

    yaz("bilgi", "IdealData baglantisi deneniyor (~30 sn)...")
    try:
        r = subprocess.run([sys.executable, probe, "--seconds", "12"],
                           capture_output=True, text=True, timeout=180, cwd=KOK)
    except subprocess.TimeoutExpired:
        yaz("hata", "test zaman asimina ugradi")
        return False

    cikti = r.stdout
    if "VERI ALINDI" in cikti:
        yaz("ok", "akis ve REST calisiyor")
        return True
    if "KISMEN" in cikti:
        yaz("hata", "yalnizca bir kaynak calisiyor:")
        for ln in cikti.splitlines():
            if "REST" in ln or "AKIS" in ln:
                print("         " + ln.strip(), flush=True)
        return True                      # yine de devam edilebilir
    yaz("hata", "veri alinamadi:")
    for ln in cikti.splitlines():
        if "REST" in ln or "AKIS" in ln or "IP" in ln:
            print("         " + ln.strip(), flush=True)
    return False


# ---------------------------------------------------------------- 3) dashboard

def dashboard_baslat(port):
    baslik("3/4  Dashboard sunucusu")
    log = open(os.path.join(KOK, "dashboard.log"), "w")
    ortam = dict(os.environ, PORT=str(port))
    p = subprocess.Popen(["node", "_frontend_runtime.js"], cwd=KOK,
                         stdout=log, stderr=subprocess.STDOUT, env=ortam)
    _surecler.append(("dashboard", p))

    for _ in range(30):                  # 15 sn bekle
        if port_dolu(port):
            yaz("ok", f"http://127.0.0.1:{port}")
            return True
        if p.poll() is not None:
            yaz("hata", "sunucu basladiktan hemen sonra kapandi — dashboard.log'a bakin")
            return False
        time.sleep(0.5)

    yaz("hata", "sunucu 15 sn icinde hazir olmadi — dashboard.log'a bakin")
    return False


# ---------------------------------------------------------------- 4) kopru

def kopru_baslat(port):
    baslik("4/4  Veri kaynagi")
    script = "mock_feed.py" if MOCK_MODE else "bridge_stream.py"
    log_yolu = os.path.join(KOK, "bridge.log")
    log = open(log_yolu, "w")
    ortam = dict(os.environ,
                 FRONTEND_BASE_URL=f"http://127.0.0.1:{port}",
                 DATA_MODE=str(DATA_MODE))
    p = subprocess.Popen([sys.executable, "-u", script], cwd=KOK,
                         stdout=log, stderr=subprocess.STDOUT, env=ortam)
    _surecler.append(("kopru", p))
    yaz("bilgi", f"{script} baslatiliyor...")

    for _ in range(60):                  # 30 sn
        time.sleep(0.5)
        if p.poll() is not None:
            yaz("hata", "kopru kapandi — bridge.log:")
            _log_kuyrugu(log_yolu, 6)
            return False
        metin = _log_oku(log_yolu)
        if MOCK_MODE and "[MOCK]" in metin:
            yaz("ok", "uretilmis veri besleniyor")
            return True
        if "Stream bridge started" in metin:
            yaz("ok", "akisa baglandi")
            return True
        if "already connected" in metin or "zaten bagli" in metin:
            yaz("hata", "login reddedildi: bu kullanici baska bir yerde bagli")
            yaz("bilgi", "diger oturumu (feeder/bridge/probe) kapatip tekrar deneyin")
            return False

    yaz("hata", "30 sn icinde baglanamadi — bridge.log:")
    _log_kuyrugu(log_yolu, 6)
    return False


def _log_oku(yol):
    try:
        with open(yol, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _log_kuyrugu(yol, n):
    for ln in _log_oku(yol).splitlines()[-n:]:
        print("         " + ln, flush=True)


# ---------------------------------------------------------------- izleme

def izle(port):
    print("\n  " + "=" * 56)
    print(f"   CALISIYOR   ->   http://127.0.0.1:{port}")
    print("  " + "=" * 56)
    print("   Durdurmak icin Ctrl+C")
    print("   Kayitlar: dashboard.log, bridge.log\n", flush=True)

    log_yolu = os.path.join(KOK, "bridge.log")
    son = ""
    kopru_uyarildi = False
    while True:
        time.sleep(5)
        for ad, p in _surecler:
            if p.poll() is None:
                continue
            if ad == "dashboard":
                # Arayuz olmadan devam etmenin anlami yok.
                yaz("hata", "dashboard durdu — kapatiliyor")
                kapat_hepsi()
            elif not kopru_uyarildi:
                # Kopru olmadan arayuz ayakta kalir, sadece tablolar bos gelir.
                kopru_uyarildi = True
                yaz("hata", "veri koprusu durdu — arayuz calisiyor ama veri akmiyor")
                yaz("bilgi", f"yeniden denemek icin:  python3 {'mock_feed.py' if MOCK_MODE else 'bridge_stream.py'}")

        # koprunun kendi ozet satirini yansit
        for ln in reversed(_log_oku(log_yolu).splitlines()):
            if "spot_posts" in ln:
                if ln != son:
                    son = ln
                    print(f"   {time.strftime('%H:%M:%S')}  {ln.strip()}", flush=True)
                break


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Derivex Dashboard baslatici")
    ap.add_argument("--port", type=int, default=VARSAYILAN_PORT)
    ap.add_argument("--no-check", action="store_true", help="veri testini atla")
    ap.add_argument("--check-only", action="store_true", help="yalnizca veri testi")
    a = ap.parse_args()

    signal.signal(signal.SIGINT, kapat_hepsi)
    signal.signal(signal.SIGTERM, kapat_hepsi)

    print("\n  DERIVEX DASHBOARD" + ("   [MOCK VERI]" if MOCK_MODE else "   [CANLI VERI]"))
    print("  " + "=" * 56)

    if a.check_only:
        sys.exit(0 if veri_testi() else 1)

    if not on_kontrol(a.port):
        print("\n  On kontroller basarisiz. Yukaridaki [!!] satirlarini giderin.\n")
        sys.exit(1)

    if MOCK_MODE:
        baslik("2/4  Veri erisimi")
        yaz("ok", "MOCK mod — IdealData erisimi gerekmiyor")
        yaz("bilgi", "canli veri icin:  DATA_MODE=1 python3 start.py")
    elif not a.no_check and not veri_testi():
        print("\n  Veri alinamiyor. Bu makinenin IP'si IdealData'da tanimli degil")
        print("  gorunuyor. Yine de baslatmak icin:  python3 start.py --no-check\n")
        sys.exit(1)

    if not dashboard_baslat(a.port):
        kapat_hepsi()
    if not kopru_baslat(a.port):
        print("\n  Dashboard calisiyor ama veri akmiyor.")
        print(f"  Arayuz: http://127.0.0.1:{a.port}  (tablolar bos olacak)\n")

    izle(a.port)


if __name__ == "__main__":
    main()
