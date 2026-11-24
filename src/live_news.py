"""
Coletor de Notícias Recentes ao Vivo (Google News RSS + G1 Maranhão RSS + Capas og:image).
Opera 100% com a biblioteca padrão Python 3, respeitando a regra de zero alucinação:
todos os títulos, datas, links e capas são extraídos estritamente dos feeds e páginas reais.
"""
from __future__ import annotations

import gzip
import html
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from src.content_extractor import ContentExtractor

logger = logging.getLogger("radar_imperatriz.live_news")

GOOGLE_NEWS_RSS_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
)

PREFEITURA_BASE_URL = "https://imperatriz.ma.gov.br"
PREFEITURA_NOTICIAS_URL = "https://imperatriz.ma.gov.br/noticias/"

DEFAULT_RSS_QUERIES: List[Tuple[str, str]] = [
    ("Imperatriz MA notícias recentes", "Imperatriz+MA+when:7d"),
    (
        "Imperatriz prefeitura, obras, saúde e educação",
        "%22Imperatriz%22+Maranhao+(prefeitura+OR+obras+OR+saude+OR+educacao)+when:7d",
    ),
    (
        "Imperatriz segurança, polícia, acidentes e bombeiros",
        "%22Imperatriz%22+Maranhao+(policia+OR+acidente+OR+operacao+OR+bombeiros)+when:7d",
    ),
    (
        "Imperatriz licitações, TCE, MPMA, denúncias e justiça",
        "%22Imperatriz%22+Maranhao+(licitacao+OR+MPMA+OR+TCE+OR+denuncia+OR+justica)+when:14d",
    ),
    (
        "Imperatriz chuvas, alagamento, Rio Tocantins, energia e água",
        "%22Imperatriz%22+Maranhao+(chuva+OR+alagamento+OR+%22Rio+Tocantins%22+OR+energia+OR+agua)+when:7d",
    ),
]

G1_MA_RSS_URL = "https://g1.globo.com/rss/g1/ma/maranhao/"

OFFICIAL_DOMAINS = (
    "imperatriz.ma.gov.br",
    "tcema.tc.br",
    "mpma.mp.br",
    "tjma.jus.br",
    "policiacivil.ma.gov.br",
    "ma.gov.br",
    "gov.br",
)


class LiveNewsFetcher:
    """Busca notícias realmente recentes de Imperatriz (MA) via RSS aberto e portais locais."""

    def __init__(
        self,
        timeout: int = 8,
        max_cover_fetches: int = 12,
        user_agent: Optional[str] = None,
    ) -> None:
        self.timeout = timeout
        self.max_cover_fetches = max_cover_fetches
        self.user_agent = user_agent or (
            "Mozilla/5.0 (compatible; RadarDePautasImperatriz/2.0; "
            "+https://imperatriz.ma.gov.br)"
        )

    def _http_get_text(self, url: str, max_bytes: int = 2_500_000, timeout: Optional[int] = None) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            raw = resp.read(max_bytes)
            ce = (resp.headers.get("Content-Encoding") or "").lower()
            if raw[:2] == b"\x1f\x8b" or "gzip" in ce:
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace").lstrip("\ufeff")

    @staticmethod
    def _upgrade_known_https_cover(url: str) -> str:
        """Promove URLs conhecidas de mídia municipal HTTP para HTTPS evitando mixed-content."""
        u = (url or "").strip()
        if not u:
            return ""
        return re.sub(
            r"^http://(media\.imperatriz\.ma\.gov\.br/)",
            r"https://\1",
            u,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _parse_pub_date(raw_date: str) -> str:
        if not raw_date:
            return ""
        raw_clean = raw_date.strip()
        # Formato ISO direto (Atom/RSS)
        iso_m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw_clean)
        if iso_m:
            return iso_m.group(1)
        try:
            dt = parsedate_to_datetime(raw_clean)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return ""

    @classmethod
    def _extract_image_from_rss_item(cls, item_el: ET.Element, raw_description: str) -> str:
        # 1. Tags <media:content>, <media:thumbnail>, <enclosure>
        for el in item_el.iter():
            tag_local = el.tag.split("}")[-1].lower()
            if tag_local in ("content", "thumbnail") and el.attrib.get("url"):
                url_val = el.attrib["url"].strip()
                if url_val.startswith(("http://", "https://")):
                    return cls._upgrade_known_https_cover(url_val)
            if tag_local == "enclosure":
                enc_type = (el.attrib.get("type") or "").lower()
                enc_url = (el.attrib.get("url") or "").strip()
                if enc_url.startswith(("http://", "https://")) and (
                    not enc_type or "image" in enc_type
                ):
                    return cls._upgrade_known_https_cover(enc_url)

        # 2. Tag <img src="..."> dentro de description ou content:encoded
        if raw_description:
            m = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', raw_description, re.IGNORECASE)
            if m:
                candidate = html.unescape(m.group(1)).strip()
                if "news.google.com" not in candidate.lower():
                    return cls._upgrade_known_https_cover(candidate)
        return ""

    @classmethod
    def _is_about_imperatriz(cls, title: str, desc: str, source_name: str) -> bool:
        """
        Verifica relevância geográfica estrita para Imperatriz (MA), removendo o nome
        do veículo para evitar vazamentos como notícias de São Luís em 'Jornal Imperatriz'.
        """
        t_low = (title or "").lower()
        d_clean = (desc or "").lower()
        publisher_tokens = [
            (source_name or "").lower().strip(),
            "jornal imperatriz",
            "imperatriz online",
            "imperatriz notícias",
            "imperatriz noticias",
            "imparcial",
        ]
        for tok in publisher_tokens:
            if tok:
                d_clean = d_clean.replace(tok, " ")
        d_clean = re.sub(r"\s+", " ", d_clean).strip()

        external_cities = (
            "são luís",
            "sao luis",
            "italuís",
            "italuis",
            "br-135",
            "paço do lumiar",
            "paco do lumiar",
            "são josé de ribamar",
            "sao jose de ribamar",
            "raposa",
            "caxias",
            "timon",
            "codó",
            "codo",
            "balsas",
            "bacabal",
            "santa inês",
            "santa ines",
            "pinheiro",
            "açailândia",
            "acailandia",
            "chapadinha",
            "pedreiras",
            "presidente dutra",
            "barra do corda",
            "grajaú",
            "grajau",
            "alcântara",
            "alcantara",
            "barreirinhas",
            "lençóis maranhenses",
        )
        if any(city in t_low for city in external_cities) and "imperatriz" not in t_low:
            return False

        local_markers = (
            "imperatriz",
            "região tocantina",
            "regiao tocantina",
            "riacho bacuri",
            "riacho capivara",
            "beira-rio",
            "socorrão",
            "socorrao",
            "nova imperatriz",
            "calçadão",
            "calcadao",
            "bacuri",
            "santa rita",
            "vilinha",
            "parque alvorada",
            "parque anhanguera",
            "vila lobão",
            "vila lobao",
            "itamar guará",
            "itamar guara",
            "salimp",
            "expoimp",
            "uemasul",
            "prefeitura de imperatriz",
            "câmara municipal de imperatriz",
        )
        return any(m in t_low for m in local_markers) or any(m in d_clean for m in local_markers)

    @staticmethod
    def _clean_google_news_title(title: str, source_name: str) -> str:
        t = (title or "").strip()
        if source_name and t.endswith(f" - {source_name}"):
            return t[: -len(f" - {source_name}")].strip()
        # Fallback genérico caso o sufixo seja variação abreviada do veículo
        if " - " in t and source_name:
            parts = t.rsplit(" - ", 1)
            if len(parts[1]) <= 40 and parts[1].lower() in source_name.lower():
                return parts[0].strip()
        return t

    @staticmethod
    def _classify_item(titulo: str, resumo: str, data_pub: str, fonte: str, url: str) -> Dict[str, Any]:
        combined = f"{titulo} {resumo}".lower()

        # Idade em dias para compor pontuação realista
        age_days = 7
        if data_pub:
            try:
                pub_dt = datetime.strptime(data_pub[:10], "%Y-%m-%d").date()
                age_days = max(0, (datetime.now().date() - pub_dt).days)
            except ValueError:
                pass

        if age_days <= 2:
            atualidade = 20
        elif age_days <= 7:
            atualidade = 16
        elif age_days <= 15:
            atualidade = 11
        else:
            atualidade = 6

        # Categoria editorial e temática
        emerg_kw = (
            "alerta amarelo",
            "alerta laranja",
            "alerta vermelho",
            "emergência",
            "calamidade",
            "enchente",
            "alagamento grave",
            "interdição total",
            "desabamento",
        )
        urg_kw = (
            "polícia",
            "prisão",
            "preso",
            "operação",
            "acidente",
            "incêndio",
            "resgate",
            "bombeiros",
            "homicídio",
            "bloqueio",
            "br-010",
            "denarc",
            "gaeco",
            "tiroteio",
            "grave",
            "alerta",
            "chuva",
            "alagamento",
        )
        den_kw = (
            "denúncia",
            "mpma",
            "ministério público",
            "tce",
            "tribunal de contas",
            "auditoria",
            "cautelar",
            "suspende",
            "irregular",
            "fraude",
            "desvio",
            "improbidade",
            "paralisada",
            "inquerito",
            "inquérito",
            "condena",
            "investig",
            "reclamação",
            "falta de",
        )
        cult_kw = (
            "cultura",
            "salimp",
            "literatura",
            "emprego",
            "vagas",
            "economia",
            "indústria",
            "comércio",
            "aeroporto",
            "festival",
            "show",
            "expoimp",
            "turismo",
        )

        emergencia_confirmada = any(k in combined for k in emerg_kw) and age_days <= 3

        if emergencia_confirmada or any(k in combined for k in urg_kw):
            categoria = "Pautas urgentes"
            tema = "Segurança, Emergências e Ocorrências Locais"
            impacto, interesse, formato = 22, 14, "Nota urgente" if emergencia_confirmada else "Matéria jornalística"
        elif any(k in combined for k in den_kw):
            categoria = "Pautas de denúncia e apuração"
            tema = "Fiscalização, Controle Público e Denúncias"
            impacto, interesse, formato = 22, 15, "Reportagem investigativa"
        elif any(k in combined for k in cult_kw):
            categoria = "Pautas de cultura, economia e cidade"
            tema = "Economia, Emprego, Cultura e Eventos"
            impacto, interesse, formato = 18, 12, "Matéria jornalística"
        else:
            categoria = "Pautas de serviço público"
            tema = "Serviços Públicos, Saúde, Educação e Infraestrutura"
            impacto, interesse, formato = 20, 14, "Serviço público"

        is_official = any(dom in url.lower() for dom in OFFICIAL_DOMAINS)
        is_major_press = any(dom in url.lower() for dom in ("g1.globo.com", "imirante.com"))

        if categoria == "Pautas de denúncia e apuração":
            # Seção 14: denúncias nunca recebem confirmação plena sem autos/decisão verificada diretamente
            nivel_verificacao = "Parcialmente confirmado"
            qualidade = 16 if (is_official or is_major_press) else 14
        elif (is_official or is_major_press) and len(resumo) >= 100:
            nivel_verificacao = "Confirmado"
            qualidade = 18
        else:
            nivel_verificacao = "Parcialmente confirmado"
            qualidade = 14

        return {
            "categoria_sugerida": categoria,
            "tema_especifico": tema,
            "emergencia_confirmada": emergencia_confirmada,
            "formato_sugerido": formato,
            "nivel_verificacao": nivel_verificacao,
            "pontuacao_base": {
                "impacto_populacao": impacto,
                "atualidade_novidade": atualidade,
                "interesse_publico": interesse,
                "qualidade_evidencias": qualidade,
                "potencial_desenvolvimento": 8,
                "exclusividade_pouco_explorada": 7,
            },
        }

    @classmethod
    def _build_news_item(
        cls,
        *,
        url: str,
        titulo: str,
        desc: str,
        data_pub: str,
        source_name: str,
        source_url: str = "",
        veiculo_instituicao: str = "",
        autor: str = "",
        consulta_origem: str = "Busca ao vivo RSS",
        imagem_capa: str = "",
    ) -> Dict[str, Any]:
        """Constrói o dicionário padronizado de descoberta jornalística para o pipeline."""
        clean_desc = (desc or "").strip() or f"{titulo}. Matéria veiculada por {source_name}."
        docs = ContentExtractor.extract_document_identifiers(f"{titulo} {clean_desc}")
        locais = ContentExtractor.extract_locations(f"{titulo} {clean_desc}")
        locais_str = "; ".join(locais) if locais else "Imperatriz (MA)"
        cls_meta = cls._classify_item(titulo, clean_desc, data_pub, source_name, url)

        resumo_3_linhas = clean_desc[:280].strip()
        if len(clean_desc) > 280:
            resumo_3_linhas += "..."

        return {
            "url": url,
            "fonte_nome": source_name,
            "veiculo_instituicao": veiculo_instituicao
            or (f"{source_name}" + (f" ({source_url})" if source_url else "")),
            "autor": autor or f"Redação {source_name}",
            "titulo": titulo,
            "data_publicacao": data_pub,
            "data_acontecimento": data_pub,
            "consulta_origem": consulta_origem,
            "tema_especifico": cls_meta["tema_especifico"],
            "categoria_sugerida": cls_meta["categoria_sugerida"],
            "emergencia_confirmada": cls_meta["emergencia_confirmada"],
            "conteudo_integral": clean_desc,
            "resumo_3_linhas": resumo_3_linhas,
            "fatos_confirmados": [
                f"Registro publicado em {data_pub or 'data recente'} por {source_name}: {titulo}."
            ],
            "declaracoes_oficiais": [],
            "documentos_citados": docs,
            "bairros_locais_instituicoes": locais_str,
            "publico_afetado": "População de Imperatriz (MA) e Região Tocantina",
            "angulo_jornalistico": f"Apuração local sobre '{titulo}' com checagem documental junto aos órgãos de Imperatriz.",
            "formato_sugerido": cls_meta["formato_sugerido"],
            "nivel_verificacao": cls_meta["nivel_verificacao"],
            "informacoes_pendentes": (
                "Ler reportagem integral na fonte original e checar eventuais documentos oficiais ou nota das autoridades envolvidas."
                if cls_meta["nivel_verificacao"] != "Confirmado"
                else ""
            ),
            "proxima_acao_recomendada": f"Acessar matéria original em {source_name} e confirmar desdobramentos em Imperatriz.",
            "pontuacao_base": cls_meta["pontuacao_base"],
            "imagem_capa": cls._upgrade_known_https_cover(imagem_capa),
        }

    @classmethod
    def parse_rss_xml(
        cls,
        xml_text: str,
        default_source_name: str = "Portal de Notícias",
        consulta_origem: str = "Busca ao vivo RSS",
        require_imperatriz_filter: bool = True,
    ) -> List[Dict[str, Any]]:
        """Faz parsing seguro de XML RSS (Google News / G1 / Atom-RSS) para itens estruturados do Radar."""
        if not xml_text or "<" not in xml_text:
            return []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("XML RSS inválido (%s): %s", consulta_origem, e)
            return []

        items: List[Dict[str, Any]] = []
        for item_el in root.findall(".//item"):
            raw_title = (item_el.findtext("title") or "").strip()
            link = (item_el.findtext("link") or "").strip()
            raw_desc = (item_el.findtext("description") or "").strip()
            pub_raw = (item_el.findtext("pubDate") or "").strip()

            source_el = item_el.find("source")
            source_name = (
                (source_el.text or "").strip() if source_el is not None and source_el.text else ""
            ) or default_source_name
            source_url = (
                (source_el.attrib.get("url") or "").strip() if source_el is not None else ""
            )

            if not raw_title or not link:
                continue

            clean_title = ContentExtractor.sanitize_text(
                cls._clean_google_news_title(raw_title, source_name)
            )
            raw_desc_sanitized = ContentExtractor.sanitize_text(raw_desc)
            if require_imperatriz_filter and not cls._is_about_imperatriz(
                clean_title, raw_desc_sanitized, source_name
            ):
                continue

            clean_desc = (
                raw_desc_sanitized
                if raw_desc_sanitized and raw_desc_sanitized.lower() != clean_title.lower()
                else f"{clean_title}. Matéria veiculada por {source_name}."
            )
            data_pub = cls._parse_pub_date(pub_raw)
            img_url = cls._extract_image_from_rss_item(item_el, raw_desc)

            items.append(
                cls._build_news_item(
                    url=link,
                    titulo=clean_title,
                    desc=clean_desc,
                    data_pub=data_pub,
                    source_name=source_name,
                    source_url=source_url,
                    consulta_origem=consulta_origem,
                    imagem_capa=img_url,
                )
            )
        return items

    @classmethod
    def parse_prefeitura_html(
        cls, html_text: str, max_items: int = 12
    ) -> List[Dict[str, Any]]:
        """Extrai notícias recentes oficiais do HTML de `imperatriz.ma.gov.br/noticias/`."""
        if not html_text or "post-list__list__item" not in html_text:
            return []

        items: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        article_blocks = re.findall(
            r'<article[^>]*class=["\'][^"\']*post-list__list__item[^"\']*["\'][^>]*>(.*?)</article>',
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for block in article_blocks:
            href_m = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', block, flags=re.IGNORECASE)
            title_m = re.search(
                r'<h4[^>]*post-list__list__item__content__title[^>]*>(.*?)</h4>',
                block,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not href_m or not title_m:
                continue

            full_url = urllib.parse.urljoin(PREFEITURA_BASE_URL, html.unescape(href_m.group(1)).strip())
            if not full_url.startswith(("http://", "https://")) or full_url.lower() in seen_urls:
                continue
            seen_urls.add(full_url.lower())

            clean_title = ContentExtractor.sanitize_text(title_m.group(1))
            if not clean_title:
                continue

            date_m = re.search(r'(\d{2})/(\d{2})/(\d{4})', block)
            data_pub = f"{date_m.group(3)}-{date_m.group(2)}-{date_m.group(1)}" if date_m else ""
            data_br = date_m.group(0) if date_m else "data recente"

            img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', block, flags=re.IGNORECASE)
            img_url = ""
            if img_m:
                raw_img = urllib.parse.urljoin(PREFEITURA_BASE_URL, html.unescape(img_m.group(1)).strip())
                img_url = cls._upgrade_known_https_cover(raw_img)

            desc = f"{clean_title}. Publicação oficial da Prefeitura Municipal de Imperatriz ({data_br})."
            items.append(
                cls._build_news_item(
                    url=full_url,
                    titulo=clean_title,
                    desc=desc,
                    data_pub=data_pub,
                    source_name="Prefeitura de Imperatriz",
                    veiculo_instituicao="Portal Oficial da Prefeitura de Imperatriz (imperatriz.ma.gov.br)",
                    autor="ASCOM / Prefeitura de Imperatriz",
                    consulta_origem="Portal Oficial Prefeitura de Imperatriz",
                    imagem_capa=img_url,
                )
            )
            if len(items) >= max_items:
                break
        return items

    @staticmethod
    def _extract_meta_description(raw_html: str) -> str:
        """Extrai meta `og:description`, `twitter:description` ou `description` de página HTML."""
        if not raw_html or "<meta" not in raw_html.lower():
            return ""
        for key_pattern in (
            r'(?:property|name)=["\']og:description["\']',
            r'(?:property|name)=["\']twitter:description["\']',
            r'name=["\']description["\']',
        ):
            m = re.search(
                rf'<meta[^>]+{key_pattern}[^>]+content=["\']([^"\']+)["\']',
                raw_html,
                flags=re.IGNORECASE,
            ) or re.search(
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{key_pattern}',
                raw_html,
                flags=re.IGNORECASE,
            )
            if m:
                txt = ContentExtractor.sanitize_text(m.group(1))
                if len(txt) >= 25:
                    return txt
        return ""

    def fetch_prefeitura_news(
        self, max_items: int = 12, enrich_top: int = 8
    ) -> List[Dict[str, Any]]:
        """Coleta notícias recentes da Prefeitura de Imperatriz e enriquece capas og:image HTTPS."""
        html_list = self._http_get_text(PREFEITURA_NOTICIAS_URL, max_bytes=400_000, timeout=10)
        items = self.parse_prefeitura_html(html_list, max_items=max_items)
        for it in items[: max(0, enrich_top)]:
            try:
                art_html = self._http_get_text(it["url"], max_bytes=160_000, timeout=5)
                og_cover = ContentExtractor.extract_cover_image(art_html, base_url=it["url"])
                if og_cover:
                    it["imagem_capa"] = self._upgrade_known_https_cover(og_cover)
                meta_desc = self._extract_meta_description(art_html)
                if meta_desc:
                    full_desc = f"{meta_desc} (Fonte oficial: Prefeitura Municipal de Imperatriz)."
                    rebuilt = self._build_news_item(
                        url=it["url"],
                        titulo=it["titulo"],
                        desc=full_desc,
                        data_pub=it["data_publicacao"],
                        source_name=it["fonte_nome"],
                        veiculo_instituicao=it["veiculo_instituicao"],
                        autor=it["autor"],
                        consulta_origem=it["consulta_origem"],
                        imagem_capa=it.get("imagem_capa", ""),
                    )
                    it.update(rebuilt)
            except Exception:
                continue
        return items

    def fetch_cover_for_url(self, url: str) -> str:
        """Tenta extrair meta `og:image`/`twitter:image` da página original com timeout curto."""
        if not url or not url.startswith(("http://", "https://")):
            return ""
        # Links do Google News RSS redirecionam via JS em boa parte dos casos
        if "news.google.com/" in url.lower():
            return ""
        try:
            html_chunk = self._http_get_text(url, max_bytes=160_000, timeout=5)
            capa = ContentExtractor.extract_cover_image(html_chunk, base_url=url)
            return self._upgrade_known_https_cover(capa)
        except Exception:
            return ""

    def fetch_recent_news(
        self,
        consultas_adicionais: Optional[List[str]] = None,
        max_items: int = 35,
    ) -> Dict[str, Any]:
        """Executa busca ao vivo no G1 MA RSS, Portal Oficial da Prefeitura e Google News RSS."""
        coletados_por_url: Dict[str, Dict[str, Any]] = {}
        erros: List[str] = []

        # 1. G1 Maranhão RSS (fornece links diretos do g1.globo.com e mídia embutida)
        try:
            g1_xml = self._http_get_text(G1_MA_RSS_URL, max_bytes=2_500_000)
            g1_items = self.parse_rss_xml(
                g1_xml,
                default_source_name="G1 Maranhão",
                consulta_origem="G1 Maranhão RSS",
                require_imperatriz_filter=True,
            )
            for it in g1_items:
                coletados_por_url[it["url"].lower()] = it
        except Exception as e:
            erros.append(f"G1 MA RSS: {e}")

        # 2. Portal Oficial de Notícias da Prefeitura de Imperatriz (nov/2026 com capas og:image)
        try:
            pref_items = self.fetch_prefeitura_news(max_items=12, enrich_top=8)
            for it in pref_items:
                key = it["url"].lower()
                if key not in coletados_por_url:
                    coletados_por_url[key] = it
        except Exception as e:
            erros.append(f"Prefeitura ITZ Notícias: {e}")

        # 3. Google News RSS (consultas recentes de Imperatriz)
        queries = list(DEFAULT_RSS_QUERIES)
        if consultas_adicionais:
            for extra in consultas_adicionais[:3]:
                termo = str(extra).strip()
                if termo:
                    encoded = f"{urllib.parse.quote_plus(termo)}+Imperatriz+when:14d"
                    queries.append((f"Termo monitorado: {termo}", encoded))

        for label, q_encoded in queries:
            rss_url = GOOGLE_NEWS_RSS_TEMPLATE.format(query=q_encoded)
            try:
                gn_xml = self._http_get_text(rss_url, max_bytes=2_500_000)
                gn_items = self.parse_rss_xml(
                    gn_xml,
                    default_source_name="Imprensa Regional",
                    consulta_origem=label,
                    require_imperatriz_filter=True,
                )
                for it in gn_items:
                    key = it["url"].lower()
                    if key not in coletados_por_url:
                        coletados_por_url[key] = it
            except Exception as e:
                erros.append(f"Google News RSS ({label}): {e}")

        # Ordena dos mais recentes para os mais antigos (priorizando capas e fontes diretas no empate)
        itens_ordenados = sorted(
            coletados_por_url.values(),
            key=lambda x: (
                x.get("data_publicacao") or "0000-00-00",
                bool(x.get("imagem_capa")),
                "g1.globo.com" in x.get("url", "").lower() or "imperatriz.ma.gov.br" in x.get("url", "").lower(),
            ),
            reverse=True,
        )[:max_items]

        # 4. Tenta enriquecer imagem_capa para URLs diretas sem capa
        fetches_feitos = 0
        for it in itens_ordenados:
            if fetches_feitos >= self.max_cover_fetches:
                break
            if not it.get("imagem_capa") and "news.google.com/" not in it["url"].lower():
                fetches_feitos += 1
                capa = self.fetch_cover_for_url(it["url"])
                if capa:
                    it["imagem_capa"] = capa

        return {
            "itens": itens_ordenados,
            "total": len(itens_ordenados),
            "erros": erros,
        }
