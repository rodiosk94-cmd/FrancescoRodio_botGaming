"""Dati per Radar, la PWA del bot: report corrente, archivio, tracker dei leak, affidabilità degli insider.

Il bot scrive i file in app/data/; il workflow li salva nel repo e pubblica l'app su GitHub Pages.
"""
import hashlib
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import fonti
from fonti import INSIDER, dominio, nome_fonte, norm_link

DIR_DATI = Path(__file__).with_name("app") / "data"
MAX_ARCHIVIO = 60  # report conservati: circa un mese
ROMA = ZoneInfo("Europe/Rome")

CHIAVI = {fonti.UFFICIALE: "ufficiale", fonti.AFFIDABILE: "affidabile", fonti.CONFERMATA: "confermata",
          fonti.RUMOR: "rumor", fonti.FONTE_UNICA: "fonte_unica", fonti.SOLO_COMMUNITY: "community"}


def _pct(stato, chiave):
    return round(100 * stato.affidabilita(chiave))


def _nome(chiave):
    return chiave if chiave in INSIDER or chiave.startswith("@") else nome_fonte(chiave)


def _origini_vere(voci, info):
    """Origini che non sono a loro volta voci del gruppo (Multiplayer cita PC Gamer, che è nel gruppo)."""
    domini = {dominio(v["link"]) for v in voci}
    return [o for o in info["origini"] if not (o["tipo"] == "testata" and o["chiave"] in domini)]


def catena(voci, info):
    """Chi ha dato la notizia per primo e chi l'ha ripresa, in ordine."""
    passi, visti = [], set()

    def aggiungi(nome, ruolo, url, tipo):
        chiavi = {nome.lower()} | ({norm_link(url)} if url else set())
        if chiavi & visti:
            return
        visti.update(chiavi)
        passi.append({"nome": nome, "ruolo": ruolo, "url": url, "tipo": tipo})

    for v in voci:
        if v["area"] == "Ufficiali":
            aggiungi(nome_fonte(v["link"]), "Comunicato ufficiale", v["link"], "origine")
    ruoli = {"ufficiale": "Origine · comunicato ufficiale", "insider": "Origine · insider",
             "testata": "Origine · prima fonte"}
    origini = _origini_vere(voci, info)
    for o in origini:
        aggiungi(_nome(o["chiave"]), ruoli[o["tipo"]], o["url"], "origine")
    for v in voci:
        if v.get("autore"):
            aggiungi(v["autore"], "Post diretto su Bluesky", v["link"], "origine")
    for v in voci:
        if v["area"] in ("Ufficiali", "Community") or v.get("autore"):
            continue
        aggiungi(nome_fonte(v["link"]), "Riprende la notizia" if origini else "Riporta la notizia", v["link"], "ripresa")
    for v in voci:
        if v["area"] == "Community":
            aggiungi(nome_fonte(v["link"]), "Discussione della community", v["link"], "community")
    return passi[:12]


def motivo(etichetta, info, voci, stato):
    """Una frase che spiega perché la notizia ha quell'etichetta."""
    n = len(info["indipendenti"])
    fp = info.get("fonte_principale")
    if etichetta == fonti.UFFICIALE:
        uff = next((nome_fonte(v["link"]) for v in voci if v["area"] == "Ufficiali"), None) or next(
            (_nome(o["chiave"]) for o in info["origini"] if o["tipo"] == "ufficiale"), None)
        return f"C'è un comunicato ufficiale ({uff})." if uff else "C'è un comunicato ufficiale."
    if etichetta == fonti.AFFIDABILE:
        if fp and fp in INSIDER and stato.affidabilita(fp) >= fonti.SOGLIA_AFFIDABILE:
            return f"Leak di {fp}, affidabilità storica {_pct(stato, fp)}%."
        return f"Leak riportato da {n} fonti indipendenti."
    if etichetta == fonti.CONFERMATA:
        return f"Riportata da {n} fonti indipendenti."
    if etichetta == fonti.RUMOR:
        frasi = []
        testate = [v for v in voci if v["area"] != "Community"]
        if n <= 1 and len(testate) > 1:
            frasi.append("Tutte le testate citano la stessa fonte: conta come una sola.")
        if fp:
            c, nc = stato.conteggi(fp)
            frasi.append(f"{_nome(fp)} non ha ancora uno storico nel tracker." if not (c or nc)
                         else f"{_nome(fp)} ha un'affidabilità del {_pct(stato, fp)}%.")
        return " ".join(frasi) or "Indiscrezione non verificata."
    if etichetta == fonti.FONTE_UNICA:
        return "Riportata da una sola testata, senza altre conferme."
    return "Solo discussioni della community, senza una fonte giornalistica."


def _combina(gruppi, meta, stato):
    voci = [v for g in gruppi for v in g["voci"]]
    # l'etichetta è la più alta tra i gruppi uniti, mai ricalcolata verso l'alto
    etichetta = min((g["etichetta"] for g in gruppi), key=fonti.ORDINE.index)
    _, info = fonti.valuta_gruppo(voci, stato)
    prima = voci[0]
    meta = meta or {}
    titolo = meta.get("titolo") or prima["titolo"]
    date = sorted(v["iso"] for v in voci if v.get("iso"))
    origini = _origini_vere(voci, info)
    return {
        "id": hashlib.sha1(norm_link(prima["link"]).encode()).hexdigest()[:8],
        "etichetta": CHIAVI[etichetta],
        "titolo": titolo,
        "riassunto": meta.get("riassunto") or prima["testo"],
        "categoria": meta.get("categoria") or fonti.categoria(titolo + " " + prima["titolo"], prima["area"]),
        "data": date[0] if date else None,
        "fonte": _nome(info["fonte_principale"]) if info.get("fonte_principale") else nome_fonte(prima["link"]),
        "riprese": list(dict.fromkeys(nome_fonte(v["link"]) for v in voci
                                      if v["area"] not in ("Community", "Insider", "Ufficiali")))[:4],
        "indipendenti": len(info["indipendenti"]),
        "catena": catena(voci, info),
        "motivo": motivo(etichetta, info, voci, stato),
        "leak_id": None,
        # campi interni (tolti prima di scrivere i file)
        "_etichetta": etichetta,
        "_fonte_principale": info.get("fonte_principale"),
        "_url_origine": next((o["url"] for o in origini if o["url"]), prima["link"]),
        "_links": [v["link"] for v in voci] + [o["url"] for o in info["origini"] if o["url"]],
    }


def componi(notizie, meta, stato):
    """Notizie per l'app: quelle trattate nel report (meta da Gemini) o, senza meta, un gruppo ciascuna."""
    if not meta:
        return [_combina([n], None, stato) for n in notizie]
    out, usati = [], set()
    for m in meta:
        idx = [k - 1 for k in m["notizie"] if k - 1 not in usati and k - 1 < len(notizie)]
        if not idx:
            continue
        usati.update(idx)
        out.append(_combina([notizie[i] for i in idx], m, stato))
    return out


def _pubblico(e):
    return {k: v for k, v in e.items() if not k.startswith("_")}


def _leggi(path, vuoto):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return vuoto


def _scrivi(path, dati):
    path.write_text(json.dumps(dati, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def _etichetta_report(adesso):
    loc = adesso.astimezone(ROMA)
    if 3 <= loc.hour < 12:
        return "Report delle 06:00"
    if 17 <= loc.hour <= 23:
        return "Report delle 20:00"
    return f"Report delle {loc:%H:%M}"


def leak_app(stato, adesso):
    out = []
    for l in sorted(stato.leak, key=lambda l: (l["data"], l["id"]), reverse=True):
        giorni = (adesso.date() - date.fromisoformat(l["data"])).days + 1
        out.append({
            "id": l["id"], "titolo": l["titolo"], "fonte": _nome(l["fonte"] or ""), "link": l["link"],
            "etichetta": CHIAVI.get(l.get("etichetta"), "rumor"), "data": l["data"], "stato": l["stato"],
            "data_esito": l.get("data_esito"), "giorno": min(giorni, fonti.GIORNI_SCADENZA),
            "scadenza": fonti.GIORNI_SCADENZA,
        })
    return out


def insider_app(stato):
    aperti = {}
    for l in stato.leak_aperti():
        aperti[l["fonte"]] = aperti.get(l["fonte"], 0) + 1
    insider = []
    for nome, d in INSIDER.items():
        c, nc = stato.conteggi(nome)
        canale = "Bluesky" if d["bsky"] else (nome_fonte(d["dominio"]) if d.get("dominio") else "citato negli articoli")
        insider.append({"nome": nome, "canale": canale, "affidabilita": _pct(stato, nome),
                        "confermati": c, "non_confermati": nc, "aperti": aperti.get(nome, 0)})
    insider.sort(key=lambda p: (-p["affidabilita"], p["nome"]))
    altre = set(stato.fonti) | {l["fonte"] for l in stato.leak if l.get("fonte")}
    nuove = []
    for k in sorted(altre - set(INSIDER)):
        c, nc = stato.conteggi(k)
        nuove.append({"nome": _nome(k), "affidabilita": _pct(stato, k), "confermati": c,
                      "non_confermati": nc, "aperti": aperti.get(k, 0)})
    nuove.sort(key=lambda p: (-p["aperti"], -p["affidabilita"], p["nome"]))
    return insider, nuove


def scrivi(voci_app, stato, adesso, ore):
    """Scrive app/data/radar.json (tutto ciò che l'app mostra) e l'archivio dei report."""
    archivio = DIR_DATI / "archivio"
    archivio.mkdir(parents=True, exist_ok=True)
    rid = adesso.astimezone(ROMA).strftime("%Y%m%d-%H%M")
    report = {"id": rid, "generato": adesso.isoformat(timespec="minutes"), "titolo": _etichetta_report(adesso),
              "ore": ore, "notizie": [_pubblico(e) for e in voci_app]}
    _scrivi(archivio / f"{rid}.json", report)

    indice = [r for r in _leggi(archivio / "indice.json", []) if r.get("id") != rid]
    conteggio = {}
    for e in report["notizie"]:
        conteggio[e["etichetta"]] = conteggio.get(e["etichetta"], 0) + 1
    indice.insert(0, {"id": rid, "generato": report["generato"], "titolo": report["titolo"],
                      "conteggio": conteggio,
                      "notizie": [{"id": e["id"], "titolo": e["titolo"], "etichetta": e["etichetta"]}
                                  for e in report["notizie"]]})
    for vecchio in indice[MAX_ARCHIVIO:]:
        (archivio / f"{vecchio['id']}.json").unlink(missing_ok=True)
    indice = indice[:MAX_ARCHIVIO]
    _scrivi(archivio / "indice.json", indice)

    insider, nuove = insider_app(stato)
    _scrivi(DIR_DATI / "radar.json", {
        "aggiornato": report["generato"], "report": report, "leak": leak_app(stato, adesso),
        "insider": insider, "fonti_nuove": nuove,
    })
    return rid
