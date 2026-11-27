/*
 * Radar de Pautas: aba "Só o Feed de Pautas".
 * A aba reaproveita a view do painel (mesmas rotas /api/pautas e /api/stats, mesmos
 * filtros, arquivamento e dossiê) e só esconde o que não é feed (radar_feed.css,
 * classe body.feed-only ligada por switchMainView('feed')).
 * No celular (até 768px) ganha navegação inferior e "puxar para atualizar", que chama
 * radarAutoAtualizar (radar_auto.js, rota pública sem token) e recarrega as pautas.
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
    var y0 = null, dy = 0, puxando = false, ocupado = false;
    var LIM = 72;
    document.addEventListener('touchstart', function (e) {
      if (!noCelular() || ocupado || modalAberto() || window.scrollY > 0) { y0 = null; return; }
      if (e.target.closest('#rf-nav, #app-sidebar, #filter-buttons-bar, header')) { y0 = null; return; }
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
    abrirPorLink();
  }

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', iniciar); } else { iniciar(); }

  // Exposto para testes manuais no console.
  window.radarFeed = { ativo: ativo, atualizar: atualizar };
})();
