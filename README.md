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
| **Risk** | Monte Carlo VaR / CVaR, portföy P&L dağılımı |
| **Pricer** | Temettü düzeltmeli opsiyon fiyatlayıcı |
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
