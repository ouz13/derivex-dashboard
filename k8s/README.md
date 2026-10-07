# Kubernetes dagitimi

Bu dizin Derivex Dashboard'u bir Kubernetes kumesinde calistirmak icin
gereken manifestleri icerir.

**Once sunu okuyun:** bu manifestler uygulamayi kumede **calistirir**,
ama **otomatik olceklemez**. Sebebi asagida, "Neden HPA yok" basliginda.
Kisit bir eksiklik olarak gizlenmedi, cunku calismayan bir
otomatik olcekleyici koymak olceklendigi izlenimi verir ve ilk gercek
yukte veriyi bozar.

---

## Hizli kurulum

```bash
# 1. Imaj — kume icinden erisilebilir bir kayit defterine
docker build -t derivex-dashboard:0.1.0 .
docker tag derivex-dashboard:0.1.0 KAYIT/derivex-dashboard:0.1.0
docker push KAYIT/derivex-dashboard:0.1.0
# deployment.yaml icindeki image: alanini bu ada cevirin

# 2. Namespace ve disk
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pvc.yaml

# 3. Yapilandirma
kubectl apply -f k8s/configmap.yaml

# 4. Sirlar — ORNEK DOSYAYI OLDUGU GIBI UYGULAMAYIN
# Degerler .env'den okunur, boylece kabuk gecmisine girmez.
# Once node auth.js --generate ile API_KEYS ve SESSION_SECRET uretip
# .env'e yazin, sonra:
kubectl -n derivex create secret generic derivex-secrets --from-env-file=.env

# 5. Uygulama
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 6. Dogrulama
kubectl -n derivex rollout status deploy/derivex-dashboard
kubectl -n derivex port-forward svc/derivex-dashboard 5173:80
curl -s localhost:5173/health | head -c 200
```

Ingress istege bagli ve alan adina gore duzenlenmeli:
`kubectl apply -f k8s/ingress.yaml`

---

## Neden HPA yok

Kalicilik **SQLite dosyasi** uzerinde. Bu, yatay olceklemeyi YAML ile
cozulebilir bir sey olmaktan cikariyor:

| Sorun | Sonuc |
|---|---|
| Birim `ReadWriteOnce` | Birden fazla pod ayni diski baglayamaz |
| SQLite yazar kilidi dosya sistemi kilitlerine dayanir | Ag dosya sistemlerinde guvenilmez; WAL kipinde bozulmaya kadar gidebilir |
| Veri koprusu her podda ayri calisir | Ayni akis iki kez okunur, ayni satirlar iki kez yazilir |
| Model kalibrasyonu her podda ayri calisir | `forecast_log` tekilligi icin iki pod yarisir |

Yani ikinci bir replika performans degil **veri butunlugu** sorunu
yaratir. `replicas: 1` ve `strategy: Recreate` bu yuzden; `Recreate`,
guncelleme sirasinda iki podun kisa sure birlikte ayakta kalmasini
(RollingUpdate'in varsayilani) engelliyor.

**Yatay ollceklemek icin gerekenler:**

1. **PostgreSQL'e gecis.** `store.py` tek dosya ve tum SQL orada; port
   etmek buyuk is degil ama `INSERT OR REPLACE` ve
   `ON CONFLICT ... DO UPDATE` ifadeleri gozden gecirilmeli.
2. **Veri koprusunu tek yazara indirmek.** Kopru bir Deployment degil
   tek replikali bir ayri is olmali (ya da lider secimi), pano
   katmani ise durumsuz hale gelip sonra olceklenebilir.
3. **Durumu pano surecinden cikarmak.** `serverState` bellekte; pano
   olceklendiginde her replika kendi gorusunu tasir.

Bunlar yapilmadan HPA eklemek, yukun artinca veriyi bozan bir sistem
uretir. Dikey olcekleme (kaynak sinirlarini yukseltmek) su an calisan
tek yol ve tek dugumde yetiyor.

---

## Problar neden `?strict=1` kullanmiyor

`/health` **daima HTTP 200** doner; karar `status` alanindadir
(`healthy` / `degraded` / `unhealthy`). `?strict=1` eklenirse
`unhealthy` icin 503 doner — ama **problar bunu kullanmiyor.**

- **livenessProbe:** isi surecin yanit verebildigini anlamak. Veri
  akmiyorsa surec sagliklidir, veri yoktur. Yeniden baslatmak hicbir
  seyi duzeltmez; yalnizca bellekteki durumu da siler.
- **readinessProbe:** basarisiz olunca pod Service'ten cikarilir. Tek
  replika oldugu icin bu panoyu **tamamen erisilemez** yapar. Oysa veri
  akmadigini teshis etmek icin tam da panoya bakmak gerekiyor.

Veri akisi uyarilari `ALERT_WEBHOOK` ile gidiyor (bkz. `health.js`,
`RUNBOOK.md`), prob ile degil. `?strict=1` harici izleme ve yuk
dengeleyici havuz kontrolu icin var.

---

## Canli veri: cikis IP'si sarti

`DATA_MODE=1` (canli IdealData akisi) icin podun **disa cikis IP'si**
IdealData tarafinda tanimli olmali. Kubernetes'te bu adres dugume ya da
bulut saglayicinin NAT gecidine bagli ve **pod yeniden
zamanlandiginda degisebilir.**

Yani sabit bir cikis IP'si (NAT gateway / egress IP / ayrilmis dugum)
olmadan canli mod guvenilmez calisir. Bu bir kod sorunu degil,
aglandirma sarti — ve su anki engelin kume tarafindaki karsiligi.
`DATA_MODE` varsayilani bu yuzden `0`.

---

## Kalici durum

Birim `/data` altinda ve `STATE_DIR` oraya bakiyor. Bu ayarin olmadigi
surumde su dosyalar `/app`e, yani **katman dosya sistemine** yaziliyordu
ve pod yeniden basladiginda siliniyordu:

| Dosya | Icerik |
|---|---|
| `derivex.db` | Gecmis seri, gunluk barlar, model surumleri, tahmin kaydi |
| `audit-log.jsonl` | Hash zincirli denetim izi |
| `health_alerts.jsonl` | Durum gecisi gecmisi |
| `model_params.json` | Son iyi kalibrasyonlar (fallback zincirinin "onceki" basamagi) |
| `quotes_demo.json` | Sube teklif akisi kayitlari (prototip) |
| `pricer_log.json` | Fiyatlama kosusu gecmisi |
| `dividends.json` | Temettu girdileri |

Denetim izinin hash zinciriyle degistirilemez olmasi, dosya
kaybolabiliyorken bir sey ifade etmiyordu. `dividends.json` hem repoda
gelen bir tohum hem duzenlenebilir durum oldugu icin okuma once `/data`,
sonra `/app` tohumuna bakiyor.

### Yedekleme

```bash
# SQLite'i calisirken kopyalamak icin .backup kullanilir; dosyayi
# dogrudan cp ile almak WAL yuzunden tutarsiz kopya verebilir.
kubectl -n derivex exec deploy/derivex-dashboard -- \
  python3 -c "import sqlite3,sys; \
    sqlite3.connect('/data/derivex.db').backup(sqlite3.connect('/data/yedek.db'))"
kubectl -n derivex cp derivex-dashboard-XXXX:/data/yedek.db ./yedek.db
```

Disk dolmaya baslarsa tick tablolari budanabilir; gunluk barlar ve
model surumleri korunur:

```bash
kubectl -n derivex exec deploy/derivex-dashboard -- python3 store.py --prune 90
```

---

## Tarihsel veri yukleme

`import_history.py` pod icinde calistirilir. Dosya once kopyalanir:

```bash
kubectl -n derivex cp gecmis.csv derivex-dashboard-XXXX:/data/gecmis.csv
kubectl -n derivex exec deploy/derivex-dashboard -- \
  python3 import_history.py /data/gecmis.csv --data-mode LIVE --dry-run
# cikti dogru gorunuyorsa --dry-run kaldirilir
```

`--data-mode` zorunludur ve varsayilani yoktur: yanlis etiket,
uretilmis fiyatlarin piyasa fiyati gibi gorunmesine yol acar.

---

## Bilinen eksikler

Bunlar yapilmadi ve yapilmis gibi sunulmuyor:

1. **Otomatik olcekleme yok.** Yukarida ayrintisi var. En buyuk eksik.
2. **Manifestler gercek bir kumede denenmedi.** Elimde kume yok ve
   `kubectl` de yok, yani sunucu tarafi sema dogrulamasi (`kubectl apply
   --dry-run=server`) yapilmadi. Yapilan sey `tests/test_k8s_manifests.py`:
   YAML cozulebilirligi, zorunlu alanlar ve **capraz referanslar** —
   Service secicisinin pod etiketleriyle, `volumeMounts`'in `volumes`
   ile, `STATE_DIR`/`STORE_DB`'nin bagli birimle, Ingress'in Service
   adi ve portuyla eslesmesi. Bu "calisiyor" demek degil, "kendi
   icinde tutarli" demek. Ilk dagitimda imaj adi, `storageClassName`
   ve Ingress sinifi kumeye gore duzeltilmeli.
3. **Konteyner root ile calisiyor.** `runAsNonRoot` kasten ayarlanmadi:
   imaj `/app`e yaziyor ve denenmemis bir deger koymak ilk dagitimda
   aciklanamayan izin hatasi verirdi. Imajin root'suz hale getirilmesi
   ayri bir is.
4. **`readOnlyRootFilesystem` yapilamiyor.** Durum dosyalari artik
   `/data`da ama `node_modules` ve Python `__pycache__` `/app` altinda.
5. **NetworkPolicy yok.** Namespace icinde her pod panoya ulasabilir.
   `AUTH_ALLOW_LOCAL=0` bunu kismen kapatiyor (kume ici istekler de
   anahtar istiyor) ama ag seviyesinde kisitlama ayri bir is.
6. **Secret sifreli degil.** Kubernetes Secret base64'tur; etcd'de duz
   metne esdeger durur. Gercek gizlilik icin kumede encryption-at-rest
   ya da harici bir sir deposu gerekir.
7. **PodDisruptionBudget yok.** Tek replikada anlamsiz: butce koymak
   dugum bakimini engellerdi, kesintiyi onlemezdi.
