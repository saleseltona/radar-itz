"""Testes das correções de coleta: evidências estruturadas, Instagram Graph API e checagem de rede."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main
from src.database import RadarDatabase
from src.error_monitor import ErrorMonitor
from src.evidence_store import parse_evidencias, persist_evidencias, referencia_evidencia
from src.instagram_client import username_from_url
from src.page_collector import PageCollector
from src.search_engine import SearchEngine
from src.source_manager import SourceManager


class TestCorrecoesColeta(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.db = RadarDatabase(base / "t.db")
        csv_path = base / "fontes.csv"
        csv_path.write_text(
            "nome,tipo,url,prioridade,temas\n"
            "Prefeitura de Imperatriz,Instagram,https://www.instagram.com/prefeituradeimperatriz/,Alta,obra\n",
            encoding="utf-8",
        )
        self.sm = SourceManager(self.db, csv_path)
        self.fontes = self.sm.load_sources_from_csv()
        self.em = ErrorMonitor(self.db)
        self.engine = SearchEngine(self.db, self.sm, PageCollector(self.db, self.em), self.em)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _count(self, table: str) -> int:
        with self.db.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_parse_e_persistencia_idempotente_de_evidencias(self) -> None:
        texto = (
            "[Fato objetivo]: Obra iniciada. | [Fato objetivo]: Prazo de 90 dias. | "
            "[Documento primário]: Contrato nº 12/2026 | [Fato objetivo]: Obra iniciada."
        )
        pares = parse_evidencias(texto)
        self.assertEqual(len(pares), 3)
        self.assertEqual(parse_evidencias("Evidências em fase de levantamento inicial."), [])

        url = "https://imperatriz.ma.gov.br/noticia"
        self.assertEqual(referencia_evidencia("Documento primário", "Contrato nº 12/2026", url), "Contrato nº 12/2026")
        self.assertNotEqual(
            referencia_evidencia("Fato objetivo", "Obra iniciada.", url),
            referencia_evidencia("Fato objetivo", "Prazo de 90 dias.", url),
        )

        persist_evidencias(self.db, "PAUTA-1", texto, url)
        persist_evidencias(self.db, "PAUTA-1", texto, url)
        self.assertEqual(self._count("evidencias"), 3)

    def test_instagram_sem_token_nao_gera_erro_nem_falso_sucesso(self) -> None:
        with mock.patch.dict(os.environ, {"INSTAGRAM_GRAPH_TOKEN": "", "INSTAGRAM_BUSINESS_ACCOUNT_ID": ""}):
            itens = self.engine.run_instagram_checks("EXEC-T", self.fontes)
        self.assertEqual(itens, [])
        self.assertEqual(self._count("erros_coleta"), 0)
        fonte = self.db.get_fonte_by_url(self.fontes[0]["url"])
        self.assertIn("Aguardando credencial", fonte["situacao_funcionamento"])
        self.assertEqual(fonte["ultima_coleta_sucesso"], "")

    def test_instagram_com_token_le_publicacoes_via_graph_api(self) -> None:
        midia = [{
            "caption": "Prefeitura inicia obra de drenagem no bairro Bacuri\nDetalhes no site.",
            "permalink": "https://www.instagram.com/p/ABC123/",
            "timestamp": "2026-11-26T12:00:00+0000",
            "media_url": "https://cdn.example/img.jpg",
        }]
        env = {"INSTAGRAM_GRAPH_TOKEN": "tok", "INSTAGRAM_BUSINESS_ACCOUNT_ID": "1784"}
        with mock.patch.dict(os.environ, env), \
                mock.patch("src.search_engine.fetch_recent_media", return_value=midia) as fetch:
            itens = self.engine.run_instagram_checks("EXEC-T", self.fontes)
        fetch.assert_called_once_with("prefeituradeimperatriz", "1784", "tok")
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["url"], "https://www.instagram.com/p/ABC123/")
        self.assertEqual(itens[0]["data_publicacao"], "2026-11-26")
        fonte = self.db.get_fonte_by_url(self.fontes[0]["url"])
        self.assertEqual(fonte["situacao_funcionamento"], "Ativa (API Oficial)")

    def test_instagram_falha_na_api_registra_erro(self) -> None:
        from src.instagram_client import InstagramGraphError

        env = {"INSTAGRAM_GRAPH_TOKEN": "tok", "INSTAGRAM_BUSINESS_ACCOUNT_ID": "1784"}
        with mock.patch.dict(os.environ, env), mock.patch(
            "src.search_engine.fetch_recent_media", side_effect=InstagramGraphError("HTTP 400 na Graph API: perfil pessoal")
        ):
            itens = self.engine.run_instagram_checks("EXEC-T", self.fontes)
        self.assertEqual(itens, [])
        self.assertEqual(self._count("erros_coleta"), 1)
        fonte = self.db.get_fonte_by_url(self.fontes[0]["url"])
        self.assertEqual(fonte["situacao_funcionamento"], "Falha na Graph API")

    def test_username_from_url(self) -> None:
        self.assertEqual(username_from_url("https://www.instagram.com/bombeiros.cbmma193/"), "bombeiros.cbmma193")

    def test_sem_rede_encerra_execucao_com_um_unico_erro(self) -> None:
        with mock.patch.object(main, "get_database", return_value=self.db), \
                mock.patch.object(main.socket, "getaddrinfo", side_effect=OSError("11001")), \
                mock.patch.dict(os.environ, {"RADAR_SKIP_NETWORK_CHECK": ""}):
            res = main.run_radar_cycle()
        self.assertEqual(res["situacao_execucao"], "SEM_REDE")
        self.assertEqual(self._count("erros_coleta"), 1)
        with self.db.connect() as conn:
            situacao = conn.execute("SELECT situacao_execucao FROM execucoes").fetchone()[0]
        self.assertEqual(situacao, "SEM_REDE")


if __name__ == "__main__":
    unittest.main()
