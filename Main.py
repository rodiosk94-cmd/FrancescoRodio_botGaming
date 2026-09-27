import os
import requests
from google import genai
from google.genai import types

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

# 2. Chiamata a Gemini con Grounding Google Search
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Esegui il monitoraggio e la redazione degli articoli di approfondimento sulle ultime notizie dal mondo dei videogiochi (PlayStation, Xbox, Nintendo, PC, sviluppatori) e relative reazioni della community delle ultime 12 ore.",
    config=types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[{"google_search": {}}],
    ),
)

article_text = response.text

# 3. Invio a Telegram (gestendo il limite di caratteri per messaggio)
bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Telegram accetta max 4000 caratteri a messaggio, dividiamo se necessario
    for i in range(0, len(text), 4000):
        chunk = text[i:i+4000]
        requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"})

send_telegram_message(article_text)
