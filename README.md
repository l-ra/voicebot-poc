# Voicebot PoC

Demonstrátor hlasové soutěže GPT-Live. Zachovává WebRTC a přidává nativní odchozí SIP. Soutěžní stav, otázky A/B/C a hodnocení řídí deterministický Python backend; žádný druhý LLM. Jde o fiktivní demo bez výhry. Používejte pouze vlastní telefonní čísla nebo čísla osob, které předem souhlasily.

## Konfigurace

```sh
cp config.example.json config.json
chmod 600 config.json
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Doplňte OpenAI API klíč, náhodný operator_token, demonstrační kód, model, hlas, public_origin (přesná adresa konzole bez koncového lomítka; při zapnutém Ingressu se nastavuje automaticky a v JSON není nutný) a SIP trunk. Pro WebRTC-only smažte objekt sip nebo nastavte null. SIP vyžaduje provider_url ve formátu sips:host:5061, Digest username/password, povolené caller ID a přesný seznam allowed_destinations v E.164. Žádné skutečné přihlašovací heslo účastníka se nevyžaduje. Operator token je autentizace konzole; nejedná se o soutěžní kód.

Nepřidávejte config.json, vlastní Secret manifesty nebo values s citlivými údaji do Git. Kontejner kopíruje výhradně aplikační soubory.

### Regionální OpenAI projekt

Pro projekt US [Storage & Processing] nastavte v config.json (nebo files/config.json v umbrella chartu) `"openai_region": "us"`. Výchozí hodnota global zachovává původní chování; eu je dostupná pro evropský projekt. Aplikace volí všechny Live REST endpointy (create, accept, reject, hangup) a Sideband WebSocket podle regionu: api.openai.com, us.api.openai.com nebo eu.api.openai.com. Nepoužívá fallback do jiného regionu. Při startu vypíše region a URL do provozního logu. Pro US použijte SIP URI sip:proj_XXXXXXXX@sip.api.openai.com;transport=tls a API klíč i webhook secret ze stejného US projektu. Pro EU používejte sip-eu.api.openai.com. Regionální projekt a API hostname musejí odpovídat; samotný region nenahrazuje případné oprávnění k GPT-Live SIP. Viz https://developers.openai.com/api/docs/guides/data-residency a https://developers.openai.com/api/docs/guides/voice-sip.

## Lokální spuštění

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest -v
python server.py
```

Otevřete http://localhost:8765 (musí odpovídat public_origin), vložte operator_token a zvolte WebRTC nebo SIP. U SIP vyplňte povolené číslo a potvrďte souhlas. Konzole sleduje vytáčení, vyzvánění, spojení a soutěžní stav. Umožňuje zavěšení; po dokončení soutěže backend vyčká 15 s na rozloučení a zavěsí. Nastavený limit zahrnuje i vytáčení. Jeden hovor současně. Při ztrátě spojení konzole se lze znovu připojit; backend se pokusí ukončit relaci při timeoutu a při výpadku sidebandu.

## SIP

OpenAI provádí signalizaci a přenos zvuku přímo s operátorem, nikoliv přes tento server. Backend volá POST /v1/live/sessions s transport.type=sip a připojuje se na sideband /attach. Zavěšení používá POST /v1/live/sessions/{id}/hangup. Příchozí hovory podporuje aktuální dev verze (viz níže); původní 0.2.0 pouze dial-out. SIP provider musí podporovat TLS, SDES-SRTP a kompatibilní kodeky; funkčnost konkrétního účtu Zadarma nebyla ověřena. Není zapotřebí vlastní veřejný SIP port, webhook ani Asterisk. Dokumentace: https://developers.openai.com/api/docs/guides/voice-sip?api=live

### Příchozí SIP hovory (dial-in)

Do existujícího files/config.json přidejte samostatnou sekci:

```json
"sip_inbound": {
  "enabled": true,
  "webhook_secret": "whsec_REPLACE_ME"
}
```

Skutečný signing secret získáte při registraci webhooku v OpenAI projektu. URL: https://<hostname>/webhooks/openai (stejný hostname jako Ingress). Přihlaste webhook pouze k live.transport.incoming; podporována je i starší událost live.call.incoming. API klíč, webhook a SIP URI musí patřit stejnému projektu. V Zadarma nasměrujte veřejné číslo na externí SIP URI sip:proj_XXXXXXXX@sip.api.openai.com;transport=tls (regionální EU endpoint sip-eu.api.openai.com používejte podle konfigurace projektu). Ověřte kompatibilitu TLS/SRTP a dostupnost GPT-Live SIP pro projekt. Dial-in nepotřebuje sekci sip pro odchozí hovory; ta může být null.

Webhook je veřejný HTTPS POST endpoint, nevyžaduje token operátora, ale ověřuje HMAC-SHA256 nad původním tělem a hlavičkami Standard Webhooks (tolerance 5 minut). Duplicitní delivery ID a session ID jsou evidovány v paměti po 73 hodinách. Po přijetí webhooku backend asynchronně přijme hovor, případně odmítne SIP 486 při obsazenosti. Volající slyší úvod pro příchozí hovor a stejný soutěžní scénář. Konzole po přihlášení automaticky objeví aktivní hovor, i pokud vznikl později. Zavření monitorovací stránky nezavěsí cizí příchozí hovor; ruční tlačítko Zavěsit ano. Platí jeden souběžný hovor napříč všemi transporty a stávající časový limit.

PoC omezení: deduplikace a fronta nejsou perzistentní, restart podu může ztratit rozpracované události a 204 webhook neznamená úspěšný SIP accept. Pro produkci použijte trvalou frontu a idempotentní evidenci. Příchozí číslo není autentizace; SIP hlavičky se nepoužívají k autorizaci. Veřejný dial-in může generovat API náklady, proto použijte testovací číslo a limity projektu/operátora. Ingress a cert-manager musí být funkční před registrací webhooku. Dokumentace: https://developers.openai.com/api/docs/guides/voice-sip?api=live

## Docker

Po publikaci image:

```sh
docker run --rm -p 127.0.0.1:8765:8765 \
  -v "$PWD/config.json:/run/voicebot/config.json:ro" \
  ghcr.io/l-ra/voicebot-poc:0.2.0
```

Soubor musí být čitelný pro neprivilegovaného uživatele kontejneru (UID/GID 10001). Nastavte ACL pro tento UID nebo používejte vlastnictví odpovídající kontejneru; nezveřejňujte soubor.

## Kubernetes / Helm OCI

```sh
kubectl create namespace voicebot
kubectl -n voicebot create secret generic voicebot-config \
  --from-file=config.json=./config.json
helm upgrade --install voicebot oci://ghcr.io/l-ra/charts/voicebot-poc \
  --version 0.2.0 -n voicebot
kubectl -n voicebot port-forward svc/voicebot-voicebot 8765:8765
```

Alternativně instalujte lokální chart: helm upgrade --install voicebot ./helm/voicebot-poc -n voicebot. Secret musí existovat před instalací. Chart záměrně používá ClusterIP, jednu repliku (stav v paměti), Recreate strategii, non-root a read-only filesystem. Pro vzdálený přístup použijte vestavěný HTTPS Ingress v aktuálním dev chartu (viz níže); starší release 0.2.0 jej ještě neobsahuje. WebRTC vyžaduje bezpečný browser origin. Po změně Secretu restartujte Deployment. Nezveřejňujte konzoli bez HTTPS a přístupové kontroly.

### Alternativa: vlastní nadřazený chart s jednou dependency

Tento postup je určen pro deklarativní nasazení (např. GitOps). Nadřazený chart sám načte konfiguraci z pevné cesty `files/config.json` a vytvoří Secret. Není potřeba `--set-file`, ruční vytváření Secretu ani jiné parametry příkazové řádky. Připravte v neveřejném deployment projektu následující strukturu:

```text
my-voicebot/
  Chart.yaml
  values.yaml
  files/
    config.json
  templates/
    secret.yaml
```

**Chart.yaml**

```yaml
apiVersion: v2
name: my-voicebot
type: application
version: 0.1.0
dependencies:
  - name: voicebot-poc
    version: 0.0.0-dev
    repository: oci://ghcr.io/l-ra/charts
```

**values.yaml**

```yaml
voicebot-poc:
  existingSecret: voicebot-config
  ingress:
    enabled: true
    className: nginx
    hostname: voicebot.example.org
    tls:
      enabled: true
    certManager:
      enabled: true
      clusterIssuer: letsencrypt-prod
```

Hostname se definuje pouze v values.yaml. Chart jej použije v pravidle Ingressu, TLS hosts a v proměnné prostředí VOICEBOT_PUBLIC_ORIGIN. Aplikace načte JSON ze Secretu a přepíše efektivní public_origin touto hodnotou před validací. Původní JSON v Secretu se nemění. Při zapnutém TLS bude výsledkem https://voicebot.example.org. Pokud je Ingress vypnutý, je nadále nutné zadat public_origin v JSON (např. http://localhost:8765 pro port-forward).

Předpoklady: funkční Ingress controller, DNS záznam hostname směřující na controller, nainstalovaný cert-manager s ingress-shim a existující ClusterIssuer letsencrypt-prod nakonfigurovaný pro Let's Encrypt. Chart cert-manager ani ClusterIssuer neinstaluje; název issueru a ingressClass lze přizpůsobit clusteru. Pro HTTP-01 musí být challenge dostupná z internetu; alternativně použijte DNS-01. TLS Secret vytvoří cert-manager automaticky. Vlastní existující TLS Secret lze použít přes ingress.tls.secretName a ingress.certManager.enabled: false. Pro čisté HTTP je nutné vypnout TLS i certManager (WebRTC mimo localhost vyžaduje HTTPS). HTTPS přesměrování nastavte podle používaného controlleru pomocí ingress.annotations.

**templates/secret.yaml**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: {{ index .Values "voicebot-poc" "existingSecret" | quote }}
type: Opaque
data:
  config.json: {{ required "Missing or empty files/config.json in parent chart" (.Files.Get "files/config.json") | b64enc | quote }}
```

**files/config.json** (soubor uvnitř nadřazeného chartu; všechny údaje níže jsou pouze příklady):

```json
{
  "openai_api_key": "REPLACE_ME",
  "operator_token": "REPLACE_WITH_RANDOM_TOKEN_AT_LEAST_24_CHARS",
  "quiz_code": "4826",
  "model": "gpt-live-1.1",
  "openai_region": "global",
  "voice": "marin",
  "max_call_seconds": 300,
  "sip_inbound": {"enabled": false, "webhook_secret": "whsec_REPLACE_ME"},
  "sip": {
    "provider_url": "sips:sip.example.invalid:5061",
    "username": "REPLACE_ME",
    "password": "REPLACE_ME",
    "caller_number": "+12025550100",
    "allowed_destinations": [
      "+12025550101"
    ]
  }
}
```

Pro čistě WebRTC instalaci nastavte `sip` na `null`. Pro SIP nahraďte ukázkové údaje skutečnými hodnotami operátora, caller ID a povolenými cílovými čísly. Nastavte vlastní náhodný operator token. Při zapnutém Ingressu `public_origin` v JSON vynechte; bez Ingressu jej doplňte. Soubor `files/config.json` musí být součástí zdrojů nadřazeného chartu při renderování; nesmí být vyloučen pomocí `.helmignore`. Pokud chybí nebo je prázdný, renderování skončí chybou.

**Deklarativní nasazení – příklad Argo CD Application**

Následující manifest patří do vašeho GitOps projektu (nikoli do šablon nadřazeného chartu). Nahraďte URL privátního deployment repozitáře a případně cestu k chartu. Argo CD musí mít přístup ke zdrojům i OCI dependency; OCI chart a image musí být veřejné, nebo je nutné nastavit odpovídající autentizaci v Argo CD a imagePullSecrets pro Kubernetes.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: voicebot
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://git.example.invalid/deployments.git
    targetRevision: main
    path: my-voicebot
    helm:
      releaseName: voicebot
  destination:
    server: https://kubernetes.default.svc
    namespace: voicebot
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

Argo CD při renderování stáhne dependency (případně použije předem připravenou dependency v `charts/`), načte `files/config.json` pomocí `.Files.Get` v nadřazeném chartu a nasadí Secret i aplikaci. Není nutné předávat žádné Helm parametry. Pro jiný GitOps systém použijte stejný nadřazený chart a jeho standardní mechanismus deklarativního nasazení. Service `voicebot-voicebot` je typu ClusterIP na portu 8765; vestavěný Ingress ji zpřístupní na hostname z values.yaml. Pro produkční nasazení používejte konkrétní neměnnou verzi chartu obsahující tuto funkci.

Při změně konfigurace aktualizujte `files/config.json` ve zdroji nasazení a synchronizujte aplikaci. Aplikace načítá konfiguraci jen při startu; samotná změna Secretu v této verzi chartu automaticky nevyvolá rollout. Restart zajistěte deklarativně pomocí správce restartů při změně Secretu (např. Reloader s odpovídající konfigurací v clusteru), nebo jiným mechanismem vašeho deployment systému. 

Tato varianta je alternativou k přímé instalaci výše, nikoli doplňkem k již existujícímu ručně spravovanému Secretu stejného jména. Při více instalacích v jednom namespace zvolte odlišné jméno Secretu přes `voicebot-poc.existingSecret`.

**Bezpečnost:** Soubor s reálnými credentials nevkládejte do veřejného upstream repozitáře ani veřejného chartu. Soubor je součástí deployment chartu v čitelné podobě a vyrenderovaný Secret obsahuje pouze base64, nikoli šifrování. Chraňte zdroj nasazení, přístup k renderovaným manifestům, Kubernetes Secretům a případným metadatům/historii Helm release. Při použití GitOps zajistěte bezpečné dodání nebo dešifrování souboru před renderováním; samotný privátní Git repozitář není šifrování secrets. Zapněte šifrování Secretů v etcd a omezte RBAC. Pokud požadujete, aby credentials nebyly součástí chartu ani renderovaných manifestů, použijte variantu s externě spravovaným Secretem.

## Provozní logy a diagnostika

Aplikace zapisuje strukturované JSON logy na stdout kontejneru. Výchozí úroveň INFO poskytuje základní audit (start, webhook, přijetí/odmítnutí hovoru, změny stavu, delegace, zavěšení). WARNING/ERROR zachytí problémy; DEBUG přidává typy Sideband událostí a odeslaných zpráv. Nastavení v files/config.json:

```json
"log_level": "DEBUG"
```

Po změně restartujte pod. Logy získáte v Kubernetes například přes kubectl -n voicebot logs deployment/voicebot-voicebot -f nebo v GUI clusteru. Pro HTTP 401 při accept hledejte event openai.http.response, operation accept, http_status, request_id, error_type, error_code a error_message. Chyba znamená, že webhook dorazil, ale OpenAI odmítlo API operaci; zkontrolujte klíč, jeho oprávnění a shodu projektu. Samotné logování autorizaci neopravuje. Při chybě WebSocketu se zaznamená typ výjimky, HTTP status (je-li dostupný) a místo v kódu.

Nikdy se úmyslně neloguje celé tělo requestu/response, Authorization, SIP heslo, soutěžní kód, telefonní čísla, SDP, audio ani přepis. Strukturované chybové zprávy poskytovatele jsou omezené a redigované podle známých citlivých hodnot a vzorů; mohou přesto obsahovat neočekávané údaje poskytovatele. Omezte přístup k logům a jejich retenci. Audit není neměnný bezpečnostní audit ani perzistentní úložiště; retenci zajišťuje infrastruktura.

### Diagnostika regionálního HTTP 404

Pro dočasné ladění nastavte v JSON konfiguraci:

```json
"openai_region": "us",
"log_level": "DEBUG",
"diagnostic_models_probe": true,
"diagnostic_body_preview": true
```

Po restartu se jednou provede GET /v1/models na nastaveném regionálním hostu se stejným API klíčem. Probe je asynchronní, nevytváří hlasovou relaci a neblokuje start; úspěch nepotvrzuje dostupnost Live SIP accept ani oprávnění k němu. Logy openai.probe.request, openai.probe.result a openai.http.response obsahují výsledek; seznam modelů se neloguje. HTTP odpovědi obsahují výslednou URL bez query, vybrané gateway hlavičky (Server, CF-Ray, Content-Type, request ID, Via aj.), redirect metadata a u accept také dobu odezvy. REST požadavky nenásledují redirect automaticky, aby nedošlo k přesměrování regionálního API klíče; případný Location se zaznamená bez query. Chybová neJSON odpověď má délku a SHA-256 omezeného prefixu; opt-in diagnostic_body_preview přidá redigovaný náhled do 1200 znaků. Čtení chybového těla je omezené na 16 KiB plus jeden detekční byte. Náhled může obsahovat neznámé citlivé údaje poskytovatele, proto jej zapínejte pouze při ladění, před sdílením logy zkontrolujte a poté obě diagnostické volby vypněte. Hlavičky Authorization, Cookie a Set-Cookie se nelogují. Chybný podpis webhooku zaznamená pouze přítomnost hlaviček a stáří timestampu, nikoli podpis ani tělo.

## CI/CD a vydávání verzí

Všechny nové image (dev, neměnné vývojové buildy, release i jejich aliasy) jsou multiplatformní OCI image pro `linux/amd64` (x86-64) a `linux/arm64` (AArch64). GitHub Actions používá Docker Buildx a QEMU pro ARM64 build; před publikací chartu ověřuje přítomnost obou platforem v registry manifestu. Docker a Kubernetes automaticky vyberou správnou architekturu; Helm chart ani konfiguraci není nutné měnit. Starší již publikované verze se zpětně nepřebudovávají.

PR provádí pouze testy. Push do main (nebo workflow_dispatch na main) po úspěšných testech publikuje vývojové artefakty. Git tag vX.Y.Z nebo vX.Y.Z-rc.N spustí řízený release; tag musí být na commitu dosažitelném z main. Soubor VERSION se již nepoužívá. Verze GUI se načítá z /healthz a odpovídá buildu.

| Trigger | Image tag | OCI chart verze |
| --- | --- | --- |
| main, ruční build na main | 0.0.0-dev.RUN.ATTEMPT | 0.0.0-dev.RUN.ATTEMPT |
| aktuální main | dev, sha-COMMIT | 0.0.0-dev |
| v0.3.0 | 0.3.0 | 0.3.0 |
| v0.4.0-rc.1 | 0.4.0-rc.1 | 0.4.0-rc.1 |

Registry: image ghcr.io/l-ra/voicebot-poc, chart oci://ghcr.io/l-ra/charts/voicebot-poc. RUN a ATTEMPT jsou GitHub Actions run number a run attempt. Neměnný vývojový chart odkazuje na stejně označený image. Plovoucí chart 0.0.0-dev odkazuje na image dev s výchozí Always pull policy. Alias sha-COMMIT se publikuje spolu s dev pro aktuální main a při opakovaném buildu téhož commitu se může změnit; pro přesné připnutí použijte RUN.ATTEMPT nebo digest.

Pro vývojový umbrella chart použijte:

```yaml
dependencies:
  - name: voicebot-poc
    version: 0.0.0-dev
    repository: oci://ghcr.io/l-ra/charts
```

Pro GitOps preferujte konkrétní neměnnou verzi, např. 0.0.0-dev.42.1; změňte ji deklarativně v Chart.yaml, obnovte případný Chart.lock a vendored dependency. Přepis plovoucího tagu sám o sobě nevyvolá rollout ani nemusí invalidovat cache GitOps systému. Dřívější build, který již není aktuálním main, neaktualizuje plovoucí aliasy.

### Řízený release

Vytvořte Git tag (například v0.3.0) přes GitHub nebo git tag a git push. Nevytvářejte předem GitHub Release, vytvoří jej pipeline s generovaným changelogem a přiloženým Helm balíčkem. RC tagy mají formát v0.4.0-rc.1. Pipeline ověří dosažitelnost z main a absenci obou release tagů v GHCR; chyby registry kromě potvrzeného neexistujícího repozitáře zastaví publikaci. Stabilní release může aktualizovat latest pouze pokud registry neobsahuje vyšší stabilní verzi, včetně původní 0.2.0. RC latest nemění. Existující 0.2.0 se nepřepisuje.

Publikační joby jsou serializovány; GitHub concurrency může nahradit čekající job novějším. Registry publikace image a chartu není atomická: při částečném selhání se vydání automaticky nepřepisuje; správce musí zkontrolovat stav a zvolit řízenou opravu nebo nové číslo verze. Ochrana platí pro tuto pipeline, nikoli ruční zápis správce do GHCR. Doporučeno chránit release tagy pravidly GitHub rulesets.

GitHub standardně vytváří GHCR balíčky jako private i u public repozitáře. Vlastník nastaví Package settings → Change visibility → Public u obou balíčků, pokud už nejsou veřejné.

## Omezení

PoC: stav v paměti, bez nahrávek a bez trvalého logu. Konzole nezobrazuje surový přepis (obsahuje i soutěžní kód). Interpretace časovaných transcript delta je heuristika a může vyžadovat zopakování odpovědi. Automatický hangup po rozloučení používá časovou rezervu, nikoliv potvrzení přehrání. Při pádu procesu není možné garantovat ukončení vzdáleného hovoru; nastavte limity i u SIP operátora. Živý hovor vyžaduje skutečné credentials a kompatibilní SIP trunk. Automatické testy nenahrazují end-to-end ověření.
