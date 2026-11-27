# Hlasová soutěž – GPT-Live 1.1 / WebRTC — 0.1.0

Historická verze původního lokálního PoC, pouze pro WebRTC. Aktuální verze je na main.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
read -rsp 'OpenAI API key: ' OPENAI_API_KEY; echo
export OPENAI_API_KEY
python server.py
```

Otevřete http://127.0.0.1:8765/, povolte mikrofon a použijte demonstrační kód 4826. Fiktivní soutěž, bez skutečné výhry. Server naslouchá pouze na loopbacku. Pro produkční nasazení použijte novější verzi.

`python -m unittest -v` spouští testy deterministické soutěžní logiky bez OpenAI.
