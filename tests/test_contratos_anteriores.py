"""Testes herméticos: pagamentos de contratos de anos anteriores e correções de valor documentais."""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.getLogger("radar_imperatriz.pncp").setLevel(logging.ERROR)

from src.transparencia_cross import (  # noqa: E402
    PNCP_ANOS_CRUZAMENTO,
    build_cruzamento_fornecedores,
)

DOC = "33333333000133"


def _pncp(ctrl: str, ano: int, doc: str, valor: float, vig_fim: str = "") -> Dict[str, Any]:
    return {
        "numero_controle_pncp": ctrl,
        "orgao_cnpj": "06158455000116",
        "orgao_sigla": "PMI",
        "ano_contrato": ano,
        "data_publicacao": f"{ano}-03-01",
        "data_vigencia_fim": vig_fim,
        "processo": f"P-{ctrl}",
        "fornecedor_cnpj": doc,
        "fornecedor_nome": "FORNECEDOR ANTIGO",
        "valor_global": valor,
    }


def _pagamento(doc: str, pago: float, ano: int = 2026) -> Dict[str, Any]:
    return {"ano": ano, "fornecedor_doc": doc, "fornecedor_nome": "FORNECEDOR ANTIGO", "pago": pago, "empenhado": pago}


def _fiorilli_remanescente(doc: str, valor: float) -> Dict[str, Any]:
    return {
        "chave_contrato": "2026|2|R1",
        "ano": 2026,
        "empresa": "2",
        "entidade": "PREFEITURA",
        "codigo": "R1",
        "fornecedor_doc": doc,
        "fornecedor_nome": "FORNECEDOR ANTIGO",
        "valor_efetivo": valor,
        "anulado": 0,
        "data_assinatura": "2023-05-10",
    }


def _alerta(res: Dict[str, Any], doc: str = DOC) -> Dict[str, Any]:
    return [r for r in res["items"] if r["fornecedor_doc"] == doc][0]


class TestContratosAnteriores(unittest.TestCase):
    def test_anos_cruzamento_incluem_anteriores(self) -> None:
        self.assertIn(2024, PNCP_ANOS_CRUZAMENTO)
        self.assertIn(2026, PNCP_ANOS_CRUZAMENTO)

    def test_pago_em_2026_de_contrato_pncp_2025_nao_vira_pago_sem_pncp(self) -> None:
        pncp = [_pncp("A-2025", 2025, DOC, 500000.0, vig_fim="2026-12-31")]
        res = build_cruzamento_fornecedores(pncp, [_pagamento(DOC, 200000.0)], [], ano=2026)
        row = _alerta(res)
        self.assertEqual(row["alerta_codigo"], "PAGO_CONTRATO_ANTERIOR")
        self.assertEqual(row["pncp_anteriores_qtd"], 1)
        self.assertEqual(row["pncp_anteriores_valor"], 500000.0)
        self.assertEqual(row["pncp_contratos_qtd"], 0)
        self.assertEqual(res["resumo_alertas"]["PAGO_SEM_PNCP"]["qtd"], 0)

    def test_contrato_anterior_encerrado_nao_explica_pagamento(self) -> None:
        pncp = [_pncp("A-2024", 2024, DOC, 500000.0, vig_fim="2024-12-31")]
        res = build_cruzamento_fornecedores(pncp, [_pagamento(DOC, 200000.0)], [], ano=2026)
        self.assertEqual(_alerta(res)["alerta_codigo"], "PAGO_SEM_PNCP")

    def test_pagamento_maior_que_tudo_mantem_alerta(self) -> None:
        pncp = [
            _pncp("A-2025", 2025, DOC, 100000.0, vig_fim="2026-06-30"),
            _pncp("B-2026", 2026, DOC, 100000.0),
        ]
        res = build_cruzamento_fornecedores(pncp, [_pagamento(DOC, 900000.0)], [], ano=2026)
        self.assertEqual(_alerta(res)["alerta_codigo"], "PAGO_ACIMA_PNCP")

    def test_pago_acima_do_exercicio_coberto_por_contrato_anterior(self) -> None:
        pncp = [
            _pncp("A-2025", 2025, DOC, 300000.0),  # sem fim de vigência: pode estar vigente
            _pncp("B-2026", 2026, DOC, 100000.0),
        ]
        res = build_cruzamento_fornecedores(pncp, [_pagamento(DOC, 350000.0)], [], ano=2026)
        self.assertEqual(_alerta(res)["alerta_codigo"], "PAGO_CONTRATO_ANTERIOR")

    def test_remanescente_fiorilli_explica_pagamento(self) -> None:
        res = build_cruzamento_fornecedores(
            [], [_pagamento(DOC, 150000.0)], [_fiorilli_remanescente(DOC, 400000.0)], ano=2026
        )
        self.assertEqual(_alerta(res)["alerta_codigo"], "PAGO_CONTRATO_ANTERIOR")

    def test_contrato_anterior_nao_cria_linha_nova(self) -> None:
        pncp = [_pncp("Z-2025", 2025, "44444444000144", 1.0, vig_fim="2026-12-31")]
        res = build_cruzamento_fornecedores(pncp, [_pagamento(DOC, 1000.0)], [], ano=2026)
        self.assertEqual({r["fornecedor_doc"] for r in res["items"]}, {DOC})

    def test_sem_ano_usa_inicio_do_recorte(self) -> None:
        pncp = [_pncp("A-2024", 2024, DOC, 500000.0, vig_fim="2025-12-31")]
        res = build_cruzamento_fornecedores(pncp, [_pagamento(DOC, 200000.0, ano=2025)], [], ano=None)
        self.assertEqual(_alerta(res)["alerta_codigo"], "PAGO_CONTRATO_ANTERIOR")


class TestCorrecoesDocumentais(unittest.TestCase):
    CTRL = "06158455000116-2-000069/2026"

    def _raw(self, valor: float) -> Dict[str, Any]:
        return {
            "numeroControlePNCP": self.CTRL,
            "anoContrato": 2026,
            "sequencialContrato": 69,
            "valorInicial": valor,
            "valorGlobal": valor,
            "fornecedor": {},
        }

    def _db(self):
        from src.database import RadarDatabase

        tmp = tempfile.mkdtemp()
        return RadarDatabase(Path(tmp) / "t.db")

    def test_normalizacao_nao_altera_valor_em_silencio(self) -> None:
        from src.pncp_client import PncpClient

        c = PncpClient.normalize_contrato(self._raw(1682632750.0), {"sigla": "PMI", "cnpj": "06158455000116"}, 2026)
        self.assertEqual(c["valor_global"], 1682632750.0)

    def test_correcao_vira_retificacao_com_valor_original(self) -> None:
        from src.pncp_client import PncpClient, registrar_correcoes_documentais

        db = self._db()
        c = PncpClient.normalize_contrato(self._raw(1682632750.0), {"sigla": "PMI", "cnpj": "06158455000116"}, 2026)
        db.upsert_pncp_contratos_batch([c])
        self.assertEqual(registrar_correcoes_documentais(db, [c]), [self.CTRL])
        ret = {r["numero_controle_pncp"]: r for r in db.get_all_pncp_retificacoes()}[self.CTRL]
        self.assertEqual(ret["valor_original_pncp"], 1682632750.0)
        self.assertEqual(ret["valor_corrigido"], 1682632.75)
        self.assertIn("Correção documental", ret["justificativa"])
        efetivos = {r["numero_controle_pncp"]: r for r in db.get_effective_pncp_contratos(ano=2026)}
        self.assertAlmostEqual(efetivos[self.CTRL]["valor_global"], 1682632.75)

        # Nova sincronização: retificação preservada, nada é duplicado nem sobrescrito
        db.upsert_pncp_contratos_batch([c])
        self.assertEqual(registrar_correcoes_documentais(db, [c]), [])
        efetivos = {r["numero_controle_pncp"]: r for r in db.get_effective_pncp_contratos(ano=2026)}
        self.assertAlmostEqual(efetivos[self.CTRL]["valor_global"], 1682632.75)

    def test_retificacao_manual_preservada(self) -> None:
        from src.pncp_client import PncpClient, registrar_correcoes_documentais

        db = self._db()
        c = PncpClient.normalize_contrato(self._raw(1682632750.0), {"sigla": "PMI", "cnpj": "06158455000116"}, 2026)
        db.upsert_pncp_contratos_batch([c])
        db.retificar_pncp_contrato(self.CTRL, 1700000.0, "Conferido manualmente pela redação")
        self.assertEqual(registrar_correcoes_documentais(db, [c]), [])
        ret = {r["numero_controle_pncp"]: r for r in db.get_all_pncp_retificacoes()}[self.CTRL]
        self.assertEqual(ret["valor_corrigido"], 1700000.0)
        self.assertEqual(ret["justificativa"], "Conferido manualmente pela redação")

    def test_orgao_corrigiu_no_pncp_nada_aplicado(self) -> None:
        from src.pncp_client import PncpClient, registrar_correcoes_documentais

        db = self._db()
        c = PncpClient.normalize_contrato(self._raw(1682632.75), {"sigla": "PMI", "cnpj": "06158455000116"}, 2026)
        db.upsert_pncp_contratos_batch([c])
        self.assertEqual(registrar_correcoes_documentais(db, [c]), [])
        self.assertEqual(db.get_all_pncp_retificacoes(), [])

    def test_sincronizacao_em_lotes_registra_correcao(self) -> None:
        from datetime import date

        from src.pncp_client import PncpClient
        from src.pncp_sync import executar_lote

        db = self._db()
        raw = self._raw(1682632750.0)

        def fetch(endpoint, cnpj, data_ini, data_fim, pagina, tamanho, timeout):
            if data_ini == "20260201":
                return 200, {"data": [raw], "totalPaginas": 1}
            return 204, {"data": [], "totalPaginas": 0}

        kw = dict(fetch=fetch, hoje=date(2026, 3, 31), orgaos=[{"sigla": "PMI", "cnpj": "06158455000116"}])
        res = executar_lote(db, PncpClient.normalize_contrato, max_janelas=100, **kw)
        while res["parcial"]:
            res = executar_lote(db, PncpClient.normalize_contrato, cursor=res["proxima_janela"], max_janelas=100, **kw)
        ret = {r["numero_controle_pncp"]: r for r in db.get_all_pncp_retificacoes()}[self.CTRL]
        self.assertEqual(ret["valor_original_pncp"], 1682632750.0)
        efetivos = {r["numero_controle_pncp"]: r for r in db.get_effective_pncp_contratos(ano=2026)}
        self.assertAlmostEqual(efetivos[self.CTRL]["valor_global"], 1682632.75)

        # Nova carga do mesmo contrato: a retificação continua valendo e não é duplicada
        executar_lote(db, PncpClient.normalize_contrato, modo="carga_completa", max_janelas=100, **kw)
        self.assertEqual(len(db.get_all_pncp_retificacoes()), 1)
        efetivos = {r["numero_controle_pncp"]: r for r in db.get_effective_pncp_contratos(ano=2026)}
        self.assertAlmostEqual(efetivos[self.CTRL]["valor_global"], 1682632.75)

    def test_falha_ao_ler_retificacoes_nao_grava(self) -> None:
        from src.pncp_client import registrar_correcoes_documentais

        class Quebrado:
            def get_all_pncp_retificacoes(self) -> List[Dict[str, Any]]:
                raise RuntimeError("indisponível")

            def retificar_pncp_contrato(self, *a: Any, **k: Any) -> None:
                raise AssertionError("não deveria gravar")

        c = {"numero_controle_pncp": self.CTRL, "valor_global": 1682632750.0}
        self.assertEqual(registrar_correcoes_documentais(Quebrado(), [c]), [])


if __name__ == "__main__":
    unittest.main()
