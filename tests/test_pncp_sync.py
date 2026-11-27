"""
Testes da sincronização PNCP em lotes (src/pncp_sync.py + /api/pncp/sync).
Isolados: sem rede (PNCP simulado por fixtures), banco SQLite em diretório temporário.
"""
from __future__ import annotations

import email.message
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest import mock

from src.database import RadarDatabase
from src.pncp_client import PncpClient
from src.pncp_sync import SyncEmAndamento, executar_lote

HOJE = date(2025, 3, 15)  # plano curto: jan–mar/2025 × 2 órgãos = 6 janelas
ORGAOS = [
    {"cnpj": "06158455000116", "sigla": "PMI", "nome": "Prefeitura Municipal de Imperatriz"},
    {"cnpj": "00939023000166", "sigla": "FMS", "nome": "Fundo Municipal de Saúde"},
]


def raw_contrato(cnpj: str, seq: int, ano: int = 2025, valor: float = 1000.0, objeto: str = "Objeto") -> Dict[str, Any]:
    return {
        "numeroControlePNCP": f"{cnpj}-2-{seq:06d}/{ano}",
        "anoContrato": ano,
        "sequencialContrato": seq,
        "orgaoEntidade": {"cnpj": cnpj, "razaoSocial": "Órgão"},
        "unidadeOrgao": {"nomeUnidade": "Unidade"},
        "niFornecedor": "12345678000199",
        "nomeRazaoSocialFornecedor": "Fornecedor LTDA",
        "objetoContrato": objeto,
        "valorInicial": valor,
        "valorGlobal": valor,
        "dataPublicacaoPncp": f"{ano}-01-10T10:00:00",
        "dataAssinatura": f"{ano}-01-05",
    }


class FakePncp:
    """PNCP simulado: dados por (cnpj, data_inicial) com paginação; registra chamadas."""

    def __init__(self) -> None:
        self.pages: Dict[Tuple[str, str], List[List[Dict[str, Any]]]] = {}
        self.calls: List[Tuple[str, str, str, int]] = []
        self.fail_on: Dict[Tuple[str, str, int], Exception] = {}

    def add(self, cnpj: str, data_ini: str, pages: List[List[Dict[str, Any]]]) -> None:
        self.pages[(cnpj, data_ini)] = pages

    def __call__(self, endpoint, cnpj, data_ini, data_fim, pagina, tamanho, timeout):
        self.calls.append((endpoint, cnpj, data_ini, pagina))
        exc = self.fail_on.get((cnpj, data_ini, pagina))
        if exc is not None:
            raise exc
        pages = self.pages.get((cnpj, data_ini), [])
        if not pages:
            return 204, {"data": [], "totalPaginas": 0}
        return 200, {"data": pages[pagina - 1], "totalPaginas": len(pages)}


class TestPncpSyncLotes(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.tmp.name) / "pncp_test.db")
        self.fake = FakePncp()
        pmi = ORGAOS[0]["cnpj"]
        fms = ORGAOS[1]["cnpj"]
        self.fake.add(pmi, "20250101", [[raw_contrato(pmi, 1), raw_contrato(pmi, 2)], [raw_contrato(pmi, 3)]])
        self.fake.add(fms, "20250201", [[raw_contrato(fms, 10)]])
        self.fake.add(pmi, "20250301", [[raw_contrato(pmi, 4)]])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _lote(self, **kw):
        kw.setdefault("fetch", self.fake)
        kw.setdefault("hoje", HOJE)
        kw.setdefault("orgaos", ORGAOS)
        return executar_lote(self.db, PncpClient.normalize_contrato, **kw)

    def _count(self) -> int:
        with self.db.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM pncp_contratos").fetchone()[0]

    def _ate_concluir(self, **kw) -> List[Dict[str, Any]]:
        out = [self._lote(**kw)]
        while out[-1]["parcial"]:
            out.append(self._lote(cursor=out[-1]["proxima_janela"], **kw))
            self.assertLess(len(out), 20)
        return out

    def test_sync_parcial_retoma_da_proxima_janela(self) -> None:
        r1 = self._lote(max_janelas=2)
        self.assertTrue(r1["parcial"])
        self.assertEqual(r1["modo"], "carga_completa")
        self.assertEqual(r1["janelas_concluidas"], 2)
        self.assertEqual(r1["proxima_janela"]["janela"], 2)
        self.assertEqual(r1["inseridos"], 3)
        self.assertFalse(r1["carga_completa_concluida"])
        self.assertIsNone(r1["ultima_data_sincronizada"])

        chamadas_antes = len(self.fake.calls)
        r2 = self._lote(cursor=r1["proxima_janela"], max_janelas=2)
        # Retoma exatamente na 3ª janela (fev/PMI), sem repetir as já concluídas
        self.assertEqual(self.fake.calls[chamadas_antes][1:3], (ORGAOS[0]["cnpj"], "20250201"))
        self.assertTrue(r2["parcial"])
        r3 = self._lote(cursor=r2["proxima_janela"], max_janelas=2)
        self.assertFalse(r3["parcial"])
        self.assertIsNone(r3["proxima_janela"])
        self.assertTrue(r3["carga_completa_concluida"])
        self.assertEqual(r3["ultima_data_sincronizada"], "2025-03-15")
        self.assertEqual(self._count(), 5)

    def test_cursor_pendente_salvo_no_banco_permite_continuar_sem_cursor_do_cliente(self) -> None:
        r1 = self._lote(max_janelas=1)
        self.assertTrue(r1["parcial"])
        r2 = self._lote(max_janelas=1)  # sem cursor: usa o cursor_pendente persistido
        self.assertEqual(r2["janelas_concluidas"], 2)

    def test_pagina_retomada_quando_tempo_acaba_no_meio_da_janela(self) -> None:
        relogio = iter([0.0, 0.0, 30.0, 30.0, 30.0])
        r1 = self._lote(relogio=lambda: next(relogio), tempo_limite=40.0, http_timeout=15)
        self.assertTrue(r1["parcial"])
        self.assertEqual(r1["motivo_pausa"], "limite_tempo")
        self.assertEqual(r1["proxima_janela"]["janela"], 0)
        self.assertEqual(r1["proxima_janela"]["pagina"], 2)
        r2 = self._lote(cursor=r1["proxima_janela"])
        self.assertEqual(self.fake.calls[1][2:], ("20250101", 2))
        self.assertFalse(r2["parcial"])
        self.assertEqual(self._count(), 5)

    def test_segunda_execucao_nao_duplica(self) -> None:
        self._ate_concluir()
        self.assertEqual(self._count(), 5)
        # Repete a carga completa inteira: tudo já existe e nada muda
        res = self._ate_concluir(modo="carga_completa")
        self.assertEqual(self._count(), 5)
        self.assertEqual(sum(r["inseridos"] for r in res), 0)
        self.assertEqual(sum(r["atualizados"] for r in res), 0)
        self.assertEqual(sum(r["ignorados"] for r in res), 5)

    def test_incremental_apos_carga_usa_margem_e_endpoint_de_atualizacao(self) -> None:
        self._ate_concluir()
        res = self._lote(hoje=date(2025, 3, 20))
        self.assertEqual(res["modo"], "incremental")
        self.assertEqual(res["periodo"], {"inicio": "2025-03-10", "fim": "2025-03-20"})  # 15/03 − 5 dias
        self.assertEqual(res["endpoint"], "atualizacao")
        self.assertTrue(self.fake.calls[-1][0].endswith("/contratos/atualizacao"))

    def test_incremental_cai_para_publicacao_se_endpoint_atualizacao_nao_existe(self) -> None:
        self._ate_concluir()

        def fetch(endpoint, *a):
            if endpoint.endswith("/atualizacao"):
                raise urllib.error.HTTPError(endpoint, 404, "Not Found", None, None)
            return self.fake(endpoint, *a)

        res = self._lote(hoje=date(2025, 3, 20), fetch=fetch)
        self.assertEqual(res["endpoint"], "publicacao")
        self.assertFalse(res["erros"])

    def test_contrato_alterado_atualiza_e_retificacao_manual_preservada(self) -> None:
        pmi = ORGAOS[0]["cnpj"]
        self._ate_concluir()
        ctrl_ret = f"{pmi}-2-000001/2025"
        ctrl_alt = f"{pmi}-2-000002/2025"
        self.assertIsNotNone(self.db.retificar_pncp_contrato(ctrl_ret, 50.0, "Erro de digitação"))

        # PNCP muda: contrato 2 tem novo objeto; contrato 1 (retificado) muda o valor no PNCP
        self.fake.add(pmi, "20250101", [[
            raw_contrato(pmi, 1, valor=2000.0),
            raw_contrato(pmi, 2, objeto="Objeto aditivado"),
        ], [raw_contrato(pmi, 3)]])
        res = self._ate_concluir(modo="carga_completa")
        self.assertEqual(sum(r["atualizados"] for r in res), 1)
        self.assertEqual(sum(r["inseridos"] for r in res), 0)
        div = [d for r in res for d in r["retificacoes_divergentes"]]
        self.assertEqual(div[0]["numero_controle_pncp"], ctrl_ret)
        self.assertEqual(div[0]["valor_original_registrado"], 1000.0)
        self.assertEqual(div[0]["valor_pncp_atual"], 2000.0)

        with self.db.connect() as conn:
            alt = conn.execute("SELECT objeto FROM pncp_contratos WHERE numero_controle_pncp=?", (ctrl_alt,)).fetchone()
            ret = conn.execute("SELECT valor_global FROM pncp_contratos WHERE numero_controle_pncp=?", (ctrl_ret,)).fetchone()
            r = conn.execute("SELECT valor_original_pncp, valor_corrigido FROM pncp_retificacoes WHERE numero_controle_pncp=?", (ctrl_ret,)).fetchone()
        self.assertEqual(alt["objeto"], "Objeto aditivado")
        self.assertEqual(ret["valor_global"], 50.0)            # correção manual mantida
        self.assertEqual(r["valor_original_pncp"], 1000.0)     # original separado, intacto
        self.assertEqual(r["valor_corrigido"], 50.0)

    def test_erro_do_pncp_para_sem_avancar_cursor(self) -> None:
        pmi = ORGAOS[0]["cnpj"]
        self.fake.fail_on[(pmi, "20250101", 2)] = TimeoutError("timed out")
        r1 = self._lote()
        self.assertTrue(r1["parcial"])
        self.assertFalse(r1["ok"])
        self.assertEqual(r1["motivo_pausa"], "erro_pncp")
        self.assertIn("tempo limite", r1["erros"][0])
        self.assertEqual((r1["proxima_janela"]["janela"], r1["proxima_janela"]["pagina"]), (0, 2))
        del self.fake.fail_on[(pmi, "20250101", 2)]
        self.assertFalse(self._ate_concluir()[-1]["parcial"])
        self.assertEqual(self._count(), 5)

    def test_lock_bloqueia_execucao_simultanea(self) -> None:
        self.assertTrue(self.db.acquire_pncp_sync_lock("outro", 90.0, 1000.0))
        with self.assertRaises(SyncEmAndamento):
            self._lote(agora_ts=lambda: 1010.0)
        self.assertEqual(self.fake.calls, [])
        # Lock vencido (função morta pelo timeout) é liberado pelo TTL
        res = self._lote(agora_ts=lambda: 2000.0)
        self.assertIn("inseridos", res)
        # e é liberado ao final da execução
        self.assertTrue(self.db.acquire_pncp_sync_lock("terceiro", 90.0, 2001.0))


class TestPncpSyncEndpoint(unittest.TestCase):
    """POST /api/pncp/sync sempre responde JSON, inclusive em erro."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.tmp.name) / "pncp_ep.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _post(self, body: Dict[str, Any], fetch=None, get_db=None) -> Tuple[int, Dict[str, str], Any]:
        from src import web_server

        raw = json.dumps(body).encode("utf-8")
        h = web_server.RadarAPIHandler.__new__(web_server.RadarAPIHandler)
        h.path = "/api/pncp/sync"
        h.command = "POST"
        h.request_version = "HTTP/1.1"
        h.requestline = "POST /api/pncp/sync HTTP/1.1"
        h.client_address = ("127.0.0.1", 0)
        h.headers = email.message.Message()
        h.headers["Content-Length"] = str(len(raw))
        h.headers["Content-Type"] = "application/json"
        h.rfile = io.BytesIO(raw)
        h.wfile = io.BytesIO()

        real = web_server.executar_lote

        def lote(db, norm, **kw):
            return real(db, norm, fetch=fetch, hoje=HOJE, orgaos=ORGAOS, **kw)

        with mock.patch.object(web_server.RadarAPIHandler, "get_db", get_db or (lambda cls=None: self.db)), \
                mock.patch.object(web_server, "executar_lote", lote), \
                mock.patch.object(web_server.RadarAPIHandler, "log_message", lambda *a: None):
            h.do_POST()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split()[1])
        headers = dict(l.split(": ", 1) for l in lines[1:] if ": " in l)
        return status, headers, json.loads(payload.decode("utf-8"))

    def test_erro_do_pncp_retorna_json_com_falha_clara(self) -> None:
        def fetch(*a):
            raise urllib.error.HTTPError("https://pncp.gov.br/x", 503, "Service Unavailable", None, None)

        status, headers, data = self._post({}, fetch=fetch)
        self.assertEqual(status, 502)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertFalse(data["ok"])
        self.assertIn("PNCP", data["erro"])
        self.assertIn("HTTP 503", data["erros"][0])
        self.assertTrue(data["parcial"])
        self.assertIsNotNone(data["proxima_janela"])

    def test_sucesso_retorna_campos_do_lote(self) -> None:
        status, _, data = self._post({}, fetch=lambda *a: (204, {"data": []}))
        self.assertEqual(status, 200)
        for campo in ("inseridos", "atualizados", "ignorados", "parcial", "proxima_janela",
                      "ultima_data_sincronizada", "erros"):
            self.assertIn(campo, data)
        self.assertFalse(data["parcial"])

    def test_lock_ocupado_retorna_409_json(self) -> None:
        import time as _t

        self.db.acquire_pncp_sync_lock("outra-execucao", 90.0, _t.time())
        status, _, data = self._post({}, fetch=lambda *a: (204, {}))
        self.assertEqual(status, 409)
        self.assertTrue(data["em_andamento"])

    def test_cursor_invalido_retorna_400_json(self) -> None:
        status, _, data = self._post({"proxima_janela": {"modo": "x"}}, fetch=lambda *a: (204, {}))
        self.assertEqual(status, 400)
        self.assertIn("Cursor", data["erro"])

    def test_excecao_interna_vira_json_sem_vazar_segredo(self) -> None:
        def broken_db(cls=None):
            raise RuntimeError("Falha conectando https://x.supabase.co?apikey=SEGREDO123")

        status, _, data = self._post({}, fetch=lambda *a: (204, {}), get_db=broken_db)
        self.assertEqual(status, 500)
        self.assertFalse(data["ok"])
        self.assertNotIn("SEGREDO123", json.dumps(data))


@unittest.skipUnless(shutil.which("node"), "node não instalado")
class TestFrontendRespostaNaoJson(unittest.TestCase):
    """Executa parsePncpSyncResponse (extraída do dashboard) no Node, sem navegador e sem rede."""

    def _run(self, status: int, ok: bool, text: str) -> Dict[str, Any]:
        html = (Path(__file__).resolve().parent.parent / "static" / "stitch_dashboard.html").read_text(encoding="utf-8")
        funcs = []
        for name in ("describePncpHttpFailure", "parsePncpSyncResponse"):
            m = re.search(r"    function " + name + r"\(.*?\n    }\n", html, re.S)
            self.assertIsNotNone(m, name)
            funcs.append(m.group(0))
        script = "\n".join(funcs) + (
            f"\nprocess.stdout.write(JSON.stringify(parsePncpSyncResponse({status}, {str(ok).lower()}, {json.dumps(text)})));"
        )
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, check=True)
        return json.loads(out.stdout)

    def test_504_em_html_vira_mensagem_clara(self) -> None:
        r = self._run(504, False, "An error occurred with your deployment\n\nFUNCTION_INVOCATION_TIMEOUT")
        self.assertFalse(r["ok"])
        self.assertIn("Tempo esgotado", r["message"])
        self.assertIn("504", r["message"])
        self.assertNotIn("FUNCTION_INVOCATION_TIMEOUT", r["message"])

    def test_json_de_erro_usa_mensagem_do_backend(self) -> None:
        r = self._run(502, False, json.dumps({"erro": "O PNCP não respondeu corretamente."}))
        self.assertEqual(r["message"], "O PNCP não respondeu corretamente.")

    def test_json_de_sucesso(self) -> None:
        r = self._run(200, True, json.dumps({"inseridos": 1, "parcial": False}))
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["inseridos"], 1)


if __name__ == "__main__":
    unittest.main()
