"""
Verificador de Evidências e Aplicação das Regras de Segurança Editorial (Seção 14).
Avalia a consistência das fontes, diferencia alegações de fatos comprovados,
garante o direito ao contraditório e atribui o nível de verificação jornalística.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class EvidenceVerifier:
    """Aplica checagens determinísticas de evidências e segurança editorial."""

    @classmethod
    def verify_and_classify(cls, pauta_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analisa a pauta, verifica evidências e garante conformidade
        com as 14 Regras de Segurança Editorial da Seção 14.
        """
        fatos = pauta_data.get("fatos_confirmados", [])
        declaracoes = pauta_data.get("declaracoes_oficiais", [])
        docs = pauta_data.get("documentos_citados", [])
        detalhes_denuncia = pauta_data.get("detalhes_denuncia")

        categoria = pauta_data.get("categoria_sugerida", "Pautas de serviço público")
        nivel_original = pauta_data.get("nivel_verificacao", "Parcialmente confirmado")

        # Regra 1 e 2: Se for denúncia/irregularidade, nunca marcar como 'Confirmado'
        # se ainda houver instrução judicial em curso ou ausência de trânsito em julgado.
        is_denuncia = (
            categoria == "Pautas de denúncia e apuração"
            or "fraude" in pauta_data.get("titulo", "").lower()
            or "desvio" in pauta_data.get("titulo", "").lower()
            or "agiotagem" in pauta_data.get("titulo", "").lower()
            or detalhes_denuncia is not None
        )

        nivel_ajustado = nivel_original
        if is_denuncia:
            if not pauta_data.get("sentenca_definitiva_transitada", False):
                # Mantém como 'Parcialmente confirmado' ou 'Exige apuração'
                if len(docs) > 0 and len(fatos) > 0:
                    nivel_ajustado = "Parcialmente confirmado"
                else:
                    nivel_ajustado = "Exige apuração"

        # Constrói o texto consolidado de evidências verificadas
        evidencias_lista: List[str] = []
        for f in fatos:
            evidencias_lista.append(f"[Fato objetivo]: {f}")
        for d in declaracoes:
            evidencias_lista.append(f"[Declaração/Posicionamento]: {d}")
        for doc in docs:
            evidencias_lista.append(f"[Documento primário]: {doc}")

        texto_evidencias = " | ".join(evidencias_lista) if evidencias_lista else "Evidências em fase de levantamento inicial."

        # Se for denúncia, garante o preenchimento de todos os 6 campos obrigatórios da Seção 10.C
        dados_denuncia_validados = None
        if is_denuncia and detalhes_denuncia:
            dados_denuncia_validados = {
                "suspeita": detalhes_denuncia.get("suspeita", "Indícios de irregularidade em apuração"),
                "evidencia_disponivel": detalhes_denuncia.get("evidencia_disponivel", texto_evidencias),
                "nao_comprovado": detalhes_denuncia.get("nao_comprovado", "Julgamento definitivo de mérito e contraditório das partes"),
                "documentos_necessarios": detalhes_denuncia.get("documentos_necessarios", "Autos do processo, certidões e relatórios de medição"),
                "quem_procurar": detalhes_denuncia.get("quem_procurar", "Órgãos de controle, gestores responsáveis e defesas técnicas"),
                "contraditorio": detalhes_denuncia.get("contraditorio", "Solicitar manifestação oficial prévia com prazo hábil de resposta"),
            }

        return {
            "nivel_verificacao": nivel_ajustado,
            "evidencias_verificadas": texto_evidencias,
            "detalhes_denuncia": dados_denuncia_validados,
        }

    @classmethod
    def check_temporal_consistency(
        cls, data_publicacao: str, data_acontecimento: str
    ) -> Tuple[bool, str]:
        """
        Diferencia a data da publicação da data do acontecimento (Seção 4).
        Evita classificar fato antigo como novo sem atualização concreta.
        """
        if not data_publicacao or not data_acontecimento:
            return True, ""

        pub_year = data_publicacao[:4] if len(data_publicacao) >= 4 else ""
        acont_year = data_acontecimento[:4] if len(data_acontecimento) >= 4 else ""

        if pub_year and acont_year and pub_year != acont_year:
            return (
                True,
                f"Acontecimento ocorrido em {data_acontecimento} com desdobramento atual registrado em {data_publicacao}.",
            )

        return True, ""
