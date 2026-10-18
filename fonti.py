"""Affidabilità delle fonti: insider, fonte originale, raggruppamento, etichette, storico dei leak.

Tutto qui è logica pura (niente rete), così si può provare in locale senza feed né API.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Insider: giornalisti e leaker con un buon storico.
# "bsky" = handle Bluesky da leggere direttamente (None se non pubblicano lì),
# "x" = handle X/Twitter (solo per riconoscerli quando un articolo linka un loro post),
# "alias" = come vengono nominati negli articoli, "dominio" = testata che pubblica i loro leak.
# Lo storico in stato.json corregge col tempo l'affidabilità di partenza.
# ---------------------------------------------------------------------------
INSIDER = {
    "Jason Schreier": {"bsky": "jasonschreier.bsky.social", "x": "jasonschreier",
                       "alias": [r"jason schreier", r"\bschreier\b"]},
    "Tom Henderson": {"bsky": None, "x": "_tom_henderson_",
                      "alias": [r"tom henderson"], "dominio": "insider-gaming.com"},
    "Jeff Grubb": {"bsky": "grubb.wtf", "x": "jeffgrubb", "alias": [r"jeff grubb", r"\bgrubb\b"]},
    "Tom Warren": {"bsky": "tomwarren.co.uk", "x": "tomwarren", "alias": [r"tom warren"]},
    "Jez Corden": {"bsky": "jezcorden.bsky.social", "x": "jezcorden", "alias": [r"jez corden"]},
    "billbil-kun": {"bsky": None, "x": "billbil_kun", "alias": [r"billbil[- ]?kun"],
                    "dominio": "dealabs.com"},
    "NateTheHate": {"bsky": None, "x": "natethehate2", "alias": [r"nate ?the ?hate"]},
}
PRIOR_INSIDER = (4, 1)   # punto di partenza: 4 conferme su 5 → affidabilità 0.8
PRIOR_IGNOTO = (1, 1)    # fonte mai vista → 0.5
SOGLIA_AFFIDABILE = 0.7
GIORNI_SCADENZA = 180    # leak mai confermato dopo 6 mesi → conta come non confermato
GIORNI_INVIATI = 4       # per quanti giorni ricordare i link già inviati

_ALIAS = [(nome, re.compile("|".join(d["alias"]), re.I)) for nome, d in INSIDER.items()]
_HANDLE_BSKY = {d["bsky"].lower(): n for n, d in INSIDER.items() if d["bsky"]}
_HANDLE_X = {d["x"].lower(): n for n, d in INSIDER.items() if d.get("x")}
_DOMINIO_INSIDER = {d["dominio"]: n for n, d in INSIDER.items() if d.get("dominio")}

# Pagine che sono fonte primaria: comunicati di piattaforme/publisher, press release, enti di rating
_UFFICIALI = re.compile(
    r"https?://(?:[\w-]+\.)*("
    r"blog\.playstation\.com|news\.xbox\.com|"
    r"nintendo\.(?:com|co\.jp|co\.uk|de|it)/.*(?:news|whatsnew|press|/ir/)|"
    r"store\.steampowered\.com/news|steamcommunity\.com/(?:games|app)/[^/]+/(?:announcements|news)|"
    r"businesswire\.com|prnewswire\.com|globenewswire\.com|"
    r"esrb\.org|pegi\.info|grac\.or\.kr|"
    r"news\.ea\.com|ea\.com/news|news\.ubisoft\.com|bethesda\.net/.*news|rockstargames\.com/newswire|"
    r"blizzard\.com/.*news|epicgames\.com/.*news|capcom\.co\.jp/ir|sega\.co\.jp/ir|bandainamco\w*\.\w+/.*ir"
    r")", re.I)
_BSKY_POST = re.compile(r"https?://(?:www\.)?bsky\.app/profile/([^/\s]+)/post/", re.I)
_X_POST = re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(\w+)/status/", re.I)
# domini che non sono una fonte: immagini, accorciatori, il forum stesso
_NON_FONTE = ("reddit.com", "redd.it", "redditmedia.com", "resetera.com", "imgur.com",
              "youtube.com", "youtu.be", "discord.gg", "patreon.com", "amazon.", "bit.ly")
COMMUNITY = ("reddit.com", "resetera.com")

# Indizi che una notizia è un leak/rumor e non un fatto annunciato
_LEAK = re.compile(
    r"\b(leak\w*|rumou?r\w*|insider|reportedly|allegedly|datamin\w*|i'?m told|sources? (?:say|claim|familiar)|"
    r"according to (?:sources|a report|insiders?)|indiscrezion\w*|trapelat\w*|secondo (?:fonti|alcune fonti|un insider)|"
    r"sarebbe in sviluppo|potrebbe essere annunciat\w*|voci di corridoio|rating (?:board|appears)|"
    r"classificat\w+ in (?:corea|taiwan)|rated in (?:korea|taiwan))\b", re.I)
# Frasi con cui un articolo attribuisce la notizia a qualcun altro
_ATTRIBUZIONE = re.compile(
    r"\b(according to|reported by|as reported|report from|first reported|via|reports?|claims?|"
    r"secondo|come riportato|riporta(?:to)?|stando a|rivelat\w*|ha (?:dichiarato|condiviso|svelato)|segnalat\w*)\b",
    re.I)


def dominio(url):
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def norm_link(url):
    return (url or "").split("?")[0].split("#")[0].rstrip("/").lower()


def _e_community(url):
    return any(dominio(url).endswith(d) for d in COMMUNITY)


def classifica_url(url):
    """(tipo, chiave) per un link citato: 'ufficiale', 'insider' o 'testata'; None se non è una fonte."""
    if not url.startswith("http"):
        return None
    if _UFFICIALI.match(url):
        return ("ufficiale", dominio(url))
    m = _BSKY_POST.match(url)
    if m:
        h = m.group(1).lower()
        return ("insider", _HANDLE_BSKY.get(h, "@" + h))
    m = _X_POST.match(url)
    if m:
        h = m.group(1).lower()
        return ("insider", _HANDLE_X.get(h, "@" + h))
    d = dominio(url)
    if not d or any(x in d for x in _NON_FONTE):
        return None
    if d in _DOMINIO_INSIDER:
        return ("insider", _DOMINIO_INSIDER[d])
    return ("testata", d)


def insider_nominati(testo):
    return [nome for nome, rx in _ALIAS if rx.search(testo or "")]


def trova_origini(v, paragrafi=(), link_pagina=()):
    """Fonti originali di una voce, in ordine di peso.

    - link presenti nella descrizione del feed (su Reddit è il link al post/articolo originale)
    - link a comunicati ufficiali o post social presenti nella pagina dell'articolo
    - link e insider nominati nei paragrafi che attribuiscono la notizia ("secondo…", "according to…")
    - insider nominati nel titolo o nel sommario
    Ritorna una lista di dict {tipo, chiave, url}.
    """
    proprio = dominio(v.get("link", ""))
    trovate = {}

    def aggiungi(tipo, chiave, url=None):
        if chiave == proprio and tipo == "testata":
            return
        k = (tipo, chiave)
        if k not in trovate or (url and not trovate[k]["url"]):
            trovate[k] = {"tipo": tipo, "chiave": chiave, "url": url}

    for u in v.get("link_esterni", []):
        c = classifica_url(u)
        if c:
            aggiungi(*c, u)
    for u in link_pagina:  # tutta la pagina: valgono solo comunicati ufficiali e post social
        c = classifica_url(u)
        if c and (c[0] == "ufficiale" or _BSKY_POST.match(u) or _X_POST.match(u)):
            aggiungi(*c, u)
    for testo, links in paragrafi:
        if not _ATTRIBUZIONE.search(testo):
            continue
        for u in links:
            c = classifica_url(u)
            if c:
                aggiungi(*c, u)
        for nome in insider_nominati(testo):
            aggiungi("insider", nome)
    for nome in insider_nominati(v.get("titolo", "") + " " + v.get("testo", "")):
        aggiungi("insider", nome)
    if proprio in _DOMINIO_INSIDER:
        aggiungi("insider", _DOMINIO_INSIDER[proprio], v.get("link"))
    peso = {"ufficiale": 0, "insider": 1, "testata": 2}
    return sorted(trovate.values(), key=lambda o: peso[o["tipo"]])


def e_leak(v):
    if "gamingleaksandrumours" in v.get("link", "").lower():  # su quel subreddit è tutto rumor
        return True
    return bool(_LEAK.search(v.get("titolo", "") + " " + v.get("testo", "")))


# ---------------------------------------------------------------------------
# Raggruppamento: stessa notizia da testate diverse → un solo gruppo
# ---------------------------------------------------------------------------
_STOP = set("""the and for with from that this will are was has have its new now out not but you your all
into about over after more than how why what when who can get gets got first just also than then
della delle degli dello dalla dalle dagli nella nelle negli sulla sulle sugli alla alle agli come
anche sono stato stata essere questo questa quello quella nuovo nuova nuovi nuove ecco ancora tutti
tutte dopo prima solo molto più per con non una uno gli les des""".split())


def parole(titolo):
    """Parole significative di un titolo, troncate a 5 lettere (delays/delayed → delay)."""
    out = set()
    for w in re.findall(r"[\w']+", (titolo or "").lower()):
        w = w.strip("'")
        if w.isdigit() or (len(w) >= 3 and w not in _STOP):
            out.add(w[:5])
    return out


def simili(a, b, minimo=2, soglia=0.5):
    """Stessa notizia se condividono abbastanza parole; con 3+ parole in comune (nomi propri,
    titolo del gioco, anno) basta una sovrapposizione minore: aggancia anche titoli IT/EN."""
    comuni = len(a & b)
    j = comuni / max(1, len(a | b))
    return (comuni >= minimo and j >= soglia) or (comuni >= max(3, minimo) and j >= soglia - 0.1)


def raggruppa(voci):
    """Union-find sui titoli simili. Ritorna liste di voci."""
    padre = list(range(len(voci)))

    def radice(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    p = [parole(v["titolo"]) for v in voci]
    for i in range(len(voci)):
        for j in range(i + 1, len(voci)):
            if simili(p[i], p[j]):
                padre[radice(j)] = radice(i)
    gruppi = {}
    for i, v in enumerate(voci):
        gruppi.setdefault(radice(i), []).append(v)
    return list(gruppi.values())


# ---------------------------------------------------------------------------
# Stato persistente (stato.json nel repo): storico dei leak, punteggi delle fonti, link già inviati
# ---------------------------------------------------------------------------
class Stato:
    def __init__(self, path):
        self.path = Path(path)
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        self.inviati = d.get("inviati", {})   # link normalizzato → data ISO
        self.leak = d.get("leak", [])         # {id, titolo, fonte, link, etichetta, data, stato, data_esito}
        self.fonti = d.get("fonti", {})       # chiave → {"confermati": n, "non_confermati": n}

    def salva(self):
        d = {"inviati": self.inviati, "leak": self.leak, "fonti": self.fonti}
        self.path.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    # --- affidabilità -------------------------------------------------------
    def conteggi(self, chiave):
        f = self.fonti.get(chiave, {})
        return f.get("confermati", 0), f.get("non_confermati", 0)

    def affidabilita(self, chiave):
        a, b = PRIOR_INSIDER if chiave in INSIDER else PRIOR_IGNOTO
        c, n = self.conteggi(chiave)
        return (c + a) / (c + n + a + b)

    def _esito(self, chiave, campo):
        self.fonti.setdefault(chiave, {"confermati": 0, "non_confermati": 0})[campo] += 1

    # --- link già inviati ---------------------------------------------------
    def gia_inviato(self, link):
        return norm_link(link) in self.inviati

    def segna_inviati(self, links, adesso):
        for l in links:
            self.inviati[norm_link(l)] = adesso.isoformat(timespec="minutes")

    # --- leak ---------------------------------------------------------------
    def leak_aperti(self):
        return [l for l in self.leak if l["stato"] == "aperto"]

    def registra_leak(self, titolo, fonte, link, etichetta, adesso):
        p = parole(titolo)
        for l in self.leak_aperti():  # già registrato (stesso leak ripreso il giorno dopo)
            if simili(p, parole(l["titolo"])):
                return None
        lid = hashlib.sha1(" ".join(sorted(p)).encode()).hexdigest()[:6]
        if any(l["id"] == lid for l in self.leak):
            return None
        self.leak.append({"id": lid, "titolo": titolo, "fonte": fonte, "link": link, "etichetta": etichetta,
                          "data": adesso.date().isoformat(), "stato": "aperto"})
        return lid

    def conferma(self, lid, adesso):
        for l in self.leak:
            if l["id"] == lid and l["stato"] == "aperto":
                l["stato"], l["data_esito"] = "confermato", adesso.date().isoformat()
                self._esito(l["fonte"], "confermati")
                return l
        return None

    def scadenze(self, adesso):
        scaduti = []
        limite = (adesso - timedelta(days=GIORNI_SCADENZA)).date().isoformat()
        for l in self.leak_aperti():
            if l["data"] < limite:
                l["stato"], l["data_esito"] = "non confermato", adesso.date().isoformat()
                self._esito(l["fonte"], "non_confermati")
                scaduti.append(l)
        return scaduti

    def pulizia(self, adesso):
        limite = (adesso - timedelta(days=GIORNI_INVIATI)).isoformat(timespec="minutes")
        self.inviati = {k: d for k, d in self.inviati.items() if d >= limite}
        # i leak chiusi servono solo come storico dei punteggi (già in self.fonti): tengo l'ultimo anno
        anno = (adesso - timedelta(days=365)).date().isoformat()
        self.leak = [l for l in self.leak if l["stato"] == "aperto" or l.get("data_esito", "") >= anno]


# ---------------------------------------------------------------------------
# Etichette di credibilità
# ---------------------------------------------------------------------------
UFFICIALE = "✅ UFFICIALE"
AFFIDABILE = "🟢 LEAK AFFIDABILE"
CONFERMATA = "🟢 CONFERMATA DA PIÙ FONTI"
RUMOR = "🟡 RUMOR"
FONTE_UNICA = "📰 FONTE UNICA"
SOLO_COMMUNITY = "💬 COMMUNITY"
ORDINE = [UFFICIALE, AFFIDABILE, CONFERMATA, RUMOR, FONTE_UNICA, SOLO_COMMUNITY]


def valuta_gruppo(gruppo, stato):
    """Etichetta di un gruppo di voci sulla stessa notizia.

    Ritorna (etichetta, info) oppure (None, info) se il gruppo va scartato
    (rumor visto solo su Reddit/ResetEra senza una fonte d'origine riconoscibile).
    """
    origini = {}
    for v in gruppo:
        for o in v.get("origini", []):
            origini.setdefault((o["tipo"], o["chiave"]), o)
    leak = any(v.get("leak") for v in gruppo)
    ufficiale = any(v["area"] == "Ufficiali" for v in gruppo) or any(t == "ufficiale" for t, _ in origini)

    # fonti indipendenti: ogni origine conta una volta; le testate senza origine contano per dominio.
    # Dieci siti che riprendono lo stesso tweet sono UNA fonte.
    indip = {k for t, k in origini if t in ("insider", "testata")}
    for v in gruppo:
        if not v.get("origini") and v["area"] not in ("Community",):
            indip.add(v.get("autore") or dominio(v["link"]))
    autori = {v["autore"] for v in gruppo if v.get("autore")}  # post Bluesky di un insider
    candidati = {k for t, k in origini if t == "insider"} | autori
    migliore = max(candidati, key=stato.affidabilita, default=None)

    info = {"origini": list(origini.values()), "indipendenti": sorted(indip), "leak": leak,
            "fonte_principale": migliore or next(iter(sorted(indip)), None)}
    if ufficiale:
        return UFFICIALE, info
    if leak:
        if (migliore and stato.affidabilita(migliore) >= SOGLIA_AFFIDABILE) or len(indip) >= 2:
            return AFFIDABILE, info
        if indip:
            return RUMOR, info
        return None, info
    if len(indip) >= 2:
        return CONFERMATA, info
    if indip:
        return FONTE_UNICA, info
    return SOLO_COMMUNITY, info


# ---------------------------------------------------------------------------
# Controllo finale: nel report solo link presenti nelle fonti raccolte
# ---------------------------------------------------------------------------
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"(?<![\(\"'=])https?://[^\s<>\")\]]+")


def filtra_link(testo, consentiti):
    """Toglie dal report i link che non compaiono tra le fonti. Ritorna (testo, rimossi)."""
    ok = {norm_link(u) for u in consentiti}
    rimossi = []

    def valido(u):
        return norm_link(u.rstrip(".,;:!?")) in ok

    def md(m):
        if valido(m.group(2)):
            return m.group(0)
        rimossi.append(m.group(2))
        return m.group(1)

    def nudo(m):
        u = m.group(0)
        if valido(u):
            return u
        rimossi.append(u)
        return "[link non verificato rimosso]"

    testo = _MD_LINK.sub(md, testo)
    testo = _URL.sub(nudo, testo)
    return testo, rimossi


def link_nel_testo(testo):
    return {u.rstrip(".,;:!?") for u in re.findall(r"https?://[^\s<>\")\]]+", testo)}


_CONFERMATI = re.compile(r"^\s*CONFERMATI\s*:\s*(.*)$", re.M | re.I)


def estrai_confermati(testo):
    """Legge (e toglie dal report) la riga finale 'CONFERMATI: id1, id2' scritta da Gemini."""
    ids = []
    for m in _CONFERMATI.finditer(testo):
        ids += re.findall(r"\b[0-9a-f]{6}\b", m.group(1).lower())
    return _CONFERMATI.sub("", testo).rstrip() + "\n", ids


def ora_utc():
    return datetime.now(timezone.utc)
