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

# 1. Inizializzazione Client Gemini
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

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
    "Reddit": [
        "https://www.reddit.com/r/Games/top/.rss?t=day",
        # più subreddit in UNA sola richiesta: evita il 429 di Reddit
        "https://www.reddit.com/r/gaming+pcgaming+PS5+NintendoSwitch+XboxSeriesX+videogiochi"
        "+GamingLeaksAndRumours/top/.rss?t=day&limit=25",
    ],
    "Ufficiali": [  # comunicati di Sony e Microsoft: fonti primarie
        "https://blog.playstation.com/feed/",
        "https://news.xbox.com/en-us/feed/",
    ],
    "Italia": [
        "https://www.spaziogames.it/feed",
        "https://it.ign.com/feed.xml",
        "https://www.gamesource.it/feed/",
        "https://www.player.it/feed",
    ],
    "Estero": [
        "https://feeds.feedburner.com/ign/news",
        "https://www.gamespot.com/feeds/news/",
        "https://www.eurogamer.net/feed/news",
        "https://www.pcgamer.com/rss/",
        "https://www.polygon.com/rss/index.xml",
        "https://www.videogameschronicle.com/feed/",
        "https://www.rockpapershotgun.com/feed/news",
        "https://kotaku.com/rss",
    ],
}
# Report alle 06:00 (copre la notte, 10 ore) e alle 20:00 (copre la giornata, 14 ore), ora italiana
ora_it = datetime.now(ZoneInfo("Europe/Rome")).hour
ORE = 10 if 3 <= ora_it < 12 else 14
MAX_PER_FEED = 8     # voci massime per feed
MAX_REDDIT = 15      # voci massime per feed Reddit
MAX_ARTICOLI = 25    # articoli da aprire per cercare dichiarazioni
MAX_CIT = 3          # paragrafi con dichiarazioni per articolo
PAUSA_REDDIT = 5     # secondi tra due richieste a Reddit
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


def leggi_feed(url, massimo):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    root = _parse_xml(r.content)
    limite = datetime.now(timezone.utc) - timedelta(hours=ORE)
    voci = []
    for it in (e for e in root.iter() if _local(e.tag) in ("item", "entry")):
        titolo = _child(it, "title")
        link = _child(it, "link")
        data = _child(it, "pubDate", "published", "updated", "date")
        desc = _child(it, "description", "summary", "encoded", "content")
        d = _data(data.text) if data is not None and data.text else None
        if d and d < limite:
            continue
        voci.append({
            "titolo": _pulisci(titolo.text if titolo is not None else "", 200),
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


raccolte = []  # (area, voce)
for area, urls in FEEDS.items():
    for i, url in enumerate(urls):
        is_reddit = "reddit.com" in url
        if is_reddit and i > 0:
            time.sleep(PAUSA_REDDIT)
        try:
            voci = leggi_feed(url, MAX_REDDIT if is_reddit else MAX_PER_FEED)
        except Exception as e:  # un feed rotto non blocca gli altri
            print(f"[KO] {url}: {e}")
            continue
        print(f"[OK] {url}: {len(voci)} voci")
        raccolte.extend((area, v) for v in voci)

# 2b. Dichiarazioni: apre gli articoli (in parallelo) e ne estrae i paragrafi con citazioni dirette
da_aprire = [v for area, v in raccolte if area != "Reddit" and v["link"].startswith("http")][:MAX_ARTICOLI]
with ThreadPoolExecutor(max_workers=8) as pool:
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
    "Nella sitografia usa esattamente i link indicati alla voce 'Fonte'. "
    "Incrocia le voci [Italia] ed [Estero] per il fact-checking; usa le voci [Reddit] "
    "per la sezione sulle reazioni della community e per i RUMOR/LEAK. "
    "Le voci [Ufficiali] sono comunicati ufficiali: trattale come fonte primaria.\n"
    "DICHIARAZIONI: le righe 'Citazioni dall'articolo' sono paragrafi originali che contengono "
    "dichiarazioni dirette. Nella trattazione di ogni notizia riporta tra virgolette le dichiarazioni "
    "rilevanti di sviluppatori, director, publisher o dirigenti, indicando chi parla e il suo ruolo "
    "SOLO se risultano dal testo. Usa esclusivamente frasi presenti in quelle righe: non inventare, "
    "non completare e non attribuire frasi a persone diverse. Citazioni brevi (massimo 2-3 frasi); "
    "se sono in inglese traducile in italiano indicando '(trad.)'. Se una notizia non ha citazioni, "
    "non aggiungerne.\n\n"
    + "\n\n---\n\n".join(blocchi)
)

# 3. Chiamata a Gemini: scorre i modelli a giri (503 = sovraccarico temporaneo)
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
