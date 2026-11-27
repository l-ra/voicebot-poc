# Voicebot PoC — 0.2.0

Demonstrátor hlasové soutěže GPT-Live. Zachovává WebRTC a přidává nativní odchozí SIP. Soutěžní stav, otázky A/B/C a hodnocení řídí deterministický Python backend; žádný druhý LLM. Jde o fiktivní demo bez výhry. Používejte pouze vlastní telefonní čísla nebo čísla osob, které předem souhlasily.

## Konfigurace

```sh
cp config.example.json config.json
chmod 600 config.json
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Doplňte OpenAI API klíč, náhodný operator_token, demonstrační kód, model, hlas, public_origin (přesná adresa konzole bez koncového lomítka) a SIP trunk. Pro WebRTC-only smažte objekt sip nebo nastavte null. SIP vyžaduje provider_url ve formátu sips:host:5061, Digest username/password, povolené caller ID a přesný seznam allowed_destinations v E.164. Žádné skutečné přihlašovací heslo účastníka se nevyžaduje. Operator token je autentizace konzole; nejedná se o soutěžní kód.

Nepřidávejte config.json, vlastní Secret manifesty nebo values s citlivými údaji do Git. Kontejner kopíruje výhradně aplikační soubory.

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

OpenAI provádí signalizaci a přenos zvuku přímo s operátorem, nikoliv přes tento server. Backend volá POST /v1/live/sessions s transport.type=sip a připojuje se na sideband /attach. Zavěšení používá POST /v1/live/sessions/{id}/hangup. Příchozí hovory nejsou součástí 0.2.0. SIP provider musí podporovat TLS, SDES-SRTP a kompatibilní kodeky; funkčnost konkrétního účtu Zadarma nebyla ověřena. Není zapotřebí vlastní veřejný SIP port, webhook ani Asterisk. Dokumentace: https://developers.openai.com/api/docs/guides/voice-sip?api=live

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

Alternativně instalujte lokální chart: helm upgrade --install voicebot ./helm/voicebot-poc -n voicebot. Secret musí existovat před instalací. Chart záměrně používá ClusterIP, jednu repliku (stav v paměti), Recreate strategii, non-root a read-only filesystem. Pro vzdálený přístup použijte vlastní HTTPS Ingress a nastavte public_origin. WebRTC vyžaduje bezpečný browser origin. Po změně Secretu restartujte Deployment. Nezveřejňujte konzoli bez HTTPS a přístupové kontroly.

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
    version: 0.2.0
    repository: oci://ghcr.io/l-ra/charts
```

**values.yaml**

```yaml
voicebot-poc:
  existingSecret: voicebot-config
```

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
  "voice": "marin",
  "public_origin": "http://localhost:8765",
  "max_call_seconds": 300,
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

Pro čistě WebRTC instalaci nastavte `sip` na `null`. Pro SIP nahraďte ukázkové údaje skutečnými hodnotami operátora, caller ID a povolenými cílovými čísly. Nastavte vlastní náhodný operator token a `public_origin` podle skutečné HTTPS adresy konzole. Soubor `files/config.json` musí být součástí zdrojů nadřazeného chartu při renderování; nesmí být vyloučen pomocí `.helmignore`. Pokud chybí nebo je prázdný, renderování skončí chybou.

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

Argo CD při renderování stáhne dependency (případně použije předem připravenou dependency v `charts/`), načte `files/config.json` pomocí `.Files.Get` v nadřazeném chartu a nasadí Secret i aplikaci. Není nutné předávat žádné Helm parametry. Pro jiný GitOps systém použijte stejný nadřazený chart a jeho standardní mechanismus deklarativního nasazení. Service `voicebot-voicebot` je typu ClusterIP na portu 8765; přístup zajistěte vlastním deklarativním HTTPS Ingressem nebo jiným odpovídajícím mechanismem.

Při změně konfigurace aktualizujte `files/config.json` ve zdroji nasazení a synchronizujte aplikaci. Aplikace načítá konfiguraci jen při startu; samotná změna Secretu v této verzi chartu automaticky nevyvolá rollout. Restart zajistěte deklarativně pomocí správce restartů při změně Secretu (např. Reloader s odpovídající konfigurací v clusteru), nebo jiným mechanismem vašeho deployment systému. 

Tato varianta je alternativou k přímé instalaci výše, nikoli doplňkem k již existujícímu ručně spravovanému Secretu stejného jména. Při více instalacích v jednom namespace zvolte odlišné jméno Secretu přes `voicebot-poc.existingSecret`.

**Bezpečnost:** Soubor s reálnými credentials nevkládejte do veřejného upstream repozitáře ani veřejného chartu. Soubor je součástí deployment chartu v čitelné podobě a vyrenderovaný Secret obsahuje pouze base64, nikoli šifrování. Chraňte zdroj nasazení, přístup k renderovaným manifestům, Kubernetes Secretům a případným metadatům/historii Helm release. Při použití GitOps zajistěte bezpečné dodání nebo dešifrování souboru před renderováním; samotný privátní Git repozitář není šifrování secrets. Zapněte šifrování Secretů v etcd a omezte RBAC. Pokud požadujete, aby credentials nebyly součástí chartu ani renderovaných manifestů, použijte variantu s externě spravovaným Secretem.

## Publikace

Actions na main testují Python, JS a Helm a publikují image ghcr.io/l-ra/voicebot-poc:0.2.0, latest a sha-<commit>; OCI chart ghcr.io/l-ra/charts/voicebot-poc:0.2.0. PR pouze testují. Při vydání další verze změňte VERSION, Chart.yaml a verzi v konzoli. GitHub standardně vytváří GHCR balíčky jako private i u public repozitáře. Po prvním úspěšném push musí vlastník v Package settings u obou balíčků nastavit Change visibility → Public, pokud už nejsou veřejné.

## Omezení

PoC: stav v paměti, bez nahrávek a bez trvalého logu. Konzole nezobrazuje surový přepis (obsahuje i soutěžní kód). Interpretace časovaných transcript delta je heuristika a může vyžadovat zopakování odpovědi. Automatický hangup po rozloučení používá časovou rezervu, nikoliv potvrzení přehrání. Při pádu procesu není možné garantovat ukončení vzdáleného hovoru; nastavte limity i u SIP operátora. Živý hovor vyžaduje skutečné credentials a kompatibilní SIP trunk. Automatické testy nenahrazují end-to-end ověření.
