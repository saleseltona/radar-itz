"""
Conector REST (PostgREST HTTPS) e Repositório Supabase 100% biblioteca padrão Python 3.
Substitui o SQLite local no servidor (Render) por persistência real em PostgreSQL sem dependências C externas.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.config import get_supabase_config, mask_secrets
from src.database import build_pncp_resumo_from_rows
from src.pncp_analytics import (
    build_pncp_estatisticas,
    classify_pncp_contrato,
    is_fornecedor_cpf,
)

logger = logging.getLogger("radar_imperatriz.supabase")

TransportFn = Callable[
    [str, str, Dict[str, str], Optional[bytes], int],
    Tuple[int, Dict[str, str], bytes],
]


class SupabaseError(RuntimeError):
    """Erro operacional ao comunicar com a API PostgREST do Supabase."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(mask_secrets(message))
        self.status = status


def _default_urllib_transport(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes],
    timeout: int,
) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.getcode() or 200)
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            data = resp.read()
            return status, resp_headers, data
    except urllib.error.HTTPError as he:
        err_bytes = he.read() if he.fp else b""
        resp_headers = {k.lower(): v for k, v in he.headers.items()} if he.headers else {}
        return int(he.code), resp_headers, err_bytes


class SupabaseRestClient:
    """Cliente HTTP leve (stdlib) para a API `/rest/v1` do Supabase (PostgREST)."""

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: int = 20,
        transport: Optional[TransportFn] = None,
    ) -> None:
        url_clean = (url or "").strip().rstrip("/")
        key_clean = (api_key or "").strip()
        if not url_clean or not key_clean:
            raise SupabaseError(
                "Credenciais Supabase ausentes: configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY."
            )
        self.base_url = url_clean
        self.rest_url = f"{url_clean}/rest/v1"
        self.api_key = key_clean
        self.timeout = int(timeout)
        self.transport: TransportFn = transport or _default_urllib_transport

    def _request(
        self,
        method: str,
        table_or_path: str,
        params: Optional[Dict[str, str]] = None,
        payload: Any = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        clean_path = table_or_path.lstrip("/")
        full_url = f"{self.rest_url}/{clean_path}"
        if params:
            qs = urllib.parse.urlencode(params, safe="(),.*:")
            full_url = f"{full_url}?{qs}"

        headers: Dict[str, str] = {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        body_bytes = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )

        try:
            status, resp_headers, resp_bytes = self.transport(
                method, full_url, headers, body_bytes, self.timeout
            )
        except SupabaseError:
            raise
        except Exception as exc:
            raise SupabaseError(
                f"Falha de conexão com Supabase ({clean_path}): {mask_secrets(str(exc))}"
            ) from exc

        norm_headers = {k.lower(): v for k, v in (resp_headers or {}).items()}
        if status >= 400:
            detail = mask_secrets(resp_bytes.decode("utf-8", errors="replace").strip())
            raise SupabaseError(
                f"Erro Supabase HTTP {status} em {clean_path}: {detail[:300]}",
                status=status,
            )
        return status, norm_headers, resp_bytes

    def select(
        self,
        table: str,
        params: Optional[Dict[str, str]] = None,
        fetch_all: bool = True,
        page_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        if not fetch_all:
            _, _, data = self._request("GET", table, params=params)
            if not data:
                return []
            parsed = json.loads(data.decode("utf-8", errors="replace"))
            return parsed if isinstance(parsed, list) else []

        rows: List[Dict[str, Any]] = []
        start = 0
        while True:
            end = start + page_size - 1
            _, _, data = self._request(
                "GET",
                table,
                params=params,
                extra_headers={"Range-Unit": "items", "Range": f"{start}-{end}"},
            )
            if not data:
                break
            chunk = json.loads(data.decode("utf-8", errors="replace"))
            if not isinstance(chunk, list) or not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < page_size:
                break
            start += page_size
        return rows

    def count(self, table: str, params: Optional[Dict[str, str]] = None) -> int:
        count_params = dict(params or {})
        count_params.setdefault("select", "*")
        _, headers, data = self._request(
            "GET",
            table,
            params=count_params,
            extra_headers={
                "Prefer": "count=exact",
                "Range-Unit": "items",
                "Range": "0-0",
            },
        )
        content_range = headers.get("content-range", "")
        if "/" in content_range:
            total_part = content_range.rsplit("/", 1)[-1].strip()
            if total_part.isdigit():
                return int(total_part)
        if data:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
            if isinstance(parsed, list):
                return len(parsed)
        return 0

    def upsert(
        self,
        table: str,
        rows: List[Dict[str, Any]],
        on_conflict: str,
        ignore_duplicates: bool = False,
        batch_size: int = 200,
    ) -> int:
        if not rows:
            return 0
        resolution = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            self._request(
                "POST",
                table,
                params={"on_conflict": on_conflict},
                payload=batch,
                extra_headers={"Prefer": f"resolution={resolution},return=minimal"},
            )
            total += len(batch)
        return total

    def insert(
        self,
        table: str,
        rows: List[Dict[str, Any]],
        batch_size: int = 200,
    ) -> int:
        if not rows:
            return 0
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            self._request(
                "POST",
                table,
                payload=batch,
                extra_headers={"Prefer": "return=minimal"},
            )
            total += len(batch)
        return total

    def patch(
        self,
        table: str,
        filters: Dict[str, str],
        payload: Dict[str, Any],
    ) -> None:
        self._request(
            "PATCH",
            table,
            params=filters,
            payload=payload,
            extra_headers={"Prefer": "return=minimal"},
        )

    def delete(self, table: str, filters: Dict[str, str]) -> None:
        self._request(
            "DELETE",
            table,
            params=filters,
            extra_headers={"Prefer": "return=minimal"},
        )


class SupabaseDatabase:
    """Implementa a interface completa do RadarDatabase diretamente sobre Supabase PostgreSQL."""

    backend_name: str = "supabase"
    db_path: Optional[Any] = None

    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client
        self.last_sync_stats: Dict[str, int] = {}

    @classmethod
    def from_env(cls, transport: Optional[TransportFn] = None) -> "SupabaseDatabase":
        url, key = get_supabase_config()
        return cls(SupabaseRestClient(url=url, api_key=key, transport=transport))

    # =========================================================================
    # EXECUÇÕES E MONITORAMENTO
    # =========================================================================

    def recover_interrupted_executions(self) -> int:
        stuck = self.client.select(
            "execucoes",
            params={"select": "id_execucao", "situacao_execucao": "eq.EM_ANDAMENTO"},
        )
        if not stuck:
            return 0
        self.client.patch(
            "execucoes",
            {"situacao_execucao": "eq.EM_ANDAMENTO"},
            {"situacao_execucao": "INTERROMPIDA_RECUPERADA"},
        )
        return len(stuck)

    def start_execution(self, id_execucao: str, modo: str = "manual") -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        self.client.upsert(
            "execucoes",
            [
                {
                    "id_execucao": id_execucao,
                    "data_horario": now_iso,
                    "modo": modo,
                    "situacao_execucao": "EM_ANDAMENTO",
                }
            ],
            on_conflict="id_execucao",
        )

    def finish_execution(
        self,
        id_execucao: str,
        fontes_consultadas: int,
        fontes_com_falha: int,
        paginas_lidas: int,
        pautas_novas: int,
        pautas_atualizadas: int,
        duplicidades_eliminadas: int,
        itens_descartados: int,
        duracao_segundos: float,
        situacao_execucao: str = "CONCLUIDA",
        resumo: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.client.patch(
            "execucoes",
            {"id_execucao": f"eq.{id_execucao}"},
            {
                "fontes_consultadas": int(fontes_consultadas),
                "fontes_com_falha": int(fontes_com_falha),
                "paginas_lidas": int(paginas_lidas),
                "pautas_novas": int(pautas_novas),
                "pautas_atualizadas": int(pautas_atualizadas),
                "duplicidades_eliminadas": int(duplicidades_eliminadas),
                "itens_descartados": int(itens_descartados),
                "duracao_segundos": round(float(duracao_segundos), 2),
                "situacao_execucao": situacao_execucao,
                "resumo_json": json.dumps(resumo or {}, ensure_ascii=False),
            },
        )

    def get_all_execucoes(self) -> List[Dict[str, Any]]:
        rows = self.client.select("execucoes", params={"order": "data_horario.asc"})
        rows.sort(key=lambda r: str(r.get("data_horario") or ""))
        return rows

    # =========================================================================
    # FONTES, PÁGINAS E ERROS
    # =========================================================================

    def get_all_fontes(self) -> List[Dict[str, Any]]:
        rows = self.client.select("fontes", params={"order": "id.asc"})
        rows.sort(key=lambda r: int(r.get("id") or 0))
        return rows

    def get_fonte_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        rows = self.client.select(
            "fontes",
            params={"url": f"eq.{url}", "limit": "1"},
            fetch_all=False,
        )
        return rows[0] if rows else None

    def upsert_fonte(self, fonte: Dict[str, str]) -> None:
        url = str(fonte.get("url") or "").strip()
        if not url:
            return
        existing = self.get_fonte_by_url(url) or {}

        def pick(field: str, default: str = "") -> str:
            val = str(fonte.get(field) or "")
            if val != "":
                return val
            return str(existing.get(field) or default)

        payload = {
            "nome": str(fonte.get("nome") or existing.get("nome") or ""),
            "tipo": str(fonte.get("tipo") or existing.get("tipo") or ""),
            "url": url,
            "prioridade": str(fonte.get("prioridade") or existing.get("prioridade") or "Média"),
            "temas": str(fonte.get("temas") or existing.get("temas") or ""),
            "situacao_funcionamento": pick("situacao_funcionamento", "Cadastrada"),
            "ultima_consulta": pick("ultima_consulta", ""),
            "ultima_coleta_sucesso": pick("ultima_coleta_sucesso", ""),
            "metodo_acesso": pick("metodo_acesso", ""),
            "observacoes_limitacoes": pick("observacoes_limitacoes", ""),
            "erro_identificado": str(fonte.get("erro_identificado") or ""),
            "proxima_acao_recomendada": pick("proxima_acao_recomendada", ""),
        }
        self.client.upsert("fontes", [payload], on_conflict="url")

    def log_collected_page(
        self,
        id_execucao: str,
        url: str,
        fonte_nome: str,
        titulo: str,
        data_publicacao: str,
        data_acontecimento: str,
        metodo_coleta: str,
        status_leitura: str,
        conteudo_hash: str,
        conteudo_extraido: str,
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        self.client.upsert(
            "paginas_coletadas",
            [
                {
                    "id_execucao": id_execucao,
                    "url": url,
                    "fonte_nome": fonte_nome,
                    "titulo": titulo,
                    "data_publicacao": data_publicacao,
                    "data_acontecimento": data_acontecimento,
                    "data_coleta": now_iso,
                    "metodo_coleta": metodo_coleta,
                    "status_leitura": status_leitura,
                    "conteudo_hash": conteudo_hash,
                    "conteudo_extraido": conteudo_extraido,
                }
            ],
            on_conflict="id_execucao,url,conteudo_hash",
            ignore_duplicates=True,
        )

    def log_error(
        self,
        id_execucao: str,
        componente: str,
        alvo_url: str,
        mensagem_erro: str,
        data_registro: Optional[str] = None,
    ) -> None:
        dt = data_registro or datetime.now().isoformat(timespec="seconds")
        self.client.upsert(
            "erros_coleta",
            [
                {
                    "id_execucao": id_execucao,
                    "componente": componente,
                    "alvo_url": alvo_url,
                    "mensagem_erro": mensagem_erro,
                    "data_registro": dt,
                }
            ],
            on_conflict="id_execucao,componente,alvo_url,data_registro",
            ignore_duplicates=True,
        )

    def get_errors_for_execution(self, id_execucao: str) -> List[Dict[str, Any]]:
        rows = self.client.select(
            "erros_coleta",
            params={"id_execucao": f"eq.{id_execucao}", "order": "id.asc"},
        )
        rows.sort(key=lambda r: int(r.get("id") or 0))
        return rows

    # =========================================================================
    # PAUTAS, EVIDÊNCIAS, HISTÓRICO E PENDÊNCIAS
    # =========================================================================

    @staticmethod
    def _format_pauta_row(
        row: Dict[str, Any], arq_row: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        d = dict(row)
        d["arquivada"] = arq_row is not None
        d["motivo_arquivamento"] = str((arq_row or {}).get("motivo") or "")
        return d

    def get_all_pautas(
        self,
        order_by: str = "recentes",
        incluir_arquivadas: bool = True,
        somente_arquivadas: bool = False,
    ) -> List[Dict[str, Any]]:
        rows = self.client.select("pautas")
        arq_rows = self.client.select("pautas_arquivadas")
        arq_map = {
            str(r.get("id_pauta") or ""): r for r in arq_rows if r.get("id_pauta")
        }

        filtered: List[Dict[str, Any]] = []
        for raw in rows:
            pid = str(raw.get("id_pauta") or "")
            arq_row = arq_map.get(pid)
            if somente_arquivadas:
                if arq_row is None:
                    continue
            elif not incluir_arquivadas and arq_row is not None:
                continue
            filtered.append(self._format_pauta_row(raw, arq_row))

        filtered.sort(key=lambda r: str(r.get("id_pauta") or ""))
        if order_by == "score":
            filtered.sort(
                key=lambda r: (
                    int(r.get("pontuacao_editorial") or 0),
                    str(r.get("data_publicacao") or str(r.get("data_hora_coleta") or "")[:10]),
                ),
                reverse=True,
            )
        else:
            filtered.sort(
                key=lambda r: (
                    str(r.get("data_publicacao") or str(r.get("data_hora_coleta") or "")[:10]),
                    int(r.get("pontuacao_editorial") or 0),
                ),
                reverse=True,
            )
        return filtered

    def arquivar_pauta(
        self, id_pauta: str, motivo: str = "Arquivada pela redação"
    ) -> Optional[Dict[str, Any]]:
        pid = (id_pauta or "").strip()
        if not pid:
            return None
        rows = self.client.select(
            "pautas",
            params={"id_pauta": f"eq.{pid}", "limit": "1"},
            fetch_all=False,
        )
        if not rows:
            return None
        now_iso = datetime.now().isoformat(timespec="seconds")
        motivo_clean = (motivo or "Arquivada pela redação").strip()[:240] or "Arquivada pela redação"
        arq_payload = {
            "id_pauta": pid,
            "motivo": motivo_clean,
            "atualizado_em": now_iso,
        }
        self.client.upsert(
            "pautas_arquivadas",
            [arq_payload],
            on_conflict="id_pauta",
        )
        return self._format_pauta_row(rows[0], arq_payload)

    def restaurar_pauta_arquivada(self, id_pauta: str) -> Optional[Dict[str, Any]]:
        pid = (id_pauta or "").strip()
        if not pid:
            return None
        rows = self.client.select(
            "pautas",
            params={"id_pauta": f"eq.{pid}", "limit": "1"},
            fetch_all=False,
        )
        if not rows:
            return None
        self.client.delete("pautas_arquivadas", {"id_pauta": f"eq.{pid}"})
        return self._format_pauta_row(rows[0], None)

    def get_all_pautas_arquivadas(self) -> List[Dict[str, Any]]:
        rows = self.client.select(
            "pautas_arquivadas", params={"order": "atualizado_em.desc"}
        )
        rows.sort(key=lambda r: str(r.get("atualizado_em") or ""), reverse=True)
        return rows

    def sync_pautas_arquivadas_from_request(self, overrides: Dict[str, Any]) -> int:
        return 0

    def get_pautas_by_execution(self, id_execucao: str) -> List[Dict[str, Any]]:
        rows = self.client.select(
            "pautas",
            params={"id_execucao_origem": f"eq.{id_execucao}"},
        )
        rows.sort(key=lambda r: str(r.get("id_pauta") or ""))
        rows.sort(key=lambda r: int(r.get("pontuacao_editorial") or 0), reverse=True)
        return rows

    def insert_pauta(self, pauta: Dict[str, Any], id_execucao: str) -> None:
        row = {
            "id_pauta": pauta.get("id_pauta", ""),
            "prioridade": pauta.get("prioridade", "Média"),
            "pontuacao_editorial": int(pauta.get("pontuacao_editorial", 50)),
            "justificativa_pontuacao": pauta.get("justificativa_pontuacao", ""),
            "emergencia_sinalizada": 1 if pauta.get("emergencia_sinalizada") else 0,
            "categoria": pauta.get("categoria", "Pautas de serviço público"),
            "tema_especifico": pauta.get("tema_especifico", "Geral"),
            "titulo_jornalistico": pauta.get("titulo_jornalistico", ""),
            "titulo_normalizado": pauta.get("titulo_normalizado", ""),
            "resumo_3_linhas": pauta.get("resumo_3_linhas", ""),
            "descricao_detalhada": pauta.get("descricao_detalhada", ""),
            "fonte_principal": pauta.get("fonte_principal", ""),
            "url_original": pauta.get("url_original", ""),
            "veiculo_instituicao": pauta.get("veiculo_instituicao", ""),
            "autor": pauta.get("autor", ""),
            "data_publicacao": pauta.get("data_publicacao", ""),
            "data_acontecimento": pauta.get("data_acontecimento", ""),
            "data_hora_coleta": pauta.get(
                "data_hora_coleta", datetime.now().isoformat(timespec="seconds")
            ),
            "fontes_complementares": pauta.get("fontes_complementares", ""),
            "urls_complementares": pauta.get("urls_complementares", ""),
            "evidencias_verificadas": pauta.get("evidencias_verificadas", ""),
            "angulo_jornalistico": pauta.get("angulo_jornalistico", ""),
            "publico_afetado": pauta.get("publico_afetado", ""),
            "bairros_locais_instituicoes": pauta.get("bairros_locais_instituicoes", ""),
            "formato_sugerido": pauta.get("formato_sugerido", "Matéria jornalística"),
            "nivel_verificacao": pauta.get("nivel_verificacao", "Parcialmente confirmado"),
            "informacoes_pendentes": pauta.get("informacoes_pendentes", ""),
            "proxima_acao_recomendada": pauta.get("proxima_acao_recomendada", ""),
            "situacao_editorial": pauta.get("situacao_editorial", "Sugerida"),
            "id_pauta_relacionada": pauta.get("id_pauta_relacionada", ""),
            "identificadores_documentos": pauta.get("identificadores_documentos", ""),
            "detalhes_denuncia_json": json.dumps(
                pauta.get("detalhes_denuncia", {}), ensure_ascii=False
            ),
            "motivo_selecao": pauta.get("motivo_selecao", ""),
            "imagem_capa": pauta.get("imagem_capa", ""),
            "id_execucao_origem": id_execucao,
            "id_execucao_ultima_atualizacao": id_execucao,
        }
        self.client.upsert(
            "pautas",
            [row],
            on_conflict="url_original",
            ignore_duplicates=True,
        )

    def update_pauta_imagem_capa(self, id_pauta: str, imagem_capa: str) -> None:
        if not imagem_capa:
            return
        rows = self.client.select(
            "pautas",
            params={"id_pauta": f"eq.{id_pauta}", "select": "id_pauta,imagem_capa"},
            fetch_all=False,
        )
        if rows and not rows[0].get("imagem_capa"):
            self.client.patch(
                "pautas",
                {"id_pauta": f"eq.{id_pauta}"},
                {"imagem_capa": imagem_capa},
            )

    def update_existing_pauta(
        self,
        id_pauta: str,
        updates: Dict[str, Any],
        id_execucao: str,
        tipo_mudanca: str,
        descricao_mudanca: str,
    ) -> None:
        rows = self.client.select(
            "pautas",
            params={"id_pauta": f"eq.{id_pauta}", "limit": "1"},
            fetch_all=False,
        )
        if not rows:
            return
        old_dict = rows[0]
        now_iso = datetime.now().isoformat(timespec="seconds")
        self.client.upsert(
            "historico_atualizacoes",
            [
                {
                    "id_pauta": id_pauta,
                    "id_execucao": id_execucao,
                    "data_atualizacao": now_iso,
                    "tipo_mudanca": tipo_mudanca,
                    "descricao_mudanca": descricao_mudanca,
                    "estado_anterior_json": json.dumps(old_dict, ensure_ascii=False),
                }
            ],
            on_conflict="id_pauta,id_execucao,data_atualizacao,tipo_mudanca",
            ignore_duplicates=True,
        )
        nova_capa = updates.get("imagem_capa") or old_dict.get("imagem_capa", "")
        self.client.patch(
            "pautas",
            {"id_pauta": f"eq.{id_pauta}"},
            {
                "fontes_complementares": updates.get(
                    "fontes_complementares", old_dict.get("fontes_complementares", "")
                ),
                "urls_complementares": updates.get(
                    "urls_complementares", old_dict.get("urls_complementares", "")
                ),
                "evidencias_verificadas": updates.get(
                    "evidencias_verificadas", old_dict.get("evidencias_verificadas", "")
                ),
                "descricao_detalhada": updates.get(
                    "descricao_detalhada", old_dict.get("descricao_detalhada", "")
                ),
                "pontuacao_editorial": int(
                    updates.get("pontuacao_editorial", old_dict.get("pontuacao_editorial", 50))
                ),
                "prioridade": updates.get("prioridade", old_dict.get("prioridade", "Média")),
                "situacao_editorial": updates.get(
                    "situacao_editorial", old_dict.get("situacao_editorial", "")
                ),
                "imagem_capa": nova_capa,
                "id_execucao_ultima_atualizacao": id_execucao,
            },
        )

    def add_evidencia(
        self, id_pauta: str, tipo_evidencia: str, descricao: str, referencia: str
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        self.client.upsert(
            "evidencias",
            [
                {
                    "id_pauta": id_pauta,
                    "tipo_evidencia": tipo_evidencia,
                    "descricao": descricao,
                    "referencia_url_ou_doc": referencia,
                    "data_registro": now_iso,
                }
            ],
            on_conflict="id_pauta,tipo_evidencia,referencia_url_ou_doc",
            ignore_duplicates=True,
        )

    def upsert_pendencia(
        self,
        id_pauta: str,
        questao_pendente: str,
        documento_necessario: str,
        instituicao_ou_pessoa: str,
        evidencia_disponivel: str,
        proxima_acao: str,
        status_apuracao: str,
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        self.client.upsert(
            "pendencias_apuracao",
            [
                {
                    "id_pauta": id_pauta,
                    "questao_pendente": questao_pendente,
                    "documento_necessario": documento_necessario,
                    "instituicao_ou_pessoa_a_consultar": instituicao_ou_pessoa,
                    "evidencia_ja_disponivel": evidencia_disponivel,
                    "proxima_acao": proxima_acao,
                    "status_apuracao": status_apuracao,
                    "data_atualizacao": now_iso,
                }
            ],
            on_conflict="id_pauta",
        )

    def get_all_pendencias(self) -> List[Dict[str, Any]]:
        rows = self.client.select("pendencias_apuracao", params={"order": "id_pauta.asc"})
        rows.sort(key=lambda r: str(r.get("id_pauta") or ""))
        return rows

    def get_pendencias_detalhadas(self) -> List[Dict[str, Any]]:
        pendencias = self.client.select("pendencias_apuracao")
        pautas = self.client.select(
            "pautas",
            params={
                "select": "id_pauta,titulo_jornalistico,prioridade,pontuacao_editorial,fonte_principal,url_original,data_publicacao,nivel_verificacao,imagem_capa"
            },
        )
        pauta_map = {str(p.get("id_pauta") or ""): p for p in pautas}
        merged: List[Dict[str, Any]] = []
        for pa in pendencias:
            p = pauta_map.get(str(pa.get("id_pauta") or ""), {})
            item = dict(pa)
            item.update(
                {
                    "titulo_jornalistico": p.get("titulo_jornalistico"),
                    "prioridade": p.get("prioridade"),
                    "pontuacao_editorial": p.get("pontuacao_editorial"),
                    "fonte_principal": p.get("fonte_principal"),
                    "url_original": p.get("url_original"),
                    "data_publicacao": p.get("data_publicacao"),
                    "nivel_verificacao": p.get("nivel_verificacao"),
                    "imagem_capa": p.get("imagem_capa"),
                }
            )
            merged.append(item)

        merged.sort(key=lambda r: str(r.get("id_pauta") or ""))
        merged.sort(
            key=lambda r: (
                str(r.get("data_publicacao") or r.get("data_atualizacao") or ""),
                int(r.get("pontuacao_editorial") or 0),
            ),
            reverse=True,
        )
        return merged

    def log_discarded_item(
        self, id_execucao: str, url: str, titulo: str, motivo: str
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        self.client.upsert(
            "itens_descartados",
            [
                {
                    "id_execucao": id_execucao,
                    "url": url,
                    "titulo": titulo,
                    "motivo_descarte": motivo,
                    "data_registro": now_iso,
                }
            ],
            on_conflict="id_execucao,url",
            ignore_duplicates=True,
        )

    def get_discarded_items(self, id_execucao: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {"order": "id.asc"}
        if id_execucao:
            params["id_execucao"] = f"eq.{id_execucao}"
        rows = self.client.select("itens_descartados", params=params)
        rows.sort(key=lambda r: int(r.get("id") or 0))
        return rows

    # =========================================================================
    # CONTRATOS DO PNCP (2024 - 2026) — SÓ UPAR NOVOS + RETIFICAÇÕES PERSISTENTES
    # =========================================================================

    def upsert_pncp_contrato(self, contrato: Dict[str, Any]) -> None:
        self.upsert_pncp_contratos_batch([contrato])

    def upsert_pncp_contratos_batch(
        self, contratos: List[Dict[str, Any]], only_new: bool = False
    ) -> int:
        if not contratos:
            self.last_sync_stats = {"novos": 0, "atualizados": 0, "ignorados_existentes": 0}
            return 0

        now_iso = datetime.now().isoformat(timespec="seconds")
        ret_rows = self.client.select("pncp_retificacoes")
        ret_map = {str(r["numero_controle_pncp"]): r for r in ret_rows if r.get("numero_controle_pncp")}

        existing_rows = self.client.select(
            "pncp_contratos",
            params={
                "select": "numero_controle_pncp,valor_global,objeto,data_vigencia_fim,categoria_tematica"
            },
        )
        existing_map = {
            str(r["numero_controle_pncp"]): r
            for r in existing_rows
            if r.get("numero_controle_pncp")
        }

        novos: List[Dict[str, Any]] = []
        alterados: List[Dict[str, Any]] = []
        total_validos = 0

        for c in contratos:
            ctrl = str(c.get("numero_controle_pncp") or "").strip()
            if not ctrl:
                continue
            total_validos += 1
            val_ini = float(c.get("valor_inicial", 0.0) or 0.0)
            val_glob = float(c.get("valor_global", 0.0) or 0.0)
            val_acum = float(c.get("valor_acumulado", 0.0) or 0.0)

            if ctrl in ret_map:
                corrigido = float(ret_map[ctrl].get("valor_corrigido") or val_glob)
                val_ini = corrigido
                val_glob = corrigido
                val_acum = corrigido

            row = {
                "numero_controle_pncp": ctrl,
                "orgao_cnpj": str(c.get("orgao_cnpj", "")),
                "orgao_sigla": str(c.get("orgao_sigla", "")),
                "orgao_nome": str(c.get("orgao_nome", "")),
                "unidade_nome": str(c.get("unidade_nome", "")),
                "ano_contrato": int(c.get("ano_contrato", 2026) or 2026),
                "numero_contrato": str(c.get("numero_contrato", "")),
                "processo": str(c.get("processo", "")),
                "tipo_contrato": str(c.get("tipo_contrato", "")),
                "fornecedor_cnpj": str(c.get("fornecedor_cnpj", "")),
                "fornecedor_nome": str(c.get("fornecedor_nome", "")),
                "objeto": str(c.get("objeto", "")),
                "valor_inicial": val_ini,
                "valor_global": val_glob,
                "valor_acumulado": val_acum,
                "data_assinatura": str(c.get("data_assinatura", "")),
                "data_publicacao": str(c.get("data_publicacao", "")),
                "data_vigencia_inicio": str(c.get("data_vigencia_inicio", "")),
                "data_vigencia_fim": str(c.get("data_vigencia_fim", "")),
                "url_pncp": str(c.get("url_pncp", "")),
                "atualizado_em": str(c.get("atualizado_em") or now_iso),
            }
            row["categoria_tematica"] = classify_pncp_contrato(row)

            old = existing_map.get(ctrl)
            if old is None:
                novos.append(row)
                existing_map[ctrl] = row
            elif not only_new:
                changed = (
                    abs(float(old.get("valor_global") or 0.0) - val_glob) > 0.005
                    or str(old.get("objeto") or "") != row["objeto"]
                    or str(old.get("data_vigencia_fim") or "") != row["data_vigencia_fim"]
                    or str(old.get("categoria_tematica") or "") != row["categoria_tematica"]
                )
                if changed:
                    alterados.append(row)
                    existing_map[ctrl] = row

        if novos:
            self.client.upsert(
                "pncp_contratos",
                novos,
                on_conflict="numero_controle_pncp",
                ignore_duplicates=True,
            )
        if alterados:
            self.client.upsert(
                "pncp_contratos",
                alterados,
                on_conflict="numero_controle_pncp",
                ignore_duplicates=False,
            )

        self.last_sync_stats = {
            "novos": len(novos),
            "atualizados": len(alterados),
            "ignorados_existentes": max(0, total_validos - len(novos) - len(alterados)),
        }
        return len(novos) + len(alterados)

    # --- Sincronização PNCP em lotes: upsert detalhado, estado persistente e lock ---

    @staticmethod
    def _in_filter(values: Sequence[str]) -> str:
        quoted = ",".join('"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values)
        return f"in.({quoted})"

    def upsert_pncp_contratos_detalhado(self, contratos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Upsert por numero_controle_pncp consultando só os contratos do lote (não a tabela inteira)."""
        from src.pncp_sync import classificar_contratos

        unicos: Dict[str, Dict[str, Any]] = {}
        for c in contratos:
            ctrl = str(c.get("numero_controle_pncp") or "").strip()
            if ctrl:
                unicos[ctrl] = c
        if not unicos:
            return {"inseridos": 0, "atualizados": 0, "ignorados": 0, "retificacoes_divergentes": []}

        ctrls = list(unicos.keys())
        existentes: Dict[str, Dict[str, Any]] = {}
        retificacoes: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(ctrls), 50):
            filtro = self._in_filter(ctrls[i : i + 50])
            for r in self.client.select("pncp_contratos", params={"numero_controle_pncp": filtro}):
                existentes[str(r.get("numero_controle_pncp"))] = r
            for r in self.client.select("pncp_retificacoes", params={"numero_controle_pncp": filtro}):
                retificacoes[str(r.get("numero_controle_pncp"))] = r

        resultado = classificar_contratos(list(unicos.values()), existentes, retificacoes)
        gravar = resultado.pop("_gravar")
        if gravar:
            now_iso = datetime.now().isoformat(timespec="seconds")
            rows: List[Dict[str, Any]] = []
            for c in gravar:
                row = {k: c.get(k) for k in (
                    "numero_controle_pncp", "orgao_cnpj", "orgao_sigla", "orgao_nome", "unidade_nome",
                    "numero_contrato", "processo", "tipo_contrato", "fornecedor_cnpj", "fornecedor_nome",
                    "objeto", "data_assinatura", "data_publicacao", "data_vigencia_inicio",
                    "data_vigencia_fim", "url_pncp",
                )}
                row = {k: str(v or "") for k, v in row.items()}
                row["ano_contrato"] = int(c.get("ano_contrato", 2026) or 2026)
                for campo in ("valor_inicial", "valor_global", "valor_acumulado"):
                    row[campo] = float(c.get(campo, 0.0) or 0.0)
                ret = retificacoes.get(row["numero_controle_pncp"])
                if ret is not None and ret.get("valor_corrigido") is not None:
                    corrigido = float(ret["valor_corrigido"])
                    row["valor_inicial"] = row["valor_global"] = row["valor_acumulado"] = corrigido
                row["atualizado_em"] = str(c.get("atualizado_em") or now_iso)
                row["categoria_tematica"] = classify_pncp_contrato(row)
                rows.append(row)
            self.client.upsert("pncp_contratos", rows, on_conflict="numero_controle_pncp")
        self.last_sync_stats = {
            "novos": resultado["inseridos"],
            "atualizados": resultado["atualizados"],
            "ignorados_existentes": resultado["ignorados"],
        }
        return resultado

    # Estado/lock ficam na tabela `pncp_sync_estado` (supabase/migrations/20261127_pncp_sync_estado.sql).
    # Enquanto a tabela não existir, cai para estado/lock em memória do processo (menos seguro).
    _sync_estado_local: Dict[str, Any] = {}
    _sync_lock_local: Dict[str, Any] = {}

    @staticmethod
    def _tabela_ausente(exc: Exception) -> bool:
        txt = str(exc)
        return getattr(exc, "status", None) == 404 or "PGRST205" in txt or "42P01" in txt or "does not exist" in txt

    def get_pncp_sync_state(self) -> Dict[str, Any]:
        try:
            rows = self.client.select(
                "pncp_sync_estado", params={"id": "eq.default", "select": "estado_json"}, fetch_all=False
            )
        except SupabaseError as exc:
            if self._tabela_ausente(exc):
                self.sync_estado_persistente = False
                return dict(self._sync_estado_local)
            raise
        self.sync_estado_persistente = True
        estado = (rows[0].get("estado_json") if rows else None) or {}
        if isinstance(estado, str):
            try:
                estado = json.loads(estado)
            except ValueError:
                estado = {}
        return estado if isinstance(estado, dict) else {}

    def save_pncp_sync_state(self, estado: Dict[str, Any]) -> None:
        try:
            self.client.upsert(
                "pncp_sync_estado",
                [{"id": "default", "estado_json": estado, "atualizado_em": datetime.now().isoformat(timespec="seconds")}],
                on_conflict="id",
            )
        except SupabaseError as exc:
            if not self._tabela_ausente(exc):
                raise
            type(self)._sync_estado_local = dict(estado)

    def acquire_pncp_sync_lock(self, owner: str, ttl_seconds: float, now_ts: float) -> bool:
        try:
            self.client.upsert(
                "pncp_sync_estado",
                [{"id": "default", "estado_json": {}, "atualizado_em": ""}],
                on_conflict="id",
                ignore_duplicates=True,
            )
            # PATCH condicional é atômico no Postgres: só uma requisição encontra a linha livre
            _, _, data = self.client._request(
                "PATCH",
                "pncp_sync_estado",
                params={"id": "eq.default", "or": f"(lock_ate.is.null,lock_ate.lt.{now_ts})"},
                payload={"lock_owner": owner, "lock_ate": now_ts + ttl_seconds},
                extra_headers={"Prefer": "return=representation"},
            )
        except SupabaseError as exc:
            if not self._tabela_ausente(exc):
                raise
            self.sync_lock_escopo = "processo"
            atual = type(self)._sync_lock_local
            if atual and atual.get("ate", 0) >= now_ts:
                return False
            type(self)._sync_lock_local = {"owner": owner, "ate": now_ts + ttl_seconds}
            return True
        self.sync_lock_escopo = "banco"
        try:
            rows = json.loads(data.decode("utf-8")) if data else []
        except ValueError:
            rows = []
        return isinstance(rows, list) and len(rows) == 1

    def release_pncp_sync_lock(self, owner: str) -> None:
        if type(self)._sync_lock_local.get("owner") == owner:
            type(self)._sync_lock_local = {}
            return
        self.client.patch(
            "pncp_sync_estado",
            {"id": "eq.default", "lock_owner": f"eq.{owner}"},
            {"lock_owner": None, "lock_ate": None},
        )

    @staticmethod
    def _format_pncp_row(
        row: Dict[str, Any],
        ret_row: Optional[Dict[str, Any]] = None,
        ign_row: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        d = dict(row)
        d["retificado"] = ret_row is not None and ret_row.get("valor_corrigido") is not None
        if ret_row and ret_row.get("valor_original_pncp") is not None:
            d["valor_original_pncp"] = float(ret_row["valor_original_pncp"])
        else:
            d["valor_original_pncp"] = None
        d["justificativa_retificacao"] = str(
            (ret_row or {}).get("justificativa") or ""
        )
        d["ignorado"] = ign_row is not None
        d["motivo_ignorado"] = str((ign_row or {}).get("motivo") or "")
        d["is_cpf"] = is_fornecedor_cpf(str(d.get("fornecedor_cnpj") or ""))
        d["categoria_tematica"] = classify_pncp_contrato(d)
        return d

    def _get_pncp_ignorado_single(self, ctrl: str) -> Optional[Dict[str, Any]]:
        rows = self.client.select(
            "pncp_ignorados",
            params={"numero_controle_pncp": f"eq.{ctrl}", "limit": "1"},
            fetch_all=False,
        )
        return rows[0] if rows else None

    def _get_pncp_retificacao_single(self, ctrl: str) -> Optional[Dict[str, Any]]:
        rows = self.client.select(
            "pncp_retificacoes",
            params={"numero_controle_pncp": f"eq.{ctrl}", "limit": "1"},
            fetch_all=False,
        )
        return rows[0] if rows else None

    def retificar_pncp_contrato(
        self,
        numero_controle_pncp: str,
        valor_corrigido: float,
        justificativa: str = "",
    ) -> Optional[Dict[str, Any]]:
        ctrl = (numero_controle_pncp or "").strip()
        val = float(valor_corrigido)
        if not ctrl or val <= 0:
            return None

        rows = self.client.select(
            "pncp_contratos",
            params={"numero_controle_pncp": f"eq.{ctrl}", "limit": "1"},
            fetch_all=False,
        )
        if not rows:
            return None
        cur_row = rows[0]

        existing_ret = self._get_pncp_retificacao_single(ctrl)
        orig_val = (
            float(existing_ret["valor_original_pncp"])
            if existing_ret and existing_ret.get("valor_original_pncp") is not None
            else float(cur_row.get("valor_global") or 0.0)
        )
        now_iso = datetime.now().isoformat(timespec="seconds")
        just_clean = (justificativa or "").strip()[:500]
        ret_payload = {
            "numero_controle_pncp": ctrl,
            "valor_original_pncp": orig_val,
            "valor_corrigido": val,
            "justificativa": just_clean,
            "atualizado_em": now_iso,
        }
        self.client.upsert(
            "pncp_retificacoes",
            [ret_payload],
            on_conflict="numero_controle_pncp",
        )
        self.client.patch(
            "pncp_contratos",
            {"numero_controle_pncp": f"eq.{ctrl}"},
            {
                "valor_inicial": val,
                "valor_global": val,
                "valor_acumulado": val,
                "atualizado_em": now_iso,
            },
        )
        cur_row.update(
            {
                "valor_inicial": val,
                "valor_global": val,
                "valor_acumulado": val,
                "atualizado_em": now_iso,
            }
        )
        return self._format_pncp_row(cur_row, ret_payload, self._get_pncp_ignorado_single(ctrl))

    def restaurar_pncp_contrato(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        ctrl = (numero_controle_pncp or "").strip()
        if not ctrl:
            return None

        existing_ret = self._get_pncp_retificacao_single(ctrl)
        if not existing_ret:
            return None
        orig_val = float(existing_ret.get("valor_original_pncp") or 0.0)
        now_iso = datetime.now().isoformat(timespec="seconds")

        self.client.patch(
            "pncp_contratos",
            {"numero_controle_pncp": f"eq.{ctrl}"},
            {
                "valor_inicial": orig_val,
                "valor_global": orig_val,
                "valor_acumulado": orig_val,
                "atualizado_em": now_iso,
            },
        )
        self.client.delete(
            "pncp_retificacoes",
            {"numero_controle_pncp": f"eq.{ctrl}"},
        )
        rows = self.client.select(
            "pncp_contratos",
            params={"numero_controle_pncp": f"eq.{ctrl}", "limit": "1"},
            fetch_all=False,
        )
        return self._format_pncp_row(rows[0], None, self._get_pncp_ignorado_single(ctrl)) if rows else None

    def get_all_pncp_retificacoes(self) -> List[Dict[str, Any]]:
        rows = self.client.select(
            "pncp_retificacoes", params={"order": "atualizado_em.desc"}
        )
        rows.sort(key=lambda r: str(r.get("atualizado_em") or ""), reverse=True)
        return rows

    def apply_pncp_retificacoes_batch(self, retificacoes: List[Dict[str, Any]]) -> int:
        if not retificacoes:
            return 0
        applied = 0
        for item in retificacoes:
            if not isinstance(item, dict):
                continue
            ctrl = str(item.get("numero_controle_pncp") or "").strip()
            just = str(
                item.get("justificativa") or item.get("justificativa_retificacao") or ""
            ).strip()
            try:
                val = float(item.get("valor_corrigido") or 0.0)
            except (TypeError, ValueError):
                val = 0.0
            if ctrl and val > 0 and self.retificar_pncp_contrato(ctrl, val, just):
                applied += 1
        return applied

    def ignorar_pncp_contrato(
        self, numero_controle_pncp: str, motivo: str = "Contrato duplicado no PNCP"
    ) -> Optional[Dict[str, Any]]:
        ctrl = (numero_controle_pncp or "").strip()
        if not ctrl:
            return None
        rows = self.client.select(
            "pncp_contratos",
            params={"numero_controle_pncp": f"eq.{ctrl}", "limit": "1"},
            fetch_all=False,
        )
        if not rows:
            return None
        now_iso = datetime.now().isoformat(timespec="seconds")
        motivo_clean = (motivo or "Contrato duplicado no PNCP").strip()[:240] or "Contrato duplicado no PNCP"
        ign_payload = {
            "numero_controle_pncp": ctrl,
            "motivo": motivo_clean,
            "atualizado_em": now_iso,
        }
        self.client.upsert(
            "pncp_ignorados",
            [ign_payload],
            on_conflict="numero_controle_pncp",
        )
        return self._format_pncp_row(rows[0], self._get_pncp_retificacao_single(ctrl), ign_payload)

    def restaurar_pncp_ignorado(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        ctrl = (numero_controle_pncp or "").strip()
        if not ctrl:
            return None
        rows = self.client.select(
            "pncp_contratos",
            params={"numero_controle_pncp": f"eq.{ctrl}", "limit": "1"},
            fetch_all=False,
        )
        if not rows:
            return None
        self.client.delete(
            "pncp_ignorados",
            {"numero_controle_pncp": f"eq.{ctrl}"},
        )
        return self._format_pncp_row(rows[0], self._get_pncp_retificacao_single(ctrl), None)

    def get_all_pncp_ignorados(self) -> List[Dict[str, Any]]:
        rows = self.client.select(
            "pncp_ignorados", params={"order": "atualizado_em.desc"}
        )
        rows.sort(key=lambda r: str(r.get("atualizado_em") or ""), reverse=True)
        return rows

    def sync_pncp_retificacoes_from_request(self, overrides: Dict[str, Any]) -> int:
        """No Supabase o banco centralizado é autoritativo; cabeçalhos legados são ignorados."""
        return 0

    def _fetch_pncp_filtered(
        self,
        ano: Optional[int] = None,
        orgao: Optional[str] = None,
        fornecedor: Optional[str] = None,
        q: Optional[str] = None,
        unidade: Optional[str] = None,
        categoria: Optional[str] = None,
        incluir_ignorados: bool = False,
        somente_ignorados: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[int], int]:
        """Carrega contratos do Supabase com retificações e ignorados e aplica filtros como no SQLite."""
        all_rows = self.client.select("pncp_contratos")
        ret_rows = self.client.select("pncp_retificacoes")
        ret_map = {
            str(r["numero_controle_pncp"]): r
            for r in ret_rows
            if r.get("numero_controle_pncp")
        }
        ign_rows = self.client.select("pncp_ignorados")
        ign_map = {
            str(r["numero_controle_pncp"]): r
            for r in ign_rows
            if r.get("numero_controle_pncp")
        }

        anos_set = {
            int(r["ano_contrato"])
            for r in all_rows
            if str(r.get("numero_controle_pncp") or "") not in ign_map
            and str(r.get("ano_contrato") or "").isdigit()
            and int(r["ano_contrato"]) > 0
        }
        anos_disponiveis = sorted(anos_set, reverse=True)

        org_clean = (orgao or "").strip()
        filtrar_orgao = bool(org_clean and org_clean.lower() not in ("todos", "all", ""))

        org_formatted: List[Dict[str, Any]] = []
        for raw in all_rows:
            ctrl = str(raw.get("numero_controle_pncp") or "")
            ign_row = ign_map.get(ctrl)
            if somente_ignorados:
                if ign_row is None:
                    continue
            elif not incluir_ignorados and ign_row is not None:
                continue

            if filtrar_orgao:
                sigla = str(raw.get("orgao_sigla") or "")
                cnpj = str(raw.get("orgao_cnpj") or "")
                if sigla.upper() != org_clean.upper() and cnpj != org_clean:
                    continue
            org_formatted.append(self._format_pncp_row(raw, ret_map.get(ctrl), ign_row))

        forn_clean = (fornecedor or "").strip()
        uni_clean = (unidade or "").strip()
        q_clean = (q or "").strip().lower()
        cat_clean = (categoria or "").strip()
        filtrar_cat = bool(cat_clean and cat_clean.lower() not in ("todas", "all"))

        filtered: List[Dict[str, Any]] = []
        for item in org_formatted:
            if ano:
                yr = int(ano)
                pub = str(item.get("data_publicacao") or "")
                if int(item.get("ano_contrato") or 0) != yr and not pub.startswith(str(yr)):
                    continue
            if forn_clean:
                fcnpj = str(item.get("fornecedor_cnpj") or "")
                fnome = str(item.get("fornecedor_nome") or "")
                if fcnpj != forn_clean and forn_clean.upper() not in fnome.upper():
                    continue
            if uni_clean:
                if uni_clean.upper() not in str(item.get("unidade_nome") or "").upper():
                    continue
            if q_clean:
                haystack = " ".join(
                    str(item.get(col) or "")
                    for col in (
                        "objeto",
                        "fornecedor_nome",
                        "fornecedor_cnpj",
                        "numero_contrato",
                        "numero_controle_pncp",
                        "unidade_nome",
                        "processo",
                        "orgao_nome",
                    )
                ).lower()
                if q_clean not in haystack:
                    continue
            if filtrar_cat and item.get("categoria_tematica") != cat_clean:
                continue
            filtered.append(item)

        return filtered, org_formatted, anos_disponiveis, len(ign_map)

    def query_pncp_contratos(
        self,
        ano: Optional[int] = None,
        orgao: Optional[str] = None,
        fornecedor: Optional[str] = None,
        q: Optional[str] = None,
        categoria: Optional[str] = None,
        unidade: Optional[str] = None,
        ordem: str = "recentes",
        pagina: int = 1,
        limite: int = 50,
        incluir_ignorados: bool = False,
        somente_ignorados: bool = False,
    ) -> Dict[str, Any]:
        pagina = max(1, int(pagina))
        limite = max(1, min(200, int(limite)))
        offset = (pagina - 1) * limite

        filtered, _, _, total_ignorados = self._fetch_pncp_filtered(
            ano=ano,
            orgao=orgao,
            fornecedor=fornecedor,
            q=q,
            unidade=unidade,
            categoria=categoria,
            incluir_ignorados=incluir_ignorados,
            somente_ignorados=somente_ignorados,
        )

        ordem_clean = (ordem or "recentes").strip().lower()
        if ordem_clean == "maior_valor":
            filtered.sort(
                key=lambda c: (
                    float(c.get("valor_global") or 0.0),
                    str(c.get("data_publicacao") or c.get("data_assinatura") or ""),
                ),
                reverse=True,
            )
        elif ordem_clean == "menor_valor":
            filtered.sort(
                key=lambda c: str(c.get("data_publicacao") or c.get("data_assinatura") or ""),
                reverse=True,
            )
            filtered.sort(key=lambda c: float(c.get("valor_global") or 0.0))
        else:
            filtered.sort(
                key=lambda c: (
                    str(c.get("data_publicacao") or c.get("data_assinatura") or ""),
                    int(c.get("ano_contrato") or 0),
                    float(c.get("valor_global") or 0.0),
                ),
                reverse=True,
            )

        total = len(filtered)
        total_paginas = max(1, (total + limite - 1) // limite)
        return {
            "items": filtered[offset : offset + limite],
            "total": total,
            "pagina": pagina,
            "limite": limite,
            "total_paginas": total_paginas,
            "total_ignorados": total_ignorados,
        }

    def get_pncp_estatisticas(
        self, ano: Optional[int] = None, orgao: Optional[str] = None
    ) -> Dict[str, Any]:
        filtered, _, _, total_ignorados = self._fetch_pncp_filtered(ano=ano, orgao=orgao)
        est = build_pncp_estatisticas(filtered)
        est["total_ignorados"] = total_ignorados
        if isinstance(est.get("resumo_geral"), dict):
            est["resumo_geral"]["contratos_ignorados"] = total_ignorados
        return est

    def get_pncp_resumo(
        self, ano: Optional[int] = None, orgao: Optional[str] = None
    ) -> Dict[str, Any]:
        filtered, org_formatted, anos_disponiveis, total_ignorados = self._fetch_pncp_filtered(
            ano=ano, orgao=orgao
        )
        return build_pncp_resumo_from_rows(
            filtered_rows=filtered,
            org_rows=org_formatted,
            anos_disponiveis=anos_disponiveis,
            total_ignorados=total_ignorados,
        )

    def get_effective_pncp_contratos(
        self,
        ano: Optional[int] = None,
        anos: Sequence[int] = (2025, 2026),
    ) -> List[Dict[str, Any]]:
        """Retorna todos os contratos PNCP ativos no Supabase (com retificações e sem ignorados)."""
        filtered, _, _, _ = self._fetch_pncp_filtered(ano=ano)
        if not ano and anos:
            anos_set = {int(a) for a in anos if int(a) > 0}
            filtered = [
                r
                for r in filtered
                if int(r.get("ano_contrato") or 0) in anos_set
                or any(str(r.get("data_publicacao") or "").startswith(str(y)) for y in anos_set)
            ]
        filtered.sort(key=lambda r: float(r.get("valor_global") or 0.0), reverse=True)
        return filtered

    # =========================================================================
    # PORTAL TRANSPARÊNCIA FIORILLI (Receitas, Despesas e Contratos/Dispensas)
    # =========================================================================

    def _upsert_fiorilli_rows(
        self,
        table: str,
        pk_col: str,
        rows: List[Dict[str, Any]],
        int_cols: Tuple[str, ...],
        float_cols: Tuple[str, ...],
        str_cols: Tuple[str, ...],
    ) -> int:
        if not rows:
            return 0
        now_iso = datetime.now().isoformat(timespec="seconds")
        dedup: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            pk_val = str(r.get(pk_col) or "").strip()
            if not pk_val:
                continue
            item: Dict[str, Any] = {pk_col: pk_val}
            for c in int_cols:
                item[c] = int(r.get(c) or 0)
            for c in float_cols:
                item[c] = float(r.get(c) or 0.0)
            for c in str_cols:
                if c == pk_col:
                    continue
                if c == "atualizado_em":
                    item[c] = str(r.get(c) or now_iso)
                else:
                    item[c] = str(r.get(c) or "")
            dedup[pk_val] = item
        payload = list(dedup.values())
        if not payload:
            return 0
        return self.client.upsert(
            table,
            payload,
            on_conflict=pk_col,
            ignore_duplicates=False,
            batch_size=200,
        )

    def upsert_fiorilli_receitas_batch(self, rows: List[Dict[str, Any]]) -> int:
        return self._upsert_fiorilli_rows(
            "fiorilli_receitas",
            "chave_receita",
            rows,
            int_cols=("ano", "eh_raiz"),
            float_cols=(
                "previsao_inicial",
                "previsao_atualizada",
                "arrecadado_periodo",
                "arrecadado_total",
            ),
            str_cols=("tipo_fonte", "ordem", "codigo", "nome", "atualizado_em"),
        )

    def upsert_fiorilli_despesas_orgaos_batch(self, rows: List[Dict[str, Any]]) -> int:
        return self._upsert_fiorilli_rows(
            "fiorilli_despesas_orgaos",
            "chave_orgao",
            rows,
            int_cols=("ano",),
            float_cols=(
                "dotacao_inicial",
                "alteracoes",
                "dotacao_atualizada",
                "empenhado",
                "liquidado",
                "pago",
            ),
            str_cols=("empresa", "codigo", "orgao_nome", "atualizado_em"),
        )

    def upsert_fiorilli_despesas_fornecedores_batch(self, rows: List[Dict[str, Any]]) -> int:
        return self._upsert_fiorilli_rows(
            "fiorilli_despesas_fornecedores",
            "chave_fornecedor",
            rows,
            int_cols=("ano",),
            float_cols=("empenhado", "liquidado", "pago"),
            str_cols=(
                "empresa",
                "codigo_fornecedor",
                "fornecedor_doc",
                "fornecedor_nome",
                "cidade",
                "atualizado_em",
            ),
        )

    def upsert_fiorilli_contratos_batch(self, rows: List[Dict[str, Any]]) -> int:
        return self._upsert_fiorilli_rows(
            "fiorilli_contratos",
            "chave_contrato",
            rows,
            int_cols=("ano", "eh_contratacao_direta", "anulado"),
            float_cols=(
                "valor_contrato",
                "valor_aditado",
                "valor_efetivo",
                "empenhado",
                "liquidado",
                "saldo_empenhar",
            ),
            str_cols=(
                "empresa",
                "entidade",
                "codigo",
                "contrato_num",
                "processo_licitacao",
                "processo_adm",
                "fund_legal",
                "modalidade",
                "tipo_contratacao",
                "fornecedor_doc",
                "fornecedor_nome",
                "objeto",
                "secretaria_gestora",
                "categoria_tematica",
                "data_assinatura",
                "data_publicacao",
                "vigencia_inicio",
                "vigencia_fim",
                "data_anulacao",
                "atualizado_em",
            ),
        )

    def get_fiorilli_receitas(
        self, ano: Optional[int] = None, tipo_fonte: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {}
        if ano:
            params["ano"] = f"eq.{int(ano)}"
        if tipo_fonte:
            params["tipo_fonte"] = f"eq.{tipo_fonte.strip()}"
        rows = self.client.select("fiorilli_receitas", params=params or None)
        rows.sort(
            key=lambda r: (
                -int(r.get("ano") or 0),
                str(r.get("tipo_fonte") or ""),
                str(r.get("codigo") or ""),
            )
        )
        return rows

    def get_fiorilli_despesas_orgaos(self, ano: Optional[int] = None) -> List[Dict[str, Any]]:
        params = {"ano": f"eq.{int(ano)}"} if ano else None
        rows = self.client.select("fiorilli_despesas_orgaos", params=params)
        rows.sort(key=lambda r: (int(r.get("ano") or 0), float(r.get("pago") or 0.0)), reverse=True)
        return rows

    def get_fiorilli_despesas_fornecedores(self, ano: Optional[int] = None) -> List[Dict[str, Any]]:
        params = {"ano": f"eq.{int(ano)}"} if ano else None
        rows = self.client.select("fiorilli_despesas_fornecedores", params=params)
        rows.sort(key=lambda r: (int(r.get("ano") or 0), float(r.get("pago") or 0.0)), reverse=True)
        return rows

    def get_fiorilli_contratos(
        self,
        ano: Optional[int] = None,
        entidade: Optional[str] = None,
        somente_diretas: bool = False,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {}
        if ano:
            params["ano"] = f"eq.{int(ano)}"
        if somente_diretas:
            params["eh_contratacao_direta"] = "eq.1"
        rows = self.client.select("fiorilli_contratos", params=params or None)
        if entidade and entidade.strip().upper() not in ("TODAS", "ALL", ""):
            ent_up = entidade.strip().upper()
            rows = [r for r in rows if str(r.get("entidade") or "").strip().upper() == ent_up]
        rows.sort(
            key=lambda r: (int(r.get("ano") or 0), float(r.get("valor_efetivo") or 0.0)),
            reverse=True,
        )
        return rows

    def get_fiorilli_meta(self) -> Dict[str, Any]:
        cnt_rec = self.client.count("fiorilli_receitas")
        cnt_org = self.client.count("fiorilli_despesas_orgaos")
        cnt_forn = self.client.count("fiorilli_despesas_fornecedores")
        cnt_con = self.client.count("fiorilli_contratos")
        rec_years = self.client.select("fiorilli_receitas", params={"select": "ano,atualizado_em"})
        con_years = self.client.select("fiorilli_contratos", params={"select": "ano,atualizado_em"})
        anos_set = {
            int(r["ano"])
            for r in (rec_years + con_years)
            if str(r.get("ano") or "").isdigit() and int(r["ano"]) > 0
        }
        ultima = max(
            (str(r.get("atualizado_em") or "") for r in (rec_years + con_years)),
            default="",
        )
        return {
            "sincronizado": (cnt_rec + cnt_org + cnt_forn + cnt_con) > 0,
            "anos_disponiveis": sorted(anos_set, reverse=True) or [2026, 2025],
            "ultima_atualizacao": ultima,
            "contagens": {
                "fiorilli_receitas": cnt_rec,
                "fiorilli_despesas_orgaos": cnt_org,
                "fiorilli_despesas_fornecedores": cnt_forn,
                "fiorilli_contratos": cnt_con,
            },
        }
