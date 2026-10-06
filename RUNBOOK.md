# Runbook — Derivex Dashboard

İşletim prosedürleri. Buradaki arıza senaryolarının çoğu geliştirme
sırasında gerçekten yaşandı; tahmini değil.

Kurulum için [KURULUM.md](KURULUM.md), mimari için [README.md](README.md).

---

## 1. Günlük işletim

### Başlatma

```bash
python3 start.py                 # mock veri (varsayılan)
DATA_MODE=1 python3 start.py     # canlı IdealData akışı
```

Beş adım sırayla çalışır: ön kontroller → veri erişimi → dashboard →
kalıcılık (geri yükleme) → veri kaynağı. Herhangi biri başarısız olursa
hangisi olduğu ekrana yazılır.

**Durdurma:** `Ctrl+C`. Dashboard ölürse `start.py` her şeyi birlikte kapatır
(arayüz olmadan devam etmenin anlamı yok). Köprü ölürse arayüz ayakta kalır,
yalnızca tablolar donar — bu bilinçli.

### Sağlık kontrolü

İlk bakılacak yer. Başlıktaki rozet her sayfada görünür; ayrıntı
`Market → Summary`.

```bash
curl -s http://127.0.0.1:5173/health | python3 -m json.tool
```

| Durum | Anlamı | Aksiyon |
|---|---|---|
| `healthy` | Tüm kontroller taze | — |
| `degraded` | Bir şey bayat, sistem çalışıyor | Bak, acele etme |
| `unhealthy` | Çalışmayı engelleyen bir şey var | Müdahale et |

**Seans dışında `degraded` normaldir.** Veri akmaması arıza değil. Konteyner
yeniden başlatmayı yalnızca `unhealthy` gerektirir.

### Zamanlanmış işler

`start.py` çalışırken arka planda dönerler:

| İş | Aralık | Değişken |
|---|---|---|
| Depo özeti → arayüz | 60 sn | — |
| Model sürümü senkronu | 60 sn | — |
| NSS eğri kalibrasyonu | 300 sn | `CURVE_REFRESH_SEC` |
| GARCH/EGARCH + tahmin puanlama | 900 sn | `GARCH_REFRESH_SEC` |
| Sağlık yoklaması | 30 sn | `HEALTH_POLL_SEC` |

Elle tetiklemek için:

```bash
python3 fit_curve.py          # faiz eğrisi
python3 garch.py              # volatilite tahminleri + puanlama
python3 store.py --push-stats  # depo özetini arayüze gönder
```

---

## 2. Arıza senaryoları

### "Veri akmıyor" — tablolar boş veya donuk

1. **Sağlığa bak:** hangi kaynak bayat? `/health` kaynak başına yaş verir.
2. **Köprü ayakta mı?**
   ```bash
   pgrep -fl "mock_feed|bridge_stream"
   tail -30 bridge.log
   ```
3. **Canlı modda IP izni:** en sık sebep. `bridge.log`'da `IpNotDefined` veya
   REST'ten 401 görürsünüz.
   ```bash
   python3 idealdata_probe.py --seconds 12
   ```
   `VERI ALINAMADI` çıkarsa makinenin dış IP'si IdealData'da tanımlı değildir.
   Kod sorunu değil — IP'yi tanımlatın.
4. **"already connected":** aynı kullanıcı başka bir yerde bağlı. Diğer
   oturumu (feeder/bridge/probe) kapatın; IdealData tek oturuma izin veriyor.

### Port kullanımda

```
[!!] port 5173 kullanimda
```

```bash
lsof -ti:5173 | xargs kill      # eski süreci kapat
python3 start.py --port 5174    # ya da başka port
```

### Eğri yok — "no fitted curve yet"

Normalde `start.py` açılıştan ~20 sn sonra kendiliğinden uydurur. Hâlâ boşsa
vadeli oranlar akmıyordur (eğri en az 3 vade gözlemi ister).

```bash
curl -s "http://127.0.0.1:5173/api/futures-rates?all=1" | head -c 300
python3 fit_curve.py            # elle dene, hata mesajını oku
```

### Depo yazma hatası

`/health` içinde `Store: unhealthy` ve bir `write_error`. Diskin dolu olması
ya da dosya izni en olası sebepler.

```bash
df -h .
ls -la derivex.db*
python3 store.py --stats
```

Depo bir **yan kayıttır**: yazamasa bile akış sürer, yalnızca geçmiş
birikmez. Yani acil değil ama sessiz de bırakılmamalı.

### Fcst sütunu boş

GARCH en az 60 günlük getiri ister. Depo yeniyse bu normaldir ve `garch.py`
bunu açıkça yazar:

```
[GARCH] 3 ticker, gunluk kapanis birikiyor; dosya yazilmadi
```

Boşluğu makul görünen bir sayıyla doldurmamak bilinçli bir karar.

### XLSX içe aktarma çalışmıyor

```bash
npm install                     # node_modules/xlsx kurulur
```

Sunucu bağımlılıksız çalışır; `xlsx` yalnızca Risk sekmesindeki portföy içe
aktarımı için gerekir.

### Volatilite eğrisi "FALLBACK" veya "NOT CALIBRATED" diyor

Arıza değil, bilgi. Hangi basamakta olunduğu yazılıdır:

| Yazı | Anlamı |
|---|---|
| `Calibrated …` | Normal |
| `FALLBACK · … did not converge, using …` | Alternatif model kullanıldı |
| `FALLBACK · using last good … calibration` | Taze uyum yok, eski parametreler |
| `NOT CALIBRATED · seed parameters` | Hiç kalibrasyon yok, eğri yalnızca gösterge |
| `STALE DATA: prices 2h old` | Parametreler iyi ama fiyatlar eski |

Son ikisi sürekli görünüyorsa kotasyon noktası yetersizdir — opsiyon zinciri
akışına bakın.

---

## 3. Bakım

### Depo büyümesi

```bash
python3 store.py --stats
python3 store.py --prune 90      # 90 günden eski tick'leri sil
```

Budama **tick tablolarını** siler, **günlük barları ve model sürümlerini
korur** — hacmi tick'ler yapar, değeri günlük seri taşır. GARCH'ın girdisi
budamadan etkilenmez.

### Kapasite ölçümü

```bash
python3 bench_stream.py           # ayrıştırma (yan etkisiz)
python3 bench_stream.py --post    # POST yolunu da ölç
```

Beklenen mertebe: ayrıştırma ~800k msg/sn, toplu POST ~24k ticker/sn. Bunun
çok altına düşerse makinede başka bir yük vardır.

### Model doğruluğu

```bash
python3 backtest.py --ufuk 30    # geçmiş veride model karşılaştırma
```

Canlı doğruluk takibi `garch.py` ile otomatik işler; sonuç Discount Rate
sekmesindeki **Forecast Accuracy** bloğunda. **Saf dayanak modelleri
geçiyorsa** model karmaşıklığının karşılığı yok demektir — çıktı bunu yazar.

### Denetim izi doğrulama

```bash
curl -s http://127.0.0.1:5173/api/audit/verify | python3 -m json.tool
```

`valid: false` dönerse geçmişe müdahale edilmiştir. Zincir kopuk olsa bile
dosya silinmemeli; inceleme için saklayın.

---

## 4. Ortam değişkenleri

| Değişken | Varsayılan | İşlevi |
|---|---|---|
| `DATA_MODE` | `0` | 0 = mock, 1 = canlı |
| `PORT` | `5173` | Dashboard portu |
| `HEALTH_FRESH_SEC` | `60` | Akış tazelik eşiği |
| `HEALTH_STALE_SEC` | `900` | Periyodik iş bayatlık eşiği |
| `HEALTH_POLL_SEC` | `30` | Sağlık yoklama aralığı |
| `HEALTH_CONFIRM` | `2` | Alarm için üst üste gözlem |
| `ALERT_WEBHOOK_URL` | — | Durum değişiminde POST edilecek adres |
| `CURVE_REFRESH_SEC` | `300` | Eğri kalibrasyon aralığı |
| `GARCH_REFRESH_SEC` | `900` | GARCH yeniden hesap aralığı |
| `STORE_DB` | `derivex.db` | Veritabanı yolu |
| `STORE_ENABLED` | `1` | `0` ile kalıcılık kapatılır |
| `GARCH_MIN_RETURNS` | `60` | GARCH için asgari getiri |

Kimlik bilgileri kodda gömülüdür; `.env` ya da ortam değişkeni ezer
(`.env.example`).

---

## 5. Sürüm yönetimi

```bash
git config core.hooksPath .githooks    # klonladıktan sonra bir kez
```

Kanca `.env` dosyalarını, yeni şifre/anahtarları ve aşırı büyük dosyaları
commit öncesi yakalar. **Yanlış alarm verirse `--no-verify` ile geçmeyin** —
deseni düzeltin; aksi halde kanca zamanla işlevsizleşir.

Her değişiklik öncesi:

```bash
npm test        # Node + Python testlerinin tamamı
```

CI (GitHub Actions) her push'ta testleri ve Docker imajını çalıştırır.
Docker derlemesi `sqlite3` varlığını da doğrular — eksik olsaydı kalıcılık
çalışma anında sessizce devre dışı kalırdı.

### Konteyner

```bash
docker build -t derivex-dashboard .
docker run --rm -p 5173:5173 derivex-dashboard
```

`HEALTHCHECK` `/health`'e bakar ve yalnızca `unhealthy` durumunu başarısızlık
sayar; `degraded` konteyneri yeniden başlatmaz.

---

## 6. Bilinen kısıtlar

Bunlar arıza değil, bilinen sınırlar:

- **Kimlik doğrulama yok.** `/api/*` uçlarının tamamı açık. Dışa açık bir
  ağda çalıştırmayın.
- **Ölçek sınırı.** Toplu POST sonrası ~24k msg/sn. Daha fazlası için
  mesajlaşma altyapısı gerekir.
- **Spot olmayan dayanaklar.** Endeks/FX/emtia vadelilerinde ima edilen getiri
  gösterilmez; akışta spot kotasyonu yok (`Market → Other Assets`).
- **Tek süreç.** Yatay ölçekleme yok; durum bellekte ve tek bir SQLite
  dosyasında.
- **Mock ve canlı geçmiş ayrıdır.** Mock modda biriken geçmiş `DATA_MODE=1`'e
  geçince kullanılmaz. Doğrusu budur: üretilmiş fiyatlardan uydurulmuş
  volatilite piyasa volatilitesi değildir.
