"""
Persistência estruturada de evidências na tabela `evidencias`.

As evidências de cada pauta são montadas pelo EvidenceVerifier como um texto único
("[Tipo]: descrição | [Tipo]: descrição ..."), guardado em pautas.evidencias_verificadas.
Este módulo separa esse texto em registros individuais e os grava via add_evidencia().
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, List, Tuple

_SEGMENTO_RE = re.compile(r"^\[(?P<tipo>[^\]]+)\]:\s*(?P<descricao>.+)$", re.DOTALL)


def parse_evidencias(texto: str) -> List[Tuple[str, str]]:
    """Converte o texto consolidado em pares (tipo_evidencia, descricao), sem repetições."""
    resultado: List[Tuple[str, str]] = []
    vistos = set()
    for segmento in (texto or "").split(" | "):
        m = _SEGMENTO_RE.match(segmento.strip())
        if not m:
            continue
        par = (m.group("tipo").strip(), m.group("descricao").strip())
        if par[1] and par not in vistos:
            vistos.add(par)
            resultado.append(par)
    return resultado


# "Documento primário" é o rótulo antigo (pautas já gravadas); "Documento primário verificado"
# é o rótulo atual do EvidenceVerifier para documentos efetivamente consultados.
TIPOS_DOCUMENTO_PRIMARIO = ("Documento primário", "Documento primário verificado")


def referencia_evidencia(tipo: str, descricao: str, url_pauta: str) -> str:
    """
    Referência única por evidência (a chave no Supabase é id_pauta + tipo + referência).
    Documentos primários usam o próprio identificador; os demais, a URL da pauta com
    um sufixo estável derivado da descrição.
    """
    if tipo in TIPOS_DOCUMENTO_PRIMARIO:
        return descricao
    sufixo = hashlib.sha1(descricao.encode("utf-8")).hexdigest()[:10]
    return f"{url_pauta}#ev-{sufixo}"


def persist_evidencias(db: Any, id_pauta: str, texto: str, url_pauta: str) -> int:
    """Grava as evidências do texto consolidado. Retorna quantas foram enviadas ao banco."""
    pares = parse_evidencias(texto)
    for tipo, descricao in pares:
        db.add_evidencia(id_pauta, tipo, descricao, referencia_evidencia(tipo, descricao, url_pauta))
    return len(pares)
