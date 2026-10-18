'use strict';
/* Radar — legge i dati che il bot scrive in data/ e li mostra. Nessuna dipendenza, nessun build. */
(() => {
  const ETICHETTE = {
    ufficiale: 'UFFICIALE', affidabile: 'LEAK AFFIDABILE', confermata: 'CONFERMATA · PIÙ FONTI',
    rumor: 'RUMOR', fonte_unica: 'FONTE UNICA', community: 'COMMUNITY',
  };
  const PERCHE = {
    ufficiale: 'Perché è ufficiale', affidabile: 'Perché è un leak affidabile', confermata: 'Perché è confermata',
    rumor: 'Perché è un rumor', fonte_unica: 'Perché è fonte unica', community: 'Perché è solo community',
  };
  const FILTRI = [
    ['tutte', 'Tutte', () => true],
    ['ufficiale', 'Ufficiali', (e) => e === 'ufficiale'],
    ['confermate', 'Confermate', (e) => e === 'affidabile' || e === 'confermata'],
    ['rumor', 'Rumor', (e) => e === 'rumor'],
    ['altre', 'Fonte unica', (e) => e === 'fonte_unica' || e === 'community'],
  ];
  const STATI_LEAK = [['aperto', 'Aperti'], ['confermato', 'Confermati'], ['non confermato', 'Scaduti']];
  const MESI = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic'];
  const ID_REPORT = /^\d{8}-\d{4}$/;

  const I = {
    logo: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--accento)" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12l5-5"/><circle cx="7.5" cy="16.5" r="1" fill="var(--accento)" stroke="none"/></svg>',
    cerca: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
    chiudi: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    indietro: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
    condividi: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7 8l5-5 5 5"/><path d="M5 14v5h14v-5"/></svg>',
    link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1"/><path d="M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.5"/></svg>',
    aggiorna: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 3v5h5"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 21v-5h-5"/></svg>',
    stella: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
  };

  const REPO = (() => {
    const seg = location.pathname.split('/').filter(Boolean)[0];
    if (location.hostname.endsWith('.github.io') && seg) return { owner: location.hostname.split('.')[0], nome: seg };
    return { owner: 'rodiosk94-cmd', nome: 'FrancescoRodio_botGaming' };
  })();
  const API = `https://api.github.com/repos/${REPO.owner}/${REPO.nome}`;
  const WORKFLOW = 'schedule.yml';
  const PAGINA_WORKFLOW = `https://github.com/${REPO.owner}/${REPO.nome}/actions/workflows/${WORKFLOW}`;
  const DURATA_MAX = 20 * 60 * 1000; // oltre, l'aggiornamento si considera perso

  const vista = document.getElementById('vista');
  const st = {
    radar: null, caricato: 0, report: {}, indice: null,
    filtro: 'tutte', categoria: null, cerca: '', mostraCerca: false, cercaArchivio: '', tabLeak: 'aperto',
  };

  // ---------- utilità ----------
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const urlSicuro = (u) => (/^https?:\/\//i.test(u || '') ? u : null);
  const memoria = {
    get(k, d) { try { const v = localStorage.getItem('radar.' + k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('radar.' + k, JSON.stringify(v)); } catch (e) { /* storage non disponibile */ } },
  };
  const seguiti = new Set(memoria.get('seguiti', []));
  const agg = memoria.get('aggiornamento', {}); // aggiornamento manuale: sopravvive alla chiusura dell'app
  const salvaAgg = () => memoria.set('aggiornamento', agg);
  const attesa = (ms) => new Promise((ok) => setTimeout(ok, ms));
  function segui(id) {
    if (seguiti.has(id)) seguiti.delete(id); else seguiti.add(id);
    memoria.set('seguiti', [...seguiti]);
  }

  function data(iso, conOra) {
    if (!iso) return '';
    const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
    if (Number.isNaN(d.getTime())) return '';
    const g = `${d.getDate()} ${MESI[d.getMonth()]}`;
    return conOra ? `${g}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` : g;
  }
  function prossimoReport() {
    let h = 12;
    try { h = Number(new Intl.DateTimeFormat('it-IT', { hour: 'numeric', hourCycle: 'h23', timeZone: 'Europe/Rome' }).format(new Date())); } catch (e) { /* fuso non supportato */ }
    return h >= 6 && h < 20 ? '20:00' : '06:00';
  }
  const fonti = (n) => (n === 1 ? '1 fonte indipendente' : `${n} fonti indipendenti`);
  const pill = (e, grande) => `<span class="pill${grande ? ' grande' : ''} e-${esc(e)}">${esc(ETICHETTE[e] || e)}</span>`;
  const iniziali = (nome) => (nome.replace(/^@/, '').match(/[A-Za-z0-9]+/g) || ['?']).slice(0, 2).map((p) => p[0].toUpperCase()).join('');

  // ---------- dati ----------
  async function scarica(url) {
    const r = await fetch(url, { cache: 'no-cache' });
    if (!r.ok) { const e = new Error('HTTP ' + r.status); e.stato = r.status; throw e; }
    return r.json();
  }
  function applica(dati) {
    st.radar = dati;
    st.caricato = Date.now();
    st.report[dati.report.id] = dati.report;
    st.indice = null;
  }
  async function radar(forza) {
    if (!st.radar || forza) applica(await scarica('data/radar.json' + (forza ? `?t=${Date.now()}` : '')));
    return st.radar;
  }

  // ---------- aggiornamento manuale (GitHub Actions) ----------
  async function gh(percorso, opz = {}) {
    const r = await fetch(API + percorso, {
      ...opz,
      cache: 'no-store',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${memoria.get('token', '')}`,
        'X-GitHub-Api-Version': '2022-11-28',
        ...(opz.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
    if (!r.ok) throw Object.assign(new Error('HTTP ' + r.status), { stato: r.status });
    return r.status === 204 ? null : r.json();
  }
  const inCorso = () => ['avvio', 'in_corso', 'pubblicazione'].includes(agg.stato) && Date.now() - agg.avviato < DURATA_MAX;
  function erroreGh(e) {
    if ([401, 403, 404].includes(e.stato)) return 'Il token non è valido o non ha il permesso Actions in lettura e scrittura.';
    if (e.stato === 422) return 'GitHub ha rifiutato l\'avvio: controlla che il workflow abbia l\'opzione telegram.';
    return 'Impossibile contattare GitHub. Controlla la connessione.';
  }

  async function avviaAggiornamento() {
    if (!memoria.get('token', '')) { location.hash = '#/impostazioni'; return; }
    if (inCorso()) return;
    Object.assign(agg, { avviato: Date.now(), stato: 'avvio', run: null, msg: '', finito: null });
    salvaAgg();
    disegna(false);
    try {
      await gh(`/actions/workflows/${WORKFLOW}/dispatches`, {
        method: 'POST',
        body: JSON.stringify({ ref: 'main', inputs: { telegram: memoria.get('telegram', false) ? 'true' : 'false' } }),
      });
      agg.stato = 'in_corso';
    } catch (e) {
      agg.stato = 'errore';
      agg.msg = erroreGh(e);
    }
    salvaAgg();
    disegna(false);
    seguiAggiornamento();
  }

  let inAscolto = false;
  async function seguiAggiornamento() {
    if (inAscolto || !inCorso()) return;
    inAscolto = true;
    try {
      while (inCorso()) {
        await attesa(15000);
        if (agg.stato === 'in_corso') {
          const d = await gh(`/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=5`);
          const run = (d.workflow_runs || []).find((r) => new Date(r.created_at).getTime() >= agg.avviato - 60000);
          if (run) {
            agg.run = run.html_url;
            if (run.status === 'completed') {
              if (run.conclusion === 'success') agg.stato = 'pubblicazione';
              else { agg.stato = 'errore'; agg.msg = 'Il bot si è fermato con un errore.'; }
            }
            salvaAgg();
            disegna(false);
          }
        }
        if (agg.stato === 'pubblicazione') {
          const nuovo = await scarica(`data/radar.json?t=${Date.now()}`);
          if (new Date(nuovo.aggiornato).getTime() >= agg.avviato - 60000) {
            applica(nuovo);
            Object.assign(agg, { stato: 'fatto', finito: Date.now() });
            salvaAgg();
            disegna(false);
          }
        }
      }
      if (['avvio', 'in_corso', 'pubblicazione'].includes(agg.stato)) {
        Object.assign(agg, { stato: 'errore', msg: 'L\'aggiornamento non ha risposto entro 20 minuti.' });
        salvaAgg();
        disegna(false);
      }
    } catch (e) {
      if (e.stato) { Object.assign(agg, { stato: 'errore', msg: erroreGh(e) }); salvaAgg(); disegna(false); }
      // senza rete: si riprende quando l'app torna in primo piano
    } finally {
      inAscolto = false;
    }
  }

  function bannerAggiornamento() {
    const link = agg.run ? ` <a href="${esc(agg.run)}" target="_blank" rel="noopener">Dettagli</a>` : '';
    if (inCorso()) {
      const testo = agg.stato === 'pubblicazione' ? 'Quasi fatto: pubblico le notizie nuove…' : 'Aggiornamento in corso, di solito 3-5 minuti…';
      return `<div class="banner attivo" role="status"><span>${testo}</span>${link}</div>`;
    }
    if (agg.stato === 'fatto' && Date.now() - agg.finito < 3 * 60 * 1000) {
      return '<div class="banner ok" role="status"><span>Notizie aggiornate.</span><button class="link" data-azione="chiudi-agg">Chiudi</button></div>';
    }
    if (agg.stato === 'errore') {
      return `<div class="banner errore" role="alert"><span>${esc(agg.msg)}${link}</span><button class="link" data-azione="chiudi-agg">Chiudi</button></div>`;
    }
    return '';
  }
  async function report(id) {
    const r = await radar();
    if (!id || id === r.report.id) return r.report;
    if (!ID_REPORT.test(id)) throw Object.assign(new Error('report'), { stato: 404 });
    if (!st.report[id]) st.report[id] = await scarica(`data/archivio/${id}.json`);
    return st.report[id];
  }
  async function indice() {
    if (!st.indice) {
      try { st.indice = await scarica('data/archivio/indice.json'); } catch (e) { st.indice = []; }
    }
    return st.indice;
  }

  // ---------- viste ----------
  function testoFonte(n) {
    if (n.etichetta === 'rumor' || n.etichetta === 'affidabile') return `Origine: ${n.fonte} · ${fonti(n.indipendenti)}`;
    const altre = (n.riprese || []).filter((x) => x !== n.fonte);
    return altre.length ? `${n.fonte} · ${altre.slice(0, 3).join(', ')}` : n.fonte;
  }
  function scheda(n, rid, extra) {
    return `<a class="scheda" href="#/notizia/${esc(rid)}/${esc(n.id)}">
      <div class="riga">${pill(n.etichetta)}<span class="meta">${esc(extra || n.categoria || '')}</span></div>
      <h2>${esc(n.titolo)}</h2>
      <div class="fonte">${I.link}<span>${esc(testoFonte(n))}</span></div>
      ${n.leak_id && seguiti.has(n.leak_id) ? '<span class="seguito">LEAK CHE SEGUI</span>' : ''}
    </a>`;
  }

  async function vistaNotizie(rid) {
    const r = await radar();
    const rep = await report(rid);
    const inArchivio = rep.id !== r.report.id;
    const tutte = rep.notizie || [];
    const categorie = [...new Set(tutte.map((n) => n.categoria).filter(Boolean))];
    if (st.categoria && !categorie.includes(st.categoria)) st.categoria = null;
    const filtro = FILTRI.find((f) => f[0] === st.filtro) || FILTRI[0];
    const q = st.cerca.trim().toLowerCase();
    const notizie = tutte.filter((n) => filtro[2](n.etichetta)
      && (!st.categoria || n.categoria === st.categoria)
      && (!q || `${n.titolo} ${n.riassunto} ${n.fonte}`.toLowerCase().includes(q)));
    const nelReport = notizie.filter((n) => n.in_report !== false);
    const altre = notizie.filter((n) => n.in_report === false);

    const chips = FILTRI.map(([k, nome]) => `<button class="chip" data-azione="filtro" data-valore="${k}" aria-pressed="${st.filtro === k}">${nome}</button>`).join('');
    const chipsCat = categorie.length > 1
      ? `<div class="filtri" role="group" aria-label="Filtra per piattaforma">
          <button class="chip piccolo" data-azione="categoria" data-valore="" aria-pressed="${!st.categoria}">Tutte le piattaforme</button>
          ${categorie.map((c) => `<button class="chip piccolo" data-azione="categoria" data-valore="${esc(c)}" aria-pressed="${st.categoria === c}">${esc(c)}</button>`).join('')}
        </div>` : '';
    return `
      <header class="testata">
        <div class="logo">${I.logo}<span>Radar</span></div>
        <div class="azioni">
        <button class="tondo${inCorso() ? ' gira' : ''}" data-azione="aggiorna" aria-label="${inCorso() ? 'Aggiornamento in corso' : 'Aggiorna le notizie'}" aria-disabled="${inCorso()}">${I.aggiorna}</button>
        <button class="tondo" data-azione="cerca" aria-label="${st.mostraCerca ? 'Chiudi la ricerca' : 'Cerca nel report'}" aria-expanded="${st.mostraCerca}">${st.mostraCerca ? I.chiudi : I.cerca}</button>
        </div>
      </header>
      ${st.mostraCerca ? `<div class="cerca"><input id="campo-cerca" type="search" placeholder="Cerca nel report" aria-label="Cerca nel report" value="${esc(st.cerca)}" autocomplete="off"></div>` : ''}
      <div class="sotto"><strong>${esc(rep.titolo)}</strong><span class="meta">${esc(data(rep.generato))} · ${tutte.length} notizie</span></div>
      ${inArchivio ? `<div class="banner"><span>Report in archivio del ${esc(data(rep.generato, true))}</span><a href="#/notizie">Torna all'ultimo</a></div>` : ''}
      <div class="filtri" role="group" aria-label="Filtra per affidabilità">${chips}</div>
      ${chipsCat}
      ${bannerAggiornamento()}
      <div class="lista">
        ${nelReport.length ? nelReport.map((n) => scheda(n, rep.id)).join('') : ''}
        ${altre.length ? `<h2 class="sezione">Altre notizie dalle fonti · ${altre.length}</h2>${altre.map((n) => scheda(n, rep.id)).join('')}` : ''}
        ${notizie.length ? '' : '<p class="vuoto">Nessuna notizia con questi filtri.</p>'}
      </div>
      <p class="piede">Aggiornato il ${esc(data(r.aggiornato, true))}${inArchivio ? '' : ` · prossimo report alle ${prossimoReport()}`}<br><a href="#/impostazioni">Impostazioni aggiornamento</a></p>`;
  }

  async function vistaNotizia(rid, nid) {
    const r = await radar();
    const rep = await report(rid);
    const n = (rep.notizie || []).find((x) => x.id === nid);
    const ritorno = rep.id === r.report.id ? '#/notizie' : `#/report/${rep.id}`;
    if (!n) return `<p class="vuoto">Notizia non trovata.<br><a class="bottone secondario" href="${ritorno}">Torna alle notizie</a></p>`;
    const leak = n.leak_id ? (r.leak || []).find((l) => l.id === n.leak_id) : null;
    const passi = (n.catena || []).map((p) => {
      const u = urlSicuro(p.url);
      return `<li class="${esc(p.tipo)}"><div class="asse"><span class="punto"></span><span class="linea"></span></div>
        <div class="testo">${u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(p.nome)}</a>` : `<strong>${esc(p.nome)}</strong>`}
        <span class="ruolo">${esc(p.ruolo)}</span></div></li>`;
    }).join('');
    const seguito = leak && seguiti.has(leak.id);
    return `
      <div class="barra">
        <a class="tondo nudo" href="${ritorno}" aria-label="Torna alle notizie">${I.indietro}</a>
        <span class="meta">${esc(rep.titolo.replace('Report delle ', 'Report '))} · ${esc(data(rep.generato))}</span>
        <button class="tondo nudo" data-azione="condividi" aria-label="Condividi la notizia">${I.condividi}</button>
      </div>
      <article class="dettaglio">
        <div style="display: flex; flex-direction: column; gap: 10px">
          ${pill(n.etichetta, true)}
          <h1>${esc(n.titolo)}</h1>
          <p class="riassunto">${esc(n.riassunto)}</p>
          <span class="meta">${esc(n.categoria || '')}${n.data ? ' · ' + esc(data(n.data, true)) : ''}</span>
        </div>
        ${passi ? `<section class="blocco"><h2>Catena della fonte</h2><ol class="catena">${passi}</ol></section>` : ''}
        <section class="perche e-${esc(n.etichetta)}">${I.info}<div><strong>${esc(PERCHE[n.etichetta] || 'Affidabilità')}</strong><p>${esc(n.motivo)}</p></div></section>
        ${leak ? `<div class="azione">
          <div><strong>Nel tracker dal ${esc(data(leak.data))}</strong><span>${esc(statoLeak(leak))}</span></div>
          <button class="bottone${seguito ? ' secondario' : ''}" data-azione="segui" data-id="${esc(leak.id)}" aria-pressed="${seguito}">${seguito ? 'Lo segui' : 'Segui il leak'}</button>
        </div>` : ''}
      </article>`;
  }

  function statoLeak(l) {
    if (l.stato === 'confermato') return `Confermato il ${data(l.data_esito)}`;
    if (l.stato === 'non confermato') return `Mai confermato entro ${l.scadenza} giorni`;
    return 'In attesa di conferma ufficiale';
  }

  async function vistaLeak() {
    const r = await radar();
    const leak = r.leak || [];
    const conta = (s) => leak.filter((l) => l.stato === s).length;
    const lista = leak.filter((l) => l.stato === st.tabLeak)
      .sort((a, b) => (seguiti.has(b.id) - seguiti.has(a.id)));
    const vuoti = {
      aperto: 'Nessun leak aperto in questo momento.',
      confermato: 'Nessun leak confermato finora. Un leak si chiude quando un annuncio ufficiale lo conferma.',
      'non confermato': 'Nessun leak scaduto. Un leak scade se non viene confermato entro 180 giorni.',
    };
    const schede = lista.map((l) => {
      const u = urlSicuro(l.link);
      const pressed = seguiti.has(l.id);
      const pct = Math.max(2, Math.round((100 * l.giorno) / l.scadenza));
      const fondo = l.stato === 'aperto'
        ? `<div class="barretta"><i style="width: ${pct}%"></i></div><span class="meta">Giorno ${l.giorno} di ${l.scadenza} · in attesa di conferma</span>`
        : `<span class="meta">${esc(statoLeak(l))}</span>`;
      return `<div class="scheda">
        <div class="riga">${pill(l.etichetta)}
          <div style="display: flex; align-items: center; gap: 6px"><span class="meta">dal ${esc(data(l.data))}</span>
          <button class="stella" data-azione="segui" data-id="${esc(l.id)}" aria-pressed="${pressed}" aria-label="${pressed ? 'Smetti di seguire' : 'Segui'} questo leak">${I.stella}</button></div>
        </div>
        <h3>${esc(l.titolo)}</h3>
        <div class="fonte">${I.link}<span>Fonte: ${u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(l.fonte)}</a>` : esc(l.fonte)}</span></div>
        ${fondo}
      </div>`;
    }).join('');
    return `
      <h1 class="titolo">Tracker leak</h1>
      <p class="intro">Ogni leak resta aperto finché un annuncio ufficiale lo conferma, o per al massimo 180 giorni.</p>
      <div class="numeri">
        <div><b class="c-aperti">${conta('aperto')}</b><span>Aperti</span></div>
        <div><b class="c-confermati">${conta('confermato')}</b><span>Confermati</span></div>
        <div><b class="c-scaduti">${conta('non confermato')}</b><span>Mai confermati</span></div>
      </div>
      <div class="segmenti" role="tablist" aria-label="Stato dei leak">
        ${STATI_LEAK.map(([k, nome]) => `<button role="tab" data-azione="tab-leak" data-valore="${k}" aria-selected="${st.tabLeak === k}">${nome}</button>`).join('')}
      </div>
      <div class="lista" role="tabpanel">${schede || `<p class="vuoto">${vuoti[st.tabLeak]}</p>`}</div>`;
  }

  async function vistaInsider() {
    const r = await radar();
    const riga = (p, conBarra) => {
      const cls = p.affidabilita >= 70 ? '' : p.affidabilita >= 50 ? ' media' : ' bassa';
      const storico = p.confermati || p.non_confermati
        ? `${p.confermati} confermati · ${p.non_confermati} mancati` : 'nessun leak ancora valutato';
      const aperti = p.aperti ? ` · ${p.aperti} ${p.aperti === 1 ? 'aperto' : 'aperti'}` : '';
      return `<div class="persona">
        <span class="iniziali" aria-hidden="true">${esc(iniziali(p.nome))}</span>
        <div class="corpo">
          <div class="riga"><span class="nome">${esc(p.nome)}</span><span class="pct${cls}">${p.affidabilita}%</span></div>
          ${conBarra ? `<div class="barretta sottile verde"><i style="width: ${p.affidabilita}%"></i></div>` : ''}
          <small>${p.canale ? esc(p.canale) + ' · ' : ''}${esc(storico)}${esc(aperti)}</small>
        </div>
      </div>`;
    };
    const nuove = r.fonti_nuove || [];
    return `
      <h1 class="titolo">Insider</h1>
      <p class="intro">L'affidabilità parte dall'80% e sale o scende a ogni leak confermato o mancato. Le fonti nuove partono dal 50%.</p>
      <div class="elenco" style="margin-top: 14px">${(r.insider || []).map((p) => riga(p, true)).join('')}</div>
      ${nuove.length ? `<h2 class="sezione">Fonti nuove</h2><div class="elenco">${nuove.map((p) => riga(p, false)).join('')}</div>` : ''}`;
  }

  async function vistaArchivio() {
    const ind = await indice();
    const q = st.cercaArchivio.trim().toLowerCase();
    let corpo;
    if (q) {
      const trovate = [];
      ind.forEach((rep) => (rep.notizie || []).forEach((n) => {
        if (n.titolo.toLowerCase().includes(q)) trovate.push({ rep, n });
      }));
      corpo = trovate.length
        ? trovate.slice(0, 80).map(({ rep, n }) => `<a class="scheda" href="#/notizia/${esc(rep.id)}/${esc(n.id)}">
            <div class="riga">${pill(n.etichetta)}<span class="meta">${esc(data(rep.generato, true))}</span></div>
            <h2>${esc(n.titolo)}</h2></a>`).join('')
        : '<p class="vuoto">Nessuna notizia trovata nei report in archivio.</p>';
    } else {
      corpo = ind.length ? ind.map((rep) => {
        const c = rep.conteggio || {};
        const pills = Object.keys(ETICHETTE).filter((k) => c[k]).map((k) => `<span class="pill e-${k}">${c[k]} ${esc(ETICHETTE[k].split(' ·')[0])}</span>`).join('');
        return `<a class="scheda report" href="#/report/${esc(rep.id)}">
          <div class="riga"><strong>${esc(rep.titolo)}</strong><span class="meta">${esc(data(rep.generato))}</span></div>
          <div class="conteggi">${pills}</div>
          <ol>${(rep.notizie || []).slice(0, 3).map((n) => `<li>${esc(n.titolo)}</li>`).join('')}</ol>
        </a>`;
      }).join('') : '<p class="vuoto">L\'archivio si riempie a ogni report: qui trovi fino a un mese di notizie.</p>';
    }
    return `
      <h1 class="titolo">Archivio</h1>
      <p class="intro">Gli ultimi report, fino a un mese.</p>
      <div class="cerca" style="margin-top: 14px"><input id="cerca-archivio" type="search" placeholder="Cerca nei report" aria-label="Cerca nei report" value="${esc(st.cercaArchivio)}" autocomplete="off"></div>
      <div class="lista">${corpo}</div>`;
  }

  function vistaImpostazioni() {
    const token = memoria.get('token', '');
    return `
      <div class="barra"><a class="tondo nudo" href="#/notizie" aria-label="Torna alle notizie">${I.indietro}</a><span></span><span style="width: 44px"></span></div>
      <h1 class="titolo">Aggiornamento manuale</h1>
      <p class="intro">Il pulsante Aggiorna avvia il bot su GitHub: in 3-5 minuti l'app mostra le notizie nuove. Serve un token GitHub, che resta salvato solo su questo dispositivo.</p>
      <section class="blocco" style="margin-top: 16px">
        <h2>Come creare il token</h2>
        <ol class="passi">
          <li>Apri <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">la pagina dei token di GitHub</a>.</li>
          <li>In <b>Repository access</b> scegli <b>Only select repositories</b> e seleziona ${esc(REPO.nome)}.</li>
          <li>In <b>Permissions → Repository permissions</b> imposta <b>Actions</b> su <b>Read and write</b>.</li>
          <li>Premi <b>Generate token</b>, copialo e incollalo qui sotto.</li>
        </ol>
      </section>
      <section class="blocco" style="margin-top: 12px">
        <h2>Token</h2>
        ${token ? `<p class="nota">Token salvato: ••••${esc(token.slice(-4))}</p>` : ''}
        <label class="campo" for="campo-token">${token ? 'Sostituisci il token' : 'Incolla il token'}</label>
        <input id="campo-token" class="input" type="password" autocomplete="off" spellcheck="false" placeholder="github_pat_…">
        <div class="riga" style="justify-content: flex-start; gap: 10px; flex-wrap: wrap">
          <button class="bottone" data-azione="salva-token">Salva</button>
          ${token ? '<button class="bottone secondario" data-azione="rimuovi-token">Rimuovi</button>' : ''}
        </div>
        <p class="nota" id="esito-token" role="status">${esc(st.esitoToken || '')}</p>
      </section>
      <section class="blocco" style="margin-top: 12px">
        <label class="spunta"><input type="checkbox" id="campo-telegram" ${memoria.get('telegram', false) ? 'checked' : ''}>
          <span><b>Invia anche il report su Telegram</b><br>Se attivo, l'aggiornamento manda il messaggio e aggiorna lo storico dei leak. Se spento aggiorna solo l'app.</span></label>
      </section>
      <p class="piede"><a href="${esc(PAGINA_WORKFLOW)}" target="_blank" rel="noopener">Avvia il bot a mano da GitHub</a></p>`;
  }

  async function salvaToken() {
    const campo = document.getElementById('campo-token');
    const valore = (campo && campo.value || '').trim();
    if (!valore) { st.esitoToken = 'Incolla prima il token.'; return; }
    const precedente = memoria.get('token', '');
    memoria.set('token', valore);
    st.esitoToken = 'Controllo il token…';
    disegna(false);
    try {
      await gh(`/actions/workflows/${WORKFLOW}`);
      st.esitoToken = 'Token salvato e valido. Ora puoi usare il pulsante Aggiorna.';
    } catch (e) {
      memoria.set('token', precedente);
      st.esitoToken = erroreGh(e);
    }
  }

  // ---------- navigazione ----------
  function rotta() {
    const parti = location.hash.replace(/^#\/?/, '').split('/').map((p) => { try { return decodeURIComponent(p); } catch (e) { return ''; } });
    const [sez, a, b] = parti;
    switch (sez) {
      case 'notizia': return { tab: 'notizie', fn: () => vistaNotizia(a, b) };
      case 'report': return { tab: 'notizie', fn: () => vistaNotizie(a) };
      case 'leak': return { tab: 'leak', fn: vistaLeak };
      case 'insider': return { tab: 'insider', fn: vistaInsider };
      case 'archivio': return { tab: 'archivio', fn: vistaArchivio };
      case 'impostazioni': return { tab: 'notizie', fn: vistaImpostazioni };
      default: return { tab: 'notizie', fn: () => vistaNotizie() };
    }
  }

  let generazione = 0;
  async function disegna(nuovaPagina) {
    const { tab, fn } = rotta();
    document.querySelectorAll('.nav a').forEach((a) => {
      if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    const mia = ++generazione;
    let html;
    try {
      html = await fn();
    } catch (e) {
      html = e.stato === 404
        ? '<p class="vuoto">Nessun report ancora disponibile.<br>Il primo arriva con il report delle 06:00 o delle 20:00.</p>'
        : '<p class="vuoto">Impossibile caricare i dati. Controlla la connessione.</p><p style="text-align: center"><button class="bottone secondario" data-azione="riprova">Riprova</button></p>';
    }
    if (mia !== generazione) return;
    const attivo = document.activeElement && document.activeElement.id;
    const cursore = attivo && document.activeElement.selectionStart;
    vista.innerHTML = html;
    if (nuovaPagina) {
      window.scrollTo(0, 0);
      vista.focus({ preventScroll: true });
    } else if (attivo) {
      const el = document.getElementById(attivo);
      if (el) { el.focus(); if (cursore != null && el.setSelectionRange) el.setSelectionRange(cursore, cursore); }
    }
  }

  vista.addEventListener('click', async (ev) => {
    const el = ev.target.closest('[data-azione]');
    if (!el) return;
    const v = el.dataset.valore;
    switch (el.dataset.azione) {
      case 'filtro': st.filtro = v; break;
      case 'categoria': st.categoria = v || null; break;
      case 'tab-leak': st.tabLeak = v; break;
      case 'segui': segui(el.dataset.id); break;
      case 'cerca':
        st.mostraCerca = !st.mostraCerca;
        if (!st.mostraCerca) st.cerca = '';
        await disegna(false);
        if (st.mostraCerca) { const c = document.getElementById('campo-cerca'); if (c) c.focus(); }
        return;
      case 'riprova': st.radar = null; break;
      case 'aggiorna': avviaAggiornamento(); return;
      case 'chiudi-agg': agg.stato = null; salvaAgg(); break;
      case 'salva-token': await salvaToken(); break;
      case 'rimuovi-token': memoria.set('token', ''); st.esitoToken = 'Token rimosso da questo dispositivo.'; break;
      case 'condividi': {
        const titolo = (vista.querySelector('h1') || {}).textContent || 'Radar';
        try {
          if (navigator.share) await navigator.share({ title: titolo, url: location.href });
          else if (navigator.clipboard) { await navigator.clipboard.writeText(location.href); el.setAttribute('aria-label', 'Link copiato'); }
        } catch (e) { /* condivisione annullata */ }
        return;
      }
      default: return;
    }
    disegna(false);
  });
  vista.addEventListener('change', (ev) => {
    if (ev.target.id === 'campo-telegram') memoria.set('telegram', ev.target.checked);
  });
  vista.addEventListener('input', (ev) => {
    if (ev.target.id === 'campo-cerca') { st.cerca = ev.target.value; disegna(false); }
    if (ev.target.id === 'cerca-archivio') { st.cercaArchivio = ev.target.value; disegna(false); }
  });
  window.addEventListener('hashchange', () => disegna(true));
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState !== 'visible') return;
    seguiAggiornamento();
    if (Date.now() - st.caricato < 10 * 60 * 1000) return;
    try { await radar(true); disegna(false); } catch (e) { /* offline: restano i dati già caricati */ }
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
  }
  disegna(true);
  seguiAggiornamento(); // riprende un aggiornamento avviato prima di chiudere l'app
})();
