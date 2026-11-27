/*
 * Service worker do Radar de Pautas (app instalado na Tela de Início).
 * Só cuida das notificações push: não guarda páginas nem dados em cache,
 * então o painel sempre mostra o que está no servidor.
 */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

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
