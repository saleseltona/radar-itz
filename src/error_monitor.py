"""
Módulo de Monitoramento de Erros, Auditoria de Segurança e Proteção de Credenciais.
Garante que nenhuma chave ou segredo seja exposto em logs ou relatórios,
e registra falhas operacionais e de rede de forma estruturada.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.config import mask_secrets
from src.database import RadarDatabase

logger = logging.getLogger("radar_imperatriz")


class ErrorMonitor:
    """Monitor estruturado de erros e proteção de credenciais."""

    def __init__(self, db: RadarDatabase) -> None:
        self.db = db
        self.erros: List[Dict[str, Any]] = []

    def record_error(
        self,
        id_execucao: str,
        componente: str,
        alvo_url: str,
        mensagem: str,
        exception: Optional[Exception] = None,
    ) -> None:
        sanitized_url = mask_secrets(alvo_url)
        sanitized_msg = mask_secrets(mensagem)
        if exception:
            sanitized_msg = f"{sanitized_msg} (Detalhe: {mask_secrets(str(exception))})"

        logger.error("[%s] Falha em %s: %s", componente, sanitized_url, sanitized_msg)

        now_iso = datetime.now().isoformat(timespec="seconds")
        erro_dict = {
            "id_execucao": id_execucao,
            "componente": componente,
            "alvo_url": sanitized_url,
            "mensagem_erro": sanitized_msg,
            "data_registro": now_iso,
        }
        self.erros.append(erro_dict)
        self.db.log_error(id_execucao, componente, sanitized_url, sanitized_msg, now_iso)

    def get_errors_for_execution(self, id_execucao: str) -> List[Dict[str, Any]]:
        return self.db.get_errors_for_execution(id_execucao)
