/*
 * Radar de Pautas: "Transparência & Cruzamento" com cara de app no celular (até 768px).
 *
 * Liga só quando a tela tem até 768px e a aba Transparência está aberta; no desktop
 * nada muda e o painel original continua sendo usado. Reaproveita as mesmas rotas
 * (/api/transparencia/resumo, /cruzamento, /dispensas), os mesmos rótulos vindos do
 * servidor e as funções do painel (pncpFetch, formatBRL, setTranspYear,
 * triggerTransparenciaSync, openPncpModal, radarAutoAtualizar). Não calcula nem
 * inventa números: só reorganiza o que o servidor devolve.
 *
 * Offline: guarda no aparelho a última resposta de cada consulta e, sem conexão,
 * mostra esses dados com o aviso "dados de [data/hora]".
 */
(function () {
  'use strict';

  var MQ = window.matchMedia ? window.matchMedia('(max-width: 768px)') : { matches: false };
  var CACHE_PREFIX = 'radar_m_transp:';
  var THEME_KEY = 'radar_m_tema';
  var PAGE = 30;          // itens por requisição nas listas paginadas no servidor
  var FIRST = 10;         // itens mostrados antes de "Ver todos"
  var ORG_STEP = 20;      // itens a mais por rolagem na lista de órgãos

  var TABS = [
    { k: 'geral', lbl: 'Visão geral', ic: 'dashboard' },
    { k: 'receitas', lbl: 'Receitas', ic: 'payments' },
    { k: 'orgaos', lbl: 'Órgãos', ic: 'account_balance' },
    { k: 'contratos', lbl: 'Contratos', ic: 'contract' },
    { k: 'pendencias', lbl: 'Pendências', ic: 'pending_actions' }
  ];
  var YEARS = [{ v: '2026', lbl: '2026' }, { v: '2025', lbl: '2025' }, { v: '', lbl: '2025 + 2026' }];

  // Rótulos dos filtros de sinal: os mesmos do painel desktop.
  var ALERTAS = [
    ['TODOS', 'Todos'], ['ALERTAS', 'Somente alertas'], ['PAGO_ACIMA_PNCP', 'Pago > PNCP'],
    ['PAGO_SEM_PNCP', 'Pago >= R$100k sem contrato localizado no PNCP'], ['DIVERGENCIA_VALOR', 'Divergência valor'],
    ['CONCILIADO', 'Conciliados'], ['FOLHA_OU_REPASSE_PUBLICO', 'Folha / Repasses'],
    ['PAGO_CONTRATO_ANTERIOR', 'Contrato de ano anterior']
  ];
  var ORDENS_CRUZ = [
    ['pago_desc', 'Maior pago'], ['divergencia_desc', 'Maior divergência'],
    ['pncp_desc', 'Maior PNCP'], ['fiorilli_desc', 'Maior Fiorilli']
  ];
  var ORDENS_ORG = [
    ['pago_desc', 'Maior valor pago'], ['dotacao_desc', 'Maior dotação'],
    ['pct_desc', 'Maior % executado'], ['pct_asc', 'Menor % executado'], ['nome_asc', 'Nome (A–Z)']
  ];
  var STATUS_DISP = [
    ['', 'Todos os status PNCP'], ['AUSENTE_PNCP', 'Não localizados no PNCP'],
    ['FORNECEDOR_NO_PNCP', 'Fornecedor no PNCP (outro proc.)'], ['CONCILIADO', 'Conciliadas no PNCP']
  ];
  var TIPOS_DISP = [['', 'Dispensa + Inexig.'], ['Dispensa', 'Somente dispensas'], ['Inexigibilidade', 'Somente inexigibilidades']];

  function novaLista(extra) {
    return Object.assign({ items: [], page: 0, totalPages: 1, total: 0, loading: false, error: '', all: false, data: null, from: 0, q: '' }, extra || {});
  }

  var st = {
    built: false,
    active: false,
    tab: 'geral',
    seg: 'fornecedores',
    resumo: null,
    resumoFrom: 0,       // 0 = ao vivo; senão, timestamp do cache usado
    resumoErr: '',
    resumoLoading: false,
    org: { q: '', sort: 'pago_desc', shown: FIRST, all: false, open: {} },
    cruz: novaLista({ ordem: 'pago_desc', alerta: 'TODOS' }),
    dir: novaLista({ tipo: '', status: '', entidade: '', rem: false }),
    pend: novaLista({ tipo: '', entidade: '', rem: false }),
    loadedTabs: {},
    seq: 0
  };

  // ----------------------------------------------------------------- utilidades
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    if (typeof window.escapeHtml === 'function') { return window.escapeHtml(s); }
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c];
    });
  }
  function brl(v) {
    if (typeof window.formatBRL === 'function') { return window.formatBRL(v); }
    return Number(v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  }
  // Valor abreviado para listas (R$ 2,78 bi). O exato fica no detalhe.
  function abbr(v) {
    var n = Number(v || 0);
    var a = Math.abs(n);
    var s = n < 0 ? '−' : '';
    function f(x, d) { return x.toFixed(d).replace('.', ','); }
    if (a >= 1e9) { return s + 'R$ ' + f(a / 1e9, 2) + ' bi'; }
    if (a >= 1e6) { return s + 'R$ ' + f(a / 1e6, 1) + ' mi'; }
    if (a >= 1e3) { return s + 'R$ ' + f(a / 1e3, 1) + ' mil'; }
    return brl(n);
  }
  function pct(v) {
    var n = Number(v || 0);
    return n.toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + '%';
  }
  function num(v) { return Number(v || 0).toLocaleString('pt-BR'); }
  function barW(p) { return Math.max(0, Math.min(100, Number(p || 0))); }
  function dataHora(v) {
    if (!v) { return ''; }
    var d = typeof v === 'number' ? new Date(v) : new Date(String(v).replace(' ', 'T'));
    if (isNaN(d.getTime())) { return String(v).replace('T', ' ').slice(0, 16); }
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function dataCurta(v) {
    if (!v) { return '-'; }
    var m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? m[3] + '/' + m[2] + '/' + m[1] : String(v);
  }
  function ic(name, extra) { return '<span class="material-symbols-outlined' + (extra ? ' ' + extra : '') + '" aria-hidden="true">' + name + '</span>'; }
  function yearLabel() {
    var y = typeof currentTranspYear !== 'undefined' ? currentTranspYear : '2026';
    return y ? 'Exercício ' + y : 'Exercícios 2025 + 2026';
  }
  function curYear() { try { return currentTranspYear; } catch (e) { return '2026'; } }

  // ------------------------------------------------- selos de situação (cor + texto)
  var SEV = {
    ok: { cls: 'ok', ic: 'check_circle', txt: 'OK' },
    rev: { cls: 'rev', ic: 'rate_review', txt: 'Revisão' },
    att: { cls: 'att', ic: 'error', txt: 'Atenção' },
    inc: { cls: 'inc', ic: 'help', txt: 'Inconclusivo' },
    neu: { cls: 'neu', ic: 'info', txt: 'Informativo' }
  };
  function badge(sevKey, label) {
    var s = SEV[sevKey] || SEV.neu;
    return '<span class="mt-badge ' + s.cls + '">' + ic(s.ic) + '<span>' + esc(label || s.txt) + '</span></span>';
  }
  function sevCruz(r) {
    var c = r.alerta_codigo;
    if (c === 'CONCILIADO') { return 'ok'; }
    if (r.alerta_conclusivo === false) { return 'inc'; }
    if (c === 'PAGO_ACIMA_PNCP') { return 'att'; }
    if (c === 'PAGO_SEM_PNCP' || c === 'DIVERGENCIA_VALOR' || c === 'FIORILLI_SEM_PNCP') { return 'rev'; }
    return 'neu';
  }
  // "Não localizado no PNCP" não indica problema: com consulta inconclusiva fica cinza.
  function sevDisp(r, conclusiva) {
    if (r.status_pncp === 'CONCILIADO') { return 'ok'; }
    if (r.status_pncp === 'FORNECEDOR_NO_PNCP') { return 'rev'; }
    if (r.status_pncp === 'AUSENTE_PNCP') { return conclusiva ? 'rev' : 'inc'; }
    return 'neu';
  }
  function sevPonto(p) {
    if (p.situacao_vinculo === 'revisao_manual_necessaria') { return 'rev'; }
    if (p.conclusivo === false) { return 'inc'; }
    if (p.severidade === 'ALTA') { return 'att'; }
    if (p.severidade === 'MEDIA' || p.severidade === 'VERIFICAR') { return 'rev'; }
    return 'neu';
  }

  // ------------------------------------------------------- busca com cache offline
  function cacheKey(url) { return CACHE_PREFIX + url; }
  function salvarCache(url, data) {
    try { localStorage.setItem(cacheKey(url), JSON.stringify({ t: Date.now(), d: data })); }
    catch (e) { limparCacheAntigo(); }
  }
  function lerCache(url) {
    try { var raw = localStorage.getItem(cacheKey(url)); return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }
  function limparCacheAntigo() {
    try {
      var chaves = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(CACHE_PREFIX) === 0) { chaves.push(k); }
      }
      chaves.forEach(function (k) { if (k.indexOf('/resumo') < 0) { localStorage.removeItem(k); } });
    } catch (e) { /* sem armazenamento: segue sem cache */ }
  }

  // Devolve { data, from } (from = 0 ao vivo, ou timestamp do cache). Só guarda o que é ok.
  async function buscar(url, guardar) {
    var fetcher = typeof window.pncpFetch === 'function' ? window.pncpFetch : function (u) { return fetch(u, { cache: 'no-store' }); };
    try {
      var res = await fetcher(url);
      if (!res.ok) {
        var err = new Error('HTTP ' + res.status);
        err.status = res.status;
        throw err;
      }
      var data = await res.json();
      if (guardar) { salvarCache(url, data); }
      return { data: data, from: 0 };
    } catch (e) {
      var c = guardar ? lerCache(url) : null;
      if (c && c.d) { return { data: c.d, from: c.t }; }
      throw e;
    }
  }
  function msgErro(e) {
    if (e && e.status === 401) { return 'Acesso recusado pelo servidor (token).'; }
    if (e && e.status) { return 'O servidor respondeu com erro (' + e.status + ').'; }
    return navigator.onLine === false ? 'Sem conexão com a internet.' : 'Não foi possível falar com o servidor.';
  }

  // ------------------------------------------------------------------- estrutura
  function build() {
    if (st.built) { return; }
    st.built = true;
    var app = document.createElement('div');
    app.id = 'mt-app';
    app.innerHTML =
      '<header class="mt-top">' +
        '<div class="mt-top-row">' +
          '<button type="button" class="mt-ib" id="mt-more" aria-label="Mais opções e outras seções" aria-haspopup="dialog">' + ic('menu') + '</button>' +
          '<div class="mt-title"><strong>Transparência</strong><span id="mt-sub" class="mt-num">Carregando…</span></div>' +
          '<button type="button" class="mt-ib" id="mt-search-btn" aria-label="Buscar" aria-expanded="false" aria-controls="mt-search">' + ic('search') + '</button>' +
          '<button type="button" class="mt-ib" id="mt-bell" aria-label="Alertas" aria-haspopup="dialog">' + ic('notifications') + '<span class="mt-dot" id="mt-bell-dot" hidden></span></button>' +
        '</div>' +
        '<div class="mt-search" id="mt-search" hidden role="search">' +
          '<label class="mt-sr" for="mt-search-input">Buscar</label>' +
          '<input id="mt-search-input" type="search" enterkeyhint="search" autocomplete="off" placeholder="Buscar fornecedor, CNPJ/CPF…"/>' +
          '<button type="button" class="mt-ib" id="mt-search-close" aria-label="Fechar busca">' + ic('close') + '</button>' +
        '</div>' +
      '</header>' +
      '<div class="mt-years" role="group" aria-label="Exercício" id="mt-years"></div>' +
      '<div class="mt-ptr" id="mt-ptr" aria-hidden="true">' + ic('refresh') + '</div>' +
      '<main class="mt-main" id="mt-main">' +
        '<div id="mt-offline" class="mt-banner" role="status" hidden></div>' +
        TABS.map(function (t) {
          return '<section class="mt-panel" id="mt-p-' + t.k + '" role="tabpanel" aria-labelledby="mt-t-' + t.k + '" tabindex="-1"' + (t.k === 'geral' ? '' : ' hidden') + '></section>';
        }).join('') +
      '</main>' +
      '<nav class="mt-nav" role="tablist" aria-label="Seções da Transparência">' +
        TABS.map(function (t) {
          return '<button type="button" class="mt-tab" role="tab" id="mt-t-' + t.k + '" data-tab="' + t.k + '" aria-controls="mt-p-' + t.k + '" aria-selected="' + (t.k === 'geral') + '" tabindex="' + (t.k === 'geral' ? '0' : '-1') + '">' +
            '<span class="mt-pill">' + ic(t.ic) + '</span><span>' + t.lbl + '</span>' +
            (t.k === 'pendencias' ? '<span class="mt-count mt-num" id="mt-pend-count" hidden></span>' : '') +
          '</button>';
        }).join('') +
      '</nav>';
    document.body.appendChild(app);

    var toast = document.createElement('div');
    toast.id = 'mt-toast';
    toast.className = 'mt-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    document.body.appendChild(toast);

    renderYears();
    aplicarTema();

    $('mt-more').addEventListener('click', abrirMais);
    $('mt-bell').addEventListener('click', abrirAlertas);
    $('mt-search-btn').addEventListener('click', function () { alternarBusca(true); });
    $('mt-search-close').addEventListener('click', function () { alternarBusca(false); });
    $('mt-search-input').addEventListener('input', aoBuscar);
    $('mt-search-input').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.target.blur(); } });
    app.querySelector('.mt-nav').addEventListener('click', function (e) {
      var b = e.target.closest('.mt-tab');
      if (b) { irPara(b.dataset.tab); }
    });
    app.querySelector('.mt-nav').addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') { return; }
      var i = TABS.findIndex(function (t) { return t.k === st.tab; });
      i = (i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length;
      irPara(TABS[i].k);
      $('mt-t-' + TABS[i].k).focus();
    });
    $('mt-main').addEventListener('click', aoTocarConteudo);
    ligarPullToRefresh();
    espelharSino();
  }

  function renderYears() {
    var box = $('mt-years');
    if (!box) { return; }
    var y = curYear();
    box.innerHTML = YEARS.map(function (o) {
      return '<button type="button" class="mt-chip mt-num" data-year="' + o.v + '" aria-pressed="' + (o.v === y) + '">' + o.lbl + '</button>';
    }).join('');
    box.onclick = function (e) {
      var b = e.target.closest('.mt-chip');
      if (!b || b.getAttribute('aria-pressed') === 'true') { return; }
      // Usa a mesma função do painel: mantém exportação e desktop no mesmo exercício.
      if (typeof window.setTranspYear === 'function') { window.setTranspYear(b.dataset.year); }
      renderYears();
    };
  }

  function irPara(tab) {
    if (!TABS.some(function (t) { return t.k === tab; })) { return; }
    st.tab = tab;
    TABS.forEach(function (t) {
      var b = $('mt-t-' + t.k);
      var p = $('mt-p-' + t.k);
      var on = t.k === tab;
      if (b) { b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; }
      if (p) { p.hidden = !on; }
    });
    var input = $('mt-search-input');
    if (input) {
      input.placeholder = tab === 'orgaos' ? 'Buscar órgão ou fundo…' : (tab === 'pendencias' || (tab === 'contratos' && st.seg === 'diretas'))
        ? 'Objeto, fornecedor, processo…' : 'Buscar fornecedor, CNPJ/CPF ou cidade…';
      input.value = valorBusca();
    }
    window.scrollTo(0, 0);
    garantirAba(tab);
  }

  function valorBusca() {
    if (st.tab === 'orgaos') { return st.org.q; }
    if (st.tab === 'pendencias') { return st.pend.q; }
    if (st.tab === 'contratos') { return st.seg === 'diretas' ? st.dir.q : st.cruz.q; }
    return '';
  }

  // --------------------------------------------------------------- carregamento
  function garantirAba(tab) {
    renderAba(tab);
    if (tab === 'contratos') {
      var l = st.seg === 'diretas' ? st.dir : st.cruz;
      if (!l.page && !l.loading) { carregarLista(st.seg === 'diretas' ? 'dir' : 'cruz', true); }
    }
    if (tab === 'pendencias' && !st.pend.page && !st.pend.loading) { carregarLista('pend', true); }
  }

  async function carregarTudo() {
    build();
    var seq = ++st.seq;
    st.resumoLoading = true;
    st.resumoErr = '';
    st.cruz = Object.assign(st.cruz, { items: [], page: 0, totalPages: 1, error: '', all: false });
    st.dir = Object.assign(st.dir, { items: [], page: 0, totalPages: 1, error: '', all: false });
    st.pend = Object.assign(st.pend, { items: [], page: 0, totalPages: 1, error: '', all: false });
    st.org.shown = FIRST; st.org.all = false;
    if (!st.resumo) { renderAba(st.tab); }
    var p = new URLSearchParams();
    if (curYear()) { p.set('ano', curYear()); }
    try {
      var r = await buscar('/api/transparencia/resumo?' + p.toString(), true);
      if (seq !== st.seq) { return; }
      st.resumo = r.data;
      st.resumoFrom = r.from;
    } catch (e) {
      if (seq !== st.seq) { return; }
      st.resumoErr = msgErro(e);
      st.resumo = null;
    }
    st.resumoLoading = false;
    renderCabecalho();
    TABS.forEach(function (t) { if (t.k !== 'contratos' && t.k !== 'pendencias') { renderAba(t.k); } });
    garantirAba(st.tab);
  }

  function paramsLista(kind, page) {
    var p = new URLSearchParams();
    if (curYear()) { p.set('ano', curYear()); }
    var l = st[kind];
    if (kind === 'cruz') {
      p.set('alerta', l.alerta || 'TODOS');
      p.set('ordem', l.ordem || 'pago_desc');
    } else {
      if (l.tipo) { p.set('tipo', l.tipo); }
      var status = kind === 'pend' ? 'AUSENTE_PNCP' : l.status;
      if (status) { p.set('status_pncp', status); }
      if (l.entidade) { p.set('entidade', l.entidade); }
      if (l.rem) { p.set('remanescentes', '1'); }
    }
    if (l.q) { p.set('q', l.q); }
    p.set('pagina', String(page));
    p.set('limite', String(PAGE));
    return p;
  }

  async function carregarLista(kind, reset) {
    var l = st[kind];
    if (l.loading) { return; }
    if (reset) { l.items = []; l.page = 0; l.totalPages = 1; l.error = ''; }
    if (l.page >= l.totalPages && l.page > 0) { return; }
    var page = l.page + 1;
    var rota = kind === 'cruz' ? '/api/transparencia/cruzamento?' : '/api/transparencia/dispensas?';
    l.loading = true;
    renderLista(kind);
    var token = ++l.reqId || (l.reqId = 1);
    try {
      // Só a 1ª página de cada filtro vai para o cache offline.
      var r = await buscar(rota + paramsLista(kind, page).toString(), page === 1);
      if (token !== l.reqId) { return; }
      var d = r.data || {};
      l.data = d;
      l.from = page === 1 ? r.from : l.from;
      l.items = l.items.concat(d.items || []);
      l.page = Number(d.pagina || page);
      l.totalPages = Number(d.total_paginas || 1);
      l.total = Number(d.total || 0);
      l.error = '';
    } catch (e) {
      if (token !== l.reqId) { return; }
      l.error = msgErro(e);
    }
    l.loading = false;
    renderLista(kind);
    renderOffline();
  }

  // ------------------------------------------------------------------ cabeçalho
  function renderCabecalho() {
    var d = st.resumo || {};
    var meta = d.meta || {};
    var sub = $('mt-sub');
    if (sub) {
      sub.textContent = meta.ultima_atualizacao
        ? 'Fiorilli: ' + dataHora(meta.ultima_atualizacao) + ' • ' + (curYear() || '25+26')
        : (st.resumoErr ? 'Sem dados' : yearLabel());
    }
    var aus = (((d.dispensas_resumo || {}).por_status_pncp || {}).AUSENTE_PNCP || {}).qtd || 0;
    var cnt = $('mt-pend-count');
    if (cnt) {
      cnt.hidden = !aus;
      cnt.textContent = aus > 99 ? '99+' : String(aus);
      var tab = $('mt-t-pendencias');
      if (tab) { tab.setAttribute('aria-label', 'Pendências' + (aus ? ', ' + aus + ' não localizadas no PNCP' : '')); }
    }
    renderYears();
    renderOffline();
  }

  function renderOffline() {
    var el = $('mt-offline');
    if (!el) { return; }
    var t = st.resumoFrom;
    if (st.tab === 'contratos') { t = (st.seg === 'diretas' ? st.dir.from : st.cruz.from) || t; }
    if (st.tab === 'pendencias') { t = st.pend.from || t; }
    if (!t) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = ic('cloud_off') + '<div><strong>Sem conexão com o servidor.</strong> Mostrando dados de ' +
      esc(dataHora(t)) + ' (último carregamento neste aparelho). Puxe para baixo para tentar de novo.</div>';
  }

  // --------------------------------------------------------------------- render
  function skeleton(n, h) {
    var out = '';
    for (var i = 0; i < n; i++) {
      out += '<div class="mt-card" aria-hidden="true"><div class="mt-skel" style="height:14px;width:40%"></div>' +
        '<div class="mt-skel" style="height:' + (h || 26) + 'px;width:70%"></div><div class="mt-skel" style="height:8px;width:100%"></div></div>';
    }
    return '<div role="status" aria-label="Carregando" style="display:flex;flex-direction:column;gap:12px">' + out + '</div>';
  }
  function estado(icone, titulo, texto, botao) {
    return '<div class="mt-card mt-state">' + ic(icone) + '<strong>' + esc(titulo) + '</strong>' +
      (texto ? '<p>' + esc(texto) + '</p>' : '') + (botao || '') + '</div>';
  }
  function erroResumo() {
    return estado('wifi_off', 'Não foi possível carregar', st.resumoErr,
      '<button type="button" class="mt-btn primary" data-act="retry">' + ic('refresh') + 'Tentar de novo</button>');
  }
  function naoSincronizado() {
    var meta = (st.resumo || {}).meta || {};
    if (meta.sincronizado !== false) { return ''; }
    return '<div class="mt-banner rev">' + ic('info') + '<div><strong>Base do Portal Fiorilli ainda não sincronizada neste banco.</strong>' +
      '<div style="margin-top:8px"><button type="button" class="mt-btn" data-act="sync-fiorilli">' + ic('sync') + 'Sincronizar agora</button></div></div></div>';
  }

  function renderAba(tab) {
    var p = $('mt-p-' + tab);
    if (!p) { return; }
    if (tab === 'contratos') { renderContratos(); return; }
    if (tab === 'pendencias') { renderPendencias(); return; }
    if (!st.resumo) {
      p.innerHTML = st.resumoLoading || !st.resumoErr ? skeleton(tab === 'geral' ? 2 : 4) : erroResumo();
      return;
    }
    if (tab === 'geral') { p.innerHTML = htmlGeral(); ligarCarrossel(); }
    if (tab === 'receitas') { p.innerHTML = htmlReceitas(); }
    if (tab === 'orgaos') { renderOrgaos(); }
  }

  // ---- Visão geral
  function kpis() {
    var d = st.resumo || {};
    var rec = d.receitas || {};
    var desp = d.despesas || {};
    var cmp = d.contratos_comparativo || {};
    var disp = d.dispensas_resumo || {};
    var aus = (disp.por_status_pncp && disp.por_status_pncp.AUSENTE_PNCP) || { qtd: 0, valor: 0 };
    return [
      {
        id: 'receita', lbl: 'Receita arrecadada líquida', val: rec.arrecadado_liquido, cor: 'var(--mt-ok-ink)',
        bar: rec.previsao_atualizada_liquida ? rec.pct_realizado : null,
        sub: pct(rec.pct_realizado) + ' da previsão (' + abbr(rec.previsao_atualizada_liquida) + ')'
      },
      {
        id: 'pago', lbl: 'Despesa paga (Fiorilli)', val: desp.pago,
        bar: desp.dotacao_atualizada ? desp.pct_execucao_pago : null, barCls: 'is-blue',
        sub: pct(desp.pct_execucao_pago) + ' da dotação • ' + pct(cmp.pct_pago_sobre_receita) + ' da receita'
      },
      {
        id: 'contratos', lbl: 'Contratado no PNCP', val: cmp.pncp_valor_total, cor: 'var(--mt-accent)', bar: null,
        sub: 'Fiorilli: ' + abbr(cmp.fiorilli_valor_total) + ' • ' + num(cmp.pncp_qtd) + ' × ' + num(cmp.fiorilli_qtd) + ' contratos'
      },
      {
        id: 'dispensas', lbl: 'Dispensas & inexigibilidades', val: disp.diretas_valor, bar: null,
        sub: num(disp.diretas_qtd) + ' diretas • ' + num(aus.qtd) + ' não localizadas no PNCP' + (disp.consulta_conclusiva === false ? ' (consulta inconclusiva)' : '')
      }
    ];
  }

  function htmlGeral() {
    var d = st.resumo;
    var cards = kpis().map(function (k, i) {
      return '<button type="button" class="mt-card mt-tap mt-kpi" data-act="kpi" data-k="' + k.id + '" aria-label="' + esc(k.lbl + ': ' + abbr(k.val) + '. Toque para ver valor exato e metodologia') + '">' +
        '<span class="mt-lbl">' + esc(k.lbl) + '</span>' +
        '<span class="mt-big mt-num"' + (k.cor ? ' style="color:' + k.cor + '"' : '') + '>' + esc(abbr(k.val)) + '</span>' +
        (k.bar !== null ? '<div class="mt-bar ' + (k.barCls || '') + '" role="img" aria-label="' + esc(pct(k.bar)) + '"><i style="width:' + barW(k.bar) + '%"></i></div>' : '') +
        '<span class="mt-sub mt-num">' + esc(k.sub) + '</span>' +
        '<span class="mt-sub" style="color:var(--mt-accent);font-weight:700">Detalhes ' + ic('chevron_right', 'mt-inline') + '</span>' +
      '</button>';
    }).join('');
    var dots = kpis().map(function (k, i) { return '<i class="' + (i ? '' : 'on') + '"></i>'; }).join('');

    var cob = ((d.pncp_meta || {}).cobertura_avaliada) || null;
    var cobHtml = cob ? '<div class="mt-card ' + (cob.conclusiva ? 'sev-ok' : 'sev-inc') + '">' +
      '<div class="mt-row"><span class="mt-lbl">Cobertura do PNCP</span>' + badge(cob.conclusiva ? 'ok' : 'inc', cob.conclusiva ? 'Conclusiva' : 'Inconclusiva') + '</div>' +
      '<span class="mt-name">' + esc(cob.estado_label || '') + '</span>' +
      (cob.motivo ? '<span class="mt-sub">' + esc(cob.motivo) + '</span>' : '') + '</div>' : '';

    var cmpAnual = (d.comparativo_anual || []).map(function (a) {
      return '<div class="mt-card"><div class="mt-row"><span class="mt-name">Exercício ' + esc(a.ano) + '</span><span class="mt-val mt-num" style="color:var(--mt-ok-ink)">' + esc(abbr(a.receita_arrecadada)) + '</span></div>' +
        '<dl class="mt-kv mt-num"><dt>Despesa paga</dt><dd>' + esc(abbr(a.despesa_paga)) + '</dd>' +
        '<dt>PNCP (' + num(a.pncp_contratos) + ')</dt><dd>' + esc(abbr(a.pncp_valor)) + '</dd>' +
        '<dt>Fiorilli (' + num(a.fiorilli_contratos) + ')</dt><dd>' + esc(abbr(a.fiorilli_valor)) + '</dd>' +
        '<dt>Diretas (' + num(a.diretas_qtd) + ')</dt><dd>' + esc(abbr(a.diretas_valor)) + '</dd></dl></div>';
    }).join('');
    var comp = d.comparabilidade || {};

    return naoSincronizado() +
      '<div class="mt-carousel" id="mt-carousel" role="region" aria-roledescription="carrossel" aria-label="Indicadores">' + cards + '</div>' +
      '<div class="mt-dots" id="mt-dots" aria-hidden="true">' + dots + '</div>' +
      cobHtml +
      '<h2 class="mt-h2">Comparativo anual</h2>' + cmpAnual +
      (comp.motivo ? '<p class="mt-note">' + esc(comp.motivo) + '</p>' : '');
  }

  function ligarCarrossel() {
    var c = $('mt-carousel');
    var dots = $('mt-dots');
    if (!c || !dots) { return; }
    c.addEventListener('scroll', function () {
      var w = c.firstElementChild ? c.firstElementChild.getBoundingClientRect().width + 12 : 1;
      var i = Math.round(c.scrollLeft / w);
      Array.prototype.forEach.call(dots.children, function (el, j) { el.className = j === i ? 'on' : ''; });
    }, { passive: true });
  }

  function abrirKpi(id) {
    var d = st.resumo || {};
    var met = d.metodologia || {};
    var rec = d.receitas || {};
    var desp = d.despesas || {};
    var cmp = d.contratos_comparativo || {};
    var disp = d.dispensas_resumo || {};
    var comp = d.comparabilidade || {};
    var k = kpis().filter(function (x) { return x.id === id; })[0];
    if (!k) { return; }
    var linhas = [];
    var m = null;
    var notas = [];
    if (id === 'receita') {
      m = met.receita;
      linhas = [['Arrecadado líquido', rec.arrecadado_liquido], ['Arrecadado bruto', rec.arrecadado_bruto], ['Deduções (FUNDEB)', rec.deducoes_arrecadado],
        ['Previsão atualizada líquida', rec.previsao_atualizada_liquida], ['Realizado', null, pct(rec.pct_realizado)],
        ['Transferências da União', rec.transferencias_uniao_arrecadado], ['Transferências do Estado (MA)', rec.transferencias_estado_arrecadado]];
    } else if (id === 'pago') {
      m = met.despesa_paga;
      linhas = [['Pago', desp.pago], ['Liquidado', desp.liquidado], ['Empenhado', desp.empenhado], ['Dotação atualizada', desp.dotacao_atualizada],
        ['Pago / dotação', null, pct(desp.pct_execucao_pago)], ['Pago / receita', null, pct(cmp.pct_pago_sobre_receita)], ['Fornecedores únicos', null, num(desp.fornecedores_unicos)]];
    } else if (id === 'contratos') {
      m = met.contratos_pncp;
      linhas = [['PNCP: valor', cmp.pncp_valor_total], ['PNCP: contratos', null, num(cmp.pncp_qtd)], ['Fiorilli: valor', cmp.fiorilli_valor_total],
        ['Fiorilli: contratos', null, num(cmp.fiorilli_qtd)], ['Fiorilli − PNCP', cmp.diferenca_fiorilli_menos_pncp],
        ['Remanescentes de anos ant.', cmp.fiorilli_remanescentes_valor, null, num(cmp.fiorilli_remanescentes_qtd) + ' contratos']];
      if (met.contratos_fiorilli) { notas.push('Fiorilli — ' + met.contratos_fiorilli.fonte + ': ' + met.contratos_fiorilli.universo); }
      if (comp.aviso_saldo) { notas.push(comp.aviso_saldo); }
      if (comp.tratamento_remanescentes) { notas.push(comp.tratamento_remanescentes); }
    } else if (id === 'dispensas') {
      m = met.contratos_fiorilli;
      var ps = disp.por_status_pncp || {};
      linhas = [['Total de diretas', disp.diretas_valor, null, num(disp.diretas_qtd)], ['Dispensas', disp.dispensas_valor, null, num(disp.dispensas_qtd)],
        ['Inexigibilidades', disp.inexigibilidades_valor, null, num(disp.inexigibilidades_qtd)],
        ['Conciliadas no PNCP', (ps.CONCILIADO || {}).valor, null, num((ps.CONCILIADO || {}).qtd)],
        ['Fornecedor no PNCP (outro proc.)', (ps.FORNECEDOR_NO_PNCP || {}).valor, null, num((ps.FORNECEDOR_NO_PNCP || {}).qtd)],
        ['Não localizadas no PNCP', (ps.AUSENTE_PNCP || {}).valor, null, num((ps.AUSENTE_PNCP || {}).qtd)]];
      if (disp.aviso_nao_localizado) { notas.push(disp.aviso_nao_localizado); }
      var nl = d.nao_localizados_pncp_resumo || {};
      if (nl.aviso) { notas.push(nl.aviso); }
    }
    var html = '<div class="mt-card"><span class="mt-lbl">Valor exato</span><span class="mt-val mt-num" style="font-size:22px;white-space:normal">' + esc(brl(k.val)) + '</span></div>' +
      '<dl class="mt-kv mt-num">' + linhas.map(function (l) {
        var v = l[2] != null ? l[2] : brl(l[1]);
        return '<dt>' + esc(l[0]) + (l[3] ? ' <small>(' + esc(l[3]) + ')</small>' : '') + '</dt><dd>' + esc(v) + '</dd>';
      }).join('') + '</dl>' +
      (m ? '<hr class="mt-hr"/><dl class="mt-kv" style="grid-template-columns:auto 1fr"><dt>Fonte</dt><dd style="text-align:left">' + esc(m.fonte) +
        '</dd><dt>Período</dt><dd style="text-align:left">' + esc(m.periodo) + '</dd><dt>Metodologia</dt><dd style="text-align:left;font-weight:500">' + esc(m.universo) + '</dd></dl>' : '') +
      notas.map(function (n) { return '<div class="mt-banner">' + ic('info') + '<div>' + esc(n) + '</div></div>'; }).join('');
    abrirSheet(k.lbl, html);
  }

  // ---- Receitas
  function htmlReceitas() {
    var rec = (st.resumo || {}).receitas || {};
    var list = rec.composicao || [];
    if (!list.length) { return naoSincronizado() + estado('inbox', 'Sem dados de receita', 'Nenhuma receita sincronizada para este exercício.'); }
    var head = '<div class="mt-card"><span class="mt-lbl">' + esc(yearLabel()) + ' • arrecadado líquido</span>' +
      '<span class="mt-big mt-num" style="color:var(--mt-ok-ink)">' + esc(abbr(rec.arrecadado_liquido)) + '</span>' +
      '<div class="mt-bar" role="img" aria-label="' + esc(pct(rec.pct_realizado)) + ' da previsão"><i style="width:' + barW(rec.pct_realizado) + '%"></i></div>' +
      '<span class="mt-sub mt-num">' + esc(pct(rec.pct_realizado)) + ' da previsão de ' + esc(abbr(rec.previsao_atualizada_liquida)) + '</span></div>';
    var orig = '<div class="mt-card"><span class="mt-lbl">Origem das transferências</span><dl class="mt-kv mt-num">' +
      '<dt>União</dt><dd>' + esc(abbr(rec.transferencias_uniao_arrecadado)) + '</dd>' +
      '<dt><small>previsto</small></dt><dd><small>' + esc(abbr(rec.transferencias_uniao_previsto)) + '</small></dd>' +
      '<dt>Estado (MA)</dt><dd>' + esc(abbr(rec.transferencias_estado_arrecadado)) + '</dd>' +
      '<dt><small>previsto</small></dt><dd><small>' + esc(abbr(rec.transferencias_estado_previsto)) + '</small></dd></dl></div>';
    var itens = list.map(function (it, i) {
      var v = Number(it.arrecadado_total || 0);
      var neg = v < 0;
      return '<button type="button" class="mt-card mt-tap" data-act="expand" aria-expanded="false">' +
        '<div class="mt-row"><span class="mt-name">' + esc(it.nome) + '</span>' + ic('expand_more', 'mt-chev') + '</div>' +
        '<div class="mt-row"><span class="mt-val mt-num" style="color:' + (neg ? 'var(--mt-att-ink)' : 'var(--mt-ok-ink)') + '">' + esc(abbr(v)) + '</span>' +
        '<span class="mt-sub mt-num">' + esc(pct(it.participacao_pct)) + ' do total</span></div>' +
        (neg ? '' : '<div class="mt-bar" role="img" aria-label="Execução ' + esc(pct(it.realizacao_pct)) + '"><i style="width:' + barW(it.realizacao_pct) + '%"></i></div>' +
        '<span class="mt-sub mt-num">Execução ' + esc(pct(it.realizacao_pct)) + ' da previsão</span>') +
        '<div class="mt-more"><dl class="mt-kv mt-num"><dt>Arrecadado</dt><dd>' + esc(brl(v)) + '</dd><dt>Previsão atualizada</dt><dd>' + esc(brl(it.previsao_atualizada)) +
        '</dd><dt>Participação</dt><dd>' + esc(pct(it.participacao_pct)) + '</dd><dt>Execução</dt><dd>' + esc(pct(it.realizacao_pct)) + '</dd></dl></div>' +
      '</button>';
    }).join('');
    return naoSincronizado() + head + '<h2 class="mt-h2">Composição da receita</h2><p class="mt-note">Contas-raiz sem dupla contagem (arrecadado bruto, transferências e deduções FUNDEB).</p>' + itens + orig;
  }

  // ---- Órgãos (lista completa vem no resumo: busca, ordem e paginação no aparelho)
  function orgaosFiltrados() {
    var lista = (((st.resumo || {}).despesas || {}).por_orgao || []).slice();
    var q = st.org.q.trim().toLowerCase();
    if (q) {
      var norm = function (s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); };
      var nq = norm(q);
      lista = lista.filter(function (o) { return norm(o.orgao_nome).indexOf(nq) >= 0 || String(o.codigo || '').indexOf(q) >= 0; });
    }
    var s = st.org.sort;
    lista.sort(function (a, b) {
      if (s === 'dotacao_desc') { return b.dotacao_atualizada - a.dotacao_atualizada; }
      if (s === 'pct_desc') { return (b.pct_pago_dotacao || 0) - (a.pct_pago_dotacao || 0); }
      if (s === 'pct_asc') { return (a.pct_pago_dotacao || 0) - (b.pct_pago_dotacao || 0); }
      if (s === 'nome_asc') { return String(a.orgao_nome).localeCompare(String(b.orgao_nome), 'pt-BR'); }
      return b.pago - a.pago;
    });
    return lista;
  }
  function cardOrgao(o) {
    var k = String(o.codigo || '') + '|' + String(o.empresa || '') + '|' + o.orgao_nome;
    var aberto = !!st.org.open[k];
    return '<button type="button" class="mt-card mt-tap" data-act="org" data-key="' + esc(k) + '" aria-expanded="' + aberto + '">' +
      '<div class="mt-row" style="align-items:flex-start"><span class="mt-name">' + esc(o.orgao_nome) + '</span>' + ic('expand_more', 'mt-chev') + '</div>' +
      '<div class="mt-row"><span class="mt-sub">Pago</span><span class="mt-val mt-num" style="color:var(--mt-accent)">' + esc(abbr(o.pago)) + '</span></div>' +
      '<div class="mt-bar is-blue" role="img" aria-label="' + esc(pct(o.pct_pago_dotacao)) + ' da dotação paga"><i style="width:' + barW(o.pct_pago_dotacao) + '%"></i></div>' +
      '<span class="mt-sub mt-num">' + esc(pct(o.pct_pago_dotacao)) + ' da dotação executada (pago)</span>' +
      '<div class="mt-more"><dl class="mt-kv mt-num"><dt>Dotação atualizada</dt><dd>' + esc(brl(o.dotacao_atualizada)) + '</dd>' +
      '<dt>Empenhado</dt><dd>' + esc(brl(o.empenhado)) + '</dd><dt>Liquidado</dt><dd>' + esc(brl(o.liquidado)) + '</dd>' +
      '<dt>Pago</dt><dd>' + esc(brl(o.pago)) + '</dd><dt>Código</dt><dd class="mt-mono">' + esc(o.codigo) + '</dd></dl></div>' +
    '</button>';
  }
  function toolbar(kind, total, rotulo) {
    return '<div class="mt-toolbar"><span class="mt-grow mt-sub mt-num" aria-live="polite">' + esc(rotulo || (num(total) + ' itens')) + '</span>' +
      '<button type="button" class="mt-btn" data-act="filtros" data-kind="' + kind + '" aria-haspopup="dialog">' + ic('tune') + 'Filtros</button></div>';
  }
  function renderOrgaos() {
    var p = $('mt-p-orgaos');
    if (!p) { return; }
    if (!st.resumo) { p.innerHTML = st.resumoErr ? erroResumo() : skeleton(4); return; }
    var lista = orgaosFiltrados();
    var desc = st.org.q ? 'para "' + st.org.q + '"' : '';
    var vis = lista.slice(0, st.org.shown);
    var corpo = vis.length ? vis.map(cardOrgao).join('') : estado('search_off', 'Nenhum órgão encontrado', desc ? 'Nada ' + desc + '.' : 'Nenhum dado de despesa por órgão sincronizado.');
    var rodape = '';
    if (lista.length > vis.length) {
      rodape = st.org.all
        ? '<div class="mt-sentinel" data-sentinel="orgaos"></div><div class="mt-loading-more">Carregando mais…</div>'
        : '<button type="button" class="mt-btn block" data-act="ver-todos" data-kind="orgaos">Ver todos (' + num(lista.length) + ')</button>';
    }
    p.innerHTML = naoSincronizado() + '<h2 class="mt-h2">Execução por órgão</h2>' + toolbar('orgaos', lista.length, num(lista.length) + ' órgãos / fundos ' + desc) + corpo + rodape;
    observarSentinela(p);
  }

  // ---- Contratos (fornecedores × PNCP, e contratações diretas)
  function renderContratos() {
    var p = $('mt-p-contratos');
    if (!p) { return; }
    var seg = '<div class="mt-seg" role="tablist" aria-label="Tipo de lista">' +
      '<button type="button" role="tab" data-act="seg" data-seg="fornecedores" aria-selected="' + (st.seg === 'fornecedores') + '">Fornecedores</button>' +
      '<button type="button" role="tab" data-act="seg" data-seg="diretas" aria-selected="' + (st.seg === 'diretas') + '">Diretas</button></div>';
    p.innerHTML = seg + '<div id="mt-list-' + (st.seg === 'diretas' ? 'dir' : 'cruz') + '"></div>';
    renderLista(st.seg === 'diretas' ? 'dir' : 'cruz');
  }

  function renderPendencias() {
    var p = $('mt-p-pendencias');
    if (!p) { return; }
    var d = st.resumo;
    var pontos = '';
    if (d && d.pontos_apuracao) {
      var pa = d.pontos_apuracao;
      var itens = (pa.items || []).filter(function (x) { return Number(x.qtd || 0) > 0; });
      pontos = '<h2 class="mt-h2">Pontos de apuração</h2>' +
        '<p class="mt-note">Sinais para checagem jornalística, não conclusões. ' + esc((((pa.cobertura || {}).limitacoes) || [])[0] || '') + '</p>' +
        (itens.length ? itens.map(function (x, i) {
          var sv = sevPonto(x);
          var valor = x.pago != null ? x.pago : x.valor_contratos;
          return '<button type="button" class="mt-card mt-tap sev-' + sv + '" data-act="ponto" data-i="' + (pa.items || []).indexOf(x) + '">' +
            '<div class="mt-row">' + badge(sv) + '<span class="mt-sub mt-num">' + num(x.qtd) + ' item(ns)</span></div>' +
            '<span class="mt-name mt-clamp2">' + esc(x.descricao) + '</span>' +
            (valor ? '<span class="mt-val mt-num">' + esc(abbr(valor)) + '</span>' : '') +
          '</button>';
        }).join('') : estado('task_alt', 'Nenhum ponto de apuração', 'Nada a checar neste recorte.'));
    } else if (!d) {
      pontos = st.resumoErr ? erroResumo() : skeleton(2);
    }
    p.innerHTML = pontos + '<h2 class="mt-h2">Contratações não localizadas no PNCP</h2>' +
      '<p class="mt-note">Não localizado no recorte sincronizado não equivale a inexistência de contrato no PNCP nem a irregularidade.</p>' +
      '<div id="mt-list-pend"></div>';
    renderLista('pend');
  }

  function cardCruz(r, i) {
    var sv = sevCruz(r);
    var diff = Number(r.diferenca_pago_pncp || 0);
    return '<button type="button" class="mt-card mt-tap" data-act="dossie" data-kind="cruz" data-i="' + i + '">' +
      '<div class="mt-row" style="align-items:flex-start"><span class="mt-name mt-clamp2">' + esc(r.fornecedor_nome) + '</span>' + ic('chevron_right', 'mt-chev') + '</div>' +
      '<span class="mt-sub mt-mono">' + esc(r.fornecedor_doc || 'Sem CPF/CNPJ') + (r.cidade ? ' • ' + esc(r.cidade) : '') + '</span>' +
      '<div>' + badge(sv, r.alerta_label) + '</div>' +
      '<dl class="mt-kv mt-num"><dt>Pago</dt><dd style="color:var(--mt-accent)">' + esc(abbr(r.pago)) + '</dd>' +
      '<dt>Contratado PNCP</dt><dd>' + esc(abbr(r.pncp_valor)) + ' <small>(' + num(r.pncp_contratos_qtd) + ')</small></dd>' +
      (r.pncp_valor ? '<dt>Pago − PNCP</dt><dd>' + (diff >= 0 ? '+' : '') + esc(abbr(diff)) + '</dd>' : '') + '</dl>' +
    '</button>';
  }
  function cardDisp(r, i, kind, conclusiva) {
    var sv = sevDisp(r, conclusiva);
    return '<button type="button" class="mt-card mt-tap sev-' + sv + '" data-act="dossie" data-kind="' + kind + '" data-i="' + i + '">' +
      '<div class="mt-row"><span class="mt-sub">' + esc(r.tipo_contratacao || '') + (r.ano ? ' • ' + esc(r.ano) : '') + '</span>' + ic('chevron_right', 'mt-chev') + '</div>' +
      '<span class="mt-sub mt-clamp2">' + esc(r.secretaria_gestora || r.entidade || '') + '</span>' +
      '<span class="mt-name mt-clamp2">' + esc(r.fornecedor_nome || '-') + '</span>' +
      '<div class="mt-row"><span class="mt-val mt-num">' + esc(abbr(r.valor_efetivo)) + '</span></div>' +
      '<div>' + badge(sv, r.status_pncp_label) + '</div>' +
    '</button>';
  }

  function resumoFiltros(kind) {
    var l = st[kind];
    var partes = [];
    if (kind === 'cruz') {
      if (l.alerta && l.alerta !== 'TODOS') { partes.push((ALERTAS.filter(function (a) { return a[0] === l.alerta; })[0] || [0, l.alerta])[1]); }
      if (l.ordem !== 'pago_desc') { partes.push((ORDENS_CRUZ.filter(function (a) { return a[0] === l.ordem; })[0] || [0, ''])[1]); }
    } else {
      if (l.tipo) { partes.push(l.tipo); }
      if (kind === 'dir' && l.status) { partes.push((STATUS_DISP.filter(function (a) { return a[0] === l.status; })[0] || [0, ''])[1]); }
      if (l.entidade) { partes.push(l.entidade); }
      if (l.rem) { partes.push('anos ant.'); }
    }
    if (l.q) { partes.push('"' + l.q + '"'); }
    return partes.join(' • ');
  }

  function renderLista(kind) {
    var box = $('mt-list-' + kind);
    if (!box) { return; }
    var l = st[kind];
    var d = l.data || {};
    var conclusiva = !!((d.cobertura_pncp || {}).conclusiva);
    var filtros = resumoFiltros(kind);
    var rot = (l.page ? num(l.total) + (kind === 'cruz' ? ' fornecedores' : ' contratações') : 'Carregando…') +
      (l.page && kind !== 'cruz' && d.valor_filtrado != null ? ' • ' + abbr(d.valor_filtrado) : '') + (filtros ? ' • ' + filtros : '');
    var head = toolbar(kind, l.total, rot);
    var aviso = '';
    if (kind !== 'cruz' && l.page && d.aviso_nao_localizado && !conclusiva) {
      aviso = '<div class="mt-banner">' + ic('help') + '<div>' + esc(d.aviso_nao_localizado) + '</div></div>';
    }
    if (kind === 'cruz' && l.page && d.cobertura_pncp && !conclusiva) {
      aviso = '<div class="mt-banner">' + ic('help') + '<div>' + esc(d.cobertura_pncp.estado_label || '') + (d.cobertura_pncp.motivo ? ': ' + esc(d.cobertura_pncp.motivo) : '') + '</div></div>';
    }
    if (!l.page && l.loading) { box.innerHTML = head + skeleton(3, 20); return; }
    if (!l.page && l.error) {
      box.innerHTML = head + estado('wifi_off', 'Não foi possível carregar a lista', l.error,
        '<button type="button" class="mt-btn primary" data-act="retry-list" data-kind="' + kind + '">' + ic('refresh') + 'Tentar de novo</button>');
      return;
    }
    if (!l.items.length) {
      box.innerHTML = head + aviso + estado('search_off', 'Nada encontrado', filtros ? 'Nenhum item para os filtros escolhidos.' : 'Nenhum item neste exercício.',
        filtros ? '<button type="button" class="mt-btn" data-act="limpar" data-kind="' + kind + '">Limpar filtros</button>' : '');
      return;
    }
    var vis = l.all ? l.items : l.items.slice(0, FIRST);
    var cards = vis.map(function (r, i) { return kind === 'cruz' ? cardCruz(r, i) : cardDisp(r, i, kind, conclusiva); }).join('');
    var rodape = '';
    var temMais = l.items.length > vis.length || l.page < l.totalPages;
    if (temMais) {
      if (!l.all) {
        rodape = '<button type="button" class="mt-btn block" data-act="ver-todos" data-kind="' + kind + '">Ver todos (' + num(l.total) + ')</button>';
      } else if (l.error) {
        rodape = '<button type="button" class="mt-btn block" data-act="mais" data-kind="' + kind + '">' + ic('refresh') + 'Tentar carregar mais</button>';
      } else {
        rodape = '<div class="mt-sentinel" data-sentinel="' + kind + '"></div><div class="mt-loading-more" role="status">' + (l.loading ? 'Carregando mais…' : '') + '</div>';
      }
    }
    box.innerHTML = head + aviso + cards + rodape;
    observarSentinela(box);
  }

  // Rolagem incremental: quando o fim da lista aparece, carrega mais.
  var observer = null;
  function observarSentinela(root) {
    var s = root.querySelector('[data-sentinel]');
    if (!s) { return; }
    if (!('IntersectionObserver' in window)) {
      s.outerHTML = '<button type="button" class="mt-btn block" data-act="mais" data-kind="' + s.dataset.sentinel + '">Carregar mais</button>';
      return;
    }
    if (!observer) {
      observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) { return; }
          observer.unobserve(en.target);
          carregarMais(en.target.dataset.sentinel);
        });
      }, { rootMargin: '400px 0px' });
    }
    observer.observe(s);
  }
  function carregarMais(kind) {
    if (kind === 'orgaos') { st.org.shown += ORG_STEP; renderOrgaos(); return; }
    carregarLista(kind, false);
  }

  // ---- Dossiê em tela cheia
  function abrirDossie(kind, i) {
    var l = st[kind];
    var r = l.items[i];
    if (!r) { return; }
    var d = l.data || {};
    var html = '';
    var titulo = 'Dossiê';
    var acoes = '';
    if (kind === 'cruz') {
      var sv = sevCruz(r);
      titulo = 'Fornecedor';
      html = '<div class="mt-card"><span class="mt-name" style="font-size:18px">' + esc(r.fornecedor_nome) + '</span>' +
        '<span class="mt-sub mt-mono">' + esc(r.fornecedor_doc || 'Sem CPF/CNPJ') + ' • ' + esc(r.tipo_pessoa || '') + (r.cidade ? ' • ' + esc(r.cidade) : '') + '</span>' +
        '<div>' + badge(sv, r.alerta_label) + '</div>' + (r.alerta_observacao ? '<span class="mt-sub">' + esc(r.alerta_observacao) + '</span>' : '') + '</div>' +
        '<div class="mt-card"><span class="mt-lbl">Valores (exatos)</span><dl class="mt-kv mt-num">' +
        '<dt>Pago</dt><dd>' + esc(brl(r.pago)) + '</dd><dt>Liquidado</dt><dd>' + esc(brl(r.liquidado)) + '</dd><dt>Empenhado</dt><dd>' + esc(brl(r.empenhado)) + '</dd>' +
        '<dt>Contratado PNCP (' + num(r.pncp_contratos_qtd) + ')</dt><dd>' + esc(brl(r.pncp_valor)) + '</dd>' +
        '<dt>Fiorilli (' + num(r.fiorilli_contratos_qtd) + ' ct • ' + num(r.fiorilli_dispensas_qtd) + ' dir.)</dt><dd>' + esc(brl(r.fiorilli_valor)) + '</dd>' +
        (r.fiorilli_remanescentes_qtd ? '<dt>Contratos de anos ant. (' + num(r.fiorilli_remanescentes_qtd) + ')</dt><dd>' + esc(brl(r.fiorilli_remanescentes_valor)) + '</dd>' : '') +
        '<dt>Pago − PNCP</dt><dd>' + esc(brl(r.diferenca_pago_pncp)) + '</dd>' +
        (r.pct_pago_sobre_pncp != null ? '<dt>Pago / PNCP</dt><dd>' + esc(pct(r.pct_pago_sobre_pncp)) + '</dd>' : '') + '</dl></div>' +
        '<div class="mt-card"><span class="mt-lbl">Vínculo</span><span class="mt-name">' + esc(r.classificacao_vinculo_label || '-') + '</span>' +
        ((r.campos_comparados || []).length ? '<span class="mt-sub">Campos comparados: ' + esc(r.campos_comparados.join(', ')) + '</span>' : '') +
        ((r.orgaos_pncp || []).length ? '<span class="mt-sub">Órgãos no PNCP: ' + esc(r.orgaos_pncp.join(', ')) + '</span>' : '') +
        ((r.entidades_fiorilli || []).length ? '<span class="mt-sub">Entidades no Fiorilli: ' + esc(r.entidades_fiorilli.join(', ')) + '</span>' : '') + '</div>';
      if (r.pncp_contratos_qtd > 0) {
        acoes = '<button type="button" class="mt-btn primary block" data-act="pncp-busca" data-q="' + esc(r.fornecedor_doc || r.fornecedor_nome || '') + '">' + ic('open_in_new') + 'Ver contratos no PNCP (' + num(r.pncp_contratos_qtd) + ')</button>';
      }
      if (d.cobertura_pncp && !d.cobertura_pncp.conclusiva) {
        html += '<div class="mt-banner">' + ic('help') + '<div>' + esc(d.cobertura_pncp.motivo || d.cobertura_pncp.estado_label || '') + '</div></div>';
      }
    } else {
      var conclusiva = !!((d.cobertura_pncp || {}).conclusiva);
      var sv2 = sevDisp(r, conclusiva);
      titulo = 'Dossiê da contratação';
      html = '<div class="mt-card sev-' + sv2 + '"><div>' + badge(sv2, r.status_pncp_label) + '</div>' +
        '<span class="mt-sub">' + esc(r.motivo_pncp || '') + '</span></div>' +
        '<div class="mt-card"><span class="mt-lbl">' + esc(r.tipo_contratacao || '') +
          (r.fund_legal && String(r.fund_legal).toUpperCase() !== String(r.tipo_contratacao || '').toUpperCase() ? ' • ' + esc(r.fund_legal) : '') + '</span>' +
        '<span class="mt-name" style="font-size:18px">' + esc(r.fornecedor_nome || '-') + '</span>' +
        '<span class="mt-sub mt-mono">' + esc(r.fornecedor_doc || 'Sem doc') + '</span>' +
        '<span class="mt-sub">' + esc(r.objeto || '-') + '</span></div>' +
        '<div class="mt-card"><span class="mt-lbl">Valores (exatos)</span><dl class="mt-kv mt-num">' +
        '<dt>Valor efetivo</dt><dd>' + esc(brl(r.valor_efetivo)) + '</dd><dt>Valor do contrato</dt><dd>' + esc(brl(r.valor_contrato)) + '</dd>' +
        (r.valor_aditado ? '<dt>Aditivos</dt><dd>' + esc(brl(r.valor_aditado)) + '</dd>' : '') +
        (r.empenhado != null ? '<dt>Empenhado</dt><dd>' + esc(brl(r.empenhado)) + '</dd>' : '') +
        (r.liquidado != null ? '<dt>Liquidado</dt><dd>' + esc(brl(r.liquidado)) + '</dd>' : '') + '</dl></div>' +
        '<div class="mt-card"><span class="mt-lbl">Identificação</span><dl class="mt-kv" style="grid-template-columns:auto 1fr">' +
        [['Entidade', r.entidade], ['Secretaria', r.secretaria_gestora], ['Tema', r.categoria_tematica], ['Modalidade', r.modalidade],
          ['Processo adm.', r.processo_adm], ['Processo licit.', r.processo_licitacao], ['Contrato', r.contrato_num], ['Código', r.codigo],
          ['Assinatura', dataCurta(r.data_assinatura)], ['Vigência até', dataCurta(r.vigencia_fim)], ['Exercício', r.ano],
          ['Controle PNCP', r.pncp_match_controle]].filter(function (x) { return x[1] && x[1] !== '-'; }).map(function (x) {
          return '<dt>' + esc(x[0]) + '</dt><dd style="text-align:left;font-weight:600;overflow-wrap:anywhere">' + esc(x[1]) + '</dd>';
        }).join('') + '</dl></div>';
      if (d.aviso_nao_localizado && !conclusiva && r.status_pncp === 'AUSENTE_PNCP') {
        html += '<div class="mt-banner">' + ic('help') + '<div>' + esc(d.aviso_nao_localizado) + '</div></div>';
      }
      acoes = r.pncp_match_url
        ? '<a class="mt-btn primary block" href="' + esc(r.pncp_match_url) + '" target="_blank" rel="noopener">' + ic('open_in_new') + 'Abrir no PNCP</a>'
        : '<button type="button" class="mt-btn primary block" data-act="pncp-busca" data-q="' + esc(r.fornecedor_doc || r.fornecedor_nome || '') + '">' + ic('search') + 'Buscar fornecedor no PNCP</button>';
    }
    abrirTelaCheia(titulo, html + acoes);
  }

  function abrirPonto(i) {
    var pa = (st.resumo || {}).pontos_apuracao || {};
    var x = (pa.items || [])[i];
    if (!x) { return; }
    var sv = sevPonto(x);
    var html = '<div class="mt-card sev-' + sv + '"><div class="mt-row">' + badge(sv) + '<span class="mt-sub mt-num">' + num(x.qtd) + ' item(ns)</span></div>' +
      '<span class="mt-name">' + esc(x.descricao) + '</span>' + (x.fonte ? '<span class="mt-sub">Fonte: ' + esc(x.fonte) + '</span>' : '') + '</div>' +
      '<dl class="mt-kv mt-num">' +
      (x.pago != null ? '<dt>Pago</dt><dd>' + esc(brl(x.pago)) + '</dd>' : '') +
      (x.pncp != null ? '<dt>PNCP</dt><dd>' + esc(brl(x.pncp)) + '</dd>' : '') +
      (x.valor_contratos != null ? '<dt>Valor dos contratos</dt><dd>' + esc(brl(x.valor_contratos)) + '</dd>' : '') + '</dl>' +
      (x.limitacao ? '<div class="mt-banner">' + ic('help') + '<div>' + esc(x.limitacao) + '</div></div>' : '');
    var forn = x.fornecedores || [];
    var cont = x.contratos || [];
    if (forn.length) {
      html += '<h2 class="mt-h2">Fornecedores</h2>' + forn.map(function (f) {
        return '<div class="mt-card"><span class="mt-name">' + esc(f.fornecedor_nome) + '</span><span class="mt-sub mt-mono">' + esc(f.fornecedor_doc || '') + '</span>' +
          '<dl class="mt-kv mt-num"><dt>Pago</dt><dd>' + esc(brl(f.pago)) + '</dd><dt>PNCP</dt><dd>' + esc(brl(f.pncp_valor)) + '</dd><dt>Fiorilli</dt><dd>' + esc(brl(f.fiorilli_valor)) + '</dd></dl>' +
          (f.classificacao_vinculo_label ? '<span class="mt-sub">' + esc(f.classificacao_vinculo_label) + '</span>' : '') + '</div>';
      }).join('');
    }
    if (cont.length) {
      html += '<h2 class="mt-h2">Contratos' + (x.amostra_limitada ? ' (amostra)' : '') + '</h2>' + cont.map(function (c) {
        return '<div class="mt-card"><span class="mt-name">' + esc(c.fornecedor_nome || '-') + '</span><span class="mt-sub mt-mono">' + esc(c.fornecedor_doc || '') + ' • ' + esc(c.codigo || '') + '</span>' +
          '<span class="mt-sub mt-clamp2">' + esc(c.objeto || '') + '</span><span class="mt-val mt-num">' + esc(brl(c.valor_efetivo)) + '</span>' +
          (c.motivo ? '<span class="mt-sub">' + esc(c.motivo) + '</span>' : '') + '</div>';
      }).join('');
    }
    abrirSheet('Ponto de apuração', html);
  }

  // ---- Filtros (bottom sheet)
  function opcoes(lista, atual) {
    return lista.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (o[0] === atual ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('');
  }
  function abrirFiltros(kind) {
    var html = '';
    var entidades = ((st.resumo || {}).entidades_disponiveis || []).map(function (e) { return [e, e]; });
    if (kind === 'orgaos') {
      html = '<label class="mt-field"><span>Buscar</span><input type="search" id="mf-q" value="' + esc(st.org.q) + '" placeholder="Nome do órgão ou fundo"/></label>' +
        '<label class="mt-field"><span>Ordenar por</span><select id="mf-sort">' + opcoes(ORDENS_ORG, st.org.sort) + '</select></label>';
    } else if (kind === 'cruz') {
      var res = (st.cruz.data || {}).resumo_alertas || {};
      var tot = Object.keys(res).reduce(function (a, k) { return a + Number(res[k].qtd || 0); }, 0);
      var alertas = ['PAGO_ACIMA_PNCP', 'PAGO_SEM_PNCP', 'DIVERGENCIA_VALOR', 'FIORILLI_SEM_PNCP'].reduce(function (a, k) { return a + Number((res[k] || {}).qtd || 0); }, 0);
      html = '<label class="mt-field"><span>Buscar</span><input type="search" id="mf-q" value="' + esc(st.cruz.q) + '" placeholder="Fornecedor, CNPJ/CPF ou cidade"/></label>' +
        '<label class="mt-field"><span>Ordenar por</span><select id="mf-ordem">' + opcoes(ORDENS_CRUZ, st.cruz.ordem) + '</select></label>' +
        '<div class="mt-field"><span id="mf-al-lbl">Sinal de auditoria</span><div class="mt-chips" role="radiogroup" aria-labelledby="mf-al-lbl">' +
        ALERTAS.map(function (a) {
          var q = a[0] === 'TODOS' ? tot : (a[0] === 'ALERTAS' ? alertas : Number((res[a[0]] || {}).qtd || 0));
          return '<button type="button" class="mt-chip" role="radio" data-alerta="' + a[0] + '" aria-checked="' + (st.cruz.alerta === a[0]) + '">' + esc(a[1]) + (st.cruz.data ? ' (' + num(q) + ')' : '') + '</button>';
        }).join('') + '</div></div>';
    } else {
      var l = st[kind];
      html = '<label class="mt-field"><span>Buscar</span><input type="search" id="mf-q" value="' + esc(l.q) + '" placeholder="Objeto, fornecedor, processo"/></label>' +
        '<label class="mt-field"><span>Tipo</span><select id="mf-tipo">' + opcoes(TIPOS_DISP, l.tipo) + '</select></label>' +
        (kind === 'dir' ? '<label class="mt-field"><span>Status no PNCP</span><select id="mf-status">' + opcoes(STATUS_DISP, l.status) + '</select></label>' : '') +
        '<label class="mt-field"><span>Entidade</span><select id="mf-ent">' + opcoes([['', 'Todas as entidades']].concat(entidades), l.entidade) + '</select></label>' +
        '<label class="mt-check"><input type="checkbox" id="mf-rem"' + (l.rem ? ' checked' : '') + '/> Incluir contratos de anos anteriores</label>';
    }
    html += '<div class="mt-toolbar"><button type="button" class="mt-btn mt-grow" data-act="f-limpar">Limpar</button>' +
      '<button type="button" class="mt-btn primary mt-grow" data-act="f-aplicar">Aplicar</button></div>';
    var sheet = abrirSheet('Busca e filtros', html);
    var body = sheet.querySelector('.mt-sheet-body');
    var alertaSel = st.cruz.alerta;
    body.addEventListener('click', function (e) {
      var chip = e.target.closest('[data-alerta]');
      if (chip) {
        alertaSel = chip.dataset.alerta;
        body.querySelectorAll('[data-alerta]').forEach(function (c) { c.setAttribute('aria-checked', String(c === chip)); });
        return;
      }
      var b = e.target.closest('[data-act]');
      if (!b) { return; }
      if (b.dataset.act === 'f-limpar') { limparFiltros(kind); fecharTopo(); }
      if (b.dataset.act === 'f-aplicar') {
        var g = function (id) { var el = body.querySelector('#' + id); return el ? (el.type === 'checkbox' ? el.checked : el.value) : undefined; };
        if (kind === 'orgaos') {
          st.org.q = String(g('mf-q') || '').trim(); st.org.sort = g('mf-sort'); st.org.shown = FIRST; st.org.all = false; renderOrgaos();
        } else {
          var l2 = st[kind];
          l2.q = String(g('mf-q') || '').trim();
          if (kind === 'cruz') { l2.ordem = g('mf-ordem'); l2.alerta = alertaSel; }
          else { l2.tipo = g('mf-tipo'); l2.entidade = g('mf-ent'); l2.rem = !!g('mf-rem'); if (kind === 'dir') { l2.status = g('mf-status'); } }
          l2.all = false;
          carregarLista(kind, true);
        }
        var input = $('mt-search-input');
        if (input) { input.value = valorBusca(); }
        fecharTopo();
      }
    });
    body.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.target.id === 'mf-q') { e.preventDefault(); var ap = body.querySelector('[data-act="f-aplicar"]'); if (ap) { ap.click(); } }
    });
  }
  function limparFiltros(kind) {
    if (kind === 'orgaos') { st.org.q = ''; st.org.sort = 'pago_desc'; st.org.shown = FIRST; renderOrgaos(); return; }
    var l = st[kind];
    l.q = ''; l.all = false;
    if (kind === 'cruz') { l.ordem = 'pago_desc'; l.alerta = 'TODOS'; } else { l.tipo = ''; l.entidade = ''; l.rem = false; l.status = ''; }
    var input = $('mt-search-input');
    if (input) { input.value = ''; }
    carregarLista(kind, true);
  }

  // ---- Busca rápida do topo
  var buscaTimer = null;
  function alternarBusca(abrir) {
    var box = $('mt-search');
    var btn = $('mt-search-btn');
    var app = $('mt-app');
    if (!box || !btn) { return; }
    box.hidden = !abrir;
    btn.setAttribute('aria-expanded', String(abrir));
    app.classList.toggle('mt-searching', abrir);
    if (abrir) {
      if (st.tab === 'geral' || st.tab === 'receitas') { irPara('contratos'); }
      var input = $('mt-search-input');
      input.value = valorBusca();
      setTimeout(function () { input.focus(); }, 30);
    } else {
      var input2 = $('mt-search-input');
      if (input2 && input2.value) { input2.value = ''; aplicarBusca(''); }
      btn.focus();
    }
  }
  function aoBuscar(e) {
    var v = e.target.value;
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(function () { aplicarBusca(v.trim()); }, 300);
  }
  function aplicarBusca(q) {
    if (st.tab === 'orgaos') { st.org.q = q; st.org.shown = FIRST; renderOrgaos(); return; }
    var kind = st.tab === 'pendencias' ? 'pend' : (st.seg === 'diretas' ? 'dir' : 'cruz');
    if (st.tab !== 'pendencias' && st.tab !== 'contratos') { return; }
    if (st[kind].q === q) { return; }
    st[kind].q = q;
    st[kind].all = false;
    carregarLista(kind, true);
  }

  // ---- Toques no conteúdo
  function aoTocarConteudo(e) {
    var b = e.target.closest('[data-act]');
    if (!b) { return; }
    var act = b.dataset.act;
    if (act === 'kpi') { abrirKpi(b.dataset.k); }
    else if (act === 'expand') { b.setAttribute('aria-expanded', String(b.getAttribute('aria-expanded') !== 'true')); }
    else if (act === 'org') {
      var aberto = b.getAttribute('aria-expanded') !== 'true';
      b.setAttribute('aria-expanded', String(aberto));
      st.org.open[b.dataset.key] = aberto;
    }
    else if (act === 'ver-todos') {
      var k = b.dataset.kind;
      if (k === 'orgaos') { st.org.all = true; st.org.shown += ORG_STEP; renderOrgaos(); }
      else { st[k].all = true; renderLista(k); }
    }
    else if (act === 'mais') { carregarMais(b.dataset.kind); }
    else if (act === 'filtros') { abrirFiltros(b.dataset.kind); }
    else if (act === 'limpar') { limparFiltros(b.dataset.kind); }
    else if (act === 'seg') {
      st.seg = b.dataset.seg;
      renderContratos();
      garantirAba('contratos');
      var input = $('mt-search-input');
      if (input) { input.value = valorBusca(); input.placeholder = st.seg === 'diretas' ? 'Objeto, fornecedor, processo…' : 'Buscar fornecedor, CNPJ/CPF ou cidade…'; }
    }
    else if (act === 'dossie') { abrirDossie(b.dataset.kind, Number(b.dataset.i)); }
    else if (act === 'ponto') { abrirPonto(Number(b.dataset.i)); }
    else if (act === 'retry') { carregarTudo(); }
    else if (act === 'retry-list') { carregarLista(b.dataset.kind, true); }
    else if (act === 'sync-fiorilli') { sincronizarFiorilli(); }
  }

  // ---------------------------------------------------- sheets e tela cheia
  var pilha = [];
  function tema() { try { return localStorage.getItem(THEME_KEY) || 'claro'; } catch (e) { return 'claro'; } }
  function temaEfetivo() {
    var t = tema();
    if (t === 'sistema') { return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }
    return t === 'escuro' ? 'dark' : 'light';
  }
  function aplicarTema() {
    var t = temaEfetivo();
    var app = $('mt-app');
    if (app) { app.dataset.theme = t; }
    pilha.forEach(function (l) { l.dataset.theme = t; });
    document.body.classList.toggle('mt-dark', t === 'dark');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      if (!meta.dataset.mtOriginal) { meta.dataset.mtOriginal = meta.getAttribute('content') || '#004deb'; }
      meta.setAttribute('content', st.active ? (t === 'dark' ? '#0b2f8a' : '#004deb') : meta.dataset.mtOriginal);
    }
  }

  function novaCamada(label) {
    var layer = document.createElement('div');
    layer.className = 'mt-layer';
    layer.dataset.theme = temaEfetivo();
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.setAttribute('aria-label', label);
    layer._retorno = document.activeElement;
    document.body.appendChild(layer);
    pilha.push(layer);
    try { history.pushState({ mtCamada: pilha.length }, ''); layer._hist = true; } catch (e) { layer._hist = false; }
    requestAnimationFrame(function () { requestAnimationFrame(function () { layer.classList.add('open'); }); });
    return layer;
  }

  function abrirSheet(titulo, html) {
    var layer = novaCamada(titulo);
    var hid = 'mt-sh-' + Date.now();
    layer.setAttribute('aria-labelledby', hid);
    layer.removeAttribute('aria-label');
    layer.innerHTML = '<div class="mt-backdrop" data-close="1"></div>' +
      '<div class="mt-sheet"><div class="mt-grab"><div class="mt-sheet-head"><h2 id="' + hid + '">' + esc(titulo) + '</h2>' +
      '<button type="button" class="mt-ib" data-close="1" aria-label="Fechar">' + ic('close') + '</button></div></div>' +
      '<div class="mt-sheet-body">' + html + '</div></div>';
    layer.addEventListener('click', function (e) { if (e.target.closest('[data-close]')) { fecharTopo(); } });
    ligarArrasto(layer);
    ligarAcoesCamada(layer);
    setTimeout(function () { var f = layer.querySelector('.mt-sheet-body input, .mt-sheet-body button, .mt-sheet-body a, [data-close]:not(.mt-backdrop)'); if (f) { f.focus({ preventScroll: true }); } }, 60);
    return layer;
  }

  function abrirTelaCheia(titulo, html) {
    var layer = novaCamada(titulo);
    layer.innerHTML = '<div class="mt-full"><div class="mt-full-head"><div class="mt-top-row">' +
      '<button type="button" class="mt-ib" data-close="1" aria-label="Voltar">' + ic('arrow_back') + '</button>' +
      '<div class="mt-title"><strong>' + esc(titulo) + '</strong><span>' + esc(yearLabel()) + '</span></div></div></div>' +
      '<div class="mt-full-body">' + html + '</div></div>';
    layer.addEventListener('click', function (e) { if (e.target.closest('[data-close]')) { fecharTopo(); } });
    ligarAcoesCamada(layer);
    setTimeout(function () { var f = layer.querySelector('[data-close]'); if (f) { f.focus({ preventScroll: true }); } }, 60);
    return layer;
  }

  function ligarAcoesCamada(layer) {
    layer.addEventListener('click', function (e) {
      var b = e.target.closest('[data-act="pncp-busca"]');
      if (b && typeof window.openPncpModal === 'function') {
        var q = b.dataset.q;
        fecharTudo();
        window.openPncpModal(curYear(), '', q, '', 'maior_valor');
      }
    });
    layer.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.stopPropagation(); fecharTopo(); return; }
      if (e.key !== 'Tab') { return; }
      var foco = Array.prototype.filter.call(layer.querySelectorAll('button, a[href], input, select, [tabindex="0"]'), function (el) { return !el.disabled && el.offsetParent !== null; });
      if (!foco.length) { return; }
      var first = foco[0], last = foco[foco.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
  }

  // Arrastar para baixo fecha o sheet (pela alça, ou pelo conteúdo quando já está no topo).
  function ligarArrasto(layer) {
    var sheet = layer.querySelector('.mt-sheet');
    var body = layer.querySelector('.mt-sheet-body');
    var y0 = null, dy = 0, t0 = 0, podeArrastar = false;
    sheet.addEventListener('touchstart', function (e) {
      var naAlca = !!e.target.closest('.mt-grab');
      podeArrastar = naAlca || body.scrollTop <= 0;
      if (!podeArrastar || e.target.closest('select, input')) { y0 = null; return; }
      y0 = e.touches[0].clientY; dy = 0; t0 = Date.now();
    }, { passive: true });
    sheet.addEventListener('touchmove', function (e) {
      if (y0 === null) { return; }
      dy = e.touches[0].clientY - y0;
      if (dy <= 0) { sheet.style.transform = ''; return; }
      if (e.cancelable) { e.preventDefault(); }
      sheet.classList.add('dragging');
      sheet.style.transform = 'translateY(' + dy + 'px)';
    }, { passive: false });
    sheet.addEventListener('touchend', function () {
      if (y0 === null) { return; }
      sheet.classList.remove('dragging');
      var vel = dy / Math.max(1, Date.now() - t0);
      if (dy > 110 || (dy > 40 && vel > 0.6)) { fecharTopo(); } else { sheet.style.transform = ''; }
      y0 = null;
    });
  }

  function removerCamada(layer) {
    layer.classList.remove('open');
    var sheet = layer.querySelector('.mt-sheet');
    if (sheet) { sheet.style.transform = ''; }
    var ret = layer._retorno;
    var reduz = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setTimeout(function () { if (layer.parentNode) { layer.parentNode.removeChild(layer); } }, reduz ? 0 : 260);
    if (ret && ret.focus && document.contains(ret)) { try { ret.focus({ preventScroll: true }); } catch (e) { /* foco opcional */ } }
  }
  function fecharTopo() {
    var layer = pilha[pilha.length - 1];
    if (!layer) { return; }
    if (layer._hist && history.state && history.state.mtCamada === pilha.length) { history.back(); return; } // popstate fecha
    pilha.pop();
    removerCamada(layer);
  }
  function fecharTudo() {
    var n = pilha.filter(function (l) { return l._hist; }).length;
    while (pilha.length) { removerCamada(pilha.pop()); }
    if (n && history.state && history.state.mtCamada) { ignorarPop = n; history.go(-n); }
  }
  var ignorarPop = 0;
  window.addEventListener('popstate', function () {
    if (ignorarPop > 0) { ignorarPop -= 1; return; }
    var layer = pilha.pop();
    if (layer) { removerCamada(layer); }
  });

  // ---- Menu "Mais" (substitui a barra lateral no celular)
  function abrirMais() {
    var nav = Array.prototype.slice.call(document.querySelectorAll('#sidebar-nav .nav-item'));
    var itens = nav.map(function (btn, i) {
      var icone = (btn.querySelector('.material-symbols-outlined') || {}).textContent || 'chevron_right';
      var rot = (btn.querySelector('.font-body-md') || btn).textContent.trim();
      var atual = btn.id === 'nav-btn-transparencia';
      return '<button type="button" class="mt-list-btn" data-nav="' + i + '"' + (atual ? ' aria-current="page"' : '') + '>' + ic(icone) + '<span>' + esc(rot) + '</span></button>';
    }).join('');
    var t = tema();
    var temas = [['claro', 'Claro'], ['escuro', 'Escuro'], ['sistema', 'Sistema']].map(function (o) {
      return '<button type="button" class="mt-chip" role="radio" data-tema="' + o[0] + '" aria-checked="' + (t === o[0]) + '">' + o[1] + '</button>';
    }).join('');
    var html = '<nav aria-label="Seções do Radar">' + itens + '</nav><hr class="mt-hr"/>' +
      '<button type="button" class="mt-list-btn" data-m="atualizar">' + ic('refresh') + '<span>Atualizar dados agora</span></button>' +
      '<button type="button" class="mt-list-btn" data-m="fiorilli">' + ic('sync') + '<span>Sincronizar Portal Fiorilli</span></button>' +
      '<a class="mt-list-btn" href="' + esc(urlExportacao()) + '">' + ic('download') + '<span>Exportar CSV desta lista</span></a>' +
      '<hr class="mt-hr"/><div class="mt-field"><span id="mt-tema-lbl">Tema</span><div class="mt-chips" role="radiogroup" aria-labelledby="mt-tema-lbl">' + temas + '</div></div>';
    var layer = abrirSheet('Mais', html);
    layer.querySelector('.mt-sheet-body').addEventListener('click', function (e) {
      var n = e.target.closest('[data-nav]');
      if (n) {
        var alvo = nav[Number(n.dataset.nav)];
        fecharTudo();
        if (alvo && alvo.id !== 'nav-btn-transparencia') { setTimeout(function () { alvo.click(); }, 0); }
        return;
      }
      var tm = e.target.closest('[data-tema]');
      if (tm) {
        try { localStorage.setItem(THEME_KEY, tm.dataset.tema); } catch (err) { /* tema só nesta sessão */ }
        layer.querySelectorAll('[data-tema]').forEach(function (c) { c.setAttribute('aria-checked', String(c === tm)); });
        aplicarTema();
        return;
      }
      var m = e.target.closest('[data-m]');
      if (!m) { return; }
      fecharTudo();
      if (m.dataset.m === 'atualizar') { atualizar(); }
      if (m.dataset.m === 'fiorilli') { sincronizarFiorilli(); }
    });
  }

  // Exportação com os filtros da lista aberta (mesma rota e parâmetros do desktop).
  function urlExportacao() {
    var p = new URLSearchParams();
    if (curYear()) { p.set('ano', curYear()); }
    var kind = st.tab === 'pendencias' ? 'pend' : (st.tab === 'contratos' && st.seg === 'diretas' ? 'dir' : 'cruz');
    var l = st[kind];
    if (kind === 'cruz') {
      p.set('visao', 'cruzamento'); p.set('alerta', l.alerta || 'TODOS'); p.set('ordem', l.ordem || 'pago_desc');
    } else {
      p.set('visao', 'dispensas');
      if (l.tipo) { p.set('tipo', l.tipo); }
      var s = kind === 'pend' ? 'AUSENTE_PNCP' : l.status;
      if (s) { p.set('status_pncp', s); }
      if (l.entidade) { p.set('entidade', l.entidade); }
      if (l.rem) { p.set('remanescentes', '1'); }
    }
    if (l.q) { p.set('q', l.q); }
    return '/api/transparencia/export/csv?' + p.toString();
  }

  // ---- Sino de alertas: mesma lista do painel + botão de avisos do aparelho
  function abrirAlertas() {
    if (typeof window.renderNotifications === 'function') { try { window.renderNotifications(); } catch (e) { /* lista opcional */ } }
    var lista = $('notifications-list');
    var push = $('radar-push-btn');
    var html = '<div class="mt-card" style="padding:8px">' + (lista && lista.innerHTML.trim() ? lista.innerHTML : '<p class="mt-note" style="padding:12px">Sem alertas urgentes nos últimos 3 dias.</p>') + '</div>';
    if (push) {
      html += '<button type="button" class="mt-btn block" data-m="push">' + ic('notifications_active') + esc(push.textContent.trim() || 'Avisos no aparelho') + '</button>';
    }
    var layer = abrirSheet('Alertas', html);
    layer.querySelector('.mt-sheet-body').addEventListener('click', function (e) {
      if (e.target.closest('[data-m="push"]')) { var pb = $('radar-push-btn'); if (pb) { pb.click(); } return; }
      // Itens da lista abrem o dossiê da pauta (modal do painel): fecha o sheet antes.
      if (e.target.closest('button[onclick]')) { fecharTudo(); }
    }, true);
    var dot = $('mt-bell-dot');
    if (dot) { dot.hidden = true; }
  }
  function espelharSino() {
    var src = $('notif-dot');
    var dot = $('mt-bell-dot');
    if (!src || !dot || !('MutationObserver' in window)) { return; }
    var sync = function () { dot.hidden = src.classList.contains('hidden'); };
    sync();
    new MutationObserver(sync).observe(src, { attributes: true, attributeFilter: ['class'] });
  }

  // ---------------------------------------------------- atualizar / sincronizar
  var toastTimer = null;
  function toast(msg) {
    var t = $('mt-toast');
    if (!t) { return; }
    t.textContent = msg;
    t.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove('on'); }, 3600);
  }

  // Puxar para atualizar: recarrega os dados do servidor e pede a atualização automática
  // já existente (radar_auto.js: com token usa a rota protegida; sem token, a pública com
  // limite por IP; mesmas travas, intervalos e esperas do servidor). Não cria rota nova.
  async function atualizar() {
    if (typeof window.radarAutoAtualizar === 'function') {
      try { window.radarAutoAtualizar(); } catch (e) { /* a atualização automática tenta de novo sozinha */ }
    }
    await carregarTudo();
    if (st.resumoErr) { toast('Sem conexão: ' + st.resumoErr); return; }
    if (st.resumoFrom) { toast('Sem conexão: mostrando dados de ' + dataHora(st.resumoFrom)); return; }
    toast('Dados recarregados. O servidor sincroniza o que estiver vencido e a tela se atualiza ao terminar.');
  }

  function sincronizarFiorilli() {
    if (typeof window.triggerTransparenciaSync === 'function') {
      toast('Sincronizando Portal Fiorilli…');
      window.triggerTransparenciaSync();
    }
  }

  function ligarPullToRefresh() {
    var ind = $('mt-ptr');
    var y0 = null, dy = 0, puxando = false, ocupado = false;
    var LIM = 72;
    document.addEventListener('touchstart', function (e) {
      if (!st.active || ocupado || pilha.length || window.scrollY > 0) { y0 = null; return; }
      if (e.target.closest('.mt-carousel, .mt-years, .mt-nav, .mt-top')) { y0 = null; return; }
      y0 = e.touches[0].clientY; dy = 0; puxando = false;
    }, { passive: true });
    document.addEventListener('touchmove', function (e) {
      if (y0 === null) { return; }
      dy = e.touches[0].clientY - y0;
      if (dy <= 0 || window.scrollY > 0) { if (puxando) { ind.style.opacity = '0'; } puxando = false; return; }
      puxando = true;
      if (e.cancelable) { e.preventDefault(); }
      var d = Math.min(dy * 0.5, LIM + 20);
      ind.classList.remove('settle');
      ind.style.opacity = String(Math.min(1, d / LIM));
      ind.style.transform = 'translateY(' + d + 'px) rotate(' + (d * 4) + 'deg)';
    }, { passive: false });
    document.addEventListener('touchend', async function () {
      if (y0 === null) { return; }
      y0 = null;
      if (!puxando) { return; }
      puxando = false;
      ind.classList.add('settle');
      if (dy * 0.5 < LIM) { ind.style.opacity = '0'; ind.style.transform = ''; return; }
      ocupado = true;
      ind.style.transform = 'translateY(' + LIM + 'px)';
      ind.classList.add('go');
      try { await atualizar(); } finally {
        ind.classList.remove('go');
        ind.style.opacity = '0';
        ind.style.transform = '';
        ocupado = false;
      }
    });
  }

  // ------------------------------------------------------------- liga / desliga
  function vistaAberta() {
    var v = $('view-transparencia');
    return !!v && !v.classList.contains('hidden');
  }
  function deveAtivar() { return MQ.matches && vistaAberta(); }

  function sincronizarModo() {
    var on = deveAtivar();
    if (on === st.active) { return; }
    st.active = on;
    if (on) { build(); }
    document.body.classList.toggle('mt-on', on);
    aplicarTema();
    if (!on) { fecharTudo(); }
  }

  var carregarDesktop = window.loadTransparenciaView;
  window.loadTransparenciaView = function () {
    if (deveAtivar()) {
      sincronizarModo();
      return carregarTudo();
    }
    return typeof carregarDesktop === 'function' ? carregarDesktop.apply(this, arguments) : Promise.resolve();
  };

  var trocarVistaOriginal = window.switchMainView;
  if (typeof trocarVistaOriginal === 'function') {
    window.switchMainView = function () {
      var r = trocarVistaOriginal.apply(this, arguments);
      sincronizarModo();
      return r;
    };
  }

  function aoMudarTela() {
    var antes = st.active;
    sincronizarModo();
    if (antes === st.active || !vistaAberta()) { return; }
    // Passou do celular para o desktop (ou o contrário) com a aba aberta: carrega a versão certa.
    if (st.active) { carregarTudo(); } else if (typeof carregarDesktop === 'function') { carregarDesktop(); }
  }
  if (MQ.addEventListener) { MQ.addEventListener('change', aoMudarTela); } else if (MQ.addListener) { MQ.addListener(aoMudarTela); }
  if (window.matchMedia) {
    var esc0 = window.matchMedia('(prefers-color-scheme: dark)');
    var reTema = function () { if (tema() === 'sistema') { aplicarTema(); } };
    if (esc0.addEventListener) { esc0.addEventListener('change', reTema); } else if (esc0.addListener) { esc0.addListener(reTema); }
  }
  window.addEventListener('online', function () { if (st.active && (st.resumoFrom || st.resumoErr)) { carregarTudo(); } });

  // Atalho do app instalado: /?aba=transparencia abre direto nesta aba.
  function abrirPorLink() {
    var q = new URLSearchParams(window.location.search);
    if (q.get('aba') === 'transparencia' && typeof window.switchMainView === 'function') {
      window.switchMainView('transparencia', $('nav-btn-transparencia'));
    }
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', abrirPorLink); } else { abrirPorLink(); }

  // Exposto para testes manuais no console.
  window.radarTranspMobile = { ativo: function () { return st.active; }, recarregar: carregarTudo, irPara: irPara };
})();
