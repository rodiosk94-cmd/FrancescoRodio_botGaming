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
    stella: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
  };

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
  async function radar(forza) {
    if (!st.radar || forza) {
      st.radar = await scarica('data/radar.json');
      st.caricato = Date.now();
      st.report[st.radar.report.id] = st.radar.report;
      st.indice = null;
    }
    return st.radar;
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

    const chips = FILTRI.map(([k, nome]) => `<button class="chip" data-azione="filtro" data-valore="${k}" aria-pressed="${st.filtro === k}">${nome}</button>`).join('');
    const chipsCat = categorie.length > 1
      ? `<div class="filtri" role="group" aria-label="Filtra per piattaforma">
          <button class="chip piccolo" data-azione="categoria" data-valore="" aria-pressed="${!st.categoria}">Tutte le piattaforme</button>
          ${categorie.map((c) => `<button class="chip piccolo" data-azione="categoria" data-valore="${esc(c)}" aria-pressed="${st.categoria === c}">${esc(c)}</button>`).join('')}
        </div>` : '';
    return `
      <header class="testata">
        <div class="logo">${I.logo}<span>Radar</span></div>
        <button class="tondo" data-azione="cerca" aria-label="${st.mostraCerca ? 'Chiudi la ricerca' : 'Cerca nel report'}" aria-expanded="${st.mostraCerca}">${st.mostraCerca ? I.chiudi : I.cerca}</button>
      </header>
      ${st.mostraCerca ? `<div class="cerca"><input id="campo-cerca" type="search" placeholder="Cerca nel report" aria-label="Cerca nel report" value="${esc(st.cerca)}" autocomplete="off"></div>` : ''}
      <div class="sotto"><strong>${esc(rep.titolo)}</strong><span class="meta">${esc(data(rep.generato))} · ${tutte.length} notizie</span></div>
      ${inArchivio ? `<div class="banner"><span>Report in archivio del ${esc(data(rep.generato, true))}</span><a href="#/notizie">Torna all'ultimo</a></div>` : ''}
      <div class="filtri" role="group" aria-label="Filtra per affidabilità">${chips}</div>
      ${chipsCat}
      <div class="lista">
        ${notizie.length ? notizie.map((n) => scheda(n, rep.id)).join('') : '<p class="vuoto">Nessuna notizia con questi filtri.</p>'}
      </div>
      <p class="piede">Aggiornato il ${esc(data(r.aggiornato, true))}${inArchivio ? '' : ` · prossimo report alle ${prossimoReport()}`}</p>`;
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
  vista.addEventListener('input', (ev) => {
    if (ev.target.id === 'campo-cerca') { st.cerca = ev.target.value; disegna(false); }
    if (ev.target.id === 'cerca-archivio') { st.cercaArchivio = ev.target.value; disegna(false); }
  });
  window.addEventListener('hashchange', () => disegna(true));
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState !== 'visible' || Date.now() - st.caricato < 10 * 60 * 1000) return;
    try { await radar(true); disegna(false); } catch (e) { /* offline: restano i dati già caricati */ }
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
  }
  disegna(true);
})();
