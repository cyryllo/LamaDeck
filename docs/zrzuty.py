#!/usr/bin/env python3
# WHAT: zrzuty ekranu do instrukcji (docs/img/<język>/) i README - z PRAWDZIWYMI danymi tego
#       komputera (hosty, modele, usługi), okno 1600x900 bez wyświetlania (offscreen).
# WHY:  zrzuty trzeba odświeżać po zmianach w wyglądzie - jedno polecenie zamiast ręcznego
#       klikania. Rozmowy z "czat" zapisują się do katalogu tymczasowego (nie do Twojej historii),
#       a na zrzucie i tak widać zwykłą ścieżkę rozmów.
# UWAGA: "czat" wysyła PRAWDZIWE pytanie (z wyszukiwaniem w internecie) do gemma4 na tym
#        komputerze - zajmuje Ollamę na 2-3 minuty.
# Użycie: QT_QPA_PLATFORM=offscreen python3 docs/zrzuty.py <pl|en> docs/img/<pl|en> [czat]
#         potem panel/czat/zasobnik kopiuje się do screenshots/lamadeck-*(_pl).png
import os, sys, time, tempfile
from pathlib import Path
jezyk, wyjscie = sys.argv[1], Path(sys.argv[2]); z_czatem = "czat" in sys.argv[3:]
sys.argv = ["x"]; sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lamadeck as om
czaty = Path(tempfile.mkdtemp())
_prawdziwy_katalog = om.katalog_rozmow
# WHY: rozmowy z testu w katalogu tymczasowym, ale podpowiedź na ekranie pokazuje zwykłą ścieżkę
om.katalog_rozmow = lambda: _prawdziwy_katalog() if sys._getframe(1).f_code.co_name == "_strona_czat" else czaty
om._wczytaj_jezyk_zapisany = lambda: jezyk
om._wczytaj_jezyk(jezyk)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEventLoop, QTimer
app = QApplication([]); app.setApplicationName(om.NAZWA_PROGRAMU); om._wczytaj_motyw(app)
wyjscie.mkdir(parents=True, exist_ok=True)

def czekaj(ms):
    p = QEventLoop(); QTimer.singleShot(ms, p.quit); p.exec()

def zrzut(widget, nazwa):
    widget.grab().save(str(wyjscie / f"{nazwa}.png"))
    print("  ", nazwa)

w = om.MainWindow(); w.resize(1600, 900); w.show()
w._przejdz("panel"); czekaj(9000)
zrzut(w, "panel")
w._przejdz("hosty"); czekaj(4000); zrzut(w, "hosty")
w._przejdz("router"); czekaj(5000); zrzut(w, "router")
w._przejdz("webui"); czekaj(2500); zrzut(w, "webui")
w._przejdz("modele"); czekaj(4000)
w.pole_szukaj_model.setText("qwen3"); w.szukaj_modeli()
for _ in range(60):
    czekaj(500)
    if w.tabela_wynikow.rowCount() and not w._w_toku("szukaj"): break
for r in range(w.tabela_wynikow.rowCount()):
    if w.tabela_wynikow.item(r, 0).text() == "qwen3":
        w.tabela_wynikow.selectRow(r); break
for _ in range(60):
    czekaj(500)
    if w.tabela_wariantow.rowCount(): break
for r in range(w.tabela_wariantow.rowCount()):
    if "30b-a3b" in w.tabela_wariantow.item(r, 0).text() and "q4_K_M" in w.tabela_wariantow.item(r, 0).text():
        w.tabela_wariantow.selectRow(r); break
else:
    w.tabela_wariantow.selectRow(0)
czekaj(800); zrzut(w, "modele")
w._przejdz("dziennik"); czekaj(4000); zrzut(w, "dziennik")
w._przejdz("ustawienia"); czekaj(2500); zrzut(w, "ustawienia")
karta_czatu = w.combo_model_rag.parentWidget().parentWidget()
zrzut(karta_czatu, "ustawienia-czat")

if z_czatem:
    w._przejdz("czat"); czekaj(3000)
    c = w.combo_czat_model
    for i in range(c.count()):
        if "gemma4" in (c.itemData(i) or "") and "localhost" in (c.itemData(i) or ""):
            c.setCurrentIndex(i); break
    print("   model:", c.currentText())
    w.btn_czat_internet.setChecked(True)
    pytanie = {"pl": "Co nowego w najnowszej wersji Ollamy? Odpowiedz krótko, w kilku punktach.",
               "en": "What's new in the latest Ollama release? Keep it short, a few bullet points."}[jezyk]
    w.pole_czat.setPlainText(pytanie); w.wyslij_czat()
    start = time.time()
    while w._czat_worker is not None and time.time() - start < 900:
        czekaj(1000)
    czekaj(1500)
    print("   odpowiedź w %.0f s, źródeł: %d" % (time.time() - start, len(w._czat[-1].get("zrodla_www", []))))
    w.widok_czatu.verticalScrollBar().setValue(0); czekaj(300)
    zrzut(w, "czat")
    w.btn_czat_internet.setChecked(False)

w._przejdz("panel"); czekaj(3000)
okienko = om.OknoZasobnika(w); w.okno_zasobnika = okienko
okienko.show(); okienko.odswiez(); czekaj(2500); okienko.odswiez(); czekaj(500)
zrzut(okienko, "zasobnik")
