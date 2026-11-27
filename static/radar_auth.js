/*
 * Radar de Pautas de Imperatriz: envia o token de administração nas chamadas à API.
 *
 * As rotas que gravam dados exigem o header X-Radar-Token quando o servidor
 * define RADAR_ADMIN_TOKEN. O token digitado fica só no localStorage deste navegador.
 */
(function () {
  'use strict';
  var TOKEN_KEY = 'radar_admin_token';
  var nativeFetch = window.fetch.bind(window);

  function readToken() {
    try { return window.localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }

  function saveToken(token) {
    try {
      if (token) { window.localStorage.setItem(TOKEN_KEY, token); }
      else { window.localStorage.removeItem(TOKEN_KEY); }
    } catch (e) { /* armazenamento indisponível: o token vale só nesta chamada */ }
  }

  function isSameOriginApi(url) {
    try {
      var parsed = new URL(url, window.location.href);
      return parsed.origin === window.location.origin && parsed.pathname.indexOf('/api/') === 0;
    } catch (e) {
      return false;
    }
  }

  function withToken(options, token) {
    var opts = Object.assign({}, options || {});
    var headers = new Headers(opts.headers || {});
    if (token) { headers.set('X-Radar-Token', token); }
    opts.headers = headers;
    return opts;
  }

  window.radarClearAdminToken = function () { saveToken(''); };
  window.radarHasAdminToken = function () { return !!readToken(); };
  window.radarSetAdminToken = function (token) { saveToken(String(token || '').trim()); };

  window.fetch = function (input, options) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!isSameOriginApi(url)) { return nativeFetch(input, options); }
    return nativeFetch(input, withToken(options, readToken())).then(function (res) {
      if (res.status !== 401) { return res; }
      // Chamadas automáticas (ex.: atualização ao abrir o painel) nunca abrem a janela do token.
      if (options && options.radarSemPrompt) { return res; }
      var typed = window.prompt('Esta ação exige o token de administração do Radar (RADAR_ADMIN_TOKEN). Digite o token:');
      if (!typed || !typed.trim()) { return res; }
      saveToken(typed.trim());
      return nativeFetch(input, withToken(options, typed.trim()));
    });
  };
})();
