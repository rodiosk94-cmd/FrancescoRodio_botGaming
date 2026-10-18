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
    r"store\.steampowered\.com/news/app/\d+/view/|steamcommunity\.com/(?:games|app)/[^/]+/(?:announcements/detail|news/view)|"
    r"businesswire\.com|prnewswire\.com|globenewswire\.com|"
    r"esrb\.org|pegi\.info|grac\.or\.kr|"
    r"news\.ea\.com|ea\.com/news|news\.ubisoft\.com|bethesda\.net/.*news|rockstargames\.com/newswire|"
    r"blizzard\.com/.*news|epicgames\.com/.*news|capcom\.co\.jp/ir|sega\.co\.jp/ir|bandainamco\w*\.\w+/.*ir"
    r")", re.I)
_BSKY_POST = re.compile(r"https?://(?:www\.)?bsky\.app/profile/([^/\s]+)/post/", re.I)
_X_POST = re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(\w+)/status/", re.I)
# domini che non sono una fonte: immagini, accorciatori, il forum stesso, negozi, wiki e mod
# (i comunicati ufficiali sugli stessi domini, es. Steam News, sono riconosciuti prima da _UFFICIALI)
_NON_FONTE = ("reddit.com", "redd.it", "redditmedia.com", "resetera.com", "imgur.com",
              "youtube.com", "youtu.be", "discord.gg", "patreon.com", "amazon.", "bit.ly",
              "steampowered.com", "steamcommunity.com", "gog.com", "epicgames.com", "playstation.com",
              "xbox.com", "nintendo.", "humblebundle.com", "moddb.com", "nexusmods.com", "wikipedia.org",
              "fandom.com", "metacritic.com", "opencritic.com", "howlongtobeat.com", "twitch.tv",
              "instagram.com", "tiktok.com", "facebook.com", "linkedin.com", "t.co", "giphy.com")
COMMUNITY = ("reddit.com", "resetera.com")

# Indizi che una notizia è un leak/rumor e non un fatto annunciato
_LEAK = re.compile(
    r"\b(leak\w*|rumou?r\w*|reportedly|allegedly|datamin\w*|i'?m told|sources? (?:say|claim|familiar)|exclusive(?=:)|"
    r"(?:an|one|the) insider|insiders? (?:say|says|claim|claims|suggest|suggests|report|reports)|"
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
    # dominio esatto o sottodominio ("t.co" non deve colpire "mp1st.com"); "amazon." vale per ogni paese
    if not d or any(d == x or d.endswith("." + x) or (x.endswith(".") and x in d) for x in _NON_FONTE):
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
    for u in link_pagina:  # tutta la pagina (post incorporati): solo post social, non menu e barre laterali
        c = classifica_url(u)
        if c and (_BSKY_POST.match(u) or _X_POST.match(u)):
            aggiungi(*c, u)
    for _, links in paragrafi:  # comunicati ufficiali: solo se linkati nel testo dell'articolo
        for u in links:
            c = classifica_url(u)
            if c and c[0] == "ufficiale":
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


_NOMI_SITI = re.compile(r"insider[- ]gaming|business insider|xbox insider\w*|windows insider\w*", re.I)


def e_leak(v):
    if "gamingleaksandrumours" in v.get("link", "").lower():  # su quel subreddit è tutto rumor
        return True
    # "Insider Gaming" e simili sono nomi di siti o programmi, non indizi di un leak
    testo = _NOMI_SITI.sub(" ", v.get("titolo", "") + " " + v.get("testo", ""))
    return bool(_LEAK.search(testo))


# ---------------------------------------------------------------------------
# Raggruppamento: stessa notizia da testate diverse → un solo gruppo
# ---------------------------------------------------------------------------
_STOP = set("""the and for with from that this will are was has have its new now out not but you your all
into about over after more than how why what when who can get gets got first just also than then
della delle degli dello dalla dalle dagli nella nelle negli sulla sulle sugli alla alle agli come
anche sono stato stata essere questo questa quello quella nuovo nuova nuovi nuove ecco ancora tutti
tutte dopo prima solo molto più per con non una uno gli les des""".split())


_SINONIMI = [
    (r"\bplay ?station ?([2-6])\b", r"ps\1"), (r"\bnintendo switch ?2\b", "switch2"), (r"\bswitch 2\b", "switch2"),
    (r"\bxbox series [xs]\b", "xboxseries"), (r"(\d)[,.](\d{3})\b", r"\1\2"), (r"['’]s\b", ""),
]


def parole(titolo):
    """Parole significative di un titolo, normalizzate (PlayStation 6 → ps6, $1,000 → 1000, costs → cost)
    e troncate a 5 lettere (delays/delayed → delay)."""
    t = (titolo or "").lower()
    for rx, sost in _SINONIMI:
        t = re.sub(rx, sost, t)
    out = set()
    for w in re.findall(r"[\w']+", t):
        w = w.strip("'")
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and not w.isdigit():
            w = w[:-1]
        if w.isdigit() or (len(w) >= 3 and w not in _STOP):
            out.add(w[:5])
    return out


def stesso_leak(a, b, stessa_fonte):
    """Due titoli raccontano lo stesso leak? Più permissivo se vengono dalla stessa fonte d'origine,
    purché condividano un nome con cifre (ps6, 2027…) o almeno tre parole."""
    if simili(a, b):
        return True
    comuni = a & b
    con_cifre = any(any(ch.isdigit() for ch in w) for w in comuni)
    if stessa_fonte:
        return len(comuni) >= 3 or (len(comuni) >= 2 and con_cifre)
    return len(comuni) >= 4 and con_cifre and len(comuni) / max(1, len(a | b)) >= 0.15


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

    def trova_leak(self, titolo, links=(), fonte=None):
        """Leak aperto già registrato: stesso link d'origine, o titolo che racconta lo stesso leak."""
        p, ln = parole(titolo), {norm_link(u) for u in links if u}
        for l in self.leak_aperti():
            stessa = bool(fonte) and fonte == l["fonte"]
            if norm_link(l["link"]) in ln or any(stesso_leak(p, parole(t), stessa)
                                                 for t in [l["titolo"], *l.get("alias", [])]):
                return l
        return None

    def registra_leak(self, titolo, fonte, link, etichetta, adesso, altri_link=()):
        """Ritorna (id, nuovo). Un leak già aperto (ripreso il giorno dopo, o da un'altra testata) non si duplica."""
        gia = self.trova_leak(titolo, (link, *altri_link), fonte)
        if gia:  # il titolo nuovo diventa un alias: le prossime riprese si riconoscono meglio
            alias = gia.setdefault("alias", [])
            if titolo != gia["titolo"] and titolo not in alias:
                alias[:] = (alias + [titolo])[-6:]
            return gia["id"], False
        lid = hashlib.sha1(" ".join(sorted(parole(titolo))).encode()).hexdigest()[:6]
        if any(l["id"] == lid for l in self.leak):
            return lid, False
        self.leak.append({"id": lid, "titolo": titolo, "fonte": fonte, "link": link, "etichetta": etichetta,
                          "data": adesso.date().isoformat(), "stato": "aperto"})
        return lid, True

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


def chiave_origine(v):
    """Da dove viene la voce: l'insider citato, altrimenti il primo sito citato, altrimenti la voce stessa."""
    for o in v.get("origini", []):
        if o["tipo"] == "insider":
            return o["chiave"]
    for o in v.get("origini", []):
        if o["tipo"] == "testata":
            return o["chiave"]
    return v.get("autore") or dominio(v["link"])


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

    # Fonti indipendenti: ogni voce conta per la SUA origine, risalendo la catena
    # (Multiplayer cita PC Gamer che cita il sito della band → una fonte sola).
    # Dieci siti che riprendono lo stesso tweet sono UNA fonte.
    origine_di = {dominio(v["link"]): chiave_origine(v) for v in gruppo}

    def risali(k):
        for _ in range(5):
            nxt = origine_di.get(k)
            if not nxt or nxt == k:
                break
            k = nxt
        return k

    indip = {risali(chiave_origine(v)) for v in gruppo
             if v["area"] != "Community" or v.get("origini")}
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


# ---------------------------------------------------------------------------
# Nomi leggibili delle fonti e categorie (per Radar)
# ---------------------------------------------------------------------------
NOMI = {
    "blog.playstation.com": "PlayStation Blog", "news.xbox.com": "Xbox Wire", "store.steampowered.com": "Steam",
    "everyeye.it": "Everyeye", "multiplayer.it": "Multiplayer.it", "spaziogames.it": "SpazioGames",
    "it.ign.com": "IGN Italia", "gamesource.it": "GameSource", "console-tribe.com": "Console Tribe",
    "nintendon.it": "Nintendon", "akibagamers.it": "AkibaGamers", "gamerbrain.net": "GamerBrain",
    "ign.com": "IGN", "gamespot.com": "GameSpot", "eurogamer.net": "Eurogamer",
    "videogameschronicle.com": "VGC", "polygon.com": "Polygon", "kotaku.com": "Kotaku",
    "gamesradar.com": "GamesRadar+", "gameinformer.com": "Game Informer", "theverge.com": "The Verge",
    "destructoid.com": "Destructoid", "gamingbolt.com": "GamingBolt", "insider-gaming.com": "Insider Gaming",
    "wccftech.com": "Wccftech", "digitalfoundry.net": "Digital Foundry", "pushsquare.com": "Push Square",
    "purexbox.com": "Pure Xbox", "nintendolife.com": "Nintendo Life", "pcgamer.com": "PC Gamer",
    "rockpapershotgun.com": "Rock Paper Shotgun", "gematsu.com": "Gematsu", "siliconera.com": "Siliconera",
    "automaton-media.com": "Automaton", "gamesindustry.biz": "GamesIndustry.biz",
    "gamedeveloper.com": "Game Developer", "resetera.com": "ResetEra", "neogaf.com": "NeoGAF",
    "dealabs.com": "Dealabs", "gamesbeat.com": "GamesBeat", "venturebeat.com": "VentureBeat",
    "mp1st.com": "MP1st", "breakit.se": "Breakit", "rockstargames.com": "Rockstar Newswire",
    "nintendo.com": "Nintendo", "nintendo.co.jp": "Nintendo", "bloomberg.com": "Bloomberg",
    "windowscentral.com": "Windows Central", "esrb.org": "ESRB", "pegi.info": "PEGI",
    "grac.or.kr": "Ente di rating coreano", "x.com": "X", "twitter.com": "X", "bsky.app": "Bluesky",
}


def nome_fonte(x):
    """Nome leggibile di un link, di un dominio o di un insider."""
    if not x:
        return ""
    if x.startswith("http"):
        if "reddit.com" in x:
            m = re.search(r"/r/([^/+]+)", x)
            return f"Reddit · r/{m.group(1)}" if m else "Reddit"
        m = _BSKY_POST.match(x)
        if m:
            return _HANDLE_BSKY.get(m.group(1).lower(), "@" + m.group(1))
        m = _X_POST.match(x)
        if m:
            return _HANDLE_X.get(m.group(1).lower(), "@" + m.group(1))
        x = dominio(x)
    if x in INSIDER or x.startswith("@"):
        return x
    for d, n in NOMI.items():
        if x == d or x.endswith("." + d):
            return n
    return x


CATEGORIE = ("PlayStation", "Xbox", "Nintendo", "PC", "Multipiattaforma", "Industria", "Hardware", "Mobile", "Altro")
_CAT = [
    ("PlayStation", r"\b(ps[456]|playstation|sony|state of play|ps plus|dualsense)\b"),
    ("Xbox", r"\b(xbox|game pass|microsoft)\b"),
    ("Nintendo", r"\b(nintendo|switch|mario|zelda|pok[eé]mon)\b"),
    ("PC", r"\b(pc|steam|valve|epic games store)\b"),
    ("Mobile", r"\b(ios|android|mobile|iphone)\b"),
]


def categoria(titolo, area=""):
    if area == "Industria":
        return "Industria"
    trovate = [c for c, rx in _CAT if re.search(rx, titolo or "", re.I)]
    return trovate[0] if len(trovate) == 1 else ("Multipiattaforma" if trovate else "Altro")


_JSON_APP = re.compile(r"```json\s*(.*?)(?:```|\Z)", re.S | re.I)


def estrai_dati_app(testo, n_max):
    """Toglie dal report il blocco ```json per Radar e lo valida. Ritorna (testo, voci o None)."""
    trovati = list(_JSON_APP.finditer(testo))
    if not trovati:
        return testo, None
    b = trovati[-1]
    testo = (testo[:b.start()] + testo[b.end():]).rstrip() + "\n"
    try:
        dati = json.loads(b.group(1).strip())
    except ValueError:
        return testo, None
    voci = []
    for d in dati if isinstance(dati, list) else []:
        if not isinstance(d, dict):
            continue
        nums = []
        for x in d.get("notizie") or []:
            try:
                n = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= n_max and n not in nums:
                nums.append(n)
        if not nums:
            continue
        cat = str(d.get("categoria") or "")
        voci.append({
            "notizie": nums,
            "titolo": " ".join(str(d.get("titolo") or "").split())[:140],
            "riassunto": " ".join(str(d.get("riassunto") or "").split())[:900],
            "categoria": cat if cat in CATEGORIE else "",
            "nel_report": d.get("nel_report") is not False,
        })
    return testo, voci or None
