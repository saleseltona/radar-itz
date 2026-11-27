/*
 * Radar de Pautas de Imperatriz: atualização automática ao abrir o painel.
 *
 * A cada carregamento (inclusive F5), pede ao servidor para rodar em segundo plano
 * a Varredura (que regera o Clipping Diário), o PNCP e o Portal Fiorilli. O servidor
 * só roda o que estiver vencido (intervalo mínimo) e nunca empilha execuções, então
 * a página abre na hora com os dados atuais e se atualiza sozinha quando cada tarefa termina.
 * Sem token de administração salvo neste navegador, nada roda: aparece só um aviso.
 */
(function () {
  'use strict';
  var POLL_MS = 8000;
  var CHIP_ID = 'radar-auto-chip';
  var BOTOES = { varredura: 'btn-sync-manual', pncp: 'btn-sync-pncp', fiorilli: 'btn-tr-sync' };
  var concluidasVistas = {};
  var timer = null;

  function chip() {
    var el = document.getElementById(CHIP_ID);
    if (el) { return el; }
    var ref = document.getElementById('btn-sync-manual');
    if (!ref || !ref.parentNode) { return null; }
    el = document.createElement('span');
    el.id = CHIP_ID;
    el.className = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/10 text-white text-[12px] font-semibold';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    ref.parentNode.insertBefore(el, ref.parentNode.firstChild);
    return el;
  }

  function mostrar(texto, icone, girando, titulo, onclick) {
    var el = chip();
    if (!el) { return; }
    el.innerHTML = '';
    var ic = document.createElement('span');
    ic.className = 'material-symbols-outlined text-[15px]' + (girando ? ' animate-spin' : '');
    ic.textContent = icone;
    var tx = document.createElement('span');
    tx.textContent = texto;
    el.appendChild(ic);
    el.appendChild(tx);
    el.title = titulo || '';
    el.style.cursor = onclick ? 'pointer' : 'default';
    el.onclick = onclick || null;
  }

  function hora(iso) {
    if (!iso) { return ''; }
    try { return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; }
  }

  function palavrasChave() {
    try {
      if (typeof window.getSavedKeywords === 'function') { return window.getSavedKeywords(); }
    } catch (e) { /* sem palavras-chave salvas */ }
    return [];
  }

  function travarBotoes(st) {
    Object.keys(BOTOES).forEach(function (nome) {
      var btn = document.getElementById(BOTOES[nome]);
      if (!btn) { return; }
      var t = (st.tarefas || {})[nome] || {};
      var ocupado = t.situacao === 'rodando' || t.situacao === 'na_fila';
      if (ocupado) {
        btn.dataset.radarAuto = '1';
        btn.disabled = true;
        btn.title = 'Atualização automática em andamento';
      } else if (btn.dataset.radarAuto) {
        delete btn.dataset.radarAuto;
        btn.disabled = false;
        btn.title = '';
      }
    });
  }

  async function recarregar(nome) {
    try {
      if (nome === 'varredura' && typeof window.loadAllData === 'function') { await window.loadAllData(); }
      if (nome === 'pncp') {
        if (typeof window.loadPncpSummary === 'function') { await window.loadPncpSummary(); }
        if (typeof window.loadPautasAndStats === 'function') { await window.loadPautasAndStats(); }
      }
      if (nome === 'fiorilli' && typeof window.loadTransparenciaView === 'function') { await window.loadTransparenciaView(); }
    } catch (e) { /* o painel recarrega na próxima abertura */ }
  }

  function resumoTarefa(nome, t) {
    var r = t.resultado || {};
    if (nome === 'varredura') { return 'Varredura: ' + (r.pautas_novas || 0) + ' pautas novas'; }
    if (nome === 'pncp') {
      if (r.pulado) { return 'PNCP: ' + (r.motivo || 'pulado'); }
      return 'PNCP: ' + (r.inseridos || 0) + ' novos, ' + (r.atualizados || 0) + ' atualizados';
    }
    return 'Fiorilli: ' + (r.total_registros || 0) + ' registros';
  }

  function aplicar(st) {
    travarBotoes(st);
    var tarefas = st.tarefas || {};
    var linhas = [];
    var erros = [];
    Object.keys(tarefas).forEach(function (nome) {
      var t = tarefas[nome];
      if (t.ultima_conclusao) {
        linhas.push(t.ok ? resumoTarefa(nome, t) + ' (' + hora(t.ultima_conclusao) + ')' : t.rotulo + ': ' + (t.erro || 'falhou'));
        if (!t.ok) { erros.push(t.rotulo); }
        if (concluidasVistas[nome] !== undefined && concluidasVistas[nome] !== t.ultima_conclusao && t.ok) {
          recarregar(nome);
        }
        concluidasVistas[nome] = t.ultima_conclusao;
      } else if (concluidasVistas[nome] === undefined) {
        concluidasVistas[nome] = null;
      }
    });
    var titulo = linhas.join('\n');
    if (st.em_andamento) {
      var atual = st.tarefa_atual && tarefas[st.tarefa_atual] ? tarefas[st.tarefa_atual].rotulo : 'dados';
      var restantes = (st.fila || []).length;
      mostrar('Atualizando ' + atual + (restantes ? ' (+' + restantes + ')' : '') + '…', 'sync', true, titulo);
      return true;
    }
    var fim = st.rodada_fim || '';
    if (erros.length) {
      mostrar('Atualizado' + (fim ? ' às ' + hora(fim) : '') + ' • falhou: ' + erros.join(', '), 'warning', false, titulo);
    } else if (fim || linhas.length) {
      mostrar('Atualizado' + (fim ? ' às ' + hora(fim) : ''), 'check_circle', false, titulo || 'Dados em dia');
    } else {
      mostrar('Dados em dia', 'check_circle', false, titulo);
    }
    return false;
  }

  function agendar() {
    if (timer) { clearTimeout(timer); }
    timer = setTimeout(acompanhar, POLL_MS);
  }

  async function acompanhar() {
    try {
      var res = await fetch('/api/auto-atualizar', { cache: 'no-store' });
      if (!res.ok) { return; }
      if (aplicar(await res.json())) { agendar(); }
    } catch (e) { agendar(); }
  }

  function pedirToken() {
    var typed = window.prompt('Para atualizar tudo automaticamente ao abrir o painel, digite o token de administração do Radar (RADAR_ADMIN_TOKEN):');
    if (!typed || !typed.trim()) { return; }
    if (typeof window.radarSetAdminToken === 'function') { window.radarSetAdminToken(typed.trim()); }
    disparar();
  }

  async function disparar() {
    if (typeof window.radarHasAdminToken === 'function' && !window.radarHasAdminToken()) {
      mostrar('Atualização automática desligada: informe o token', 'key', false, 'Clique para informar o token de administração', pedirToken);
      return;
    }
    mostrar('Verificando atualizações…', 'sync', true, '');
    try {
      var res = await fetch('/api/auto-atualizar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ consultas_adicionais: palavrasChave() }),
        radarSemPrompt: true,
      });
      if (res.status === 401 || res.status === 403) {
        mostrar('Token recusado: atualização automática parada', 'key_off', false, 'Clique para informar o token de novo', pedirToken);
        return;
      }
      var st = await res.json();
      if (st.desabilitada) { mostrar('Atualização automática desligada no servidor', 'pause_circle', false, ''); return; }
      if (aplicar(st)) { agendar(); }
    } catch (e) {
      mostrar('Sem conexão para atualizar', 'cloud_off', false, '');
    }
  }

  window.radarAutoAtualizar = disparar;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', disparar);
  } else {
    disparar();
  }
})();
