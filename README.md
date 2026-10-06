# Derivex Dashboard

BIST pay senetleri için vadeli işlem, opsiyon ve volatilite verilerini tek
ekranda toplayan finansal türev platformu. IdealData canlı akışından beslenir.

## Hızlı başlangıç

```bash
pip3 install requests
python3 start.py
```

Ayar gerekmez — IdealData bağlantı bilgileri kodda gömülüdür.

## Veri kaynağı anahtarı

`DATA_MODE` iki modu seçer:

| Değer | Mod | Veri kaynağı |
|---|---|---|
| `0` *(varsayılan)* | **MOCK** | `mock_feed.py` — üretilmiş örnek veri, IdealData erişimi gerekmez |
| `1` | **CANLI** | `bridge_stream.py` — gerçek IdealData akışı |

```bash
python3 start.py                 # mock (varsayılan)
DATA_MODE=1 python3 start.py     # canlı
```

Mock moddayken ekranın üstünde kırmızı bir uyarı şeridi görünür. Bu
kasıtlıdır: üretilmiş sayıların piyasa verisi sanılmasını önler.

Mock modda Risk sekmesi de örnek bir portföyle dolu gelir (canlı modda portföy
XLSX ile içeri aktarılır; ilgili uç canlı modda kapalıdır).

Mock veri, canlı moddakiyle **birebir aynı API uçlarından** ve aynı JSON
şeklinde akar; opsiyon fiyatları Black-Scholes'tan üretilir, gerçekleşmiş
volatilite değerleri tahmin dosyasıyla tutarlı tutulur. Yani mock modda
çalışan bir ekran canlı modda da çalışır.

Tarayıcıda: **http://127.0.0.1:5173** — durdurmak için `Ctrl+C`.

Ayrıntılı kurulum, sorun giderme ve elle çalıştırma için → [KURULUM.md](KURULUM.md)

## Neler var

| Sekme | İçerik |
|---|---|
| **Futures** | Spot fiyat ve vadeli kontratlardan türetilen yıllık getiri (alış/satış ayrı) |
| **Options** | Opsiyon zinciri, Black-Scholes ile zımni volatilite ve Greeks |
| **Realized Vols** | 15/30/60/90/180 günlük gerçekleşmiş volatilite ve GARCH tahmini |
| **Volatility Curve** | Heston kalibrasyonu, piyasa IV'si ile model karşılaştırması |
| **Risk** | Monte Carlo VaR / CVaR, P&L dağılımı, stres testi senaryoları |
| **Pricer** | Temettü düzeltmeli opsiyon fiyatlayıcı, Greeks ve yöntem karşılaştırması |
| **Discount Rate / Dividends** | İskonto eğrisi ve temettü girişi |

## Mimari

```
IdealData TCP  ──▶  bridge_stream.py  ──▶  /api/spot
(ssdata1:9443)                             /api/futures-rates    ──▶  Tarayıcı
                                           /api/options-chain
IdealData REST ──────────────────────▶  _frontend_runtime.js
(servisapi)                                (Realized Vols)
```

| Dosya | Görev |
|---|---|
| `_frontend_runtime.js` | Dashboard sunucusu (Node, bağımlılıksız) — arayüz + `/api/*` |
| `bridge_stream.py` | TCP akış köprüsü — canlı veri kaynağı |
| `bridge_http.py` | HTTP köprüsü (alternatif kaynak) |
| `start.py` | Tek komutla her şeyi ayağa kaldırır |
| `idealdata_probe.py` | Bağlantı/yetki teşhis aracı |
| `store.py` | SQLite kalıcılık katmanı — zaman serisi, model sürümleme |
| `model_fallback.js` | Kalibrasyon başarısızlığında fallback zinciri |
| `garch.py` | Depodaki geçmişten gerçekleşmiş volatilite ve GARCH(1,1) |
| `yield_curve.py` / `fit_curve.py` | Nelson-Siegel-Svensson eğri uydurma |

### Akış protokolü

Kotasyonlar üç mesaj tipiyle gelir ve **hepsi kısmi güncellemedir** — bir mesaj
yalnızca değişen alanı taşır, bu yüzden sembol başına birleştirilmiş defter
tutulur:

| Tip | Alanlar |
|---|---|
| `YU` | `6`/`9` alış-satış fiyatı, `7`/`10` miktar |
| `WU` | `108`/`109` alış-satış fiyatı, `110`/`111` miktar |
| `DU` | `3` fiyat, `4` miktar, `101` taraf (B/A), `100` derinlik seviyesi (0 = en iyi) |

Sembol biçimleri: spot `THYAO`, vadeli `F_THYAO1026`, opsiyon
`O_KCHOLE1026C210.00` (dayanak, vade, C/P, kullanım fiyatı).

## Geliştirme

IdealData test hesabının bilgileri kodda gömülüdür; kurulum sırasında hiçbir
şey girilmesi gerekmez. Başka bir hesapla çalışmak isterseniz ortam değişkeni
ya da `.env` dosyası bunları ezer (`.env.example` şablonuna bakın).

Repoyu klonladıktan sonra sır koruma kancasını etkinleştirin:

```bash
git config core.hooksPath .githooks
```

Bu kanca `.env` dosyalarını, **yeni** şifre/anahtarları ve aşırı büyük
dosyaları commit öncesinde yakalar. Mevcut IdealData bilgileri bilinçli bir
karar olduğu için muaf tutulmuştur.

## Kalıcılık (SQLite)

Daha önce her şey bellekteydi ve süreç kapandığında kayboluyordu. `store.py`
bunu kalıcı hale getirir: `derivex.db`.

```bash
python3 store.py --stats          # depoda ne var
python3 store.py --restore        # son durumu arayüze geri yükle
python3 store.py --versions nss   # model parametre geçmişi
python3 store.py --prune 90       # 90 günden eski tick'leri sil
```

`start.py` bunları kendiliğinden yapar; elle çalıştırmak gerekmez.

**Veritabanı neden Python tarafında?** Veriyi üreten zaten Python
(`bridge_stream.py`), `sqlite3` her CPython'da gömülü geliyor ve `node:sqlite`
hâlâ deneysel — CI ile Docker imajının çalıştırdığı Node 20'de hiç yok. Node
veritabanını hiç açmaz; ihtiyacı olanı zaten var olan HTTP uçlarından alır.

**Yazma noktası tek.** `post_spot_mid`, `post_futures_rates_batch` ve
`post_options_chain` hem canlı köprünün hem `mock_feed.py`'nin tek geçididir;
depo oraya bağlıdır, dolayısıyla iki mod da kendiliğinden kalıcıdır. Depo bir
yan kayıttır: yazamazsa akış depo olmadan sürer.

**Mock ve canlı geçmiş karışmaz.** Her satır `data_mode` taşır ve her okuma
onu süzer. Üretilmiş fiyatlardan uydurulmuş bir volatilite, piyasadan
uydurulmuş gibi görünemez.

| Tablo | İçerik |
|---|---|
| `spot_tick` / `spot_daily` | Spot zaman serisi ve günlük bar (GARCH girdisi) |
| `futures_rate_tick` | Vadeli ima edilen getiri serisi |
| `option_quote` | Opsiyon zinciri anlık görüntüleri (5 dk örnekleme) |
| `model_version` | Her uydurma **yeni satır** — üzerine yazılmaz |
| `snapshot` | En son tam durum; yeniden başlatmada arayüzü doldurur |

Model sürümleme olmadan "bu opsiyon hangi eğriyle fiyatlandı" sorusu
cevaplanamaz; bu yüzden uydurmalar güncellenmez, biriktirilir. Durum ve sürüm
listesi Discount Rate sekmesindeki **Persistence & Model Versions** panelinde
görünür.

### GARCH(1,1)

Fcst sütunu eskiden durağan bir JSON dosyasından okunuyordu. Depo günlük
kapanışları biriktirdiğinden tahmin artık gerçekten hesaplanabiliyor:

```bash
python3 garch.py                 # tüm ticker'lar
python3 garch.py --ticker THYAO  # tek ticker, ekrana
```

Varyans hedeflemeli maksimum olabilirlik (ω = σ̄²(1−α−β)) kullanılır; serbest
parametre ikiye düştüğü için iki boyutlu kaba + ince ızgara taraması yeter ve
dış bağımlılık gerekmez. Testler bilinen parametrelerle üretilmiş seriden aynı
parametrelerin geri geldiğini doğrular.

**60 günlük getiriden az veri varsa tahmin üretilmez.** Depo yeni dolarken
doğal olarak bu durumdadır; eksikliği makul görünen bir sayıyla doldurmak
kullanıcıya yanlış bilgi vermek olurdu. Hesaplanan tahmin, durağan dosyanın
üzerine yazılmaz — Realized Vols sekmesindeki lookback seçicisine **"Store"**
adıyla ek seçenek olarak eklenir. Depo durağan dosya kadar ticker kapsadığında
varsayılan olur.

## Model başarısızlığında ne oluyor

Kalibrasyon "ya çalışır ya patlar" bir iş değil; üç ayrı şekilde bozuluyor ve
üçünün davranışı farklı:

1. **Hiç uyum yapılamaz** — yeterli kotasyon noktası yok
2. **Uyum yapılır ama kötü** — optimize edici bir şey döndürür, RMSE %40'tır
3. **Veri bayattır** — parametreler iyi ama dayandıkları fiyatlar eski

İkincisi en sinsisi: `fitHeston`/`fitSvi` asla `null` dönmüyordu, her zaman
ızgaranın en iyi noktasını veriyordu. Uyum kalitesine bakılmadığı sürece
"Calibrated" yazısı hiçbir şey anlatmayan bir parametre setini meşrulaştırır.
Artık **IV RMSE'si 5 volatilite puanını aşan uyum reddediliyor.**

**Zincir:**

| Basamak | Koşul | Güvenilir? |
|---|---|---|
| `kalibre` | İstenen model yakınsadı | ✓ |
| `alternatif` | İstenen yakınsamadı, diğeri yakınsadı (Heston ↔ SVI) | ✓ |
| `onceki` | İkisi de olmadı → bu vadenin son başarılı kalibrasyonu | ✗ |
| `onceki-alternatif` | Diğer modelin son başarılı kalibrasyonu | ✗ |
| `varsayilan` | Hiçbiri yok → tohum parametreleri | ✗ |

**Hangi basamakta olunduğu ekranda açıkça yazıyor**, red sebepleriyle
birlikte. Sessizce varsayılana düşmek, kullanıcının kalibre edilmiş bir model
gördüğünü sanmasına yol açardı. Fallback modeli değiştirirse sütun başlıkları
da değişiyor — "Heston IV" başlığı altında SVI değeri gösterilmiyor.

Veri yaşı ayrı bir uyarı: parametreler iyi olsa bile fiyatlar 15 dakikadan
eskiyse `STALE DATA: prices 2h old` yazıyor. Seans dışında son kapanışı görmek
meşru, onu canlı sanmak değil.

Mantık `model_fallback.js` içinde ayrı duruyor — hem tarayıcıya servis
ediliyor hem testlerden `require` ediliyor. Son iyi kalibrasyonlar
`/api/model-params` ucunda tutulup `model_params.json`'a yazılıyor;
`store.py --sync-models` bunları sürüm geçmişine alıyor.

## Faiz eğrisi (Nelson-Siegel-Svensson)

Vadeli işlem fiyatlarından türeyen oranlara pürüzsüz bir eğri uydurulur; ara
vadeler doğrusal interpolasyon yerine modelin kendisinden okunur.

```bash
python3 fit_curve.py            # bir kez uydur
python3 fit_curve.py --watch    # periyodik
```

Sonuç Discount Rate sekmesinde görünür. Uydurma yöntemi λ sabitlendiğinde
model β'larda doğrusal olduğu için kapalı formda çözülür — genel amaçlı bir
optimize edici ve dolayısıyla dış bağımlılık gerekmez.

**Model gözlem sayısına göre küçülür.** VİOP'ta genelde yalnızca 3 aktif
vadeli kontrat vade bulunur; altı parametreli NSS bunu kaldırmaz. 5+ gözlemde
NSS, 3–4 gözlemde Nelson-Siegel (tek kambur) kullanılır. Panel hangisinin
kullanıldığını yazar.

İki şey bilinçli olarak ekranda belirtilir:

- **Tam belirlenmişlik** — parametre sayısı gözlem sayısına eşitse uyum
  zorunlu olarak tam çıkar ve RMSE ≈ 0 olur. Bu uyum kalitesi değildir;
  panel bunu uyarı olarak gösterir.
- **Geçerlilik aralığı** — eğri, en uzun gözlemin 1.5 katından öteye
  örneklenmez. 2 aya yayılmış üç noktadan 2 yıllık oran üretmek, modelin
  söyleyemeyeceği bir şeyi söyletmek olurdu.

## Fiyatlama yöntemleri

| Yöntem | Kullanım |
|---|---|
| Black-Scholes | Avrupa tipi standart opsiyonlar, zımni volatilite, Greeks |
| Binom ağacı (CRR) | Erken kullanım hakkı taşıyan (Amerikan tipi) opsiyonlar |
| Monte Carlo | Portföy düzeyinde VaR / CVaR |

Pricer sekmesi aynı opsiyonu her iki analitik yöntemle fiyatlayıp sapmayı
gösterir. Binom ağacının Avrupa sürümü Black-Scholes'a yakınsamalıdır;
belirgin bir sapma model ya da parametre tarafında sorun olduğuna işaret
eder. Amerikan ile Avrupa arasındaki fark erken kullanım hakkının değeridir
(temettüsüz call'da sıfır, faiz yüksekken derin ITM put'ta belirgin).

## Denetim izi

Fiyatlama ve risk koşuları zincirlenmiş bir kayda yazılır (`audit-log.jsonl`):
her kayıt bir öncekinin SHA-256 özetini taşır. Geçmişteki bir kaydı
değiştirmek ondan sonraki tüm özetleri bozar ve doğrulamada yakalanır.

| Uç | Ne yapar |
|---|---|
| `GET /api/audit` | Son kayıtlar + bütünlük durumu |
| `GET /api/audit/verify` | Yalnızca doğrulama sonucu |
| `POST /api/audit` | Yeni kayıt ekler |

Risk sekmesindeki **Denetim İzi** satırı kayıt sayısını ve zincirin bütün
olup olmadığını gösterir. Her kayda veri modu (MOCK / CANLI) gömülür —
üretilmiş veriyle yapılmış bir koşunun sonradan canlı sanılmaması için.

Bunun engellediği, kayıtların **fark edilmeden değiştirilmesidir**;
silinmelerini engellemez. Gerçek bir blok zinciri değil, append-only hash
zinciridir.

## Risk raporu

Risk sekmesindeki **Rapor İndir (CSV)** butonu portföyü, VaR sonuçlarını ve
stres testi senaryolarını tek dosyada dışa aktarır. Rapor hangi veri modunda
(MOCK / CANLI) üretildiğini başına yazar.

Biçim olarak CSV seçildi: XLSX kütüphanesi `node_modules`'e bağlı ve taze bir
klonda bulunmayabiliyor; rapor üretiminin bağımlılığa takılmaması için.

> **Not:** Risk sekmesindeki XLSX portföy *içe aktarımı* `npm install`
> gerektirir. Kurulmazsa `/xlsx.js` 404 döner ve içe aktarma sessizce
> çalışmaz; `start.py` bu durumda uyarır. Docker imajı bağımlılığı kendisi
> kurar.

## Stres testi

Risk sekmesindeki **Stres Testi** butonu portföyü yedi senaryoda yeniden
değerler: spot ±%10/-%20, volatilite +%50 ve ×2, ve korelasyonun 0.95'e
çıktığı bir kriz senaryosu.

İki ayrı şey raporlanır — **anlık etki** şokun kendisinden doğan kar/zarardır
(tek sayı), **VaR** ise şok sonrası durumda hesaplanan dağılımdan gelir. Tek
bir VaR sayısı "ne olursa ne olur" sorusunu cevaplamaz; bu katman onu
tamamlar.

Senaryo VaR'ları baz senaryoyla doğrudan kıyaslanmamalıdır: büyük bir spot
şoku uzun opsiyonları değersizleştirdiği için geriye kaybedilecek daha az
şey kalır ve şok sonrası VaR küçülebilir. Okunması gereken, anlık etki ile
VaR'ın birlikte verdiği resimdir.

## Docker

```bash
docker build -t derivex-dashboard .
docker run --rm -p 5173:5173 derivex-dashboard                 # mock veri
docker run --rm -p 5173:5173 -e DATA_MODE=1 derivex-dashboard  # canlı veri
```

İmaj Node ve Python'u birlikte taşır; `npm install` gerekmez çünkü sunucu
yalnızca Node'un yerleşik modüllerini kullanır. Konteyner `HOST=0.0.0.0` ile
açılır, aksi halde dışarıdan erişilemez.

Canlı modda konteynerin dışa açık IP'sinin IdealData'da tanımlı olması gerekir.

## CI

`.github/workflows/ci.yml` her push ve pull request'te iki iş çalıştırır:
testler (Node + Python + sözdizimi) ve Docker imajının derlenip gerçekten
ayağa kalkması.

## Testler

```bash
npm test
```

39 test: Python tarafı fiyatlama ve akış ayrıştırmayı (`tests/test_bridge.py`),
Node tarafı Monte Carlo VaR çekirdeğini (`tests/test_risk.mjs`) kapsar. Ek
bağımlılık yok — `unittest` ve `node --test` kullanılır.

JS testleri sunucuyu ayağa kaldırıp `/risk-handler.js`'i gerçekten servis
edildiği haliyle çeker; yani tarayıcıya giden kodun kendisi sınanır.

## Risk: korelasyon varsayımı

Monte Carlo VaR, dayanaklar arası korelasyonu **tek faktörlü** bir modelle
ele alır:

```
z_i = √ρ · z_piyasa + √(1−ρ) · z_özgü
```

`ρ` arayüzden girilir (varsayılan 0.50). BIST hisseleri tek bir piyasada
işlem gördüğü için gerçekte 0.4–0.7 bandında korelasyon beklenir; `ρ = 0`
seçmek riski belirgin biçimde olduğundan düşük gösterir.

Bu gerçek bir kovaryans matrisi değildir — tarihsel seri saklanmadığı için
matris tahmin edilemiyor. Tek ortalama korelasyon parametresi, bağımsızlık
varsayımına göre çok daha gerçekçi ancak varlık çiftlerine özgü ilişkileri
yakalamaz.

## Bilinen davranışlar

**Vade sonuna yakın getiri sütunu çok büyük değerler gösterir.** Yıllık getiri
`((F−S)/S)/DTM×365` ile hesaplanır; vadeye 1–2 gün kalınca payda küçüldüğü için
küçük fiyat farkları yüzlerce yüzdeye çıkar. Ölçüm: DTM 34 ve 63'te tüm oranlar
makul, sorun yalnızca DTM ≤ 2'de görülür. Vade geçince kendiliğinden düzelir.

**Tek taraflı kotasyonlarda Spot Mid boş kalır.** Yalnızca alış ya da yalnızca
satış gelen sembollerde orta fiyat hesaplanamaz. Likit sembollerde gün içinde
dolar.

**Tek stream oturumu hakkı vardır.** Aynı kullanıcıyla ikinci bir bağlantı
denenirse login reddedilir.
