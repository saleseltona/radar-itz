"""
Script CLI para coletar e gravar no banco os Dados Abertos JSON do Portal
Transparência Fiorilli (Imperatriz - MA) para os exercícios 2025 e 2026.

Uso:
  python scripts/fiorilli_sync.py
  python scripts/fiorilli_sync.py --anos 2025 2026
  python scripts/fiorilli_sync.py --anos 2026 --backend sqlite
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import FIORILLI_DEFAULT_YEARS, load_dotenv_file  # noqa: E402
from src.database import get_database  # noqa: E402
from src.fiorilli_client import FiorilliClient  # noqa: E402


def main() -> int:
    load_dotenv_file()

    parser = argparse.ArgumentParser(
        description="Sincroniza Receitas, Despesas e Contratos/Dispensas do Portal Fiorilli (2025–2026)."
    )
    parser.add_argument(
        "--anos",
        nargs="+",
        type=int,
        default=list(FIORILLI_DEFAULT_YEARS),
        help="Exercícios financeiros a sincronizar (padrão: 2025 2026).",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "sqlite", "supabase"),
        default="auto",
        help="Backend de destino (padrão: auto conforme configuração .env).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Timeout por requisição HTTP em segundos (padrão: 45).",
    )

    args = parser.parse_args()
    if args.backend != "auto":
        os.environ["RADAR_STORAGE_BACKEND"] = args.backend

    db = get_database()
    client = FiorilliClient(db, timeout=args.timeout)
    print(f"Backend ativo: {getattr(db, 'backend_name', 'sqlite')}")
    print(f"Sincronizando Portal Transparência Fiorilli ({client.base_url}) para anos {args.anos}...")

    resumo = client.sync_imperatriz(anos=args.anos)
    print(
        f"Concluído em {resumo['duracao_segundos']}s | total_registros={resumo['total_registros']} "
        f"| ok={resumo['ok']}"
    )
    for ano, st in sorted(resumo["por_ano"].items(), reverse=True):
        print(
            f"  Ano {ano}: receitas={st.get('fiorilli_receitas', 0)}, "
            f"orgaos={st.get('fiorilli_despesas_orgaos', 0)}, "
            f"fornecedores={st.get('fiorilli_despesas_fornecedores', 0)}, "
            f"contratos/dispensas={st.get('fiorilli_contratos', 0)}"
        )
    if resumo["erros"]:
        print("Erros reportados:", file=sys.stderr)
        for err in resumo["erros"]:
            print(f"  - {err}", file=sys.stderr)
        return 1 if resumo["total_registros"] == 0 else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
