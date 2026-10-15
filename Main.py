import html
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import errors, types

# Modalità test: SOLO_FEED=1 raccoglie le fonti e stampa le statistiche, senza Gemini né Telegram
SOLO_FEED = os.environ.get("SOLO_FEED") == "1"

system_instruction = """
Sei un agente specializzato in Gaming Intelligence, Giornalismo d'Inchiesta e Fact-Checking. Il tuo compito è monitorare, verificare e redigere articoli e report estremamente approfonditi, completi ed esaustivi sul mondo dei videogiochi (PlayStation, Xbox, Nintendo, PC, hardware, software, case di sviluppo e dinamiche di mercato).

## METODOLOGIA DI RACCOLTA E VERIFICA
1. Fonti Ufficiali e Primarie: Consulta annunci ufficiali, blog degli sviluppatori e publisher (es. PlayStation Blog, Xbox Wire, Nintendo Direct), dichiarazioni dirette, comunicati stampa e report finanziari.
2. Double-Check e Fact-Checking: Incrocia ogni notizia su più fonti. Se l'informazione deriva da leaker o rumor (es. Reddit r/GamingLeaksAndRumours, ResetEra), verifica lo storico dell'autore e contrassegnala chiaramente come "RUMOR/LEAK".
3. Analisi Empirica della Community: Analizza a fondo discussioni, thread e reazioni su Reddit, forum e social media per estrarre il sentiment dei giocatori, problemi tecnici o polemiche.

## STRUTTURA DEL REPORT NOTIZIE (LONG-FORM)
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
ora_it = datetime.now(ZoneInfo("Europe/Rome")).hour
ORE = 10 if 3 <= ora_it < 12 else 14
MAX_PER_FEED = 6     # voci massime per feed
MAX_COMMUNITY = 20   # voci massime per feed Reddit/ResetEra
MAX_ARTICOLI = 40    # articoli da aprire per cercare dichiarazioni (scelti a turno tra le testate)
MAX_CIT = 3          # paragrafi con dichiarazioni per articolo
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
        voci.append({
            "titolo": t,
            "link": ((link.get("href") or link.text or "").strip() if link is not None else ""),
            "data": d.strftime("%d/%m %H:%M UTC") if d else "n.d.",
            "testo": _pulisci(desc.text if desc is not None else ""),
        })
        if len(voci) >= massimo:
            break
    return voci


class _Paragrafi(HTMLParser):
    """Estrae il testo dei paragrafi <p> di una pagina, ignorando script e stili."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragrafi, self._buf, self._in_p, self._skip = [], [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "p":
            self._in_p += 1
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "p" and self._in_p:
            self._in_p -= 1
            testo = " ".join("".join(self._buf).split())
            if testo:
                self.paragrafi.append(testo)

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buf.append(data)


# frase tra virgolette di almeno ~40 caratteri: una dichiarazione, non un titolo o un nome
_CITAZIONE = re.compile(r'[“«"]([^”»"]{40,600})[”»"]')


def estrai_citazioni(url):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        parser = _Paragrafi()
        parser.feed(r.content.decode("utf-8", "replace"))
    except Exception as e:  # sito bloccato o lento: l'articolo resta senza citazioni
        print(f"[citazioni KO] {url}: {e}")
        return []
    trovate = []
    for par in parser.paragrafi:
        if _CITAZIONE.search(par):
            trovate.append(par[:500] + ("…" if len(par) > 500 else ""))
            if len(trovate) >= MAX_CIT:
                break
    return trovate


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
with ThreadPoolExecutor(max_workers=10) as pool:
    esiti = list(pool.map(lambda au: leggi_sicuro(au[1], MAX_PER_FEED), lavori))
reddit_letti = 0
for url in FEEDS["Community"]:
    if "reddit.com" in url:
        if reddit_letti:
            time.sleep(PAUSA_REDDIT)
        reddit_letti += 1
    lavori.append(("Community", url))
    esiti.append(leggi_sicuro(url, MAX_COMMUNITY))

raccolte = []  # (area, voce)
visti = set()   # stesso articolo in più feed (link o titolo identici): lo tengo una volta sola
for (area, url), voci in zip(lavori, esiti):
    for v in voci or []:
        chiavi = {v["link"].split("?")[0].split("#")[0].rstrip("/").lower(), v["titolo"].lower()}
        if chiavi & visti:
            continue
        visti |= chiavi
        v["feed"] = url
        raccolte.append((area, v))
ko = sum(1 for e in esiti if e is None)
print(f"Feed letti: {len(esiti) - ko}/{len(esiti)} — voci raccolte: {len(raccolte)}")

# 2b. Dichiarazioni: apre gli articoli (in parallelo) e ne estrae i paragrafi con citazioni dirette.
# Gli articoli si scelgono a turno tra le testate, così nessun sito monopolizza il limite.
per_feed = {}
for area, v in raccolte:
    if v["link"].startswith("http") and not any(d in v["link"] for d in NO_CITAZIONI):
        per_feed.setdefault(v["feed"], []).append(v)
da_aprire, code = [], list(per_feed.values())
while code and len(da_aprire) < MAX_ARTICOLI:
    for coda in code:
        if coda and len(da_aprire) < MAX_ARTICOLI:
            da_aprire.append(coda.pop(0))
    code = [c for c in code if c]
with ThreadPoolExecutor(max_workers=10) as pool:
    for v, cit in zip(da_aprire, pool.map(estrai_citazioni, [v["link"] for v in da_aprire])):
        v["citazioni"] = cit
print(f"Dichiarazioni trovate in {sum(1 for v in da_aprire if v.get('citazioni'))}/{len(da_aprire)} articoli")

blocchi = []
titoli = []  # (area, titolo, link) per il riepilogo di emergenza
for area, v in raccolte:
    titoli.append((area, v["titolo"], v["link"]))
    blocco = f"[{area}] {v['titolo']}\nFonte: {v['link']}\nData: {v['data']}\n{v['testo']}"
    if v.get("citazioni"):
        blocco += "\nCitazioni dall'articolo:\n" + "\n".join(f"> {c}" for c in v["citazioni"])
    blocchi.append(blocco)

if not blocchi:
    sys.exit("Nessuna notizia raccolta dai feed: controlla le righe [KO] nel log.")

prompt = (
    f"Qui sotto trovi notizie e discussioni delle ultime {ORE} ore raccolte da feed RSS. "
    "Scrivi il report usando SOLO queste fonti: non aggiungere fatti, date o link che non compaiono qui. "
    "Nella sitografia usa esattamente i link indicati alla voce 'Fonte'.\n"
    "FONTI: [Ufficiali] sono comunicati di piattaforme e store: trattale come fonte primaria. "
    "[Italia], [Estero], [Piattaforme] e [Giappone] sono testate giornalistiche: incrociale per il "
    "fact-checking. [Industria] riguarda mercato, vendite, acquisizioni e licenziamenti. "
    "[Community] sono discussioni di Reddit e ResetEra: usale per le reazioni dei giocatori e per i "
    "RUMOR/LEAK, mai come conferma di un fatto.\n"
    "SELEZIONE: molte voci parlano dello stesso fatto da testate diverse: raggruppale in un'unica "
    "notizia e cita tutte le fonti che la riportano. Dai la precedenza alle notizie confermate da più "
    "fonti o da fonti ufficiali; tralascia recensioni di prodotti, guide, offerte e contenuti promozionali.\n"
    "DICHIARAZIONI: le righe 'Citazioni dall'articolo' sono paragrafi originali che contengono "
    "dichiarazioni dirette. Nella trattazione di ogni notizia riporta tra virgolette le dichiarazioni "
    "rilevanti di sviluppatori, director, publisher o dirigenti, indicando chi parla e il suo ruolo "
    "SOLO se risultano dal testo. Usa esclusivamente frasi presenti in quelle righe: non inventare, "
    "non completare e non attribuire frasi a persone diverse. Citazioni brevi (massimo 2-3 frasi); "
    "se sono in inglese traducile in italiano indicando '(trad.)'. Se una notizia non ha citazioni, "
    "non aggiungerne.\n\n"
    + "\n\n---\n\n".join(blocchi)
)

if SOLO_FEED:
    conteggio = {}
    for area, _ in raccolte:
        conteggio[area] = conteggio.get(area, 0) + 1
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
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
response = None
for giro in range(GIRI):
    for modello in MODELLI:
        try:
            response = client.models.generate_content(
                model=modello,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
        except errors.APIError as e:
            if e.code not in (404, 429, 500, 503):  # errori di configurazione: stop subito
                raise
            print(f"[{modello}] errore {e.code}, provo il modello successivo")
            continue
        if response.text:
            print(f"Report generato con {modello}")
            break
        print(f"[{modello}] risposta vuota, provo il modello successivo")
        response = None
    if response is not None:
        break
    if giro < GIRI - 1:
        print(f"Nessun modello disponibile, nuovo giro tra {PAUSA_GIRO}s")
        time.sleep(PAUSA_GIRO)

if response is not None:
    article_text = response.text
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


def send_chunk(chunk: str) -> None:
    base = {"chat_id": chat_id, "link_preview_options": {"is_disabled": True}}
    r = requests.post(TG_URL, json={**base, "text": md_to_html(chunk),
                                    "parse_mode": "HTML"}, timeout=30)
    if r.status_code == 400:  # formattazione rifiutata -> ripiego su testo semplice
        r = requests.post(TG_URL, json={**base, "text": chunk}, timeout=30)
    if not r.ok:  # fa fallire il workflow invece di "passare" senza inviare nulla
        sys.exit(f"Invio Telegram fallito ({r.status_code}): {r.text}")


for chunk in split_text(article_text):
    send_chunk(chunk)
    time.sleep(1)  # evita il flood limit di Telegram
