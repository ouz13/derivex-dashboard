#!/usr/bin/env python3
"""
k8s/ manifest testleri.

    python3 -m unittest discover tests -p "test_k8s_manifests.py"

NEDEN BUNLAR TEST
-----------------
Manifestler gercek bir kumede denenmedi. Denenmemis YAML'in en azindan
KENDI ICINDE tutarli oldugunu garanti etmek, elle gozden gecirmekten
guvenilir: capraz referanslar (ConfigMap adi, PVC adi, birim yolu)
sessizce birbirinden kayar ve hata ancak dagitimda cikar.

Birkac test de KARAR sabitliyor, bicim degil:

  * replicas 1 olmali. SQLite ile ikinci replika performans degil veri
    butunlugu sorunu. Birisi bunu 2 yaparsa test, sebebiyle birlikte
    duruyor.
  * strategy Recreate olmali. RollingUpdate iki podu kisa sure birlikte
    ayakta tutar ve ikisi ayni birime yazar.
  * STATE_DIR birimin icinde olmali. Degilse durum dosyalari katman
    dosya sistemine yazilir ve pod yeniden basladiginda silinir.
"""

import os
import sys
import unittest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K8S = os.path.join(KOK, "k8s")

try:
    import yaml
except ImportError:                                           # pragma: no cover
    yaml = None


@unittest.skipIf(yaml is None, "pyyaml yok")
@unittest.skipIf(not os.path.isdir(K8S), "k8s/ dizini yok")
class Manifestler(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.belgeler = {}
        for ad in sorted(os.listdir(K8S)):
            if not ad.endswith((".yaml", ".yml")):
                continue
            with open(os.path.join(K8S, ad), encoding="utf-8") as f:
                cls.belgeler[ad] = [d for d in yaml.safe_load_all(f) if d]

    def tek(self, dosya):
        self.assertIn(dosya, self.belgeler, dosya + " yok")
        belge = self.belgeler[dosya]
        self.assertEqual(len(belge), 1, dosya + " tek belge icermeli")
        return belge[0]

    # --- bicim ------------------------------------------------------------

    def test_hepsi_cozulebilir_yaml(self):
        self.assertTrue(self.belgeler, "k8s/ altinda manifest yok")
        for ad, belgeler in self.belgeler.items():
            for b in belgeler:
                self.assertIsInstance(b, dict, ad + " sozluk degil")

    def test_her_belgede_zorunlu_alanlar(self):
        for ad, belgeler in self.belgeler.items():
            for b in belgeler:
                for alan in ("apiVersion", "kind", "metadata"):
                    self.assertIn(alan, b, f"{ad}: {alan} eksik")
                self.assertIn("name", b["metadata"], ad + ": metadata.name eksik")

    def test_namespace_disinda_her_sey_derivex_namespace_inde(self):
        for ad, belgeler in self.belgeler.items():
            for b in belgeler:
                if b["kind"] == "Namespace":
                    continue
                self.assertEqual(b["metadata"].get("namespace"), "derivex",
                                 f"{ad}: namespace derivex olmali")

    # --- kararlar ---------------------------------------------------------

    def test_REPLIKA_BIR(self):
        # SQLite dosya tabanli ve birim ReadWriteOnce. Ikinci replika
        # ayni diske yazmaya calisir: ayni akis iki kez okunur, ayni
        # satirlar iki kez yazilir, forecast_log tekilligi icin iki pod
        # yarisir. Olceklemek icin PostgreSQL'e gecis gerekiyor —
        # k8s/README.md "Neden HPA yok".
        d = self.tek("deployment.yaml")
        self.assertEqual(d["spec"]["replicas"], 1)

    def test_strateji_RECREATE(self):
        # RollingUpdate guncelleme sirasinda iki podu kisa sure birlikte
        # ayakta tutar; tam olarak kacinilmak istenen durum.
        d = self.tek("deployment.yaml")
        self.assertEqual(d["spec"]["strategy"]["type"], "Recreate")

    def test_OTOMATIK_OLCEKLEYICI_YOK(self):
        # Calismayan bir HPA, olceklendigi izlenimi verip ilk yukte
        # veriyi bozardi. Eklenecekse once kalicilik katmani degismeli.
        turler = [b["kind"] for belgeler in self.belgeler.values() for b in belgeler]
        self.assertNotIn("HorizontalPodAutoscaler", turler)

    def test_pvc_readwriteonce(self):
        p = self.tek("pvc.yaml")
        self.assertEqual(p["spec"]["accessModes"], ["ReadWriteOnce"])

    # --- capraz referanslar -----------------------------------------------

    def kap(self):
        d = self.tek("deployment.yaml")
        kaplar = d["spec"]["template"]["spec"]["containers"]
        self.assertEqual(len(kaplar), 1)
        return d, kaplar[0]

    def test_deployment_configmap_ve_secret_adlarini_dogru_yaziyor(self):
        _, kap = self.kap()
        kaynaklar = set()
        for e in kap.get("envFrom", []):
            for tur in ("configMapRef", "secretRef"):
                if tur in e:
                    kaynaklar.add(e[tur]["name"])
        self.assertEqual(kaynaklar,
                         {self.tek("configmap.yaml")["metadata"]["name"],
                          self.tek("secret.example.yaml")["metadata"]["name"]})

    def test_deployment_pvc_adini_dogru_yaziyor(self):
        d, _ = self.kap()
        iddialar = {v["persistentVolumeClaim"]["claimName"]
                    for v in d["spec"]["template"]["spec"]["volumes"]
                    if "persistentVolumeClaim" in v}
        self.assertIn(self.tek("pvc.yaml")["metadata"]["name"], iddialar)

    def test_service_secicisi_pod_etiketleriyle_eslesiyor(self):
        # Eslesmezse Service hicbir pod'a yonlenmez ve bu yalnizca
        # dagitimda, "neden 503 aliyorum" diye fark edilir.
        d = self.tek("deployment.yaml")
        s = self.tek("service.yaml")
        etiketler = d["spec"]["template"]["metadata"]["labels"]
        for k, v in s["spec"]["selector"].items():
            self.assertEqual(etiketler.get(k), v, "Service secicisi eslesmiyor")

    def test_deployment_secicisi_pod_etiketleriyle_eslesiyor(self):
        d = self.tek("deployment.yaml")
        etiketler = d["spec"]["template"]["metadata"]["labels"]
        for k, v in d["spec"]["selector"]["matchLabels"].items():
            self.assertEqual(etiketler.get(k), v)

    def test_service_hedef_portu_kap_portuyla_eslesiyor(self):
        _, kap = self.kap()
        s = self.tek("service.yaml")
        port_adlari = {p["name"] for p in kap["ports"]}
        for p in s["spec"]["ports"]:
            self.assertIn(p["targetPort"], port_adlari)

    def test_ingress_service_adini_ve_portunu_dogru_yaziyor(self):
        if "ingress.yaml" not in self.belgeler:
            self.skipTest("ingress.yaml yok")
        ing = self.tek("ingress.yaml")
        s = self.tek("service.yaml")
        port_adlari = {p["name"] for p in s["spec"]["ports"]}
        for kural in ing["spec"]["rules"]:
            for yol in kural["http"]["paths"]:
                arka = yol["backend"]["service"]
                self.assertEqual(arka["name"], s["metadata"]["name"])
                self.assertIn(arka["port"]["name"], port_adlari)

    # --- kalici durum -----------------------------------------------------

    def test_STATE_DIR_BIRIMIN_ICINDE(self):
        # Degilse denetim izi, uyari gecmisi, model parametreleri ve
        # teklifler katman dosya sistemine yazilir ve pod yeniden
        # basladiginda silinir. Denetim izinin hash zinciri, dosya
        # kaybolabiliyorken bir sey ifade etmez.
        cm = self.tek("configmap.yaml")["data"]
        _, kap = self.kap()
        yollar = [m["mountPath"] for m in kap["volumeMounts"]]
        self.assertIn(cm["STATE_DIR"], yollar,
                      "STATE_DIR bagli bir birime isaret etmiyor")

    def test_STORE_DB_BIRIMIN_ICINDE(self):
        cm = self.tek("configmap.yaml")["data"]
        _, kap = self.kap()
        yollar = [m["mountPath"].rstrip("/") for m in kap["volumeMounts"]]
        self.assertTrue(
            any(cm["STORE_DB"].startswith(y + "/") for y in yollar),
            "STORE_DB bagli bir birimin altinda degil: " + cm["STORE_DB"])

    def test_birim_adi_volumes_ile_eslesiyor(self):
        d, kap = self.kap()
        tanimli = {v["name"] for v in d["spec"]["template"]["spec"]["volumes"]}
        for m in kap["volumeMounts"]:
            self.assertIn(m["name"], tanimli,
                          "tanimsiz birim baglanmis: " + m["name"])

    # --- guvenlik ve varsayilanlar ----------------------------------------

    def test_canli_mod_VARSAYILAN_DEGIL(self):
        # Canli mod podun disa cikis IP'sinin IdealData'da tanimli
        # olmasini gerektiriyor ve o adres pod yeniden zamanlandiginda
        # degisebiliyor. Varsayilan olarak acmak, sessizce baglanamayan
        # bir dagitim demek.
        self.assertEqual(self.tek("configmap.yaml")["data"]["DATA_MODE"], "0")

    def test_kume_ici_istekler_de_anahtar_istiyor(self):
        # 1 birakilsaydi ayni namespace'teki her pod anahtarsiz
        # erisebilirdi; podun ag alani "yerel" sayiliyor.
        self.assertEqual(
            self.tek("configmap.yaml")["data"]["AUTH_ALLOW_LOCAL"], "0")

    def test_CONFIGMAPTE_SIR_YOK(self):
        # ConfigMap kume genelinde okunabilir sayilmali.
        yasak = ("STREAM_PASSWORD", "STREAM_USERNAME", "IDEALDATA_API_KEY",
                 "API_KEYS", "SESSION_SECRET", "TLS_KEY")
        veri = self.tek("configmap.yaml")["data"]
        for k in yasak:
            self.assertNotIn(k, veri, "sir ConfigMap'e sizmis: " + k)

    def test_ornek_secret_DOLU_DEGIL(self):
        # Doldurulmus bir ornek dosya, repoya kimlik bilgisi girmesinin
        # en kolay yolu olurdu.
        s = self.tek("secret.example.yaml")
        for k, v in (s.get("stringData") or {}).items():
            self.assertEqual(v, "", "ornek Secret'ta deger var: " + k)
        self.assertNotIn("data", s, "ornek Secret base64 veri icermemeli")

    def test_ayricalik_yukseltme_kapali(self):
        _, kap = self.kap()
        g = kap.get("securityContext", {})
        self.assertFalse(g.get("allowPrivilegeEscalation", True))
        self.assertEqual(g.get("capabilities", {}).get("drop"), ["ALL"])

    # --- problar ve kaynaklar ---------------------------------------------

    def test_uc_prob_da_tanimli(self):
        _, kap = self.kap()
        for p in ("startupProbe", "livenessProbe", "readinessProbe"):
            self.assertIn(p, kap, p + " yok")

    def test_PROBLAR_STRICT_KULLANMIYOR(self):
        # readiness basarisiz olunca pod Service'ten cikar ve tek replika
        # oldugu icin pano tamamen erisilemez olur. Oysa veri akmadigini
        # teshis etmek icin panoya bakmak gerekiyor. Veri akisi alarmlari
        # webhook ile gidiyor, prob ile degil.
        _, kap = self.kap()
        for ad in ("startupProbe", "livenessProbe", "readinessProbe"):
            yol = kap[ad]["httpGet"]["path"]
            self.assertEqual(yol, "/health", ad + " beklenmeyen yol: " + yol)
            self.assertNotIn("strict", yol)

    def test_problar_health_ucuna_bakiyor(self):
        # Eskiden Docker HEALTHCHECK '/' kontrol ediyordu ve bu yalnizca
        # surecin ayakta oldugunu soyluyordu. Ayni hataya dusulmesin.
        _, kap = self.kap()
        for ad in ("startupProbe", "livenessProbe", "readinessProbe"):
            self.assertNotEqual(kap[ad]["httpGet"]["path"], "/")

    def test_startup_toleransi_en_az_30_saniye(self):
        # start.py once Node'u sonra veri kopruyu baslatiyor; ilk yanit
        # birkac saniye surebiliyor. Dar bir tolerans, saglikli bir
        # podu yeniden baslatma dongusune sokar.
        _, kap = self.kap()
        s = kap["startupProbe"]
        self.assertGreaterEqual(s["periodSeconds"] * s["failureThreshold"], 30)

    def test_kaynak_istegi_ve_siniri_var(self):
        _, kap = self.kap()
        r = kap["resources"]
        for bolum in ("requests", "limits"):
            self.assertIn("memory", r[bolum])
            self.assertIn("cpu", r[bolum])

    def test_bellek_siniri_istekten_buyuk(self):
        _, kap = self.kap()
        r = kap["resources"]

        def mib(s):
            s = str(s)
            if s.endswith("Mi"):
                return float(s[:-2])
            if s.endswith("Gi"):
                return float(s[:-2]) * 1024
            return float(s) / (1024 * 1024)

        self.assertGreater(mib(r["limits"]["memory"]),
                           mib(r["requests"]["memory"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
