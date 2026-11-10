/*
 * LamaDeck - czat w przeglądarce (1.2).
 *
 * WHAT: cały interfejs strony czatu: logowanie, lista rozmów, wybór modelu, rozmowa
 *       i pole pytania. Dane i odpowiedzi daje serwer w programie (lamadeck.py, SerwerCzatu):
 *         GET  /api/stan, /api/modele, /api/rozmowy, /api/rozmowa?id=, /api/strumien?id= (SSE)
 *         POST /api/zaloguj, /api/wyslij, /api/akcja, /api/zatrzymaj, /api/nazwa, /api/usun
 * WHY:  zwykły JavaScript bez bibliotek i bez kroku budowania (poza marked.js do Markdownu).
 *       Napisy po polsku w funkcji _() - to klucze słownika z lang/*.json, jak w programie;
 *       serwer podaje tłumaczenia dokładnie tych kluczy (szuka wywołań _() i _f() w tym pliku),
 *       więc teksty muszą być w podwójnych cudzysłowach i bez sklejania.
 * BEZPIECZEŃSTWO: treść od modelu i z plików wstawiamy jako tekst albo przez marked z
 *       wyłączonym surowym HTML i obrazkami; linki tylko http(s)/mailto, otwierane w nowej karcie.
 */
"use strict";

// --- Tłumaczenia ---
let T = {};

function _(tekst) {
  return T[tekst] || tekst;
}

function _f(tekst, wartosci) {
  // WHAT: _() + podstawienie {nazwa} i {nazwa:.1f} (jak .format() w Pythonie).
  return _(tekst).replace(/\{(\w+)(?::\.(\d)f)?\}/g, (calosc, nazwa, miejsca) => {
    if (!(nazwa in wartosci)) return calosc;
    const v = wartosci[nazwa];
    return miejsca !== undefined ? Number(v).toFixed(Number(miejsca)) : String(v);
  });
}

// --- Ikony (kształty jak w programie; kolor z CSS przez currentColor) ---
const IKONY = {
  kopiuj: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  odswiez: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  edytuj: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/>',
  swiat: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pobierz: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
  spinacz: '<path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  zamknij: '<path d="M6 6l12 12M18 6L6 18"/>',
  otworz: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
};

function ikona(nazwa) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true">' + IKONY[nazwa] + "</svg>";
}

function el(znacznik, klasa, tekst) {
  const e = document.createElement(znacznik);
  if (klasa) e.className = klasa;
  if (tekst !== undefined) e.textContent = tekst;
  return e;
}

function przyciskIkona(nazwa, opis, klasa) {
  const b = el("button", klasa || "mala-ikona");
  b.type = "button";
  b.innerHTML = ikona(nazwa);
  b.title = opis;
  b.setAttribute("aria-label", opis);
  return b;
}

// --- Stan strony ---
const S = {
  stan: null,          // /api/stan
  modele: [],          // /api/modele - pary model+host
  kilkaHostow: false,
  model: null,         // id wybranego modelu ("adres|nazwa")
  rozmowy: [],
  rozmowa: null,       // otwarta rozmowa z /api/rozmowa; null = nowa
  zrodlo: null,        // EventSource pisanej odpowiedzi
  szukaj: false,       // przycisk z globusem
  szukane: "",
  kody: [],            // treść bloków kodu pod przyciskami "kopiuj"
  katalogi: [],        // katalogi robocze wybrane kiedyś w programie (/api/katalogi)
  domyslnyTryb: "pytaj",
  katalog: null,       // wybrany katalog roboczy (ścieżka) albo null
  zalaczniki: [],      // pliki do wysłania: {nazwa, typ, rozmiar, token, stan: "wysyla"|"gotowy"|"blad", blad}
  podglad: null,       // otwarty podgląd pliku: {sciezka, tryb, dane, mtime}
  prosbaNr: null,      // numer pokazanej prośby o zapis
};
const E = {};          // elementy strony

function zapamietaj(klucz, wartosc) {
  // WHY: localStorage bywa niedostępne (tryb prywatny) - wtedy po prostu nie pamiętamy wyboru
  try { localStorage.setItem("lamadeck." + klucz, JSON.stringify(wartosc)); } catch (e) { /* bez pamięci */ }
}

function przypomnij(klucz, domyslnie) {
  try {
    const v = localStorage.getItem("lamadeck." + klucz);
    return v === null ? domyslnie : JSON.parse(v);
  } catch (e) {
    return domyslnie;
  }
}

// --- Serwer ---
class BladApi extends Error {
  constructor(tekst, kod) { super(tekst); this.kod = kod; }
}

async function api(sciezka, dane) {
  const opcje = { credentials: "same-origin", headers: {} };
  if (dane !== undefined) {
    opcje.method = "POST";
    opcje.headers["Content-Type"] = "application/json";
    opcje.body = JSON.stringify(dane);
  }
  let odp;
  try {
    odp = await fetch(sciezka, opcje);
  } catch (e) {
    throw new BladApi(_("Brak połączenia z programem LamaDeck - czy działa na komputerze?"), 0);
  }
  let wynik = {};
  try { wynik = await odp.json(); } catch (e) { /* pusta odpowiedź */ }
  if (odp.status === 401 && S.stan && S.stan.wymaga_hasla) {
    pokazLogowanie();
    throw new BladApi(wynik.blad || _("Zaloguj się"), 401);
  }
  if (!odp.ok) throw new BladApi(wynik.blad || ("HTTP " + odp.status), odp.status);
  return wynik;
}

// --- Start ---
async function start() {
  try {
    S.stan = await api("/api/stan");
  } catch (e) {
    document.getElementById("app").replaceChildren(el("p", "powitanie", e.message));
    return;
  }
  T = S.stan.tlumaczenia || {};
  document.documentElement.lang = S.stan.jezyk || "pl";
  document.title = S.stan.nazwa;
  if (S.stan.wymaga_hasla && !S.stan.zalogowany) {
    pokazLogowanie();
    return;
  }
  zbudujUklad();
  S.szukaj = false;
  await Promise.all([wczytajModele(), wczytajRozmowy(), wczytajKatalogi()]);
  const id = (location.hash.match(/^#r=([\w-]+)$/) || [])[1];
  if (id) otworzRozmowe(id); else pokazRozmowe();
}

function pokazLogowanie() {
  zamknijStrumien();
  const forma = el("form");
  const logo = el("img");
  logo.src = "/logo.png";
  logo.alt = "";
  const haslo = el("input");
  haslo.type = "password";
  haslo.autocomplete = "current-password";
  haslo.placeholder = _("Hasło");
  haslo.required = true;
  const blad = el("p");
  const przycisk = el("button", "przycisk glowny", _("Zaloguj"));
  przycisk.type = "submit";
  forma.append(logo, el("h1", "", S.stan.nazwa),
    el("p", "", _("Czat w przeglądarce. Hasło ustawisz w programie: Ustawienia → Czat w przeglądarce.")),
    haslo, przycisk, blad);
  forma.addEventListener("submit", async (z) => {
    z.preventDefault();
    przycisk.disabled = true;
    blad.textContent = "";
    try {
      await api("/api/zaloguj", { haslo: haslo.value });
      S.stan = await api("/api/stan");
      start();
    } catch (e) {
      blad.textContent = e.message;
      przycisk.disabled = false;
      haslo.select();
    }
  });
  const ekran = el("div", "logowanie");
  ekran.append(forma);
  document.getElementById("app").replaceChildren(ekran);
  haslo.focus();
}

function zbudujUklad() {
  const app = document.getElementById("app");
  const uklad = el("div", "uklad");
  E.uklad = uklad;

  // --- pasek z rozmowami ---
  const pasek = el("aside", "pasek");
  const marka = el("div", "marka");
  const logo = el("img");
  logo.src = "/logo.png";
  logo.alt = "";
  const nazwa = el("div");
  nazwa.append(el("b", "", S.stan.nazwa), el("small", "", _("Czat w przeglądarce")));
  marka.append(logo, nazwa);
  const nowa = el("button", "przycisk", "");
  nowa.type = "button";
  nowa.innerHTML = ikona("plus");
  nowa.append(_("Nowa rozmowa"));
  nowa.addEventListener("click", () => { nowaRozmowa(); zamknijMenu(); });
  E.szukaj = el("input");
  E.szukaj.type = "search";
  E.szukaj.placeholder = _("Szukaj w rozmowach...");
  let opoznienie = null;
  E.szukaj.addEventListener("input", () => {
    clearTimeout(opoznienie);
    opoznienie = setTimeout(() => { S.szukane = E.szukaj.value; wczytajRozmowy(); }, 250);
  });
  E.lista = el("nav", "lista-rozmow");
  E.lista.setAttribute("aria-label", _("Rozmowy"));
  const stopka = el("div", "stopka");
  stopka.append(el("span", "", "v" + S.stan.wersja));
  if (S.stan.wymaga_hasla) {
    const wyloguj = el("button", "", _("Wyloguj"));
    wyloguj.type = "button";
    wyloguj.addEventListener("click", async () => {
      try { await api("/api/wyloguj", {}); } catch (e) { /* i tak pokazujemy logowanie */ }
      pokazLogowanie();
    });
    stopka.append(wyloguj);
  }
  pasek.append(marka, nowa, E.szukaj, E.lista, stopka);

  // --- rozmowa ---
  const glowna = el("main", "glowna");
  const gora = el("header", "gora");
  const menu = przyciskIkona("menu", _("Rozmowy"), "przycisk ikona menu-przycisk");
  menu.addEventListener("click", () => uklad.classList.add("menu-otwarte"));
  E.model = el("select");
  E.model.setAttribute("aria-label", _("Model"));
  E.model.addEventListener("change", () => {
    S.model = E.model.value;
    zapamietaj("model", S.model);
    pokazModel();
  });
  E.opisModelu = el("div", "opis-modelu");
  const mysl = el("label", "przelacznik");
  E.myslenie = el("input");
  E.myslenie.type = "checkbox";
  E.myslenie.checked = przypomnij("myslenie", true);
  E.myslenie.addEventListener("change", () => zapamietaj("myslenie", E.myslenie.checked));
  mysl.append(E.myslenie, _("Myślenie modelu"));
  E.przelacznikMyslenia = mysl;
  E.kontekst = el("div", "kontekst");
  E.kontekst.title = _("Ile okna kontekstu modelu zajmuje już ta rozmowa (tokeny)");
  E.liczbyKontekstu = el("div", "liczby", "");
  const miernik = el("div", "miernik");
  E.pasekKontekstu = el("div");
  miernik.append(E.pasekKontekstu);
  E.kontekst.append(E.liczbyKontekstu, miernik);
  gora.append(menu, E.model, E.opisModelu, mysl, E.kontekst);

  E.rozmowa = el("div", "rozmowa");
  E.rozmowa.setAttribute("aria-live", "polite");
  E.rozmowa.addEventListener("click", klikWRozmowie);
  E.uwaga = el("div", "uwaga-czatu");
  E.uwaga.hidden = true;

  E.chipy = el("div", "chipy");
  E.chipy.hidden = true;
  E.ramkaKatalogu = el("div", "ramka-katalogu");
  E.ramkaKatalogu.hidden = true;
  const pole = el("form", "pole");
  E.plik = el("input");
  E.plik.type = "file";
  E.plik.multiple = true;
  E.plik.hidden = true;
  E.plik.addEventListener("change", () => { dodajPliki(E.plik.files); E.plik.value = ""; });
  E.zalacz = przyciskIkona("spinacz", _("Dołącz zdjęcie, dokument (PDF, Word, Excel, PowerPoint, LibreOffice, EPUB) albo plik tekstowy (kod, .txt, .md, .csv...). Możesz też przeciągnąć plik do okna albo wkleić zrzut ekranu (Ctrl+V)."), "przycisk ikona");
  E.zalacz.addEventListener("click", () => E.plik.click());
  E.katalogPrzycisk = przyciskIkona("folder", _("Katalog roboczy: model może przeglądać i czytać pliki w wybranym folderze, a zapisywać je tylko po Twojej zgodzie. Wymaga modelu z tagiem \"narzędzia\"."), "przycisk ikona");
  E.katalogPrzycisk.addEventListener("click", () => menuKatalogow(E.katalogPrzycisk));
  E.internet = przyciskIkona("swiat", "", "przycisk ikona");
  E.internet.hidden = !S.stan.szukanie;
  E.internet.addEventListener("click", () => {
    S.szukaj = !S.szukaj;
    pokazPrzyciskInternetu();
  });
  E.pytanie = el("textarea");
  E.pytanie.rows = 1;
  E.pytanie.placeholder = dotykowy() ? _("Napisz wiadomość...")
    : _("Napisz wiadomość... (Enter wysyła, Shift+Enter - nowa linia)");
  E.pytanie.addEventListener("input", dopasujPole);
  E.pytanie.addEventListener("keydown", (z) => {
    // WHY: na telefonie Enter to nowa linia (klawiatura ekranowa nie ma Shift+Enter) - wysyła przycisk
    if (z.key === "Enter" && !z.shiftKey && !z.isComposing && !dotykowy()) {
      z.preventDefault();
      wyslij();
    }
  });
  E.wyslij = el("button", "przycisk glowny", _("Wyślij"));
  E.wyslij.type = "submit";
  pole.addEventListener("submit", (z) => { z.preventDefault(); wyslij(); });
  E.pytanie.addEventListener("paste", (z) => {
    // WHY: zrzut ekranu ze schowka (Ctrl+V) jak w programie
    if (z.clipboardData && z.clipboardData.files.length) {
      z.preventDefault();
      dodajPliki(z.clipboardData.files);
    }
  });
  pole.append(E.plik, E.zalacz, E.katalogPrzycisk, E.internet, E.pytanie, E.wyslij);
  glowna.append(gora, E.rozmowa, E.uwaga, E.chipy, E.ramkaKatalogu, pole);
  glowna.addEventListener("dragover", (z) => { if (z.dataTransfer.types.includes("Files")) z.preventDefault(); });
  glowna.addEventListener("drop", (z) => {
    if (!z.dataTransfer.files.length) return;
    z.preventDefault();
    dodajPliki(z.dataTransfer.files);
  });
  E.podglad = zbudujPodglad();

  const zaslona = el("div", "zaslona");
  zaslona.addEventListener("click", zamknijMenu);
  E.zaslona = zaslona;
  zaslona.hidden = true;
  uklad.append(pasek, glowna, E.podglad, zaslona);
  app.replaceChildren(uklad);
  new MutationObserver(() => { zaslona.hidden = !uklad.classList.contains("menu-otwarte"); })
    .observe(uklad, { attributes: true, attributeFilter: ["class"] });
  pokazPrzyciskInternetu();

  // WHY: po powrocie do karty (telefon zasnął, rozmowa zmieniła się w programie) - świeże dane
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    wczytajRozmowy();
    if (S.rozmowa && !S.zrodlo) otworzRozmowe(S.rozmowa.id, true);
  });
  window.addEventListener("hashchange", () => {
    const id = (location.hash.match(/^#r=([\w-]+)$/) || [])[1];
    if (id && (!S.rozmowa || S.rozmowa.id !== id)) otworzRozmowe(id);
  });
  setInterval(odswiezLicznik, 1000);
}

function dotykowy() {
  return window.matchMedia("(pointer: coarse)").matches;
}

function zamknijMenu() {
  E.uklad.classList.remove("menu-otwarte");
}

function dopasujPole() {
  E.pytanie.style.height = "auto";
  E.pytanie.style.height = Math.min(E.pytanie.scrollHeight + 2, window.innerHeight * 0.4) + "px";
}

function pokazPrzyciskInternetu() {
  E.internet.classList.toggle("wlaczony", S.szukaj);
  E.internet.setAttribute("aria-pressed", String(S.szukaj));
  const opis = _f("Szukaj w internecie: {stan}", { stan: S.szukaj ? _("włączone") : _("wyłączone") });
  E.internet.title = opis;
  E.internet.setAttribute("aria-label", opis);
}

function uwaga(tekst, stan) {
  E.uwaga.textContent = tekst || "";
  E.uwaga.className = "uwaga-czatu" + (stan ? " " + stan : "");
  E.uwaga.hidden = !tekst;
}

function informacja(tekst) {
  const d = el("div", "dymek-info", tekst);
  document.body.append(d);
  setTimeout(() => d.remove(), 1800);
}

// --- Modele ---
async function wczytajModele(odswiez) {
  try {
    const wynik = await api("/api/modele" + (odswiez ? "?odswiez=1" : ""));
    S.modele = wynik.modele;
    S.kilkaHostow = wynik.kilka_hostow;
    const zapamietany = przypomnij("model", null);
    const ids = S.modele.map((m) => m.id);
    S.model = ids.includes(S.model) ? S.model
      : ids.includes(zapamietany) ? zapamietany
        : ids.includes(wynik.domyslny) ? wynik.domyslny : ids[0] || null;
  } catch (e) {
    if (e.kod !== 401) uwaga(e.message, "error");
  }
  E.model.replaceChildren();
  // WHY: modele OCR tylko ze zdjęciem w pytaniu (jak w programie) - bez niego powtarzają pytanie
  const zeZdjeciem = S.zalaczniki.some((z) => z.typ === "obraz");
  for (const m of S.modele.filter((m) => zeZdjeciem || !m.tylko_ze_zdjeciem)) {
    const o = el("option", "", m.krotka + (S.kilkaHostow ? "  ·  " + m.host : ""));
    o.value = m.id;
    o.title = m.nazwa + " → " + m.host;
    E.model.append(o);
  }
  if (!S.modele.length) E.model.append(el("option", "", _("brak modeli")));
  E.model.disabled = !S.modele.length;
  if (S.model) E.model.value = S.model;
  pokazModel();
}

function wybranyModel() {
  return S.modele.find((m) => m.id === S.model) || null;
}

function pokazModel() {
  const m = wybranyModel();
  E.opisModelu.replaceChildren();
  if (m) {
    const czesci = [(m.rozmiar / 1024 ** 3).toFixed(1) + " GB"];
    if (m.parametry) czesci.push(m.parametry);
    if (m.kwantyzacja) czesci.push(m.kwantyzacja);
    czesci.push(m.host);
    E.opisModelu.append(el("span", "", czesci.join(" · ")));
    const tag = el("span", "tag" + (m.w_pamieci ? " ok" : ""), m.w_pamieci ? _("w pamięci") : _("na dysku"));
    tag.title = m.w_pamieci ? _("model jest już wczytany - odpowie od razu")
      : _("wczyta się do pamięci przy pierwszym pytaniu");
    E.opisModelu.append(tag);
    if (m.zdjecia) E.opisModelu.append(el("span", "tag", _("zdjęcia")));
    if (m.mysli) E.opisModelu.append(el("span", "tag", _("myślenie")));
  }
  // WHY: przełącznik ma sens tylko dla modeli, które myślą
  E.myslenie.disabled = !(m && m.mysli);
  E.przelacznikMyslenia.title = m && m.mysli ? _("potrafi rozważyć odpowiedź przed jej napisaniem")
    : _("ten model nie myśli przed odpowiedzią");
  pokazPrzyciskWyslij();
}

// --- Lista rozmów ---
async function wczytajRozmowy() {
  try {
    const wynik = await api("/api/rozmowy?q=" + encodeURIComponent(S.szukane || ""));
    S.rozmowy = wynik.rozmowy;
  } catch (e) {
    return;
  }
  pokazListe();
}

function pokazListe() {
  E.lista.replaceChildren();
  if (!S.rozmowy.length) {
    E.lista.append(el("div", "pusto", S.szukane ? _("Nic nie znaleziono.") : _("Nie ma jeszcze zapisanych rozmów.")));
    return;
  }
  for (const r of S.rozmowy) {
    const poz = el("div", "rozmowa-poz" + (S.rozmowa && S.rozmowa.id === r.id ? " wybrana" : ""));
    poz.tabIndex = 0;
    poz.setAttribute("role", "button");
    const data = new Date(r.zmieniono * 1000).toLocaleString(S.stan.jezyk, {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
    const model = (r.model || "").split("/").pop().split(":")[0];
    poz.append(el("div", "tytul", r.tytul), el("div", "opis", data + (model ? "  ·  " + model : "")));
    poz.title = r.tytul;
    const wiecej = el("button", "wiecej", "⋯");
    wiecej.type = "button";
    wiecej.title = _("Więcej");
    wiecej.setAttribute("aria-label", _("Więcej"));
    wiecej.addEventListener("click", (z) => { z.stopPropagation(); menuRozmowy(r, wiecej); });
    poz.append(wiecej);
    poz.addEventListener("click", () => { otworzRozmowe(r.id); zamknijMenu(); });
    poz.addEventListener("keydown", (z) => { if (z.key === "Enter") { otworzRozmowe(r.id); zamknijMenu(); } });
    poz.addEventListener("contextmenu", (z) => { z.preventDefault(); menuRozmowy(r, poz, z); });
    E.lista.append(poz);
  }
}

function menuRozmowy(r, przy, zdarzenie) {
  const zaslona = el("div", "zaslona przezroczysta");
  const menu = el("div", "menu");
  const zamknij = () => { menu.remove(); zaslona.remove(); };
  zaslona.addEventListener("click", zamknij);
  const pozycje = [
    [_("Zmień nazwę"), () => zmienNazwe(r)],
    [_("Eksportuj do Markdown..."), () => { location.href = "/api/eksport?id=" + encodeURIComponent(r.id); }],
    [_("Usuń rozmowę"), () => usunRozmowe(r), "niebezpieczne"],
  ];
  for (const [tekst, akcja, klasa] of pozycje) {
    const b = el("button", klasa || "", tekst);
    b.type = "button";
    b.addEventListener("click", () => { zamknij(); akcja(); });
    menu.append(b);
  }
  document.body.append(zaslona, menu);
  const p = przy.getBoundingClientRect();
  const x = zdarzenie ? zdarzenie.clientX : p.right - menu.offsetWidth;
  const y = zdarzenie ? zdarzenie.clientY : p.bottom + 4;
  menu.style.left = Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8)) + "px";
  menu.style.top = Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8)) + "px";
}

async function zmienNazwe(r) {
  const tytul = prompt(_("Nazwa rozmowy (puste pole = tytuł z pierwszego pytania):"), r.tytul);
  if (tytul === null) return;
  try {
    await api("/api/nazwa", { id: r.id, tytul: tytul });
  } catch (e) {
    uwaga(e.message, "warn");
  }
  await wczytajRozmowy();
}

async function usunRozmowe(r) {
  if (!confirm(_f("Usunąć rozmowę \"{tytul}\"? Tego nie da się cofnąć.", { tytul: r.tytul }))) return;
  try {
    await api("/api/usun", { id: r.id });
  } catch (e) {
    uwaga(e.message, "warn");
    return;
  }
  if (S.rozmowa && S.rozmowa.id === r.id) nowaRozmowa();
  await wczytajRozmowy();
}

// --- Rozmowa ---
function nowaRozmowa() {
  zamknijStrumien();
  zamknijPodglad();
  S.rozmowa = null;
  history.replaceState(null, "", location.pathname);
  uwaga("");
  pokazRozmowe();
  pokazListe();
  E.pytanie.focus();
}

async function otworzRozmowe(id, cicho) {
  let r;
  try {
    r = await api("/api/rozmowa?id=" + encodeURIComponent(id));
  } catch (e) {
    if (!cicho) uwaga(e.kod === 400 ? _("Tej rozmowy już nie ma.") : e.message, "warn");
    if (cicho && S.rozmowa && S.rozmowa.id === id) nowaRozmowa();
    return;
  }
  const naDole = blisko(E.rozmowa);
  const inna = !S.rozmowa || S.rozmowa.id !== id;
  S.rozmowa = r;
  if (inna) {
    zamknijStrumien();
    zamknijPodglad();
    uwaga("");
    // WHY: katalog roboczy wraca razem z rozmową (jak w programie)
    S.katalog = r.katalog || null;
    pokazKatalog();
    history.replaceState(null, "", "#r=" + id);
    // WHY: dalej rozmawiamy tym samym modelem - o ile jest na liście (jak w programie)
    const dotychczasowy = r.host + "|" + r.model;
    if (S.modele.some((m) => m.id === dotychczasowy)) {
      S.model = dotychczasowy;
      E.model.value = dotychczasowy;
      pokazModel();
    }
  }
  pokazRozmowe(inna || naDole);
  pokazListe();
  if (r.w_programie) uwaga(_("W tej rozmowie pisze się teraz odpowiedź w programie - poczekaj, aż się skończy."), "warn");
  pokazProsbe(r.trwa ? r.wiadomosci[r.wiadomosci.length - 1].prosba : null);
  if (r.trwa) sluchaj(id);
}

function blisko(przewijany) {
  return przewijany.scrollHeight - przewijany.scrollTop - przewijany.clientHeight < 80;
}

function przewinNaDol() {
  E.rozmowa.scrollTop = E.rozmowa.scrollHeight;
}

function pokazRozmowe(naDol) {
  S.kody = [];
  E.rozmowa.replaceChildren();
  const wiadomosci = S.rozmowa ? S.rozmowa.wiadomosci : [];
  if (!wiadomosci.length) {
    const p = el("div", "powitanie");
    const logo = el("img");
    logo.src = "/logo.png";
    logo.alt = "";
    p.append(logo, el("p", "", S.modele.length ? _("Wybierz model u góry i zadaj pytanie.")
      : _("Brak modeli do rozmowy na hostach online. Pobierz model na ekranie \"Modele\".")));
    E.rozmowa.append(p);
  }
  wiadomosci.forEach((w, i) => E.rozmowa.append(wiadomoscElement(w, i, wiadomosci)));
  pokazKontekst();
  pokazPrzyciskWyslij();
  if (naDol) przewinNaDol();
}

function wiadomoscElement(w, i, wszystkie) {
  const blok = el("article", "wiadomosc " + (w.rola === "user" ? "user" : w.rola === "blad" ? "blad" : "model"));
  blok.dataset.indeks = i;
  const glowka = el("div", "naglowek");
  const ostatniaPara = i >= wszystkie.length - 2 && wszystkie.length >= 2 &&
    wszystkie[wszystkie.length - 2].rola === "user" && !wszystkie[wszystkie.length - 1].trwa && !(S.rozmowa && S.rozmowa.trwa);
  if (w.rola === "user") {
    glowka.append(el("b", "ty", _("Ty")));
    if (ostatniaPara && i === wszystkie.length - 2) {
      const b = przyciskIkona("edytuj", _("Popraw pytanie - wróci do pola, a ono i odpowiedź znikną z rozmowy"));
      b.dataset.akcja = "popraw";
      glowka.append(b);
    }
  } else {
    glowka.append(el("b", "", w.rola === "blad" ? _("Błąd") : (w.kto || _("Model"))));
    if (w.stat) glowka.append(el("span", "stat", w.stat));
    if (w.rola === "assistant" && !w.trwa && odpowiedzBezMyslenia(w.tresc || "").trim()) {
      const b = przyciskIkona("kopiuj", _("Kopiuj odpowiedź"));
      b.dataset.kopiuj = S.kody.push(odpowiedzBezMyslenia(w.tresc).trim()) - 1;
      glowka.append(b);
    }
    if (ostatniaPara && i === wszystkie.length - 1) {
      const b = przyciskIkona("odswiez", _("Wygeneruj ponownie - modelem wybranym teraz"));
      b.dataset.akcja = "ponow";
      glowka.append(b);
    }
  }
  const dymek = el("div", "dymek");
  dymek.dataset.indeks = i;
  wypelnijDymek(dymek, w);
  blok.append(glowka, dymek);
  return blok;
}

function odpowiedzBezMyslenia(tresc) {
  // WHAT: odpowiedź bez <think>...</think> (część modeli pisze rozumowanie wprost w treści).
  const t = tresc.trimStart();
  if (!t.startsWith("<think>")) return tresc;
  const koniec = t.indexOf("</think>");
  return koniec < 0 ? "" : t.slice(koniec + 8).trimStart();
}

function mysleniezTresci(w) {
  const t = (w.tresc || "").trimStart();
  if (!t.startsWith("<think>")) return w.myslenie || "";
  const koniec = t.indexOf("</think>");
  return (w.myslenie || "") + (koniec < 0 ? t.slice(7) : t.slice(7, koniec));
}

function wypelnijDymek(dymek, w) {
  dymek.replaceChildren();
  if (w.rola === "user") {
    dymek.append(document.createTextNode(w.tresc || ""));
    if (w.zalaczniki && w.zalaczniki.length) {
      const z = el("div", "zalaczniki");
      w.zalaczniki.forEach((zal, nr) => {
        if (zal.typ === "obraz" && S.rozmowa && dymek.dataset.indeks !== undefined) {
          const img = el("img", "miniatura");
          img.alt = zal.nazwa;
          img.title = zal.nazwa;
          img.loading = "lazy";
          img.src = "/api/obraz?id=" + encodeURIComponent(S.rozmowa.id) + "&w=" + dymek.dataset.indeks + "&z=" + nr;
          z.append(img);
          return;
        }
        const opis = zal.url ? "🔗 " + zal.url : (zal.typ === "obraz" ? "🖼 " : "📎 ") + zal.nazwa;
        z.append(el("span", "zalacznik", opis));
      });
      dymek.append(z);
    }
    return;
  }
  if (w.rola === "blad") {
    dymek.textContent = w.tresc || "";
    return;
  }
  if (w.narzedzia && w.narzedzia.length) {
    const kroki = el("div", "kroki");
    for (const k of w.narzedzia) kroki.append(el("div", k.stan === "blad" ? "blad" : "", "› " + k.opis));
    dymek.append(kroki);
  }
  const myslenie = mysleniezTresci(w);
  if (myslenie.trim()) {
    const d = el("details", "mysl");
    d.append(el("summary", "", _("Rozumowanie modelu")), el("div", "", myslenie.trim()));
    dymek.append(d);
  }
  const odpowiedz = odpowiedzBezMyslenia(w.tresc || "");
  if (odpowiedz.trim()) {
    const tresc = el("div", "markdown");
    tresc.innerHTML = markdown(odpowiedz);
    dymek.append(tresc);
  }
  // WHAT: pliki zapisane w tej odpowiedzi - klik otwiera podgląd (jak w programie)
  const zapisane = [...new Set((w.narzedzia || []).filter((k) => (k.nazwa === "write_file" || k.nazwa === "edit_file")
    && k.stan === "ok").map((k) => k.sciezka))];
  if (zapisane.length && !w.trwa) {
    const linia = el("div", "zapisane");
    linia.append(el("b", "", _("Zapisane pliki:")), " ");
    zapisane.forEach((sciezka, n) => {
      if (n) linia.append(" · ");
      const b = el("button", "link-pliku", sciezka);
      b.type = "button";
      b.dataset.plik = sciezka;
      b.title = _("Pokaż podgląd obok rozmowy");
      linia.append(b);
    });
    dymek.append(linia);
  }
  if (w.trwa) {
    const stan = el("div", "stan");
    stan.dataset.start = w.start || "";
    if (w.pliki) {
      stan.dataset.pliki = "1";
      stan.dataset.ostatni = w.ostatni || w.start || "";
    }
    stan.dataset.tekst = tekstStanu(w, odpowiedz);
    stan.dataset.etap = w.etap || "";
    stan.textContent = stan.dataset.tekst;
    dymek.append(stan);
    odswiezLicznik();
  }
  if (w.uwagi && w.uwagi.length) {
    for (const u of w.uwagi) dymek.append(el("div", "dopisek warn", u));
  }
  if (w.zrodla && w.zrodla.length) {
    for (const [nazwa, zakresy] of w.zrodla) {
      const linie = zakresy.map(([od, doLinii]) => od === doLinii ? String(od) : od + "-" + doLinii).join(", ");
      dymek.append(el("div", "dopisek", _f("Na podstawie fragmentów: {nazwa}, linie {linie}", { nazwa: nazwa, linie: linie })));
    }
  }
  if (w.zrodla_www && w.zrodla_www.length) {
    const zr = el("div", "zrodla");
    zr.append(el("b", "", _("Źródła:")));
    w.zrodla_www.forEach((z, n) => {
      const wiersz = el("div");
      wiersz.append("[" + (n + 1) + "] ");
      wiersz.append(bezpiecznyLink(z.url, z.tytul || z.url));
      zr.append(wiersz);
    });
    dymek.append(zr);
  }
  if (!w.trwa && !odpowiedz.trim() && !myslenie.trim() && !w.narzedzia && !zapisane.length) {
    dymek.append(el("div", "stan", w.stat || "…"));
  }
}

function tekstStanu(w, odpowiedz) {
  if (w.etap) return w.etap;
  if (odpowiedz.trim()) return "";
  if (mysleniezTresci(w).trim()) return _("model myśli...");
  if (w.szuka) return _("Szukam pasujących fragmentów w pliku...");
  if (w.laduje) {
    return _f("Wczytuję model do pamięci ({gb:.1f} GB) - przy pierwszym pytaniu to chwilę trwa, potem odpowiedzi idą od razu...",
      { gb: w.gb || 0 });
  }
  return _("czekam na odpowiedź...");
}

function odswiezLicznik() {
  // WHAT: licznik sekund przy "czekam na odpowiedź" - widać, że program żyje.
  for (const stan of document.querySelectorAll(".stan[data-start]")) {
    const start = Number(stan.dataset.start);
    // WHY: zapis długiego pliku Ollama oddaje dopiero w całości - minuty bez żadnego tekstu
    const cisza = Math.floor(Date.now() / 1000 - Number(stan.dataset.ostatni || 0));
    if (stan.dataset.pliki && stan.dataset.ostatni && cisza >= 5 && !/\S/.test(stan.dataset.etap || "")) {
      stan.textContent = _f("Model wciąż pracuje ({s} s) - zapis dłuższego pliku może potrwać kilka minut; potem pojawi się okienko z podglądem zmian.", { s: cisza });
      continue;
    }
    if (!start || !stan.dataset.tekst) {
      stan.textContent = stan.dataset.tekst || "";
      continue;
    }
    const sekundy = Math.floor(Date.now() / 1000 - start);
    stan.textContent = stan.dataset.tekst + (sekundy >= 1 ? "  " + sekundy + " s" : "");
  }
}

function bezpiecznyLink(adres, tekst) {
  // WHY: tylko http(s) - żaden "javascript:" z treści modelu ani ze strony nie trafi do linku
  try {
    const u = new URL(adres);
    if (u.protocol === "http:" || u.protocol === "https:") {
      const a = el("a", "", tekst);
      a.href = u.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      return a;
    }
  } catch (e) { /* zły adres - zwykły tekst */ }
  return document.createTextNode(tekst);
}

// --- Markdown (marked.js) ---
function escapeHtml(tekst) {
  return String(tekst).replace(/[&<>"']/g, (z) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[z]));
}

let markedGotowy = false;

function markdown(tekst) {
  if (typeof marked === "undefined") return "<p>" + escapeHtml(tekst).replace(/\n/g, "<br>") + "</p>";
  if (!markedGotowy) {
    marked.use({
      gfm: true,
      renderer: {
        // WHY: surowy HTML z odpowiedzi modelu pokazujemy jako tekst - nic się nie uruchomi
        html(token) { return escapeHtml(token.text); },
        // WHY: bez obrazków z internetu - strona nie pobiera niczego z obcych serwerów
        image(token) { return escapeHtml(token.text || token.href); },
        link(token) {
          const tekst = this.parser.parseInline(token.tokens);
          if (!/^(https?:|mailto:)/i.test(token.href || "")) return tekst;
          return '<a href="' + escapeHtml(token.href) + '" target="_blank" rel="noopener noreferrer">' + tekst + "</a>";
        },
        code(token) {
          const numer = S.kody.push(token.text) - 1;
          return '<div class="kod"><div class="kod-gora"><span>' + escapeHtml(token.lang || "") +
            '</span><button type="button" class="mala-ikona" data-kopiuj="' + numer + '" title="' +
            escapeHtml(_("Kopiuj kod")) + '" aria-label="' + escapeHtml(_("Kopiuj kod")) + '">' + ikona("kopiuj") +
            "</button></div><pre><code>" + escapeHtml(token.text) + "</code></pre></div>";
        },
      },
    });
    markedGotowy = true;
  }
  return marked.parse(tekst);
}

// --- Kontekst ---
function pokazKontekst() {
  const wiadomosci = S.rozmowa ? S.rozmowa.wiadomosci : [];
  const z = [...wiadomosci].reverse().find((w) => w.kontekst);
  E.kontekst.classList.remove("warn", "error");
  E.kontekst.hidden = !z;
  if (!z) return;
  const [uzyte, okno] = z.kontekst;
  const liczba = (n) => Number(n).toLocaleString(S.stan.jezyk);
  if (!okno) {
    E.liczbyKontekstu.textContent = _f("{n} tokenów", { n: liczba(uzyte) });
    E.pasekKontekstu.style.width = "0";
    return;
  }
  const udzial = uzyte / okno;
  E.liczbyKontekstu.textContent = _f("{uzyte} / {okno} ({proc}%)",
    { uzyte: liczba(uzyte), okno: liczba(okno), proc: Math.min(100, Math.round(100 * udzial)) });
  E.pasekKontekstu.style.width = Math.min(100, 100 * udzial) + "%";
  if (udzial >= 0.95) {
    E.kontekst.classList.add("error");
    E.kontekst.title = _("Prawie pełne - model zapomina już początek rozmowy. Zacznij nową albo zwiększ OLLAMA_CONTEXT_LENGTH w Ustawieniach.");
  } else if (udzial >= 0.8) {
    E.kontekst.classList.add("warn");
    E.kontekst.title = _("Blisko limitu - wkrótce model zacznie zapominać początek rozmowy. Zacznij nową albo zwiększ OLLAMA_CONTEXT_LENGTH w Ustawieniach.");
  } else {
    E.kontekst.title = _("Ile okna kontekstu modelu zajmuje już ta rozmowa (tokeny)");
  }
}

// --- Wysyłanie i strumień odpowiedzi ---
function pisze() {
  return Boolean(S.zrodlo) || Boolean(S.rozmowa && S.rozmowa.trwa);
}

function pokazPrzyciskWyslij() {
  if (!E.wyslij) return;
  const trwa = pisze();
  E.wyslij.textContent = trwa ? _("Zatrzymaj") : _("Wyślij");
  E.wyslij.disabled = !trwa && (!wybranyModel() || Boolean(S.rozmowa && S.rozmowa.w_programie));
}

async function wyslij() {
  if (pisze()) {
    if (S.rozmowa) api("/api/zatrzymaj", { id: S.rozmowa.id }).catch(() => {});
    return;
  }
  const tekst = E.pytanie.value.trim();
  const gotowe = S.zalaczniki.filter((z) => z.stan === "gotowy");
  if ((!tekst && !gotowe.length) || !wybranyModel()) return;
  if (S.zalaczniki.some((z) => z.stan === "wysyla")) {
    uwaga(_("Poczekaj chwilę - plik jeszcze się wysyła."), "warn");
    return;
  }
  uwaga("");
  E.wyslij.disabled = true;
  try {
    const wynik = await api("/api/wyslij", {
      id: S.rozmowa ? S.rozmowa.id : null, model: S.model, tekst: tekst,
      myslenie: E.myslenie.checked, szukaj: S.szukaj, katalog: S.katalog,
      zalaczniki: gotowe.map((z) => z.token),
    });
    E.pytanie.value = "";
    S.zalaczniki = [];
    pokazChipy();
    dopasujPole();
    await otworzRozmowe(wynik.id);
    przewinNaDol();
    wczytajRozmowy();
  } catch (e) {
    uwaga(e.message, "warn");
    pokazPrzyciskWyslij();
  }
}

function zamknijStrumien() {
  if (S.zrodlo) {
    S.zrodlo.close();
    S.zrodlo = null;
  }
}

function sluchaj(id) {
  // WHAT: stan pisanej odpowiedzi z /api/strumien (Server-Sent Events) - przy każdej zmianie
  //       cała ostatnia wiadomość; "koniec" = odpowiedź zapisana, wczytujemy rozmowę od nowa.
  // WHY:  EventSource sam łączy się ponownie po zerwaniu (telefon zasnął) - serwer od razu
  //       wysyła pełny stan, więc nic nie ginie.
  zamknijStrumien();
  const zrodlo = new EventSource("/api/strumien?id=" + encodeURIComponent(id));
  S.zrodlo = zrodlo;
  pokazPrzyciskWyslij();
  let zaplanowane = null;
  zrodlo.onmessage = (z) => {
    if (S.zrodlo !== zrodlo || !S.rozmowa || S.rozmowa.id !== id) return;
    const w = JSON.parse(z.data);
    const lista = S.rozmowa.wiadomosci;
    if (lista.length && lista[lista.length - 1].rola !== "user") lista[lista.length - 1] = w;
    else lista.push(w);
    zaplanowane = zaplanowane || requestAnimationFrame(() => {
      zaplanowane = null;
      odswiezOstatnia();
    });
    pokazProsbe(w.prosba);
  };
  zrodlo.addEventListener("koniec", async () => {
    if (S.zrodlo !== zrodlo) return;
    zamknijStrumien();
    pokazProsbe(null);
    if (S.rozmowa && S.rozmowa.id === id) await otworzRozmowe(id, true);
    if (S.podglad) wczytajPodglad(true);
    wczytajRozmowy();
    wczytajModele();  // WHY: model jest teraz w pamięci - widać to w opisie
  });
}

function odswiezOstatnia() {
  const lista = S.rozmowa.wiadomosci;
  const naDole = blisko(E.rozmowa);
  const stary = E.rozmowa.querySelector("article.wiadomosc:last-of-type");
  const i = lista.length - 1;
  if (stary && Number(stary.dataset.indeks) === i) {
    wypelnijDymek(stary.querySelector(".dymek"), lista[i]);
    const stat = stary.querySelector(".naglowek .stat");
    if (stat) stat.textContent = lista[i].stat || "";
  } else {
    if (!stary) E.rozmowa.replaceChildren();
    E.rozmowa.append(wiadomoscElement(lista[i], i, lista));
  }
  if (naDole) przewinNaDol();
}

// --- Kliknięcia w rozmowie: kopiowanie, ponów, popraw ---
async function klikWRozmowie(z) {
  const przycisk = z.target.closest("button");
  if (!przycisk) return;
  if (przycisk.dataset.kopiuj !== undefined) {
    kopiuj(S.kody[Number(przycisk.dataset.kopiuj)] || "");
    return;
  }
  if (przycisk.dataset.plik !== undefined) {
    otworzPodglad(przycisk.dataset.plik);
    return;
  }
  const akcja = przycisk.dataset.akcja;
  if (!akcja || !S.rozmowa || pisze()) return;
  try {
    const wynik = await api("/api/akcja", {
      id: S.rozmowa.id, akcja: akcja, model: S.model, myslenie: E.myslenie.checked, szukaj: S.szukaj,
      katalog: S.katalog,
    });
    if (akcja === "popraw") {
      E.pytanie.value = wynik.tekst || "";
      dopasujPole();
      E.pytanie.focus();
      if (wynik.id) await otworzRozmowe(wynik.id, true); else nowaRozmowa();
      E.pytanie.value = wynik.tekst || "";
      dopasujPole();
    } else {
      await otworzRozmowe(wynik.id, true);
      przewinNaDol();
    }
    wczytajRozmowy();
  } catch (e) {
    uwaga(e.message, "warn");
  }
}

function kopiuj(tekst) {
  // WHY: navigator.clipboard działa tylko na https albo localhost - w sieci domowej (http://192.168...)
  //      zostaje stary sposób przez zaznaczenie ukrytego pola
  const gotowe = () => informacja(_("Skopiowano"));
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(tekst).then(gotowe, () => kopiujStarymSposobem(tekst, gotowe));
  } else {
    kopiujStarymSposobem(tekst, gotowe);
  }
}

function kopiujStarymSposobem(tekst, gotowe) {
  const pole = el("textarea");
  pole.value = tekst;
  pole.setAttribute("readonly", "");
  pole.style.position = "fixed";
  pole.style.opacity = "0";
  document.body.append(pole);
  pole.select();
  try {
    if (document.execCommand("copy")) gotowe();
  } finally {
    pole.remove();
  }
}

// --- Katalog roboczy ---
async function wczytajKatalogi() {
  try {
    const wynik = await api("/api/katalogi");
    S.katalogi = wynik.katalogi;
    S.domyslnyTryb = wynik.domyslny;
  } catch (e) {
    return;
  }
  pokazKatalog();
}

function nazwaTrybu(tryb) {
  return { pytaj: _("Pytaj za każdym razem"), sesja: _("Pytaj raz na sesję"), zawsze: _("Zezwalaj zawsze") }[tryb] || tryb;
}

function menuKatalogow(przy) {
  // WHAT: wybór katalogu roboczego - tylko z wybranych kiedyś w programie (decyzja użytkownika).
  const zaslona = el("div", "zaslona przezroczysta");
  const menu = el("div", "menu");
  const zamknij = () => { menu.remove(); zaslona.remove(); };
  zaslona.addEventListener("click", zamknij);
  const pozycja = (tekst, akcja, wybrana) => {
    const b = el("button", wybrana ? "wybrana" : "", tekst);
    b.type = "button";
    b.addEventListener("click", () => { zamknij(); akcja(); });
    menu.append(b);
  };
  pozycja(_("Bez katalogu roboczego"), () => ustawKatalog(null), !S.katalog);
  for (const k of S.katalogi) pozycja(k.krotko, () => ustawKatalog(k.sciezka), S.katalog === k.sciezka);
  menu.append(el("p", "menu-uwaga", _("Nowy katalog wybierzesz w programie - przyciskiem z folderem w czacie.")));
  document.body.append(zaslona, menu);
  const p = przy.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(p.left, window.innerWidth - menu.offsetWidth - 8)) + "px";
  menu.style.top = Math.max(8, p.top - menu.offsetHeight - 6) + "px";
}

function ustawKatalog(katalog) {
  S.katalog = katalog;
  pokazKatalog();
  const m = wybranyModel();
  if (katalog && m && !m.narzedzia) {
    uwaga(_f("{model} nie umie korzystać z narzędzi - do pracy na plikach wybierz model z tagiem \"narzędzia\" (np. qwen3, gemma4) albo odepnij katalog.",
      { model: m.krotka }), "warn");
  }
}

function pokazKatalog() {
  if (!E.ramkaKatalogu) return;
  E.katalogPrzycisk.classList.toggle("wlaczony", Boolean(S.katalog));
  E.ramkaKatalogu.replaceChildren();
  E.ramkaKatalogu.hidden = !S.katalog;
  if (!S.katalog) return;
  const k = S.katalogi.find((x) => x.sciezka === S.katalog);
  const krotko = k ? k.krotko : S.katalog;
  E.ramkaKatalogu.append(ikonaElement("folder"), el("span", "sciezka-katalogu", _f("Katalog roboczy: {katalog}", { katalog: krotko })));
  // WHAT: tryb zapisu dla tego projektu - jak menu "Zapis: ..." w programie
  const wybor = el("select");
  wybor.setAttribute("aria-label", _("Zapis w katalogu roboczym"));
  const jak = el("option", "", _f("Jak w Ustawieniach ({tryb})", { tryb: nazwaTrybu(S.domyslnyTryb).toLowerCase() }));
  jak.value = "";
  wybor.append(jak);
  for (const tryb of ["pytaj", "sesja", "zawsze"]) {
    const o = el("option", "", nazwaTrybu(tryb));
    o.value = tryb;
    wybor.append(o);
  }
  wybor.value = k && k.wlasny ? k.tryb : "";
  wybor.addEventListener("change", async () => {
    try {
      const wynik = await api("/api/tryb_zapisu", { katalog: S.katalog, tryb: wybor.value || null });
      S.katalogi = wynik.katalogi;
    } catch (e) {
      uwaga(e.message, "warn");
    }
    pokazKatalog();
  });
  const odepnij = przyciskIkona("zamknij", _("Odepnij katalog"));
  odepnij.addEventListener("click", () => ustawKatalog(null));
  E.ramkaKatalogu.append(wybor, odepnij);
}

function ikonaElement(nazwa) {
  const s = el("span", "ikona-w-tekscie");
  s.innerHTML = ikona(nazwa);
  return s;
}

// --- Zgoda na zapis pliku (jak okienko w programie) ---
function pokazProsbe(prosba) {
  const stary = document.getElementById("prosba");
  if (!prosba) {
    if (stary) stary.remove();
    S.prosbaNr = null;
    return;
  }
  if (stary && S.prosbaNr === prosba.nr) return;
  if (stary) stary.remove();
  S.prosbaNr = prosba.nr;
  const zaslona = el("div", "okno-zaslona");
  zaslona.id = "prosba";
  const okno = el("div", "okno");
  okno.setAttribute("role", "dialog");
  okno.setAttribute("aria-modal", "true");
  okno.append(el("h2", "", prosba.nowy ? _("Model chce utworzyć plik") : _("Model chce zmienić plik")),
    el("div", "mono", prosba.nowy ? _f("Nowy plik: {sciezka}", { sciezka: prosba.sciezka })
      : _f("Zmiana pliku: {sciezka}", { sciezka: prosba.sciezka })));
  const k = S.katalogi.find((x) => x.sciezka === prosba.katalog);
  okno.append(el("p", "wyciszony", _f("Katalog roboczy: {katalog}", { katalog: k ? k.krotko : prosba.katalog })));
  okno.append(roznicaElement(prosba.roznica));
  const przyciski = el("div", "przyciski");
  const decyzja = (wybor) => async () => {
    for (const b of przyciski.querySelectorAll("button")) b.disabled = true;
    try {
      await api("/api/zgoda", { id: S.rozmowa.id, nr: prosba.nr, wybor: wybor });
    } catch (e) {
      uwaga(e.message, "warn");
    }
    zaslona.remove();
    if (wybor === "sesja" || wybor === "zawsze") wczytajKatalogi();
  };
  const odrzuc = el("button", "przycisk", _("Odrzuć"));
  odrzuc.type = "button";
  odrzuc.addEventListener("click", decyzja(null));
  przyciski.append(odrzuc, el("span", "odstep"));
  for (const [tekst, wybor, dymek, klasa] of [
    [_("Zezwalaj zawsze dla tego katalogu"), "zawsze", _("Kolejne zapisy w tym katalogu bez pytania - także po ponownym uruchomieniu programu. Zmienisz to na pasku katalogu w czacie (\"Zapis: ...\")."), ""],
    [_("Zezwalaj w tej sesji"), "sesja", _("Kolejne zapisy w tym katalogu bez pytania, dopóki nie zamkniesz programu."), ""],
    [_("Zapisz"), "raz", _("Tylko ta jedna zmiana."), "glowny"]]) {
    const b = el("button", "przycisk " + klasa, tekst);
    b.type = "button";
    b.title = dymek;
    b.addEventListener("click", decyzja(wybor));
    przyciski.append(b);
  }
  okno.append(przyciski);
  zaslona.append(okno);
  document.body.append(zaslona);
}

function roznicaElement(tekst) {
  // WHAT: unified diff z kolorami linii (dodane / usunięte / nagłówki) - jak podgląd zmian w programie.
  const pre = el("pre", "roznica");
  for (const linia of (tekst || "").split("\n")) {
    const klasa = linia.startsWith("+++") || linia.startsWith("---") || linia.startsWith("@@") ? "naglowek"
      : linia.startsWith("+") ? "dodane" : linia.startsWith("-") ? "usuniete" : "";
    pre.append(el("span", klasa, linia + "\n"));
  }
  return pre;
}

// --- Załączniki ---
async function dodajPliki(lista) {
  for (const plik of Array.from(lista)) {
    const z = { nazwa: plik.name || "zrzut.png", typ: "", rozmiar: plik.size, stan: "wysyla" };
    S.zalaczniki.push(z);
    pokazChipy();
    try {
      const odp = await fetch("/api/zalacznik", {
        method: "POST", credentials: "same-origin", body: plik,
        headers: { "Content-Type": "application/octet-stream", "X-Nazwa": encodeURIComponent(z.nazwa) },
      });
      const wynik = await odp.json().catch(() => ({}));
      if (!odp.ok) throw new Error(wynik.blad || ("HTTP " + odp.status));
      Object.assign(z, { token: wynik.token, typ: wynik.typ, rozmiar: wynik.rozmiar, nazwa: wynik.nazwa, stan: "gotowy" });
    } catch (e) {
      z.stan = "blad";
      z.blad = e.message;
    }
    if (!S.zalaczniki.includes(z)) continue;  // WHY: usunięty w trakcie wysyłania
    pokazChipy();
    if (z.typ === "obraz") wczytajModele();  // WHY: ze zdjęciem lista pokazuje też modele OCR
  }
}

function pokazChipy() {
  E.chipy.replaceChildren();
  E.chipy.hidden = !S.zalaczniki.length;
  for (const z of S.zalaczniki) {
    let opis = z.nazwa + "  ·  ";
    if (z.stan === "wysyla") opis += _("wysyłam...");
    else if (z.stan === "blad") opis = z.blad;
    else if (z.typ === "obraz") opis += _("zdjęcie");
    else opis += Math.max(1, Math.round(z.rozmiar / 1024)) + " KB" + (z.typ === "dokument" ? "  ·  " + _("wyszukiwanie fragmentów") : "");
    const chip = el("span", "chip" + (z.stan === "blad" ? " blad" : ""), opis);
    const usun = przyciskIkona("zamknij", _("Usuń załącznik"));
    usun.addEventListener("click", () => {
      S.zalaczniki = S.zalaczniki.filter((x) => x !== z);
      pokazChipy();
      if (z.typ === "obraz") wczytajModele();
    });
    chip.append(usun);
    E.chipy.append(chip);
  }
}

// --- Podgląd pliku (Kod / Zmiany / Strona) ---
function zbudujPodglad() {
  const panel = el("section", "podglad");
  panel.hidden = true;
  const gora = el("div", "podglad-gora");
  E.podgladNazwa = el("b", "mono", "");
  E.podgladChipy = {};
  const chipy = el("div", "przelacznik-widokow");
  for (const [tryb, tekst, opis] of [["kod", _("Kod"), _("Treść pliku z numerami linii")],
    ["zmiany", _("Zmiany"), _("Co zmienił model - względem wersji sprzed pierwszej zmiany w tej sesji")],
    ["strona", _("Strona"), _("Strona tak, jak w przeglądarce - także gry z JavaScriptem")]]) {
    const b = el("button", "chip-widoku", tekst);
    b.type = "button";
    b.title = opis;
    b.addEventListener("click", () => { S.podglad.tryb = tryb; pokazPodglad(); });
    E.podgladChipy[tryb] = b;
    chipy.append(b);
  }
  E.podgladOtworz = el("a", "przycisk ikona");
  E.podgladOtworz.innerHTML = ikona("otworz");
  E.podgladOtworz.target = "_blank";
  E.podgladOtworz.rel = "noopener noreferrer";
  E.podgladOtworz.title = _("Otwórz stronę w nowej karcie");
  const zamknij = przyciskIkona("zamknij", _("Zamknij podgląd"), "przycisk ikona");
  zamknij.addEventListener("click", zamknijPodglad);
  gora.append(E.podgladNazwa, chipy, E.podgladOtworz, zamknij);
  E.podgladInfo = el("div", "podglad-info");
  E.podgladTresc = el("div", "podglad-tresc");
  panel.append(gora, E.podgladInfo, E.podgladTresc);
  return panel;
}

async function otworzPodglad(sciezka) {
  S.podglad = { sciezka: sciezka, tryb: null, dane: null, mtime: null };
  E.podglad.hidden = false;
  E.uklad.classList.add("z-podgladem");
  E.podgladNazwa.textContent = sciezka;
  await wczytajPodglad();
  clearInterval(S.zegarPodgladu);
  S.zegarPodgladu = setInterval(sprawdzPodglad, 1500);
}

function zamknijPodglad() {
  clearInterval(S.zegarPodgladu);
  S.podglad = null;
  if (!E.podglad) return;
  E.podglad.hidden = true;
  E.uklad.classList.remove("z-podgladem");
  E.podgladTresc.replaceChildren();
}

async function wczytajPodglad(cicho) {
  const p = S.podglad;
  if (!p || !S.rozmowa) return;
  try {
    p.dane = await api("/api/plik?id=" + encodeURIComponent(S.rozmowa.id) + "&sciezka=" + encodeURIComponent(p.sciezka));
    p.mtime = p.dane.mtime;
  } catch (e) {
    if (!cicho) {
      E.podgladInfo.textContent = e.message;
      E.podgladTresc.replaceChildren();
    }
    return;
  }
  if (!p.tryb) p.tryb = p.dane.strona ? "strona" : "kod";
  pokazPodglad();
}

async function sprawdzPodglad() {
  // WHAT: co 1,5 s - czy plik się zmienił (model zapisał nową wersję); tak = podgląd od nowa.
  const p = S.podglad;
  if (!p || !S.rozmowa || document.visibilityState !== "visible") return;
  try {
    const meta = await api("/api/plik?meta=1&id=" + encodeURIComponent(S.rozmowa.id) + "&sciezka=" + encodeURIComponent(p.sciezka));
    if (S.podglad === p && meta.mtime !== p.mtime) wczytajPodglad(true);
  } catch (e) { /* plik zniknął albo brak połączenia - spróbujemy za chwilę */ }
}

function pokazPodglad() {
  const p = S.podglad;
  const d = p.dane;
  if (p.tryb === "zmiany" && !d.roznica) p.tryb = "kod";
  if (p.tryb === "strona" && !d.strona) p.tryb = "kod";
  for (const [tryb, b] of Object.entries(E.podgladChipy)) {
    b.classList.toggle("wybrany", tryb === p.tryb);
    b.disabled = (tryb === "zmiany" && !d.roznica) || (tryb === "strona" && !d.strona);
  }
  E.podgladOtworz.hidden = !d.strona;
  if (d.strona) E.podgladOtworz.href = d.strona;
  const godzina = new Date(d.mtime * 1000).toLocaleTimeString(S.stan.jezyk);
  E.podgladInfo.textContent = d.binarny ? _("plik binarny - użyj \"Otwórz\" albo \"Strona\"")
    : _f("{kb} KB · zmieniony {godzina} · odświeża się sam", { kb: Math.max(1, Math.round(d.rozmiar / 1024)), godzina: godzina });
  const ramka = E.podgladTresc.querySelector("iframe");
  if (p.tryb === "strona") {
    // WHY: piaskownica bez allow-same-origin - skrypt strony nie dostanie się do czatu ani ciasteczek
    const adres = d.strona + "?t=" + d.mtime;
    if (ramka) {
      if (ramka.dataset.adres !== adres) { ramka.src = adres; ramka.dataset.adres = adres; }
      return;
    }
    const nowa = el("iframe", "strona");
    nowa.setAttribute("sandbox", "allow-scripts allow-forms allow-modals allow-pointer-lock allow-popups");
    nowa.src = adres;
    nowa.dataset.adres = adres;
    nowa.title = p.sciezka;
    E.podgladTresc.replaceChildren(nowa);
    return;
  }
  if (p.tryb === "zmiany") {
    E.podgladTresc.replaceChildren(roznicaElement(d.roznica));
    return;
  }
  const pre = el("pre", "kod-pliku");
  if (d.tekst !== null) {
    const linie = d.tekst.split("\n");
    const szerokosc = String(linie.length).length;
    pre.textContent = linie.map((l, n) => String(n + 1).padStart(szerokosc, " ") + " │ " + l).join("\n");
  }
  E.podgladTresc.replaceChildren(pre);
}

start();
