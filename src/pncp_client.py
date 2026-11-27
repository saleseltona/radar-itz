"""
Cliente Oficial da API de Consulta Pública do PNCP (Portal Nacional de Contratações Públicas).
Coleta contratos públicos dos órgãos de Imperatriz (MA) para os exercícios 2024, 2025 e 2026
usando exclusivamente a biblioteca padrão Python 3 (`urllib.request`, `json`).
"""
from __future__ import annotations

import calendar
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.config import PNCP_ANOS_MONITORADOS, PNCP_ORGAOS_IMPERATRIZ
from src.database import RadarDatabase

logger = logging.getLogger("radar_imperatriz.pncp")

PNCP_CONTRATOS_API = "https://pncp.gov.br/api/consulta/v1/contratos"

# Retificações documentais verificadas contra erro evidente de digitação no lançamento do PNCP
PNCP_VALUE_CORRECTIONS: Dict[str, float] = {
    # Processo 02.18.00.033/2026-SEDES / Pregão Eletrônico 006/2025 (BATISTA E COELHO LTDA):
    # lançado no PNCP como R$ 1.682.632.750,00 (1000x), valor real em contrato: R$ 1.682.632,75
    "06158455000116-2-000069/2026": 1682632.75,
}


class PncpClient:
    """Conector resiliente para consulta e cache local em SQLite dos contratos do PNCP."""

    def __init__(self, db: RadarDatabase, timeout: int = 40) -> None:
        self.db = db
        self.timeout = timeout

    def _fetch_page(
        self,
        cnpj_orgao: str,
        data_inicial: str,
        data_final: str,
        pagina: int = 1,
        tamanho_pagina: int = 100,
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
        url = f"{PNCP_CONTRATOS_API}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, */*",
                "User-Agent": "RadarDePautasImperatriz/2.0 (+https://imperatriz.ma.gov.br)",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            status = resp.getcode()
            if status == 204:
                return 204, {"data": [], "totalPaginas": 0, "totalRegistros": 0}
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return status, {"data": [], "totalPaginas": 0, "totalRegistros": 0}
            payload = json.loads(raw)
            if isinstance(payload, list):
                return status, {"data": payload, "totalPaginas": 1, "totalRegistros": len(payload)}
            return status, payload

    @staticmethod
    def normalize_contrato(
        raw_item: Dict[str, Any], orgao_meta: Dict[str, str], fallback_ano: int = 2026
    ) -> Dict[str, Any]:
        """Normaliza o payload JSON da API `/v1/contratos` do PNCP para o schema do banco."""
        orgao_ent = raw_item.get("orgaoEntidade") or {}
        unidade_org = raw_item.get("unidadeOrgao") or {}
        tipo_obj = raw_item.get("tipoContrato")

        orgao_cnpj = str(
            orgao_ent.get("cnpj") or orgao_meta.get("cnpj") or ""
        ).strip()
        # Mantém apenas dígitos no CNPJ do órgão
        orgao_cnpj_digits = "".join(ch for ch in orgao_cnpj if ch.isdigit()) or orgao_meta.get("cnpj", "")

        orgao_sigla = str(orgao_meta.get("sigla") or "ITZ").strip()
        orgao_nome = str(
            orgao_ent.get("razaoSocial") or orgao_meta.get("nome") or "Município de Imperatriz"
        ).strip()

        ano_contrato = int(raw_item.get("anoContrato") or fallback_ano or 2026)
        seq = raw_item.get("sequencialContrato")
        num_controle = str(
            raw_item.get("numeroControlePNCP")
            or raw_item.get("numeroControlePncp")
            or f"{orgao_cnpj_digits}-{ano_contrato}-{seq or raw_item.get('numeroContratoEmpenho') or '0'}"
        ).strip()

        if isinstance(tipo_obj, dict):
            tipo_nome = str(tipo_obj.get("nome") or "").strip()
        else:
            tipo_nome = str(tipo_obj or "").strip()

        num_contrato = str(
            raw_item.get("numeroContratoEmpenho")
            or raw_item.get("numeroContrato")
            or ""
        ).strip()

        data_pub = str(raw_item.get("dataPublicacaoPncp") or "")[:10]
        data_ass = str(raw_item.get("dataAssinatura") or "")[:10]
        vig_ini = str(raw_item.get("dataVigenciaInicio") or "")[:10]
        vig_fim = str(raw_item.get("dataVigenciaFim") or "")[:10]

        valor_ini = float(raw_item.get("valorInicial") or 0.0)
        valor_glob = float(raw_item.get("valorGlobal") or valor_ini or 0.0)
        valor_acum = float(raw_item.get("valorAcumulado") or valor_glob or 0.0)

        if num_controle in PNCP_VALUE_CORRECTIONS and valor_glob > 1_000_000_000.0:
            corrected_val = PNCP_VALUE_CORRECTIONS[num_controle]
            valor_ini = corrected_val
            valor_glob = corrected_val
            valor_acum = corrected_val

        if seq not in (None, ""):
            url_pncp = f"https://pncp.gov.br/app/contratos/{orgao_cnpj_digits}/{ano_contrato}/{seq}"
        else:
            url_pncp = f"https://pncp.gov.br/app/contratos?q={orgao_cnpj_digits}"

        return {
            "numero_controle_pncp": num_controle,
            "orgao_cnpj": orgao_cnpj_digits,
            "orgao_sigla": orgao_sigla,
            "orgao_nome": orgao_nome,
            "unidade_nome": str(unidade_org.get("nomeUnidade") or "").strip(),
            "ano_contrato": ano_contrato,
            "numero_contrato": num_contrato,
            "processo": str(raw_item.get("processo") or "").strip(),
            "tipo_contrato": tipo_nome,
            "fornecedor_cnpj": str(raw_item.get("niFornecedor") or "").strip(),
            "fornecedor_nome": str(raw_item.get("nomeRazaoSocialFornecedor") or "").strip(),
            "objeto": str(raw_item.get("objetoContrato") or "").strip(),
            "valor_inicial": valor_ini,
            "valor_global": valor_glob,
            "valor_acumulado": valor_acum,
            "data_assinatura": data_ass,
            "data_publicacao": data_pub,
            "data_vigencia_inicio": vig_ini,
            "data_vigencia_fim": vig_fim,
            "url_pncp": url_pncp,
            "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _quarter_windows_dates(ano: int, today: date) -> List[Tuple[date, date]]:
        quarters = [
            (date(ano, 1, 1), date(ano, 3, 31)),
            (date(ano, 4, 1), date(ano, 6, 30)),
            (date(ano, 7, 1), date(ano, 9, 30)),
            (date(ano, 10, 1), date(ano, 12, 31)),
        ]
        windows: List[Tuple[date, date]] = []
        for q_start, q_end in quarters:
            if q_start > today:
                continue
            windows.append((q_start, min(q_end, today)))
        return windows

    @classmethod
    def _quarter_windows(cls, ano: int, today: date) -> List[Tuple[str, str]]:
        return [
            (s.strftime("%Y%m%d"), e.strftime("%Y%m%d"))
            for s, e in cls._quarter_windows_dates(ano, today)
        ]

    @staticmethod
    def _month_windows(q_start: date, q_end: date) -> List[Tuple[date, date]]:
        """Subdivide uma janela de datas em meses (ou quinzenas se já couber em um único mês)."""
        windows: List[Tuple[date, date]] = []
        cursor = date(q_start.year, q_start.month, 1)
        while cursor <= q_end:
            last_day = calendar.monthrange(cursor.year, cursor.month)[1]
            m_start = max(q_start, cursor)
            m_end = min(q_end, date(cursor.year, cursor.month, last_day))
            if m_start <= m_end:
                windows.append((m_start, m_end))
            cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)

        if len(windows) <= 1 and (q_end - q_start).days > 14:
            mid = q_start + timedelta(days=(q_end - q_start).days // 2)
            return [(q_start, mid), (mid + timedelta(days=1), q_end)]
        return windows

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        if isinstance(exc, urllib.error.URLError):
            return "timed out" in str(getattr(exc, "reason", exc)).lower()
        return "timed out" in str(exc).lower()

    def _fetch_window(
        self,
        orgao_meta: Dict[str, str],
        ano: int,
        data_ini: str,
        data_fim: str,
        max_pages: int = 8,
        tamanho_pagina: int = 50,
        truncamentos: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Busca as páginas de uma janela, até `max_pages`.

        Se o PNCP informar mais páginas do que o limite, a busca fica incompleta. Isso é
        registrado em `truncamentos` (quando informado) e no log, nunca descartado em silêncio.
        """
        coletados: List[Dict[str, Any]] = []
        pagina = 1
        total_paginas = 1
        while pagina <= max_pages:
            status, payload = self._fetch_page(
                cnpj_orgao=orgao_meta["cnpj"],
                data_inicial=data_ini,
                data_final=data_fim,
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
            )
            if status == 204:
                break
            rows = payload.get("data") or []
            for raw_item in rows:
                coletados.append(self.normalize_contrato(raw_item, orgao_meta, fallback_ano=ano))
            total_paginas = int(payload.get("totalPaginas") or 1)
            if not rows or pagina >= total_paginas:
                break
            pagina += 1
        else:
            # Saiu pelo limite de páginas com páginas ainda por buscar no PNCP
            msg = (
                f"{orgao_meta.get('sigla', '?')} {ano} ({data_ini}-{data_fim}): "
                f"busca interrompida em {max_pages} de {total_paginas} página(s)"
            )
            logger.warning("Busca PNCP incompleta: %s", msg)
            if truncamentos is not None:
                truncamentos.append(msg)
        return coletados

    def _sync_window_with_fallback(
        self,
        orgao: Dict[str, str],
        ano: int,
        w_start: date,
        w_end: date,
        max_pages: int,
        truncamentos: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Consulta uma janela trimestral/curta e subdivide em meses se houver timeout ou HTTP 400/422."""
        data_ini = w_start.strftime("%Y%m%d")
        data_fim = w_end.strftime("%Y%m%d")
        erros_local: List[str] = []

        try:
            items = self._fetch_window(
                orgao, ano, data_ini, data_fim, max_pages=max_pages, tamanho_pagina=50,
                truncamentos=truncamentos,
            )
            return items, erros_local
        except urllib.error.HTTPError as he:
            if he.code == 204:
                return [], []
            if he.code not in (400, 422):
                erros_local.append(f"{orgao['sigla']} {ano} ({data_ini}-{data_fim}): HTTP {he.code}")
                return [], erros_local
        except Exception as e:
            if not self._is_timeout_error(e):
                erros_local.append(f"{orgao['sigla']} {ano} ({data_ini}-{data_fim}): {e}")
                return [], erros_local

        # Fallback automático: subdivide janela em meses
        batch: List[Dict[str, Any]] = []
        sub_windows = self._month_windows(w_start, w_end)
        for m_start, m_end in sub_windows:
            m_ini = m_start.strftime("%Y%m%d")
            m_fim = m_end.strftime("%Y%m%d")
            try:
                batch.extend(
                    self._fetch_window(
                        orgao, ano, m_ini, m_fim, max_pages=max_pages, tamanho_pagina=50,
                        truncamentos=truncamentos,
                    )
                )
            except urllib.error.HTTPError as mhe:
                if mhe.code != 204:
                    erros_local.append(f"{orgao['sigla']} {ano} ({m_ini}-{m_fim}): HTTP {mhe.code}")
            except Exception as me:
                erros_local.append(f"{orgao['sigla']} {ano} ({m_ini}-{m_fim}): {me}")
        return batch, erros_local

    def sync_imperatriz(
        self,
        anos: Optional[List[int]] = None,
        max_pages_per_window: int = 8,
        mode: str = "full",
        max_workers: int = 4,
    ) -> Dict[str, Any]:
        """
        Sincroniza contratos dos órgãos públicos de Imperatriz (MA) no PNCP para 2024, 2025 e 2026.
        Usa janelas trimestrais (ou últimos 60 dias em mode='recent') e subdivide em meses se houver timeout.
        """
        target_anos = anos or list(PNCP_ANOS_MONITORADOS)
        today = datetime.now().date()
        tasks: List[Tuple[Dict[str, str], int, date, date, int]] = []

        if mode == "recent":
            effective_max_pages = min(max_pages_per_window, 4)
            if len(target_anos) == 1 and target_anos[0] < today.year:
                yr = target_anos[0]
                w_start, w_end = date(yr, 10, 1), date(yr, 12, 31)
                task_ano = yr
            else:
                w_start = max(date(min(target_anos), 1, 1), today - timedelta(days=60))
                w_end = today
                task_ano = today.year
            for orgao in PNCP_ORGAOS_IMPERATRIZ:
                tasks.append((orgao, task_ano, w_start, w_end, effective_max_pages))
        else:
            for orgao in PNCP_ORGAOS_IMPERATRIZ:
                for ano in target_anos:
                    for q_start, q_end in self._quarter_windows_dates(ano, today):
                        tasks.append((orgao, ano, q_start, q_end, max_pages_per_window))

        por_ctrl: Dict[str, Dict[str, Any]] = {}
        erros: List[str] = []
        truncamentos: List[str] = []

        if tasks:
            workers = max(1, min(max_workers, len(tasks)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                # Uma lista de truncamentos por tarefa: sem estado compartilhado entre threads
                trunc_por_tarefa: List[List[str]] = [[] for _ in tasks]
                futures = [
                    executor.submit(
                        self._sync_window_with_fallback, orgao, ano, w_start, w_end, max_pg, trunc
                    )
                    for (orgao, ano, w_start, w_end, max_pg), trunc in zip(tasks, trunc_por_tarefa)
                ]
                for fut, trunc in zip(futures, trunc_por_tarefa):
                    items, errs = fut.result()
                    erros.extend(errs)
                    truncamentos.extend(trunc)
                    for c in items:
                        ctrl = c.get("numero_controle_pncp")
                        if ctrl:
                            por_ctrl[ctrl] = c

        todos_contratos = list(por_ctrl.values())
        por_orgao: Dict[str, int] = {o["sigla"]: 0 for o in PNCP_ORGAOS_IMPERATRIZ}
        por_ano: Dict[str, int] = {str(a): 0 for a in target_anos}
        for c in todos_contratos:
            sig = c.get("orgao_sigla") or "ITZ"
            yr_key = str(c.get("ano_contrato") or "")
            por_orgao[sig] = por_orgao.get(sig, 0) + 1
            if yr_key:
                por_ano[yr_key] = por_ano.get(yr_key, 0) + 1

        gravados = self.db.upsert_pncp_contratos_batch(todos_contratos)
        logger.info(
            "Sincronização PNCP (%s) concluída: %d contratos gravados/atualizados (%s).",
            mode,
            gravados,
            por_ano,
        )
        cobertura = self.avaliar_cobertura_sync(mode, truncamentos, erros, target_anos)
        if not cobertura["completa"]:
            logger.warning("Sincronização PNCP (%s) com cobertura incompleta: %s", mode, cobertura["motivo"])
        return {
            "total_sincronizados": gravados,
            "por_orgao": por_orgao,
            "por_ano": por_ano,
            "erros": erros,
            "truncamentos": truncamentos,
            "cobertura": cobertura,
        }

    @staticmethod
    def avaliar_cobertura_sync(
        mode: str, truncamentos: List[str], erros: List[str], anos: List[int]
    ) -> Dict[str, Any]:
        """Diz se a sincronização cobriu todo o período pedido.

        A cobertura só é completa numa carga `full` sem páginas cortadas e sem erros.
        O modo `recent` cobre só os últimos dias, então nunca vale para afirmar ausência.
        """
        motivos: List[str] = []
        if mode != "full":
            motivos.append("sincronização parcial (apenas período recente)")
        if truncamentos:
            motivos.append(f"{len(truncamentos)} janela(s) com páginas não buscadas")
        if erros:
            motivos.append(f"{len(erros)} janela(s) com erro na consulta")
        return {
            "completa": not motivos,
            "truncada": bool(truncamentos),
            "modo": mode,
            "anos": list(anos),
            "janelas_truncadas": len(truncamentos),
            "janelas_com_erro": len(erros),
            "motivo": "; ".join(motivos),
        }
