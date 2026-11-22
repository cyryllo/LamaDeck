# Serwery MCP w czacie LamaDeck: plan i specyfikacja

Dokument dla asystenta AI, który rozwija LamaDeck. Spisany 2026-11-22 na prośbę użytkownika w projekcie ArchFlow (`~/Projekty/FossFLOW`). Opisuje funkcję zbudowaną w LamaDeck 1.3.0 (2026-11-22); decyzje użytkownika w §5.

**Cel użytkownika:** w czacie LamaDeck (w oknie i w przeglądarce) podłączyć serwery **MCP** (Model Context Protocol). Na początek serwer **ArchFlow**: lokalny model z Ollamy ma rysować diagramy, np. „narysuj mój homelab”, a gotowy diagram pojawia się w ArchFlow.

## 1. ArchFlow i jego serwer MCP

**ArchFlow** to edytor diagramów izometrycznych użytkownika (repo `cyryllo/ArchFlow`, lokalnie `~/Projekty/FossFLOW`; aplikacja w przeglądarce, Docker i aplikacja desktopowa). Ma serwer MCP **`archflow-mcp`**, opublikowany na npm (od 0.8.0; HTTP od 0.8.1). Pełna instrukcja: `docs/mcp.pl.md` w repo ArchFlow, online: https://github.com/cyryllo/ArchFlow/blob/master/docs/mcp.pl.md

**Narzędzia (14):** `list_icons`, `list_diagrams`, `get_diagram`, `build_diagram`, `add_nodes`, `update_node`, `remove_node`, `add_connectors`, `remove_connector`, `add_zone`, `remove_zone`, `add_text`, `rename_diagram`, `export_json`. Najważniejsze jest `build_diagram`: cały diagram jednym wywołaniem, rozmieszczanie na siatce robi serwer.

**Dwa sposoby połączenia:**

| Transport | Jak | Gdzie trafiają diagramy |
|---|---|---|
| **HTTP** (Streamable HTTP) | `http://<serwer>:9500/mcp`, nagłówek `Authorization: Bearer <MCP_TOKEN>` | ArchFlow w Dockerze (*Moje diagramy*) |
| **stdio** (proces) | `npx -y archflow-mcp@latest`, zmienna `ARCHFLOW_URL=http://<serwer>:9500` albo `ARCHFLOW_DIR=<folder>` | Docker albo folder; domyślnie `~/Dokumenty/ArchFlow` (aplikacja desktopowa) |

Szczegóły ważne dla klienta:

- **HTTP jest bezstanowy.** Serwer nie wysyła `Mcp-Session-Id` i odpowiada zwykłym JSON-em (nie SSE). Klient i tak powinien obsłużyć sesję i SSE, bo inne serwery MCP mogą ich używać (kod referencyjny w §6 to robi).
- **Bez tokenu albo ze złym** HTTP zwraca 401. Gdy MCP w kontenerze jest wyłączone (brak `MCP_TOKEN`), `/mcp` zwraca 503 z komunikatem *The ArchFlow MCP server is off*.
- **stdio wymaga Node.js 18+** (polecenie `npx`) na komputerze z LamaDeck. HTTP nie wymaga niczego.
- **Instrukcje serwera.** `initialize` zwraca pole `instructions` (ok. 1,5 tys. znaków): jak budować diagramy, że ikony są ogólne, a nie logo produktów, i jak działa rozmieszczanie. Trzeba je dołączyć do rozmowy jako wiadomość systemową. Bez nich model gorzej sobie radzi.
- **Serwer wybacza błędy modeli.** Nieznany identyfikator ikony (np. `raspberry-pi`) zastępuje najbliższą ikoną i pisze o tym w wyniku. Inne błędy wracają jako wynik z `isError: true` i opisem; trzeba je przekazać modelowi jako tekst, żeby mógł się poprawić.
- **Adnotacje narzędzi:** `readOnlyHint` (`list_icons`, `list_diagrams`, `get_diagram`) oraz `destructiveHint` (`remove_node`, `remove_connector`, `remove_zone`).

**Sprawdzone z lokalnymi modelami** (Ollama, kontekst 32k): `qwen3:30b-a3b` i `gemma4:26b` zbudowały poprawny diagram homelaba w 4–5 minut; gemma sama poprawiła swój błąd po pierwszej próbie. Modele 7–8B słabo radzą sobie z długim opisem `build_diagram`.

## 2. Co już jest w LamaDeck, a czego brakuje

Pętla narzędzi już istnieje: `CzatWorker._zapytanie()` dodaje `tools` (dziś `NARZEDZIA_KATALOGU` dla katalogu roboczego), `_runda()` zbiera `tool_calls`, a `_wykonaj_narzedzie()` je wykonuje i odsyła tekst. Są też limit `_MAKS_KROKOW_NARZEDZI`, zgoda na zapis przez `prosba_zapisu` z `threading.Event` (działa też z przeglądarki) i sprawdzanie możliwości `tools` modelu (`_sprawdz_narzedzia`).

**Brakuje tylko klienta MCP**, który:

1. łączy się z serwerem MCP;
2. pobiera listę narzędzi i dokleja ją do `tools`;
3. przekazuje wywołania tych narzędzi do serwera.

Sama Ollama nie mówi po MCP: wywołania narzędzi zawsze wykonuje program-klient, czyli tu LamaDeck.

## 3. Zakres do zbudowania (propozycja)

### 3.1 Klient MCP (bez nowych zależności)

- Transporty **HTTP** (`requests`) i **stdio** (`subprocess`). Działający kod referencyjny jest w §6, sprawdzony 2026-11-22 z `archflow-mcp` 0.8.1 przez oba transporty.
- Przebieg rozmowy z serwerem:
  1. `initialize`, z `protocolVersion` `2025-06-18`; serwer odpowiada wersją, której używa;
  2. powiadomienie `notifications/initialized`;
  3. `tools/list`;
  4. dalej `tools/call` z polami `name` i `arguments`.
- Nagłówki HTTP:
  - `Accept: application/json, text/event-stream`;
  - po inicjalizacji `MCP-Protocol-Version`;
  - `Mcp-Session-Id`, jeśli serwer go podał.
- stdio: jedna wiadomość JSON-RPC na linię, bez znaków nowej linii w środku. stderr procesu nie jest kanałem protokołu (do logu albo ignorować).
- Cały ruch sieciowy w wątku, tak jak reszta czatu (`QThread`, nieblokujące GUI).

### 3.2 Integracja z czatem

- **Dla rozmowy z włączonym MCP:** `_zapytanie()` dokleja do `tools` narzędzia z włączonych serwerów (`narzedzia_dla_ollamy()` w §6), niezależnie od katalogu roboczego. `instructions` z `initialize` idą jako wiadomość systemowa.
- **Nazwy narzędzi:** obecne nazwy z ArchFlow nie kolidują z narzędziami katalogu (`list_files`, `read_file`, `search_files`, `write_file`, `edit_file`). Przy kolizji (inne serwery) dodaj przedrostek, np. `archflow__build_diagram`. Słownik `nazwa -> (serwer, oryginalna nazwa)` rozstrzyga, gdzie wysłać wywołanie.
- **`_wykonaj_narzedzie()`:** zanim zgłosi „unknown tool”, sprawdza, czy to narzędzie MCP. Jeśli tak, woła `tools/call` i zwraca `tekst_wyniku()`. Sygnał `narzedzie` z krokiem: `nazwa` = narzędzie, a w miejscu ścieżki np. nazwa serwera.
- **Zgody:** narzędzia z `destructiveHint` (usuwanie) potwierdza użytkownik, tak jak zapis pliku (ten sam mechanizm `prosba_zapisu`, z innym tekstem okna). Narzędzia z `readOnlyHint` działają bez pytania. Pozostałe są do decyzji użytkownika (np. ustawienie „pytaj o każde narzędzie MCP / tylko o usuwanie”).
- **Tylko dla modeli z `tools`** (już jest `_sprawdz_narzedzia`).
- **Kontekst:** LamaDeck świadomie nie ustawia `num_ctx` na rozmowę (todo.md, „Do rozważenia później”, decyzja z 2026-10-30), działa globalne `OLLAMA_CONTEXT_LENGTH`. Opisy narzędzi ArchFlow i lista ikon zajmują kilka tysięcy tokenów, więc przy włączonym MCP warto pokazać ostrzeżenie, gdy kontekst jest mniejszy niż ok. 16–32k. Wskaźnik zajętości kontekstu już istnieje.
- **Czat w przeglądarce:** ta sama logika przez `CzatWorker`; zgody przez istniejący mechanizm próśb do przeglądarki.

### 3.3 Ustawienia i interfejs

- **Ustawienia → Czat → „Serwery MCP”:** lista serwerów, każdy z polami nazwa, włączony/wyłączony i typ (HTTP: adres i token; polecenie: polecenie, argumenty i zmienne środowiskowe). Przycisk **„Sprawdź połączenie”** wykonuje `initialize` i `tools/list` i pokazuje liczbę narzędzi albo błąd.
- **Gotowy wpis „ArchFlow”**, wypełniający pola:
  - HTTP: `http://<host>:9500/mcp` i token;
  - polecenie: `npx -y archflow-mcp@latest` z `ARCHFLOW_URL`.
  Plus krótka informacja, jak włączyć MCP w ArchFlow (`MCP_TOKEN` w `.env` kontenera).
- **W czacie:** przełącznik „Narzędzia MCP” przy wiadomości albo rozmowie, na wzór przełącznika wyszukiwania w internecie. Kroki narzędzi pokazywane tak jak dziś przy katalogu roboczym.
- **Przechowywanie:** QSettings. Token zapisany otwartym tekstem jest do przyjęcia w tym projekcie (osobiste narzędzie w LAN), ale warto to powiedzieć użytkownikowi.
- **Procesy stdio:** uruchamiane przy pierwszym użyciu, trzymane do zamknięcia programu, zamykane przy wyjściu. Listę narzędzi trzeba odświeżać przy „Sprawdź połączenie” i przy starcie rozmowy.
- **Konwencje projektu:** napisy przez `_()` (plus tłumaczenia w `lang/` i `web/`), kolory tylko w `theme.qss`, komentarze WHAT/WHY po polsku.

## 4. Jak testować z ArchFlow

1. ArchFlow z MCP w Dockerze, z repo ArchFlow:

   ~~~bash
   cd ~/Projekty/FossFLOW
   MCP_TOKEN=$(openssl rand -hex 32) docker compose -f compose.dev.yml up -d --build
   ~~~

   ArchFlow będzie pod `http://localhost:3000`, a MCP pod `http://localhost:3000/mcp`. Token trzeba zapamiętać albo dopisać do `.env`. Produkcyjny `compose.yml` używa portu 9500.
2. Sprawdzenie bez LamaDeck: `curl -X POST http://localhost:3000/mcp` ma zwrócić *Missing or wrong token*.
3. W LamaDeck dodaj serwer i kliknij „Sprawdź połączenie”: oczekiwane **14 narzędzi**.
4. Czat z modelem z `tools` (np. `qwen3:30b-a3b`, kontekst ≥ 32k) i prośba: *„Narysuj w ArchFlow diagram mojego homelaba: router, switch, serwer Proxmox z trzema maszynami wirtualnymi (Home Assistant, Nextcloud, Jellyfin), NAS i Raspberry Pi z Pi-hole. Pogrupuj to w strefy.”*
5. Diagram powinien pojawić się w ArchFlow: *Moje diagramy*, a model poda link `…/display/<nazwa>`.
6. Transport stdio: serwer typu polecenie `npx -y archflow-mcp@latest` z `ARCHFLOW_DIR=/tmp/test-archflow`; pliki `.json` pojawią się w tym folderze.

## 5. Do decyzji użytkownika

- [x] Numer wersji: **1.3.0**.
- [x] Transport: **od razu HTTP i stdio** (polecenie).
- [x] Zgody: **ustawienie, domyślnie tylko przed usuwaniem** (drugi wybór: przed każdym, które coś zmienia); odczyt bez pytania.
- [x] Czat w przeglądarce: **od 1.3.0**. Serwerów można dodać dowolnie wiele (ArchFlow to gotowy wpis).

## 6. Kod referencyjny (sprawdzony)

Minimalny klient obu transportów, bez nowych zależności. Sprawdzony 2026-11-22 z `archflow-mcp` 0.8.1:

- przez HTTP (Docker): 14 narzędzi, `build_diagram`, błąd dla nieistniejącego diagramu;
- przez stdio (`npx -y archflow-mcp@latest`): to samo, plus zastąpienie złej ikony `databse` przez `storage`.

Do przeniesienia do `lamadeck.py` według konwencji projektu: wątki, sygnały, `_()`.

~~~python
# Minimalny klient MCP (HTTP i stdio) bez nowych zależności: requests + biblioteka standardowa.
# Przykład referencyjny dla LamaDeck - sprawdzony z serwerem archflow-mcp.
import json, os, subprocess, threading, queue, itertools
import requests

WERSJA_PROTOKOLU = "2025-06-18"
KLIENT = {"name": "LamaDeck", "version": "1.3.0"}


class KlientMcpHttp:
    # WHAT: serwer MCP przez Streamable HTTP (np. ArchFlow w Dockerze: http://host:9500/mcp).
    def __init__(self, url, token=None, timeout=120):
        self.url, self.timeout = url, timeout
        self.naglowki = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            self.naglowki["Authorization"] = f"Bearer {token}"
        self._id = itertools.count(1)
        self.instrukcje = ""

    def _wyslij(self, metoda, params=None, powiadomienie=False):
        wiad = {"jsonrpc": "2.0", "method": metoda}
        if params is not None:
            wiad["params"] = params
        if not powiadomienie:
            wiad["id"] = next(self._id)
        r = requests.post(self.url, json=wiad, headers=self.naglowki, timeout=self.timeout, stream=True)
        # WHY: serwer z sesją podaje Mcp-Session-Id - odsyłamy go w każdym kolejnym zapytaniu
        if r.headers.get("Mcp-Session-Id"):
            self.naglowki["Mcp-Session-Id"] = r.headers["Mcp-Session-Id"]
        if powiadomienie:
            return None
        if r.status_code == 401:
            raise RuntimeError("Zły albo brak tokenu (Authorization: Bearer)")
        r.raise_for_status()
        if r.headers.get("Content-Type", "").startswith("text/event-stream"):
            # WHY: część serwerów odpowiada strumieniem SSE - szukamy wiadomości z naszym id
            for linia in r.iter_lines(decode_unicode=True):
                if linia and linia.startswith("data:"):
                    odp = json.loads(linia[5:].strip())
                    if odp.get("id") == wiad["id"]:
                        return self._wynik(odp)
            raise RuntimeError("Serwer zamknął strumień bez odpowiedzi")
        return self._wynik(r.json())

    @staticmethod
    def _wynik(odp):
        if "error" in odp:
            raise RuntimeError(odp["error"].get("message", "błąd MCP"))
        return odp["result"]

    def polacz(self):
        wynik = self._wyslij("initialize", {"protocolVersion": WERSJA_PROTOKOLU, "capabilities": {}, "clientInfo": KLIENT})
        self.instrukcje = wynik.get("instructions", "")
        self.naglowki["MCP-Protocol-Version"] = wynik.get("protocolVersion", WERSJA_PROTOKOLU)
        self._wyslij("notifications/initialized", powiadomienie=True)
        return wynik

    def narzedzia(self):
        return self._wyslij("tools/list")["tools"]

    def wywolaj(self, nazwa, argumenty):
        return self._wyslij("tools/call", {"name": nazwa, "arguments": argumenty})


class KlientMcpStdio(KlientMcpHttp):
    # WHAT: serwer MCP jako proces (np. "npx -y archflow-mcp@latest"); JSON-RPC po jednej linii.
    def __init__(self, polecenie, env=None, timeout=120):
        self.timeout, self._id, self.instrukcje = timeout, itertools.count(1), ""
        self.naglowki = {}  # WHY: wspólny polacz() zapisuje tu wersję protokołu; w stdio nieużywane
        self.proc = subprocess.Popen(polecenie, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env={**os.environ, **(env or {})},
                                     text=True, encoding="utf-8", bufsize=1)
        self._odpowiedzi = queue.Queue()
        threading.Thread(target=self._czytaj, daemon=True).start()

    def _czytaj(self):
        for linia in self.proc.stdout:
            if linia.strip():
                self._odpowiedzi.put(json.loads(linia))

    def _wyslij(self, metoda, params=None, powiadomienie=False):
        wiad = {"jsonrpc": "2.0", "method": metoda}
        if params is not None:
            wiad["params"] = params
        if not powiadomienie:
            wiad["id"] = next(self._id)
        self.proc.stdin.write(json.dumps(wiad, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if powiadomienie:
            return None
        while True:
            odp = self._odpowiedzi.get(timeout=self.timeout)
            if odp.get("id") == wiad["id"]:
                return self._wynik(odp)

    def zamknij(self):
        self.proc.terminate()


def narzedzia_dla_ollamy(narzedzia_mcp):
    # WHAT: lista tools/list z MCP -> format "tools" dla /api/chat Ollamy.
    return [{"type": "function", "function": {"name": n["name"], "description": n.get("description", ""),
             "parameters": n.get("inputSchema", {"type": "object", "properties": {}})}} for n in narzedzia_mcp]


def tekst_wyniku(wynik):
    # WHAT: wynik tools/call -> tekst dla modelu (błąd narzędzia też jako tekst - model może się poprawić).
    tekst = "\n".join(c.get("text", "") for c in wynik.get("content", []) if c.get("type") == "text")
    return f"Error: {tekst}" if wynik.get("isError") else tekst
~~~

Przykład użycia:

~~~python
klient = KlientMcpHttp("http://localhost:3000/mcp", token="...")
klient.polacz()                                   # klient.instrukcje -> wiadomość systemowa
tools = narzedzia_dla_ollamy(klient.narzedzia())  # -> dane["tools"] w /api/chat
wynik = klient.wywolaj("build_diagram", {"title": "Test", "nodes": [{"name": "Serwer", "icon": "server"}]})
print(tekst_wyniku(wynik))                        # -> {"role": "tool", "content": ...}
~~~
