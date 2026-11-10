#!/usr/bin/env python3
# WHAT: zrzuty czatu w przeglądarce do instrukcji i README: strona na komputerze i na telefonie
#       (www.png, www-telefon.png) oraz karta "Czat w przeglądarce" z kodem QR (ustawienia-www.png).
# WHY:  strona to Chromium (QtWebEngine) - w trybie offscreen nie rysuje tekstu, więc ten skrypt
#       działa na ZWYKŁYM pulpicie (okno pokaże się na kilka minut). Twoich ustawień nie zmienia:
#       osobny serwer na wolnym porcie, rozmowy w katalogu tymczasowym, a ustawienia czatu
#       w przeglądarce do zrzutu karty są tylko udawane w pamięci (bez hasła - nikt się nie zaloguje).
# UWAGA: wysyła PRAWDZIWE pytanie do gemma4 na tym komputerze (ok. minuty pracy Ollamy).
# Użycie: python3 docs/zrzuty_www.py <pl|en> docs/img/<pl|en>
import sys, time, socket, tempfile
from pathlib import Path
jezyk, wyjscie = sys.argv[1], Path(sys.argv[2])
sys.argv = ["x"]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lamadeck as om
czaty = Path(tempfile.mkdtemp())
om.katalog_rozmow = lambda: czaty
om._wczytaj_jezyk_zapisany = lambda: jezyk
om._wczytaj_jezyk(jezyk)


def _wolny_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# WHY: karta w Ustawieniach "jak po włączeniu sieci domowej" - tylko w tym procesie
_udawane = {"wlaczony": True, "lan": True, "port": om.WWW_PORT_DOMYSLNY}
om.www_ustawienie = lambda klucz, domyslnie=None, typ=None: _udawane.get(klucz, domyslnie)
om.www_zapisz = lambda klucz, wartosc: None
om.www_jest_haslo = lambda: True
# WHY: na zrzucie domyślny port 11500, ale serwer startuje na wolnym (11500 zajmuje działający program)
_uruchom, _adresy = om.SerwerCzatu.uruchom, om.www_adresy
om.SerwerCzatu.uruchom = lambda self, port, lan: _uruchom(self, _wolny_port(), lan)
om.www_adresy = lambda port=None: _adresy(om.WWW_PORT_DOMYSLNY)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEventLoop, QTimer, QUrl, Qt
from PyQt6.QtWebEngineWidgets import QWebEngineView
app = QApplication(["lamadeck"])
app.setApplicationName(om.NAZWA_PROGRAMU)
om._wczytaj_motyw(app)
wyjscie.mkdir(parents=True, exist_ok=True)


def czekaj(ms):
    p = QEventLoop(); QTimer.singleShot(ms, p.quit); p.exec()


def zapisz(obraz, nazwa, szerokosc=None):
    # WHY: pulpit ze skalowaniem daje większe zrzuty - jeden rozmiar w całej instrukcji
    if szerokosc and obraz.width() > szerokosc:
        obraz = obraz.scaledToWidth(szerokosc, Qt.TransformationMode.SmoothTransformation)
    obraz.save(str(wyjscie / f"{nazwa}.png"))
    print("  ", nazwa)


# --- karta w Ustawieniach (adres w sieci domowej i kod QR) ---
okno = om.MainWindow()
okno.resize(1600, 900)
okno.show()
okno._przejdz("ustawienia")
czekaj(2000)
karta = okno.kod_qr_www.parentWidget().parentWidget()
zapisz(karta.grab(), "ustawienia-www", 1240)
okno.serwer_www.zatrzymaj()
okno.hide()

# --- strona czatu: osobny serwer tylko na tym komputerze ---
serwer = om.SerwerCzatu()
serwer.uruchom(_wolny_port(), False)
widok = QWebEngineView()
widok.resize(1400, 860)
widok.show()
widok.setUrl(QUrl(f"http://localhost:{serwer.port}/"))
czekaj(3000)


def js(kod):
    wynik = {}
    p = QEventLoop()
    widok.page().runJavaScript(kod, 0, lambda w: (wynik.update(w=w), p.quit()))
    QTimer.singleShot(5000, p.quit)
    p.exec()
    return wynik.get("w")


model = js("(S.modele.find(m => m.nazwa.startsWith('gemma4') && m.host === 'Lokalny') || "
           "S.modele.find(m => m.nazwa.startsWith('gemma4')) || {}).id")
if not model:
    sys.exit("Brak gemma4 na hostach - zrzut wymaga tego modelu.")
pytanie = {"pl": "Zaplanuj jednodniową wycieczkę w Tatry z Zakopanego - krótko: kilka punktów "
                 "i tabelka godzina | co robimy.",
           "en": "Plan a one-day hike in the Alps from Zermatt - briefly: a few points and a small table "
                 "time | what we do."}[jezyk]
js(f"S.model = {model!r}; E.model.value = S.model; pokazModel(); E.myslenie.checked = false; "
   f"E.pytanie.value = {pytanie!r}; wyslij();")
start = time.time()
czekaj(3000)
while time.time() - start < 600 and not js("Boolean(S.rozmowa && !S.rozmowa.trwa && !S.zrodlo)"):
    czekaj(1000)
czekaj(1500)
print("   odpowiedź w %.0f s" % (time.time() - start))
js("E.rozmowa.scrollTop = 0")
czekaj(300)
zapisz(widok.grab().toImage(), "www", 1600)
widok.resize(400, 820)
czekaj(1200)
js("E.rozmowa.scrollTop = 0")
czekaj(300)
zapisz(widok.grab().toImage(), "www-telefon")
serwer.zatrzymaj()
