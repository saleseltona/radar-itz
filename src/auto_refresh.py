"""
Atualização automática disparada ao abrir o painel.

Ao carregar o dashboard (com token de administração), o navegador chama
POST /api/auto-atualizar. Este módulo roda em segundo plano, uma de cada vez,
só as tarefas cujo último disparo ficou mais antigo que o intervalo mínimo:

- varredura: ciclo de pautas (o mesmo do botão "Buscar Recentes"; regera o Clipping Diário);
- pncp: lotes incrementais do PNCP (o mesmo do botão "Atualizar PNCP");
- fiorilli: Portal da Transparência Fiorilli (o mesmo do botão "Sincronizar Portal Fiorilli").

Vários F5 seguidos não empilham execuções: enquanto uma rodada está em andamento,
novos disparos só devolvem o status, e cada tarefa respeita o intervalo mínimo.
O botão manual de cada tarefa usa a mesma trava, então manual e automático não rodam juntos.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import mask_secrets

logger = logging.getLogger("radar_imperatriz.auto")

TAREFAS: Tuple[str, ...] = ("varredura", "pncp", "fiorilli")
ROTULOS = {
    "varredura": "Varredura e Clipping",
    "pncp": "PNCP",
    "fiorilli": "Portal Fiorilli",
}

# Intervalos mínimos entre disparos automáticos (minutos), configuráveis por ambiente.
INTERVALO_PADRAO_MIN = 30.0
INTERVALO_FIORILLI_PADRAO_MIN = 180.0


class TarefaOcupada(RuntimeError):
    """A tarefa já está rodando (automática ou manual)."""


def _env_minutos(nome: str, padrao: float) -> float:
    raw = os.environ.get(nome, "").strip()
    if not raw:
        return padrao
    try:
        return max(0.0, float(raw.replace(",", ".")))
    except ValueError:
        return padrao


def intervalos_do_ambiente() -> Dict[str, float]:
    """Intervalos em segundos. RADAR_AUTO_INTERVALO_MINUTOS vale para varredura e PNCP."""
    geral = _env_minutos("RADAR_AUTO_INTERVALO_MINUTOS", INTERVALO_PADRAO_MIN)
    fiorilli = _env_minutos("RADAR_AUTO_INTERVALO_FIORILLI_MINUTOS", INTERVALO_FIORILLI_PADRAO_MIN)
    return {"varredura": geral * 60, "pncp": geral * 60, "fiorilli": fiorilli * 60}


def auto_atualizacao_habilitada() -> bool:
    return os.environ.get("RADAR_AUTO_ATUALIZAR", "1").strip().lower() not in ("0", "false", "nao", "não", "off")


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resumir(resultado: Any) -> Dict[str, Any]:
    """Guarda só números e textos curtos do resultado (nada de listas grandes no status)."""
    if not isinstance(resultado, dict):
        return {}
    resumo: Dict[str, Any] = {}
    for chave, valor in resultado.items():
        if isinstance(valor, bool) or isinstance(valor, (int, float)):
            resumo[chave] = valor
        elif isinstance(valor, str) and len(valor) <= 300:
            resumo[chave] = mask_secrets(valor)
    return resumo


class AutoAtualizador:
    """Coordena as tarefas automáticas de um processo do servidor."""

    def __init__(
        self,
        executores: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]],
        intervalos: Optional[Dict[str, float]] = None,
        relogio: Callable[[], float] = time.time,
        iniciar: Optional[Callable[[Callable[[], None]], None]] = None,
    ) -> None:
        self.executores = dict(executores)
        self.intervalos = dict(intervalos or intervalos_do_ambiente())
        self.relogio = relogio
        self._iniciar = iniciar or self._iniciar_thread
        self._estado_lock = threading.Lock()
        self._travas = {nome: threading.Lock() for nome in TAREFAS}
        self._rodada_ativa = False
        self._fila: List[str] = []
        self._tarefa_atual: Optional[str] = None
        self._rodada_inicio: Optional[float] = None
        self._rodada_fim: Optional[float] = None
        self._tarefas: Dict[str, Dict[str, Any]] = {
            nome: {"ultimo_disparo": None, "ultima_conclusao": None, "ok": None, "erro": None, "resultado": {}}
            for nome in TAREFAS
        }

    @staticmethod
    def _iniciar_thread(alvo: Callable[[], None]) -> None:
        threading.Thread(target=alvo, name="radar-auto-atualizar", daemon=True).start()

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def _vencidas(self, agora: float) -> List[str]:
        vencidas = []
        for nome in TAREFAS:
            if nome not in self.executores:
                continue
            ultimo = self._tarefas[nome]["ultimo_disparo"]
            if ultimo is None or agora - ultimo >= self.intervalos.get(nome, INTERVALO_PADRAO_MIN * 60):
                vencidas.append(nome)
        return vencidas

    def tarefa_rodando(self, nome: str) -> bool:
        return self._travas[nome].locked()

    def status(self) -> Dict[str, Any]:
        with self._estado_lock:
            agora = self.relogio()
            tarefas = {}
            for nome in TAREFAS:
                info = self._tarefas[nome]
                intervalo = self.intervalos.get(nome, INTERVALO_PADRAO_MIN * 60)
                ultimo = info["ultimo_disparo"]
                proxima = 0 if ultimo is None else max(0, int(ultimo + intervalo - agora))
                if self._travas[nome].locked():
                    situacao = "rodando"
                elif nome in self._fila:
                    situacao = "na_fila"
                elif info["ok"] is None:
                    situacao = "nunca_rodou"
                else:
                    situacao = "ok" if info["ok"] else "erro"
                tarefas[nome] = {
                    "rotulo": ROTULOS[nome],
                    "situacao": situacao,
                    "ultimo_disparo": _iso(ultimo),
                    "ultima_conclusao": _iso(info["ultima_conclusao"]),
                    "ok": info["ok"],
                    "erro": info["erro"],
                    "resultado": dict(info["resultado"]),
                    "intervalo_minutos": round(intervalo / 60, 1),
                    "proxima_em_segundos": proxima,
                }
            return {
                "em_andamento": self._rodada_ativa,
                "tarefa_atual": self._tarefa_atual,
                "fila": list(self._fila),
                "rodada_inicio": _iso(self._rodada_inicio),
                "rodada_fim": _iso(self._rodada_fim),
                "tarefas": tarefas,
            }

    # ------------------------------------------------------------------
    # Disparo automático
    # ------------------------------------------------------------------
    def disparar(self, contexto: Optional[Dict[str, Any]] = None, forcar: bool = False) -> Dict[str, Any]:
        """Agenda as tarefas vencidas em segundo plano. Nunca bloqueia nem empilha rodadas."""
        contexto = dict(contexto or {})
        with self._estado_lock:
            if self._rodada_ativa:
                iniciou: List[str] = []
            else:
                agora = self.relogio()
                iniciou = [n for n in TAREFAS if n in self.executores] if forcar else self._vencidas(agora)
                if iniciou:
                    self._rodada_ativa = True
                    self._fila = list(iniciou)
                    self._rodada_inicio = agora
                    self._rodada_fim = None
        if iniciou:
            self._iniciar(lambda: self._rodar(iniciou, contexto))
        st = self.status()
        st["iniciadas"] = iniciou
        return st

    def _rodar(self, nomes: List[str], contexto: Dict[str, Any]) -> None:
        try:
            for nome in nomes:
                with self._estado_lock:
                    if nome in self._fila:
                        self._fila.remove(nome)
                try:
                    self._executar(nome, contexto, bloquear=False)
                except TarefaOcupada:
                    # Botão manual da mesma tarefa já está rodando: não repete.
                    with self._estado_lock:
                        self._tarefas[nome]["ultimo_disparo"] = self.relogio()
                except Exception:  # noqa: BLE001 - erro já registrado em _executar
                    pass
        finally:
            with self._estado_lock:
                self._rodada_ativa = False
                self._tarefa_atual = None
                self._fila = []
                self._rodada_fim = self.relogio()

    # ------------------------------------------------------------------
    # Execução (automática ou manual)
    # ------------------------------------------------------------------
    def _executar(self, nome: str, contexto: Dict[str, Any], bloquear: bool) -> Dict[str, Any]:
        trava = self._travas[nome]
        if not trava.acquire(blocking=bloquear):
            raise TarefaOcupada(f"{ROTULOS[nome]} já está em andamento. Aguarde terminar.")
        try:
            with self._estado_lock:
                self._tarefa_atual = nome if self._rodada_ativa else self._tarefa_atual
                self._tarefas[nome]["ultimo_disparo"] = self.relogio()
            try:
                resultado = self.executores[nome](contexto) or {}
                ok = not resultado.get("erro")
                erro = None if ok else mask_secrets(str(resultado.get("erro")))[:300]
            except Exception as exc:  # noqa: BLE001
                logger.error("Atualização automática '%s' falhou: %s", nome, mask_secrets(str(exc)))
                with self._estado_lock:
                    self._tarefas[nome].update(
                        ultima_conclusao=self.relogio(), ok=False, erro=mask_secrets(str(exc))[:300], resultado={}
                    )
                raise
            with self._estado_lock:
                self._tarefas[nome].update(
                    ultima_conclusao=self.relogio(), ok=ok, erro=erro, resultado=_resumir(resultado)
                )
            return resultado
        finally:
            trava.release()

    def executar_manual(self, nome: str, executor: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        """Roda a ação de um botão com a mesma trava da automática. Lança TarefaOcupada se já rodando."""
        trava = self._travas[nome]
        if not trava.acquire(blocking=False):
            raise TarefaOcupada(f"{ROTULOS[nome]} já está em andamento (atualização automática). Aguarde terminar.")
        try:
            return self._executar_travado(nome, executor)
        finally:
            trava.release()

    def iniciar_manual(self, nome: str, executor: Callable[[], Dict[str, Any]]) -> None:
        """
        Igual a `executar_manual`, mas em segundo plano: devolve na hora e o resultado aparece
        em `status()["tarefas"][nome]`. Evita requisições longas (o navegador ou o proxy desistem
        e o servidor fica com "Broken pipe"). Lança TarefaOcupada se a tarefa já estiver rodando.
        """
        trava = self._travas[nome]
        if not trava.acquire(blocking=False):
            raise TarefaOcupada(f"{ROTULOS[nome]} já está em andamento. Aguarde terminar.")
        with self._estado_lock:
            self._tarefas[nome]["ultimo_disparo"] = self.relogio()

        def alvo() -> None:
            try:
                self._executar_travado(nome, executor)
            except Exception as exc:  # noqa: BLE001 - erro já registrado no status
                logger.error("Tarefa '%s' em segundo plano falhou: %s", nome, mask_secrets(str(exc)))
            finally:
                trava.release()

        try:
            self._iniciar(alvo)
        except Exception:
            trava.release()
            raise

    def _executar_travado(self, nome: str, executor: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        """Executa com a trava de `nome` já adquirida e registra o resultado no status."""
        with self._estado_lock:
            self._tarefas[nome]["ultimo_disparo"] = self.relogio()
        try:
            resultado = executor() or {}
        except Exception as exc:
            with self._estado_lock:
                self._tarefas[nome].update(
                    ultima_conclusao=self.relogio(), ok=False, erro=mask_secrets(str(exc))[:300], resultado={}
                )
            raise
        with self._estado_lock:
            ok = not resultado.get("erro")
            self._tarefas[nome].update(
                ultima_conclusao=self.relogio(),
                ok=ok,
                erro=None if ok else mask_secrets(str(resultado.get("erro")))[:300],
                resultado=_resumir(resultado),
            )
        return resultado
