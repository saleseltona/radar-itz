"""
Gerenciador de Fontes do Radar de Pautas de Imperatriz.
Responsável por carregar, validar, atualizar e cadastrar novas fontes dinamicamente
em `fontes.csv` e no banco de dados SQLite sem alteração no código.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.config import FONTES_CSV_PATH, mask_secrets
from src.database import RadarDatabase

logger = logging.getLogger("radar_imperatriz.source_manager")


class SourceManager:
    """Gerencia cadastro e integridade das fontes monitoradas."""

    def __init__(self, db: RadarDatabase, csv_path: Path | None = None) -> None:
        self.db = db
        self.csv_path = csv_path or FONTES_CSV_PATH
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_url(url: str) -> bool:
        """Valida se a URL é sintaticamente válida e possui domínio e esquema HTTP/HTTPS."""
        if not url or not isinstance(url, str):
            return False
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        if not parsed.netloc or "." not in parsed.netloc:
            return False
        return True

    def load_sources_from_csv(self) -> List[Dict[str, str]]:
        """Lê o arquivo fontes.csv de forma compatível com UTF-8 e delimitadores."""
        if not self.csv_path.exists():
            logger.warning("Arquivo de fontes não encontrado em %s", self.csv_path)
            return []

        raw_content = self.csv_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw_content:
            return []

        # Detecção de delimitador (, ou ;)
        first_line = raw_content.splitlines()[0]
        delimiter = ";" if ";" in first_line and "," not in first_line else ","

        fontes: List[Dict[str, str]] = []
        reader = csv.DictReader(raw_content.splitlines(), delimiter=delimiter)
        for row in reader:
            nome = (row.get("nome") or "").strip()
            tipo = (row.get("tipo") or "").strip()
            url = (row.get("url") or "").strip()
            prioridade = (row.get("prioridade") or "Média").strip()
            temas = (row.get("temas") or "").strip()

            if not nome or not url:
                continue

            if not self.validate_url(url):
                logger.warning("URL inválida descartada no cadastro de fontes: %s (%s)", url, nome)
                continue

            fonte_dict = {
                "nome": nome,
                "tipo": tipo,
                "url": url,
                "prioridade": prioridade,
                "temas": temas,
            }
            fontes.append(fonte_dict)
            self._sync_fonte_to_db(fonte_dict)

        return fontes

    def _sync_fonte_to_db(self, fonte: Dict[str, str]) -> None:
        """Sincroniza fonte do CSV para a base SQLite com metadados operacionais."""
        tipo = fonte.get("tipo", "").lower()
        url = fonte.get("url", "")

        is_instagram = "instagram.com" in url.lower() or "instagram" in tipo
        metodo_acesso = (
            "Instagram Graph API / Verificação de Perfil Público"
            if is_instagram
            else "HTTP Direto / Extrator Web e Portal de Transparência"
        )
        observacoes = (
            "Acesso a publicações restrito por autenticação/login wall da plataforma Meta; requer Instagram Graph API oficial."
            if is_instagram
            else "Portal web aberto ou página institucional com consulta pública regular."
        )

        dados_completos = {
            "nome": fonte["nome"],
            "tipo": fonte.get("tipo", "Geral"),
            "url": url,
            "prioridade": fonte.get("prioridade", "Média"),
            "temas": fonte.get("temas", ""),
            "situacao_funcionamento": "Cadastrada",
            "metodo_acesso": metodo_acesso,
            "observacoes_limitacoes": observacoes,
            "proxima_acao_recomendada": (
                "Configurar INSTAGRAM_GRAPH_TOKEN no .env para coleta automatizada de legendas"
                if is_instagram
                else "Consultar via requisição HTTP/RSS regular"
            ),
        }
        self.db.upsert_fonte(dados_completos)

    def register_new_source(
        self,
        nome: str,
        tipo: str,
        url: str,
        prioridade: str = "Média",
        temas: str = "",
        persist_csv: bool = True,
    ) -> bool:
        """Cadastra nova fonte sem modificar o código do sistema."""
        url_clean = url.strip()
        if not self.validate_url(url_clean):
            raise ValueError(f"URL inválida fornecida: {url_clean}")

        nome_clean = nome.strip()
        tipo_clean = tipo.strip()
        prioridade_clean = prioridade.strip() or "Média"
        temas_clean = temas.strip()

        fonte_dict = {
            "nome": nome_clean,
            "tipo": tipo_clean,
            "url": url_clean,
            "prioridade": prioridade_clean,
            "temas": temas_clean,
        }

        self._sync_fonte_to_db(fonte_dict)

        if persist_csv:
            self._append_to_csv(fonte_dict)

        logger.info("Nova fonte cadastrada com sucesso: %s (%s)", nome_clean, url_clean)
        return True

    def _append_to_csv(self, fonte: Dict[str, str]) -> None:
        """Adiciona fonte ao arquivo fontes.csv se ainda não existir."""
        todas = self.load_sources_from_csv()
        if any(f["url"].lower() == fonte["url"].lower() for f in todas):
            return

        with open(self.csv_path, mode="a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    fonte["nome"],
                    fonte["tipo"],
                    fonte["url"],
                    fonte["prioridade"],
                    fonte["temas"],
                ]
            )

    def update_source_status(
        self,
        url: str,
        status: str,
        sucesso: bool,
        erro: str = "",
        proxima_acao: str = "",
        metodo: Optional[str] = None,
        observacoes: Optional[str] = None,
    ) -> None:
        """Atualiza a situação operacional da fonte após tentativa de consulta."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        dados: Dict[str, str] = {
            "url": url,
            "situacao_funcionamento": status,
            "ultima_consulta": now_iso,
            "erro_identificado": mask_secrets(erro),
        }
        if sucesso:
            dados["ultima_coleta_sucesso"] = now_iso
            dados["erro_identificado"] = ""
        if proxima_acao:
            dados["proxima_acao_recomendada"] = proxima_acao
        if metodo:
            dados["metodo_acesso"] = metodo
        if observacoes:
            dados["observacoes_limitacoes"] = observacoes

        # Recupera registro atual para mesclar
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM fontes WHERE url = ?", (url,)).fetchone()
            if row:
                atual = dict(row)
                atual.update(dados)
                self.db.upsert_fonte(atual)
