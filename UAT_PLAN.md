# Kabul Testi ve Pilot Planı

Derivex Dashboard'un kurum içi pilot kullanıma alınması için kabul kriterleri,
test senaryoları ve etki ölçüm planı.

İşletim prosedürleri [RUNBOOK.md](RUNBOOK.md), mimari [README.md](README.md).

---

## 1. Kapsam ve ön koşullar

### Kapsam içi

Fiyatlama motoru, risk analizi, volatilite ve faiz modelleme, veri akışı,
kalıcılık, işletim ve API erişimi.

### Kapsam dışı

Şube iş akışı (teklif girişi, onay mekanizması, rol bazlı yetkilendirme) bu
pilotun kapsamında değildir; ayrı bir faz olarak planlanmaktadır.

### Ön koşullar

Pilot başlamadan **önce** sağlanması gerekenler:

| # | Ön koşul | Sorumlu | Durum |
|---|---|---|---|
| Ö1 | Pilot sunucusunun dış IP'si veri sağlayıcısında yetkilendirilmiş | Dışa bağımlı | **Bekliyor** |
| Ö2 | `DATA_MODE=1` ile canlı akış doğrulanmış (`idealdata_probe.py` → `VERI ALINDI`) | Geliştirme | Ö1'e bağlı |
| Ö3 | API anahtarları üretilmiş ve tüketicilere dağıtılmış | Geliştirme | Hazır |
| Ö4 | TLS sertifikası veya ters vekil sunucu yapılandırılmış | Sistem yönetimi | — |
| Ö5 | Pilot kullanıcı listesi ve erişim bilgileri belirlenmiş | İş birimi | — |

> **Ö1 kritik yoldadır.** Yetkilendirme sağlanmadan sistem yalnızca üretilmiş
> veriyle çalışır ve fiyat doğruluğu test edilemez.

---

## 2. Kabul kriterleri

Pilotun başarılı sayılması için aşağıdakilerin tamamı karşılanmalıdır.

### K1 — Veri bütünlüğü

| Kriter | Eşik |
|---|---|
| Hedef enstrümanların veri kapsamı | ≥ %95'inde canlı kotasyon |
| Spot fiyat gecikmesi (kaynak → ekran) | ≤ 5 saniye |
| Seans boyunca veri kesintisi | Toplam ≤ 5 dakika |
| `/health` durumu seans içinde | `healthy` |

### K2 — Fiyatlama doğruluğu

| Kriter | Eşik |
|---|---|
| Zımni volatilite — piyasa fiyatından geri hesap | Mutlak fark ≤ 0,5 volatilite puanı |
| Black-Scholes ile binom ağacı sapması (Avrupa tipi) | ≤ %0,5 |
| Put-call paritesi sapması | ≤ %1 |
| Greek'lerin sayısal türevle tutarlılığı | Otomatik testlerde geçiyor |

> Son iki kriter zaten otomatik test kapsamındadır; pilotta canlı veriyle
> yeniden doğrulanır.

### K3 — Risk hesabı

| Kriter | Eşik |
|---|---|
| Monte Carlo VaR tekrarlanabilirliği (aynı girdi, 100k yol) | Değişim ≤ %1 |
| Korelasyon matrisi kapsamı | Portföydeki dayanakların ≥ %90'ı |
| VaR hesap süresi (10.000 yol) | ≤ 1 saniye |
| Stres testi senaryoları | 7 senaryonun tamamı sonuç üretiyor |

### K4 — Erişilebilirlik ve performans

| Kriter | Eşik |
|---|---|
| Sayfa açılış süresi | ≤ 3 saniye |
| Seans içi kullanılabilirlik | ≥ %99 |
| API yanıt süresi (p95) | ≤ 200 ms |
| Veri işleme kapasitesi | ≥ 10.000 mesaj/sn (ölçüldü: 24.000) |

### K5 — Güvenlik ve denetlenebilirlik

| Kriter | Eşik |
|---|---|
| Anahtarsız API erişimi | Reddediliyor (401/403) |
| Hız sınırı | Aşımda 429 dönüyor |
| Denetim izi bütünlüğü | `/api/audit/verify` → `valid: true` |
| Veri modu etiketi | Her denetim kaydında mevcut |

---

## 3. Test senaryoları

### S1 — Veri akışı

| # | Adım | Beklenen |
|---|---|---|
| S1.1 | `DATA_MODE=1 python3 start.py` | Beş adım da `[OK]`, akış başlıyor |
| S1.2 | Market → Futures sekmesi | Dayanak başına vadeli oran ve getiri görünüyor |
| S1.3 | Market → Options, bir dayanak seç | 29 sütunlu zincir, Greek'ler dolu |
| S1.4 | Market → Other Assets | Endeks/FX/emtia vadelileri listeleniyor, **ima edilen getiri sütunu yok** |
| S1.5 | Spot fiyatı kaynakla karşılaştır | Fark yok (gecikme hariç) |

### S2 — Fiyatlama

| # | Adım | Beklenen |
|---|---|---|
| S2.1 | Tools → Pricer, piyasa fiyatı gir | Zımni volatilite makul aralıkta |
| S2.2 | Hesaplanan primi piyasa primiyle karşılaştır | K2 eşiği içinde |
| S2.3 | Binom ağacı ile Black-Scholes karşılaştır | Avrupa tipinde sapma ≤ %0,5 |
| S2.4 | Amerikan tipi seç | Fiyat Avrupa tipinden küçük değil |

### S3 — Risk

| # | Adım | Beklenen |
|---|---|---|
| S3.1 | Portföyü XLSX ile içe aktar | Pozisyonlar doğru okunuyor |
| S3.2 | VaR simülasyonu çalıştır | Sonuç ≤ 1 sn, durum satırı **korelasyon matrisi** diyor |
| S3.3 | Aynı parametrelerle tekrarla | VaR değişimi ≤ %1 |
| S3.4 | Korelasyonu elle 0,90 yap | VaR büyüyor (çeşitlendirme faydası azalıyor) |
| S3.5 | Stres testi | Yedi senaryo da sonuç veriyor, kriz en kötüsü |
| S3.6 | CSV raporu indir | Tüm bölümler ve veri modu etiketi var |

### S4 — Modeller

| # | Adım | Beklenen |
|---|---|---|
| S4.1 | Volatility Curve, Heston seç | Kalibrasyon başarılı, IV RMSE ≤ 5 puan |
| S4.2 | SVI'ya geç | Kalibrasyon belirgin daha hızlı |
| S4.3 | Discount Rate | Uydurulmuş eğri ve model adı görünüyor |
| S4.4 | Realized Vols, lookback = Store | GARCH ve EGARCH seçilebiliyor |

### S5 — Güvenlik

| # | Adım | Beklenen |
|---|---|---|
| S5.1 | Anahtarsız `/api/spot` | `401` |
| S5.2 | Geçersiz anahtarla | `403` |
| S5.3 | Geçerli anahtarla | `200` |
| S5.4 | Limiti aşacak kadar istek | `429` + `Retry-After` |
| S5.5 | `/api/audit/verify` | `valid: true` |

---

## 4. Sınır durumları

Bunlar arıza değil, **doğru davranışın sınandığı** durumlardır. Sistemin burada
sessizce yanlış sonuç üretmemesi, doğru sonuç üretmesinden daha önemlidir.

| # | Durum | Beklenen davranış |
|---|---|---|
| C1 | Seans dışında açılış | `/health` → `degraded`, veri yaşı yazılı, **uydurma fiyat yok** |
| C2 | Veri akışı seans ortasında kesilir | Tablolar donuyor, sağlık `degraded`, durum değişimi kaydediliyor |
| C3 | Yeni kurulum, geçmiş yok | GARCH tahmini **üretilmiyor**, "awaiting horizon" yazıyor |
| C4 | Kalibrasyon yakınsamıyor | Durum satırı `FALLBACK` diyor, hangi basamakta olunduğu yazılı |
| C5 | Fiyatlar bayat (>15 dk) | `STALE DATA: prices Xh old` uyarısı |
| C6 | Portföyde matriste olmayan dayanak | Skaler ρ'ya düşülüyor, **eksik varlık adıyla yazılıyor** |
| C7 | Tek taraflı kotasyon (yalnız alış) | Orta fiyat boş, hesaplama yapılmıyor |
| C8 | Vadeye 2 günden az kalmış sözleşme | Yıllık getiri gösterilmiyor (payda sıfıra yaklaşıyor) |
| C9 | Depo yazamıyor (disk dolu) | Akış sürüyor, sağlık `unhealthy`, hata yazılı |
| C10 | Denetim izi dosyası değiştirilmiş | Doğrulama `valid: false` döndürüyor |

---

## 5. Etki ölçüm planı

Dökümanın hedefi *"manuel süreçlere kıyasla fiyatlama hatasını azaltmak ve
karar alma süresini kısaltmak"*. Bunun ölçülebilmesi için **pilot öncesi
başlangıç değeri (baseline) alınmalıdır** — pilot başladıktan sonra karşılaştırma
noktası kalmaz.

### Ö1 — Karar alma süresi

| | |
|---|---|
| **Tanım** | Fiyat talebinin alınmasından tekliflendirmeye kadar geçen süre |
| **Baseline** | Pilot öncesi 2 hafta, mevcut süreçte elle kayıt |
| **Pilot ölçümü** | Sistem üzerinden aynı işlem tipleri |
| **Örneklem** | İşlem tipi başına ≥ 30 gözlem |
| **Hedef** | Medyan sürede anlamlı azalma |

### Ö2 — Fiyatlama tutarlılığı

| | |
|---|---|
| **Tanım** | Aynı enstrüman için farklı kişilerin verdiği fiyatlar arasındaki yayılım |
| **Baseline** | Pilot öncesi teklif kayıtlarından |
| **Pilot ölçümü** | Sistem çıktılarının yayılımı |
| **Hedef** | Standart sapmada azalma |

### Ö3 — Model doğruluğu

| | |
|---|---|
| **Tanım** | Volatilite tahmininin gerçekleşenle karşılaştırması |
| **Ölçüm** | Sistem bunu **kendisi yapıyor** (`forecast_log`), ek iş gerekmez |
| **Görünüm** | Discount Rate → Forecast Accuracy |
| **Not** | İlk anlamlı sonuç bir ufuk süresi (30 gün) sonra gelir |

### Ö4 — Operasyonel kullanılabilirlik

| | |
|---|---|
| **Tanım** | Seans içi erişilebilirlik ve veri kesintisi |
| **Ölçüm** | `/health` geçiş kayıtları (`health_alerts.jsonl`) |
| **Not** | Elle kayıt gerekmiyor, sistem zaten tutuyor |

> Ö3 ve Ö4 için ayrı bir ölçüm süreci kurmaya gerek yoktur; sistem bu verileri
> kendi üretir. Ö1 ve Ö2 **elle baseline** gerektirir ve pilottan önce
> başlatılmalıdır.

---

## 6. Pilot akışı

| Faz | Süre | İçerik | Çıkış koşulu |
|---|---|---|---|
| 0 — Hazırlık | 1 hafta | Ön koşullar, baseline ölçümü başlar | Ö1–Ö5 tamam |
| 1 — Duman testi | 2 gün | S1–S5 senaryoları, sınır durumları | Kritik hata yok |
| 2 — Gözetimli kullanım | 2 hafta | Sınırlı kullanıcı, günlük sağlık kontrolü | K1–K5 karşılanıyor |
| 3 — Genişletilmiş pilot | 4 hafta | Tüm pilot kullanıcılar, Ö1–Ö2 ölçümü | Etki metrikleri toplandı |
| 4 — Değerlendirme | 1 hafta | Sonuçların raporlanması | Üretim kararı |

**Toplam: yaklaşık 8 hafta** (Ö1 yetkilendirmesi sağlandıktan sonra).

### Geri dönüş koşulları

Aşağıdaki durumlarda pilot durdurulur ve manuel sürece dönülür:

- Fiyatlama hatası K2 eşiğini **sistematik** olarak aşıyorsa
- Veri kesintisi günde 30 dakikayı aşıyorsa
- Denetim izi bütünlüğü bozulmuşsa
- Güvenlik açığı tespit edilmişse

---

## 7. Bilinen kısıtlar

Pilot katılımcılarına **önceden** bildirilmesi gerekenler:

- **Şube iş akışı yoktur.** Teklif girişi, onay mekanizması ve rol bazlı
  yetkilendirme bu fazın kapsamında değildir.
- **Volatilite yüzeyi tek vade kesitidir.** Kullanım fiyatı × vade ızgarası
  henüz yoktur.
- **Model doğruluğu zamanla olgunlaşır.** GARCH/EGARCH tahminleri 60 günlük
  geçmiş gerektirir; ilk haftalarda bu sütunlar boş kalır ve bu beklenen
  durumdur.
- **Tek veri kaynağı vardır.** Yedek sağlayıcı yoktur; kaynak kesilirse sistem
  veri alamaz.
- **Tek süreçtir.** Yatay ölçekleme yoktur; eşzamanlı kullanıcı sayısı
  sınırlıdır.
