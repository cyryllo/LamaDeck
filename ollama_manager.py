#!/usr/bin/env python3
# =============================================================================
#  Ollama Manager - menedżer Ollamy pod KDE (PyQt6)
#
#  WHAT: okno do zarządzania Ollamą:
#        - start / stop usługi systemd (przez pkexec = graficzny polkit KDE),
#        - autostart przy starcie systemu (systemctl enable/disable),
#        - lista zainstalowanych modeli + pobieranie i usuwanie.
#  WHY:  szablon do rozwoju w VCS. Logika sieci (OllamaClient) i sterowanie
#        usługą (funkcje serwisu) są odseparowane od GUI, żeby okno zostało
#        czyste. Dwa poziomy komentarzy: WHAT (skan) + WHY (decyzje).
#
#  Zależności:  PyQt6, requests   ->  pip install PyQt6 requests
#  Wymaga też:  systemd + polkit (pkexec) - standard na KDE/Debian.
#  Uruchomienie: python3 ollama_manager.py
# =============================================================================

import sys
import json
import html
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests  # WHY: czytelniejsze od urllib przy strumieniowaniu /api/pull

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSettings
from PyQt6.QtGui import QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QListWidget, QProgressBar, QTabWidget,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPlainTextEdit, QComboBox, QMessageBox, QCheckBox,
    QDialog, QLineEdit, QScrollArea,
)

# --- Konfiguracja ---------------------------------------------------------
# WHAT: wersja aplikacji - widoczna w tytule okna.
# WHY:  ostatnia cyfra rośnie przy każdym commicie; pierwsze dwie zmieniają się
#       tylko na wyraźne polecenie (patrz CLAUDE.md, sekcja "Wersjonowanie").
WERSJA = "0.5.12"

# WHAT: bazowy adres serwera Ollamy (operacje na modelach).
# WHY:  wydzielony na górę - możesz wskazać BC-250
#       (np. http://192.168.0.236:11434) zamiast localhost.
#       UWAGA: sterowanie USŁUGĄ (start/stop/autostart) dotyczy zawsze
#       LOKALNEGO systemd - nie da się zdalnie startować BC-250 tą drogą.
OLLAMA_URL = "http://localhost:11434"

# WHAT: nazwa usługi systemd. WHY: wydzielona, gdyby u Ciebie nazywała się inaczej.
SERVICE_NAME = "ollama"

# WHAT: adres panelu Open WebUI (domyślny port 'open-webui serve').
# WHY:  wydzielony na górę tak jak reszta adresów - gdybyś kiedyś zmienił port.
WEBUI_URL = "http://localhost:8080"

# WHAT: adres agregatora LiteLLM (domyślny port 'litellm --config ...').
# WHY:  jw. - jedno miejsce do zmiany, gdyby port kolidował z czymś innym.
LITELLM_URL = "http://localhost:4000"

# WHAT: profil startowy dla przycisku "Zastosuj zalecane wartości" w zakładce
#       Zaawansowane - wypełnia formularz, NIC nie zapisuje samo z siebie.
# WHY:  dobrane pod BC-250 (Vulkan+iGPU włączone, kontekst 16k, keep_alive 30m,
#       jeden model/zapytanie naraz, q8_0 dla mniejszego zużycia VRAM) - to
#       tylko punkt startu do przejrzenia i ewentualnej korekty przed "Zapisz",
#       appka nigdy nie wstrzykuje tych wartości automatycznie/po cichu.
OLLAMA_ZALECANE = {
    "OLLAMA_KEEP_ALIVE": "30m",
    "OLLAMA_CONTEXT_LENGTH": "16384",
    "OLLAMA_MAX_LOADED_MODELS": "1",
    "OLLAMA_NUM_PARALLEL": "1",
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_VULKAN": "1",
    "OLLAMA_IGPU_ENABLE": "1",
    "OLLAMA_HOST": "",
    "GGML_VK_VISIBLE_DEVICES": "0",
}

# WHAT: WSZYSTKIE zmienne, które mają dedykowane pole w formularzu zakładki
#       Zaawansowane - używane do odróżnienia "znanych" wpisów override.conf
#       od "własnych parametrów" (patrz _odswiez_zakladke_zaawansowane).
# WHY:  to NIE to samo co OLLAMA_ZALECANE.keys() - OLLAMA_GPU_OVERHEAD ma pole
#       w formularzu, ale świadomie NIE ma zalecanej wartości (zależy od tego,
#       czy na danym hoście jest środowisko graficzne, appka tego nie zgaduje).
OLLAMA_ZNANE_ZMIENNE = frozenset(OLLAMA_ZALECANE.keys()) | {"OLLAMA_GPU_OVERHEAD"}


# =============================================================================
#  Tłumaczenia interfejsu (i18n)
# =============================================================================
# WHAT: prosty słownik klucz -> tłumaczenie, jeden plik JSON na język w lang/.
# WHY:  polski tekst w kodzie jest jednocześnie kluczem słownika - brak wpisu
#       w wybranym języku po prostu pokazuje polski oryginał (nigdy pusto/crash).
#       Bez zależności od pyyaml/Qt Linguist (pylupdate6/lrelease) - dodanie
#       kolejnego języka to nowy plik lang/<kod>.json, bez dotykania kodu.
JEZYKI = {
    "pl": "polski", "en": "English", "de": "Deutsch", "es": "español",
    "fr": "français", "pt": "português", "it": "italiano",
}
# WHY: JEZYK_ZRODLOWY (nie JEZYK_DOMYSLNY!) to język, w którym NAPISANY jest
#      kod - polski tekst w wywołaniach _("...") sam jest kluczem słownika,
#      więc dla tego jednego języka nie ma (i nie może być) pliku lang/*.json.
#      JEZYK_DOMYSLNY to za to tylko to, co widzi nowy użytkownik przy
#      pierwszym uruchomieniu (bez zapisanego wyboru w QSettings) - "en", bo
#      program ma być uniwersalny, angielski jest tu najszerzej zrozumiały.
#      Te dwie stałe MUSZĄ być rozdzielone: gdyby JEZYK_DOMYSLNY == JEZYK_ZRODLOWY
#      == "en", `_wczytaj_jezyk("en")` czyściłby słownik do {} zamiast wczytać
#      lang/en.json - _() zwracałby wtedy surowy polski klucz zamiast tłumaczenia.
JEZYK_ZRODLOWY = "pl"
JEZYK_DOMYSLNY = "en"
_KATALOG_LANG = Path(__file__).resolve().parent / "lang"
_tlumaczenia = {}


def _wczytaj_jezyk(kod):
    # WHAT: ładuje słownik tłumaczeń danego języka do pamięci (moduł _()).
    global _tlumaczenia
    if kod == JEZYK_ZRODLOWY:
        _tlumaczenia = {}  # WHY: polski to sam kod źródłowy - nie potrzeba pliku
        return
    try:
        _tlumaczenia = json.loads((_KATALOG_LANG / f"{kod}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _tlumaczenia = {}


def _(tekst):
    # WHAT: zwraca tłumaczenie danego (polskiego) tekstu w aktualnym języku.
    # WHY:  brak wpisu w słowniku -> pokazujemy oryginał, więc niekompletne
    #       tłumaczenie nigdy nie wywala okna ani nie pokazuje pustego pola.
    return _tlumaczenia.get(tekst, tekst)


def _wczytaj_jezyk_zapisany():
    return _ustawienia().value("jezyk", JEZYK_DOMYSLNY)


def _zapisz_jezyk(kod):
    _ustawienia().setValue("jezyk", kod)


# =============================================================================
#  Serwery Ollama - lista do przełącznika w oknie (localhost / hosty w LAN)
# =============================================================================
# WHAT: edytowalna lista adresów, między którymi można przełączać się z okna,
#       zamiast raz na zawsze edytować stałą OLLAMA_URL.
# WHY:  jedyny realny sposób na "wiele instancji Ollamy" to zwykłe wskazanie
#       klientowi innego adresu API (patrz CLAUDE.md, sekcja "Modele zdalne") -
#       dyrektywy Modelfile typu REMOTE_HOST nie istnieją. Przełącznik dotyczy
#       TYLKO operacji na modelach (lista/pobierz/usuń/VRAM) - sterowanie
#       usługą systemd zawsze zostaje lokalne, więc nie jest tu uwzględnione.
DOMYSLNY_SERWER = {"nazwa": "Lokalny", "adres": OLLAMA_URL}


def _ustawienia():
    # WHAT: jeden wspólny obiekt QSettings (plik INI w ~/.config).
    return QSettings("OllamaManager", "OllamaManager")


def _wczytaj_serwery():
    # WHAT: lista serwerów zapisana jako JSON w ustawieniach.
    # WHY:  QSettings nie zna list słowników wprost - zapisujemy jako tekst.
    surowe = _ustawienia().value("serwery/lista", "")
    if surowe:
        try:
            lista = json.loads(surowe)
            if lista:
                return lista
        except (json.JSONDecodeError, TypeError):
            pass
    return [dict(DOMYSLNY_SERWER)]


def _zapisz_serwery(lista):
    _ustawienia().setValue("serwery/lista", json.dumps(lista))


def _wczytaj_serwer_aktywny(serwery):
    # WHY: jeśli zapisany adres zniknął z listy (usunięty ręcznie), wracamy na pierwszy.
    adres = _ustawienia().value("serwery/aktywny", "")
    return adres if adres in [s["adres"] for s in serwery] else serwery[0]["adres"]


def _zapisz_serwer_aktywny(adres):
    _ustawienia().setValue("serwery/aktywny", adres)


# =============================================================================
#  Sterowanie usługą systemd
# =============================================================================
def _systemctl_query(arg):
    # WHAT: nieuprzywilejowane pytanie o stan usługi (is-active / is-enabled).
    # WHY:  zapytania o stan NIE wymagają roota; wynik jest na stdout nawet
    #       gdy kod wyjścia jest niezerowy (np. "inactive"), więc czytamy stdout.
    try:
        r = subprocess.run(
            ["systemctl", arg, SERVICE_NAME],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def _pkexec(args, wejscie=None):
    # WHAT: uruchamia polecenie jako root przez polkit.
    # WHY:  pkexec pokazuje graficzny dialog KDE z prośbą o hasło - nie potrzeba
    #       terminala ani sudo. Rzucamy wyjątek przy błędzie, żeby worker go złapał.
    #       Opcjonalne 'wejscie' idzie na STDIN procesu (np. treść pliku do
    #       zapisania) - bezpieczniejsze niż wstrzykiwanie tekstu do argumentów
    #       powłoki, bo w ogóle nie dotyka parsera 'sh'.
    r = subprocess.run(["pkexec"] + args, input=wejscie, capture_output=True, text=True)
    if r.returncode != 0:
        # WHY: kod 126 = użytkownik anulował/brak uprawnień, 127 = błąd autoryzacji.
        raise RuntimeError(r.stderr.strip() or _("pkexec: kod wyjścia {kod}").format(kod=r.returncode))


def usluga_start():
    _pkexec(["systemctl", "start", SERVICE_NAME])


def usluga_stop():
    _pkexec(["systemctl", "stop", SERVICE_NAME])


def usluga_autostart(wlacz):
    # WHAT: włącza lub wyłącza automatyczny start po restarcie systemu.
    akcja = "enable" if wlacz else "disable"
    _pkexec(["systemctl", akcja, SERVICE_NAME])


def _usluga_override_sciezka():
    return Path("/etc/systemd/system") / f"{SERVICE_NAME}.service.d" / "override.conf"


def _usluga_env_wszystkie():
    # WHAT: czyta WSZYSTKIE zmienne środowiskowe zapisane w override.conf.
    # WHY:  sam ODCZYT pliku w /etc nie wymaga roota - tylko jego ZMIANA (patrz
    #       usluga_zapisz_env). BUG naprawiony: poprzednia wersja zakładała,
    #       że plik ZAWSZE ma dokładnie nasz własny format (jedna zmienna na
    #       linię, w cudzysłowach) - ale systemd.exec(5) dopuszcza też WIELE par
    #       KEY=VALUE oddzielonych spacją w jednej linii Environment=, a cudzysłów
    #       może być pojedynczy ('...') albo brak go wcale. Jeśli plik był kiedyś
    #       ręcznie edytowany (np. `sudo systemctl edit ollama` z jakiegoś
    #       poradnika o Vulkanie), stary parser po cichu gubił zmienne (wchłaniał
    #       kolejne pary w wartość poprzedniej) albo dostawał klucz z doklejonym
    #       apostrofem (`'OLLAMA_VULKAN`), który nigdy nie pasował do
    #       "OLLAMA_VULKAN" - checkbox pokazywał zły stan, a "Zastosuj" dopisywał
    #       nowy, poprawny wpis OBOK osieroconego śmiecia zamiast go nadpisać.
    #       shlex.split() parsuje to tak samo, jak zrobiłby to systemd.
    try:
        tresc = _usluga_override_sciezka().read_text()
    except OSError:
        return {}
    zmienne = {}
    for linia in tresc.splitlines():
        linia = linia.strip()
        if not linia.startswith("Environment="):
            continue
        try:
            czesci = shlex.split(linia.removeprefix("Environment="))
        except ValueError:
            continue  # WHY: niesparsowalna linia (np. niedomknięty cudzysłów) - pomijamy
        for czesc in czesci:
            if "=" in czesc:
                klucz, wartosc = czesc.split("=", 1)
                zmienne[klucz] = wartosc
    return zmienne


def usluga_zapisz_env(zmienne):
    # WHAT: nadpisuje CAŁY override.conf zadanym słownikiem zmiennych (jeden
    #       zapis, jedna zmiana pliku) i restartuje usługę.
    # WHY:  zakładka "Zaawansowane" ma jeden przycisk "Zapisz" dla wszystkich
    #       9 zmiennych + własnych parametrów naraz, zamiast osobnego zapisu i
    #       restartu na każde pole (jak w starszych wersjach) - wywołujący
    #       (zapisz_zaawansowane w MainWindow) sam składa kompletny słownik
    #       "co ma się finalnie znaleźć w pliku"; ta funkcja niczego nie scala
    #       z poprzednią zawartością - zrobiła to już strona wywołująca. Gotowa
    #       treść pliku leci na STDIN procesu roota, nie jako argument powłoki -
    #       żaden wpisany tekst nie ma szansy dotknąć parsera 'sh', bo w ogóle
    #       nie trafia do wiersza poleceń.
    tresc = "[Service]\n" + "".join(f'Environment="{k}={v}"\n' for k, v in zmienne.items())
    skrypt = (
        'mkdir -p "$(dirname "$1")" && cat > "$1" && '
        "systemctl daemon-reload && "
        f"systemctl restart {SERVICE_NAME}"
    )
    _pkexec(["sh", "-c", skrypt, "sh", str(_usluga_override_sciezka())], wejscie=tresc)


def ollama_zainstalowana():
    # WHAT: sprawdza, czy binarka 'ollama' w ogóle jest w systemie (PATH).
    # WHY:  usługa systemd może po prostu nie istnieć, gdy Ollama nie jest
    #       zainstalowana - to sprawdzenie działa niezależnie od stanu usługi
    #       i zawsze dotyczy LOKALNEJ maszyny (tak jak reszta sterowania usługą).
    return shutil.which("ollama") is not None


def ollama_zainstaluj():
    # WHAT: pobiera i uruchamia oficjalny skrypt instalacyjny Ollamy z ollama.com.
    # WHY:  robimy to przez pkexec (graficzny polkit), nie przez sudo w terminalu -
    #       skrypt instalacyjny sam wykrywa, że działa jako root, i pomija własne
    #       wywołanie sudo, więc nie pojawi się drugi, tekstowy prompt o hasło.
    # UWAGA: adres to oficjalna, udokumentowana metoda instalacji z ollama.com/download -
    #        warto od czasu do czasu sprawdzić, czy się nie zmieniła.
    _pkexec(["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])


def ollama_wersja_lokalna():
    # WHAT: odczytuje wersję zainstalowanej lokalnie Ollamy z 'ollama --version'.
    # WHY:  binarka drukuje np. "ollama version is 0.3.12" (a gdy usługa akurat
    #       nie działa, dorzuca ostrzeżenie na stderr) - wyciągamy sam numer
    #       wyrażeniem regularnym zamiast zależeć od dokładnego formatu tekstu,
    #       który już się zmieniał między wersjami Ollamy.
    try:
        r = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    dopasowanie = re.search(r"(\d+\.\d+\.\d+)", r.stdout + r.stderr)
    return dopasowanie.group(1) if dopasowanie else None


def ollama_wersja_najnowsza():
    # WHAT: pyta GitHub o tag najnowszego wydania Ollamy. Rzuca wyjątek przy
    #       błędzie sieci - łapie go wywołujący (SprawdzAktualizacjeWorker).
    # WHY:  oficjalne repo projektu - nie ma osobnego, prostszego API do
    #       "jaka jest najnowsza wersja"; ten sam adres, co linkują strony
    #       release notes na ollama.com/download.
    r = requests.get(
        "https://api.github.com/repos/ollama/ollama/releases/latest",
        timeout=5, headers={"Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()
    tag = r.json().get("tag_name", "")
    return tag.lstrip("v") or None


def _rozbij_wersje(tekst):
    # WHAT: rozbija string wersji na krotkę liczb do porównania, np. "0.3.12" -> (0, 3, 12).
    # WHY:  proste porównanie tekstowe źle zestawiłoby np. "0.3.9" i "0.3.10"
    #       (ten sam problem co w install.sh _compare_versions, tu rozwiązany
    #       w Pythonie zamiast 'sort -V'). Zatrzymuje się na pierwszym
    #       nienumerycznym segmencie (np. "-rc1"), żeby nie wywalić wyjątku.
    wynik = []
    for czesc in re.split(r"[.\-]", tekst.lstrip("v")):
        if not czesc.isdigit():
            break
        wynik.append(int(czesc))
    return tuple(wynik)


# =============================================================================
#  Open WebUI - panel czatu w przeglądarce (patrz CLAUDE.md, sekcja roadmapy)
# =============================================================================
def webui_binarka():
    # WHAT: szuka binarki 'open-webui' - w PATH albo pod domyślną ścieżką
    #       'pip install --user' (~/.local/bin).
    # WHY:  aplikacja odpalona ze skrótu na pulpicie może mieć okrojony PATH
    #       bez ~/.local/bin, mimo że pakiet jest już zainstalowany.
    znaleziona = shutil.which("open-webui")
    if znaleziona:
        return znaleziona
    kandydat = Path.home() / ".local" / "bin" / "open-webui"
    return str(kandydat) if kandydat.exists() else None


def webui_zainstalowane():
    return webui_binarka() is not None


def _uv_binarka():
    # WHAT: jak webui_binarka(), tylko dla narzędzia 'uv'.
    znaleziona = shutil.which("uv")
    if znaleziona:
        return znaleziona
    kandydat = Path.home() / ".local" / "bin" / "uv"
    return str(kandydat) if kandydat.exists() else None


def _uv_zapewnij():
    # WHAT: zwraca ścieżkę do 'uv', instalując je w razie potrzeby przez oficjalny
    #       standalone installer Astral (curl | sh), NIE przez 'pip install --user'.
    # WHY:  Debian trixie (i każdy Python 3.11+ zgodny z PEP 668) oznacza systemowego
    #       python3 jako "externally-managed" - 'pip install --user uv' na sys.executable
    #       wywala się błędem "externally-managed-environment", zanim w ogóle dojdzie
    #       do instalacji uv/LiteLLM/WebUI. Installer Astral pobiera gotową binarkę
    #       uv wprost do ~/.local/bin i w ogóle nie dotyka pip ani systemowego
    #       Pythona, więc PEP 668 nie ma tu zastosowania - ten sam wzorzec (curl | sh),
    #       co już używany do instalacji samej Ollamy (patrz ollama_zainstaluj()).
    #       Wspólny helper zamiast duplikowania bootstrapu w webui_zainstaluj()
    #       i litellm_zainstaluj().
    uv = _uv_binarka()
    if uv:
        return uv

    wynik = subprocess.run(
        ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        capture_output=True, text=True, timeout=None,
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("instalacja uv: nieznany błąd"))

    # WHY: installer dopisuje ~/.local/bin do PATH w plikach powłoki (.bashrc itp.),
    #      ale ten proces (odpalony ze skrótu .desktop) już wystartował ze starym
    #      PATH i tego nie przeczyta - stąd _uv_binarka() sprawdza katalog wprost,
    #      niezależnie od PATH.
    uv = _uv_binarka()
    if not uv:
        raise RuntimeError(_("Zainstalowano 'uv', ale nie widać go w ~/.local/bin."))
    return uv


def webui_zainstaluj():
    # WHAT: instaluje Open WebUI przez 'uv tool install', z Pythonem przypiętym na 3.11.
    # WHY:  Open WebUI (stan na 2026) NIE wspiera jeszcze Pythona 3.13, a to on jest
    #       domyślnym 'python3' na świeżym Debianie/KDE - zwykłe 'pip install' na
    #       systemowym interpreterze kończy się błędem "no matching distribution".
    #       'uv' sam dociąga i zarządza kompatybilnym Pythonem 3.11 (bez apt/roota/
    #       Dockera) i instaluje open-webui w odizolowanym środowisku, wystawiając
    #       binarkę w ~/.local/bin - dokładnie tam, gdzie szuka jej webui_binarka().
    uv = _uv_zapewnij()

    wynik = subprocess.run(
        [uv, "tool", "install", "--python", "3.11", "open-webui"],
        capture_output=True, text=True, timeout=None,  # WHY: pobranie Pythona 3.11 + zależności - może to potrwać
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("uv tool install: nieznany błąd"))


def webui_wersja_lokalna():
    # WHAT: czyta zainstalowaną wersję Open WebUI z 'uv tool list'.
    # WHY:  binarka 'open-webui' nie ma flagi --version - ale 'uv', który ją
    #       zainstalował (patrz webui_zainstaluj), zna dokładną wersję w swoim
    #       środowisku narzędzia i wypisuje ją jako "open-webui vX.Y.Z".
    uv = _uv_binarka()
    if not uv:
        return None
    try:
        r = subprocess.run([uv, "tool", "list"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    dopasowanie = re.search(r"^open-webui\s+v?(\d+\.\d+\.\d+)", r.stdout, re.MULTILINE)
    return dopasowanie.group(1) if dopasowanie else None


def webui_wersja_najnowsza():
    # WHAT: pyta PyPI o najnowszą opublikowaną wersję pakietu 'open-webui'. Rzuca
    #       wyjątek przy błędzie sieci - łapie go wywołujący (SprawdzAktualizacjeWorker).
    # WHY:  'uv tool install'/'uv tool upgrade' i tak instalują z PyPI - to samo
    #       źródło prawdy, którego użyje sama instalacja, nie osobne repo GitHub.
    r = requests.get("https://pypi.org/pypi/open-webui/json", timeout=5)
    r.raise_for_status()
    return r.json().get("info", {}).get("version") or None


def webui_aktualizuj():
    # WHAT: aktualizuje Open WebUI do najnowszej wersji przez 'uv tool upgrade'.
    # WHY:  'uv tool upgrade' pamięta oryginalne opcje instalacji (m.in. przypięty
    #       Python 3.11 z webui_zainstaluj) i reinstaluje w tym samym środowisku
    #       narzędzia - nie trzeba duplikować 'uv tool install --python 3.11' tutaj.
    #       Bez pkexec - w przeciwieństwie do Ollamy, to zwykły proces użytkownika.
    uv = _uv_zapewnij()
    wynik = subprocess.run(
        [uv, "tool", "upgrade", "open-webui"],
        capture_output=True, text=True, timeout=None,
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("uv tool upgrade: nieznany błąd"))


def webui_zainstaluj_opcjonalne():
    # WHAT: instaluje pakiety systemowe potrzebne do PEŁNEJ funkcjonalności WebUI
    #       (transkrypcja audio, import PDF/Word do RAG) - ffmpeg, pandoc, zstd.
    # WHY:  to jedyny krok instalacji WebUI wymagający roota - reszta (webui_zainstaluj,
    #       'uv tool install') celowo działa bez uprawnień administratora. Robimy to
    #       przez ten sam pkexec (dialog polkit KDE) co start/stop usługi Ollama, ale
    #       TYLKO na wyraźne życzenie użytkownika (patrz klik_webui) - te pakiety są
    #       opcjonalne, WebUI działa i bez nich, tylko bez tych dwóch funkcji.
    _pkexec(["apt-get", "install", "-y", "ffmpeg", "pandoc", "zstd"])


def _webui_dziala():
    try:
        r = requests.get(WEBUI_URL, timeout=1)
        return r.status_code < 500
    except requests.RequestException:
        return False


def webui_uruchom():
    # WHAT: jeśli WebUI już odpowiada - nic nie rób. Jeśli nie - zapisz/odśwież
    #       jego usługę systemd --user i ją wystartuj, potem poczekaj, aż odpowie.
    # WHY:  ta sama usługa --user co przy autostarcie (_zapisz_webui_unit) - jedna
    #       spójna droga uruchamiania, dzięki czemu "Zatrzymaj" (webui_zatrzymaj)
    #       zawsze działa, niezależnie od tego, czy WebUI wystartowało z tego
    #       przycisku, czy z włączonego wcześniej autostartu.
    if _webui_dziala():
        return
    _zapisz_webui_unit()
    _systemctl_user(["start", "open-webui.service"])

    # WHY: 3 minuty zamiast 30 s - pierwsze uruchomienie robi migracje bazy
    #      i potrafi ściągnąć domyślny model embeddingowy do RAG.
    for _ in range(180):
        if _webui_dziala():
            return
        time.sleep(1)

    raise RuntimeError(
        _("WebUI nie odpowiedziało w ciągu 3 minut. Log usługi: journalctl --user -u open-webui -e")
    )


def webui_zatrzymaj():
    # WHAT: zatrzymuje usługę systemd --user Open WebUI. Jeśli systemd nie zna
    #       takiej usługi ("not loaded" - WebUI działa jako goły proces, np.
    #       uruchomiony ręcznie albo starszą wersją tej aplikacji sprzed
    #       przejścia na systemd), dobija proces bezpośrednio po nazwie polecenia.
    # WHY:  bez tego fallbacku przycisk "Zatrzymaj" nic by nie robił w takim przypadku.
    try:
        _systemctl_user(["stop", "open-webui.service"])
        return
    except RuntimeError as e:
        if "not loaded" not in str(e) and "not found" not in str(e):
            raise

    binarka = webui_binarka()
    wzorzec = f"{binarka} serve" if binarka else "open-webui serve"
    wynik = subprocess.run(["pkill", "-f", wzorzec], capture_output=True, text=True, timeout=5)
    if wynik.returncode not in (0, 1):  # WHY: 1 = pkill nie znalazł procesu - i tak już zatrzymane
        raise RuntimeError(wynik.stderr.strip() or _("pkill: nie udało się zatrzymać procesu WebUI"))


def _webui_service_sciezka():
    return Path.home() / ".config" / "systemd" / "user" / "open-webui.service"


def _systemctl_user(args):
    # WHAT: jak _pkexec, tylko dla 'systemctl --user' - podnosi wyjątek z treścią
    #       stderr przy błędzie.
    # WHY:  usługi --user działają w sesji użytkownika, więc NIE wymagają roota
    #       ani pkexec - w przeciwieństwie do systemowej usługi Ollamy.
    r = subprocess.run(
        ["systemctl", "--user"] + args, capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(
            r.stderr.strip()
            or _("systemctl --user {polecenie}: kod wyjścia {kod}").format(polecenie=" ".join(args), kod=r.returncode)
        )


def webui_autostart_wlaczony():
    # WHAT: sprawdza, czy usługa --user open-webui jest włączona (autostart po zalogowaniu).
    # WHY:  nieuprzywilejowane zapytanie, jak _systemctl_query, tylko w trybie --user.
    r = subprocess.run(
        ["systemctl", "--user", "is-enabled", "open-webui.service"],
        capture_output=True, text=True, timeout=3,
    )
    return r.stdout.strip() == "enabled"


def _zapisz_webui_unit():
    # WHAT: (re)zapisuje plik usługi systemd --user dla Open WebUI i przeładowuje systemd.
    # WHY:  wspólne dla ręcznego uruchamiania i włączania autostartu - jedna prawda
    #       o tym, jak wygląda usługa (ExecStart, zmienne środowiskowe), zamiast
    #       dwóch osobnych dróg startowania, które trzeba by osobno zatrzymywać.
    binarka = webui_binarka()
    if not binarka:
        raise RuntimeError(_("Open WebUI nie jest zainstalowane."))
    tresc = (
        "[Unit]\n"
        "Description=Open WebUI\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={binarka} serve\n"
        f"Environment=OLLAMA_BASE_URL={OLLAMA_URL}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    sciezka = _webui_service_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(tresc)
    _systemctl_user(["daemon-reload"])


def webui_autostart(wlacz):
    # WHAT: włącza/wyłącza autostart Open WebUI po zalogowaniu przez usługę systemd --user.
    # WHY:  to usługa UŻYTKOWNIKA (nie systemowa jak Ollama) - open-webui i tak żyje
    #       w katalogu domowym (~/.local/bin, dane w ~/.local/share), więc nie ma
    #       powodu prosić o roota. 'enable --now'/'disable --now' od razu też
    #       startuje/zatrzymuje serwer, więc nie trzeba osobno klikać "Uruchom".
    if wlacz:
        _zapisz_webui_unit()
        _systemctl_user(["enable", "--now", "open-webui.service"])
    else:
        _systemctl_user(["disable", "--now", "open-webui.service"])


# =============================================================================
#  LiteLLM - agregator wielu serwerów Ollamy pod jednym API (patrz CLAUDE.md,
#  sekcja roadmapy "Proxy dla modeli zdalnych przez LiteLLM")
# =============================================================================
def litellm_binarka():
    # WHAT: jak webui_binarka() - szuka binarki 'litellm' w PATH albo ~/.local/bin.
    znaleziona = shutil.which("litellm")
    if znaleziona:
        return znaleziona
    kandydat = Path.home() / ".local" / "bin" / "litellm"
    return str(kandydat) if kandydat.exists() else None


def litellm_zainstalowane():
    return litellm_binarka() is not None


def litellm_zainstaluj():
    # WHAT: instaluje LiteLLM (z obsługą proxy) przez 'uv tool install'.
    # WHY:  ten sam wzorzec co Open WebUI (bez Dockera/roota), ale prościej -
    #       LiteLLM (stan na 2026) wspiera Pythona 3.10-3.13, więc w
    #       przeciwieństwie do Open WebUI nie trzeba pinować konkretnej wersji.
    uv = _uv_zapewnij()

    wynik = subprocess.run(
        [uv, "tool", "install", "litellm[proxy]"],
        capture_output=True, text=True, timeout=None,
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("uv tool install: nieznany błąd"))


def litellm_wersja_lokalna():
    # WHAT: czyta zainstalowaną wersję LiteLLM z 'uv tool list' - jak
    #       webui_wersja_lokalna(). WHY: instalacja szła jako 'litellm[proxy]'
    #       (extra), ale 'uv tool list' i tak rejestruje narzędzie pod nazwą
    #       samego pakietu ("litellm vX.Y.Z"), bez dopisku ekstrasa.
    uv = _uv_binarka()
    if not uv:
        return None
    try:
        r = subprocess.run([uv, "tool", "list"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    dopasowanie = re.search(r"^litellm\s+v?(\d+\.\d+\.\d+)", r.stdout, re.MULTILINE)
    return dopasowanie.group(1) if dopasowanie else None


def litellm_wersja_najnowsza():
    # WHAT: pyta PyPI o najnowszą opublikowaną wersję pakietu 'litellm'. Rzuca
    #       wyjątek przy błędzie sieci - łapie go wywołujący (SprawdzAktualizacjeWorker).
    r = requests.get("https://pypi.org/pypi/litellm/json", timeout=5)
    r.raise_for_status()
    return r.json().get("info", {}).get("version") or None


def litellm_aktualizuj():
    # WHAT: aktualizuje LiteLLM do najnowszej wersji przez 'uv tool upgrade'.
    # WHY:  'uv tool upgrade' pamięta oryginalne opcje instalacji (m.in. extra
    #       '[proxy]' z litellm_zainstaluj) i reinstaluje w tym samym środowisku
    #       narzędzia. Bez pkexec - tak jak Open WebUI, to zwykły proces użytkownika.
    uv = _uv_zapewnij()
    wynik = subprocess.run(
        [uv, "tool", "upgrade", "litellm"],
        capture_output=True, text=True, timeout=None,
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("uv tool upgrade: nieznany błąd"))


def _litellm_dziala():
    try:
        r = requests.get(f"{LITELLM_URL}/health/liveliness", timeout=1)
        return r.status_code < 500
    except requests.RequestException:
        return False


def _litellm_config_sciezka():
    return Path.home() / ".config" / "ollama-manager" / "litellm_config.yaml"


def _wykryj_modele_na_serwerach(serwery):
    # WHAT: dla każdego serwera z listy (TA SAMA lista co przełącznik na pasku)
    #       pyta /api/tags o zainstalowane modele.
    # WHY:  jedno źródło prawdy o hostach - żeby dodać model do agregatora,
    #       wystarczy mieć host na liście serwerów, bez osobnej listy do
    #       ręcznego utrzymywania w dwóch miejscach naraz.
    wpisy = []
    for s in serwery:
        adres = s["adres"].rstrip("/")
        try:
            r = requests.get(f"{adres}/api/tags", timeout=3)
            r.raise_for_status()
            modele = [m["name"] for m in r.json().get("models", [])]
        except requests.RequestException:
            continue  # WHY: host akurat nieosiągalny - pomijamy, nie wywalamy całości
        for model in modele:
            wpisy.append((s["nazwa"], model, adres))
    return wpisy


def _zbuduj_config_litellm(serwery):
    # WHAT: generuje treść config.yaml dla LiteLLM - jeden wpis w model_list
    #       na każdy model na każdym hoście.
    # WHY:  format jest prosty (lista słowników o 3 polach tekstowych), więc
    #       piszemy YAML ręcznie zamiast dociągać zależność 'pyyaml'.
    def _yaml_str(tekst):
        return '"' + tekst.replace("\\", "\\\\").replace('"', '\\"') + '"'

    wpisy = _wykryj_modele_na_serwerach(serwery)
    if not wpisy:
        return "model_list: []\n"

    linie = ["model_list:"]
    for _nazwa_hosta, model, adres in wpisy:
        linie.append(f"  - model_name: {_yaml_str(model)}")
        linie.append("    litellm_params:")
        linie.append(f"      model: {_yaml_str('ollama_chat/' + model)}")
        linie.append(f"      api_base: {_yaml_str(adres)}")
    return "\n".join(linie) + "\n"


def litellm_zapisz_config(serwery):
    sciezka = _litellm_config_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(_zbuduj_config_litellm(serwery))


def _zbuduj_config_continue(modele):
    # WHAT: generuje treść config.yaml dla Continue.dev (VS Code) - jeden wpis
    #       w 'models' na każdy UNIKALNY model wykryty w agregatorze LiteLLM.
    # WHY:  Continue rozmawia tylko z LiteLLM (jeden endpoint zgodny z OpenAI),
    #       więc nie musi wiedzieć, na jakim hoście faktycznie liczy się model -
    #       liczy się tylko jego nazwa, identyczna z tą, którą LiteLLM wystawia
    #       pod /v1/models (stąd 'modele' to lista nazw, nie krotki host+model
    #       jak w _zbuduj_config_litellm). ŻADNYCH kotwic YAML (&anchor /
    #       <<: *anchor), każdy model rozpisany osobno - Continue parsuje YAML
    #       1.2, gdzie merge keys bez jawnego nagłówka "%YAML 1.1" po cichu się
    #       nie stosują i model znika z selektora klienta (potwierdzone w
    #       praktyce - patrz pamięć/notatki z testów BC-250 + Continue).
    if not modele:
        return "models: []\n"

    def _yaml_str(tekst):
        return '"' + tekst.replace("\\", "\\\\").replace('"', '\\"') + '"'

    linie = ["name: Local LiteLLM", "version: 1.0.0", "schema: v1", "", "models:"]
    for model in modele:
        linie.append(f"  - name: {_yaml_str(model)}")
        linie.append("    provider: openai")
        linie.append(f"    model: {_yaml_str(model)}")
        linie.append(f"    apiBase: {LITELLM_URL}/v1")
        linie.append("    apiKey: sk-anything")
        linie.append("    roles:")
        linie.append("      - chat")
        linie.append("      - edit")
        linie.append("      - apply")
    return "\n".join(linie) + "\n"
    return sciezka


def _litellm_service_sciezka():
    return Path.home() / ".config" / "systemd" / "user" / "litellm.service"


def _zapisz_litellm_unit():
    # WHAT: (re)zapisuje plik usługi systemd --user dla LiteLLM i przeładowuje systemd.
    # WHY:  ten sam wzorzec co Open WebUI - jedna droga uruchamiania, żeby
    #       "Zatrzymaj" działało niezależnie od tego, skąd LiteLLM wystartował.
    binarka = litellm_binarka()
    if not binarka:
        raise RuntimeError(_("LiteLLM nie jest zainstalowane."))
    sciezka_config = _litellm_config_sciezka()
    tresc = (
        "[Unit]\n"
        "Description=LiteLLM - agregator modeli Ollama\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={binarka} --config {sciezka_config}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    sciezka = _litellm_service_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(tresc)
    _systemctl_user(["daemon-reload"])


def litellm_uruchom(serwery):
    # WHAT: generuje świeży config z aktualnej listy serwerów, (re)startuje
    #       usługę systemd --user i czeka, aż API zacznie odpowiadać.
    # WHY:  config generujemy na nowo przy KAŻDYM starcie - lista serwerów
    #       (i modeli na nich) mogła się zmienić od ostatniego uruchomienia.
    litellm_zapisz_config(serwery)
    _zapisz_litellm_unit()
    _systemctl_user(["restart", "litellm.service"])

    for _ in range(60):
        if _litellm_dziala():
            return
        time.sleep(1)

    raise RuntimeError(
        _("LiteLLM nie odpowiedziało w ciągu 60 s. Log usługi: journalctl --user -u litellm -e")
    )


def litellm_zatrzymaj():
    _systemctl_user(["stop", "litellm.service"])


def litellm_autostart_wlaczony():
    r = subprocess.run(
        ["systemctl", "--user", "is-enabled", "litellm.service"],
        capture_output=True, text=True, timeout=3,
    )
    return r.stdout.strip() == "enabled"


def litellm_autostart(wlacz, serwery):
    # WHY: 'enable --now' potrzebuje świeżego configu i unitu - tak samo jak
    #      ręczne uruchomienie, żeby autostart nie odpalił się na nieaktualnej liście.
    if wlacz:
        litellm_zapisz_config(serwery)
        _zapisz_litellm_unit()
        _systemctl_user(["enable", "--now", "litellm.service"])
    else:
        _systemctl_user(["disable", "--now", "litellm.service"])


# =============================================================================
#  Wyszukiwanie i pobieranie modeli - JEDNA wyszukiwarka dla dwóch źródeł:
#  oficjalnej biblioteki Ollamy (ollama.com) i Hugging Face (tylko GGUF - reszta
#  HF jest w surowym safetensors, tego Ollama nie konwertuje w locie; Ollama
#  pobiera GGUF z HF przez 'hf.co/<user>/<repo>:<kwantyzacja>').
#  Te funkcje tylko ZNAJDUJĄ modele/warianty i dają gotową nazwę do pobrania -
#  samo pobieranie idzie istniejącym pull_stream()/PullWorker (patrz
#  MainWindow._rozpocznij_pobieranie), zero duplikacji paska postępu/logu.
# =============================================================================

# WHAT: znane PRZEDROSTKI rodzin kwantyzacji GGUF (nazewnictwo llama.cpp/ggml)
#       - regex niżej dopasowuje przedrostek + WSZYSTKO, co po nim bezpośrednio
#       następuje w tym samym "słowie" (cyfry/litery/podkreślniki), więc cały
#       token typu "Q4_K_M", "Q3_K_XL" czy "Q4_0_4_4" (ARM repack) łapie się
#       w całości jako JEDNA kwantyzacja - nie tylko sam przedrostek.
# BUG naprawiony: poprzednia wersja miała sztywną listę PEŁNYCH etykiet
#       (np. "Q4_0", "Q3_K") z granicą dopuszczającą "_" jako koniec słowa -
#       więc np. plik "...-Q4_0_4_4.gguf" (osobny wariant repackowany pod ARM,
#       NIE to samo co zwykłe "Q4_0") dopasowywał się jako zwykłe "Q4_0" i jego
#       rozmiar sumował się z prawdziwym plikiem Q4_0.gguf - w oknie wychodziła
#       jedna pozycja "Q4_0" z zawyżonym, bezsensownym rozmiarem (odkryte na
#       żywych danych bartowski/Qwen2.5-Coder-7B-Instruct-GGUF: Q4_0 pokazywało
#       16,5 GB zamiast prawdziwych ~4,1 GB, bo doliczało 3 różne pliki
#       "_4_4"/"_4_8"/"_8_8"). To samo dotyczyło "_L"/"_XL" (np. Q3_K_XL,
#       Q4_K_L, Q6_K_L) - żadnego z tych sufiksów nie było na sztywnej liście,
#       więc padały na krótszy wpis z listy (np. "Q3_K") zamiast zostać własną,
#       osobną pozycją.
_GGUF_KWANTYZACJA_PREFIKSY = [
    "IQ1", "IQ2", "IQ3", "IQ4",
    "TQ1", "TQ2",
    "Q2", "Q3", "Q4", "Q5", "Q6", "Q8",
    "BF16", "FP16", "F16", "FP32", "F32",
]
_GGUF_KWANTYZACJA_WZORZEC = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(_GGUF_KWANTYZACJA_PREFIKSY) + r")[A-Za-z0-9_]*",
    re.IGNORECASE,
)


def _hf_repo_z_linku(tekst):
    # WHAT: wyciąga "użytkownik/repo" z dowolnego wejścia - pełnego linku do
    #       Hugging Face albo już gotowego "użytkownik/repo". Zwraca None,
    #       gdy tekst jest za krótki, żeby w ogóle mógł być repo.
    tekst = tekst.strip()
    # WHY: dopuszczamy link bez schematu (samo "huggingface.co/...", tak jak
    #      bywa wklejane z paska adresu bez zaznaczonego "https://").
    tekst = re.sub(r"^(https?://)?(www\.)?huggingface\.co/", "", tekst, flags=re.IGNORECASE)
    czesci = [c for c in tekst.strip("/").split("/") if c]
    # WHY: link do konkretnego pliku/gałęzi (".../blob/main/plik.gguf") ma
    #      więcej niż dwa segmenty - samo repo to zawsze pierwsze dwa.
    if len(czesci) < 2:
        return None
    return "/".join(czesci[:2])


def _skroc_nazwe_modelu_hf(nazwa):
    # WHAT: skraca długą nazwę modelu pobranego z Hugging Face
    #       ('hf.co/<użytkownik>/<repo>:<kwant>') do samej nazwy repo +
    #       kwantyzacji, bez "hf.co/<użytkownik>/" - źródło pokazuje osobna
    #       kolumna "Źródło" (patrz _zrodlo_modelu). Modele z ollama.com/library (krótkie
    #       nazwy typu "qwen2.5-coder:7b") wracają bez zmian - dotyczy
    #       WYŁĄCZNIE modeli zaczynających się od "hf.co/".
    # WHY:  pełna nazwa (np. "hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF:
    #       Q4_K_M") jest za długa dla kolumny "Model" w tabeli - obcina się/
    #       zasłania resztę wiersza. Tylko WYŚWIETLANIE - prawdziwa nazwa (do
    #       usuwania modelu przez API) leci osobno jako dane wiersza, patrz
    #       Qt.ItemDataRole.UserRole w _po_odswiezeniu/_wybrany_model.
    if not nazwa.startswith("hf.co/"):
        return nazwa
    reszta = nazwa[len("hf.co/"):]  # "<użytkownik>/<repo>:<kwant>"
    # WHY: NIE nazywamy separatora bezimiennym "_" - to by przesłoniło
    #      globalną funkcję tłumaczeń _() w reszcie funkcji (patrz bug
    #      naprawiony w _zbuduj_tooltip_modelu, ten sam wzorzec partition()).
    _uzytkownik, _sep, repo_i_kwant = reszta.partition("/")
    return repo_i_kwant or reszta


def _zrodlo_modelu(nazwa):
    # WHAT: skąd pochodzi zainstalowany model - po samej nazwie ("hf.co/..." =
    #       Hugging Face, reszta = biblioteka Ollamy). Nazwy własne źródeł,
    #       nietłumaczone - tak samo w każdym języku.
    # WHY:  na wyraźną prośbę użytkownika ("żeby było wiadomo, skąd model jest")
    #       - osobna kolumna w tabeli zainstalowanych i w wynikach wyszukiwania,
    #       zamiast dawnego dopisku "(hf)" przy nazwie.
    return "Hugging Face" if nazwa.startswith("hf.co/") else "Ollama"


def _zbuduj_tooltip_modelu(m):
    # WHAT: składa dymek (tooltip) z podstawowymi informacjami o modelu -
    #       skąd pochodzi (Ollama library / Hugging Face + autor kwantyzacji
    #       dla HF), rodzina, parametry, kwantyzacja, data pobrania.
    # WHY:  na wyraźną prośbę użytkownika ("skąd pobranie, kto jest autorem
    #       itd") - najprostsza droga do pokazania tego bez budowania osobnego
    #       panelu: dymek po najechaniu na wiersz, wszystko już mamy z
    #       /api/tags (patrz OllamaClient.list_models), zero dodatkowych zapytań.
    #       Zwykły tekst (nie <a href>) dla adresów URL - dymki nie są klikalne,
    #       więc prawdziwy link tylko myliłby.
    nazwa = m.get("name", "")
    linie = [f"<b>{nazwa}</b>"]

    if nazwa.startswith("hf.co/"):
        reszta = nazwa[len("hf.co/"):]
        # WHY: NIE nazywamy separatora "_" - przesłoniłoby to globalną
        #      funkcję tłumaczeń _(), której ta funkcja jeszcze kilka linii
        #      niżej używa (BUG, który tak właśnie się objawił przy pierwszej
        #      wersji tego kodu - TypeError: 'str' object is not callable).
        uzytkownik, _sep, _reszta = reszta.partition("/")
        repo = reszta.split(":", 1)[0]
        linie.append(_("Źródło: Hugging Face (huggingface.co/{repo})").format(repo=repo))
        if uzytkownik:
            linie.append(_("Autor/wydawca kwantyzacji: {uzytkownik}").format(uzytkownik=uzytkownik))
    else:
        rodzina_z_nazwy = nazwa.split(":", 1)[0]
        linie.append(_("Źródło: ollama.com/library/{model}").format(model=rodzina_z_nazwy))

    rodzina = m.get("family")
    if rodzina:
        linie.append(_("Rodzina modelu: {rodzina}").format(rodzina=rodzina))
    parametry = m.get("parameter_size")
    if parametry:
        linie.append(_("Parametry: {parametry}").format(parametry=parametry))
    kwantyzacja = m.get("quantization_level")
    # BUG naprawiony: Ollama dla rzadziej spotykanych etykiet kwantyzacji
    #      (np. "Q6_K_XL" - nowsze warianty od bartowski/unsloth) zwraca
    #      dosłowne "unknown" zamiast pustego pola - dawniej appka pokazywała
    #      bezużyteczne "Kwantyzacja: unknown". Dla modeli z HF PRAWDZIWA
    #      kwantyzacja i tak jest częścią tagu (":Q6_K_XL" w nazwie modelu -
    #      to dokładnie to, co appka złożyła w wyszukiwarce), więc dla
    #      takich przypadków bierzemy ją stamtąd zamiast z niewiarygodnego
    #      pola API. Zweryfikowane na żywym modelu: hf.co/unsloth/
    #      GLM-4.7-Flash-GGUF:Q6_K_XL - details.family="deepseek2" (GLM-4
    #      dzieli architekturę z DeepSeek2 w silniku Ollamy - to PRAWDZIWA
    #      wartość z API, nie błąd), details.quantization_level="unknown".
    if (not kwantyzacja or kwantyzacja.lower() == "unknown") and nazwa.startswith("hf.co/") and ":" in nazwa:
        kwantyzacja = nazwa.rsplit(":", 1)[1]
    elif kwantyzacja and kwantyzacja.lower() == "unknown":
        kwantyzacja = None  # WHY: modele spoza HF nie mają tagu do fallbacku - lepiej nic niż "unknown"
    if kwantyzacja:
        linie.append(_("Kwantyzacja: {kwantyzacja}").format(kwantyzacja=kwantyzacja))
    # WHY: Ollama zwraca znacznik czasu ISO 8601 z nanosekundami - Python nie
    #      sparsuje > 6 cyfr ułamka sekundy, więc bierzemy tylko samą datę
    #      (pierwsze 10 znaków: RRRR-MM-DD) zamiast pełnego parsowania.
    zmodyfikowano = (m.get("modified_at") or "")[:10]
    if zmodyfikowano:
        linie.append(_("Pobrano/zmodyfikowano: {data}").format(data=zmodyfikowano))

    return "<br>".join(linie)


def hf_wyszukaj_warianty_gguf(repo):
    # WHAT: pyta publiczne API Hugging Face o pliki w repo (endpoint 'tree' -
    #       w przeciwieństwie do 'api/models/<repo>' zwraca rozmiar KAŻDEGO
    #       pliku wprost w odpowiedzi, bez osobnego zapytania na plik),
    #       filtruje `.gguf` i grupuje wg wyciągniętej kwantyzacji.
    # WHY:  modele dzielone na części ("...-00001-of-00002.gguf") mają kilka
    #       plików na TĘ SAMĄ kwantyzację - sumujemy ich rozmiar, żeby appka
    #       pokazała jeden wpis z prawdziwym łącznym rozmiarem do pobrania
    #       (Ollama sama ściągnie i złoży wszystkie części pod jednym tagiem).
    # BUG naprawiony: niektóre repo mają TEN SAM model dwa razy - w całości
    #       I pocięty na części (np. Qwen/Qwen2.5-Coder-7B-Instruct-GGUF: fp16
    #       jako jeden plik 14,2 GB + te same wagi w 4 częściach). Dawniej
    #       wszystko się sumowało (28,4 GB dla modelu 7B). Teraz: jest plik w
    #       całości -> liczy się tylko on; części sumujemy tylko, gdy go brak.
    r = requests.get(
        f"https://huggingface.co/api/models/{repo}/tree/main",
        params={"recursive": "true"}, timeout=10,
    )
    if r.status_code == 404:
        raise ValueError(_("Nie znaleziono takiego repozytorium na Hugging Face."))
    r.raise_for_status()

    calosci = {}  # kwantyzacja (str) albo None -> rozmiar pliku w całości
    czesci = {}   # kwantyzacja -> suma rozmiarów części "-00001-of-0000N"
    for wpis in r.json():
        sciezka = wpis.get("path", "")
        if not sciezka.lower().endswith(".gguf"):
            continue
        dopasowanie = _GGUF_KWANTYZACJA_WZORZEC.search(Path(sciezka).stem)
        # WHY: group(0), nie group(1) - przedrostki w regexie są teraz
        #      niepobierające ('(?:...)'), całe dopasowanie (przedrostek +
        #      cała reszta tokenu) to jedyna "grupa", jaką mamy.
        kwant = dopasowanie.group(0).upper() if dopasowanie else None
        cel = czesci if re.search(r"-\d{5}-of-\d{5}$", Path(sciezka).stem) else calosci
        cel[kwant] = cel.get(kwant, 0) + wpis.get("size", 0)

    warianty = {**czesci, **calosci}  # WHY: plik w całości wygrywa z sumą części
    if not warianty:
        raise ValueError(_("To repozytorium nie zawiera żadnych plików .gguf."))

    # WHY: None (nierozpoznana kwantyzacja w nazwie pliku - zwykle repo z
    #      jednym plikiem .gguf bez dopisku) ląduje na końcu listy.
    return sorted(warianty.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))


def hf_wyszukaj_repozytoria(zapytanie):
    # WHAT: szuka repo na Hugging Face pasujących do zapytania (nazwa, nie
    #       dokładny "user/repo"), ograniczone do tych oznaczonych tagiem
    #       'gguf' (HF sam dokleja go repo, w którym wykryje pliki .gguf) -
    #       odsiewa z góry repo w surowym safetensors, których appka/Ollama i
    #       tak by nie pobrała. Posortowane wg liczby pobrań - najpopularniejsze
    #       (zwykle najlepiej utrzymane kwantyzacje, np. bartowski/unsloth) pierwsze.
    # WHY:  Wariant C z CLAUDE.md (roadmapa) - alternatywa dla wklejania gotowego
    #       linku/repo (Wariant B, hf_wyszukaj_warianty_gguf), gdy użytkownik
    #       zna tylko nazwę modelu, nie dokładne repo z kwantyzacją.
    r = requests.get(
        "https://huggingface.co/api/models",
        params={
            "search": zapytanie, "filter": "gguf",
            "sort": "downloads", "direction": "-1", "limit": 20,
        },
        timeout=10,
    )
    r.raise_for_status()
    return [
        {"id": wpis.get("id", ""), "pobrania": wpis.get("downloads", 0)}
        for wpis in r.json() if wpis.get("id")
    ]


def _rozmiar_na_bajty(tekst):
    # WHAT: zamienia rozmiar w formacie strony ollama.com ("4.7GB", "398MB") na
    #       bajty. Zwraca None, gdy tekstu nie da się odczytać.
    # WHY:  HF podaje rozmiar w bajtach wprost z API, ollama.com tylko jako tekst
    #       na stronie - jedna jednostka dla obu źródeł, żeby tabela wariantów
    #       i kalkulator pamięci nie musiały ich rozróżniać.
    dopasowanie = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?B)\s*", tekst or "", re.IGNORECASE)
    if not dopasowanie:
        return None
    mnozniki = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
    return int(float(dopasowanie.group(1)) * mnozniki[dopasowanie.group(2).upper()])


def _formatuj_liczbe(n):
    # WHAT: liczba pobrań w skróconej formie ("21.8M", "146.0K") - tak jak
    #       pokazuje ją ollama.com, żeby oba źródła w tabeli wyników wyglądały
    #       spójnie (HF podaje surową liczbę).
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def ollama_wyszukaj_modele(zapytanie):
    # WHAT: szuka modeli w oficjalnej bibliotece Ollamy (ollama.com/search) -
    #       lista słowników: nazwa, opis, możliwości (tools/vision/embedding/
    #       thinking), dostępne rozmiary i liczba pobrań (tekst jak na stronie).
    # WHY:  ollama.com NIE ma publicznego API wyszukiwania (sprawdzone 2026-09:
    #       /api/search i podobne dają 404) - jedynym źródłem jest zwykła strona
    #       HTML, więc odczytujemy ją wyrażeniami regularnymi.
    # UWAGA: celowo KRUCHE miejsce - gdy ollama.com zmieni wygląd strony,
    #        parser przestanie znajdować wyniki (pusta lista, appka się nie
    #        wywali) i trzeba go będzie poprawić. Przy HF tego ryzyka nie ma
    #        (prawdziwe API, patrz hf_wyszukaj_repozytoria).
    r = requests.get("https://ollama.com/search", params={"q": zapytanie}, timeout=10)
    r.raise_for_status()
    czesci = re.split(r'<a href="/library/([^"]+)" class="group w-full">', r.text)
    wyniki = []
    for i in range(1, len(czesci), 2):
        blok = czesci[i + 1].split("</a>", 1)[0]
        opis = re.search(r'<p class="max-w-lg[^"]*">(.*?)</p>', blok, re.S)
        pobrania = re.search(r'>\s*([\d.,]+[KMB]?)\s*</span>\s*<span class="hidden sm:flex">&nbsp;Pulls', blok)
        wyniki.append({
            "zrodlo": "ollama",
            "id": czesci[i],
            "opis": html.unescape(re.sub(r"\s+", " ", opis.group(1))).strip() if opis else "",
            "mozliwosci": re.findall(r'bg-indigo-50[^>]*>\s*([^<]+?)\s*<', blok),
            "rozmiary": re.findall(r'bg-\[#ddf4ff\][^>]*>\s*([^<]+?)\s*<', blok),
            "pobrania": pobrania.group(1) if pobrania else "",
        })
    return wyniki


def ollama_warianty_modelu(model):
    # WHAT: czyta stronę tagów modelu (ollama.com/library/<model>/tags) i zwraca
    #       listę (tag, rozmiar_bajty, kontekst) - każdy dokładny, pobieralny
    #       wariant z PRAWDZIWYM rozmiarem pliku.
    # WHY:  zastępuje dawne, ręcznie utrzymywane tabele ROZMIARY_WG_MODELU/
    #       KWANTYZACJE_WG_MODELU (segmenty "instruct"/"it" w tagu zgadywane
    #       i weryfikowane ręcznie - źródło kilku błędów) - tagi przychodzą
    #       gotowe ze źródła, nie trzeba ich znać z góry ani składać z kawałków.
    #       Pomijamy warianty MLX (tylko Apple Silicon - na Linuksie się nie
    #       uruchomią) i tagi "cloud"/bez rozmiaru (liczone na serwerach Ollamy,
    #       nie do pobrania lokalnie).
    # UWAGA: ta sama kruchość co ollama_wyszukaj_modele - odczyt HTML, nie API.
    r = requests.get(f"https://ollama.com/library/{model}/tags", timeout=10)
    if r.status_code == 404:
        raise ValueError(_("Nie znaleziono takiego modelu w bibliotece Ollamy."))
    r.raise_for_status()
    wiersze = re.findall(
        r'<a href="/library/' + re.escape(model) + r':([^"]+)" class="md:hidden[^"]*">(.*?)</a>',
        r.text, re.S,
    )
    warianty = []
    for tag, blok in wiersze:
        tekst = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", blok))
        if "cloud" in tag or re.search(r"\bMLX\b", tekst):
            continue
        rozmiar = re.search(r"•\s*([\d.]+\s*[KMGT]?B)\s*•", tekst)
        if not rozmiar:
            continue
        kontekst = re.search(r"•\s*(\d+[KM]?)\s+context window", tekst)
        warianty.append((tag, _rozmiar_na_bajty(rozmiar.group(1)), kontekst.group(1) if kontekst else ""))
    if not warianty:
        raise ValueError(_("Ten model nie ma wariantów do pobrania na Linuksie (tylko MLX albo cloud)."))
    return warianty


def _parsuj_rozmiar_parametrow(tekst):
    # WHAT: wyciąga liczbę parametrów (W MILIARDACH) z tekstu typu "8b", "1.5B",
    #       "300m", "70b-instruct", "llama3.1:8b" albo details.parameter_size z
    #       /api/tags (np. "8.0B", "134.52M") - bierze pierwsze dopasowanie
    #       "<liczba>b"/"<liczba>m" ("m" = miliony, dzielone przez 1000).
    # WHY:  wspólna dla pola "Rozmiar" przy pobieraniu (i dla samej nazwy modelu
    #       jako fallback) ORAZ dla kolumny VRAM na liście zainstalowanych modeli -
    #       jedna funkcja zamiast dwóch osobnych, żeby nie rozjeżdżały się przy
    #       zmianach (modele embeddingowe jak mxbai-embed-large/bge-m3/
    #       embeddinggemma podają rozmiar w milionach, nie miliardach).
    if not tekst:
        return None
    dopasowanie = re.search(r"(\d+(?:\.\d+)?)\s*([bBmM])(?![a-zA-Z])", tekst)
    if not dopasowanie:
        return None
    liczba = float(dopasowanie.group(1))
    return liczba / 1000 if dopasowanie.group(2).upper() == "M" else liczba


# WHAT: mnożnik GB/mld parametrów, gdy Ollama nie poda kwantyzacji modelu
#       (odpowiednik Q4_K_M - najczęstszej domyślnej kwantyzacji Ollamy).
_BPW_DOMYSLNE = 0.61

# WHAT: bajty na element pamięci podręcznej kontekstu (KV cache) wg jej kwantyzacji.
_KVQ_BAJTY_NA_ELEMENT = {
    "f16": 2,
    "q8_0": 1,
    "q4_0": 0.5,
}

# WHAT: przybliżenia architektury (liczba warstw L, liczba głowic KV H_KV) wg
#       wielkości modelu P (mld parametrów) - progi górne przedziałów.
# WHY:  appka nie zna dokładnej architektury modelu, dopóki go nie pobierze -
#       to tabela przybliżeń zamiast prawdziwych metadanych.
_PRZYBLIZENIE_ARCHITEKTURY = [
    (4, 36, 8),
    (9, 32, 8),
    (16, 48, 8),
    (40, 64, 8),
]
_PRZYBLIZENIE_ARCHITEKTURY_MAX = (80, 8)  # P > 40

_TYPOWE_WIELKOSCI_RAM = (8, 16, 32, 48, 64, 96, 128)

# WHAT: kontekst i kwantyzacja cache przyjęte jako STAŁE w kalkulatorze - appka
#       nie daje ich wybrać przy pobieraniu (kontekst ustawia się osobno w
#       zakładce Zaawansowane; 16384 to tamtejsza zalecana wartość - patrz
#       OLLAMA_ZALECANE). q8_0 (nie f16) - bo profil OLLAMA_ZALECANE i tak
#       włącza OLLAMA_FLASH_ATTENTION=1, a bez niego Ollama liczy cache jak
#       f16 mimo wybranej kwantyzacji - stąd stałe ostrzeżenie o tym wymogu
#       w _aktualizuj_szacowana_pamiec, dopóki KVQ != "f16".
_KALKULATOR_CTX = 16384
_KALKULATOR_KVQ = "q8_0"
_KALKULATOR_D = 128  # wymiar głowicy - domyślny


# WHAT: mapowanie PRAWDZIWEJ wartości details.quantization_level z /api/tags
#       (np. "Q4_K_M", "F16") na mnożnik GB/mld parametrów (BPW).
# WHY:  lista zainstalowanych modeli liczy VRAM na faktycznych metadanych
#       Ollamy, nie na zgadywaniu - ale Ollama zna więcej wariantów, niż appka
#       daje wybrać przy pobieraniu, stąd osobna, szersza tabela. Nieznany/
#       nierozpoznany wariant dostaje _BPW_DOMYSLNE (jak Q4_K_M).
_QUANTIZATION_LEVEL_BPW = {
    "FP16": 2.00,
    "F16": 2.00,
    "Q8_0": 1.06,
    "Q6_K": 0.82,
    "Q5_K_M": 0.71,
    "Q4_K_M": 0.61,
    "Q3_K_M": 0.49,
    "Q2_K": 0.42,
}


def _oszacuj_pamiec_z_wag(wagi_gb, miliardy_parametrow, etykieta_q):
    # WHAT: kalkulator pamięci dla modelu LLM - wagi + KV cache + bufory, z
    #       zapasem na system - zwraca słownik ze wszystkimi krokami pośrednimi.
    #       Wspólny rdzeń dla karty "Pobierz nowy model" (wagi = PRAWDZIWY
    #       rozmiar wybranego pliku) i listy "Zainstalowane modele" (wagi
    #       wyliczone z metadanych API - patrz _oszacuj_pamiec_bpw).
    # WHY:  sama waga to za mało - KV cache potrafi przewyższyć wagi przy
    #       mniejszych modelach i długim kontekście, więc liczymy go osobno, na
    #       podstawie przybliżonej architektury (L, H_KV) dobranej wg wielkości P.
    l, h_kv = _PRZYBLIZENIE_ARCHITEKTURY_MAX
    for prog, prog_l, prog_h_kv in _PRZYBLIZENIE_ARCHITEKTURY:
        if miliardy_parametrow <= prog:
            l, h_kv = prog_l, prog_h_kv
            break

    bajty_na_element = _KVQ_BAJTY_NA_ELEMENT[_KALKULATOR_KVQ]
    bajty_na_token = 2 * l * h_kv * _KALKULATOR_D * bajty_na_element
    kv_cache_gb = (bajty_na_token * _KALKULATOR_CTX) / (1024 ** 3)

    bufory_gb = 0.3 + (miliardy_parametrow * 0.04)

    zajete_realnie_gb = wagi_gb + kv_cache_gb + bufory_gb
    z_zapasem_gb = zajete_realnie_gb / 0.78  # model+cache = maks. 78% pamięci, reszta dla systemu

    rekomendowany_ram = next(
        (r for r in _TYPOWE_WIELKOSCI_RAM if r >= z_zapasem_gb),
        _TYPOWE_WIELKOSCI_RAM[-1],
    )

    return {
        "etykieta_q": etykieta_q,
        "wagi_gb": wagi_gb,
        "kv_cache_gb": kv_cache_gb,
        "bufory_gb": bufory_gb,
        "zajete_realnie_gb": zajete_realnie_gb,
        "z_zapasem_gb": z_zapasem_gb,
        "rekomendowany_ram": rekomendowany_ram,
    }


def _oszacuj_pamiec_bpw(miliardy_parametrow, bpw, etykieta_q):
    # WHAT: wariant dla listy "Zainstalowane modele" - wagi liczone z liczby
    #       parametrów i kwantyzacji z metadanych API (patrz _QUANTIZATION_LEVEL_BPW).
    return _oszacuj_pamiec_z_wag(miliardy_parametrow * bpw, miliardy_parametrow, etykieta_q)


# =============================================================================
#  Warstwa sieci - odseparowana od GUI
# =============================================================================
class OllamaClient:
    """Cienka nakładka na REST API Ollamy.

    WHAT: lista modeli, strumieniowe pobieranie, usuwanie modelu.
    WHY:  trzymamy sieć poza oknem - łatwiej rozwijać i testować osobno.
    """

    def __init__(self, base_url=OLLAMA_URL):
        # WHY: ucinamy końcowy '/', inaczej powstałby podwójny slash w ścieżkach.
        self.base_url = base_url.rstrip("/")

    def api_dziala(self):
        # WHAT: szybki ping API. WHY: /api/tags odpowiada 200 tylko gdy serwer żyje.
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self):
        # WHAT: lista zainstalowanych modeli - nazwa, rozmiar na dysku (bajty)
        #       i metadane kwantyzacji (details.parameter_size/quantization_level),
        #       plus rodzina (details.family) i data pobrania/modyfikacji
        #       (modified_at) - do dymku z informacjami o modelu (patrz
        #       _zbuduj_tooltip_modelu), bez osobnego zapytania per model.
        # WHY:  rozmiar do pokazania, ile miejsca zajmuje model na dysku;
        #       parameter_size/quantization_level - do wyliczenia orientacyjnego
        #       VRAM tym samym kalkulatorem co karta "Pobierz nowy model"
        #       (patrz _oszacuj_pamiec_bpw), na PRAWDZIWYCH metadanych modelu,
        #       nie na zgadywaniu rozmiaru z samej nazwy. family/modified_at są
        #       w tej samej odpowiedzi /api/tags - appka je tylko dotąd ignorowała.
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            wynik = []
            for m in r.json().get("models", []):
                szczegoly = m.get("details") or {}
                wynik.append({
                    "name": m["name"],
                    "size": m.get("size", 0),
                    "parameter_size": szczegoly.get("parameter_size", ""),
                    "quantization_level": szczegoly.get("quantization_level", ""),
                    "family": szczegoly.get("family", ""),
                    "modified_at": m.get("modified_at", ""),
                })
            return wynik
        except requests.RequestException:
            return []

    def list_loaded(self):
        # WHAT: modele aktualnie załadowane do pamięci (RAM/VRAM), z /api/ps.
        # WHY:  pozwala zobaczyć, co faktycznie siedzi teraz na karcie graficznej,
        #       zamiast zgadywać po zużyciu VRAM w innym narzędziu.
        try:
            r = requests.get(f"{self.base_url}/api/ps", timeout=5)
            r.raise_for_status()
            return r.json().get("models", [])
        except requests.RequestException:
            return []

    def delete_model(self, name):
        # WHAT: usuwa model z dysku przez API.
        # WHY:  używamy API (a nie 'ollama rm'), bo działa też gdy OLLAMA_URL
        #       wskazuje zdalny host (BC-250). Rzuca wyjątek przy błędzie.
        r = requests.delete(
            f"{self.base_url}/api/delete",
            json={"model": name},
            timeout=30,
        )
        r.raise_for_status()

    def pull_stream(self, model):
        # WHAT: generator - oddaje kolejne komunikaty postępu pobierania.
        # WHY:  /api/pull zwraca linie JSON z polami status/total/completed;
        #       strumień pozwala pokazać pasek zamiast zamrożonego okna.
        with requests.post(
            f"{self.base_url}/api/pull",
            json={"model": model, "stream": True},
            stream=True,
            timeout=None,  # WHY: pobranie kilku GB trwa - brak limitu czasu
        ) as r:
            r.raise_for_status()
            for linia in r.iter_lines():
                if linia:
                    yield json.loads(linia)


# =============================================================================
#  Wątki robocze - żeby nie blokować GUI
# =============================================================================
class RefreshWorker(QThread):
    """Pobiera w tle stan usługi i listę modeli.

    WHY: zapytania sieciowe i wywołania systemctl nie mogą iść w wątku GUI.
    """
    wynik = pyqtSignal(dict)  # patrz klucze w self.wynik.emit({...}) niżej

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        zainstalowana = ollama_zainstalowana()
        active = _systemctl_query("is-active") == "active"
        enabled = _systemctl_query("is-enabled") == "enabled"
        env_ollama = _usluga_env_wszystkie()  # WHY: jedno czytanie override.conf - zakładka "Zaawansowane"
        api = self.client.api_dziala()
        modele = self.client.list_models() if api else []
        zaladowane = self.client.list_loaded() if api else []
        webui = webui_zainstalowane()
        webui_autostart = webui_autostart_wlaczony()
        # WHY: samo "zainstalowane" nie mówi, czy serwer akurat teraz odpowiada -
        #      pasek statystyk ma pokazywać żywy stan, nie tylko obecność binarki.
        webui_dziala = _webui_dziala()

        litellm = litellm_zainstalowane()
        litellm_autostart = litellm_autostart_wlaczony()
        litellm_dziala = _litellm_dziala()

        self.wynik.emit({
            "zainstalowana": zainstalowana,
            "active": active,
            "enabled": enabled,
            "env_ollama": env_ollama,
            "api": api,
            "models": modele,
            "zaladowane": zaladowane,
            "webui": webui,
            "webui_autostart": webui_autostart,
            "webui_dziala": webui_dziala,
            "litellm": litellm,
            "litellm_autostart": litellm_autostart,
            "litellm_dziala": litellm_dziala,
        })


class PullWorker(QThread):
    """Pobiera model w tle wraz z postępem."""
    postep = pyqtSignal(int, str)       # (procent lub -1, opis_statusu)
    zakonczono = pyqtSignal(bool, str)  # (sukces, komunikat)

    def __init__(self, client, model):
        super().__init__()
        self.client = client
        self.model = model

    def run(self):
        try:
            for dane in self.client.pull_stream(self.model):
                status = dane.get("status", "")
                total = dane.get("total")
                completed = dane.get("completed")
                # WHAT: procent liczymy tylko gdy znamy rozmiar całości.
                # WHY:  komunikaty typu "pulling manifest" nie mają total -> -1.
                if total and completed:
                    self.postep.emit(int(completed / total * 100), status)
                else:
                    self.postep.emit(-1, status)
            self.zakonczono.emit(True, _("Pobrano model: {model}").format(model=self.model))
        except Exception as e:
            self.zakonczono.emit(False, _("Błąd pobierania: {blad}").format(blad=e))


class ActionWorker(QThread):
    """Uruchamia dowolną krótką akcję (funkcję) w tle.

    WHY: sterowanie usługą (dialog pkexec) i usuwanie modelu (sieć) są krótkie,
         ale blokujące - nie mogą iść w wątku GUI. Jeden worker na obie rzeczy.
    """
    zakonczono = pyqtSignal(bool, str)

    def __init__(self, funkcja, opis):
        super().__init__()
        self.funkcja = funkcja
        self.opis = opis

    def run(self):
        try:
            self.funkcja()
            self.zakonczono.emit(True, f"{self.opis}: OK")
        except Exception as e:
            self.zakonczono.emit(False, _("{opis}: błąd - {blad}").format(opis=self.opis, blad=e))


class SprawdzAktualizacjeWorker(QThread):
    """Sprawdza w tle, czy jest dostępna nowsza wersja programu niż zainstalowana.

    WHY: odczyt lokalnej wersji (subprocess) i zapytanie o najnowszą (sieć) są
         blokujące - jak każda inna sieć/proces w tej appce, idą w osobnym wątku.
         Osobny worker (nie ActionWorker) - potrzebujemy z powrotem STRUKTURY
         danych (obie wersje + czy jest aktualizacja), nie tylko tekstu komunikatu.
         Ogólny - przyjmuje funkcje "wersja lokalna"/"wersja najnowsza" w
         konstruktorze, żeby nie duplikować tej samej logiki dla Ollamy i Open
         WebUI (dwa różne miejsca w oknie wołają ten sam worker z różnymi funkcjami).
    """
    # {"blad": str|None, "lokalna": str|None, "najnowsza": str|None, "dostepna": bool}
    wynik = pyqtSignal(dict)

    def __init__(self, funkcja_lokalna, funkcja_najnowsza):
        super().__init__()
        self.funkcja_lokalna = funkcja_lokalna
        self.funkcja_najnowsza = funkcja_najnowsza

    def run(self):
        lokalna = self.funkcja_lokalna()
        try:
            najnowsza = self.funkcja_najnowsza()
        except requests.RequestException as e:
            self.wynik.emit({"blad": str(e), "lokalna": lokalna, "najnowsza": None, "dostepna": False})
            return
        if lokalna is None or najnowsza is None:
            self.wynik.emit({
                "blad": _("nie udało się odczytać numeru wersji"),
                "lokalna": lokalna, "najnowsza": najnowsza, "dostepna": False,
            })
            return
        dostepna = _rozbij_wersje(najnowsza) > _rozbij_wersje(lokalna)
        self.wynik.emit({"blad": None, "lokalna": lokalna, "najnowsza": najnowsza, "dostepna": dostepna})


class SzukajModeliWorker(QThread):
    """Szuka modeli naraz w obu źródłach (biblioteka Ollamy + Hugging Face), w tle.

    WHY: jedna wspólna wyszukiwarka w karcie "Pobierz nowy model" - błąd
         jednego źródła (np. brak sieci do HF albo zmiana strony ollama.com)
         NIE może ukryć wyników z drugiego, więc każde źródło łapie swój błąd
         osobno i trafia do listy błędów zamiast przerywać całość.
    """
    wynik = pyqtSignal(list, list)  # (wyniki z obu źródeł, komunikaty błędów)

    def __init__(self, zapytanie):
        super().__init__()
        self.zapytanie = zapytanie

    def run(self):
        wyniki, bledy = [], []
        try:
            wyniki += ollama_wyszukaj_modele(self.zapytanie)
        except requests.RequestException as e:
            bledy.append(_("Ollama: {blad}").format(blad=e))
        try:
            for wpis in hf_wyszukaj_repozytoria(self.zapytanie):
                wyniki.append({
                    "zrodlo": "hf", "id": wpis["id"], "opis": "", "mozliwosci": [],
                    "rozmiary": [], "pobrania": _formatuj_liczbe(wpis["pobrania"]),
                })
        except requests.RequestException as e:
            bledy.append(_("Hugging Face: {blad}").format(blad=e))
        self.wynik.emit(wyniki, bledy)


class WariantyModeluWorker(QThread):
    """Pobiera w tle listę wariantów wybranego modelu - z Ollamy albo z HF.

    WHY: jeden worker dla obu źródeł - różni się tylko funkcja czytająca
         warianty, wynik ma ten sam kształt: (tag/kwantyzacja, rozmiar_bajty,
         kontekst). Dla HF kontekstu nie znamy przed pobraniem (pusty tekst).
    """
    wynik = pyqtSignal(str, str, list)  # (źródło, id modelu, warianty)
    blad = pyqtSignal(str, str, str)    # (źródło, id modelu, komunikat)

    def __init__(self, zrodlo, model_id):
        super().__init__()
        self.zrodlo = zrodlo
        self.model_id = model_id

    def run(self):
        try:
            if self.zrodlo == "hf":
                warianty = [(kwant, rozmiar, "") for kwant, rozmiar in hf_wyszukaj_warianty_gguf(self.model_id)]
            else:
                warianty = ollama_warianty_modelu(self.model_id)
        except (requests.RequestException, ValueError) as e:
            self.blad.emit(self.zrodlo, self.model_id, str(e))
            return
        self.wynik.emit(self.zrodlo, self.model_id, warianty)


class AgregatorWorker(QThread):
    """Odpytuje na żywo hosty z listy serwerów o zainstalowane modele.

    WHY: podgląd "co trafi do configu LiteLLM" wymaga /api/tags na KAŻDYM
         hoście z listy - może potrwać, więc w tle jak każda inna sieć.
    """
    wynik = pyqtSignal(list)  # lista krotek (nazwa_hosta, model, adres)

    def __init__(self, serwery):
        super().__init__()
        self.serwery = serwery

    def run(self):
        self.wynik.emit(_wykryj_modele_na_serwerach(self.serwery))


# =============================================================================
#  Zarządzanie listą serwerów (dialog otwierany z paska w głównym oknie)
# =============================================================================
class DialogZarzadzajSerwerami(QDialog):
    """Dodawanie/usuwanie wpisów z listy serwerów Ollama do przełącznika."""

    def __init__(self, serwery, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Zarządzaj serwerami"))
        self.setMinimumWidth(420)
        # WHY: kopia robocza - lista trafia do wywołującego dopiero po zamknięciu.
        self.serwery = [dict(s) for s in serwery]

        layout = QVBoxLayout(self)

        self.lista = QListWidget()
        layout.addWidget(self.lista)

        pasek_usun = QHBoxLayout()
        pasek_usun.addStretch(1)
        btn_usun = QPushButton(_("Usuń zaznaczony"))
        btn_usun.clicked.connect(self._usun)
        pasek_usun.addWidget(btn_usun)
        layout.addLayout(pasek_usun)

        formularz = QHBoxLayout()
        self.pole_nazwa = QLineEdit()
        self.pole_nazwa.setPlaceholderText(_("Nazwa (np. BC-250)"))
        self.pole_adres = QLineEdit()
        self.pole_adres.setPlaceholderText(_("Adres (np. http://192.168.0.236:11434)"))
        btn_dodaj = QPushButton(_("Dodaj"))
        btn_dodaj.clicked.connect(self._dodaj)
        formularz.addWidget(self.pole_nazwa)
        formularz.addWidget(self.pole_adres, 1)
        formularz.addWidget(btn_dodaj)
        layout.addLayout(formularz)

        btn_zamknij = QPushButton(_("Zamknij"))
        btn_zamknij.clicked.connect(self.accept)
        layout.addWidget(btn_zamknij, alignment=Qt.AlignmentFlag.AlignRight)

        self._odswiez_liste()

    def _odswiez_liste(self):
        self.lista.clear()
        for s in self.serwery:
            self.lista.addItem(f"{s['nazwa']}  —  {s['adres']}")

    def _dodaj(self):
        nazwa = self.pole_nazwa.text().strip()
        adres = self.pole_adres.text().strip().rstrip("/")
        if not nazwa or not adres:
            QMessageBox.warning(self, _("Brak danych"), _("Podaj nazwę i adres serwera."))
            return
        self.serwery.append({"nazwa": nazwa, "adres": adres})
        self.pole_nazwa.clear()
        self.pole_adres.clear()
        self._odswiez_liste()

    def _usun(self):
        wiersz = self.lista.currentRow()
        if wiersz < 0:
            return
        # WHY: przełącznik zawsze potrzebuje przynajmniej jednej pozycji do wyboru.
        if len(self.serwery) <= 1:
            QMessageBox.warning(self, _("Nie można usunąć"), _("Musi zostać co najmniej jeden serwer."))
            return
        del self.serwery[wiersz]
        self._odswiez_liste()


class DialogConfigContinue(QDialog):
    """Podgląd gotowego config.yaml dla Continue.dev - tylko do skopiowania.

    WHY: appka celowo NIGDY nie zapisuje ~/.continue/config.yaml sama - ten
    plik należy do użytkownika i może mieć inne, niezwiązane z Ollamą wpisy;
    automatyczny zapis mógłby je nadpisać. Zamiast tego pokazujemy gotową,
    poprawną treść (patrz _zbuduj_config_continue) do ręcznego wklejenia/scalenia.
    """

    def __init__(self, tresc, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Config dla Continue.dev"))
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        lbl_opis = QLabel(
            _("Wklej poniższą treść do pliku ~/.continue/config.yaml (nadpisując "
              "go całkowicie, albo scalając ręcznie z istniejącą zawartością, "
              "jeśli masz tam już inne modele). Appka niczego tu nie zapisuje "
              "automatycznie.")
        )
        lbl_opis.setWordWrap(True)
        layout.addWidget(lbl_opis)

        # WHY: każdy model dostaje tu domyślnie role chat/edit/apply - dobre dla
        #      modeli czatowych/kodujących (np. qwen2.5-coder), ale niepoprawne
        #      dla innych ról z listy docelowych modeli projektu (patrz
        #      CLAUDE.md) - model embeddingowy jak nomic-embed-text (używany w
        #      RAG WebUI) potrzebuje ZAMIAST tego "roles: [embed]". Dopisujemy
        #      to wprost w opisie, żeby nie trzeba było szukać w dokumentacji
        #      Continue, jeśli taki model trafi na listę.
        lbl_role = QLabel(
            _("Każdy model dostaje domyślnie role: chat, edit, apply. Dostępne są "
              "też inne role do ręcznej zmiany w treści poniżej: autocomplete "
              "(podpowiedzi w edytorze), embed (modele embeddingowe, np. "
              "nomic-embed-text - używane w RAG), rerank (przeszukiwanie/ranking "
              "wyników). Model, który nie służy do czatu/edycji kodu, powinien "
              "mieć podmienione roles na odpowiednią z tej listy.")
        )
        lbl_role.setWordWrap(True)
        layout.addWidget(lbl_role)

        self.pole_tresc = QPlainTextEdit()
        self.pole_tresc.setPlainText(tresc)
        self.pole_tresc.setReadOnly(True)
        czcionka = self.pole_tresc.font()
        czcionka.setFamily("monospace")  # WHY: YAML czyta się lepiej ze stałą szerokością znaków
        self.pole_tresc.setFont(czcionka)
        layout.addWidget(self.pole_tresc, 1)

        pasek = QHBoxLayout()
        pasek.addStretch(1)
        btn_kopiuj = QPushButton(_("Kopiuj do schowka"))
        btn_kopiuj.clicked.connect(self._kopiuj)
        pasek.addWidget(btn_kopiuj)
        btn_zamknij = QPushButton(_("Zamknij"))
        btn_zamknij.clicked.connect(self.accept)
        pasek.addWidget(btn_zamknij)
        layout.addLayout(pasek)

    def _kopiuj(self):
        QApplication.clipboard().setText(self.pole_tresc.toPlainText())


class DialogPolaczenieUnsloth(QDialog):
    """Dane do ręcznego dodania agregatora LiteLLM jako połączenia w Unsloth Studio.

    WHY: Unsloth Studio NIE ma pliku konfiguracyjnego, który dałoby się
         wygenerować jak config.yaml dla Continue - połączenia dodaje się
         w jego oknie (Settings -> Connections -> Add Connection; sprawdzone
         w docs 2026-09: unsloth.ai/docs/integrations/connections). Typ
         "vLLM" to w Studio ogólne połączenie z serwerem zgodnym z API
         OpenAI (Base URL + opcjonalny klucz + "Load Models" z /v1/models) -
         LiteLLM wystawia dokładnie takie API, więc to właściwy wybór. Dialog
         pokazuje gotowe wartości do skopiowania + listę ID modeli na wypadek,
         gdyby "Load Models" nic nie wczytało (Studio pozwala wpisać je ręcznie).
    """

    def __init__(self, modele, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Połączenie dla Unsloth Studio"))
        self.setMinimumSize(560, 420)
        self.adres = f"{LITELLM_URL}/v1"
        layout = QVBoxLayout(self)

        lbl_kroki = QLabel(_(
            "Unsloth Studio nie ma pliku konfiguracyjnego - połączenie dodaje się w jego "
            "oknie:\n"
            "1. Settings → Connections → Add Connection.\n"
            "2. Typ: vLLM (to w Studio ogólne połączenie z serwerem zgodnym z API OpenAI, "
            "a takie API wystawia LiteLLM).\n"
            "3. Base URL: {adres}\n"
            "4. API key: zostaw puste.\n"
            "5. Kliknij Load Models i zaznacz modele. Jeśli lista się nie wczyta, wpisz "
            "ID modeli ręcznie z listy poniżej.\n"
            "Studio na innym komputerze w sieci: zamiast localhost podaj adres IP tego "
            "komputera. LiteLLM musi być uruchomione (karta wyżej)."
        ).format(adres=self.adres))
        lbl_kroki.setWordWrap(True)
        lbl_kroki.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(lbl_kroki)

        layout.addWidget(QLabel(_("ID modeli w agregatorze:")))
        self.pole_modele = QPlainTextEdit()
        self.pole_modele.setPlainText("\n".join(modele))
        self.pole_modele.setReadOnly(True)
        czcionka = self.pole_modele.font()
        czcionka.setFamily("monospace")
        self.pole_modele.setFont(czcionka)
        layout.addWidget(self.pole_modele, 1)

        pasek = QHBoxLayout()
        pasek.addStretch(1)
        btn_adres = QPushButton(_("Kopiuj adres"))
        btn_adres.clicked.connect(lambda: QApplication.clipboard().setText(self.adres))
        pasek.addWidget(btn_adres)
        btn_modele = QPushButton(_("Kopiuj listę modeli"))
        btn_modele.clicked.connect(lambda: QApplication.clipboard().setText(self.pole_modele.toPlainText()))
        pasek.addWidget(btn_modele)
        btn_zamknij = QPushButton(_("Zamknij"))
        btn_zamknij.clicked.connect(self.accept)
        pasek.addWidget(btn_zamknij)
        layout.addLayout(pasek)


# =============================================================================
#  Główne okno
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.serwery = _wczytaj_serwery()
        self._serwer_aktywny_adres = _wczytaj_serwer_aktywny(self.serwery)
        self.client = OllamaClient(base_url=self._serwer_aktywny_adres)
        self.pull_worker = None
        self.refresh_worker = None
        self._instalacja_worker = None  # WHY: osobne śledzenie, żeby nie odpalić instalacji 2x naraz
        self._szukaj_worker = None      # WHY: wyszukiwarka modeli (karta "Pobierz nowy model") - jedno szukanie naraz
        self._warianty_dla = None       # WHY: (źródło, id) modelu, którego warianty CHCEMY pokazać - spóźnione odpowiedzi dla innych są ignorowane
        self._pobieranie_trwa = False   # WHY: blokada przycisku "Pobierz", niezależna od zaznaczeń w tabelach
        self._aktualizacja_worker = None  # WHY: to samo dla sprawdzania aktualizacji Ollamy
        self._webui_worker = None       # WHY: to samo dla instalacji/uruchomienia Open WebUI
        self._webui_zainstalowane = False  # WHY: potrzebne w klik_webui, żeby wiedzieć co robi przycisk
        self._webui_dziala = False         # WHY: jw. - odróżnia "Uruchom" od "Otwórz" w tym samym miejscu
        self._aktualizacja_webui_worker = None  # WHY: to samo dla sprawdzania aktualizacji Open WebUI
        self._aktualizacja_litellm_worker = None  # WHY: to samo dla sprawdzania aktualizacji LiteLLM
        self._litellm_worker = None     # WHY: to samo dla instalacji/uruchomienia LiteLLM
        self._litellm_zainstalowany = False  # WHY: potrzebne w klik_litellm, żeby wiedzieć co robi przycisk
        self._agregator_worker = None   # WHY: osobny wątek na podgląd modeli (zakładka "Agregator modeli")
        self._workers = []            # WHY: trzymamy referencje, by wątki nie zniknęły w trakcie
        self._ostatni_status = None   # WHY: żeby nie spamować logu tym samym statusem pull

        # WHY: język wczytujemy PRZED _buduj_ui() - wszystkie widgety mają
        #      dostać właściwe napisy od razu przy pierwszym budowaniu okna.
        self._jezyk_aktywny = _wczytaj_jezyk_zapisany()
        _wczytaj_jezyk(self._jezyk_aktywny)

        self.setWindowTitle(f"Ollama Manager {WERSJA}")
        # WHY: rosło razem z appką - "Usługi" mieści sterowanie usługą + Open
        #      WebUI jedna pod drugą, "Agregator modeli" dwie karty jedna pod
        #      drugą, "Zaawansowane" to teraz 10-polowy formularz + pole
        #      własnych parametrów + dwa przyciski, a karta "Pobierz nowy
        #      model" pokazuje wieloliniowe wyliczenie pamięci (kalkulator
        #      wagi+KV cache+bufory) pod polami modelu/rozmiaru/kwantyzacji -
        #      przy 760x700 część z tego się już nie mieściła bez przewijania.
        #      Szerokość podbita z 860 do 960 - nazwy modeli z Hugging Face
        #      ("<repo>:<kwant>", patrz _skroc_nazwe_modelu_hf) bywają
        #      dłuższe niż krótkie tagi z ollama.com/library i przy dwóch
        #      kolumnach obok siebie ("Pobierz nowy model" + "Zainstalowane
        #      modele") ucinały się w tabeli przy starej szerokości.
        self.setMinimumSize(960, 910)
        self._buduj_ui()

        # WHAT: cykliczne odświeżanie co 10 s.
        # WHY:  stan usługi/API może zmienić się poza aplikacją (terminal, reboot).
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.odswiez)
        self.timer.start(10000)
        self.odswiez()

    # --- Budowa interfejsu ---
    def _buduj_ui(self):
        # WHAT: pasek serwera + pasek statystyk na górze, pod nim 5 zakładek
        #       (Usługi / Modele / Agregator modeli / Zaawansowane / Pomoc),
        #       dziennik na dole na całą szerokość.
        # WHY:  finalny układ po trzech podejściach w Claude Design - zakładki
        #       trzymają wysokość okna w ryzach (jedna zakładka = jeden ekran),
        #       a pasek statystyk daje podgląd stanu bez klikania w ogóle.
        #       Kolory/ramki są natywne (Qt/Breeze, jasny/ciemny wg motywu systemu).
        # WHY self._zaawansowane_wczytane: formularz w zakładce "Zaawansowane"
        #      ma teraz JEDEN przycisk "Zapisz" zamiast osobnego zapisu na
        #      pole - żeby cykliczne odświeżanie co 10 s nie kasowało
        #      niezapisanych zmian w trakcie edycji, pola wypełniamy z
        #      rzeczywistego stanu TYLKO raz po (prze)budowaniu UI i zaraz po
        #      zapisaniu (patrz _odswiez_zakladke_zaawansowane/zapisz_zaawansowane).
        self._zaawansowane_wczytane = False
        centralny = QWidget()
        self.setCentralWidget(centralny)
        layout = QVBoxLayout(centralny)

        # === Pasek serwera ===============================================
        # WHY: przełącza, do którego hosta Ollama idą operacje na modelach
        #      (lista/pobierz/usuń/VRAM) - bez edycji stałej OLLAMA_URL.
        #      Sterowanie USŁUGĄ (start/stop/autostart) zawsze zostaje lokalne.
        pasek_serwer = QHBoxLayout()
        pasek_serwer.addWidget(QLabel(_("Serwer:")))
        self.combo_serwer = QComboBox()
        self.combo_serwer.currentIndexChanged.connect(self._zmien_serwer)
        pasek_serwer.addWidget(self.combo_serwer, 1)
        btn_zarzadzaj_serwerami = QPushButton(_("Zarządzaj serwerami..."))
        btn_zarzadzaj_serwerami.clicked.connect(self._zarzadzaj_serwerami)
        pasek_serwer.addWidget(btn_zarzadzaj_serwerami)
        # WHY: przełącznik języka obok serwerów - jedyne dwa "globalne" ustawienia
        #      okna, więc żyją w tym samym górnym pasku zamiast osobnego wiersza.
        pasek_serwer.addWidget(QLabel(_("Język:")))
        self.combo_jezyk = QComboBox()
        self.combo_jezyk.currentIndexChanged.connect(self._zmien_jezyk)
        pasek_serwer.addWidget(self.combo_jezyk)
        layout.addLayout(pasek_serwer)
        self._wypelnij_combo_serwer()
        self._wypelnij_combo_jezyk()

        # === Pasek statystyk ============================================
        pasek_staty = QHBoxLayout()
        kafelek, self.lbl_stat_ollama = self._kafelek_stat(_("OLLAMA"))
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_webui = self._kafelek_stat(_("WEBUI"))
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_vram_lokalnie = self._kafelek_stat(_("VRAM"))
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_modele = self._kafelek_stat(_("MODELE"))
        pasek_staty.addWidget(kafelek)
        pasek_staty.addStretch(1)
        layout.addLayout(pasek_staty)

        # === Zakładki ====================================================
        zakladki = QTabWidget()
        zakladki.addTab(self._zakladka_uslugi(), _("Usługi"))
        zakladki.addTab(self._zakladka_modele_lokalne(), _("Modele"))
        zakladki.addTab(self._zakladka_agregator(), _("Agregator modeli"))
        zakladki.addTab(self._zakladka_zaawansowane(), _("Zaawansowane"))
        zakladki.addTab(self._zakladka_pomoc(), _("Pomoc"))
        layout.addWidget(zakladki, 1)

        # === Dziennik - na dole, pod zakładkami =========================
        # WHY: log ma być widoczny bez względu na to, którą zakładkę oglądasz
        #      (np. postęp pobierania modelu widać, nawet patrząc na Usługi).
        karta_log = QGroupBox(_("Dziennik"))
        uk_log = QVBoxLayout(karta_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)  # WHY: nie rośnij w nieskończoność
        self.log.setMaximumHeight(120)  # WHY: dziennik to podgląd, nie ma zdominować okna
        uk_log.addWidget(self.log)
        layout.addWidget(karta_log)

    def _kafelek_stat(self, podpis):
        # WHAT: mały kafelek "podpis nad wartością" do paska statystyk.
        # WHY:  zwraca też etykietę wartości, żeby _po_odswiezeniu mogło ją podmieniać.
        widget = QWidget()
        v = QVBoxLayout(widget)
        v.setContentsMargins(8, 4, 8, 4)
        lbl_podpis = QLabel(podpis)
        czcionka = lbl_podpis.font()
        czcionka.setPointSize(max(7, czcionka.pointSize() - 2))
        lbl_podpis.setFont(czcionka)
        lbl_wartosc = QLabel("—")
        czcionka_w = lbl_wartosc.font()
        czcionka_w.setBold(True)
        czcionka_w.setPointSize(czcionka_w.pointSize() + 1)
        lbl_wartosc.setFont(czcionka_w)
        v.addWidget(lbl_podpis)
        v.addWidget(lbl_wartosc)
        return widget, lbl_wartosc

    def _naglowek_sekcji(self, tytul, widget_z_prawej=None):
        # WHAT: wspólny nagłówek sekcji - tytuł po lewej, opcjonalny status/przycisk z prawej.
        pasek = QHBoxLayout()
        etykieta = QLabel(tytul)
        czcionka = etykieta.font()
        czcionka.setBold(True)
        czcionka.setPointSize(czcionka.pointSize() + 1)
        etykieta.setFont(czcionka)
        pasek.addWidget(etykieta)
        pasek.addStretch(1)
        if widget_z_prawej is not None:
            pasek.addWidget(widget_z_prawej)
        return pasek

    def _zakladka_uslugi(self):
        # WHAT: dwie kolumny - lewa: Sterowanie i Open WebUI jedna pod drugą
        #       (obie "usługi w tle" tej aplikacji); prawa: Załadowane do VRAM
        #       na całą wysokość zakładki, jako pełnoprawne okno, nie mały podgląd.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        kolumny = QHBoxLayout()
        layout.addLayout(kolumny, 1)

        # --- Lewa kolumna: Ollama + Open WebUI, ta sama "ramka" i styl nagłówka ---
        lewa_kolumna = QVBoxLayout()
        kolumny.addLayout(lewa_kolumna, 1)

        karta_ollama = QGroupBox(_("Ollama"))
        uk_ollama = QVBoxLayout(karta_ollama)
        self.lbl_ollama_status = QLabel(_("sprawdzam..."))
        uk_ollama.addLayout(self._naglowek_sekcji(_("Usługa systemd"), self.lbl_ollama_status))
        pasek_przyciskow = QHBoxLayout()
        self.btn_start = QPushButton(_("Uruchom"))
        self.btn_start.clicked.connect(self.start_uslugi)
        self.btn_stop = QPushButton(_("Zatrzymaj"))
        self.btn_stop.clicked.connect(self.stop_uslugi)
        self.btn_instaluj = QPushButton(_("Zainstaluj Ollama"))
        self.btn_instaluj.setVisible(False)  # WHY: widoczny tylko gdy wykryjemy brak instalacji
        self.btn_instaluj.clicked.connect(self.zainstaluj_ollame)
        pasek_przyciskow.addWidget(self.btn_start)
        pasek_przyciskow.addWidget(self.btn_stop)
        pasek_przyciskow.addWidget(self.btn_instaluj)
        uk_ollama.addLayout(pasek_przyciskow)
        self.chk_autostart = QCheckBox(_("Uruchamiaj automatycznie przy starcie systemu"))
        self.chk_autostart.toggled.connect(self.przelacz_autostart)
        uk_ollama.addWidget(self.chk_autostart)

        # WHAT: osobny wiersz na sprawdzanie aktualizacji Ollamy + wynik ostatniego
        #       sprawdzenia (wersja lokalna / "dostępna nowsza").
        # WHY:  NA ŻĄDANIE (przycisk), nie w cyklicznym odświeżaniu co 10 s - tak
        #       samo jak podgląd modeli w Agregatorze (patrz CLAUDE.md) - zapytanie
        #       do GitHub przy każdym tyknięciu zegara byłoby zbędnym ruchem sieciowym.
        pasek_aktualizacji = QHBoxLayout()
        self.btn_sprawdz_aktualizacje = QPushButton(_("Sprawdź aktualizacje"))
        self.btn_sprawdz_aktualizacje.clicked.connect(self.sprawdz_aktualizacje_ollamy)
        pasek_aktualizacji.addWidget(self.btn_sprawdz_aktualizacje)
        self.lbl_wersja_ollama = QLabel("")
        pasek_aktualizacji.addWidget(self.lbl_wersja_ollama)
        pasek_aktualizacji.addStretch(1)
        uk_ollama.addLayout(pasek_aktualizacji)

        lewa_kolumna.addWidget(karta_ollama)

        # --- Open WebUI, w takiej samej ramce (QGroupBox) jak Sterowanie ---
        karta_webui = QGroupBox("Open WebUI")
        uk_webui = QVBoxLayout(karta_webui)
        self.lbl_webui_status = QLabel(_("sprawdzam..."))
        uk_webui.addLayout(self._naglowek_sekcji(_("Panel czatu w przeglądarce"), self.lbl_webui_status))
        pasek_webui = QHBoxLayout()
        self.btn_webui = QPushButton(_("sprawdzam..."))
        self.btn_webui.clicked.connect(self.klik_webui)
        self.btn_webui_stop = QPushButton(_("Zatrzymaj"))
        self.btn_webui_stop.setEnabled(False)  # WHY: aktywny dopiero gdy WebUI faktycznie działa
        self.btn_webui_stop.clicked.connect(self.zatrzymaj_webui)
        pasek_webui.addWidget(self.btn_webui)
        pasek_webui.addWidget(self.btn_webui_stop)
        uk_webui.addLayout(pasek_webui)
        # WHY: usługa --user, więc to osobny checkbox od autostartu Ollamy (systemowego)
        self.chk_webui_autostart = QCheckBox(_("Uruchamiaj automatycznie po zalogowaniu"))
        self.chk_webui_autostart.setEnabled(False)  # WHY: bez sensu, dopóki WebUI nie jest zainstalowane
        self.chk_webui_autostart.toggled.connect(self.przelacz_webui_autostart)
        uk_webui.addWidget(self.chk_webui_autostart)

        # WHAT: to samo co przy Ollamie wyżej - sprawdzanie aktualizacji NA
        #       ŻĄDANIE (przycisk), nie w cyklicznym odświeżaniu co 10 s.
        pasek_aktualizacji_webui = QHBoxLayout()
        self.btn_sprawdz_aktualizacje_webui = QPushButton(_("Sprawdź aktualizacje"))
        self.btn_sprawdz_aktualizacje_webui.clicked.connect(self.sprawdz_aktualizacje_webui)
        pasek_aktualizacji_webui.addWidget(self.btn_sprawdz_aktualizacje_webui)
        self.lbl_wersja_webui = QLabel("")
        pasek_aktualizacji_webui.addWidget(self.lbl_wersja_webui)
        pasek_aktualizacji_webui.addStretch(1)
        uk_webui.addLayout(pasek_aktualizacji_webui)

        lewa_kolumna.addWidget(karta_webui)

        lewa_kolumna.addStretch(1)  # WHY: karty trzymają się góry lewej kolumny

        # --- Prawa kolumna: Załadowane do pamięci (VRAM) ---
        karta_vram = QGroupBox(_("Załadowane do pamięci (VRAM)"))
        uk_vram = QVBoxLayout(karta_vram)
        self.lista_zaladowane = QListWidget()  # WHY: cała kolumna - niech się rozciąga, bez limitu wysokości
        uk_vram.addWidget(self.lista_zaladowane)
        kolumny.addWidget(karta_vram, 1)

        return strona

    def _zakladka_modele_lokalne(self):
        # WHAT: dwie kolumny - lewa: pobieranie nowych modeli; prawa:
        #       zainstalowane modele (lista + usuwanie) na całą wysokość zakładki.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        kolumny = QHBoxLayout()
        layout.addLayout(kolumny, 1)

        # --- Lewa kolumna: Pobierz nowy model ---
        # WHAT: JEDNA wyszukiwarka dla obu źródeł modeli naraz - biblioteki
        #       Ollamy (ollama.com) i Hugging Face (GGUF). Kroki: wpisz nazwę ->
        #       wybierz model z wyników (kolumna "Źródło" mówi, skąd jest) ->
        #       wybierz wariant (tag/kwantyzacja z PRAWDZIWYM rozmiarem pliku)
        #       -> Pobierz.
        # WHY:  na prośbę użytkownika - zastępuje dawną listę polecanych
        #       modeli, pola Rozmiar/Kwantyzacja (oparte na ręcznie utrzymywanych
        #       tabelach tagów) i osobny dialog HF; jedno miejsce do szukania
        #       zamiast trzech.
        karta_pobierz = QGroupBox(_("Pobierz nowy model"))
        uk_pobierz = QVBoxLayout(karta_pobierz)

        pasek_szukaj = QHBoxLayout()
        self.pole_szukaj_model = QLineEdit()
        self.pole_szukaj_model.setPlaceholderText(_("nazwa modelu, np. qwen2.5-coder"))
        self.pole_szukaj_model.returnPressed.connect(self.szukaj_modeli)
        self.pole_szukaj_model.textChanged.connect(self._aktualizuj_przycisk_pobierz)
        self.btn_szukaj_model = QPushButton(_("Szukaj"))
        self.btn_szukaj_model.clicked.connect(self.szukaj_modeli)
        pasek_szukaj.addWidget(self.pole_szukaj_model, 1)
        pasek_szukaj.addWidget(self.btn_szukaj_model)
        uk_pobierz.addLayout(pasek_szukaj)

        self.lbl_status_szukania = QLabel("")
        self.lbl_status_szukania.setWordWrap(True)
        uk_pobierz.addWidget(self.lbl_status_szukania)

        # WHAT: wyniki z obu źródeł - kolumna "Źródło" zawsze mówi, skąd jest
        #       model (wyraźna prośba użytkownika). Opis/możliwości/rozmiary
        #       modelu z Ollamy są w dymku po najechaniu na wiersz.
        self.tabela_wynikow = QTableWidget(0, 3)
        self.tabela_wynikow.setHorizontalHeaderLabels([_("Model"), _("Źródło"), _("Pobrania")])
        self._ustaw_tabele_wyboru(self.tabela_wynikow)
        self.tabela_wynikow.itemSelectionChanged.connect(self._wybrano_wynik_wyszukiwania)
        uk_pobierz.addWidget(self.tabela_wynikow, 1)

        self.tabela_wariantow = QTableWidget(0, 3)
        self.tabela_wariantow.setHorizontalHeaderLabels([_("Wariant"), _("Rozmiar"), _("Kontekst")])
        self._ustaw_tabele_wyboru(self.tabela_wariantow)
        self.tabela_wariantow.itemSelectionChanged.connect(self._wybrano_wariant)
        uk_pobierz.addWidget(self.tabela_wariantow, 1)

        pasek_pull = QHBoxLayout()
        self.lbl_do_pobrania = QLabel("")
        self.lbl_do_pobrania.setWordWrap(True)
        self.btn_pull = QPushButton(_("Pobierz"))
        self.btn_pull.setEnabled(False)  # WHY: aktywny dopiero po wyborze wariantu (albo wpisaniu dokładnej nazwy)
        self.btn_pull.clicked.connect(self.pobierz_model)
        pasek_pull.addWidget(self.lbl_do_pobrania, 1)
        pasek_pull.addWidget(self.btn_pull)
        uk_pobierz.addLayout(pasek_pull)

        # WHAT: orientacyjne zużycie pamięci dla WYBRANEGO wariantu - wagi to
        #       prawdziwy rozmiar pliku, reszta (KV cache, bufory) szacowana.
        self.lbl_szacowana_pamiec = QLabel("")
        self.lbl_szacowana_pamiec.setWordWrap(True)
        uk_pobierz.addWidget(self.lbl_szacowana_pamiec)

        # WHAT: skąd pochodzą wyniki + ostrzeżenie o HF + skrót dla znanej nazwy.
        # WHY:  HF to repozytoria SPOŁECZNOŚCIOWE, które może założyć każdy -
        #       appka/Ollama nie weryfikuje ich zawartości, więc dopisek jasno
        #       to mówi i daje konkretną wskazówkę zamiast ogólnikowego "uważaj".
        lbl_zrodla = QLabel(
            _('Źródła: <a href="https://ollama.com/library">ollama.com/library</a> (oficjalna '
              'biblioteka Ollamy) i <a href="https://huggingface.co/models?library=gguf">Hugging '
              'Face</a> (tylko modele GGUF). Hugging Face to repozytoria SPOŁECZNOŚCIOWE - każdy '
              'może tam coś opublikować, appka niczego nie weryfikuje. Pobieraj stamtąd tylko od '
              'znanych, zaufanych autorów kwantyzacji (np. bartowski, mradermacher, unsloth). '
              'Znasz dokładną nazwę (np. llama3.2:3b albo hf.co/...:Q4_K_M)? Wpisz ją i kliknij '
              'Pobierz bez szukania.')
        )
        lbl_zrodla.setWordWrap(True)
        lbl_zrodla.setOpenExternalLinks(True)
        uk_pobierz.addWidget(lbl_zrodla)

        self.pasek_postepu = QProgressBar()
        self.pasek_postepu.setVisible(False)
        uk_pobierz.addWidget(self.pasek_postepu)
        kolumny.addWidget(karta_pobierz, 1)

        # --- Prawa kolumna: Zainstalowane modele ---
        karta_zainstalowane = QGroupBox(_("Zainstalowane modele"))
        uk_zainstalowane = QVBoxLayout(karta_zainstalowane)
        self.btn_usun = QPushButton(_("Usuń zaznaczony"))
        self.btn_usun.setEnabled(False)  # WHY: aktywny dopiero po zaznaczeniu modelu
        self.btn_usun.clicked.connect(self.usun_model)
        pasek_usun = QHBoxLayout()
        pasek_usun.addStretch(1)
        pasek_usun.addWidget(self.btn_usun)
        uk_zainstalowane.addLayout(pasek_usun)
        # WHAT: tabela zamiast zwykłej listy - cztery kolumny: nazwa, źródło
        #       (Ollama/Hugging Face - patrz _zrodlo_modelu), rozmiar na
        #       dysku (dokładny, z /api/tags) i szacowane VRAM (patrz
        #       _po_odswiezeniu: realne zużycie, jeśli model jest akurat
        #       załadowany, w przeciwnym razie wyliczenie tym samym kalkulatorem
        #       co karta "Pobierz nowy model" - _oszacuj_pamiec_bpw).
        self.lista_modeli = QTableWidget(0, 4)
        self.lista_modeli.setHorizontalHeaderLabels([_("Model"), _("Źródło"), _("Rozmiar na dysku"), _("VRAM")])
        # WHY: TYLKO kolumna "Model" się rozciąga - dostaje całą wolną
        #      przestrzeń, żeby dłuższe nazwy (np. z Hugging Face, patrz
        #      _skroc_nazwe_modelu_hf) miały jak najwięcej miejsca zamiast się
        #      ucinać. "Źródło"/"Rozmiar na dysku"/"VRAM" to zawsze krótki tekst typu
        #      "4.4 GB" - ResizeToContents ciasno dopasowuje ich szerokość, nie
        #      marnując miejsca, które wcześniej zabierał im setStretchLastSection
        #      (kolumna "VRAM" jako ostatnia rozciągała się RÓWNIEŻ, walcząc
        #      o przestrzeń z kolumną "Model" zamiast zostawić jej całą resztę).
        self.lista_modeli.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.lista_modeli.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.lista_modeli.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.lista_modeli.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.lista_modeli.verticalHeader().setVisible(False)
        self.lista_modeli.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lista_modeli.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lista_modeli.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lista_modeli.itemSelectionChanged.connect(self._aktualizuj_przycisk_usun)
        uk_zainstalowane.addWidget(self.lista_modeli)
        kolumny.addWidget(karta_zainstalowane, 1)

        return strona

    def _zakladka_agregator(self):
        # WHAT: karta sterowania usługą LiteLLM (instalacja/start/stop/autostart)
        #       + karta z podglądem modeli, które trafią do jej configu.
        # WHY:  hosty biorą się WPROST z listy serwerów (pasek u góry, ten sam
        #       `self.serwery` co przełącznik) - żadnej drugiej listy hostów
        #       do ręcznego utrzymywania w dwóch miejscach.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        karta_litellm = QGroupBox("LiteLLM")
        uk_litellm = QVBoxLayout(karta_litellm)
        self.lbl_litellm_status = QLabel(_("sprawdzam..."))
        uk_litellm.addLayout(
            self._naglowek_sekcji(_("Jeden adres dla modeli z wielu serwerów Ollamy"), self.lbl_litellm_status)
        )
        opis_litellm = QLabel(
            _("Wystawia jeden endpoint (zgodny z API OpenAI), za którym LiteLLM "
              "kieruje zapytania do modeli na hostach z listy serwerów (pasek u "
              "góry okna). Dzięki temu np. VS Code/Continue może korzystać z "
              "modeli na kilku komputerach naraz, wskazując tylko na ten jeden adres.")
        )
        opis_litellm.setWordWrap(True)
        uk_litellm.addWidget(opis_litellm)
        # WHAT: adres, który trzeba wpisać w kliencie (VS Code/Continue itp.),
        #       zaznaczalny myszką do skopiowania - bez grzebania w kodzie po LITELLM_URL.
        # WHY:  to jedyne miejsce w oknie, gdzie ten adres jest w ogóle pokazany -
        #       bez niego użytkownik nie wie, gdzie właściwie podłączyć klienta.
        lbl_litellm_adres = QLabel(
            _("Adres dla klientów (np. Continue): <b>{adres}</b>").format(adres=f"{LITELLM_URL}/v1")
        )
        lbl_litellm_adres.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        uk_litellm.addWidget(lbl_litellm_adres)
        pasek_litellm = QHBoxLayout()
        self.btn_litellm = QPushButton(_("sprawdzam..."))
        self.btn_litellm.clicked.connect(self.klik_litellm)
        self.btn_litellm_stop = QPushButton(_("Zatrzymaj"))
        self.btn_litellm_stop.setEnabled(False)  # WHY: aktywny dopiero gdy LiteLLM faktycznie działa
        self.btn_litellm_stop.clicked.connect(self.zatrzymaj_litellm)
        pasek_litellm.addWidget(self.btn_litellm)
        pasek_litellm.addWidget(self.btn_litellm_stop)
        uk_litellm.addLayout(pasek_litellm)
        # WHY: usługa --user, więc osobny checkbox od autostartu Ollamy (systemowego)
        self.chk_litellm_autostart = QCheckBox(_("Uruchamiaj automatycznie po zalogowaniu"))
        self.chk_litellm_autostart.setEnabled(False)  # WHY: bez sensu, dopóki LiteLLM nie jest zainstalowane
        self.chk_litellm_autostart.toggled.connect(self.przelacz_litellm_autostart)
        uk_litellm.addWidget(self.chk_litellm_autostart)

        # WHAT: to samo co przy Ollamie/Open WebUI - sprawdzanie aktualizacji NA
        #       ŻĄDANIE (przycisk), nie w cyklicznym odświeżaniu co 10 s.
        pasek_aktualizacji_litellm = QHBoxLayout()
        self.btn_sprawdz_aktualizacje_litellm = QPushButton(_("Sprawdź aktualizacje"))
        self.btn_sprawdz_aktualizacje_litellm.clicked.connect(self.sprawdz_aktualizacje_litellm)
        pasek_aktualizacji_litellm.addWidget(self.btn_sprawdz_aktualizacje_litellm)
        self.lbl_wersja_litellm = QLabel("")
        pasek_aktualizacji_litellm.addWidget(self.lbl_wersja_litellm)
        pasek_aktualizacji_litellm.addStretch(1)
        uk_litellm.addLayout(pasek_aktualizacji_litellm)

        layout.addWidget(karta_litellm)

        karta_agregator = QGroupBox(_("Modele w agregatorze"))
        uk_agregator = QVBoxLayout(karta_agregator)
        pasek_agregator = QHBoxLayout()
        lbl_agregator = QLabel(
            _("Podgląd modeli i hostów, które trafią do configu LiteLLM - na "
              "podstawie listy serwerów u góry okna.")
        )
        lbl_agregator.setWordWrap(True)
        pasek_agregator.addWidget(lbl_agregator, 1)
        self.btn_odswiez_agregator = QPushButton(_("Odśwież listę"))
        self.btn_odswiez_agregator.clicked.connect(self.odswiez_liste_agregatora)
        pasek_agregator.addWidget(self.btn_odswiez_agregator)
        self.btn_config_continue = QPushButton(_("Config dla Continue..."))
        self.btn_config_continue.clicked.connect(self.generuj_config_continue)
        pasek_agregator.addWidget(self.btn_config_continue)
        self.btn_polaczenie_unsloth = QPushButton(_("Połączenie dla Unsloth Studio..."))
        self.btn_polaczenie_unsloth.clicked.connect(self.pokaz_polaczenie_unsloth)
        pasek_agregator.addWidget(self.btn_polaczenie_unsloth)
        uk_agregator.addLayout(pasek_agregator)
        self.lista_agregator = QListWidget()
        uk_agregator.addWidget(self.lista_agregator)
        layout.addWidget(karta_agregator, 1)

        return strona

    def _zakladka_zaawansowane(self):
        # WHAT: zmienne środowiskowe usługi Ollama - kompaktowy formularz
        #       "nazwa + pole/checkbox", bez opisów (te są w osobnej zakładce
        #       "Pomoc", patrz _zakladka_pomoc) i bez osobnego zapisu na każde
        #       pole - jeden przycisk "Zapisz" na dole stosuje WSZYSTKO naraz.
        # WHY:  osobna zakładka zamiast kolejnych pól w zakładce "Usługi" -
        #       to ustawienia dla kogoś, kto świadomie tuninguje sprzęt (np.
        #       BC-250 z ograniczonym VRAM), nie coś, co widać na pierwszy rzut
        #       oka. Wcześniejsza wersja miała osobny opis + przycisk "Zastosuj"
        #       przy KAŻDYM z 9 pól - zajmowało to mnóstwo miejsca (dużo
        #       przewijania) i każda zmiana osobno restartowała usługę. Jeden
        #       formularz + jeden zapis = jeden restart, nawet przy zmianie
        #       kilku pól naraz. Dotyczą WYŁĄCZNIE lokalnej Ollamy - tak jak
        #       reszta sterowania usługą.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        karta = QGroupBox(_("Ollama - zmienne środowiskowe usługi"))
        uk = QVBoxLayout(karta)
        lbl_wstep = QLabel(
            _("Opis każdej zmiennej: zakładka \"Pomoc\". \"Zapisz\" stosuje "
              "WSZYSTKIE pola naraz i restartuje usługę Ollama (wymaga uprawnień "
              "administratora - załadowane modele na chwilę znikną z pamięci). "
              "Puste pole = domyślne zachowanie Ollamy.")
        )
        lbl_wstep.setWordWrap(True)
        uk.addWidget(lbl_wstep)

        formularz = QFormLayout()

        self.pole_keep_alive = QLineEdit()
        self.pole_keep_alive.setPlaceholderText("np. 30m")
        formularz.addRow("OLLAMA_KEEP_ALIVE", self.pole_keep_alive)

        self.pole_context_length = QLineEdit()
        self.pole_context_length.setPlaceholderText("np. 16384")
        formularz.addRow("OLLAMA_CONTEXT_LENGTH", self.pole_context_length)

        self.pole_max_loaded = QLineEdit()
        self.pole_max_loaded.setPlaceholderText("np. 1")
        formularz.addRow("OLLAMA_MAX_LOADED_MODELS", self.pole_max_loaded)

        self.pole_num_parallel = QLineEdit()
        self.pole_num_parallel.setPlaceholderText("np. 1")
        formularz.addRow("OLLAMA_NUM_PARALLEL", self.pole_num_parallel)

        self.combo_flash_attention = QComboBox()
        for etykieta, wartosc in [(_("domyślne (auto)"), ""), (_("włączone"), "1"), (_("wyłączone"), "0")]:
            self.combo_flash_attention.addItem(etykieta, wartosc)
        formularz.addRow("OLLAMA_FLASH_ATTENTION", self.combo_flash_attention)

        self.combo_kv_cache = QComboBox()
        for etykieta, wartosc in [(_("domyślne (f16)"), ""), ("q8_0", "q8_0"), ("q4_0", "q4_0")]:
            self.combo_kv_cache.addItem(etykieta, wartosc)
        formularz.addRow("OLLAMA_KV_CACHE_TYPE", self.combo_kv_cache)

        self.chk_vulkan = QCheckBox()
        formularz.addRow("OLLAMA_VULKAN", self.chk_vulkan)

        self.chk_igpu = QCheckBox()
        formularz.addRow("OLLAMA_IGPU_ENABLE", self.chk_igpu)

        self.chk_host = QCheckBox()
        formularz.addRow("OLLAMA_HOST", self.chk_host)

        self.pole_ggml_vk_visible_devices = QLineEdit()
        self.pole_ggml_vk_visible_devices.setPlaceholderText("np. 0")
        formularz.addRow("GGML_VK_VISIBLE_DEVICES", self.pole_ggml_vk_visible_devices)

        # WHAT: ograniczony wybór (nie wolne pole) - wartość w bajtach zależy
        #       tylko od tego, czy na tym hoście działa środowisko graficzne.
        #       "Pulpit" jako PIERWSZA pozycja (domyślnie wybrana w świeżo
        #       zbudowanym formularzu) - appka ma GUI, więc najczęściej i tak
        #       działa na maszynie z pulpitem, nie na czystym serwerze bez X.
        #       Realny stan wczytany z override.conf (patrz
        #       _odswiez_zakladke_zaawansowane) nadpisuje to i tak, jeśli
        #       zmienna była już kiedyś świadomie ustawiona.
        # WHY:  serwer bez X (np. BC-250 jako czysty backend) nie potrzebuje
        #       żadnej rezerwacji; stacja robocza z pulpitem (GNOME/KDE) dzieli
        #       tę samą pulę VRAM z kompozytorem, więc bez rezerwacji Ollama
        #       mogłaby zająć całość i zdusić pulpit. 500 MB (dokładnie 512 MiB)
        #       to bezpieczny, orientacyjny punkt startu - sam kompozytor/sterownik
        #       i tak rezerwuje coś jeszcze OSOBNO, poza kontrolą appki (patrz
        #       rozmowa o OLLAMA_GPU_OVERHEAD vs. rezerwacja systemowa/sterownika) -
        #       do korekty przez pole "własne parametry" niżej, jeśli 500 MB
        #       okaże się za mało/za dużo w praktyce.
        # WHY jawne "0" zamiast pustego: obie opcje muszą dawać się rozróżnić
        #     po zapisie i przeładowaniu (tak jak Vulkan/iGPU wcześniej) - gdyby
        #     "serwer" zapisywał puste (czyli w ogóle nie dopisywał klucza), nie
        #     dałoby się potem odróżnić "świadomie wybrał serwer" od "nigdy nie
        #     dotknął tego pola", i po każdym przeładowaniu wracałoby do "pulpit".
        self.combo_gpu_overhead = QComboBox()
        for etykieta, wartosc in [
            (_("pulpit (GNOME/KDE)"), "536870912"),
            (_("serwer (bez X-ów)"), "0"),
        ]:
            self.combo_gpu_overhead.addItem(etykieta, wartosc)
        formularz.addRow("OLLAMA_GPU_OVERHEAD", self.combo_gpu_overhead)

        uk.addLayout(formularz)

        lbl_wlasne = QLabel(_("Własne parametry (jeden na linię, KLUCZ=WARTOŚĆ):"))
        uk.addWidget(lbl_wlasne)
        self.pole_wlasne_zmienne = QPlainTextEdit()
        self.pole_wlasne_zmienne.setPlaceholderText("OLLAMA_...=...")
        self.pole_wlasne_zmienne.setMaximumHeight(100)
        uk.addWidget(self.pole_wlasne_zmienne)

        pasek_przyciskow = QHBoxLayout()
        self.btn_zalecane_zaawansowane = QPushButton(_("Zastosuj zalecane wartości"))
        self.btn_zalecane_zaawansowane.clicked.connect(self.zastosuj_zalecane_zaawansowane)
        pasek_przyciskow.addWidget(self.btn_zalecane_zaawansowane)
        pasek_przyciskow.addStretch(1)
        self.btn_zapisz_zaawansowane = QPushButton(_("Zapisz"))
        self.btn_zapisz_zaawansowane.clicked.connect(self.zapisz_zaawansowane)
        pasek_przyciskow.addWidget(self.btn_zapisz_zaawansowane)
        uk.addLayout(pasek_przyciskow)

        layout.addWidget(karta)
        layout.addStretch(1)

        przewijanie = QScrollArea()
        przewijanie.setWidget(strona)
        przewijanie.setWidgetResizable(True)
        przewijanie.setFrameShape(QScrollArea.Shape.NoFrame)  # WHY: bez podwójnej ramki (QScrollArea + karta)
        return przewijanie

    def _zakladka_pomoc(self):
        # WHAT: pełne opisy zmiennych środowiskowych Ollamy z zakładki
        #       "Zaawansowane" - wydzielone tutaj, żeby tamta zakładka mogła
        #       być krótkim formularzem (nazwa + pole), bez ściany tekstu.
        # WHY:  9 opisów naraz w jednej zakładce z polami wymuszało mnóstwo
        #       przewijania - rozdzielenie "co ustawić" (Zaawansowane) od
        #       "co to znaczy" (tu) trzyma obie zakładki czytelne.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        karta = QGroupBox(_("Opis zmiennych środowiskowych Ollamy"))
        uk = QVBoxLayout(karta)

        opisy = [
            ("OLLAMA_KEEP_ALIVE", _("Jak długo model zostaje w pamięci po ostatnim zapytaniu, zanim zostanie "
                                     "wyładowany (domyślnie kilka minut). np. 30m, 1h, -1 (zawsze), 0 (od razu).")),
            ("OLLAMA_CONTEXT_LENGTH", _("Rozmiar okna kontekstu w tokenach (domyślnie 4096 - za mało do pracy "
                                         "agentowej w Continue/OpenCode).")),
            ("OLLAMA_MAX_LOADED_MODELS", _("Ile modeli może być jednocześnie załadowanych do pamięci (domyślnie "
                                            "3x liczba GPU).")),
            ("OLLAMA_NUM_PARALLEL", _("Ile równoległych zapytań obsłuży jeden załadowany model naraz.")),
            ("OLLAMA_FLASH_ATTENTION", _("Zmniejsza zużycie pamięci przy dłuższym kontekście, jeśli model i "
                                          "sprzęt to wspierają.")),
            ("OLLAMA_KV_CACHE_TYPE", _("Kwantyzacja pamięci podręcznej kontekstu - q8_0 to ok. -50% zużycia "
                                        "VRAM przy pomijalnej stracie jakości.")),
            ("OLLAMA_VULKAN", _("Backend Vulkan zamiast ROCm - szersza kompatybilność z kartami AMD "
                                 "bez pełnego wsparcia ROCm (np. BC-250).")),
            ("OLLAMA_IGPU_ENABLE", _("Czy Ollama może korzystać ze zintegrowanego GPU (iGPU) - domyślnie "
                                      "włączone. Wyłącz (w zakładce Zaawansowane), żeby wymusić pominięcie iGPU "
                                      "(np. przy problemach na jednolitej architekturze CPU+GPU jak BC-250).")),
            ("OLLAMA_HOST", _("Domyślnie Ollama nasłuchuje tylko lokalnie (127.0.0.1) - inne urządzenia "
                               "w sieci nie mogą się połączyć. Włącz (w zakładce Zaawansowane), żeby "
                               "nasłuchiwała na wszystkich interfejsach (0.0.0.0) i była dostępna spoza tego "
                               "komputera (np. z innego PC albo dla BC-250 pracującego jako klient).")),
            ("GGML_VK_VISIBLE_DEVICES", _("Który indeks urządzenia Vulkan ma być widoczny dla backendu "
                                           "(0 = pierwsze) - niezbędne na niektórych APU (np. BC-250), gdzie "
                                           "Vulkan wylicza kilka urządzeń naraz i bez tego Ollama może wybrać "
                                           "złe. Działa tylko razem z włączonym OLLAMA_VULKAN.")),
            ("OLLAMA_GPU_OVERHEAD", _("Ile pamięci GPU (w bajtach) zarezerwować z dala od Ollamy, dla reszty "
                                       "systemu - istotne na APU z pamięcią współdzieloną z systemem RAM. "
                                       "Serwer bez środowiska graficznego zwykle nie potrzebuje rezerwacji; "
                                       "stacja robocza z pulpitem (KDE/GNOME) dzieli tę samą pulę VRAM z "
                                       "kompozytorem, więc bez rezerwacji Ollama mogłaby zająć całość.")),
        ]
        for nazwa, opis in opisy:
            lbl_nazwa = QLabel(nazwa)
            czcionka = lbl_nazwa.font()
            czcionka.setBold(True)
            lbl_nazwa.setFont(czcionka)
            uk.addWidget(lbl_nazwa)
            lbl_opis = QLabel(opis)
            lbl_opis.setWordWrap(True)
            uk.addWidget(lbl_opis)

        layout.addWidget(karta)

        # WHAT: skąd biorą się modele w wyszukiwarce na karcie "Pobierz nowy
        #       model" i dlaczego część wariantów się nie pokazuje.
        # WHY:  bez tego pominięte warianty (MLX, cloud) wyglądałyby jak awaria
        #       appki, a różnica w zaufaniu między źródłami - jak detal.
        karta_zrodla = QGroupBox(_("Skąd pochodzą modele"))
        uk_zrodla = QVBoxLayout(karta_zrodla)
        zrodla = [
            ("Ollama", _("Oficjalna biblioteka Ollamy (ollama.com/library). Wyszukiwarka pokazuje "
                         "wszystkie warianty modelu (tagi) z prawdziwym rozmiarem pliku. Pomija warianty "
                         "MLX (działają tylko na komputerach Apple) i \"cloud\" (liczone na serwerach "
                         "Ollamy, nie do pobrania).")),
            ("Hugging Face", _("Repozytoria społecznościowe - tylko modele w formacie GGUF, bo innych "
                               "Ollama nie pobierze. Każdy może tam coś opublikować, appka niczego nie "
                               "weryfikuje - wybieraj znanych autorów kwantyzacji (np. bartowski, "
                               "mradermacher, unsloth).")),
        ]
        for nazwa, opis in zrodla:
            lbl_nazwa = QLabel(nazwa)
            czcionka = lbl_nazwa.font()
            czcionka.setBold(True)
            lbl_nazwa.setFont(czcionka)
            uk_zrodla.addWidget(lbl_nazwa)
            lbl_opis = QLabel(opis)
            lbl_opis.setWordWrap(True)
            uk_zrodla.addWidget(lbl_opis)
        layout.addWidget(karta_zrodla)

        layout.addStretch(1)

        przewijanie = QScrollArea()
        przewijanie.setWidget(strona)
        przewijanie.setWidgetResizable(True)
        przewijanie.setFrameShape(QScrollArea.Shape.NoFrame)
        return przewijanie

    # --- Przełącznik serwera ---
    def _wypelnij_combo_serwer(self):
        # WHY: blokujemy sygnały na czas wypełniania - inaczej samo dodawanie
        #      pozycji odpaliłoby _zmien_serwer() i niepotrzebny wpis w logu.
        self.combo_serwer.blockSignals(True)
        self.combo_serwer.clear()
        aktywny_index = 0
        for i, s in enumerate(self.serwery):
            self.combo_serwer.addItem(f"{s['nazwa']} ({s['adres']})", s["adres"])
            if s["adres"] == self._serwer_aktywny_adres:
                aktywny_index = i
        self.combo_serwer.setCurrentIndex(aktywny_index)
        self.combo_serwer.blockSignals(False)

    def _zmien_serwer(self, index):
        if index < 0:
            return
        adres = self.combo_serwer.itemData(index)
        if not adres or adres == self.client.base_url:
            return
        self.client.base_url = adres
        self._serwer_aktywny_adres = adres
        _zapisz_serwer_aktywny(adres)
        self.wpis_log(_("Przełączono na serwer: {serwer}").format(serwer=self.combo_serwer.itemText(index)))
        self.odswiez()

    def _zarzadzaj_serwerami(self):
        dialog = DialogZarzadzajSerwerami(self.serwery, self)
        dialog.exec()
        self.serwery = dialog.serwery
        _zapisz_serwery(self.serwery)
        # WHY: jeśli aktywny adres zniknął z listy (usunięty w dialogu), wracamy na pierwszy.
        if self._serwer_aktywny_adres not in [s["adres"] for s in self.serwery]:
            self._serwer_aktywny_adres = self.serwery[0]["adres"]
            self.client.base_url = self._serwer_aktywny_adres
            _zapisz_serwer_aktywny(self._serwer_aktywny_adres)
        self._wypelnij_combo_serwer()
        self.odswiez()

    # --- Przełącznik języka ---
    def _wypelnij_combo_jezyk(self):
        self.combo_jezyk.blockSignals(True)
        self.combo_jezyk.clear()
        for i, (kod, nazwa) in enumerate(JEZYKI.items()):
            self.combo_jezyk.addItem(nazwa, kod)
            if kod == self._jezyk_aktywny:
                self.combo_jezyk.setCurrentIndex(i)
        self.combo_jezyk.blockSignals(False)

    def _zmien_jezyk(self, index):
        # WHY: przebudowujemy CAŁE okno tą samą funkcją co przy starcie
        #      (_buduj_ui) - żywa zmiana języka bez restartu aplikacji, bo
        #      widgety i tak zawsze budujemy od zera z aktualnych danych
        #      (self.serwery, self._serwer_aktywny_adres, ...).
        kod = self.combo_jezyk.itemData(index)
        if not kod or kod == self._jezyk_aktywny:
            return
        self._jezyk_aktywny = kod
        _wczytaj_jezyk(kod)
        _zapisz_jezyk(kod)
        self._buduj_ui()
        self.odswiez()

    # --- Pomocnicze ---
    def wpis_log(self, tekst):
        self.log.appendPlainText(tekst)

    def _uruchom_akcje(self, funkcja, opis):
        # WHAT: uruchamia akcję w tle i po zakończeniu loguje wynik + odświeża.
        # WHY:  wspólna droga dla start/stop/autostart/usuwania - jeden mechanizm.
        worker = ActionWorker(funkcja, opis)
        self._workers.append(worker)

        def _sprzatnij(sukces, komunikat, w=worker):
            self.wpis_log(komunikat)
            if w in self._workers:
                self._workers.remove(w)
            # WHY: krótkie opóźnienie - stan usługi ustala się chwilę po komendzie.
            QTimer.singleShot(1200, self.odswiez)

        worker.zakonczono.connect(_sprzatnij)
        worker.start()
        return worker

    # --- Odświeżanie stanu ---
    def odswiez(self):
        if self.refresh_worker and self.refresh_worker.isRunning():
            return
        self.refresh_worker = RefreshWorker(self.client)
        self.refresh_worker.wynik.connect(self._po_odswiezeniu)
        self.refresh_worker.start()

    def _ustaw_combo_wg_wartosci(self, combo, wartosc):
        indeks = combo.findData(wartosc)
        combo.setCurrentIndex(indeks if indeks >= 0 else 0)

    def _odswiez_zakladke_zaawansowane(self, stan):
        # WHAT: blokuje formularz, gdy Ollama nie jest zainstalowana (restart
        #       nie ma sensu) - to dzieje się przy KAŻDYM odświeżeniu (co 10 s).
        #       Rzeczywisty stan override.conf wczytujemy do pól TYLKO raz
        #       (patrz self._zaawansowane_wczytane, ustawiane w _buduj_ui i
        #       zerowane w zapisz_zaawansowane) - inaczej cykliczne odświeżanie
        #       nadpisywałoby to, co user właśnie wpisuje, zanim zdąży kliknąć
        #       "Zapisz".
        for widget in (
            self.pole_keep_alive, self.pole_context_length, self.pole_max_loaded,
            self.pole_num_parallel, self.combo_flash_attention, self.combo_kv_cache,
            self.chk_vulkan, self.chk_igpu, self.chk_host, self.pole_ggml_vk_visible_devices,
            self.combo_gpu_overhead,
            self.pole_wlasne_zmienne, self.btn_zalecane_zaawansowane, self.btn_zapisz_zaawansowane,
        ):
            widget.setEnabled(stan["zainstalowana"])

        if self._zaawansowane_wczytane:
            return
        self._zaawansowane_wczytane = True

        env = stan["env_ollama"]
        self.pole_keep_alive.setText(env.get("OLLAMA_KEEP_ALIVE", ""))
        self.pole_context_length.setText(env.get("OLLAMA_CONTEXT_LENGTH", ""))
        self.pole_max_loaded.setText(env.get("OLLAMA_MAX_LOADED_MODELS", ""))
        self.pole_num_parallel.setText(env.get("OLLAMA_NUM_PARALLEL", ""))
        self._ustaw_combo_wg_wartosci(self.combo_flash_attention, env.get("OLLAMA_FLASH_ATTENTION", ""))
        self._ustaw_combo_wg_wartosci(self.combo_kv_cache, env.get("OLLAMA_KV_CACHE_TYPE", ""))
        self.chk_vulkan.setChecked(env.get("OLLAMA_VULKAN") == "1")
        # WHY: IGPU_ENABLE domyślnie WŁĄCZONE - checkbox zaznaczony, dopóki nie
        #      ma jawnego wyłączenia. Akceptujemy zarówno "0" (nowy, jawny
        #      zapis - patrz zapisz_zaawansowane) jak i "false" (stary format
        #      sprzed przebudowy tej zakładki na jeden formularz).
        self.chk_igpu.setChecked(env.get("OLLAMA_IGPU_ENABLE") not in ("0", "false"))
        self.chk_host.setChecked(bool(env.get("OLLAMA_HOST")))
        self.pole_ggml_vk_visible_devices.setText(env.get("GGML_VK_VISIBLE_DEVICES", ""))
        self._ustaw_combo_wg_wartosci(self.combo_gpu_overhead, env.get("OLLAMA_GPU_OVERHEAD", ""))

        # WHY: pola/checkboxy wyżej pokrywają "znane" zmienne (OLLAMA_ZNANE_ZMIENNE) -
        #      wszystko inne, co siedzi w override.conf (np. dopisane ręcznie
        #      kiedyś przez systemctl edit), ląduje w polu "własne parametry",
        #      żeby formularz niczego nie gubił przy następnym zapisie.
        wlasne = [f"{k}={v}" for k, v in env.items() if k not in OLLAMA_ZNANE_ZMIENNE]
        self.pole_wlasne_zmienne.setPlainText("\n".join(wlasne))

    def _po_odswiezeniu(self, stan):
        # WHAT: przełóż stan usługi/API na wygląd okna.
        # WHY: ten sam wzorzec statusu instalacji co przy karcie Open WebUI -
        #      spójny styl obu kart w zakładce "Usługi".
        self.lbl_ollama_status.setText(
            _("zainstalowana ✓") if stan["zainstalowana"] else _("nie zainstalowana ✗")
        )
        if not stan["zainstalowana"]:
            # WHY: bez binarki 'ollama' sterowanie usługą nie ma sensu - chowamy
            #      Start/Stop/autostart i pokazujemy tylko przycisk instalacji.
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.chk_autostart.setEnabled(False)
            self.btn_instaluj.setVisible(True)
            instalacja_w_toku = bool(self._instalacja_worker and self._instalacja_worker.isRunning())
            self.btn_instaluj.setEnabled(not instalacja_w_toku)
            self.btn_sprawdz_aktualizacje.setEnabled(False)  # WHY: nie ma czego sprawdzać bez instalacji
        else:
            self.btn_instaluj.setVisible(False)
            self.chk_autostart.setEnabled(True)

            active = stan["active"]
            self.btn_start.setEnabled(not active)  # nie startuj czegoś, co już działa
            self.btn_stop.setEnabled(active)

            # WHY: blokujemy sygnały, żeby programowe ustawienie checkboxa
            #      nie wywołało przypadkiem enable/disable.
            self.chk_autostart.blockSignals(True)
            self.chk_autostart.setChecked(stan["enabled"])
            self.chk_autostart.blockSignals(False)

            aktualizacja_w_toku = bool(self._aktualizacja_worker and self._aktualizacja_worker.isRunning())
            self.btn_sprawdz_aktualizacje.setEnabled(not aktualizacja_w_toku)

        self._odswiez_zakladke_zaawansowane(stan)

        # Odśwież tabelę modeli, zachowując zaznaczenie jeśli się da
        # WHY: kolumna "VRAM" pokazuje REALNE zużycie (z /api/ps), jeśli model
        #      jest akurat załadowany, albo orientacyjne wyliczenie tym samym
        #      kalkulatorem co karta "Pobierz nowy model" - na podstawie
        #      prawdziwych details.parameter_size/quantization_level z
        #      /api/tags, nie zgadywania rozmiaru z nazwy.
        vram_wg_nazwy = {m.get("name", "?"): m.get("size_vram", 0) for m in stan["zaladowane"]}
        zaznaczony = self._wybrany_model()
        self.lista_modeli.setRowCount(0)
        for m in stan["models"]:
            nazwa = m["name"]
            rozmiar_gb = m.get("size", 0) / (1024 ** 3)

            if nazwa in vram_wg_nazwy:
                vram_tekst = _("{gb:.1f} GB (załadowany)").format(gb=vram_wg_nazwy[nazwa] / (1024 ** 3))
            else:
                miliardy = _parsuj_rozmiar_parametrow(m.get("parameter_size", ""))
                if miliardy is None:
                    vram_tekst = "—"
                else:
                    bpw = _QUANTIZATION_LEVEL_BPW.get(
                        (m.get("quantization_level") or "").upper(), _BPW_DOMYSLNE
                    )
                    oszacowanie = _oszacuj_pamiec_bpw(miliardy, bpw, "")
                    vram_tekst = _("~{gb:.1f} GB").format(gb=oszacowanie["zajete_realnie_gb"])

            wiersz = self.lista_modeli.rowCount()
            self.lista_modeli.insertRow(wiersz)
            pozycja_nazwa = QTableWidgetItem(_skroc_nazwe_modelu_hf(nazwa))
            # WHY: prawdziwa nazwa (potrzebna np. do usuwania modelu przez API)
            #      leci jako dane wiersza, niezależnie od tego, co się WYŚWIETLA -
            #      patrz _wybrany_model().
            pozycja_nazwa.setData(Qt.ItemDataRole.UserRole, nazwa)
            # WHAT: dymek po najechaniu na wiersz - skąd model pochodzi, kto
            #       jest autorem kwantyzacji (dla HF), rodzina, parametry itd.
            # WHY:  na wyraźną prośbę użytkownika - prościej niż osobny panel
            #       ze szczegółami (patrz _zbuduj_tooltip_modelu); dodatkowo
            #       pokazuje PEŁNĄ nazwę, gdyby skrócona (patrz wyżej) mimo
            #       rozciągania kolumny wciąż się nie mieściła.
            tooltip = _zbuduj_tooltip_modelu(m)
            pozycja_nazwa.setToolTip(tooltip)
            self.lista_modeli.setItem(wiersz, 0, pozycja_nazwa)
            for kolumna, tekst in enumerate(
                (_zrodlo_modelu(nazwa), f"{rozmiar_gb:.1f} GB", vram_tekst), start=1
            ):
                pozycja = QTableWidgetItem(tekst)
                pozycja.setToolTip(tooltip)
                self.lista_modeli.setItem(wiersz, kolumna, pozycja)
        if zaznaczony:
            for i in range(self.lista_modeli.rowCount()):
                if self.lista_modeli.item(i, 0).data(Qt.ItemDataRole.UserRole) == zaznaczony:
                    self.lista_modeli.setCurrentCell(i, 0)
                    break

        # Odśwież listę modeli załadowanych do pamięci (VRAM)
        self.lista_zaladowane.clear()
        for m in stan["zaladowane"]:
            nazwa = m.get("name", "?")
            vram_gb = m.get("size_vram", 0) / (1024 ** 3)
            self.lista_zaladowane.addItem(
                f"{_skroc_nazwe_modelu_hf(nazwa)}  —  {_zrodlo_modelu(nazwa)}  —  {vram_gb:.1f} GB VRAM"
            )

        # WHAT: jeden przycisk, trzy znaczenia - "Zainstaluj" / "Uruchom" / "Otwórz",
        #       zależnie od tego, czy binarka jest zainstalowana i czy WebUI
        #       już odpowiada. Drugi przycisk ("Zatrzymaj") aktywny tylko gdy działa.
        self._webui_zainstalowane = stan["webui"]
        self._webui_dziala = stan["webui_dziala"]
        self.lbl_webui_status.setText(_("zainstalowane ✓") if stan["webui"] else _("nie zainstalowane ✗"))
        webui_w_toku = bool(self._webui_worker and self._webui_worker.isRunning())
        if webui_w_toku:
            self.btn_webui.setEnabled(False)
            self.btn_webui_stop.setEnabled(False)
        else:
            if not stan["webui"]:
                self.btn_webui.setText(_("Zainstaluj WebUI"))
            elif stan["webui_dziala"]:
                self.btn_webui.setText(_("Otwórz WebUI"))
            else:
                self.btn_webui.setText(_("Uruchom WebUI"))
            self.btn_webui.setEnabled(True)
            self.btn_webui_stop.setEnabled(stan["webui"] and stan["webui_dziala"])

        # WHY: bez sensu włączać autostart czegoś, co nie jest zainstalowane;
        #      blokujemy sygnały jak przy chk_autostart, żeby nie odpalić
        #      przypadkiem enable/disable przy samym odświeżeniu.
        self.chk_webui_autostart.setEnabled(stan["webui"])
        self.chk_webui_autostart.blockSignals(True)
        self.chk_webui_autostart.setChecked(stan["webui_autostart"])
        self.chk_webui_autostart.blockSignals(False)

        # WHY: jak przy Ollamie - bez sensu sprawdzać aktualizacje czegoś, co
        #      nie jest zainstalowane, ani odpalać sprawdzenie 2x naraz.
        aktualizacja_webui_w_toku = bool(
            self._aktualizacja_webui_worker and self._aktualizacja_webui_worker.isRunning()
        )
        self.btn_sprawdz_aktualizacje_webui.setEnabled(stan["webui"] and not aktualizacja_webui_w_toku)

        # WHAT: jeden przycisk, dwa znaczenia - "Zainstaluj" / "Uruchom" - w
        #       przeciwieństwie do WebUI nie ma tu trzeciego stanu "Otwórz",
        #       bo LiteLLM to sam endpoint API, nie strona do oglądania.
        self._litellm_zainstalowany = stan["litellm"]
        self.lbl_litellm_status.setText(_("zainstalowane ✓") if stan["litellm"] else _("nie zainstalowane ✗"))
        litellm_w_toku = bool(self._litellm_worker and self._litellm_worker.isRunning())
        if litellm_w_toku:
            self.btn_litellm.setEnabled(False)
            self.btn_litellm_stop.setEnabled(False)
        else:
            if not stan["litellm"]:
                self.btn_litellm.setText(_("Zainstaluj LiteLLM"))
            elif stan["litellm_dziala"]:
                self.btn_litellm.setText(_("Uruchom ponownie"))
            else:
                self.btn_litellm.setText(_("Uruchom LiteLLM"))
            self.btn_litellm.setEnabled(True)
            self.btn_litellm_stop.setEnabled(stan["litellm"] and stan["litellm_dziala"])

        self.chk_litellm_autostart.setEnabled(stan["litellm"])
        self.chk_litellm_autostart.blockSignals(True)
        self.chk_litellm_autostart.setChecked(stan["litellm_autostart"])
        self.chk_litellm_autostart.blockSignals(False)

        # WHY: jak przy Ollamie/Open WebUI wyżej - bez sensu sprawdzać
        #      aktualizacje czegoś, co nie jest zainstalowane, ani odpalać
        #      sprawdzenie 2x naraz.
        aktualizacja_litellm_w_toku = bool(
            self._aktualizacja_litellm_worker and self._aktualizacja_litellm_worker.isRunning()
        )
        self.btn_sprawdz_aktualizacje_litellm.setEnabled(stan["litellm"] and not aktualizacja_litellm_w_toku)

        # === Pasek statystyk ============================================
        # WHY: to jedyne miejsce w oknie ze statusem Ollamy - usunięty osobny
        #      pill w zakładce "Usługi", żeby nie dublować tej samej informacji.
        if not stan["zainstalowana"]:
            self.lbl_stat_ollama.setText(_("nie zainstalowana ✗"))
        elif stan["active"]:
            self.lbl_stat_ollama.setText(_("działa ✓"))
        elif stan["api"]:
            self.lbl_stat_ollama.setText("API")
        else:
            self.lbl_stat_ollama.setText(_("zatrzymana ✗"))

        self.lbl_stat_webui.setText(_("działa ✓") if stan["webui_dziala"] else _("zatrzymane ✗"))

        vram_lokalnie_gb = sum(m.get("size_vram", 0) for m in stan["zaladowane"]) / (1024 ** 3)
        self.lbl_stat_vram_lokalnie.setText(f"{vram_lokalnie_gb:.1f} GB ({len(stan['zaladowane'])})")

        self.lbl_stat_modele.setText(str(len(stan["models"])))

    # --- Instalacja ---
    def zainstaluj_ollame(self):
        if self._instalacja_worker and self._instalacja_worker.isRunning():
            self.wpis_log(_("Instalacja już trwa - poczekaj na zakończenie."))
            return
        # WHY: pobieranie i uruchamianie skryptu z internetu jako root - wymagamy
        #      świadomej zgody, tak jak przy usuwaniu modelu.
        odp = QMessageBox.question(
            self, _("Zainstaluj Ollama"),
            _("Zainstalować Ollamę teraz?\n\n"
              "Pobierze i uruchomi oficjalny skrypt instalacyjny z ollama.com\n"
              "(wymaga uprawnień administratora i połączenia z internetem)."),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Instaluję Ollamę - to może potrwać kilka minut..."))
        self.btn_instaluj.setEnabled(False)
        self._instalacja_worker = self._uruchom_akcje(ollama_zainstaluj, _("Instalacja Ollamy"))

    def sprawdz_aktualizacje_ollamy(self):
        # WHAT: pyta lokalną binarkę o zainstalowaną wersję i GitHub o najnowszą,
        #       w tle (SprawdzAktualizacjeWorker), i - jeśli jest nowsza - proponuje
        #       aktualizację tym samym skryptem, którym instaluje się Ollama.
        # WHY:  po wyjściu nowej wersji Ollamy część modeli bywa spakowana w
        #       formacie, którego starsza Ollama nie obsłuży ("model requires
        #       newer Ollama") - stąd potrzeba szybkiego sprawdzenia z okna,
        #       zamiast dowiadywania się o tym z błędu przy pobieraniu.
        if self._aktualizacja_worker and self._aktualizacja_worker.isRunning():
            self.wpis_log(_("Sprawdzanie aktualizacji już trwa - poczekaj na zakończenie."))
            return
        self.wpis_log(_("Sprawdzam dostępność aktualizacji Ollamy..."))
        self.btn_sprawdz_aktualizacje.setEnabled(False)
        worker = SprawdzAktualizacjeWorker(ollama_wersja_lokalna, ollama_wersja_najnowsza)
        self._workers.append(worker)

        def _po_sprawdzeniu(wynik, w=worker):
            if w in self._workers:
                self._workers.remove(w)
            self.btn_sprawdz_aktualizacje.setEnabled(True)
            self._pokaz_wynik_sprawdzenia_aktualizacji(wynik)

        worker.wynik.connect(_po_sprawdzeniu)
        self._aktualizacja_worker = worker
        worker.start()

    def _pokaz_wynik_sprawdzenia_aktualizacji(self, wynik):
        # WHAT: loguje wynik sprawdzenia i - gdy jest nowsza wersja - pyta,
        #       czy zainstalować ją teraz (świadoma zgoda, tak jak przy
        #       pierwszej instalacji - to znowu pkexec + skrypt z internetu).
        if wynik["lokalna"]:
            self.lbl_wersja_ollama.setText(_("wersja: {wersja}").format(wersja=wynik["lokalna"]))
        if wynik["blad"]:
            self.wpis_log(_("Nie udało się sprawdzić aktualizacji Ollamy: {blad}").format(blad=wynik["blad"]))
            return
        if not wynik["dostepna"]:
            self.wpis_log(_("Masz najnowszą wersję Ollamy ({wersja}).").format(wersja=wynik["lokalna"]))
            return
        self.wpis_log(_("Dostępna nowa wersja Ollamy: {najnowsza} (masz {lokalna}).").format(
            najnowsza=wynik["najnowsza"], lokalna=wynik["lokalna"]))
        odp = QMessageBox.question(
            self, _("Aktualizacja Ollamy"),
            _("Dostępna jest nowsza wersja Ollamy: {najnowsza}\n"
              "Masz zainstalowaną: {lokalna}\n\n"
              "Niektóre modele wymagają nowszej wersji Ollamy, żeby działać "
              "poprawnie - warto aktualizować.\n\n"
              "Zainstalować aktualizację teraz?\n\n"
              "Pobierze i uruchomi oficjalny skrypt instalacyjny z ollama.com\n"
              "(wymaga uprawnień administratora i połączenia z internetem).").format(
                najnowsza=wynik["najnowsza"], lokalna=wynik["lokalna"]),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Aktualizuję Ollamę - to może potrwać kilka minut..."))
        # WHY: ten sam skrypt instalacyjny aktualizuje istniejącą instalację
        #      w miejscu (oficjalne zachowanie install.sh z ollama.com) - nie
        #      trzeba osobnej funkcji na "aktualizuj" vs "zainstaluj od zera".
        #      Zapisujemy jako _instalacja_worker - to ten sam worker, który
        #      blokuje przycisk "Zainstaluj Ollama" w _po_odswiezeniu.
        self._instalacja_worker = self._uruchom_akcje(ollama_zainstaluj, _("Aktualizacja Ollamy"))

    def klik_webui(self):
        if self._webui_worker and self._webui_worker.isRunning():
            self.wpis_log(_("Operacja na WebUI już trwa - poczekaj na zakończenie."))
            return

        if not self._webui_zainstalowane:
            # WHY: pip ściąga sporo zależności z internetu - jak przy Ollamie,
            #      wymagamy świadomej zgody zamiast robić to bez pytania.
            odp = QMessageBox.question(
                self, _("Zainstaluj Open WebUI"),
                _("Zainstalować Open WebUI teraz?\n\n"
                  "Zainstaluje pakiet 'open-webui' przez pip, dla bieżącego użytkownika\n"
                  "(bez Dockera, bez uprawnień administratora - wymaga internetu)."),
            )
            if odp != QMessageBox.StandardButton.Yes:
                return
            self.wpis_log(_("Instaluję Open WebUI - to może potrwać kilka minut..."))
            self.btn_webui.setEnabled(False)
            worker = ActionWorker(webui_zainstaluj, _("Instalacja Open WebUI"))
            self._webui_worker = worker
            self._workers.append(worker)

            # WHY: nie idzie tu przez wspólne _uruchom_akcje - po UDANEJ instalacji
            #      trzeba dodatkowo zapytać o opcjonalne pakiety (patrz
            #      _zapytaj_o_opcjonalne_pakietach_webui), a to nie jest coś, co
            #      pasuje do żadnej innej akcji korzystającej z tego helpera.
            def _po_instalacji(sukces, komunikat, w=worker):
                self.wpis_log(komunikat)
                if w in self._workers:
                    self._workers.remove(w)
                QTimer.singleShot(1200, self.odswiez)
                if sukces:
                    self._zapytaj_o_opcjonalne_pakiety_webui()

            worker.zakonczono.connect(_po_instalacji)
            worker.start()
            return

        if self._webui_dziala:
            # WHY: trzecia funkcja przycisku - WebUI już działa, więc w tym
            #      miejscu tylko otwiera przeglądarkę, nic nie uruchamiamy.
            QDesktopServices.openUrl(QUrl(WEBUI_URL))
            return

        # WHY: druga funkcja przycisku - uruchom serwer w tle. NIE otwieramy
        #      już automatycznie przeglądarki po starcie - użytkownik czasem
        #      chce tylko odpalić WebUI w tle, bez nowej karty w przeglądarce
        #      za każdym razem; otwiera ją sam przyciskiem "Otwórz WebUI".
        self.wpis_log(_("Uruchamiam Open WebUI..."))
        self.btn_webui.setEnabled(False)
        worker = ActionWorker(webui_uruchom, _("Uruchomienie Open WebUI"))
        self._webui_worker = worker
        self._workers.append(worker)

        def _po_uruchomieniu(sukces, komunikat, w=worker):
            self.wpis_log(komunikat)
            if w in self._workers:
                self._workers.remove(w)
            QTimer.singleShot(1200, self.odswiez)

        worker.zakonczono.connect(_po_uruchomieniu)
        worker.start()

    def _zapytaj_o_opcjonalne_pakiety_webui(self):
        # WHAT: po udanej instalacji WebUI pyta, czy dociągnąć opcjonalne pakiety
        #       systemowe (ffmpeg/pandoc/zstd) dla głosu i RAG dokumentów.
        # WHY:  Docker image WebUI ma je w środku, goły 'uv tool install' - nie;
        #       to jedyny krok instalacji WebUI wymagający roota (apt), więc pytamy
        #       osobno i tylko za wyraźną zgodą, zamiast dociągać je bez pytania
        #       razem z główną instalacją.
        odp = QMessageBox.question(
            self, _("Opcjonalne pakiety WebUI"),
            _("Zainstalować też opcjonalne pakiety systemowe (ffmpeg, pandoc, zstd)?\n\n"
              "Potrzebne do transkrypcji audio i importu dokumentów (PDF/Word) do RAG.\n"
              "Bez nich WebUI działa normalnie, tylko bez tych dwóch funkcji.\n"
              "Wymaga uprawnień administratora."),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Instaluję opcjonalne pakiety WebUI (ffmpeg, pandoc, zstd)..."))
        self._uruchom_akcje(webui_zainstaluj_opcjonalne, _("Instalacja opcjonalnych pakietów WebUI"))

    def zatrzymaj_webui(self):
        if self._webui_worker and self._webui_worker.isRunning():
            self.wpis_log(_("Operacja na WebUI już trwa - poczekaj na zakończenie."))
            return
        self.wpis_log(_("Zatrzymuję Open WebUI..."))
        self._webui_worker = self._uruchom_akcje(webui_zatrzymaj, _("Zatrzymanie Open WebUI"))

    def sprawdz_aktualizacje_webui(self):
        # WHAT: to samo co sprawdz_aktualizacje_ollamy, tylko dla Open WebUI -
        #       'uv tool list' (lokalnie) + PyPI (najnowsza), a przy zgodzie
        #       'uv tool upgrade' zamiast oficjalnego skryptu instalacyjnego.
        # WHY:  Open WebUI dostaje częste aktualizacje (poprawki, nowe funkcje,
        #       kompatybilność z nowszym API Ollamy) - łatwo przeoczyć, że jest
        #       nowsza wersja, skoro appka sama nigdy nie sprawdza tego w tle.
        if self._aktualizacja_webui_worker and self._aktualizacja_webui_worker.isRunning():
            self.wpis_log(_("Sprawdzanie aktualizacji już trwa - poczekaj na zakończenie."))
            return
        self.wpis_log(_("Sprawdzam dostępność aktualizacji Open WebUI..."))
        self.btn_sprawdz_aktualizacje_webui.setEnabled(False)
        worker = SprawdzAktualizacjeWorker(webui_wersja_lokalna, webui_wersja_najnowsza)
        self._workers.append(worker)

        def _po_sprawdzeniu(wynik, w=worker):
            if w in self._workers:
                self._workers.remove(w)
            self.btn_sprawdz_aktualizacje_webui.setEnabled(True)
            self._pokaz_wynik_sprawdzenia_aktualizacji_webui(wynik)

        worker.wynik.connect(_po_sprawdzeniu)
        self._aktualizacja_webui_worker = worker
        worker.start()

    def _pokaz_wynik_sprawdzenia_aktualizacji_webui(self, wynik):
        # WHAT: loguje wynik i - gdy jest nowsza wersja - pyta, czy zaktualizować
        #       teraz. W przeciwieństwie do Ollamy NIE wymaga pkexec/roota -
        #       'uv tool upgrade' działa jako zwykły użytkownik (patrz webui_aktualizuj).
        if wynik["lokalna"]:
            self.lbl_wersja_webui.setText(_("wersja: {wersja}").format(wersja=wynik["lokalna"]))
        if wynik["blad"]:
            self.wpis_log(_("Nie udało się sprawdzić aktualizacji Open WebUI: {blad}").format(blad=wynik["blad"]))
            return
        if not wynik["dostepna"]:
            self.wpis_log(_("Masz najnowszą wersję Open WebUI ({wersja}).").format(wersja=wynik["lokalna"]))
            return
        self.wpis_log(_("Dostępna nowa wersja Open WebUI: {najnowsza} (masz {lokalna}).").format(
            najnowsza=wynik["najnowsza"], lokalna=wynik["lokalna"]))
        odp = QMessageBox.question(
            self, _("Aktualizacja Open WebUI"),
            _("Dostępna jest nowsza wersja Open WebUI: {najnowsza}\n"
              "Masz zainstalowaną: {lokalna}\n\n"
              "Nowe wersje zwykle poprawiają błędy i dodają funkcje, a czasem "
              "wymagają ich, by dobrze współpracować z nowszą Ollamą.\n\n"
              "Zainstalować aktualizację teraz?\n\n"
              "Uruchomi 'uv tool upgrade' dla bieżącego użytkownika\n"
              "(bez uprawnień administratora - wymaga internetu).").format(
                najnowsza=wynik["najnowsza"], lokalna=wynik["lokalna"]),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Aktualizuję Open WebUI - to może potrwać kilka minut..."))
        # WHY: zapisujemy jako _webui_worker - ten sam worker, który blokuje
        #      "Uruchom/Zatrzymaj WebUI" w _po_odswiezeniu, żeby nie dało się
        #      odpalić/zatrzymać WebUI w trakcie jego własnej aktualizacji.
        self._webui_worker = self._uruchom_akcje(webui_aktualizuj, _("Aktualizacja Open WebUI"))

    # --- Sterowanie usługą ---
    def start_uslugi(self):
        self.wpis_log(_("Uruchamiam usługę Ollama..."))
        self._uruchom_akcje(usluga_start, _("Start usługi"))

    def stop_uslugi(self):
        self.wpis_log(_("Zatrzymuję usługę Ollama..."))
        self._uruchom_akcje(usluga_stop, _("Stop usługi"))

    def przelacz_autostart(self, wlacz):
        # WHAT: reakcja na kliknięcie checkboxa autostartu.
        opis = _("Włączenie autostartu") if wlacz else _("Wyłączenie autostartu")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: usluga_autostart(wlacz), opis)

    def zastosuj_zalecane_zaawansowane(self):
        # WHAT: wypełnia formularz zalecanym profilem (OLLAMA_ZALECANE) - NIC
        #       nie zapisuje na dysk. To tylko punkt startu do przejrzenia i
        #       ewentualnej poprawki przed kliknięciem "Zapisz".
        z = OLLAMA_ZALECANE
        self.pole_keep_alive.setText(z["OLLAMA_KEEP_ALIVE"])
        self.pole_context_length.setText(z["OLLAMA_CONTEXT_LENGTH"])
        self.pole_max_loaded.setText(z["OLLAMA_MAX_LOADED_MODELS"])
        self.pole_num_parallel.setText(z["OLLAMA_NUM_PARALLEL"])
        self._ustaw_combo_wg_wartosci(self.combo_flash_attention, z["OLLAMA_FLASH_ATTENTION"])
        self._ustaw_combo_wg_wartosci(self.combo_kv_cache, z["OLLAMA_KV_CACHE_TYPE"])
        self.chk_vulkan.setChecked(z["OLLAMA_VULKAN"] == "1")
        self.chk_igpu.setChecked(z["OLLAMA_IGPU_ENABLE"] == "1")
        self.chk_host.setChecked(bool(z["OLLAMA_HOST"]))
        self.pole_ggml_vk_visible_devices.setText(z["GGML_VK_VISIBLE_DEVICES"])
        self.wpis_log(_('Wypełniono formularz zalecanymi wartościami - sprawdź i kliknij "Zapisz", żeby je zastosować.'))

    def zapisz_zaawansowane(self):
        # WHAT: składa WSZYSTKIE pola formularza (9 znanych zmiennych + własne
        #       parametry) w jeden słownik i zapisuje go JEDNYM wywołaniem
        #       usluga_zapisz_env - jeden zapis, jeden restart Ollamy,
        #       niezależnie od tego, ile pól zmieniłeś naraz.
        zmienne = {}
        keep_alive = self.pole_keep_alive.text().strip()
        if keep_alive:
            zmienne["OLLAMA_KEEP_ALIVE"] = keep_alive
        context_length = self.pole_context_length.text().strip()
        if context_length:
            zmienne["OLLAMA_CONTEXT_LENGTH"] = context_length
        max_loaded = self.pole_max_loaded.text().strip()
        if max_loaded:
            zmienne["OLLAMA_MAX_LOADED_MODELS"] = max_loaded
        num_parallel = self.pole_num_parallel.text().strip()
        if num_parallel:
            zmienne["OLLAMA_NUM_PARALLEL"] = num_parallel
        flash_attention = self.combo_flash_attention.currentData()
        if flash_attention:
            zmienne["OLLAMA_FLASH_ATTENTION"] = flash_attention
        kv_cache = self.combo_kv_cache.currentData()
        if kv_cache:
            zmienne["OLLAMA_KV_CACHE_TYPE"] = kv_cache
        # WHY: Vulkan i iGPU zawsze dostają jawną wartość "1"/"0" (nie "puste =
        #      domyślne") - łatwiej odczytać z pliku, co faktycznie jest
        #      ustawione, niż zgadywać ze samego braku wpisu.
        zmienne["OLLAMA_VULKAN"] = "1" if self.chk_vulkan.isChecked() else "0"
        zmienne["OLLAMA_IGPU_ENABLE"] = "1" if self.chk_igpu.isChecked() else "0"
        # WHY: HOST to adres, nie zwykły przełącznik 0/1 - odznaczenie po
        #      prostu nie dopisuje klucza (powrót do wbudowanego domyślnego
        #      "127.0.0.1:11434", czyli tylko lokalnie).
        if self.chk_host.isChecked():
            zmienne["OLLAMA_HOST"] = "0.0.0.0:11434"
        ggml_vk_visible_devices = self.pole_ggml_vk_visible_devices.text().strip()
        if ggml_vk_visible_devices:
            zmienne["GGML_VK_VISIBLE_DEVICES"] = ggml_vk_visible_devices
        # WHY: zawsze jawna wartość ("0" albo rozmiar w bajtach), nigdy pusta -
        #      patrz WHY przy budowie combo_gpu_overhead w _zakladka_zaawansowane.
        zmienne["OLLAMA_GPU_OVERHEAD"] = self.combo_gpu_overhead.currentData()

        for numer, linia in enumerate(self.pole_wlasne_zmienne.toPlainText().splitlines(), start=1):
            linia = linia.strip()
            if not linia:
                continue
            if "=" not in linia:
                QMessageBox.warning(
                    self, _("Błąd we własnych parametrach"),
                    _('Linia {numer} ("{linia}") nie ma postaci KLUCZ=WARTOŚĆ - popraw ją przed zapisem.').format(
                        numer=numer, linia=linia
                    ),
                )
                return
            klucz, wartosc = linia.split("=", 1)
            klucz = klucz.strip()
            if klucz:
                zmienne[klucz] = wartosc.strip()

        odp = QMessageBox.question(
            self, _("Zapisanie ustawień Ollamy"),
            _("Zapisać wszystkie pola z tej zakładki i zrestartować usługę Ollama?") + "\n\n" + _(
                "Wymaga uprawnień administratora - aktualnie załadowane\n"
                "modele zostaną na chwilę wyładowane z pamięci."
            ),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Zapisuję ustawienia Ollamy..."))
        self._zaawansowane_wczytane = False  # WHY: po restarcie odśwież formularz faktycznie zapisanym stanem
        self._uruchom_akcje(lambda: usluga_zapisz_env(zmienne), _("Zapisanie ustawień Ollamy"))

    def przelacz_webui_autostart(self, wlacz):
        # WHAT: reakcja na kliknięcie checkboxa autostartu WebUI.
        # WHY:  usługa --user, nie systemowa - ale i tak idzie przez _uruchom_akcje
        #       (w tle, bo dopisanie pliku .service + systemctl --user to blokujące I/O).
        opis = _("Włączenie autostartu WebUI") if wlacz else _("Wyłączenie autostartu WebUI")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: webui_autostart(wlacz), opis)

    # --- Agregator modeli (LiteLLM) ---
    def klik_litellm(self):
        if self._litellm_worker and self._litellm_worker.isRunning():
            self.wpis_log(_("Operacja na LiteLLM już trwa - poczekaj na zakończenie."))
            return

        if not self._litellm_zainstalowany:
            odp = QMessageBox.question(
                self, _("Zainstaluj LiteLLM"),
                _("Zainstalować LiteLLM teraz?\n\n"
                  "Zainstaluje pakiet 'litellm[proxy]' przez uv, dla bieżącego użytkownika\n"
                  "(bez Dockera, bez uprawnień administratora - wymaga internetu)."),
            )
            if odp != QMessageBox.StandardButton.Yes:
                return
            self.wpis_log(_("Instaluję LiteLLM - to może potrwać kilka minut..."))
            self.btn_litellm.setEnabled(False)
            self._litellm_worker = self._uruchom_akcje(litellm_zainstaluj, _("Instalacja LiteLLM"))
            return

        # WHY: config.yaml generujemy tuż przed (re)startem z AKTUALNEJ listy
        #      serwerów - dodanie/usunięcie hosta widać dopiero po tym kliknięciu.
        self.wpis_log(_("Uruchamiam LiteLLM..."))
        self.btn_litellm.setEnabled(False)
        worker = ActionWorker(lambda: litellm_uruchom(self.serwery), _("Uruchomienie LiteLLM"))
        self._litellm_worker = worker
        self._workers.append(worker)

        def _po_uruchomieniu(sukces, komunikat, w=worker):
            self.wpis_log(komunikat)
            if sukces:
                # WHY: przypomnienie w dzienniku, gdzie podłączyć klienta -
                #      łatwiej znaleźć w logu niż wracać do zakładki po adres.
                self.wpis_log(_("Adres dla klientów (np. Continue): {adres}").format(adres=f"{LITELLM_URL}/v1"))
            if w in self._workers:
                self._workers.remove(w)
            QTimer.singleShot(1200, self.odswiez)

        worker.zakonczono.connect(_po_uruchomieniu)
        worker.start()

    def zatrzymaj_litellm(self):
        if self._litellm_worker and self._litellm_worker.isRunning():
            self.wpis_log(_("Operacja na LiteLLM już trwa - poczekaj na zakończenie."))
            return
        self.wpis_log(_("Zatrzymuję LiteLLM..."))
        self._litellm_worker = self._uruchom_akcje(litellm_zatrzymaj, _("Zatrzymanie LiteLLM"))

    def sprawdz_aktualizacje_litellm(self):
        # WHAT: to samo co sprawdz_aktualizacje_webui, tylko dla LiteLLM -
        #       'uv tool list' (lokalnie) + PyPI (najnowsza), a przy zgodzie
        #       'uv tool upgrade' zamiast reinstalacji od zera.
        if self._aktualizacja_litellm_worker and self._aktualizacja_litellm_worker.isRunning():
            self.wpis_log(_("Sprawdzanie aktualizacji już trwa - poczekaj na zakończenie."))
            return
        self.wpis_log(_("Sprawdzam dostępność aktualizacji LiteLLM..."))
        self.btn_sprawdz_aktualizacje_litellm.setEnabled(False)
        worker = SprawdzAktualizacjeWorker(litellm_wersja_lokalna, litellm_wersja_najnowsza)
        self._workers.append(worker)

        def _po_sprawdzeniu(wynik, w=worker):
            if w in self._workers:
                self._workers.remove(w)
            self.btn_sprawdz_aktualizacje_litellm.setEnabled(True)
            self._pokaz_wynik_sprawdzenia_aktualizacji_litellm(wynik)

        worker.wynik.connect(_po_sprawdzeniu)
        self._aktualizacja_litellm_worker = worker
        worker.start()

    def _pokaz_wynik_sprawdzenia_aktualizacji_litellm(self, wynik):
        # WHAT: loguje wynik i - gdy jest nowsza wersja - pyta, czy zaktualizować
        #       teraz. Jak Open WebUI, NIE wymaga pkexec/roota - 'uv tool
        #       upgrade' działa jako zwykły użytkownik (patrz litellm_aktualizuj).
        if wynik["lokalna"]:
            self.lbl_wersja_litellm.setText(_("wersja: {wersja}").format(wersja=wynik["lokalna"]))
        if wynik["blad"]:
            self.wpis_log(_("Nie udało się sprawdzić aktualizacji LiteLLM: {blad}").format(blad=wynik["blad"]))
            return
        if not wynik["dostepna"]:
            self.wpis_log(_("Masz najnowszą wersję LiteLLM ({wersja}).").format(wersja=wynik["lokalna"]))
            return
        self.wpis_log(_("Dostępna nowa wersja LiteLLM: {najnowsza} (masz {lokalna}).").format(
            najnowsza=wynik["najnowsza"], lokalna=wynik["lokalna"]))
        odp = QMessageBox.question(
            self, _("Aktualizacja LiteLLM"),
            _("Dostępna jest nowsza wersja LiteLLM: {najnowsza}\n"
              "Masz zainstalowaną: {lokalna}\n\n"
              "Nowe wersje zwykle poprawiają błędy i dodają obsługę nowszych "
              "modeli/API.\n\n"
              "Zainstalować aktualizację teraz?\n\n"
              "Uruchomi 'uv tool upgrade' dla bieżącego użytkownika\n"
              "(bez uprawnień administratora - wymaga internetu).").format(
                najnowsza=wynik["najnowsza"], lokalna=wynik["lokalna"]),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Aktualizuję LiteLLM - to może potrwać kilka minut..."))
        # WHY: zapisujemy jako _litellm_worker - ten sam worker, który blokuje
        #      "Uruchom/Zatrzymaj LiteLLM" w _po_odswiezeniu, żeby nie dało się
        #      odpalić/zatrzymać LiteLLM w trakcie jego własnej aktualizacji. Jak
        #      przy Open WebUI - BEZ auto-restartu usługi po aktualizacji, jeśli
        #      akurat działa; użytkownik restartuje ręcznie ("Uruchom ponownie").
        self._litellm_worker = self._uruchom_akcje(litellm_aktualizuj, _("Aktualizacja LiteLLM"))

    def przelacz_litellm_autostart(self, wlacz):
        # WHY: jw. co przy WebUI - usługa --user, ale i tak przez _uruchom_akcje (blokujące I/O).
        opis = _("Włączenie autostartu LiteLLM") if wlacz else _("Wyłączenie autostartu LiteLLM")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: litellm_autostart(wlacz, self.serwery), opis)

    def odswiez_liste_agregatora(self):
        # WHAT: na żądanie (nie co 10 s razem z resztą) odpytuje /api/tags na
        #       każdym hoście z listy serwerów - pokazuje, co realnie trafi
        #       do configu LiteLLM przy następnym uruchomieniu.
        # WHY:  osobny przycisk zamiast robić to w RefreshWorker - inaczej
        #       każde odświeżenie stanu (co 10 s) pytałoby wszystkie hosty,
        #       nawet gdy nikt nie patrzy akurat na tę zakładkę.
        if self._agregator_worker and self._agregator_worker.isRunning():
            return
        self.lista_agregator.clear()
        self.lista_agregator.addItem(_("Sprawdzam hosty..."))
        self.btn_odswiez_agregator.setEnabled(False)
        worker = AgregatorWorker(self.serwery)
        self._agregator_worker = worker

        def _po_sprawdzeniu(wpisy, w=worker):
            self.btn_odswiez_agregator.setEnabled(True)
            self.lista_agregator.clear()
            if not wpisy:
                self.lista_agregator.addItem(_("Brak dostępnych modeli (hosty nieosiągalne albo puste)."))
                return
            for nazwa_hosta, model, adres in wpisy:
                self.lista_agregator.addItem(f"{model}  —  {nazwa_hosta} ({adres})")

        worker.wynik.connect(_po_sprawdzeniu)
        worker.start()

    def _z_modelami_agregatora(self, przycisk, akcja):
        # WHAT: odpytuje hosty (jak odswiez_liste_agregatora), deduplikuje
        #       wykryte modele i woła `akcja(modele)` - wspólne dla eksportu
        #       configu Continue i połączenia Unsloth Studio.
        # WHY:  model może być na kilku hostach naraz (LiteLLM sam routuje) -
        #       klient widzi tylko nazwę, więc listę deduplikujemy.
        if self._agregator_worker and self._agregator_worker.isRunning():
            return
        przycisk.setEnabled(False)
        worker = AgregatorWorker(self.serwery)
        self._agregator_worker = worker

        def _po_sprawdzeniu(wpisy, w=worker):
            przycisk.setEnabled(True)
            modele = sorted({model for _nazwa_hosta, model, _adres in wpisy})
            if not modele:
                QMessageBox.information(
                    self, _("Brak modeli"),
                    _("Brak dostępnych modeli (hosty nieosiągalne albo puste) - "
                      "sprawdź listę serwerów u góry okna i spróbuj ponownie."),
                )
                return
            akcja(modele)

        worker.wynik.connect(_po_sprawdzeniu)
        worker.start()

    def generuj_config_continue(self):
        # WHAT: gotowy config.yaml dla Continue.dev z modeli agregatora,
        #       pokazany do skopiowania w DialogConfigContinue.
        # WHY:  osobne, świadome kliknięcie zamiast automatycznego zapisu - appka
        #       nie ma dotykać ~/.continue/config.yaml (patrz DialogConfigContinue).
        self._z_modelami_agregatora(
            self.btn_config_continue,
            lambda modele: DialogConfigContinue(_zbuduj_config_continue(modele), self).exec(),
        )

    def pokaz_polaczenie_unsloth(self):
        # WHAT: dane do dodania agregatora jako połączenia w Unsloth Studio
        #       (patrz DialogPolaczenieUnsloth - Studio nie ma pliku configu).
        self._z_modelami_agregatora(
            self.btn_polaczenie_unsloth,
            lambda modele: DialogPolaczenieUnsloth(modele, self).exec(),
        )

    # --- Modele ---
    def _wybrany_model(self):
        wiersz = self.lista_modeli.currentRow()
        if wiersz < 0:
            return None
        item = self.lista_modeli.item(wiersz, 0)
        # WHY: PRAWDZIWA nazwa modelu (do API/usuwania) leci jako dane wiersza,
        #      nie jako wyświetlany tekst - patrz _skroc_nazwe_modelu_hf w
        #      _po_odswiezeniu (modele z HF wyświetlają skróconą nazwę).
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _aktualizuj_przycisk_usun(self):
        # WHY: usuwać można tylko gdy coś jest zaznaczone.
        self.btn_usun.setEnabled(self._wybrany_model() is not None)

    def usun_model(self):
        model = self._wybrany_model()
        if not model:
            return
        # WHY: usuwanie jest nieodwracalne - wymagamy potwierdzenia.
        odp = QMessageBox.question(
            self, _("Usuń model"),
            _("Na pewno usunąć model:\n\n{model}\n\nTej operacji nie da się cofnąć.").format(model=model),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Usuwam model: {model}").format(model=model))
        self._uruchom_akcje(
            lambda: self.client.delete_model(model), _("Usunięcie {model}").format(model=model)
        )

    # --- Wyszukiwarka modeli (karta "Pobierz nowy model") ---
    def _ustaw_tabele_wyboru(self, tabela):
        # WHAT: wspólny wygląd tabel wyników i wariantów - bez edycji, wybór
        #       całego wiersza, jeden naraz; pierwsza kolumna (nazwa) dostaje całą
        #       wolną szerokość, reszta (krótki tekst) tyle, ile potrzebuje.
        naglowek = tabela.horizontalHeader()
        naglowek.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for kolumna in range(1, tabela.columnCount()):
            naglowek.setSectionResizeMode(kolumna, QHeaderView.ResizeMode.ResizeToContents)
        tabela.verticalHeader().setVisible(False)
        tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def szukaj_modeli(self):
        # WHAT: tekst z "/" (link do HF, "użytkownik/repo" albo "hf.co/...") ->
        #       od razu warianty tego repo, bez szukania; zwykła nazwa -> szukanie
        #       naraz w bibliotece Ollamy i na Hugging Face (SzukajModeliWorker).
        zapytanie = self.pole_szukaj_model.text().strip()
        if not zapytanie or (self._szukaj_worker and self._szukaj_worker.isRunning()):
            return
        if "/" in zapytanie:
            # WHY: "hf.co/user/repo:Q4_K_M" to też poprawny odnośnik do repo -
            #      zdejmujemy przedrostek i tag, reszta jak zwykły link do HF.
            # BUG naprawiony: tag obcinamy dopiero z GOTOWEGO repo - obcięcie
            #      po ":" z całego tekstu zostawiało z "https://..." samo "https".
            repo = _hf_repo_z_linku(re.sub(r"^hf\.co/", "", zapytanie))
            if repo:
                repo = repo.split(":", 1)[0]
                self._wypelnij_wyniki([{
                    "zrodlo": "hf", "id": repo, "opis": "", "mozliwosci": [], "rozmiary": [], "pobrania": "",
                }])
                self.lbl_status_szukania.setText("")
                self.tabela_wynikow.selectRow(0)  # WHY: od razu ładuje warianty (_wybrano_wynik_wyszukiwania)
                return

        self._wypelnij_wyniki([])
        self.btn_szukaj_model.setEnabled(False)
        self.lbl_status_szukania.setText(_("Szukam w bibliotece Ollamy i na Hugging Face..."))
        worker = SzukajModeliWorker(zapytanie)
        self._workers.append(worker)

        def _po_szukaniu(wyniki, bledy, w=worker):
            if w in self._workers:
                self._workers.remove(w)
            self.btn_szukaj_model.setEnabled(True)
            self._wypelnij_wyniki(wyniki)
            linie = [_("Znalezione modele: {n}").format(n=len(wyniki))]
            linie += [_("Błąd wyszukiwania - {blad}").format(blad=b) for b in bledy]
            self.lbl_status_szukania.setText("\n".join(linie))

        worker.wynik.connect(_po_szukaniu)
        self._szukaj_worker = worker
        worker.start()

    def _wypelnij_wyniki(self, wyniki):
        # WHAT: tabela wyników - nazwa, źródło (Ollama/Hugging Face), pobrania;
        #       cały słownik wyniku leci jako dane wiersza (UserRole). Czyści też
        #       warianty - dotyczyły poprzedniego wyboru.
        self.tabela_wynikow.blockSignals(True)
        self.tabela_wynikow.setRowCount(0)
        for wynik in wyniki:
            zrodlo = "Hugging Face" if wynik["zrodlo"] == "hf" else "Ollama"
            # WHY: dymek to HTML (pogrubienie, <br>) - tekst ze strony trzeba
            #      zabezpieczyć, bo opis typu "<4GB VRAM" rozbiłby formatowanie.
            linie = [f"<b>{html.escape(wynik['id'])}</b>", _("Źródło: {zrodlo}").format(zrodlo=zrodlo)]
            if wynik["opis"]:
                linie.append(html.escape(wynik["opis"]))
            if wynik["mozliwosci"]:
                linie.append(_("Możliwości: {lista}").format(lista=", ".join(wynik["mozliwosci"])))
            if wynik["rozmiary"]:
                linie.append(_("Rozmiary: {lista}").format(lista=", ".join(wynik["rozmiary"])))
            dymek = "<br>".join(linie)

            wiersz = self.tabela_wynikow.rowCount()
            self.tabela_wynikow.insertRow(wiersz)
            for kolumna, tekst in enumerate((wynik["id"], zrodlo, wynik["pobrania"])):
                pozycja = QTableWidgetItem(tekst)
                pozycja.setToolTip(dymek)
                if kolumna == 0:
                    pozycja.setData(Qt.ItemDataRole.UserRole, wynik)
                self.tabela_wynikow.setItem(wiersz, kolumna, pozycja)
        self.tabela_wynikow.blockSignals(False)
        self._wyczysc_warianty()

    def _wyczysc_warianty(self):
        self._warianty_dla = None
        self.tabela_wariantow.setRowCount(0)
        self.lbl_do_pobrania.setText("")
        self.lbl_szacowana_pamiec.setText("")
        self.lbl_szacowana_pamiec.setToolTip("")
        self._aktualizuj_przycisk_pobierz()

    def _wybrano_wynik_wyszukiwania(self):
        # WHAT: wybór modelu z wyników -> w tle wczytuje jego warianty
        #       (WariantyModeluWorker) do tabeli wariantów.
        # WHY:  spóźniona odpowiedź dla modelu, który użytkownik zdążył już
        #       odznaczyć, jest ignorowana (patrz self._warianty_dla) - inaczej
        #       szybkie klikanie mogło pokazać warianty nie tego modelu.
        wiersz = self.tabela_wynikow.currentRow()
        self._wyczysc_warianty()
        if wiersz < 0:
            return
        wynik = self.tabela_wynikow.item(wiersz, 0).data(Qt.ItemDataRole.UserRole)
        self._warianty_dla = (wynik["zrodlo"], wynik["id"])
        self.lbl_status_szukania.setText(_("Szukam dostępnych wariantów..."))
        worker = WariantyModeluWorker(wynik["zrodlo"], wynik["id"])
        self._workers.append(worker)

        def _sprzatnij(w=worker):
            if w in self._workers:
                self._workers.remove(w)

        def _po_wariantach(zrodlo, model_id, warianty):
            _sprzatnij()
            if self._warianty_dla != (zrodlo, model_id):
                return
            self.lbl_status_szukania.setText(_("Znalezione warianty: {n}").format(n=len(warianty)))
            for kwant, rozmiar_bajty, kontekst in warianty:
                wiersz_w = self.tabela_wariantow.rowCount()
                self.tabela_wariantow.insertRow(wiersz_w)
                etykieta = kwant or _("domyślny (bez sufiksu)")
                rozmiar = f"{rozmiar_bajty / (1024 ** 3):.1f} GB" if rozmiar_bajty else "—"
                for kolumna, tekst in enumerate((etykieta, rozmiar, kontekst)):
                    pozycja = QTableWidgetItem(tekst)
                    if kolumna == 0:
                        pozycja.setData(Qt.ItemDataRole.UserRole, (kwant, rozmiar_bajty))
                    self.tabela_wariantow.setItem(wiersz_w, kolumna, pozycja)

        def _po_bledzie(zrodlo, model_id, komunikat):
            _sprzatnij()
            if self._warianty_dla == (zrodlo, model_id):
                self.lbl_status_szukania.setText(komunikat)

        worker.wynik.connect(_po_wariantach)
        worker.blad.connect(_po_bledzie)
        worker.start()

    def _pelna_nazwa_wariantu(self):
        # WHAT: gotowa nazwa do 'ollama pull' dla zaznaczonego wariantu -
        #       "model:tag" (Ollama) albo "hf.co/user/repo:kwant" (Hugging Face).
        #       None, gdy nic nie zaznaczono.
        wiersz = self.tabela_wariantow.currentRow()
        if wiersz < 0 or not self._warianty_dla:
            return None
        zrodlo, model_id = self._warianty_dla
        kwant, _rozmiar = self.tabela_wariantow.item(wiersz, 0).data(Qt.ItemDataRole.UserRole)
        if zrodlo == "hf":
            # WHY: bez rozpoznanej kwantyzacji (repo z jednym plikiem .gguf bez
            #      dopisku) Ollama chce sam 'hf.co/user/repo', bez dwukropka.
            return f"hf.co/{model_id}:{kwant}" if kwant else f"hf.co/{model_id}"
        return f"{model_id}:{kwant}"

    def _model_do_pobrania(self):
        # WHAT: co pobierze przycisk "Pobierz" - zaznaczony wariant, a bez niego
        #       dokładna nazwa wpisana w pole (musi mieć tag po ":" albo zaczynać
        #       się od "hf.co/" - sama nazwa bez tagu to raczej zapytanie do
        #       wyszukiwarki niż świadomy wybór konkretnego wariantu).
        nazwa = self._pelna_nazwa_wariantu()
        if nazwa:
            return nazwa
        tekst = self.pole_szukaj_model.text().strip()
        if ":" in tekst or tekst.startswith("hf.co/"):
            return tekst
        return None

    def _aktualizuj_przycisk_pobierz(self):
        self.btn_pull.setEnabled(not self._pobieranie_trwa and self._model_do_pobrania() is not None)

    def _wybrano_wariant(self):
        # WHAT: pokazuje, co dokładnie zostanie pobrane, i szacuje pamięć dla
        #       wybranego wariantu - wagi to PRAWDZIWY rozmiar pliku.
        self._aktualizuj_przycisk_pobierz()
        nazwa = self._pelna_nazwa_wariantu()
        if not nazwa:
            self.lbl_do_pobrania.setText("")
            self.lbl_szacowana_pamiec.setText("")
            return
        self.lbl_do_pobrania.setText(_("Do pobrania: {model}").format(model=nazwa))
        kwant, rozmiar_bajty = self.tabela_wariantow.item(
            self.tabela_wariantow.currentRow(), 0
        ).data(Qt.ItemDataRole.UserRole)
        # WHY: liczbę parametrów bierzemy z tagu ("7b-instruct-q8_0"), a gdy jej
        #      tam nie ma (np. "latest", kwantyzacja z HF) - z nazwy modelu/repo.
        miliardy = _parsuj_rozmiar_parametrow(kwant or "") or _parsuj_rozmiar_parametrow(self._warianty_dla[1])
        self._pokaz_szacowana_pamiec(rozmiar_bajty, miliardy, kwant or "")

    def _pokaz_szacowana_pamiec(self, rozmiar_bajty, miliardy, etykieta):
        self.lbl_szacowana_pamiec.setToolTip("")
        if not rozmiar_bajty:
            self.lbl_szacowana_pamiec.setText("")
            return
        wagi_gb = rozmiar_bajty / (1024 ** 3)
        if miliardy is None:
            # WHY: bez liczby parametrów nie da się oszacować KV cache - uczciwie
            #      pokazujemy tylko to, co wiemy na pewno (rozmiar pliku).
            self.lbl_szacowana_pamiec.setText(_(
                "Wagi (rozmiar pliku): {wagi:.1f} GB - nie znam liczby parametrów, więc bez "
                "szacowania cache kontekstu. Realne zużycie pamięci będzie wyższe."
            ).format(wagi=wagi_gb))
            return
        w = _oszacuj_pamiec_z_wag(wagi_gb, miliardy, etykieta)
        # WHAT: jedna zwięzła linia w karcie, szczegóły i ostrzeżenia w dymku.
        # WHY:  pełny rozpis (kilkanaście linii) zabierał wysokość tabelom
        #       wyników i wariantów - mieściły po 2 wiersze.
        self.lbl_szacowana_pamiec.setText(_(
            "Szacowana pamięć: ~{zajete:.1f} GB (wagi {wagi:.1f} + cache kontekstu {kv_cache:.1f} "
            "+ bufory {bufory:.1f}), z zapasem {z_zapasem:.1f} GB - zalecany RAM {rekomendowany} GB. "
            "Szczegóły w dymku."
        ).format(
            zajete=w["zajete_realnie_gb"], wagi=w["wagi_gb"], kv_cache=w["kv_cache_gb"],
            bufory=w["bufory_gb"], z_zapasem=w["z_zapasem_gb"], rekomendowany=w["rekomendowany_ram"],
        ))
        szczegoly = [_("Model: {p:.3g} mld parametrów, kontekst {ctx} tokenów, cache {kvq}.").format(
            p=miliardy, ctx=_KALKULATOR_CTX, kvq=_KALKULATOR_KVQ)]
        if w["kv_cache_gb"] > w["wagi_gb"]:
            szczegoly.append(_(
                "Uwaga: cache kontekstu przewyższa wagi modelu — rozważ mniejszy "
                "kontekst lub kwantyzację cache."
            ))
        if _KALKULATOR_KVQ != "f16":
            szczegoly.append(_(
                "Cache {kvq} działa TYLKO z OLLAMA_FLASH_ATTENTION=1 (zakładka Zaawansowane) - "
                "bez tego Ollama i tak liczy cache jak f16, czyli więcej niż wyliczono wyżej."
            ).format(kvq=_KALKULATOR_KVQ))
        szczegoly.append(_(
            "Na APU / pamięci współdzielonej zarezerwuj pamięć dla systemu przez OLLAMA_GPU_OVERHEAD."
        ))
        self.lbl_szacowana_pamiec.setToolTip("\n".join(szczegoly))

    def pobierz_model(self):
        nazwa = self._model_do_pobrania()
        if nazwa:
            self._rozpocznij_pobieranie(nazwa)

    def _rozpocznij_pobieranie(self, model):
        # WHAT: start pobierania (PullWorker) - ten sam dla obu źródeł (Ollama/HF),
        #       jeden pasek postępu/log/blokada "jedno pobieranie naraz".
        # WHY:  jedno pobieranie naraz - prościej i nie obciąża łącza/dysku podwójnie.
        if self.pull_worker and self.pull_worker.isRunning():
            self.wpis_log(_("Pobieranie już trwa - poczekaj na zakończenie."))
            return

        self._pobieranie_trwa = True
        self._aktualizuj_przycisk_pobierz()
        self.pasek_postepu.setVisible(True)
        self.pasek_postepu.setRange(0, 100)
        self.pasek_postepu.setValue(0)
        self._ostatni_status = None
        self.wpis_log(_("Rozpoczynam pobieranie: {model}").format(model=model))

        self.pull_worker = PullWorker(self.client, model)
        self.pull_worker.postep.connect(self._postep_pull)
        self.pull_worker.zakonczono.connect(self._koniec_pull)
        self.pull_worker.start()

    def _postep_pull(self, procent, status):
        # WHAT: aktualizuj pasek; do logu pisz tylko przy ZMIANIE statusu.
        # WHY:  strumień sypie dziesiątkami linii/s - log dostaje tylko nowe etapy.
        if procent < 0:
            self.pasek_postepu.setRange(0, 0)  # tryb nieokreślony
        else:
            self.pasek_postepu.setRange(0, 100)
            self.pasek_postepu.setValue(procent)

        if status != self._ostatni_status:
            self.wpis_log(f"  {status}")
            self._ostatni_status = status

    def _koniec_pull(self, sukces, komunikat):
        self.pasek_postepu.setVisible(False)
        self._pobieranie_trwa = False
        self._aktualizuj_przycisk_pobierz()
        self.wpis_log(komunikat)
        self.odswiez()  # WHY: świeżo pobrany model powinien pojawić się na liście


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ollama Manager")
    # WHY: ikona z motywu systemowego - spójny wygląd w Breeze, bez własnych zasobów.
    app.setWindowIcon(QIcon.fromTheme("applications-system"))
    okno = MainWindow()
    okno.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
