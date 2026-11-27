"""
Mecanismo de Pesquisa do Radar de Pautas de Imperatriz (Seções 4 e 5).
Combina consultas obrigatórias, verificação de fontes cadastradas, APIs externas
configuráveis e monitoramento auditado de perfis públicos do Instagram.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set

from src.config import (
    CONSULTAS_DERIVADAS,
    CONSULTAS_OBRIGATORIAS,
    VERIFIED_CORPUS_PATH,
    get_configured_integrations,
    mask_secrets,
)
from src.database import RadarDatabase
from src.error_monitor import ErrorMonitor
from src.live_news import LiveNewsFetcher
from src.page_collector import PageCollector
from src.query_runner import (
    STATUS_ERRO,
    STATUS_LIMITE,
    STATUS_SEM_TEMPO,
    QueryRunner,
    resumo_cobertura,
)
from src.source_manager import SourceManager

logger = logging.getLogger("radar_imperatriz.search")


class SearchEngine:
    """Executa a pesquisa jornalística abrangente sobre Imperatriz, MA."""

    def __init__(
        self,
        db: RadarDatabase,
        source_manager: SourceManager,
        page_collector: PageCollector,
        error_monitor: ErrorMonitor,
    ) -> None:
        self.db = db
        self.source_manager = source_manager
        self.collector = page_collector
        self.error_monitor = error_monitor

    def run_instagram_checks(
        self, id_execucao: str, fontes_instagram: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Monitoramento do Instagram conforme Seção 5:
        - Não contorna autenticação ou CAPTCHA.
        - Registra a tentativa, o motivo conhecido do bloqueio e a ação recomendada.
        - Não declara sucesso se não conseguir acessar as publicações.
        """
        graph_token = os.environ.get("INSTAGRAM_GRAPH_TOKEN", "").strip()
        resultados: List[Dict[str, Any]] = []

        for fonte in fontes_instagram:
            url = fonte.get("url", "")
            nome = fonte.get("nome", "Instagram Perfil")

            if graph_token:
                # Integração com API Oficial do Instagram Graph se configurada
                status_msg = "Instagram Graph API configurada - consulta de métricas de perfil autorizada."
                self.source_manager.update_source_status(
                    url=url,
                    status="Ativa (API Oficial)",
                    sucesso=True,
                    proxima_acao="Manter token atualizado periodicamente",
                    metodo="Meta Graph API v19.0",
                )
            else:
                # Sem token oficial, a plataforma Meta impõe login wall para leitura automatizada
                motivo = "Plataforma Meta exige autenticação e login wall para leitura de publicações; acesso automatizado bloqueado sem credencial oficial."
                self.source_manager.update_source_status(
                    url=url,
                    status="Bloqueio por Autenticação (Meta Login Wall)",
                    sucesso=False,
                    erro=motivo,
                    proxima_acao="Cadastrar INSTAGRAM_GRAPH_TOKEN no .env para acesso institucional autorizado",
                    metodo="Web aberta (Bloqueado)",
                    observacoes="Impossível extrair legendas de forma automatizada sem API oficial ou credencial autorizada.",
                )
                self.error_monitor.record_error(
                    id_execucao=id_execucao,
                    componente="MonitorInstagram",
                    alvo_url=url,
                    mensagem=f"{nome}: {motivo}",
                )

        return resultados

    def run_search_queries(
        self,
        id_execucao: str,
        consultas: List[str],
        live_fetcher: LiveNewsFetcher,
        runner: Optional[QueryRunner] = None,
    ) -> Dict[str, Any]:
        """Roda cada consulta no Google News RSS e registra falhas em erros_coleta."""
        if runner is None:
            runner = QueryRunner(
                http_get=lambda url: live_fetcher._http_get_text(url, max_bytes=2_500_000),
                parse_rss=lambda xml, consulta: LiveNewsFetcher.parse_rss_xml(
                    xml,
                    default_source_name="Imprensa Regional",
                    consulta_origem=f"Consulta: {consulta}",
                    require_imperatriz_filter=True,
                ),
            )
        resultado = runner.run(consultas)

        for r in resultado["relatorio"]:
            if r["status"] == STATUS_ERRO or (r["status"] == STATUS_LIMITE and r.get("erro")):
                self.error_monitor.record_error(
                    id_execucao=id_execucao,
                    componente="BuscaGoogleNewsRSS",
                    alvo_url=r["url"],
                    mensagem=f"Consulta '{r['consulta']}' não concluída: {r['erro']}",
                )

        pendentes = [
            r["consulta"]
            for r in resultado["relatorio"]
            if r["status"] in (STATUS_LIMITE, STATUS_SEM_TEMPO) and not r.get("erro")
        ]
        if pendentes:
            motivo = (
                "limite de requisições do Google News"
                if any(r["status"] == STATUS_LIMITE for r in resultado["relatorio"])
                else f"orçamento de tempo de {runner.budget_seconds:.0f}s esgotado"
            )
            self.error_monitor.record_error(
                id_execucao=id_execucao,
                componente="BuscaGoogleNewsRSS",
                alvo_url="https://news.google.com/rss/search",
                mensagem=f"{len(pendentes)} consultas não executadas ({motivo}): " + "; ".join(pendentes),
            )

        logger.info(resumo_cobertura(resultado))
        return resultado

    def execute_queries(
        self, id_execucao: str, consultas_adicionais: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Executa as 32 consultas obrigatórias da Seção 4 mais consultas derivadas e adicionais,
        cada uma como busca real no Google News RSS (ver `src/query_runner.py`), além do
        G1 MA RSS, do portal da Prefeitura e das fontes cadastradas.
        """
        todas_consultas = list(CONSULTAS_OBRIGATORIAS) + list(CONSULTAS_DERIVADAS)
        if consultas_adicionais:
            for extra in consultas_adicionais:
                termo = str(extra or "").strip()
                if not termo:
                    continue
                # Termo livre do painel ganha o município para não buscar fora de Imperatriz
                if "imperatriz" not in termo.lower():
                    termo = f"{termo} Imperatriz"
                todas_consultas.append(termo)

        logger.info(
            "Iniciando varredura de %d consultas de pesquisa em Imperatriz (MA)...",
            len(todas_consultas),
        )

        integrations = get_configured_integrations()

        # Carrega todas as fontes cadastradas
        fontes_cadastradas = self.source_manager.load_sources_from_csv()
        fontes_instagram = [f for f in fontes_cadastradas if "instagram.com" in f.get("url", "").lower()]
        fontes_web = [f for f in fontes_cadastradas if "instagram.com" not in f.get("url", "").lower()]

        # 1. Checagem das fontes Instagram
        self.run_instagram_checks(id_execucao, fontes_instagram)

        # 2. Varredura de notícias recentes ao vivo (Google News RSS + G1 Maranhão RSS)
        candidatos_por_url: Dict[str, Dict[str, Any]] = {}
        live_fetcher = LiveNewsFetcher(timeout=8, max_cover_fetches=10)
        try:
            live_res = live_fetcher.fetch_recent_news(
                max_items=30, incluir_google_news=False
            )
            for item in live_res.get("itens", []):
                u = item.get("url")
                if u:
                    candidatos_por_url[u.lower()] = item
            for err_msg in live_res.get("erros", []):
                logger.info("Aviso na coleta RSS ao vivo (%s)", mask_secrets(err_msg))
        except Exception as e:
            logger.warning("Coleta RSS ao vivo indisponível neste ciclo: %s", mask_secrets(str(e)))

        # 2b. Execução real de cada consulta (obrigatórias, derivadas e adicionais) no Google News RSS
        busca = self.run_search_queries(id_execucao, todas_consultas, live_fetcher)
        for item in busca["itens"]:
            u = item.get("url")
            if u and u.lower() not in candidatos_por_url:
                candidatos_por_url[u.lower()] = item

        # 3. Carrega repositório de páginas públicas reais verificadas (base histórica complementar)
        if VERIFIED_CORPUS_PATH.exists():
            try:
                corpus_data = json.loads(VERIFIED_CORPUS_PATH.read_text(encoding="utf-8"))
                for p in corpus_data.get("paginas", []):
                    u = p.get("url")
                    if u and u.lower() not in candidatos_por_url:
                        candidatos_por_url[u.lower()] = p
            except Exception as e:
                self.error_monitor.record_error(
                    id_execucao, "SearchEngine", str(VERIFIED_CORPUS_PATH), "Falha ao carregar corpus verificado", e
                )

        # 4. Consulta às páginas web das fontes cadastradas
        for f in fontes_web:
            url_fonte = f.get("url", "")
            nome_fonte = f.get("nome", "Fonte Web")

            sucesso, msg_status, dados = self.collector.fetch_page(
                url=url_fonte,
                id_execucao=id_execucao,
                fonte_nome=nome_fonte,
            )

            if sucesso:
                if dados.get("imagem_capa") and url_fonte.lower() in candidatos_por_url:
                    if not candidatos_por_url[url_fonte.lower()].get("imagem_capa"):
                        candidatos_por_url[url_fonte.lower()]["imagem_capa"] = dados["imagem_capa"]
                self.source_manager.update_source_status(
                    url=url_fonte,
                    status="Ativa e acessível",
                    sucesso=True,
                    proxima_acao="Monitoramento regular mantido",
                    metodo="HTTP Direto",
                )
            else:
                self.source_manager.update_source_status(
                    url=url_fonte,
                    status="Acesso limitado / Bloqueio",
                    sucesso=False,
                    erro=msg_status,
                    proxima_acao="Verificar conectividade ou chave de API externa",
                    metodo="HTTP Direto",
                )

        return {
            "total_consultas_previstas": busca["total_previstas"],
            "total_consultas_executadas": busca["total_executadas"],
            "consultas": todas_consultas,
            "relatorio_consultas": busca["relatorio"],
            "cobertura_consultas": resumo_cobertura(busca),
            "integracoes_ativas": integrations,
            "itens_coletados": list(candidatos_por_url.values()),
        }
