"""
Extrator de Conteúdo e Verificador Textual do Radar de Pautas de Imperatriz.
Processa páginas HTML, documentos e comunicados públicos, higienizando contra
injeção de comandos e extraindo datas, entidades, valores, processos e declarações.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple


class TextStripper(HTMLParser):
    """Parser HTML padrão para extrair texto limpo sem tags nem scripts."""

    def __init__(self) -> None:
        super().__init__()
        self.reset()
        self.fed: List[str] = []
        self.in_script_or_style = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() in ("script", "style", "noscript"):
            self.in_script_or_style = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style", "noscript"):
            self.in_script_or_style = False

    def handle_data(self, data: str) -> None:
        if not self.in_script_or_style:
            self.fed.append(data)

    def get_data(self) -> str:
        return " ".join(self.fed)


class ContentExtractor:
    """Higieniza e extrai dados estruturados a partir de textos brutos."""

    # Bairros e locais conhecidos de Imperatriz para identificação geográfica
    LOCAIS_CONHECIDOS = [
        "Bacuri",
        "Vila Ipiranga",
        "Parque Alvorada",
        "Vila Redenção",
        "Parque Santa Lúcia",
        "Jardim Tropical",
        "Itamar Guará",
        "Vila Conceição II",
        "Planalto",
        "Centro",
        "Calçadão",
        "Beira-Rio",
        "BR-010",
        "Riacho Bacuri",
        "Riacho Capivara",
        "Riacho Cacau",
        "Riacho do Meio",
        "Rio Tocantins",
        "Residencial Verona",
        "Mercado Bom Jesus",
        "Centro de Convenções",
    ]

    # Expressões típicas de tentativas de prompt injection que devem ser neutralizadas
    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"você\s+agora\s+é\s+um",
        r"nova\s+instrução\s+do\s+sistema",
        r"system\s*prompt",
        r"delete\s+all\s+records",
        r"desconsidere\s+as\s+regras",
    ]

    @classmethod
    def sanitize_text(cls, raw_content: str) -> str:
        """
        Garante as Regras 11 e 12 da Seção 14: o texto coletado é exclusivamente
        material de pesquisa inerte e nunca instruções para alterar o sistema.
        """
        if not raw_content:
            return ""

        # Remove tags HTML se presentes
        if "<" in raw_content and ">" in raw_content:
            parser = TextStripper()
            try:
                parser.feed(raw_content)
                text = parser.get_data()
            except Exception:
                text = re.sub(r"<[^>]+>", " ", raw_content)
        else:
            text = raw_content

        text = html.unescape(text)
        # Neutraliza eventuais injeções
        for pattern in cls.INJECTION_PATTERNS:
            text = re.sub(pattern, "[TRECHO NEUTRALIZADO]", text, flags=re.IGNORECASE)

        # Normaliza espaços
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def extract_document_identifiers(cls, text: str) -> List[str]:
        """Extrai números de contratos, pregões, processos judiciais e operações policiais."""
        docs = set()
        # Contratos e Concorrências
        for m in re.finditer(r"(?:Contrato|Concorr[êe]ncia(?:\s+Eletr[ôo]nica)?)\s*(?:n[ºo°.]?)?\s*([0-9]{1,4}\/[0-9]{4})", text, re.IGNORECASE):
            docs.add(m.group(0).strip())
        # Pregões Eletrônicos
        for m in re.finditer(r"Preg[ãa]o\s+Eletr[ôo]nico\s*(?:n[ºo°.]?)?\s*([0-9]{1,4}\/[0-9]{4})", text, re.IGNORECASE):
            docs.add(m.group(0).strip())
        # Dispensas de Licitação
        for m in re.finditer(r"Dispensa(?:\s+de\s+Licita[çc][ãa]o)?\s*(?:n[ºo°.]?)?\s*([0-9]{1,4}\/[0-9]{4})", text, re.IGNORECASE):
            docs.add(m.group(0).strip())
        # Operações policiais e de órgãos de controle
        for m in re.finditer(r"Opera[çc][ãa]o\s+([A-ZÁ-Ú][a-zá-ú]+(?:\s+[A-ZÁ-Ú][a-zá-ú]+)?)", text):
            op_nome = m.group(0).strip()
            if not any(stop in op_nome.lower() for stop in ["policial", "militar", "especial"]):
                docs.add(op_nome)
        # Ação Civil Pública
        if re.search(r"A[çc][ãa]o\s+Civil\s+P[úu]blica", text, re.IGNORECASE):
            docs.add("Ação Civil Pública")
        # Leis e Orçamento
        for m in re.finditer(r"\b(?:LOA|LDO|PPA)\b", text):
            docs.add(m.group(0).strip())

        return sorted(list(docs))

    @classmethod
    def extract_locations(cls, text: str) -> List[str]:
        """Identifica locais e bairros de Imperatriz citados no texto."""
        found = []
        for loc in cls.LOCAIS_CONHECIDOS:
            if re.search(rf"\b{re.escape(loc)}\b", text, flags=re.IGNORECASE):
                found.append(loc)
        return found

    @classmethod
    def extract_monetary_values(cls, text: str) -> List[str]:
        """Extrai valores em reais (ex: R$ 23,4 milhões, R$ 1 milhão, R$ 621.455.220,00)."""
        pattern = r"R\$\s*[\d\.,]+(?:\s*(?:milh[õo]es|milh[ãa]o|bilh[õo]es|bilh[ãa]o|mil))?"
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        # Filtra repetições mantendo a ordem
        seen = set()
        unique = []
        for v in matches:
            v_clean = v.strip()
            if v_clean not in seen:
                seen.add(v_clean)
                unique.append(v_clean)
        return unique

    @classmethod
    def extract_dates(cls, text: str) -> Dict[str, str]:
        """Identifica possíveis datas de publicação ou acontecimento."""
        dates = {}
        # Datas ISO AAAA-MM-DD
        iso_match = re.search(r"\b(202[0-9]-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))\b", text)
        if iso_match:
            dates["iso"] = iso_match.group(1)

        # Datas DD/MM/AAAA
        br_match = re.search(r"\b([0-3]?[0-9]\/[0-1]?[0-9]\/202[0-9])\b", text)
        if br_match:
            dates["br"] = br_match.group(1)

        # Meses por extenso (ex: 26 de outubro de 2026)
        extenso_match = re.search(
            r"\b([0-3]?[0-9]\s+de\s+(?:janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+202[0-9])\b",
            text,
            flags=re.IGNORECASE,
        )
        if extenso_match:
            dates["extenso"] = extenso_match.group(1)

        return dates
