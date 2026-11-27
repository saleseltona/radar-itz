/*
 * Radar de Pautas de Imperatriz: app instalável e notificações push.
 *
 * Registra o service worker (/sw.js) e põe um botão de sino no topo do painel:
 * - no iPhone fora do app instalado: explica como "Adicionar à Tela de Início";
 * - no app (ou navegador compatível): pede permissão e inscreve este aparelho.
 * O servidor envia um aviso por rodada de coleta quando chegam pautas ou contratos novos.
 */
(function () {
  'use strict';
  var BTN_ID = 'radar-push-btn';
  var PAINEL_ID = 'radar-push-painel';
  var chavePublica = null;
  var configurado = null;
  var registro = null;
  var inscrito = false; // guardado para o toque decidir sem esperar (gesto do usuário no iOS)

  var ehIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var instalado = window.matchMedia && window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;
  var suportaPush = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  function b64ParaBytes(b64) {
    var pad = '='.repeat((4 - (b64.length % 4)) % 4);
    var bruto = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(bruto.length);
    for (var i = 0; i < bruto.length; i++) { out[i] = bruto.charCodeAt(i); }
    return out;
  }

  function botao() {
    var el = document.getElementById(BTN_ID);
    if (el) { return el; }
    var ref = document.getElementById('btn-sync-manual');
    if (!ref || !ref.parentNode) { return null; }
    el = document.createElement('button');
    el.id = BTN_ID;
    el.type = 'button';
    el.className = 'inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-white/15 hover:bg-white/25 text-white font-label-md text-label-md font-semibold transition-all';
    el.onclick = aoClicar;
    ref.parentNode.insertBefore(el, ref);
    return el;
  }

  function desenhar(icone, texto, titulo) {
    var el = botao();
    if (!el) { return; }
    el.innerHTML = '';
    var ic = document.createElement('span');
    ic.className = 'material-symbols-outlined text-[17px]';
    ic.textContent = icone;
    var tx = document.createElement('span');
    tx.textContent = texto;
    el.appendChild(ic);
    el.appendChild(tx);
    el.title = titulo || '';
  }

  function painel(html) {
    fecharPainel();
    var p = document.createElement('div');
    p.id = PAINEL_ID;
    p.setAttribute('role', 'dialog');
    p.style.cssText = 'position:fixed;left:16px;right:16px;bottom:16px;max-width:420px;margin:0 auto;z-index:9999;' +
      'background:#fff;color:#191c1e;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.25);padding:16px 18px;' +
      'font:14px/1.45 "Plus Jakarta Sans",system-ui,sans-serif';
    p.innerHTML = html + '<div style="text-align:right;margin-top:10px">' +
      '<button type="button" data-acao="fechar" style="padding:8px 14px;border-radius:999px;background:#edeef1;font-weight:600">Fechar</button></div>';
    p.addEventListener('click', function (ev) {
      var acao = ev.target && ev.target.getAttribute('data-acao');
      if (acao === 'fechar') { fecharPainel(); }
      if (acao === 'testar') { testar(); }
      if (acao === 'desligar') { desligar(); }
    });
    document.body.appendChild(p);
  }

  function fecharPainel() {
    var p = document.getElementById(PAINEL_ID);
    if (p) { p.parentNode.removeChild(p); }
  }

  function mostrarInstrucoesIOS() {
    painel('<strong style="font-size:15px">Instale o Radar no iPhone</strong>' +
      '<ol style="margin:8px 0 0 18px;padding:0;list-style:decimal">' +
      '<li>No Safari, toque em <b>Compartilhar</b> (quadrado com seta para cima).</li>' +
      '<li>Escolha <b>Adicionar à Tela de Início</b> e toque em <b>Adicionar</b>.</li>' +
      '<li>Abra o <b>Radar Itz</b> pelo ícone novo e toque no sino para ativar as notificações.</li>' +
      '</ol><p style="margin-top:8px;color:#565e74">Requer iOS 16.4 ou mais recente.</p>');
  }

  function atualizarEstado() {
    if (!suportaPush) {
      if (ehIOS && !instalado) { desenhar('install_mobile', 'Instalar app', 'Como instalar o Radar no iPhone'); }
      else { desenhar('notifications_off', 'Sem notificações', 'Este navegador não suporta notificações push'); }
      return Promise.resolve();
    }
    if (configurado === false) {
      desenhar('notifications_off', 'Notificações', 'Notificações ainda não configuradas no servidor');
      return Promise.resolve();
    }
    if (Notification.permission === 'denied') {
      desenhar('notifications_off', 'Notificações bloqueadas', 'Libere em Ajustes > Notificações > Radar Itz');
      return Promise.resolve();
    }
    return pegarInscricao().then(function (insc) {
      inscrito = !!insc;
      if (insc && Notification.permission === 'granted') {
        desenhar('notifications_active', 'Avisos ligados', 'Você recebe avisos de pautas e contratos novos');
      } else {
        desenhar('notifications', 'Ativar avisos', 'Receber notificação de pautas e contratos novos');
      }
    });
  }

  function pegarInscricao() {
    if (!registro) { return Promise.resolve(null); }
    return registro.pushManager.getSubscription().catch(function () { return null; });
  }

  function postar(rota, corpo) {
    return fetch(rota, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(corpo || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { throw new Error(j.erro || ('HTTP ' + r.status)); }
        return j;
      });
    });
  }

  function ativar() {
    // No iOS a permissão precisa ser pedida direto no toque, antes de qualquer espera.
    return Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') { throw new Error('Permissão de notificação não concedida.'); }
      if (!registro || !chavePublica) { throw new Error('Servidor de notificações indisponível. Tente de novo.'); }
      return registro.pushManager.getSubscription().then(function (atual) {
        return atual || registro.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: b64ParaBytes(chavePublica)
        });
      });
    }).then(function (insc) {
      return postar('/api/push/inscrever', { inscricao: insc.toJSON() });
    }).then(function () {
      return postar('/api/push/testar', {});
    }).then(function () {
      painel('<strong>Notificações ligadas.</strong><p style="margin-top:6px">Enviamos um aviso de teste. ' +
        'A partir de agora você recebe um aviso quando chegarem pautas novas ou contratos novos do PNCP.</p>');
    });
  }

  function testar() {
    postar('/api/push/testar', {}).then(function (r) {
      painel(r.enviadas ? '<strong>Teste enviado.</strong> Deve chegar em alguns segundos.' :
        '<strong>Nenhum aparelho recebeu.</strong> Desligue e ative os avisos de novo.');
    }).catch(function (e) { painel('<strong>Falhou:</strong> ' + escapar(e.message)); });
  }

  function desligar() {
    pegarInscricao().then(function (insc) {
      if (!insc) { return null; }
      var endpoint = insc.endpoint;
      return insc.unsubscribe().then(function () { return postar('/api/push/cancelar', { endpoint: endpoint }); });
    }).then(function () {
      painel('Avisos desligados neste aparelho.');
      atualizarEstado();
    }).catch(function (e) { painel('<strong>Falhou:</strong> ' + escapar(e.message)); });
  }

  function escapar(t) {
    var d = document.createElement('div');
    d.textContent = String(t || '');
    return d.innerHTML;
  }

  function aoClicar() {
    if (!suportaPush) {
      if (ehIOS && !instalado) { mostrarInstrucoesIOS(); }
      else { painel('Este navegador não recebe notificações push. No iPhone, instale o app pela Tela de Início (iOS 16.4+).'); }
      return;
    }
    if (configurado === false) {
      painel('As notificações ainda não foram configuradas no servidor (chave RADAR_VAPID_PRIVATE_KEY).');
      return;
    }
    if (Notification.permission === 'denied') {
      painel('As notificações estão bloqueadas. No iPhone: <b>Ajustes &gt; Notificações &gt; Radar Itz</b> e ative <b>Permitir Notificações</b>.');
      return;
    }
    if (inscrito && Notification.permission === 'granted') {
      painel('<strong>Avisos ligados neste aparelho.</strong>' +
        '<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">' +
        '<button type="button" data-acao="testar" style="padding:8px 14px;border-radius:999px;background:#004deb;color:#fff;font-weight:600">Enviar teste</button>' +
        '<button type="button" data-acao="desligar" style="padding:8px 14px;border-radius:999px;background:#ffdad6;color:#93000a;font-weight:600">Desligar avisos</button></div>');
      return;
    }
    desenhar('hourglass_top', 'Ativando…', '');
    ativar().catch(function (e) {
      painel('<strong>Não deu para ativar:</strong> ' + escapar(e.message));
    }).then(atualizarEstado);
  }

  function iniciar() {
    atualizarEstado();
    if (!('serviceWorker' in navigator)) { return; }
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function (reg) {
      registro = reg;
      return fetch('/api/push/chave-publica', { cache: 'no-store' }).then(function (r) { return r.json(); });
    }).then(function (j) {
      configurado = !!(j && j.configurado);
      chavePublica = j && j.chave_publica;
      return atualizarEstado();
    }).catch(function () { /* sem service worker: o painel segue normal */ });
  }

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', iniciar); }
  else { iniciar(); }
})();
