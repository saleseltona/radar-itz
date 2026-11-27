"""
Testes da execução real das consultas de busca (src/query_runner.py e SearchEngine).
Tudo offline: as respostas do Google News RSS são fixtures e o relógio é falso.
"""
from __future__ import annotations

import email.message
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

from src.config import CONSULTAS_DERIVADAS, CONSULTAS_OBRIGATORIAS
from src.exporter import CSVExporter
from src.live_news import LiveNewsFetcher
from src.query_runner import (
    STATUS_COM_ITENS,
    STATUS_ERRO,
    STATUS_LIMITE,
    STATUS_SEM_ITENS,
    STATUS_SEM_TEMPO,
    QueryRunner,
    build_google_news_url,
    resumo_cobertura,
)
from src.search_engine import SearchEngine


def rss_fixture(consulta: str) -> str:
    """RSS no formato do Google News com um item sobre Imperatriz ligado à consulta."""
    slug = urllib.parse.quote(consulta.lower().replace(" ", "-"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Notícia sobre {consulta} em Imperatriz - Imirante</title>
    <link>https://imirante.com/imperatriz/{slug}</link>
    <pubDate>Tue, 24 Nov 2026 09:30:00 GMT</pubDate>
    <source url="https://imirante.com">Imirante</source>
    <description>Fato ocorrido em Imperatriz relacionado a {consulta}.</description>
  </item>
</channel></rss>"""


RSS_VAZIO = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'


def consulta_da_url(url: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
    return q.rsplit(" when:", 1)[0]


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Too Many Requests", email.message.Message(), None)


def parse(xml: str, consulta: str) -> List[Dict[str, Any]]:
    return LiveNewsFetcher.parse_rss_xml(
        xml, default_source_name="Imprensa Regional", consulta_origem=f"Consulta: {consulta}"
    )


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class TestQueryRunner(unittest.TestCase):
    def test_url_codifica_consulta_e_janela(self) -> None:
        url = build_google_news_url("Câmara Municipal de Imperatriz")
        self.assertTrue(url.startswith("https://news.google.com/rss/search?q="))
        self.assertEqual(consulta_da_url(url), "Câmara Municipal de Imperatriz")
        self.assertIn("+when:14d&hl=pt-BR", url)

    def test_executa_todas_as_32_obrigatorias_e_derivadas(self) -> None:
        chamadas: List[str] = []

        def http_get(url: str) -> str:
            chamadas.append(url)
            return rss_fixture(consulta_da_url(url))

        clock = FakeClock()
        consultas = list(CONSULTAS_OBRIGATORIAS) + list(CONSULTAS_DERIVADAS)
        self.assertEqual(len(CONSULTAS_OBRIGATORIAS), 32)
        res = QueryRunner(http_get, parse, delay_seconds=0.5, budget_seconds=600,
                          clock=clock, sleep=clock.sleep).run(consultas)

        self.assertEqual(len(chamadas), len(consultas))
        self.assertEqual(res["total_previstas"], len(consultas))
        self.assertEqual(res["total_executadas"], len(consultas))
        self.assertEqual(len(res["itens"]), len(consultas))
        self.assertEqual({r["status"] for r in res["relatorio"]}, {STATUS_COM_ITENS})
        self.assertEqual(res["itens"][0]["consulta_origem"], "Consulta: Imperatriz hoje")
        # Pausa entre requisições (não antes da primeira)
        self.assertAlmostEqual(clock.t, 0.5 * (len(consultas) - 1))

    def test_sem_itens_e_erro_de_rede_sao_registrados_sem_parar(self) -> None:
        def http_get(url: str) -> str:
            c = consulta_da_url(url)
            if c == "B":
                return RSS_VAZIO
            if c == "C":
                raise OSError("[Errno -3] Temporary failure in name resolution")
            if c == "D":
                raise http_error(url, 404)
            return rss_fixture(c)

        res = QueryRunner(http_get, parse, delay_seconds=0, budget_seconds=60).run(
            ["A Imperatriz", "B", "C", "D", "E Imperatriz"]
        )
        status = [r["status"] for r in res["relatorio"]]
        self.assertEqual(status, [STATUS_COM_ITENS, STATUS_SEM_ITENS, STATUS_ERRO, STATUS_ERRO, STATUS_COM_ITENS])
        self.assertIn("name resolution", res["relatorio"][2]["erro"])
        self.assertIn("HTTP 404", res["relatorio"][3]["erro"])
        # Consulta com erro não conta como executada
        self.assertEqual(res["total_executadas"], 3)

    def test_limite_de_requisicoes_interrompe_sem_insistir(self) -> None:
        chamadas: List[str] = []

        def http_get(url: str) -> str:
            chamadas.append(url)
            if len(chamadas) == 2:
                raise http_error(url, 429)
            return rss_fixture(consulta_da_url(url))

        res = QueryRunner(http_get, parse, delay_seconds=0, budget_seconds=60).run(
            ["Q1 Imperatriz", "Q2 Imperatriz", "Q3 Imperatriz", "Q4 Imperatriz"]
        )
        self.assertEqual(len(chamadas), 2)
        self.assertEqual([r["status"] for r in res["relatorio"]],
                         [STATUS_COM_ITENS, STATUS_LIMITE, STATUS_LIMITE, STATUS_LIMITE])
        self.assertIn("HTTP 429", res["relatorio"][1]["erro"])
        self.assertEqual(res["relatorio"][2]["erro"], "")
        self.assertEqual(res["total_executadas"], 1)
        self.assertIn("3 interrompidas por limite", resumo_cobertura(res))

    def test_orcamento_de_tempo_marca_restantes_como_nao_executadas(self) -> None:
        clock = FakeClock()

        def http_get(url: str) -> str:
            clock.t += 4  # cada requisição leva 4 s
            return rss_fixture(consulta_da_url(url))

        res = QueryRunner(http_get, parse, delay_seconds=1, budget_seconds=10,
                          clock=clock, sleep=clock.sleep).run([f"T{i} Imperatriz" for i in range(6)])
        status = [r["status"] for r in res["relatorio"]]
        # t=0 -> 4; pausa 5 -> 9; pausa 10: a 3ª já começaria fora do orçamento
        self.assertEqual(status[:2], [STATUS_COM_ITENS, STATUS_COM_ITENS])
        self.assertEqual(status.count(STATUS_SEM_TEMPO), 4)
        self.assertEqual(res["total_executadas"], 2)
        self.assertEqual(resumo_cobertura(res).split(";")[0], "2 de 6 consultas de busca executadas no Google News RSS")

    def test_remove_consultas_vazias_e_repetidas(self) -> None:
        res = QueryRunner(lambda u: RSS_VAZIO, parse, delay_seconds=0, budget_seconds=60).run(
            ["Imperatriz hoje", " ", "imperatriz HOJE", "Imperatriz saúde"]
        )
        self.assertEqual([r["consulta"] for r in res["relatorio"]], ["Imperatriz hoje", "Imperatriz saúde"])


class FakeErrorMonitor:
    def __init__(self) -> None:
        self.erros: List[Dict[str, str]] = []

    def record_error(self, id_execucao: str, componente: str, alvo_url: str, mensagem: str, exception: Any = None) -> None:
        self.erros.append({"componente": componente, "alvo_url": alvo_url, "mensagem": mensagem})


class TestSearchEngineConsultas(unittest.TestCase):
    def _engine(self) -> tuple:
        monitor = FakeErrorMonitor()
        sources = mock.Mock()
        sources.load_sources_from_csv.return_value = []
        engine = SearchEngine(db=mock.Mock(), source_manager=sources,
                              page_collector=mock.Mock(), error_monitor=monitor)
        return engine, monitor

    def test_execute_queries_roda_cada_consulta_no_google_news(self) -> None:
        engine, monitor = self._engine()
        urls: List[str] = []

        def fake_get(self_: Any, url: str, max_bytes: int = 0, timeout: Any = None) -> str:
            urls.append(url)
            if "news.google.com" in url:
                c = consulta_da_url(url)
                if c == "Imperatriz trânsito":
                    raise OSError("timed out")
                return rss_fixture(c)
            raise OSError("fonte fora do teste")

        with mock.patch.object(LiveNewsFetcher, "_http_get_text", fake_get), \
                mock.patch.dict("os.environ", {"RADAR_QUERY_DELAY_SECONDS": "0", "RADAR_QUERY_BUDGET_SECONDS": "600"}):
            res = engine.execute_queries("EXEC-T", consultas_adicionais=["enchente", "Imperatriz hoje"])

        gn = [u for u in urls if "news.google.com" in u]
        previstas = 32 + len(CONSULTAS_DERIVADAS) + 1  # "Imperatriz hoje" repetida é ignorada
        self.assertEqual(len(gn), previstas)
        self.assertIn("enchente Imperatriz", [consulta_da_url(u) for u in gn])
        self.assertEqual(res["total_consultas_previstas"], previstas)
        self.assertEqual(res["total_consultas_executadas"], previstas - 1)
        self.assertEqual(len(res["relatorio_consultas"]), previstas)
        origens = {i["consulta_origem"] for i in res["itens_coletados"]}
        self.assertIn("Consulta: Imperatriz denúncia", origens)
        # As 5 consultas fixas antigas não rodam mais em paralelo às 32
        self.assertFalse(any("Rio+Tocantins" in u or "Rio%20Tocantins" in u for u in gn))
        # Falha real fica em erros_coleta
        falhas = [e for e in monitor.erros if e["componente"] == "BuscaGoogleNewsRSS"]
        self.assertEqual(len(falhas), 1)
        self.assertIn("Imperatriz trânsito", falhas[0]["mensagem"])
        self.assertIn(f"{previstas - 1} de {previstas}", res["cobertura_consultas"])

    def test_limite_gera_um_erro_e_um_aviso_agregado(self) -> None:
        engine, monitor = self._engine()

        def http_get(url: str) -> str:
            if consulta_da_url(url) == "Q2 Imperatriz":
                raise http_error(url, 429)
            return rss_fixture(consulta_da_url(url))

        runner = QueryRunner(http_get, parse, delay_seconds=0, budget_seconds=60)
        engine.run_search_queries("EXEC-T", ["Q1 Imperatriz", "Q2 Imperatriz", "Q3 Imperatriz"],
                                  LiveNewsFetcher(), runner=runner)
        msgs = [e["mensagem"] for e in monitor.erros]
        self.assertEqual(len(msgs), 2)
        self.assertIn("HTTP 429", msgs[0])
        self.assertIn("1 consultas não executadas (limite de requisições do Google News): Q3 Imperatriz", msgs[1])

    def test_export_consultas_status_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = CSVExporter(db=mock.Mock(), output_dir=Path(tmp))
            path = exp.export_consultas_status([
                {"consulta": "Imperatriz hoje", "status": STATUS_COM_ITENS, "itens": 3, "erro": "", "url": "u"},
                {"consulta": "Imperatriz saúde", "status": STATUS_SEM_TEMPO, "itens": 0, "erro": "", "url": "v"},
            ])
            linhas = path.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(linhas[0], "consulta,status,itens,erro,url")
        self.assertIn("nao_executada_tempo_esgotado", linhas[2])


if __name__ == "__main__":
    unittest.main()
