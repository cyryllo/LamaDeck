#!/usr/bin/env python3
# =============================================================================
#  LamaDeck (dawniej Ollama Manager) - panel lokalnego stosu AI pod KDE (PyQt6):
#  Ollama, router LiteLLM, Open WebUI i czat - nieoficjalny, niezwiązany z Ollamą
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
#  Uruchomienie: python3 lamadeck.py
# =============================================================================

import sys
import ipaddress
import json
import html
import os
import re
import secrets
import base64
import heapq
import math
import operator
from array import array
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
import shlex
import socket
import sqlite3
import getpass
import grp
import shutil
import subprocess
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests  # WHY: czytelniejsze od urllib przy strumieniowaniu /api/pull

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, pyqtProperty, QTimer, QUrl, QSettings, QSize, QByteArray, QRectF, QPointF, QPoint,
    QRect, QObject, QEvent, QVariant, QMetaType, QBuffer, QIODevice,
)
from PyQt6.QtGui import (
    QIcon, QDesktopServices, QFontDatabase, QPainter, QColor, QPen, QPainterPath, QFont, QFontMetrics, QPixmap,
    QImage, QBitmap, QRegion, QCursor, QTextCursor, QTextFrameFormat, QTextBlockFormat, QTextCharFormat,
    QTextDocument, QTextImageFormat, QKeySequence, QShortcut, QTextFormat,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QListWidget, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPlainTextEdit, QComboBox, QMessageBox, QCheckBox,
    QDialog, QLineEdit, QScrollArea, QListWidgetItem,
    QFrame, QStackedWidget, QGridLayout, QSizePolicy, QAbstractButton, QButtonGroup, QMenu, QFileDialog,
    QSystemTrayIcon, QTextBrowser, QToolTip, QInputDialog,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtDBus import QDBusInterface, QDBusArgument, QDBusMessage

# --- Konfiguracja ---------------------------------------------------------
# WHAT: wersja aplikacji - widoczna w tytule okna.
# WHY:  ostatnia cyfra rośnie przy każdym commicie; pierwsze dwie zmieniają się
#       tylko na wyraźne polecenie (patrz CLAUDE.md, sekcja "Wersjonowanie").
# WHAT: nazwa programu - wyświetlana (NAZWA_PROGRAMU) i techniczna (ID_PROGRAMU: pliki
#       .desktop, ikona, pakiet, katalogi). Do 0.9.x program nazywał się "Ollama Manager".
# WHY:  nazwa bez "Ollama" - nie sugeruje oficjalnej aplikacji Ollamy (decyzja użytkownika
#       2026-10-30, przed szerszym udostępnieniem od 1.0).
NAZWA_PROGRAMU = "LamaDeck"
ID_PROGRAMU = "lamadeck"
WERSJA = "0.9.15"

# WHAT: bazowy adres serwera Ollamy (operacje na modelach).
# WHY:  wydzielony na górę - możesz wskazać BC-250
#       (np. http://192.168.0.236:11434) zamiast localhost.
#       UWAGA: sterowanie USŁUGĄ (start/stop/autostart) dotyczy zawsze
#       LOKALNEGO systemd - nie da się zdalnie startować BC-250 tą drogą.
OLLAMA_URL = "http://localhost:11434"

# WHAT: nazwa usługi systemd. WHY: wydzielona, gdyby u Ciebie nazywała się inaczej.
SERVICE_NAME = "ollama"

# WHAT: domyślny port panelu Open WebUI ('open-webui serve').
# WHY:  od ekranu "Open WebUI" z makiet port da się zmienić z okna (pole "Port",
#       zapisane w QSettings) - adres liczy _webui_url(), nie stała.
WEBUI_PORT_DOMYSLNY = 8080

# WHAT: adres agregatora LiteLLM (domyślny port 'litellm --config ...').
# WHY:  jw. - jedno miejsce do zmiany, gdyby port kolidował z czymś innym.
LITELLM_URL = "http://localhost:4000"

# WHAT: profil startowy dla przycisku "Zastosuj zalecane wartości" w zakładce
#       Zaawansowane - wypełnia formularz, NIC nie zapisuje samo z siebie.
# WHY:  dobrane pod BC-250 (Vulkan+iGPU włączone, kontekst 16k, keep_alive 30m,
#       dwa modele w pamięci - czat + model embeddingów do wyszukiwania w dużych
#       plikach na ekranie Czat (przy jednym każde pytanie wymieniało model czatu
#       i wczytywało go od nowa); jedno zapytanie naraz, q8_0 dla mniejszego zużycia VRAM) - to
#       tylko punkt startu do przejrzenia i ewentualnej korekty przed "Zapisz",
#       appka nigdy nie wstrzykuje tych wartości automatycznie/po cichu.
OLLAMA_ZALECANE = {
    "OLLAMA_KEEP_ALIVE": "30m",
    "OLLAMA_CONTEXT_LENGTH": "16384",
    "OLLAMA_MAX_LOADED_MODELS": "2",
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

# WHAT: zakresy blokowane przez IPAddressDeny=, gdy pole IPAddressAllow w
#       zakładce Zaawansowane NIE jest puste - wtedy z sieci lokalnej łączą się
#       tylko adresy z listy (localhost nie jest tu wymieniony, więc i tak
#       zawsze działa).
# WHY:  NIE "IPAddressDeny=any" - filtr systemd działa w OBIE strony (ruch
#       przychodzący i wychodzący usługi), więc "any" odcięłoby też Ollamie
#       registry.ollama.ai i pobieranie modeli. Blokujemy więc tylko prywatne
#       zakresy LAN - internet (pobieranie modeli) dalej działa.
OLLAMA_IP_DENY_LAN = "10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 link-local"
# WHY: słowa kluczowe, które systemd.resource-control(5) przyjmuje obok adresów/CIDR.
_IP_SLOWA_KLUCZOWE = {"any", "localhost", "link-local", "multicast"}


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


def _katalog_config():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _katalog_danych():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _przenies_dane_ollama_manager():
    # WHAT: jednorazowe przeniesienie danych z czasów nazwy "Ollama Manager" (do 0.9.x):
    #       ustawienia, zapisane rozmowy, config routera LiteLLM i autostart programu.
    # WHY:  zmiana nazwy nie może nikomu zabrać hostów, aliasów ani rozmów. Przenosimy
    #       tylko, gdy nowego miejsca jeszcze nie ma - nigdy nie nadpisujemy nowych danych.
    #       Błąd przeniesienia nie może zablokować startu programu.
    try:
        stary = _katalog_config() / "OllamaManager" / "OllamaManager.conf"
        nowy = _katalog_config() / NAZWA_PROGRAMU / f"{NAZWA_PROGRAMU}.conf"
        if stary.exists() and not nowy.exists():
            nowy.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stary), str(nowy))
        stare_czaty = _katalog_danych() / "OllamaManager" / "czaty"
        nowe_czaty = _katalog_danych() / NAZWA_PROGRAMU / "czaty"
        if stare_czaty.is_dir() and not nowe_czaty.exists():
            nowe_czaty.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stare_czaty), str(nowe_czaty))
        for katalog in (stary.parent, stare_czaty.parent):
            if katalog.is_dir() and not any(katalog.iterdir()):
                katalog.rmdir()
        # WHY: usługa routera (systemd --user) wskazuje plik configu po ścieżce - po
        #      przeniesieniu poprawiamy ją w pliku usługi, żeby router dalej startował
        stary_litellm = _katalog_config() / "ollama-manager" / "litellm_config.yaml"
        nowy_litellm = _litellm_config_sciezka()
        if stary_litellm.exists() and not nowy_litellm.exists():
            nowy_litellm.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stary_litellm), str(nowy_litellm))
            usluga = _litellm_service_sciezka()
            if usluga.exists() and str(stary_litellm) in usluga.read_text():
                usluga.write_text(usluga.read_text().replace(str(stary_litellm), str(nowy_litellm)))
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
            if not any(stary_litellm.parent.iterdir()):
                stary_litellm.parent.rmdir()
        # WHY: autostart po zalogowaniu wskazywał starą nazwę i stary plik programu
        stary_autostart = Path.home() / ".config" / "autostart" / "ollama-manager.desktop"
        if stary_autostart.exists():
            stary_autostart.unlink()
            ustaw_autostart_programu(True)
    except (OSError, subprocess.SubprocessError):
        pass


def _ustawienia():
    # WHAT: jeden wspólny obiekt QSettings (plik INI w ~/.config).
    return QSettings(NAZWA_PROGRAMU, NAZWA_PROGRAMU)


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


def _sciezka_programu(nazwa):
    # WHAT: pełna ścieżka do programu uruchamianego przez pkexec.
    # WHY:  pkexec szuka programu w PATH zwykłego użytkownika, a w Debianie nie ma
    #       w nim /usr/sbin i /sbin - "usermod" kończył się błędem "Cannot run
    #       program usermod: No such file or directory", zanim w ogóle pojawiło
    #       się pytanie o hasło. Szukamy więc też w katalogach programów
    #       administracyjnych i podajemy pkexec gotową ścieżkę.
    if os.path.isabs(nazwa):
        return nazwa
    sciezki = os.environ.get("PATH", "") + ":/usr/local/sbin:/usr/sbin:/sbin"
    return shutil.which(nazwa, path=sciezki) or nazwa


def _pkexec(args, wejscie=None):
    # WHAT: uruchamia polecenie jako root przez polkit.
    # WHY:  pkexec pokazuje graficzny dialog KDE z prośbą o hasło - nie potrzeba
    #       terminala ani sudo. Rzucamy wyjątek przy błędzie, żeby worker go złapał.
    #       Opcjonalne 'wejscie' idzie na STDIN procesu (np. treść pliku do
    #       zapisania) - bezpieczniejsze niż wstrzykiwanie tekstu do argumentów
    #       powłoki, bo w ogóle nie dotyka parsera 'sh'.
    r = subprocess.run(["pkexec", _sciezka_programu(args[0])] + args[1:],
                       input=wejscie, capture_output=True, text=True)
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


def usluga_restart():
    # WHAT: "Uruchom ponownie" na ekranie "Hosty Ollama" (tylko ten komputer).
    _pkexec(["systemctl", "restart", SERVICE_NAME])


def usluga_dziennik(linie=200):
    # WHAT: ostatnie wpisy dziennika systemd usługi Ollama (journalctl).
    # WHY:  najpierw bez roota - działa, gdy użytkownik jest w grupie
    #       adm/systemd-journal. Inaczej journalctl zwraca tylko "-- No entries --"
    #       (bez błędu), więc wtedy pytamy o hasło przez pkexec, zamiast pokazać
    #       pusty dziennik i kazać szukać przyczyny w terminalu.
    polecenie = ["journalctl", "-u", SERVICE_NAME, "-n", str(linie), "--no-pager"]
    r = subprocess.run(polecenie, capture_output=True, text=True, timeout=15)
    wpisy = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("-- No entries --")]
    if wpisy:
        return "\n".join(wpisy)
    r = subprocess.run(["pkexec", _sciezka_programu(polecenie[0])] + polecenie[1:], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or _("pkexec: kod wyjścia {kod}").format(kod=r.returncode))
    return r.stdout.strip() or _("Dziennik usługi jest pusty.")


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


def _usluga_ip_allow():
    # WHAT: czyta listę adresów z IPAddressAllow= w override.conf (pusty
    #       tekst = brak ograniczenia, dostęp dla wszystkich z LAN).
    try:
        tresc = _usluga_override_sciezka().read_text()
    except OSError:
        return ""
    adresy = []
    for linia in tresc.splitlines():
        linia = linia.strip()
        if linia.startswith("IPAddressAllow="):
            wartosc = linia.removeprefix("IPAddressAllow=").split()
            # WHY: puste przypisanie w systemd resetuje listę - tak samo tutaj.
            adresy = adresy + wartosc if wartosc else []
    return " ".join(adresy)


def _sprawdz_ip_allow(tekst):
    # WHAT: normalizuje listę z pola IPAddressAllow (spacje/przecinki) i
    #       zwraca (lista, błędny_wpis) - błędny_wpis=None, gdy wszystko OK.
    # WHY:  wartość trafia prosto do override.conf - walidacja adres/CIDR/słowo
    #       kluczowe wyklucza literówki (systemd po cichu zignorowałby całą
    #       linię) i przemycenie nowej linii z inną dyrektywą.
    adresy = tekst.replace(",", " ").split()
    for adres in adresy:
        if adres in _IP_SLOWA_KLUCZOWE:
            continue
        try:
            ipaddress.ip_network(adres, strict=False)
        except ValueError:
            return adresy, adres
    return adresy, None


def usluga_zapisz_env(zmienne, ip_allow=""):
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
    # WHY: IPAddressAllow= samo z siebie niczego nie blokuje - działa dopiero w
    #      parze z IPAddressDeny= (patrz OLLAMA_IP_DENY_LAN). Pusta lista = brak
    #      obu linii, czyli dostęp dla wszystkich (zależnie od OLLAMA_HOST).
    if ip_allow:
        tresc += f"IPAddressAllow={ip_allow}\nIPAddressDeny={OLLAMA_IP_DENY_LAN}\n"
    skrypt = (
        'mkdir -p "$(dirname "$1")" && cat > "$1" && '
        "systemctl daemon-reload && "
        f"systemctl restart {SERVICE_NAME}"
    )
    _pkexec(["sh", "-c", skrypt, "sh", str(_usluga_override_sciezka())], wejscie=tresc)


def _ollama_pamiec_procesow():
    # WHAT: ile RAM-u zajmuje lokalna Ollama - proces 'ollama serve' i WSZYSTKIE
    #       jego procesy potomne (modele działają w osobnych procesach, np.
    #       'llama-server'). Zwraca {"anon": bajty, "pliki": bajty, "procesy": n}
    #       albo None, gdy Ollama nie działa.
    # WHY:  na prośbę użytkownika - VRAM to nie całe zużycie: Ollama mapuje
    #       pliki modeli do pamięci (mmap) i trzyma tam część modelu, zwłaszcza
    #       gdy nie mieści się w VRAM. "pliki" = RssFile (zmapowane pliki modeli
    #       w RAM), "anon" = RssAnon + RssShmem (zwykła pamięć procesów: bufory,
    #       cache kontekstu po stronie CPU). Czytane z /proc/<pid>/status -
    #       działa BEZ roota mimo że procesy należą do użytkownika 'ollama'.
    #       Tylko lokalna maszyna - zdalnego serwera tak nie zmierzymy.
    procesy = {}  # pid -> (ppid, comm, cmdline)
    for katalog in Path("/proc").iterdir():
        if not katalog.name.isdigit():
            continue
        try:
            stat = (katalog / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            comm = (katalog / "comm").read_text().strip()
            cmdline = (katalog / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, ValueError, IndexError):
            continue  # WHY: proces zdążył się zakończyć - pomijamy
        procesy[int(katalog.name)] = (ppid, comm, cmdline)

    nasze = {pid for pid, (_pp, comm, cmd) in procesy.items() if comm == "ollama" and " serve" in f" {cmd}"}
    if not nasze:
        return None
    dolaczono = True
    while dolaczono:  # WHY: potomkowie potomków (runner może mieć własne procesy)
        dolaczono = False
        for pid, (ppid, _comm, _cmd) in procesy.items():
            if ppid in nasze and pid not in nasze:
                nasze.add(pid)
                dolaczono = True

    anon = pliki = 0
    for pid in nasze:
        try:
            for linia in Path(f"/proc/{pid}/status").read_text().splitlines():
                klucz, _sep, wartosc = linia.partition(":")
                if klucz in ("RssAnon", "RssShmem"):
                    anon += int(wartosc.split()[0]) * 1024
                elif klucz == "RssFile":
                    pliki += int(wartosc.split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            continue
    return {"anon": anon, "pliki": pliki, "procesy": len(nasze)}


def _adres_lokalny(adres):
    # WHAT: czy adres serwera Ollamy wskazuje na TEN komputer.
    # WHY:  pomiar RAM z /proc ma sens tylko dla lokalnej Ollamy - dla zdalnego
    #       serwera (np. BC-250) pokazujemy zamiast tego dane z jego API.
    host = (urlparse(adres).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", socket.gethostname().lower())


def _adres_ip_lan():
    # WHAT: adres IP tego komputera w sieci lokalnej (np. "192.168.0.10") albo None.
    # WHY:  "connect" na gnieździe UDP niczego nie wysyła - system tylko wybiera
    #       interfejs, przez który poszedłby ruch, i wiemy, jaki ma adres. Pewniejsze
    #       niż gethostbyname(hostname), które na Debianie często zwraca 127.0.1.1.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 9))
            adres = s.getsockname()[0]
    except OSError:
        return None
    return None if adres.startswith("127.") else adres


def _vram_lokalnie_calkowita():
    # WHAT: całkowita pamięć karty graficznej TEGO komputera w bajtach, albo None.
    # WHY:  API Ollamy podaje tylko, ile VRAM zajmują modele (/api/ps), a nie ile
    #       ma karta - bez tego pasek "7,5 / 12 GB" z makiety nie ma mianownika.
    #       AMD: sysfs, czytelny bez roota (bierzemy cardN, nie renderD - to ta
    #       sama karta drugi raz). NVIDIA: nvidia-smi, jeśli jest. Zdalnych
    #       hostów nie da się tak zmierzyć, tam pokazujemy samo zużycie.
    suma = 0
    for plik in Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_vram_total"):
        try:
            suma += int(plik.read_text().strip())
        except (OSError, ValueError):
            pass
    if suma:
        return suma
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            mib = sum(int(l) for l in r.stdout.split() if l.isdigit())
            if mib:
                return mib * 1024 ** 2
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _gtt_lokalnie_zajete():
    # WHAT: ile zwykłego RAM-u zajmuje karta graficzna TEGO komputera (AMD: GTT), w bajtach.
    # WHY:  na APU (zgłoszenie użytkownika: 16 GB wydzielone na VRAM, reszta modelu w RAM)
    #       Ollama liczy cały model jako "w VRAM", a pamięci GTT przydziela jądro - nie ma
    #       jej w pamięci procesów (_ollama_pamiec_procesow). Bez tego karta RAM pokazywała
    #       0,9 GB przy 15 GB modelu w RAM. Liczy się całe GTT karty (z pulpitem - zwykle
    #       kilkaset MB), bo sterownik nie dzieli go na programy. NVIDIA - brak takiego odczytu.
    suma = 0
    for plik in Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_gtt_used"):
        try:
            suma += int(plik.read_text().strip())
        except (OSError, ValueError):
            pass
    return suma


def sprawdz_host(serwer):
    # WHAT: stan jednego hosta z listy serwerów do ekranu "Hosty Ollama":
    #       czy odpowiada, wersja Ollamy, czas odpowiedzi, modele (na dysku /
    #       w pamięci), zajęty VRAM i RAM, a dla tego komputera także stan usługi systemd.
    # WHY:  czas odpowiedzi mierzymy na /api/version - najlżejsze zapytanie API,
    #       więc liczba to głównie sieć, a nie praca serwera.
    adres = serwer["adres"].rstrip("/")
    wynik = {
        "nazwa": serwer["nazwa"], "adres": adres, "lokalny": _adres_lokalny(adres),
        "online": False, "wersja": None, "ping_ms": None, "modele": [],
        "vram_uzyte": 0, "vram_calkowite": None, "ram_uzyte": 0, "ram_calkowite": None,
        "ram_procesy": None, "usluga_aktywna": None, "usluga_autostart": None, "sprawdzono": time.time(),
    }
    if wynik["lokalny"]:
        wynik["vram_calkowite"] = _vram_lokalnie_calkowita()
        wynik["usluga_aktywna"] = _systemctl_query("is-active") == "active"
        wynik["usluga_autostart"] = _systemctl_query("is-enabled") == "enabled"
    try:
        start = time.monotonic()
        r = requests.get(f"{adres}/api/version", timeout=3)
        r.raise_for_status()
        wynik["ping_ms"] = (time.monotonic() - start) * 1000
        wynik["wersja"] = r.json().get("version")
        wynik["online"] = True
        klient = OllamaClient(base_url=adres)
        zaladowane = {m.get("name"): m for m in klient.list_loaded()}
        wynik["vram_uzyte"] = sum(m.get("size_vram", 0) for m in zaladowane.values())
        # WHAT: RAM - dla tego komputera zmierzony z procesów Ollamy (pamięć procesów
        #       + zmapowane pliki modeli, _ollama_pamiec_procesow), dla zdalnego hosta
        #       to, co podaje jego API: część modeli, której nie zmieścił w VRAM.
        # WHY:  prośba użytkownika ("żeby było widać też normalny RAM") - sam VRAM
        #       zaniżał zużycie, gdy model nie mieści się na karcie.
        wynik["ram_uzyte"] = sum(max(0, m.get("size", 0) - m.get("size_vram", 0)) for m in zaladowane.values())
        if wynik["lokalny"]:
            wynik["ram_calkowite"] = ProbnikZasobow._ram()[1]
            procesy = _ollama_pamiec_procesow()
            if procesy:
                procesy["gtt"] = _gtt_lokalnie_zajete()
                wynik["ram_procesy"] = procesy
                wynik["ram_uzyte"] = procesy["anon"] + procesy["pliki"] + procesy["gtt"]
        for m in klient.list_models():
            zal = zaladowane.get(m["name"])
            wynik["modele"].append({
                "nazwa": m["name"], "rozmiar": m["size"],
                "kwantyzacja": m["quantization_level"], "w_pamieci": zal is not None,
                "vram": zal.get("size_vram", 0) if zal else 0,
                "ram": max(0, zal.get("size", 0) - zal.get("size_vram", 0)) if zal else 0,
            })
    except (requests.RequestException, ValueError):
        pass  # WHY: host nie odpowiada - zostaje "offline", ekran pokaże podpowiedź
    return wynik


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


PAKIETY_OPCJONALNE_WEBUI = ["ffmpeg", "pandoc", "zstd"]


def webui_zainstaluj_opcjonalne():
    # WHAT: instaluje pakiety systemowe potrzebne do PEŁNEJ funkcjonalności WebUI
    #       (transkrypcja audio, import PDF/Word do RAG) - ffmpeg, pandoc, zstd.
    # WHY:  to jedyny krok instalacji WebUI wymagający roota - reszta (webui_zainstaluj,
    #       'uv tool install') celowo działa bez uprawnień administratora. Robimy to
    #       przez ten sam pkexec (dialog polkit KDE) co start/stop usługi Ollama, ale
    #       TYLKO na wyraźne życzenie użytkownika (patrz klik_webui) - te pakiety są
    #       opcjonalne, WebUI działa i bez nich, tylko bez tych dwóch funkcji.
    _pkexec(["apt-get", "install", "-y", *PAKIETY_OPCJONALNE_WEBUI])


def _webui_port():
    try:
        return int(_ustawienia().value("webui/port", WEBUI_PORT_DOMYSLNY))
    except (TypeError, ValueError):
        return WEBUI_PORT_DOMYSLNY


def _webui_url():
    return f"http://localhost:{_webui_port()}"


def webui_ustawienie(klucz, domyslnie):
    # WHAT: przełączniki z karty "Ustawienia" ekranu Open WebUI (QSettings "webui/...").
    return _ustawienia().value(f"webui/{klucz}", domyslnie, type=bool)


def webui_zapisz_ustawienie(klucz, wartosc):
    _ustawienia().setValue(f"webui/{klucz}", wartosc)


def _webui_dziala():
    try:
        r = requests.get(_webui_url(), timeout=1)
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
        f"ExecStart={binarka} serve --port {_webui_port()}\n"
        f"Environment=OLLAMA_BASE_URL={OLLAMA_URL}\n"
        # WHY: WEBUI_AUTH=False tylko gdy użytkownik świadomie wyłączył logowanie -
        #      Open WebUI pozwala na to wyłącznie przy pustej bazie kont (patrz
        #      webui_liczba_kont), w innym wypadku logowanie przestałoby działać.
        + ("" if webui_ustawienie("logowanie", True) else "Environment=WEBUI_AUTH=False\n") +
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    sciezka = _webui_service_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(tresc)
    _systemctl_user(["daemon-reload"])


def webui_katalog_danych():
    # WHAT: katalog z danymi Open WebUI (baza czatów, wgrane pliki) albo None.
    # WHY:  'uv tool install' trzyma pakiet w ~/.local/share/uv/tools/open-webui,
    #       a WebUI domyślnie zapisuje dane OBOK swojego kodu (open_webui/data,
    #       sprawdzone w env.py 0.11.3: DATA_DIR = BACKEND_DIR / "data").
    for kandydat in sorted((Path.home() / ".local/share/uv/tools/open-webui/lib").glob(
            "python3*/site-packages/open_webui/data")):
        if kandydat.is_dir():
            return kandydat
    return None


def _webui_baza():
    katalog = webui_katalog_danych()
    baza = katalog / "webui.db" if katalog else None
    return baza if baza and baza.exists() else None


def webui_liczba_kont():
    # WHAT: ile kont jest w bazie Open WebUI (None = nie da się sprawdzić).
    # WHY:  przełącznik "Wymagaj logowania" - WEBUI_AUTH=False działa tylko
    #       przy pustej bazie kont (routers/auths.py, 0.11.3: inaczej błąd
    #       EXISTING_USERS i nikt się nie zaloguje).
    baza = _webui_baza()
    if not baza:
        return 0 if webui_katalog_danych() else None
    try:
        with sqlite3.connect(f"file:{baza}?mode=ro", uri=True, timeout=2) as polaczenie:
            return polaczenie.execute('SELECT COUNT(*) FROM "user"').fetchone()[0]
    except sqlite3.Error:
        return None


def webui_polaczenia_openai():
    # WHAT: adresy połączeń "OpenAI API" zapisane w bazie Open WebUI.
    # WHY:  pokazujemy na ekranie WebUI, czy jest już podłączone do routera
    #       LiteLLM - WebUI trzyma to w swojej bazie (tabela config, klucz
    #       "openai.api_base_urls"), nie w zmiennych środowiskowych.
    baza = _webui_baza()
    if not baza:
        return []
    try:
        with sqlite3.connect(f"file:{baza}?mode=ro", uri=True, timeout=2) as polaczenie:
            wiersz = polaczenie.execute(
                "SELECT value FROM config WHERE key = 'openai.api_base_urls'").fetchone()
            wlaczone = polaczenie.execute(
                "SELECT value FROM config WHERE key = 'openai.enable'").fetchone()
    except sqlite3.Error:
        return []
    if wlaczone and str(wlaczone[0]).lower() == "false":
        return []
    try:
        return json.loads(wiersz[0]) if wiersz else []
    except (TypeError, ValueError):
        return []


def webui_podlaczone_do_routera():
    adresy = [a.rstrip("/") for a in webui_polaczenia_openai()]
    port = urlparse(LITELLM_URL).port or 4000
    return any(urlparse(a).port == port for a in adresy)


def webui_kopia_zapasowa(plik_zip):
    # WHAT: pakuje katalog danych Open WebUI do jednego pliku .zip.
    # WHY:  bazę kopiujemy przez sqlite3.backup, a nie zwykłe kopiowanie pliku -
    #       WebUI może akurat do niej pisać, a backup daje spójną kopię.
    katalog = webui_katalog_danych()
    if not katalog:
        raise RuntimeError(_("Nie znaleziono danych Open WebUI."))
    with tempfile.TemporaryDirectory() as tymczasowy:
        cel = Path(tymczasowy) / "open-webui-data"
        shutil.copytree(katalog, cel, ignore=shutil.ignore_patterns("webui.db*"))
        baza = katalog / "webui.db"
        if baza.exists():
            with sqlite3.connect(f"file:{baza}?mode=ro", uri=True) as zrodlo, \
                    sqlite3.connect(cel / "webui.db") as kopia:
                zrodlo.backup(kopia)
        plik_zip = str(plik_zip)
        if plik_zip.endswith(".zip"):
            plik_zip = plik_zip[:-4]
        shutil.make_archive(plik_zip, "zip", tymczasowy, "open-webui-data")


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
#  SearXNG - wyszukiwarka dla czatu, instalowana dla użytkownika (jak Open WebUI)
# =============================================================================
# WHAT: SearXNG odpytuje zwykłe wyszukiwarki (Google, Bing, DuckDuckGo...) z tego
#       komputera i oddaje wyniki w JSON - źródło wyników dla wyszukiwania w czacie.
# WHY:  bez konta i klucza, w całości w katalogu domowym. NIE przez 'uv tool install':
#       SearXNG nie ma pakietu na PyPI (pod nazwą "searxng" jest tam cudzy, inny
#       projekt), a jego setup.py przy budowaniu importuje sam program - w czystym
#       środowisku budowania uv brakuje mu bibliotek. Dlatego jak w oficjalnej
#       instrukcji: środowisko (uv venv), najpierw zależności, potem program bez
#       izolacji budowania.
SEARXNG_PORT = 8888
SEARXNG_URL = f"http://127.0.0.1:{SEARXNG_PORT}"
_SEARXNG_COMMIT_API = "https://api.github.com/repos/searxng/searxng/commits/master"
_SEARXNG_ARCHIWUM = "https://codeload.github.com/searxng/searxng/tar.gz/{sha}"


def _searxng_katalog():
    # WHY: w katalogu danych LamaDeck, nie programu - install.sh --uninstall kasuje program
    return _katalog_danych() / NAZWA_PROGRAMU / "searxng"


def _searxng_python():
    return _searxng_katalog() / "venv" / "bin" / "python"


def _searxng_plik_wersji():
    return _searxng_katalog() / "wersja.json"


def _searxng_settings_sciezka():
    return _katalog_config() / NAZWA_PROGRAMU / "searxng" / "settings.yml"


def _searxng_service_sciezka():
    return Path.home() / ".config" / "systemd" / "user" / "searxng.service"


def searxng_zainstalowane():
    # WHY: plik wersji powstaje na samym końcu instalacji - przerwana instalacja
    #      nie udaje gotowej
    return _searxng_plik_wersji().exists() and (_searxng_katalog() / "venv" / "bin" / "granian").exists()


def _searxng_commit_master():
    # WHAT: najnowszy commit gałęzi master SearXNG: (sha, wersja "RRRR.MM.DD.GGMM").
    # WHY:  SearXNG nie wydaje wersji - rozwija się ciągle na master. Wersją jest data
    #       commita (z godziną, bo bywa kilka commitów dziennie); SearXNG sam nazywa
    #       wersje datą + skrótem commita.
    r = requests.get(_SEARXNG_COMMIT_API, timeout=10, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    dane = r.json()
    data = dane["commit"]["committer"]["date"]  # np. 2026-10-30T18:05:25Z
    wersja = f"{data[0:4]}.{data[5:7]}.{data[8:10]}.{data[11:13]}{data[14:16]}"
    return dane["sha"], wersja


def searxng_wersja_lokalna():
    try:
        return json.loads(_searxng_plik_wersji().read_text(encoding="utf-8"))["wersja"]
    except (OSError, ValueError, KeyError):
        return None


def searxng_wersja_najnowsza():
    return _searxng_commit_master()[1]


def _searxng_zapisz_settings():
    # WHAT: settings.yml SearXNG - tylko różnice względem domyślnych (use_default_settings).
    # WHY:  - json w "formats": domyślnie SearXNG oddaje tylko HTML, a program czyta JSON;
    #       - 127.0.0.1: wyszukiwarka tylko dla tego komputera, nie dla całej sieci;
    #       - limiter wyłączony: chroni publiczne instancje przed botami i wymaga
    #         osobnej bazy (Valkey) - tu jedynym "klientem" jest ten program;
    #       - secret_key losowy, generowany raz (domyślny SearXNG odrzuca przy starcie).
    #       Istniejący plik zostaje - ktoś mógł w nim coś zmienić (np. wyszukiwarki).
    sciezka = _searxng_settings_sciezka()
    if sciezka.exists():
        return
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(
        "# SearXNG dla LamaDeck - tylko zmiany względem ustawień domyślnych.\n"
        "# Pełna lista: https://docs.searxng.org/admin/settings/\n"
        "use_default_settings: true\n"
        "general:\n"
        "  instance_name: \"LamaDeck SearXNG\"\n"
        "server:\n"
        f"  port: {SEARXNG_PORT}\n"
        "  bind_address: \"127.0.0.1\"\n"
        f"  secret_key: \"{secrets.token_hex(32)}\"\n"
        "  limiter: false\n"
        "  public_instance: false\n"
        "search:\n"
        "  formats:\n"
        "    - html\n"
        "    - json\n",
        encoding="utf-8")
    sciezka.chmod(0o600)  # WHY: zawiera secret_key


def _zapisz_searxng_unit():
    # WHAT: usługa systemd --user SearXNG (serwer granian, jak w oficjalnym kontenerze).
    katalog = _searxng_katalog()
    tresc = (
        "[Unit]\n"
        "Description=SearXNG (LamaDeck)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        f"Environment=SEARXNG_SETTINGS_PATH={_searxng_settings_sciezka()}\n"
        # WHY: --blocking-threads 4 jak w oficjalnym kontenerze - domyślnie granian
        #      pozwala na 512 wątków (ostrzeżenie w logu), a tu pyta jeden program
        f"ExecStart={katalog}/venv/bin/granian --interface wsgi --host 127.0.0.1 "
        f"--port {SEARXNG_PORT} --blocking-threads 4 searx.webapp:app\n"
        f"WorkingDirectory={katalog}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    sciezka = _searxng_service_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(tresc)
    _systemctl_user(["daemon-reload"])


def _uv_uruchom(polecenie, opis):
    wynik = subprocess.run(polecenie, capture_output=True, text=True, timeout=None)
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip()[-2000:] or _("{opis}: nieznany błąd").format(opis=opis))


def searxng_zainstaluj():
    # WHAT: pobiera SearXNG (archiwum z GitHuba, bez gita), instaluje go w osobnym
    #       środowisku w katalogu domowym i zapisuje ustawienia oraz usługę --user.
    #       Ta sama funkcja aktualizuje - instaluje od nowa najnowszy stan.
    uv = _uv_zapewnij()
    sha, wersja = _searxng_commit_master()
    katalog = _searxng_katalog()
    katalog.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=katalog) as tmp:
        tmp = Path(tmp)
        archiwum = tmp / "searxng.tar.gz"
        with requests.get(_SEARXNG_ARCHIWUM.format(sha=sha), stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(archiwum, "wb") as f:
                for kawalek in r.iter_content(1 << 16):
                    f.write(kawalek)
        with tarfile.open(archiwum) as tar:
            # WHY: filtr "data" odrzuca ścieżki poza katalogiem i pliki specjalne (Python 3.12+)
            if hasattr(tarfile, "data_filter"):
                tar.extractall(tmp, filter="data")
            else:
                tar.extractall(tmp)
        zrodla = next(p for p in tmp.iterdir() if p.is_dir())
        # WHY: SearXNG bierze wersję z gita - w archiwum go nie ma i import kończyłby się
        #      błędem. version_frozen.py to jego własny sposób na "zamrożoną" wersję.
        (zrodla / "searx" / "version_frozen.py").write_text(
            f'VERSION_STRING = "{wersja}+{sha[:7]}"\n'
            f'VERSION_TAG = "{wersja}+{sha[:7]}"\n'
            f'DOCKER_TAG = "{wersja}-{sha[:7]}"\n'
            'GIT_URL = "https://github.com/searxng/searxng"\n'
            'GIT_BRANCH = "master"\n', encoding="utf-8")
        venv = katalog / "venv"
        python = str(_searxng_python())
        # WHY: nowe środowisko przy każdej instalacji - aktualizacja nie zostawia starych bibliotek
        _uv_uruchom([uv, "venv", "--clear", "--python", "3.12", str(venv)], "uv venv")
        _uv_uruchom([uv, "pip", "install", "--python", python, "setuptools", "wheel",
                     "-r", str(zrodla / "requirements.txt"), "-r", str(zrodla / "requirements-server.txt")],
                    "uv pip install")
        _uv_uruchom([uv, "pip", "install", "--python", python, "--no-build-isolation", str(zrodla)],
                    "uv pip install searxng")
    _searxng_zapisz_settings()
    _zapisz_searxng_unit()
    _searxng_plik_wersji().write_text(json.dumps({"wersja": wersja, "sha": sha}), encoding="utf-8")


def searxng_aktualizuj():
    # WHY: działający serwer trzyma stare pliki w pamięci - po aktualizacji restart
    dzialal = _searxng_dziala()
    searxng_zainstaluj()
    if dzialal:
        _systemctl_user(["restart", "searxng.service"])


def _searxng_dziala():
    try:
        return requests.get(f"{SEARXNG_URL}/healthz", timeout=1).status_code == 200
    except requests.RequestException:
        return False


def searxng_uruchom():
    if _searxng_dziala():
        return
    _zapisz_searxng_unit()
    _systemctl_user(["start", "searxng.service"])
    # WHY: pierwszy start wczytuje listę wyszukiwarek - zwykle kilka sekund
    for _ in range(60):
        if _searxng_dziala():
            return
        time.sleep(1)
    raise RuntimeError(_("SearXNG nie odpowiedział w ciągu minuty. Log usługi: ekran Dziennik."))


def searxng_zatrzymaj():
    _systemctl_user(["stop", "searxng.service"])


def searxng_autostart_wlaczony():
    try:
        r = subprocess.run(["systemctl", "--user", "is-enabled", "searxng.service"],
                           capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() == "enabled"


def searxng_autostart(wlacz):
    if wlacz:
        _zapisz_searxng_unit()
        _systemctl_user(["enable", "--now", "searxng.service"])
    else:
        _systemctl_user(["disable", "--now", "searxng.service"])


def searxng_usun():
    # WHAT: zatrzymuje i usuwa SearXNG: usługę, środowisko i ustawienia.
    try:
        _systemctl_user(["disable", "--now", "searxng.service"])
    except (RuntimeError, OSError):
        pass
    _searxng_service_sciezka().unlink(missing_ok=True)
    try:
        _systemctl_user(["daemon-reload"])
    except (RuntimeError, OSError):
        pass
    shutil.rmtree(_searxng_katalog(), ignore_errors=True)
    shutil.rmtree(_searxng_settings_sciezka().parent, ignore_errors=True)


# =============================================================================
#  Wyszukiwanie w internecie - źródła wyników dla czatu
# =============================================================================
# WHAT: źródło wyników (QSettings wyszukiwanie/zrodlo). Domyślnie wyłączone -
#       zapytania wychodzą do internetu, więc to świadomy wybór użytkownika.
ZRODLA_WYSZUKIWANIA = ["wylaczone", "searxng", "searxng_adres", "ollama"]
OLLAMA_WEB_SEARCH = "https://ollama.com/api/web_search"


def wyszukiwanie_ustawienie(klucz, domyslnie=""):
    return _ustawienia().value(f"wyszukiwanie/{klucz}", domyslnie)


def wyszukiwanie_zapisz(klucz, wartosc):
    _ustawienia().setValue(f"wyszukiwanie/{klucz}", wartosc)


def wyszukaj_w_internecie(zapytanie, ile=5, zrodlo=None):
    # WHAT: wyniki wyszukiwania z wybranego źródła: [{"tytul", "url", "tresc"}].
    # WHY:  jeden format dla obu źródeł - czat nie musi wiedzieć, skąd są wyniki.
    zrodlo = zrodlo or wyszukiwanie_ustawienie("zrodlo", "wylaczone")
    if zrodlo in ("searxng", "searxng_adres"):
        adres = SEARXNG_URL if zrodlo == "searxng" else wyszukiwanie_ustawienie("searxng_adres", "").rstrip("/")
        if not adres:
            raise RuntimeError(_("Nie podano adresu SearXNG."))
        try:
            r = requests.get(f"{adres}/search", params={"q": zapytanie, "format": "json"}, timeout=20)
        except requests.ConnectionError:
            raise RuntimeError(_("SearXNG pod adresem {adres} nie odpowiada.").format(adres=adres)) from None
        if r.status_code == 403:
            # WHY: najczęstszy problem z cudzym SearXNG - format JSON jest domyślnie wyłączony
            raise RuntimeError(_("SearXNG odmówił wyników w formacie JSON - w jego settings.yml dopisz "
                                 "\"json\" w search: formats."))
        r.raise_for_status()
        wyniki = r.json().get("results", [])
        return [{"tytul": w.get("title", ""), "url": w.get("url", ""), "tresc": w.get("content", "")}
                for w in wyniki if w.get("url")][:ile]
    if zrodlo == "ollama":
        klucz = wyszukiwanie_ustawienie("klucz_ollama", "")
        if not klucz:
            raise RuntimeError(_("Brak klucza API Ollamy (ollama.com/settings/keys)."))
        r = requests.post(OLLAMA_WEB_SEARCH, json={"query": zapytanie, "max_results": min(ile, 10)},
                          headers={"Authorization": f"Bearer {klucz}"}, timeout=30)
        if r.status_code in (401, 403):
            raise RuntimeError(_("Ollama odrzuciła klucz API - sprawdź go na ollama.com/settings/keys."))
        r.raise_for_status()
        return [{"tytul": w.get("title", ""), "url": w.get("url", ""), "tresc": w.get("content", "")}
                for w in r.json().get("results", [])]
    raise RuntimeError(_("Wyszukiwanie w internecie jest wyłączone."))


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
    # WHY: obok ustawień programu (~/.config/LamaDeck) - jeden katalog zamiast dwóch
    return _katalog_config() / NAZWA_PROGRAMU / "litellm_config.yaml"


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


def _litellm_wylaczone():
    # WHAT: zbiór modeli ODZNACZONYCH w karcie "Modele w agregatorze", jako
    #       klucze "adres|model" (np. "http://192.168.0.236:11434|qwen3:32b").
    # WHY:  pamiętamy WYŁĄCZONE, a nie włączone - nowy model na hoście domyślnie
    #       trafia do LiteLLM (jak dotąd), bez potrzeby zaznaczania go ręcznie.
    #       Para adres+model, nie sama nazwa: ten sam model bywa na kilku hostach
    #       i można chcieć serwować go np. tylko z BC-250.
    return set(_ustawienia().value("litellm/wylaczone_modele", [], type=list))


def _ustaw_litellm_model_wylaczony(adres, model, wylacz):
    wylaczone = _litellm_wylaczone()
    klucz = f"{adres}|{model}"
    if wylacz:
        wylaczone.add(klucz)
    else:
        wylaczone.discard(klucz)
    _ustawienia().setValue("litellm/wylaczone_modele", sorted(wylaczone))


def _modele_serwowane(wpisy):
    # WHAT: z wykrytych (nazwa_hosta, model, adres) zostawia tylko te, których
    #       użytkownik NIE odznaczył - to, co realnie trafia do configu LiteLLM.
    wylaczone = _litellm_wylaczone()
    return [w for w in wpisy if f"{w[2]}|{w[1]}" not in wylaczone]


def _litellm_klucz():
    # WHAT: klucz API (master key) LiteLLM, gdy włączona opcja "Wymagaj klucza
    #       API" - w przeciwnym razie None (agregator bez hasła).
    # WHY:  opcja na wyraźną prośbę użytkownika ("jednym będzie potrzebne,
    #       innym nie") - np. laptop w obcej sieci z kluczem, dom bez. Klucz
    #       generowany RAZ (losowy, bezpieczny - moduł secrets) i zapamiętany w
    #       QSettings, żeby nie zmieniał się przy każdym restarcie - inaczej
    #       klienci (np. Continue) traciliby połączenie co restart.
    ustawienia = _ustawienia()
    if not ustawienia.value("litellm/wymagaj_klucza", False, type=bool):
        return None
    klucz = ustawienia.value("litellm/klucz", "")
    if not klucz:
        klucz = "sk-" + secrets.token_hex(24)
        ustawienia.setValue("litellm/klucz", klucz)
    return klucz


def _ustaw_litellm_wymagaj_klucza(wlacz):
    _ustawienia().setValue("litellm/wymagaj_klucza", bool(wlacz))


# WHAT: strategie wyboru hosta, gdy ten sam model/alias jest na kilku hostach.
# WHY:  nazwy sprawdzone w zainstalowanym LiteLLM 1.105 (router.py,
#       RoutingStrategyName). "simple-shuffle" rozkłada zapytania losowo
#       (w praktyce "na zmianę"), "least-busy" - host z najmniejszą kolejką,
#       "latency-based-routing" - najszybszy.
LITELLM_STRATEGIE = ["simple-shuffle", "least-busy", "latency-based-routing"]


def _litellm_strategia():
    wartosc = _ustawienia().value("litellm/strategia", LITELLM_STRATEGIE[0])
    return wartosc if wartosc in LITELLM_STRATEGIE else LITELLM_STRATEGIE[0]


def _ustaw_litellm_strategie(strategia):
    _ustawienia().setValue("litellm/strategia", strategia)


def _litellm_aliasy():
    # WHAT: aliasy routera z ekranu "Router LiteLLM": [{"alias", "model", "hosty"}].
    #       "hosty" to lista adresów; pusta = wszystkie hosty, które mają ten model.
    # WHY:  alias ("czat", "kod") to stała nazwa dla programów - zmiana modelu za
    #       aliasem nie wymaga przestawiania Continue/Open WebUI.
    try:
        lista = json.loads(_ustawienia().value("litellm/aliasy", "[]") or "[]")
    except (TypeError, ValueError):
        return []
    return [a for a in lista if isinstance(a, dict) and a.get("alias") and a.get("model")]


def _zapisz_litellm_aliasy(aliasy):
    _ustawienia().setValue("litellm/aliasy", json.dumps(aliasy))


def _wpisy_aliasow(wykryte):
    # WHAT: (alias, model, adres) dla każdego hosta, na którym model aliasu jest.
    wynik = []
    for a in _litellm_aliasy():
        for _nazwa_hosta, model, adres in wykryte:
            if model == a["model"] and (not a.get("hosty") or adres in a["hosty"]):
                wynik.append((a["alias"], model, adres))
    return wynik


def _zbuduj_config_litellm(serwery):
    # WHAT: generuje treść config.yaml dla LiteLLM - jeden wpis w model_list
    #       na każdy model na każdym hoście.
    # WHY:  format jest prosty (lista słowników o 3 polach tekstowych), więc
    #       piszemy YAML ręcznie zamiast dociągać zależność 'pyyaml'.
    def _yaml_str(tekst):
        return '"' + tekst.replace("\\", "\\\\").replace('"', '\\"') + '"'

    # WHY: LiteLLM od ok. 1.105 (2026-10) ODMAWIA startu bez master key
    #      ("LiteLLM proxy refused to start: no master key is set") - na świeżej
    #      instalacji (najnowsza wersja) usługa padała od razu, a starsze
    #      instalacje (np. 1.100) działały dalej. Dwa tryby (opcja "Wymagaj
    #      klucza API" w karcie LiteLLM, patrz _litellm_klucz): z kluczem -
    #      master_key w configu, klienci muszą go podać; bez klucza (domyślnie,
    #      jak dotąd) - jawnie zezwalamy na brak hasła. Uwaga: LiteLLM nasłuchuje
    #      na 0.0.0.0:4000 - bez klucza każdy w LAN może używać modeli. Starsze
    #      wersje ignorują nieznane im ustawienie (sprawdzone na 1.100).
    klucz = _litellm_klucz()
    if klucz:
        naglowek = ["general_settings:", f"  master_key: {_yaml_str(klucz)}"]
    else:
        naglowek = ["general_settings:", "  dangerously_permit_weak_or_unset_master_key: true"]
    # WHY: strategia dotyczy modeli/aliasów dostępnych na kilku hostach naraz
    naglowek += ["router_settings:", f"  routing_strategy: {_litellm_strategia()}"]
    wykryte = _wykryj_modele_na_serwerach(serwery)
    # WHY: aliasy działają także dla modeli odznaczonych na liście "Wszystkie
    #      modele" - odznaczenie ukrywa nazwę modelu, nie blokuje aliasu.
    wpisy = [(model, model, adres) for _n, model, adres in _modele_serwowane(wykryte)]
    wpisy += _wpisy_aliasow(wykryte)
    if not wpisy:
        return "\n".join(naglowek + ["model_list: []"]) + "\n"

    linie = naglowek + ["model_list:"]
    for nazwa, model, adres in wpisy:
        linie.append(f"  - model_name: {_yaml_str(nazwa)}")
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
        # WHY: z włączonym kluczem LiteLLM odrzuci zapytanie bez niego; bez
        #      klucza wystarczy dowolny niepusty tekst (Continue wymaga pola).
        linie.append(f"    apiKey: {_litellm_klucz() or 'sk-anything'}")
        linie.append("    roles:")
        linie.append("      - chat")
        linie.append("      - edit")
        linie.append("      - apply")
    return "\n".join(linie) + "\n"


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

# WHAT: pliki .gguf, które NIE są wariantem modelu: projektor obrazu (mmproj),
#       macierz ważności (imatrix) i głowice MTP do dekodowania spekulacyjnego.
# BUG naprawiony: dawniej mmproj-BF16.gguf (0,9 GB) pokazywał się jako wariant
#       "BF16" modelu 27B, a MTP/mtp-...-Q4_0.gguf doliczał się do rozmiaru Q4_0
#       (sprawdzone na unsloth/Qwen3.8-27B-GGUF).
_PLIK_POMOCNICZY_GGUF = re.compile(r"(^|[/_-])(mmproj|imatrix|mtp)([/_.-]|$)", re.IGNORECASE)


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
        if not sciezka.lower().endswith(".gguf") or _PLIK_POMOCNICZY_GGUF.search(sciezka):
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
        {"id": wpis.get("id", ""), "pobrania": wpis.get("downloads", 0), "tagi": wpis.get("tags") or []}
        for wpis in r.json() if wpis.get("id")
    ]


# WHAT: rozpoznawanie modeli MoE (mixture of experts) dla pola "Tylko modele MoE".
# WHY:  ani ollama.com, ani Hugging Face nie mają takiego filtra - sprawdzone na
#       żywych wynikach (2026-10-28): MoE widać w opisie ("Mixture of Experts",
#       "MoE"), w tagach HF ("moe", "mixture-of-experts"), w nazwie rozmiaru
#       ("8x7b") albo aktywnych parametrów ("30b-a3b", "30B-A3B"). Część rodzin
#       nie mówi tego nigdzie (np. gpt-oss, qwen3-coder) - stąd krótka lista
#       znanych rodzin MoE. Rozpoznawanie nie jest pełne i okno mówi to uczciwie.
_WZORZEC_MOE = re.compile(
    r"\bmoe\b|mixture[- ]of[- ]experts|mixtral|\d+x\d+(\.\d+)?b\b|[-_]a\d+(\.\d+)?b\b", re.IGNORECASE)
_RODZINY_MOE = re.compile(
    r"gpt-oss|qwen3-coder|qwen3-next|deepseek-v[23]|llama4|glm-4\.[5-9]|olmoe|dbrx|jamba|kimi-k2|phi-3\.5-moe",
    re.IGNORECASE)


def jest_moe(wynik):
    # WHAT: czy wynik wyszukiwania (Ollama albo HF) wygląda na model MoE.
    if wynik.get("moe_z_tagow"):
        return True
    tekst = " ".join([wynik.get("id", ""), wynik.get("opis", "")]
                     + list(wynik.get("tagi", [])) + list(wynik.get("rozmiary", [])))
    return bool(_WZORZEC_MOE.search(tekst) or _RODZINY_MOE.search(wynik.get("id", "")))


def uzupelnij_moe_z_tagow(wyniki):
    # WHAT: dla modeli z Ollamy, których opis/nazwa nie zdradza MoE, sprawdza tagi
    #       wariantów (np. qwen3.6: "35b-a3b-...") i zapisuje wynik w "moe_z_tagow".
    # WHY:  tagi na ollama.com mówią o MoE dużo pewniej niż opisy (sprawdzone:
    #       qwen3.5/3.6, nemotron, qwen3.8-flash-next nie mówią tego w opisie).
    #       Osobne zapytanie na model, więc tylko przy włączonym filtrze MoE i
    #       równolegle - zwykle ok. sekundy zamiast kilkunastu.
    do_sprawdzenia = [w for w in wyniki if w.get("zrodlo") == "ollama"
                      and "moe_z_tagow" not in w and not jest_moe(w)]

    def _sprawdz(wynik):
        try:
            wynik["moe_z_tagow"] = any(wariant_moe(w[0]) for w in ollama_warianty_modelu(wynik["id"]))
        except (requests.RequestException, ValueError):
            pass  # WHY: błąd jednego modelu nie psuje reszty - zostaje "nie-MoE"

    with ThreadPoolExecutor(max_workers=8) as pula:
        list(pula.map(_sprawdz, do_sprawdzenia))
    return wyniki


def wariant_moe(tag):
    # WHAT: czy konkretny wariant (tag Ollamy / kwantyzacja HF) to wersja MoE,
    #       np. "30b-a3b-q4_K_M" albo "8x7b" - dla rodzin z wariantami zwykłymi i MoE.
    return bool(re.search(r"(^|[-_])a\d+(\.\d+)?b\b|\d+x\d+(\.\d+)?b\b|\bmoe\b", tag or "", re.IGNORECASE))


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
    # BUG naprawiony (2026-10): ollama.com zmieniła klasy CSS przy wynikach
    #      ("group w-full" -> "group flex ..."), stary parser szukał dokładnej
    #      klasy i po cichu zwracał ZERO wyników - w wyszukiwarce widać było
    #      tylko HF. Teraz parser nie zależy od klas linku: blok wyniku to link
    #      do /library/<nazwa> zawierający nagłówek <h2> (to odróżnia kartę
    #      wyniku od innych linków na stronie). Liczbę pobrań bierzemy najpierw
    #      z atrybutu title="22,347,504 downloads" (dokładna liczba), a gdy go
    #      nie ma - ze starego formatu "...&nbsp;Pulls".
    r = requests.get("https://ollama.com/search", params={"q": zapytanie}, timeout=10)
    r.raise_for_status()
    czesci = re.split(r'<a href="/library/([^"/:?#]+)"[^>]*>', r.text)
    wyniki = []
    for i in range(1, len(czesci), 2):
        blok = czesci[i + 1].split("</a>", 1)[0]
        if "<h2" not in blok:
            continue
        rozmiary = re.findall(r'bg-\[#ddf4ff\][^>]*>\s*([^<]+?)\s*<', blok)
        # WHY: modele TYLKO w chmurze Ollamy (znaczek "cloud", brak rozmiarów) nie
        #      dadzą się pobrać - pomijamy je, zamiast pokazywać wynik, który po
        #      kliknięciu kończy się "brak wariantów". Model z rozmiarami I wersją
        #      cloud zostaje (lokalne tagi da się pobrać).
        if not rozmiary and re.search(r">\s*cloud\s*<", blok):
            continue
        opis = re.search(r"<p[^>]*>(.*?)</p>", blok, re.S)
        pobrania = ""
        dokladnie = re.search(r'title="([\d,]+) (?:downloads|pulls)"', blok, re.IGNORECASE)
        if dokladnie:
            pobrania = _formatuj_liczbe(int(dokladnie.group(1).replace(",", "")))
        else:
            stary = re.search(r'>\s*([\d.,]+[KMB]?)\s*</span>\s*<span[^>]*>&nbsp;Pulls', blok)
            pobrania = stary.group(1) if stary else ""
        wyniki.append({
            "zrodlo": "ollama",
            "id": czesci[i],
            "opis": html.unescape(re.sub(r"<[^>]+>|\s+", " ", opis.group(1))).strip() if opis else "",
            "mozliwosci": re.findall(r'bg-indigo-50[^>]*>\s*([^<]+?)\s*<', blok),
            "rozmiary": rozmiary,
            "pobrania": pobrania,
        })
    # WHY: strona ma linki do modeli, a parser nic z niej nie wyczytał = znowu
    #      zmienił się jej wygląd. Zgłaszamy to jako błąd (SzukajModeliWorker
    #      pokaże komunikat), zamiast po cichu pokazywać same wyniki z HF.
    if not wyniki and "<h2" in r.text and 'href="/library/' in r.text and ">cloud<" not in r.text.replace(" ", ""):
        raise ValueError(_("Nie udało się odczytać wyników z ollama.com - strona mogła zmienić wygląd."))
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
                    "digest": m.get("digest", ""),
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
#  Modele na wszystkich hostach (ekran "Modele")
# =============================================================================
# WHAT: pamięć podręczna /api/show - (adres, model, digest) -> informacje.
# WHY:  /api/show to osobne zapytanie na KAŻDY model; jego wynik zmienia się
#       tylko po ponownym pobraniu modelu (nowy digest), więc pytamy raz.
_INFO_MODELI = {}


def ollama_info_modelu(adres, nazwa, digest=""):
    # WHAT: możliwości modelu (completion/tools/vision/embedding...) i długość
    #       kontekstu z /api/show (sprawdzone na Ollamie 0.33: pole "capabilities"
    #       i model_info "<architektura>.context_length").
    klucz = (adres, nazwa, digest)
    if klucz in _INFO_MODELI:
        return _INFO_MODELI[klucz]
    try:
        r = requests.post(f"{adres}/api/show", json={"model": nazwa}, timeout=5)
        r.raise_for_status()
        dane = r.json()
    except (requests.RequestException, ValueError):
        return {"mozliwosci": [], "kontekst": None}  # WHY: błędu nie zapamiętujemy - spróbujemy następnym razem
    kontekst = next((v for k, v in (dane.get("model_info") or {}).items()
                     if k.endswith(".context_length")), None)
    info = {"mozliwosci": dane.get("capabilities") or [], "kontekst": kontekst}
    _INFO_MODELI[klucz] = info
    return info


def rodzaj_modelu(nazwa, mozliwosci):
    # WHAT: filtr z makiety - "embedding" / "wizja" / "kod" / "czat".
    # WHY:  embedding i wizję mówi wprost Ollama (capabilities); "kod" nie jest
    #       osobną możliwością w API, więc rozpoznajemy go po nazwie (qwen2.5-coder,
    #       codellama, starcoder...).
    if "embedding" in mozliwosci:
        return "embedding"
    if "vision" in mozliwosci:
        return "wizja"
    if re.search(r"cod(e|er|estral)", nazwa, re.IGNORECASE):
        return "kod"
    return "czat"


def _kontekst_krotko(tokeny):
    # WHAT: 131072 -> "128k", 512 -> "512".
    if not tokeny:
        return ""
    return f"{tokeny // 1024}k" if tokeny >= 1024 else str(tokeny)


def zbierz_modele(serwery):
    # WHAT: modele ze WSZYSTKICH hostów, połączone po nazwie: rozmiar, kwantyzacja,
    #       kontekst, rodzaj i lista hostów (z informacją, czy model jest w pamięci).
    # WHY:  makieta pokazuje jeden kafelek na model z hostami jako etykietami -
    #       ten sam model na dwóch hostach to jeden kafelek, nie dwa.
    modele = {}
    hosty_online = []
    for serwer in serwery:
        klient = OllamaClient(base_url=serwer["adres"])
        if not klient.api_dziala():
            continue
        hosty_online.append(serwer)
        zaladowane = {m.get("name"): m for m in klient.list_loaded()}
        for m in klient.list_models():
            info = ollama_info_modelu(klient.base_url, m["name"], m.get("digest", ""))
            wpis = modele.setdefault(m["name"], {
                "nazwa": m["name"], "rozmiar": m["size"], "kwantyzacja": m["quantization_level"],
                "parametry": m["parameter_size"], "rodzina": m["family"], "modified_at": m["modified_at"],
                "kontekst": info["kontekst"], "mozliwosci": info["mozliwosci"],
                "rodzaj": rodzaj_modelu(m["name"], info["mozliwosci"]), "hosty": [],
            })
            zal = zaladowane.get(m["name"])
            wpis["hosty"].append({
                "adres": klient.base_url, "nazwa": serwer["nazwa"], "w_pamieci": zal is not None,
                "vram": zal.get("size_vram", 0) if zal else 0,
                "ram": max(0, zal.get("size", 0) - zal.get("size_vram", 0)) if zal else 0,
            })
    return sorted(modele.values(), key=lambda m: m["nazwa"].lower()), hosty_online


def ollama_zaladuj_model(adres, nazwa, embedding=False, zwolnij=False):
    # WHAT: ładuje model do pamięci (przycisk "Załaduj") albo go z niej zwalnia.
    # WHY:  Ollama nie ma osobnego "load" - zapytanie bez treści ładuje model,
    #       a keep_alive=0 każe go od razu zwolnić (docs.ollama.com/faq). Modele
    #       embeddingowe nie obsługują /api/generate, dla nich /api/embed.
    if embedding:
        url, dane = f"{adres}/api/embed", {"model": nazwa, "input": "ping"}
    else:
        url, dane = f"{adres}/api/generate", {"model": nazwa}
    if zwolnij:
        dane["keep_alive"] = 0
    r = requests.post(url, json=dane, timeout=600)  # WHY: duży model ładuje się nawet kilka minut
    if r.status_code >= 400:
        try:
            blad = r.json().get("error")
        except ValueError:
            blad = None
        raise RuntimeError(blad or f"HTTP {r.status_code}")


# =============================================================================
#  Zasoby komputera (karta "Zasoby komputera" na panelu głównym)
# =============================================================================
class ProbnikZasobow:
    """Odczyt CPU, RAM, GPU i ruchu sieciowego - bez roota, z /proc i sysfs.

    WHY: CPU i sieć to liczniki narastające - procent i prędkość wychodzą
         dopiero z różnicy dwóch odczytów, więc obiekt pamięta poprzedni.
    """

    def __init__(self):
        self._cpu = None
        self._siec = None

    def _cpu_procent(self):
        try:
            pola = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
        except (OSError, ValueError):
            return None
        bezczynny, razem = pola[3] + pola[4], sum(pola)  # WHY: idle + iowait
        poprzedni, self._cpu = self._cpu, (bezczynny, razem)
        if not poprzedni or razem == poprzedni[1]:
            return None
        return 100.0 * (1 - (bezczynny - poprzedni[0]) / (razem - poprzedni[1]))

    def _siec_bajty_na_s(self):
        try:
            linie = Path("/proc/net/dev").read_text().splitlines()[2:]
        except OSError:
            return None
        suma = 0
        for linia in linie:
            nazwa, _dw, dane = linia.partition(":")
            if nazwa.strip() == "lo":
                continue
            pola = dane.split()
            suma += int(pola[0]) + int(pola[8])  # WHY: odebrane + wysłane
        teraz = time.monotonic()
        poprzedni, self._siec = self._siec, (suma, teraz)
        if not poprzedni or teraz <= poprzedni[1]:
            return None
        return (suma - poprzedni[0]) / (teraz - poprzedni[1])

    @staticmethod
    def _ram():
        wartosci = {}
        try:
            for linia in Path("/proc/meminfo").read_text().splitlines():
                klucz, _dw, reszta = linia.partition(":")
                wartosci[klucz] = int(reszta.split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            return None, None
        return wartosci.get("MemTotal", 0) - wartosci.get("MemAvailable", 0), wartosci.get("MemTotal")

    @staticmethod
    def _gpu():
        # WHAT: (obciążenie %, VRAM zajęty, VRAM całkowity) - AMD z sysfs, NVIDIA z nvidia-smi.
        obciazenie, uzyte, calkowite = None, 0, 0
        for katalog in Path("/sys/class/drm").glob("card[0-9]*/device"):
            try:
                calk = int((katalog / "mem_info_vram_total").read_text())
                uzyte += int((katalog / "mem_info_vram_used").read_text())
                calkowite += calk
                obciazenie = max(obciazenie or 0, int((katalog / "gpu_busy_percent").read_text()))
            except (OSError, ValueError):
                continue
        if calkowite:
            return obciazenie, uzyte, calkowite
        if shutil.which("nvidia-smi"):
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
                wiersze = [[int(x) for x in l.split(",")] for l in r.stdout.strip().splitlines() if l]
                if wiersze:
                    return (max(w[0] for w in wiersze), sum(w[1] for w in wiersze) * 1024 ** 2,
                            sum(w[2] for w in wiersze) * 1024 ** 2)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        return None, None, None

    def probka(self):
        ram_uzyte, ram_calkowite = self._ram()
        gpu, vram_uzyte, vram_calkowite = self._gpu()
        return {
            "cpu": self._cpu_procent(), "ram_uzyte": ram_uzyte, "ram_calkowite": ram_calkowite,
            "gpu": gpu, "vram_uzyte": vram_uzyte, "vram_calkowite": vram_calkowite,
            "siec": self._siec_bajty_na_s(),
        }


# =============================================================================
#  Dziennik - wpisy z systemd (Ollama, LiteLLM, Open WebUI) + komunikaty programu
# =============================================================================
# WHAT: usługa w dzienniku -> (czy usługa użytkownika, nazwa jednostki systemd).
USLUGI_DZIENNIKA = {
    "Ollama": (False, f"{SERVICE_NAME}.service"),
    "LiteLLM": (True, "litellm.service"),
    "Open WebUI": (True, "open-webui.service"),
    "SearXNG": (True, "searxng.service"),
}

def _n(tekst):
    # WHAT: znacznik "ten tekst trafi do tłumaczeń" - sam niczego nie tłumaczy.
    # WHY:  wyjaśnienia błędów są tłumaczone dopiero przy wyświetlaniu (_() w
    #       wyjasnij_blad), bo język może się zmienić po starcie programu.
    return tekst


# WHAT: proste wyjaśnienia częstych błędów (makieta "Dziennik": "z prostym
#       wyjaśnieniem, co znaczą błędy"). Wzorzec -> tekst dla człowieka.
_WYJASNIENIA_BLEDOW = [
    (r"connection refused|connecterror|cannot connect|failed to establish|no route to host",
     _n("Usługa pod tym adresem nie odpowiada. Sprawdź, czy komputer jest włączony i czy działa na nim Ollama "
     "(a przy hoście zdalnym - czy nasłuchuje na adresie sieciowym, OLLAMA_HOST=0.0.0.0).")),
    (r"address already in use|errno 98",
     _n("Port jest zajęty przez inny program. Zatrzymaj tamten program albo zmień port tej usługi.")),
    (r"out of memory|cudamalloc failed|failed to allocate|insufficient memory|oom",
     _n("Zabrakło pamięci karty graficznej albo RAM. Wybierz mniejszy model lub kwantyzację, zmniejsz kontekst "
     "albo zwolnij inne modele z pamięci.")),
    (r"model ['\"]?[\w./:-]*['\"]? not found|file does not exist|no such model",
     _n("Tego modelu nie ma na hoście. Sprawdź nazwę albo pobierz model na ekranie \"Modele\".")),
    (r"master key",
     _n("LiteLLM wymaga klucza API. Zmień opcję klucza na ekranie \"Router LiteLLM\" i uruchom router ponownie.")),
    (r"requires (a )?newer (version of )?ollama|newer version",
     _n("Model wymaga nowszej wersji Ollamy. Zaktualizuj ją w \"Ustawieniach\".")),
    (r"permission denied",
     _n("Brak uprawnień do pliku albo urządzenia. Zwykle pomaga ponowne uruchomienie usługi.")),
    (r"timed? ?out",
     _n("Usługa odpowiadała za długo. Host może być przeciążony albo model dopiero się ładuje.")),
]


def wyjasnij_blad(tekst):
    for wzorzec, wyjasnienie in _WYJASNIENIA_BLEDOW:
        if re.search(wzorzec, tekst, re.IGNORECASE):
            return _(wyjasnienie)
    return None


def _poziom_wpisu(tekst, priorytet=6):
    # WHAT: "BŁĄD" / "UWAGA" / "INFO" - z treści (Ollama pisze level=ERROR,
    #       Python "ERROR"/"WARNING"), a w ostateczności z priorytetu journald.
    if re.search(r"level=(error|fatal)|\b(error|critical|fatal|traceback)\b", tekst, re.IGNORECASE):
        return "BŁĄD"
    if re.search(r"level=warn|\bwarn(ing)?\b", tekst, re.IGNORECASE):
        return "UWAGA"
    if priorytet <= 3:
        return "BŁĄD"
    if priorytet == 4:
        return "UWAGA"
    return "INFO"


def _oczysc_wpis_ollamy(tekst):
    # WHAT: 'time=... level=INFO source=server.go:12 msg="model loaded" x=1' ->
    #       'model loaded x=1' - czas i poziom pokazujemy w osobnych kolumnach.
    tekst = re.sub(r"\b(time|level|source)=\S+\s*", "", tekst)
    return re.sub(r'msg="((?:[^"\\]|\\.)*)"', r"\1", tekst).strip()


def _oczysc_wpis_pythona(tekst):
    # WHAT: '2026-10-22 22:54:25.410 | INFO | modul:12 - treść' (Open WebUI) albo
    #       'INFO:     treść' (LiteLLM/uvicorn) -> 'treść'.
    # WHY:  czas i poziom są już w osobnych kolumnach dziennika.
    tekst = re.sub(r"^\d{4}-\d\d-\d\d[ T][\d:.,]+\s*\|\s*\w+\s*\|\s*", "", tekst)
    tekst = re.sub(r"^\S+:\d+\s+-\s+", "", tekst)
    return re.sub(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s+", "", tekst)


def dostep_do_dziennika_systemu():
    # WHAT: czy ten proces może czytać dziennik usług SYSTEMOWYCH (Ollama).
    # WHY:  bez uprawnień journalctl -o json zwraca po prostu pusto, bez błędu
    #       ani podpowiedzi - sprawdzamy więc grupy (adm/systemd-journal/wheel)
    #       procesu. Po dopisaniu do grupy trzeba się przelogować, więc liczy
    #       się to, z czym program faktycznie wystartował, a nie /etc/group.
    if os.geteuid() == 0:
        return True
    try:
        nazwy = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except KeyError:
        return False
    return bool(nazwy & {"adm", "systemd-journal", "wheel"})


def dziennik_usluga(usluga, kursor=None, linie=300, przez_pkexec=False):
    # WHAT: nowe wpisy jednej usługi z journald -> (wpisy, nowy kursor, czy_dostep).
    # WHY:  kursor journald pozwala przy kolejnym odczycie pobrać TYLKO nowe
    #       wpisy, zamiast za każdym razem całej historii. Ollama to usługa
    #       systemowa - bez grupy adm/systemd-journal nie da się jej czytać
    #       (patrz dostep_do_dziennika_systemu).
    uzytkownika, jednostka = USLUGI_DZIENNIKA[usluga]
    polecenie = ["journalctl", "-u", jednostka, "-o", "json", "--no-pager"]
    if uzytkownika:
        polecenie.insert(1, "--user")
    polecenie += ["--after-cursor", kursor] if kursor else ["-n", str(linie)]
    if przez_pkexec:
        polecenie = ["pkexec", _sciezka_programu(polecenie[0])] + polecenie[1:]
    try:
        r = subprocess.run(polecenie, capture_output=True, text=True, timeout=None if przez_pkexec else 15)
    except (OSError, subprocess.SubprocessError):
        return [], kursor, True
    wpisy = []
    for linia in r.stdout.splitlines():
        try:
            dane = json.loads(linia)
        except ValueError:
            continue
        kursor = dane.get("__CURSOR", kursor)
        tresc = dane.get("MESSAGE", "")
        if isinstance(tresc, list):  # WHY: journald podaje bajty jako listę liczb, gdy treść nie jest UTF-8
            tresc = bytes(tresc).decode("utf-8", "replace")
        tresc = str(tresc).strip()
        if not tresc:
            continue
        try:
            czas = int(dane.get("__REALTIME_TIMESTAMP", "0")) / 1e6
            priorytet = int(dane.get("PRIORITY", 6))
        except ValueError:
            czas, priorytet = time.time(), 6
        poziom = _poziom_wpisu(tresc, priorytet)
        if usluga == "Ollama":
            tresc = _oczysc_wpis_ollamy(tresc)
        else:
            tresc = _oczysc_wpis_pythona(tresc)
        wpisy.append({"czas": czas, "usluga": usluga, "poziom": poziom, "tekst": tresc})
    brak_dostepu = not uzytkownika and not przez_pkexec and not dostep_do_dziennika_systemu()
    return wpisy, kursor, not brak_dostepu


def dziennik_zezwol_na_odczyt():
    # WHAT: dopisuje użytkownika do grupy systemd-journal (przez pkexec).
    # WHY:  jednorazowo i na stałe - potem dziennik Ollamy czyta się bez hasła
    #       (po ponownym zalogowaniu, bo grupy wczytują się przy logowaniu).
    _pkexec(["usermod", "-a", "-G", "systemd-journal", getpass.getuser()])


# =============================================================================
#  Wątki robocze - żeby nie blokować GUI
# =============================================================================
class WatekTla(QThread):
    """Wspólna baza wątków roboczych: wątek żyje, dopóki naprawdę się nie skończy.

    WHY: wątek wysyła swój sygnał "gotowe" jeszcze W TRAKCIE run() (potem np.
         zamyka połączenie). Gdy okno w odpowiedzi wyrzucało ostatnie odwołanie
         do niego (self._czat_worker = None, self._pobieranie = None), Python
         niszczył działający wątek, a Qt w takiej sytuacji celowo zabija cały
         program ("QThread: Destroyed while thread is still running") - program
         "sam się zamykał" (zgłoszenie użytkownika 2026-10-29, potwierdzone
         w zrzucie pamięci). Tu wątek trzyma sam siebie w _zywe od start() do
         sygnału finished, niezależnie od tego, co zrobi okno.
    """
    _zywe = set()

    def start(self, *argumenty):
        WatekTla._zywe.add(self)
        self.finished.connect(self._zwolnij)
        super().start(*argumenty)

    def _zwolnij(self):
        # WHY: finished przychodzi tuż PRZED faktycznym końcem wątku - chwila czekania
        self.wait()
        WatekTla._zywe.discard(self)


class RefreshWorker(WatekTla):
    """Stan usług tego komputera: Ollama (systemd), Open WebUI, LiteLLM.

    WHY: wywołania systemctl i zapytania HTTP nie mogą iść w wątku GUI. Hosty
         i modele mają osobne wątki (HostyWorker, ModeleWorker), bo dotyczą
         wszystkich komputerów z listy, nie tylko tego.
    """
    wynik = pyqtSignal(dict)

    def run(self):
        lokalny = OllamaClient(base_url=OLLAMA_URL)
        self.wynik.emit({
            "zainstalowana": ollama_zainstalowana(),
            "active": _systemctl_query("is-active") == "active",
            "enabled": _systemctl_query("is-enabled") == "enabled",
            "env_ollama": _usluga_env_wszystkie(),  # WHY: jedno czytanie override.conf - ekran "Ustawienia"
            "ip_allow": _usluga_ip_allow(),
            "api": lokalny.api_dziala(),
            "webui": webui_zainstalowane(),
            "webui_autostart": webui_autostart_wlaczony(),
            "webui_dziala": _webui_dziala(),
            "litellm": litellm_zainstalowane(),
            "litellm_autostart": litellm_autostart_wlaczony(),
            "litellm_dziala": _litellm_dziala(),
            "searxng": searxng_zainstalowane(),
            "searxng_autostart": searxng_autostart_wlaczony(),
            "searxng_dziala": _searxng_dziala(),
        })


class ModeleWorker(WatekTla):
    """Modele ze wszystkich hostów (ekran "Modele"), patrz zbierz_modele()."""
    wynik = pyqtSignal(list, list)  # (modele, hosty online)

    def __init__(self, serwery):
        super().__init__()
        self.serwery = [dict(s) for s in serwery]

    def run(self):
        self.wynik.emit(*zbierz_modele(self.serwery))


class ZasobyWorker(WatekTla):
    """Jedna próbka zasobów komputera (ProbnikZasobow) w tle.

    WHY: odczyt /proc i sysfs jest szybki, ale nvidia-smi to osobny proces -
         nie może przyciąć okna.
    """
    wynik = pyqtSignal(dict)

    def __init__(self, probnik):
        super().__init__()
        self.probnik = probnik

    def run(self):
        self.wynik.emit(self.probnik.probka())


class DziennikWorker(WatekTla):
    """Nowe wpisy z journald dla Ollamy, LiteLLM i Open WebUI."""
    wynik = pyqtSignal(list, dict, bool)  # (wpisy, kursory, czy dziennik Ollamy jest dostępny)

    def __init__(self, kursory, ollama_przez_pkexec=False):
        super().__init__()
        self.kursory = dict(kursory)
        self.ollama_przez_pkexec = ollama_przez_pkexec

    def run(self):
        wszystkie, dostep_ollama = [], True
        for usluga in USLUGI_DZIENNIKA:
            pkexec = usluga == "Ollama" and self.ollama_przez_pkexec
            wpisy, kursor, dostep = dziennik_usluga(usluga, self.kursory.get(usluga), przez_pkexec=pkexec)
            if usluga == "Ollama":
                dostep_ollama = dostep
            if kursor:
                self.kursory[usluga] = kursor
            wszystkie += wpisy
        wszystkie.sort(key=lambda w: w["czas"])
        self.wynik.emit(wszystkie, self.kursory, dostep_ollama)


class PullWorker(WatekTla):
    """Pobiera model w tle wraz z postępem (z możliwością anulowania)."""
    postep = pyqtSignal(int, str, object, object)  # (procent lub -1, status, pobrane B, całość B)
    zakonczono = pyqtSignal(bool, str)  # (sukces, komunikat)

    def __init__(self, client, model):
        super().__init__()
        self.client = client
        self.model = model
        self._anuluj = False

    def anuluj(self):
        # WHY: przerywamy czytanie strumienia - Ollama kończy pobieranie, gdy
        #      klient się rozłączy, a już pobrane części zostają do wznowienia.
        self._anuluj = True

    def run(self):
        strumien = self.client.pull_stream(self.model)
        try:
            for dane in strumien:
                if self._anuluj:
                    self.zakonczono.emit(False, _("Anulowano pobieranie: {model}").format(model=self.model))
                    return
                status = dane.get("status", "")
                total = dane.get("total")
                completed = dane.get("completed")
                # WHAT: procent liczymy tylko gdy znamy rozmiar całości.
                # WHY:  komunikaty typu "pulling manifest" nie mają total -> -1.
                if total and completed:
                    self.postep.emit(int(completed / total * 100), status, completed, total)
                else:
                    self.postep.emit(-1, status, 0, 0)
            self.zakonczono.emit(True, _("Pobrano model: {model}").format(model=self.model))
        except Exception as e:
            self.zakonczono.emit(False, _("Błąd pobierania: {blad}").format(blad=e))
        finally:
            strumien.close()


class CzatWorker(WatekTla):
    """Jedna odpowiedź modelu na ekranie "Czat" (POST /api/chat, strumień).

    WHY: wprost do Ollamy wybranego hosta, nie przez router - czat działa też
         bez zainstalowanego LiteLLM. Ostatnia linia strumienia ("done": true)
         niesie liczby tokenów i czasy, z których liczymy tokeny/s.
    """
    fragment = pyqtSignal(str, str)        # (kawałek odpowiedzi, kawałek rozumowania)
    ladowanie = pyqtSignal()               # model nie jest w pamięci - pierwsze słowo chwilę potrwa
    zrodla = pyqtSignal(object)            # [(plik, [(linia_od, linia_do), ...])] - fragmenty z dużych plików
    etap = pyqtSignal(str)                 # co się dzieje przed odpowiedzią (wyszukiwanie w internecie)
    zrodla_www = pyqtSignal(object)        # [{"tytul", "url"}] - strony z wyszukiwania, w kolejności numerów
    zakonczono = pyqtSignal(bool, object)  # (sukces, statystyki z Ollamy albo tekst błędu)

    def __init__(self, adres, model, wiadomosci, dokumenty=None, myslenie=None, wyszukiwanie=None):
        super().__init__()
        self.adres = adres
        self.model = model
        self.wiadomosci = wiadomosci
        self.dokumenty = dokumenty or []
        self.myslenie = myslenie  # WHY: "think" do /api/chat; None = nie wysyłać (model nie myśli)
        # WHY: {"zrodlo", "pytanie", "emb": (model, adres) albo None} - None = bez wyszukiwania
        self.wyszukiwanie = wyszukiwanie
        self._anuluj = False
        self._odpowiedz = None

    def anuluj(self):
        # WHY: zamknięcie połączenia przerywa generowanie po stronie Ollamy.
        self._anuluj = True
        if self._odpowiedz is not None:
            try:
                self._odpowiedz.close()
            except Exception:
                pass

    def _zapytanie(self):
        dane = {"model": self.model, "messages": self.wiadomosci, "stream": True}
        if self.myslenie is not None:
            dane["think"] = self.myslenie
        return dane

    def _okno_kontekstu(self):
        # WHAT: okno kontekstu, z jakim model NAPRAWDĘ działa (/api/ps: context_length); None = nie wiadomo.
        # WHY:  to nie zawsze maksimum modelu (gemma4: 262 144) - Ollama uruchamia go z oknem
        #       z OLLAMA_CONTEXT_LENGTH albo domyślnym; po przekroczeniu po cichu obcina
        #       najstarszą część rozmowy, więc liczy się właśnie ta liczba.
        try:
            odp = requests.get(f"{self.adres}/api/ps", timeout=3)
            odp.raise_for_status()
            for m in odp.json().get("models", []):
                if self.model in (m.get("name"), m.get("model")):
                    return m.get("context_length")
        except (requests.RequestException, ValueError):
            pass
        return None

    def _model_w_pamieci(self):
        # WHAT: czy model jest już wczytany na tym hoście (/api/ps); None = nie wiadomo.
        # WHY:  Ollama nie wysyła żadnego znaku "wczytuję model" ani postępu wczytywania -
        #       pierwszy kawałek odpowiedzi przychodzi dopiero po wczytaniu. Pytamy więc
        #       sami, zanim wyślemy pytanie, żeby okno mogło uprzedzić o dłuższym czekaniu.
        try:
            odp = requests.get(f"{self.adres}/api/ps", timeout=3)
            odp.raise_for_status()
            return any(self.model in (m.get("name"), m.get("model")) for m in odp.json().get("models", []))
        except (requests.RequestException, ValueError):
            return None

    def _parametr_myslenia_pomocniczy(self):
        # WHY: zapytania do wyszukiwarki to krótka praca pomocnicza - bez myślenia (sekundy
        #      zamiast kilkunastu); gpt-oss nie umie wyłączyć myślenia, zna tylko poziomy
        if self.myslenie is None:
            return None
        return "low" if isinstance(self.myslenie, str) else False

    def _zapytania_wyszukiwania(self, pytanie):
        # WHAT: 1-3 zapytania do wyszukiwarki ułożone przez model czatu z pytania i rozmowy.
        # WHY:  jak w Open WebUI (DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE): pytanie w rozmowie
        #       bywa niepełne ("a ile kosztuje?") - model zna kontekst i pisze pełne zapytania.
        #       Wymuszony JSON ("format" ze schematem, Ollama 0.5+) działa z każdym modelem,
        #       bez wywoływania narzędzi. Przy jakimkolwiek kłopocie - samo pytanie.
        historia = [m for m in self.wiadomosci[:-1] if m.get("content")][-4:]
        rozmowa = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in historia) or "-"
        polecenie = (
            "### Task\n"
            "Write web search queries that will find up-to-date information to answer the user's "
            f"latest question. Today's date: {time.strftime('%Y-%m-%d')}.\n"
            "### Guidelines\n"
            "- Return 1 to 3 short, specific queries (keywords, names, versions, dates).\n"
            "- Write them in the language of the question; add one English query when English "
            "sources are likely better.\n"
            "- Use the conversation to complete a question that refers to earlier messages.\n"
            "- Respond only with JSON: {\"queries\": [\"...\"]}\n"
            f"### Conversation\n{rozmowa}\n"
            f"### Latest question\n{pytanie[:2000]}")
        dane = {"model": self.model, "stream": False, "messages": [{"role": "user", "content": polecenie}],
                "format": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
                           "required": ["queries"]},
                "options": {"temperature": 0}}
        mysl = self._parametr_myslenia_pomocniczy()
        if mysl is not None:
            dane["think"] = mysl
        try:
            odp = requests.post(f"{self.adres}/api/chat", json=dane, timeout=(10, 600))
            odp.raise_for_status()
            zapytania = json.loads(odp.json()["message"]["content"]).get("queries", [])
            zapytania = [str(z).strip() for z in zapytania if str(z).strip()][:_MAKS_ZAPYTAN_WWW]
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            zapytania = []
        return zapytania or [pytanie.strip()[:200]]

    def _szukaj_w_internecie(self):
        # WHAT: zapytania -> wyszukiwarka -> strony -> pasujące fragmenty; całość doklejona
        #       do OSTATNIEGO pytania (jak fragmenty dużych plików), źródła sygnałem.
        w = self.wyszukiwanie
        pytanie = w["pytanie"] or " "
        self.etap.emit(_("Układam zapytania do wyszukiwarki..."))
        zapytania = self._zapytania_wyszukiwania(pytanie)
        if self._anuluj:
            return
        self.etap.emit(_("Szukam w internecie: {zapytania}").format(zapytania=" · ".join(zapytania)))
        listy, bledy = [], []
        for zapytanie in zapytania:
            try:
                listy.append(wyszukaj_w_internecie(zapytanie, 5, w["zrodlo"]))
            except Exception as e:
                bledy.append(str(e))
        if not any(listy):
            raise RuntimeError(_("Wyszukiwanie w internecie nie dało wyników. {blad}").format(
                blad=bledy[0] if bledy else ""))
        # WHY: na zmianę z każdego zapytania (pierwszy z 1., pierwszy z 2., ...) - różne
        #      strony zamiast pięciu wyników jednego zapytania; bez powtórzeń adresów
        wyniki, adresy = [], set()
        for poziom in range(max(len(l) for l in listy)):
            for lista in listy:
                if poziom < len(lista) and lista[poziom]["url"] not in adresy and len(wyniki) < _MAKS_STRON_WWW:
                    adresy.add(lista[poziom]["url"])
                    wyniki.append(dict(lista[poziom]))
        if self._anuluj:
            return
        self.etap.emit(_("Czytam strony ({n})...").format(n=len(wyniki)))

        def _tekst_strony(wynik):
            try:
                return pobierz_strone(wynik["url"])["tekst"][:_ZNAKOW_ZE_STRONY]
            except Exception:
                return ""  # WHY: strona niedostępna albo w JavaScripcie - zostaje opis z wyszukiwarki

        with ThreadPoolExecutor(max_workers=len(wyniki)) as pula:
            teksty = list(pula.map(_tekst_strony, wyniki))
        if self._anuluj:
            return
        tresci = self._wybierz_z_stron(pytanie, wyniki, teksty)
        czesci = [_("Wyniki wyszukiwania w internecie (dziś jest {data}). Odpowiedz na ich podstawie. Przy "
                    "informacjach z wyników podawaj numer źródła w nawiasie kwadratowym, np. [1]. Jeśli "
                    "wyniki nie wystarczają, powiedz to wprost.").format(data=time.strftime("%Y-%m-%d"))]
        for nr, (wynik, tresc) in enumerate(zip(wyniki, tresci), 1):
            czesci.append(f"[{nr}] {wynik['tytul']}\n{wynik['url']}\n```\n{tresc}\n```")
        self.wiadomosci[-1] = dict(self.wiadomosci[-1], content="\n\n".join(czesci + [self.wiadomosci[-1]["content"]]))
        self.zrodla_www.emit([{"tytul": wynik["tytul"] or _nazwa_strony(wynik["url"]), "url": wynik["url"]}
                              for wynik in wyniki])

    def _wybierz_z_stron(self, pytanie, wyniki, teksty):
        # WHAT: tekst dla modelu z każdej strony: najlepiej pasujące fragmenty (model
        #       embeddingów), a bez niego - początek strony; bez strony - opis z wyszukiwarki.
        # WHY:  całe strony zapchałyby okno kontekstu (menu, komentarze, stopki), a pytanie
        #       dotyczy zwykle kilku akapitów.
        emb = self.wyszukiwanie.get("emb")
        if emb and any(teksty):
            try:
                self.etap.emit(_("Wybieram z nich pasujące fragmenty..."))
                fragmenty = [(i, f[0]) for i, t in enumerate(teksty) if t for f in _podziel_na_fragmenty(t)]
                model, adres = emb
                wektory = []
                for i in range(0, len(fragmenty), 32):
                    if self._anuluj:
                        return [w["tresc"] for w in wyniki]
                    wektory += ollama_embeddingi(adres, model, [f[1] for f in fragmenty[i:i + 32]])
                wektor = ollama_embeddingi(adres, model, [pytanie], pytanie=True)[0]
                najlepsze = heapq.nlargest(_FRAGMENTOW_WWW, range(len(wektory)),
                                           key=lambda k: sum(map(operator.mul, wektory[k], wektor)))
                wybrane = {}
                for k in sorted(najlepsze):  # WHY: w kolejności ze strony - czyta się jak tekst
                    wybrane.setdefault(fragmenty[k][0], []).append(fragmenty[k][1])
                return ["\n[...]\n".join(wybrane[i]) if i in wybrane else wynik["tresc"]
                        for i, wynik in enumerate(wyniki)]
            except Exception:
                pass  # WHY: model embeddingów niedostępny - jak bez niego
        return [t[:_ZNAKOW_BEZ_EMBEDDINGOW] if t else wynik["tresc"] for wynik, t in zip(wyniki, teksty)]

    def _dolacz_fragmenty(self):
        # WHAT: do OSTATNIEGO pytania dokleja fragmenty dużych plików pasujące do niego.
        # WHY:  tylko do bieżącego pytania - starsze pytania zostają bez fragmentów,
        #       żeby historia nie zjadała okna kontekstu.
        pytanie = self.wiadomosci[-1]["content"] or " "
        wektory_pytania = {}
        czesci, zrodla = [], []
        for d in self.dokumenty:
            klucz = (d["adres_emb"], d["model_emb"])
            if klucz not in wektory_pytania:
                wektory_pytania[klucz] = ollama_embeddingi(d["adres_emb"], d["model_emb"], [pytanie], pytanie=True)[0]
            numery = wyszukaj_fragmenty(d, wektory_pytania[klucz])
            czesci.append(_("Fragmenty pliku {nazwa}, wyszukane jako pasujące do pytania. Odpowiedz na ich "
                            "podstawie; jeśli nie wystarczą, powiedz to wprost.").format(nazwa=d["nazwa"]))
            for i in numery:
                tekst, od, do = d["fragmenty"][i]
                czesci.append(f"[{_('linie')} {od}-{do}]\n```\n{tekst}\n```")
            zrodla.append((d["nazwa"], [d["fragmenty"][i][1:] for i in numery]))
        self.wiadomosci[-1] = dict(self.wiadomosci[-1], content="\n\n".join(czesci + [self.wiadomosci[-1]["content"]]))
        self.zrodla.emit(zrodla)

    def run(self):
        statystyki = {}
        try:
            if self.wyszukiwanie:
                self._szukaj_w_internecie()
                if self._anuluj:
                    self.zakonczono.emit(True, {"anulowano": True})
                    return
            if self.dokumenty:
                self._dolacz_fragmenty()
            if self._model_w_pamieci() is False:
                self.ladowanie.emit()
            # WHY: długi limit czytania - wczytanie dużego modelu do pamięci przed
            #      pierwszym słowem potrafi trwać ponad minutę.
            with requests.post(f"{self.adres}/api/chat", stream=True, timeout=(10, 600),
                               json=self._zapytanie()) as odp:
                self._odpowiedz = odp
                if odp.status_code != 200:
                    try:
                        blad = odp.json().get("error") or odp.text
                    except ValueError:
                        blad = odp.text
                    raise RuntimeError(f"HTTP {odp.status_code}: {blad}")
                for linia in odp.iter_lines():
                    if self._anuluj:
                        break
                    if not linia:
                        continue
                    dane = json.loads(linia)
                    if dane.get("error"):
                        raise RuntimeError(dane["error"])
                    wiadomosc = dane.get("message") or {}
                    if wiadomosc.get("content") or wiadomosc.get("thinking"):
                        self.fragment.emit(wiadomosc.get("content", ""), wiadomosc.get("thinking", ""))
                    if dane.get("done"):
                        statystyki = dane
            if self._anuluj:
                statystyki = {"anulowano": True}
            elif statystyki:
                statystyki["okno_kontekstu"] = self._okno_kontekstu()
            self.zakonczono.emit(True, statystyki)
        except Exception as e:
            if self._anuluj:
                self.zakonczono.emit(True, {"anulowano": True})
            else:
                self.zakonczono.emit(False, str(e))


class RagWorker(WatekTla):
    """Przygotowuje duży plik do wyszukiwania: fragmenty + wektory z Ollamy (/api/embed)."""
    postep = pyqtSignal(int, int)          # (gotowe fragmenty, wszystkie)
    zakonczono = pyqtSignal(bool, object)  # (sukces, {"fragmenty", "wektory"} albo tekst błędu)

    def __init__(self, adres, model, tekst):
        super().__init__()
        self.adres = adres
        self.model = model
        self.tekst = tekst
        self._anuluj = False

    def anuluj(self):
        self._anuluj = True

    def run(self):
        try:
            fragmenty = _podziel_na_fragmenty(self.tekst)
            wektory = []
            # WHY: po 32 naraz - mniej zapytań przy dużych plikach, a pasek postępu nadal żyje
            for i in range(0, len(fragmenty), 32):
                if self._anuluj:
                    return
                wektory += ollama_embeddingi(self.adres, self.model, [f[0] for f in fragmenty[i:i + 32]])
                self.postep.emit(len(wektory), len(fragmenty))
            if len(wektory) != len(fragmenty):
                raise RuntimeError(_("model embeddingów zwrócił złą liczbę wektorów"))
            self.zakonczono.emit(True, {"fragmenty": fragmenty, "wektory": wektory})
        except Exception as e:
            self.zakonczono.emit(False, str(e))


class ActionWorker(WatekTla):
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


class SprawdzAktualizacjeWorker(WatekTla):
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


class SzukajModeliWorker(WatekTla):
    """Szuka modeli naraz w obu źródłach (biblioteka Ollamy + Hugging Face), w tle.

    WHY: jedna wspólna wyszukiwarka w karcie "Pobierz nowy model" - błąd
         jednego źródła (np. brak sieci do HF albo zmiana strony ollama.com)
         NIE może ukryć wyników z drugiego, więc każde źródło łapie swój błąd
         osobno i trafia do listy błędów zamiast przerywać całość.
    """
    wynik = pyqtSignal(list, list)  # (wyniki z obu źródeł, komunikaty błędów)

    def __init__(self, zapytanie, sprawdz_moe=False):
        super().__init__()
        self.zapytanie = zapytanie
        self.sprawdz_moe = sprawdz_moe

    def run(self):
        wyniki, bledy = [], []
        try:
            # WHY: wyniki z Ollamy ZAWSZE na górze listy (oficjalna biblioteka),
            #      wyniki z HF dopisywane dopiero pod nimi.
            wyniki += ollama_wyszukaj_modele(self.zapytanie)
            if self.sprawdz_moe:
                uzupelnij_moe_z_tagow(wyniki)
        except (requests.RequestException, ValueError) as e:
            bledy.append(_("Ollama: {blad}").format(blad=e))
        try:
            for wpis in hf_wyszukaj_repozytoria(self.zapytanie):
                wyniki.append({
                    "zrodlo": "hf", "id": wpis["id"], "opis": "", "mozliwosci": [],
                    "rozmiary": [], "pobrania": _formatuj_liczbe(wpis["pobrania"]), "tagi": wpis["tagi"],
                })
        except requests.RequestException as e:
            bledy.append(_("Hugging Face: {blad}").format(blad=e))
        self.wynik.emit(wyniki, bledy)


class WariantyModeluWorker(WatekTla):
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


class AgregatorWorker(WatekTla):
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
class HostyWorker(WatekTla):
    """Sprawdza w tle wszystkie hosty z listy serwerów (ekran "Hosty Ollama").

    WHY: kilka zapytań na host, a nieosiągalny host czeka do końca timeoutu -
         w wątku GUI okno by się zawiesiło. Hosty sprawdzamy po kolei: to kilka
         komputerów w LAN, równoległość nie jest warta dodatkowej złożoności.
    """
    wynik = pyqtSignal(list)  # lista słowników z sprawdz_host()

    def __init__(self, serwery):
        super().__init__()
        self.serwery = [dict(s) for s in serwery]

    def run(self):
        self.wynik.emit([sprawdz_host(s) for s in self.serwery])


class FunkcjaWorker(WatekTla):
    """Uruchamia w tle funkcję, która ZWRACA wynik (np. tekst dziennika).

    WHY: ActionWorker oddaje tylko "OK"/błąd do dziennika aplikacji - tu wynik
         trzeba pokazać w oknie, więc oddajemy go sygnałem.
    """
    zakonczono = pyqtSignal(bool, object)  # (sukces, wynik albo tekst błędu)

    def __init__(self, funkcja):
        super().__init__()
        self.funkcja = funkcja

    def run(self):
        try:
            self.zakonczono.emit(True, self.funkcja())
        except Exception as e:
            self.zakonczono.emit(False, str(e))


def _normalizuj_adres_hosta(tekst):
    # WHAT: "192.168.0.236" / "bc250:11434" / "http://bc250" -> "http://bc250:11434".
    # WHY:  podpowiedź na ekranie mówi "wpisz adres i port" - nie każemy pamiętać
    #       o "http://" ani o domyślnym porcie Ollamy.
    tekst = tekst.strip().rstrip("/")
    if not tekst:
        return ""
    if "://" not in tekst:
        tekst = "http://" + tekst
    url = urlparse(tekst)
    if not url.hostname:
        return ""
    if url.port is None:
        tekst = f"{url.scheme}://{url.hostname}:11434{url.path}"
    return tekst


class DialogDodajHost(QDialog):
    """Nazwa + adres nowego hosta Ollama (przycisk "Dodaj host")."""

    def __init__(self, serwery, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Dodaj host"))
        self.setMinimumWidth(460)
        self._zajete = {s["adres"] for s in serwery}
        self.serwer = None  # WHY: wynik dla wywołującego, gdy dialog zamknięto "Dodaj"

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        formularz = QFormLayout()
        self.pole_nazwa = QLineEdit()
        self.pole_nazwa.setPlaceholderText(_("np. Serwer GPU"))
        self.pole_adres = QLineEdit()
        self.pole_adres.setPlaceholderText(_("np. 192.168.0.236:11434"))
        formularz.addRow(_("Nazwa:"), self.pole_nazwa)
        formularz.addRow(_("Adres:"), self.pole_adres)
        layout.addLayout(formularz)
        opis = QLabel(_(
            "Wystarczy adres i port - \"http://\" i domyślny port 11434 program dopisze sam. "
            "Po dodaniu sprawdzi połączenie i listę modeli."
        ))
        opis.setWordWrap(True)
        opis.setProperty("role", "muted")
        layout.addWidget(opis)

        pasek = QHBoxLayout()
        pasek.addStretch(1)
        btn_anuluj = QPushButton(_("Anuluj"))
        btn_anuluj.clicked.connect(self.reject)
        pasek.addWidget(btn_anuluj)
        btn_dodaj = QPushButton(_("Dodaj"))
        btn_dodaj.setObjectName("primary")
        btn_dodaj.setDefault(True)
        btn_dodaj.clicked.connect(self._dodaj)
        pasek.addWidget(btn_dodaj)
        layout.addLayout(pasek)

    def _dodaj(self):
        nazwa = self.pole_nazwa.text().strip()
        adres = _normalizuj_adres_hosta(self.pole_adres.text())
        if not nazwa or not adres:
            QMessageBox.warning(self, _("Brak danych"), _("Podaj nazwę i adres serwera."))
            return
        if adres in self._zajete:
            QMessageBox.warning(self, _("Host już jest"), _("Ten adres jest już na liście: {adres}").format(adres=adres))
            return
        self.serwer = {"nazwa": nazwa, "adres": adres}
        self.accept()


class DialogTekst(QDialog):
    """Długi tekst tylko do odczytu (np. dziennik usługi) z przyciskiem kopiowania."""

    def __init__(self, tytul, tresc, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tytul)
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        pole = QPlainTextEdit()
        pole.setObjectName("log")
        pole.setReadOnly(True)
        pole.setPlainText(tresc)
        # WHY: najnowsze wpisy są na końcu - od razu tam przewijamy
        pole.moveCursor(pole.textCursor().MoveOperation.End)
        layout.addWidget(pole, 1)
        pasek = QHBoxLayout()
        pasek.addStretch(1)
        btn_kopiuj = QPushButton(_("Kopiuj do schowka"))
        btn_kopiuj.clicked.connect(lambda: QApplication.clipboard().setText(pole.toPlainText()))
        pasek.addWidget(btn_kopiuj)
        btn_zamknij = QPushButton(_("Zamknij"))
        btn_zamknij.clicked.connect(self.accept)
        pasek.addWidget(btn_zamknij)
        layout.addLayout(pasek)


def _odmaluj_rodzica(*etykiety):
    # WHAT: po zmianie napisów odmalowuje CAŁE widżety-rodziców (kafelek, karta).
    # WHY:  przy ułamkowym skalowaniu ekranu (Wayland, np. 115%) Qt przy zmianie
    #       samego napisu odmalowuje tylko jego prostokąt, a krawędź wypada w połowie
    #       piksela - zostawał 1-pikselowy ciemny pasek pod napisem, który zmienia
    #       się co kilka sekund (zgłoszenie użytkownika: "czarny pasek pod ilością ms").
    #       Odmalowanie rodzica pokrywa ten wiersz pikseli tłem kafelka.
    for rodzic in {e.parentWidget() for e in etykiety if e is not None and e.parentWidget() is not None}:
        rodzic.update()


def _odswiez_styl(widget):
    # WHAT: przelicza QSS po zmianie właściwości (np. setProperty("state", "ok")).
    # WHY:  Qt nie stosuje reguł typu QLabel[state="ok"] sam z siebie po zmianie
    #       właściwości - trzeba odpiąć i przypiąć styl (patrz theme.qss).
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _etykieta(tekst="", rola=None, **wlasciwosci):
    # WHAT: QLabel z rolą z theme.qss (muted / mono / h1 / h2 / h3 / value).
    lbl = QLabel(tekst)
    if rola:
        lbl.setProperty("role", rola)
    for nazwa, wartosc in wlasciwosci.items():
        lbl.setProperty(nazwa, wartosc)
    return lbl


class EtykietaSkracana(QLabel):
    """QLabel, który zamiast ucinać tekst w pół słowa skraca go wielokropkiem
    (pełna treść w dymku) - dla długich nazw modeli i adresów w kartach."""

    def __init__(self, tekst="", parent=None):
        super().__init__(parent)
        self._pelny = ""
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(tekst)

    def setText(self, tekst):
        self._pelny = tekst
        self.setToolTip(tekst)
        self._skroc()

    def resizeEvent(self, zdarzenie):
        super().resizeEvent(zdarzenie)
        self._skroc()

    def _skroc(self):
        super().setText(self.fontMetrics().elidedText(self._pelny, Qt.TextElideMode.ElideRight, max(0, self.width())))


class KartaHosta(QFrame):
    """Jeden host na liście ekranu "Hosty Ollama" - klikalna karta z makiety."""
    kliknieto = pyqtSignal(str)  # adres hosta

    def __init__(self, serwer, parent=None):
        super().__init__(parent)
        self.adres = serwer["adres"]
        self.setObjectName("card")
        self.setProperty("clickable", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(serwer["nazwa"])
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)
        wiersz = QHBoxLayout()
        wiersz.setSpacing(10)
        self.kropka = QLabel()
        self.kropka.setObjectName("dot")
        self.kropka.setFixedSize(10, 10)
        wiersz.addWidget(self.kropka)
        nazwa = QLabel(serwer["nazwa"])
        czcionka = nazwa.font()
        czcionka.setPointSizeF(czcionka.pointSizeF() * 1.1)
        czcionka.setWeight(czcionka.Weight.DemiBold)
        nazwa.setFont(czcionka)
        wiersz.addWidget(nazwa, 1)
        self.lbl_stan = _etykieta(_("sprawdzam..."), "muted")
        wiersz.addWidget(self.lbl_stan)
        v.addLayout(wiersz)
        v.addWidget(_etykieta(self.adres, "mono", muted="true"))
        self.lbl_info = _etykieta("", "muted")
        v.addWidget(self.lbl_info)

    def ustaw_stan(self, stan):
        # WHAT: wypełnia kartę wynikiem sprawdz_host().
        if stan["online"]:
            self.lbl_stan.setText("online")
            self.lbl_stan.setProperty("state", "ok")
            self.kropka.setProperty("state", "ok")
            gib = 1024 ** 3
            self.lbl_info.setText(_("VRAM {vram:.1f} GB · RAM {ram:.1f} GB · modele: {n}").format(
                vram=stan["vram_uzyte"] / gib, ram=stan["ram_uzyte"] / gib, n=len(stan["modele"])))
        else:
            self.lbl_stan.setText("offline")
            self.lbl_stan.setProperty("state", "warn")
            self.kropka.setProperty("state", "off")
            self.lbl_info.setText(_("brak połączenia"))
        _odswiez_styl(self.lbl_stan)
        _odswiez_styl(self.kropka)

    def ustaw_wybrana(self, wybrana):
        self.setProperty("selected", "true" if wybrana else "false")
        _odswiez_styl(self)

    def mousePressEvent(self, zdarzenie):
        if zdarzenie.button() == Qt.MouseButton.LeftButton:
            self.kliknieto.emit(self.adres)
        super().mousePressEvent(zdarzenie)


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


class DialogPolaczenieWebUI(QDialog):
    """Instrukcja ręcznego podłączenia agregatora LiteLLM w Open WebUI.

    WHY: appka NIE wpisuje tego sama. Open WebUI trzyma połączenia w swojej bazie
         (webui.db) i zmienne środowiskowe (OPENAI_API_BASE_URLS) traktuje tylko
         jako wartości domyślne - po pierwszym starcie wygrywa baza, więc wpis
         w usłudze nic by nie zmienił (sprawdzone w kodzie Open WebUI 0.11.3,
         models/config.py). Zapis wprost do bazy byłby kruchy przy aktualizacjach
         WebUI. Wystarczy JEDEN wpis - LiteLLM zbiera modele ze wszystkich hostów,
         więc nowy host to tylko "Uruchom ponownie" LiteLLM, bez ruszania WebUI.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Połączenie dla Open WebUI"))
        self.setMinimumWidth(560)
        self.adres = f"{LITELLM_URL}/v1"
        ip = _adres_ip_lan()
        # WHY: WebUI na innym komputerze w sieci nie dosięgnie "localhost" -
        #      dla niego trzeba podać IP tego komputera (LiteLLM słucha na 0.0.0.0).
        self.adres_lan = f"http://{ip}:{urlparse(LITELLM_URL).port or 4000}/v1" if ip else None
        layout = QVBoxLayout(self)

        lbl_kroki = QLabel(_(
            "Open WebUI pokaże modele ze wszystkich hostów, gdy podłączysz je raz do "
            "agregatora LiteLLM:\n"
            "1. W Open WebUI otwórz: Panel administratora → Ustawienia → Połączenia "
            "(Admin Panel → Settings → Connections).\n"
            "2. W sekcji \"OpenAI API\" kliknij \"+\" (dodaj połączenie).\n"
            "3. URL: {adres}\n"
            "4. Klucz: {klucz}\n"
            "5. Zapisz i odśwież stronę WebUI - modele pojawią się na liście wyboru.\n"
            "Nowy host? Dodaj go do listy serwerów u góry okna i kliknij \"Uruchom ponownie\" "
            "przy LiteLLM - w WebUI wystarczy odświeżyć stronę. LiteLLM musi być "
            "uruchomione (karta wyżej)."
        ).format(
            adres=self.adres,
            klucz=_litellm_klucz() or _(
                "zostaw puste (jeśli WebUI nie pozwoli zapisać bez klucza, wpisz cokolwiek, "
                "np. none - LiteLLM bez hasła go nie sprawdza)."
            ),
        ))
        lbl_kroki.setWordWrap(True)
        lbl_kroki.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(lbl_kroki)

        if self.adres_lan:
            lbl_lan = QLabel(_(
                "Open WebUI na innym komputerze w sieci? Zamiast localhost wpisz: <b>{adres}</b>"
            ).format(adres=self.adres_lan))
            lbl_lan.setWordWrap(True)
            lbl_lan.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(lbl_lan)

        # WHY: WebUI łączy się też wprost z lokalną Ollamą (OLLAMA_BASE_URL w usłudze),
        #      więc lokalne modele widać dwa razy - raz z Ollamy, raz przez LiteLLM.
        lbl_duplikaty = QLabel(_(
            "Wskazówka: lokalne modele mogą pojawić się dwa razy (z Ollamy i z LiteLLM). "
            "Żeby tego uniknąć, wyłącz w tym samym miejscu połączenie \"Ollama API\"."
        ))
        lbl_duplikaty.setWordWrap(True)
        layout.addWidget(lbl_duplikaty)

        pasek = QHBoxLayout()
        pasek.addStretch(1)
        btn_adres = QPushButton(_("Kopiuj adres"))
        btn_adres.clicked.connect(lambda: QApplication.clipboard().setText(self.adres))
        pasek.addWidget(btn_adres)
        if self.adres_lan:
            btn_adres_lan = QPushButton(_("Kopiuj adres w sieci"))
            btn_adres_lan.clicked.connect(lambda: QApplication.clipboard().setText(self.adres_lan))
            pasek.addWidget(btn_adres_lan)
        btn_zamknij = QPushButton(_("Zamknij"))
        btn_zamknij.clicked.connect(self.accept)
        pasek.addWidget(btn_zamknij)
        layout.addLayout(pasek)


# =============================================================================
#  Wygląd z makiet: kolory z theme.qss, ikony SVG, własne widżety
#  (patrz CLAUDE.md, sekcja "Makiety interfejsu")
# =============================================================================
def _wlasciwosc_koloru(nazwa, domyslny):
    # WHAT: właściwość Qt typu QColor, którą theme.qss ustawia przez
    #       "qproperty-<nazwa>: #rrggbb;".
    # WHY:  widżety rysowane w kodzie (przełącznik, wykresy, schemat routera)
    #       też biorą kolory z theme.qss - w Pythonie zostaje tylko kolor
    #       zapasowy na wypadek braku pliku motywu.
    def pobierz(self):
        return self._kolory.get(nazwa, QColor(domyslny))

    def ustaw(self, kolor):
        self._kolory[nazwa] = QColor(kolor)
        self.update()

    return pyqtProperty(QColor, pobierz, ustaw)


# WHAT: załączniki czatu - zdjęcia idą do modelu jako obraz (pole "images" w /api/chat),
#       pliki tekstowe jako tekst w wiadomości.
# WHY:  PDF/Word/arkusze świadomie NIE - trzeba je rozbierać na tekst (nowe zależności),
#       a duże dokumenty i tak nie mieszczą się w kontekście; to robi Open WebUI (RAG).
_ROZSZERZENIA_OBRAZOW = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
# WHY: dokumenty, z których tekst wyciągamy sami - formaty Office/LibreOffice/EPUB to ZIP
#      z plikami XML (wystarczy biblioteka standardowa), PDF czyta QtPdf z rodziny Qt
_ROZSZERZENIA_DOKUMENTOW = {".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub"}
# WHY: stare formaty binarne (sprzed 2007) i RTF - świadomie bez obsługi (decyzja użytkownika)
_ROZSZERZENIA_STARE = {".doc", ".xls", ".ppt", ".rtf"}
_MAKS_PLIK_DOKUMENTU = 100 * 1024 * 1024    # WHY: rozmiar samego pliku (np. PDF ze zdjęciami)
_MAKS_ROZPAKOWANY = 300 * 1024 * 1024       # WHY: ochrona przed "bombą ZIP" - XML po rozpakowaniu
_MAKS_PLIK_TEKSTOWY = 20 * 1024 * 1024  # WHY: 20 MB = ok. 23 tys. fragmentów: 75 MB wektorów i ok. 2,6 s
                                        #      wyszukiwania na pytanie (zmierzone); więcej = minuty czekania
_DUZY_PLIK_TEKSTOWY = 100 * 1024        # WHY: powyżej - tryb wyszukiwania (RAG), bo całość nie zmieści się
                                        #      w oknie kontekstu modelu (64k tokenów to ok. 150-250 KB tekstu)
_FRAGMENT_ZNAKOW = 900                  # WHY: modele embeddingów mają małe okno (nomic: 512 tokenów)
_FRAGMENT_ZAKLADKA = 150                # WHY: zdanie przecięte na granicy fragmentów trafia do obu
_FRAGMENTOW_NA_PYTANIE = 6
# WHAT: wyszukiwanie w internecie w czacie - ile zapytań, stron i tekstu trafia do modelu.
# WHY:  5 stron po 8 fragmentów (ok. 7 tys. znaków) mieści się nawet w domyślnym oknie
#       kontekstu; 30 tys. znaków ze strony = do ok. 35 fragmentów do wektorów na stronę
#       (u użytkownika ok. 20 fragmentów/s, więc kilka sekund, nie minuty).
_MAKS_ZAPYTAN_WWW = 3
_MAKS_STRON_WWW = 5
_ZNAKOW_ZE_STRONY = 30000
_FRAGMENTOW_WWW = 8
_ZNAKOW_BEZ_EMBEDDINGOW = 2500
_MAKS_BOK_OBRAZU = 2048                 # WHY: modele i tak zmniejszają obraz - nie wysyłamy zbędnych MB


def _obraz_do_wyslania(obraz, nazwa):
    # WHAT: QImage -> załącznik (JPEG w base64, najwyżej 2048 px na dłuższym boku).
    # WHY:  jeden format dla wszystkiego (zrzut ze schowka, webp, gif) - Ollama
    #       na pewno zna JPEG/PNG, a pomniejszenie oszczędza czas wysyłania.
    if obraz.isNull():
        raise ValueError(f"{nazwa}: " + _("nie udało się odczytać obrazu"))
    if max(obraz.width(), obraz.height()) > _MAKS_BOK_OBRAZU:
        obraz = obraz.scaled(_MAKS_BOK_OBRAZU, _MAKS_BOK_OBRAZU, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    bufor = QBuffer()
    bufor.open(QIODevice.OpenModeFlag.WriteOnly)
    obraz.convertToFormat(QImage.Format.Format_RGB888).save(bufor, "JPEG", 90)
    dane = bytes(bufor.data())
    return {"typ": "obraz", "nazwa": nazwa, "rozmiar": len(dane),
            "b64": base64.b64encode(dane).decode("ascii")}


def wczytaj_zalacznik(sciezka):
    # WHAT: plik z dysku -> załącznik czatu (obraz albo tekst); ValueError z czytelnym powodem.
    sciezka = Path(sciezka)
    rozszerzenie = sciezka.suffix.lower()
    if rozszerzenie in _ROZSZERZENIA_OBRAZOW:
        return _obraz_do_wyslania(QImage(str(sciezka)), sciezka.name)
    if rozszerzenie in _ROZSZERZENIA_STARE:
        raise ValueError(f"{sciezka.name}: " + _("stary format - zapisz plik jako .docx, .xlsx, .pptx albo .odt "
                                                 "i dołącz ponownie"))
    if rozszerzenie in _ROZSZERZENIA_DOKUMENTOW:
        # WHY: tekst wyciąga dopiero wątek w tle (odczytaj_dokument) - duży PDF czy
        #      arkusz potrafi zająć kilka sekund, a okno nie może wtedy stać
        if sciezka.stat().st_size > _MAKS_PLIK_DOKUMENTU:
            raise ValueError(f"{sciezka.name}: " + _("plik jest za duży na czat (ponad 100 MB)"))
        return {"typ": "do_odczytu", "nazwa": sciezka.name, "rozmiar": sciezka.stat().st_size,
                "sciezka": str(sciezka)}
    if sciezka.stat().st_size > _MAKS_PLIK_TEKSTOWY:
        raise ValueError(f"{sciezka.name}: " + _("plik jest za duży na czat (ponad 20 MB) - większe dokumenty "
                                                 "obsłuży Open WebUI"))
    dane = sciezka.read_bytes()
    # WHY: plik tekstowy = UTF-8 bez bajtów zerowych; wszystko inne to plik binarny
    #      (program, archiwum...), którego model i tak by nie zrozumiał
    try:
        if b"\0" in dane:
            raise UnicodeDecodeError("utf-8", dane, 0, 1, "NUL")
        tekst = dane.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{sciezka.name}: " + _("to nie jest zdjęcie ani plik tekstowy")) from None
    return zalacznik_z_tekstu(sciezka.name, tekst)


def zalacznik_z_tekstu(nazwa, tekst):
    # WHAT: tekst (z pliku tekstowego albo wyciągnięty z dokumentu) -> załącznik.
    # WHY:  duży tekst nie idzie w całości - "dokument" przechodzi w tryb wyszukiwania;
    #       limity liczone od TEKSTU, nie od pliku (2 MB .xlsx potrafi dać 10 MB tekstu).
    rozmiar = len(tekst.encode("utf-8"))
    if rozmiar > _MAKS_PLIK_TEKSTOWY:
        raise ValueError(f"{nazwa}: " + _("plik jest za duży na czat (ponad 20 MB) - większe dokumenty "
                                          "obsłuży Open WebUI"))
    if not tekst.strip():
        raise ValueError(f"{nazwa}: " + _("w pliku nie ma żadnego tekstu"))
    typ = "dokument" if rozmiar > _DUZY_PLIK_TEKSTOWY else "tekst"
    return {"typ": typ, "nazwa": nazwa, "rozmiar": rozmiar, "tekst": tekst}


# --- Tekst z dokumentów (PDF, Office, LibreOffice, EPUB) ---
# WHY: bez nowych bibliotek - Office/ODF/EPUB to archiwa ZIP z XML (zipfile + ElementTree),
#      PDF czyta QtPdf. Wynik to zwykły tekst; układ strony, obrazy i formatowanie giną.
def _lokalna(el):
    return el.tag.rsplit("}", 1)[-1]


def _xml_z_zip(archiwum, nazwa):
    info = archiwum.getinfo(nazwa)
    if info.file_size > _MAKS_ROZPAKOWANY:
        raise ValueError(_("dokument po rozpakowaniu jest za duży"))
    return ET.fromstring(archiwum.read(info))


def _tekst_docx(archiwum):
    # WHAT: akapity i tabele (komórki rozdzielone średnikami) z word/document.xml.
    def akapit(p):
        czesci = []
        for el in p.iter():
            nazwa = _lokalna(el)
            if nazwa == "t" and el.text:
                czesci.append(el.text)
            elif nazwa == "tab":
                czesci.append("\t")
            elif nazwa in ("br", "cr"):
                czesci.append("\n")
        return "".join(czesci)

    linie = []

    def blok(el):
        for dziecko in el:
            nazwa = _lokalna(dziecko)
            if nazwa == "p":
                linie.append(akapit(dziecko))
            elif nazwa == "tbl":
                for wiersz in (w for w in dziecko if _lokalna(w) == "tr"):
                    komorki = [" ".join(akapit(p) for p in k.iter() if _lokalna(p) == "p").strip()
                               for k in wiersz if _lokalna(k) == "tc"]
                    linie.append("; ".join(komorki))
            else:
                blok(dziecko)  # WHY: treść w kontrolkach (sdt) i podobnych opakowaniach

    blok(_xml_z_zip(archiwum, "word/document.xml"))
    return "\n".join(linie)


def _kolumna_xlsx(adres):
    # WHAT: "C12" -> 2 (numer kolumny od zera)
    numer = 0
    for znak in adres:
        if not znak.isalpha():
            break
        numer = numer * 26 + ord(znak.upper()) - 64
    return numer - 1


def _tekst_xlsx(archiwum):
    # WHAT: każdy arkusz osobno, wiersze jako "a; b; c" - wartości wyliczone przez formuły.
    # WHY:  daty Excel zapisuje jako liczby dni (np. 45962) - zostają liczbami; rozpoznanie
    #       wymagałoby czytania stylów komórek.
    wspolne = []
    if "xl/sharedStrings.xml" in archiwum.namelist():
        for si in _xml_z_zip(archiwum, "xl/sharedStrings.xml"):
            wspolne.append("".join(t.text or "" for t in si.iter() if _lokalna(t) == "t"))
    skoroszyt = _xml_z_zip(archiwum, "xl/workbook.xml")
    relacje = {r.get("Id"): r.get("Target") for r in _xml_z_zip(archiwum, "xl/_rels/workbook.xml.rels")}
    wynik = []
    for arkusz in (a for a in skoroszyt.iter() if _lokalna(a) == "sheet"):
        rid = next((v for k, v in arkusz.attrib.items() if k.endswith("}id")), None)
        cel = relacje.get(rid, "")
        sciezka = cel.lstrip("/") if cel.startswith("/") else "xl/" + cel
        if sciezka not in archiwum.namelist():
            continue
        wynik.append(_("Arkusz: {nazwa}").format(nazwa=arkusz.get("name", "")))
        for wiersz in (w for w in _xml_z_zip(archiwum, sciezka).iter() if _lokalna(w) == "row"):
            komorki = {}
            for c in (c for c in wiersz if _lokalna(c) == "c"):
                typ = c.get("t")
                if typ == "inlineStr":
                    wartosc = "".join(t.text or "" for t in c.iter() if _lokalna(t) == "t")
                else:
                    v = next((x.text for x in c if _lokalna(x) == "v"), None)
                    if v is None:
                        continue
                    if typ == "s":
                        wartosc = wspolne[int(v)] if int(v) < len(wspolne) else ""
                    elif typ == "b":
                        wartosc = "TRUE" if v == "1" else "FALSE"
                    else:
                        wartosc = v
                if wartosc != "":
                    komorki[_kolumna_xlsx(c.get("r", "A"))] = wartosc
            if komorki:
                wynik.append("; ".join(komorki.get(i, "") for i in range(max(komorki) + 1)))
        wynik.append("")
    return "\n".join(wynik)


def _tekst_pptx(archiwum):
    # WHAT: tekst każdego slajdu po kolei, z numerem slajdu.
    slajdy = sorted((n for n in archiwum.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
    wynik = []
    for nr, nazwa in enumerate(slajdy, 1):
        wynik.append(_("Slajd {n}").format(n=nr))
        for p in (p for p in _xml_z_zip(archiwum, nazwa).iter() if _lokalna(p) == "p"):
            linia = "".join(t.text or "" for t in p.iter() if _lokalna(t) == "t")
            if linia.strip():
                wynik.append(linia)
        wynik.append("")
    return "\n".join(wynik)


def _tekst_odf(archiwum):
    # WHAT: content.xml z .odt/.ods/.odp: akapity, nagłówki, tabele ("a; b; c"),
    #       arkusze i slajdy z nazwą.
    # WHY:  ODF powtarza puste komórki/wiersze atrybutem "number-*-repeated" (nawet tysiące) -
    #       powtarzamy tylko niepuste i ucinamy puste końcówki.
    def tekst(el):
        czesci = [el.text or ""]
        for d in el:
            nazwa = _lokalna(d)
            if nazwa == "s":
                czesci.append(" " * int(next((v for k, v in d.attrib.items() if k.endswith("}c")), "1")))
            elif nazwa == "tab":
                czesci.append("\t")
            elif nazwa == "line-break":
                czesci.append("\n")
            elif nazwa not in ("annotation", "note"):
                czesci.append(tekst(d))
            czesci.append(d.tail or "")
        return "".join(czesci)

    def atrybut(el, koncowka, domyslnie=None):
        return next((v for k, v in el.attrib.items() if k.endswith("}" + koncowka)), domyslnie)

    linie = []
    slajdy = [0]  # WHY: numer slajdu - nazwa strony ODP bywa tytułem, a nie numerem

    def blok(el):
        for d in el:
            nazwa = _lokalna(d)
            if nazwa in ("p", "h"):
                linie.append(tekst(d))
            elif nazwa == "table":
                if atrybut(d, "name") and _lokalna(el) == "spreadsheet":
                    linie.append(_("Arkusz: {nazwa}").format(nazwa=atrybut(d, "name")))
                blok(d)
                linie.append("")
            elif nazwa == "table-row":
                komorki = []
                for k in (k for k in d if _lokalna(k) in ("table-cell", "covered-table-cell")):
                    wartosc = " ".join(tekst(p) for p in k if _lokalna(p) in ("p", "h")).strip()
                    ile = int(atrybut(k, "number-columns-repeated", "1")) if wartosc else 1
                    komorki += [wartosc] * min(ile, 1000)
                while komorki and not komorki[-1]:
                    komorki.pop()
                if komorki:
                    linie.append("; ".join(komorki))
            elif nazwa == "page":
                slajdy[0] += 1
                linie.append(_("Slajd {n}").format(n=slajdy[0]))
                blok(d)
                linie.append("")
            else:
                blok(d)

    blok(_xml_z_zip(archiwum, "content.xml"))
    return "\n".join(linie)


class _TekstHtml(HTMLParser):
    """Tekst z HTML (rozdział EPUB, strona WWW) - nowa linia przy blokach, bez skryptów i stylów."""
    _BLOKI = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "blockquote",
              "article", "main", "pre", "td", "th", "dt", "dd"}
    # WHY: <title> to w EPUB często nazwa pliku ("ch001.xhtml")
    _POMIJANE = {"script", "style", "title"}

    def __init__(self, dodatkowo_pomijane=()):
        super().__init__()
        self.czesci, self._pomin = [], 0
        self.pomijane = self._POMIJANE | set(dodatkowo_pomijane)

    def handle_starttag(self, tag, attrs):
        if tag in self.pomijane:
            self._pomin += 1
        elif tag in self._BLOKI:
            self.czesci.append("\n")

    def handle_endtag(self, tag):
        if tag in self.pomijane and self._pomin:
            self._pomin -= 1

    def handle_data(self, data):
        if not self._pomin:
            self.czesci.append(data)


def _tekst_epub(archiwum):
    # WHAT: rozdziały w kolejności czytania (spine z pliku OPF).
    kontener = _xml_z_zip(archiwum, "META-INF/container.xml")
    opf = next(e.get("full-path") for e in kontener.iter() if _lokalna(e) == "rootfile")
    katalog = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
    pakiet = _xml_z_zip(archiwum, opf)
    pozycje = {e.get("id"): e.get("href") for e in pakiet.iter() if _lokalna(e) == "item"}
    wynik = []
    for ref in (e.get("idref") for e in pakiet.iter() if _lokalna(e) == "itemref"):
        sciezka = katalog + (pozycje.get(ref) or "")
        if sciezka in archiwum.namelist():
            parser = _TekstHtml()
            parser.feed(archiwum.read(sciezka).decode("utf-8", errors="replace"))
            wynik.append(re.sub(r"\n\s*\n+", "\n\n", "".join(parser.czesci)).strip())
    return "\n\n".join(w for w in wynik if w)


def _tekst_pdf(sciezka):
    # WHAT: tekst stron PDF przez QtPdf, z oznaczeniem strony (pomaga w źródłach RAG).
    # WHY:  skan (strony to same obrazy) nie ma warstwy tekstu - wtedy mówimy o tym
    #       wprost, zamiast wysyłać modelowi pusty plik.
    try:
        from PyQt6.QtPdf import QPdfDocument
    except ImportError:
        raise ValueError(_("brak modułu QtPdf - doinstaluj pakiet python3-pyqt6.qtpdf")) from None
    dokument = QPdfDocument(None)
    dokument.load(str(sciezka))
    if dokument.status() != QPdfDocument.Status.Ready:
        raise ValueError(_("nie udało się otworzyć PDF (uszkodzony albo chroniony hasłem)"))
    teksty = [dokument.getAllText(i).text() for i in range(dokument.pageCount())]
    dokument.close()
    if teksty and sum(len(t.strip()) for t in teksty) < 20 * len(teksty):
        raise ValueError(_("to skan bez warstwy tekstu - zrób zrzut strony i wklej go (Ctrl+V) do modelu, "
                           "który widzi zdjęcia (np. glm-ocr)"))
    return "\n\n".join(_("Strona {n}").format(n=i) + "\n" + t for i, t in enumerate(teksty, 1))


def odczytaj_dokument(sciezka):
    # WHAT: tekst z PDF / .docx / .xlsx / .pptx / .odt / .ods / .odp / .epub -> załącznik.
    # WHY:  wołane w wątku w tle (FunkcjaWorker) - ValueError niesie czytelny powód.
    sciezka = Path(sciezka)
    rozszerzenie = sciezka.suffix.lower()
    try:
        if rozszerzenie == ".pdf":
            tekst = _tekst_pdf(sciezka)
        else:
            with zipfile.ZipFile(sciezka) as archiwum:
                if sum(i.file_size for i in archiwum.infolist()) > _MAKS_ROZPAKOWANY * 3:
                    raise ValueError(_("dokument po rozpakowaniu jest za duży"))
                czytnik = {".docx": _tekst_docx, ".xlsx": _tekst_xlsx, ".pptx": _tekst_pptx,
                           ".epub": _tekst_epub}.get(rozszerzenie, _tekst_odf)
                tekst = czytnik(archiwum)
    except ValueError as e:
        raise ValueError(f"{sciezka.name}: {e}") from None
    except (zipfile.BadZipFile, KeyError, ET.ParseError, StopIteration) as e:
        raise ValueError(f"{sciezka.name}: " + _("nie udało się odczytać dokumentu ({blad})").format(
            blad=type(e).__name__)) from None
    # WHY: puste linie seriami (puste akapity, odstępy w arkuszach) tylko zjadają kontekst
    tekst = re.sub(r"\n{3,}", "\n\n", tekst)
    return zalacznik_z_tekstu(sciezka.name, tekst)


# WHAT: model embeddingów proponowany, gdy na hostach nie ma żadnego (Ustawienia → Czat).
# WHY:  wielojęzyczny (polskie dokumenty) - mniejszy nomic-embed-text (274 MB) jest głównie
#       angielski. Rozmiar sprawdzony na ollama.com 2026-10-29.
RAG_ZALECANY_MODEL = "nomic-embed-text-v2-moe"
RAG_ZALECANY_ROZMIAR = "958 MB"


# --- Wyszukiwanie w dużych plikach (RAG) - tylko Ollama, bez dodatkowych bibliotek ---
# WHAT: plik cięty na fragmenty, każdy zamieniany na wektor przez model embeddingów
#       w Ollamie (/api/embed); przy pytaniu - kilka fragmentów najbliższych pytaniu.
# WHY:  bez bazy wektorów - rozmowa i tak nie jest zapisywana, a kilka tysięcy
#       fragmentów przeszukuje się w czystym Pythonie w ułamku sekundy.
def _podziel_na_fragmenty(tekst):
    # WHAT: [(tekst, linia_od, linia_do)] po ok. _FRAGMENT_ZNAKOW znaków, z zakładką.
    # WHY:  numery linii - pod odpowiedzią widać, z której części pliku są fragmenty.
    kawalki = []  # (numer linii, tekst) - bardzo długie linie pocięte
    for nr, linia in enumerate(tekst.splitlines(), 1):
        while len(linia) > _FRAGMENT_ZNAKOW:
            kawalki.append((nr, linia[:_FRAGMENT_ZNAKOW]))
            linia = linia[_FRAGMENT_ZNAKOW:]
        kawalki.append((nr, linia))
    fragmenty, biezace, dlugosc = [], [], 0
    for kawalek in kawalki:
        biezace.append(kawalek)
        dlugosc += len(kawalek[1]) + 1
        if dlugosc >= _FRAGMENT_ZNAKOW:
            fragmenty.append(biezace)
            zakladka, ile = [], 0
            for k in reversed(biezace):
                if ile + len(k[1]) > _FRAGMENT_ZAKLADKA:
                    break
                zakladka.insert(0, k)
                ile += len(k[1]) + 1
            biezace, dlugosc = zakladka, ile
    if biezace and (not fragmenty or biezace[-1] != fragmenty[-1][-1]):
        fragmenty.append(biezace)
    wynik = []
    for f in fragmenty:
        tresc = "\n".join(k[1] for k in f)
        if tresc.strip():
            wynik.append((tresc, f[0][0], f[-1][0]))
    return wynik


def ollama_embeddingi(adres, model, teksty, pytanie=False):
    # WHAT: wektory (znormalizowane, array('f')) dla listy tekstów z /api/embed.
    # WHY:  rodzina nomic wymaga przedrostków zadania (z karty modelu) - bez nich
    #       wyszukiwanie wyraźnie słabnie; inne modele dostają tekst bez zmian.
    if "nomic" in model.lower():
        przedrostek = "search_query: " if pytanie else "search_document: "
        teksty = [przedrostek + t for t in teksty]
    odp = requests.post(f"{adres}/api/embed", json={"model": model, "input": teksty}, timeout=(10, 300))
    if odp.status_code != 200:
        try:
            blad = odp.json().get("error") or odp.text
        except ValueError:
            blad = odp.text
        raise RuntimeError(f"{model}: {blad}")
    wektory = []
    for w in odp.json().get("embeddings", []):
        dlugosc = math.sqrt(sum(x * x for x in w)) or 1.0
        wektory.append(array("f", (x / dlugosc for x in w)))
    return wektory


def wyszukaj_fragmenty(dokument, wektor_pytania, ile=_FRAGMENTOW_NA_PYTANIE):
    # WHAT: numery fragmentów najbliższych pytaniu (iloczyn skalarny znormalizowanych
    #       wektorów = podobieństwo kosinusowe), posortowane wg położenia w pliku.
    wyniki = heapq.nlargest(ile, range(len(dokument["wektory"])),
                            key=lambda i: sum(map(operator.mul, dokument["wektory"][i], wektor_pytania)))
    return sorted(wyniki)


# --- Strony WWW podane linkiem w pytaniu ---
# WHAT: link w pytaniu -> program sam pobiera stronę i dołącza jej tekst jak plik.
# WHY:  wprost z tego komputera, bez konta i bez wyszukiwarki (to dopiero wersja 1.1).
#       Strona idzie dalej tą samą drogą co plik tekstowy: do 100 KB w całości, większa
#       przez wyszukiwanie fragmentów.
_LINK_WZORZEC = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_MAKS_LINKOW = 3                        # WHY: więcej stron naraz to minuty czekania i pełny kontekst
_MAKS_STRONA = 10 * 1024 * 1024         # WHY: tyle najwyżej czytamy z serwera (np. duży PDF pod linkiem)
# WHY: menu, stopki, formularze i ramki boczne to na większości stron szum, nie treść
_POMIJANE_NA_STRONIE = {"nav", "header", "footer", "aside", "form", "noscript", "svg", "button", "select",
                        "iframe", "template"}


def linki_w_tekscie(tekst):
    # WHAT: adresy http(s) z tekstu, bez kropki/przecinka/nawiasu z końca zdania, bez powtórzeń.
    linki = []
    for link in _LINK_WZORZEC.findall(tekst):
        link = link.rstrip(".,;:!?)]}»”")
        if link not in linki:
            linki.append(link)
    return linki[:_MAKS_LINKOW]


def _nazwa_strony(url):
    # WHAT: krótka nazwa do chipa: host + początek ścieżki ("pl.wikipedia.org/wiki/Lama").
    adres = urlparse(url)
    nazwa = adres.netloc + adres.path.rstrip("/")
    return nazwa if len(nazwa) <= 60 else nazwa[:59] + "…"


def pobierz_strone(url):
    # WHAT: strona pod adresem -> załącznik z jej tekstem; ValueError z czytelnym powodem.
    # WHY:  wołane w wątku w tle (FunkcjaWorker) - strona potrafi odpowiadać kilka sekund.
    nazwa = _nazwa_strony(url)
    try:
        odp = requests.get(url, stream=True, timeout=(10, 30), headers={
            # WHY: część serwisów odrzuca zapytania bez nazwy przeglądarki
            "User-Agent": f"Mozilla/5.0 (X11; Linux x86_64) {NAZWA_PROGRAMU}/{WERSJA}",
            "Accept-Language": "pl,en;q=0.8"})
        odp.raise_for_status()
        dane = b""
        for kawalek in odp.iter_content(65536):
            dane += kawalek
            if len(dane) > _MAKS_STRONA:
                raise ValueError(_("strona jest za duża (ponad 10 MB)"))
    except requests.RequestException as e:
        raise ValueError(f"{nazwa}: " + _("nie udało się pobrać strony ({blad})").format(blad=e)) from None
    except ValueError as e:
        raise ValueError(f"{nazwa}: {e}") from None
    typ = odp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if typ == "application/pdf" or url.lower().endswith(".pdf"):
        # WHY: PDF pod linkiem czyta ten sam QtPdf co dołączony plik
        with tempfile.NamedTemporaryFile(suffix=".pdf") as plik:
            plik.write(dane)
            plik.flush()
            try:
                tekst = _tekst_pdf(plik.name)
            except ValueError as e:
                raise ValueError(f"{nazwa}: {e}") from None
    elif typ in ("text/html", "application/xhtml+xml", ""):
        # WHY: kodowanie z nagłówka, a bez niego z <meta charset> w samej stronie; domyślnie UTF-8
        #      (zgadywanie requests czyta treść jeszcze raz, a ta jest już pobrana kawałkami)
        kodowanie = odp.encoding if "charset" in odp.headers.get("Content-Type", "").lower() else None
        if not kodowanie:
            meta = re.search(rb"""<meta[^>]+charset=["']?([\w-]+)""", dane[:4096], re.IGNORECASE)
            kodowanie = meta.group(1).decode("ascii") if meta else "utf-8"
        try:
            html_tekst = dane.decode(kodowanie, errors="replace")
        except LookupError:  # WHY: nieznana nazwa kodowania w stronie
            html_tekst = dane.decode("utf-8", errors="replace")
        parser = _TekstHtml(_POMIJANE_NA_STRONIE)
        parser.feed(html_tekst)
        # WHY: tytuł strony na początku - model wie, co czyta (w <title>, który parser pomija)
        tytul = re.search(r"<title[^>]*>(.*?)</title>", html_tekst, re.IGNORECASE | re.DOTALL)
        tekst = (html.unescape(tytul.group(1)).strip() + "\n\n" if tytul else "") + "".join(parser.czesci)
    elif typ.startswith("text/") or typ in ("application/json", "application/xml"):
        tekst = dane.decode(odp.encoding or "utf-8", errors="replace")
    else:
        raise ValueError(f"{nazwa}: " + _("pod tym linkiem nie ma strony ani tekstu ({typ})").format(typ=typ))
    # WHY: strony mają mnóstwo pustych linii i wcięć z kodu HTML - tylko zjadają kontekst
    tekst = re.sub(r"[ \t]+", " ", tekst)
    tekst = re.sub(r"\n\s*\n+", "\n\n", tekst).strip()
    if len(tekst) < 50:
        # WHY: strona budowana JavaScriptem (aplikacja w przeglądarce) przychodzi pusta. Próg
        #      niski, bo zmierzone: takie strony dają 0-180 znaków (stopka, "Browser not
        #      supported"), a prawdziwa krótka strona (example.com) - 156; lepiej czasem
        #      dołączyć samą stopkę (model powie, że nic tam nie ma), niż odrzucić treść
        raise ValueError(f"{nazwa}: " + _("strona jest prawie pusta - pewnie treść wczytuje się dopiero "
                                          "w przeglądarce (JavaScript); skopiuj tekst i wklej go w pytanie"))
    z = zalacznik_z_tekstu(nazwa, tekst)
    z["url"] = url
    return z


def _tresc_dla_modelu(wiadomosc):
    # WHAT: treść wiadomości użytkownika razem z dołączonymi plikami tekstowymi.
    # WHY:  model nie "otwiera" plików - dostaje ich treść w wiadomości, z nazwą,
    #       żeby wiedział, o czym mowa ("popraw ten plik").
    czesci = [wiadomosc["tresc"]] if wiadomosc["tresc"] else []
    for plik in wiadomosc.get("zalaczniki", []):
        if plik["typ"] == "tekst":
            naglowek = f"Strona: {plik['url']}" if plik.get("url") else f"Plik: {plik['nazwa']}"
            czesci.append(f"{naglowek}\n```\n{plik['tekst']}\n```")
    return "\n\n".join(czesci)


# --- Zapisane rozmowy czatu ---
# WHAT: każda rozmowa to jeden plik JSON w ~/.local/share/LamaDeck/czaty/,
#       zapisywany po każdej odpowiedzi.
# WHY:  osobny katalog, NIE ~/.local/share/lamadeck - tam instaluje się program,
#       a "./install.sh --uninstall" kasuje go w całości (razem z rozmowami).
#       Ollama sama niczego nie zapamiętuje - historię zawsze trzyma program.
_WERSJA_PLIKU_ROZMOWY = 1
# WHY: klucze potrzebne tylko w trakcie pracy okna - do pliku nie trafiają
_KLUCZE_ROBOCZE_WIADOMOSCI = {"trwa", "start", "laduje", "szuka", "etap"}
_KLUCZE_ROBOCZE_ZALACZNIKA = {"fragmenty", "wektory", "gotowy", "procent", "eta", "sciezka", "tekst_pelny"}


def katalog_rozmow():
    return _katalog_danych() / NAZWA_PROGRAMU / "czaty"


def _tytul_rozmowy(wiadomosci):
    # WHAT: tytuł z pierwszego pytania (pierwsza linia, do 70 znaków) albo nazwy pliku.
    for w in wiadomosci:
        if w["rola"] != "user":
            continue
        linia = next((l.strip() for l in w.get("tresc", "").splitlines() if l.strip()), "")
        if linia:
            return linia if len(linia) <= 70 else linia[:69].rstrip() + "…"
        if w.get("zalaczniki"):
            return w["zalaczniki"][0]["nazwa"]
    return _("Rozmowa")


def _wiadomosci_do_zapisu(wiadomosci):
    # WHAT: kopia rozmowy bez stanu roboczego okna.
    # WHY:  zdjęcia zostają (base64 po pomniejszeniu - 0,2-1,5 MB), żeby po otwarciu
    #       model dalej je "widział"; wektory dużych plików NIE (przy 20 MB to ok. 75 MB
    #       na rozmowę) - po otwarciu taki plik trzeba dołączyć ponownie.
    wynik = []
    for w in wiadomosci:
        if w.get("trwa"):
            continue  # WHY: odpowiedź jeszcze się pisze - zapisze się po jej końcu
        kopia = {k: v for k, v in w.items() if not k.startswith("_") and k not in _KLUCZE_ROBOCZE_WIADOMOSCI}
        if w.get("zalaczniki"):
            kopia["zalaczniki"] = []
            for z in w["zalaczniki"]:
                zz = {k: v for k, v in z.items() if not k.startswith("_") and k not in _KLUCZE_ROBOCZE_ZALACZNIKA}
                if z["typ"] == "dokument":
                    zz.pop("tekst", None)
                    zz["bez_wyszukiwania"] = True
                kopia["zalaczniki"].append(zz)
        wynik.append(kopia)
    return wynik


def _tekst_do_szukania(dane):
    # WHAT: tytuł + treść wszystkich wiadomości, małymi literami - do wyszukiwarki rozmów.
    czesci = [dane.get("tytul", "")]
    for w in dane.get("wiadomosci", []):
        czesci.append(w.get("tresc", ""))
        czesci += [z.get("nazwa", "") for z in w.get("zalaczniki", [])]
    return "\n".join(czesci).lower()


def _zapisz_plik_rozmowy(id_rozmowy, dane):
    # WHY: najpierw plik tymczasowy, potem os.replace - przerwany zapis (np. brak
    #      miejsca, zamknięcie programu) nie zostawi uszkodzonego pliku rozmowy.
    katalog = katalog_rozmow()
    katalog.mkdir(parents=True, exist_ok=True)
    plik = katalog / f"{id_rozmowy}.json"
    tymczasowy = plik.with_suffix(".tmp")
    tymczasowy.write_text(json.dumps(dane, ensure_ascii=False), encoding="utf-8")
    os.replace(tymczasowy, plik)


def zapisz_rozmowe(id_rozmowy, utworzono, wiadomosci, model, host, tytul_wlasny=None):
    # WHAT: zapis rozmowy do pliku; zwraca wpis do listy rozmów (bez treści wiadomości).
    # WHY:  tytuł nadany przez użytkownika (tytul_wlasny) wygrywa z tym z pierwszego pytania
    #       i nie nadpisuje się przy kolejnych odpowiedziach.
    dane = {"wersja": _WERSJA_PLIKU_ROZMOWY, "tytul": tytul_wlasny or _tytul_rozmowy(wiadomosci),
            "tytul_wlasny": bool(tytul_wlasny), "utworzono": utworzono,
            "zmieniono": time.time(), "model": model, "host": host,
            "wiadomosci": _wiadomosci_do_zapisu(wiadomosci)}
    _zapisz_plik_rozmowy(id_rozmowy, dane)
    return _wpis_listy_rozmow(id_rozmowy, dane)


def zmien_nazwe_rozmowy(id_rozmowy, tytul):
    # WHAT: nowy tytuł zapisanej rozmowy (też nie otwartej); pusty = znów z pierwszego pytania.
    dane = wczytaj_rozmowe(id_rozmowy)
    dane["tytul"] = tytul or _tytul_rozmowy(dane.get("wiadomosci", []))
    dane["tytul_wlasny"] = bool(tytul)
    _zapisz_plik_rozmowy(id_rozmowy, dane)
    return _wpis_listy_rozmow(id_rozmowy, dane)


def rozmowa_do_markdown(dane):
    # WHAT: rozmowa jako Markdown - tytuł, data i model, potem pytania i odpowiedzi.
    # WHY:  odpowiedzi modeli już są w Markdownie, więc trafiają bez zmian; rozumowanie
    #       pomijamy (to notatki robocze modelu), zdjęcia i pliki - tylko z nazwy.
    linie = [f"# {dane.get('tytul', '')}", ""]
    opis = [time.strftime("%d.%m.%Y %H:%M", time.localtime(dane.get("utworzono") or dane.get("zmieniono") or 0))]
    if dane.get("model"):
        opis.append(dane["model"])
    linie += [f"*{' · '.join(opis)}*", ""]
    for w in dane.get("wiadomosci", []):
        if w["rola"] == "user":
            linie += [f"## {_('Ty')}", "", w.get("tresc", "")]
            for z in w.get("zalaczniki", []):
                if z.get("url"):
                    linie.append(f"- {_('Strona')}: {z['url']}")
                else:
                    linie.append(f"- {_('Zdjęcie') if z['typ'] == 'obraz' else _('Plik')}: {z['nazwa']}")
        elif w["rola"] == "assistant":
            linie += [f"## {w.get('kto', '')}", "", _rozdziel_myslenie(w.get("tresc", ""))[1]]
            if w.get("zrodla_www"):
                linie += ["", f"**{_('Źródła:')}**"]
                linie += [f"{nr}. [{z['tytul']}]({z['url']})" for nr, z in enumerate(w["zrodla_www"], 1)]
            if w.get("stat"):
                linie += ["", f"*{w['stat']}*"]
        else:
            linie += [f"## {_('Błąd')}", "", w.get("tresc", "")]
        linie.append("")
    return "\n".join(linie)


def _wpis_listy_rozmow(id_rozmowy, dane):
    return {"id": id_rozmowy, "tytul": dane.get("tytul", ""), "zmieniono": dane.get("zmieniono", 0),
            "tytul_wlasny": dane.get("tytul_wlasny", False),
            "model": dane.get("model", ""), "szukaj": _tekst_do_szukania(dane)}


def lista_rozmow():
    # WHAT: wszystkie zapisane rozmowy (najnowsze na górze) - wołane w wątku w tle.
    katalog = katalog_rozmow()
    wynik = []
    for plik in katalog.glob("*.json") if katalog.is_dir() else []:
        try:
            wynik.append(_wpis_listy_rozmow(plik.stem, json.loads(plik.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue  # WHY: uszkodzony plik nie może zablokować całej listy
    return sorted(wynik, key=lambda w: w["zmieniono"], reverse=True)


def wczytaj_rozmowe(id_rozmowy):
    dane = json.loads((katalog_rozmow() / f"{id_rozmowy}.json").read_text(encoding="utf-8"))
    for w in dane.get("wiadomosci", []):
        for z in w.get("zalaczniki", []):
            if z.get("typ") == "dokument":
                z["bez_wyszukiwania"] = True  # WHY: wektorów nie zapisujemy - nie jest "gotowy"
    return dane


def _rozdziel_myslenie(tresc):
    # WHAT: (rozumowanie, odpowiedź) z treści, w której model sam wstawił <think>...</think>.
    # WHY:  nowsza Ollama oddaje rozumowanie w osobnym polu "thinking", ale część
    #       modeli (np. z Hugging Face) pisze je wprost w treści, w znacznikach.
    if not tresc.lstrip().startswith("<think>"):
        return "", tresc
    reszta = tresc.lstrip()[len("<think>"):]
    if "</think>" not in reszta:
        return reszta, ""  # WHY: model wciąż myśli - odpowiedzi jeszcze nie ma
    myslenie, odpowiedz = reszta.split("</think>", 1)
    return myslenie, odpowiedz.lstrip()


class WidokCzatu(QTextBrowser):
    akcja = pyqtSignal(str)  # "ponow" / "popraw" - ikony przy ostatniej wiadomości

    """Rozmowa na ekranie "Czat": nagłówek (kto mówi) i dymek z treścią.

    WHY: QTextBrowser, a nie etykiety w dymkach - etykieta z Markdownem rozpychała
         się przez długie linie kodu na szerokość całego okna. Tu Markdown modelu
         (kod, listy, tabele) wstawia sam Qt (insertMarkdown), a długi kod się zawija.
         Kolory dymków i kodu przychodzą z theme.qss przez qproperty.
    """
    kolorTy = _wlasciwosc_koloru("kolorTy", "#808080")
    kolorModelu = _wlasciwosc_koloru("kolorModelu", "#808080")
    kolorBledu = _wlasciwosc_koloru("kolorBledu", "#808080")
    kolorWyciszony = _wlasciwosc_koloru("kolorWyciszony", "#808080")
    tloTy = _wlasciwosc_koloru("tloTy", "#808080")
    tloModelu = _wlasciwosc_koloru("tloModelu", "#808080")
    tloKodu = _wlasciwosc_koloru("tloKodu", "#808080")
    kolorLinku = _wlasciwosc_koloru("kolorLinku", "#808080")

    def __init__(self):
        super().__init__()
        self._kolory = {}
        self.setObjectName("chatView")
        # WHY: linki obsługujemy sami - "Kopiuj kod" to nie adres, a zwykłe linki
        #      z odpowiedzi modelu otwieramy w przeglądarce (_klik_linku)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._klik_linku)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.pokaz_myslenie = False
        self._start_ostatniej = 0
        self._kody = {}  # WHY: treść bloków kodu pod linkami "Kopiuj kod" (kopiuj:<numer>)
        self._dymki = {}  # WHY: opis ikony pod jej adresem - QTextBrowser sam dymków nie pokazuje
        self.ensurePolished()

    def _przewin(self, wartosc, na_dole, do_dolu=False):
        # WHY: w dół tylko, gdy ktoś był na dole (albo właśnie wysłał pytanie) - inaczej
        #      wraca dokładnie tam, gdzie czytał. Bez tego przy przebudowie tekstu
        #      dokument na chwilę się skracał i suwak "uciekał" (zgłoszenie użytkownika).
        pasek = self.verticalScrollBar()
        pasek.setValue(pasek.maximum() if (na_dole or do_dolu) else min(wartosc, pasek.maximum()))

    def _pozycja(self):
        pasek = self.verticalScrollBar()
        return pasek.value(), pasek.value() >= pasek.maximum() - 8

    def pokaz(self, wiadomosci, pusty_tekst="", do_dolu=False):
        # WHAT: cała rozmowa od nowa (nowa wiadomość, koniec odpowiedzi, zmiana języka).
        wartosc, na_dole = self._pozycja()
        self.document().clear()  # WHY: czyści też miniatury zdjęć - wstawiane są od nowa
        self._kody = {}
        self._dymki = {}
        if not wiadomosci:
            c = QTextCursor(self.document())
            format_bloku = QTextBlockFormat()
            format_bloku.setAlignment(Qt.AlignmentFlag.AlignCenter)
            format_bloku.setTopMargin(80)
            c.setBlockFormat(format_bloku)
            c.insertText(pusty_tekst, self._znaki(self.kolorWyciszony))
            return
        # WHY: ikony "popraw"/"ponów" tylko przy ostatniej parze pytanie-odpowiedź, gdy
        #      odpowiedź jest gotowa
        ostatnia_para = (len(wiadomosci) >= 2 and wiadomosci[-2]["rola"] == "user"
                         and wiadomosci[-1]["rola"] != "user" and not wiadomosci[-1].get("trwa"))
        for i, wiadomosc in enumerate(wiadomosci[:-1]):
            self._wstaw(wiadomosc, ostatnia_para and i == len(wiadomosci) - 2)
        self._start_ostatniej = self._koniec()
        self._wstaw(wiadomosci[-1], ostatnia_para)
        self._przewin(wartosc, na_dole, do_dolu)

    def odswiez_ostatnia(self, wiadomosc):
        # WHAT: podmienia tylko ostatnią wiadomość - tę, która właśnie się pisze.
        # WHY:  przebudowa całej długiej rozmowy kilka razy na sekundę byłaby
        #       zbędnym kosztem. Usunięcie i wstawienie w JEDNYM bloku edycji -
        #       dokument przelicza układ raz, bez chwilowego skrócenia.
        wartosc, na_dole = self._pozycja()
        c = QTextCursor(self.document())
        c.beginEditBlock()
        c.setPosition(self._start_ostatniej)
        c.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        self._wstaw(wiadomosc)
        c.endEditBlock()
        self._przewin(wartosc, na_dole)

    def _klik_linku(self, url):
        if url.scheme() == "akcja":
            self.akcja.emit(url.path())
        elif url.scheme() == "kopiuj":
            QApplication.clipboard().setText(self._kody.get(url.path(), ""))
            QToolTip.showText(QCursor.pos(), _("Skopiowano do schowka"), self)
        else:
            QDesktopServices.openUrl(url)

    def _koniec(self):
        return self.document().characterCount() - 1

    def _znaki(self, kolor, gruby=False, kursywa=False):
        f = QTextCharFormat()
        f.setForeground(kolor)
        if gruby:
            f.setFontWeight(QFont.Weight.Bold)
        f.setFontItalic(kursywa)
        return f

    def _ikona(self, c, nazwa, adres, dymek):
        # WHAT: mała ikona-link w nagłówku wiadomości (kolor przygaszonego tekstu z theme.qss).
        zasob = QUrl(f"ikona:{nazwa}")
        if self.document().resource(QTextDocument.ResourceType.ImageResource, zasob) is None:
            # WHY: rysowana w podwójnej rozdzielczości - ostra także przy skalowaniu ekranu
            obraz = ikona(nazwa, self.kolorWyciszony, rozmiar=32, grubosc=2).pixmap(32, 32).toImage()
            self.document().addResource(QTextDocument.ResourceType.ImageResource, zasob, obraz)
        f = QTextImageFormat()
        f.setName(zasob.toString())
        f.setWidth(15)
        f.setHeight(15)
        f.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
        f.setAnchor(True)
        f.setAnchorHref(adres)
        c.insertText("   ", self._znaki(self.kolorWyciszony))
        c.insertImage(f)
        self._dymki[adres] = dymek

    # WHAT: własna właściwość ramki - które tło (pytanie / odpowiedź) narysować pod dymkiem
    _ROLA_DYMKA = QTextFormat.Property.UserProperty + 1
    _PROMIEN_DYMKA = 10

    def paintEvent(self, zdarzenie):
        # WHAT: zaokrąglone tła dymków pod tekstem, potem zwykłe rysowanie dokumentu.
        # WHY:  QTextFrameFormat nie zna zaokrągleń (rysuje kanciaste tło), a widżetów
        #       w QTextBrowser osadzić się nie da. Pozycje ramek bierzemy z układu dokumentu
        #       i przesuwamy o przewinięcie, kolory - z theme.qss (tloTy / tloModelu).
        malarz = QPainter(self.viewport())
        malarz.setRenderHint(QPainter.RenderHint.Antialiasing)
        malarz.setPen(Qt.PenStyle.NoPen)
        przesuniecie = QPointF(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())
        widoczne = QRectF(self.viewport().rect())
        uklad = self.document().documentLayout()
        for ramka in self.document().rootFrame().childFrames():
            rola = ramka.frameFormat().property(self._ROLA_DYMKA)
            if not rola:
                continue
            prostokat = uklad.frameBoundingRect(ramka).translated(przesuniecie)
            if not prostokat.intersects(widoczne):
                continue
            f = ramka.frameFormat()
            # WHY: frameBoundingRect obejmuje marginesy - tło tylko na obszarze dymka
            prostokat = prostokat.adjusted(f.leftMargin(), f.topMargin(), -f.rightMargin(), -f.bottomMargin())
            malarz.setBrush(self.tloTy if rola == "user" else self.tloModelu)
            malarz.drawRoundedRect(prostokat, self._PROMIEN_DYMKA, self._PROMIEN_DYMKA)
        malarz.end()
        super().paintEvent(zdarzenie)

    def viewportEvent(self, zdarzenie):
        # WHAT: dymek z opisem po najechaniu na ikonę.
        if zdarzenie.type() == QEvent.Type.ToolTip:
            dymek = self._dymki.get(self.anchorAt(zdarzenie.pos()))
            if dymek:
                QToolTip.showText(zdarzenie.globalPos(), dymek, self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(zdarzenie)

    def _wstaw(self, w, akcje=False):
        c = QTextCursor(self.document())
        c.movePosition(QTextCursor.MoveOperation.End)
        if self._koniec() > 0:
            c.insertBlock(QTextBlockFormat())
        # --- nagłówek: kto mówi + statystyki odpowiedzi ---
        kolor = {"user": self.kolorTy, "blad": self.kolorBledu}.get(w["rola"], self.kolorModelu)
        # WHY: "Ty"/"Błąd" tłumaczone przy wyświetlaniu - rozmowa przeżywa zmianę języka
        kto = {"user": _("Ty"), "blad": _("Błąd")}.get(w["rola"], w.get("kto", ""))
        c.setCharFormat(self._znaki(kolor, gruby=True))
        c.insertText(kto, self._znaki(kolor, gruby=True))
        if w.get("stat"):
            c.insertText("   " + w["stat"], self._znaki(self.kolorWyciszony))
        if w["rola"] == "assistant" and not w.get("trwa"):
            # WHAT: ikona "kopiuj" - cała odpowiedź w Markdownie, bez rozumowania
            odpowiedz = _rozdziel_myslenie(w.get("tresc", ""))[1].strip()
            if odpowiedz:
                numer = f"o{len(self._kody)}"
                self._kody[numer] = odpowiedz
                self._ikona(c, "kopiuj", f"kopiuj:{numer}", _("Kopiuj odpowiedź"))
        if akcje and w["rola"] != "user":
            self._ikona(c, "odswiez", "akcja:ponow", _("Wygeneruj ponownie - modelem wybranym teraz po prawej"))
        if akcje and w["rola"] == "user":
            self._ikona(c, "edytuj", "akcja:popraw", _("Popraw pytanie - wróci do pola, a ono i odpowiedź "
                                                       "znikną z rozmowy"))
        # --- dymek ---
        ramka_f = QTextFrameFormat()
        # WHY: bez tła w dokumencie - ramka QTextDocument nie ma zaokrąglonych rogów, więc
        #      dymek rysuje paintEvent (zaokrąglony prostokąt pod tekstem); tu tylko rola
        ramka_f.setProperty(self._ROLA_DYMKA, "user" if w["rola"] == "user" else "model")
        ramka_f.setPadding(12)
        ramka_f.setTopMargin(4)
        ramka_f.setBottomMargin(16)
        # WHY: odstęp od suwaka po prawej - dymki dochodziły do samej krawędzi
        ramka_f.setRightMargin(14)
        ramka_f.setBorder(0)
        ramka = c.insertFrame(ramka_f)
        if w["rola"] != "assistant":
            # WHY: tekst użytkownika i błędy wprost, bez Markdownu - wklejony kod
            #      czy gwiazdki mają zostać dokładnie takie, jak je wpisano.
            c.insertText(w["tresc"], self._znaki(self.kolorBledu if w["rola"] == "blad" else self.kolorModelu))
            self._wstaw_zalaczniki(c, w.get("zalaczniki", []), bool(w["tresc"]))
            return
        myslenie_z_tresci, odpowiedz = _rozdziel_myslenie(w["tresc"])
        myslenie = (w.get("myslenie", "") + myslenie_z_tresci).strip()
        if myslenie and self.pokaz_myslenie:
            c.insertText(_("Rozumowanie modelu") + "\n", self._znaki(self.kolorWyciszony, gruby=True))
            c.insertText(myslenie + "\n\n", self._znaki(self.kolorWyciszony, kursywa=True))
        if odpowiedz.strip():
            c.insertMarkdown(odpowiedz)
            # WHAT: pod odpowiedzią - klikalne źródła z wyszukiwania (numery jak w odpowiedzi)
            if w.get("zrodla_www"):
                # WHY: osobny akapit z odstępem - inaczej lista przylegała do ostatniego zdania
                odstep = QTextBlockFormat()
                odstep.setTopMargin(10)
                c.insertBlock(odstep)
                c.insertText(_("Źródła:"), self._znaki(self.kolorWyciszony, gruby=True))
                for nr, z in enumerate(w["zrodla_www"], 1):
                    c.insertBlock(QTextBlockFormat())  # WHY: bez odstępu z nagłówka "Źródła:"
                    c.insertText(f"[{nr}] ", self._znaki(self.kolorWyciszony))
                    link = self._znaki(self.kolorLinku)
                    link.setAnchor(True)
                    link.setAnchorHref(z["url"])
                    link.setToolTip(z["url"])
                    c.insertText(z["tytul"], link)
            # WHAT: pod odpowiedzią - z których linii dużych plików wzięto fragmenty
            for nazwa, zakresy in w.get("zrodla") or []:
                linie = ", ".join(f"{od}-{do}" if od != do else str(od) for od, do in zakresy)
                c.insertText("\n" + _("Na podstawie fragmentów: {nazwa}, linie {linie}").format(nazwa=nazwa, linie=linie),
                             self._znaki(self.kolorWyciszony, kursywa=True))
        elif w.get("trwa"):
            if w.get("etap"):
                tekst = w["etap"]
            elif myslenie:
                tekst = _("model myśli...")
            elif w.get("szuka"):
                tekst = _("Szukam pasujących fragmentów w pliku...")
            elif w.get("laduje"):
                tekst = _("Wczytuję model do pamięci ({gb:.1f} GB) - przy pierwszym pytaniu to chwilę "
                          "trwa, potem odpowiedzi idą od razu...").format(gb=w.get("gb", 0))
            else:
                tekst = _("czekam na odpowiedź...")
            # WHY: licznik sekund - widać, że program żyje, a nie zawiesił się
            sekundy = int(time.monotonic() - w.get("start", time.monotonic()))
            if sekundy >= 1:
                tekst += f"  {sekundy} s"
            c.insertText(tekst, self._znaki(self.kolorWyciszony, kursywa=True))
        self._styl_kodu(ramka)

    def _wstaw_zalaczniki(self, c, zalaczniki, po_tekscie):
        # WHAT: miniatury zdjęć (do 260 px) i nazwy plików tekstowych w dymku pytania.
        for i, z in enumerate(zalaczniki):
            if po_tekscie or i:
                c.insertText("\n")
            if z["typ"] == "obraz":
                adres = QUrl(f"zalacznik:{id(z)}")
                if self.document().resource(QTextDocument.ResourceType.ImageResource, adres) is None:
                    obraz = QImage.fromData(base64.b64decode(z["b64"]))
                    self.document().addResource(QTextDocument.ResourceType.ImageResource, adres, obraz)
                    z["_wymiary"] = (obraz.width(), obraz.height())
                szer, wys = z.get("_wymiary", (260, 260))
                skala = min(1.0, 260 / max(1, szer), 260 / max(1, wys))
                f = QTextImageFormat()
                f.setName(adres.toString())
                f.setWidth(szer * skala)
                f.setHeight(wys * skala)
                c.insertImage(f)
            else:
                wzor = _("Strona: {nazwa} ({kb} KB)") if z.get("url") else _("Plik: {nazwa} ({kb} KB)")
                tekst = wzor.format(nazwa=z["nazwa"], kb=max(1, z["rozmiar"] // 1024))
                if z["typ"] == "dokument":
                    # WHY: w otwartej zapisanej rozmowie wektorów już nie ma
                    tekst += " - " + (_("żeby dalej pytać o ten plik, dołącz go ponownie")
                                      if z.get("bez_wyszukiwania") else _("wyszukiwanie fragmentów"))
                c.insertText(tekst, self._znaki(self.kolorWyciszony))

    def _styl_kodu(self, ramka):
        # WHAT: bloki kodu - tło i JetBrains Mono, zawijane, z linkiem "Kopiuj kod" nad
        #       każdym; `kod` w tekście - ta sama czcionka.
        # WHY:  Qt rozpoznaje kod z Markdownu (czcionka o stałej szerokości), ale nie
        #       daje mu tła, a długie linie zostawiłby niezawinięte (przewijanie w bok).
        czcionka = QFont("JetBrains Mono")
        czcionka.setPointSizeF(max(1.0, self.font().pointSizeF() - 0.5))
        # WHY: bez ligatur - JetBrains Mono rysowała "<!--" jako strzałki, a "->" / ">="
        #      jako jeden znak; w kodzie do skopiowania ma być widać dokładnie te znaki.
        #      PreferNoShaping, a nie setFeature("liga"/"calt") - to drugie jest dopiero od
        #      Qt 6.7, a Ubuntu 24.04 ma starsze (czat z kodem kończył się błędem). Dla pisma
        #      łacińskiego wynik ten sam: sprawdzone numery glifów na Qt 6.4 i 6.11.
        czcionka.setStyleStrategy(QFont.StyleStrategy.PreferNoShaping)
        f_kodu = QTextCharFormat()
        f_kodu.setFont(czcionka, QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly)
        grupy = []  # (pozycja pierwszego bloku, linie) - kolejne bloki kodu = jeden fragment kodu
        poprzedni_kod = False
        blok = ramka.firstCursorPosition().block()
        while blok.isValid() and blok.position() < ramka.lastPosition():
            format_bloku = blok.blockFormat()
            kod = format_bloku.nonBreakableLines() or blok.charFormat().fontFixedPitch()
            if kod:
                format_bloku.setNonBreakableLines(False)
                format_bloku.setBackground(self.tloKodu)
                QTextCursor(blok).setBlockFormat(format_bloku)
                if poprzedni_kod:
                    grupy[-1][1].append(blok.text())
                else:
                    grupy.append((blok.position(), [blok.text()]))
            poprzedni_kod = kod
            it = blok.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid() and (kod or fragment.charFormat().fontFixedPitch()):
                    c = QTextCursor(self.document())
                    c.setPosition(fragment.position())
                    c.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                    c.mergeCharFormat(f_kodu)
                it += 1
            blok = blok.next()
        # WHY: od końca - wstawienie linku przesuwa pozycje wszystkiego, co jest dalej
        for pozycja, linie in reversed(grupy):
            numer = str(len(self._kody))
            self._kody[numer] = "\n".join(linie)
            c = QTextCursor(self.document())
            c.setPosition(pozycja)
            c.insertBlock()  # WHY: kod przechodzi do nowego bloku, a ten przed nim dostaje link
            c.movePosition(QTextCursor.MoveOperation.PreviousBlock)
            format_linku = QTextBlockFormat()
            format_linku.setAlignment(Qt.AlignmentFlag.AlignRight)
            format_linku.setTopMargin(6)
            c.setBlockFormat(format_linku)
            link = self._znaki(self.kolorLinku)
            link.setAnchor(True)
            link.setAnchorHref(f"kopiuj:{numer}")
            link.setFontPointSize(max(1.0, self.font().pointSizeF() - 1))
            c.insertText(_("Kopiuj kod"), link)


class Paleta(QWidget):
    """Niewidoczny widżet - przenosi kolory z theme.qss do ikon SVG."""
    tekst = _wlasciwosc_koloru("tekst", "#808080")
    wyciszony = _wlasciwosc_koloru("wyciszony", "#808080")
    akcent = _wlasciwosc_koloru("akcent", "#808080")
    naAkcencie = _wlasciwosc_koloru("naAkcencie", "#808080")
    blad = _wlasciwosc_koloru("blad", "#808080")
    boczny = _wlasciwosc_koloru("boczny", "#808080")
    bocznyWybrany = _wlasciwosc_koloru("bocznyWybrany", "#808080")
    ok = _wlasciwosc_koloru("ok", "#808080")

    def __init__(self):
        super().__init__()
        self._kolory = {}
        self.setObjectName("paleta")
        self.ensurePolished()


# WHAT: kształty ikon przepisane z makiet (viewBox 24x24, kreska w kolorze ikony).
_IKONY_SVG = {
    "logo": '<path d="M12 2l8 4.5v11L12 22l-8-4.5v-11z"/><path d="M9 10h.01M15 10h.01M9.5 15c1.5 1 3.5 1 5 0"/>',
    "panel": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/>'
             '<rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "hosty": '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/>'
             '<path d="M7 7.5h.01M7 16.5h.01"/>',
    "router": '<path d="M4 7h14l-3-3M20 17H6l3 3"/>',
    "modele": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    "webui": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>',
    "dziennik": '<path d="M6 3h9l4 4v14H6z"/><path d="M9 11h7M9 15h7M9 7h3"/>',
    "ustawienia": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "odswiez": '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    "pobierz": '<path d="M12 4v12M6 11l6 6 6-6M5 20h14"/>',
    "kopiuj": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "kosz": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "otworz": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "szukaj": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "edytuj": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "czat": '<path d="M4 5h16v11H10l-5 4v-4H4z"/><path d="M8 9.5h8M8 12.5h5"/>',
    "internet": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "spinacz": '<path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7L9.7 17.2a1.6 1.6 0 0 1-2.3-2.3L15 7.5"/>',
    "zamknij": '<path d="M6 6l12 12M18 6L6 18"/>',
    # WHAT: stan modelu w tabeli hosta - kość RAM (wczytany do pamięci) i dysk (tylko na dysku)
    "pamiec": '<rect x="2" y="7" width="20" height="10" rx="1.5"/>'
              '<path d="M6 10v4M10 10v4M14 10v4M18 10v4M5 17v3M9 17v3M15 17v3M19 17v3"/>',
    "dysk": '<path d="M22 12H2"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6'
            'l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><path d="M6 16h.01M10 16h.01"/>',
}


def _pixmapa_svg(nazwa, kolor, rozmiar, grubosc=2.0):
    # WHAT: rysuje ikonę z _IKONY_SVG w danym kolorze, ostro także na ekranach HiDPI.
    tresc = _IKONY_SVG[nazwa].replace("currentColor", kolor.name())
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{kolor.name()}" stroke-width="{grubosc}" stroke-linecap="round" '
           f'stroke-linejoin="round">{tresc}</svg>')
    skala = 2
    pixmapa = QPixmap(rozmiar * skala, rozmiar * skala)
    pixmapa.fill(Qt.GlobalColor.transparent)
    malarz = QPainter(pixmapa)
    QSvgRenderer(QByteArray(svg.encode())).render(malarz)
    malarz.end()
    pixmapa.setDevicePixelRatio(skala)
    return pixmapa


def ikona(nazwa, kolor, kolor_wybrany=None, rozmiar=18, grubosc=2.0):
    # WHAT: QIcon z kształtu makiety; opcjonalnie inny kolor dla zaznaczonej
    #       pozycji (pasek boczny: zwykła szara, wybrana niebieska).
    wynik = QIcon()
    wynik.addPixmap(_pixmapa_svg(nazwa, kolor, rozmiar, grubosc), QIcon.Mode.Normal)
    if kolor_wybrany is not None:
        wybrana = _pixmapa_svg(nazwa, kolor_wybrany, rozmiar, grubosc)
        wynik.addPixmap(wybrana, QIcon.Mode.Selected)
        wynik.addPixmap(wybrana, QIcon.Mode.Active)
    return wynik


class Przelacznik(QAbstractButton):
    """Przełącznik ON/OFF z makiet - QSS nie umie go narysować (Wyglad/CLAUDE.md)."""
    kolorWlaczony = _wlasciwosc_koloru("kolorWlaczony", "#3daee9")
    kolorWylaczony = _wlasciwosc_koloru("kolorWylaczony", "#444c56")
    kolorGalki = _wlasciwosc_koloru("kolorGalki", "#ffffff")

    def __init__(self, opis="", parent=None):
        super().__init__(parent)
        self._kolory = {}
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)
        self.setAccessibleName(opis)
        self.setToolTip(opis)

    def sizeHint(self):
        return QSize(44, 24)

    def ustaw_cicho(self, wlaczony):
        # WHY: stan z odświeżania nie może udawać kliknięcia użytkownika
        self.blockSignals(True)
        self.setChecked(wlaczony)
        self.blockSignals(False)
        self.update()

    def paintEvent(self, _zdarzenie):
        malarz = QPainter(self)
        malarz.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            malarz.setOpacity(0.4)
        malarz.setPen(Qt.PenStyle.NoPen)
        malarz.setBrush(self.kolorWlaczony if self.isChecked() else self.kolorWylaczony)
        malarz.drawRoundedRect(QRectF(0, 0, 44, 24), 12, 12)
        malarz.setBrush(self.kolorGalki)
        malarz.drawEllipse(QRectF(23 if self.isChecked() else 3, 3, 18, 18))


class Pierscien(QWidget):
    """Okrągły wskaźnik z karty "Zasoby komputera" (CPU / VRAM / RAM)."""
    kolorTla = _wlasciwosc_koloru("kolorTla", "#353b43")
    kolorLuku = _wlasciwosc_koloru("kolorLuku", "#3daee9")
    kolorPodpisu = _wlasciwosc_koloru("kolorPodpisu", "#a3abb5")

    def __init__(self, rodzaj, parent=None):
        super().__init__(parent)
        self._kolory = {}
        self.setProperty("rodzaj", rodzaj)  # WHY: theme.qss dobiera kolor łuku po rodzaju
        self.setFixedSize(84, 84)
        self._wartosc, self._tekst, self._podpis = None, "—", ""

    def ustaw(self, wartosc, tekst, podpis):
        self._wartosc, self._tekst, self._podpis = wartosc, tekst, podpis
        self.setAccessibleName(f"{podpis} {tekst}")
        self.update()

    def paintEvent(self, _zdarzenie):
        malarz = QPainter(self)
        malarz.setRenderHint(QPainter.RenderHint.Antialiasing)
        prostokat = QRectF(6, 6, 72, 72)
        for kolor, kat in ((self.kolorTla, 360), (self.kolorLuku, 360 * max(0.0, min(1.0, self._wartosc or 0)))):
            pioro = QPen(kolor, 7.7)
            pioro.setCapStyle(Qt.PenCapStyle.RoundCap)
            malarz.setPen(pioro)
            if kat >= 360:
                malarz.drawEllipse(prostokat)
            elif kat > 0:
                malarz.drawArc(prostokat, 90 * 16, int(-kat * 16))
        czcionka = QFont(self.font())
        czcionka.setPointSizeF(self.font().pointSizeF() * 1.25)
        czcionka.setWeight(QFont.Weight.Bold)
        malarz.setFont(czcionka)
        malarz.setPen(self.palette().color(self.foregroundRole()))
        malarz.drawText(QRectF(0, 18, 84, 30), Qt.AlignmentFlag.AlignCenter, self._tekst)
        czcionka = QFont(self.font())
        czcionka.setPointSizeF(self.font().pointSizeF() * 0.8)
        malarz.setFont(czcionka)
        malarz.setPen(self.kolorPodpisu)
        malarz.drawText(QRectF(0, 44, 84, 20), Qt.AlignmentFlag.AlignCenter, self._podpis)


class Wykres(QWidget):
    """Mały wykres liniowy z makiet (obciążenie GPU, sieć, procesor)."""
    kolorLinii = _wlasciwosc_koloru("kolorLinii", "#3daee9")

    def __init__(self, rodzaj, maks=None, punkty=60, parent=None):
        super().__init__(parent)
        self._kolory = {}
        self.setProperty("rodzaj", rodzaj)
        self._maks = maks  # WHY: None = skala dopasowana do największej wartości (np. sieć)
        self._wartosci = [0] * punkty  # WHY: linia bazowa od startu, jak w makiecie, zamiast pustego pola
        self._punkty = punkty
        self.setMinimumHeight(36)

    def dodaj(self, wartosc):
        self._wartosci = (self._wartosci + [wartosc or 0])[-self._punkty:]
        self.update()

    def paintEvent(self, _zdarzenie):
        if len(self._wartosci) < 2:
            return
        malarz = QPainter(self)
        malarz.setRenderHint(QPainter.RenderHint.Antialiasing)
        maks = self._maks or max(max(self._wartosci), 1)
        szer, wys = self.width() - 2, self.height() - 4
        krok = szer / (self._punkty - 1)
        start = self._punkty - len(self._wartosci)
        sciezka = QPainterPath()
        for i, w in enumerate(self._wartosci):
            punkt = QPointF(1 + (start + i) * krok, 2 + wys - wys * min(w, maks) / maks)
            if i == 0:
                sciezka.moveTo(punkt)
            else:
                sciezka.lineTo(punkt)
        malarz.setPen(QPen(self.kolorLinii, 2))
        malarz.drawPath(sciezka)


class SchematRoutera(QWidget):
    """Schemat tras z makiety Router: programy -> LiteLLM -> aliasy -> hosty."""
    kolorLinii = _wlasciwosc_koloru("kolorLinii", "#4f5862")
    kolorWezla = _wlasciwosc_koloru("kolorWezla", "#2c3138")
    kolorRamki = _wlasciwosc_koloru("kolorRamki", "#3a4048")
    kolorRoutera = _wlasciwosc_koloru("kolorRoutera", "#1d3a4f")
    kolorAkcentu = _wlasciwosc_koloru("kolorAkcentu", "#3daee9")
    kolorAkcentuJasny = _wlasciwosc_koloru("kolorAkcentuJasny", "#9fd6f3")
    kolorTekstu = _wlasciwosc_koloru("kolorTekstu", "#e8eaed")
    kolorWyciszony = _wlasciwosc_koloru("kolorWyciszony", "#a3abb5")
    kolorOnline = _wlasciwosc_koloru("kolorOnline", "#5fd38d")
    kolorOffline = _wlasciwosc_koloru("kolorOffline", "#6b737d")
    kolorTrasy1 = _wlasciwosc_koloru("kolorTrasy1", "#3daee9")
    kolorTrasy2 = _wlasciwosc_koloru("kolorTrasy2", "#c792ea")
    kolorTrasy3 = _wlasciwosc_koloru("kolorTrasy3", "#5fd38d")

    # WHY: proporcje kolumn wprost z makiety (szerokość 944 px)
    _KOLUMNY = ((0, 160), (230, 150), (470, 190), (744, 200))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kolory = {}
        self._programy = []
        self._router = ("LiteLLM", ":4000/v1")
        self._aliasy = []   # [(nazwa, opis, [indeksy hostów])]
        self._hosty = []    # [(adres, opis, online)]
        self.setMinimumHeight(212)

    def ustaw(self, programy, router, aliasy, hosty):
        self._programy, self._router, self._aliasy, self._hosty = programy, router, aliasy, hosty
        wiersze = max(3, len(aliasy), len(hosty), len(programy))
        self.setMinimumHeight(20 + 64 * wiersze)
        self.update()

    def _wiersze(self, n, wysokosc_wezla):
        # WHAT: pionowe pozycje n węzłów rozłożonych równo w obszarze schematu.
        obszar = self.height() - 20
        odstep = obszar / max(n, 1)
        return [20 + odstep * i + (odstep - wysokosc_wezla) / 2 for i in range(n)]

    def _x(self, kolumna):
        skala = self.width() / 944
        x, szer = self._KOLUMNY[kolumna]
        return x * skala, szer * skala

    def _wezel(self, malarz, prostokat, ramka, tlo=None, promien=8):
        malarz.setPen(QPen(ramka, 1))
        malarz.setBrush(tlo or self.kolorWezla)
        malarz.drawRoundedRect(prostokat, promien, promien)

    def _tekst(self, malarz, prostokat, tekst, kolor, czcionka, wyrownanie=Qt.AlignmentFlag.AlignLeft):
        malarz.setFont(czcionka)
        malarz.setPen(kolor)
        metryka = QFontMetrics(czcionka)
        tekst = metryka.elidedText(tekst, Qt.TextElideMode.ElideRight, int(prostokat.width()))
        malarz.drawText(prostokat, wyrownanie | Qt.AlignmentFlag.AlignVCenter, tekst)

    @staticmethod
    def _krzywa(x1, y1, x2, y2):
        sciezka = QPainterPath(QPointF(x1, y1))
        srodek = (x1 + x2) / 2
        sciezka.cubicTo(QPointF(srodek, y1), QPointF(srodek, y2), QPointF(x2, y2))
        return sciezka

    def paintEvent(self, _zdarzenie):
        malarz = QPainter(self)
        malarz.setRenderHint(QPainter.RenderHint.Antialiasing)
        zwykla = QFont(self.font())
        mala = QFont(self.font())
        mala.setPointSizeF(self.font().pointSizeF() * 0.82)
        naglowek = QFont(mala)
        naglowek.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        gruba = QFont(self.font())
        gruba.setWeight(QFont.Weight.Bold)
        mono = QFont("JetBrains Mono")
        mono.setPointSizeF(self.font().pointSizeF() * 0.92)
        trasy = [self.kolorTrasy1, self.kolorTrasy2, self.kolorTrasy3]

        for kolumna, tytul in enumerate((_("PROGRAMY"), _("ROUTER"), _("ALIASY"), _("HOSTY OLLAMA"))):
            x, szer = self._x(kolumna)
            self._tekst(malarz, QRectF(x, 0, szer, 14), tytul, self.kolorWyciszony, naglowek)

        # --- węzły i ich pozycje ---
        x_p, s_p = self._x(0)
        y_p = self._wiersze(len(self._programy), 44)
        x_r, s_r = self._x(1)
        y_r = 20 + (self.height() - 20 - 80) / 2
        x_a, s_a = self._x(2)
        y_a = self._wiersze(len(self._aliasy), 44)
        x_h, s_h = self._x(3)
        y_h = self._wiersze(len(self._hosty), 52)

        # --- linie: programy -> router (szare), router -> aliasy -> hosty (kolor trasy) ---
        malarz.setBrush(Qt.BrushStyle.NoBrush)
        for i, y in enumerate(y_p):
            malarz.setPen(QPen(self.kolorLinii, 1.6))
            malarz.drawPath(self._krzywa(x_p + s_p, y + 22, x_r, y_r + 30 + 10 * (i - 1)))
        for i, (_n, _o, hosty) in enumerate(self._aliasy):
            kolor = trasy[i % 3]
            malarz.setPen(QPen(kolor, 2))
            malarz.drawPath(self._krzywa(x_r + s_r, y_r + 30 + 10 * min(i, 2) - 10 * (i > 2),
                                         x_a, y_a[i] + 22))
            pioro = QPen(kolor, 2)
            if len(hosty) > 1:
                pioro.setDashPattern([2.5, 2])  # WHY: jak w makiecie - przerywana = kilka hostów naraz
            malarz.setPen(pioro)
            for j, indeks in enumerate(hosty):
                malarz.drawPath(self._krzywa(x_a + s_a, y_a[i] + 22, x_h, y_h[indeks] + 20 + 6 * j))

        for i, y in enumerate(y_p):
            prostokat = QRectF(x_p, y, s_p, 44)
            self._wezel(malarz, prostokat, self.kolorRamki)
            self._tekst(malarz, prostokat.adjusted(12, 0, -8, 0), self._programy[i], self.kolorTekstu, zwykla)

        prostokat = QRectF(x_r, y_r, s_r, 80)
        self._wezel(malarz, prostokat, self.kolorAkcentu, self.kolorRoutera, 12)
        self._tekst(malarz, QRectF(x_r, y_r + 18, s_r, 22), self._router[0], self.kolorTekstu, gruba,
                    Qt.AlignmentFlag.AlignHCenter)
        self._tekst(malarz, QRectF(x_r, y_r + 42, s_r, 20), self._router[1], self.kolorAkcentuJasny, mono,
                    Qt.AlignmentFlag.AlignHCenter)

        for i, (nazwa, opis, _h) in enumerate(self._aliasy):
            prostokat = QRectF(x_a, y_a[i], s_a, 44)
            self._wezel(malarz, prostokat, trasy[i % 3])
            self._tekst(malarz, QRectF(x_a + 12, y_a[i] + 4, s_a - 20, 20), nazwa, self.kolorTekstu, mono)
            self._tekst(malarz, QRectF(x_a + 12, y_a[i] + 22, s_a - 20, 18), opis, self.kolorWyciszony, mala)

        for i, (adres, opis, online) in enumerate(self._hosty):
            prostokat = QRectF(x_h, y_h[i], s_h, 52)
            self._wezel(malarz, prostokat, self.kolorRamki)
            malarz.setPen(Qt.PenStyle.NoPen)
            malarz.setBrush(self.kolorOnline if online else self.kolorOffline)
            malarz.drawEllipse(QRectF(x_h + 12, y_h[i] + 22, 8, 8))
            self._tekst(malarz, QRectF(x_h + 30, y_h[i] + 7, s_h - 38, 20), adres, self.kolorTekstu, mono)
            self._tekst(malarz, QRectF(x_h + 30, y_h[i] + 26, s_h - 38, 18), opis, self.kolorWyciszony, mala)


def _miniatura_czatu(szer, wys, duza=False):
    # WHAT: szkic okna czatu z makiet (Panel główny, Open WebUI) z ramek QSS.
    # WHY:  czysta dekoracja - mówi bez słów "to jest czat w przeglądarce".
    ramka = QFrame()
    ramka.setObjectName("thumb")
    ramka.setFixedSize(szer, wys)
    v = QVBoxLayout(ramka)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    if duza:
        pasek = QFrame()
        pasek.setObjectName("thumbBar")
        pasek.setFixedHeight(24)
        hp = QHBoxLayout(pasek)
        hp.setContentsMargins(10, 0, 40, 0)
        hp.setSpacing(6)
        for _i in range(2):
            kropka = QFrame()
            kropka.setObjectName("thumbDot")
            kropka.setFixedSize(8, 8)
            hp.addWidget(kropka)
        adres = QFrame()
        adres.setObjectName("thumbUrl")
        adres.setFixedHeight(12)
        hp.addSpacing(24)
        hp.addWidget(adres, 1)
        v.addWidget(pasek)
    srodek = QHBoxLayout()
    srodek.setSpacing(0)
    boczny = QFrame()
    boczny.setObjectName("thumbSide")
    boczny.setFixedWidth(90 if duza else 34)
    vb = QVBoxLayout(boczny)
    vb.setContentsMargins(10 if duza else 6, 12 if duza else 8, 10 if duza else 6, 8)
    vb.setSpacing(7 if duza else 5)
    for i in range(4 if duza else 3):
        linia = QFrame()
        linia.setObjectName("thumbLine" if i or not duza else "thumbLineStrong")
        linia.setFixedHeight(8 if duza else 5)
        vb.addWidget(linia)
    vb.addStretch(1)
    srodek.addWidget(boczny)
    czat = QVBoxLayout()
    czat.setContentsMargins(14 if duza else 8, 14 if duza else 10, 14 if duza else 8, 10)
    czat.setSpacing(10 if duza else 6)

    def _dymek(nazwa, wysokosc, szerokosc, prawo=False):
        dymek = QFrame()
        dymek.setObjectName(nazwa)
        dymek.setFixedHeight(wysokosc)
        dymek.setFixedWidth(szerokosc)
        czat.addWidget(dymek, alignment=Qt.AlignmentFlag.AlignRight if prawo else Qt.AlignmentFlag.AlignLeft)

    szer_czatu = szer - (90 if duza else 34) - (28 if duza else 16)
    if duza:
        etykieta = QLabel("model: czat")
        etykieta.setObjectName("thumbCaption")
        czat.addWidget(etykieta, alignment=Qt.AlignmentFlag.AlignHCenter)
    else:
        _dymek("thumbLine", 6, int(szer_czatu * 0.6))
    _dymek("thumbBubble", 14 if not duza else 26, int(szer_czatu * (1 if not duza else 0.7)), duza)
    _dymek("thumbBubbleMine", 14 if not duza else 44, int(szer_czatu * (0.8 if not duza else 0.85)), not duza)
    czat.addStretch(1)
    pole = QFrame()
    pole.setObjectName("thumbInput")
    pole.setFixedHeight(26 if duza else 12)
    czat.addWidget(pole)
    srodek.addLayout(czat, 1)
    v.addLayout(srodek, 1)
    return ramka


class PilnujWysokosci(QObject):
    """Filtr zdarzeń: minimalna wysokość widżetu = wysokość jego zawiniętych tekstów.

    WHY: Qt nie wlicza zawijanego tekstu (QLabel z setWordWrap) do minimalnej
         wysokości - przy niskim oknie albo dłuższym tłumaczeniu (np. niemieckim)
         ściskał kartę i elementy nachodziły na siebie. Tylko dla widżetów o stałej
         szerokości (np. karta strategii routera), więc przeliczanie się nie zapętla.
    """

    def eventFilter(self, obiekt, zdarzenie):
        if zdarzenie.type() == QEvent.Type.Resize and obiekt.layout():
            potrzebna = obiekt.layout().totalHeightForWidth(obiekt.width())
            if potrzebna > 0 and potrzebna != obiekt.minimumHeight():
                obiekt.setMinimumHeight(potrzebna)
        return False


class KartaWyboru(QFrame):
    """Pozycja "radio" z makiety Router (strategia wyboru hosta)."""
    kliknieto = pyqtSignal(str)

    def __init__(self, klucz, tytul, opis, parent=None):
        super().__init__(parent)
        self.klucz = klucz
        self.setObjectName("choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tytul)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 9, 12, 9)
        h.setSpacing(12)
        self.kropka = QLabel()
        self.kropka.setObjectName("radioDot")
        self.kropka.setFixedSize(14, 14)
        h.addWidget(self.kropka)
        v = QVBoxLayout()
        v.setSpacing(2)
        t = QLabel(tytul)
        t.setObjectName("choiceTitle")
        v.addWidget(t)
        o = _etykieta(opis, "muted")
        o.setWordWrap(True)
        v.addWidget(o)
        h.addLayout(v, 1)

    def resizeEvent(self, zdarzenie):
        # WHAT: minimalna wysokość = tyle, ile potrzebuje zawinięty opis przy obecnej szerokości.
        # WHY:  Qt nie liczy wysokości zawijanego tekstu do minimum - przy niskim
        #       oknie ściskał ramki i ucinał drugą linię opisu (np. w dłuższych
        #       tłumaczeniach). Szerokość karty jest stała, więc to się nie zapętla.
        super().resizeEvent(zdarzenie)
        potrzebna = self.layout().totalHeightForWidth(self.width())
        if potrzebna > 0 and potrzebna != self.minimumHeight():
            self.setMinimumHeight(potrzebna)

    def ustaw_wybrana(self, wybrana):
        for widget in (self, self.kropka):
            widget.setProperty("selected", "true" if wybrana else "false")
            _odswiez_styl(widget)

    def mousePressEvent(self, zdarzenie):
        if zdarzenie.button() == Qt.MouseButton.LeftButton:
            self.kliknieto.emit(self.klucz)
        super().mousePressEvent(zdarzenie)


class DialogAlias(QDialog):
    """Dodawanie / edycja aliasu routera ("czat" -> model na wybranych hostach)."""

    def __init__(self, modele, zajete, alias=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Edytuj alias") if alias else _("Dodaj alias"))
        self.setMinimumWidth(480)
        self._modele = {m["nazwa"]: m for m in modele}
        self._zajete = set(zajete) - ({alias["alias"]} if alias else set())
        self.wynik = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        formularz = QFormLayout()
        self.pole_alias = QLineEdit(alias["alias"] if alias else "")
        self.pole_alias.setPlaceholderText(_("np. czat, kod, embed"))
        formularz.addRow(_("Alias:"), self.pole_alias)
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.addItems(sorted(self._modele))
        if alias:
            self.combo_model.setCurrentText(alias["model"])
        self.combo_model.currentTextChanged.connect(self._wypelnij_hosty)
        formularz.addRow(_("Model:"), self.combo_model)
        layout.addLayout(formularz)

        layout.addWidget(_etykieta(_("Hosty (bez zaznaczenia = wszystkie, które mają ten model):"), "muted"))
        self._ramka_hostow = QVBoxLayout()
        layout.addLayout(self._ramka_hostow)
        self._pola_hostow = []
        self._wybrane = set(alias.get("hosty", [])) if alias else set()
        self._wypelnij_hosty()

        opis = _etykieta(_("Programy (Open WebUI, Continue) wybierają alias zamiast nazwy modelu - "
                           "model za aliasem zmienisz tutaj, bez przestawiania programów."), "muted")
        opis.setWordWrap(True)
        layout.addWidget(opis)
        pasek = QHBoxLayout()
        pasek.addStretch(1)
        btn_anuluj = QPushButton(_("Anuluj"))
        btn_anuluj.clicked.connect(self.reject)
        pasek.addWidget(btn_anuluj)
        btn_zapisz = QPushButton(_("Zapisz"))
        btn_zapisz.setObjectName("primary")
        btn_zapisz.setDefault(True)
        btn_zapisz.clicked.connect(self._zapisz)
        pasek.addWidget(btn_zapisz)
        layout.addLayout(pasek)

    def _wypelnij_hosty(self):
        for pole in self._pola_hostow:
            pole.deleteLater()
        self._pola_hostow = []
        model = self._modele.get(self.combo_model.currentText().strip())
        for host in (model["hosty"] if model else []):
            pole = QCheckBox(f"{host['nazwa']} ({urlparse(host['adres']).netloc})")
            pole.setProperty("adres", host["adres"])
            pole.setChecked(host["adres"] in self._wybrane)
            self._ramka_hostow.addWidget(pole)
            self._pola_hostow.append(pole)
        if not self._pola_hostow:
            brak = _etykieta(_("Ten model nie jest teraz widoczny na żadnym hoście."), "muted")
            self._ramka_hostow.addWidget(brak)
            self._pola_hostow.append(brak)

    def _zapisz(self):
        alias = self.pole_alias.text().strip()
        model = self.combo_model.currentText().strip()
        # WHY: alias trafia do configu LiteLLM i do programów jako nazwa modelu -
        #      bez spacji i znaków, które klienci API potrafią źle zinterpretować.
        if not re.fullmatch(r"[\w.:-]+", alias or ""):
            QMessageBox.warning(self, _("Błędny alias"), _("Alias to jedno słowo: litery, cyfry, kropka, myślnik."))
            return
        if alias in self._zajete:
            QMessageBox.warning(self, _("Alias już jest"), _("Alias {alias} już istnieje.").format(alias=alias))
            return
        if not model:
            QMessageBox.warning(self, _("Brak modelu"), _("Wybierz model dla aliasu."))
            return
        hosty = [p.property("adres") for p in self._pola_hostow
                 if isinstance(p, QCheckBox) and p.isChecked()]
        self.wynik = {"alias": alias, "model": model, "hosty": hosty}
        self.accept()


class KartaModelu(QFrame):
    """Kafelek modelu z makiety "Modele" (nazwa, rodzaj, rozmiar, hosty, akcje)."""

    def __init__(self, model, okno):
        super().__init__()
        self.setObjectName("card")
        self.model = model
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        gora = QHBoxLayout()
        nazwa = _etykieta("", "mono")
        nazwa.setObjectName("modelName")
        pelna = model["nazwa"]
        nazwa.setText(QFontMetrics(QFont("JetBrains Mono")).elidedText(
            _skroc_nazwe_modelu_hf(pelna), Qt.TextElideMode.ElideMiddle, 230))
        nazwa.setToolTip(_zbuduj_tooltip_modelu({
            "name": pelna, "family": model["rodzina"], "parameter_size": model["parametry"],
            "quantization_level": model["kwantyzacja"], "modified_at": model["modified_at"],
        }))
        gora.addWidget(nazwa, 1)
        rodzaj = QLabel(okno.nazwa_rodzaju(model["rodzaj"]))
        rodzaj.setObjectName("badge")
        gora.addWidget(rodzaj)
        v.addLayout(gora)

        rozmiar = model["rozmiar"]
        szczegoly = [f"{rozmiar / 1024 ** 3:.1f} GB" if rozmiar >= 1024 ** 3 else f"{rozmiar / 1024 ** 2:.0f} MB"]
        if model["kwantyzacja"] and model["kwantyzacja"] != "unknown":
            szczegoly.append(model["kwantyzacja"])
        if model["kontekst"]:
            szczegoly.append(_kontekst_krotko(model["kontekst"]))
        v.addWidget(_etykieta("   ·   ".join(szczegoly), "muted"))

        hosty = QHBoxLayout()
        hosty.setSpacing(6)
        for host in model["hosty"]:
            chip = QLabel(host["nazwa"])
            chip.setObjectName("hostChip")
            chip.setProperty("loaded", "true" if host["w_pamieci"] else "false")
            if host["w_pamieci"]:
                chip.setToolTip(_("w pamięci: {vram:.1f} GB VRAM + {ram:.1f} GB RAM").format(
                    vram=host["vram"] / 1024 ** 3, ram=host["ram"] / 1024 ** 3))
            hosty.addWidget(chip)
        hosty.addStretch(1)
        v.addLayout(hosty)
        v.addStretch(1)

        akcje = QHBoxLayout()
        akcje.setSpacing(6)
        w_pamieci = [h for h in model["hosty"] if h["w_pamieci"]]
        btn_laduj = QPushButton(_("Zwolnij") if w_pamieci else _("Załaduj"))
        btn_laduj.setObjectName("small")
        btn_laduj.setToolTip(_("Zwolnij model z pamięci karty") if w_pamieci
                             else _("Załaduj model do pamięci, żeby pierwsza odpowiedź była od razu"))
        btn_laduj.clicked.connect(lambda: okno.zaladuj_model(model, btn_laduj, bool(w_pamieci)))
        akcje.addWidget(btn_laduj)
        btn_kopiuj = QPushButton(_("Kopiuj na host"))
        btn_kopiuj.setObjectName("small")
        cele = [s for s in okno.hosty_online_modeli if s["adres"].rstrip("/") not in
                {h["adres"] for h in model["hosty"]}]
        btn_kopiuj.setEnabled(bool(cele))
        btn_kopiuj.setToolTip(_("Pobierz ten sam model na inny host") if cele
                              else _("Model jest już na wszystkich hostach online"))
        btn_kopiuj.clicked.connect(lambda: okno.kopiuj_model_na_host(model, btn_kopiuj, cele))
        akcje.addWidget(btn_kopiuj)
        akcje.addStretch(1)
        btn_usun = QPushButton()
        btn_usun.setObjectName("smallDanger")
        btn_usun.setIcon(ikona("kosz", PALETA.blad, rozmiar=13))
        btn_usun.setToolTip(_("Usuń model"))
        btn_usun.setAccessibleName(_("Usuń model"))
        btn_usun.clicked.connect(lambda: okno.usun_model(model, btn_usun))
        akcje.addWidget(btn_usun)
        v.addLayout(akcje)


# =============================================================================
#  Zasobnik systemowy (makieta Tray.dc.html) - ikona przy zegarze KDE
# =============================================================================
_PLIK_AUTOSTARTU = Path.home() / ".config" / "autostart" / f"{ID_PROGRAMU}.desktop"


def zasobnik_wlaczony():
    # WHY: domyślnie WYŁĄCZONY (decyzja użytkownika) - z włączonym zasobnikiem
    #      krzyżyk chowa okno zamiast zamykać program, to nie może zaskoczyć.
    return _ustawienia().value("zasobnik/wlaczony", False, type=bool)


def autostart_programu_wlaczony():
    return _PLIK_AUTOSTARTU.exists()


# WHAT: ścieżka do pliku .AppImage, gdy program działa z AppImage (ustawia ją jego środowisko).
# WHY:  z AppImage skrypt i Python leżą w katalogu zamontowanym na czas działania
#       (/tmp/.mount_...) - inny przy każdym uruchomieniu, więc autostart i wpis w menu
#       muszą wskazywać sam plik .AppImage.
_APPIMAGE = os.environ.get("APPIMAGE") or None

_PLIK_MENU = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") \
    / "applications" / f"{ID_PROGRAMU}.desktop"
_PLIK_IKONY_MENU = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") \
    / "icons" / "hicolor" / "256x256" / "apps" / f"{ID_PROGRAMU}.png"
_ZNACZNIK_APPIMAGE = "X-LamaDeck-AppImage=true"  # WHY: odróżnia nasz wpis od wpisu z install.sh


def _polecenie_programu():
    # WHAT: linia Exec= uruchamiająca ten program (bez argumentów).
    if _APPIMAGE:
        return f'"{_APPIMAGE}"'
    return f'"{sys.executable}" "{Path(__file__).resolve()}"'


def wpis_menu_appimage():
    # WHAT: stan wpisu w menu dla AppImage: "nasz" (dodany przez program), "inny"
    #       (program jest w menu z install.sh albo .deb) albo None (brak).
    if _PLIK_MENU.exists():
        try:
            return "nasz" if _ZNACZNIK_APPIMAGE in _PLIK_MENU.read_text(encoding="utf-8") else "inny"
        except OSError:
            return "inny"
    if Path(f"/usr/share/applications/{ID_PROGRAMU}.desktop").exists():
        return "inny"
    return None


def ustaw_wpis_menu_appimage(wlacz):
    # WHAT: dodaje/usuwa wpis w menu aplikacji wskazujący plik .AppImage, z ikoną.
    # WHY:  AppImage to sam plik - bez tego program nie pojawia się w menu KDE ani nie
    #       ma ikony na pasku zadań (KDE łączy okno z wpisem po nazwie lamadeck).
    #       Wpisu z install.sh / .deb nie ruszamy (brak znacznika).
    if wpis_menu_appimage() == "inny":
        return
    if not wlacz:
        _PLIK_MENU.unlink(missing_ok=True)
        _PLIK_IKONY_MENU.unlink(missing_ok=True)
    else:
        _PLIK_IKONY_MENU.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_PLIK_IKONY, _PLIK_IKONY_MENU)
        _PLIK_MENU.parent.mkdir(parents=True, exist_ok=True)
        _PLIK_MENU.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={NAZWA_PROGRAMU}\n"
            "Comment=Control panel for your local AI stack (Ollama, LiteLLM, Open WebUI)\n"
            "Comment[pl]=Panel sterowania lokalnym stosem AI (Ollama, LiteLLM, Open WebUI)\n"
            f"Exec={_polecenie_programu()}\n"
            f"Icon={ID_PROGRAMU}\n"
            "Categories=Utility;\n"
            "Terminal=false\n"
            f"{_ZNACZNIK_APPIMAGE}\n",
            encoding="utf-8")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(_PLIK_MENU.parent)],
                       capture_output=True, timeout=30)


def _odswiez_sciezke_appimage():
    # WHAT: po przeniesieniu pliku .AppImage poprawia ścieżkę w autostarcie i w menu.
    # WHY:  ktoś pobiera plik do Pobranych, a potem przenosi np. do ~/Programy - stare
    #       wpisy wskazywałyby nieistniejący plik.
    if not _APPIMAGE:
        return
    try:
        if wpis_menu_appimage() == "nasz" and _polecenie_programu() not in _PLIK_MENU.read_text(encoding="utf-8"):
            ustaw_wpis_menu_appimage(True)
        if _PLIK_AUTOSTARTU.exists() and _polecenie_programu() not in _PLIK_AUTOSTARTU.read_text(encoding="utf-8"):
            ustaw_autostart_programu(True)
    except (OSError, subprocess.SubprocessError):
        pass


def ustaw_autostart_programu(wlacz):
    # WHAT: wpis w ~/.config/autostart - KDE uruchamia program po zalogowaniu,
    #       od razu schowany w zasobniku (--w-zasobniku).
    # WHY:  bez roota i bez systemd - to standardowy autostart programów pulpitu.
    #       Wskazuje dokładnie ten skrypt i ten Python, które teraz działają (albo
    #       plik .AppImage), więc działa tak samo dla install.sh, .deb, AppImage
    #       i uruchomienia z klonu repo.
    if not wlacz:
        _PLIK_AUTOSTARTU.unlink(missing_ok=True)
        return
    _PLIK_AUTOSTARTU.parent.mkdir(parents=True, exist_ok=True)
    _PLIK_AUTOSTARTU.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAZWA_PROGRAMU}\n"
        f"Exec={_polecenie_programu()} --w-zasobniku\n"
        f"Icon={ID_PROGRAMU}\n"
        "X-KDE-autostart-after=panel\n"
        "Terminal=false\n"
    )


# WHAT: tytuł okienka zasobnika - po nim skrypt KWin znajduje okno do przesunięcia.
_TYTUL_OKNA_ZASOBNIKA = f"{NAZWA_PROGRAMU} - zasobnik"

# WHAT: skrypt KWin stawiający okienko w rogu nad zasobnikiem (przy panelu).
# WHY:  na Waylandzie program nie może sam ustawić położenia okna ani poznać
#       położenia kursora poza swoimi oknami - zna je tylko menedżer okien.
#       Skrypt pyta KWin o obszar roboczy (ekran bez panelu): panel u góry =
#       okienko u góry, inaczej na dole; zawsze przy prawej krawędzi, gdzie
#       w KDE jest zasobnik. Sprawdzone na Plasma 6.3 (Wayland).
#       Skrypt CZEKA na pojawienie się okna (windowAdded) i ustawia je od razu -
#       przesuwanie dopiero po pokazaniu dawało mignięcie na środku ekranu
#       (zgłoszenie użytkownika). Pętla po istniejących oknach obsługuje okienko,
#       które już jest na ekranie w chwili wczytania skryptu.
_SKRYPT_KWIN_ZASOBNIK = """
(function () {
  function ustaw(w) {
    if (w.caption !== "%s") return;
    const obszar = workspace.clientArea(KWin.PlacementArea, w);
    const ekran = workspace.clientArea(KWin.FullScreenArea, w);
    const g = w.frameGeometry;
    const y = (obszar.y > ekran.y) ? obszar.y + 8 : obszar.y + obszar.height - g.height - 8;
    w.frameGeometry = {x: obszar.x + obszar.width - g.width - 8, y: y, width: g.width, height: g.height};
  }
  workspace.windowAdded.connect(ustaw);
  for (const w of workspace.windowList()) ustaw(w);
})();
""" % _TYTUL_OKNA_ZASOBNIKA


def _kwin_przesun_okienko_zasobnika():
    # WHAT: wczytuje skrypt KWin (DBus, bez roota), który ustawia okienko zasobnika.
    # WHY:  wołane RAZ, przed pierwszym pokazaniem okienka - skrypt zostaje w KWin
    #       i ustawia okienko przy każdym kolejnym pojawieniu się. Stary skrypt
    #       o tej samej nazwie (np. z poprzedniego uruchomienia programu)
    #       najpierw wyładowujemy, żeby nie działały dwie kopie naraz.
    plik = Path(tempfile.gettempdir()) / f"{ID_PROGRAMU}-zasobnik-{getpass.getuser()}.js"
    try:
        plik.write_text(_SKRYPT_KWIN_ZASOBNIK)
    except OSError:
        return
    kwin = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting")
    if not kwin.isValid():
        return  # WHY: inny menedżer okien niż KWin - okienko zostaje tam, gdzie je postawił
    kwin.call("unloadScript", f"{ID_PROGRAMU}-zasobnik")
    kwin.call("loadScript", str(plik), f"{ID_PROGRAMU}-zasobnik")
    kwin.call("start")


def powiadomienie_systemowe(tytul, tekst, zasobnik=None):
    # WHAT: dymek powiadomienia KDE (org.freedesktop.Notifications przez DBus).
    # WHY:  DBus, a nie QSystemTrayIcon.showMessage - zasobnik jest domyślnie
    #       wyłączony, a bez ikony w zasobniku Qt nie pokaże żadnego dymka.
    #       DBus działa zawsze, bez dodatkowych programów (notify-send).
    #       "desktop-entry" łączy powiadomienie z wpisem programu w menu, więc KDE
    #       pokazuje je pod nazwą i ikoną programu (i pozwala je wyciszyć
    #       w Ustawieniach systemu → Powiadomienia).
    dbus = QDBusInterface("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                          "org.freedesktop.Notifications")
    if dbus.isValid():
        # WHY: sygnatura Notify to (susssasa{sv}i) - id i lista akcji muszą mieć
        #      dokładny typ DBus, inaczej odpowiedź "No such method 'Notify'".
        bez_id = QVariant(0)
        bez_id.convert(QMetaType(QMetaType.Type.UInt.value))
        bez_akcji = QDBusArgument()
        bez_akcji.add([], QMetaType.Type.QStringList.value)
        ikona_pliku = str(_PLIK_IKONY) if _PLIK_IKONY.exists() else ID_PROGRAMU
        odp = dbus.call("Notify", NAZWA_PROGRAMU, bez_id, ikona_pliku, tytul, tekst, bez_akcji,
                        {"desktop-entry": QVariant(ID_PROGRAMU)}, 10000)
        if odp.type() != QDBusMessage.MessageType.ErrorMessage:
            return
    # WHY: awaryjnie (pulpit bez usługi powiadomień) - dymek ikony, jeśli jest w zasobniku
    if zasobnik is not None and zasobnik.isVisible():
        zasobnik.showMessage(tytul, tekst, _ikona_programu(), 10000)


class OknoZasobnika(QWidget):
    """Okienko po kliknięciu ikony w zasobniku: usługi, zasoby, modele w pamięci.

    WHY: Qt.Popup - samo znika po kliknięciu obok, jak okienka apletów KDE.
         Dane bierze z głównego okna (stan usług, hosty, ostatnia próbka
         zasobów), a przełączniki wołają te same funkcje co panel główny.
    """

    def __init__(self, okno):
        # WHY: na Waylandzie okno typu Popup musi wisieć na WIDOCZNYM oknie programu -
        #      przy schowanym głównym oknie w ogóle się nie pokazywało, a przy
        #      otwartym wyskakiwało obok niego. Tam to zwykłe okno bez ramki,
        #      zawsze na wierzchu, ustawiane przez KWin (_kwin_przesun_okienko_zasobnika)
        #      i chowane, gdy klikniesz gdzie indziej (changeEvent). Na X11 Popup
        #      przy kursorze działa dobrze, więc zostaje.
        self.wayland = QApplication.platformName() == "wayland"
        flagi = Qt.WindowType.FramelessWindowHint
        flagi |= (Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint) if self.wayland else Qt.WindowType.Popup
        super().__init__(None, flagi)
        self.setWindowTitle(_TYTUL_OKNA_ZASOBNIKA)
        self.okno = okno
        self.zamkniete_o = 0.0
        self._bylo_aktywne = False
        self._skrypt_kwin = False
        # WHY: zaokrąglone rogi panelu - tło okna musi być przezroczyste
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(380)
        zewn = QVBoxLayout(self)
        zewn.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("trayPanel")
        zewn.addWidget(panel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(14)

        gora = QHBoxLayout()
        gora.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(_ikona_programu().pixmap(40, 40))
        gora.addWidget(logo)
        teksty = QVBoxLayout()
        teksty.setSpacing(0)
        tytul = QLabel(_("Lokalny stos AI"))
        tytul.setObjectName("trayTitle")
        teksty.addWidget(tytul)
        self.lbl_podsumowanie = QLabel("—")
        self.lbl_podsumowanie.setObjectName("allState")
        teksty.addWidget(self.lbl_podsumowanie)
        gora.addLayout(teksty, 1)
        ustawienia = QPushButton()
        ustawienia.setObjectName("iconButton")
        ustawienia.setIcon(ikona("ustawienia", PALETA.boczny, rozmiar=18, grubosc=1.8))
        ustawienia.setToolTip(_("Ustawienia"))
        ustawienia.setAccessibleName(_("Ustawienia"))
        ustawienia.clicked.connect(lambda: self._i_zamknij(lambda: okno.pokaz_okno("ustawienia")))
        gora.addWidget(ustawienia)
        v.addLayout(gora)

        uslugi = QFrame()
        uslugi.setObjectName("trayServices")
        vu = QVBoxLayout(uslugi)
        vu.setContentsMargins(0, 0, 0, 0)
        vu.setSpacing(0)
        self._wiersze = {}
        for i, (klucz, nazwa, slot) in enumerate((("ollama", "Ollama", okno.przelacz_ollame),
                                                  ("litellm", _("Router LiteLLM"), okno.przelacz_litellm),
                                                  ("webui", "Open WebUI", okno.przelacz_webui))):
            wiersz = QFrame()
            wiersz.setObjectName("trayRow")
            wiersz.setProperty("first", "true" if i == 0 else "false")
            h = QHBoxLayout(wiersz)
            h.setContentsMargins(14, 12, 14, 12)
            h.setSpacing(12)
            kropka = QLabel()
            kropka.setObjectName("trayDot")
            kropka.setFixedSize(9, 9)
            h.addWidget(kropka)
            napisy = QVBoxLayout()
            napisy.setSpacing(0)
            napisy.addWidget(QLabel(nazwa))
            opis = _etykieta("", "mono", muted="true")
            napisy.addWidget(opis)
            h.addLayout(napisy, 1)
            przelacznik = Przelacznik(nazwa)
            przelacznik.clicked.connect(slot)
            h.addWidget(przelacznik)
            vu.addWidget(wiersz)
            self._wiersze[klucz] = (kropka, opis, przelacznik)
        self._ramka_uslug = uslugi
        v.addWidget(uslugi)

        # WHY: w makiecie trzeci kafelek to "Tokeny/s" - takiej liczby żadna usługa
        #      nie podaje, więc jest RAM (zasada: nie zmyślamy danych).
        kafelki = QHBoxLayout()
        kafelki.setSpacing(8)
        self._kafelki = {}
        for klucz in ("CPU", "VRAM", "RAM"):
            kafelek = QFrame()
            kafelek.setObjectName("trayTile")
            vk = QVBoxLayout(kafelek)
            vk.setContentsMargins(10, 10, 10, 10)
            vk.setSpacing(2)
            vk.addWidget(_etykieta(klucz, "muted"))
            wartosc = _etykieta("—", "value")
            vk.addWidget(wartosc)
            kafelki.addWidget(kafelek, 1)
            self._kafelki[klucz] = wartosc
        v.addLayout(kafelki)

        v.addWidget(_etykieta(_("W pamięci"), "muted"))
        self._chipy = QHBoxLayout()
        self._chipy.setSpacing(6)
        v.addLayout(self._chipy)
        v.addStretch(1)

        self.btn_czat = QPushButton(_("Otwórz czat"))
        self.btn_czat.setObjectName("trayPrimary")
        self.btn_czat.setIcon(ikona("czat", PALETA.naAkcencie, rozmiar=16, grubosc=2.2))
        self.btn_czat.clicked.connect(lambda: self._i_zamknij(okno.otworz_czat))
        # WHAT: obok czatu mały przycisk do Open WebUI - tylko, gdy WebUI działa
        #       (układ 70/30, prośba użytkownika); odświeżany w odswiez().
        self.btn_webui = QPushButton("WebUI")
        self.btn_webui.setObjectName("trayButton")
        self.btn_webui.setIcon(ikona("otworz", PALETA.tekst, rozmiar=15, grubosc=2))
        self.btn_webui.setToolTip(_("Otwórz Open WebUI w przeglądarce"))
        self.btn_webui.clicked.connect(lambda: self._i_zamknij(okno.otworz_webui))
        self.btn_webui.hide()
        rzad_czatu = QHBoxLayout()
        rzad_czatu.setSpacing(8)
        rzad_czatu.addWidget(self.btn_czat, 7)
        rzad_czatu.addWidget(self.btn_webui, 3)
        v.addLayout(rzad_czatu)
        btn_panel = QPushButton(_("Otwórz panel zarządzania"))
        btn_panel.setObjectName("trayButton")
        btn_panel.clicked.connect(lambda: self._i_zamknij(okno.pokaz_okno))
        v.addWidget(btn_panel)
        # WHY: 560 px jak w makiecie - przy wszystkich usługach; bez niektórych
        #      okienko kurczy się o schowane wiersze (_dopasuj_wysokosc)
        self._pelna_wysokosc = self._wysokosc_ukladu()
        self.setFixedHeight(560)

    def _i_zamknij(self, akcja):
        self.hide()
        akcja()

    def changeEvent(self, zdarzenie):
        # WHAT: na Waylandzie okienko znika, gdy klikniesz poza nim (traci aktywność) -
        #       tak jak Popup na X11 i okienka apletów KDE.
        if zdarzenie.type() == QEvent.Type.ActivationChange and self.wayland:
            if self.isActiveWindow():
                self._bylo_aktywne = True
            elif self._bylo_aktywne and self.isVisible():
                self.hide()
        super().changeEvent(zdarzenie)

    def hideEvent(self, zdarzenie):
        # WHY: kliknięcie ikony przy otwartym okienku najpierw je zamyka (Qt.Popup),
        #      a zaraz potem przychodzi sygnał kliknięcia - bez tej chwili okienko
        #      otwierałoby się od razu z powrotem.
        self.zamkniete_o = time.monotonic()
        super().hideEvent(zdarzenie)

    def odswiez(self):
        # WHAT: stan z głównego okna -> podsumowanie, przełączniki, zasoby, modele w pamięci.
        okno = self.okno
        stan = okno._stan
        if stan:
            uslugi = {"ollama": (stan["zainstalowana"], stan["active"]),
                      "litellm": (stan["litellm"], stan["litellm_dziala"]),
                      "webui": (stan["webui"], stan["webui_dziala"])}
            online = sum(1 for srv in okno.serwery if (okno._hosty_stan.get(srv["adres"]) or {}).get("online"))
            opisy = {"ollama": _("{n} z {m} hostów online").format(n=online, m=len(okno.serwery)),
                     "litellm": f":{urlparse(LITELLM_URL).port or 4000}",
                     "webui": f":{_webui_port()}"}
            # WHAT: wiersze usług, których nie ma, znikają (jak w pasku bocznym i Ustawieniach
            #       głównego okna); instalacja jest na panelu głównym.
            widoczne = [k for k, (z, _d) in uslugi.items() if z]
            if widoczne != getattr(self, "_widoczne_uslugi", None):
                self._widoczne_uslugi = widoczne
                for klucz, (_k, _o, przelacznik) in self._wiersze.items():
                    wiersz = przelacznik.parentWidget()
                    wiersz.setVisible(klucz in widoczne)
                    # WHY: górna kreska oddziela wiersze - pierwszy WIDOCZNY jej nie ma
                    wiersz.setProperty("first", "true" if widoczne and klucz == widoczne[0] else "false")
                    _odswiez_styl(wiersz)
                self._ramka_uslug.setVisible(bool(widoczne))
                self._dopasuj_wysokosc()
            self.btn_webui.setVisible(stan["webui_dziala"])
            for klucz, (zainstalowana, dziala) in uslugi.items():
                kropka, opis, przelacznik = self._wiersze[klucz]
                kropka.setProperty("state", "ok" if dziala else "off")
                _odswiez_styl(kropka)
                opis.setText(opisy[klucz] if zainstalowana else _("Nie zainstalowane"))
                przelacznik.setEnabled(zainstalowana and not okno._w_toku(klucz))
                przelacznik.ustaw_cicho(dziala)
            zainstalowane = [d for z, d in uslugi.values() if z]
            dzialajace = sum(zainstalowane)
            if not zainstalowane:
                tekst, kolor = _("Nic nie jest zainstalowane"), "off"
            elif dzialajace == len(zainstalowane):
                tekst, kolor = _("Wszystko działa"), "ok"
            elif dzialajace:
                tekst, kolor = _("{n} z {m} usług działa").format(n=dzialajace, m=len(zainstalowane)), "warn"
            else:
                tekst, kolor = _("Wszystko zatrzymane"), "error"
            self.lbl_podsumowanie.setText(tekst)
            self.lbl_podsumowanie.setProperty("state", kolor)
            _odswiez_styl(self.lbl_podsumowanie)

        p = okno._ostatnie_zasoby
        if p:
            if p["cpu"] is not None:
                self._kafelki["CPU"].setText(f"{p['cpu']:.0f}%")
            if p["vram_calkowite"]:
                self._kafelki["VRAM"].setText(f"{100 * p['vram_uzyte'] / p['vram_calkowite']:.0f}%")
            if p["ram_calkowite"]:
                self._kafelki["RAM"].setText(f"{100 * p['ram_uzyte'] / p['ram_calkowite']:.0f}%")

        while self._chipy.count():
            element = self._chipy.takeAt(0)
            if element.widget():
                element.widget().deleteLater()
        zaladowane = []
        for st in okno._hosty_stan.values():
            for m in st.get("modele", []):
                if m["w_pamieci"] and m["nazwa"] not in zaladowane:
                    zaladowane.append(m["nazwa"])
        for nazwa in zaladowane[:3]:
            chip = QLabel(_skroc_nazwe_modelu_hf(nazwa))
            chip.setObjectName("trayChip")
            chip.setToolTip(nazwa)
            self._chipy.addWidget(chip)
        if len(zaladowane) > 3:
            self._chipy.addWidget(_etykieta(f"+{len(zaladowane) - 3}", "muted"))
        if not zaladowane:
            self._chipy.addWidget(_etykieta(_("żaden model nie jest w pamięci"), "muted"))
        self._chipy.addStretch(1)
        _odmaluj_rodzica(self.lbl_podsumowanie, *self._kafelki.values())

    def _wysokosc_ukladu(self):
        # WHY: przed pierwszym pokazaniem układy nie są jeszcze przeliczone i
        #      sizeHint() kłamie - najpierw style i przeliczenie układów.
        for widget in [self] + self.findChildren(QWidget):
            widget.ensurePolished()
            if widget.layout():
                widget.layout().invalidate()
                widget.layout().activate()
        return self.sizeHint().height()

    def _dopasuj_wysokosc(self):
        # WHAT: okienko krótsze dokładnie o schowane wiersze usług i "Otwórz czat".
        self.setFixedHeight(560 - max(0, self._pelna_wysokosc - self._wysokosc_ukladu()))

    def pokaz_przy(self, punkt):
        # WHAT: okienko przy kursorze (czyli przy ikonie), w całości na ekranie.
        # WHY:  geometria ikony zasobnika bywa pusta (KDE/Wayland) - kursor w chwili
        #       kliknięcia jest zawsze tuż przy niej.
        self.odswiez()
        if self.wayland:
            self._bylo_aktywne = False
            if not self._skrypt_kwin:
                # WHY: skrypt musi już czekać w KWin, ZANIM okno się pojawi - wtedy
                #      KWin stawia je od razu w rogu, bez mignięcia na środku
                _kwin_przesun_okienko_zasobnika()
                self._skrypt_kwin = True
            self.show()
            self.raise_()
            self.activateWindow()
            return
        ekran = (QApplication.screenAt(punkt) or QApplication.primaryScreen()).availableGeometry()
        x = min(max(punkt.x() - self.width() // 2, ekran.left() + 8), ekran.right() - self.width() - 8)
        y = punkt.y() - self.height() - 12 if punkt.y() > ekran.center().y() else punkt.y() + 12
        y = min(max(y, ekran.top() + 8), ekran.bottom() - self.height() - 8)
        self.move(x, y)
        self.show()


class WierszDziennika(QFrame):
    """Wpis z makiety "Dziennik": czas, usługa, poziom, treść i - przy błędzie - wyjaśnienie."""

    def __init__(self, wpis, okno):
        super().__init__()
        self.setObjectName("logRow")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 9, 18, 9)
        v.setSpacing(6)
        gora = QHBoxLayout()
        gora.setSpacing(14)
        czas = QLabel(time.strftime("%H:%M:%S", time.localtime(wpis["czas"])))
        czas.setObjectName("logTime")
        czas.setFixedWidth(70)
        czas.setToolTip(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wpis["czas"])))
        gora.addWidget(czas, alignment=Qt.AlignmentFlag.AlignTop)
        usluga = QLabel(_("Program") if wpis["usluga"] == "Program" else wpis["usluga"])
        usluga.setObjectName("logService")
        usluga.setProperty("usluga", wpis["usluga"])
        usluga.setFixedWidth(92)
        gora.addWidget(usluga, alignment=Qt.AlignmentFlag.AlignTop)
        poziom = QLabel({"BŁĄD": _("BŁĄD"), "UWAGA": _("UWAGA")}.get(wpis["poziom"], "INFO"))
        poziom.setObjectName("logLevel")
        poziom.setProperty("poziom", {"BŁĄD": "error", "UWAGA": "warn"}.get(wpis["poziom"], "info"))
        poziom.setFixedWidth(58)
        poziom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gora.addWidget(poziom, alignment=Qt.AlignmentFlag.AlignTop)
        tresc = QLabel(wpis["tekst"])
        tresc.setObjectName("logText")
        tresc.setWordWrap(True)
        tresc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tresc.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        gora.addWidget(tresc, 1, Qt.AlignmentFlag.AlignTop)
        v.addLayout(gora)

        pomoc = wpis.get("pomoc")
        if pomoc:
            ramka = QFrame()
            ramka.setObjectName("logHelp")
            h = QHBoxLayout(ramka)
            h.setContentsMargins(12, 10, 12, 10)
            h.setSpacing(12)
            tekst = QLabel(pomoc)
            tekst.setWordWrap(True)
            h.addWidget(tekst, 1)
            adres = okno.host_z_tekstu(wpis["tekst"])
            if adres:
                link = QPushButton(_("Pokaż host"))
                link.setObjectName("logHelpLink")
                link.setCursor(Qt.CursorShape.PointingHandCursor)
                link.clicked.connect(lambda: okno.pokaz_host(adres))
                h.addWidget(link)
            # WHY: wyjaśnienie wcięte pod czasem wpisu, jak w makiecie (margin-left 84px)
            wciecie = QHBoxLayout()
            wciecie.addSpacing(84)
            wciecie.addWidget(ramka, 1)
            v.addLayout(wciecie)


# =============================================================================
#  Główne okno - pasek boczny z siedmioma ekranami z makiet
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.serwery = _wczytaj_serwery()
        self._hosty_stan = {}           # WHY: adres -> wynik sprawdz_host(); przeżywa przebudowę UI
        self._host_wybrany = self.serwery[0]["adres"]
        self._strona = "panel"          # WHY: wybrany ekran; przeżywa przebudowę UI (zmiana języka)
        self._stan = None               # WHY: ostatni wynik RefreshWorker (usługi tego komputera)
        self._modele = []               # WHY: modele ze wszystkich hostów (ModeleWorker)
        self._czat = []                 # WHY: bieżąca rozmowa (ekran "Czat") - przeżywa zmianę języka
        self._czat_worker = None        # WHY: odpowiedź, która właśnie się pisze (CzatWorker)
        self._zalaczniki = []           # WHY: pliki czekające na wysłanie z następną wiadomością
        self._linki_nieudane = set()    # WHY: linki z pytania, których strony nie dało się pobrać
        self._szukaj_w_internecie = False  # WHY: przycisk z globusem w czacie - przeżywa przebudowę okna
        self._wyslij_po_stronach = False  # WHY: pytanie czeka na pobranie/przygotowanie stron z linków
        self._czat_id = None            # WHY: plik bieżącej rozmowy (nazwa bez .json); None = jeszcze nie zapisana
        self._czat_utworzono = None
        self._czat_tytul = None         # WHY: tytuł nadany przez użytkownika; None = z pierwszego pytania
        self._rozmowy = None            # WHY: lista zapisanych rozmów; None = jeszcze nie wczytana
        self._etykiety_zalacznikow = {}  # WHY: id(załącznika) -> etykieta chipa (postęp przygotowania)
        self.hosty_online_modeli = []
        self._filtr_modeli = "all"
        self._tylko_moe = False         # WHY: pole "Tylko modele MoE" w wyszukiwarce; przeżywa przebudowę UI
        self._wyniki_szukania = []      # WHY: pełne wyniki - pole MoE filtruje je bez ponownego szukania
        self._pobieranie = None         # WHY: jedno pobieranie naraz - {"worker", "model", "host", ...}
        self._warianty_dla = None       # WHY: (źródło, id) modelu, którego warianty CHCEMY pokazać
        self._wpisy = []                # WHY: wszystkie wpisy dziennika (program + systemd)
        self._kursory = {}              # WHY: kursory journald - kolejne odczyty pobierają tylko nowe wpisy
        self._dostep_ollama = True
        self._filtr_dziennika = "all"
        self._dziennik_od = 0.0         # WHY: "Wyczyść widok" ukrywa starsze wpisy, niczego nie kasuje
        self._router_zmiany = False     # WHY: zmiany tras czekają na ponowne uruchomienie routera
        self._probnik = ProbnikZasobow()
        self._workers = []              # WHY: trzymamy referencje, by wątki nie zniknęły w trakcie
        self._watki = {}                # WHY: rodzaj zadania -> wątek; jedno zadanie danego rodzaju naraz
        self._ustawienia_wczytane = False
        self._webui_info = {}
        self._wersje = {}
        self._ostatnie_zasoby = None    # WHY: ostatnia próbka zasobów - pokazuje ją też okienko zasobnika
        self.zasobnik = None            # WHY: ikona w zasobniku (QSystemTrayIcon), gdy włączona w Ustawieniach
        self.okno_zasobnika = None
        self._konczenie = False         # WHY: "Zakończ" z menu ikony - wtedy krzyżyk ma naprawdę zamknąć program

        # WHY: język wczytujemy PRZED _buduj_ui() - widgety od razu dostają właściwe napisy.
        self._jezyk_aktywny = _wczytaj_jezyk_zapisany()
        _wczytaj_jezyk(self._jezyk_aktywny)

        self.setWindowTitle(_("{nazwa} - lokalny stos AI").format(nazwa=NAZWA_PROGRAMU))
        # WHY: makiety mają 1280x840 razem z paskiem tytułu - tyle potrzeba,
        #      żeby każdy ekran mieścił się bez ściskania kart.
        # WHY: 1600x900 zamiast 1280x800 z makiet - prośba użytkownika ("program za
        #      mały"; ekran 3440x1440). Na mniejszym ekranie (laptop ze skalowaniem)
        #      minimum nie może przekroczyć dostępnej przestrzeni, bo okno nie
        #      zmieściłoby się w całości - stąd przycięcie do rozmiaru ekranu.
        ekran = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        self.setMinimumSize(min(1600, ekran.width()) if ekran else 1600,
                            min(900, ekran.height()) if ekran else 900)
        self._buduj_ui()
        self._ustaw_zasobnik(zasobnik_wlaczony())

        # WHAT: odświeżanie co 10 s (stan usług + dane widocznego ekranu) i co 2 s
        #       zasoby komputera - tylko gdy panel główny jest na ekranie.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tyk)
        self.timer.start(10000)
        self.timer_zasobow = QTimer(self)
        self.timer_zasobow.timeout.connect(self._tyk_zasobow)
        self.timer_zasobow.start(2000)
        self.odswiez()
        self.odswiez_hosty()
        self.odswiez_modele()
        self.odswiez_dziennik()

    # --- Budowa okna ---------------------------------------------------------
    def _sekcje(self):
        # WHAT: ekrany w kolejności paska bocznego - u góry codzienna praca
        #       (panel, hosty, modele), na dole router, Open WebUI, ustawienia
        #       i dziennik (patrz _SEKCJE_NA_DOLE).
        # WHY:  układ wskazany przez użytkownika (2026-10-29), inny niż w makiecie.
        return [
            ("panel", "panel", _("Panel główny"), self._strona_panel),
            ("hosty", "hosty", _("Hosty Ollama"), self._strona_hosty),
            ("modele", "modele", _("Modele"), self._strona_modele),
            ("czat", "czat", _("Czat"), self._strona_czat),
            ("router", "router", _("Router LiteLLM"), self._strona_router),
            ("webui", "webui", _("Open WebUI"), self._strona_webui),
            ("ustawienia", "ustawienia", _("Ustawienia"), self._strona_ustawienia),
            ("dziennik", "dziennik", _("Dziennik"), self._strona_dziennik),
        ]

    # WHAT: sekcje w dolnej grupie paska bocznego (nad linkiem do repozytorium) -
    #       usługi, separator, a pod nim ustawienia i dziennik programu.
    _SEKCJE_NA_DOLE = ("router", "webui")
    _SEKCJE_PROGRAMU = ("ustawienia", "dziennik")

    def _buduj_ui(self):
        # WHAT: pasek boczny po lewej, ekrany (QStackedWidget) po prawej.
        # WHY:  _buduj_ui woła też zmiana języka - całe okno budujemy od zera
        #       z danych trzymanych w self (serwery, stan hostów, wpisy dziennika).
        self._ustawienia_wczytane = False
        self._wiersze_dziennika = 0
        centralny = QWidget()
        centralny.setObjectName("central")
        self.setCentralWidget(centralny)
        glowny = QHBoxLayout(centralny)
        glowny.setContentsMargins(0, 0, 0, 0)
        glowny.setSpacing(0)

        self.strony = QStackedWidget()
        self._indeksy_stron = {}
        sekcje = self._sekcje()
        glowny.addWidget(self._pasek_boczny(sekcje))
        for klucz, _ikona, _nazwa, buduj in sekcje:
            strona = buduj()
            opakowanie = QWidget()
            uk = QVBoxLayout(opakowanie)
            uk.setContentsMargins(28, 24, 28, 24)
            uk.addWidget(strona)
            self._indeksy_stron[klucz] = self.strony.addWidget(opakowanie)
        glowny.addWidget(self.strony, 1)
        self.statusBar().setSizeGripEnabled(False)
        # WHY: przed pierwszym odczytem stanu (RefreshWorker) - szybkie sprawdzenie
        #      binarek, żeby pozycje routera/WebUI nie mrugały przy starcie
        self._pokaz_sekcje_uslug(self._stan or {"litellm": litellm_zainstalowane(),
                                                "webui": webui_zainstalowane()})
        self._przejdz(self._strona)
        if self._stan:
            self._po_odswiezeniu(self._stan)
        self._po_modelach(self._modele, self.hosty_online_modeli)

    def _pasek_boczny(self, sekcje):
        # WHAT: logo z nazwą i wersją, sekcje, na dole "Ustawienia" i link do repozytorium.
        panel = QFrame()
        panel.setObjectName("sidebarPanel")
        panel.setFixedWidth(232)
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 16, 0, 16)
        v.setSpacing(4)

        marka = QHBoxLayout()
        marka.setContentsMargins(20, 4, 20, 18)
        marka.setSpacing(10)
        logo = QLabel()
        logo.setObjectName("brandIcon")
        # WHY: 52 px zamiast 36 z makiety - prośba użytkownika ("ikona za mała"),
        #      logo ma dużo detali, które w 36 px się zlewały.
        logo.setFixedSize(52, 52)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(_ikona_programu().pixmap(52, 52))
        marka.addWidget(logo)
        napisy = QVBoxLayout()
        napisy.setSpacing(0)
        nazwa = QLabel(NAZWA_PROGRAMU)
        nazwa.setObjectName("brandName")
        napisy.addWidget(nazwa)
        wersja = QLabel(f"v{WERSJA}")
        wersja.setObjectName("brandVersion")
        napisy.addWidget(wersja)
        marka.addLayout(napisy, 1)
        v.addLayout(marka)

        # WHY: dwie listy (u góry codzienna praca, na dole _SEKCJE_NA_DOLE) - jak
        #      w typowych programach KDE; zaznaczenie pilnujemy ręcznie w _przejdz.
        self.lista_sekcji = QListWidget()
        self.lista_dolna = QListWidget()
        self.lista_programu = QListWidget()
        # WHY: trzy listy (u góry codzienna praca, na dole usługi i - pod separatorem -
        #      ustawienia z dziennikiem; prośba użytkownika o separator) - zaznaczenie
        #      w całym pasku pilnuje _przejdz.
        self._listy_paska = (self.lista_sekcji, self.lista_dolna, self.lista_programu)
        for lista in self._listy_paska:
            lista.setObjectName("sidebar")
            lista.setIconSize(QSize(18, 18))
            lista.setAccessibleName(_("Sekcje programu"))
            lista.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            lista.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # WHY: bez fokusu. Lista z fokusem, ale bez bieżącej pozycji (bo wybrany
            #      ekran jest w innej z trzech list), po powrocie okna na wierzch
            #      (Alt+Tab, "Ustawienia" w okienku zasobnika) sama wybierała pierwszą
            #      pozycję - i program przeskakiwał na Panel główny (zgłoszenie użytkownika).
            lista.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for klucz, nazwa_ikony, nazwa_sekcji, _b in sekcje:
            element = QListWidgetItem(ikona(nazwa_ikony, PALETA.boczny, PALETA.bocznyWybrany, 18, 1.8), nazwa_sekcji)
            element.setData(Qt.ItemDataRole.UserRole, klucz)
            if klucz in self._SEKCJE_NA_DOLE:
                self.lista_dolna.addItem(element)
            elif klucz in self._SEKCJE_PROGRAMU:
                self.lista_programu.addItem(element)
            else:
                self.lista_sekcji.addItem(element)
        self._dopasuj_wysokosc_listy(self.lista_dolna)
        self._dopasuj_wysokosc_listy(self.lista_programu)
        for lista in self._listy_paska:
            lista.currentItemChanged.connect(lambda e, _p: e and self._przejdz(e.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(self.lista_sekcji, 1)
        v.addWidget(self.lista_dolna)
        separator = QFrame()
        separator.setObjectName("sidebarSeparator")
        separator.setFixedHeight(1)
        # WHY: w osobnym widżecie, żeby dało się schować separator razem z marginesami
        #      (gdy nie ma ani routera, ani Open WebUI - patrz _pokaz_sekcje_uslug)
        self.ramka_separatora = QWidget()
        self.ramka_separatora.setObjectName("sidebarSeparatorRow")
        ramka_separatora = QHBoxLayout(self.ramka_separatora)
        ramka_separatora.setContentsMargins(20, 6, 20, 6)
        ramka_separatora.addWidget(separator)
        v.addWidget(self.ramka_separatora)
        v.addWidget(self.lista_programu)

        link = QPushButton("github.com/cyryllo/LamaDeck")
        link.setObjectName("sidebarLink")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/cyryllo/LamaDeck")))
        v.addWidget(link)
        return panel

    def _przejdz(self, klucz):
        # WHAT: pokazuje ekran i odświeża jego dane od razu (bez czekania 10 s).
        if klucz not in self._indeksy_stron:
            return
        self._strona = klucz
        self.strony.setCurrentIndex(self._indeksy_stron[klucz])
        for lista in self._listy_paska:
            lista.blockSignals(True)
            lista.clearSelection()
            for i in range(lista.count()):
                if lista.item(i).data(Qt.ItemDataRole.UserRole) == klucz:
                    lista.setCurrentRow(i)
            if not any(lista.item(i).isSelected() for i in range(lista.count())):
                lista.setCurrentRow(-1)
            lista.blockSignals(False)
        if klucz in ("panel", "hosty"):
            self.odswiez_hosty()
        if klucz == "panel":
            self._tyk_zasobow()
        if klucz in ("modele", "router"):
            self.odswiez_modele()
        if klucz == "router":
            self.odswiez_liste_agregatora()
        if klucz == "webui":
            self.odswiez_info_webui()
        if klucz == "ustawienia":
            self.odswiez_wersje()
            self.odswiez_modele()  # WHY: lista modeli embeddingów w karcie "Czat"
        if klucz == "dziennik":
            self.odswiez_dziennik()
        if klucz == "czat":
            self.odswiez_modele()
            self.pole_czat.setFocus()

    def _tytul_strony(self, tytul, opis):
        # WHAT: duży tytuł ekranu i jednozdaniowy opis pod nim (każdy ekran makiet).
        v = QVBoxLayout()
        v.setSpacing(4)
        v.addWidget(_etykieta(tytul, "h1"))
        lbl = _etykieta(opis, "muted")
        lbl.setObjectName("subtitle")
        lbl.setWordWrap(True)
        v.addWidget(lbl)
        return v

    def _przycisk(self, tekst, nazwa_ikony=None, rola=None, slot=None):
        # WHAT: przycisk w stylu makiet - opcjonalna ikona w kolorze tekstu przycisku.
        btn = QPushButton(tekst)
        if rola:
            btn.setObjectName(rola)
        if nazwa_ikony:
            kolor = {"primary": PALETA.naAkcencie, "danger": PALETA.blad}.get(rola, PALETA.tekst)
            btn.setIcon(ikona(nazwa_ikony, kolor, rozmiar=15, grubosc=2.4 if rola == "primary" else 2))
        if slot:
            btn.clicked.connect(slot)
        return btn

    def _karta(self, nazwa="card"):
        karta = QFrame()
        karta.setObjectName(nazwa)
        uk = QVBoxLayout(karta)
        uk.setContentsMargins(20, 18, 20, 18)
        uk.setSpacing(12)
        return karta, uk

    def _pill(self):
        pill = QLabel()
        pill.setObjectName("pill")
        return pill

    def _ustaw_pill(self, pill, tekst, stan):
        pill.setText(tekst)
        pill.setProperty("state", stan)
        _odswiez_styl(pill)

    # --- Pomocnicze: wątki, akcje, dziennik ---------------------------------
    def _uruchom_watek(self, rodzaj, worker, sygnal, slot):
        # WHAT: start wątku danego rodzaju, o ile poprzedni jeszcze nie pracuje.
        # WHY:  co 10 s odświeżamy kilka rzeczy naraz - wolny host nie może
        #       spiętrzyć kolejki tych samych zapytań.
        stary = self._watki.get(rodzaj)
        if stary and stary.isRunning():
            return False
        self._watki[rodzaj] = worker
        sygnal.connect(slot)
        worker.start()
        return True

    def _w_toku(self, rodzaj):
        w = self._watki.get(rodzaj)
        return bool(w and w.isRunning())

    def wpis_log(self, tekst, poziom=None, usluga="Program"):
        # WHAT: komunikat programu -> ekran "Dziennik" + pasek stanu na dole okna.
        # WHY:  makiety nie mają dziennika pod spodem każdego ekranu - krótka
        #       informacja na pasku stanu mówi, że coś się stało, a pełna historia
        #       jest w "Dzienniku" razem z wpisami usług.
        if poziom is None:
            poziom = "BŁĄD" if re.search(r"błąd|nie udało|error", tekst, re.IGNORECASE) else "INFO"
        self._dodaj_wpisy([{"czas": time.time(), "usluga": usluga, "poziom": poziom, "tekst": tekst}])
        self.statusBar().showMessage(tekst, 10000)

    def _uruchom_akcje(self, funkcja, opis, po=None, powiadom=False):
        # WHAT: akcja w tle, wynik do dziennika, potem odświeżenie stanu.
        #       powiadom=True - długie operacje (instalacje, aktualizacje): po końcu
        #       dymek KDE, gdy okno nie jest na wierzchu (_powiadom_w_tle).
        worker = ActionWorker(funkcja, opis)
        self._workers.append(worker)

        def _sprzatnij(sukces, komunikat, w=worker):
            self.wpis_log(komunikat, None if sukces else "BŁĄD")
            if powiadom:
                self._powiadom_w_tle(_("Gotowe") if sukces else _("Błąd"), komunikat)
            if w in self._workers:
                self._workers.remove(w)
            if po:
                po(sukces)
            # WHY: krótkie opóźnienie - stan usługi ustala się chwilę po komendzie.
            QTimer.singleShot(1200, self.odswiez)
            QTimer.singleShot(1500, self.odswiez_hosty)

        worker.zakonczono.connect(_sprzatnij)
        worker.start()
        return worker

    def _powiadom_w_tle(self, tytul, tekst):
        # WHY: tylko gdy okno NIE jest aktywne (schowane, zminimalizowane, inne okno
        #      na wierzchu) - gdy patrzysz na program, wynik i tak widać w oknie.
        if not self.isActiveWindow():
            powiadomienie_systemowe(tytul, tekst, self.zasobnik)

    def _zasobnik_otwarty(self):
        return bool(self.okno_zasobnika and self.okno_zasobnika.isVisible())

    def _tyk(self):
        self.odswiez()
        if (self._strona in ("panel", "hosty") and self.isVisible()) or self._zasobnik_otwarty():
            self.odswiez_hosty()
        if self._strona in ("modele", "router", "czat") and self.isVisible():
            self.odswiez_modele()
        if self._strona == "dziennik" and self.isVisible():
            self.odswiez_dziennik()

    def odswiez(self):
        worker = RefreshWorker()
        self._uruchom_watek("stan", worker, worker.wynik, self._po_odswiezeniu)

    @staticmethod
    def _dopasuj_wysokosc_listy(lista):
        # WHY: dolne listy paska mają dokładnie tyle wysokości, ile WIDOCZNE pozycje -
        #      bez przewijania; pusta lista znika całkiem.
        wiersze = [i for i in range(lista.count()) if not lista.isRowHidden(i)]
        lista.setVisible(bool(wiersze))
        lista.setFixedHeight(sum(lista.sizeHintForRow(i) for i in wiersze) + 2 * lista.frameWidth() + 4)

    def _pokaz_sekcje_uslug(self, stan):
        # WHAT: "Router LiteLLM" i "Open WebUI" w pasku bocznym tylko, gdy są zainstalowane.
        # WHY:  prośba użytkownika - kto ich nie używa, ma krótsze menu. Instalacja
        #       zostaje na panelu głównym (przycisk "Zainstaluj" w karcie usługi),
        #       a po niej pozycja sama się pojawia przy kolejnym odświeżeniu (10 s).
        ukryte = {"router": not stan.get("litellm"), "webui": not stan.get("webui")}
        lista = self.lista_dolna
        for i in range(lista.count()):
            klucz = lista.item(i).data(Qt.ItemDataRole.UserRole)
            lista.setRowHidden(i, ukryte.get(klucz, False))
        self._dopasuj_wysokosc_listy(lista)
        self.ramka_separatora.setVisible(lista.isVisible())
        # WHY: "Edytuj trasy" prowadzi na ekran routera - bez routera nie ma dokąd
        self.btn_edytuj_trasy.setVisible(not ukryte["router"])
        if ukryte.get(self._strona):
            self._przejdz("panel")  # WHY: usługę odinstalowano, gdy jej ekran był otwarty

    def _po_odswiezeniu(self, stan):
        # WHAT: stan usług tego komputera -> panel główny, ekran WebUI, Router, Ustawienia.
        self._stan = stan
        self._pokaz_sekcje_uslug(stan)
        self._odswiez_zasobnik()
        self._odswiez_panel_uslug(stan)
        self._odswiez_webui(stan)
        self._odswiez_router_stan(stan)
        self._odswiez_ustawienia(stan)

    # =========================================================================
    #  Ekran "Panel główny" (makieta Main.dc.html)
    # =========================================================================
    def _strona_panel(self):
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(20)

        naglowek = QHBoxLayout()
        naglowek.setSpacing(10)
        naglowek.addLayout(self._tytul_strony(
            _("Lokalny stos AI"), _("Bez terminala, bez Dockera, bez chmury. Wszystko działa na Twoim komputerze.")), 1)
        self.sw_wszystkie = Przelacznik(_("Wszystkie usługi"))
        self.sw_wszystkie.clicked.connect(self.przelacz_wszystkie)
        naglowek.addWidget(self.sw_wszystkie)
        podpis = QVBoxLayout()
        podpis.setSpacing(0)
        lbl = QLabel(_("WSZYSTKIE USŁUGI"))
        lbl.setObjectName("caption")
        podpis.addWidget(lbl)
        self.lbl_wszystkie = QLabel("—")
        self.lbl_wszystkie.setObjectName("allState")
        podpis.addWidget(self.lbl_wszystkie)
        naglowek.addLayout(podpis)
        naglowek.addSpacing(8)
        naglowek.addWidget(self._przycisk(_("Uruchom ponownie"), "odswiez", slot=self.uruchom_ponownie_wszystko))
        naglowek.addWidget(self._przycisk(_("Zatrzymaj wszystko"), "stop", "danger", self.zatrzymaj_wszystko))
        v.addLayout(naglowek)

        siatka = QGridLayout()
        siatka.setSpacing(18)
        siatka.addWidget(self._karta_panel_ollama(), 0, 0)
        siatka.addWidget(self._karta_panel_router(), 0, 1)
        siatka.addWidget(self._karta_panel_webui(), 1, 0)
        siatka.addWidget(self._karta_panel_zasoby(), 1, 1)
        for i in range(2):
            siatka.setColumnStretch(i, 1)
            siatka.setRowStretch(i, 1)
        v.addLayout(siatka, 1)
        return strona

    def _naglowek_karty(self, uk, tytul, podtytul_mono=False):
        # WHAT: tytuł + podtytuł po lewej, plakietka stanu + przełącznik (albo
        #       przycisk instalacji) po prawej - wspólne dla trzech kart usług.
        gora = QHBoxLayout()
        teksty = QVBoxLayout()
        teksty.setSpacing(2)
        teksty.addWidget(_etykieta(tytul, "h2"))
        podtytul = EtykietaSkracana()
        podtytul.setProperty("role", "mono" if podtytul_mono else "muted")
        podtytul.setProperty("muted", "true")
        teksty.addWidget(podtytul)
        gora.addLayout(teksty, 1)
        pill = self._pill()
        gora.addWidget(pill, alignment=Qt.AlignmentFlag.AlignTop)
        przelacznik = Przelacznik(tytul)
        gora.addWidget(przelacznik, alignment=Qt.AlignmentFlag.AlignTop)
        instaluj = QPushButton(_("Zainstaluj"))
        instaluj.setObjectName("primarySmall")
        instaluj.hide()
        gora.addWidget(instaluj, alignment=Qt.AlignmentFlag.AlignTop)
        uk.addLayout(gora)
        return podtytul, pill, przelacznik, instaluj

    def _karta_panel_ollama(self):
        karta, uk = self._karta()
        (self.lbl_ol_podtytul, self.pill_ol, self.sw_ol, self.btn_ol_instaluj) = self._naglowek_karty(uk, "Ollama")
        self.sw_ol.clicked.connect(self.przelacz_ollame)
        self.btn_ol_instaluj.clicked.connect(self.zainstaluj_ollame)
        self.ramka_hostow_panel = QFrame()
        self.ramka_hostow_panel.setObjectName("cardRow")
        self._wiersze_hostow_panel = QVBoxLayout(self.ramka_hostow_panel)
        self._wiersze_hostow_panel.setContentsMargins(0, 0, 0, 0)
        self._wiersze_hostow_panel.setSpacing(0)
        uk.addWidget(self.ramka_hostow_panel)
        uk.addStretch(1)
        dol = QHBoxLayout()
        dol.setSpacing(12)
        lewa = QVBoxLayout()
        lewa.setSpacing(6)
        lewa.addWidget(_etykieta(_("Załadowane modele"), "muted"))
        self._chipy_zaladowanych = QHBoxLayout()
        self._chipy_zaladowanych.setSpacing(6)
        lewa.addLayout(self._chipy_zaladowanych)
        dol.addLayout(lewa, 1)
        prawa = QVBoxLayout()
        prawa.setSpacing(4)
        self.lbl_gpu = _etykieta(_("Obciążenie GPU"), "muted")
        prawa.addWidget(self.lbl_gpu)
        self.wykres_gpu = Wykres("gpu", maks=100, punkty=150)  # WHY: 150 próbek co 2 s = 5 minut, jak w makiecie
        self.wykres_gpu.setFixedSize(170, 44)
        prawa.addWidget(self.wykres_gpu)
        dol.addLayout(prawa)
        uk.addLayout(dol)
        return karta

    def _karta_panel_router(self):
        karta, uk = self._karta()
        (self.lbl_ll_podtytul, self.pill_ll, self.sw_ll, self.btn_ll_instaluj) = self._naglowek_karty(
            uk, _("Router LiteLLM"), podtytul_mono=True)
        port = urlparse(LITELLM_URL).port or 4000
        self.lbl_ll_podtytul.setText(_("localhost:{port} · zgodny z OpenAI API").format(port=port))
        self.sw_ll.clicked.connect(self.przelacz_litellm)
        self.btn_ll_instaluj.clicked.connect(self.zainstaluj_litellm)
        self._trasy_panel = QGridLayout()
        self._trasy_panel.setHorizontalSpacing(8)
        self._trasy_panel.setVerticalSpacing(8)
        self._trasy_panel.setColumnStretch(2, 1)
        uk.addLayout(self._trasy_panel)
        uk.addStretch(1)
        stopka = QFrame()
        stopka.setObjectName("cardFooter")
        h = QHBoxLayout(stopka)
        h.setContentsMargins(0, 10, 0, 0)
        self.lbl_router_stopka = _etykieta("", "muted")
        h.addWidget(self.lbl_router_stopka, 1)
        self.btn_edytuj_trasy = QPushButton(_("Edytuj trasy"))
        self.btn_edytuj_trasy.setObjectName("link")
        self.btn_edytuj_trasy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edytuj_trasy.clicked.connect(lambda: self._przejdz("router"))
        h.addWidget(self.btn_edytuj_trasy)
        uk.addWidget(stopka)
        self._odswiez_panel_routera()
        return karta

    def _karta_panel_webui(self):
        karta, uk = self._karta()
        (self.lbl_wu_podtytul, self.pill_wu, self.sw_wu, self.btn_wu_instaluj) = self._naglowek_karty(
            uk, "Open WebUI", podtytul_mono=True)
        self.sw_wu.clicked.connect(self.przelacz_webui)
        self.btn_wu_instaluj.clicked.connect(self.zainstaluj_webui)
        srodek = QHBoxLayout()
        srodek.setSpacing(16)
        lewa = QVBoxLayout()
        lewa.setSpacing(10)
        lewa.addStretch(1)
        self.btn_wu_panel = self._przycisk(_("Otwórz w przeglądarce"), "otworz", "launchSmall", self.otworz_webui)
        self.btn_wu_panel.setIcon(ikona("otworz", PALETA.naAkcencie, rozmiar=18, grubosc=2.2))
        self.btn_wu_panel.setIconSize(QSize(18, 18))
        lewa.addWidget(self.btn_wu_panel)
        lewa.addWidget(_etykieta(_("Jedno kliknięcie, bez logowania do terminala."), "muted"))
        lewa.addStretch(1)
        srodek.addLayout(lewa, 1)
        srodek.addWidget(_miniatura_czatu(150, 100), alignment=Qt.AlignmentFlag.AlignVCenter)
        uk.addLayout(srodek, 1)
        return karta

    def _karta_panel_zasoby(self):
        karta, uk = self._karta()
        uk.setSpacing(10)
        uk.addWidget(_etykieta(_("Zasoby komputera"), "h2"))
        pierscienie = QHBoxLayout()
        self.pr_cpu, self.pr_vram, self.pr_ram = Pierscien("cpu"), Pierscien("vram"), Pierscien("ram")
        for p in (self.pr_cpu, self.pr_vram, self.pr_ram):
            pierscienie.addStretch(1)
            pierscienie.addWidget(p)
        pierscienie.addStretch(1)
        uk.addLayout(pierscienie)
        uk.addStretch(1)
        wykresy = QGridLayout()
        wykresy.setHorizontalSpacing(16)
        wykresy.setVerticalSpacing(2)
        self.lbl_siec = _etykieta(_("Ruch sieciowy"), "muted")
        self.lbl_cpu_wykres = _etykieta(_("Procesor"), "muted")
        self.wykres_siec = Wykres("siec", punkty=150)
        self.wykres_cpu = Wykres("cpu", maks=100, punkty=150)
        wykresy.addWidget(self.lbl_siec, 0, 0)
        wykresy.addWidget(self.lbl_cpu_wykres, 0, 1)
        wykresy.addWidget(self.wykres_siec, 1, 0)
        wykresy.addWidget(self.wykres_cpu, 1, 1)
        uk.addLayout(wykresy)
        return karta

    def _odswiez_panel_uslug(self, stan):
        # WHAT: plakietki, przełączniki i przyciski instalacji trzech usług.
        def _usluga(pill, przelacznik, instaluj, zainstalowana, dziala, w_toku, nazwa_zatrzymana):
            instaluj.setVisible(not zainstalowana)
            przelacznik.setVisible(zainstalowana)
            przelacznik.setEnabled(not w_toku)
            instaluj.setEnabled(not w_toku)
            przelacznik.ustaw_cicho(dziala)
            if not zainstalowana:
                self._ustaw_pill(pill, _("Nie zainstalowane"), "warn")
            elif dziala:
                self._ustaw_pill(pill, _("Działa"), "ok")
            else:
                self._ustaw_pill(pill, nazwa_zatrzymana, "stopped")

        _usluga(self.pill_ol, self.sw_ol, self.btn_ol_instaluj, stan["zainstalowana"], stan["active"],
                self._w_toku("ollama"), _("Zatrzymana"))
        _usluga(self.pill_ll, self.sw_ll, self.btn_ll_instaluj, stan["litellm"], stan["litellm_dziala"],
                self._w_toku("litellm"), _("Zatrzymany"))
        _usluga(self.pill_wu, self.sw_wu, self.btn_wu_instaluj, stan["webui"], stan["webui_dziala"],
                self._w_toku("webui"), _("Zatrzymane"))

        polaczone = self._webui_info.get("router")
        self.lbl_wu_podtytul.setText(
            f"localhost:{_webui_port()} · " + (_("połączony z routerem") if polaczone else _("bez routera")))
        if not stan["webui"]:
            self.btn_wu_panel.setText(_("Zainstaluj Open WebUI"))
        elif stan["webui_dziala"]:
            self.btn_wu_panel.setText(_("Otwórz w przeglądarce"))
        else:
            self.btn_wu_panel.setText(_("Uruchom Open WebUI"))
        self.btn_wu_panel.setEnabled(not self._w_toku("webui"))

        uslugi = [(stan["zainstalowana"], stan["active"]), (stan["litellm"], stan["litellm_dziala"]),
                  (stan["webui"], stan["webui_dziala"])]
        zainstalowane = [d for z, d in uslugi if z]
        dzialajace = sum(zainstalowane)
        self.sw_wszystkie.ustaw_cicho(bool(zainstalowane) and dzialajace == len(zainstalowane))
        if zainstalowane and dzialajace == len(zainstalowane):
            tekst, stan_etykiety = _("Działają"), "ok"
        elif dzialajace:
            tekst, stan_etykiety = _("Działają {n} z {m}").format(n=dzialajace, m=len(zainstalowane)), "warn"
        else:
            tekst, stan_etykiety = _("Zatrzymane"), "error"
        self.lbl_wszystkie.setText(tekst)
        self.lbl_wszystkie.setProperty("state", stan_etykiety)
        _odswiez_styl(self.lbl_wszystkie)

    def _odswiez_panel_hostow(self):
        # WHAT: mini-lista hostów i załadowane modele na karcie Ollama.
        while self._wiersze_hostow_panel.count():
            element = self._wiersze_hostow_panel.takeAt(0)
            if element.widget():
                element.widget().deleteLater()
        online = 0
        for i, serwer in enumerate(self.serwery[:4]):
            stan = self._hosty_stan.get(serwer["adres"])
            wiersz = QFrame()
            wiersz.setObjectName("hostRow")
            wiersz.setProperty("last", "true" if i == min(len(self.serwery), 4) - 1 else "false")
            h = QHBoxLayout(wiersz)
            h.setContentsMargins(12, 8, 12, 8)
            h.setSpacing(10)
            kropka = QLabel()
            kropka.setObjectName("dotSmall")
            kropka.setFixedSize(8, 8)
            kropka.setProperty("state", "ok" if stan and stan["online"] else "off")
            h.addWidget(kropka)
            adres = _etykieta(urlparse(serwer["adres"]).netloc or serwer["adres"], "mono")
            if not (stan and stan["online"]):
                adres.setProperty("muted", "true")
            h.addWidget(adres, 1)
            if stan is None:
                info = _etykieta(_("sprawdzam..."), "muted")
            elif stan["online"]:
                gib = 1024 ** 3
                opis = (_("ten komputer") if stan["lokalny"] else _("zdalny"))
                if stan["vram_calkowite"]:
                    opis += f" · {stan['vram_calkowite'] / gib:.0f} GB"
                info = _etykieta(opis, "muted")
            else:
                info = _etykieta(_("brak połączenia"), state="warn")
            h.addWidget(info)
            self._wiersze_hostow_panel.addWidget(wiersz)
        online = sum(1 for s in self.serwery if (self._hosty_stan.get(s["adres"]) or {}).get("online"))
        self.lbl_ol_podtytul.setText(_("{n} z {m} hostów online").format(n=online, m=len(self.serwery)))

        while self._chipy_zaladowanych.count():
            element = self._chipy_zaladowanych.takeAt(0)
            if element.widget():
                element.widget().deleteLater()
        zaladowane = []
        for stan in self._hosty_stan.values():
            for m in stan.get("modele", []):
                if m["w_pamieci"] and m["nazwa"] not in zaladowane:
                    zaladowane.append(m["nazwa"])
        for nazwa in zaladowane[:3]:
            chip = QLabel(_skroc_nazwe_modelu_hf(nazwa))
            chip.setObjectName("chip")
            chip.setToolTip(nazwa)
            self._chipy_zaladowanych.addWidget(chip)
        if len(zaladowane) > 3:
            reszta = QLabel(f"+{len(zaladowane) - 3}")
            reszta.setObjectName("chipMuted")
            self._chipy_zaladowanych.addWidget(reszta)
        if not zaladowane:
            self._chipy_zaladowanych.addWidget(_etykieta(_("żaden model nie jest w pamięci"), "muted"))
        self._chipy_zaladowanych.addStretch(1)

    def _odswiez_panel_routera(self):
        # WHAT: do trzech tras "alias -> model @ hosty" i stopka z liczbą aliasów i strategią.
        while self._trasy_panel.count():
            element = self._trasy_panel.takeAt(0)
            if element.widget():
                element.widget().deleteLater()
        aliasy = _litellm_aliasy()
        nazwy_hostow = {s["adres"]: s["nazwa"] for s in self.serwery}
        for i, a in enumerate(aliasy[:3]):
            alias = QLabel(a["alias"])
            alias.setObjectName("routeAlias")
            self._trasy_panel.addWidget(alias, i, 0)
            strzalka = QLabel()
            strzalka.setPixmap(_pixmapa_svg("router", PALETA.akcent, 16))
            self._trasy_panel.addWidget(strzalka, i, 1)
            hosty = [nazwy_hostow.get(h, urlparse(h).hostname) for h in a.get("hosty", [])]
            cel = f"{_skroc_nazwe_modelu_hf(a['model'])} @ " + (", ".join(hosty) if hosty else _("wszystkie hosty"))
            lbl_cel = EtykietaSkracana(cel)
            lbl_cel.setProperty("role", "mono")
            lbl_cel.setProperty("muted", "true")
            self._trasy_panel.addWidget(lbl_cel, i, 2)
        if not aliasy:
            opis = _etykieta(_("Brak aliasów - programy widzą modele pod ich własnymi nazwami. "
                               "Aliasy (np. \"czat\", \"kod\") dodasz na ekranie Router LiteLLM."), "muted")
            opis.setWordWrap(True)
            self._trasy_panel.addWidget(opis, 0, 0, 1, 3)
        nazwy = {"simple-shuffle": _("na zmianę"), "least-busy": _("najmniej zajęty"),
                 "latency-based-routing": _("najszybszy")}
        self.lbl_router_stopka.setText(_("Aliasy: {n} · gdy model jest na kilku hostach: {strategia}").format(
            n=len(aliasy), strategia=nazwy[_litellm_strategia()]))

    def _tyk_zasobow(self):
        if not ((self._strona == "panel" and self.isVisible()) or self._zasobnik_otwarty()):
            return
        worker = ZasobyWorker(self._probnik)
        self._uruchom_watek("zasoby", worker, worker.wynik, self._po_zasobach)

    def _po_zasobach(self, p):
        self._ostatnie_zasoby = p
        if self._zasobnik_otwarty():
            self.okno_zasobnika.odswiez()
        gib = 1024 ** 3
        if p["cpu"] is not None:
            self.pr_cpu.ustaw(p["cpu"] / 100, f"{p['cpu']:.0f}%", "CPU")
            self.wykres_cpu.dodaj(p["cpu"])
            self.lbl_cpu_wykres.setText(_("Procesor · {proc:.0f}%").format(proc=p["cpu"]))
        if p["vram_calkowite"]:
            udzial = p["vram_uzyte"] / p["vram_calkowite"]
            self.pr_vram.ustaw(udzial, f"{udzial * 100:.0f}%", "VRAM")
            self.pr_vram.setToolTip(_("Pamięć karty: {u:.1f} z {c:.1f} GB").format(
                u=p["vram_uzyte"] / gib, c=p["vram_calkowite"] / gib))
        else:
            self.pr_vram.ustaw(None, "—", "VRAM")
        if p["ram_calkowite"]:
            self.pr_ram.ustaw(p["ram_uzyte"] / p["ram_calkowite"], f"{p['ram_uzyte'] / gib:.1f}",
                              _("z {gb:.0f} GB").format(gb=p["ram_calkowite"] / gib))
        if p["gpu"] is not None:
            self.wykres_gpu.dodaj(p["gpu"])
            self.lbl_gpu.setText(_("Obciążenie GPU · {proc}%").format(proc=p["gpu"]))
        if p["siec"] is not None:
            self.wykres_siec.dodaj(p["siec"])
            mb = p["siec"] / 1024 ** 2
            tekst = f"{mb:.1f} MB/s" if mb >= 0.1 else f"{p['siec'] / 1024:.0f} KB/s"
            self.lbl_siec.setText(_("Ruch sieciowy · {predkosc}").format(predkosc=tekst))
        _odmaluj_rodzica(self.lbl_siec, self.lbl_cpu_wykres, self.lbl_gpu)

    # --- Akcje panelu: usługi ------------------------------------------------
    def przelacz_ollame(self, wlacz):
        self.wpis_log(_("Uruchamiam usługę Ollama...") if wlacz else _("Zatrzymuję usługę Ollama..."))
        self._watki["ollama"] = self._uruchom_akcje(
            usluga_start if wlacz else usluga_stop, _("Start usługi") if wlacz else _("Stop usługi"))

    def przelacz_litellm(self, wlacz):
        if wlacz:
            self.uruchom_litellm()
        else:
            self.wpis_log(_("Zatrzymuję LiteLLM..."))
            self._watki["litellm"] = self._uruchom_akcje(litellm_zatrzymaj, _("Zatrzymanie LiteLLM"))

    def uruchom_litellm(self):
        # WHY: config.yaml generujemy tuż przed (re)startem z AKTUALNEJ listy
        #      serwerów, aliasów i strategii - zmiany zadziałają dopiero teraz.
        if self._w_toku("litellm"):
            return
        self.wpis_log(_("Uruchamiam LiteLLM..."))

        def _po(sukces):
            if sukces:
                self._router_zmiany = False
                self._pokaz_zmiany_routera()
                self.wpis_log(_("Adres dla klientów (np. Continue): {adres}").format(adres=f"{LITELLM_URL}/v1"))

        self._watki["litellm"] = self._uruchom_akcje(
            lambda: litellm_uruchom(self.serwery), _("Uruchomienie LiteLLM"), _po)

    def przelacz_webui(self, wlacz):
        if wlacz:
            self.uruchom_webui()
        else:
            self.wpis_log(_("Zatrzymuję Open WebUI..."))
            self._watki["webui"] = self._uruchom_akcje(webui_zatrzymaj, _("Zatrzymanie Open WebUI"))

    def uruchom_webui(self, restart=False):
        if self._w_toku("webui"):
            return
        self.wpis_log(_("Uruchamiam Open WebUI..."))

        def _start():
            if restart:
                webui_zatrzymaj()
            webui_uruchom()

        def _po(sukces):
            # WHY: przeglądarka otwiera się sama tylko na życzenie (przełącznik na
            #      ekranie Open WebUI) - czasem chcesz tylko odpalić WebUI w tle.
            if sukces and webui_ustawienie("otworz_po_starcie", False):
                QDesktopServices.openUrl(QUrl(_webui_url()))

        self._watki["webui"] = self._uruchom_akcje(_start, _("Uruchomienie Open WebUI"), _po)

    def otworz_czat(self):
        # WHAT: "Otwórz czat" w zasobniku - ekran "Czat" w programie.
        # WHY:  decyzja użytkownika (2026-10-29): wbudowany czat, nie Open WebUI -
        #       otwiera się od razu, bez startu WebUI i bez przeglądarki.
        self.pokaz_okno("czat")

    def otworz_webui(self):
        # WHAT: jeden przycisk, trzy znaczenia - instaluj / uruchom / otwórz.
        if not self._stan:
            return
        if not self._stan["webui"]:
            self.zainstaluj_webui()
        elif self._stan["webui_dziala"]:
            QDesktopServices.openUrl(QUrl(_webui_url()))
        else:
            self.uruchom_webui()

    def _zadania_uslug(self, dzialanie):
        # WHAT: lista (opis, funkcja) dla zainstalowanych usług - do akcji "wszystkie naraz".
        stan = self._stan or {}
        zadania = []
        if stan.get("zainstalowana"):
            zadania.append(("Ollama", {"start": usluga_start, "stop": usluga_stop,
                                       "restart": usluga_restart}[dzialanie]))
        if stan.get("litellm"):
            zadania.append(("LiteLLM", {"start": lambda: litellm_uruchom(self.serwery), "stop": litellm_zatrzymaj,
                                        "restart": lambda: litellm_uruchom(self.serwery)}[dzialanie]))
        if stan.get("webui"):
            zadania.append(("Open WebUI", {"start": webui_uruchom, "stop": webui_zatrzymaj,
                                           "restart": lambda: (webui_zatrzymaj(), webui_uruchom())}[dzialanie]))
        return zadania

    def _wszystkie(self, dzialanie, opis):
        # WHY: po kolei w jednym wątku - Ollama musi wstać przed LiteLLM, który
        #      przy starcie pyta hosty o listę modeli.
        zadania = self._zadania_uslug(dzialanie)
        if dzialanie == "stop":
            zadania.reverse()
        if not zadania or self._w_toku("wszystkie"):
            return

        def _po_kolei():
            bledy = []
            for nazwa, funkcja in zadania:
                try:
                    funkcja()
                except Exception as e:
                    bledy.append(f"{nazwa}: {e}")
            if bledy:
                raise RuntimeError("; ".join(bledy))

        self.wpis_log(opis + "...")
        self._watki["wszystkie"] = self._uruchom_akcje(_po_kolei, opis)

    def przelacz_wszystkie(self, wlacz):
        self._wszystkie("start" if wlacz else "stop",
                        _("Uruchamianie wszystkich usług") if wlacz else _("Zatrzymywanie wszystkich usług"))

    def uruchom_ponownie_wszystko(self):
        self._wszystkie("restart", _("Ponowne uruchamianie wszystkich usług"))

    def zatrzymaj_wszystko(self):
        self._wszystkie("stop", _("Zatrzymywanie wszystkich usług"))

    # --- Instalacja usług ----------------------------------------------------
    def zainstaluj_ollame(self):
        if self._w_toku("ollama"):
            return
        # WHY: pobieranie i uruchamianie skryptu z internetu jako root - wymagamy świadomej zgody.
        odp = QMessageBox.question(
            self, _("Zainstaluj Ollama"),
            _("Zainstalować Ollamę teraz?\n\n"
              "Pobierze i uruchomi oficjalny skrypt instalacyjny z ollama.com\n"
              "(wymaga uprawnień administratora i połączenia z internetem)."),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Instaluję Ollamę - to może potrwać kilka minut..."))
        self._watki["ollama"] = self._uruchom_akcje(ollama_zainstaluj, _("Instalacja Ollamy"), powiadom=True)

    def zainstaluj_litellm(self):
        if self._w_toku("litellm"):
            return
        odp = QMessageBox.question(
            self, _("Zainstaluj LiteLLM"),
            _("Zainstalować LiteLLM teraz?\n\n"
              "Zainstaluje pakiet 'litellm[proxy]' przez uv, dla bieżącego użytkownika\n"
              "(bez Dockera, bez uprawnień administratora - wymaga internetu)."),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Instaluję LiteLLM - to może potrwać kilka minut..."))
        self._watki["litellm"] = self._uruchom_akcje(litellm_zainstaluj, _("Instalacja LiteLLM"), powiadom=True)

    def zainstaluj_webui(self):
        if self._w_toku("webui"):
            return
        odp = QMessageBox.question(
            self, _("Zainstaluj Open WebUI"),
            _("Zainstalować Open WebUI teraz?\n\n"
              "Zainstaluje pakiet 'open-webui' przez pip, dla bieżącego użytkownika\n"
              "(bez Dockera, bez uprawnień administratora - wymaga internetu)."),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Instaluję Open WebUI - to może potrwać kilka minut..."))

        def _po(sukces):
            if sukces:
                self._zapytaj_o_opcjonalne_pakiety_webui()

        self._watki["webui"] = self._uruchom_akcje(webui_zainstaluj, _("Instalacja Open WebUI"), _po, powiadom=True)

    def _zapytaj_o_opcjonalne_pakiety_webui(self):
        # WHY: Docker image WebUI ma ffmpeg/pandoc/zstd w środku, goły 'uv tool
        #      install' - nie; to jedyny krok instalacji WebUI wymagający roota,
        #      więc pytamy osobno i tylko za wyraźną zgodą.
        if not shutil.which("apt-get"):
            # WHY: poza Debianem/Ubuntu (np. AppImage na Fedorze) pakiety nazywają się
            #      różnie i są w różnych repozytoriach (ffmpeg na Fedorze to RPM Fusion) -
            #      zamiast zgadywać, mówimy, czego szukać w menedżerze pakietów systemu.
            QMessageBox.information(
                self, _("Opcjonalne pakiety WebUI"),
                _("Do transkrypcji audio i importu dokumentów (PDF/Word) do RAG Open WebUI potrzebuje "
                  "też programów: {pakiety}.\n\nZainstaluj je menedżerem pakietów swojego systemu. "
                  "Bez nich WebUI działa normalnie, tylko bez tych dwóch funkcji.").format(
                    pakiety=", ".join(PAKIETY_OPCJONALNE_WEBUI)))
            return
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
        self._uruchom_akcje(webui_zainstaluj_opcjonalne, _("Instalacja opcjonalnych pakietów WebUI"), powiadom=True)

    # =========================================================================
    #  Ekran "Hosty Ollama" (makieta Hosts.dc.html)
    # =========================================================================
    def _strona_hosty(self):
        # WHAT: po lewej karty hostów, po prawej szczegóły wybranego: kafelki
        #       VRAM / czas odpowiedzi / usługa, tabela modeli, ostrzeżenie offline.
        # WHY:  ta sama lista serwerów (self.serwery) co router LiteLLM i "Na host"
        #       na ekranie Modele - jedna lista, bez przepisywania w kilku miejscach.
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(20)

        naglowek = QHBoxLayout()
        naglowek.addLayout(self._tytul_strony(
            _("Hosty Ollama"), _("Komputery z Ollamą, które widzi router. Lokalny jest zarządzany przez systemd.")), 1)
        self.btn_sprawdz_hosty = self._przycisk(_("Sprawdź wszystkie"), "odswiez", slot=self.odswiez_hosty)
        naglowek.addWidget(self.btn_sprawdz_hosty, alignment=Qt.AlignmentFlag.AlignVCenter)
        naglowek.addWidget(self._przycisk(_("Dodaj host"), "plus", "primary", self.dodaj_host),
                           alignment=Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(naglowek)

        tresc = QHBoxLayout()
        tresc.setSpacing(18)
        v.addLayout(tresc, 1)

        lewa = QVBoxLayout()
        lewa.setSpacing(10)
        przewijanie = QScrollArea()
        przewijanie.setWidgetResizable(True)
        przewijanie.setFrameShape(QScrollArea.Shape.NoFrame)
        przewijanie.setFixedWidth(330)
        przewijanie.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        kontener = QWidget()
        self._lista_hostow = QVBoxLayout(kontener)
        self._lista_hostow.setContentsMargins(0, 0, 0, 0)
        self._lista_hostow.setSpacing(10)
        przewijanie.setWidget(kontener)
        lewa.addWidget(przewijanie, 1)
        podpowiedz = QLabel(_("Dodajesz zdalny host? Wpisz adres i port. Program sam sprawdzi "
                              "połączenie i listę modeli."))
        podpowiedz.setObjectName("hint")
        podpowiedz.setWordWrap(True)
        podpowiedz.setFixedWidth(330)
        lewa.addWidget(podpowiedz)
        tresc.addLayout(lewa)

        karta = QFrame()
        karta.setObjectName("card")
        uk = QVBoxLayout(karta)
        uk.setContentsMargins(24, 22, 24, 22)
        uk.setSpacing(18)
        tresc.addWidget(karta, 1)

        gora = QHBoxLayout()
        nazwy = QVBoxLayout()
        nazwy.setSpacing(4)
        self.lbl_host_nazwa = _etykieta("", "h2")
        nazwy.addWidget(self.lbl_host_nazwa)
        self.lbl_host_adres = _etykieta("", "mono", muted="true")
        self.lbl_host_adres.setWordWrap(True)
        nazwy.addWidget(self.lbl_host_adres)
        gora.addLayout(nazwy, 1)
        self.btn_host_restart = self._przycisk(_("Uruchom ponownie"), slot=self.restart_hosta)
        gora.addWidget(self.btn_host_restart, alignment=Qt.AlignmentFlag.AlignTop)
        self.btn_host_dziennik = self._przycisk(_("Dziennik"), slot=self.pokaz_dziennik_hosta)
        gora.addWidget(self.btn_host_dziennik, alignment=Qt.AlignmentFlag.AlignTop)
        self.btn_host_usun = self._przycisk("", "kosz", "danger", self.usun_host)
        self.btn_host_usun.setToolTip(_("Usuń host"))
        self.btn_host_usun.setAccessibleName(_("Usuń host"))
        gora.addWidget(self.btn_host_usun, alignment=Qt.AlignmentFlag.AlignTop)
        uk.addLayout(gora)

        kafelki = QGridLayout()
        kafelki.setSpacing(14)
        kafelek, self.lbl_host_vram, self.lbl_host_vram_opis = self._kafelek_hosta(_("Pamięć karty (VRAM)"))
        self.pasek_host_vram = QProgressBar()
        self.pasek_host_vram.setObjectName("meter")
        self.pasek_host_vram.setTextVisible(False)
        self.pasek_host_vram.setFixedHeight(6)
        kafelek.layout().addWidget(self.pasek_host_vram)
        kafelki.addWidget(kafelek, 0, 0)
        kafelek, self.lbl_host_ram, self.lbl_host_ram_opis = self._kafelek_hosta(_("Pamięć RAM"))
        self.pasek_host_ram = QProgressBar()
        self.pasek_host_ram.setObjectName("meter")
        self.pasek_host_ram.setTextVisible(False)
        self.pasek_host_ram.setFixedHeight(6)
        kafelek.layout().addWidget(self.pasek_host_ram)
        kafelki.addWidget(kafelek, 0, 1)
        # WHY: siatka 2x2 zamiast czterech w rzędzie - przy czterech kafelkach w
        #      jednym rzędzie podpisy i "ollama.service" się ucinały. Pamięć (VRAM
        #      i RAM) u góry, obok siebie - łatwo porównać.
        kafelek, self.lbl_host_ping, self.lbl_host_ping_opis = self._kafelek_hosta(_("Czas odpowiedzi"))
        kafelki.addWidget(kafelek, 1, 0)
        kafelek, self.lbl_host_usluga, self.lbl_host_usluga_opis = self._kafelek_hosta(_("Usługa"))
        self.lbl_host_usluga.setProperty("role", "monoValue")
        kafelki.addWidget(kafelek, 1, 1)
        for kolumna in range(2):
            kafelki.setColumnStretch(kolumna, 1)
        uk.addLayout(kafelki)

        uk.addWidget(_etykieta(_("Modele na tym hoście"), "h3"))
        self.tabela_host_modele = QTableWidget(0, 4)
        self.tabela_host_modele.setHorizontalHeaderLabels([_("Nazwa"), _("Rozmiar"), _("Kwantyzacja"), _("Stan")])
        naglowki = self.tabela_host_modele.horizontalHeader()
        naglowki.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        naglowki.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for kolumna in (1, 2):
            naglowki.setSectionResizeMode(kolumna, QHeaderView.ResizeMode.ResizeToContents)
        # WHY: w kolumnie "Stan" jest sama ikona (setCellWidget) - stała, wąska
        #      szerokość wystarczy na nagłówek i ikonę.
        naglowki.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.tabela_host_modele.setColumnWidth(
            3, self.tabela_host_modele.fontMetrics().horizontalAdvance(_("Stan")) + 48)
        self.tabela_host_modele.verticalHeader().setVisible(False)
        self.tabela_host_modele.verticalHeader().setDefaultSectionSize(40)
        self.tabela_host_modele.setShowGrid(False)
        self.tabela_host_modele.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela_host_modele.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tabela_host_modele.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        uk.addWidget(self.tabela_host_modele, 1)

        self.lbl_host_ostrzezenie = QLabel(_(
            "Host nie odpowiada. Sprawdź, czy komputer jest włączony i czy Ollama nasłuchuje "
            "na adresie sieciowym (OLLAMA_HOST=0.0.0.0)."))
        self.lbl_host_ostrzezenie.setObjectName("notice")
        self.lbl_host_ostrzezenie.setWordWrap(True)
        uk.addWidget(self.lbl_host_ostrzezenie)

        self._wypelnij_liste_hostow()
        return strona

    def _kafelek_hosta(self, podpis):
        # WHAT: kafelek statystyki z makiety: podpis, duża wartość, linia opisu.
        kafelek = QFrame()
        kafelek.setObjectName("statTile")
        v = QVBoxLayout(kafelek)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)
        v.addWidget(_etykieta(podpis, "muted"))
        wartosc = _etykieta("—", "value")
        v.addWidget(wartosc)
        opis = _etykieta("", "muted")
        opis.setWordWrap(True)
        v.addWidget(opis)
        return kafelek, wartosc, opis

    def _wypelnij_liste_hostow(self):
        # WHAT: karty hostów od nowa z self.serwery (po dodaniu/usunięciu hosta).
        while self._lista_hostow.count():
            element = self._lista_hostow.takeAt(0)
            if element.widget():
                element.widget().deleteLater()
        self._karty_hostow = {}
        adresy = [s["adres"] for s in self.serwery]
        if self._host_wybrany not in adresy:
            self._host_wybrany = adresy[0]
        for serwer in self.serwery:
            karta = KartaHosta(serwer)
            karta.kliknieto.connect(self._wybierz_host)
            if serwer["adres"] in self._hosty_stan:
                karta.ustaw_stan(self._hosty_stan[serwer["adres"]])
            karta.ustaw_wybrana(serwer["adres"] == self._host_wybrany)
            self._karty_hostow[serwer["adres"]] = karta
            self._lista_hostow.addWidget(karta)
        self._lista_hostow.addStretch(1)
        self._pokaz_szczegoly_hosta()

    def _wybierz_host(self, adres):
        self._host_wybrany = adres
        for adres_karty, karta in self._karty_hostow.items():
            karta.ustaw_wybrana(adres_karty == adres)
        self._pokaz_szczegoly_hosta()

    def pokaz_host(self, adres):
        # WHAT: "Pokaż host" z dziennika - przejście do hosta o tym adresie.
        self._przejdz("hosty")
        self._wybierz_host(adres)

    def host_z_tekstu(self, tekst):
        # WHAT: adres hosta z listy, o którym mówi wpis dziennika (np. "192.168.0.250:11434").
        for serwer in self.serwery:
            url = urlparse(serwer["adres"])
            if url.hostname and url.hostname not in ("localhost", "127.0.0.1") and url.hostname in tekst:
                return serwer["adres"]
        return None

    def _pokaz_szczegoly_hosta(self):
        # WHAT: wypełnia prawą kartę danymi wybranego hosta (z ostatniego sprawdzenia).
        serwer = next((s for s in self.serwery if s["adres"] == self._host_wybrany), self.serwery[0])
        adres = serwer["adres"]
        stan = self._hosty_stan.get(adres)
        lokalny = _adres_lokalny(adres)
        self.lbl_host_nazwa.setText(serwer["nazwa"])
        # WHY: adres bez "http://", jak w makiecie - krócej, a i tak jednoznacznie
        adres_krotki = urlparse(adres).netloc or adres
        wersja = stan["wersja"] if stan and stan["wersja"] else "?"
        self.lbl_host_adres.setText(f"{adres_krotki} · Ollama {wersja}")

        self.btn_host_restart.setEnabled(lokalny)
        self.btn_host_dziennik.setEnabled(lokalny)
        # WHY: systemctl/journalctl działają tylko na tym komputerze - zdalnej
        #      usługi nie da się stąd uruchomić ponownie (patrz CLAUDE.md,
        #      "Sterowanie usługą zawsze dotyczy LOKALNEGO systemd").
        podpowiedz = "" if lokalny else _("Dostępne tylko dla tego komputera - zdalną usługą steruje się na tamtym komputerze.")
        self.btn_host_restart.setToolTip(podpowiedz)
        self.btn_host_dziennik.setToolTip(podpowiedz)
        # WHY: ten komputer zostaje zawsze - jego usługą Ollama program steruje (start/stop,
        #      ustawienia, dziennik), a router i czat liczą na lokalny host; kosz tylko przy zdalnych
        self.btn_host_usun.setVisible(not lokalny)
        self.btn_host_usun.setEnabled(len(self.serwery) > 1)

        gib = 1024 ** 3
        online = bool(stan and stan["online"])
        if not stan:
            self.lbl_host_vram.setText("—")
            self.lbl_host_vram_opis.setText(_("sprawdzam..."))
            self.pasek_host_vram.hide()
        elif not online:
            self.lbl_host_vram.setText(_("brak danych"))
            self.lbl_host_vram_opis.setText("")
            self.pasek_host_vram.hide()
        elif stan["vram_calkowite"]:
            uzyte, calkowite = stan["vram_uzyte"] / gib, stan["vram_calkowite"] / gib
            procent = round(100 * stan["vram_uzyte"] / stan["vram_calkowite"])
            self.lbl_host_vram.setText(f"{uzyte:.1f} / {calkowite:.1f} GB")
            self.lbl_host_vram_opis.setText(_("zajęte przez modele: {procent}%").format(procent=procent))
            self.pasek_host_vram.setValue(min(100, procent))
            self.pasek_host_vram.setProperty("state", "error" if procent > 80 else "")
            _odswiez_styl(self.pasek_host_vram)
            self.pasek_host_vram.show()
        else:
            self.lbl_host_vram.setText(f"{stan['vram_uzyte'] / gib:.1f} GB")
            # WHY: API Ollamy nie podaje, ile pamięci ma karta zdalnego hosta
            self.lbl_host_vram_opis.setText(_("zajęte przez modele (rozmiaru karty zdalnego hosta API nie podaje)"))
            self.pasek_host_vram.hide()

        if not stan:
            self.lbl_host_ram.setText("—")
            self.lbl_host_ram_opis.setText(_("sprawdzam..."))
            self.pasek_host_ram.hide()
        elif not online:
            self.lbl_host_ram.setText(_("brak danych"))
            self.lbl_host_ram_opis.setText("")
            self.pasek_host_ram.hide()
        else:
            uzyte = stan["ram_uzyte"] / gib
            procesy = stan["ram_procesy"]
            if stan["ram_calkowite"]:
                procent = round(100 * stan["ram_uzyte"] / stan["ram_calkowite"])
                self.lbl_host_ram.setText(f"{uzyte:.1f} / {stan['ram_calkowite'] / gib:.0f} GB")
                self.pasek_host_ram.setValue(min(100, procent))
                self.pasek_host_ram.setProperty("state", "error" if procent > 80 else "")
                _odswiez_styl(self.pasek_host_ram)
                self.pasek_host_ram.show()
            else:
                self.lbl_host_ram.setText(f"{uzyte:.1f} GB")
                self.pasek_host_ram.hide()
            if procesy:
                # WHY: krótko na karcie (prośba użytkownika) - Ollama (procesy + pliki modeli)
                #      i APU (GTT, tylko gdy jest); dokładny podział w dymku
                ollama = (procesy["anon"] + procesy["pliki"]) / gib
                if procesy.get("gtt"):
                    self.lbl_host_ram_opis.setText(_("Ollama {o:.1f} GB + APU {g:.1f} GB").format(
                        o=ollama, g=procesy["gtt"] / gib))
                else:
                    self.lbl_host_ram_opis.setText(_("Ollama {o:.1f} GB").format(o=ollama))
                dymek = _("Ollama: procesy {a:.1f} GB + pliki modeli {p:.1f} GB").format(
                    a=procesy["anon"] / gib, p=procesy["pliki"] / gib)
                if procesy.get("gtt"):
                    dymek += "\n" + _(
                        "APU: {g:.1f} GB zwykłej pamięci RAM używanej przez kartę graficzną (GTT) - tam trafia "
                        "część modelu, która nie mieści się w wydzielonym VRAM. Liczona dla całej karty, więc "
                        "obejmuje też trochę pamięci pulpitu.").format(g=procesy["gtt"] / gib)
                self.lbl_host_ram_opis.setToolTip(dymek)
            else:
                # WHY: zdalnego hosta nie zmierzymy z tego komputera - tylko API
                self.lbl_host_ram_opis.setText(_("część modeli poza VRAM (wg API)"))
                self.lbl_host_ram_opis.setToolTip("")

        if stan and stan["ping_ms"] is not None:
            self.lbl_host_ping.setText(f"{stan['ping_ms']:.0f} ms")
        else:
            self.lbl_host_ping.setText(_("brak") if stan else "—")
        self.lbl_host_ping_opis.setText(
            _("sprawdzono o {godzina}").format(godzina=time.strftime("%H:%M:%S", time.localtime(stan["sprawdzono"])))
            if stan else "")

        if lokalny:
            self.lbl_host_usluga.setText(f"{SERVICE_NAME}.service")
            if stan and stan["usluga_aktywna"] is not None:
                czesci = [_("systemd"),
                          _("działa") if stan["usluga_aktywna"] else _("zatrzymana"),
                          _("start z systemem") if stan["usluga_autostart"] else _("bez autostartu")]
                self.lbl_host_usluga_opis.setText(", ".join(czesci))
            else:
                self.lbl_host_usluga_opis.setText(_("systemd"))
        else:
            self.lbl_host_usluga.setText(_("zdalny"))
            self.lbl_host_usluga_opis.setText(_("tylko odczyt stanu przez API"))

        modele = stan["modele"] if online else []
        self.tabela_host_modele.setRowCount(len(modele))
        mono = QFont("JetBrains Mono")
        for wiersz, m in enumerate(modele):
            nazwa = QTableWidgetItem(m["nazwa"])
            nazwa.setFont(mono)
            nazwa.setToolTip(m["nazwa"])
            self.tabela_host_modele.setItem(wiersz, 0, nazwa)
            rozmiar = m["rozmiar"]
            tekst = f"{rozmiar / gib:.1f} GB" if rozmiar >= gib else f"{rozmiar / 1024 ** 2:.0f} MB"
            self.tabela_host_modele.setItem(wiersz, 1, QTableWidgetItem(tekst))
            # WHY: modele z HF potrafią mieć w API "unknown" - to nic nie mówi
            kwant = QTableWidgetItem(m["kwantyzacja"] if m["kwantyzacja"] not in ("", "unknown") else "—")
            kwant.setFont(mono)
            self.tabela_host_modele.setItem(wiersz, 2, kwant)
            # WHAT: ikona zamiast napisu - zielona kość RAM = wczytany do pamięci,
            #       szary dysk = tylko na dysku (prośba użytkownika: "żeby było
            #       widać, że wczytane"). Słowny opis w dymku i dla czytnika ekranu.
            opis = (_("w pamięci: {vram:.1f} GB VRAM + {ram:.1f} GB RAM").format(
                vram=m["vram"] / gib, ram=m["ram"] / gib) if m["w_pamieci"] else _("na dysku"))
            lbl = QLabel()
            lbl.setPixmap(_pixmapa_svg("pamiec" if m["w_pamieci"] else "dysk",
                                       PALETA.ok if m["w_pamieci"] else PALETA.wyciszony, 18))
            lbl.setToolTip(opis)
            lbl.setAccessibleName(opis)
            self.tabela_host_modele.setCellWidget(wiersz, 3, lbl)
        self.lbl_host_ostrzezenie.setVisible(bool(stan) and not online)
        _odmaluj_rodzica(self.lbl_host_vram, self.lbl_host_ram, self.lbl_host_ping, self.lbl_host_usluga)

    def odswiez_hosty(self):
        self.btn_sprawdz_hosty.setEnabled(False)
        worker = HostyWorker(self.serwery)
        if not self._uruchom_watek("hosty", worker, worker.wynik, self._po_sprawdzeniu_hostow):
            self.btn_sprawdz_hosty.setEnabled(True)

    def _po_sprawdzeniu_hostow(self, wyniki):
        self.btn_sprawdz_hosty.setEnabled(True)
        for stan in wyniki:
            poprzedni = self._hosty_stan.get(stan["adres"])
            # WHY: zmiana online/offline trafia do dziennika - widać, kiedy host znikał
            if poprzedni is not None and poprzedni["online"] != stan["online"]:
                if stan["online"]:
                    self.wpis_log(_("Host {nazwa} znów odpowiada").format(nazwa=stan["nazwa"]), "INFO", "Ollama")
                else:
                    tekst = _("Host {nazwa} ({adres}) nie odpowiada").format(
                        nazwa=stan["nazwa"], adres=urlparse(stan["adres"]).netloc)
                    self._dodaj_wpisy([{"czas": time.time(), "usluga": "Ollama", "poziom": "BŁĄD", "tekst": tekst,
                                        "pomoc": wyjasnij_blad("connection refused")}])
            self._hosty_stan[stan["adres"]] = stan
            karta = self._karty_hostow.get(stan["adres"])
            if karta:
                karta.ustaw_stan(stan)
        self._pokaz_szczegoly_hosta()
        self._odswiez_panel_hostow()
        self._odswiez_schemat()
        self._odswiez_zasobnik()

    def dodaj_host(self):
        dialog = DialogDodajHost(self.serwery, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.serwer:
            return
        self.serwery.append(dialog.serwer)
        self._host_wybrany = dialog.serwer["adres"]
        self.wpis_log(_("Dodano host: {nazwa} ({adres})").format(**dialog.serwer))
        self._po_zmianie_listy_serwerow()

    def usun_host(self):
        serwer = next((s for s in self.serwery if s["adres"] == self._host_wybrany), None)
        if not serwer or _adres_lokalny(serwer["adres"]):
            return  # WHY: lokalnego hosta nie usuwamy (przycisk jest wtedy schowany)
        # WHY: router i ekran Modele zawsze potrzebują przynajmniej jednego hosta.
        if len(self.serwery) <= 1:
            QMessageBox.warning(self, _("Nie można usunąć"), _("Musi zostać co najmniej jeden serwer."))
            return
        odpowiedz = QMessageBox.question(
            self, _("Usuń host"),
            _("Usunąć host {nazwa} ({adres}) z listy? Modele na nim zostaną - znika tylko wpis "
              "w tym programie i w agregatorze LiteLLM (po ponownym uruchomieniu).").format(**serwer))
        if odpowiedz != QMessageBox.StandardButton.Yes:
            return
        self.serwery.remove(serwer)
        self._hosty_stan.pop(serwer["adres"], None)
        self.wpis_log(_("Usunięto host: {nazwa} ({adres})").format(**serwer))
        self._po_zmianie_listy_serwerow()

    def _po_zmianie_listy_serwerow(self):
        # WHAT: zapis listy + odświeżenie wszystkiego, co z niej korzysta.
        _zapisz_serwery(self.serwery)
        self._wypelnij_liste_hostow()
        self._wypelnij_combo_hostow()
        self._odswiez_panel_hostow()
        self._oznacz_zmiany_routera()
        self.odswiez_hosty()
        self.odswiez_modele()

    def restart_hosta(self):
        self.wpis_log(_("Ponowne uruchomienie Ollamy..."))
        self._uruchom_akcje(usluga_restart, _("Ponowne uruchomienie Ollamy"))
        # WHY: usługa wstaje kilka sekund - sprawdzamy hosty dopiero po chwili
        QTimer.singleShot(4000, self.odswiez_hosty)

    def pokaz_dziennik_hosta(self):
        # WHAT: jak link "Dziennik" w makiecie - ekran Dziennik z filtrem "Ollama".
        self._filtr_dziennika = "Ollama"
        self._przejdz("dziennik")
        self._zaznacz_filtr_dziennika()

    # =========================================================================
    #  Ekran "Router LiteLLM" (makieta Router.dc.html)
    # =========================================================================
    def _strona_router(self):
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(18)

        naglowek = QHBoxLayout()
        naglowek.setSpacing(10)
        naglowek.addLayout(self._tytul_strony(
            _("Router LiteLLM"), _("Jeden adres dla wszystkich programów. Router sam wybiera host i model.")), 1)
        self.pill_router = self._pill()
        naglowek.addWidget(self.pill_router, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.btn_router_start = self._przycisk(_("Uruchom"), "odswiez", slot=self.uruchom_litellm)
        naglowek.addWidget(self.btn_router_start, alignment=Qt.AlignmentFlag.AlignVCenter)
        # WHAT: gotowe ustawienia dla programów łączących się z routerem - w menu.
        # WHY:  w nagłówku, nie w karcie strategii: tam brakowało wysokości i w
        #       dłuższych tłumaczeniach ramki strategii nachodziły na siebie.
        btn_programy = self._przycisk(_("Połącz program"), "kopiuj")
        menu_programow = QMenu(btn_programy)
        menu_programow.addAction(_("Config dla Continue..."), self.generuj_config_continue)
        menu_programow.addAction(_("Połączenie z Open WebUI..."), lambda: DialogPolaczenieWebUI(self).exec())
        btn_programy.setMenu(menu_programow)
        naglowek.addWidget(btn_programy, alignment=Qt.AlignmentFlag.AlignVCenter)
        naglowek.addWidget(self._przycisk(_("Dodaj alias"), "plus", "primary", self.dodaj_alias),
                           alignment=Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(naglowek)

        # WHAT: pasek "zmiany czekają" - trasy działają dopiero po restarcie routera.
        self.ramka_zmian = QFrame()
        self.ramka_zmian.setObjectName("noticeBar")
        h = QHBoxLayout(self.ramka_zmian)
        h.setContentsMargins(14, 8, 8, 8)
        h.addWidget(QLabel(_("Zmiany tras zadziałają po ponownym uruchomieniu routera.")), 1)
        h.addWidget(self._przycisk(_("Uruchom ponownie router"), "odswiez", slot=self.uruchom_litellm))
        v.addWidget(self.ramka_zmian)
        self.ramka_zmian.setVisible(self._router_zmiany)

        karta_schemat, uk = self._karta()
        uk.setContentsMargins(24, 18, 24, 18)
        self.schemat = SchematRoutera()
        uk.addWidget(self.schemat)
        v.addWidget(karta_schemat)

        dol = QHBoxLayout()
        dol.setSpacing(18)
        v.addLayout(dol, 1)

        # --- Aliasy / wszystkie modele ---
        karta, uk = self._karta()
        gora = QHBoxLayout()
        self.lbl_router_tabela = _etykieta(_("Aliasy modeli"), "h2")
        gora.addWidget(self.lbl_router_tabela, 1)
        self.chip_aliasy = self._chip(_("Aliasy"), lambda: self._pokaz_tabele_routera(0))
        self.chip_wszystkie_modele = self._chip(_("Wszystkie modele"), lambda: self._pokaz_tabele_routera(1))
        grupa = QButtonGroup(karta)
        for chip in (self.chip_aliasy, self.chip_wszystkie_modele):
            grupa.addButton(chip)
            gora.addWidget(chip)
        self.chip_aliasy.setChecked(True)
        uk.addLayout(gora)

        self.stos_routera = QStackedWidget()
        self.tabela_aliasow = QTableWidget(0, 4)
        self.tabela_aliasow.setHorizontalHeaderLabels([_("Alias"), _("Model"), _("Hosty"), _("Akcje")])
        nagl = self.tabela_aliasow.horizontalHeader()
        nagl.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for kolumna, tryb in ((0, QHeaderView.ResizeMode.Stretch), (1, QHeaderView.ResizeMode.Stretch),
                              (2, QHeaderView.ResizeMode.Stretch), (3, QHeaderView.ResizeMode.Fixed)):
            nagl.setSectionResizeMode(kolumna, tryb)
        self.tabela_aliasow.setColumnWidth(3, 160)
        self.tabela_aliasow.verticalHeader().setVisible(False)
        self.tabela_aliasow.verticalHeader().setDefaultSectionSize(46)
        self.tabela_aliasow.setShowGrid(False)
        self.tabela_aliasow.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela_aliasow.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tabela_aliasow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabela_aliasow.setWordWrap(False)
        self.tabela_aliasow.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.stos_routera.addWidget(self.tabela_aliasow)

        strona_modeli = QWidget()
        vm = QVBoxLayout(strona_modeli)
        vm.setContentsMargins(0, 0, 0, 0)
        pasek = QHBoxLayout()
        opis = _etykieta(_("Odznacz modele, których router ma nie udostępniać pod ich własną nazwą "
                           "(aliasy działają dalej)."), "muted")
        opis.setWordWrap(True)
        pasek.addWidget(opis, 1)
        self.btn_odswiez_agregator = self._przycisk(_("Odśwież listę"), "odswiez", slot=self.odswiez_liste_agregatora)
        pasek.addWidget(self.btn_odswiez_agregator)
        vm.addLayout(pasek)
        # WHAT: tabela (Model | Host | Adres) z polem wyboru przy modelu.
        # WHY:  zwykła lista zlewała się w jeden blok tekstu (zgłoszenie użytkownika) -
        #       wiersze z liniami i nagłówki jak w pozostałych tabelach programu.
        self.lista_agregator = QTableWidget(0, 3)
        self.lista_agregator.setHorizontalHeaderLabels([_("Model"), _("Host"), _("Adres")])
        nagl = self.lista_agregator.horizontalHeader()
        nagl.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        nagl.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for kolumna in (1, 2):
            nagl.setSectionResizeMode(kolumna, QHeaderView.ResizeMode.ResizeToContents)
        self.lista_agregator.verticalHeader().setVisible(False)
        self.lista_agregator.verticalHeader().setDefaultSectionSize(40)
        self.lista_agregator.setShowGrid(False)
        self.lista_agregator.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lista_agregator.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.lista_agregator.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lista_agregator.setWordWrap(False)
        self.lista_agregator.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.lista_agregator.itemChanged.connect(self._zmiana_modelu_agregatora)
        vm.addWidget(self.lista_agregator, 1)
        self.stos_routera.addWidget(strona_modeli)
        uk.addWidget(self.stos_routera, 1)

        stopka = QHBoxLayout()
        stopka.setSpacing(10)
        self.sw_klucz = Przelacznik(_("Wymagaj klucza API (hasła)"))
        self.sw_klucz.setChecked(_litellm_klucz() is not None)
        self.sw_klucz.clicked.connect(self.przelacz_litellm_klucz)
        stopka.addWidget(self.sw_klucz)
        lbl = QLabel(_("Klucz API"))
        lbl.setToolTip(_("Bez klucza każdy w Twojej sieci może korzystać z modeli przez LiteLLM. "
                         "Z kluczem - tylko klienci, którzy go podadzą (config Continue "
                         "dostanie go automatycznie)."))
        stopka.addWidget(lbl)
        self.lbl_litellm_klucz = _etykieta("", "mono", muted="true")
        self.lbl_litellm_klucz.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        stopka.addWidget(self.lbl_litellm_klucz, 1)
        self.btn_kopiuj_klucz = self._przycisk("", "kopiuj",
                                               slot=lambda: QApplication.clipboard().setText(_litellm_klucz() or ""))
        self.btn_kopiuj_klucz.setToolTip(_("Kopiuj klucz"))
        stopka.addWidget(self.btn_kopiuj_klucz)
        uk.addLayout(stopka)
        self._pokaz_klucz_litellm()
        dol.addWidget(karta, 1)

        # --- Strategia + adres ---
        karta, uk = self._karta()
        karta.setFixedWidth(330)
        # WHY: tytuł i opisy strategii zawijają się (zwłaszcza w tłumaczeniach) -
        #      karta musi dostać ich pełną wysokość, inaczej ramki nachodziły na siebie.
        self._pilnuj_wysokosci_strategii = PilnujWysokosci(karta)
        karta.installEventFilter(self._pilnuj_wysokosci_strategii)
        uk.setSpacing(14)
        tytul = _etykieta(_("Gdy model jest na kilku hostach"), "h2")
        tytul.setWordWrap(True)
        uk.addWidget(tytul)
        self._karty_strategii = []
        wybory = QVBoxLayout()
        wybory.setSpacing(6)
        for klucz, nazwa, opis in (
            ("simple-shuffle", _("Na zmianę"), _("rozkłada zapytania między hosty")),
            ("least-busy", _("Najmniej zajęty"), _("wybiera host z najmniejszą kolejką")),
            ("latency-based-routing", _("Najszybszy"), _("host, który odpowiada najszybciej")),
        ):
            wybor = KartaWyboru(klucz, nazwa, opis)
            wybor.kliknieto.connect(self.wybierz_strategie)
            wybory.addWidget(wybor)
            self._karty_strategii.append(wybor)
        uk.addLayout(wybory)
        uk.addStretch(1)
        uk.addWidget(_etykieta(_("Adres dla programów"), "muted"))
        adres = QHBoxLayout()
        adres.setSpacing(6)
        pole = QLineEdit(f"{LITELLM_URL}/v1")
        pole.setReadOnly(True)
        pole.setObjectName("monoField")
        adres.addWidget(pole, 1)
        btn = self._przycisk("", "kopiuj", slot=lambda: QApplication.clipboard().setText(f"{LITELLM_URL}/v1"))
        btn.setToolTip(_("Kopiuj adres"))
        btn.setAccessibleName(_("Kopiuj adres"))
        adres.addWidget(btn)
        uk.addLayout(adres)
        dol.addWidget(karta)

        self._wypelnij_aliasy()
        self._zaznacz_strategie()
        return strona

    def _chip(self, tekst, slot):
        # WHAT: przycisk-"pigułka" z makiet (filtry, zakładki) - zaznaczony jest niebieski.
        chip = QPushButton(tekst)
        chip.setObjectName("chip")
        chip.setCheckable(True)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.clicked.connect(slot)
        return chip

    def _pokaz_tabele_routera(self, indeks):
        self.stos_routera.setCurrentIndex(indeks)
        self.lbl_router_tabela.setText(_("Aliasy modeli") if indeks == 0 else _("Modele w routerze"))
        if indeks == 1:
            self.odswiez_liste_agregatora()

    def _odswiez_router_stan(self, stan):
        port = urlparse(LITELLM_URL).port or 4000
        if not stan["litellm"]:
            self._ustaw_pill(self.pill_router, _("Nie zainstalowany"), "warn")
            self.btn_router_start.setText(_("Zainstaluj LiteLLM"))
            self.btn_router_start.clicked.disconnect()
            self.btn_router_start.clicked.connect(self.zainstaluj_litellm)
        else:
            if stan["litellm_dziala"]:
                self._ustaw_pill(self.pill_router, _("Działa · port {port}").format(port=port), "ok")
            else:
                self._ustaw_pill(self.pill_router, _("Zatrzymany"), "stopped")
            self.btn_router_start.setText(_("Uruchom ponownie") if stan["litellm_dziala"] else _("Uruchom"))
            self.btn_router_start.clicked.disconnect()
            self.btn_router_start.clicked.connect(self.uruchom_litellm)
        self.btn_router_start.setEnabled(not self._w_toku("litellm"))

    def _oznacz_zmiany_routera(self):
        # WHY: zmiany tras trafiają do configu przy starcie - jeśli router działa,
        #      trzeba mu powiedzieć, że czeka na restart (bez samoczynnego przestoju).
        if self._stan and self._stan["litellm_dziala"]:
            self._router_zmiany = True
        self._pokaz_zmiany_routera()
        self._wypelnij_aliasy()
        self._zaznacz_strategie()
        self._odswiez_panel_routera()
        self._odswiez_schemat()

    def _pokaz_zmiany_routera(self):
        self.ramka_zmian.setVisible(self._router_zmiany)

    def _wypelnij_aliasy(self):
        aliasy = _litellm_aliasy()
        nazwy_hostow = {s["adres"]: s["nazwa"] for s in self.serwery}
        self.tabela_aliasow.setRowCount(len(aliasy))
        mono = QFont("JetBrains Mono")
        for wiersz, a in enumerate(aliasy):
            for kolumna, tekst in enumerate((a["alias"], a["model"])):
                pozycja = QTableWidgetItem(tekst)
                pozycja.setFont(mono)
                pozycja.setToolTip(tekst)
                self.tabela_aliasow.setItem(wiersz, kolumna, pozycja)
            hosty = [nazwy_hostow.get(h, h) for h in a.get("hosty", [])]
            self.tabela_aliasow.setItem(wiersz, 2, QTableWidgetItem(", ".join(hosty) or _("wszystkie z tym modelem")))
            akcje = QWidget()
            h = QHBoxLayout(akcje)
            h.setContentsMargins(0, 0, 8, 0)
            h.setSpacing(6)
            edytuj = QPushButton(_("Edytuj"))
            edytuj.setObjectName("small")
            edytuj.clicked.connect(lambda _c=False, i=wiersz: self.edytuj_alias(i))
            edytuj.setAccessibleName(_("Edytuj alias {alias}").format(alias=a["alias"]))
            h.addWidget(edytuj)
            usun = QPushButton(_("Usuń"))
            usun.setObjectName("smallDanger")
            usun.clicked.connect(lambda _c=False, i=wiersz: self.usun_alias(i))
            h.addWidget(usun)
            self.tabela_aliasow.setCellWidget(wiersz, 3, akcje)
        if not aliasy:
            self.tabela_aliasow.setRowCount(1)
            pusty = QTableWidgetItem(_("Brak aliasów. \"Dodaj alias\" daje stałą nazwę (np. czat) "
                                       "dla modelu na wybranych hostach."))
            self.tabela_aliasow.setItem(0, 0, pusty)
            self.tabela_aliasow.setSpan(0, 0, 1, 4)
        else:
            self.tabela_aliasow.clearSpans()

    def _odswiez_schemat(self):
        # WHAT: schemat tras z prawdziwych danych - aliasy (albo jeden węzeł
        #       "modele pod własnymi nazwami") i hosty z listy serwerów.
        if not hasattr(self, "schemat"):
            return
        hosty = []
        for serwer in self.serwery[:5]:
            stan = self._hosty_stan.get(serwer["adres"])
            hosty.append((urlparse(serwer["adres"]).netloc, serwer["nazwa"], bool(stan and stan["online"])))
        indeks = {s["adres"]: i for i, s in enumerate(self.serwery[:5])}
        modele = {m["nazwa"]: m for m in self._modele}
        aliasy = []
        for a in _litellm_aliasy()[:5]:
            adresy = a.get("hosty") or [h["adres"] for h in modele.get(a["model"], {}).get("hosty", [])]
            cele = [indeks[x] for x in adresy if x in indeks]
            opis = _skroc_nazwe_modelu_hf(a["model"])
            if len(cele) > 1:
                opis += " · " + _("{n} hosty").format(n=len(cele))
            aliasy.append((a["alias"], opis, cele))
        if not aliasy:
            liczba = len(self._modele)
            wszystkie = [i for i, h in enumerate(hosty) if h[2]]
            aliasy.append((_("modele"), _("{n} pod własnymi nazwami").format(n=liczba), wszystkie))
        self.schemat.ustaw(
            [_("Open WebUI"), _("Continue.dev (VS Code)"), _("Własne skrypty")],
            ("LiteLLM", f":{urlparse(LITELLM_URL).port or 4000}/v1"), aliasy, hosty)

    def _zaznacz_strategie(self):
        obecna = _litellm_strategia()
        for karta in self._karty_strategii:
            karta.ustaw_wybrana(karta.klucz == obecna)

    def wybierz_strategie(self, klucz):
        if klucz == _litellm_strategia():
            return
        _ustaw_litellm_strategie(klucz)
        self.wpis_log(_("Nowa strategia routera zapisana - zadziała po ponownym uruchomieniu LiteLLM."))
        self._oznacz_zmiany_routera()

    def dodaj_alias(self):
        dialog = DialogAlias(self._modele, [a["alias"] for a in _litellm_aliasy()], parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.wynik:
            _zapisz_litellm_aliasy(_litellm_aliasy() + [dialog.wynik])
            self.wpis_log(_("Dodano alias {alias} -> {model}").format(**dialog.wynik))
            self._oznacz_zmiany_routera()

    def edytuj_alias(self, indeks):
        aliasy = _litellm_aliasy()
        dialog = DialogAlias(self._modele, [a["alias"] for a in aliasy], aliasy[indeks], self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.wynik:
            aliasy[indeks] = dialog.wynik
            _zapisz_litellm_aliasy(aliasy)
            self.wpis_log(_("Zmieniono alias {alias} -> {model}").format(**dialog.wynik))
            self._oznacz_zmiany_routera()

    def usun_alias(self, indeks):
        aliasy = _litellm_aliasy()
        alias = aliasy.pop(indeks)
        odp = QMessageBox.question(self, _("Usuń alias"), _("Usunąć alias {alias}?").format(alias=alias["alias"]))
        if odp != QMessageBox.StandardButton.Yes:
            return
        _zapisz_litellm_aliasy(aliasy)
        self.wpis_log(_("Usunięto alias {alias}").format(alias=alias["alias"]))
        self._oznacz_zmiany_routera()

    def _pokaz_klucz_litellm(self):
        klucz = _litellm_klucz()
        self.lbl_litellm_klucz.setText(klucz or _("wyłączony - router dostępny dla całej sieci"))
        self.btn_kopiuj_klucz.setVisible(klucz is not None)

    def przelacz_litellm_klucz(self, wlacz):
        # WHY: tak jak przy trasach - bez samoczynnego restartu routera.
        _ustaw_litellm_wymagaj_klucza(wlacz)
        self._pokaz_klucz_litellm()
        if wlacz:
            self.wpis_log(_(
                "Klucz API LiteLLM włączony. Zadziała po ponownym uruchomieniu LiteLLM - "
                "potem klienci muszą podać klucz (wygeneruj na nowo config Continue)."
            ))
        else:
            self.wpis_log(_(
                "Klucz API LiteLLM wyłączony. Zadziała po ponownym uruchomieniu LiteLLM - "
                "potem każdy w sieci może korzystać z modeli bez hasła."
            ))
        self._oznacz_zmiany_routera()

    def odswiez_liste_agregatora(self):
        # WHAT: modele i hosty, które trafią do configu - z polem wyboru przy każdym.
        # WHY:  na żądanie (wejście na ekran / przycisk), nie co 10 s - nie odpytujemy
        #       wszystkich hostów w tle, gdy nikt nie patrzy na tę listę.
        self.btn_odswiez_agregator.setEnabled(False)
        worker = AgregatorWorker(self.serwery)

        def _po_sprawdzeniu(wpisy):
            self.btn_odswiez_agregator.setEnabled(True)
            tabela = self.lista_agregator
            tabela.blockSignals(True)
            tabela.clearSpans()
            tabela.setRowCount(max(1, len(wpisy)))
            if not wpisy:
                tabela.setItem(0, 0, QTableWidgetItem(_("Brak dostępnych modeli (hosty nieosiągalne albo puste).")))
                tabela.setSpan(0, 0, 1, 3)
            wylaczone = _litellm_wylaczone()
            mono = QFont("JetBrains Mono")
            for wiersz, (nazwa_hosta, model, adres) in enumerate(wpisy):
                item = QTableWidgetItem(model)
                item.setFont(mono)
                item.setToolTip(model)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Unchecked if f"{adres}|{model}" in wylaczone else Qt.CheckState.Checked)
                # WHY: dane do zapisu wyboru tylko w kolumnie z polem wyboru -
                #      _zmiana_modelu_agregatora pomija komórki bez nich
                item.setData(Qt.ItemDataRole.UserRole, (adres, model))
                tabela.setItem(wiersz, 0, item)
                tabela.setItem(wiersz, 1, QTableWidgetItem(nazwa_hosta))
                pozycja_adresu = QTableWidgetItem(urlparse(adres).netloc or adres)
                pozycja_adresu.setFont(mono)
                tabela.setItem(wiersz, 2, pozycja_adresu)
            tabela.blockSignals(False)

        if not self._uruchom_watek("agregator", worker, worker.wynik, _po_sprawdzeniu):
            self.btn_odswiez_agregator.setEnabled(True)

    def _zmiana_modelu_agregatora(self, item):
        # WHY: zapis od razu (QSettings), config bierze go przy następnym starcie.
        dane = item.data(Qt.ItemDataRole.UserRole)
        if not dane:
            return
        adres, model = dane
        wylacz = item.checkState() != Qt.CheckState.Checked
        _ustaw_litellm_model_wylaczony(adres, model, wylacz)
        szablon = (_("LiteLLM nie będzie serwować: {model} ({adres})") if wylacz
                   else _("LiteLLM będzie serwować: {model} ({adres})"))
        self.wpis_log(szablon.format(model=model, adres=adres) + " - "
                      + _("zadziała po ponownym uruchomieniu LiteLLM."))
        self._oznacz_zmiany_routera()

    def generuj_config_continue(self):
        # WHAT: gotowy config.yaml dla Continue.dev - tylko do skopiowania (DialogConfigContinue).
        # WHY:  aliasy pierwsze - to stałe nazwy, które przeżyją zmianę modelu za aliasem.
        modele = [a["alias"] for a in _litellm_aliasy()]
        wylaczone = _litellm_wylaczone()
        for m in self._modele:
            if any(f"{h['adres']}|{m['nazwa']}" not in wylaczone for h in m["hosty"]) and m["nazwa"] not in modele:
                modele.append(m["nazwa"])
        if not modele:
            QMessageBox.information(
                self, _("Brak modeli"),
                _("Brak dostępnych modeli (hosty nieosiągalne albo puste) - "
                  "sprawdź ekran \"Hosty Ollama\" i spróbuj ponownie."))
            return
        DialogConfigContinue(_zbuduj_config_continue(modele), self).exec()

    # =========================================================================
    #  Ekran "Modele" (makieta Models.dc.html + wyszukiwarka Ollama/HF)
    # =========================================================================
    def nazwa_rodzaju(self, rodzaj):
        return {"czat": _("czat"), "kod": _("kod"), "embedding": _("embedding"), "wizja": _("wizja")}.get(rodzaj, rodzaj)

    def _strona_modele(self):
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(18)
        v.addLayout(self._tytul_strony(
            _("Modele"), _("Pobieraj, usuwaj i przenoś modele między hostami. Router widzi zmiany od razu.")))

        # --- Pobieranie: pole, host, szukaj/pobierz ---
        karta, uk = self._karta()
        uk.setContentsMargins(20, 16, 20, 16)
        uk.setSpacing(14)
        wiersz = QHBoxLayout()
        wiersz.setSpacing(10)
        kol = QVBoxLayout()
        kol.setSpacing(6)
        kol.addWidget(_etykieta(_("Nazwa modelu z biblioteki Ollama albo Hugging Face"), "muted"))
        self.pole_szukaj_model = QLineEdit()
        self.pole_szukaj_model.setObjectName("monoField")
        self.pole_szukaj_model.setPlaceholderText(_("np. gemma3:12b albo qwen2.5-coder"))
        self.pole_szukaj_model.returnPressed.connect(self._enter_w_polu_modelu)
        self.pole_szukaj_model.textChanged.connect(self._aktualizuj_przycisk_pobierz)
        kol.addWidget(self.pole_szukaj_model)
        wiersz.addLayout(kol, 1)
        kol = QVBoxLayout()
        kol.setSpacing(6)
        kol.addWidget(_etykieta(_("Na host"), "muted"))
        self.combo_host_pobierz = QComboBox()
        self.combo_host_pobierz.setFixedWidth(200)
        kol.addWidget(self.combo_host_pobierz)
        wiersz.addLayout(kol)
        self.chk_moe = QCheckBox(_("Tylko modele MoE"))
        self.chk_moe.setChecked(self._tylko_moe)
        self.chk_moe.setToolTip(_(
            "MoE (mixture of experts) to modele, które przy każdym zapytaniu liczą tylko część "
            "parametrów - działają szybciej na APU i komputerach bez mocnej karty graficznej.\n"
            "Rozpoznawane po opisie, nazwie i tagach - część modeli MoE może zostać pominięta."))
        self.chk_moe.toggled.connect(self._przelacz_moe)
        wiersz.addWidget(self.chk_moe, alignment=Qt.AlignmentFlag.AlignBottom)
        self.btn_szukaj_model = self._przycisk(_("Szukaj"), "szukaj", slot=self.szukaj_modeli)
        wiersz.addWidget(self.btn_szukaj_model, alignment=Qt.AlignmentFlag.AlignBottom)
        self.btn_pull = self._przycisk(_("Pobierz"), "pobierz", "primary", self.pobierz_model)
        self.btn_pull.setEnabled(False)
        wiersz.addWidget(self.btn_pull, alignment=Qt.AlignmentFlag.AlignBottom)
        uk.addLayout(wiersz)

        # WHAT: wyniki z obu źródeł i warianty wybranego modelu - rozwijane pod polem.
        # WHY:  makieta ma samo pole na nazwę; wyszukiwarka zostaje (decyzja
        #       użytkownika), ale chowa się, gdy nie jest potrzebna.
        self.panel_wynikow = QFrame()
        self.panel_wynikow.setObjectName("searchPanel")
        vp = QVBoxLayout(self.panel_wynikow)
        vp.setContentsMargins(0, 0, 0, 0)
        vp.setSpacing(8)
        gora = QHBoxLayout()
        self.lbl_status_szukania = _etykieta("", "muted")
        self.lbl_status_szukania.setWordWrap(True)
        gora.addWidget(self.lbl_status_szukania, 1)
        zamknij = self._przycisk("", "zamknij", slot=lambda: self.panel_wynikow.hide())
        zamknij.setObjectName("small")
        zamknij.setToolTip(_("Zwiń wyniki"))
        gora.addWidget(zamknij)
        vp.addLayout(gora)
        tabele = QHBoxLayout()
        tabele.setSpacing(12)
        self.tabela_wynikow = QTableWidget(0, 3)
        self.tabela_wynikow.setHorizontalHeaderLabels([_("Model"), _("Źródło"), _("Pobrania")])
        self._ustaw_tabele_wyboru(self.tabela_wynikow)
        self.tabela_wynikow.itemSelectionChanged.connect(self._wybrano_wynik_wyszukiwania)
        tabele.addWidget(self.tabela_wynikow, 3)
        self.tabela_wariantow = QTableWidget(0, 3)
        self.tabela_wariantow.setHorizontalHeaderLabels([_("Wariant"), _("Rozmiar"), _("Kontekst")])
        self._ustaw_tabele_wyboru(self.tabela_wariantow)
        self.tabela_wariantow.itemSelectionChanged.connect(self._wybrano_wariant)
        tabele.addWidget(self.tabela_wariantow, 2)
        for tabela in (self.tabela_wynikow, self.tabela_wariantow):
            tabela.setFixedHeight(180)
        vp.addLayout(tabele)
        self.lbl_do_pobrania = _etykieta("", "muted")
        self.lbl_do_pobrania.setWordWrap(True)
        vp.addWidget(self.lbl_do_pobrania)
        self.lbl_szacowana_pamiec = _etykieta("", "muted")
        self.lbl_szacowana_pamiec.setWordWrap(True)
        vp.addWidget(self.lbl_szacowana_pamiec)
        # WHY: HF to repozytoria SPOŁECZNOŚCIOWE - każdy może tam coś opublikować,
        #      więc dopisek mówi to wprost i daje konkretną wskazówkę.
        zrodla = _etykieta(_("Źródła: oficjalna biblioteka Ollamy (ollama.com) i Hugging Face (tylko modele GGUF). "
                             "Hugging Face to repozytoria społecznościowe - pobieraj od znanych autorów "
                             "kwantyzacji (np. bartowski, mradermacher, unsloth)."), "muted")
        zrodla.setWordWrap(True)
        vp.addWidget(zrodla)
        self.panel_wynikow.hide()
        uk.addWidget(self.panel_wynikow)

        # --- Trwające pobieranie ---
        self.ramka_pobierania = QFrame()
        self.ramka_pobierania.setObjectName("progressRow")
        h = QHBoxLayout(self.ramka_pobierania)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(14)
        self.lbl_pobierany = _etykieta("", "mono")
        self.lbl_pobierany.setFixedWidth(220)
        h.addWidget(self.lbl_pobierany)
        self.pasek_postepu = QProgressBar()
        self.pasek_postepu.setTextVisible(False)
        h.addWidget(self.pasek_postepu, 1)
        self.lbl_postep = QLabel("")
        self.lbl_postep.setObjectName("progressText")
        self.lbl_postep.setFixedWidth(200)
        self.lbl_postep.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.lbl_postep)
        anuluj = QPushButton(_("Anuluj"))
        anuluj.setObjectName("small")
        anuluj.clicked.connect(self.anuluj_pobieranie)
        h.addWidget(anuluj)
        self.ramka_pobierania.setVisible(self._pobieranie is not None)
        uk.addWidget(self.ramka_pobierania)
        v.addWidget(karta)

        # --- Filtry ---
        filtry = QHBoxLayout()
        filtry.setSpacing(6)
        self._chipy_filtrow = {}
        grupa = QButtonGroup(strona)
        for klucz, nazwa in (("all", _("Wszystkie")), ("czat", _("Czat")), ("kod", _("Kod")),
                             ("embedding", _("Embedding")), ("wizja", _("Wizja"))):
            chip = self._chip(nazwa, lambda _c=False, k=klucz: self._filtruj_modele(k))
            chip.setChecked(klucz == self._filtr_modeli)
            grupa.addButton(chip)
            filtry.addWidget(chip)
            self._chipy_filtrow[klucz] = chip
        filtry.addStretch(1)
        self.lbl_zajete = _etykieta("", "muted")
        filtry.addWidget(self.lbl_zajete)
        v.addLayout(filtry)

        # --- Siatka kafelków ---
        przewijanie = QScrollArea()
        przewijanie.setWidgetResizable(True)
        przewijanie.setFrameShape(QScrollArea.Shape.NoFrame)
        przewijanie.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        kontener = QWidget()
        self._siatka_modeli = QGridLayout(kontener)
        self._siatka_modeli.setContentsMargins(0, 0, 0, 0)
        self._siatka_modeli.setSpacing(14)
        for kolumna in range(3):
            self._siatka_modeli.setColumnStretch(kolumna, 1)
        przewijanie.setWidget(kontener)
        v.addWidget(przewijanie, 1)
        self._podpis_modeli = None
        self._wypelnij_combo_hostow()
        return strona

    def _wypelnij_combo_hostow(self):
        # WHAT: lista "Na host" - wszystkie hosty; wybór zostaje po przebudowie listy.
        if not hasattr(self, "combo_host_pobierz"):
            return
        poprzedni = self.combo_host_pobierz.currentData()
        self.combo_host_pobierz.blockSignals(True)
        self.combo_host_pobierz.clear()
        for i, s in enumerate(self.serwery):
            self.combo_host_pobierz.addItem(s["nazwa"], s["adres"])
            self.combo_host_pobierz.setItemData(i, urlparse(s["adres"]).netloc, Qt.ItemDataRole.ToolTipRole)
        indeks = self.combo_host_pobierz.findData(poprzedni)
        self.combo_host_pobierz.setCurrentIndex(max(indeks, 0))
        self.combo_host_pobierz.blockSignals(False)

    def odswiez_modele(self):
        worker = ModeleWorker(self.serwery)
        self._uruchom_watek("modele", worker, worker.wynik, self._po_modelach)

    def _po_modelach(self, modele, hosty_online):
        self._modele = modele
        self.hosty_online_modeli = hosty_online
        gib = 1024 ** 3
        suma = sum(m["rozmiar"] * len(m["hosty"]) for m in modele)
        hosty_z_modelami = {h["adres"] for m in modele for h in m["hosty"]}
        self.lbl_zajete.setText(_("Zajęte miejsce: {gb:.1f} GB na {n} hostach").format(
            gb=suma / gib, n=len(hosty_z_modelami)))
        self._wypelnij_siatke_modeli()
        self._odswiez_schemat()
        self._odswiez_modele_czatu()
        self._odswiez_modele_rag()

    def _filtruj_modele(self, klucz):
        self._filtr_modeli = klucz
        self._podpis_modeli = None
        self._wypelnij_siatke_modeli()

    def _wypelnij_siatke_modeli(self):
        # WHY: siatkę przebudowujemy tylko, gdy coś się zmieniło - odświeżanie co
        #      10 s nie może zamykać otwartego menu ani migać kafelkami.
        widoczne = [m for m in self._modele if self._filtr_modeli == "all" or m["rodzaj"] == self._filtr_modeli]
        podpis = (self._filtr_modeli, tuple(
            (m["nazwa"], tuple((h["adres"], h["w_pamieci"]) for h in m["hosty"])) for m in widoczne),
            tuple(s["adres"] for s in self.hosty_online_modeli))
        if podpis == self._podpis_modeli:
            return
        self._podpis_modeli = podpis
        while self._siatka_modeli.count():
            element = self._siatka_modeli.takeAt(0)
            if element.widget():
                element.widget().deleteLater()
        for i, model in enumerate(widoczne):
            self._siatka_modeli.addWidget(KartaModelu(model, self), i // 3, i % 3)
        if not widoczne:
            pusto = _etykieta(_("Brak modeli w tej kategorii.") if self._modele
                              else _("Brak modeli - pobierz pierwszy model polem wyżej."), "muted")
            self._siatka_modeli.addWidget(pusto, 0, 0, 1, 3)
        self._siatka_modeli.setRowStretch(self._siatka_modeli.rowCount(), 1)

    def _wybierz_host_z_menu(self, przycisk, hosty, akcja, wszystkie=None):
        # WHAT: jeden host -> od razu akcja; kilka -> menu z nazwami hostów pod przyciskiem.
        if len(hosty) == 1 and not wszystkie:
            akcja(hosty[0])
            return
        menu = QMenu(self)
        for host in hosty:
            menu.addAction(f"{host['nazwa']} ({urlparse(host['adres']).netloc})", lambda h=host: akcja(h))
        if wszystkie:
            menu.addSeparator()
            menu.addAction(wszystkie[0], wszystkie[1])
        menu.exec(przycisk.mapToGlobal(QPoint(0, przycisk.height())))

    def zaladuj_model(self, model, przycisk, zwolnij):
        hosty = [h for h in model["hosty"] if h["w_pamieci"] == zwolnij] or model["hosty"]

        def _akcja(host):
            nazwa = model["nazwa"]
            opis = (_("Zwolnienie {model} na {host}") if zwolnij else _("Załadowanie {model} na {host}")).format(
                model=nazwa, host=host["nazwa"])
            self.wpis_log(opis + "...")
            self._uruchom_akcje(
                lambda: ollama_zaladuj_model(host["adres"], nazwa, model["rodzaj"] == "embedding", zwolnij),
                opis, lambda _s: self.odswiez_modele())

        self._wybierz_host_z_menu(przycisk, hosty, _akcja)

    def kopiuj_model_na_host(self, model, przycisk, cele):
        # WHAT: "Kopiuj na host" = pobranie tego samego modelu na inny host.
        # WHY:  Ollama nie kopiuje modeli między hostami; pobranie z sieci daje
        #       ten sam plik (te same warstwy), a jedna kolejka pobierania nie
        #       obciąża łącza podwójnie.
        self._wybierz_host_z_menu(przycisk, cele, lambda host: self._rozpocznij_pobieranie(
            model["nazwa"], host["adres"], host["nazwa"]))

    def usun_model(self, model, przycisk):
        def _usun(hosty):
            nazwy = ", ".join(h["nazwa"] for h in hosty)
            # WHY: usuwanie jest nieodwracalne - wymagamy potwierdzenia.
            odp = QMessageBox.question(
                self, _("Usuń model"),
                _("Na pewno usunąć model:\n\n{model}\n\nz hostów: {hosty}\n\nTej operacji nie da się cofnąć.").format(
                    model=model["nazwa"], hosty=nazwy))
            if odp != QMessageBox.StandardButton.Yes:
                return

            def _wykonaj():
                for h in hosty:
                    OllamaClient(base_url=h["adres"]).delete_model(model["nazwa"])

            self.wpis_log(_("Usuwam model: {model}").format(model=model["nazwa"]))
            self._uruchom_akcje(_wykonaj, _("Usunięcie {model}").format(model=model["nazwa"]),
                                lambda _s: self.odswiez_modele())

        self._wybierz_host_z_menu(
            przycisk, model["hosty"], lambda h: _usun([h]),
            (_("Ze wszystkich hostów"), lambda: _usun(model["hosty"])) if len(model["hosty"]) > 1 else None)

    # --- Wyszukiwarka (Ollama + Hugging Face) --------------------------------
    def _ustaw_tabele_wyboru(self, tabela):
        # WHAT: wspólny wygląd tabel wyników i wariantów - wybór całego wiersza, jeden naraz.
        naglowek = tabela.horizontalHeader()
        naglowek.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        naglowek.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for kolumna in range(1, tabela.columnCount()):
            naglowek.setSectionResizeMode(kolumna, QHeaderView.ResizeMode.ResizeToContents)
        tabela.verticalHeader().setVisible(False)
        tabela.verticalHeader().setDefaultSectionSize(32)
        tabela.setShowGrid(False)
        tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def _enter_w_polu_modelu(self):
        # WHY: Enter przy dokładnej nazwie (z tagiem) pobiera od razu, a przy
        #      samej nazwie - szuka; nie trzeba sięgać po mysz.
        if self._model_do_pobrania() and not self.panel_wynikow.isVisible():
            self.pobierz_model()
        else:
            self.szukaj_modeli()

    def szukaj_modeli(self):
        # WHAT: tekst z "/" (link do HF, "użytkownik/repo" albo "hf.co/...") ->
        #       od razu warianty tego repo; zwykła nazwa -> szukanie naraz w
        #       bibliotece Ollamy i na Hugging Face (SzukajModeliWorker).
        zapytanie = self.pole_szukaj_model.text().strip()
        if not zapytanie or self._w_toku("szukaj"):
            return
        self.panel_wynikow.show()
        if "/" in zapytanie:
            # WHY: tag obcinamy dopiero z GOTOWEGO repo - obcięcie po ":" z całego
            #      tekstu zostawiało z "https://..." samo "https".
            repo = _hf_repo_z_linku(re.sub(r"^hf\.co/", "", zapytanie))
            if repo:
                repo = repo.split(":", 1)[0]
                self._wypelnij_wyniki([{
                    "zrodlo": "hf", "id": repo, "opis": "", "mozliwosci": [], "rozmiary": [], "pobrania": "",
                }])
                self.lbl_status_szukania.setText("")
                self.tabela_wynikow.selectRow(0)
                return

        self._wypelnij_wyniki([])
        self.btn_szukaj_model.setEnabled(False)
        self.lbl_status_szukania.setText(_("Szukam w bibliotece Ollamy i na Hugging Face..."))
        worker = SzukajModeliWorker(zapytanie, sprawdz_moe=self._tylko_moe)

        def _po_szukaniu(wyniki, bledy):
            self.btn_szukaj_model.setEnabled(True)
            self._wyniki_szukania = wyniki
            self._bledy_szukania = bledy
            self._pokaz_wyniki_szukania()

        self._uruchom_watek("szukaj", worker, worker.wynik, _po_szukaniu)

    def _pokaz_wyniki_szukania(self):
        # WHAT: wyniki ostatniego szukania - wszystkie albo tylko MoE (pole wyboru).
        widoczne = [w for w in self._wyniki_szukania if not self._tylko_moe or jest_moe(w)]
        self._wypelnij_wyniki(widoczne)
        if self._tylko_moe:
            linie = [_("Znalezione modele: {n} (MoE: {m})").format(n=len(self._wyniki_szukania), m=len(widoczne))]
        else:
            linie = [_("Znalezione modele: {n}").format(n=len(widoczne))]
        linie += [_("Błąd wyszukiwania - {blad}").format(blad=b) for b in getattr(self, "_bledy_szukania", [])]
        self.lbl_status_szukania.setText("\n".join(linie))

    def _przelacz_moe(self, wlacz):
        self._tylko_moe = wlacz
        if not self._wyniki_szukania:
            return
        if wlacz and any(w.get("zrodlo") == "ollama" and "moe_z_tagow" not in w and not jest_moe(w)
                         for w in self._wyniki_szukania):
            # WHY: wyniki szukane bez filtra - tagi modeli z Ollamy sprawdzamy dopiero teraz
            self.lbl_status_szukania.setText(_("Sprawdzam warianty modeli..."))
            wyniki = self._wyniki_szukania
            worker = FunkcjaWorker(lambda: uzupelnij_moe_z_tagow(wyniki))
            self._uruchom_watek("moe", worker, worker.zakonczono, lambda _ok, _w: self._pokaz_wyniki_szukania())
        else:
            self._pokaz_wyniki_szukania()

    def _wypelnij_wyniki(self, wyniki):
        # WHAT: tabela wyników - nazwa, źródło, pobrania; cały wynik jako dane wiersza.
        self.tabela_wynikow.blockSignals(True)
        self.tabela_wynikow.setRowCount(0)
        for wynik in wyniki:
            zrodlo = "Hugging Face" if wynik["zrodlo"] == "hf" else "Ollama"
            # WHY: dymek to HTML - tekst ze strony trzeba zabezpieczyć, bo opis
            #      typu "<4GB VRAM" rozbiłby formatowanie.
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
        # WHY: spóźniona odpowiedź dla modelu, który użytkownik zdążył już
        #      odznaczyć, jest ignorowana (self._warianty_dla).
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
            # WHY: rodzina z wariantami zwykłymi i MoE (np. qwen3: 8b obok 30b-a3b) -
            #      przy włączonym filtrze pokazujemy tylko MoE. Gdy żaden tag nie
            #      zdradza MoE (np. gpt-oss), cały model jest MoE - zostają wszystkie.
            if self._tylko_moe and any(wariant_moe(w[0]) for w in warianty):
                warianty = [w for w in warianty if wariant_moe(w[0])]
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
        # WHAT: "model:tag" (Ollama) albo "hf.co/user/repo:kwant" dla zaznaczonego wariantu.
        wiersz = self.tabela_wariantow.currentRow()
        if wiersz < 0 or not self._warianty_dla:
            return None
        zrodlo, model_id = self._warianty_dla
        kwant, _rozmiar = self.tabela_wariantow.item(wiersz, 0).data(Qt.ItemDataRole.UserRole)
        if zrodlo == "hf":
            # WHY: bez rozpoznanej kwantyzacji Ollama chce sam 'hf.co/user/repo'.
            return f"hf.co/{model_id}:{kwant}" if kwant else f"hf.co/{model_id}"
        return f"{model_id}:{kwant}"

    def _model_do_pobrania(self):
        # WHAT: zaznaczony wariant, a bez niego dokładna nazwa z pola (z tagiem
        #       po ":" albo "hf.co/...") - sama nazwa bez tagu to zapytanie.
        nazwa = self._pelna_nazwa_wariantu()
        if nazwa:
            return nazwa
        tekst = self.pole_szukaj_model.text().strip()
        if ":" in tekst or tekst.startswith("hf.co/"):
            return tekst
        return None

    def _aktualizuj_przycisk_pobierz(self):
        self.btn_pull.setEnabled(self._pobieranie is None and self._model_do_pobrania() is not None)

    def _wybrano_wariant(self):
        self._aktualizuj_przycisk_pobierz()
        nazwa = self._pelna_nazwa_wariantu()
        if not nazwa:
            self.lbl_do_pobrania.setText("")
            self.lbl_szacowana_pamiec.setText("")
            return
        self.lbl_do_pobrania.setText(_("Do pobrania: {model}").format(model=nazwa))
        kwant, rozmiar_bajty = self.tabela_wariantow.item(
            self.tabela_wariantow.currentRow(), 0).data(Qt.ItemDataRole.UserRole)
        # WHY: liczbę parametrów bierzemy z tagu, a gdy jej tam nie ma - z nazwy modelu/repo.
        miliardy = _parsuj_rozmiar_parametrow(kwant or "") or _parsuj_rozmiar_parametrow(self._warianty_dla[1])
        self._pokaz_szacowana_pamiec(rozmiar_bajty, miliardy, kwant or "")

    def _pokaz_szacowana_pamiec(self, rozmiar_bajty, miliardy, etykieta):
        self.lbl_szacowana_pamiec.setToolTip("")
        if not rozmiar_bajty:
            self.lbl_szacowana_pamiec.setText("")
            return
        wagi_gb = rozmiar_bajty / (1024 ** 3)
        if miliardy is None:
            # WHY: bez liczby parametrów nie da się oszacować KV cache - pokazujemy
            #      tylko to, co wiemy na pewno (rozmiar pliku).
            self.lbl_szacowana_pamiec.setText(_(
                "Wagi (rozmiar pliku): {wagi:.1f} GB - nie znam liczby parametrów, więc bez "
                "szacowania cache kontekstu. Realne zużycie pamięci będzie wyższe."
            ).format(wagi=wagi_gb))
            return
        w = _oszacuj_pamiec_z_wag(wagi_gb, miliardy, etykieta)
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
                "Cache {kvq} działa TYLKO z OLLAMA_FLASH_ATTENTION=1 (ekran Ustawienia) - "
                "bez tego Ollama i tak liczy cache jak f16, czyli więcej niż wyliczono wyżej."
            ).format(kvq=_KALKULATOR_KVQ))
        szczegoly.append(_(
            "Na APU / pamięci współdzielonej zarezerwuj pamięć dla systemu przez OLLAMA_GPU_OVERHEAD."
        ))
        self.lbl_szacowana_pamiec.setToolTip("\n".join(szczegoly))

    # --- Pobieranie ----------------------------------------------------------
    def pobierz_model(self):
        nazwa = self._model_do_pobrania()
        adres = self.combo_host_pobierz.currentData()
        if nazwa and adres:
            serwer = next((s for s in self.serwery if s["adres"] == adres), {"nazwa": adres})
            self._rozpocznij_pobieranie(nazwa, adres, serwer["nazwa"])

    def _rozpocznij_pobieranie(self, model, adres, nazwa_hosta):
        # WHY: jedno pobieranie naraz - prościej i nie obciąża łącza/dysku podwójnie.
        if self._pobieranie is not None:
            self.wpis_log(_("Pobieranie już trwa - poczekaj na zakończenie."))
            return
        worker = PullWorker(OllamaClient(base_url=adres), model)
        self._pobieranie = {"worker": worker, "model": model, "host": nazwa_hosta,
                            "probka": None, "predkosc": None, "status": None}
        self.lbl_pobierany.setText(QFontMetrics(QFont("JetBrains Mono")).elidedText(
            f"{_skroc_nazwe_modelu_hf(model)} → {nazwa_hosta}", Qt.TextElideMode.ElideMiddle, 215))
        self.lbl_pobierany.setToolTip(f"{model} → {nazwa_hosta}")
        self.pasek_postepu.setRange(0, 0)
        self.lbl_postep.setText(_("łączę..."))
        self.ramka_pobierania.show()
        self._aktualizuj_przycisk_pobierz()
        self.wpis_log(_("Rozpoczynam pobieranie: {model}").format(model=f"{model} → {nazwa_hosta}"), "INFO", "Ollama")
        worker.postep.connect(self._postep_pull)
        worker.zakonczono.connect(self._koniec_pull)
        worker.start()

    def _postep_pull(self, procent, status, pobrane, calosc):
        # WHAT: pasek + "4,1 z 7,1 GB · 38 MB/s"; do dziennika tylko ZMIANA statusu.
        # WHY:  strumień /api/pull sypie dziesiątkami linii na sekundę.
        p = self._pobieranie
        if p is None:
            return
        if procent < 0:
            self.pasek_postepu.setRange(0, 0)
            self.lbl_postep.setText(status)
        else:
            self.pasek_postepu.setRange(0, 100)
            self.pasek_postepu.setValue(procent)
            teraz = time.monotonic()
            if p["probka"] and teraz - p["probka"][0] >= 1 and pobrane >= p["probka"][1]:
                chwilowa = (pobrane - p["probka"][1]) / (teraz - p["probka"][0])
                # WHY: wygładzanie - sama chwilowa prędkość skakałaby co sekundę
                p["predkosc"] = chwilowa if p["predkosc"] is None else 0.7 * p["predkosc"] + 0.3 * chwilowa
                p["probka"] = (teraz, pobrane)
            elif not p["probka"] or pobrane < p["probka"][1]:
                p["probka"] = (teraz, pobrane)
            gib = 1024 ** 3
            tekst = _("{a:.1f} z {b:.1f} GB").format(a=pobrane / gib, b=calosc / gib)
            if p["predkosc"]:
                tekst += f" · {p['predkosc'] / 1024 ** 2:.0f} MB/s"
            self.lbl_postep.setText(tekst)
        if status != p["status"]:
            p["status"] = status
            self.wpis_log(f"{p['model']}: {status}", "INFO", "Ollama")

    def anuluj_pobieranie(self):
        if self._pobieranie:
            self._pobieranie["worker"].anuluj()
            self.lbl_postep.setText(_("anuluję..."))

    def _koniec_pull(self, sukces, komunikat):
        anulowane = bool(self._pobieranie and self._pobieranie["worker"]._anuluj)
        self._pobieranie = None
        self.ramka_pobierania.hide()
        self._aktualizuj_przycisk_pobierz()
        self.wpis_log(komunikat, "INFO" if sukces or anulowane else "BŁĄD", "Ollama")
        if not anulowane:
            # WHY: pobranie dużego modelu trwa długo - pewnie patrzysz wtedy na coś innego
            self._powiadom_w_tle(_("Pobieranie zakończone") if sukces else _("Pobieranie nie powiodło się"),
                                 komunikat)
        self.odswiez_modele()  # WHY: świeżo pobrany model powinien od razu pojawić się w siatce
        self.odswiez_hosty()

    # =========================================================================
    #  Ekran "Czat" (spoza makiet - prośba użytkownika, todo.md)
    # =========================================================================
    def _strona_czat(self):
        # WHAT: prosty czat z modelem prosto z Ollamy - po lewej rozmowa, po prawej
        #       wybór modelu i statystyki ostatniej odpowiedzi (tokeny/s).
        # WHY:  do szybkich pytań i sprawdzenia modelu bez instalowania Open WebUI;
        #       świadomie bez zapisywania rozmów (pełny czat z historią to WebUI).
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(18)
        naglowek = QHBoxLayout()
        naglowek.addLayout(self._tytul_strony(
            _("Czat"), _("Szybka rozmowa z modelem prosto z Ollamy - bez Open WebUI i bez przeglądarki.")), 1)
        naglowek.addWidget(self._przycisk(_("Nowa rozmowa"), "plus", slot=self.nowa_rozmowa),
                           0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(naglowek)

        rzad = QHBoxLayout()
        rzad.setSpacing(18)
        # --- zapisane rozmowy ---
        karta, uk = self._karta()
        karta.setFixedWidth(340)  # WHY: szersza kosztem prawej karty - tytuły rozmów w 1-2 liniach
        uk.addWidget(_etykieta(_("Rozmowy"), "h2"))
        self.pole_szukaj_rozmow = QLineEdit()
        self.pole_szukaj_rozmow.setPlaceholderText(_("Szukaj w rozmowach..."))
        self.pole_szukaj_rozmow.setClearButtonEnabled(True)
        self.pole_szukaj_rozmow.textChanged.connect(self._filtruj_rozmowy)
        uk.addWidget(self.pole_szukaj_rozmow)
        self.lista_rozmow = QListWidget()
        self.lista_rozmow.setObjectName("chatList")
        self.lista_rozmow.setWordWrap(True)
        self.lista_rozmow.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_rozmow.itemClicked.connect(lambda e: self._otworz_rozmowe(e.data(Qt.ItemDataRole.UserRole)))
        self.lista_rozmow.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lista_rozmow.customContextMenuRequested.connect(self._menu_rozmowy)
        uk.addWidget(self.lista_rozmow, 1)
        # WHY: bez przycisków pod listą (prośba użytkownika) - zmiana nazwy, eksport i usuwanie
        #      są w menu pod prawym przyciskiem; do tego dwuklik/F2 = nazwa, Delete = usuń
        self.lista_rozmow.itemDoubleClicked.connect(lambda _e: self._zmien_nazwe_rozmowy())
        for klawisz, slot in ((Qt.Key.Key_F2, self._zmien_nazwe_rozmowy), (Qt.Key.Key_Delete, self._usun_rozmowe)):
            skrot = QShortcut(QKeySequence(klawisz), self.lista_rozmow)
            skrot.setContext(Qt.ShortcutContext.WidgetShortcut)
            skrot.activated.connect(slot)
        sciezka = str(katalog_rozmow()).replace(str(Path.home()), "~", 1)
        uwaga = _etykieta(_("Prawy klik na rozmowie: zmień nazwę, eksportuj, usuń. Zapisywane automatycznie, "
                            "tylko na tym komputerze: {sciezka}").format(sciezka=sciezka), "muted")
        uwaga.setObjectName("smallNote")  # WHY: mała czcionka - to tylko podpowiedź (prośba użytkownika)
        uwaga.setWordWrap(True)
        uk.addWidget(uwaga)
        rzad.addWidget(karta)

        # --- rozmowa ---
        karta, uk = self._karta()
        uk.setSpacing(12)
        self.widok_czatu = WidokCzatu()
        self.widok_czatu.pokaz_myslenie = _ustawienia().value("czat/rozumowanie", False, type=bool)
        self.widok_czatu.akcja.connect(self._akcja_czatu)
        uk.addWidget(self.widok_czatu, 1)
        # --- załączniki: uwaga + "chipy" z nazwami nad polem ---
        self.lbl_czat_uwaga = _etykieta("", "muted")
        self.lbl_czat_uwaga.setObjectName("chatNotice")
        self.lbl_czat_uwaga.setWordWrap(True)
        self.lbl_czat_uwaga.hide()
        uk.addWidget(self.lbl_czat_uwaga)
        self.rzad_zalacznikow = QHBoxLayout()
        self.rzad_zalacznikow.setSpacing(6)
        uk.addLayout(self.rzad_zalacznikow)
        wpis = QHBoxLayout()
        wpis.setSpacing(10)
        self.btn_czat_zalacz = QPushButton()
        self.btn_czat_zalacz.setObjectName("attachButton")
        self.btn_czat_zalacz.setIcon(ikona("spinacz", PALETA.tekst, rozmiar=20, grubosc=1.8))
        self.btn_czat_zalacz.setIconSize(QSize(20, 20))
        self.btn_czat_zalacz.setFixedSize(48, 76)
        self.btn_czat_zalacz.setToolTip(_("Dołącz zdjęcie, dokument (PDF, Word, Excel, PowerPoint, LibreOffice, EPUB) "
                                          "albo plik tekstowy (kod, .txt, .md, .csv...). Możesz też przeciągnąć plik "
                                          "do okna albo wkleić zrzut ekranu (Ctrl+V)."))
        self.btn_czat_zalacz.clicked.connect(self._wybierz_zalaczniki)
        wpis.addWidget(self.btn_czat_zalacz)
        # WHAT: przełącznik "szukaj w internecie" - widoczny tylko, gdy w Ustawieniach wybrano źródło.
        # WHY:  włączony zostaje do wyłączenia (kolejne pytania też szukają), ale po ponownym
        #       uruchomieniu programu jest wyłączony - zapytania wychodzą do internetu tylko świadomie
        self.btn_czat_internet = QPushButton()
        self.btn_czat_internet.setObjectName("attachButton")
        self.btn_czat_internet.setCheckable(True)
        self.btn_czat_internet.setChecked(self._szukaj_w_internecie)
        self.btn_czat_internet.setIcon(ikona("internet", PALETA.tekst, rozmiar=20, grubosc=1.8))
        self.btn_czat_internet.setIconSize(QSize(20, 20))
        self.btn_czat_internet.setFixedSize(48, 76)
        self.btn_czat_internet.toggled.connect(self._przelacz_szukanie_w_internecie)
        wpis.addWidget(self.btn_czat_internet)
        self._odswiez_przycisk_internetu()
        self.pole_czat = QPlainTextEdit()
        self.pole_czat.setObjectName("chatInput")
        self.pole_czat.setPlaceholderText(_("Napisz wiadomość... (Enter wysyła, Shift+Enter - nowa linia)"))
        self.pole_czat.setFixedHeight(76)
        self.pole_czat.installEventFilter(self)
        # WHY: pliki można upuścić na pole albo na rozmowę - filtr na "viewport",
        #      bo to on dostaje zdarzenia przeciągania w polach tekstowych
        self.pole_czat.viewport().installEventFilter(self)
        self.widok_czatu.viewport().installEventFilter(self)
        self.widok_czatu.viewport().setAcceptDrops(True)
        wpis.addWidget(self.pole_czat, 1)
        self.btn_czat_wyslij = self._przycisk(_("Wyślij"), rola="primary", slot=self.wyslij_czat)
        self.btn_czat_wyslij.setFixedHeight(76)
        self.btn_czat_wyslij.setMinimumWidth(120)
        wpis.addWidget(self.btn_czat_wyslij)
        uk.addLayout(wpis)
        rzad.addWidget(karta, 1)

        # --- model i statystyki ---
        # WHY: zwarta i wąska (prośba użytkownika) - więcej miejsca dla rozmowy i listy rozmów;
        #      opisy statystyk w dymkach po najechaniu, nie pod każdą liczbą
        karta, uk = self._karta()
        karta.setFixedWidth(290)
        uk.setSpacing(8)
        uk.addWidget(_etykieta(_("Model"), "h3"))
        self.combo_czat_model = QComboBox()
        self.combo_czat_model.currentIndexChanged.connect(self._zmiana_modelu_czatu)
        uk.addWidget(self.combo_czat_model)
        # WHAT: jedna drobna linia (rozmiar · parametry · kwantyzacja · host) i pod nią tagi
        #       możliwości modelu (w pamięci / zdjęcia / myślenie / narzędzia).
        self.lbl_czat_model = _etykieta("", "muted")
        self.lbl_czat_model.setObjectName("modelInfo")
        uk.addWidget(self.lbl_czat_model)
        self.ramka_tagow_modelu = QWidget()
        self.ramka_tagow_modelu.setObjectName("capTags")
        self.uklad_tagow_modelu = QVBoxLayout(self.ramka_tagow_modelu)
        self.uklad_tagow_modelu.setContentsMargins(0, 0, 0, 0)
        self.uklad_tagow_modelu.setSpacing(4)
        uk.addWidget(self.ramka_tagow_modelu)
        # WHAT: dwa przełączniki dla modeli, które "myślą" (qwen3, gemma4...): czy w ogóle
        #       myśleć (think w /api/chat) i czy pokazywać rozumowanie w rozmowie.
        # WHY:  bez myślenia proste pytanie to ułamek sekundy zamiast kilkunastu (zmierzone
        #       na gemma4: 4 tokeny / 0,5 s zamiast ok. 260 tokenów / 15 s, ta sama odpowiedź).
        def przelacznik(tytul, opis, slot, wlaczony):
            wiersz = QHBoxLayout()
            lbl = QLabel(tytul)
            lbl.setToolTip(opis)
            wiersz.addWidget(lbl, 1)
            sw = Przelacznik(tytul)
            sw.setToolTip(opis)
            sw.clicked.connect(slot)
            sw.ustaw_cicho(wlaczony)
            wiersz.addWidget(sw)
            uk.addLayout(wiersz)
            return sw, lbl

        self.sw_czat_myslenie, self.lbl_czat_myslenie = przelacznik(
            _("Myślenie modelu"),
            _("model rozważa odpowiedź, zanim ją napisze - dokładniej przy trudnych pytaniach, ale dużo "
              "wolniej; wyłącz dla prostych pytań"),
            self._przelacz_myslenie, _ustawienia().value("czat/myslenie", True, type=bool))
        self.sw_czat_rozumowanie, self.lbl_czat_rozumowanie = przelacznik(
            _("Pokazuj rozumowanie"), _("pokazuj w rozmowie, co model \"myślał\" przed odpowiedzią"),
            self._przelacz_rozumowanie, self.widok_czatu.pokaz_myslenie)

        # WHAT: ile okna kontekstu zajmuje rozmowa - liczba w linii nagłówka, pasek i rada.
        uk.addSpacing(8)
        wiersz = QHBoxLayout()
        wiersz.addWidget(_etykieta(_("Kontekst"), "h3"))
        wiersz.addStretch(1)
        self.lbl_kontekst = _etykieta("—", "mono")
        self.lbl_kontekst.setToolTip(_("tokeny rozmowy / okno kontekstu, z jakim działa model"))
        wiersz.addWidget(self.lbl_kontekst)
        uk.addLayout(wiersz)
        self.pasek_kontekstu = QProgressBar()
        self.pasek_kontekstu.setObjectName("meter")
        self.pasek_kontekstu.setTextVisible(False)
        self.pasek_kontekstu.setRange(0, 1000)
        uk.addWidget(self.pasek_kontekstu)
        self.lbl_kontekst_rada = _etykieta("", "muted")
        self.lbl_kontekst_rada.setObjectName("contextHint")
        self.lbl_kontekst_rada.setWordWrap(True)
        self.lbl_kontekst_rada.hide()
        uk.addWidget(self.lbl_kontekst_rada)

        uk.addSpacing(8)
        uk.addWidget(_etykieta(_("Ostatnia odpowiedź"), "h3"))
        siatka = QGridLayout()
        siatka.setHorizontalSpacing(10)
        siatka.setVerticalSpacing(6)
        siatka.setColumnStretch(0, 1)
        self._lbl_stat_czatu = {}
        for nr, (klucz, nazwa, opis) in enumerate((
                ("generowanie", _("Generowanie"), _("tokeny odpowiedzi na sekundę")),
                ("prompt", _("Przetwarzanie pytania"), _("tokeny pytania i historii na sekundę")),
                ("tokeny", _("Tokeny"), _("pytanie + historia / odpowiedź")),
                ("calosc", _("Czas całkowity"), ""))):
            lbl = _etykieta(nazwa, "muted")
            lbl.setToolTip(opis)
            lbl.setWordWrap(True)  # WHY: dłuższe tłumaczenia ("Verarbeitung der Frage") w dwóch liniach
            siatka.addWidget(lbl, nr, 0)
            wartosc = _etykieta("—", "mono")
            wartosc.setToolTip(opis)
            siatka.addWidget(wartosc, nr, 1, Qt.AlignmentFlag.AlignRight)
            self._lbl_stat_czatu[klucz] = wartosc
        uk.addLayout(siatka)
        uk.addStretch(1)
        rzad.addWidget(karta)
        v.addLayout(rzad, 1)

        self._wypelnij_liste_rozmow()
        self._odswiez_modele_czatu()
        self._pokaz_czat()
        self._pokaz_kontekst()
        self._pokaz_zalaczniki()
        self._odswiez_przycisk_czatu()
        return strona

    def eventFilter(self, obiekt, zdarzenie):
        # WHAT: pole czatu - Enter wysyła (Shift+Enter = nowa linia), Ctrl+V z obrazem
        #       w schowku dołącza zrzut ekranu; pole i rozmowa przyjmują upuszczone pliki.
        pole = getattr(self, "pole_czat", None)
        if pole is None:
            return super().eventFilter(obiekt, zdarzenie)
        typ = zdarzenie.type()
        if obiekt is pole and typ == QEvent.Type.KeyPress:
            if (zdarzenie.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and not zdarzenie.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.wyslij_czat()
                return True
            if zdarzenie.matches(QKeySequence.StandardKey.Paste) and QApplication.clipboard().mimeData().hasImage():
                # WHY: zrzut ekranu ze schowka (Spectacle, PrintScreen) - jak w komunikatorach
                self._dodaj_zalacznik(lambda: _obraz_do_wyslania(QApplication.clipboard().image(),
                                                                   _("zrzut ekranu.jpg")))
                return True
        if obiekt in (pole.viewport(), self.widok_czatu.viewport()):
            if typ in (QEvent.Type.DragEnter, QEvent.Type.DragMove) and zdarzenie.mimeData().hasUrls():
                zdarzenie.acceptProposedAction()
                return True
            if typ == QEvent.Type.Drop and zdarzenie.mimeData().hasUrls():
                for url in zdarzenie.mimeData().urls():
                    if url.isLocalFile():
                        self._dodaj_zalacznik(lambda p=url.toLocalFile(): wczytaj_zalacznik(p))
                zdarzenie.acceptProposedAction()
                return True
        return super().eventFilter(obiekt, zdarzenie)

    # --- załączniki czatu ---------------------------------------------------
    def _wybierz_zalaczniki(self):
        pliki, _f = QFileDialog.getOpenFileNames(
            self, _("Dołącz do wiadomości"), str(Path.home()),
            _("Zdjęcia, dokumenty i pliki tekstowe (*)") + ";;" + _("Zdjęcia") + " (*.png *.jpg *.jpeg *.webp *.gif *.bmp)"
            + ";;" + _("Dokumenty") + " (*.pdf *.docx *.xlsx *.pptx *.odt *.ods *.odp *.epub)")
        for sciezka in pliki:
            self._dodaj_zalacznik(lambda p=sciezka: wczytaj_zalacznik(p))

    def _dodaj_zalacznik(self, wczytaj):
        # WHAT: wczytuje załącznik (funkcja może rzucić ValueError z powodem) i odświeża pasek.
        try:
            z = wczytaj()
        except (ValueError, OSError) as e:
            self._uwaga_czatu(str(e), "warn")
            return
        self._uwaga_czatu("")
        if z["typ"] == "do_odczytu":
            # WHY: PDF/Office/EPUB - tekst wyciąga wątek w tle, chip pokazuje "odczytuję..."
            worker = FunkcjaWorker(lambda sciezka=z["sciezka"]: odczytaj_dokument(sciezka))
            z["_watek"] = worker
            worker.zakonczono.connect(lambda ok, wynik, zz=z: self._dokument_odczytany(zz, ok, wynik))
            worker.start()
        elif z["typ"] == "dokument" and not self._przygotuj_dokument(z):
            return
        self._zalaczniki.append(z)
        self._pokaz_zalaczniki()
        self._odswiez_modele_czatu()  # WHY: ze zdjęciem lista pokazuje też modele OCR

    def _dokument_odczytany(self, z, ok, wynik):
        z.pop("_watek", None)
        if z not in self._zalaczniki:
            return  # WHY: usunięty w trakcie odczytu
        if not ok:
            self._zalaczniki.remove(z)
            self._uwaga_czatu(wynik, "warn")
        else:
            z.pop("sciezka", None)
            z.update(wynik)
            # WHY: wyciągnięty tekst idzie tą samą drogą co plik tekstowy - w całości
            #      albo (powyżej 100 KB) przez wyszukiwanie fragmentów
            if z["typ"] == "dokument" and not self._przygotuj_dokument(z):
                self._zalaczniki.remove(z)
        self._pokaz_zalaczniki()

    def _modele_embeddingow(self):
        # WHAT: [(nazwa, adres, nazwa hosta)] - modele embeddingów na hostach online.
        return [(m["nazwa"], h["adres"], h["nazwa"]) for m in self._modele
                if "embedding" in m.get("mozliwosci", []) or m["rodzaj"] == "embedding" for h in m["hosty"]]

    def _model_embeddingow(self):
        # WHAT: (model, adres) do embeddingów - wybrany w Ustawieniach, a przy "Automatycznie"
        #       (albo gdy wybrany host jest offline) najlepiej na hoście modelu czatu.
        # WHY:  Ollama liczy wektory tylko modelem typu "embedding"; ten sam model musi
        #       potem liczyć wektor pytania, więc zapamiętujemy go przy dokumencie.
        kandydaci = [(nazwa, adres) for nazwa, adres, _h in self._modele_embeddingow()]
        wybrany = _ustawienia().value("czat/model_rag", "")
        for nazwa, adres in kandydaci:
            if f"{adres}|{nazwa}" == wybrany:
                return nazwa, adres
        _model, host = self._wybrany_model_czatu()
        for nazwa, adres in kandydaci:
            if host and adres == host["adres"]:
                return nazwa, adres
        return kandydaci[0] if kandydaci else None

    def _przygotuj_dokument(self, z):
        # WHAT: duży plik -> RagWorker (fragmenty + wektory) w tle; postęp na chipie.
        wybor = self._model_embeddingow()
        if not wybor:
            self._uwaga_czatu(_("{nazwa} jest duży (ponad 100 KB) - do wyszukiwania w nim potrzebny jest model "
                                "embeddingów. Zalecany pobierzesz jednym przyciskiem w \"Ustawieniach\" (karta "
                                "\"Czat\").").format(nazwa=z["nazwa"]), "warn")
            return False
        z["model_emb"], z["adres_emb"] = wybor
        z["gotowy"], z["procent"] = False, 0
        z["_start"] = time.monotonic()
        worker = RagWorker(z["adres_emb"], z["model_emb"], z.pop("tekst"))
        z["_watek"] = worker
        worker.postep.connect(lambda gotowe, wszystkie, zz=z: self._postep_dokumentu(zz, gotowe, wszystkie))
        worker.zakonczono.connect(lambda ok, wynik, zz=z: self._dokument_gotowy(zz, ok, wynik))
        worker.start()
        return True

    def _postep_dokumentu(self, z, gotowe, wszystkie):
        z["procent"] = int(100 * gotowe / max(1, wszystkie))
        # WHAT: szacowany pozostały czas z FAKTYCZNEJ prędkości tego komputera.
        # WHY:  prędkość embeddingów to od kilkunastu (zmierzone u użytkownika: ok. 20
        #       fragmentów/s) do setek fragmentów na sekundę - przy 20 MB to minuty albo
        #       kwadrans, więc trzeba widzieć, ile jeszcze. Pierwsze 3 s pomijamy - wtedy
        #       Ollama dopiero wczytuje model i prędkość byłaby zaniżona.
        uplynelo = time.monotonic() - z.get("_start", time.monotonic())
        z["eta"] = uplynelo / gotowe * (wszystkie - gotowe) if gotowe and uplynelo > 3 else None
        etykieta = self._etykiety_zalacznikow.get(id(z))
        if etykieta is not None:
            # WHY: szerokość tylko rośnie, mierzona czcionką już ze stylu - inaczej
            #      rosnący procent i zmieniający się czas były ucinane albo "skakały"
            etykieta.setText(self._opis_zalacznika(z))
            etykieta.setMinimumWidth(max(etykieta.minimumWidth(), etykieta.fontMetrics().horizontalAdvance(
                self._opis_zalacznika(dict(z, procent=100))) + 6, etykieta.sizeHint().width()))

    def _dokument_gotowy(self, z, ok, wynik):
        z.pop("_watek", None)
        if z not in self._zalaczniki:
            return  # WHY: usunięty w trakcie przygotowania
        if not ok:
            self._zalaczniki.remove(z)
            self._wyslij_po_stronach = False
            self._uwaga_czatu(_("{nazwa}: nie udało się przygotować do wyszukiwania - {blad}").format(
                nazwa=z["nazwa"], blad=wynik), "warn")
        else:
            z.update(wynik, gotowy=True)
            self._uwaga_czatu("")
            # WHY: przy jednym modelu w pamięci (OLLAMA_MAX_LOADED_MODELS=1) każde pytanie
            #      wyrzuca model czatu na rzecz modelu embeddingów - i wczytuje go od nowa
            env = (self._stan or {}).get("env_ollama", {})
            if (urlparse(z["adres_emb"]).hostname in ("localhost", "127.0.0.1")
                    and env.get("OLLAMA_MAX_LOADED_MODELS") == "1"):
                self._uwaga_czatu(_("Wskazówka: Ollama trzyma w pamięci tylko 1 model (OLLAMA_MAX_LOADED_MODELS=1), "
                                    "więc przy każdym pytaniu model czatu ustąpi miejsca modelowi embeddingów i "
                                    "wczyta się od nowa. Ustaw 2 w \"Ustawieniach\", jeśli starczy pamięci."), "warn")
        self._pokaz_zalaczniki()
        # WHY: pytanie z linkiem do dużej strony czekało na jej przygotowanie - teraz idzie samo
        if (ok and self._wyslij_po_stronach
                and not any(zz["typ"] == "dokument" and not zz.get("gotowy") for zz in self._zalaczniki)):
            self._wyslij_po_stronach = False
            self.wyslij_czat()

    def _opis_zalacznika(self, z):
        if z["typ"] == "obraz":
            opis = _("zdjęcie")
        elif z["typ"] == "do_odczytu":
            opis = _("odczytuję tekst...")
        else:
            opis = f"{max(1, z['rozmiar'] // 1024)} KB"
        if z["typ"] == "dokument":
            opis += "  ·  " + (_("wyszukiwanie fragmentów") if z.get("gotowy")
                               else _("przygotowuję {n}%").format(n=z.get("procent", 0)))
            eta = z.get("eta")
            if not z.get("gotowy") and eta is not None:
                opis += "  ·  " + (_("ok. {n} min").format(n=round(eta / 60)) if eta >= 90
                                   else _("ok. {n} s").format(n=max(1, round(eta))))
        return f"{z['nazwa']}  ·  {opis}"

    def _usun_zalacznik(self, z):
        if hasattr(z.get("_watek"), "anuluj"):
            z["_watek"].anuluj()  # WHY: przygotowanie do wyszukiwania; odczytu tekstu się nie przerywa
        if z in self._zalaczniki:
            self._zalaczniki.remove(z)
        self._uwaga_czatu("")
        self._pokaz_zalaczniki()
        self._odswiez_modele_czatu()

    def _uwaga_czatu(self, tekst, stan="warn"):
        self.lbl_czat_uwaga.setText(tekst)
        self.lbl_czat_uwaga.setProperty("state", stan)
        _odswiez_styl(self.lbl_czat_uwaga)
        self.lbl_czat_uwaga.setVisible(bool(tekst))

    def _pokaz_zalaczniki(self):
        # WHAT: "chipy" z nazwami załączników nad polem, każdy z krzyżykiem do usunięcia.
        self._etykiety_zalacznikow = {}
        while self.rzad_zalacznikow.count():
            element = self.rzad_zalacznikow.takeAt(0)
            chip = element.widget()
            if chip:
                # WHY: od razu zdjęty z okna - samo deleteLater zostawiało go na chwilę
                #      jako luźną ramkę na rozmowie
                chip.setParent(None)
                chip.deleteLater()
        for z in self._zalaczniki:
            chip = QFrame()
            chip.setObjectName("chatChip")
            h = QHBoxLayout(chip)
            h.setContentsMargins(10, 4, 4, 4)
            h.setSpacing(4)
            etykieta = QLabel(self._opis_zalacznika(z))
            self._etykiety_zalacznikow[id(z)] = etykieta
            h.addWidget(etykieta)
            usun = QPushButton()
            usun.setObjectName("iconButton")
            usun.setIcon(ikona("zamknij", PALETA.wyciszony, rozmiar=14, grubosc=2))
            usun.setFixedSize(24, 24)
            usun.setToolTip(_("Usuń załącznik"))
            usun.clicked.connect(lambda _c, zz=z: self._usun_zalacznik(zz))
            h.addWidget(usun)
            self.rzad_zalacznikow.addWidget(chip)
        self.rzad_zalacznikow.addStretch(1)

    @staticmethod
    def _umie_rozmawiac(model, ze_zdjeciem=False):
        # WHAT: czy model obsługuje czat - możliwość "completion" z /api/show.
        # WHY:  modele bez niej (embeddingi, rerankery, generatory obrazów) i tak
        #       zwróciłyby błąd. Gdy Ollama nie podała możliwości (starsza wersja),
        #       odrzucamy tylko embeddingi, jak wcześniej.
        # WHY:  modele OCR (glm-ocr, deepseek-ocr, olmOCR) mają "completion", ale służą
        #       do odczytu tekstu z obrazów - w rozmowie powtarzają pytanie (sprawdzone
        #       na glm-ocr). Ollama nie ma flagi "model specjalistyczny", więc po nazwie
        #       (zgłoszenie użytkownika).
        #       Ze zdjęciem w załącznikach wracają - to właśnie ich zadanie.
        if "ocr" in model["nazwa"].lower() and not ze_zdjeciem:
            return False
        if model.get("mozliwosci"):
            return "completion" in model["mozliwosci"]
        return model["rodzaj"] != "embedding"

    def _modele_czatu(self):
        # WHAT: (model, host) do wyboru - tylko modele umiejące rozmawiać;
        #       tylko hosty online, bo tylko takie zbiera zbierz_modele.
        ze_zdjeciem = any(z["typ"] == "obraz" for z in getattr(self, "_zalaczniki", []))
        return [(m, h) for m in self._modele if self._umie_rozmawiac(m, ze_zdjeciem) for h in m["hosty"]]

    def _odswiez_modele_czatu(self):
        if not hasattr(self, "combo_czat_model"):
            return
        pary = self._modele_czatu()
        biezacy = self.combo_czat_model.currentData() or _ustawienia().value("czat/model", "")
        kilka_hostow = len({h["adres"] for _m, h in pary}) > 1
        self.combo_czat_model.blockSignals(True)
        self.combo_czat_model.clear()
        for m, h in pary:
            # WHY: przy kilku hostach ten sam model jest kilka razy - nazwa hosta je rozróżnia
            tekst = _skroc_nazwe_modelu_hf(m["nazwa"]) + (f"  ·  {h['nazwa']}" if kilka_hostow else "")
            self.combo_czat_model.addItem(tekst, f"{h['adres']}|{m['nazwa']}")
            self.combo_czat_model.setItemData(self.combo_czat_model.count() - 1,
                                              f"{m['nazwa']} → {h['nazwa']}", Qt.ItemDataRole.ToolTipRole)
        indeks = self.combo_czat_model.findData(biezacy)
        self.combo_czat_model.setCurrentIndex(max(0, indeks))
        self.combo_czat_model.blockSignals(False)
        self._zmiana_modelu_czatu(zapisz=False)
        if not self._czat:
            self._pokaz_czat()  # WHY: tekst pustej rozmowy zależy od tego, czy są modele

    def _wybrany_model_czatu(self):
        dane = self.combo_czat_model.currentData()
        if not dane:
            return None, None
        adres, nazwa = dane.split("|", 1)
        model = next((m for m in self._modele if m["nazwa"] == nazwa), None)
        host = next((h for h in (model or {}).get("hosty", []) if h["adres"] == adres), None)
        return model, host

    def _odswiez_modele_rag(self):
        # WHAT: lista modeli embeddingów w Ustawieniach ("Automatycznie" + model · host).
        if not hasattr(self, "combo_model_rag"):
            return
        modele = self._modele_embeddingow()
        wybrany = _ustawienia().value("czat/model_rag", "")
        self.combo_model_rag.blockSignals(True)
        self.combo_model_rag.clear()
        self.combo_model_rag.addItem(_("Automatycznie"), "")
        for nazwa, adres, nazwa_hosta in modele:
            self.combo_model_rag.addItem(f"{nazwa}  ·  {nazwa_hosta}", f"{adres}|{nazwa}")
        if wybrany and self.combo_model_rag.findData(wybrany) < 0:
            # WHY: wybrany model jest na hoście offline - nie gubimy wyboru, a czat
            #      do czasu powrotu hosta działa jak "Automatycznie"
            nazwa = wybrany.split("|", 1)[1]
            self.combo_model_rag.addItem(_("{model} (host niedostępny)").format(model=nazwa), wybrany)
        self.combo_model_rag.setCurrentIndex(max(0, self.combo_model_rag.findData(wybrany)))
        self.combo_model_rag.blockSignals(False)
        self.ramka_rag_brak.setVisible(not modele and bool(self.serwery))
        self.btn_pobierz_rag.setEnabled(self._pobieranie is None)

    def _zmiana_modelu_rag(self, *_a):
        _ustawienia().setValue("czat/model_rag", self.combo_model_rag.currentData() or "")

    def _pobierz_model_rag(self):
        # WHAT: pobiera zalecany model embeddingów na host modelu czatu (albo pierwszy z listy)
        #       i przechodzi na ekran Modele, gdzie widać postęp pobierania.
        _model, host = self._wybrany_model_czatu()
        if host:
            adres, nazwa_hosta = host["adres"], host["nazwa"]
        else:
            adres, nazwa_hosta = self.serwery[0]["adres"], self.serwery[0]["nazwa"]
        self._rozpocznij_pobieranie(RAG_ZALECANY_MODEL, adres, nazwa_hosta)
        self._przejdz("modele")

    def _zmiana_modelu_czatu(self, *_a, zapisz=True):
        model, host = self._wybrany_model_czatu()
        if zapisz and self.combo_czat_model.currentData():
            _ustawienia().setValue("czat/model", self.combo_czat_model.currentData())
        if not model:
            self.lbl_czat_model.setText("")
            self._pokaz_tagi_modelu([])
        else:
            czesci = [f"{model['rozmiar'] / 1024 ** 3:.1f} GB"]
            if model.get("parametry"):
                czesci.append(model["parametry"])
            if model.get("kwantyzacja"):
                czesci.append(model["kwantyzacja"])
            czesci.append(host["nazwa"])
            self.lbl_czat_model.setText(" · ".join(czesci))
            self.lbl_czat_model.setToolTip(_("na hoście: {host}").format(host=host["nazwa"]))
            mozliwosci = model.get("mozliwosci", [])
            tagi = [(_("w pamięci"), _("model jest już wczytany - odpowie od razu"), "ok") if host["w_pamieci"]
                    else (_("na dysku"), _("wczyta się do pamięci przy pierwszym pytaniu"), "")]
            for klucz, nazwa, opis in (
                    ("vision", _("zdjęcia"), _("widzi dołączone zdjęcia i zrzuty ekranu")),
                    ("thinking", _("myślenie"), _("potrafi rozważyć odpowiedź przed jej napisaniem")),
                    ("tools", _("narzędzia"), _("umie wywoływać narzędzia - przyda się np. w Continue"))):
                if klucz in mozliwosci:
                    tagi.append((nazwa, opis, ""))
            self._pokaz_tagi_modelu(tagi)
        if hasattr(self, "sw_czat_myslenie"):
            self._odswiez_przelaczniki_myslenia()
        self._odswiez_przycisk_czatu()

    @staticmethod
    def _widzi_obrazy(model):
        return "vision" in model.get("mozliwosci", [])

    def _pokaz_tagi_modelu(self, tagi):
        # WHAT: tagi [(napis, dymek, stan)] w wierszach - nowy wiersz, gdy kolejny się nie mieści.
        # WHY:  Qt nie ma układu "przepływowego"; przy dłuższych tłumaczeniach (niemiecki)
        #       cztery tagi nie mieszczą się w jednej linii wąskiej karty.
        while self.uklad_tagow_modelu.count():
            wiersz = self.uklad_tagow_modelu.takeAt(0).layout()
            while wiersz and wiersz.count():
                tag = wiersz.takeAt(0).widget()
                if tag:
                    tag.setParent(None)
                    tag.deleteLater()
        szerokosc = 250  # WHY: szerokość treści prawej karty (290 px minus marginesy)
        wiersz, zajete = None, 0
        for napis, opis, stan in tagi:
            # WHY: od razu w karcie - styl tagu ma selektor z rodzicem (QFrame#card ...), a bez
            #      niego sizeHint liczyłby szerokość bez odstępów i tag byłby ucinany
            tag = QLabel(napis, self.ramka_tagow_modelu)
            tag.setObjectName("capTag")
            tag.setProperty("state", stan)
            tag.setToolTip(opis)
            tag.ensurePolished()
            w = tag.sizeHint().width()
            tag.setMinimumWidth(w)
            if wiersz is None or zajete + w > szerokosc:
                wiersz = QHBoxLayout()
                wiersz.setSpacing(4)
                wiersz.addStretch(1)
                self.uklad_tagow_modelu.addLayout(wiersz)
                zajete = 0
            wiersz.insertWidget(wiersz.count() - 1, tag)
            zajete += w + 4
        self.ramka_tagow_modelu.setVisible(bool(tagi))

    def _przelacz_myslenie(self, wlacz):
        _ustawienia().setValue("czat/myslenie", wlacz)
        self._odswiez_przelaczniki_myslenia()

    def _odswiez_przelaczniki_myslenia(self):
        # WHY: przełączniki mają sens tylko dla modeli z możliwością "thinking"; "pokazuj
        #      rozumowanie" także tylko wtedy, gdy model w ogóle myśli
        model, _host = self._wybrany_model_czatu()
        mysli = bool(model) and "thinking" in model.get("mozliwosci", [])
        # WHY: przy modelu, który nie myśli, oba wyglądają na wyłączone (wyszarzony, ale
        #      "włączony" przełącznik mylił); zapisany wybór wraca przy modelu, który myśli
        wlaczone = _ustawienia().value("czat/myslenie", True, type=bool)
        self.sw_czat_myslenie.ustaw_cicho(mysli and wlaczone)
        self.sw_czat_rozumowanie.ustaw_cicho(mysli and self.widok_czatu.pokaz_myslenie)
        for sw, lbl, aktywny in ((self.sw_czat_myslenie, self.lbl_czat_myslenie, mysli),
                                 (self.sw_czat_rozumowanie, self.lbl_czat_rozumowanie, mysli and wlaczone)):
            sw.setEnabled(aktywny)
            lbl.setEnabled(aktywny)
        if not mysli:
            self.lbl_czat_myslenie.setToolTip(_("ten model nie myśli przed odpowiedzią"))

    def _parametr_myslenia(self, model):
        # WHAT: wartość "think" do /api/chat albo None (nie wysyłać wcale).
        # WHY:  tylko dla modeli z "thinking" - innym Ollama mogłaby odpowiedzieć błędem.
        #       gpt-oss nie umie myśleć wcale - zna tylko poziomy low/medium/high, więc
        #       "wyłączone" = "low" (wg dokumentacji Ollamy; NIE sprawdzone na żywo).
        if "thinking" not in model.get("mozliwosci", []):
            return None
        wlaczone = _ustawienia().value("czat/myslenie", True, type=bool)
        if model["nazwa"].startswith("gpt-oss"):
            return "medium" if wlaczone else "low"
        return wlaczone

    def _przelacz_rozumowanie(self, wlacz):
        _ustawienia().setValue("czat/rozumowanie", wlacz)
        self.widok_czatu.pokaz_myslenie = wlacz
        self._pokaz_czat()

    def _pokaz_czat(self, do_dolu=False):
        if not hasattr(self, "widok_czatu"):
            return
        if self._modele_czatu():
            pusty = _("Wybierz model po prawej i zadaj pytanie.")
        else:
            pusty = _("Brak modeli do rozmowy na hostach online. Pobierz model na ekranie \"Modele\".")
        self.widok_czatu.pokaz(self._czat, pusty, do_dolu)

    def _odswiez_przycisk_czatu(self):
        if not hasattr(self, "btn_czat_wyslij"):
            return
        pisze = self._czat_worker is not None
        self.btn_czat_wyslij.setText(_("Zatrzymaj") if pisze else _("Wyślij"))
        self.btn_czat_wyslij.setObjectName("danger" if pisze else "primary")
        _odswiez_styl(self.btn_czat_wyslij)
        self.btn_czat_wyslij.setEnabled(pisze or self.combo_czat_model.currentData() is not None)

    def nowa_rozmowa(self):
        if self._czat_worker is not None:
            self._czat_worker.anuluj()
        self._czat = []
        self._czat_id = None  # WHY: nowy plik powstanie przy pierwszej odpowiedzi
        self._czat_tytul = None
        self._pokaz_czat()
        self._pokaz_kontekst()
        self._wypelnij_liste_rozmow()
        self.pole_czat.setFocus()

    # --- zapisane rozmowy ------------------------------------------------------
    def _wypelnij_liste_rozmow(self):
        # WHAT: lista rozmów po lewej; za pierwszym razem czyta pliki w tle.
        # WHY:  pliki ze zdjęciami potrafią ważyć kilka MB - czytanie ich w wątku
        #       okna przy kilkudziesięciu rozmowach przycinałoby ekran.
        if not hasattr(self, "lista_rozmow"):
            return
        if self._rozmowy is None:
            if not self._w_toku("rozmowy"):
                worker = FunkcjaWorker(lista_rozmow)
                self._uruchom_watek("rozmowy", worker, worker.zakonczono, self._po_liscie_rozmow)
            return
        self.lista_rozmow.clear()
        for r in self._rozmowy:
            data = time.strftime("%d.%m.%Y %H:%M", time.localtime(r["zmieniono"]))
            # WHY: sama nazwa bez tagu ("gemma4") - pełna "gemma4:26b-a4b-it-q8_0" zawijała się
            #      na wąskiej liście w dwie linie; pełną widać w otwartej rozmowie
            model = _skroc_nazwe_modelu_hf(r["model"]).split(":")[0] if r["model"] else ""
            element = QListWidgetItem(f"{r['tytul']}\n{data}" + (f"  ·  {model}" if model else ""))
            element.setData(Qt.ItemDataRole.UserRole, r["id"])
            element.setToolTip(r["tytul"])
            self.lista_rozmow.addItem(element)
            if r["id"] == self._czat_id:
                element.setSelected(True)
        self._filtruj_rozmowy(self.pole_szukaj_rozmow.text())

    def _po_liscie_rozmow(self, ok, wynik):
        self._rozmowy = wynik if ok else []
        if not ok:
            self.wpis_log(_("Nie udało się wczytać zapisanych rozmów: {blad}").format(blad=wynik), "BŁĄD")
        self._wypelnij_liste_rozmow()

    def _filtruj_rozmowy(self, tekst):
        # WHAT: ukrywa rozmowy, w których tytule i treści nie ma wpisanego tekstu.
        szukane = tekst.strip().lower()
        wg_id = {r["id"]: r for r in self._rozmowy or []}
        for i in range(self.lista_rozmow.count()):
            element = self.lista_rozmow.item(i)
            r = wg_id.get(element.data(Qt.ItemDataRole.UserRole))
            element.setHidden(bool(szukane) and not (r and szukane in r["szukaj"]))

    def _zapisz_biezaca_rozmowe(self):
        # WHAT: zapis po każdej odpowiedzi (także przerwanej i z błędem).
        if not self._czat:
            return
        if self._czat_id is None:
            self._czat_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            self._czat_utworzono = time.time()
        model, host = self._wybrany_model_czatu()
        try:
            wpis = zapisz_rozmowe(self._czat_id, self._czat_utworzono, self._czat,
                                  model["nazwa"] if model else "", host["adres"] if host else "", self._czat_tytul)
        except OSError as e:
            self._uwaga_czatu(_("Nie udało się zapisać rozmowy: {blad}").format(blad=e), "warn")
            return
        if self._rozmowy is not None:
            self._rozmowy = [wpis] + [r for r in self._rozmowy if r["id"] != self._czat_id]
        self._wypelnij_liste_rozmow()

    def _otworz_rozmowe(self, id_rozmowy):
        if id_rozmowy == self._czat_id:
            return
        if self._czat_worker is not None:
            # WHY: odpowiedź jeszcze się pisze - podmiana rozmowy w trakcie zgubiłaby ją
            self._uwaga_czatu(_("Poczekaj na koniec odpowiedzi albo ją zatrzymaj."), "warn")
            self._wypelnij_liste_rozmow()
            return
        try:
            dane = wczytaj_rozmowe(id_rozmowy)
        except (OSError, ValueError) as e:
            self._uwaga_czatu(_("Nie udało się otworzyć rozmowy: {blad}").format(blad=e), "warn")
            return
        self._czat = dane.get("wiadomosci", [])
        self._czat_id = id_rozmowy
        self._czat_utworzono = dane.get("utworzono") or time.time()
        self._czat_tytul = dane.get("tytul") if dane.get("tytul_wlasny") else None
        self._uwaga_czatu("")
        # WHY: dalej rozmawiamy tym samym modelem - o ile jest jeszcze na liście
        indeks = self.combo_czat_model.findData(f"{dane.get('host', '')}|{dane.get('model', '')}")
        if indeks >= 0:
            self.combo_czat_model.setCurrentIndex(indeks)
        self._pokaz_czat(do_dolu=True)
        self._pokaz_kontekst()

    def _zaznaczona_rozmowa(self):
        element = self.lista_rozmow.currentItem()
        if element is None or not element.isSelected():
            return None
        return element.data(Qt.ItemDataRole.UserRole)

    def _zmien_nazwe_rozmowy(self):
        id_rozmowy = self._zaznaczona_rozmowa()
        if id_rozmowy is None:
            return
        wpis = next((r for r in self._rozmowy or [] if r["id"] == id_rozmowy), None)
        tytul, ok = QInputDialog.getText(
            self, _("Zmień nazwę"), _("Nazwa rozmowy (puste pole = tytuł z pierwszego pytania):"),
            text=wpis["tytul"] if wpis else "")
        if not ok:
            return
        try:
            nowy = zmien_nazwe_rozmowy(id_rozmowy, tytul.strip())
        except (OSError, ValueError) as e:
            self._uwaga_czatu(_("Nie udało się zmienić nazwy: {blad}").format(blad=e), "warn")
            return
        # WHY: kolejność listy bez zmian - zmiana nazwy to nie nowa wiadomość
        nowy["zmieniono"] = wpis["zmieniono"] if wpis else nowy["zmieniono"]
        self._rozmowy = [nowy if r["id"] == id_rozmowy else r for r in self._rozmowy or []]
        if id_rozmowy == self._czat_id:
            self._czat_tytul = tytul.strip() or None
        self._wypelnij_liste_rozmow()

    def _eksportuj_rozmowe(self):
        id_rozmowy = self._zaznaczona_rozmowa()
        if id_rozmowy is None:
            return
        try:
            dane = wczytaj_rozmowe(id_rozmowy)
        except (OSError, ValueError) as e:
            self._uwaga_czatu(_("Nie udało się otworzyć rozmowy: {blad}").format(blad=e), "warn")
            return
        # WHY: nazwa pliku z tytułu, bez znaków, których system plików nie lubi
        nazwa = re.sub(r"[\\/:*?\"<>|\n]+", " ", dane.get("tytul", "")).strip()[:60] or _("Rozmowa")
        plik, _f = QFileDialog.getSaveFileName(self, _("Eksportuj do Markdown..."),
                                               str(Path.home() / f"{nazwa}.md"), "Markdown (*.md)")
        if not plik:
            return
        if not plik.endswith(".md"):
            plik += ".md"
        try:
            Path(plik).write_text(rozmowa_do_markdown(dane), encoding="utf-8")
        except OSError as e:
            self._uwaga_czatu(_("Nie udało się zapisać pliku: {blad}").format(blad=e), "warn")
            return
        self.wpis_log(_("Rozmowa zapisana do pliku: {plik}").format(plik=plik))

    def _menu_rozmowy(self, punkt):
        element = self.lista_rozmow.itemAt(punkt)
        if element is None:
            return
        self.lista_rozmow.setCurrentItem(element)
        menu = QMenu(self)
        menu.addAction(_("Zmień nazwę"), self._zmien_nazwe_rozmowy)
        menu.addAction(_("Eksportuj do Markdown..."), self._eksportuj_rozmowe)
        menu.addSeparator()
        menu.addAction(_("Usuń rozmowę"), self._usun_rozmowe)
        menu.exec(self.lista_rozmow.viewport().mapToGlobal(punkt))

    def _usun_rozmowe(self):
        element = self.lista_rozmow.currentItem()
        if element is None:
            return
        id_rozmowy = element.data(Qt.ItemDataRole.UserRole)
        odp = QMessageBox.question(self, _("Usuń rozmowę"),
                                   _("Usunąć rozmowę \"{tytul}\"? Tego nie da się cofnąć.").format(
                                       tytul=element.toolTip()))
        if odp != QMessageBox.StandardButton.Yes:
            return
        try:
            (katalog_rozmow() / f"{id_rozmowy}.json").unlink(missing_ok=True)
        except OSError as e:
            self._uwaga_czatu(_("Nie udało się usunąć rozmowy: {blad}").format(blad=e), "warn")
            return
        self._rozmowy = [r for r in self._rozmowy or [] if r["id"] != id_rozmowy]
        if id_rozmowy == self._czat_id:
            self.nowa_rozmowa()
        else:
            self._wypelnij_liste_rozmow()

    def wyslij_czat(self):
        # WHAT: ten sam przycisk wysyła albo przerywa pisanie odpowiedzi.
        if self._czat_worker is not None:
            self._czat_worker.anuluj()
            return
        tekst = self.pole_czat.toPlainText().strip()
        model, host = self._wybrany_model_czatu()
        if not (tekst or self._zalaczniki) or not model:
            return
        if self._w_toku("strony"):
            return  # WHY: strony jeszcze się pobierają - pytanie pójdzie samo, gdy skończą
        # WHAT: linki w pytaniu -> najpierw pobranie stron jako załączników, potem wysyłka.
        # WHY:  pominięte linki już dołączone i te, których nie udało się pobrać (inaczej
        #       ponowne "Wyślij" próbowałoby je w kółko)
        dolaczone = {z.get("url") for z in self._zalaczniki}
        nowe = [l for l in linki_w_tekscie(tekst) if l not in dolaczone and l not in self._linki_nieudane]
        if nowe:
            self._pobierz_strony(nowe)
            return
        if any(z["typ"] == "do_odczytu" for z in self._zalaczniki):
            self._uwaga_czatu(_("Poczekaj chwilę - odczytuję tekst z dołączonego dokumentu."), "warn")
            return
        if any(z["typ"] == "dokument" and not z.get("gotowy") for z in self._zalaczniki):
            self._uwaga_czatu(_("Poczekaj chwilę - duży plik jest jeszcze przygotowywany do wyszukiwania."), "warn")
            return
        if any(z["typ"] == "obraz" for z in self._zalaczniki) and not self._widzi_obrazy(model):
            self._uwaga_czatu(_("{model} nie widzi zdjęć. Wybierz model z tagiem \"zdjęcia\" "
                                "(np. gemma3, qwen2.5vl, llava).").format(model=_skroc_nazwe_modelu_hf(model["nazwa"])),
                              "warn")
            return
        self._czat.append({"rola": "user", "tresc": tekst, "zalaczniki": list(self._zalaczniki)})
        self._zalaczniki = []
        self._linki_nieudane = set()
        self._pokaz_zalaczniki()
        self._uwaga_czatu("")
        self.pole_czat.clear()
        self._zadaj_pytanie(model, host)

    def _zadaj_pytanie(self, model, host):
        # WHAT: wysyła modelowi rozmowę kończącą się pytaniem użytkownika i pisze odpowiedź.
        # WHY:  osobno od wyslij_czat - tę samą drogę wybiera "Wygeneruj ponownie".
        # WHY: historia dla modelu - bez błędów i bez rozumowania (model go nie potrzebuje,
        #      a zjadałoby kontekst); zdjęcia zostają przy swoich pytaniach, żeby model
        #      pamiętał je w dalszej rozmowie ("a co jest w lewym rogu?")
        historia = []
        for w in self._czat:
            if w["rola"] == "user":
                wpis = {"role": "user", "content": _tresc_dla_modelu(w)}
                obrazy = [z["b64"] for z in w.get("zalaczniki", []) if z["typ"] == "obraz"]
                if obrazy:
                    wpis["images"] = obrazy
                historia.append(wpis)
            elif w["rola"] == "assistant" and not w.get("przerwano"):
                historia.append({"role": "assistant", "content": _rozdziel_myslenie(w["tresc"])[1]})
        wyszukiwanie = None
        if self._szukanie_aktywne():
            # WHY: do zapytań sam tekst pytania - bez treści dołączonych plików
            wyszukiwanie = {"zrodlo": wyszukiwanie_ustawienie("zrodlo", "wylaczone"),
                            "pytanie": self._czat[-1].get("tresc", ""), "emb": self._model_embeddingow()}
        self._czat.append({"rola": "assistant", "kto": _skroc_nazwe_modelu_hf(model["nazwa"]),
                           "tresc": "", "myslenie": "", "trwa": True, "start": time.monotonic(),
                           "gb": model["rozmiar"] / 1024 ** 3})
        if wyszukiwanie:
            self._czat[-1]["etap"] = _("Układam zapytania do wyszukiwarki...")
        self._pokaz_czat(do_dolu=True)
        # WHY: duże pliki z CAŁEJ rozmowy - można dopytywać o dokument dołączony wcześniej
        dokumenty = [{"nazwa": z["nazwa"], "fragmenty": z["fragmenty"], "wektory": z["wektory"],
                      "model_emb": z["model_emb"], "adres_emb": z["adres_emb"]}
                     for w in self._czat if w["rola"] == "user"
                     for z in w.get("zalaczniki", []) if z["typ"] == "dokument" and z.get("gotowy")]
        if dokumenty:
            self._czat[-1]["szuka"] = True
        worker = CzatWorker(host["adres"], model["nazwa"], historia, dokumenty, self._parametr_myslenia(model),
                            wyszukiwanie)
        self._czat_worker = worker
        self._czat_odswiezenie_zaplanowane = False
        worker.fragment.connect(self._czat_fragment)
        worker.ladowanie.connect(self._czat_ladowanie)
        worker.zrodla.connect(self._czat_zrodla)
        worker.etap.connect(self._czat_etap)
        worker.zrodla_www.connect(self._czat_zrodla_www)
        worker.zakonczono.connect(self._czat_koniec)
        # WHY: co sekundę odświeżamy licznik czekania, dopóki nie przyjdzie pierwsze słowo
        if not hasattr(self, "_czat_zegar"):
            self._czat_zegar = QTimer(self)
            self._czat_zegar.setInterval(1000)
            self._czat_zegar.timeout.connect(self._czat_tyk_czekania)
        self._czat_zegar.start()
        worker.start()
        self._odswiez_przycisk_czatu()

    def _akcja_czatu(self, akcja):
        # WHAT: ikony w rozmowie - "ponow" (ostatnia odpowiedź od nowa, wybranym teraz modelem)
        #       i "popraw" (ostatnie pytanie wraca do pola, ono i odpowiedź znikają z rozmowy).
        # WHY:  nieudana odpowiedź nie zostaje w historii - model by się nią sugerował, zjada
        #       kontekst i trafia do zapisu i eksportu.
        if self._czat_worker is not None or len(self._czat) < 2 or self._czat[-2]["rola"] != "user":
            return
        if akcja == "ponow":
            model, host = self._wybrany_model_czatu()
            if not model:
                return
            pytanie = self._czat[-2]
            if any(z["typ"] == "obraz" for z in pytanie.get("zalaczniki", [])) and not self._widzi_obrazy(model):
                self._uwaga_czatu(_("{model} nie widzi zdjęć. Wybierz model z tagiem \"zdjęcia\" "
                                    "(np. gemma3, qwen2.5vl, llava).").format(
                                        model=_skroc_nazwe_modelu_hf(model["nazwa"])), "warn")
                return
            self._czat.pop()
            self._uwaga_czatu("")
            self._zadaj_pytanie(model, host)
        elif akcja == "popraw":
            self._czat.pop()
            pytanie = self._czat.pop()
            # WHY: załączniki wracają razem z tekstem (duże pliki bez wektorów - z zapisanej
            #      rozmowy - trzeba dołączyć ponownie, więc ich nie przywracamy)
            self._zalaczniki = [z for z in pytanie.get("zalaczniki", []) if not z.get("bez_wyszukiwania")]
            self.pole_czat.setPlainText(pytanie.get("tresc", ""))
            self.pole_czat.setFocus()
            self.pole_czat.moveCursor(QTextCursor.MoveOperation.End)
            self._pokaz_zalaczniki()
            self._pokaz_czat()
            self._pokaz_kontekst()
            if self._czat:
                self._zapisz_biezaca_rozmowe()

    def _pobierz_strony(self, linki):
        self._uwaga_czatu(_("Pobieram: {linki}...").format(linki=", ".join(_nazwa_strony(l) for l in linki)), "info")
        self._wyslij_po_stronach = True
        self.btn_czat_wyslij.setEnabled(False)

        def _pobierz():
            # WHY: każda strona osobno - błąd jednej nie przekreśla pozostałych
            wyniki = []
            for link in linki:
                try:
                    wyniki.append((link, pobierz_strone(link), None))
                except ValueError as e:
                    wyniki.append((link, None, str(e)))
            return wyniki

        worker = FunkcjaWorker(_pobierz)
        self._uruchom_watek("strony", worker, worker.zakonczono, self._strony_pobrane)

    def _strony_pobrane(self, ok, wyniki):
        self.btn_czat_wyslij.setEnabled(True)
        if not ok:
            wyniki = []
        bledy = []
        for link, z, blad in wyniki:
            if z is None:
                self._linki_nieudane.add(link)
                bledy.append(blad)
                continue
            if z["typ"] == "dokument" and not self._przygotuj_dokument(z):
                self._linki_nieudane.add(link)
                bledy.append(self.lbl_czat_uwaga.text())
                continue
            self._zalaczniki.append(z)
        self._pokaz_zalaczniki()
        if bledy:
            # WHY: przy błędzie pytania nie wysyłamy samo - użytkownik decyduje, czy bez strony
            self._wyslij_po_stronach = False
            self._uwaga_czatu("\n".join(bledy) + "\n" + _("\"Wyślij\" wyśle pytanie bez tej strony."), "warn")
            return
        self._uwaga_czatu("")
        if not any(z["typ"] == "dokument" and not z.get("gotowy") for z in self._zalaczniki):
            self._wyslij_po_stronach = False
            self.wyslij_czat()
        # WHY: duża strona przygotowuje się do wyszukiwania - wyśle ją _dokument_gotowy

    def _czat_zrodla(self, zrodla):
        if self.sender() is self._czat_worker and self._czat:
            self._czat[-1]["szuka"] = False
            self._czat[-1]["zrodla"] = zrodla
            self._czat_odswiez_ostatnia()

    def _czat_etap(self, tekst):
        if self.sender() is self._czat_worker and self._czat:
            self._czat[-1]["etap"] = tekst
            self._czat_odswiez_ostatnia()

    def _czat_zrodla_www(self, zrodla):
        if self.sender() is self._czat_worker and self._czat:
            self._czat[-1].pop("etap", None)
            self._czat[-1]["zrodla_www"] = zrodla
            self._czat_odswiez_ostatnia()

    def _szukanie_aktywne(self):
        return self._szukaj_w_internecie and wyszukiwanie_ustawienie("zrodlo", "wylaczone") != "wylaczone"

    def _przelacz_szukanie_w_internecie(self, wlacz):
        self._szukaj_w_internecie = wlacz
        self._odswiez_przycisk_internetu()

    def _odswiez_przycisk_internetu(self):
        # WHAT: przycisk widać tylko przy wybranym źródle; dymek mówi, dokąd idą zapytania.
        if not hasattr(self, "btn_czat_internet"):
            return
        zrodlo = wyszukiwanie_ustawienie("zrodlo", "wylaczone")
        self.btn_czat_internet.setVisible(zrodlo != "wylaczone")
        dokad = {"searxng": _("SearXNG na tym komputerze"), "searxng_adres": _("SearXNG pod innym adresem"),
                 "ollama": _("Ollama (konto na ollama.com)")}.get(zrodlo, "")
        stan = _("włączone") if self._szukaj_w_internecie else _("wyłączone")
        self.btn_czat_internet.setToolTip(
            _("Szukaj w internecie: {stan}\nModel ułoży zapytania, program przeczyta znalezione strony, a odpowiedź "
              "poda źródła. Źródło wyników: {zrodlo} (zmienisz w Ustawieniach).").format(stan=stan, zrodlo=dokad))

    def _czat_ladowanie(self):
        if self.sender() is self._czat_worker and self._czat:
            self._czat[-1]["laduje"] = True
            self._czat_odswiez_ostatnia()

    def _czat_tyk_czekania(self):
        ostatnia = self._czat[-1] if self._czat else None
        if self._czat_worker is None or not ostatnia or not ostatnia.get("trwa"):
            self._czat_zegar.stop()
        elif not _rozdziel_myslenie(ostatnia["tresc"])[1].strip():
            self._czat_odswiez_ostatnia()

    def _czat_fragment(self, tresc, myslenie):
        if self.sender() is not self._czat_worker or not self._czat:
            return  # WHY: spóźniony kawałek z przerwanej odpowiedzi (np. po "Nowa rozmowa")
        ostatnia = self._czat[-1]
        ostatnia["tresc"] += tresc
        ostatnia["myslenie"] += myslenie
        # WHY: model sypie kilkadziesiąt kawałków na sekundę - widok odświeżamy
        #      najwyżej co 80 ms, a nie przy każdym słowie
        if not self._czat_odswiezenie_zaplanowane:
            self._czat_odswiezenie_zaplanowane = True
            QTimer.singleShot(80, self._czat_odswiez_ostatnia)

    def _czat_odswiez_ostatnia(self):
        self._czat_odswiezenie_zaplanowane = False
        # WHY: tylko odpowiedź w trakcie pisania - gotową narysował już _czat_koniec, razem
        #      z ikonami "ponów"/"popraw"; spóźnione odświeżenie (zaplanowane 80 ms po ostatnim
        #      kawałku) rysowało ją od nowa bez ikon i te losowo znikały
        if self._czat and hasattr(self, "widok_czatu") and self._czat[-1].get("trwa"):
            self.widok_czatu.odswiez_ostatnia(self._czat[-1])

    def _czat_koniec(self, sukces, wynik):
        if self.sender() is not self._czat_worker:
            return
        self._czat_worker = None
        ostatnia = self._czat[-1] if self._czat else None
        if ostatnia is not None and ostatnia.get("trwa"):
            ostatnia["trwa"] = False
            if not sukces:
                if ostatnia["tresc"].strip():
                    ostatnia["stat"] = _("błąd: {blad}").format(blad=wynik)
                else:
                    # WHY: surowy błąd + proste wyjaśnienie, jak na ekranie Dziennik
                    wyjasnienie = wyjasnij_blad(wynik)
                    ostatnia.update({"rola": "blad",
                                     "tresc": wynik + (f"\n\n{wyjasnienie}" if wyjasnienie else "")})
                self.wpis_log(_("Czat: błąd - {blad}").format(blad=wynik), "BŁĄD", "Ollama")
            elif wynik.get("anulowano"):
                ostatnia["stat"] = _("przerwano")
                ostatnia["przerwano"] = not ostatnia["tresc"].strip()
            else:
                ostatnia["stat"] = self._statystyki_czatu(wynik)
                # WHY: prompt_eval_count to CAŁA historia (sprawdzone: także gdy Ollama ma ją
                #      w pamięci podręcznej - ta część jest osobno w prompt_eval_cached_count)
                ostatnia["kontekst"] = [(wynik.get("prompt_eval_count") or 0) + (wynik.get("eval_count") or 0),
                                        wynik.get("okno_kontekstu")]
            self._pokaz_czat()
            self._pokaz_kontekst()
            self._zapisz_biezaca_rozmowe()
        self._odswiez_przycisk_czatu()
        self.odswiez_modele()  # WHY: model jest teraz w pamięci - widać to w opisie i na ekranie Modele

    def _pokaz_kontekst(self):
        # WHAT: "4 051 / 65 536 tokenów (6%)" z ostatniej odpowiedzi; od 80% ostrzeżenie,
        #       od 95% - model zaczyna zapominać początek rozmowy.
        if not hasattr(self, "lbl_kontekst"):
            return
        uzyte, okno = next((w["kontekst"] for w in reversed(self._czat) if w.get("kontekst")), (None, None))
        stan, rada = "", ""

        def liczba(n):
            return f"{n:,}".replace(",", "\u202f")  # WHY: wąska spacja co 3 cyfry - 65 536

        if uzyte is None:
            self.lbl_kontekst.setText("—")
            self.pasek_kontekstu.setValue(0)
        elif not okno:
            # WHY: host nie podał okna (np. starsza Ollama) - pokazujemy samą zajętość
            self.lbl_kontekst.setText(_("{n} tokenów").format(n=liczba(uzyte)))
            self.pasek_kontekstu.setValue(0)
        else:
            udzial = uzyte / okno
            # WHY: bez słowa "tokenów" - w wąskiej karcie liczba stoi w linii nagłówka (opis w dymku)
            self.lbl_kontekst.setText(_("{uzyte} / {okno} ({proc}%)").format(
                uzyte=liczba(uzyte), okno=liczba(okno), proc=min(100, round(100 * udzial))))
            self.pasek_kontekstu.setValue(min(1000, round(1000 * udzial)))
            if udzial >= 0.95:
                stan = "error"
                rada = _("Prawie pełne - model zapomina już początek rozmowy. Zacznij nową albo zwiększ "
                         "OLLAMA_CONTEXT_LENGTH w Ustawieniach.")
            elif udzial >= 0.8:
                stan = "warn"
                rada = _("Blisko limitu - wkrótce model zacznie zapominać początek rozmowy. Zacznij nową albo "
                         "zwiększ OLLAMA_CONTEXT_LENGTH w Ustawieniach.")
        self.pasek_kontekstu.setProperty("state", stan)
        _odswiez_styl(self.pasek_kontekstu)
        self.lbl_kontekst_rada.setText(rada)
        self.lbl_kontekst_rada.setProperty("state", stan)
        _odswiez_styl(self.lbl_kontekst_rada)
        self.lbl_kontekst_rada.setVisible(bool(rada))

    def _statystyki_czatu(self, d):
        # WHAT: tokeny/s i czasy z ostatniej linii strumienia Ollamy (czasy w nanosekundach).
        # WHY:  liczby podaje sama Ollama - bez własnego stopera, więc nie wlicza się
        #       w nie czas sieci ani rysowania okna.
        def na_sekunde(tokeny, ns):
            return tokeny / (ns / 1e9) if tokeny and ns else None
        gen = na_sekunde(d.get("eval_count"), d.get("eval_duration"))
        prompt = na_sekunde(d.get("prompt_eval_count"), d.get("prompt_eval_duration"))
        calosc = (d.get("total_duration") or 0) / 1e9
        st = self._lbl_stat_czatu
        # WHY: od 100 tok/s bez części dziesiętnej - "18382.4" nie mieściło się w wąskiej karcie,
        #      a przy takich liczbach ułamek i tak nic nie mówi
        def tok_s(n):
            return _("{n} tok/s").format(n=f"{n:.0f}" if n >= 100 else f"{n:.1f}") if n else "—"
        st["generowanie"].setText(tok_s(gen))
        st["prompt"].setText(tok_s(prompt))
        st["tokeny"].setText(f"{d.get('prompt_eval_count', 0)} / {d.get('eval_count', 0)}")
        st["calosc"].setText(f"{calosc:.1f} s")
        _odmaluj_rodzica(*st.values())
        czesci = []
        if gen:
            czesci.append(_("{n:.1f} tok/s").format(n=gen))
        if d.get("eval_count"):
            czesci.append(_("{n} tokenów").format(n=d["eval_count"]))
        czesci.append(f"{calosc:.1f} s")
        return " · ".join(czesci)

    # =========================================================================
    #  Ekran "Open WebUI" (makieta WebUI.dc.html)
    # =========================================================================
    def _strona_webui(self):
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(18)
        v.addLayout(self._tytul_strony(
            "Open WebUI", _("Czat w przeglądarce, podłączony do routera. Instalowany w osobnym środowisku "
                            "Pythona, bez Dockera.")))

        karta = QFrame()
        karta.setObjectName("card")
        h = QHBoxLayout(karta)
        h.setContentsMargins(24, 24, 24, 24)
        h.setSpacing(28)
        lewa = QVBoxLayout()
        lewa.setSpacing(16)
        lewa.addStretch(1)
        stan = QHBoxLayout()
        stan.setSpacing(10)
        self.kropka_webui = QLabel()
        self.kropka_webui.setObjectName("dotBig")
        self.kropka_webui.setFixedSize(10, 10)
        stan.addWidget(self.kropka_webui)
        self.lbl_webui_stan = QLabel("—")
        self.lbl_webui_stan.setObjectName("stateTitle")
        stan.addWidget(self.lbl_webui_stan)
        self.lbl_webui_adres = _etykieta("", "mono", muted="true")
        stan.addWidget(self.lbl_webui_adres)
        stan.addStretch(1)
        lewa.addLayout(stan)
        self.btn_webui_duzy = QPushButton(_("Otwórz Open WebUI"))
        self.btn_webui_duzy.setObjectName("launch")
        self.btn_webui_duzy.setIcon(ikona("otworz", PALETA.naAkcencie, rozmiar=20, grubosc=2.2))
        self.btn_webui_duzy.setIconSize(QSize(20, 20))
        self.btn_webui_duzy.setMaximumWidth(380)
        self.btn_webui_duzy.clicked.connect(self.otworz_webui)
        lewa.addWidget(self.btn_webui_duzy)
        przyciski = QHBoxLayout()
        przyciski.setSpacing(8)
        self.btn_webui_restart = self._przycisk(_("Uruchom ponownie"), slot=lambda: self.uruchom_webui(restart=True))
        przyciski.addWidget(self.btn_webui_restart)
        self.btn_webui_stop = self._przycisk(_("Zatrzymaj"), rola="danger", slot=lambda: self.przelacz_webui(False))
        przyciski.addWidget(self.btn_webui_stop)
        przyciski.addWidget(self._przycisk(_("Kopiuj adres"),
                                           slot=lambda: QApplication.clipboard().setText(_webui_url())))
        przyciski.addStretch(1)
        lewa.addLayout(przyciski)
        lewa.addStretch(1)
        h.addLayout(lewa, 1)
        h.addWidget(_miniatura_czatu(420, 250, duza=True))
        v.addWidget(karta)

        dol = QGridLayout()
        dol.setSpacing(18)
        v.addLayout(dol, 1)

        # --- Ustawienia ---
        karta, uk = self._karta()
        uk.setSpacing(0)
        tytul = _etykieta(_("Ustawienia"), "h2")
        tytul.setContentsMargins(0, 0, 0, 8)
        uk.addWidget(tytul)
        self.sw_webui_przegladarka = self._wiersz_przelacznika(
            uk, _("Otwieraj przeglądarkę po starcie"), _("przydatne, gdy komputer służy tylko do czatu"),
            lambda w: (webui_zapisz_ustawienie("otworz_po_starcie", w),
                       self.wpis_log(_("Zapisano ustawienie Open WebUI."))))
        self.sw_webui_przegladarka.setChecked(webui_ustawienie("otworz_po_starcie", False))
        self.sw_webui_logowanie = self._wiersz_przelacznika(
            uk, _("Wymagaj logowania"), _("włącz, jeśli z czatu korzysta kilka osób w sieci"),
            self.przelacz_webui_logowanie)
        self.sw_webui_logowanie.setChecked(webui_ustawienie("logowanie", True))
        wiersz = QFrame()
        wiersz.setObjectName("settingRow")
        hw = QHBoxLayout(wiersz)
        hw.setContentsMargins(0, 10, 0, 10)
        hw.addWidget(QLabel(_("Port")), 1)
        self.pole_webui_port = QLineEdit(str(_webui_port()))
        self.pole_webui_port.setObjectName("monoField")
        self.pole_webui_port.setFixedWidth(90)
        self.pole_webui_port.editingFinished.connect(self.zmien_port_webui)
        hw.addWidget(self.pole_webui_port)
        uk.addWidget(wiersz)
        uk.addStretch(1)
        dol.addWidget(karta, 0, 0)

        # --- Instalacja ---
        karta, uk = self._karta()
        uk.addWidget(_etykieta(_("Instalacja"), "h2"))
        siatka = QGridLayout()
        siatka.setHorizontalSpacing(16)
        siatka.setVerticalSpacing(10)
        siatka.setColumnMinimumWidth(0, 130)
        self._pola_instalacji_webui = {}
        for i, (klucz, podpis) in enumerate((("srodowisko", _("Środowisko")),
                                             ("dane", _("Dane czatów")), ("usluga", _("Usługa")),
                                             ("polaczenie", _("Połączenie")))):
            siatka.addWidget(_etykieta(podpis, "muted"), i, 0)
            pole = _etykieta("—", "mono")
            pole.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            pole.setWordWrap(True)
            siatka.addWidget(pole, i, 1)
            self._pola_instalacji_webui[klucz] = pole
        siatka.setColumnStretch(1, 1)
        uk.addLayout(siatka)
        uk.addStretch(1)
        przyciski = QHBoxLayout()
        przyciski.setSpacing(8)
        self.btn_kopia_webui = self._przycisk(_("Kopia zapasowa danych"), slot=self.kopia_zapasowa_webui)
        przyciski.addWidget(self.btn_kopia_webui)
        self.btn_webui_router = self._przycisk(_("Połącz z routerem..."), slot=lambda: DialogPolaczenieWebUI(self).exec())
        przyciski.addWidget(self.btn_webui_router)
        przyciski.addStretch(1)
        uk.addLayout(przyciski)
        dol.addWidget(karta, 0, 1)
        dol.setColumnStretch(0, 1)
        dol.setColumnStretch(1, 1)
        self._pokaz_info_webui()
        return strona

    def _wiersz_przelacznika(self, uk, tytul, opis, slot):
        # WHAT: wiersz ustawienia z makiety - tytuł i opis po lewej, przełącznik po prawej.
        wiersz = QFrame()
        wiersz.setObjectName("settingRow")
        h = QHBoxLayout(wiersz)
        h.setContentsMargins(0, 10, 0, 10)
        h.setSpacing(14)
        teksty = QVBoxLayout()
        teksty.setSpacing(2)
        teksty.addWidget(QLabel(tytul))
        lbl = _etykieta(opis, "muted")
        lbl.setWordWrap(True)
        teksty.addWidget(lbl)
        h.addLayout(teksty, 1)
        przelacznik = Przelacznik(tytul)
        przelacznik.clicked.connect(slot)
        h.addWidget(przelacznik)
        uk.addWidget(wiersz)
        return przelacznik

    def _odswiez_webui(self, stan):
        zainstalowane, dziala = stan["webui"], stan["webui_dziala"]
        w_toku = self._w_toku("webui")
        self.kropka_webui.setProperty("state", "ok" if dziala else "off")
        _odswiez_styl(self.kropka_webui)
        self.lbl_webui_stan.setText(_("Działa") if dziala else (_("Zatrzymane") if zainstalowane
                                                                 else _("Nie zainstalowane")))
        self.lbl_webui_adres.setText(_webui_url())
        if not zainstalowane:
            self.btn_webui_duzy.setText(_("Zainstaluj Open WebUI"))
        elif dziala:
            self.btn_webui_duzy.setText(_("Otwórz Open WebUI"))
        else:
            self.btn_webui_duzy.setText(_("Uruchom Open WebUI"))
        self.btn_webui_duzy.setEnabled(not w_toku)
        self.btn_webui_restart.setEnabled(zainstalowane and not w_toku)
        self.btn_webui_stop.setEnabled(dziala and not w_toku)
        self.btn_kopia_webui.setEnabled(zainstalowane)

    def odswiez_info_webui(self):
        # WHAT: katalog danych, konta i połączenie z routerem - raz przy wejściu na ekran.
        # WHY:  'uv tool list' i odczyt bazy to wolniejsze operacje - nie co 10 s.
        def _zbierz():
            katalog = webui_katalog_danych()
            return {"dane": str(katalog) if katalog else None,
                    "konta": webui_liczba_kont(), "router": webui_podlaczone_do_routera()}

        worker = FunkcjaWorker(_zbierz)

        def _po(sukces, wynik):
            if sukces:
                self._webui_info = wynik
                self._pokaz_info_webui()
                if self._stan:
                    self._odswiez_panel_uslug(self._stan)

        self._uruchom_watek("info_webui", worker, worker.zakonczono, _po)

    def _pokaz_info_webui(self):
        info = self._webui_info
        dom = str(Path.home())
        krotko = lambda sciezka: sciezka.replace(dom, "~", 1) if sciezka else "—"
        self._pola_instalacji_webui["srodowisko"].setText("~/.local/share/uv/tools/open-webui")
        self._pola_instalacji_webui["dane"].setText(krotko(info.get("dane")))
        self._pola_instalacji_webui["usluga"].setText(_("open-webui.service (użytkownik)"))
        polaczenie = self._pola_instalacji_webui["polaczenie"]
        if info.get("router"):
            polaczenie.setText(f"{LITELLM_URL}/v1")
            polaczenie.setProperty("state", "ok")
        else:
            polaczenie.setText(_("nie połączone z routerem"))
            polaczenie.setProperty("state", "warn")
        _odswiez_styl(polaczenie)
        self.btn_webui_router.setVisible(not info.get("router"))
        # WHY: wyłączenie logowania działa tylko przy pustej bazie kont - przy
        #      istniejących kontach Open WebUI odmówiłby logowania (patrz webui_liczba_kont).
        konta = info.get("konta")
        mozna_wylaczyc = konta == 0 or not webui_ustawienie("logowanie", True)
        self.sw_webui_logowanie.setEnabled(mozna_wylaczyc)
        self.sw_webui_logowanie.setToolTip("" if mozna_wylaczyc else _(
            "W Open WebUI są już konta ({n}). Logowania nie da się wyłączyć, bo Open WebUI "
            "przestałby wpuszczać do istniejących kont.").format(n=konta if konta is not None else "?"))

    def przelacz_webui_autostart(self, wlacz):
        # WHY: usługa --user (bez roota), ale w tle - zapis pliku .service + systemctl to blokujące I/O.
        opis = _("Włączenie autostartu WebUI") if wlacz else _("Wyłączenie autostartu WebUI")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: webui_autostart(wlacz), opis)

    def _proponuj_restart_webui(self):
        # WHY: port i logowanie są w pliku usługi - działają po ponownym uruchomieniu.
        if self._stan and self._stan["webui_dziala"]:
            odp = QMessageBox.question(self, "Open WebUI", _(
                "Zmiana zadziała po ponownym uruchomieniu Open WebUI. Uruchomić ponownie teraz?"))
            if odp == QMessageBox.StandardButton.Yes:
                self.uruchom_webui(restart=True)

    def przelacz_webui_logowanie(self, wlacz):
        webui_zapisz_ustawienie("logowanie", wlacz)
        self.wpis_log(_("Logowanie w Open WebUI: {stan}.").format(stan=_("włączone") if wlacz else _("wyłączone")))
        self._pokaz_info_webui()
        self._proponuj_restart_webui()

    def zmien_port_webui(self):
        tekst = self.pole_webui_port.text().strip()
        if tekst == str(_webui_port()):
            return
        # WHY: porty poniżej 1024 wymagają roota, a WebUI działa jako zwykły użytkownik
        if not tekst.isdigit() or not 1024 <= int(tekst) <= 65535:
            QMessageBox.warning(self, _("Błędny port"), _("Podaj numer portu od 1024 do 65535."))
            self.pole_webui_port.setText(str(_webui_port()))
            return
        # WHY: webui_zatrzymaj zatrzymuje usługę systemd niezależnie od portu, więc
        #      restart po zmianie (webui_zatrzymaj + webui_uruchom) przenosi WebUI na nowy port.
        stary = _webui_port()
        _ustawienia().setValue("webui/port", int(tekst))
        self.wpis_log(_("Port Open WebUI: {stary} → {nowy}.").format(stary=stary, nowy=tekst))
        self._proponuj_restart_webui()

    def kopia_zapasowa_webui(self):
        domyslny = str(Path.home() / f"open-webui-kopia-{time.strftime('%Y-%m-%d')}.zip")
        plik, _filtr = QFileDialog.getSaveFileName(self, _("Kopia zapasowa danych Open WebUI"), domyslny,
                                                   _("Archiwum ZIP (*.zip)"))
        if not plik:
            return
        if not plik.endswith(".zip"):
            plik += ".zip"
        self.wpis_log(_("Tworzę kopię zapasową danych Open WebUI..."))
        self._uruchom_akcje(lambda: webui_kopia_zapasowa(plik),
                            _("Kopia zapasowa Open WebUI: {plik}").format(plik=plik), powiadom=True)

    # --- Aktualizacje (Ollama / WebUI / LiteLLM) -----------------------------
    def _sprawdz_aktualizacje(self, rodzaj, nazwa, lokalna, najnowsza, przycisk, aktualizuj, opis_aktualizacji):
        # WHAT: wspólne sprawdzanie: wersja lokalna vs najnowsza, potem pytanie o aktualizację.
        # WHY:  NA ŻĄDANIE (przycisk), nie co 10 s - pytanie GitHub/PyPI przy każdym
        #       tyknięciu zegara byłoby zbędnym ruchem sieciowym.
        if self._w_toku(rodzaj):
            return
        self.wpis_log(_("Sprawdzam dostępność aktualizacji: {nazwa}...").format(nazwa=nazwa))
        przycisk.setEnabled(False)
        worker = SprawdzAktualizacjeWorker(lokalna, najnowsza)

        def _po(wynik):
            przycisk.setEnabled(True)
            if wynik["lokalna"]:
                self._wersje[rodzaj] = wynik["lokalna"]
                self._pokaz_wersje()
            if wynik["blad"]:
                self.wpis_log(_("Nie udało się sprawdzić aktualizacji: {nazwa} - {blad}").format(
                    nazwa=nazwa, blad=wynik["blad"]))
                return
            if not wynik["dostepna"]:
                self.wpis_log(_("Masz najnowszą wersję: {nazwa} {wersja}.").format(nazwa=nazwa, wersja=wynik["lokalna"]))
                return
            odp = QMessageBox.question(
                self, _("Aktualizacja: {nazwa}").format(nazwa=nazwa),
                _("Dostępna jest nowsza wersja: {nazwa} {najnowsza}\nMasz zainstalowaną: {lokalna}\n\n"
                  "Zainstalować aktualizację teraz?\n\n{opis}").format(
                    nazwa=nazwa, najnowsza=wynik["najnowsza"], lokalna=wynik["lokalna"], opis=opis_aktualizacji))
            if odp == QMessageBox.StandardButton.Yes:
                self.wpis_log(_("Aktualizuję: {nazwa} - to może potrwać kilka minut...").format(nazwa=nazwa))
                self._watki[rodzaj] = self._uruchom_akcje(aktualizuj, _("Aktualizacja: {nazwa}").format(nazwa=nazwa), powiadom=True)

        self._uruchom_watek(rodzaj, worker, worker.wynik, _po)

    def sprawdz_aktualizacje_ollamy(self):
        # WHY: ten sam oficjalny skrypt instalacyjny aktualizuje istniejącą instalację w miejscu.
        self._sprawdz_aktualizacje(
            "aktualizacja_ollamy", "Ollama", ollama_wersja_lokalna, ollama_wersja_najnowsza,
            self.btn_aktualizacje_ollamy, ollama_zainstaluj,
            _("Pobierze i uruchomi oficjalny skrypt instalacyjny z ollama.com\n"
              "(wymaga uprawnień administratora i połączenia z internetem)."))

    def sprawdz_aktualizacje_webui(self):
        self._sprawdz_aktualizacje(
            "aktualizacja_webui", "Open WebUI", webui_wersja_lokalna, webui_wersja_najnowsza,
            self.btn_sprawdz_aktualizacje_webui, webui_aktualizuj,
            _("Uruchomi 'uv tool upgrade' dla bieżącego użytkownika\n"
              "(bez uprawnień administratora - wymaga internetu)."))

    def sprawdz_aktualizacje_litellm(self):
        # WHY: BEZ samoczynnego restartu po aktualizacji - użytkownik uruchamia ponownie sam.
        self._sprawdz_aktualizacje(
            "aktualizacja_litellm", "LiteLLM", litellm_wersja_lokalna, litellm_wersja_najnowsza,
            self.btn_aktualizacje_litellm, litellm_aktualizuj,
            _("Uruchomi 'uv tool upgrade' dla bieżącego użytkownika\n"
              "(bez uprawnień administratora - wymaga internetu)."))

    # =========================================================================
    #  Ekran "Dziennik" (makieta Logs.dc.html)
    # =========================================================================
    def _strona_dziennik(self):
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        naglowek = QHBoxLayout()
        naglowek.addLayout(self._tytul_strony(
            _("Dziennik"), _("Komunikaty z usług, z prostym wyjaśnieniem, co znaczą błędy.")), 1)
        naglowek.addWidget(self._przycisk(_("Zapisz do pliku"), slot=self.zapisz_dziennik),
                           alignment=Qt.AlignmentFlag.AlignVCenter)
        naglowek.addWidget(self._przycisk(_("Wyczyść widok"), slot=self.wyczysc_dziennik),
                           alignment=Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(naglowek)

        filtry = QHBoxLayout()
        filtry.setSpacing(6)
        self._chipy_dziennika = {}
        grupa = QButtonGroup(strona)
        for klucz, nazwa in (("all", _("Wszystkie")), ("Ollama", "Ollama"), ("LiteLLM", "LiteLLM"),
                             ("Open WebUI", "Open WebUI"), ("SearXNG", "SearXNG"), ("Program", _("Program"))):
            chip = self._chip(nazwa, lambda _c=False, k=klucz: self._filtruj_dziennik(k))
            grupa.addButton(chip)
            filtry.addWidget(chip)
            self._chipy_dziennika[klucz] = chip
        filtry.addStretch(1)
        self.chk_autoprzewijanie = QCheckBox(_("Przewijaj automatycznie"))
        self.chk_autoprzewijanie.setChecked(True)
        filtry.addWidget(self.chk_autoprzewijanie)
        v.addLayout(filtry)

        # WHAT: brak uprawnień do dziennika Ollamy (usługa systemowa) - dwa wyjścia.
        self.ramka_dostepu = QFrame()
        self.ramka_dostepu.setObjectName("noticeBar")
        h = QHBoxLayout(self.ramka_dostepu)
        h.setContentsMargins(14, 8, 8, 8)
        h.setSpacing(8)
        opis = QLabel(_("Wpisy Ollamy (usługa systemowa) wymagają uprawnień. Możesz wczytać je raz "
                        "albo zezwolić na stałe (po ponownym zalogowaniu)."))
        opis.setWordWrap(True)
        h.addWidget(opis, 1)
        h.addWidget(self._przycisk(_("Wczytaj teraz"), slot=lambda: self.odswiez_dziennik(ollama_przez_pkexec=True)))
        h.addWidget(self._przycisk(_("Zezwól na stałe"), slot=self.zezwol_na_dziennik))
        self.ramka_dostepu.setVisible(not self._dostep_ollama)
        v.addWidget(self.ramka_dostepu)

        self.lista_dziennika = QListWidget()
        self.lista_dziennika.setObjectName("logList")
        self.lista_dziennika.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.lista_dziennika.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lista_dziennika.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        v.addWidget(self.lista_dziennika, 1)
        self._zaznacz_filtr_dziennika()
        self._wypelnij_dziennik()
        return strona

    def _zaznacz_filtr_dziennika(self):
        for klucz, chip in self._chipy_dziennika.items():
            chip.setChecked(klucz == self._filtr_dziennika)
        self._wypelnij_dziennik()

    def _filtruj_dziennik(self, klucz):
        self._filtr_dziennika = klucz
        self._wypelnij_dziennik()

    def _pasuje_do_filtra(self, wpis):
        return (wpis["czas"] >= self._dziennik_od
                and (self._filtr_dziennika == "all" or wpis["usluga"] == self._filtr_dziennika))

    def _dodaj_wiersz_dziennika(self, wpis):
        wiersz = WierszDziennika(wpis, self)
        element = QListWidgetItem()
        self.lista_dziennika.addItem(element)
        self.lista_dziennika.setItemWidget(element, wiersz)
        # WHY: wysokość wiersza zależy od zawijania tekstu przy aktualnej szerokości
        #      listy - liczymy ją dla tej szerokości (heightForWidth), nie "na oko".
        szerokosc = max(400, self.lista_dziennika.viewport().width())
        wiersz.ensurePolished()
        wysokosc = wiersz.layout().heightForWidth(szerokosc) if wiersz.layout().hasHeightForWidth() \
            else wiersz.sizeHint().height()
        element.setSizeHint(QSize(szerokosc, wysokosc))

    def _wypelnij_dziennik(self):
        # WHY: pokazujemy najwyżej 400 ostatnich pasujących wpisów - każdy wiersz
        #      to osobny widżet, a pełna historia i tak trafia do "Zapisz do pliku".
        if not hasattr(self, "lista_dziennika"):
            return
        self.lista_dziennika.clear()
        for wpis in [w for w in self._wpisy if self._pasuje_do_filtra(w)][-400:]:
            self._dodaj_wiersz_dziennika(wpis)
        self.lista_dziennika.scrollToBottom()

    def _dodaj_wpisy(self, wpisy):
        # WHAT: nowe wpisy do historii (+ wyjaśnienie błędów) i do widocznej listy.
        for wpis in wpisy:
            if wpis["poziom"] != "INFO" and "pomoc" not in wpis:
                wpis["pomoc"] = wyjasnij_blad(wpis["tekst"])
        self._wpisy = (self._wpisy + wpisy)[-3000:]  # WHY: nie rośnij w nieskończoność
        if not hasattr(self, "lista_dziennika"):
            return
        pasujace = [w for w in wpisy if self._pasuje_do_filtra(w)]
        for wpis in pasujace:
            self._dodaj_wiersz_dziennika(wpis)
        while self.lista_dziennika.count() > 400:
            self.lista_dziennika.takeItem(0)
        if pasujace and self.chk_autoprzewijanie.isChecked():
            self.lista_dziennika.scrollToBottom()

    def odswiez_dziennik(self, ollama_przez_pkexec=False):
        worker = DziennikWorker(self._kursory, ollama_przez_pkexec)

        def _po(wpisy, kursory, dostep):
            self._kursory = kursory
            # WHY: jednorazowy odczyt przez pkexec nie zmienia uprawnień - pasek zostaje,
            #      dopóki program nie wystartuje z grupą systemd-journal
            self._dostep_ollama = dostep_do_dziennika_systemu()
            self.ramka_dostepu.setVisible(not self._dostep_ollama)
            self._dodaj_wpisy(wpisy)

        self._uruchom_watek("dziennik", worker, worker.wynik, _po)

    def zezwol_na_dziennik(self):
        odp = QMessageBox.question(self, _("Dziennik Ollamy"), _(
            "Dopisać Twoje konto do grupy systemd-journal?\n\nDzięki temu dziennik Ollamy będzie widoczny "
            "bez hasła. Wymaga uprawnień administratora i zadziała po ponownym zalogowaniu."))
        if odp == QMessageBox.StandardButton.Yes:
            self._uruchom_akcje(dziennik_zezwol_na_odczyt, _("Dostęp do dziennika Ollamy (po ponownym zalogowaniu)"))

    def wyczysc_dziennik(self):
        # WHY: tylko widok - historia zostaje (np. do "Zapisz do pliku").
        self._dziennik_od = time.time()
        self._wypelnij_dziennik()

    def zapisz_dziennik(self):
        domyslny = str(Path.home() / f"{ID_PROGRAMU}-dziennik-{time.strftime('%Y-%m-%d')}.txt")
        plik, _filtr = QFileDialog.getSaveFileName(self, _("Zapisz dziennik"), domyslny, _("Plik tekstowy (*.txt)"))
        if not plik:
            return
        linie = [f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(w['czas']))}  {w['usluga']:<10}  "
                 f"{w['poziom']:<5}  {w['tekst']}" for w in self._wpisy
                 if self._filtr_dziennika == "all" or w["usluga"] == self._filtr_dziennika]
        try:
            Path(plik).write_text("\n".join(linie) + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, _("Błąd zapisu"), str(e))
            return
        self.wpis_log(_("Zapisano dziennik: {plik}").format(plik=plik))

    # =========================================================================
    #  Ekran "Ustawienia" (dawne "Zaawansowane" + opisy z dawnej "Pomocy")
    # =========================================================================
    def _strona_ustawienia(self):
        przewijanie = QScrollArea()
        przewijanie.setWidgetResizable(True)
        przewijanie.setFrameShape(QScrollArea.Shape.NoFrame)
        przewijanie.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        strona = QWidget()
        v = QVBoxLayout(strona)
        v.setContentsMargins(0, 0, 12, 0)
        v.setSpacing(18)
        v.addLayout(self._tytul_strony(
            _("Ustawienia"), _("Język, usługi na tym komputerze, aktualizacje i zmienne Ollamy.")))

        # --- Ogólne + usługi ---
        rzad = QHBoxLayout()
        rzad.setSpacing(18)
        karta, uk = self._karta()
        uk.setSpacing(0)
        uk.addWidget(_etykieta(_("Ogólne"), "h2"))
        wiersz = QFrame()
        wiersz.setObjectName("settingRow")
        h = QHBoxLayout(wiersz)
        h.setContentsMargins(0, 10, 0, 10)
        h.addWidget(QLabel(_("Język interfejsu")), 1)
        self.combo_jezyk = QComboBox()
        for kod, nazwa in JEZYKI.items():
            self.combo_jezyk.addItem(nazwa, kod)
        self.combo_jezyk.setCurrentIndex(max(0, self.combo_jezyk.findData(self._jezyk_aktywny)))
        self.combo_jezyk.currentIndexChanged.connect(self._zmien_jezyk)
        h.addWidget(self.combo_jezyk)
        uk.addWidget(wiersz)
        self.sw_ollama_autostart = self._wiersz_przelacznika(
            uk, _("Ollama startuje razem z systemem"), _("usługa systemowa - zmiana wymaga hasła administratora"),
            self.przelacz_autostart)
        self.sw_litellm_autostart = self._wiersz_przelacznika(
            uk, _("Router LiteLLM startuje po zalogowaniu"), _("usługa użytkownika, bez hasła"),
            self.przelacz_litellm_autostart)
        # WHY: autostart i aktualizacje wszystkich trzech usług w jednym miejscu
        #      (prośba użytkownika) - ekran Open WebUI zostaje od codziennego użycia.
        self.sw_webui_autostart = self._wiersz_przelacznika(
            uk, _("Open WebUI startuje po zalogowaniu"), _("usługa użytkownika, bez hasła"),
            self.przelacz_webui_autostart)
        self.sw_zasobnik = self._wiersz_przelacznika(
            uk, _("Ikona w zasobniku systemowym"),
            _("krzyżyk chowa okno do zasobnika, program zamykasz przez \"Zakończ\" w menu ikony"),
            self.przelacz_zasobnik)
        self.sw_zasobnik.setChecked(zasobnik_wlaczony())
        self.sw_autostart_programu = self._wiersz_przelacznika(
            uk, _("Uruchamiaj program po zalogowaniu"), _("od razu schowany w zasobniku"),
            self.przelacz_autostart_programu)
        self.sw_autostart_programu.setChecked(autostart_programu_wlaczony())
        self.sw_autostart_programu.setEnabled(zasobnik_wlaczony())
        # WHY: tylko z AppImage - install.sh i .deb same dodają program do menu
        self.sw_wpis_menu = None
        if _APPIMAGE:
            wpis = wpis_menu_appimage()
            self.sw_wpis_menu = self._wiersz_przelacznika(
                uk, _("Program w menu aplikacji"),
                _("program jest już w menu (zainstalowany w inny sposób)") if wpis == "inny"
                else _("wpis wskazuje ten plik AppImage - po przeniesieniu pliku poprawi się sam"),
                self.przelacz_wpis_menu)
            self.sw_wpis_menu.setChecked(wpis is not None)
            self.sw_wpis_menu.setEnabled(wpis != "inny")
        uk.addStretch(1)
        rzad.addWidget(karta, 1)

        karta, uk = self._karta()
        uk.setSpacing(0)
        uk.addWidget(_etykieta(_("Wersje i aktualizacje"), "h2"))
        self._lbl_wersji = {}
        for rodzaj, nazwa, slot in (("aktualizacja_ollamy", "Ollama", self.sprawdz_aktualizacje_ollamy),
                                    ("aktualizacja_litellm", "LiteLLM", self.sprawdz_aktualizacje_litellm),
                                    ("aktualizacja_webui", "Open WebUI", self.sprawdz_aktualizacje_webui),
                                    ("aktualizacja_searxng", "SearXNG", self.sprawdz_aktualizacje_searxng)):
            wiersz = QFrame()
            wiersz.setObjectName("settingRow")
            h = QHBoxLayout(wiersz)
            h.setContentsMargins(0, 10, 0, 10)
            h.addWidget(QLabel(nazwa))
            lbl = _etykieta(self._wersje.get(rodzaj, ""), "mono", muted="true")
            h.addWidget(lbl, 1)
            self._lbl_wersji[rodzaj] = lbl
            btn = self._przycisk(_("Sprawdź aktualizacje"), slot=slot)
            h.addWidget(btn)
            if rodzaj == "aktualizacja_ollamy":
                self.btn_aktualizacje_ollamy = btn
            elif rodzaj == "aktualizacja_litellm":
                self.btn_aktualizacje_litellm = btn
            elif rodzaj == "aktualizacja_searxng":
                self.btn_aktualizacje_searxng = btn
            else:
                self.btn_sprawdz_aktualizacje_webui = btn
            uk.addWidget(wiersz)
        uwaga = _etykieta(_("Niektóre nowe modele wymagają nowszej Ollamy - warto sprawdzać co jakiś czas."),
                          "muted")
        uwaga.setWordWrap(True)
        uwaga.setContentsMargins(0, 10, 0, 0)
        uk.addWidget(uwaga)
        self.lbl_uwaga_wersji = uwaga
        # WHY: gdy żadnej usługi nie ma, wiersze wersji są schowane - karta nie może być pusta
        self.lbl_brak_uslug = _etykieta(_("Żadna usługa nie jest jeszcze zainstalowana. Zainstalujesz je "
                                          "przyciskami \"Zainstaluj\" na panelu głównym."), "muted")
        self.lbl_brak_uslug.setWordWrap(True)
        self.lbl_brak_uslug.setContentsMargins(0, 10, 0, 0)
        uk.addWidget(self.lbl_brak_uslug)
        uk.addStretch(1)
        rzad.addWidget(karta, 1)
        v.addLayout(rzad)

        # --- Czat: model do wyszukiwania w dużych plikach ---
        karta, uk = self._karta()
        uk.setSpacing(0)
        uk.addWidget(_etykieta(_("Czat"), "h2"))
        wiersz = QFrame()
        wiersz.setObjectName("settingRow")
        h = QHBoxLayout(wiersz)
        h.setContentsMargins(0, 10, 0, 10)
        h.setSpacing(14)
        teksty = QVBoxLayout()
        teksty.setSpacing(2)
        teksty.addWidget(QLabel(_("Model do wyszukiwania w dużych plikach")))
        opis = _etykieta(_("model embeddingów - przy plikach powyżej 100 KB zamienia ich fragmenty na wektory, "
                           "żeby do pytania trafiły tylko pasujące. \"Automatycznie\" = pierwszy znaleziony, "
                           "najlepiej na hoście modelu czatu."), "muted")
        opis.setWordWrap(True)
        teksty.addWidget(opis)
        h.addLayout(teksty, 1)
        self.combo_model_rag = QComboBox()
        self.combo_model_rag.setMinimumWidth(320)
        self.combo_model_rag.currentIndexChanged.connect(self._zmiana_modelu_rag)
        h.addWidget(self.combo_model_rag, 0, Qt.AlignmentFlag.AlignTop)
        uk.addWidget(wiersz)
        # WHY: gdy żadnego modelu embeddingów nie ma - jeden przycisk zamiast szukania nazwy
        self.ramka_rag_brak = QFrame()
        self.ramka_rag_brak.setObjectName("ragMissing")
        h = QHBoxLayout(self.ramka_rag_brak)
        h.setContentsMargins(0, 10, 0, 0)
        brak = _etykieta(_("Na hostach nie ma jeszcze żadnego modelu embeddingów. Zalecany jest wielojęzyczny "
                           "(dobrze radzi sobie z polskimi tekstami); możesz też pobrać inny na ekranie "
                           "\"Modele\" i wybrać go tutaj."), "muted")
        brak.setWordWrap(True)
        h.addWidget(brak, 1)
        self.btn_pobierz_rag = self._przycisk(
            _("Pobierz {model} ({rozmiar})").format(model=RAG_ZALECANY_MODEL, rozmiar=RAG_ZALECANY_ROZMIAR),
            "pobierz", slot=self._pobierz_model_rag)
        h.addWidget(self.btn_pobierz_rag, 0, Qt.AlignmentFlag.AlignTop)
        uk.addWidget(self.ramka_rag_brak)
        v.addWidget(karta)
        self._odswiez_modele_rag()
        v.addWidget(self._karta_wyszukiwania())

        # --- Zmienne środowiskowe Ollamy ---
        karta, uk = self._karta()
        uk.setSpacing(0)
        uk.addWidget(_etykieta(_("Ollama na tym komputerze - zmienne środowiskowe"), "h2"))
        wstep = _etykieta(_("\"Zapisz\" stosuje wszystkie pola naraz i uruchamia ponownie usługę Ollama "
                            "(wymaga hasła administratora - załadowane modele na chwilę znikną z pamięci). "
                            "Puste pole = domyślne zachowanie Ollamy."), "muted")
        wstep.setWordWrap(True)
        wstep.setContentsMargins(0, 6, 0, 8)
        uk.addWidget(wstep)

        self.pole_keep_alive = QLineEdit()
        self.pole_keep_alive.setPlaceholderText(_("np. 30m"))
        # WHAT: typowe rozmiary kontekstu do wyboru, ale pole jest edytowalne -
        #       nietypowa wartość z override.conf (np. 24576) nie zgubi się przy zapisie.
        self.combo_context_length = QComboBox()
        self.combo_context_length.setEditable(True)
        for etykieta, wartosc in [
            (_("domyślne (Ollama)"), ""), ("4k (4096)", "4096"), ("8k (8192)", "8192"),
            ("16k (16384)", "16384"), ("32k (32768)", "32768"), ("64k (65536)", "65536"),
            ("128k (131072)", "131072"),
        ]:
            self.combo_context_length.addItem(etykieta, wartosc)
        self.pole_max_loaded = QLineEdit()
        self.pole_max_loaded.setPlaceholderText(_("np. 1"))
        self.pole_num_parallel = QLineEdit()
        self.pole_num_parallel.setPlaceholderText(_("np. 1"))
        self.combo_flash_attention = QComboBox()
        for etykieta, wartosc in [(_("domyślne (auto)"), ""), (_("włączone"), "1"), (_("wyłączone"), "0")]:
            self.combo_flash_attention.addItem(etykieta, wartosc)
        self.combo_kv_cache = QComboBox()
        for etykieta, wartosc in [(_("domyślne (f16)"), ""), ("q8_0", "q8_0"), ("q4_0", "q4_0")]:
            self.combo_kv_cache.addItem(etykieta, wartosc)
        self.chk_vulkan = Przelacznik("OLLAMA_VULKAN")
        self.chk_igpu = Przelacznik("OLLAMA_IGPU_ENABLE")
        self.chk_host = Przelacznik("OLLAMA_HOST")
        self.pole_ip_allow = QLineEdit()
        self.pole_ip_allow.setPlaceholderText(_("np. localhost 192.168.8.20 (puste = cały LAN)"))
        self.pole_ggml_vk_visible_devices = QLineEdit()
        self.pole_ggml_vk_visible_devices.setPlaceholderText(_("np. 0"))
        # WHY: wartość w bajtach zależy tylko od tego, czy na hoście działa pulpit -
        #      jawne "0" dla serwera, żeby dało się odróżnić świadomy wybór od braku wpisu.
        self.combo_gpu_overhead = QComboBox()
        for etykieta, wartosc in [(_("pulpit (GNOME/KDE)"), "536870912"), (_("serwer (bez X-ów)"), "0")]:
            self.combo_gpu_overhead.addItem(etykieta, wartosc)

        pola = [
            ("OLLAMA_KEEP_ALIVE", self.pole_keep_alive,
             _("Jak długo model zostaje w pamięci po ostatnim zapytaniu, zanim zostanie wyładowany (domyślnie "
               "kilka minut). np. 30m, 1h, -1 (zawsze), 0 (od razu).")),
            ("OLLAMA_CONTEXT_LENGTH", self.combo_context_length,
             _("Rozmiar okna kontekstu w tokenach (domyślnie 4096 - za mało do pracy agentowej w Continue/OpenCode).")),
            ("OLLAMA_MAX_LOADED_MODELS", self.pole_max_loaded,
             _("Ile modeli może być jednocześnie załadowanych do pamięci (domyślnie 3x liczba GPU).")),
            ("OLLAMA_NUM_PARALLEL", self.pole_num_parallel,
             _("Ile równoległych zapytań obsłuży jeden załadowany model naraz.")),
            ("OLLAMA_FLASH_ATTENTION", self.combo_flash_attention,
             _("Zmniejsza zużycie pamięci przy dłuższym kontekście, jeśli model i sprzęt to wspierają.")),
            ("OLLAMA_KV_CACHE_TYPE", self.combo_kv_cache,
             _("Kwantyzacja pamięci podręcznej kontekstu - q8_0 to ok. -50% zużycia VRAM przy pomijalnej stracie "
               "jakości.")),
            ("OLLAMA_VULKAN", self.chk_vulkan,
             _("Backend Vulkan zamiast ROCm - szersza kompatybilność z kartami AMD bez pełnego wsparcia ROCm "
               "(np. BC-250).")),
            ("OLLAMA_IGPU_ENABLE", self.chk_igpu,
             _("Czy Ollama może korzystać ze zintegrowanego GPU (iGPU) - domyślnie włączone. Wyłącz, żeby wymusić "
               "pominięcie iGPU (np. przy problemach na jednolitej architekturze CPU+GPU jak BC-250).")),
            ("OLLAMA_HOST", self.chk_host,
             _("Domyślnie Ollama nasłuchuje tylko lokalnie (127.0.0.1). Włącz, żeby nasłuchiwała na wszystkich "
               "interfejsach (0.0.0.0) i była dostępna z innych komputerów w sieci.")),
            ("IPAddressAllow", self.pole_ip_allow,
             _("Adresy IP / podsieci (oddzielone spacją), które mogą łączyć się z Ollamą z sieci lokalnej. Puste = "
               "cały LAN. Internet działa dalej (pobieranie modeli). Jeśli DNS to router w LAN, dopisz go do "
               "listy. Ma sens razem z OLLAMA_HOST.")),
            ("GGML_VK_VISIBLE_DEVICES", self.pole_ggml_vk_visible_devices,
             _("Który indeks urządzenia Vulkan ma być widoczny (0 = pierwsze) - potrzebne na niektórych APU "
               "(np. BC-250). Działa tylko z włączonym OLLAMA_VULKAN.")),
            ("OLLAMA_GPU_OVERHEAD", self.combo_gpu_overhead,
             _("Ile pamięci GPU zostawić dla reszty systemu - ważne na APU z pamięcią współdzieloną. Stacja "
               "z pulpitem dzieli VRAM z kompozytorem, serwer bez pulpitu nie potrzebuje rezerwacji.")),
        ]
        self._pola_zmiennych = []
        for nazwa, pole, opis in pola:
            wiersz = QFrame()
            wiersz.setObjectName("settingRow")
            h = QHBoxLayout(wiersz)
            h.setContentsMargins(0, 10, 0, 10)
            h.setSpacing(18)
            teksty = QVBoxLayout()
            teksty.setSpacing(2)
            teksty.addWidget(_etykieta(nazwa, "mono"))
            lbl = _etykieta(opis, "muted")
            lbl.setWordWrap(True)
            teksty.addWidget(lbl)
            h.addLayout(teksty, 1)
            if isinstance(pole, Przelacznik):
                h.addWidget(pole, alignment=Qt.AlignmentFlag.AlignVCenter)
                h.addSpacing(216)  # WHY: przełączniki w tej samej kolumnie co pola obok
            else:
                pole.setFixedWidth(260)
                h.addWidget(pole, alignment=Qt.AlignmentFlag.AlignVCenter)
            uk.addWidget(wiersz)
            self._pola_zmiennych.append(pole)

        uk.addWidget(_etykieta(_("Własne parametry (jeden na linię, KLUCZ=WARTOŚĆ):"), "muted"))
        self.pole_wlasne_zmienne = QPlainTextEdit()
        self.pole_wlasne_zmienne.setPlaceholderText("OLLAMA_...=...")
        self.pole_wlasne_zmienne.setFixedHeight(90)
        uk.addWidget(self.pole_wlasne_zmienne)
        przyciski = QHBoxLayout()
        przyciski.setContentsMargins(0, 12, 0, 0)
        self.btn_zalecane_zaawansowane = self._przycisk(_("Zastosuj zalecane wartości"),
                                                        slot=self.zastosuj_zalecane_zaawansowane)
        przyciski.addWidget(self.btn_zalecane_zaawansowane)
        przyciski.addStretch(1)
        self.btn_zapisz_zaawansowane = self._przycisk(_("Zapisz"), rola="primary", slot=self.zapisz_zaawansowane)
        przyciski.addWidget(self.btn_zapisz_zaawansowane)
        uk.addLayout(przyciski)
        v.addWidget(karta)
        v.addStretch(1)
        przewijanie.setWidget(strona)
        return przewijanie

    def odswiez_wersje(self):
        # WHAT: zainstalowane wersje Ollamy, LiteLLM i Open WebUI - bez sprawdzania internetu.
        worker = FunkcjaWorker(lambda: {"aktualizacja_ollamy": ollama_wersja_lokalna(),
                                        "aktualizacja_litellm": litellm_wersja_lokalna(),
                                        "aktualizacja_webui": webui_wersja_lokalna()})

        def _po(sukces, wynik):
            if sukces:
                self._wersje.update({k: v for k, v in wynik.items() if v})
                self._pokaz_wersje()

        self._uruchom_watek("wersje", worker, worker.zakonczono, _po)

    def _pokaz_wersje(self):
        for rodzaj, lbl in getattr(self, "_lbl_wersji", {}).items():
            lbl.setText(self._wersje.get(rodzaj, ""))

    def _odswiez_ustawienia(self, stan):
        # WHAT: przełączniki autostartu przy każdym odświeżeniu; pola zmiennych
        #       TYLKO raz po (prze)budowaniu okna i po zapisie.
        # WHY:  cykliczne odświeżanie nadpisywałoby to, co użytkownik właśnie wpisuje.
        self.sw_ollama_autostart.setEnabled(stan["zainstalowana"])
        self.sw_ollama_autostart.ustaw_cicho(stan["enabled"])
        self.sw_litellm_autostart.setEnabled(stan["litellm"])
        self.sw_litellm_autostart.ustaw_cicho(stan["litellm_autostart"])
        self.btn_aktualizacje_ollamy.setEnabled(stan["zainstalowana"] and not self._w_toku("aktualizacja_ollamy"))
        self.btn_aktualizacje_litellm.setEnabled(stan["litellm"] and not self._w_toku("aktualizacja_litellm"))
        self.sw_webui_autostart.setEnabled(stan["webui"])
        self.sw_webui_autostart.ustaw_cicho(stan["webui_autostart"])
        self.btn_sprawdz_aktualizacje_webui.setEnabled(stan["webui"] and not self._w_toku("aktualizacja_webui"))
        self.btn_aktualizacje_searxng.setEnabled(stan["searxng"] and not self._w_toku("aktualizacja_searxng"))
        self._odswiez_wyszukiwanie(stan)
        # WHAT: wiersze autostartu i wersji usługi, której nie ma, znikają (jak jej pozycja
        #       w pasku bocznym - _pokaz_sekcje_uslug); po instalacji same wracają.
        for widget, zainstalowana in ((self.sw_ollama_autostart, stan["zainstalowana"]),
                                      (self.btn_aktualizacje_ollamy, stan["zainstalowana"]),
                                      (self.sw_litellm_autostart, stan["litellm"]),
                                      (self.btn_aktualizacje_litellm, stan["litellm"]),
                                      (self.sw_webui_autostart, stan["webui"]),
                                      (self.btn_sprawdz_aktualizacje_webui, stan["webui"]),
                                      (self.btn_aktualizacje_searxng, stan["searxng"])):
            widget.parentWidget().setVisible(zainstalowana)  # WHY: rodzic = cały wiersz (settingRow)
        self.lbl_uwaga_wersji.setVisible(stan["zainstalowana"])
        self.lbl_brak_uslug.setVisible(not (stan["zainstalowana"] or stan["litellm"] or stan["webui"]
                                            or stan["searxng"]))
        for widget in self._pola_zmiennych + [self.pole_wlasne_zmienne, self.btn_zalecane_zaawansowane,
                                              self.btn_zapisz_zaawansowane]:
            widget.setEnabled(stan["zainstalowana"])
        if self._ustawienia_wczytane:
            return
        self._ustawienia_wczytane = True
        env = stan["env_ollama"]
        self.pole_keep_alive.setText(env.get("OLLAMA_KEEP_ALIVE", ""))
        self._ustaw_kontekst(env.get("OLLAMA_CONTEXT_LENGTH", ""))
        self.pole_max_loaded.setText(env.get("OLLAMA_MAX_LOADED_MODELS", ""))
        self.pole_num_parallel.setText(env.get("OLLAMA_NUM_PARALLEL", ""))
        self._ustaw_combo_wg_wartosci(self.combo_flash_attention, env.get("OLLAMA_FLASH_ATTENTION", ""))
        self._ustaw_combo_wg_wartosci(self.combo_kv_cache, env.get("OLLAMA_KV_CACHE_TYPE", ""))
        self.chk_vulkan.setChecked(env.get("OLLAMA_VULKAN") == "1")
        # WHY: IGPU_ENABLE domyślnie WŁĄCZONE - akceptujemy "0" i stary format "false".
        self.chk_igpu.setChecked(env.get("OLLAMA_IGPU_ENABLE") not in ("0", "false"))
        self.chk_host.setChecked(bool(env.get("OLLAMA_HOST")))
        self.pole_ip_allow.setText(stan["ip_allow"])
        self.pole_ggml_vk_visible_devices.setText(env.get("GGML_VK_VISIBLE_DEVICES", ""))
        self._ustaw_combo_wg_wartosci(self.combo_gpu_overhead, env.get("OLLAMA_GPU_OVERHEAD", ""))
        # WHY: wszystko spoza znanych zmiennych ląduje w "własnych parametrach" -
        #      formularz niczego nie gubi przy następnym zapisie.
        wlasne = [f"{k}={v}" for k, v in env.items() if k not in OLLAMA_ZNANE_ZMIENNE]
        self.pole_wlasne_zmienne.setPlainText("\n".join(wlasne))

    # --- Wyszukiwanie w internecie (źródło wyników + SearXNG) ----------------
    def _karta_wyszukiwania(self):
        # WHAT: karta w Ustawieniach - skąd czat bierze wyniki wyszukiwania i obsługa
        #       SearXNG na tym komputerze (instalacja, start/stop, autostart, usuwanie).
        # WHY:  wszystko o wyszukiwaniu w jednym miejscu; pola trzech źródeł są w osobnych
        #       ramkach, widać tylko ramkę wybranego (_pokaz_zrodlo_wyszukiwania).
        karta, uk = self._karta()
        uk.setSpacing(0)
        uk.addWidget(_etykieta(_("Wyszukiwanie w internecie"), "h2"))
        wstep = _etykieta(_("Skąd czat bierze wyniki, gdy poprosisz go o wyszukanie czegoś w internecie. "
                            "Zapytania wychodzą wtedy z tego komputera do internetu - dlatego domyślnie "
                            "jest wyłączone."), "muted")
        wstep.setWordWrap(True)
        wstep.setContentsMargins(0, 6, 0, 8)
        uk.addWidget(wstep)

        wiersz = QFrame()
        wiersz.setObjectName("settingRow")
        h = QHBoxLayout(wiersz)
        h.setContentsMargins(0, 10, 0, 10)
        h.addWidget(QLabel(_("Źródło wyników")), 1)
        self.combo_zrodlo_wyszukiwania = QComboBox()
        for kod, nazwa in (("wylaczone", _("Wyłączone")),
                           ("searxng", _("SearXNG na tym komputerze")),
                           ("searxng_adres", _("SearXNG pod innym adresem")),
                           ("ollama", _("Ollama (konto na ollama.com)"))):
            self.combo_zrodlo_wyszukiwania.addItem(nazwa, kod)
        self.combo_zrodlo_wyszukiwania.setMinimumWidth(320)
        zrodlo = wyszukiwanie_ustawienie("zrodlo", "wylaczone")
        self.combo_zrodlo_wyszukiwania.setCurrentIndex(max(0, self.combo_zrodlo_wyszukiwania.findData(zrodlo)))
        self.combo_zrodlo_wyszukiwania.currentIndexChanged.connect(self._zmiana_zrodla_wyszukiwania)
        h.addWidget(self.combo_zrodlo_wyszukiwania)
        uk.addWidget(wiersz)

        # --- SearXNG na tym komputerze ---
        self.ramka_searxng = QFrame()
        self.ramka_searxng.setObjectName("searchPanel")
        v = QVBoxLayout(self.ramka_searxng)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        wiersz = QFrame()
        wiersz.setObjectName("settingRow")
        h = QHBoxLayout(wiersz)
        h.setContentsMargins(0, 10, 0, 10)
        h.setSpacing(8)
        teksty = QVBoxLayout()
        teksty.setSpacing(2)
        teksty.addWidget(QLabel("SearXNG"))
        self.lbl_stan_searxng = _etykieta("", "muted")
        self.lbl_stan_searxng.setWordWrap(True)
        teksty.addWidget(self.lbl_stan_searxng)
        h.addLayout(teksty, 1)
        self.btn_searxng_zainstaluj = self._przycisk(_("Zainstaluj"), "pobierz", "primary",
                                                     slot=self.zainstaluj_searxng)
        self.btn_searxng_start = self._przycisk(_("Uruchom"), slot=self.przelacz_searxng)
        self.btn_searxng_otworz = self._przycisk(_("Otwórz"), "otworz", slot=self.otworz_searxng)
        self.btn_searxng_usun = self._przycisk(_("Usuń"), "kosz", "danger", slot=self.usun_searxng)
        for btn in (self.btn_searxng_zainstaluj, self.btn_searxng_start, self.btn_searxng_otworz,
                    self.btn_searxng_usun):
            h.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
        v.addWidget(wiersz)
        self.sw_searxng_autostart = self._wiersz_przelacznika(
            v, _("SearXNG startuje po zalogowaniu"), _("usługa użytkownika, bez hasła"),
            self.przelacz_searxng_autostart)
        uk.addWidget(self.ramka_searxng)

        # --- SearXNG pod innym adresem ---
        self.ramka_searxng_adres = QFrame()
        self.ramka_searxng_adres.setObjectName("searchPanel")
        v = QVBoxLayout(self.ramka_searxng_adres)
        v.setContentsMargins(0, 10, 0, 10)
        v.setSpacing(6)
        v.addWidget(QLabel(_("Adres SearXNG")))
        self.pole_searxng_adres = QLineEdit(wyszukiwanie_ustawienie("searxng_adres", ""))
        self.pole_searxng_adres.setPlaceholderText(_("np. http://192.168.0.10:8888"))
        self.pole_searxng_adres.editingFinished.connect(self._zapisz_adres_searxng)
        v.addWidget(self.pole_searxng_adres)
        opis = _etykieta(_("Twój własny SearXNG, np. na serwerze w domu. W jego settings.yml musi być "
                           "włączony format json (search: formats)."), "muted")
        opis.setWordWrap(True)
        v.addWidget(opis)
        uk.addWidget(self.ramka_searxng_adres)

        # --- Ollama ---
        self.ramka_wyszukiwania_ollama = QFrame()
        self.ramka_wyszukiwania_ollama.setObjectName("searchPanel")
        v = QVBoxLayout(self.ramka_wyszukiwania_ollama)
        v.setContentsMargins(0, 10, 0, 10)
        v.setSpacing(6)
        v.addWidget(QLabel(_("Klucz API Ollamy")))
        self.pole_klucz_ollama = QLineEdit(wyszukiwanie_ustawienie("klucz_ollama", ""))
        self.pole_klucz_ollama.setEchoMode(QLineEdit.EchoMode.Password)
        self.pole_klucz_ollama.setPlaceholderText(_("wklej klucz"))
        self.pole_klucz_ollama.editingFinished.connect(self._zapisz_klucz_ollama)
        v.addWidget(self.pole_klucz_ollama)
        opis = _etykieta(_("Wyszukiwarka Ollamy wymaga darmowego konta. Klucz utworzysz na "
                           "<a href=\"https://ollama.com/settings/keys\">ollama.com/settings/keys</a>. "
                           "Zapytania trafiają wtedy do ollama.com."), "muted")
        opis.setWordWrap(True)
        opis.setOpenExternalLinks(True)
        v.addWidget(opis)
        uk.addWidget(self.ramka_wyszukiwania_ollama)

        # --- Sprawdzenie ---
        self.ramka_test_wyszukiwania = QFrame()
        self.ramka_test_wyszukiwania.setObjectName("searchPanel")
        h = QHBoxLayout(self.ramka_test_wyszukiwania)
        h.setContentsMargins(0, 10, 0, 0)
        self.lbl_test_wyszukiwania = _etykieta("", "muted")
        self.lbl_test_wyszukiwania.setWordWrap(True)
        h.addWidget(self.lbl_test_wyszukiwania, 1)
        self.btn_test_wyszukiwania = self._przycisk(_("Sprawdź wyszukiwanie"), "szukaj",
                                                    slot=self.sprawdz_wyszukiwanie)
        h.addWidget(self.btn_test_wyszukiwania, 0, Qt.AlignmentFlag.AlignTop)
        uk.addWidget(self.ramka_test_wyszukiwania)
        self._pokaz_zrodlo_wyszukiwania()
        return karta

    def _pokaz_zrodlo_wyszukiwania(self):
        zrodlo = self.combo_zrodlo_wyszukiwania.currentData()
        self.ramka_searxng.setVisible(zrodlo == "searxng")
        self.ramka_searxng_adres.setVisible(zrodlo == "searxng_adres")
        self.ramka_wyszukiwania_ollama.setVisible(zrodlo == "ollama")
        self.ramka_test_wyszukiwania.setVisible(zrodlo != "wylaczone")

    def _zmiana_zrodla_wyszukiwania(self, _indeks=None):
        zrodlo = self.combo_zrodlo_wyszukiwania.currentData()
        wyszukiwanie_zapisz("zrodlo", zrodlo)
        self.lbl_test_wyszukiwania.setText("")
        self._pokaz_zrodlo_wyszukiwania()
        self._odswiez_przycisk_internetu()
        self.wpis_log(_("Źródło wyszukiwania w internecie: {zrodlo}.").format(
            zrodlo=self.combo_zrodlo_wyszukiwania.currentText()))

    def _odswiez_wyszukiwanie(self, stan):
        # WHAT: stan SearXNG na tym komputerze -> opis i przyciski karty.
        zainstalowane, dziala = stan["searxng"], stan["searxng_dziala"]
        w_toku = self._w_toku("searxng")
        if w_toku:
            opis = _("trwa instalacja albo aktualizacja - to może potrwać kilka minut...")
        elif not zainstalowane:
            opis = _("nie jest zainstalowany - instalacja dla bieżącego użytkownika, bez hasła "
                     "(wymaga internetu)")
        elif dziala:
            opis = _("działa - {adres}").format(adres=SEARXNG_URL)
        else:
            opis = _("zainstalowany, zatrzymany")
        self.lbl_stan_searxng.setText(opis)
        self.btn_searxng_zainstaluj.setVisible(not zainstalowane)
        self.btn_searxng_zainstaluj.setEnabled(not w_toku)
        self.btn_searxng_start.setVisible(zainstalowane)
        self.btn_searxng_start.setText(_("Zatrzymaj") if dziala else _("Uruchom"))
        self.btn_searxng_otworz.setVisible(zainstalowane and dziala)
        self.btn_searxng_usun.setVisible(zainstalowane)
        for btn in (self.btn_searxng_start, self.btn_searxng_usun):
            btn.setEnabled(not w_toku)
        self.sw_searxng_autostart.parentWidget().setVisible(zainstalowane)
        self.sw_searxng_autostart.ustaw_cicho(stan["searxng_autostart"])

    def zainstaluj_searxng(self):
        odp = QMessageBox.question(
            self, _("Zainstaluj SearXNG"),
            _("Zainstalować SearXNG teraz?\n\n"
              "Pobierze go z GitHuba i zainstaluje dla bieżącego użytkownika w katalogu domowym "
              "(bez Dockera, bez uprawnień administratora). Potrwa kilka minut.\n\n"
              "SearXNG będzie działał tylko na tym komputerze ({adres}).").format(adres=SEARXNG_URL))
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Instaluję SearXNG - to może potrwać kilka minut..."))

        def _po(sukces):
            if sukces:
                self.przelacz_searxng(uruchom=True)

        self._watki["searxng"] = self._uruchom_akcje(searxng_zainstaluj, _("Instalacja SearXNG"), _po, powiadom=True)
        if self._stan:
            self._odswiez_wyszukiwanie(self._stan)

    def przelacz_searxng(self, _klik=False, uruchom=None):
        if uruchom is None:
            uruchom = not (self._stan and self._stan["searxng_dziala"])
        if uruchom:
            self.wpis_log(_("Uruchamiam SearXNG..."))
            self._uruchom_akcje(searxng_uruchom, _("Uruchomienie SearXNG"))
        else:
            self._uruchom_akcje(searxng_zatrzymaj, _("Zatrzymanie SearXNG"))

    def otworz_searxng(self):
        QDesktopServices.openUrl(QUrl(SEARXNG_URL))

    def usun_searxng(self):
        odp = QMessageBox.question(
            self, _("Usuń SearXNG"),
            _("Usunąć SearXNG z tego komputera?\n\nZniknie usługa, program i jego ustawienia. "
              "Możesz go później zainstalować ponownie."))
        if odp != QMessageBox.StandardButton.Yes:
            return
        self._watki["searxng"] = self._uruchom_akcje(searxng_usun, _("Usunięcie SearXNG"))

    def przelacz_searxng_autostart(self, wlacz):
        opis = _("Włączenie autostartu SearXNG") if wlacz else _("Wyłączenie autostartu SearXNG")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: searxng_autostart(wlacz), opis)

    def _zapisz_adres_searxng(self):
        tekst = self.pole_searxng_adres.text().strip().rstrip("/")
        # WHY: jak przy hostach Ollamy - nie każemy pamiętać o "http://"
        if tekst and not re.match(r"^https?://", tekst):
            tekst = "http://" + tekst
            self.pole_searxng_adres.setText(tekst)
        if tekst != wyszukiwanie_ustawienie("searxng_adres", ""):
            wyszukiwanie_zapisz("searxng_adres", tekst)
            self.lbl_test_wyszukiwania.setText("")

    def _zapisz_klucz_ollama(self):
        tekst = self.pole_klucz_ollama.text().strip()
        if tekst != wyszukiwanie_ustawienie("klucz_ollama", ""):
            wyszukiwanie_zapisz("klucz_ollama", tekst)
            self.lbl_test_wyszukiwania.setText("")

    def sprawdz_wyszukiwanie(self):
        # WHAT: jedno próbne zapytanie do wybranego źródła - wynik pod kartą.
        self._zapisz_adres_searxng()
        self._zapisz_klucz_ollama()
        zrodlo = self.combo_zrodlo_wyszukiwania.currentData()
        self.btn_test_wyszukiwania.setEnabled(False)
        self.lbl_test_wyszukiwania.setText(_("Szukam..."))
        worker = FunkcjaWorker(lambda: wyszukaj_w_internecie("Ollama", 5, zrodlo))

        def _po(sukces, wynik):
            self.btn_test_wyszukiwania.setEnabled(True)
            if not sukces:
                self.lbl_test_wyszukiwania.setText(_("Błąd: {blad}").format(blad=wynik))
            elif not wynik:
                self.lbl_test_wyszukiwania.setText(_("Działa, ale nie zwrócił żadnych wyników."))
            else:
                self.lbl_test_wyszukiwania.setText(_("Działa - wyniki: {n}, pierwszy: {tytul}").format(
                    n=len(wynik), tytul=wynik[0]["tytul"] or wynik[0]["url"]))

        self._uruchom_watek("test_wyszukiwania", worker, worker.zakonczono, _po)

    def sprawdz_aktualizacje_searxng(self):
        # WHY: SearXNG nie ma wydań - "aktualizacja" to instalacja najnowszego stanu z GitHuba
        self._sprawdz_aktualizacje(
            "aktualizacja_searxng", "SearXNG", searxng_wersja_lokalna, searxng_wersja_najnowsza,
            self.btn_aktualizacje_searxng, searxng_aktualizuj,
            _("Pobierze najnowszy SearXNG z GitHuba i zainstaluje go od nowa\n"
              "(bez uprawnień administratora - wymaga internetu; ustawienia zostają)."))

    def _ustaw_kontekst(self, wartosc):
        # WHY: wartość spoza listy (np. 24576) wpisujemy jako tekst, nie przeskakujemy na "domyślne".
        indeks = self.combo_context_length.findData(wartosc)
        if indeks >= 0:
            self.combo_context_length.setCurrentIndex(indeks)
        else:
            self.combo_context_length.setEditText(wartosc)

    def _wartosc_kontekstu(self):
        tekst = self.combo_context_length.currentText().strip()
        indeks = self.combo_context_length.findText(tekst)
        return self.combo_context_length.itemData(indeks) if indeks >= 0 else tekst

    def _ustaw_combo_wg_wartosci(self, combo, wartosc):
        indeks = combo.findData(wartosc)
        combo.setCurrentIndex(indeks if indeks >= 0 else 0)

    def przelacz_autostart(self, wlacz):
        opis = _("Włączenie autostartu") if wlacz else _("Wyłączenie autostartu")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: usluga_autostart(wlacz), opis)

    def przelacz_litellm_autostart(self, wlacz):
        opis = _("Włączenie autostartu LiteLLM") if wlacz else _("Wyłączenie autostartu LiteLLM")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: litellm_autostart(wlacz, self.serwery), opis)

    def zastosuj_zalecane_zaawansowane(self):
        # WHAT: wypełnia formularz zalecanym profilem - NIC nie zapisuje samo.
        z = OLLAMA_ZALECANE
        self.pole_keep_alive.setText(z["OLLAMA_KEEP_ALIVE"])
        self._ustaw_kontekst(z["OLLAMA_CONTEXT_LENGTH"])
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
        # WHAT: wszystkie pola w jeden słownik i JEDEN zapis usluga_zapisz_env -
        #       jeden zapis, jeden restart Ollamy, niezależnie od liczby zmian.
        zmienne = {}
        keep_alive = self.pole_keep_alive.text().strip()
        if keep_alive:
            zmienne["OLLAMA_KEEP_ALIVE"] = keep_alive
        context_length = self._wartosc_kontekstu()
        if context_length and not context_length.isdigit():
            QMessageBox.warning(
                self, _("Błąd w OLLAMA_CONTEXT_LENGTH"),
                _('"{wartosc}" to nie jest liczba tokenów - wybierz rozmiar z listy albo wpisz '
                  'samą liczbę (np. 24576).').format(wartosc=context_length))
            return
        if context_length:
            zmienne["OLLAMA_CONTEXT_LENGTH"] = context_length
        for klucz, pole in (("OLLAMA_MAX_LOADED_MODELS", self.pole_max_loaded),
                            ("OLLAMA_NUM_PARALLEL", self.pole_num_parallel)):
            if pole.text().strip():
                zmienne[klucz] = pole.text().strip()
        for klucz, combo in (("OLLAMA_FLASH_ATTENTION", self.combo_flash_attention),
                             ("OLLAMA_KV_CACHE_TYPE", self.combo_kv_cache)):
            if combo.currentData():
                zmienne[klucz] = combo.currentData()
        # WHY: Vulkan i iGPU zawsze jawnie "1"/"0" - łatwiej odczytać, co jest ustawione.
        zmienne["OLLAMA_VULKAN"] = "1" if self.chk_vulkan.isChecked() else "0"
        zmienne["OLLAMA_IGPU_ENABLE"] = "1" if self.chk_igpu.isChecked() else "0"
        # WHY: HOST to adres - odznaczenie po prostu nie dopisuje klucza (tylko lokalnie).
        if self.chk_host.isChecked():
            zmienne["OLLAMA_HOST"] = "0.0.0.0:11434"
        if self.pole_ggml_vk_visible_devices.text().strip():
            zmienne["GGML_VK_VISIBLE_DEVICES"] = self.pole_ggml_vk_visible_devices.text().strip()
        zmienne["OLLAMA_GPU_OVERHEAD"] = self.combo_gpu_overhead.currentData()

        for numer, linia in enumerate(self.pole_wlasne_zmienne.toPlainText().splitlines(), start=1):
            linia = linia.strip()
            if not linia:
                continue
            if "=" not in linia:
                QMessageBox.warning(
                    self, _("Błąd we własnych parametrach"),
                    _('Linia {numer} ("{linia}") nie ma postaci KLUCZ=WARTOŚĆ - popraw ją przed zapisem.').format(
                        numer=numer, linia=linia))
                return
            klucz, wartosc = linia.split("=", 1)
            if klucz.strip():
                zmienne[klucz.strip()] = wartosc.strip()

        ip_allow, bledny = _sprawdz_ip_allow(self.pole_ip_allow.text())
        if bledny:
            QMessageBox.warning(
                self, _("Błąd w IPAddressAllow"),
                _('"{adres}" to nie jest poprawny adres IP, podsieć (CIDR) ani słowo kluczowe '
                  '(localhost, link-local, multicast, any) - popraw przed zapisem.').format(adres=bledny))
            return
        ip_allow = " ".join(ip_allow)
        odp = QMessageBox.question(
            self, _("Zapisanie ustawień Ollamy"),
            _("Zapisać wszystkie zmienne i uruchomić ponownie usługę Ollama?") + "\n\n" + _(
                "Wymaga uprawnień administratora - aktualnie załadowane\n"
                "modele zostaną na chwilę wyładowane z pamięci."))
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Zapisuję ustawienia Ollamy..."))
        self._ustawienia_wczytane = False  # WHY: po restarcie formularz pokaże faktycznie zapisany stan
        self._uruchom_akcje(lambda: usluga_zapisz_env(zmienne, ip_allow), _("Zapisanie ustawień Ollamy"))

    # =========================================================================
    #  Zasobnik systemowy
    # =========================================================================
    def _ustaw_zasobnik(self, wlacz):
        # WHAT: pokazuje albo chowa ikonę w zasobniku.
        # WHY:  przy włączonym zasobniku zamknięcie okna nie może kończyć programu -
        #       także zamknięcie okna dialogowego, gdy główne jest schowane (Qt
        #       uznałby je za "ostatnie okno" i zakończył cały program).
        dostepny = QSystemTrayIcon.isSystemTrayAvailable()
        if wlacz and dostepny:
            if self.zasobnik is None:
                self.zasobnik = QSystemTrayIcon(_ikona_programu(), self)
                self.zasobnik.activated.connect(self._klik_zasobnika)
            self._zbuduj_menu_zasobnika()
            self.zasobnik.show()
            self._odswiez_zasobnik()
        elif self.zasobnik is not None:
            self.zasobnik.hide()
        QApplication.setQuitOnLastWindowClosed(not (wlacz and dostepny))

    def _zbuduj_menu_zasobnika(self):
        # WHY: osobna funkcja - po zmianie języka menu trzeba zbudować na nowo
        menu = QMenu(self)
        menu.addAction(_("Otwórz panel zarządzania"), self.pokaz_okno)
        menu.addAction(_("Otwórz czat"), self.otworz_czat)  # WHY: wbudowany czat, jak przycisk w okienku zasobnika
        menu.addSeparator()
        menu.addAction(_("Zakończ"), self.zakoncz)
        self.zasobnik.setContextMenu(menu)
        self._menu_zasobnika = menu

    def _odswiez_zasobnik(self):
        if self.zasobnik is None:
            return
        stan = self._stan
        if stan:
            dzialajace = sum(1 for z, d in ((stan["zainstalowana"], stan["active"]),
                                            (stan["litellm"], stan["litellm_dziala"]),
                                            (stan["webui"], stan["webui_dziala"])) if z and d)
            self.zasobnik.setToolTip(f"{NAZWA_PROGRAMU} - " + _("działa: {n}").format(n=dzialajace))
        if self._zasobnik_otwarty():
            self.okno_zasobnika.odswiez()

    def _klik_zasobnika(self, powod):
        # WHAT: lewy klik = okienko z makiety, środkowy = główne okno (prawy = menu, robi Qt).
        if powod == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.pokaz_okno()
            return
        if powod != QSystemTrayIcon.ActivationReason.Trigger:
            return
        if self.okno_zasobnika is None:
            self.okno_zasobnika = OknoZasobnika(self)
        if self.okno_zasobnika.isVisible() or time.monotonic() - self.okno_zasobnika.zamkniete_o < 0.3:
            self.okno_zasobnika.hide()
            return
        self.okno_zasobnika.pokaz_przy(QCursor.pos())
        # WHY: świeże dane od razu, nie dopiero przy następnym tyknięciu zegara
        self.odswiez_hosty()
        self._tyk_zasobow()

    def pokaz_okno(self, strona=None):
        if strona:
            self._przejdz(strona)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def zakoncz(self):
        self._konczenie = True
        QApplication.quit()

    def closeEvent(self, zdarzenie):
        # WHAT: z włączonym zasobnikiem krzyżyk tylko chowa okno (decyzja użytkownika);
        #       program kończy "Zakończ" w menu ikony.
        if self.zasobnik is not None and self.zasobnik.isVisible() and not self._konczenie:
            zdarzenie.ignore()
            self.hide()
            if not _ustawienia().value("zasobnik/powiadomiono", False, type=bool):
                # WHY: raz - żeby nikt nie myślał, że program się zamknął, a on dalej działa
                _ustawienia().setValue("zasobnik/powiadomiono", True)
                self.zasobnik.showMessage(
                    NAZWA_PROGRAMU,
                    _("Program działa dalej w zasobniku. Zamkniesz go przez \"Zakończ\" w menu ikony."),
                    _ikona_programu(), 8000)
            return
        zdarzenie.accept()

    def przelacz_zasobnik(self, wlacz):
        if wlacz and not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.warning(self, _("Zasobnik systemowy"),
                                _("Zasobnik systemowy jest niedostępny w tym środowisku pulpitu."))
            self.sw_zasobnik.ustaw_cicho(False)
            return
        _ustawienia().setValue("zasobnik/wlaczony", wlacz)
        self._ustaw_zasobnik(wlacz)
        if not wlacz and autostart_programu_wlaczony():
            # WHY: start po zalogowaniu chowa okno do zasobnika - bez zasobnika
            #      program startowałby niewidoczny, więc autostart też wyłączamy.
            ustaw_autostart_programu(False)
            self.sw_autostart_programu.ustaw_cicho(False)
        self.sw_autostart_programu.setEnabled(wlacz)
        self.wpis_log(_("Ikona w zasobniku: {stan}.").format(stan=_("włączona") if wlacz else _("wyłączona")))

    def przelacz_wpis_menu(self, wlacz):
        try:
            ustaw_wpis_menu_appimage(wlacz)
        except (OSError, subprocess.SubprocessError) as e:
            self.wpis_log(_("Nie udało się zmienić wpisu w menu: {blad}").format(blad=e), "BŁĄD")
            if self.sw_wpis_menu is not None:
                self.sw_wpis_menu.ustaw_cicho(wpis_menu_appimage() is not None)
            return
        self.wpis_log(_("Program w menu aplikacji: {stan}.").format(
            stan=_("dodany") if wlacz else _("usunięty")))

    def zapytaj_o_wpis_menu(self):
        # WHAT: przy pierwszym uruchomieniu z AppImage - pytanie o dodanie do menu.
        # WHY:  jedno pytanie, raz (QSettings appimage/zapytano); później to samo
        #       zmienia przełącznik w Ustawieniach.
        ustawienia = _ustawienia()
        if not _APPIMAGE or wpis_menu_appimage() is not None or ustawienia.value("appimage/zapytano", False, type=bool):
            return
        ustawienia.setValue("appimage/zapytano", True)
        odp = QMessageBox.question(
            self, _("Dodać do menu?"),
            _("Dodać {nazwa} do menu aplikacji?\n\n"
              "Wpis wskaże ten plik AppImage:\n{plik}\n\n"
              "Możesz to zmienić później w Ustawieniach.").format(nazwa=NAZWA_PROGRAMU, plik=_APPIMAGE))
        if odp == QMessageBox.StandardButton.Yes:
            self.przelacz_wpis_menu(True)
            if self.sw_wpis_menu is not None:
                self.sw_wpis_menu.ustaw_cicho(True)

    def przelacz_autostart_programu(self, wlacz):
        try:
            ustaw_autostart_programu(wlacz)
        except OSError as e:
            self.wpis_log(_("Nie udało się zmienić autostartu programu: {blad}").format(blad=e), "BŁĄD")
            self.sw_autostart_programu.ustaw_cicho(autostart_programu_wlaczony())
            return
        self.wpis_log(_("Start programu po zalogowaniu: {stan}.").format(
            stan=_("włączony") if wlacz else _("wyłączony")))

    def _zmien_jezyk(self, index):
        # WHY: przebudowujemy CAŁE okno tą samą funkcją co przy starcie - żywa
        #      zmiana języka bez restartu, bo widżety i tak budujemy z danych w self.
        kod = self.combo_jezyk.itemData(index)
        if not kod or kod == self._jezyk_aktywny:
            return
        self._jezyk_aktywny = kod
        _wczytaj_jezyk(kod)
        _zapisz_jezyk(kod)
        self.setWindowTitle(_("{nazwa} - lokalny stos AI").format(nazwa=NAZWA_PROGRAMU))
        self._buduj_ui()
        # WHY: okienko i menu zasobnika też mają napisy - budujemy je od nowa
        if self.okno_zasobnika is not None:
            self.okno_zasobnika.deleteLater()
            self.okno_zasobnika = None
        if self.zasobnik is not None and self.zasobnik.isVisible():
            self._zbuduj_menu_zasobnika()
        self.odswiez()


PALETA = None  # WHY: tworzona w main() po wczytaniu theme.qss (patrz Paleta)
_PLIK_MOTYWU = Path(__file__).resolve().parent / "theme.qss"
_PLIK_IKONY = Path(__file__).resolve().parent / "logo.png"


def _ikona_programu():
    # WHAT: własna ikona programu (lama w goglach na stosie serwerów) - okno i pasek boczny.
    # WHY:  leży obok skryptu jak theme.qss; bez pliku - ikona z motywu systemu.
    #       logo.png ma ok. 11% przezroczystego marginesu z każdej strony - przycinamy
    #       go przy wczytaniu (do kwadratu wokół treści), żeby lama wypełniała ikonę
    #       także w małych rozmiarach (pasek zadań). Sam plik zostaje nietknięty.
    if not _PLIK_IKONY.exists():
        return QIcon.fromTheme("applications-system")
    obraz = QImage(str(_PLIK_IKONY))
    tresc = QRegion(QBitmap.fromImage(obraz.createAlphaMask())).boundingRect()
    if tresc.isEmpty():
        return QIcon(str(_PLIK_IKONY))
    bok = max(tresc.width(), tresc.height())
    kwadrat = QRect(tresc.center().x() - bok // 2, tresc.center().y() - bok // 2, bok, bok)
    return QIcon(QPixmap.fromImage(obraz.copy(kwadrat)))
_KATALOG_CZCIONEK = Path(__file__).resolve().parent / "fonts"


def _wczytaj_motyw(app):
    # WHAT: czcionki z fonts/ (Inter, JetBrains Mono) i arkusz stylów theme.qss.
    # WHY:  czcionki dołączone do programu wyglądają tak samo na każdym
    #       komputerze (JetBrains Mono rzadko jest zainstalowana). Kolory są
    #       tylko w theme.qss - brak pliku = zwykły motyw systemu.
    global PALETA
    for plik in sorted(_KATALOG_CZCIONEK.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(plik))
    try:
        app.setStyleSheet(_PLIK_MOTYWU.read_text(encoding="utf-8"))
    except OSError:
        pass
    PALETA = Paleta()


def main():
    _przenies_dane_ollama_manager()  # WHY: przed pierwszym użyciem QSettings
    _odswiez_sciezke_appimage()
    app = QApplication(sys.argv)
    app.setApplicationName(NAZWA_PROGRAMU)
    app.setWindowIcon(_ikona_programu())
    # WHY: KDE (Wayland) łączy okno z wpisem .desktop po tej nazwie - pasek zadań
    #      pokaże wtedy ikonę z menu, a nie ogólną ikonę Pythona.
    app.setDesktopFileName(ID_PROGRAMU)
    w_zasobniku = "--w-zasobniku" in sys.argv

    # WHAT: tylko jedna kopia programu naraz.
    # WHY:  z zasobnikiem program działa w tle (np. z autostartu) - drugie
    #       uruchomienie z menu pokazuje istniejące okno, zamiast drugiej ikony.
    nazwa_gniazda = f"{ID_PROGRAMU}-{getpass.getuser()}"
    gniazdo = QLocalSocket()
    gniazdo.connectToServer(nazwa_gniazda)
    if gniazdo.waitForConnected(300):
        if not w_zasobniku:
            gniazdo.write(b"pokaz")
            gniazdo.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(nazwa_gniazda)  # WHY: gniazdo po kopii, która się wywaliła
    serwer = QLocalServer()
    serwer.listen(nazwa_gniazda)

    _wczytaj_motyw(app)
    okno = MainWindow()
    def _nowe_polaczenie():
        # WHY: okno pokazujemy tylko na wiadomość "pokaz" - kopia z autostartu
        #      (--w-zasobniku) łączy się bez niej i ma po prostu się zakończyć.
        polaczenie = serwer.nextPendingConnection()
        polaczenie.readyRead.connect(
            lambda: okno.pokaz_okno() if bytes(polaczenie.readAll()).startswith(b"pokaz") else None)

    serwer.newConnection.connect(_nowe_polaczenie)
    # WHY: z autostartu (--w-zasobniku) tylko ikona - o ile zasobnik naprawdę działa
    if not (w_zasobniku and okno.zasobnik is not None and okno.zasobnik.isVisible()):
        okno.show()
        QTimer.singleShot(500, okno.zapytaj_o_wpis_menu)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
