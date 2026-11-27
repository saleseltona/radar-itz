/*
 * Radar de Pautas: aba "Só o Feed de Pautas".
 * A aba reaproveita a view do painel (mesmas rotas /api/pautas e /api/stats, mesmos
 * filtros, arquivamento e dossiê) e só esconde o que não é feed (radar_feed.css,
 * classe body.feed-only ligada por switchMainView('feed')).
 * No celular (até 768px) ganha navegação inferior e "puxar para atualizar", que chama
 * radarAutoAtualizar (radar_auto.js, rota pública sem token) e recarrega as pautas.
 * Em qualquer aba do feed no celular, deslizar um cartão para a esquerda arquiva a pauta.
 * Atalho do app instalado: /?aba=feed.
 */
(function () {
  'use strict';

  var MQ = window.matchMedia ? window.matchMedia('(max-width: 768px)') : { matches: false };

  function $(id) { return document.getElementById(id); }
  function ativo() { return document.body.classList.contains('feed-only'); }
  function noCelular() { return ativo() && MQ.matches; }

  function clicar(id) {
    var b = $(id);
    if (b) { b.click(); }
  }

  // ---- Navegação inferior (só aparece no celular com a aba aberta; ver CSS)
  function montarNav() {
    if ($('rf-nav')) { return; }
    var itens = [
      ['feed', 'feed', 'Feed'],
      ['painel', 'dashboard', 'Painel'],
      ['transp', 'account_balance', 'Transparência'],
      ['menu', 'menu', 'Menu'],
    ];
    var nav = document.createElement('nav');
    nav.id = 'rf-nav';
    nav.className = 'rf-nav';
    nav.setAttribute('aria-label', 'Navegação do Radar');
    nav.innerHTML = itens.map(function (it) {
      return '<button type="button" data-rf="' + it[0] + '"' + (it[0] === 'feed' ? ' aria-current="page"' : '') + '>' +
        '<span class="material-symbols-outlined" aria-hidden="true">' + it[1] + '</span><span>' + it[2] + '</span></button>';
    }).join('');
    nav.addEventListener('click', function (e) {
      var b = e.target.closest('[data-rf]');
      if (!b) { return; }
      var k = b.dataset.rf;
      if (k === 'feed') { window.scrollTo({ top: 0, behavior: 'smooth' }); }
      else if (k === 'painel') { clicar('nav-btn-dashboard'); window.scrollTo({ top: 0 }); }
      else if (k === 'transp') { clicar('nav-btn-transparencia'); }
      else if (k === 'menu' && typeof window.toggleMobileSidebar === 'function') { window.toggleMobileSidebar(true); }
    });
    document.body.appendChild(nav);

    var ind = document.createElement('div');
    ind.id = 'rf-ptr';
    ind.className = 'rf-ptr';
    ind.setAttribute('aria-hidden', 'true');
    ind.innerHTML = '<span class="material-symbols-outlined">refresh</span>';
    document.body.appendChild(ind);
  }

  // ---- Atualizar: dispara a atualização automática e recarrega o feed na hora
  async function atualizar() {
    var tarefas = [];
    if (typeof window.radarAutoAtualizar === 'function') { tarefas.push(window.radarAutoAtualizar()); }
    if (typeof window.loadPautasAndStats === 'function') { tarefas.push(window.loadPautasAndStats()); }
    try { await Promise.all(tarefas); } catch (e) { /* o painel mostra o erro no próprio feed */ }
  }

  function modalAberto() {
    return ['stitch-modal', 'pncp-modal', 'fontes-modal', 'pendencias-modal'].some(function (id) {
      var m = $(id);
      return m && !m.classList.contains('hidden');
    });
  }

  function ligarPullToRefresh() {
    var ind = $('rf-ptr');
    var y0 = null, x0 = 0, dy = 0, puxando = false, ocupado = false;
    var LIM = 72;
    document.addEventListener('touchstart', function (e) {
      if (!noCelular() || ocupado || modalAberto() || window.scrollY > 0) { y0 = null; return; }
      if (e.target.closest('#rf-nav, #app-sidebar, #filter-buttons-bar, header')) { y0 = null; return; }
      y0 = e.touches[0].clientY; x0 = e.touches[0].clientX; dy = 0; puxando = false;
    }, { passive: true });
    document.addEventListener('touchmove', function (e) {
      if (y0 === null) { return; }
      dy = e.touches[0].clientY - y0;
      // Gesto de lado (deslizar para arquivar) não vira puxar para atualizar.
      if (!puxando && Math.abs(e.touches[0].clientX - x0) > Math.abs(dy)) { y0 = null; return; }
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

  // ---- Deslizar o cartão para a esquerda arquiva (celular, feed principal e aba Feed)
  function ligarDeslizarParaArquivar() {
    var box = $('pautas-cards-container');
    if (!box) { return; }
    var card = null, x0 = 0, y0 = 0, dx = 0, modo = '', fundo = null, arrastou = 0;
    var LIM = 0.35;

    function limpar(voltar) {
      if (!card) { return; }
      var c = card, f = fundo;
      card = null; fundo = null; modo = '';
      c.classList.remove('rf-arrastando');
      if (voltar) {
        c.classList.add('rf-voltando');
        c.style.transform = '';
        setTimeout(function () {
          c.classList.remove('rf-voltando');
          if (f && f.parentNode) { var w = f.parentNode; w.parentNode.insertBefore(c, w); w.remove(); }
        }, 190);
      }
    }

    box.addEventListener('touchstart', function (e) {
      if (!MQ.matches || e.touches.length !== 1) { return; }
      var a = e.target.closest('article[data-pauta-id]');
      if (!a || a.dataset.arquivada || e.target.closest('button, a')) { return; }
      card = a; x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = 0; modo = '';
    }, { passive: true });

    box.addEventListener('touchmove', function (e) {
      if (!card) { return; }
      var mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
      if (!modo) {
        if (Math.abs(mx) < 10 && Math.abs(my) < 10) { return; }
        modo = (Math.abs(mx) > Math.abs(my) * 1.3 && mx < 0) ? 'h' : 'v';
        if (modo === 'v') { card = null; return; }
        var wrap = document.createElement('div');
        wrap.className = 'rf-swipe-wrap';
        fundo = document.createElement('div');
        fundo.className = 'rf-swipe-fundo';
        fundo.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">archive</span><span>Arquivar</span>';
        card.parentNode.insertBefore(wrap, card);
        wrap.appendChild(fundo);
        wrap.appendChild(card);
        card.classList.add('rf-arrastando');
      }
      if (e.cancelable) { e.preventDefault(); }
      dx = Math.min(0, mx);
      card.style.transform = 'translateX(' + dx + 'px)';
      fundo.classList.toggle('pronto', -dx > card.offsetWidth * LIM);
    }, { passive: false });

    function soltar() {
      if (!card) { return; }
      if (modo !== 'h') { card = null; return; }
      arrastou = Date.now();
      var c = card;
      if (-dx > c.offsetWidth * LIM && typeof window.arquivarPauta === 'function') {
        c.classList.remove('rf-arrastando');
        c.classList.add('rf-voltando');
        c.style.transform = 'translateX(-110%)';
        card = null; fundo = null; modo = '';
        var id = c.dataset.pautaId;
        setTimeout(function () { window.arquivarPauta(id); }, 150);
      } else {
        limpar(true);
      }
    }
    box.addEventListener('touchend', soltar);
    box.addEventListener('touchcancel', function () { limpar(true); });
    // Depois de arrastar, o toque não abre o dossiê.
    box.addEventListener('click', function (e) {
      if (Date.now() - arrastou < 400) { e.stopPropagation(); e.preventDefault(); }
    }, true);
  }

  // ---- Atalho do app instalado: /?aba=feed abre direto nesta aba.
  function abrirPorLink() {
    var q = new URLSearchParams(window.location.search);
    if (q.get('aba') === 'feed' && typeof window.switchMainView === 'function') {
      window.switchMainView('feed', $('nav-btn-feed'));
    }
  }

  function iniciar() {
    montarNav();
    ligarPullToRefresh();
    ligarDeslizarParaArquivar();
    abrirPorLink();
  }

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', iniciar); } else { iniciar(); }

  // Exposto para testes manuais no console.
  window.radarFeed = { ativo: ativo, atualizar: atualizar };
})();
