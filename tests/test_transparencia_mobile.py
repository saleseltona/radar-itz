"""
Testes estáticos do layout mobile da aba Transparência (static/radar_transp_mobile.*)
e do cache offline do service worker. Herméticos: só leem arquivos.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


class TestLayoutMobileTransparencia(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (STATIC / "stitch_dashboard.html").read_text(encoding="utf-8")
        self.js = (STATIC / "radar_transp_mobile.js").read_text(encoding="utf-8")
        self.css = (STATIC / "radar_transp_mobile.css").read_text(encoding="utf-8")

    def test_painel_carrega_css_e_js_depois_do_script_principal(self) -> None:
        self.assertIn('href="/static/radar_transp_mobile.css', self.html)
        pos_js = self.html.find('src="/static/radar_transp_mobile.js')
        self.assertGreater(pos_js, 0)
        # Precisa rodar depois do script inline que define loadTransparenciaView/switchMainView.
        self.assertGreater(pos_js, self.html.find("function loadTransparenciaView"))
        self.assertIn('id="app-main-wrap"', self.html)
        self.assertIn("viewport-fit=cover", self.html)

    def test_css_so_vale_ate_768px_com_classe_ligada(self) -> None:
        # Toda regra que esconde o painel original fica dentro do media query de celular.
        bloco = re.search(r"@media \(max-width: 768px\) \{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(bloco)
        self.assertIn("body.mt-on #app-main-wrap", bloco.group(1))
        fora = self.css.replace(bloco.group(0), "")
        self.assertNotIn("#app-main-wrap", fora)
        self.assertNotIn("#app-sidebar", fora)
        self.assertIn("#mt-app { display: none; }", fora)
        self.assertIn("env(safe-area-inset-bottom", self.css)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_js_usa_rotas_existentes_e_nao_inventa_endpoints(self) -> None:
        rotas = set(re.findall(r"'(/api/[a-z\-/]+)", self.js))
        self.assertTrue(rotas)
        permitidas = {
            "/api/transparencia/resumo",
            "/api/transparencia/cruzamento",
            "/api/transparencia/dispensas",
            "/api/transparencia/export/csv",
        }
        self.assertTrue(rotas <= permitidas, rotas - permitidas)
        self.assertIn("(max-width: 768px)", self.js)
        # Sincronização reaproveita os fluxos protegidos do painel.
        self.assertIn("radarAutoAtualizar", self.js)
        self.assertIn("triggerTransparenciaSync", self.js)

    def test_nao_localizado_nunca_vira_irregular(self) -> None:
        # A única menção a irregularidade é a negação exibida na aba Pendências.
        self.assertIn("nem a irregularidade", self.js)
        self.assertNotIn("irregular", self.js.lower().replace("nem a irregularidade", ""))
        # Ausente com consulta inconclusiva fica cinza (inconclusivo), nunca vermelho.
        self.assertIn("if (r.status_pncp === 'AUSENTE_PNCP') { return conclusiva ? 'rev' : 'inc'; }", self.js)


class TestServiceWorkerOffline(unittest.TestCase):
    def test_sw_guarda_pagina_e_estaticos_mas_nao_a_api(self) -> None:
        sw = (STATIC / "sw.js").read_text(encoding="utf-8")
        self.assertIn("addEventListener('fetch'", sw)
        self.assertIn("url.pathname.indexOf('/api/') === 0) { return; }", sw)
        self.assertIn("req.method !== 'GET'", sw)
        # Notificações push continuam.
        self.assertIn("addEventListener('push'", sw)
        self.assertIn("addEventListener('notificationclick'", sw)

    def test_manifest_instalavel(self) -> None:
        m = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(m["display"], "standalone")
        self.assertTrue(m["theme_color"])
        self.assertTrue(any(s["url"] == "/?aba=transparencia" for s in m.get("shortcuts", [])))


if __name__ == "__main__":
    unittest.main()
