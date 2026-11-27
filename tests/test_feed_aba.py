"""
Testes estáticos da aba "Só o Feed de Pautas" (static/radar_feed.*). Herméticos: só leem arquivos.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


class TestAbaFeed(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (STATIC / "stitch_dashboard.html").read_text(encoding="utf-8")
        self.js = (STATIC / "radar_feed.js").read_text(encoding="utf-8")
        self.css = (STATIC / "radar_feed.css").read_text(encoding="utf-8")

    def test_painel_tem_botao_e_carrega_arquivos_depois_do_script_principal(self) -> None:
        self.assertIn('id="nav-btn-feed"', self.html)
        self.assertIn("switchMainView('feed', this)", self.html)
        self.assertIn('href="/static/radar_feed.css', self.html)
        pos = self.html.find('src="/static/radar_feed.js')
        self.assertGreater(pos, self.html.find("function switchMainView"))
        self.assertGreater(pos, self.html.find('src="/static/radar_transp_mobile.js'))
        # A troca de aba liga/desliga a classe em todas as abas.
        self.assertIn("document.body.classList.toggle('feed-only', soFeed)", self.html)

    def test_ids_que_o_css_usa_existem_no_painel(self) -> None:
        for alvo in set(re.findall(r"#(sec-[a-z\-]+)", self.css)):
            self.assertIn(f'id="{alvo}"', self.html, alvo)
        self.assertIn('id="pautas-cards-container"', self.html)

    def test_regras_so_valem_com_a_classe_ligada(self) -> None:
        for regra in re.findall(r"([^{}]+)\{", self.css):
            sel = regra.strip()
            if not sel or sel.startswith("@") or sel in ("to",):
                continue
            for parte in sel.split(","):
                parte = parte.strip()
                if "#sec-" in parte or parte.startswith("main") or "#pautas-cards" in parte:
                    self.assertIn("body.feed-only", parte, parte)
        self.assertIn("@media (max-width: 768px)", self.css)
        self.assertIn("env(safe-area-inset-bottom", self.css)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_js_nao_inventa_rotas_e_reaproveita_atualizacao(self) -> None:
        self.assertEqual(re.findall(r"'/api/[^']*'", self.js), [])
        self.assertIn("radarAutoAtualizar", self.js)
        self.assertIn("loadPautasAndStats", self.js)
        self.assertIn("(max-width: 768px)", self.js)
        self.assertIn("q.get('aba') === 'feed'", self.js)

    def test_manifest_tem_atalho_do_feed(self) -> None:
        m = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
        urls = [s["url"] for s in m.get("shortcuts", [])]
        self.assertIn("/?aba=feed", urls)
        self.assertIn("/?aba=transparencia", urls)


if __name__ == "__main__":
    unittest.main()
