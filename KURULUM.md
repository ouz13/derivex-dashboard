# Derivex Dashboard — Kurulum ve Çalıştırma

Verinin aktığı (IdealData'da IP'si tanımlı) makinede çalıştırmak için.

---

# ÖZET — 3 adım

```bash
# 1. Node.js 14+ ve Python 3.8+ kurulu olsun, sonra:
pip3 install requests

# 2. Bu klasöre gir
cd derivex-dashboard

# 3. Başlat
python3 start.py
```

Tarayıcıda aç: **http://127.0.0.1:5173**

Varsayılan olarak **mock veri** ile açılır (IdealData erişimi gerekmez) ve
ekranın üstünde kırmızı uyarı şeridi görünür. Canlı veri için:

```bash
DATA_MODE=1 python3 start.py
```

Durdurmak için `Ctrl+C`.

`start.py` sırayla şunları yapar: ön kontroller → veri erişim testi →
dashboard sunucusu → veri köprüsü. Bir adım başarısız olursa nedenini
söyleyip durur.

Faydalı seçenekler:

| Komut | Ne yapar |
|---|---|
| `python3 start.py --port 8080` | Farklı portta çalıştırır |
| `python3 start.py --check-only` | Yalnızca veri erişimini test eder |
| `python3 start.py --no-check` | Veri testini atlar (IP yetkisi yokken arayüzü görmek için) |

Aşağısı elle kurulum ve sorun giderme içindir.

---

## 0. Gereksinimler

| | Sürüm | Kontrol |
|---|---|---|
| Node.js | 14+ | `node --version` |
| Python | 3.8+ | `python3 --version` |
| `requests` | — | `python3 -c "import requests"` |

`requests` yoksa:

```bash
pip3 install requests
```

Başka bağımlılık yok. `npm install` gerekmiyor — sunucu yalnızca Node'un
yerleşik modüllerini kullanıyor.

---

## 1. Ön kontrol: veri akıyor mu?

Herhangi bir şey başlatmadan önce bu makinenin IdealData'ya erişebildiğini
doğrulayın:

```bash
python3 idealdata_probe.py
```

Çıktıda şunu görmeniz gerekir:

```
  REST  (tarihsel veri) ........... VERI GELDI (3/3 sembol)
  AKIS  (canli veri, 25 sn) ....... VERI GELDI (12.480 mesaj)

   SONUC:  VERI ALINDI
```

`YETKI YOK (401)` veya `BAGLANTI YOK` görüyorsanız durun — bu makinenin IP'si
IdealData'da tanımlı değildir, devam etmenin anlamı yok. Probe çıktısında bu
makinenin dışa açık IP'si yazıyor; IdealData'ya tanımlatılması gereken odur.

> **Önemli:** Stream'de tek login hakkı var. Başka bir
> yerde açık oturum varsa (`feeder.py`, başka bir bridge, ikinci bir probe)
> login reddedilir. Önce onları kapatın.

---

## 2. Dashboard sunucusunu başlatın

```bash
node _frontend_runtime.js
```

Beklenen çıktı:

```
Frontend shell ready: http://127.0.0.1:5173 (bind 127.0.0.1)
```

Bu terminali açık bırakın.

Farklı port isterseniz: `PORT=8080 node _frontend_runtime.js`
Ağdaki başka makinelerden erişilsin isterseniz: `HOST=0.0.0.0 node _frontend_runtime.js`

---

## 3. Veri köprüsünü başlatın

**Yeni bir terminalde**, aynı klasörde:

```bash
python3 bridge_stream.py
```

Beklenen çıktı:

```
[INFO] Connecting to ssdata1.idealdata.com.tr:9443 ... (attempt 1/6)
[INFO] Stream bridge started.
[INFO] Frontend: http://127.0.0.1:5173
[INFO] Maturities: ['Sep(0926, DTM=2)', 'Oct(1026, DTM=34)', ...]
```

Ardından periyodik olarak şöyle satırlar akmaya başlar:

```
[SUMMARY] spot_posts=377  rates_posts=152  opts_posts=104
```

Bu sayılar **artıyorsa veri akıyor demektir.** Sıfırda kalıyorsa 5. bölüme
bakın.

> 2. adımda portu değiştirdiyseniz köprüye de söyleyin:
> `FRONTEND_BASE_URL=http://127.0.0.1:8080 python3 bridge_stream.py`

---

## 4. Açın

```
http://127.0.0.1:5173
```

Kontrol listesi:

- **Market → Futures**: Spot Mid dolu, Oct/Nov getiri sütunlarında %25–35
  aralığında değerler
- **Market → Options**: Veri gelen bir dayanak seçin; C Bid/Ask ve IV dolu olmalı
- **Tools → Realized Vols**: Tablo dolu olmalı (bu veri akıştan değil,
  `servisapi.idealdata.com.tr` REST ucundan doğrudan Node tarafından çekilir —
  aynı IP yetkisini gerektirir)

Elle girilmesi gerekenler (akıştan gelmez, tarayıcıda saklanır):

- **Tools → Discount Rate** — vade bazında iskonto oranları
- **Tools → Dividends** — temettü tutarı ve tarihleri
- **Tools → Risk** — portföyü XLSX olarak içe aktarın

---

## 5. Sorun giderme

**`spot_posts=0` kalıyor**
Köprü frontend'e ulaşamıyor. Portların eşleştiğini doğrulayın:
`curl http://127.0.0.1:5173/api/spot?ticker=AKBNK`

**`login rejected: user already connected`**
Başka bir stream oturumu açık. Kapatıp tekrar deneyin.

**Futures'ta Eylül/vade sonu sütunu saçma değerler gösteriyor (%400, -%300)**
Beklenen davranış, hata değil. Yıllık getiri `((F−S)/S)/DTM×365` ile
hesaplanıyor; vadeye 1–2 gün kalınca payda küçüldüğü için en ufak fiyat farkı
yüzlerce yüzdeye çıkıyor. Ölçüm: DTM 34 ve 63'te 98 oranın tamamı makul,
DTM 2'de 78 oranın 50'si %100'ü aşıyor. Vade geçince kendiliğinden düzelir.
Rahatsız ediyorsa vade sonuna yakın sütun gizlenebilir.

**Bazı hisselerde Spot Mid 0.000**
O sembolde yalnızca tek taraf (sadece alış ya da sadece satış) geliyor, orta
fiyat hesaplanamıyor. Likit sembollerde gün içinde kendiliğinden dolar.

**Realized Vols boş**
REST ucu (`servisapi.idealdata.com.tr`) IP yetkisi istiyor. `idealdata_probe.py`
çalıştırıp REST satırına bakın.

---

## 6. Dosyalar

| Dosya | Görev |
|---|---|
| `_frontend_runtime.js` | Dashboard sunucusu (Node) — arayüz + `/api/*` uçları |
| `bridge_stream.py` | **TCP akış köprüsü** — canlı veri kaynağı |
| `bridge_http.py` | HTTP köprüsü (alternatif; `192.168.1.106:8000` fiyat servisini kullanır) |
| `idealdata_probe.py` | Bağlantı/yetki teşhis aracı |
| `*.json` | Başlangıç verileri (temettü, GARCH tahminleri, pricer kaydı) |

Veri akışı:

```
IdealData TCP  ──▶  bridge_stream.py  ──▶  /api/spot
(ssdata1:9443)                             /api/futures-rates     ──▶  Tarayıcı
                                           /api/options-chain
IdealData REST ──────────────────────▶  (Node doğrudan çağırır)
(servisapi)                                Realized Vols
```

---

## 7. Kimlik bilgileri

IdealData test hesabının bilgileri kodda gömülüdür — kurulumda hiçbir şey
girmeniz gerekmez. Başka bir hesapla çalışmak isterseniz ortam değişkeni ya da
`.env` dosyası bunları ezer:

| Değişken | Ne |
|---|---|
| `STREAM_USERNAME` | IdealData stream kullanıcı adı |
| `STREAM_PASSWORD` | IdealData stream şifresi |
| `IDEALDATA_API_KEY` | REST (`servisapi`) anahtarı — Realized Vols için |

```bash
export STREAM_USERNAME=... STREAM_PASSWORD=... IDEALDATA_API_KEY=...
python3 start.py
```

Repo private'tır. Dışarıya açılacak olursa bu bilgilerin önce yenilenmesi
gerekir.
