"""
Script CLI de carga inicial e sincronização incremental ("só upar novos")
do banco SQLite local (`data/radar_imperatriz.db`) para o Supabase (PostgreSQL via PostgREST).

Uso:
  python scripts/supabase_sync.py --check
  python scripts/supabase_sync.py --dry-run
  python scripts/supabase_sync.py
  python scripts/supabase_sync.py --only-pncp
  python scripts/supabase_sync.py --tables pncp_retificacoes,pncp_contratos
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import DB_PATH, get_supabase_config, load_dotenv_file  # noqa: E402
from src.database import RadarDatabase  # noqa: E402
from src.pncp_analytics import classify_pncp_contrato  # noqa: E402
from src.supabase_store import SupabaseError, SupabaseRestClient  # noqa: E402

TABLE_SPECS: List[Dict[str, Any]] = [
    {
        "table": "fontes",
        "key_cols": ("url",),
        "on_conflict": "url",
        "strip_id": True,
    },
    {
        "table": "execucoes",
        "key_cols": ("id_execucao",),
        "on_conflict": "id_execucao",
        "strip_id": False,
    },
    {
        "table": "pautas",
        "key_cols": ("id_pauta", "url_original"),
        "on_conflict": "id_pauta",
        "strip_id": False,
    },
    {
        "table": "pautas_arquivadas",
        "key_cols": ("id_pauta",),
        "on_conflict": "id_pauta",
        "strip_id": False,
    },
    {
        "table": "pendencias_apuracao",
        "key_cols": ("id_pauta",),
        "on_conflict": "id_pauta",
        "strip_id": True,
    },
    {
        "table": "pncp_retificacoes",
        "key_cols": ("numero_controle_pncp",),
        "on_conflict": "numero_controle_pncp",
        "strip_id": False,
    },
    {
        "table": "pncp_ignorados",
        "key_cols": ("numero_controle_pncp",),
        "on_conflict": "numero_controle_pncp",
        "strip_id": False,
    },
    {
        "table": "pncp_contratos",
        "key_cols": ("numero_controle_pncp",),
        "on_conflict": "numero_controle_pncp",
        "strip_id": False,
    },
    {
        "table": "paginas_coletadas",
        "key_cols": ("id_execucao", "url", "conteudo_hash"),
        "on_conflict": "id_execucao,url,conteudo_hash",
        "strip_id": True,
    },
    {
        "table": "evidencias",
        "key_cols": ("id_pauta", "tipo_evidencia", "referencia_url_ou_doc"),
        "on_conflict": "id_pauta,tipo_evidencia,referencia_url_ou_doc",
        "strip_id": True,
    },
    {
        "table": "historico_atualizacoes",
        "key_cols": ("id_pauta", "id_execucao", "data_atualizacao", "tipo_mudanca"),
        "on_conflict": "id_pauta,id_execucao,data_atualizacao,tipo_mudanca",
        "strip_id": True,
    },
    {
        "table": "itens_descartados",
        "key_cols": ("id_execucao", "url"),
        "on_conflict": "id_execucao,url",
        "strip_id": True,
    },
    {
        "table": "erros_coleta",
        "key_cols": ("id_execucao", "componente", "alvo_url", "data_registro"),
        "on_conflict": "id_execucao,componente,alvo_url,data_registro",
        "strip_id": True,
    },
]

SPEC_BY_TABLE: Dict[str, Dict[str, Any]] = {s["table"]: s for s in TABLE_SPECS}


def _row_key(row: Dict[str, Any], key_cols: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(str(row.get(col) or "") for col in key_cols)


def sync_sqlite_to_supabase(
    sqlite_db: RadarDatabase,
    client: SupabaseRestClient,
    tables: Optional[List[str]] = None,
    dry_run: bool = False,
    force_update: bool = False,
) -> Dict[str, Dict[str, int]]:
    """
    Sincroniza registros do SQLite local para o Supabase.
    Por padrão (`force_update=False`), consulta antes as chaves no Supabase e
    envia estritamente os registros novos, sem duplicar e sem sobrescrever retificações.
    """
    selected = tables or [s["table"] for s in TABLE_SPECS]
    selected_specs = [SPEC_BY_TABLE[t] for t in selected if t in SPEC_BY_TABLE]

    summary: Dict[str, Dict[str, int]] = {}
    remote_ret_map: Optional[Dict[str, Dict[str, Any]]] = None

    for spec in selected_specs:
        table = spec["table"]
        key_cols: Tuple[str, ...] = spec["key_cols"]
        on_conflict: str = spec["on_conflict"]
        strip_id: bool = bool(spec["strip_id"])

        with sqlite_db.connect() as conn:
            local_raw = conn.execute(f"SELECT * FROM {table}").fetchall()
            local_rows = [dict(r) for r in local_raw]

        if table == "pncp_contratos":
            remote_ret_rows = client.select("pncp_retificacoes")
            remote_ret_map = {
                str(r.get("numero_controle_pncp") or ""): r
                for r in remote_ret_rows
                if r.get("numero_controle_pncp")
            }

        remote_existing_rows = client.select(
            table,
            params={"select": ",".join(key_cols)},
        )
        remote_keys = {_row_key(r, key_cols) for r in remote_existing_rows}

        if table == "pautas":
            existing_pauta_ids = {str(r.get("id_pauta") or "") for r in remote_existing_rows}
            existing_pauta_urls = {str(r.get("url_original") or "") for r in remote_existing_rows}

        to_send: List[Dict[str, Any]] = []
        seen_batch_keys: set[Tuple[str, ...]] = set()
        seen_pauta_urls: set[str] = set()

        for orig in local_rows:
            row = dict(orig)
            if strip_id:
                row.pop("id", None)

            if table == "pncp_contratos":
                ctrl = str(row.get("numero_controle_pncp") or "").strip()
                row["categoria_tematica"] = classify_pncp_contrato(row)
                if remote_ret_map and ctrl in remote_ret_map:
                    corrigido = float(
                        remote_ret_map[ctrl].get("valor_corrigido")
                        or row.get("valor_global")
                        or 0.0
                    )
                    row["valor_inicial"] = corrigido
                    row["valor_global"] = corrigido
                    row["valor_acumulado"] = corrigido

            k = _row_key(row, key_cols)
            if k in seen_batch_keys:
                continue

            if table == "pautas":
                pid = str(row.get("id_pauta") or "")
                purl = str(row.get("url_original") or "")
                if purl in seen_pauta_urls:
                    continue
                if not force_update and (pid in existing_pauta_ids or purl in existing_pauta_urls):
                    continue
                seen_pauta_urls.add(purl)
            else:
                if not force_update and k in remote_keys:
                    continue

            seen_batch_keys.add(k)
            to_send.append(row)

        sent = 0
        if not dry_run and to_send:
            sent = client.upsert(
                table,
                to_send,
                on_conflict=on_conflict,
                ignore_duplicates=(not force_update),
                batch_size=200,
            )

        summary[table] = {
            "local": len(local_rows),
            "ja_no_supabase": len(remote_existing_rows),
            "novos_pendentes": len(to_send),
            "novos_enviados": sent,
            "ignorados": max(0, len(local_rows) - len(to_send)),
        }

    return summary


def check_counts(sqlite_db: RadarDatabase, client: SupabaseRestClient, tables: List[str]) -> int:
    print(f"{'Tabela':<26} {'SQLite Local':>12} {'Supabase':>12} {'Diferença':>12}")
    print("-" * 66)
    for t in tables:
        if t not in SPEC_BY_TABLE:
            continue
        with sqlite_db.connect() as conn:
            local_cnt = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        remote_cnt = client.count(t)
        diff = local_cnt - remote_cnt
        print(f"{t:<26} {local_cnt:>12} {remote_cnt:>12} {diff:>12}")
    return 0


def main() -> int:
    load_dotenv_file()

    parser = argparse.ArgumentParser(
        description="Sincronização incremental (só upar novos) do SQLite local para o Supabase."
    )
    parser.add_argument(
        "--db",
        default=str(DB_PATH),
        help="Caminho do banco SQLite de origem (padrão: data/radar_imperatriz.db).",
    )
    parser.add_argument(
        "--tables",
        default="",
        help="Lista separada por vírgula de tabelas específicas.",
    )
    parser.add_argument(
        "--only-pncp",
        action="store_true",
        help="Atalho para sincronizar apenas pncp_retificacoes e pncp_contratos.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula a carga e mostra quantos registros novos seriam enviados, sem gravar.",
    )
    parser.add_argument(
        "--force-update",
        action="store_true",
        help="Atualiza também registros já existentes no Supabase (sempre preservando retificações).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Apenas compara contagem de linhas entre SQLite local e Supabase.",
    )

    args = parser.parse_args()

    url, key = get_supabase_config()
    if not url or not key:
        print(
            "ERRO: Credenciais Supabase não configuradas.\n"
            "Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no arquivo .env ou variáveis de ambiente.",
            file=sys.stderr,
        )
        return 1

    db_file = Path(args.db)
    if not db_file.exists():
        print(f"ERRO: Banco SQLite não encontrado em: {db_file}", file=sys.stderr)
        return 1

    if args.only_pncp:
        selected_tables = ["pncp_retificacoes", "pncp_ignorados", "pncp_contratos"]
    elif args.tables.strip():
        selected_tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    else:
        selected_tables = [s["table"] for s in TABLE_SPECS]

    sqlite_db = RadarDatabase(db_file)
    client = SupabaseRestClient(url=url, api_key=key)

    try:
        if args.check:
            return check_counts(sqlite_db, client, selected_tables)

        modo_txt = "DRY-RUN (simulação)" if args.dry_run else (
            "UPSERT COMPLETO" if args.force_update else "INCREMENTAL (só novos)"
        )
        print(f"Conectado ao Supabase: {url}")
        print(f"Origem SQLite: {db_file} | Modo: {modo_txt}")
        print("-" * 74)

        resumo = sync_sqlite_to_supabase(
            sqlite_db=sqlite_db,
            client=client,
            tables=selected_tables,
            dry_run=args.dry_run,
            force_update=args.force_update,
        )
        for tbl, st in resumo.items():
            print(
                f"{tbl:<24} local={st['local']:>4} | ja_no_supa={st['ja_no_supabase']:>4} | "
                f"pendentes={st['novos_pendentes']:>4} | enviados={st['novos_enviados']:>4} | "
                f"ignorados={st['ignorados']:>4}"
            )
        return 0
    except SupabaseError as se:
        print(f"ERRO Supabase: {se}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
