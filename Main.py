import html
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

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
    "Italia": [
        "https://www.spaziogames.it/feed",
        "https://it.ign.com/feed.xml",
        "https://www.eurogamer.it/feed",
        "https://www.gamesource.it/feed/",
        "https://www.player.it/feed",
        "https://www.nintendoon.it/feed/",
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
ORE = 24             # finestra temporale delle notizie
MAX_PER_FEED = 8     # voci massime per feed
MAX_REDDIT = 15      # voci massime per feed Reddit
PAUSA_REDDIT = 5     # secondi tra due richieste a Reddit
UA = {"User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0; +https://github.com/rodiosk94-cmd)"}
MODELLI = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]  # gratuiti sul free tier


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


blocchi = []
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
        for v in voci:
            blocchi.append(f"[{area}] {v['titolo']}\nFonte: {v['link']}\nData: {v['data']}\n{v['testo']}")

if not blocchi:
    sys.exit("Nessuna notizia raccolta dai feed: controlla le righe [KO] nel log.")

prompt = (
    f"Qui sotto trovi notizie e discussioni delle ultime {ORE} ore raccolte da feed RSS. "
    "Scrivi il report usando SOLO queste fonti: non aggiungere fatti, date o link che non compaiono qui. "
    "Nella sitografia usa esattamente i link indicati alla voce 'Fonte'. "
    "Incrocia le voci [Italia] ed [Estero] per il fact-checking; usa le voci [Reddit] "
    "per la sezione sulle reazioni della community e per i RUMOR/LEAK.\n\n"
    + "\n\n---\n\n".join(blocchi)
)

# 3. Chiamata a Gemini con retry e modelli di riserva (503 = sovraccarico temporaneo)
response = None
for modello in MODELLI:
    for tentativo in range(3):
        try:
            response = client.models.generate_content(
                model=modello,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
            print(f"Report generato con {modello}")
            break
        except errors.APIError as e:
            if e.code not in (429, 500, 503):  # errori non temporanei: stop subito
                raise
            attesa = 30 * (tentativo + 1)
            print(f"[{modello}] errore {e.code}, nuovo tentativo tra {attesa}s")
            time.sleep(attesa)
    if response is not None:
        break

if response is None:
    sys.exit("Tutti i modelli sono sovraccarichi: riprova più tardi.")

article_text = response.text
if not article_text:
    sys.exit("Gemini ha restituito una risposta vuota: niente da inviare.")

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
