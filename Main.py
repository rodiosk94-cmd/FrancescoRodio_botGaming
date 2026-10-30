import html
import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import errors, types

import fonti
import radar
from fonti import INSIDER, Stato, dominio, norm_link

# Modalità test: SOLO_FEED=1 raccoglie le fonti e stampa le statistiche, senza Gemini né Telegram
SOLO_FEED = os.environ.get("SOLO_FEED") == "1"
# INVIA_TELEGRAM=0: prova completa (Gemini + dati per Radar) senza messaggio e senza toccare lo storico
INVIA_TELEGRAM = os.environ.get("INVIA_TELEGRAM", "1") != "0"
RADAR_URL = os.environ.get("RADAR_URL", "").strip()  # indirizzo dell'app, se pubblicata
STATO = Stato(Path(__file__).with_name("stato.json"))  # storico leak e link inviati, salvato nel repo

system_instruction = """
Sei un agente specializzato in Gaming Intelligence, Giornalismo d'Inchiesta e Fact-Checking. Il tuo compito è monitorare, verificare e redigere articoli e report estremamente approfonditi, completi ed esaustivi sul mondo dei videogiochi (PlayStation, Xbox, Nintendo, PC, hardware, software, case di sviluppo e dinamiche di mercato).

## METODOLOGIA DI RACCOLTA E VERIFICA
1. Fonti Ufficiali e Primarie: Consulta annunci ufficiali, blog degli sviluppatori e publisher (es. PlayStation Blog, Xbox Wire, Nintendo Direct), dichiarazioni dirette, comunicati stampa e report finanziari.
2. Double-Check e Fact-Checking: Incrocia ogni notizia su più fonti. Ogni notizia arriva già con un'ETICHETTA DI CREDIBILITÀ calcolata dal sistema (fonte ufficiale, numero di fonti indipendenti, storico dell'insider): riportala sempre e non alzarla mai di tua iniziativa. Risali sempre alla fonte originale (comunicato, insider, testata che ha dato la notizia per prima) e attribuisci la notizia a lei, non a chi l'ha ripresa.
3. Analisi Empirica della Community: Analizza a fondo discussioni, thread e reazioni su Reddit, forum e social media per estrarre il sentiment dei giocatori, problemi tecnici o polemiche.

## STRUTTURA DEL REPORT NOTIZIE (LONG-FORM)
Ogni notizia si apre con la sua etichetta (es. "✅ UFFICIALE", "🟢 LEAK AFFIDABILE", "🟡 RUMOR") seguita dal titolo.
1. TITOLO E INQUADRAMENTO GENERALE
2. TRATTAZIONE COMPLETA E DETTAGLI TECNICI
3. FACT-CHECKING E TRIANGOLAZIONE FONTI
4. REAZIONI DELLA COMMUNITY E DIVERGENZE
5. SITOGRAFIA E FONTI CONSULTATE

## REGOLA DI STILE E LUNGHEZZA
- ESAUSTIVITÀ ASSOLUTA: Non sintetizzare né limitarti a brevi riassunti. Sviluppa ogni notizia in modo completo e articolato, fornendo tutti i dettagli disponibili.
- TONO RIGOROSO: Mantieni uno stile giornalistico professionale, oggettivo, preciso e privo di formule di cortesia o elementi promozionali/clickbait.
"""

# 2. Raccolta notizie da feed RSS/Atom
FEEDS = {
    # Reddit + ResetEra: reazioni dei giocatori e rumor/leak. Letti in sequenza (Reddit dà 429 a raffica)
    "Community": [
        # tutti i subreddit in UNA sola richiesta: dai server di GitHub la seconda prende sempre 429.
        # Niente r/gaming: è quasi solo meme e, avendo più voti, oscurerebbe gli altri subreddit
        "https://www.reddit.com/r/Games+GamingLeaksAndRumours+pcgaming+PS5+NintendoSwitch+XboxSeriesX"
        "+videogiochi/top/.rss?t=day&limit=25",
        "https://www.resetera.com/forums/video-games.7/index.rss",
    ],
    "Ufficiali": [  # comunicati di piattaforme e store: fonti primarie
        "https://blog.playstation.com/feed/",
        "https://news.xbox.com/en-us/feed/",
        "https://store.steampowered.com/feeds/news/app/593110/",  # Steam News di Valve
    ],
    "Italia": [
        "https://www.everyeye.it/feed/feed_news_rss.asp",
        "https://multiplayer.it/feed/rss/news/",
        "https://www.spaziogames.it/feed",
        "https://it.ign.com/feed.xml",
        "https://www.gamesource.it/feed/",
        "https://www.console-tribe.com/feed/",
        "https://www.nintendon.it/feed/",
        "https://www.akibagamers.it/feed/",
        "https://www.gamerbrain.net/feed/",
    ],
    "Estero": [
        "https://feeds.feedburner.com/ign/news",
        "https://www.gamespot.com/feeds/news/",
        "https://www.eurogamer.net/feed/news",
        "https://www.videogameschronicle.com/feed/",
        "https://www.polygon.com/rss/index.xml",
        "https://kotaku.com/rss",
        "https://www.gamesradar.com/rss/",
        "https://www.gameinformer.com/news.xml",
        "https://www.theverge.com/rss/games/index.xml",
        "https://www.destructoid.com/feed/",
        "https://www.gamingbolt.com/feed",
        "https://insider-gaming.com/feed/",       # spesso prima fonte di leak e notizie interne
        "https://wccftech.com/topic/games/feed/",  # rumor tecnici e hardware
        "https://www.digitalfoundry.net/feed",     # analisi tecniche
    ],
    "Piattaforme": [  # testate dedicate a una singola piattaforma
        "https://www.pushsquare.com/feeds/latest",   # PlayStation
        "https://www.purexbox.com/feeds/latest",     # Xbox
        "https://www.nintendolife.com/feeds/latest", # Nintendo
        "https://www.pcgamer.com/rss/",              # PC
        "https://www.rockpapershotgun.com/feed/news",
    ],
    "Giappone": [  # mercato giapponese e publisher asiatici
        "https://www.gematsu.com/feed",
        "https://www.siliconera.com/feed/",
        "https://automaton-media.com/en/feed/",
    ],
    "Industria": [  # mercato, licenziamenti, acquisizioni, dati di vendita
        "https://www.gamesindustry.biz/feed",
        "https://www.gamedeveloper.com/rss.xml",
    ],
}
# Report alle 06:00 (copre la notte, 10 ore) e alle 20:00 (copre la giornata, 14 ore), ora italiana
ROMA = ZoneInfo("Europe/Rome")
_adesso_roma = datetime.now(ROMA)
ora_it = _adesso_roma.hour
ORE_BASE = 10 if 3 <= ora_it < 12 else 14
ORE_MAX = 36  # dopo run saltate o fallite si recupera al massimo un giorno e mezzo di notizie


def _ultimo_report():
    try:
        return datetime.fromisoformat(STATO.ultimo_report) if STATO.ultimo_report else None
    except ValueError:
        return None


def _slot_corrente(adesso):
    """L'ultimo orario di report (06:00 o 20:00 italiane) già passato."""
    for h in (20, 6):
        s = adesso.replace(hour=h, minute=0, second=0, microsecond=0)
        if s <= adesso:
            return s
    return (adesso - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)


_ultimo = _ultimo_report()
# Run del cron di GitHub (spesso in ritardo di ore): se il report di questo turno è già uscito,
# avviato da un trigger esterno puntuale o da una run precedente, non ne mando un secondo.
if os.environ.get("DA_CRON") == "1" and _ultimo and _ultimo >= _slot_corrente(_adesso_roma) - timedelta(minutes=30):
    print(f"Report del turno delle {_slot_corrente(_adesso_roma):%H:%M} già inviato "
          f"({_ultimo.astimezone(ROMA):%d/%m %H:%M}): niente da fare")
    sys.exit(0)
# Finestra: dall'ultimo report riuscito (così una run saltata o in ritardo non perde notizie),
# mai meno della finestra normale e mai più di ORE_MAX
ORE = ORE_BASE
if _ultimo:
    ORE = min(max(ORE_BASE, int((_adesso_roma - _ultimo).total_seconds() // 3600) + 1), ORE_MAX)
print(f"Finestra notizie: ultime {ORE} ore"
      + (f" (ultimo report: {_ultimo.astimezone(ROMA):%d/%m %H:%M})" if _ultimo else ""))
MAX_PER_FEED = 10    # voci massime per feed
MAX_COMMUNITY = 20   # voci massime per feed Reddit/ResetEra
MAX_ARTICOLI = 40    # articoli da aprire per cercare dichiarazioni (scelti a turno tra le testate)
MAX_CIT = 3          # paragrafi con dichiarazioni per articolo
MAX_BSKY = 8         # post massimi per insider su Bluesky
BSKY_API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"  # pubblica, senza login
PAUSA_REDDIT = 8     # secondi tra due richieste a Reddit
NO_CITAZIONI = ("reddit.com", "resetera.com", "gamespot.com")  # pagine che bloccano il bot
# titoli che non sono notizie: soluzioni di puzzle, offerte, guide
_NON_NOTIZIA = re.compile(
    r"\b(wordle|nyt connections|connections hint|strands hint|quordle|best .{0,40} deals|deal alert|"
    r"codes|codici|walkthrough|how to|guide|tier list|guida (completa|a|per|ai|alle)|come sbloccare|"
    r"come ottenere|dove trovare|in offerta|in sconto|offerta amazon|offerte)\b", re.I)
UA = {"User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0; +https://github.com/rodiosk94-cmd)"}
MODELLI = [  # in ordine di preferenza; le versioni Lite hanno una capacità separata e sono meno sature
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]
GIRI = 2            # quante volte scorrere tutta la lista prima di arrendersi
PAUSA_GIRO = 60     # secondi di attesa tra un giro e l'altro
TIMEOUT_GEMINI = 150        # secondi massimi per una singola chiamata (senza, un modello bloccato tiene fermo il job;
                            # 90s rischiava di troncare un report lungo che stava arrivando)
BUDGET_GEMINI = 660         # secondi massimi per tutta la fase Gemini, giri e pause compresi (job: 20 minuti)

_T0 = time.monotonic()


def fase(nome):
    """Log dei tempi: dopo quanti secondi dall'avvio inizia/finisce ogni fase."""
    print(f"[tempi] {nome}: {time.monotonic() - _T0:.0f}s dall'avvio", flush=True)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _data(s):
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)          # formato RSS
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s)     # formato Atom
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pulisci(s, n=300):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def _parse_xml(raw):
    try:
        return ET.fromstring(raw)
    except ET.ParseError:  # feed malformato: ripulisco caratteri di controllo e "&" non escapati
        t = raw.decode("utf-8", "replace")
        t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
        t = re.sub(r"&(?!#?\w+;)", "&amp;", t)
        return ET.fromstring(t.encode("utf-8"))


def _scarica(url):
    r = requests.get(url, headers=UA, timeout=20)
    if r.status_code == 429:  # Reddit: attendo quanto chiesto (max 60s) e riprovo una volta
        attesa = min(int(r.headers.get("Retry-After", "30") or 30), 60)
        print(f"[429] {url}: riprovo tra {attesa}s")
        time.sleep(attesa)
        r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.content


_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def leggi_feed(url, massimo):
    root = _parse_xml(_scarica(url))
    adesso = datetime.now(timezone.utc)
    limite = adesso - timedelta(hours=ORE)
    voci = []
    for it in (e for e in root.iter() if _local(e.tag) in ("item", "entry")):
        titolo = _child(it, "title")
        link = _child(it, "link")
        data = _child(it, "pubDate", "published", "updated", "date")
        desc = _child(it, "description", "summary", "encoded", "content")
        d = _data(data.text) if data is not None and data.text else None
        if d and (d < limite or d > adesso + timedelta(hours=1)):  # vecchia o con data futura errata
            continue
        t = _pulisci(titolo.text if titolo is not None else "", 200)
        if not t or _NON_NOTIZIA.search(t):
            continue
        grezzo = html.unescape(desc.text or "") if desc is not None else ""
        href = ((link.get("href") or link.text or "").strip() if link is not None else "")
        voci.append({
            "titolo": t,
            "link": href,
            "data": d.strftime("%d/%m %H:%M UTC") if d else "n.d.",
            "iso": d.isoformat(timespec="minutes") if d else None,
            "testo": _pulisci(grezzo),
            # link dentro la descrizione: su Reddit/ResetEra è l'articolo o il post originale
            "link_esterni": [u for u in _HREF.findall(grezzo)
                             if u.startswith("http") and dominio(u) != dominio(href)],
        })
        if len(voci) >= massimo:
            break
    return voci


def leggi_bluesky(nome, handle, massimo):
    """Ultimi post di un insider su Bluesky (API pubblica, niente repost né risposte)."""
    r = requests.get(BSKY_API, params={"actor": handle, "filter": "posts_no_replies", "limit": 30},
                     headers=UA, timeout=20)
    r.raise_for_status()
    adesso = datetime.now(timezone.utc)
    limite = adesso - timedelta(hours=ORE)
    voci = []
    for it in r.json().get("feed", []):
        if it.get("reason"):  # repost di qualcun altro
            continue
        post = it.get("post", {})
        rec = post.get("record", {})
        d = _data(rec.get("createdAt", ""))
        if not d or d < limite or d > adesso + timedelta(hours=1):
            continue
        testo = " ".join((rec.get("text") or "").split())
        esterni = [f["uri"] for fc in rec.get("facets", []) for f in fc.get("features", []) if f.get("uri")]
        ext = (post.get("embed") or {}).get("external") or {}
        if ext.get("uri"):
            esterni.append(ext["uri"])
            testo += f" [link: {ext.get('title', '')}]"
        if len(testo) < 60 and not esterni:  # battute e reazioni: non sono notizie
            continue
        voci.append({
            "titolo": _pulisci(testo, 160),
            "link": f"https://bsky.app/profile/{handle}/post/{post.get('uri', '').rsplit('/', 1)[-1]}",
            "data": d.strftime("%d/%m %H:%M UTC"),
            "iso": d.isoformat(timespec="minutes"),
            "testo": _pulisci(testo, 500),
            "link_esterni": esterni,
            "autore": nome,
        })
        if len(voci) >= massimo:
            break
    return voci


class _Paragrafi(HTMLParser):
    """Estrae dalla pagina i paragrafi <p> (con i link che contengono) e tutti i link del corpo."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragrafi, self._buf, self._in_p, self._skip = [], [], 0, 0
        self.link_par, self._link_buf, self.link_pagina = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "p":
            self._in_p += 1
            self._buf, self._link_buf = [], []
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith("http"):
                self.link_pagina.append(href)
                if self._in_p:
                    self._link_buf.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "p" and self._in_p:
            self._in_p -= 1
            testo = " ".join("".join(self._buf).split())
            if testo:
                self.paragrafi.append(testo)
                self.link_par.append(self._link_buf)

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buf.append(data)


# frase tra virgolette di almeno ~40 caratteri: una dichiarazione, non un titolo o un nome
_CITAZIONE = re.compile(r'[“«"]([^”»"]{40,600})[”»"]')


def analizza_articolo(v):
    """Apre l'articolo: estrae le dichiarazioni dirette e risale alla fonte originale della notizia."""
    url = v["link"]
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        parser = _Paragrafi()
        parser.feed(r.content.decode("utf-8", "replace"))
    except Exception as e:  # sito bloccato o lento: l'articolo resta senza citazioni
        print(f"[articolo KO] {url}: {e}")
        return [], []
    trovate = []
    for par in parser.paragrafi:
        if _CITAZIONE.search(par):
            trovate.append(par[:500] + ("…" if len(par) > 500 else ""))
            if len(trovate) >= MAX_CIT:
                break
    origini = fonti.trova_origini(v, list(zip(parser.paragrafi, parser.link_par)), parser.link_pagina)
    return trovate, origini


def leggi_sicuro(url, massimo):
    try:
        voci = leggi_feed(url, massimo)
    except Exception as e:  # un feed rotto non blocca gli altri
        print(f"[KO] {url}: {e}")
        return None
    print(f"[OK] {url}: {len(voci)} voci")
    return voci


# Testate in parallelo; Reddit e ResetEra in sequenza, con pausa tra le richieste a Reddit
lavori = [(area, url) for area, urls in FEEDS.items() if area != "Community" for url in urls]
insider_bsky = [(nome, d["bsky"]) for nome, d in INSIDER.items() if d["bsky"]]


def leggi_bluesky_sicuro(nome, handle):
    try:
        voci = leggi_bluesky(nome, handle, MAX_BSKY)
    except Exception as e:
        print(f"[KO] Bluesky {handle}: {e}")
        return None
    print(f"[OK] Bluesky {handle}: {len(voci)} post")
    return voci


with ThreadPoolExecutor(max_workers=10) as pool:
    esiti = list(pool.map(lambda au: leggi_sicuro(au[1], MAX_PER_FEED), lavori))
    for (nome, handle), voci in zip(insider_bsky, pool.map(lambda nh: leggi_bluesky_sicuro(*nh), insider_bsky)):
        lavori.append(("Insider", f"bsky:{handle}"))
        esiti.append(voci)
reddit_letti = 0
for url in FEEDS["Community"]:
    if "reddit.com" in url:
        if reddit_letti:
            time.sleep(PAUSA_REDDIT)
        reddit_letti += 1
    lavori.append(("Community", url))
    esiti.append(leggi_sicuro(url, MAX_COMMUNITY))

raccolte = []  # voci con area, feed, leak e fonti d'origine
visti = set()   # stesso articolo in più feed (link o titolo identici): lo tengo una volta sola
gia_inviate = 0
for (area, url), voci in zip(lavori, esiti):
    for v in voci or []:
        chiavi = {norm_link(v["link"]), v["titolo"].lower()}
        if chiavi & visti:
            continue
        visti |= chiavi
        # già nel report precedente (es. top del giorno di Reddit). Nell'aggiornamento solo-app le teniamo:
        # servono a mostrare in Radar tutte le notizie della finestra
        if INVIA_TELEGRAM and STATO.gia_inviato(v["link"]):
            gia_inviate += 1
            continue
        v["area"], v["feed"] = area, url
        v["leak"] = fonti.e_leak(v)
        v["origini"] = fonti.trova_origini(v)  # dal feed: link nella descrizione, insider nominati
        raccolte.append(v)
ko = sum(1 for e in esiti if e is None)
print(f"Feed letti: {len(esiti) - ko}/{len(esiti)} — voci raccolte: {len(raccolte)} "
      f"(+{gia_inviate} già inviate e scartate)")

# 2b. Apre gli articoli (in parallelo): dichiarazioni dirette e fonte originale della notizia.
# Prima i leak (serve sapere da chi vengono), poi gli altri a turno tra le testate.
per_feed = {}
for v in sorted(raccolte, key=lambda v: not v["leak"]):
    if v["link"].startswith("http") and not any(d in v["link"] for d in NO_CITAZIONI + ("bsky.app",)):
        per_feed.setdefault(v["feed"], []).append(v)
da_aprire = [c.pop(0) for c in per_feed.values() if c and c[0]["leak"]]
code = list(per_feed.values())
while code and len(da_aprire) < MAX_ARTICOLI:
    for coda in code:
        if coda and len(da_aprire) < MAX_ARTICOLI:
            da_aprire.append(coda.pop(0))
    code = [c for c in code if c]
with ThreadPoolExecutor(max_workers=10) as pool:
    for v, (cit, orig) in zip(da_aprire, pool.map(analizza_articolo, da_aprire)):
        v["citazioni"] = cit
        if orig:
            v["origini"] = orig
print(f"Dichiarazioni trovate in {sum(1 for v in da_aprire if v.get('citazioni'))}/{len(da_aprire)} articoli; "
      f"fonte originale individuata per {sum(1 for v in raccolte if v['origini'])}/{len(raccolte)} voci")

# 2c. Raggruppa le voci sulla stessa notizia ed etichetta ogni gruppo
notizie, scartati = [], 0
for gruppo in fonti.raggruppa(raccolte):
    etichetta, info = fonti.valuta_gruppo(gruppo, STATO)
    if etichetta is None:
        scartati += 1
        continue
    peso_area = {"Ufficiali": 0, "Insider": 1}
    gruppo.sort(key=lambda v: (peso_area.get(v["area"], 3 if v["area"] == "Community" else 2), v["titolo"]))
    notizie.append({"etichetta": etichetta, "voci": gruppo, **info})
notizie.sort(key=lambda n: (fonti.ORDINE.index(n["etichetta"]), -len(n["indipendenti"])))
conteggio_etichette = {}
for n in notizie:
    conteggio_etichette[n["etichetta"]] = conteggio_etichette.get(n["etichetta"], 0) + 1
print(f"Notizie: {len(notizie)} {conteggio_etichette} — rumor senza fonte d'origine scartati: {scartati}")


def descrivi_fonte(chiave):
    c, n = STATO.conteggi(chiave)
    storico = f"storico: {c} leak confermati, {n} non confermati" if c or n else "storico: nessun dato"
    return f"{chiave} (affidabilità {STATO.affidabilita(chiave):.0%}, {storico})"


blocchi = []
titoli = []  # (area, titolo, link) per il riepilogo di emergenza
consentiti = set()  # link che il report può citare
for i, n in enumerate(notizie, 1):
    righe = [f"=== NOTIZIA {i} — ETICHETTA: {n['etichetta']} ==="]
    for o in n["origini"]:
        tipo = {"ufficiale": "comunicato ufficiale", "insider": "insider", "testata": "testata"}[o["tipo"]]
        chi = descrivi_fonte(o["chiave"]) if o["tipo"] == "insider" else o["chiave"]
        righe.append(f"Fonte originale ({tipo}): {chi}" + (f" — {o['url']}" if o["url"] else ""))
        if o["url"]:
            consentiti.add(o["url"])
    autori = {v["autore"] for v in n["voci"] if v.get("autore")}
    for a in sorted(autori):
        righe.append(f"Post diretto dell'insider: {descrivi_fonte(a)}")
    if len(n["indipendenti"]) > 1:
        righe.append(f"Fonti indipendenti ({len(n['indipendenti'])}): {', '.join(n['indipendenti'])}")
    for k, v in enumerate(n["voci"]):
        titoli.append((v["area"], v["titolo"], v["link"]))
        consentiti.add(v["link"])
        consentiti.update(v.get("link_esterni", []))
        righe.append(f"[{v['area']}] {v['titolo']}\nFonte: {v['link']}\nData: {v['data']}")
        if k == 0 or v["area"] in ("Ufficiali", "Insider", "Community"):  # dalle repliche basta il titolo
            righe.append(v["testo"])
        if v.get("citazioni"):
            righe.append("Citazioni dall'articolo:\n" + "\n".join(f"> {c}" for c in v["citazioni"]))
    blocchi.append("\n".join(righe))

if not blocchi:
    sys.exit("Nessuna notizia raccolta dai feed: controlla le righe [KO] nel log.")

# Leak ancora in attesa di conferma: Gemini segnala quelli confermati dalle fonti ufficiali di oggi
aperti = sorted(STATO.leak_aperti(), key=lambda l: l["data"], reverse=True)[:40]
sezione_leak = ""
if aperti and any(n["etichetta"] == fonti.UFFICIALE for n in notizie):
    sezione_leak = (
        "\n\nLEAK IN ATTESA DI CONFERMA (dai report passati):\n"
        + "\n".join(f"- id {l['id']} ({l['data']}, fonte {l['fonte']}): {l['titolo']}" for l in aperti)
        + "\nSe una notizia ✅ UFFICIALE di oggi conferma in modo esplicito uno di questi leak, citalo nella "
          "notizia ('confermato il leak di <fonte> del <data>'). In ogni caso chiudi il report con UNA riga "
          "finale nel formato 'CONFERMATI: id1, id2' oppure 'CONFERMATI: nessuno'. Conta solo le conferme "
          "ufficiali, non le notizie che riprendono lo stesso rumor."
    )

prompt = (
    f"Qui sotto trovi notizie e discussioni delle ultime {ORE} ore raccolte da feed RSS e da Bluesky, "
    "già raggruppate per notizia ed etichettate. "
    "Scrivi il report usando SOLO queste fonti: non aggiungere fatti, date o link che non compaiono qui. "
    "Ogni notizia del report deve avere almeno un link: nella sitografia usa esattamente i link indicati "
    "alle voci 'Fonte' e 'Fonte originale'.\n"
    "ETICHETTE (calcolate dal sistema, riportale in apertura di ogni notizia e non alzarle mai): "
    "✅ UFFICIALE = c'è un comunicato di piattaforma/publisher o lo cita direttamente; "
    "🟢 LEAK AFFIDABILE = leak da un insider con buono storico o da almeno due fonti indipendenti; "
    "🟢 CONFERMATA DA PIÙ FONTI = notizia riportata da più fonti indipendenti; "
    "🟡 RUMOR = una sola fonte non verificata: presentalo come indiscrezione, al condizionale; "
    "📰 FONTE UNICA = notizia di una sola testata; "
    "💬 COMMUNITY = solo discussioni Reddit/ResetEra: usale per le reazioni dei giocatori, mai come notizia. "
    "Se unisci due gruppi che parlano dello stesso fatto (es. un articolo italiano e uno inglese), usa "
    "l'etichetta più alta tra i due; non promuovere un 🟡 RUMOR per nessun altro motivo.\n"
    "FONTE ORIGINALE: quando c'è la riga 'Fonte originale', attribuisci la notizia a quella fonte "
    "(es. 'secondo Tom Henderson', 'come annunciato sul PlayStation Blog') e mettila per prima in sitografia; "
    "le testate che l'hanno ripresa sono solo conferme. Per i leak indica l'affidabilità storica dell'insider "
    "quando è riportata. I post [Insider] sono post Bluesky dei giornalisti: ignora quelli non legati ai videogiochi.\n"
    "SELEZIONE: dai la precedenza alle notizie ✅ e 🟢; tralascia recensioni di prodotti, guide, offerte e "
    "contenuti promozionali.\n"
    "DICHIARAZIONI: le righe 'Citazioni dall'articolo' sono paragrafi originali che contengono "
    "dichiarazioni dirette. Nella trattazione di ogni notizia riporta tra virgolette le dichiarazioni "
    "rilevanti di sviluppatori, director, publisher o dirigenti, indicando chi parla e il suo ruolo "
    "SOLO se risultano dal testo. Usa esclusivamente frasi presenti in quelle righe: non inventare, "
    "non completare e non attribuire frasi a persone diverse. Citazioni brevi (massimo 2-3 frasi); "
    "se sono in inglese traducile in italiano indicando '(trad.)'. Se una notizia non ha citazioni, "
    "non aggiungerne.\n"
    "DATI PER L'APP: dopo il report (e prima dell'eventuale riga CONFERMATI) aggiungi un blocco di codice "
    "```json con un array: PRIMA un oggetto per ogni notizia trattata nel report, nello stesso ordine, "
    "POI un oggetto per ciascuna delle altre NOTIZIA della lista che riguardano i videogiochi (escluse quelle "
    "💬 COMMUNITY, le offerte e le recensioni di prodotti). Formato: "
    "{\"notizie\": [i numeri delle NOTIZIA che hai usato, es. [4, 7] se le hai unite], "
    "\"nel_report\": true se l'hai trattata nel report, altrimenti false, "
    "\"titolo\": \"titolo in italiano, massimo 90 caratteri\", "
    "\"riassunto\": \"in italiano, solo fatti presenti nelle fonti, al condizionale per i rumor: 2-3 frasi "
    "per le notizie del report, una frase per le altre\", "
    "\"categoria\": una tra \"PlayStation\", \"Xbox\", \"Nintendo\", \"PC\", \"Multipiattaforma\", "
    "\"Industria\", \"Hardware\", \"Mobile\", \"Altro\"}. Niente link e niente testo dopo il blocco, "
    "a parte la riga CONFERMATI se richiesta."
    + sezione_leak + "\n\n"
    + "\n\n".join(blocchi)
)

if SOLO_FEED:
    conteggio = {}
    for v in raccolte:
        conteggio[v["area"]] = conteggio.get(v["area"], 0) + 1
    print("Voci per area:", conteggio)
    print(f"Prompt: {len(prompt)} caratteri")
    if os.environ.get("GEMINI_API_KEY"):  # conteggio esatto dei token, se la chiave è disponibile
        try:
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
            n = client.models.count_tokens(model=MODELLI[0], contents=system_instruction + prompt).total_tokens
            print(f"Token in ingresso: {n}")
        except Exception as e:
            print(f"Conteggio token non riuscito: {e}")
    sys.exit(0)

# 3. Chiamata a Gemini: scorre i modelli a giri (503 = sovraccarico temporaneo)
fase("raccolta fonti finita, inizio Gemini")
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                      http_options=types.HttpOptions(timeout=TIMEOUT_GEMINI * 1000))  # millisecondi
response = None
_inizio_gemini = time.monotonic()
_fuori_budget = False
for giro in range(GIRI):
    for modello in MODELLI:
        if time.monotonic() - _inizio_gemini > BUDGET_GEMINI:
            print(f"Budget Gemini di {BUDGET_GEMINI}s esaurito: mi fermo")
            _fuori_budget = True
            break
        _t = time.monotonic()
        try:
            response = client.models.generate_content(
                model=modello,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
        except errors.APIError as e:
            # chiave non valida o progetto senza accesso: inutile provare gli altri modelli.
            # Tutto il resto (400 di un singolo modello, 404, 429, 5xx, 504 DEADLINE_EXCEEDED…) → modello dopo:
            # prima un 504 o un 400 di un solo modello faceva fallire l'intera run.
            if e.code in (401, 403) or (e.code == 400 and "API key" in str(e)):
                raise
            print(f"[{modello}] errore {e.code} dopo {time.monotonic() - _t:.0f}s, provo il modello successivo: "
                  f"{str(e)[:200]}")
            continue
        except Exception as e:  # timeout o rete: non blocco il job, passo al modello dopo
            print(f"[{modello}] {type(e).__name__} dopo {time.monotonic() - _t:.0f}s, provo il modello successivo")
            continue
        if response.text:
            print(f"Report generato con {modello} in {time.monotonic() - _t:.0f}s")
            break
        print(f"[{modello}] risposta vuota, provo il modello successivo")
        response = None
    if response is not None or _fuori_budget:
        break
    if giro < GIRI - 1:
        print(f"Nessun modello disponibile, nuovo giro tra {PAUSA_GIRO}s")
        time.sleep(PAUSA_GIRO)
fase("Gemini finito")

adesso = fonti.ora_utc()
confermati = []
meta_app = None
if response is not None:
    article_text, ids = fonti.estrai_confermati(response.text)
    article_text, meta_app = fonti.estrai_dati_app(article_text, len(notizie))
    print(f"Dati per Radar da Gemini: {len(meta_app) if meta_app else 'assenti, uso i gruppi'}")
    # nel report restano solo i link presenti nelle fonti raccolte: niente URL inventati
    article_text, rimossi = fonti.filtra_link(article_text, consentiti)
    if rimossi:
        print(f"Link non presenti nelle fonti rimossi dal report: {len(rimossi)} → {rimossi[:5]}")
    # conferme dei leak passati: quelle segnalate da Gemini (solo id esistenti) + somiglianza
    # diretta con i titoli delle notizie ufficiali di oggi
    aperti_ids = {l["id"] for l in STATO.leak_aperti()}
    titoli_ufficiali = [fonti.parole(v["titolo"]) for n in notizie if n["etichetta"] == fonti.UFFICIALE
                        for v in n["voci"]]
    scartati_ids = [i for i in ids if i in aperti_ids and not STATO.conferma_plausibile(i, titoli_ufficiali)]
    if scartati_ids:
        print(f"Conferme di Gemini senza una notizia ufficiale sullo stesso argomento, ignorate: {scartati_ids}")
    ids = [i for i in ids if i in aperti_ids and i not in scartati_ids]
    for l in STATO.leak_aperti():
        p = fonti.parole(l["titolo"])
        if any(fonti.simili(p, t, minimo=4, soglia=0.6) for t in titoli_ufficiali):
            ids.append(l["id"])
    for lid in dict.fromkeys(ids):
        l = STATO.conferma(lid, adesso)
        if l:
            confermati.append(l)
            print(f"Leak confermato: [{l['id']}] {l['titolo']} — punto a {l['fonte']}")
    if confermati:
        article_text += "\n**📈 STORICO LEAK**\n" + "\n".join(
            f"• Confermato il leak di {l['fonte']} del {l['data']}: {l['titolo']} "
            f"(affidabilità ora {STATO.affidabilita(l['fonte']):.0%})" for l in confermati)
else:
    # Gemini irraggiungibile: invio comunque i titoli raccolti, senza report
    print("Gemini non disponibile: invio il riepilogo dei titoli.")
    righe = ["**RIEPILOGO TITOLI (Gemini non disponibile, report non generato)**", ""]
    area_corrente = None
    for area, titolo, link in titoli:
        if area != area_corrente:
            righe += ["", f"**{area}**"]
            area_corrente = area
        righe.append(f"• {titolo}\n{link}")
    article_text = "\n".join(righe)

# 4. Invio a Telegram
fase("inizio invio Telegram")
bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]
TG_URL = f"https://api.telegram.org/bot{bot_token}/sendMessage"
LIMIT = 4000  # Telegram: max 4096 caratteri, contati dopo il parsing dell'HTML


def md_to_html(md: str) -> str:
    """Markdown di Gemini -> HTML di Telegram (titoli, grassetto, link)."""
    t = html.escape(md, quote=False)
    t = re.sub(r"^#{1,6}\s*(.+)$",
               lambda m: "<b>" + m.group(1).replace("*", "") + "</b>", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def split_text(text: str, limit: int = LIMIT) -> list[str]:
    """Divide sui fine riga, mai a metà parola o a metà formattazione."""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # singola riga più lunga del limite
            if cur.strip():
                chunks.append(cur)
            cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            if cur.strip():
                chunks.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        chunks.append(cur)
    return chunks


def _post_telegram(payload):
    """POST con nuovi tentativi: 429 (flood limit, attende retry_after), 5xx ed errori di rete.
    Prima un 429 a metà di un report lungo faceva fallire la run dopo aver già inviato metà messaggi."""
    r = None
    for tentativo in range(5):
        try:
            r = requests.post(TG_URL, json=payload, timeout=30)
        except requests.RequestException as e:
            print(f"[Telegram] errore di rete ({type(e).__name__}), nuovo tentativo")
            time.sleep(5)
            continue
        if r.status_code == 429:
            try:
                attesa = int((r.json().get("parameters") or {}).get("retry_after", 5))
            except (ValueError, AttributeError):
                attesa = 5
            print(f"[Telegram] 429, attendo {attesa}s")
            time.sleep(min(attesa, 60) + 1)
            continue
        if r.status_code >= 500:
            time.sleep(5)
            continue
        return r
    return r


def send_chunk(chunk: str) -> None:
    base = {"chat_id": chat_id, "link_preview_options": {"is_disabled": True}}
    r = _post_telegram({**base, "text": md_to_html(chunk), "parse_mode": "HTML"})
    if r is not None and r.status_code == 400:  # formattazione rifiutata -> ripiego su testo semplice
        r = _post_telegram({**base, "text": chunk})
    if r is None or not r.ok:  # fa fallire il workflow invece di "passare" senza inviare nulla
        sys.exit(f"Invio Telegram fallito ({r.status_code if r is not None else 'rete'}): "
                 f"{r.text[:300] if r is not None else ''}")


usati = {norm_link(u) for u in fonti.link_nel_testo(article_text)}
try:
    voci_app = radar.componi(notizie, meta_app, STATO, usati)  # notizie come le mostra Radar
except Exception:  # un errore nei dati per l'app non deve bloccare il report su Telegram
    traceback.print_exc()
    print("Dati per Radar non composti: invio comunque il report, Radar resta all'aggiornamento precedente")
    voci_app = None
if RADAR_URL:
    article_text = article_text.rstrip() + f"\n\n[📡 Apri il report in Radar]({RADAR_URL})\n"

if INVIA_TELEGRAM:
    for chunk in split_text(article_text):
        send_chunk(chunk)
        time.sleep(1)  # evita il flood limit di Telegram
else:
    print(f"INVIA_TELEGRAM=0: messaggio non inviato ({len(article_text)} caratteri), storico non modificato")

# 5. Aggiorna lo storico (il workflow fa il commit di stato.json): solo dopo un invio riuscito.
# I leak si registrano per notizia come la mostra Radar: due gruppi che Gemini ha unito sono UN leak.
nuovi = 0
for e in voci_app or []:
    if e["_etichetta"] not in (fonti.AFFIDABILE, fonti.RUMOR):
        continue
    if INVIA_TELEGRAM and response is not None and e["in_report"]:  # nel tracker solo i leak del report
        lid, nuovo = STATO.registra_leak(e["titolo"], e["_fonte_principale"], e["_url_origine"],
                                         e["_etichetta"], adesso, e["_links"])
        nuovi += nuovo
        e["leak_id"] = lid
    else:
        gia = STATO.trova_leak(e["titolo"], e["_links"], e["_fonte_principale"])
        e["leak_id"] = gia["id"] if gia else None
if INVIA_TELEGRAM:
    if response is not None:
        STATO.segna_inviati([u for u in consentiti if norm_link(u) in usati], adesso)
        STATO.ultimo_report = adesso.isoformat(timespec="minutes")  # la prossima finestra parte da qui
        print(f"Storico: {nuovi} nuovi leak registrati, {len(confermati)} confermati")
    for l in STATO.scadenze(adesso):
        print(f"Leak mai confermato dopo {fonti.GIORNI_SCADENZA} giorni: [{l['id']}] {l['titolo']} ({l['fonte']})")
    STATO.pulizia(adesso)
    STATO.salva()
# Il messaggio è partito e lo storico è salvato: un errore su Radar non deve far fallire la run,
# altrimenti il workflow non fa il commit di stato.json e il report successivo ripete le stesse notizie
if voci_app is not None:
    try:
        print(f"Radar: report {radar.scrivi(voci_app, STATO, adesso, ORE)} con {len(voci_app)} notizie")
    except Exception:
        traceback.print_exc()
        print("Radar non aggiornato per un errore nei dati (il report Telegram è partito)")
fase("fine")
