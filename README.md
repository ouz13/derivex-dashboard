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
| `health.js` | Servis sağlığı değerlendirmesi ve alarm kararı (`/health`) |
| `watchlist.js` | İzleme listesi |
| `auth.js` | API anahtarı doğrulama, hız sınırı, oturum çerezi |
| `ws.js` | WebSocket (RFC 6455), bağımlılıksız |
| `backtest.py` | Kayan kökenli çapraz doğrulama |
| `correlation.py` | Varlıklar arası korelasyon matrisi |
| `bench_stream.py` | Veri boru hattı kapasite ölçümü |
| `garch.py` | Depodaki geçmişten gerçekleşmiş volatilite ve GARCH(1,1) |
| `yield_curve.py` / `fit_curve.py` | Nelson-Siegel-Svensson eğri uydurma |
| `feed_schema.py` | Ortak veri şeması ve kaynak adaptörleri (IdealData/FIX/CSV/XML/JSON) |
| `import_history.py` | Tarihsel günlük fiyat aktarımı (CSV/JSON → `spot_daily`) |
| `k8s/` | Kubernetes manifestleri — bkz. [k8s/README.md](k8s/README.md) |

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

### Ortak veri şeması ve kaynak adaptörleri

Yukarıdaki biçim **tek bir kaynağa** ait. İkinci bir kaynak eklemek, akış
döngüsünü baştan yazmak demekti: her kaynağın alan adları, fiyat ölçeği ve
sembol dilbilgisi farklı. `feed_schema.py` araya kanonik bir kayıt biçimi
koyar; hattın geri kalanı yalnızca onu bilir.

```
spot    {source, ticker, ts, bid, ask, mid, bid_size, ask_size, flags}
option  {source, ticker, ts, expiry, strike, opt_type, bid, ask, mid, flags}
futures {source, ticker, ts, code, fut_mid, spot_mid, dtm, flags}
```

Beş adaptör var: IdealData (yukarıdaki etiketler), FIX 4.x, CSV, XML ve alan
haritasıyla yapılandırılabilen jenerik JSON. **Aynı kotasyonun dört biçimde
aynı kanonik kayda çıktığı testle sabitlendi** — çıkmasaydı modülün varlık
sebebi olmazdı.

```bash
python3 feed_schema.py --self-test
```

- **`source` her kayıtta.** Devretme anında hangi fiyatın hangi kaynaktan
  geldiği bilinmeden yedek kaynağa geçilemez.
- **`mid` türetme tek yerde:** iki taraflı ortası, tek taraflı `one-sided`,
  defter yoksa son işlem `no-book`. **Çapraz defter (`bid > ask`) atılmaz,
  işaretlenir** — gerçek piyasada anında olur ve atmak o anda fiyatı tamamen
  kaybetmek olurdu.
- **Reddedilen kayıt sayılır.** Sessiz düşme, boru hattının boşluğunu kaynağın
  boşluğundan ayırt edilemez kılardı. `Hat.rapor()` her red sebebini ayrı tutar.
- Bir adaptör patlarsa hat durmaz; bir kaynağın bozuk yükü diğerlerini kesmemeli.

`KaynakSecici` öncelik, bayatlıkta devretme ve birincil döndüğünde otomatik geri
dönüş yapar. **Bu yedek kaynağın kendisi değildir** — ikinci bir gerçek kaynağın
sözleşmesi (uç nokta, kimlik doğrulama, sembol listesi) hâlâ dışarıda. Burada
olan yalnızca karar mantığı, sözleşme geldiğinde bağlanacak yer.

`bridge_stream.py`'nin sıcak döngüsü bu sürümde **değiştirilmedi**: canlı akışın
yolu ve testleri ona bağlı, birleştirme ayrı bir iş.

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

### Kalıcı durum dizini (`STATE_DIR`)

Veritabanı dışındaki durum dosyaları da diske yazılır: denetim izi, uyarı
geçmişi, son iyi kalibrasyonlar, teklifler, fiyatlama kaydı, temettüler.
Varsayılan olarak kodun yanına yazılırlar. **Konteynerde bu `/app`'tir ve
katman dosya sistemidir** — kapsayıcı silindiğinde hepsi gider. Denetim izinin
hash zinciriyle değiştirilemez olması, dosyanın kendisi kaybolabiliyorken bir
şey ifade etmiyordu.

`STATE_DIR` bir birime işaret ettiğinde yedisi de orada durur:

```bash
docker run -v derivex-data:/data \
  -e STATE_DIR=/data -e STORE_DB=/data/derivex.db ... derivex-dashboard
```

Kubernetes'te bunu ConfigMap yapar ([k8s/README.md](k8s/README.md)).
`dividends.json` hem repoda gelen bir tohum hem düzenlenebilir durum olduğu
için okuma önce `STATE_DIR`'e, sonra tohuma bakar.

### Tarihsel veri aktarımı

Depo yalnızca **ileriye** birikir: ilk çalıştırıldığı günden bugüne. Yani 10
yıllık seri kendiliğinden hiç oluşmaz ve GARCH 60 getirinin altında uydurmayı
reddettiği için modeller bekler. `import_history.py` dışarıdan gelen veriyi
yükler:

```bash
python3 import_history.py gecmis.csv --data-mode LIVE --dry-run
python3 import_history.py gecmis.csv --data-mode LIVE --report-gaps
```

Başlık adları esnek eşlenir (`tarih`/`date`, `kapanis`/`close`, …); eşleşmezse
`--map "Fiyat=close"` ile elle verilir. Dört karar:

- **`--data-mode` zorunlu, varsayılanı yok.** Deponun tüm tasarımı üretilmiş
  fiyatların piyasa fiyatı gibi görünmemesine dayanıyor; aktarımda bu etiketi
  tahmin etmek o korumayı tek hamlede boşa çıkarırdı.
- **Gün/ay sırası tahmin edilmiyor.** `03/04/2016` hem 3 Nisan hem 4 Mart.
  Dosyadan çıkarılabiliyorsa çıkarılır (bir satırda bileşen > 12), aksi halde
  aktarım durur ve `--date-format` ister. Ters çevrilmiş bir seri **hiçbir hata
  vermez**: tarihler geçerli, fiyatlar geçerli, yalnızca her getiri yanlıştır.
- **Tutarsız bar reddedilir, düzeltilmez.** `high < low` bozuk veridir.
- **Bugünün barı atlanır** (`--allow-today` ile alınır) ve çakışan
  ticker/gün satırlarının **hepsi** düşer — "son satır kazanır" demek dosyadaki
  sıraya güvenmek olurdu.

Aktarılan barlar `n = 0` ile işaretlenir, böylece hangi barın ölçüldüğü
hangisinin aktarıldığı ayırt edilebilir; gün içi tick'ten oluşmuş bir bar
(`n > 0`) varsayılan olarak **ezilmez** (`--overwrite`). Yeniden çalıştırmak
güvenlidir. Rapor, hangi ticker'ın GARCH eşiğini geçtiğini yazar.

### GARCH(1,1)

Fcst sütunu eskiden durağan bir JSON dosyasından okunuyordu. Depo günlük
kapanışları biriktirdiğinden tahmin artık gerçekten hesaplanabiliyor:

```bash
python3 garch.py                 # tüm ticker'lar
python3 garch.py --ticker THYAO  # tek ticker, ekrana
```

### EGARCH(1,1)

GARCH'ın yakalayamadığı bir şeyi yakalar: **asimetri.** GARCH'ta yalnızca r²
var, getirinin işareti kareyle birlikte kaybolur. EGARCH log uzayında çalışır
ve γ terimi işareti taşır:

```
log(σ²ₜ) = ω + α(|z| − E|z|) + γ·z + β·log(σ²ₜ₋₁)
```

γ negatifse düşüşler volatiliteyi yükselişlerden daha çok artırıyor demektir
(kaldıraç etkisi) — hisse serilerinde beklenen davranış. Çıktıda `asimetri`
alanı bunu düz Türkçe yazar.

İki şey ölçülerek ayarlandı:

- **Jensen düzeltmesi.** Naif hedefleme (ω = (1−β)·log σ̄²) koşulsuz
  *log*-varyansı hedefler, ama E[exp(X)] ≠ exp(E[X]): modelin ima ettiği
  varyans σ̄²'den büyük çıkıyordu — ölçüldü, 1.09–1.15× (volatilitede %4–7
  yukarı sapma). Düzeltmeyle sapma %1'in altına indi. Çok yüksek kalıcılık
  köşesinde ~%8 artık sapma kalıyor, kodda yazılı.
- **α alt sınırı.** α = 0 modeli şok *büyüklüğüne* tamamen duyarsız bırakır;
  ince ızgara bu dejenere köşeye düşebiliyordu, 0.01 ile sınırlandı.

**α zayıf tanımlıdır.** Olabilirlik yüzeyi α yönünde çok düz; uydurucunun
bulduğu nokta gerçek parametrelerden daha yüksek olabilirlik veriyor, yani
sapma optimize edici hatası değil sonlu örnek özelliği. γ ve β ~0.02
hassasiyetle geri geliyor, α'da tolerans bilerek geniş.

Çok adımlı tahmin için |z| terimi yüzünden kapalı form yok; benzetim
kullanılıyor (sabit tohum, yani tekrarlanabilir).

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

Durum dosyaları varsayılan olarak imajın içine yazılır ve konteyner silinince
gider. Kalıcılık için bir birim bağlayın (bkz. **Kalıcı durum dizini**).

## Kubernetes

Manifestler `k8s/` altında: Namespace, PVC, ConfigMap, Secret örneği,
Deployment, Service ve Ingress örneği. Ayrıntılı kurulum ve kısıtlar
[k8s/README.md](k8s/README.md)'de.

**Otomatik ölçekleme yok ve bu gizlenmedi.** Kalıcılık SQLite dosyası üzerinde
ve birim `ReadWriteOnce`. İkinci replika performans değil **veri bütünlüğü**
sorunu yaratır: aynı akış iki kez okunur, aynı satırlar iki kez yazılır,
`forecast_log` tekilliği için iki pod yarışır, SQLite yazar kilidi ağ dosya
sistemlerinde güvenilmez. HPA kasten eklenmedi — çalışmayan bir ölçekleyici
koymak ölçeklendiği izlenimi verip ilk gerçek yükte veriyi bozardı.

Dolayısıyla `replicas: 1` ve `strategy: Recreate` (`RollingUpdate` güncelleme
sırasında iki podu kısa süre birlikte ayakta tutar — tam kaçınılan durum).
Yatay ölçekleme PostgreSQL'e geçiş ve köprünün tek yazara indirilmesi demek;
bu bir YAML işi değil.

**Problar `/health`'e bakar ve `?strict=1` kullanmaz.** `readinessProbe`
başarısız olunca pod Service'ten çıkar ve tek replika olduğu için pano tamamen
erişilemez olur — oysa veri akmadığını teşhis etmek için tam da panoya bakmak
gerekir. Veri akışı uyarıları webhook ile gider. `?strict=1` (unhealthy → 503)
JSON okuyamayan harici izleme ve yük dengeleyici havuz kontrolü için vardır.

Manifestler gerçek bir kümede **denenmedi**; `tests/test_k8s_manifests.py`
yalnızca kendi içlerinde tutarlı olduklarını (çapraz referanslar, `STATE_DIR`
ile birim yolunun eşleşmesi, `replicas=1`, örnek Secret'ın boşluğu) garanti
eder. Yedi bilinen eksik `k8s/README.md`'de listelidir.

## CI

`.github/workflows/ci.yml` her push ve pull request'te iki iş çalıştırır:
testler (Node + Python + sözdizimi) ve Docker imajının derlenip gerçekten
ayağa kalkması.

## Testler

```bash
npm test
```

**376 Python + 197 Node testi.** Ek çalışma zamanı bağımlılığı yok —
`unittest` ve `node --test` kullanılır. (`pyyaml` yalnızca CI'da, yalnızca
Kubernetes manifest testleri için kurulur; o test `pyyaml` yoksa atlanır.)

JS testleri sunucuyu ayağa kaldırıp `/risk-handler.js`'i gerçekten servis
edildiği haliyle çeker; yani tarayıcıya giden kodun kendisi sınanır. Birçok
test bir **kararı** sabitler, biçimi değil: şubenin kendi teklifini
onaylayamaması, aktarımın mock/live'ı karıştırmaması, `replicas` değerinin 1
kalması gibi. Bu testler bozulursa, düzeltilecek şey genellikle test değildir.

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

## Diğer varlıklar (endeks, FX, emtia)

Endeks, döviz ve emtia vadelileri hisselerle aynı akıştan geliyordu ama
`TARGET_TICKERS` yalnızca hisse içerdiği için filtrelenip atılıyordu — örnek
kayıtta 4.216 mesaj. Artık `Market → Other Assets` sekmesinde görünüyorlar:
XU030, X10XB, USDTRY, CNHTRY, XAUUSD, XAGUSD, XAUTRYM, XPTUSD, XPDUSD.

İki şey bilinçli olarak farklı yapıldı:

- **İma edilen getiri gösterilmiyor.** Getiri vadeli/spot oranından türüyor ve
  bu dayanakların **spot kotasyonu akışta yok** (kayıtta 9 dayanaktan 7'sinde
  hiç, kalan ikisinde çok seyrek). Spot olmadan getiri üretmek uydurma bir sayı
  olurdu; ekranda bunun neden boş olduğu yazıyor.
- **Vade döngüsü farklı.** Hissede ardışık aylar (0926, 1026, 1126), altında
  çift aylar (1026, 1226, 0227), CNHTRY çift ay, USDTRY aylık. Tek bir döngü
  varsaymak ya yanlış sembol üretir ya var olanı kaçırır. Köprü "hepsini al,
  filtrele" modeliyle çalıştığı için eşleşmeyen aday sembolün maliyeti yok —
  bu yüzden döngü tahmin etmek yerine **önümüzdeki 6 ayın hepsi aday
  üretiliyor.** Örnek kayıt üzerinde doğrulandı: 21 sembolün tamamı, %100.

## Hesaplama süreleri

Kalibrasyon, VaR koşusu ve opsiyon zinciri zenginleştirme ölçülüyor; süreler
durum satırında ve denetim izinde duruyor.

Ölçüm hemen bir şey gösterdi: **Heston kalibrasyonu ~2.8 saniye, SVI ~7
milisaniye.** Maliyetin tamamı Heston'ın karakteristik fonksiyon integralini
ızgara üzerinde taramasından geliyor. Fallback zinciri önce her iki modeli de
uyduruyordu; artık alternatif model yalnızca birincisi reddedilince
uyduruluyor, bu da SVI seçiliyken kalibrasyonu ~400 kat hızlandırdı.

Heston'ın kendisi hâlâ yavaş — bu ayrı bir iş olarak duruyor.

## Servis sağlığı

`/health` eskiden sabit `{"ok":true}` dönüyordu — hiçbir bağımlılığı kontrol
etmiyordu, yani sunucu ayakta ama **veri hiç akmıyorken de "sağlıklı"** diyordu.
İzleme açısından bu, anlaması gereken tek durumu kaçırmak demekti.

Artık yedi kontrol dönüyor: dört veri kaynağı (spot, vadeli, opsiyon, diğer
varlıklar), depo, volatilite kalibrasyonu, faiz eğrisi. Başlıkta her sayfada
görünen bir rozet, ayrıntılı tablo `Market → Summary` sekmesinde.

**Üç durum, iki eşik:**

| Durum | Anlamı |
|---|---|
| `healthy` | Tüm kontroller taze |
| `degraded` | Bir şey bayat ama sistem çalışıyor |
| `unhealthy` | Çalışmayı engelleyen bir şey var |

Ayrım önemli: **seans dışında veri akmaması normaldir** ve konteyneri yeniden
başlatmayı gerektirmez. Bu yüzden HTTP durumu 200 kalır, karar `status` alanına
bırakılır. "Hiç veri gelmedi" ile "bayatladı" da ayrı tutulur — biri kurulumun
çalışmadığını, öteki akışın durduğunu gösterir.

Kaynaklar **kendi temposuna göre** yargılanır: veri akışı saniyede bir yazar
(60 sn eşiği), depo raporu 60 sn'de bir gönderilir, eğri 300 sn'de bir
uydurulur (900 sn eşiği). Periyodik işleri akış eşiğiyle ölçmek, 5 dakikada bir
çalışan bir işi her seferinde bayat göstermek olurdu.

Eşikler `HEALTH_FRESH_SEC` / `HEALTH_STALE_SEC` ile değiştirilir. Docker'ın
`HEALTHCHECK`'i artık `/` yerine `/health`'e bakıyor ve yalnızca `unhealthy`
durumunu başarısızlık sayıyor.

## Kapasite ölçümü

Dökümandaki *"saniyede 10.000+ mesaj"* senaryosu hiç test edilmemişti.
`bench_stream.py` gerçek kayıttan (59.078 çerçeve) ölçer:

```bash
python3 bench_stream.py           # yalnızca ayrıştırma (yan etkisiz)
python3 bench_stream.py --post    # POST yolunu da ölç (çalışan dashboard gerekir)
```

POST ölçümü opt-in: gerçek POST yolunu ölçmek için gerçekten POST atması
gerekiyor, bu yüzden `ZZ_BENCH` adlı sahte bir ticker gönderip sonra depodan
temizliyor. Tanılama aracının canlı duruma kalıcı iz bırakmaması için varsayılan
kapalı.

Aşamalar ayrı ayrı ölçülür, çünkü toplam sayı tek başına nerede tıkandığını
söylemez. Ölçüm sonucu:

| Aşama | Verim |
|---|---|
| `parse_frames` | 6.7M/sn |
| `parse_frame` | 750k/sn |
| `extract_quote` | 4.2M/sn |
| `apply_quote` | 2.5M/sn |
| **Uçtan uca ayrıştırma** | **829k/sn** |
| **Dashboard'a POST** | **2.4k/sn** |

**İki bulgu:**

1. **`parse_frames` kareseldi.** Her çerçeve için tamponun kalanını kopyalıyor
   ve her turda tamponu iki kez tarıyordu. Normalde görünmüyordu çünkü tampon
   4 KB'lık parçalarla büyür — ama akış hızlanıp döngü geri kaldığında tampon
   büyür ve maliyet *tam da zaten geride kalmışken* patlar. `split()` ile tek
   geçişe çevrildi: **8.9k/sn → 6.7M/sn.** Doğrusal ölçeklendiği testle
   sabitlendi.

2. **Darboğaz POST yolu.** Ayrıştırma hedefi 83 kat aşıyor ama köprü her spot
   değişiminde ayrı bir POST attığı için pratik tavan **~2.400 msg/sn**.
   Dökümandaki 10.000 hedefi bu haliyle **karşılanmıyor**. Çözüm yönü spot
   başına tek POST yerine toplu gönderim — ayrı bir madde.

## İzleme listesi

Realized Vols tablosunda ticker'ları yıldızlayıp **Watchlist** düğmesiyle
yalnızca izlenenleri gösterebilirsiniz. Tarayıcıda (`localStorage`) tutulur,
sunucu tarafı değişiklik yok.

İki şey bilinçli:

- **Liste boşken süzme yapılmaz.** Boş bir listeyle tabloyu boşaltmak
  kullanıcıya "veri yok" gibi görünürdü; bunun yerine ne yapması gerektiği
  yazılır.
- **`localStorage` erişilemezse liste yine çalışır.** Gizli pencerede veya
  site verisi engellendiğinde erişim istisna fırlatıyor; bunun bir tabloyu
  çökertmesi saçma olurdu, bellek yedeğine düşülür.

## Çapraz doğrulama (model karşılaştırma)

Dört tahmin yöntemi var (Realized, EWMA, GARCH, EGARCH) ama hangisinin daha
iyi olduğu ölçülmeden bilinmiyordu. Kalibrasyon anındaki RMSE bunu söylemez:
o, modelin geçmişe ne kadar iyi **uyduğunu** ölçer, ileriyi ne kadar iyi
**tahmin ettiğini** değil.

```bash
python3 backtest.py                 # depodaki tüm ticker'lar
python3 backtest.py --ticker THYAO --ufuk 15
```

**Kayan köken yöntemi:** köken ileri kayar, her adımda model *yalnızca
kökene kadarki* veriyle yeniden uydurulur ve sonraki `ufuk` günün
gerçekleşmiş volatilitesi tahmin edilir. Modelin geleceği görmemesi bu
yeniden uydurmaya bağlı — tek sefer uydurup tüm geçmişi test etmek sızıntı
olurdu. Testlerden biri doğrudan bunu sınar: kökenden sonraki veriyi
değiştirip tahminin değişmediğini doğrular.

**Saf dayanak (naive baseline) karşılaştırmaya dahil:** "son `ufuk` günün
volatilitesi aynen sürecek". Modeller bunu geçemiyorsa karmaşıklığın bir
karşılığı yok demektir. Çıktı bunu açıkça yazar.

Yanlılık (bias) sütunu ayrıca gösterilir: RMSE tek başına modelin sistematik
olarak yüksek mi alçak mı tahmin ettiğini gizler.

## Uyarılar

`/health` durumu **değerlendiriyordu** ama kimseye **haber vermiyordu** —
ekrana bakmıyorsanız beslemenin saat 11'de öldüğünü fark etmezsiniz. Artık
sunucu durumu `HEALTH_POLL_SEC` (varsayılan 30 sn) aralıklarla yokluyor ve
geçişleri kaydediyor. Geçmiş `Market → Summary` sayfasında.

İki kural gürültüyü engelliyor:

- **Yalnızca değişimde** alarm üretilir. Aynı durumu her yoklamada bildirmek
  30 saniyede bir aynı satırı yazmak olurdu ve gerçek bir değişim arada
  kaybolurdu.
- **Yeni durum üst üste `HEALTH_CONFIRM` kez** (varsayılan 2) görülmeden kabul
  edilmez. Tek bir geç kalmış POST yüzünden alarm üretip bir saniye sonra geri
  dönmek alarmı değersizleştirir.

Sağlıklı açılış alarm üretmez (her yeniden başlatmada gürültü olurdu), ama
**bozuk açılış üretir** — sistemin bozuk kalktığını bilmek gerekir.

`ALERT_WEBHOOK_URL` tanımlıysa her geçiş oraya POST edilir. Tanımlı değilse
alarmlar yine kaydedilir (`health_alerts.jsonl`, yeniden başlatmaya dayanıklı).

## Canlı tahmin doğruluğu

`backtest.py` geçmiş veri üzerinde tek seferlik çalışır. Bunun yanında
üretilen **her tahmin hedef tarihiyle** kaydediliyor (`forecast_log`); ufuk
dolunca gerçekleşen volatiliteyle karşılaştırılıp puanlanıyor. Sonuç Discount
Rate sekmesindeki **Forecast Accuracy** bloğunda.

Bu, kalibrasyon RMSE'sinden farklı bir şey ölçer: o, modelin geçmişe ne kadar
iyi **uyduğunu**; bu, ileriyi ne kadar iyi **tuttuğunu**.

Gün başına model ve ufuk için tek kayıt açılır — `garch.py` 15 dakikada bir
çalıştığı için aksi halde kopyalar birikir ve aynı tahmin defalarca sayılırdı.

İlk sonuç bir ufuk süresi sonra gelir; o zamana kadar panel "awaiting horizon"
yazar, uydurma bir sayı göstermez.

## İşletim

Günlük işletim, arıza senaryoları ve bakım için → **[RUNBOOK.md](RUNBOOK.md)**

## API erişimi

`/api/*` uçlarının tamamı açıktı — portföy pozisyonları, fiyatlama kayıtları,
denetim izi dahil. "Kurumsal veri servisi" iddiasıyla en çelişen eksik buydu.

```bash
node auth.js --generate          # anahtar üret, .env'e ekle
curl -H "X-API-Key: <anahtar>" http://sunucu:5173/api/spot?all=1
```

`401` anahtar yok · `403` geçersiz · `429` hız sınırı (`Retry-After` ile).

**Ne yapar, ne yapmaz.** Döküman OAuth2 istiyor; burada yapılan onun bir **alt
kümesi**: API anahtarı + anahtar başına hız sınırı. Fark şu: OAuth2 jeton
süresi, yenileme ve yetki kapsamı getirir — burada anahtar süresizdir ve tüm
uçlara erişir. Tam OAuth2 ayrı bir iş olarak duruyor.

**Üç karar:**

- **Yerel istekler muaf** (varsayılan). Veri köprüsü ve arayüz aynı makineden
  konuşuyor; tehdit modeli "ağdaki başka biri uçlara vuruyor", işletmecinin
  kendi makinesi değil. `AUTH_ALLOW_LOCAL=0` ile kaldırılır.
- **Anahtar tanımlı değilse doğrulama kapalıdır** ve `/health` bunu bildirir.
  Sessizce açık bırakmak yerine görünür bırakmak: geliştirme akışını bozmadan
  eksiği ortada tutuyor. Canlı modda kapalıysa sağlık durumu `degraded` olur.
- **Yalnızca `/api/*` korunur.** Tarayıcı gezinmesi özel başlık taşıyamadığı
  için HTML sayfaları açıktır. Dışa açarken önüne TLS ve sayfa doğrulaması
  yapan ters vekil sunucu gerekir.

**Hız sınırı kayan penceredir.** Sabit pencerede 59. saniyede limit kadar,
61. saniyede yine limit kadar istek geçerdi — yani sınırın iki katı. Testi var.

`X-Forwarded-For` **dikkate alınmaz**: istemcinin gönderdiği bir başlıktır ve
güvenilen bir vekil sunucu olmadan ona bakmak doğrudan atlatma yolu açardı.

## Korelasyon matrisi

Risk hesabı **tek bir skaler korelasyon katsayısına** dayanıyordu (arayüzde elle
girilen ρ, varsayılan 0.50) ve tek faktörlü bir model kullanıyordu:

```
z_i = √ρ·z_piyasa + √(1−ρ)·z_özgü
```

Bu, THYAO–GARAN ile THYAO–HEKTS korelasyonunu **aynı** kabul etmek demek. VaR bu
varsayıma yüksek duyarlılık gösterdiği için sonuç sistematik olarak yanılıyordu.

```bash
python3 correlation.py            # hesapla ve dashboard'a gönder
python3 correlation.py --show     # yalnızca ekrana yaz
```

Matris Risk sekmesinde ısı haritası olarak görünür; Monte Carlo onu Cholesky
çarpanıyla kullanır (`z = L·ε`). Ölçülen etki: blok yapılı bir portföyde
99% VaR **−171.020 → −152.700** (%10.7 fark), çünkü tek ρ=0.50 bloklar arası
gerçek korelasyonu (0.13–0.40) olduğundan yüksek varsayıyordu.

**Üç teknik karar:**

- **Ortak tarih kesişimi** kullanılır (listwise). Çift bazlı hesap her çiftte
  farklı örnek kullanacağı için pozitif yarı-tanımlı olmayan matris üretebilir
  ve Cholesky patlar.
- **Büzülme (shrinkage)** sabit-korelasyon hedefine doğru yapılır, birim
  matrise değil. Birim matrise büzülmek varlıklar arası bağıntıyı sistematik
  azaltır ve **riski olduğundan düşük** gösterirdi. Hedef sabit-korelasyon
  olduğu için ortalama korelasyon korunur, yalnızca çiftler ortalamaya çekilir.
- **Pozitif tanımlılık** özdeğer kırpmayla değil büzülmeyle sağlanır: PSD bir
  örnek matrisi, PD bir hedefe λ>0 ile büzüldüğünde sonuç PD olur. Yine de kör
  inanca bırakılmaz — Cholesky'nin kendisi sınama olarak kullanılıp
  başarısızlıkta λ kademeli artırılır.

Büzülme katsayısı gözlem/varlık oranına göre seçilir (örnek zayıfladıkça hedefe
daha çok yaslanır). Tam Ledoit-Wolf tahmincisi bir iyileştirme olarak
durmaktadır.

**Matris portföydeki dayanakların tamamını kapsamıyorsa skaler ρ'ya düşülür** ve
hangi varlığın eksik olduğu durum satırında yazılır. Eksik varlıkları bağımsız
saymak, onların riskini olduğundan düşük gösterirdi.

## WebSocket

Kurumsal tüketiciler veriyi HTTP ile **yoklamak** zorundaydı. Artık abone
olunabiliyor:

```
ws://sunucu:5173/ws?topics=spot,options&key=<anahtar>
```

Konular: `spot`, `futures`, `options`, `other`, `health`, ya da hepsi için `*`.
Bağlantı sırasında abonelik değiştirilebilir:

```json
{"subscribe": ["options"]}
```

**Bağımlılık eklenmedi.** El sıkışması ve çerçeveleme elle yazıldı (~150 satır);
projede hiçbir çalışma zamanı bağımlılığı olmaması ilkesini tek bir özellik için
bozmak, kurulumu ve güvenlik yüzeyini kalıcılaştıracak bir maliyetti.

**Desteklenmeyenler açıkça reddedilir** — parçalı çerçeve, ikili veri, uzantılar,
maskesiz istemci çerçevesi. Sessizce yanlış çözmektense bağlantıyı protokol
hatasıyla kapatmak doğrusu.

> İstemci yazarken dikkat: sunucudan gelen çerçeveler **maskesizdir**. `ws.js`
> içindeki çözücü sunucu tarafı içindir ve maskesiz çerçeveyi reddeder.

Anahtar sorgu parametresinden de kabul edilir, çünkü tarayıcı WebSocket'i özel
başlık gönderemez. Bu bir ödün: sorgu dizeleri erişim kayıtlarına düşer.
Sunucu-sunucu entegrasyonda `X-API-Key` başlığını tercih edin.

## TLS ve sayfa koruması

A6'da yalnızca `/api/*` korunuyordu; tarayıcı gezinmesi özel başlık
taşıyamadığı için HTML sayfaları açıktı. Artık:

```bash
AUTH_PROTECT_PAGES=1 python3 start.py
```

Sayfaya girişte anahtar **bir kez** sorulur (`/login`), karşılığında **HMAC ile
imzalı** bir oturum çerezi verilir. İmzasız bir çerez, içeriği değiştirip
başkası gibi görünmeye izin verirdi. Çerez `HttpOnly` (JavaScript okuyamaz),
`SameSite=Strict` (başka sitelerden gönderilmez) ve TLS varken `Secure`.

HTTPS doğrudan da çalışır:

```bash
node auth.js --cert                                    # geliştirme sertifikası
TLS_CERT=dev-cert.pem TLS_KEY=dev-key.pem python3 start.py
```

**Sertifika okunamazsa sunucu başlamaz.** Sessizce HTTP'ye düşmek, operatörün
şifreli çalıştığını sanırken düz metin yayın yapması demek olurdu.

## Volatilite yüzeyi

Volatility Curve sekmesi tek vade kesitini gösteriyordu — smile/skew eğrisi
vardı, yüzey yoktu. **Build surface** düğmesi artık tüm vadeleri ayrı ayrı
kalibre edip tek ızgarada birleştiriyor.

**Ortak eksen moneyness (K/S)**, ham kullanım fiyatı değil: vadeler farklı
strike'lar taşıyor, ham eksende ızgara tırtıklı çıkar ve vadeler
karşılaştırılamaz.

İki görünüm: üst üste bindirilmiş smile eğrileri (vade yapısı burada okunur,
noktalar piyasa kotasyonları) ve ızgaranın kendisi ısı haritası olarak.

**Üç şey bilinçli olarak gizlenmiyor:**

- **Kalibre edilemeyen vade atlanır**, komşulardan doldurulmaz. Uydurulmuş bir
  dilim, yüzeyin geri kalanından ayırt edilemezdi. Atlanan vade ve sebebi
  durum satırında yazılır.
- **Zayıf belirlenmişlik uyarısı.** Hem Heston hem SVI beş parametreli; vade
  başına 10'dan az kotasyon varsa RMSE küçük çıkar ama **bu uyum kalitesi
  değildir** — dilimin şekli büyük ölçüde modelin kendi eğilimidir. VİOP'ta
  vade başına tipik olarak 9 kotasyon olduğu için bu uyarı pratikte sürekli
  görünür, ve görünmesi gerekir.
- **Piyasa noktaları eğrilerin üzerinde** çizilir: yüzeyin verinin neresinde
  desteklendiği görülsün diye.

**Yüzey otomatik kurulmaz.** Heston vade başına ~2,8 saniye sürüyor (ölçüldü);
her sayfa açılışında bunu ödemek gereksiz. SVI ile neredeyse anlık.

## Şube ekranı (prototip)

`Branch` sekmesi şube iş akışının **şeklini** gösterir: teklif girişi, trading
masasına iletme, rol bazlı onay. Sayfanın en üstünde kaldırılamaz bir uyarı
var ve kasıtlı:

> **Nothing is sent anywhere.** Teklifler bu sunucuda kalır, rol gerçek bir
> giriş değil bir seçicidir.

**Neden tam uygulanmadı:** iş akışı müşteriye özeldir — onay kuralları, roller,
kimlik sağlayıcı ve hedef sistem her kurumda farklıdır. Üretim sürümü
müşterinin kendi iş akışı motoruna karşı yazılır.

**Gerçek olan kısım:** fiyatlama. Seçilen sözleşmenin piyasa orta fiyatı ve
zımni volatilitesi canlı zincirden okunur. Tek taraflı kotasyonda fiyat
**üretilmez**, "cannot price it" denir.

**Gösterim olsa da durum makinesi gerçek:**

```
draft → submitted → approved
                  ↘ rejected → (revise) → draft
```

Geçişler **sunucuda** zorlanır, arayüzde düğme gizlemekle yetinilmez. Şube
kendi teklifini onaylamaya çalışırsa — doğrudan API çağrısıyla bile — `403`
alır. Görev ayrılığı ilkesi budur ve testi var.

Her geçiş aktör ve zaman damgasıyla ize yazılır. Üretimde bu iz, Risk
sekmesinin zaten doğruladığı değiştirilemez denetim kaydına bağlanır.
