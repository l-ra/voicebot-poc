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

## Publikace

Actions na main testují Python, JS a Helm a publikují image ghcr.io/l-ra/voicebot-poc:0.2.0, latest a sha-<commit>; OCI chart ghcr.io/l-ra/charts/voicebot-poc:0.2.0. PR pouze testují. Při vydání další verze změňte VERSION, Chart.yaml a verzi v konzoli. GitHub standardně vytváří GHCR balíčky jako private i u public repozitáře. Po prvním úspěšném push musí vlastník v Package settings u obou balíčků nastavit Change visibility → Public, pokud už nejsou veřejné.

## Omezení

PoC: stav v paměti, bez nahrávek a bez trvalého logu. Konzole nezobrazuje surový přepis (obsahuje i soutěžní kód). Interpretace časovaných transcript delta je heuristika a může vyžadovat zopakování odpovědi. Automatický hangup po rozloučení používá časovou rezervu, nikoliv potvrzení přehrání. Při pádu procesu není možné garantovat ukončení vzdáleného hovoru; nastavte limity i u SIP operátora. Živý hovor vyžaduje skutečné credentials a kompatibilní SIP trunk. Automatické testy nenahrazují end-to-end ověření.
