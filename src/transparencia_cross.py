"""
Motor Analítico de Cruzamento entre PNCP e Portal Transparência Fiorilli (Imperatriz - MA).
Opera 100% em Python 3 stdlib cruzando:
  1. Receitas Arrecadadas x Despesas Pagas x Volume Contratado (2025 e 2026)
  2. Fornecedores (CNPJ/CPF): Contratado no PNCP x Contratado no Fiorilli x Empenhado/Liquidado/Pago
  3. Auditoria de Dispensas e Inexigibilidades do Portal da Prefeitura x PNCP
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.fiorilli_client import normalize_doc, normalize_processo
from src.pncp_analytics import PNCP_CATEGORIAS_ICONS, PNCP_CATEGORIAS_ORDEM

ALLOWED_CROSS_YEARS: Tuple[int, ...] = (2025, 2026)

ALERTA_LABELS: Dict[str, str] = {
    "PAGO_ACIMA_PNCP": "Pago acima do contratado no PNCP",
    "PAGO_SEM_PNCP": "Pago sem contrato localizado no PNCP",
    "DIVERGENCIA_VALOR": "Divergência relevante PNCP × Fiorilli",
    "CONTRATO_SEM_PAGAMENTO": "Contratado no PNCP sem pagamento no Fiorilli",
    "FIORILLI_SEM_PNCP": "Contratado no Fiorilli sem PNCP",
    "FOLHA_OU_REPASSE_PUBLICO": "Folha / Encargos / Repasse Público",
    "CONCILIADO": "Conciliado (PNCP compatível)",
    "SEM_ALERTA": "Pagamento sem PNCP abaixo do corte",
}

_MUNICIPAL_PUBLIC_DOCS = {
    "06158455000116",
    "69555019000109",
    "00939023000166",
    "07801241000189",
    "06074091000196",
}

_FOLHA_OU_REPASSE_MARKERS = (
    "FOPAG",
    "FOLHA DE PAGAMENTO",
    "INSS",
    "SEGURIDADE SOCIAL",
    "PASEP",
    "FGTS",
    "RECEITA FEDERAL",
    "MINISTERIO DA FAZENDA",
    "TESOURO NACIONAL",
    "TRIBUNAL DE JUSTICA",
    "PREFEITURA MUNICIPAL DE IMPERATRIZ",
    "CAMARA MUNICIPAL DE IMPERATRIZ",
    "FUNDO MUNICIPAL DE ",
    "FUNDO DE MANUTENCAO",
    "IMPREV",
    "INSTITUTO DE PREVIDENCIA",
    "CONSIGNACAO",
    "PENSAO ALIMENTICIA",
)


def _is_folha_ou_repasse_publico(doc: str, nome: str) -> bool:
    doc_clean = normalize_doc(doc)
    if doc_clean and doc_clean in _MUNICIPAL_PUBLIC_DOCS:
        return True
    nome_up = _strip_accents_upper(nome)
    return any(marker in nome_up for marker in _FOLHA_OU_REPASSE_MARKERS)


def _is_contrato_do_exercicio(c: Dict[str, Any]) -> bool:
    """Identifica se o contrato Fiorilli foi firmado no próprio exercício (`ano`), separando remanescentes."""
    ex_ano = int(c.get("ano") or 0)
    if ex_ano <= 0:
        return True
    for dt_field in ("data_assinatura", "data_publicacao"):
        dt_str = str(c.get(dt_field) or "").strip()
        if len(dt_str) >= 4 and dt_str[:4].isdigit():
            yr = int(dt_str[:4])
            if 2000 <= yr <= 2100:
                return yr == ex_ano
    cod = str(c.get("codigo") or "").strip()
    m = re.search(r"/(\d{2})$", cod)
    if m:
        return (2000 + int(m.group(1))) == ex_ano
    return True


def _contrato_identity_key(c: Dict[str, Any]) -> Tuple[str, str]:
    emp = str(c.get("empresa") or "").strip()
    cod = str(c.get("codigo") or c.get("chave_contrato") or "").strip()
    return (emp, cod)


def _dedup_fiorilli_contratos(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicidade de contratos antigos que se repetem nas listagens de 2025 e 2026."""
    sorted_rows = sorted(rows, key=lambda r: int(r.get("ano") or 0), reverse=True)
    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, Any]] = []
    for r in sorted_rows:
        key = _contrato_identity_key(r)
        if key[1] and key in seen:
            continue
        if key[1]:
            seen.add(key)
        out.append(r)
    return out


def _split_fiorilli_contratos_by_exercicio(
    con_f: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Separa contratos firmados no próprio exercício dos remanescentes de anos anteriores (deduplicados)."""
    con_ex = [c for c in con_f if _is_contrato_do_exercicio(c)]
    ex_keys = {_contrato_identity_key(c) for c in con_ex if _contrato_identity_key(c)[1]}
    con_rem_raw = [c for c in con_f if not _is_contrato_do_exercicio(c)]
    con_rem = [
        c
        for c in _dedup_fiorilli_contratos(con_rem_raw)
        if _contrato_identity_key(c) not in ex_keys
    ]
    return con_ex, con_rem


def _strip_accents_upper(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain).strip().upper()


def _safe_pct(num: float, den: float) -> float:
    if not den:
        return 0.0
    return round((num / den) * 100.0, 1)


def _row_matches_years(row_ano: int, data_pub: str, target_years: Sequence[int]) -> bool:
    yr_set = {int(y) for y in target_years}
    if row_ano in yr_set:
        return True
    pub = str(data_pub or "").strip()
    return any(pub.startswith(str(y)) for y in yr_set)


def _filter_pncp_rows(
    pncp_rows: List[Dict[str, Any]],
    ano: Optional[int] = None,
    allowed_years: Sequence[int] = ALLOWED_CROSS_YEARS,
) -> List[Dict[str, Any]]:
    targets = [int(ano)] if ano else list(allowed_years)
    return [
        r
        for r in pncp_rows
        if _row_matches_years(
            int(r.get("ano_contrato") or 0),
            str(r.get("data_publicacao") or ""),
            targets,
        )
    ]


def _filter_year_rows(
    rows: List[Dict[str, Any]],
    ano: Optional[int] = None,
    allowed_years: Sequence[int] = ALLOWED_CROSS_YEARS,
) -> List[Dict[str, Any]]:
    targets = {int(ano)} if ano else set(allowed_years)
    return [r for r in rows if int(r.get("ano") or 0) in targets]


def _build_pncp_indexes(
    pncp_rows: List[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    by_proc: Dict[str, List[Dict[str, Any]]] = {}
    by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for r in pncp_rows:
        proc = normalize_processo(r.get("processo"))
        if proc:
            by_proc.setdefault(proc, []).append(r)
        doc = normalize_doc(r.get("fornecedor_cnpj"))
        if doc:
            by_doc.setdefault(doc, []).append(r)
    return by_proc, by_doc


def _match_dispensa_with_pncp(
    disp: Dict[str, Any],
    by_proc: Dict[str, List[Dict[str, Any]]],
    by_doc: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    disp_ano = int(disp.get("ano") or 0)
    disp_val = float(disp.get("valor_efetivo") or disp.get("valor_contrato") or 0.0)
    proc = normalize_processo(disp.get("processo_adm"))
    doc = normalize_doc(disp.get("fornecedor_doc"))

    # 1. Tentativa exata por número de processo administrativo
    if proc and proc in by_proc:
        cands = [
            c
            for c in by_proc[proc]
            if not disp_ano
            or not c.get("ano_contrato")
            or abs(int(c.get("ano_contrato") or 0) - disp_ano) <= 1
        ]
        if cands:
            cands.sort(
                key=lambda c: (
                    0 if (doc and normalize_doc(c.get("fornecedor_cnpj")) == doc) else 1,
                    abs(float(c.get("valor_global") or 0.0) - disp_val),
                )
            )
            best = cands[0]
            return {
                "status_pncp": "CONCILIADO",
                "status_pncp_label": "Consta no PNCP (processo)",
                "motivo_pncp": f"Processo administrativo localizado no PNCP ({best.get('numero_controle_pncp', '')}).",
                "pncp_match_controle": str(best.get("numero_controle_pncp") or ""),
                "pncp_match_valor": round(float(best.get("valor_global") or 0.0), 2),
                "pncp_match_url": str(best.get("url_pncp") or ""),
            }

    # 2. Tentativa por CNPJ/CPF + exercício + valor compatível
    if doc and doc in by_doc:
        year_cands = [
            c
            for c in by_doc[doc]
            if not disp_ano
            or not c.get("ano_contrato")
            or abs(int(c.get("ano_contrato") or 0) - disp_ano) <= 1
        ]
        if year_cands:
            tol = max(1000.0, 0.05 * disp_val) if disp_val > 0 else 1000.0
            close_cands = [
                c
                for c in year_cands
                if disp_val > 0 and abs(float(c.get("valor_global") or 0.0) - disp_val) <= tol
            ]
            if close_cands:
                close_cands.sort(key=lambda c: abs(float(c.get("valor_global") or 0.0) - disp_val))
                best = close_cands[0]
                return {
                    "status_pncp": "CONCILIADO",
                    "status_pncp_label": "Consta no PNCP (CNPJ + valor)",
                    "motivo_pncp": "Mesmo fornecedor, exercício e valor compatível localizados no PNCP.",
                    "pncp_match_controle": str(best.get("numero_controle_pncp") or ""),
                    "pncp_match_valor": round(float(best.get("valor_global") or 0.0), 2),
                    "pncp_match_url": str(best.get("url_pncp") or ""),
                }

            total_forn_pncp = round(sum(float(c.get("valor_global") or 0.0) for c in year_cands), 2)
            return {
                "status_pncp": "FORNECEDOR_NO_PNCP",
                "status_pncp_label": "Fornecedor no PNCP (outro processo)",
                "motivo_pncp": (
                    f"Fornecedor possui {len(year_cands)} contrato(s) no PNCP neste recorte, "
                    "mas sem vínculo de processo ou valor compatível com esta dispensa."
                ),
                "pncp_match_controle": str(year_cands[0].get("numero_controle_pncp") or ""),
                "pncp_match_valor": total_forn_pncp,
                "pncp_match_url": str(year_cands[0].get("url_pncp") or ""),
            }

    return {
        "status_pncp": "AUSENTE_PNCP",
        "status_pncp_label": "Ausente no PNCP",
        "motivo_pncp": "Nem o processo nem o CPF/CNPJ do fornecedor constam no PNCP para este exercício.",
        "pncp_match_controle": "",
        "pncp_match_valor": 0.0,
        "pncp_match_url": "",
    }


def _aggregate_receitas_bloco(receitas_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    orc_rows = [r for r in receitas_rows if r.get("tipo_fonte") == "orcamentaria"]
    uniao_rows = [r for r in receitas_rows if r.get("tipo_fonte") == "uniao"]
    estado_rows = [r for r in receitas_rows if r.get("tipo_fonte") == "estado"]

    orc_roots = [r for r in orc_rows if int(r.get("eh_raiz") or 0) == 1]
    prev_liquida = round(sum(float(r.get("previsao_atualizada") or 0.0) for r in orc_roots), 2)
    arr_liquida = round(sum(float(r.get("arrecadado_total") or 0.0) for r in orc_roots), 2)
    deducoes_arr = round(
        sum(float(r.get("arrecadado_total") or 0.0) for r in orc_roots if str(r.get("codigo") or "").startswith("9")),
        2,
    )
    deducoes_prev = round(
        sum(float(r.get("previsao_atualizada") or 0.0) for r in orc_roots if str(r.get("codigo") or "").startswith("9")),
        2,
    )
    arr_bruta = round(arr_liquida - deducoes_arr, 2)

    # Composição contábil nível 2 sem dupla contagem
    bucket_defs = [
        ("tributaria", "Impostos, Taxas e Contrib. de Melhoria", ("1100",)),
        ("contribuicoes", "Contribuições (COSIP / Previdência)", ("1200",)),
        ("servicos_patrimonial", "Patrimonial, Serviços e Outras Correntes", ("1300", "1600", "1900")),
        ("transferencias_correntes", "Transferências Correntes (União / Estado / SUS / FUNDEB)", ("1700",)),
        ("capital", "Receitas de Capital (Convênios / Empréstimos)", ("2000",)),
        ("deducoes", "Deduções da Receita (FUNDEB)", ("9000",)),
    ]
    composicao: List[Dict[str, Any]] = []
    for key, label, prefixes in bucket_defs:
        # Pega o código contábil mais agregado de cada ano para o prefixo (evita dupla contagem e soma 2025+2026)
        matched_rows: List[Dict[str, Any]] = []
        for pref in prefixes:
            by_yr: Dict[int, List[Dict[str, Any]]] = {}
            for r in orc_rows:
                if str(r.get("codigo") or "").startswith(f"{pref}."):
                    by_yr.setdefault(int(r.get("ano") or 0), []).append(r)
            for yr_rows in by_yr.values():
                yr_rows.sort(key=lambda r: (str(r.get("codigo") or ""), str(r.get("ordem") or "")))
                matched_rows.append(yr_rows[0])
        prev_b = round(sum(float(r.get("previsao_atualizada") or 0.0) for r in matched_rows), 2)
        arr_b = round(sum(float(r.get("arrecadado_total") or 0.0) for r in matched_rows), 2)
        composicao.append(
            {
                "chave": key,
                "nome": label,
                "previsao_atualizada": prev_b,
                "arrecadado_total": arr_b,
                "participacao_pct": _safe_pct(arr_b, arr_bruta),
                "realizacao_pct": _safe_pct(abs(arr_b), abs(prev_b)) if prev_b else 0.0,
            }
        )

    uniao_roots = [r for r in uniao_rows if int(r.get("eh_raiz") or 0) == 1]
    estado_roots = [r for r in estado_rows if int(r.get("eh_raiz") or 0) == 1]
    uniao_prev = round(sum(float(r.get("previsao_atualizada") or 0.0) for r in uniao_roots), 2)
    uniao_arr = round(sum(float(r.get("arrecadado_total") or 0.0) for r in uniao_roots), 2)
    estado_prev = round(sum(float(r.get("previsao_atualizada") or 0.0) for r in estado_roots), 2)
    estado_arr = round(sum(float(r.get("arrecadado_total") or 0.0) for r in estado_roots), 2)

    return {
        "previsao_atualizada_liquida": prev_liquida,
        "arrecadado_liquido": arr_liquida,
        "arrecadado_bruto": arr_bruta,
        "deducoes_arrecadado": deducoes_arr,
        "deducoes_previsto": deducoes_prev,
        "pct_realizado": _safe_pct(arr_liquida, prev_liquida),
        "transferencias_uniao_previsto": uniao_prev,
        "transferencias_uniao_arrecadado": uniao_arr,
        "transferencias_estado_previsto": estado_prev,
        "transferencias_estado_arrecadado": estado_arr,
        "composicao": composicao,
    }


def build_transparencia_resumo(
    pncp_rows: List[Dict[str, Any]],
    receitas_rows: List[Dict[str, Any]],
    desp_orgaos_rows: List[Dict[str, Any]],
    desp_forn_rows: List[Dict[str, Any]],
    fiorilli_contratos_rows: List[Dict[str, Any]],
    ano: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Consolida painel fiscal PNCP × Receitas × Despesas × Dispensas para 2025 e 2026."""
    pncp_f = _filter_pncp_rows(pncp_rows, ano=ano)
    rec_f = _filter_year_rows(receitas_rows, ano=ano)
    org_f = _filter_year_rows(desp_orgaos_rows, ano=ano)
    forn_f = _filter_year_rows(desp_forn_rows, ano=ano)
    con_f_all = _filter_year_rows(fiorilli_contratos_rows, ano=ano)
    con_f = [c for c in con_f_all if int(c.get("anulado") or 0) == 0]

    receitas = _aggregate_receitas_bloco(rec_f)

    # Despesas por órgão agregadas (evita duplicação ao consolidar 2025+2026)
    org_agg: Dict[str, Dict[str, Any]] = {}
    for o in org_f:
        cod = str(o.get("codigo") or "")
        nome = str(o.get("orgao_nome") or f"Órgão {cod}").strip()
        emp = str(o.get("empresa") or "")
        key = f"{emp}|{cod}|{nome}"
        if key not in org_agg:
            org_agg[key] = {
                "codigo": cod,
                "empresa": emp,
                "orgao_nome": nome,
                "dotacao_atualizada": 0.0,
                "empenhado": 0.0,
                "liquidado": 0.0,
                "pago": 0.0,
            }
        org_agg[key]["dotacao_atualizada"] += float(o.get("dotacao_atualizada") or 0.0)
        org_agg[key]["empenhado"] += float(o.get("empenhado") or 0.0)
        org_agg[key]["liquidado"] += float(o.get("liquidado") or 0.0)
        org_agg[key]["pago"] += float(o.get("pago") or 0.0)

    desp_orgaos_lista = []
    for item in org_agg.values():
        dot = round(item["dotacao_atualizada"], 2)
        emp_v = round(item["empenhado"], 2)
        liq_v = round(item["liquidado"], 2)
        pag_v = round(item["pago"], 2)
        desp_orgaos_lista.append(
            {
                "codigo": item["codigo"],
                "empresa": item["empresa"],
                "orgao_nome": item["orgao_nome"],
                "dotacao_atualizada": dot,
                "empenhado": emp_v,
                "liquidado": liq_v,
                "pago": pag_v,
                "pct_pago_dotacao": _safe_pct(pag_v, dot),
            }
        )
    desp_orgaos_lista.sort(key=lambda x: x["pago"], reverse=True)

    desp_dotacao = round(sum(o["dotacao_atualizada"] for o in desp_orgaos_lista), 2)
    desp_empenhado = round(sum(o["empenhado"] for o in desp_orgaos_lista), 2)
    desp_liquidado = round(sum(o["liquidado"] for o in desp_orgaos_lista), 2)
    desp_pago = round(sum(o["pago"] for o in desp_orgaos_lista), 2)

    forn_pago_total = round(sum(float(f.get("pago") or 0.0) for f in forn_f), 2)
    forn_emp_total = round(sum(float(f.get("empenhado") or 0.0) for f in forn_f), 2)
    forn_unicos_fiorilli = len(
        {normalize_doc(f.get("fornecedor_doc")) or f.get("fornecedor_nome", "") for f in forn_f}
    )

    pncp_valor_total = round(sum(float(c.get("valor_global") or 0.0) for c in pncp_f), 2)
    pncp_qtd = len(pncp_f)

    con_ex, con_rem = _split_fiorilli_contratos_by_exercicio(con_f)
    fiorilli_valor_total = round(sum(float(c.get("valor_efetivo") or 0.0) for c in con_ex), 2)
    fiorilli_qtd = len(con_ex)
    fiorilli_rem_valor = round(sum(float(c.get("valor_efetivo") or 0.0) for c in con_rem), 2)
    fiorilli_rem_qtd = len(con_rem)

    by_proc, by_doc = _build_pncp_indexes(pncp_f)
    diretas = [c for c in con_ex if int(c.get("eh_contratacao_direta") or 0) == 1]
    dispensas_rows = [c for c in diretas if c.get("tipo_contratacao") == "Dispensa"]
    inexig_rows = [c for c in diretas if c.get("tipo_contratacao") == "Inexigibilidade"]

    status_counts = {
        "CONCILIADO": {"qtd": 0, "valor": 0.0},
        "FORNECEDOR_NO_PNCP": {"qtd": 0, "valor": 0.0},
        "AUSENTE_PNCP": {"qtd": 0, "valor": 0.0},
    }
    for d in diretas:
        m = _match_dispensa_with_pncp(d, by_proc, by_doc)
        st = m["status_pncp"]
        status_counts[st]["qtd"] += 1
        status_counts[st]["valor"] = round(
            status_counts[st]["valor"] + float(d.get("valor_efetivo") or 0.0), 2
        )

    arr_liq = receitas["arrecadado_liquido"]

    # Comparativo anual fixo (2026 vs 2025)
    comparativo_anual = []
    for y in sorted(ALLOWED_CROSS_YEARS, reverse=True):
        ry = _aggregate_receitas_bloco(_filter_year_rows(receitas_rows, ano=y))
        oy = _filter_year_rows(desp_orgaos_rows, ano=y)
        py = _filter_pncp_rows(pncp_rows, ano=y)
        cy_all = [c for c in _filter_year_rows(fiorilli_contratos_rows, ano=y) if int(c.get("anulado") or 0) == 0]
        cy = [c for c in cy_all if _is_contrato_do_exercicio(c)]
        dy = [c for c in cy if int(c.get("eh_contratacao_direta") or 0) == 1]
        comparativo_anual.append(
            {
                "ano": y,
                "receita_arrecadada": ry["arrecadado_liquido"],
                "receita_prevista": ry["previsao_atualizada_liquida"],
                "despesa_paga": round(sum(float(x.get("pago") or 0.0) for x in oy), 2),
                "despesa_empenhada": round(sum(float(x.get("empenhado") or 0.0) for x in oy), 2),
                "pncp_contratos": len(py),
                "pncp_valor": round(sum(float(x.get("valor_global") or 0.0) for x in py), 2),
                "fiorilli_contratos": len(cy),
                "fiorilli_valor": round(sum(float(x.get("valor_efetivo") or 0.0) for x in cy), 2),
                "diretas_qtd": len(dy),
                "diretas_valor": round(sum(float(x.get("valor_efetivo") or 0.0) for x in dy), 2),
            }
        )

    entidades_map: Dict[str, int] = {}
    for c in con_f_all:
        ent = str(c.get("entidade") or "").strip()
        if ent:
            entidades_map[ent] = entidades_map.get(ent, 0) + 1
    entidades_disponiveis = [k for k, _ in sorted(entidades_map.items(), key=lambda x: (-x[1], x[0]))]

    return {
        "ano_filtro": ano,
        "anos_suportados": list(ALLOWED_CROSS_YEARS),
        "meta": meta or {},
        "receitas": receitas,
        "despesas": {
            "dotacao_atualizada": desp_dotacao,
            "empenhado": desp_empenhado,
            "liquidado": desp_liquidado,
            "pago": desp_pago,
            "pct_execucao_pago": _safe_pct(desp_pago, desp_dotacao),
            "fornecedores_pago_total": forn_pago_total,
            "fornecedores_empenhado_total": forn_emp_total,
            "fornecedores_unicos": forn_unicos_fiorilli,
            "por_orgao": desp_orgaos_lista,
        },
        "contratos_comparativo": {
            "pncp_qtd": pncp_qtd,
            "pncp_valor_total": pncp_valor_total,
            "fiorilli_qtd": fiorilli_qtd,
            "fiorilli_valor_total": fiorilli_valor_total,
            "fiorilli_remanescentes_qtd": fiorilli_rem_qtd,
            "fiorilli_remanescentes_valor": fiorilli_rem_valor,
            "fiorilli_anuladas_qtd": len(con_f_all) - len(con_f),
            "diferenca_fiorilli_menos_pncp": round(fiorilli_valor_total - pncp_valor_total, 2),
            "pct_pago_sobre_receita": _safe_pct(desp_pago, arr_liq),
            "pct_pncp_sobre_receita": _safe_pct(pncp_valor_total, arr_liq),
            "pct_fiorilli_sobre_receita": _safe_pct(fiorilli_valor_total, arr_liq),
        },
        "dispensas_resumo": {
            "diretas_qtd": len(diretas),
            "diretas_valor": round(sum(float(d.get("valor_efetivo") or 0.0) for d in diretas), 2),
            "dispensas_qtd": len(dispensas_rows),
            "dispensas_valor": round(sum(float(d.get("valor_efetivo") or 0.0) for d in dispensas_rows), 2),
            "inexigibilidades_qtd": len(inexig_rows),
            "inexigibilidades_valor": round(sum(float(d.get("valor_efetivo") or 0.0) for d in inexig_rows), 2),
            "por_status_pncp": status_counts,
        },
        "comparativo_anual": comparativo_anual,
        "entidades_disponiveis": entidades_disponiveis,
    }


def _classify_supplier_alert(item: Dict[str, Any], corte_sem_pncp: float) -> Tuple[str, str]:
    pncp_v = float(item.get("pncp_valor") or 0.0)
    fior_v = float(item.get("fiorilli_valor") or 0.0)
    pago_v = float(item.get("pago") or 0.0)
    doc = str(item.get("fornecedor_doc") or "")
    nome = str(item.get("fornecedor_nome") or "")

    if pncp_v > 0 and pago_v > (pncp_v * 1.05) and (pago_v - pncp_v) >= 1000.0:
        return "PAGO_ACIMA_PNCP", ALERTA_LABELS["PAGO_ACIMA_PNCP"]
    if pncp_v <= 0 and _is_folha_ou_repasse_publico(doc, nome):
        return "FOLHA_OU_REPASSE_PUBLICO", ALERTA_LABELS["FOLHA_OU_REPASSE_PUBLICO"]
    if pncp_v <= 0 and pago_v >= corte_sem_pncp:
        return "PAGO_SEM_PNCP", ALERTA_LABELS["PAGO_SEM_PNCP"]
    if pncp_v > 0 and fior_v > 0:
        diff = abs(fior_v - pncp_v)
        if diff >= 10000.0 and (diff / max(pncp_v, fior_v)) > 0.20:
            return "DIVERGENCIA_VALOR", ALERTA_LABELS["DIVERGENCIA_VALOR"]
    if pncp_v > 0 and pago_v <= 0:
        return "CONTRATO_SEM_PAGAMENTO", ALERTA_LABELS["CONTRATO_SEM_PAGAMENTO"]
    if pncp_v > 0:
        return "CONCILIADO", ALERTA_LABELS["CONCILIADO"]
    if pncp_v <= 0 and fior_v >= corte_sem_pncp:
        return "FIORILLI_SEM_PNCP", ALERTA_LABELS["FIORILLI_SEM_PNCP"]
    return "SEM_ALERTA", ALERTA_LABELS["SEM_ALERTA"]


def build_cruzamento_fornecedores(
    pncp_rows: List[Dict[str, Any]],
    desp_forn_rows: List[Dict[str, Any]],
    fiorilli_contratos_rows: List[Dict[str, Any]],
    ano: Optional[int] = None,
    alerta: Optional[str] = None,
    q: Optional[str] = None,
    ordem: str = "pago_desc",
    pagina: int = 1,
    limite: int = 50,
    corte_sem_pncp: float = 100000.0,
) -> Dict[str, Any]:
    """Cruza fornecedores (CNPJ/CPF) entre PNCP, Contratos Fiorilli e Pagamentos Reais."""
    pncp_f = _filter_pncp_rows(pncp_rows, ano=ano)
    forn_f = _filter_year_rows(desp_forn_rows, ano=ano)
    con_f = [c for c in _filter_year_rows(fiorilli_contratos_rows, ano=ano) if int(c.get("anulado") or 0) == 0]
    con_ex, con_rem = _split_fiorilli_contratos_by_exercicio(con_f)

    sup_map: Dict[str, Dict[str, Any]] = {}

    def ensure_sup(raw_doc: Any, raw_name: Any) -> Dict[str, Any]:
        doc = normalize_doc(raw_doc)
        name = str(raw_name or "").strip() or "Fornecedor não informado"
        key = doc if doc else f"NAME:{_strip_accents_upper(name)}"
        if key not in sup_map:
            sup_map[key] = {
                "fornecedor_doc": doc,
                "fornecedor_nome": name,
                "tipo_pessoa": "CPF" if len(doc) == 11 else ("CNPJ" if len(doc) == 14 else "N/I"),
                "cidade": "",
                "orgaos_pncp": set(),
                "entidades_fiorilli": set(),
                "pncp_contratos_qtd": 0,
                "pncp_valor": 0.0,
                "fiorilli_contratos_qtd": 0,
                "fiorilli_valor": 0.0,
                "fiorilli_dispensas_qtd": 0,
                "fiorilli_dispensas_valor": 0.0,
                "fiorilli_remanescentes_qtd": 0,
                "fiorilli_remanescentes_valor": 0.0,
                "empenhado": 0.0,
                "liquidado": 0.0,
                "pago": 0.0,
            }
        elif len(name) > len(sup_map[key]["fornecedor_nome"]) and sup_map[key]["fornecedor_nome"] == "Fornecedor não informado":
            sup_map[key]["fornecedor_nome"] = name
        return sup_map[key]

    for c in pncp_f:
        item = ensure_sup(c.get("fornecedor_cnpj"), c.get("fornecedor_nome"))
        item["pncp_contratos_qtd"] += 1
        item["pncp_valor"] += float(c.get("valor_global") or 0.0)
        if c.get("orgao_sigla"):
            item["orgaos_pncp"].add(str(c["orgao_sigla"]))

    for f in forn_f:
        item = ensure_sup(f.get("fornecedor_doc"), f.get("fornecedor_nome"))
        item["empenhado"] += float(f.get("empenhado") or 0.0)
        item["liquidado"] += float(f.get("liquidado") or 0.0)
        item["pago"] += float(f.get("pago") or 0.0)
        if f.get("cidade") and not item["cidade"]:
            item["cidade"] = str(f.get("cidade") or "")

    for fc in con_ex:
        item = ensure_sup(fc.get("fornecedor_doc"), fc.get("fornecedor_nome"))
        val_eff = float(fc.get("valor_efetivo") or 0.0)
        item["fiorilli_contratos_qtd"] += 1
        item["fiorilli_valor"] += val_eff
        if int(fc.get("eh_contratacao_direta") or 0) == 1:
            item["fiorilli_dispensas_qtd"] += 1
            item["fiorilli_dispensas_valor"] += val_eff
        if fc.get("entidade"):
            item["entidades_fiorilli"].add(str(fc["entidade"]))

    for fc in con_rem:
        item = ensure_sup(fc.get("fornecedor_doc"), fc.get("fornecedor_nome"))
        val_eff = float(fc.get("valor_efetivo") or 0.0)
        item["fiorilli_remanescentes_qtd"] += 1
        item["fiorilli_remanescentes_valor"] += val_eff
        if fc.get("entidade"):
            item["entidades_fiorilli"].add(str(fc["entidade"]))

    all_rows: List[Dict[str, Any]] = []
    resumo_alertas: Dict[str, Dict[str, Any]] = {
        code: {"qtd": 0, "pago": 0.0, "pncp": 0.0} for code in ALERTA_LABELS
    }

    for entry in sup_map.values():
        pncp_v = round(entry["pncp_valor"], 2)
        fior_v = round(entry["fiorilli_valor"], 2)
        disp_v = round(entry["fiorilli_dispensas_valor"], 2)
        rem_v = round(entry["fiorilli_remanescentes_valor"], 2)
        emp_v = round(entry["empenhado"], 2)
        liq_v = round(entry["liquidado"], 2)
        pag_v = round(entry["pago"], 2)

        row = {
            "fornecedor_doc": entry["fornecedor_doc"],
            "fornecedor_nome": entry["fornecedor_nome"],
            "tipo_pessoa": entry["tipo_pessoa"],
            "cidade": entry["cidade"],
            "orgaos_pncp": sorted(entry["orgaos_pncp"]),
            "entidades_fiorilli": sorted(entry["entidades_fiorilli"]),
            "pncp_contratos_qtd": entry["pncp_contratos_qtd"],
            "pncp_valor": pncp_v,
            "fiorilli_contratos_qtd": entry["fiorilli_contratos_qtd"],
            "fiorilli_valor": fior_v,
            "fiorilli_dispensas_qtd": entry["fiorilli_dispensas_qtd"],
            "fiorilli_dispensas_valor": disp_v,
            "fiorilli_remanescentes_qtd": entry["fiorilli_remanescentes_qtd"],
            "fiorilli_remanescentes_valor": rem_v,
            "tem_contrato_anterior_fiorilli": entry["fiorilli_remanescentes_qtd"] > 0,
            "empenhado": emp_v,
            "liquidado": liq_v,
            "pago": pag_v,
            "diferenca_pago_pncp": round(pag_v - pncp_v, 2),
            "diferenca_fiorilli_pncp": round(fior_v - pncp_v, 2),
            "pct_pago_sobre_pncp": _safe_pct(pag_v, pncp_v) if pncp_v > 0 else None,
        }
        code, label = _classify_supplier_alert(row, corte_sem_pncp=corte_sem_pncp)
        row["alerta_codigo"] = code
        row["alerta_label"] = label
        all_rows.append(row)

        resumo_alertas[code]["qtd"] += 1
        resumo_alertas[code]["pago"] = round(resumo_alertas[code]["pago"] + pag_v, 2)
        resumo_alertas[code]["pncp"] = round(resumo_alertas[code]["pncp"] + pncp_v, 2)

    alerta_clean = (alerta or "").strip().upper()
    q_clean = _strip_accents_upper(q or "")

    filtered: List[Dict[str, Any]] = []
    for r in all_rows:
        if alerta_clean and alerta_clean not in ("TODOS", "ALL"):
            if alerta_clean == "ALERTAS":
                if r["alerta_codigo"] not in ("PAGO_ACIMA_PNCP", "PAGO_SEM_PNCP", "DIVERGENCIA_VALOR", "FIORILLI_SEM_PNCP"):
                    continue
            elif r["alerta_codigo"] != alerta_clean:
                continue
        if q_clean:
            hay = _strip_accents_upper(
                f"{r['fornecedor_nome']} {r['fornecedor_doc']} {r['cidade']} {' '.join(r['entidades_fiorilli'])}"
            )
            if q_clean not in hay:
                continue
        filtered.append(r)

    ordem_clean = (ordem or "pago_desc").strip().lower()
    if ordem_clean == "pncp_desc":
        filtered.sort(key=lambda r: (r["pncp_valor"], r["pago"]), reverse=True)
    elif ordem_clean == "fiorilli_desc":
        filtered.sort(key=lambda r: (r["fiorilli_valor"], r["pago"]), reverse=True)
    elif ordem_clean == "divergencia_desc":
        filtered.sort(key=lambda r: (abs(r["diferenca_pago_pncp"]), r["pago"]), reverse=True)
    else:
        filtered.sort(key=lambda r: (r["pago"], r["pncp_valor"], r["fiorilli_valor"]), reverse=True)

    pagina = max(1, int(pagina))
    limite = max(1, min(200, int(limite)))
    total = len(filtered)
    total_paginas = max(1, (total + limite - 1) // limite)
    offset = (pagina - 1) * limite

    return {
        "items": filtered[offset : offset + limite],
        "total": total,
        "pagina": pagina,
        "limite": limite,
        "total_paginas": total_paginas,
        "resumo_alertas": resumo_alertas,
        "corte_sem_pncp": corte_sem_pncp,
    }


def build_auditoria_dispensas(
    pncp_rows: List[Dict[str, Any]],
    fiorilli_contratos_rows: List[Dict[str, Any]],
    ano: Optional[int] = None,
    tipo: Optional[str] = None,
    status_pncp: Optional[str] = None,
    categoria: Optional[str] = None,
    entidade: Optional[str] = None,
    q: Optional[str] = None,
    pagina: int = 1,
    limite: int = 50,
    incluir_remanescentes: bool = False,
) -> Dict[str, Any]:
    """Audita Dispensas e Inexigibilidades do Portal Fiorilli cruzando com o PNCP."""
    pncp_f = _filter_pncp_rows(pncp_rows, ano=ano)
    con_raw = [
        c
        for c in _filter_year_rows(fiorilli_contratos_rows, ano=ano)
        if int(c.get("eh_contratacao_direta") or 0) == 1 and int(c.get("anulado") or 0) == 0
    ]
    con_ex, con_rem = _split_fiorilli_contratos_by_exercicio(con_raw)
    remanescentes_omitidos_qtd = len(con_rem)
    con_f = _dedup_fiorilli_contratos(con_ex + con_rem) if incluir_remanescentes else con_ex

    by_proc, by_doc = _build_pncp_indexes(pncp_f)

    enriched: List[Dict[str, Any]] = []
    por_status: Dict[str, Dict[str, Any]] = {
        "AUSENTE_PNCP": {"qtd": 0, "valor": 0.0},
        "FORNECEDOR_NO_PNCP": {"qtd": 0, "valor": 0.0},
        "CONCILIADO": {"qtd": 0, "valor": 0.0},
    }
    cat_agg: Dict[str, Dict[str, Any]] = {
        nome: {"categoria": nome, "icone": PNCP_CATEGORIAS_ICONS.get(nome, "category"), "qtd": 0, "valor_total": 0.0, "ausentes_qtd": 0, "ausentes_valor": 0.0}
        for nome in PNCP_CATEGORIAS_ORDEM
    }

    for c in con_f:
        match = _match_dispensa_with_pncp(c, by_proc, by_doc)
        val_eff = round(float(c.get("valor_efetivo") or 0.0), 2)
        cat_nome = str(c.get("categoria_tematica") or "Outros serviços e fornecimentos")
        if cat_nome not in cat_agg:
            cat_nome = "Outros serviços e fornecimentos"

        row = {
            "chave_contrato": str(c.get("chave_contrato") or ""),
            "ano": int(c.get("ano") or 0),
            "codigo": str(c.get("codigo") or ""),
            "contrato_num": str(c.get("contrato_num") or ""),
            "entidade": str(c.get("entidade") or ""),
            "secretaria_gestora": str(c.get("secretaria_gestora") or ""),
            "tipo_contratacao": str(c.get("tipo_contratacao") or "Dispensa"),
            "fund_legal": str(c.get("fund_legal") or ""),
            "modalidade": str(c.get("modalidade") or ""),
            "processo_licitacao": str(c.get("processo_licitacao") or ""),
            "processo_adm": str(c.get("processo_adm") or ""),
            "fornecedor_doc": str(c.get("fornecedor_doc") or ""),
            "fornecedor_nome": str(c.get("fornecedor_nome") or ""),
            "objeto": str(c.get("objeto") or ""),
            "categoria_tematica": cat_nome,
            "valor_contrato": round(float(c.get("valor_contrato") or 0.0), 2),
            "valor_aditado": round(float(c.get("valor_aditado") or 0.0), 2),
            "valor_efetivo": val_eff,
            "empenhado": round(float(c.get("empenhado") or 0.0), 2),
            "liquidado": round(float(c.get("liquidado") or 0.0), 2),
            "data_assinatura": str(c.get("data_assinatura") or ""),
            "vigencia_fim": str(c.get("vigencia_fim") or ""),
            **match,
        }
        enriched.append(row)

        st = row["status_pncp"]
        por_status[st]["qtd"] += 1
        por_status[st]["valor"] = round(por_status[st]["valor"] + val_eff, 2)

        cat_agg[cat_nome]["qtd"] += 1
        cat_agg[cat_nome]["valor_total"] = round(cat_agg[cat_nome]["valor_total"] + val_eff, 2)
        if st == "AUSENTE_PNCP":
            cat_agg[cat_nome]["ausentes_qtd"] += 1
            cat_agg[cat_nome]["ausentes_valor"] = round(cat_agg[cat_nome]["ausentes_valor"] + val_eff, 2)

    tipo_clean = _strip_accents_upper(tipo or "")
    status_clean = (status_pncp or "").strip().upper()
    cat_clean = (categoria or "").strip()
    ent_clean = _strip_accents_upper(entidade or "")
    q_clean = _strip_accents_upper(q or "")

    filtered: List[Dict[str, Any]] = []
    for r in enriched:
        if tipo_clean and tipo_clean not in ("TODAS", "TODOS", "ALL"):
            if _strip_accents_upper(r["tipo_contratacao"]) != tipo_clean:
                continue
        if status_clean and status_clean not in ("TODOS", "TODAS", "ALL"):
            if r["status_pncp"] != status_clean:
                continue
        if cat_clean and cat_clean.lower() not in ("todas", "all"):
            if r["categoria_tematica"] != cat_clean:
                continue
        if ent_clean and ent_clean not in ("TODAS", "ALL"):
            if _strip_accents_upper(r["entidade"]) != ent_clean:
                continue
        if q_clean:
            hay = _strip_accents_upper(
                f"{r['fornecedor_nome']} {r['fornecedor_doc']} {r['objeto']} "
                f"{r['processo_adm']} {r['codigo']} {r['secretaria_gestora']}"
            )
            if q_clean not in hay:
                continue
        filtered.append(r)

    filtered.sort(key=lambda r: (r["valor_efetivo"], r["ano"], r["data_assinatura"]), reverse=True)

    por_categoria = sorted(
        [v for v in cat_agg.values() if v["qtd"] > 0],
        key=lambda x: x["valor_total"],
        reverse=True,
    )

    pagina = max(1, int(pagina))
    limite = max(1, min(200, int(limite)))
    total = len(filtered)
    total_paginas = max(1, (total + limite - 1) // limite)
    offset = (pagina - 1) * limite

    return {
        "items": filtered[offset : offset + limite],
        "total": total,
        "pagina": pagina,
        "limite": limite,
        "total_paginas": total_paginas,
        "valor_filtrado": round(sum(r["valor_efetivo"] for r in filtered), 2),
        "por_status": por_status,
        "por_categoria": por_categoria,
        "incluir_remanescentes": bool(incluir_remanescentes),
        "remanescentes_omitidos_qtd": remanescentes_omitidos_qtd,
    }
