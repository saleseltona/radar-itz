"""Testes herméticos: busca PNCP incompleta não pode virar "não localizado" conclusivo nem alerta ALTA."""
from __future__ import annotations

import json
import logging
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.getLogger("radar_imperatriz.pncp").setLevel(logging.ERROR)

from src.transparencia_cross import (  # noqa: E402
    avaliar_cobertura_pncp,
    build_auditoria_dispensas,
    build_contratos_nao_localizados_pncp,
    build_cruzamento_fornecedores,
    build_pontos_apuracao,
    build_transparencia_resumo,
)

# Frases que afirmariam ausência ou irregularidade. Os avisos dizem "não significa irregular",
# por isso a checagem é por frase afirmativa, não pela palavra solta.
PALAVRAS_PROIBIDAS = (
    "constam no pncp",
    "ausente no pncp",
    "ausentes no pncp",
    "é irregular",
    "indício de irregularidade",
)


def _fiorilli(codigo: str, processo: str, doc: str, valor: float) -> Dict[str, Any]:
    return {
        "chave_contrato": f"2026|2|{codigo}",
        "ano": 2026,
        "empresa": "2",
        "entidade": "PREFEITURA",
        "codigo": codigo,
        "processo_adm": processo,
        "tipo_contratacao": "Dispensa",
        "eh_contratacao_direta": 1,
        "fornecedor_doc": doc,
        "fornecedor_nome": f"FORNECEDOR {codigo}",
        "objeto": "Objeto de teste",
        "valor_contrato": valor,
        "valor_efetivo": valor,
        "anulado": 0,
        "data_assinatura": "2026-02-01",
    }


def _pncp(ctrl: str, processo: str, doc: str, valor: float) -> Dict[str, Any]:
    return {
        "numero_controle_pncp": ctrl,
        "orgao_cnpj": "06158455000116",
        "ano_contrato": 2026,
        "numero_contrato": ctrl,
        "processo": processo,
        "fornecedor_cnpj": doc,
        "fornecedor_nome": "FORNECEDOR PNCP",
        "objeto": "Objeto PNCP",
        "valor_global": valor,
        "valor_inicial": valor,
    }


PNCP_ROWS = [_pncp("CTRL-1", "02.04.00.0010/2026", "11111111000111", 100000.0)]
FIORILLI_ROWS = [
    _fiorilli("C1", "02.04.00.0010/2026", "11111111000111", 100000.0),
    _fiorilli("C3", "8888888888/2026", "22222222000122", 300000.0),
]
PAGAMENTOS = [
    {"ano": 2026, "fornecedor_doc": "22222222000122", "fornecedor_nome": "FORNECEDOR C3", "pago": 250000.0, "empenhado": 250000.0},
]
TRUNCADA = {"completa": False, "truncada": True, "motivo": "1 janela(s) com páginas não buscadas", "anos": [2026]}
COMPLETA = {"completa": True, "truncada": False, "anos": [2025, 2026]}


def _textos(obj: Any) -> List[str]:
    return [json.dumps(obj, ensure_ascii=False).lower()]


class TestAvaliarCobertura(unittest.TestCase):
    def test_um_contrato_nao_basta_para_concluir(self) -> None:
        cob = avaliar_cobertura_pncp(1, None, None)
        self.assertFalse(cob["conclusiva"])
        self.assertEqual(cob["estado"], "NAO_CONFIRMADA")

    def test_truncada_e_parcial(self) -> None:
        cob = avaliar_cobertura_pncp(500, None, TRUNCADA)
        self.assertFalse(cob["conclusiva"])
        self.assertEqual(cob["estado"], "PARCIAL")
        self.assertIn("páginas não buscadas", cob["motivo"])

    def test_completa_sem_o_ano_filtrado_nao_conclui(self) -> None:
        cob = avaliar_cobertura_pncp(10, None, {"completa": True, "anos": [2025]}, ano=2026)
        self.assertFalse(cob["conclusiva"])

    def test_erro_e_sem_dados(self) -> None:
        self.assertEqual(avaliar_cobertura_pncp(10, "timeout", COMPLETA)["estado"], "ERRO")
        self.assertEqual(avaliar_cobertura_pncp(0, None, COMPLETA)["estado"], "SEM_DADOS")

    def test_completa(self) -> None:
        self.assertTrue(avaliar_cobertura_pncp(10, None, COMPLETA, ano=2026)["conclusiva"])


class TestCruzamentoComBuscaTruncada(unittest.TestCase):
    def test_nao_localizado_vira_inconclusivo(self) -> None:
        res = build_contratos_nao_localizados_pncp(
            PNCP_ROWS, FIORILLI_ROWS, ano=2026, limite=50, pncp_cobertura=TRUNCADA
        )
        resumo = res["resumo"]
        self.assertFalse(resumo["consulta_conclusiva"])
        self.assertEqual(resumo["nao_localizados_qtd"], 0)
        self.assertEqual(resumo["inconclusivos_qtd"], 1)
        self.assertEqual(resumo["localizados_qtd"], 1)
        self.assertEqual(resumo["cobertura_pncp"]["estado"], "PARCIAL")

    def test_sem_registro_de_cobertura_tambem_e_inconclusivo(self) -> None:
        res = build_contratos_nao_localizados_pncp(PNCP_ROWS, FIORILLI_ROWS, ano=2026, limite=50)
        self.assertFalse(res["resumo"]["consulta_conclusiva"])
        self.assertEqual(res["resumo"]["nao_localizados_qtd"], 0)

    def test_pago_sem_pncp_nao_gera_alta_nem_conclusivo(self) -> None:
        cruz = build_cruzamento_fornecedores(
            PNCP_ROWS, PAGAMENTOS, FIORILLI_ROWS, ano=2026, alerta="ALERTAS", pncp_cobertura=TRUNCADA
        )
        sem_pncp = [r for r in cruz["items"] if r["alerta_codigo"] == "PAGO_SEM_PNCP"]
        self.assertEqual(len(sem_pncp), 1)
        self.assertFalse(sem_pncp[0]["alerta_conclusivo"])
        self.assertIn("inconclusiva", sem_pncp[0]["alerta_observacao"].lower())

        nao_loc = build_contratos_nao_localizados_pncp(
            PNCP_ROWS, FIORILLI_ROWS, ano=2026, limite=50, pncp_cobertura=TRUNCADA
        )
        pontos = build_pontos_apuracao(
            cruzamento_res=cruz,
            nao_pncp_res=nao_loc,
            ano=2026,
            pncp_meta={"sincronizado": True, "total_contratos": 1, "cobertura": TRUNCADA},
            fiorilli_meta={"sincronizado": True},
        )
        tipos = {p["tipo"]: p for p in pontos["items"]}
        ponto = tipos["PAGAMENTO_SEM_CONTRATO_PNCP"]
        self.assertNotEqual(ponto["severidade"], "ALTA")
        self.assertFalse(ponto["conclusivo"])
        self.assertIn("COBERTURA_INSUFICIENTE_PNCP", tipos)
        self.assertNotIn("CONTRATO_NAO_LOCALIZADO_PNCP", tipos)
        self.assertFalse(pontos["cobertura"]["conclusiva"])
        self.assertTrue(pontos["cobertura"]["pncp"]["truncada"])
        for p in pontos["items"]:
            if p["situacao_vinculo"] == "ausencia_no_recorte":
                self.assertNotEqual(p["severidade"], "ALTA", p["tipo"])

    def test_dispensas_texto_nao_localizado(self) -> None:
        disp = build_auditoria_dispensas(PNCP_ROWS, FIORILLI_ROWS, ano=2026, pncp_cobertura=TRUNCADA)
        ausente = [r for r in disp["items"] if r["status_pncp"] == "AUSENTE_PNCP"][0]
        self.assertIn("não localizado", ausente["status_pncp_label"].lower())
        self.assertIn("inconclusiva", ausente["motivo_pncp"].lower())
        self.assertFalse(disp["cobertura_pncp"]["conclusiva"])

    def test_nenhum_texto_afirma_irregularidade(self) -> None:
        for cob in (TRUNCADA, COMPLETA, None):
            resumo = build_transparencia_resumo(
                PNCP_ROWS, [], [], PAGAMENTOS, FIORILLI_ROWS, ano=2026,
                meta={"sincronizado": True}, pncp_cobertura=cob,
            )
            disp = build_auditoria_dispensas(PNCP_ROWS, FIORILLI_ROWS, ano=2026, pncp_cobertura=cob)
            texto = " ".join(_textos(resumo) + _textos(disp))
            for palavra in PALAVRAS_PROIBIDAS:
                self.assertNotIn(palavra, texto, (cob, palavra))


class TestCoberturaCompleta(unittest.TestCase):
    def test_conclui_nao_localizado_sem_alta(self) -> None:
        nao_loc = build_contratos_nao_localizados_pncp(
            PNCP_ROWS, FIORILLI_ROWS, ano=2026, limite=50, pncp_cobertura=COMPLETA
        )
        self.assertTrue(nao_loc["resumo"]["consulta_conclusiva"])
        self.assertEqual(nao_loc["resumo"]["nao_localizados_qtd"], 1)
        pontos = build_pontos_apuracao(
            cruzamento_res={"items": [], "resumo_alertas": {"PAGO_SEM_PNCP": {"qtd": 1, "pago": 1.0, "pncp": 0.0}}},
            nao_pncp_res=nao_loc,
            ano=2026,
            pncp_meta={"sincronizado": True, "total_contratos": 1, "cobertura": COMPLETA},
            fiorilli_meta={"sincronizado": True},
        )
        tipos = {p["tipo"]: p for p in pontos["items"]}
        self.assertTrue(tipos["CONTRATO_NAO_LOCALIZADO_PNCP"]["conclusivo"])
        self.assertEqual(tipos["CONTRATO_NAO_LOCALIZADO_PNCP"]["severidade"], "VERIFICAR")
        self.assertEqual(tipos["PAGAMENTO_SEM_CONTRATO_PNCP"]["severidade"], "VERIFICAR")


class _FakeDB:
    def __init__(self) -> None:
        self.gravados: List[Dict[str, Any]] = []

    def upsert_pncp_contratos_batch(self, contratos: List[Dict[str, Any]]) -> int:
        self.gravados.extend(contratos)
        return len(contratos)


class TestPncpClientTruncamento(unittest.TestCase):
    def _client(self, total_paginas: int):
        from src.pncp_client import PncpClient

        client = PncpClient.__new__(PncpClient)
        client.db = _FakeDB()
        client.timeout = 5
        chamadas: List[int] = []

        def fake_fetch_page(cnpj_orgao, data_inicial, data_final, pagina, tamanho_pagina):
            chamadas.append(pagina)
            item = {
                "numeroControlePNCP": f"{cnpj_orgao}-{data_inicial}-{pagina}",
                "anoContrato": 2026,
                "valorGlobal": 10.0,
            }
            return 200, {"data": [item], "totalPaginas": total_paginas}

        client._fetch_page = fake_fetch_page  # type: ignore[assignment]
        return client, chamadas

    def test_fetch_window_registra_truncamento(self) -> None:
        client, chamadas = self._client(total_paginas=20)
        trunc: List[str] = []
        itens = client._fetch_window(
            {"sigla": "PMI", "cnpj": "06158455000116"}, 2026, "20260101", "20260331",
            max_pages=4, truncamentos=trunc,
        )
        self.assertEqual(len(itens), 4)
        self.assertEqual(chamadas, [1, 2, 3, 4])
        self.assertEqual(len(trunc), 1)
        self.assertIn("4 de 20", trunc[0])

    def test_fetch_window_completo_sem_truncamento(self) -> None:
        client, _ = self._client(total_paginas=3)
        trunc: List[str] = []
        client._fetch_window({"sigla": "PMI", "cnpj": "x"}, 2026, "a", "b", max_pages=8, truncamentos=trunc)
        self.assertEqual(trunc, [])

    def test_sync_imperatriz_informa_cobertura_parcial(self) -> None:
        client, _ = self._client(total_paginas=20)
        res = client.sync_imperatriz(anos=[2026], max_pages_per_window=2, mode="full", max_workers=1)
        self.assertFalse(res["cobertura"]["completa"])
        self.assertTrue(res["cobertura"]["truncada"])
        self.assertTrue(res["truncamentos"])

    def test_sync_imperatriz_completo(self) -> None:
        client, _ = self._client(total_paginas=1)
        res = client.sync_imperatriz(anos=[2026], max_pages_per_window=2, mode="full", max_workers=1)
        self.assertTrue(res["cobertura"]["completa"])
        recent = client.sync_imperatriz(anos=[2026], max_pages_per_window=2, mode="recent", max_workers=1)
        self.assertFalse(recent["cobertura"]["completa"])


class TestCoberturaRegistradaWeb(unittest.TestCase):
    def test_estado_da_sincronizacao(self) -> None:
        from src.web_server import pncp_cobertura_registrada

        class SemEstado:
            pass

        class ComEstado:
            def __init__(self, estado: Dict[str, Any]) -> None:
                self.estado = estado

            def get_pncp_sync_state(self) -> Dict[str, Any]:
                return self.estado

        class Quebrado:
            def get_pncp_sync_state(self) -> Dict[str, Any]:
                raise RuntimeError("tabela ausente")

        self.assertIsNone(pncp_cobertura_registrada(SemEstado()))
        self.assertIsNone(pncp_cobertura_registrada(Quebrado()))
        self.assertIsNone(pncp_cobertura_registrada(ComEstado({})))
        ok = pncp_cobertura_registrada(
            ComEstado({"carga_completa_concluida": True, "carga_completa_anos": [2025, 2026]})
        )
        self.assertTrue(ok["completa"])
        pendente = pncp_cobertura_registrada(
            ComEstado({"carga_completa_concluida": True, "cursor_pendente": {"modo": "carga_completa"}})
        )
        self.assertFalse(pendente["completa"])
        self.assertFalse(pncp_cobertura_registrada(ComEstado({"ultima_execucao": "x"}))["completa"])


if __name__ == "__main__":
    unittest.main()
