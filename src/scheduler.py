"""
Módulo de Execução Automatizada e Agendador do Radar de Pautas de Imperatriz (Seção 15).
Suporta modo manual e modo agendado real com proteção contra sobreposição (lockfile)
e recuperação de tarefas interrompidas.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

from src.config import LOCK_FILE_PATH
from src.database import RadarDatabase

logger = logging.getLogger("radar_imperatriz.scheduler")


class ExecutionLockError(RuntimeError):
    """Exceção levantada quando já existe uma execução em andamento."""


@contextmanager
def execution_lock(lock_path: Path | None = None) -> Iterator[None]:
    """Garante que apenas uma instância do coletor execute por vez."""
    path = lock_path or LOCK_FILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        # Verifica se o processo anterior está ativo ou se é lock órfão
        try:
            pid_str = path.read_text(encoding="utf-8").strip()
            msg = f"Arquivo de trava existente ({path}) criado pelo PID {pid_str}. Outra execução está em andamento."
            raise ExecutionLockError(msg)
        except (ValueError, OSError):
            path.unlink(missing_ok=True)

    try:
        path.write_text(str(os.getpid()), encoding="utf-8")
        yield
    finally:
        path.unlink(missing_ok=True)


class RadarScheduler:
    """Orquestra execuções agendadas ou manuais com tolerância a falhas."""

    def __init__(self, db: RadarDatabase, task_fn: Callable[..., Any]) -> None:
        self.db = db
        self.task_fn = task_fn

    def run_manual(self, **kwargs: Any) -> Any:
        """Executa um ciclo avulso sob demanda no modo manual."""
        recuperadas = self.db.recover_interrupted_executions()
        if recuperadas > 0:
            logger.info("Recuperadas %d execuções interrompidas anteriores.", recuperadas)

        with execution_lock():
            return self.task_fn(modo="manual", **kwargs)

    def run_scheduled_loop(
        self,
        interval_seconds: int = 3600,
        max_iterations: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        """
        Executa pesquisas em intervalos regulares.
        Evita sobreposição de tarefas e recupera execuções interrompidas.
        """
        logger.info(
            "Iniciando modo agendado com intervalo de %d segundos (max: %s)...",
            interval_seconds,
            str(max_iterations or "infinito"),
        )
        iterations = 0
        while True:
            iterations += 1
            logger.info("--- Disparando ciclo agendado #%d em %s ---", iterations, datetime.now().isoformat())
            try:
                self.run_manual(**kwargs)
            except ExecutionLockError as el:
                logger.warning("Ciclo agendado postergado: %s", el)
            except Exception as e:
                logger.error("Erro no ciclo agendado #%d: %s", iterations, e, exc_info=True)

            if max_iterations and iterations >= max_iterations:
                logger.info("Limite de %d iterações atingido no agendador.", max_iterations)
                break

            time.sleep(interval_seconds)
