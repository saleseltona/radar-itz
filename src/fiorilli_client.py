"""
Cliente Oficial dos Dados Abertos JSON do Portal Transparência Fiorilli (Imperatriz - MA).
Opera 100% com a biblioteca padrão Python 3 (`urllib.request`, `json`) coletando:
  - Receitas (Orçamentária, União e Estado)
  - Despesas (por Órgão e por Fornecedor)
  - Contratos & Contratações Diretas (Dispensas e Inexigibilidades)
Recorte padrão de exercícios: 2025 e 2026.
"""
from __future__ import annotations

import logging
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.config import FIORILLI_BASE_URL, FIORILLI_DEFAULT_EMPRESA, FIORILLI_DEFAULT_YEARS
from src.pncp_analytics import classify_pncp_contrato

logger = logging.getLogger("radar_imperatriz.fiorilli")

TransportJsonFn = Callable[[str, int], bytes]

_DOC_PLACEHOLDER_MARKERS = ("CLIQUE", "DUAS VEZES", "CONSULTAR")


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def parse_fiorilli_number(raw: Any) -> float:
    """
    Converte strings monetárias do padrão Fiorilli PT-BR (incluindo zero e negativos,
    como deduções do FUNDEB `-98396231,85`) para float arredondado em 2 casas decimais.
    """
    if isinstance(raw, bool) or raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val != val or abs(val) >= 1e15:
            return 0.0
        val = round(val, 2)
        return 0.0 if val == 0 else val

    txt = str(raw).strip()
    if not txt:
        return 0.0
    cleaned = re.sub(r"[^\d,.\-]", "", txt)
    if not cleaned or cleaned in ("-", ".", ",", "-.", "-,"):
        return 0.0

    # Trata sinal negativo possivelmente mal posicionado
    neg = cleaned.startswith("-")
    unsigned = cleaned.lstrip("-")
    if not unsigned:
        return 0.0

    if "," in unsigned and "." in unsigned:
        if unsigned.rfind(",") > unsigned.rfind("."):
            unsigned = unsigned.replace(".", "").replace(",", ".")
        else:
            unsigned = unsigned.replace(",", "")
    elif "," in unsigned:
        unsigned = unsigned.replace(",", ".")
    elif unsigned.count(".") > 1:
        unsigned = unsigned.replace(".", "")

    try:
        val = float(unsigned)
    except ValueError:
        return 0.0
    if val != val or abs(val) >= 1e15:
        return 0.0
    if neg:
        val = -val
    val = round(val, 2)
    return 0.0 if val == 0 else val


def normalize_doc(raw: Any) -> str:
    """Normaliza CPF/CNPJ para dígitos apenas, descartando textos placeholder do Fiorilli."""
    txt = str(raw or "").strip()
    if not txt:
        return ""
    up = _strip_accents(txt).upper()
    if any(m in up for m in _DOC_PLACEHOLDER_MARKERS):
        return ""
    digits = re.sub(r"\D", "", txt)
    if len(digits) < 11:
        return ""
    if 12 <= len(digits) <= 13:
        return digits.zfill(14)
    return digits


def normalize_processo(raw: Any) -> str:
    """Normaliza número de processo administrativo (`02.04.00.0078/2025` -> `02040000782025`)."""
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits if len(digits) >= 6 else ""


def parse_fiorilli_date(raw: Any) -> str:
    """Converte datas Fiorilli (`DD/MM/YYYY` ou `DD/MM/YYYY HH:MM:SS`) para `YYYY-MM-DD`."""
    txt = str(raw or "").strip()
    if not txt:
        return ""
    date_part = txt.split(" ", 1)[0].strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", date_part)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", txt)
    if m_iso:
        return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
    return ""


def classify_contratacao_fiorilli(fund_legal: str, modalidade: str) -> Tuple[str, int]:
    """Identifica se o contrato Fiorilli decorre de Dispensa, Inexigibilidade ou Licitação."""
    joined = _strip_accents(f"{fund_legal or ''} {modalidade or ''}").upper()
    if "DISPENSA" in joined:
        return "Dispensa", 1
    if "INEXIG" in joined:
        return "Inexigibilidade", 1
    return "Licitação / Outros", 0


def _receita_prefix_code(codigo: str) -> str:
    digits = re.sub(r"\D", "", str(codigo or ""))
    stripped = digits.rstrip("0")
    return stripped or digits


def compute_receita_hierarchy(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Marca `eh_raiz = 1` exclusivamente nos nós-raiz contábeis de cada `(ano, tipo_fonte)`
    para impedir dupla contagem entre contas totalizadoras (ex.: `1000`, `2000`, `9000`)
    e suas subcontas analíticas (`1100`, `1700`, `9500`, etc.).
    """
    groups: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        ano = int(item.get("ano") or 0)
        tf = str(item.get("tipo_fonte") or "orcamentaria")
        item["_norm_code"] = _receita_prefix_code(str(item.get("codigo") or ""))
        groups.setdefault((ano, tf), []).append(item)

    output: List[Dict[str, Any]] = []
    for _, group_rows in groups.items():
        all_codes = {r["_norm_code"] for r in group_rows if r["_norm_code"]}
        seen_root_codes: set[str] = set()
        for r in group_rows:
            code = r.pop("_norm_code", "")
            if not code:
                r["eh_raiz"] = 0
                output.append(r)
                continue
            has_ancestor = any(
                other != code and len(other) < len(code) and code.startswith(other)
                for other in all_codes
            )
            if not has_ancestor and code not in seen_root_codes:
                r["eh_raiz"] = 1
                seen_root_codes.add(code)
            else:
                r["eh_raiz"] = 0
            output.append(r)
    return output


def _default_json_transport(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, */*",
            "User-Agent": "RadarImperatriz/1.0 (+https://imperatriz.ma.gov.br)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class FiorilliClient:
    """Conector JSON oficial para o Portal da Transparência Fiorilli de Imperatriz (MA)."""

    def __init__(
        self,
        db: Any,
        base_url: str = FIORILLI_BASE_URL,
        empresa: str = FIORILLI_DEFAULT_EMPRESA,
        timeout: int = 35,
        transport: Optional[TransportJsonFn] = None,
    ) -> None:
        self.db = db
        self.base_url = (base_url or FIORILLI_BASE_URL).strip().rstrip("/")
        self.empresa = (empresa or FIORILLI_DEFAULT_EMPRESA).strip() or "1"
        self.timeout = int(timeout)
        self.transport: TransportJsonFn = transport or _default_json_transport

    def _fetch_json(self, section: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        qs = urllib.parse.urlencode(params)
        url = f"{self.base_url}/VersaoJson/{section}/?{qs}"

        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                raw = self.transport(url, self.timeout)
                text = raw.decode("utf-8-sig", errors="replace").strip()
                if not text:
                    return []
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [r for r in parsed if isinstance(r, dict)]
                if isinstance(parsed, dict):
                    for key in ("data", "items", "registros", "Registros"):
                        val = parsed.get(key)
                        if isinstance(val, list):
                            return [r for r in val if isinstance(r, dict)]
                    return [parsed]
                return []
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_err = exc
                logger.warning(
                    "Tentativa %d falhou no endpoint Fiorilli (%s): %s",
                    attempt + 1,
                    params.get("Listagem", section),
                    exc,
                )
                if attempt == 0:
                    time.sleep(0.5)
        raise RuntimeError(
            f"Falha ao consultar Fiorilli ({params.get('Listagem', section)}): {last_err}"
        ) from last_err

    def _base_period_params(self, ano: int, listagem: str) -> Dict[str, Any]:
        return {
            "ConectarExercicio": str(ano),
            "Listagem": listagem,
            "DiaInicioPeriodo": "01",
            "MesInicialPeriodo": "01",
            "DiaFinalPeriodo": "31",
            "MesFinalPeriodo": "12",
            "Ano": str(ano),
            "Empresa": self.empresa,
            "MostraDadosConsolidado": "True",
        }

    def fetch_receitas(self, ano: int) -> List[Dict[str, Any]]:
        now_iso = datetime.now().isoformat(timespec="seconds")
        fontes_spec = [
            ("orcamentaria", "ReceitaOrcamentaria"),
            ("uniao", "ReceitaUniao"),
            ("estado", "ReceitaEstado"),
        ]
        all_rows: List[Dict[str, Any]] = []
        for tipo_fonte, listagem in fontes_spec:
            raw_list = self._fetch_json("Receitas", self._base_period_params(ano, listagem))
            batch: List[Dict[str, Any]] = []
            seen_keys: set[str] = set()
            for r in raw_list:
                codigo = str(r.get("CODIGO") or "").strip()
                nome = str(r.get("NOME") or "").strip()
                ordem = str(r.get("ORDEM") or "").strip()
                if not codigo and not nome:
                    continue
                chave = f"{ano}|{tipo_fonte}|{codigo}|{ordem}"
                if chave in seen_keys:
                    continue
                seen_keys.add(chave)
                batch.append(
                    {
                        "chave_receita": chave,
                        "ano": int(ano),
                        "tipo_fonte": tipo_fonte,
                        "ordem": ordem,
                        "codigo": codigo,
                        "nome": nome,
                        "eh_raiz": 0,
                        "previsao_inicial": parse_fiorilli_number(r.get("PREVISAO_INICIAL")),
                        "previsao_atualizada": parse_fiorilli_number(r.get("PREVISAO_ATUALIZADA")),
                        "arrecadado_periodo": parse_fiorilli_number(r.get("ARRECADADO_PERIODO")),
                        "arrecadado_total": parse_fiorilli_number(r.get("ARRECADADO_TOTAL")),
                        "atualizado_em": now_iso,
                    }
                )
            all_rows.extend(compute_receita_hierarchy(batch))
        return all_rows

    def fetch_despesas_orgaos(self, ano: int) -> List[Dict[str, Any]]:
        now_iso = datetime.now().isoformat(timespec="seconds")
        raw_list = self._fetch_json("Despesas", self._base_period_params(ano, "DespesasPorOrgao"))
        dedup: Dict[str, Dict[str, Any]] = {}
        for idx, r in enumerate(raw_list):
            emp = str(r.get("EMPRESA") or "").strip()
            cod = str(r.get("CODIGO") or f"ROW{idx}").strip()
            nome = str(r.get("DESCRICAO") or "").strip() or f"Órgão {cod}"
            chave = f"{ano}|{emp}|{cod}"
            dedup[chave] = {
                "chave_orgao": chave,
                "ano": int(ano),
                "empresa": emp,
                "codigo": cod,
                "orgao_nome": nome,
                "dotacao_inicial": parse_fiorilli_number(r.get("DOTAC")),
                "alteracoes": parse_fiorilli_number(r.get("ALTDO")),
                "dotacao_atualizada": parse_fiorilli_number(r.get("DOTACAO_ATUALIZADA")),
                "empenhado": parse_fiorilli_number(r.get("EMPENHADO")),
                "liquidado": parse_fiorilli_number(r.get("LIQUIDADO")),
                "pago": parse_fiorilli_number(r.get("PAGO")),
                "atualizado_em": now_iso,
            }
        return list(dedup.values())

    def fetch_despesas_fornecedores(self, ano: int) -> List[Dict[str, Any]]:
        now_iso = datetime.now().isoformat(timespec="seconds")
        params = self._base_period_params(ano, "DespesasPorFornecedor")
        params["MostrarFornecedor"] = "True"
        raw_list = self._fetch_json("Despesas", params)
        dedup: Dict[str, Dict[str, Any]] = {}
        for idx, r in enumerate(raw_list):
            emp = str(r.get("EMPRESA") or "").strip()
            cod = str(r.get("CODIGO") or "").strip()
            doc = normalize_doc(r.get("INSMF"))
            nome = str(r.get("DESCRICAO") or "").strip() or "Fornecedor não identificado"
            key_tail = cod or doc or f"IDX{idx}"
            chave = f"{ano}|{emp}|{key_tail}"
            item = {
                "chave_fornecedor": chave,
                "ano": int(ano),
                "empresa": emp,
                "codigo_fornecedor": cod,
                "fornecedor_doc": doc,
                "fornecedor_nome": nome,
                "cidade": str(r.get("CEPCI") or "").strip(),
                "empenhado": parse_fiorilli_number(r.get("EMPENHADO")),
                "liquidado": parse_fiorilli_number(r.get("LIQUIDADO")),
                "pago": parse_fiorilli_number(r.get("PAGO")),
                "atualizado_em": now_iso,
            }
            if chave in dedup:
                prev = dedup[chave]
                prev["empenhado"] = round(prev["empenhado"] + item["empenhado"], 2)
                prev["liquidado"] = round(prev["liquidado"] + item["liquidado"], 2)
                prev["pago"] = round(prev["pago"] + item["pago"], 2)
            else:
                dedup[chave] = item
        return list(dedup.values())

    def fetch_contratos(self, ano: int) -> List[Dict[str, Any]]:
        now_iso = datetime.now().isoformat(timespec="seconds")
        params = {
            "ConectarExercicio": str(ano),
            "Listagem": "Contratos",
            "Ano": str(ano),
            "Empresa": self.empresa,
            "MostraDadosConsolidado": "True",
            "ContratosApenasPublicados": "False",
        }
        raw_list = self._fetch_json("LicitacoesEContratos", params)
        dedup: Dict[str, Dict[str, Any]] = {}
        for idx, r in enumerate(raw_list):
            emp = str(r.get("EMPRESA") or "").strip()
            codigo = str(r.get("CODIGO") or f"C{idx}").strip()
            chave = f"{ano}|{emp}|{codigo}"
            fund_legal = str(r.get("FUNDLEGAL") or "").strip()
            modalidade = str(r.get("LICIT") or r.get("MODALI") or "").strip()
            tipo_contratacao, eh_direta = classify_contratacao_fiorilli(fund_legal, modalidade)
            objeto = str(r.get("OBJETO_COMPLETO") or r.get("OBJETO") or "").strip()
            entidade = str(r.get("ENTIDADE") or "").strip()
            secretaria = str(r.get("CODLO_GESTORNOME") or entidade).strip()
            categoria = classify_pncp_contrato({"objeto": objeto, "unidade_nome": secretaria})

            val_contrato = parse_fiorilli_number(r.get("VALCON"))
            val_aditado = parse_fiorilli_number(r.get("ADITADO"))
            val_efetivo = round(val_contrato + val_aditado, 2)
            dt_anula = parse_fiorilli_date(r.get("DTANULA"))

            dedup[chave] = {
                "chave_contrato": chave,
                "ano": int(ano),
                "empresa": emp,
                "entidade": entidade,
                "codigo": codigo,
                "contrato_num": str(r.get("CONTRATONUM") or codigo).strip(),
                "processo_licitacao": str(r.get("PROCLIC") or "").strip(),
                "processo_adm": str(r.get("NPROLI") or "").strip(),
                "fund_legal": fund_legal,
                "modalidade": modalidade,
                "tipo_contratacao": tipo_contratacao,
                "eh_contratacao_direta": int(eh_direta),
                "fornecedor_doc": normalize_doc(r.get("INSMF")),
                "fornecedor_nome": str(r.get("FORNECEDOR") or "").strip(),
                "objeto": objeto,
                "secretaria_gestora": secretaria,
                "categoria_tematica": categoria,
                "valor_contrato": val_contrato,
                "valor_aditado": val_aditado,
                "valor_efetivo": val_efetivo,
                "empenhado": parse_fiorilli_number(r.get("EMPENHADO")),
                "liquidado": parse_fiorilli_number(r.get("LIQUIDADO")),
                "saldo_empenhar": parse_fiorilli_number(r.get("SALDOEMPENHAR")),
                "data_assinatura": parse_fiorilli_date(r.get("DTASSI")),
                "data_publicacao": parse_fiorilli_date(r.get("DTPUBL")),
                "vigencia_inicio": parse_fiorilli_date(r.get("VIGENI")),
                "vigencia_fim": parse_fiorilli_date(r.get("VIGENF")),
                "data_anulacao": dt_anula,
                "anulado": 1 if dt_anula else 0,
                "atualizado_em": now_iso,
            }
        return list(dedup.values())

    def sync_imperatriz(self, anos: Sequence[int] = FIORILLI_DEFAULT_YEARS) -> Dict[str, Any]:
        """Sincroniza receitas, despesas por órgão, despesas por fornecedor e contratos/dispensas."""
        t0 = time.time()
        anos_norm = sorted({int(a) for a in anos if str(a).isdigit()}, reverse=True)
        totais = {
            "fiorilli_receitas": 0,
            "fiorilli_despesas_orgaos": 0,
            "fiorilli_despesas_fornecedores": 0,
            "fiorilli_contratos": 0,
        }
        por_ano: Dict[str, Dict[str, int]] = {}
        erros: List[str] = []

        steps: List[Tuple[str, Callable[[int], List[Dict[str, Any]]], Callable[[List[Dict[str, Any]]], int]]] = [
            ("fiorilli_receitas", self.fetch_receitas, self.db.upsert_fiorilli_receitas_batch),
            ("fiorilli_despesas_orgaos", self.fetch_despesas_orgaos, self.db.upsert_fiorilli_despesas_orgaos_batch),
            (
                "fiorilli_despesas_fornecedores",
                self.fetch_despesas_fornecedores,
                self.db.upsert_fiorilli_despesas_fornecedores_batch,
            ),
            ("fiorilli_contratos", self.fetch_contratos, self.db.upsert_fiorilli_contratos_batch),
        ]

        for ano in anos_norm:
            year_stats: Dict[str, int] = {}
            for tbl_name, fetcher, saver in steps:
                try:
                    rows = fetcher(ano)
                    saved = int(saver(rows) or 0)
                    year_stats[tbl_name] = saved
                    totais[tbl_name] += saved
                except Exception as exc:
                    msg = f"Ano {ano} ({tbl_name}): {exc}"
                    logger.error(msg)
                    erros.append(msg)
                    year_stats[tbl_name] = 0
            por_ano[str(ano)] = year_stats

        duracao = round(time.time() - t0, 2)
        total_registros = sum(totais.values())
        return {
            "ok": total_registros > 0 and len(erros) == 0,
            "parcial": total_registros > 0 and len(erros) > 0,
            "anos": anos_norm,
            "totais": totais,
            "total_registros": total_registros,
            "por_ano": por_ano,
            "erros": erros,
            "duracao_segundos": duracao,
        }
