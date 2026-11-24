"""
Detector de Duplicidades e Memória Histórica Persistente (Seção 11).
Compara novas descobertas com pautas registradas no banco SQLite, funde fontes
do mesmo fato em uma única pauta enriquecida e registra atualizações sem apagar o histórico.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.database import RadarDatabase

logger = logging.getLogger("radar_imperatriz.deduplicator")


class Deduplicator:
    """Controla duplicidade, consolidação de fontes e memória histórica."""

    def __init__(self, db: RadarDatabase) -> None:
        self.db = db

    @staticmethod
    def normalize_title(title: str) -> str:
        """Normaliza título para comparação (letras minúsculas, sem pontuação excessiva)."""
        if not title:
            return ""
        norm = title.lower()
        norm = re.sub(r"[^\w\s]", " ", norm)
        norm = re.sub(r"\s+", " ", norm).strip()
        return norm

    @classmethod
    def calculate_similarity(cls, text_a: str, text_b: str) -> float:
        """Calcula similaridade textual via SequenceMatcher."""
        norm_a = cls.normalize_title(text_a)
        norm_b = cls.normalize_title(text_b)
        if not norm_a or not norm_b:
            return 0.0
        return difflib.SequenceMatcher(None, norm_a, norm_b).ratio()

    def find_matching_pauta(
        self,
        url: str,
        titulo: str,
        documentos: List[str],
        tema: str,
        existing_pautas: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Verifica se a descoberta já corresponde a uma pauta existente por:
        1. URL idêntica.
        2. Identificador de documento oficial coincidente (ex: Contrato nº 02/2026).
        3. Mesma operação policial / judicial com similaridade temática.
        4. Alta similaridade de título (> 0.72) e mesmo tema.
        """
        for p in existing_pautas:
            # 1. Checagem direta de URL
            if url.lower() == p.get("url_original", "").lower():
                return p
            urls_comp = [u.strip().lower() for u in p.get("urls_complementares", "").split(";") if u.strip()]
            if url.lower() in urls_comp:
                return p

            # 2. Identificadores de documentos e processos oficiais
            p_docs = [d.strip().lower() for d in p.get("identificadores_documentos", "").split(";") if d.strip()]
            for doc in documentos:
                doc_clean = doc.strip().lower()
                if doc_clean in p_docs:
                    return p

            # 3. Similaridade temática e textual
            sim = self.calculate_similarity(titulo, p.get("titulo_jornalistico", ""))
            if sim >= 0.72 and tema.lower() == p.get("tema_especifico", "").lower():
                return p

        return None

    def process_discovery(
        self,
        item: Dict[str, Any],
        id_execucao: str,
        existing_pautas_cache: List[Dict[str, Any]],
    ) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Classifica a descoberta em:
        - ('DUPLICATA_CONSOLIDADA', pauta_existente, dados_atualizacao)
        - ('ATUALIZACAO', pauta_existente, dados_atualizacao)
        - ('NOVA', None, item_pauta_novo)
        """
        url = item.get("url", "")
        titulo = item.get("titulo", "")
        docs = item.get("documentos_citados", [])
        tema = item.get("tema_especifico", "")

        match = self.find_matching_pauta(url, titulo, docs, tema, existing_pautas_cache)

        if match:
            # Já existe no banco: verifica se adiciona nova fonte/URL complementar
            old_sources = [s.strip() for s in match.get("fontes_complementares", "").split(";") if s.strip()]
            old_urls = [u.strip() for u in match.get("urls_complementares", "").split(";") if u.strip()]

            nova_fonte = item.get("fonte_nome", "")
            nova_url = url

            mudou = False
            if nova_fonte and nova_fonte != match.get("fonte_principal") and nova_fonte not in old_sources:
                old_sources.append(nova_fonte)
                mudou = True
            if nova_url and nova_url != match.get("url_original") and nova_url not in old_urls:
                old_urls.append(nova_url)
                mudou = True

            # Verifica se há novos fatos ou evidências
            novas_evidencias = item.get("fatos_confirmados", [])
            evidencias_atuais = match.get("evidencias_verificadas", "")
            for ev in novas_evidencias:
                if ev not in evidencias_atuais:
                    evidencias_atuais += f" | [Atualização/Segunda Fonte]: {ev}"
                    mudou = True

            if mudou:
                updates = {
                    "fontes_complementares": "; ".join(old_sources),
                    "urls_complementares": "; ".join(old_urls),
                    "evidencias_verificadas": evidencias_atuais,
                    "situacao_editorial": "Atualizada com nova fonte e evidências complementares",
                }
                return "DUPLICATA_CONSOLIDADA", match, updates

            return "DUPLICATA_IGNORADA", match, None

        return "NOVA", None, item
