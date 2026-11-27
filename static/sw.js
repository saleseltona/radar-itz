/*
 * Service worker do Radar de Pautas (app instalado na Tela de Início).
 *
 * Notificações push e um cache básico para abrir sem internet:
 * - páginas (/ e /dashboard) e arquivos de /static/: rede primeiro; o cache só
 *   entra quando não há conexão, então com internet o painel mostra sempre o servidor;
 * - Tailwind e fontes (CDN): usa o que já foi baixado e renova em segundo plano.
 * As respostas da API não passam por aqui: o último carregamento da aba
 * Transparência fica no próprio aparelho (radar_transp_mobile.js), com "dados de [data/hora]".
 */
var CACHE = 'radar-shell-v1';
var CDN = ['https://cdn.tailwindcss.com', 'https://fonts.googleapis.com', 'https://fonts.gstatic.com'];

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (nomes) {
      return Promise.all(nomes.filter(function (n) { return n.indexOf('radar-shell-') === 0 && n !== CACHE; })
        .map(function (n) { return caches.delete(n); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function guardar(req, res) {
  if (res && (res.ok || res.type === 'opaque')) {
    var copia = res.clone();
    caches.open(CACHE).then(function (c) { c.put(req, copia); });
  }
  return res;
}

function redePrimeiro(req, chave) {
  return fetch(req).then(function (res) { return guardar(chave || req, res); }).catch(function () {
    return caches.match(chave || req).then(function (r) { return r || Response.error(); });
  });
}

self.addEventListener('fetch', function (event) {
  var req = event.request;
  if (req.method !== 'GET') { return; }
  var url = new URL(req.url);
  if (url.origin === self.location.origin) {
    if (url.pathname.indexOf('/api/') === 0) { return; }
    if (req.mode === 'navigate' && (url.pathname === '/' || url.pathname === '/dashboard')) {
      // Uma cópia só da página, qualquer que seja a query (?aba=transparencia).
      event.respondWith(redePrimeiro(req, new Request(url.origin + url.pathname)));
      return;
    }
    if (url.pathname.indexOf('/static/') === 0 || url.pathname === '/manifest.webmanifest' || url.pathname === '/apple-touch-icon.png') {
      event.respondWith(redePrimeiro(req));
    }
    return;
  }
  if (CDN.some(function (o) { return req.url.indexOf(o) === 0; })) {
    event.respondWith(caches.match(req).then(function (hit) {
      var rede = fetch(req).then(function (res) { return guardar(req, res); }).catch(function () { return hit || Response.error(); });
      return hit || rede;
    }));
  }
});

self.addEventListener('push', function (event) {
  var dados = {};
  try { dados = event.data ? event.data.json() : {}; } catch (e) { dados = { body: event.data ? event.data.text() : '' }; }
  var titulo = dados.title || 'Radar de Pautas';
  var opcoes = {
    body: dados.body || 'Há novidades no Radar.',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: dados.tag || 'radar',
    renotify: true,
    data: { url: dados.url || '/' }
  };
  event.waitUntil(self.registration.showNotification(titulo, opcoes));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var destino = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (janelas) {
      for (var i = 0; i < janelas.length; i++) {
        var j = janelas[i];
        if (new URL(j.url).origin === self.location.origin && 'focus' in j) {
          if ('navigate' in j) { j.navigate(destino); }
          return j.focus();
        }
      }
      return self.clients.openWindow(destino);
    })
  );
});
