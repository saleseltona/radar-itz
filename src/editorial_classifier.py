"""
Classificador Editorial e Sistema de Pontuação (Seções 7 e 8).
Filtra conteúdos descartáveis (publicidade, boatos, outros municípios) e calcula
o índice editorial de 0 a 100 pontos com pesos estritos e justificativa explícita.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from src.config import PESOS_PONTUACAO

logger = logging.getLogger("radar_imperatriz.classifier")


class EditorialClassifier:
    """Aplica os critérios de seleção e o algoritmo de pontuação de 0 a 100."""

    @classmethod
    def evaluate_exclusion(cls, item: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Aplica os critérios de descarte da Seção 7:
        Retorna (True, motivo) se o item deve ser descartado, ou (False, '') se for válido.
        """
        # 1. Publicidade sem interesse jornalístico
        if item.get("is_publicidade", False):
            return True, "Descarte editorial: Informe publicitário / comercial sem interesse jornalístico público."

        # 2. Rumores ou boatos sem evidências
        if item.get("is_rumor_sem_evidencia", False):
            return True, "Descarte editorial: Boato em circulação sem evidência ou confirmação documental mínima."

        # 3. Notícias restritas a outros municípios sem vínculo concreto com Imperatriz
        ext_mun = item.get("municipio_exclusivo_externo")
        if ext_mun and "imperatriz" not in item.get("titulo", "").lower() and "imperatriz" not in item.get("conteudo_integral", "").lower():
            return True, f"Descarte editorial: Fato exclusivo do município de {ext_mun} sem relação concreta com Imperatriz."

        # 4. Notícia antiga republicada sem nenhum desdobramento novo
        data_acont = item.get("data_acontecimento", "")
        data_pub = item.get("data_publicacao", "")
        if data_acont and data_pub:
            acont_year = int(data_acont[:4]) if data_acont[:4].isdigit() else 2026
            pub_year = int(data_pub[:4]) if data_pub[:4].isdigit() else 2026
            if pub_year - acont_year > 1 and not item.get("explicacao_desdobramento"):
                return True, "Descarte editorial: Notícia de fato antigo republicada sem desdobramento concreto recente."

        # 5. Conteúdo vazio ou sem fonte
        if not item.get("conteudo_integral") and not item.get("titulo"):
            return True, "Descarte editorial: Conteúdo sem texto e sem fonte acessível."

        return False, ""

    @classmethod
    def calculate_score(cls, item: Dict[str, Any]) -> Tuple[int, str, str, bool]:
        """
        Calcula a pontuação de 0 a 100 de acordo com os pesos da Seção 8:
        - Impacto na população: 25
        - Atualidade e novidade: 20
        - Interesse público: 15
        - Qualidade e confiabilidade das evidências: 20
        - Potencial de desenvolvimento jornalístico: 10
        - Exclusividade ou informação pouco explorada: 10

        Retorna (pontuacao_total, prioridade, justificativa_detalhada, emergencia_sinalizada).
        """
        base_scores = item.get("pontuacao_base", {})

        # Extração de notas parciais (garantindo o teto de cada peso)
        impacto = min(PESOS_PONTUACAO["impacto_populacao"], base_scores.get("impacto_populacao", 18))
        atualidade = min(PESOS_PONTUACAO["atualidade_novidade"], base_scores.get("atualidade_novidade", 16))
        interesse = min(PESOS_PONTUACAO["interesse_publico"], base_scores.get("interesse_publico", 13))
        qualidade = min(PESOS_PONTUACAO["qualidade_evidencias"], base_scores.get("qualidade_evidencias", 17))
        potencial = min(PESOS_PONTUACAO["potencial_desenvolvimento"], base_scores.get("potencial_desenvolvimento", 8))
        exclusividade = min(PESOS_PONTUACAO["exclusividade_pouco_explorada"], base_scores.get("exclusividade_pouco_explorada", 7))

        pontuacao_total = impacto + atualidade + interesse + qualidade + potencial + exclusividade

        # Classificação de prioridade
        if pontuacao_total >= 80:
            prioridade = "Alta"
        elif pontuacao_total >= 55:
            prioridade = "Média"
        else:
            prioridade = "Baixa"

        # Sinalização especial de emergência (Seção 8)
        emergencia = bool(item.get("emergencia_confirmada", False))
        if emergencia:
            prioridade = "Emergência"

        # Justificativa estruturada para transparência editorial
        justificativa = (
            f"Total: {pontuacao_total}/100 pts | "
            f"Impacto na População: {impacto}/{PESOS_PONTUACAO['impacto_populacao']} | "
            f"Atualidade/Novidade: {atualidade}/{PESOS_PONTUACAO['atualidade_novidade']} | "
            f"Interesse Público: {interesse}/{PESOS_PONTUACAO['interesse_publico']} | "
            f"Qualidade das Evidências: {qualidade}/{PESOS_PONTUACAO['qualidade_evidencias']} | "
            f"Potencial Editorial: {potencial}/{PESOS_PONTUACAO['potencial_desenvolvimento']} | "
            f"Exclusividade/Ângulo Próprio: {exclusividade}/{PESOS_PONTUACAO['exclusividade_pouco_explorada']}"
        )
        if emergencia:
            justificativa += " [ALERTA: Sinalização especial de emergência ativada]"

        return pontuacao_total, prioridade, justificativa, emergencia
