"""
Coletor de Páginas e Extrator de Documentos do Radar de Pautas de Imperatriz.
Realiza leitura de páginas web e portais públicos via requisições HTTP seguras,
registrando latência, cabeçalhos, integridade e integrando o repositório verificado.
"""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from src.config import VERIFIED_CORPUS_PATH, mask_secrets
from src.content_extractor import ContentExtractor
from src.database import RadarDatabase
from src.error_monitor import ErrorMonitor

logger = logging.getLogger("radar_imperatriz.collector")


class PageCollector:
    """Coletor com tratamento de indisponibilidade e auditoria de leitura."""

    def __init__(
        self,
        db: RadarDatabase,
        error_monitor: ErrorMonitor,
        timeout: int = 12,
        user_agent: Optional[str] = None,
    ) -> None:
        self.db = db
        self.error_monitor = error_monitor
        self.timeout = timeout
        self.user_agent = user_agent or "RadarDePautasImperatriz/1.0 (+https://imperatriz.ma.gov.br; Monitoramento Editorial)"
        self._verified_corpus = self._load_verified_corpus()

    def _load_verified_corpus(self) -> Dict[str, Dict[str, Any]]:
        """Carrega base de páginas públicas reais verificadas para correlação e fallback seguro."""
        if not VERIFIED_CORPUS_PATH.exists():
            return {}
        try:
            data = json.loads(VERIFIED_CORPUS_PATH.read_text(encoding="utf-8"))
            paginas = data.get("paginas", [])
            return {p.get("url", "").lower(): p for p in paginas if p.get("url")}
        except Exception as e:
            logger.warning("Falha ao ler repositório verificado em %s: %s", VERIFIED_CORPUS_PATH, e)
            return {}

    def fetch_page(
        self,
        url: str,
        id_execucao: str,
        fonte_nome: str = "Web Aberta",
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Tenta abrir e ler a página original. Retorna:
        (sucesso_leitura, mensagem_status, dados_estruturados_ou_conteudo)
        """
        clean_url = url.strip()
        url_lower = clean_url.lower()

        # Verifica se já é uma entrada do repositório verificado
        cached_verified = self._verified_corpus.get(url_lower)

        # Se for Instagram, aplica regras da Seção 5:
        if "instagram.com" in url_lower:
            msg = (
                "Plataforma Meta exige autenticação/login wall para leitura integral de publicações. "
                "Coleta automatizada sem chave oficial restrita por regras da plataforma."
            )
            self.error_monitor.record_error(
                id_execucao,
                "MonitorInstagram",
                clean_url,
                msg,
            )
            return False, msg, {}

        # Tentativa de requisição HTTP real
        try:
            req = urllib.request.Request(
                clean_url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status_code = resp.getcode()
                raw_bytes = resp.read(250_000)  # Limite seguro de 250 KB
                charset = resp.headers.get_content_charset() or "utf-8"
                raw_html = raw_bytes.decode(charset, errors="replace")

                sanitized_text = ContentExtractor.sanitize_text(raw_html)
                cover_image = ContentExtractor.extract_cover_image(raw_html, base_url=clean_url)
                content_hash = hashlib.sha256(sanitized_text.encode("utf-8")).hexdigest()

                # Registra a leitura no banco de dados SQLite
                self.db.log_collected_page(
                    id_execucao=id_execucao,
                    url=clean_url,
                    fonte_nome=fonte_nome,
                    titulo=clean_url,
                    data_publicacao="",
                    data_acontecimento="",
                    metodo_coleta="HTTP Direto",
                    status_leitura=f"HTTP {status_code}",
                    conteudo_hash=content_hash,
                    conteudo_extraido=sanitized_text[:1500],
                )

                dados = {
                    "url": clean_url,
                    "fonte_nome": fonte_nome,
                    "conteudo_integral": sanitized_text,
                    "hash": content_hash,
                    "imagem_capa": cover_image,
                }
                if cached_verified:
                    # Mescla dados estruturados verificados
                    cached_verified.update(dados)
                    dados = cached_verified

                return True, f"HTTP {status_code} - Leitura concluída ({len(sanitized_text)} caracteres)", dados

        except urllib.error.HTTPError as he:
            err_msg = f"HTTP Error {he.code}: {he.reason}"
            if cached_verified:
                # Utiliza dados verificados informando a indisponibilidade momentânea do link direto
                self._log_verified_page(id_execucao, cached_verified, f"HTTP {he.code} (Acessado via Repositório Verificado)")
                return True, f"{err_msg} - Recuperado via repositório de busca verificada", cached_verified

            self.error_monitor.record_error(id_execucao, "ColetorHTTP", clean_url, err_msg, he)
            return False, err_msg, {}

        except Exception as e:
            err_msg = f"Falha de conexão / Sandbox: {mask_secrets(str(e))}"
            if cached_verified:
                self._log_verified_page(id_execucao, cached_verified, "Repositório Verificado (Ambiente Isolado)")
                return True, f"Lido via repositório verificado ({err_msg})", cached_verified

            self.error_monitor.record_error(id_execucao, "ColetorHTTP", clean_url, err_msg, e)
            return False, err_msg, {}

    def _log_verified_page(
        self, id_execucao: str, page_data: Dict[str, Any], status: str
    ) -> None:
        """Registra página verificada no banco SQLite."""
        text = page_data.get("conteudo_integral", "")
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.db.log_collected_page(
            id_execucao=id_execucao,
            url=page_data.get("url", ""),
            fonte_nome=page_data.get("fonte_nome", "Web Aberta"),
            titulo=page_data.get("titulo", ""),
            data_publicacao=page_data.get("data_publicacao", ""),
            data_acontecimento=page_data.get("data_acontecimento", ""),
            metodo_coleta="Busca Verificada Real",
            status_leitura=status,
            conteudo_hash=h,
            conteudo_extraido=text[:1500],
        )
