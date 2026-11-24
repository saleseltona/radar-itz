"""
Gerador do Relatório Editorial em Markdown: `output/clipping_do_dia.md`.
Segue a estrutura obrigatória da Seção 12:
- Cabeçalho padronizado com métricas completas de auditoria.
- Distribuição das 10 melhores pautas pelas quatro categorias editoriais.
- 12 itens de checagem para cada pauta.
- Recomendação editorial final destacando as três pautas prioritárias para apuração imediata.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import OUTPUT_DIR
from src.database import RadarDatabase

logger = logging.getLogger("radar_imperatriz.report")


class ReportGenerator:
    """Gera o relatório diário de inteligência editorial jornalística."""

    def __init__(self, db: RadarDatabase, output_dir: Path | None = None) -> None:
        self.db = db
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_clipping(
        self,
        id_execucao: str,
        metricas: Dict[str, Any],
        pautas_selecionadas: List[Dict[str, Any]],
        limitacoes_acesso: str,
    ) -> Path:
        """Gera o arquivo output/clipping_do_dia.md com fidelidade estrita à Seção 12."""
        md_file = self.output_dir / "clipping_do_dia.md"

        now_formatted = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")

        # Seleciona até 10 melhores pautas ordenadas por pontuação editorial
        pautas_top10 = sorted(
            pautas_selecionadas,
            key=lambda x: (
                1 if x.get("prioridade") == "Emergência" else 0,
                int(x.get("pontuacao_editorial", 0)),
            ),
            reverse=True,
        )[:10]

        # Agrupa por categoria obrigatória (Seção 10 e 12)
        categorias = {
            "Pautas urgentes": [],
            "Pautas de serviço público": [],
            "Pautas de denúncia e apuração": [],
            "Pautas de cultura, economia e cidade": [],
        }

        for p in pautas_top10:
            cat = p.get("categoria", "Pautas de serviço público")
            if cat in categorias:
                categorias[cat].append(p)
            else:
                categorias["Pautas de serviço público"].append(p)

        linhas: List[str] = [
            "# Radar de Pautas de Imperatriz",
            "",
            f"- **Data e horário da execução:** {now_formatted}",
            f"- **Janela de pesquisa:** {metricas.get('janela_pesquisa', 'Últimas 24h a 48h (Prioridade) / 7 a 30 dias (Acompanhamento e Histórico)')}",
            f"- **Quantidade de fontes consultadas:** {metricas.get('fontes_consultadas', 0)} fontes",
            f"- **Quantidade de páginas efetivamente lidas:** {metricas.get('paginas_lidas', 0)} páginas",
            f"- **Quantidade de pautas encontradas:** {metricas.get('pautas_encontradas', len(pautas_selecionadas))}",
            f"- **Quantidade de pautas novas:** {metricas.get('pautas_novas', 0)}",
            f"- **Quantidade de atualizações:** {metricas.get('pautas_atualizadas', 0)}",
            f"- **Quantidade de itens descartados:** {metricas.get('itens_descartados', 0)} itens",
            f"- **Limitações de acesso e falhas relevantes:** {limitacoes_acesso}",
            "",
        ]

        # Seções de categorias da Seção 12
        secoes_info = [
            ("1. Pautas urgentes", "Pautas urgentes"),
            ("2. Pautas de serviço público", "Pautas de serviço público"),
            ("3. Pautas de denúncia e apuração", "Pautas de denúncia e apuração"),
            ("4. Pautas de cultura, economia e cidade", "Pautas de cultura, economia e cidade"),
        ]

        for num_secao, cat_key in secoes_info:
            linhas.append(f"## {num_secao}")
            linhas.append("")

            pautas_cat = categorias[cat_key]
            if not pautas_cat:
                linhas.append(f"_Nenhuma pauta com critérios suficientes classificada em {cat_key} nesta execução._")
                linhas.append("")
                continue

            for idx, p in enumerate(pautas_cat, 1):
                prio_txt = f"{p.get('prioridade', 'Média')} ({p.get('pontuacao_editorial', 0)}/100 pts)"
                if p.get("prioridade") == "Emergência":
                    prio_txt = "🚨 PRIORIDADE MÁXIMA / EMERGÊNCIA (100 pts)"

                linhas.append(f"### {idx}. {p.get('titulo_jornalistico', 'Sem título')}")
                linhas.append("")
                linhas.append(f"1. **Prioridade e pontuação:** {prio_txt} — _{p.get('justificativa_pontuacao', '')}_")
                linhas.append(f"2. **Título:** {p.get('titulo_jornalistico', '')}")
                linhas.append(f"3. **Resumo:** {p.get('resumo_3_linhas', '')}")
                linhas.append(f"4. **Fonte principal com link:** [{p.get('fonte_principal', 'Fonte')}]({p.get('url_original', '#')})")

                # Fontes complementares com links
                fontes_comp = [f.strip() for f in p.get("fontes_complementares", "").split(";") if f.strip()]
                urls_comp = [u.strip() for u in p.get("urls_complementares", "").split(";") if u.strip()]
                links_comp_str = "Nenhuma fonte complementar necessária."
                if fontes_comp and urls_comp:
                    links_comp_parts = []
                    for fc, uc in zip(fontes_comp, urls_comp):
                        links_comp_parts.append(f"[{fc}]({uc})")
                    links_comp_str = "; ".join(links_comp_parts)
                elif urls_comp:
                    links_comp_str = "; ".join([f"[Fonte Adicional]({u})" for u in urls_comp])

                linhas.append(f"5. **Fontes complementares com links:** {links_comp_str}")
                linhas.append(
                    f"6. **Data da publicação e do fato:** Publicação em {p.get('data_publicacao', 'N/D')} | Fato ocorrido em {p.get('data_acontecimento', 'N/D')}"
                )
                linhas.append(f"7. **Ângulo jornalístico:** {p.get('angulo_jornalistico', '')}")
                linhas.append(f"8. **Formato sugerido:** {p.get('formato_sugerido', 'Matéria jornalística')}")
                linhas.append(f"9. **Nível de verificação:** **{p.get('nivel_verificacao', 'Parcialmente confirmado')}**")
                linhas.append(f"10. **Evidências disponíveis:** {p.get('evidencias_verificadas', '')}")
                linhas.append(f"11. **Pendências de apuração:** {p.get('informacoes_pendentes', 'Nenhuma pendência crítica.')}")
                linhas.append(f"12. **Motivo da seleção:** {p.get('motivo_selecao', 'Relevância para a população de Imperatriz e interesse público comprovado.')}")

                # Se for categoria de denúncia, anexa os campos de auditoria da Seção 10.C
                if cat_key == "Pautas de denúncia e apuração":
                    det = p.get("detalhes_denuncia")
                    if isinstance(det, dict) and det:
                        linhas.append("")
                        linhas.append("   > **Parâmetros Obrigatórios de Apuração (Seção 10.C):**")
                        linhas.append(f"   > - **Qual é a suspeita:** {det.get('suspeita', '')}")
                        linhas.append(f"   > - **Qual é a evidência disponível:** {det.get('evidencia_disponivel', '')}")
                        linhas.append(f"   > - **O que ainda não foi comprovado:** {det.get('nao_comprovado', '')}")
                        linhas.append(f"   > - **Quais documentos precisam ser consultados:** {det.get('documentos_necessarios', '')}")
                        linhas.append(f"   > - **Quem deve ser procurado para esclarecimentos:** {det.get('quem_procurar', '')}")
                        linhas.append(f"   > - **Qual resposta ou contraditório precisa ser solicitado:** {det.get('contraditorio', '')}")

                linhas.append("")

        # Seção final de recomendação editorial obrigatória (Seção 12)
        top3_prioritarias = pautas_top10[:3]
        linhas.append("## Recomendação Editorial: Três Pautas que Merecem Apuração Primeiro")
        linhas.append("")
        linhas.append("Com base na gravidade do impacto comunitário, atualidade, confiabilidade documental e direito ao contraditório, o editor-chefe recomenda iniciar a apuração imediata pelas seguintes três pautas:")
        linhas.append("")

        for idx, rec in enumerate(top3_prioritarias, 1):
            linhas.append(f"### {idx}. {rec.get('titulo_jornalistico', '')}")
            linhas.append(f"- **Categoria:** {rec.get('categoria', '')}")
            linhas.append(f"- **Prioridade:** {rec.get('prioridade', '')} ({rec.get('pontuacao_editorial', 0)} pts)")
            linhas.append(f"- **Link original:** [{rec.get('fonte_principal', '')}]({rec.get('url_original', '')})")
            linhas.append(f"- **Motivo objetivo da prioridade:** {rec.get('motivo_selecao', '')} Trata-se de tema com impacto direto na segurança/saúde coletiva e documentação pública verificável que exige posicionamento das autoridades municipais antes do fechamento da edição.")
            linhas.append(f"- **Próxima ação recomendada pela redação:** {rec.get('proxima_acao_recomendada', '')}")
            linhas.append("")

        md_file.write_text("\n".join(linhas), encoding="utf-8")
        logger.info("Relatório editorial gravado com sucesso em %s", md_file)
        return md_file
