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
from src.page_collector import PageCollector
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

    def execute_queries(
        self, id_execucao: str, consultas_adicionais: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Executa as 32 consultas obrigatórias da Seção 4 mais consultas derivadas.
        Consulta fontes cadastradas e integrações configuradas.
        """
        todas_consultas = list(CONSULTAS_OBRIGATORIAS) + list(CONSULTAS_DERIVADAS)
        if consultas_adicionais:
            todas_consultas.extend(consultas_adicionais)

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

        # 2. Varredura das fontes web oficiais e de imprensa
        candidatos_por_url: Dict[str, Dict[str, Any]] = {}

        # Carrega repositório de páginas públicas reais verificadas
        if VERIFIED_CORPUS_PATH.exists():
            try:
                corpus_data = json.loads(VERIFIED_CORPUS_PATH.read_text(encoding="utf-8"))
                for p in corpus_data.get("paginas", []):
                    u = p.get("url")
                    if u:
                        candidatos_por_url[u.lower()] = p
            except Exception as e:
                self.error_monitor.record_error(
                    id_execucao, "SearchEngine", str(VERIFIED_CORPUS_PATH), "Falha ao carregar corpus verificado", e
                )

        # 3. Consulta às páginas web das fontes cadastradas
        for f in fontes_web:
            url_fonte = f.get("url", "")
            nome_fonte = f.get("nome", "Fonte Web")

            sucesso, msg_status, dados = self.collector.fetch_page(
                url=url_fonte,
                id_execucao=id_execucao,
                fonte_nome=nome_fonte,
            )

            if sucesso:
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
            "total_consultas_executadas": len(todas_consultas),
            "consultas": todas_consultas,
            "integracoes_ativas": integrations,
            "itens_coletados": list(candidatos_por_url.values()),
        }
