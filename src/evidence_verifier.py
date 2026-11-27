"""
Verificador de Evidências e Aplicação das Regras de Segurança Editorial (Seção 14).
Avalia a consistência das fontes, diferencia alegações de fatos comprovados,
garante o direito ao contraditório e atribui o nível de verificação jornalística.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


NIVEL_CONFIRMADO = "Confirmado"
NIVEL_PARCIAL = "Parcialmente confirmado"
NIVEL_EXIGE_APURACAO = "Exige apuração"

# Mensagens das exigências que faltam para uma pauta poder ser marcada como "Confirmado".
FALTA_DOCUMENTO_PRIMARIO = (
    "consultar e registrar documento primário (ato oficial, diário oficial, autos, contrato ou dado bruto) "
    "com link ou referência verificável"
)
FALTA_CONTRADITORIO = "registrar o contraditório (resposta ou posicionamento das partes citadas)"
FALTA_DECISAO_DEFINITIVA = "denúncia só pode ser confirmada com decisão definitiva (trânsito em julgado) verificada"


def _as_list(value: Any) -> List[str]:
    """Normaliza listas ou textos separados por ';' / ' | ' em lista de strings não vazias."""
    if not value:
        return []
    if isinstance(value, str):
        partes = re.split(r";|\s\|\s", value)
    elif isinstance(value, (list, tuple, set)):
        partes = [str(v) for v in value if v is not None]
    else:
        partes = [str(value)]
    return [p.strip() for p in partes if p and p.strip()]


class EvidenceVerifier:
    """Aplica checagens determinísticas de evidências e segurança editorial.

    Relevância editorial (pontuação, fonte oficial, tamanho do texto) NÃO é confirmação.
    "Confirmado" exige, cumulativamente:
      1. ao menos um documento primário verificado (``documentos_primarios_verificados``);
      2. contraditório registrado (``contraditorio_registrado``);
      3. para denúncias, decisão definitiva verificada (``sentenca_definitiva_transitada``).
    Identificadores extraídos por regex (``documentos_citados``) e o simples registro de
    que um veículo publicou a notícia (``registros_publicacao``) não contam como prova.
    """

    @staticmethod
    def is_denuncia(pauta_data: Dict[str, Any]) -> bool:
        titulo = (pauta_data.get("titulo") or pauta_data.get("titulo_jornalistico") or "").lower()
        categoria = pauta_data.get("categoria_sugerida") or pauta_data.get("categoria") or ""
        return (
            categoria == "Pautas de denúncia e apuração"
            or "fraude" in titulo
            or "desvio" in titulo
            or "agiotagem" in titulo
            or pauta_data.get("detalhes_denuncia") is not None
        )

    @classmethod
    def requisitos_confirmacao(cls, pauta_data: Dict[str, Any]) -> List[str]:
        """Lista o que ainda falta para a pauta poder ser marcada como 'Confirmado' (vazia = pode)."""
        faltas: List[str] = []
        if not _as_list(pauta_data.get("documentos_primarios_verificados")):
            faltas.append(FALTA_DOCUMENTO_PRIMARIO)
        if not _as_list(pauta_data.get("contraditorio_registrado")):
            faltas.append(FALTA_CONTRADITORIO)
        if cls.is_denuncia(pauta_data) and not pauta_data.get("sentenca_definitiva_transitada", False):
            faltas.append(FALTA_DECISAO_DEFINITIVA)
        return faltas

    @classmethod
    def verify_and_classify(cls, pauta_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analisa a pauta, verifica evidências e garante conformidade
        com as 14 Regras de Segurança Editorial da Seção 14.
        """
        fatos = _as_list(pauta_data.get("fatos_confirmados"))
        registros = _as_list(pauta_data.get("registros_publicacao"))
        declaracoes = _as_list(pauta_data.get("declaracoes_oficiais"))
        docs_citados = _as_list(pauta_data.get("documentos_citados"))
        docs_primarios = _as_list(pauta_data.get("documentos_primarios_verificados"))
        contraditorio = _as_list(pauta_data.get("contraditorio_registrado"))
        detalhes_denuncia = pauta_data.get("detalhes_denuncia")

        nivel_original = pauta_data.get("nivel_verificacao") or NIVEL_PARCIAL
        is_denuncia = cls.is_denuncia(pauta_data)
        faltas = cls.requisitos_confirmacao(pauta_data)

        if not faltas:
            nivel_ajustado = NIVEL_CONFIRMADO
        elif is_denuncia:
            # Regras 1 e 2: denúncia sem decisão definitiva nunca é 'Confirmado'.
            tem_base = bool(docs_primarios or docs_citados) and bool(fatos or docs_primarios)
            nivel_ajustado = NIVEL_PARCIAL if tem_base else NIVEL_EXIGE_APURACAO
        elif nivel_original == NIVEL_CONFIRMADO:
            # Fonte oficial ou texto longo não bastam: rebaixa até haver documento + contraditório.
            nivel_ajustado = NIVEL_PARCIAL
        else:
            nivel_ajustado = nivel_original

        # Constrói o texto consolidado de evidências, separando prova de mero registro.
        evidencias_lista: List[str] = []
        for d in docs_primarios:
            evidencias_lista.append(f"[Documento primário verificado]: {d}")
        for f in fatos:
            evidencias_lista.append(f"[Fato objetivo]: {f}")
        for r in registros:
            evidencias_lista.append(f"[Registro de publicação]: {r}")
        for d in declaracoes:
            evidencias_lista.append(f"[Declaração/Posicionamento]: {d}")
        for c in contraditorio:
            evidencias_lista.append(f"[Contraditório]: {c}")
        for doc in docs_citados:
            if doc not in docs_primarios:
                evidencias_lista.append(f"[Documento citado, não consultado]: {doc}")

        texto_evidencias = " | ".join(evidencias_lista) if evidencias_lista else "Evidências em fase de levantamento inicial."

        pendencias_confirmacao = ""
        if nivel_ajustado != NIVEL_CONFIRMADO and faltas:
            pendencias_confirmacao = "Para confirmar: " + "; ".join(faltas) + "."

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
            "pendencias_confirmacao": pendencias_confirmacao,
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
