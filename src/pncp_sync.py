"""
Sincronização PNCP incremental e em lotes, segura para funções serverless com tempo limite.

Cada chamada de `executar_lote` processa só o que cabe no orçamento de tempo, grava os
contratos (upsert por `numero_controle_pncp`) e devolve um cursor (`proxima_janela`) para
a próxima chamada. O frontend repete a chamada até `parcial == False`.

Fluxo:
- Carga completa (uma vez): janelas mensais de 2025 e 2026 × órgãos de Imperatriz.
- Depois: incremental desde `ultima_data_sincronizada - MARGEM_DIAS`, consultando contratos
  novos ou alterados (endpoint de atualização do PNCP; cai para o de publicação se indisponível).
- Lock no banco impede duas sincronizações simultâneas.
"""
from __future__ import annotations

import calendar
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import PNCP_ORGAOS_IMPERATRIZ, mask_secrets

logger = logging.getLogger("radar_imperatriz.pncp_sync")

PNCP_CONSULTA_BASE = "https://pncp.gov.br/api/consulta/v1"
ENDPOINT_PUBLICACAO = f"{PNCP_CONSULTA_BASE}/contratos"
ENDPOINT_ATUALIZACAO = f"{PNCP_CONSULTA_BASE}/contratos/atualizacao"

ANOS_CARGA_COMPLETA: Tuple[int, ...] = (2025, 2026)
MARGEM_DIAS = 5
TEMPO_LIMITE_SEGUNDOS = 40.0  # Vercel maxDuration = 60s; sobra margem para gravar e responder
HTTP_TIMEOUT_SEGUNDOS = 15
MAX_JANELAS_POR_LOTE = 12
TAMANHO_PAGINA = 50
LOCK_TTL_SEGUNDOS = 90.0  # maior que maxDuration: um lock órfão expira sozinho

MODO_CARGA = "carga_completa"
MODO_INCREMENTAL = "incremental"

# Campos comparados para decidir se um contrato existente mudou no PNCP
CAMPOS_TEXTO = (
    "orgao_cnpj", "orgao_sigla", "orgao_nome", "unidade_nome", "numero_contrato", "processo",
    "tipo_contrato", "fornecedor_cnpj", "fornecedor_nome", "objeto", "data_assinatura",
    "data_publicacao", "data_vigencia_inicio", "data_vigencia_fim", "url_pncp",
)
CAMPOS_VALOR = ("valor_inicial", "valor_global", "valor_acumulado")

# fetch(endpoint, cnpj, data_ini, data_fim, pagina, tamanho, timeout) -> (status, payload)
FetchFn = Callable[[str, str, str, str, int, int, int], Tuple[int, Dict[str, Any]]]


class SyncEmAndamento(RuntimeError):
    """Outra sincronização PNCP já está em execução."""


class CursorInvalido(ValueError):
    """Cursor `proxima_janela` recebido do cliente é inválido."""


# ---------------------------------------------------------------------------
# Comparação / idempotência
# ---------------------------------------------------------------------------

def _num(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def contrato_mudou(novo: Dict[str, Any], existente: Dict[str, Any], comparar_valores: bool = True) -> bool:
    if int(_num(novo.get("ano_contrato"))) != int(_num(existente.get("ano_contrato"))):
        return True
    for campo in CAMPOS_TEXTO:
        if str(novo.get(campo) or "") != str(existente.get(campo) or ""):
            return True
    if comparar_valores:
        for campo in CAMPOS_VALOR:
            if abs(_num(novo.get(campo)) - _num(existente.get(campo))) > 0.005:
                return True
    return False


def classificar_contratos(
    contratos: List[Dict[str, Any]],
    existentes: Dict[str, Dict[str, Any]],
    retificacoes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Separa contratos em inseridos / atualizados / ignorados (sem mudança).
    Em contratos com retificação manual os valores do PNCP não sobrescrevem a correção:
    só os demais campos são comparados, e uma mudança de valor no PNCP é relatada
    em `retificacoes_divergentes` (valor original registrado × valor novo do PNCP).
    """
    gravar: List[Dict[str, Any]] = []
    inseridos = atualizados = ignorados = 0
    divergentes: List[Dict[str, Any]] = []
    preservadas = 0
    for c in contratos:
        ctrl = str(c.get("numero_controle_pncp") or "").strip()
        if not ctrl:
            continue
        ret = retificacoes.get(ctrl)
        if ret is not None:
            preservadas += 1
            original = _num(ret.get("valor_original_pncp"))
            if abs(_num(c.get("valor_global")) - original) > 0.005:
                divergentes.append(
                    {
                        "numero_controle_pncp": ctrl,
                        "valor_original_registrado": original,
                        "valor_pncp_atual": _num(c.get("valor_global")),
                        "valor_corrigido_mantido": _num(ret.get("valor_corrigido")),
                    }
                )
        old = existentes.get(ctrl)
        if old is None:
            inseridos += 1
            gravar.append(c)
        elif contrato_mudou(c, old, comparar_valores=ret is None):
            atualizados += 1
            gravar.append(c)
        else:
            ignorados += 1
    return {
        "inseridos": inseridos,
        "atualizados": atualizados,
        "ignorados": ignorados,
        "retificacoes_preservadas": preservadas,
        "retificacoes_divergentes": divergentes,
        "_gravar": gravar,
    }


# ---------------------------------------------------------------------------
# Planejamento de janelas e cursor
# ---------------------------------------------------------------------------

def _parse_date(raw: Any) -> date:
    return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()


def _janelas_mensais(inicio: date, fim: date) -> List[Tuple[date, date]]:
    out: List[Tuple[date, date]] = []
    cursor = date(inicio.year, inicio.month, 1)
    while cursor <= fim:
        ultimo = calendar.monthrange(cursor.year, cursor.month)[1]
        a = max(inicio, cursor)
        b = min(fim, date(cursor.year, cursor.month, ultimo))
        if a <= b:
            out.append((a, b))
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
    return out


def planejar_janelas(cursor: Dict[str, Any], orgaos: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Lista determinística de janelas (mês × órgão) a partir do cursor; mesma entrada, mesmo plano."""
    inicio = _parse_date(cursor["inicio"])
    ate = _parse_date(cursor["ate"])
    janelas: List[Dict[str, Any]] = []
    for a, b in _janelas_mensais(inicio, ate):
        for idx, orgao in enumerate(orgaos):
            janelas.append({"orgao_idx": idx, "sigla": orgao["sigla"], "ini": a, "fim": b})
    return janelas


def novo_cursor(modo: str, inicio: date, ate: date) -> Dict[str, Any]:
    return {
        "modo": modo,
        "inicio": inicio.isoformat(),
        "ate": ate.isoformat(),
        "janela": 0,
        "pagina": 1,
    }


def validar_cursor(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise CursorInvalido("proxima_janela deve ser um objeto.")
    modo = raw.get("modo")
    if modo not in (MODO_CARGA, MODO_INCREMENTAL):
        raise CursorInvalido("proxima_janela.modo inválido.")
    try:
        inicio = _parse_date(raw.get("inicio"))
        ate = _parse_date(raw.get("ate"))
        janela = int(raw.get("janela", 0))
        pagina = int(raw.get("pagina", 1))
    except (TypeError, ValueError):
        raise CursorInvalido("proxima_janela com datas ou índices inválidos.")
    if inicio > ate or (ate - inicio).days > 800 or janela < 0 or not 1 <= pagina <= 10_000:
        raise CursorInvalido("proxima_janela fora dos limites.")
    return {"modo": modo, "inicio": inicio.isoformat(), "ate": ate.isoformat(), "janela": janela, "pagina": pagina}


# ---------------------------------------------------------------------------
# Acesso HTTP ao PNCP (substituível nos testes)
# ---------------------------------------------------------------------------

def fetch_pagina_pncp(
    endpoint: str,
    cnpj_orgao: str,
    data_inicial: str,
    data_final: str,
    pagina: int,
    tamanho_pagina: int,
    timeout: int,
) -> Tuple[int, Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "cnpjOrgao": cnpj_orgao,
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
        }
    )
    req = urllib.request.Request(
        f"{endpoint}?{params}",
        headers={
            "Accept": "application/json, */*",
            "User-Agent": "RadarDePautasImperatriz/2.0 (+https://imperatriz.ma.gov.br)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.getcode()
        if status == 204:
            return 204, {"data": [], "totalPaginas": 0}
        raw = resp.read().decode("utf-8", errors="replace").strip()
    if not raw:
        return status, {"data": [], "totalPaginas": 0}
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ValueError(f"PNCP respondeu conteúdo que não é JSON (HTTP {status}).")
    if isinstance(payload, list):
        return status, {"data": payload, "totalPaginas": 1}
    return status, payload if isinstance(payload, dict) else {"data": [], "totalPaginas": 0}


def _descrever_erro(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"PNCP respondeu HTTP {exc.code}"
    texto = str(exc) or exc.__class__.__name__
    if "timed out" in texto.lower() or isinstance(exc, TimeoutError):
        return "PNCP não respondeu dentro do tempo limite"
    return mask_secrets(texto)[:200]


# ---------------------------------------------------------------------------
# Execução de um lote
# ---------------------------------------------------------------------------

def _resolver_cursor(
    estado: Dict[str, Any],
    cursor_cliente: Optional[Dict[str, Any]],
    modo: str,
    hoje: date,
    estado_cliente: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if cursor_cliente is not None:
        return validar_cursor(cursor_cliente)
    if modo == MODO_CARGA:
        return novo_cursor(MODO_CARGA, date(min(ANOS_CARGA_COMPLETA), 1, 1), hoje)
    pendente = estado.get("cursor_pendente")
    if pendente and modo == "auto":
        try:
            return validar_cursor(pendente)
        except CursorInvalido:
            pass
    carga_ok = bool(estado.get("carga_completa_concluida"))
    ultima = estado.get("ultima_data_sincronizada")
    # Sem estado persistente no servidor (ex.: SQLite efêmero), aceita a dica do navegador
    if not estado and isinstance(estado_cliente, dict):
        carga_ok = bool(estado_cliente.get("carga_completa_concluida"))
        ultima = estado_cliente.get("ultima_data_sincronizada")
    if not carga_ok and modo != MODO_INCREMENTAL:
        return novo_cursor(MODO_CARGA, date(min(ANOS_CARGA_COMPLETA), 1, 1), hoje)
    try:
        desde = _parse_date(ultima) - timedelta(days=MARGEM_DIAS)
    except (TypeError, ValueError):
        desde = hoje - timedelta(days=60)
    desde = max(desde, date(min(ANOS_CARGA_COMPLETA), 1, 1))
    return novo_cursor(MODO_INCREMENTAL, min(desde, hoje), hoje)


def executar_lote(
    db: Any,
    normalizar: Callable[[Dict[str, Any], Dict[str, str], int], Dict[str, Any]],
    cursor: Optional[Dict[str, Any]] = None,
    modo: str = "auto",
    estado_cliente: Optional[Dict[str, Any]] = None,
    fetch: FetchFn = fetch_pagina_pncp,
    hoje: Optional[date] = None,
    relogio: Callable[[], float] = time.monotonic,
    agora_ts: Callable[[], float] = time.time,
    tempo_limite: float = TEMPO_LIMITE_SEGUNDOS,
    http_timeout: int = HTTP_TIMEOUT_SEGUNDOS,
    max_janelas: int = MAX_JANELAS_POR_LOTE,
    orgaos: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Processa um lote limitado por tempo e nº de janelas. Lança SyncEmAndamento se houver lock."""
    if modo not in ("auto", MODO_CARGA, MODO_INCREMENTAL):
        modo = "auto"
    orgaos = orgaos or list(PNCP_ORGAOS_IMPERATRIZ)
    hoje = hoje or datetime.now().date()
    owner = uuid.uuid4().hex
    if not db.acquire_pncp_sync_lock(owner, LOCK_TTL_SEGUNDOS, agora_ts()):
        raise SyncEmAndamento("Já existe uma sincronização do PNCP em andamento. Aguarde e tente de novo.")

    try:
        estado = db.get_pncp_sync_state() or {}
        cur = _resolver_cursor(estado, cursor, modo, hoje, estado_cliente)
        janelas = planejar_janelas(cur, orgaos)
        endpoint = ENDPOINT_ATUALIZACAO if cur["modo"] == MODO_INCREMENTAL else ENDPOINT_PUBLICACAO
        endpoint_usado = endpoint

        totais = {"inseridos": 0, "atualizados": 0, "ignorados": 0, "retificacoes_preservadas": 0}
        divergentes: List[Dict[str, Any]] = []
        erros: List[str] = []
        idx, pagina = cur["janela"], cur["pagina"]
        janelas_neste_lote = 0
        inicio = relogio()
        motivo_pausa = ""

        while idx < len(janelas):
            if janelas_neste_lote >= max_janelas:
                motivo_pausa = "limite_janelas"
                break
            if relogio() - inicio + http_timeout > tempo_limite:
                motivo_pausa = "limite_tempo"
                break
            jan = janelas[idx]
            orgao = orgaos[jan["orgao_idx"]]
            ini_s, fim_s = jan["ini"].strftime("%Y%m%d"), jan["fim"].strftime("%Y%m%d")
            try:
                try:
                    status, payload = fetch(endpoint_usado, orgao["cnpj"], ini_s, fim_s, pagina, TAMANHO_PAGINA, http_timeout)
                except urllib.error.HTTPError as he:
                    if he.code == 404 and endpoint_usado == ENDPOINT_ATUALIZACAO:
                        endpoint_usado = ENDPOINT_PUBLICACAO
                        status, payload = fetch(endpoint_usado, orgao["cnpj"], ini_s, fim_s, pagina, TAMANHO_PAGINA, http_timeout)
                    elif he.code == 204:
                        status, payload = 204, {"data": [], "totalPaginas": 0}
                    else:
                        raise
            except Exception as exc:  # erro do PNCP: para aqui e mantém o cursor nesta página
                erros.append(f"{orgao['sigla']} {ini_s}-{fim_s} pág. {pagina}: {_descrever_erro(exc)}")
                motivo_pausa = "erro_pncp"
                break

            rows = [] if status == 204 else (payload.get("data") or [])
            normalizados = [normalizar(r, orgao, jan["ini"].year) for r in rows if isinstance(r, dict)]
            if normalizados:
                res = db.upsert_pncp_contratos_detalhado(normalizados)
                for k in totais:
                    totais[k] += int(res.get(k, 0) or 0)
                divergentes.extend(res.get("retificacoes_divergentes") or [])
            try:
                total_paginas = int(payload.get("totalPaginas") or 0)
            except (TypeError, ValueError):
                total_paginas = 0
            if not rows or pagina >= total_paginas:
                idx, pagina = idx + 1, 1
                janelas_neste_lote += 1
            else:
                pagina += 1

        concluido = idx >= len(janelas)
        proximo = None if concluido else dict(cur, janela=idx, pagina=pagina)

        novo_estado = dict(estado)
        novo_estado["ultima_execucao"] = datetime.now().isoformat(timespec="seconds")
        if concluido:
            novo_estado["cursor_pendente"] = None
            novo_estado["ultima_data_sincronizada"] = cur["ate"]
            if cur["modo"] == MODO_CARGA:
                novo_estado["carga_completa_concluida"] = True
                novo_estado["carga_completa_anos"] = list(ANOS_CARGA_COMPLETA)
        else:
            novo_estado["cursor_pendente"] = proximo
        db.save_pncp_sync_state(novo_estado)

        progresso = 100.0 if not janelas else round(100.0 * idx / len(janelas), 1)
        janela_atual = None
        if proximo is not None:
            j = janelas[idx]
            janela_atual = {"orgao": j["sigla"], "inicio": j["ini"].isoformat(), "fim": j["fim"].isoformat(), "pagina": pagina}

        return {
            "ok": not erros,
            "modo": cur["modo"],
            "inseridos": totais["inseridos"],
            "atualizados": totais["atualizados"],
            "ignorados": totais["ignorados"],
            "total_sincronizados": totais["inseridos"] + totais["atualizados"],
            "parcial": not concluido,
            "proxima_janela": proximo,
            "janela_atual": janela_atual,
            "motivo_pausa": motivo_pausa or None,
            "ultima_data_sincronizada": novo_estado.get("ultima_data_sincronizada"),
            "carga_completa_concluida": bool(novo_estado.get("carga_completa_concluida")),
            "janelas_total": len(janelas),
            "janelas_concluidas": idx,
            "progresso_pct": progresso,
            "periodo": {"inicio": cur["inicio"], "fim": cur["ate"]},
            "endpoint": "atualizacao" if endpoint_usado == ENDPOINT_ATUALIZACAO else "publicacao",
            "retificacoes_preservadas": totais["retificacoes_preservadas"],
            "retificacoes_divergentes": divergentes[:50],
            "erros": erros,
        }
    finally:
        try:
            db.release_pncp_sync_lock(owner)
        except Exception as exc:  # lock expira sozinho pelo TTL
            logger.warning("Falha ao liberar lock PNCP: %s", mask_secrets(str(exc)))
