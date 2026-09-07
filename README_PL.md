# Ollama Manager

Aplikacja desktopowa (PyQt6) pod KDE, która zaczynała jako prosty manager usługi
[Ollamy](https://ollama.com), a wyrosła w panel sterowania całym lokalnym stackiem AI:
zarządzanie modelami na wielu hostach naraz, agregator modeli (LiteLLM) wystawiający
jeden endpoint zgodny z API OpenAI dla narzędzi typu Continue/VS Code, oraz panel czatu
w przeglądarce (Open WebUI). Bez terminala, bez Dockera, bez chmury — wszystko działa
lokalnie, w Twoim LAN.

> **Status: stabilna (v0.5.0).** Używam jej na co dzień na własnym sprzęcie i działa
> zgodnie z oczekiwaniami. Nadal aktywnie rozwijana — regularnie dochodzą nowe opcje
> i funkcjonalności; [zgłoszenia błędów i uwagi](../../issues) są mile widziane.

![Ollama Manager — zakładka Usługi](om.png)

## Wymagania

- Python 3 + **PyQt6**, **requests**
- **systemd** + **polkit** (`pkexec`) — standard na KDE/Debian; `install.sh` proponuje instalację
  `pkexec` przez `apt`, jeśli go brakuje (potrzebne do każdej akcji wymagającej uprawnień admina)
- **curl** — używany przez własne przyciski instalacyjne appki (Ollama, oraz uv dla Open WebUI/LiteLLM);
  `install.sh` proponuje jego instalację przez `apt`, jeśli go brakuje
- Ollama (jeśli jej nie masz, aplikacja sama ją zainstaluje jednym przyciskiem)
- Opcjonalnie: **uv** (do instalacji Open WebUI i LiteLLM — aplikacja doinstaluje je sama w razie potrzeby)
- Opcjonalnie: `ffmpeg`, `pandoc`, `zstd` (pełna funkcjonalność Open WebUI — głos, dokumenty w RAG;
  aplikacja proponuje ich instalację przez `apt` zaraz po udanej instalacji Open WebUI)

## Instalacja i uruchomienie

Najpierw sklonuj repozytorium i wejdź do jego katalogu — poniższe skrypty
instalacyjne trzeba uruchamiać stamtąd (szukają `ollama_manager.py` obok siebie):

```
git clone https://github.com/cyryllo/Ollama-manager.git
cd Ollama-manager
```

**Jako aplikacja z menu, bez roota (zalecane)** — instaluje zależności przez pip,
kopiuje apkę do `~/.local/share/ollama-manager` i dodaje wpis w menu (sekcja
Narzędzia). Wykrywa istniejącą instalację i proponuje aktualizację/reinstalację:

```
./install.sh
```

Odinstalowanie: `./install.sh --uninstall` (komunikaty skryptu są po angielsku).

**Jako pakiet `.deb` (Debian/Ubuntu)** — zależności z `apt`, łatwe odinstalowanie:

```
./build-deb.sh
sudo apt install ./ollama-manager_*_all.deb
```

**Ręcznie, do dewelopowania** — bez kopiowania i wpisu w menu:

```
pip install PyQt6 requests
python3 ollama_manager.py
```

## Funkcje

**Usługa Ollama**
- Start / stop usługi systemd, wykrywanie stanu na bieżąco
- Autostart przy starcie systemu
- Wykrywanie braku instalacji + przycisk instalującej ją jednym kliknięciem
- Sprawdzanie aktualizacji na żądanie względem najnowszego wydania na GitHubie, z propozycją
  reinstalacji (oficjalny skrypt instalacyjny aktualizuje istniejącą instalację w miejscu),
  gdy masz starszą wersję — przydatne, bo niektóre modele wymagają nowszej Ollamy, żeby w ogóle się wczytać

**Modele**
- Tabela zainstalowanych modeli — nazwa, rozmiar na dysku i kolumna z szacowanym/realnym
  VRAM dla każdego — + usuwanie
- Pobieranie nowych modeli z podpowiedziami popularnych, pogrupowanymi wg zastosowania wprost
  w rozwijanej liście — ogólne/czat (Llama, Gemma, Mistral, Phi, Qwen3), kod (Qwen-Coder,
  DeepSeek-Coder-V2, Ornith), rozumowanie/agentowe (DeepSeek-R1, GPT-OSS), embeddingi do RAG
  (Nomic, Mixedbread, BGE-M3, EmbeddingGemma) i wizyjne (Qwen2.5-VL, Llama 3.2 Vision, Moondream)
  — z paskiem postępu
- Wybór rozmiaru i kwantyzacji (domyślna/Q8_0/pełna F16) przy pobieraniu — obie listy są
  dopasowane do wybranego modelu (a kwantyzacja dodatkowo do rozmiaru), zweryfikowane na
  ollama.com/library; dla modeli wymagających dodatkowego segmentu w tagu przy niedomyślnej
  kwantyzacji (np. "instruct"/"it") appka dokleja go sama — Q8_0/F16 w ogóle się pokazują
  tylko tam, gdzie potwierdzono, że działający tag istnieje
- Bieżące, orientacyjne wyliczenie zużycia pamięci (wagi + KV cache + bufory, plus zalecany RAM)
  przy wyborze rozmiaru/kwantyzacji
- Podgląd modeli aktualnie załadowanych do pamięci (VRAM)
- **Pobieranie modeli GGUF wprost z Hugging Face** — jedno pole, dwa sposoby użycia: wklej link
  do repo (albo `użytkownik/repo`), żeby od razu zobaczyć dostępne warianty kwantyzacji (z
  rozmiarami), albo po prostu wpisz nazwę modelu, żeby wyszukać pasujące repo na Hugging Face
  (posortowane wg liczby pobrań) i wybrać jedno z wyników. W obu przypadkach appka wylistuje
  warianty na podstawie listy plików w repo, więc nie trzeba znać dokładnego stringa kwantyzacji
  z góry. Pokazują się/działają tylko repo, które faktycznie mają w środku pliki `.gguf` —
  większość modeli na HF jest w surowym `safetensors`, tego Ollama nie pobierze bezpośrednio.
  **To opcjonalne, społecznościowe źródło** (w przeciwieństwie do oficjalnej biblioteki Ollamy
  wyżej) — appka nie weryfikuje zawartości repo, więc dopisek obok przycisku doradza trzymać się
  znanych autorów kwantyzacji (np. bartowski, mradermacher, unsloth)

**Open WebUI**
- Instalacja panelu czatu w przeglądarce jednym przyciskiem (bez Dockera)
- Start / stop, autostart po zalogowaniu
- Przycisk "Otwórz WebUI" otwiera panel w przeglądarce (bez automatycznego otwierania)
- Sprawdzanie aktualizacji na żądanie względem najnowszego wydania na PyPI, z propozycją
  aktualizacji przez `uv tool upgrade` (bez uprawnień administratora), gdy masz starszą wersję
- Po udanej instalacji appka proponuje doinstalowanie opcjonalnych pakietów `ffmpeg`/`pandoc`/`zstd`
  potrzebnych do transkrypcji głosu i importu dokumentów do RAG (jedyny krok wymagający roota)

**Przełącznik serwera**
- Wybór hosta Ollama (localhost albo dowolny w LAN, np. BC-250) dla operacji na modelach
- Dodawanie/usuwanie serwerów z poziomu okna, zapamiętywane między uruchomieniami

**Agregator modeli (LiteLLM)**
- Instalacja, start/stop i autostart LiteLLM jednym przyciskiem (bez Dockera)
- Wystawia jeden endpoint (zgodny z API OpenAI) łączący modele ze WSZYSTKICH
  serwerów z listy przełącznika — VS Code/Continue wskazuje tylko na ten adres
- Podgląd, jakie modele i hosty trafią do configu, przed uruchomieniem
- Generuje gotowy do wklejenia `config.yaml` dla Continue.dev na podstawie wystawionych modeli —
  appka nigdy sama nie zapisuje tego pliku, tylko podgląd/kopiowanie
- Sprawdzanie aktualizacji na żądanie względem najnowszego wydania na PyPI, z propozycją
  aktualizacji przez `uv tool upgrade` (bez uprawnień administratora), gdy masz starszą wersję

**Zaawansowane (zmienne środowiskowe Ollamy)**
- `OLLAMA_KEEP_ALIVE`, `OLLAMA_CONTEXT_LENGTH` (domyślne 4096 za mało do pracy agentowej),
  `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`,
  `OLLAMA_VULKAN` (backend Vulkan zamiast ROCm, przydatne na kartach AMD bez pełnego wsparcia ROCm
  np. BC-250), `OLLAMA_IGPU_ENABLE`, `OLLAMA_HOST` (nasłuch w sieci LAN zamiast tylko lokalnie),
  `GGML_VK_VISIBLE_DEVICES` (indeks urządzenia Vulkan — niezbędne na niektórych APU), `OLLAMA_GPU_OVERHEAD`
  (ile VRAM zarezerwować dla reszty systemu — wybór z gotowych profili pulpit/serwer)
- Jeden przycisk "Zapisz" stosuje wszystkie pola naraz i restartuje usługę tylko raz, plus
  przycisk "Zastosuj zalecane wartości", który wypełnia formularz sensownym profilem startowym
  do przejrzenia przed zapisem
- Wolne pole na dowolną inną zmienną środowiskową, spoza gotowego formularza
- Pełne opisy każdej zmiennej są w osobnej zakładce "Pomoc" — ta zakładka zostaje kompaktowa

**Pasek statystyk**
- Status Ollamy i Open WebUI
- Zużycie VRAM na aktualnie wybranym serwerze
- Liczba zainstalowanych modeli

**Dziennik zdarzeń** — log wszystkich operacji, zawsze widoczny na dole okna.

**Język interfejsu** — polski, angielski, niemiecki, hiszpański, francuski, portugalski i włoski, przełączany z poziomu okna, zapamiętywany między uruchomieniami.

## Uwagi

- Sterowanie usługą Ollama zawsze dotyczy lokalnej maszyny — nawet jeśli w oknie
  wybrany jest zdalny serwer (np. BC-250), start/stop/autostart działają lokalnie.
