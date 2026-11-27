"""
Preenche a tabela `evidencias` a partir do texto já salvo em pautas.evidencias_verificadas.
Idempotente: evidências já gravadas são ignoradas.

Uso:
    python scripts/backfill_evidencias.py
    python scripts/backfill_evidencias.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DB_PATH  # noqa: E402
from src.database import get_database  # noqa: E402
from src.evidence_store import parse_evidencias, persist_evidencias  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="Só conta, não grava")
    args = parser.parse_args()

    db = get_database(DB_PATH)
    pautas = db.get_all_pautas()
    total = 0
    for p in pautas:
        texto = p.get("evidencias_verificadas", "")
        if args.dry_run:
            total += len(parse_evidencias(texto))
        else:
            total += persist_evidencias(db, p["id_pauta"], texto, p.get("url_original", ""))
    acao = "seriam gravadas" if args.dry_run else "processadas"
    print(f"{len(pautas)} pautas lidas, {total} evidências {acao}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
