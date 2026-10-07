# Nasıl çalıştırılır

Temiz bir makinede sıfırdan çalıştırma. **5 dakika.**

## Gereken

| | Sürüm | Kontrol |
|---|---|---|
| Python | 3.9+ | `python3 --version` |
| Node | 20+ | `node --version` |
| `requests` | — | `pip3 install requests` |

macOS'ta Node yoksa: `brew install node`

Başka hiçbir şey gerekmiyor. Veritabanı yok, hesap yok, API anahtarı yok.

## Çalıştır

```bash
git clone https://github.com/ouz13/derivex-dashboard.git
cd derivex-dashboard
pip3 install requests
python3 start.py
```

Tarayıcıda **http://127.0.0.1:5173** açılır. Durdurmak için `Ctrl+C`.

Port doluysa: `python3 start.py --port 8080`

## Demo verisini doldur (önerilir)

Yukarıdaki haliyle fiyatlar, opsiyon zincirleri, Greeks ve fiyatlama
**anında** çalışır. Ama **geçmişe dayanan ekranlar boş kalır** — depo o
makinede yeni oluştuğu için henüz tek günlük veri var:

- Realized Vols / GARCH tahminleri (60 getiri gerekiyor, 1 gün var)
- Korelasyon matrisi (en az 2 ortak gün gerekiyor)

Bir yıllık geçmiş üretmek için, pano çalışırken **ikinci bir terminalde**:

```bash
cd derivex-dashboard
python3 seed_demo_history.py      # ~250 iş günü üretir
python3 garch.py                  # volatilite tahminleri
python3 correlation.py            # korelasyon matrisi
```

Sonra panoyu `Ctrl+C` ile durdurup `python3 start.py` ile yeniden
başlatın — tüm ekranlar dolu gelir.

Geri almak için: `python3 seed_demo_history.py --temizle`

## Veri nereden geliyor?

**Üretilmiş (mock) veriden.** `mock_feed.py` her saniye fiyat üretir;
GitHub'da **veritabanı yok**, sadece onu üreten kod var. Her klon kendi
verisini sıfırdan oluşturur.

Panonun üstündeki kırmızı bant bunu söylüyor:
*"MOCK DATA — the figures on this screen are generated, not market data."*

Depoya yazılan her satır `MOCK` etiketi taşır ve gerçek piyasa verisiyle
asla aynı seriye girmez. Bu bilinçli bir karar: üretilmiş fiyatlardan
hesaplanmış bir volatilite, piyasadan hesaplanmış gibi görünemesin diye.

## Neye bakmalı

| Sekme | Ne var |
|---|---|
| **Market** | Spot, vadeli getiri eğrisi, opsiyon zincirleri, endeks/döviz/emtia |
| **Tools › Pricer** | BSM / binom / Monte Carlo fiyatlama, Greeks |
| **Tools › Realized Vols** | Gerçekleşmiş volatilite, GARCH + EGARCH tahmini |
| **Tools › Volatility Curve** | Heston / SVI kalibrasyonu, volatilite yüzeyi |
| **Tools › Risk** | Monte Carlo VaR/CVaR, stres testi, korelasyon matrisi |
| **Tools › Discount Rate** | NSS faiz eğrisi, model sürüm geçmişi |
| **Branch** | Şube teklif akışı — **prototip** |

Sağ üstteki renkli nokta servis sağlığıdır; tıklayınca ayrıntı açılır.
Veri akmadığında `degraded` yazması normaldir.

## Sık karşılaşılanlar

**"xlsx kurulu degil"** — Risk sekmesindeki XLSX portföy içe aktarma
çalışmaz, geri kalan her şey çalışır. Gerekirse: `npm install`

**"Volatility calibration: never calibrated"** — kalibrasyon ilk birkaç
dakikada zamanlanmış olarak çalışır; beklemek yeterli.

**Branch sekmesinde menü boş** — opsiyon zincirleri henüz gelmemiştir;
menü kendiliğinden dolar, sayfayı yenilemeye gerek yok.

**Port dolu** — `python3 start.py --port 8080`

## Testler

```bash
python3 -m unittest discover tests     # 376 test
node --test tests/*.mjs                # 197 test
```

## Canlı veri

Proje **mock veriyle demo üretmek üzere** kurgulandı; canlı IdealData
akışı kapsam dışı bırakıldı. Yine de denemek isterseniz
`DATA_MODE=1 python3 start.py` çalışır, ancak makinenin dışa açık
IP'sinin IdealData tarafında tanımlı olması gerekir.

Başka bir veri kaynağı bağlamak gerekirse `feed_schema.py` tam da bunun
için var: bir adaptör yazmak yeterli, akış döngüsüne dokunulmaz
(bkz. README.md › *Ortak veri şeması ve kaynak adaptörleri*).

---

Daha ayrıntılı bilgi: [README.md](README.md) ·
İşletim: [RUNBOOK.md](RUNBOOK.md) ·
Kubernetes: [k8s/README.md](k8s/README.md)
