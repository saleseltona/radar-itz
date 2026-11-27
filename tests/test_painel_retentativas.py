"""Painel: pausas passageiras (409, 502/503/504, queda de conexão) esperam e continuam sozinhas.

Executa trechos de static/stitch_dashboard.html e static/radar_auto.js no Node, sem navegador e sem rede.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any, List

STATIC = Path(__file__).resolve().parent.parent / "static"


def _extrair(fonte: str, nome: str, indent: str = "    ", prefixo: str = "function ") -> str:
    m = re.search(
        r"^" + indent + r"(?:async )?" + prefixo + re.escape(nome) + r"\(.*?\n" + indent + r"}\n",
        fonte,
        re.S | re.M,
    )
    if m is None:
        raise AssertionError(f"função {nome} não encontrada")
    return m.group(0)


def _const(fonte: str, nome: str) -> str:
    m = re.search(r"^\s*const " + nome + r" = .*?;\n", fonte, re.M)
    if m is None:
        raise AssertionError(f"constante {nome} não encontrada")
    return m.group(0)


def _node(script: str) -> Any:
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "node não instalado")
class TestPainelPncpRetentativas(unittest.TestCase):
    html = (STATIC / "stitch_dashboard.html").read_text(encoding="utf-8")

    def _base(self) -> str:
        partes = [
            _const(self.html, "PNCP_SYNC_STORAGE_KEY"),
            _const(self.html, "PNCP_SYNC_MAX_REQUESTS"),
            _const(self.html, "PNCP_SYNC_MAX_RETRIES"),
        ]
        for nome in ("pncpSyncRetryDelayMs", "describePncpHttpFailure", "parsePncpSyncResponse"):
            partes.append(_extrair(self.html, nome))
        return "\n".join(partes)

    def test_atrasos(self) -> None:
        script = self._base() + (
            "\nprocess.stdout.write(JSON.stringify(["
            "pncpSyncRetryDelayMs(409, 0), pncpSyncRetryDelayMs(504, 0), pncpSyncRetryDelayMs(502, 3),"
            "pncpSyncRetryDelayMs(0, 0), pncpSyncRetryDelayMs('pncp', 1), pncpSyncRetryDelayMs(500, 0),"
            "pncpSyncRetryDelayMs(400, 0), pncpSyncRetryDelayMs(409, PNCP_SYNC_MAX_RETRIES)]));"
        )
        self.assertEqual(_node(script), [20000, 15000, 30000, 15000, 20000, 0, 0, 0])

    def _simular(self, respostas: List[Any]) -> Any:
        """Roda triggerPncpSync com fetch simulado; cada resposta é [status, corpo] ou "rede" (queda)."""
        loop = _extrair(self.html, "triggerPncpSync")
        stubs = """
let pncpSyncRunning = false;
const _mem = {}; const localStorage = { getItem: (k) => _mem[k] || null, setItem: (k, v) => { _mem[k] = v; } };
const document = { getElementById: () => null, addEventListener: () => {} };
const labels = []; const alerts = []; const chamadas = [];
function setPncpSyncLabel(t) { labels.push(t); }
function refreshPncpSyncLabel() {}
function loadPncpSyncLocalState() { try { return JSON.parse(localStorage.getItem(PNCP_SYNC_STORAGE_KEY) || '{}'); } catch (_) { return {}; } }
function savePncpSyncLocalState(st) { localStorage.setItem(PNCP_SYNC_STORAGE_KEY, JSON.stringify(st)); }
function pncpSleep() { return Promise.resolve(); }
function alert(m) { alerts.push(m); }
async function loadPncpSummary() {} async function loadPautasAndStats() {} async function loadPncpModal() {}
"""
        script = (
            self._base() + stubs + f"const fila = {json.dumps(respostas)};\n"
            + """
async function pncpFetch(url, opts) {
  chamadas.push(JSON.parse(opts.body).proxima_janela);
  const r = fila.shift();
  if (r === undefined || r === 'rede') throw new TypeError('Failed to fetch');
  const [status, corpo] = r;
  return { status, ok: status >= 200 && status < 300, text: async () => (typeof corpo === 'string' ? corpo : JSON.stringify(corpo)) };
}
""" + loop + "\ntriggerPncpSync().then(() => process.stdout.write(JSON.stringify({ labels, alerts, chamadas, restantes: fila.length })));"
        )
        return _node(script)

    def test_409_e_504_esperam_e_continuam(self) -> None:
        r = self._simular([
            [200, {"parcial": True, "proxima_janela": "c1", "inseridos": 2, "progresso_pct": 30}],
            [409, {"erro": "Já existe uma sincronização em andamento.", "em_andamento": True}],
            [504, "<html>Gateway Timeout</html>"],
            "rede",
            [200, {"parcial": False, "inseridos": 1, "ultima_data_sincronizada": "2026-11-27"}],
        ])
        self.assertEqual(r["restantes"], 0)
        self.assertEqual(r["chamadas"], [None, "c1", "c1", "c1", "c1"])
        self.assertEqual(len(r["alerts"]), 1)
        self.assertIn("concluída", r["alerts"][0])
        self.assertIn("Novos: 3", r["alerts"][0])
        self.assertTrue(any("Sincronização em andamento" in t for t in r["labels"]))
        self.assertTrue(any("HTTP 504" in t for t in r["labels"]))

    def test_erro_do_pncp_no_lote_retoma_do_cursor(self) -> None:
        r = self._simular([
            [200, {"parcial": True, "proxima_janela": "c2", "erros": ["timeout"], "inseridos": 1}],
            [200, {"parcial": False, "atualizados": 4}],
        ])
        self.assertEqual(r["chamadas"], [None, "c2"])
        self.assertIn("concluída", r["alerts"][0])
        self.assertTrue(any("PNCP instável" in t for t in r["labels"]))

    def test_desiste_depois_do_limite(self) -> None:
        r = self._simular([[409, {"erro": "Ocupado"}]] * 10)
        self.assertEqual(len(r["chamadas"]), 7)  # 1 tentativa + 6 esperas
        self.assertIn("interrompida", r["alerts"][0])
        self.assertIn("Ocupado", r["alerts"][0])

    def test_erro_definitivo_nao_espera(self) -> None:
        r = self._simular([[400, {"erro": "Requisição inválida"}]])
        self.assertEqual(len(r["chamadas"]), 1)
        self.assertFalse(any("nova tentativa" in t for t in r["labels"]))
        self.assertIn("Requisição inválida", r["alerts"][0])


@unittest.skipUnless(shutil.which("node"), "node não instalado")
class TestRadarAutoRetentativas(unittest.TestCase):
    js = (STATIC / "radar_auto.js").read_text(encoding="utf-8")

    def test_atrasos(self) -> None:
        func = _extrair(self.js, "atrasoNovaTentativa", indent="  ")
        script = "var MAX_TENTATIVAS = 6;\n" + func + (
            "\nprocess.stdout.write(JSON.stringify([atrasoNovaTentativa(409, 0), atrasoNovaTentativa(504, 1),"
            " atrasoNovaTentativa(0, 0), atrasoNovaTentativa(500, 0), atrasoNovaTentativa(401, 0),"
            " atrasoNovaTentativa(503, 6)]));"
        )
        self.assertEqual(_node(script), [20000, 20000, 15000, 0, 0, 0])

    def test_acompanhar_nao_para_mais_em_erro(self) -> None:
        self.assertNotIn("if (!res.ok) { return; }", self.js)
        self.assertIn("tentarDeNovo(res.status, acompanhar)", self.js)
        self.assertIn("tentarDeNovo(res.status, disparar)", self.js)


if __name__ == "__main__":
    unittest.main()
