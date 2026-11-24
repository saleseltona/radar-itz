"""
Exportador de Arquivos Obrigatórios em CSV (Seção 12).
Gera:
- output/pautas_novas.csv (sem formatação markdown, UTF-8, esquema fixo)
- output/historico_execucoes.csv
- output/fontes_status.csv
- output/pendencias_apuracao.csv
Permite alternar delimitador entre vírgula (,) e ponto e vírgula (;).
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import (
    COLUNAS_FONTES_STATUS,
    COLUNAS_HISTORICO_EXECUCOES,
    COLUNAS_PAUTAS_CSV,
    COLUNAS_PENDENCIAS_APURACAO,
    OUTPUT_DIR,
)
from src.database import RadarDatabase

logger = logging.getLogger("radar_imperatriz.exporter")


def strip_markdown(text: Any) -> str:
    """Remove qualquer formatação Markdown de textos a serem gravados em CSV."""
    if text is None:
        return ""
    s = str(text)
    # Remove links [texto](url) -> texto (url)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", s)
    # Remove negrito/itálico (*, _)
    s = re.sub(r"[*_]{1,3}", "", s)
    # Remove hashtags de cabeçalho
    s = re.sub(r"^\s*#+\s*", "", s, flags=re.MULTILINE)
    # Remove quebras de linha excessivas dentro de campos CSV
    s = re.sub(r"[\r\n]+", " ", s)
    return s.strip()


class CSVExporter:
    """Gera os arquivos CSV obrigatórios com integridade de cabeçalho e codificação UTF-8."""

    def __init__(self, db: RadarDatabase, output_dir: Path | None = None) -> None:
        self.db = db
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_pautas_novas(
        self, pautas: List[Dict[str, Any]], delimiter: str = ","
    ) -> Path:
        """Gera output/pautas_novas.csv e versão compatível com Excel brasileiro se ponto e vírgula."""
        csv_file = self.output_dir / "pautas_novas.csv"

        with open(csv_file, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=COLUNAS_PAUTAS_CSV,
                delimiter=delimiter,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()

            if not pautas:
                # Se nenhuma pauta nova, grava aviso sem corromper o esquema
                linha_aviso = {c: "" for c in COLUNAS_PAUTAS_CSV}
                linha_aviso["id_pauta"] = "INFO"
                linha_aviso["titulo_jornalistico"] = "Nenhuma pauta inédita identificada nesta execução"
                linha_aviso["situacao_editorial"] = "Sem novidades"
                writer.writerow(linha_aviso)
            else:
                for p in pautas:
                    row = {}
                    for col in COLUNAS_PAUTAS_CSV:
                        val = p.get(col, "")
                        row[col] = strip_markdown(val)
                    writer.writerow(row)

        logger.info("Arquivo %s gerado com %d registros.", csv_file, len(pautas))

        # Se o delimitador for ',', gera também uma cópia com ';' para compatibilidade direta no Excel BR
        if delimiter == ",":
            ptbr_file = self.output_dir / "pautas_novas_ptbr.csv"
            with open(ptbr_file, mode="w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=COLUNAS_PAUTAS_CSV,
                    delimiter=";",
                    quoting=csv.QUOTE_MINIMAL,
                )
                writer.writeheader()
                for p in pautas:
                    row = {col: strip_markdown(p.get(col, "")) for col in COLUNAS_PAUTAS_CSV}
                    writer.writerow(row)

        return csv_file

    def export_historico_execucoes(self, delimiter: str = ",") -> Path:
        """Gera output/historico_execucoes.csv a partir do banco de dados SQLite."""
        csv_file = self.output_dir / "historico_execucoes.csv"
        execucoes = self.db.get_all_execucoes()

        with open(csv_file, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=COLUNAS_HISTORICO_EXECUCOES,
                delimiter=delimiter,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()
            for ex in execucoes:
                row = {}
                for col in COLUNAS_HISTORICO_EXECUCOES:
                    row[col] = strip_markdown(ex.get(col, ""))
                writer.writerow(row)

        return csv_file

    def export_fontes_status(self, delimiter: str = ",") -> Path:
        """Gera output/fontes_status.csv com o estado de cada fonte cadastrada."""
        csv_file = self.output_dir / "fontes_status.csv"
        fontes = self.db.get_all_fontes()

        with open(csv_file, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=COLUNAS_FONTES_STATUS,
                delimiter=delimiter,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()
            for fonte in fontes:
                row = {
                    "nome": strip_markdown(fonte.get("nome", "")),
                    "url": strip_markdown(fonte.get("url", "")),
                    "ultima_tentativa": strip_markdown(fonte.get("ultima_consulta", "")),
                    "ultima_coleta_bem_sucedida": strip_markdown(fonte.get("ultima_coleta_sucesso", "")),
                    "status": strip_markdown(fonte.get("situacao_funcionamento", "")),
                    "metodo_acesso": strip_markdown(fonte.get("metodo_acesso", "")),
                    "erro_identificado": strip_markdown(fonte.get("erro_identificado", "")),
                    "proxima_acao_recomendada": strip_markdown(fonte.get("proxima_acao_recomendada", "")),
                }
                writer.writerow(row)

        return csv_file

    def export_pendencias_apuracao(self, delimiter: str = ",") -> Path:
        """Gera output/pendencias_apuracao.csv com as pautas que exigem apuração."""
        csv_file = self.output_dir / "pendencias_apuracao.csv"
        pendencias = self.db.get_all_pendencias()

        with open(csv_file, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=COLUNAS_PENDENCIAS_APURACAO,
                delimiter=delimiter,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()
            for item in pendencias:
                row = {}
                for col in COLUNAS_PENDENCIAS_APURACAO:
                    row[col] = strip_markdown(item.get(col, ""))
                writer.writerow(row)

        return csv_file
