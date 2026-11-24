"""
Classificador Temático (14 categorias) e Motor de Estatísticas Reais de Contratos do PNCP.
Opera 100% com a biblioteca padrão Python 3 sobre os registros homologados armazenados em SQLite.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

PNCP_CATEGORIAS_META: List[Tuple[str, str]] = [
    ("Alimentação", "restaurant"),
    ("Obras e engenharia", "engineering"),
    ("Educação e merenda", "school"),
    ("Imóveis alugados e comprados", "apartment"),
    ("Advocacia e assessoria jurídica", "gavel"),
    ("Saúde e remédios", "medication"),
    ("Limpeza e lixo", "delete_sweep"),
    ("Veículos e transporte", "directions_bus"),
    ("Combustível", "local_gas_station"),
    ("Tecnologia e sistemas", "computer"),
    ("Shows e eventos", "celebration"),
    ("Publicidade, TV e rádio", "campaign"),
    ("Vigilância e segurança", "security"),
    ("Consultoria e treinamento", "psychology"),
    ("Outros serviços e fornecimentos", "category"),
]

PNCP_CATEGORIAS_ORDEM: List[str] = [nome for nome, _ in PNCP_CATEGORIAS_META]
PNCP_CATEGORIAS_ICONS: Dict[str, str] = dict(PNCP_CATEGORIAS_META)

# Ordem calibrada da regra mais específica para a mais ampla
_CLASSIFICATION_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    (
        "Combustível",
        (
            "combustivel",
            "combustiveis",
            "gasolina",
            "oleo diesel",
            "diesel",
            "etanol",
            "abastecimento de veic",
            "abastecimento da frota",
            "posto de combust",
            "lubrificante",
            "arla 32",
            "gas de cozinha",
            "glp",
        ),
    ),
    (
        "Imóveis alugados e comprados",
        (
            "locacao de imovel",
            "locacao de 1 (um) imovel",
            "locacao de um imovel",
            "locacao do imovel",
            "aluguel de imovel",
            "imovel urbano",
            "imovel situado",
            "imovel localizado",
            "imovel destinado",
            "imovel comercial",
            "imovel residencial",
            "aquisicao de imovel",
            "compra de imovel",
            "desapropriacao",
            "aluguel de sala",
            "locacao de galpao",
            "locacao de predio",
            "locacao de espaco",
        ),
    ),
    (
        "Advocacia e assessoria jurídica",
        (
            "advocacia",
            "advogad",
            "assessoria juridica",
            "consultoria juridica",
            "servicos juridicos",
            "patrocinio juridico",
            "defesa judicial",
            "contencioso",
            "escritorio de advocacia",
            "parecer juridico",
        ),
    ),
    (
        "Publicidade, TV e rádio",
        (
            "publicidade",
            "propaganda",
            "agencia de comunicacao",
            "divulgacao institucional",
            "veiculacao",
            "emissora",
            "radio",
            "televisao",
            "midia",
            "outdoor",
            "campanha publicitaria",
            "jornal impresso",
            "comunicacao social",
            "comunicacao visual",
            "assessoria de imprensa",
            "servicos graficos",
            "servico, graficos",
            "material grafico",
            "grafica",
            "publicacao de atos oficiais",
        ),
    ),
    (
        "Shows e eventos",
        (
            "show ",
            "shows",
            "apresentacao artistica",
            "apresentacao musical",
            "atracao artistica",
            "atracao musical",
            "artista",
            "banda ",
            "cantor",
            "dupla ",
            "palco",
            "sonorizacao",
            "iluminacao cenica",
            "trio eletrico",
            "festejo",
            "carnaval",
            "reveillon",
            "sao joao",
            "arraial",
            "aniversario da cidade",
            "aniversario de imperatriz",
            "festival",
            "evento",
            "eventos",
            "tenda",
            "cache ",
            "espetaculo",
        ),
    ),
    (
        "Alimentação",
        (
            "generos alimenticios",
            "genero alimenticio",
            "alimentacao",
            "alimento",
            "alimentos",
            "cesta basica",
            "cestas basicas",
            "refeicao",
            "refeicoes",
            "marmitex",
            "coffee break",
            "lanche",
            "lanches",
            "agua mineral",
            "cafe ",
            "acucar",
            "hortifruti",
            "carne",
            "frango",
            "panificacao",
            "buffet",
            "cozinha",
            "restaurante",
        ),
    ),
    (
        "Vigilância e segurança",
        (
            "vigilancia",
            "seguranca patrimonial",
            "seguranca armada",
            "seguranca desarmada",
            "vigilante",
            "monitoramento eletronico",
            "cftv",
            "camera de seguranca",
            "alarme",
            "guarda municipal",
            "armamento",
            "municao",
            "colete balistico",
            "videomonitoramento",
            "controle de acesso",
        ),
    ),
    (
        "Tecnologia e sistemas",
        (
            "software",
            "sistema informatizado",
            "sistema de gestao",
            "sistema eletronico",
            "tecnologia da informacao",
            "informatica",
            "licenca de uso",
            "licenciamento",
            "hospedagem",
            "nuvem",
            "cloud",
            "internet",
            "link dedicado",
            "fibra optica",
            "telefonia",
            "telecomunicac",
            "computador",
            "notebook",
            "impressora",
            "toner",
            "servidor de rede",
            "servidor de dados",
            "solucao educacional",
            "solucao tecnologica",
            "plataforma digital",
            "aplicativo",
            "suporte de ti",
            "backup",
            "certificado digital",
            "biometria",
            "geoprocessamento",
            "folha de pagamento",
            "processamento de dados",
            "ferramenta de pesquisa de precos",
            "ferramenta de pesquisas de precos",
            "aeronave remotamente pilotada",
            "drone",
        ),
    ),
    (
        "Limpeza e lixo",
        (
            "residuos solidos",
            "coleta de lixo",
            "limpeza urbana",
            "varricao",
            "capina",
            "rocagem",
            "aterro sanitario",
            "transbordo",
            "lixo hospitalar",
            "residuos de saude",
            "limpeza predial",
            "conservacao e limpeza",
            "material de limpeza",
            "material limpeza",
            "higiene e limpeza",
            "higiene e descar",
            "descartaveis",
            "dedetizacao",
            "desratizacao",
            "sanitizacao",
            "fossa",
            "esgotamento",
            "zeladoria",
        ),
    ),
    (
        "Veículos e transporte",
        (
            "locacao de veiculo",
            "locacao de veiculos",
            "aluguel de veiculo",
            "transporte escolar",
            "transporte coletivo",
            "transporte de passageiros",
            "vale transporte",
            "vale-transporte",
            "fretamento",
            "aquisicao de veiculo",
            "compra de veiculo",
            "ambulancia",
            "onibus",
            "micro-onibus",
            "caminhao",
            "motocicleta",
            "manutencao de veiculo",
            "manutencao da frota",
            "pecas automotivas",
            "pneu",
            "pneus",
            "passagem aerea",
            "passagens aereas",
            "agenciamento de viagens",
            "maquina pesada",
            "retroescavadeira",
            "patrol",
            "motoniveladora",
            "trator",
            "frota",
        ),
    ),
    (
        "Obras e engenharia",
        (
            "pavimentacao",
            "asfalt",
            "drenagem",
            "engenharia",
            "obra ",
            "obras",
            "construcao",
            "reforma",
            "ampliacao",
            "manutencao predial",
            "manutencao preventiva e corretiva",
            "iluminacao publica",
            "terraplanagem",
            "calcamento",
            "bloco intertravado",
            "ponte",
            "bueiro",
            "aduela",
            "praca ",
            "quadra ",
            "arquitetura",
            "topografia",
            "sondagem",
            "material de construcao",
            "cimento",
            "brita",
            "areia",
            "infraestrutura",
            "edificacao",
            "recapeamento",
            "tapa-buraco",
            "sinalizacao viaria",
            "semafor",
        ),
    ),
    (
        "Saúde e remédios",
        (
            "medicamento",
            "medicamentos",
            "remedio",
            "farmac",
            "insumo hospitalar",
            "insumos hospitalares",
            "material hospitalar",
            "hospital",
            "socorrao",
            "upa ",
            "ubs ",
            "saude",
            "odonto",
            "laboratorio",
            "exame",
            "exames",
            "hemodialise",
            "oxigenio medicinal",
            "gases medicinais",
            "cirurg",
            "medico",
            "medicos",
            "enferm",
            "fisioterap",
            "vacina",
            "ortese",
            "protese",
            "equipamento medico",
            "equipamento hospitalar",
            "leito",
            "uti ",
            "clinica",
            "diagnostico",
        ),
    ),
    (
        "Educação e merenda",
        (
            "livro didatico",
            "livros didaticos",
            "material escolar",
            "kit escolar",
            "fardamento escolar",
            "uniforme escolar",
            "mobiliario escolar",
            "escola",
            "escolas",
            "creche",
            "creches",
            "educacao",
            "pedagogic",
            "didatic",
            "ensino",
            "aluno",
            "alunos",
            "professor",
            "robotica",
        ),
    ),
    (
        "Consultoria e treinamento",
        (
            "consultoria",
            "treinamento",
            "capacitacao",
            "curso ",
            "cursos",
            "palestra",
            "oficina ",
            "workshop",
            "assessoria tecnica",
            "assessoria contabil",
            "assessoria administrativa",
            "auditoria",
            "planejamento",
            "estudo tecnico",
            "elaboracao de projeto",
            "formacao continuada",
            "aperfeicoamento",
            "seminario",
            "congresso",
        ),
    ),
]


def normalize_text_for_matching(text: str) -> str:
    """Remove acentos e normaliza espaços/quebras de linha mantendo fronteiras simples."""
    raw = unicodedata.normalize("NFKD", (text or "").lower())
    without_accents = "".join(ch for ch in raw if not unicodedata.combining(ch))
    collapsed = re.sub(r"\s+", " ", without_accents).strip()
    return f" {collapsed} " if collapsed else " "


def is_fornecedor_cpf(cnpj_or_cpf: str) -> bool:
    """Identifica se o documento cadastrado para o fornecedor tem tamanho compatível com CPF."""
    digits = "".join(ch for ch in str(cnpj_or_cpf or "") if ch.isdigit())
    return 1 <= len(digits) <= 11


def classify_pncp_contrato(row: Dict[str, Any]) -> str:
    """Classifica um contrato do PNCP em uma das 14 categorias temáticas ou fallback."""
    obj_text = normalize_text_for_matching(row.get("objeto") or "")
    uni_text = normalize_text_for_matching(row.get("unidade_nome") or "")

    # 1. Merenda e alimentação vinculada à Educação sempre vão para Educação e merenda
    if any(k in obj_text for k in ("merenda", "alimentacao escolar", "pnae")) or (
        ("generos alimenticios" in obj_text or "genero alimenticio" in obj_text)
        and "educacao" in uni_text
    ):
        return "Educação e merenda"

    # 2. Fundação Cultural com contratação artística / cultural / festividade
    if "fundacao cultural" in uni_text and any(
        k in obj_text
        for k in (
            "apresentacao",
            "artist",
            "musical",
            "show",
            "banda",
            "cantor",
            "festejo",
            "evento",
            "sonoriz",
            "palco",
            "carnaval",
            "sao joao",
            "natal",
            "cultural",
        )
    ):
        return "Shows e eventos"

    # Evita que contratos genéricos de mão de obra terceirizada caiam por acidente em Obras
    obj_no_mao = obj_text.replace("mao de obra", " ")

    for categoria, keywords in _CLASSIFICATION_RULES:
        haystack = obj_no_mao if categoria == "Obras e engenharia" else obj_text
        if any(kw in haystack for kw in keywords):
            return categoria

    # 3. Fallback pela secretaria/unidade quando o objeto é genérico
    if "saude" in uni_text:
        return "Saúde e remédios"
    if "educacao" in uni_text:
        return "Educação e merenda"
    if "infraestrutura" in uni_text or "iluminacao" in uni_text:
        return "Obras e engenharia"
    if "fundacao cultural" in uni_text:
        return "Shows e eventos"
    if "seguranca" in uni_text or "guarda" in uni_text:
        return "Vigilância e segurança"

    return "Outros serviços e fornecimentos"


def build_pncp_estatisticas(
    rows: List[Dict[str, Any]], today: Optional[date] = None
) -> Dict[str, Any]:
    """Calcula estatísticas detalhadas, classificação por tema e sinais de auditoria dos contratos."""
    ref_today = today or datetime.now().date()
    today_iso = ref_today.isoformat()

    total_contratos = len(rows)
    valores: List[float] = [float(r.get("valor_global") or 0.0) for r in rows]
    valor_total_global = round(sum(valores), 2)
    ticket_medio = round(valor_total_global / total_contratos, 2) if total_contratos else 0.0
    valor_mediano = round(float(statistics.median(valores)), 2) if valores else 0.0
    maior_contrato_valor = round(max(valores), 2) if valores else 0.0
    menor_contrato_valor = round(min(valores), 2) if valores else 0.0

    # Inicializa todas as 14 categorias oficiais para que sempre apareçam com valor ao lado
    cat_agg: Dict[str, Dict[str, Any]] = {
        cat: {"categoria": cat, "icone": icon, "contratos": 0, "valor_total": 0.0}
        for cat, icon in PNCP_CATEGORIAS_META
    }

    sec_map: Dict[str, Dict[str, Any]] = {}
    forn_map: Dict[str, Dict[str, Any]] = {}
    anos_map: Dict[int, Dict[str, Any]] = {
        yr: {"ano": yr, "contratos": 0, "valor_total": 0.0} for yr in (2026, 2025, 2024)
    }

    faixas_bins = [
        ("Até R$ 50 mil", 0.0, 50_000.0),
        ("R$ 50 mil a R$ 500 mil", 50_000.0, 500_000.0),
        ("R$ 500 mil a R$ 5 MI", 500_000.0, 5_000_000.0),
        ("Acima de R$ 5 MI", 5_000_000.0, float("inf")),
    ]
    faixas_agg: List[Dict[str, Any]] = [
        {"faixa": nome, "contratos": 0, "valor_total": 0.0} for nome, _, _ in faixas_bins
    ]

    cpf_stats = {"contratos": 0, "valor_total": 0.0}
    cnpj_stats = {"contratos": 0, "valor_total": 0.0}

    contratos_ativos = 0
    contratos_retificados = 0
    outliers: List[Dict[str, Any]] = []
    dup_groups: Dict[Tuple[str, float, int], List[Dict[str, Any]]] = {}
    emergenciais_items: List[Dict[str, Any]] = []
    vencendo_items: List[Dict[str, Any]] = []

    enriched_rows: List[Dict[str, Any]] = []

    for r in rows:
        item = dict(r)
        vg = float(item.get("valor_global") or 0.0)
        cat = item.get("categoria_tematica") or classify_pncp_contrato(item)
        item["categoria_tematica"] = cat
        cpf_flag = (
            bool(item["is_cpf"])
            if "is_cpf" in item
            else is_fornecedor_cpf(str(item.get("fornecedor_cnpj") or ""))
        )
        item["is_cpf"] = cpf_flag
        enriched_rows.append(item)

        if bool(item.get("retificado")):
            contratos_retificados += 1

        # 1. Categoria temática
        if cat not in cat_agg:
            cat_agg[cat] = {
                "categoria": cat,
                "icone": PNCP_CATEGORIAS_ICONS.get(cat, "category"),
                "contratos": 0,
                "valor_total": 0.0,
            }
        cat_agg[cat]["contratos"] += 1
        cat_agg[cat]["valor_total"] += vg

        # 2. Secretaria / Unidade
        uni = (item.get("unidade_nome") or item.get("orgao_nome") or "Não informada").strip()
        sigla = (item.get("orgao_sigla") or "ITZ").strip()
        if uni not in sec_map:
            sec_map[uni] = {
                "unidade_nome": uni,
                "orgao_sigla": sigla,
                "contratos": 0,
                "valor_total": 0.0,
            }
        sec_map[uni]["contratos"] += 1
        sec_map[uni]["valor_total"] += vg

        # 3. Faixas de valor
        for idx_f, (_, low, high) in enumerate(faixas_bins):
            if idx_f == len(faixas_bins) - 1:
                in_bin = vg >= low
            else:
                in_bin = low <= vg < high
            if in_bin:
                faixas_agg[idx_f]["contratos"] += 1
                faixas_agg[idx_f]["valor_total"] += vg
                break

        # 4. Natureza CPF vs CNPJ
        if cpf_flag:
            cpf_stats["contratos"] += 1
            cpf_stats["valor_total"] += vg
        else:
            cnpj_stats["contratos"] += 1
            cnpj_stats["valor_total"] += vg

        # 5. Comparativo anual
        try:
            ano_val = int(item.get("ano_contrato") or 0)
        except (TypeError, ValueError):
            ano_val = 0
        if ano_val:
            if ano_val not in anos_map:
                anos_map[ano_val] = {"ano": ano_val, "contratos": 0, "valor_total": 0.0}
            anos_map[ano_val]["contratos"] += 1
            anos_map[ano_val]["valor_total"] += vg

        # 6. Fornecedor
        fcnpj = (item.get("fornecedor_cnpj") or "").strip()
        fnome = (item.get("fornecedor_nome") or "Fornecedor não informado").strip()
        fkey = fcnpj or fnome.upper()
        if fkey not in forn_map:
            forn_map[fkey] = {
                "fornecedor_cnpj": fcnpj,
                "fornecedor_nome": fnome,
                "is_cpf": cpf_flag,
                "total_valor": 0.0,
                "qtd_contratos": 0,
                "orgaos": set(),
            }
        forn_map[fkey]["total_valor"] += vg
        forn_map[fkey]["qtd_contratos"] += 1
        if sigla:
            forn_map[fkey]["orgaos"].add(sigla)

        # 7. Vigência e vencimento
        vig_fim = str(item.get("data_vigencia_fim") or "")[:10]
        if len(vig_fim) == 10:
            if vig_fim >= today_iso:
                contratos_ativos += 1
            try:
                dt_fim = datetime.strptime(vig_fim, "%Y-%m-%d").date()
                dias_rest = (dt_fim - ref_today).days
                if 0 <= dias_rest <= 60:
                    v_copy = dict(item)
                    v_copy["dias_restantes"] = dias_rest
                    vencendo_items.append(v_copy)
            except ValueError:
                pass

        # 8. Alertas: possíveis erros / outliers
        motivo = ""
        if cpf_flag and vg >= 500_000.0:
            motivo = (
                "Contrato em CPF com valor elevado (>= R$ 500 mil) — conferir possível erro de lançamento no PNCP"
            )
        elif vg >= 25_000_000.0:
            motivo = (
                "Contrato de altíssimo vulto (>= R$ 25 milhões) — conferir valor e vigência"
            )
        if motivo:
            o_copy = dict(item)
            o_copy["motivo_alerta"] = motivo
            outliers.append(o_copy)

        # 9. Possíveis duplicidades (mesmo fornecedor + mesmo valor >= 100k + mesmo ano)
        if vg >= 100_000.0 and fkey:
            dkey = (fkey, round(vg, 2), ano_val)
            dup_groups.setdefault(dkey, []).append(item)

        # 10. Emergenciais e dispensas
        obj_norm = normalize_text_for_matching(item.get("objeto") or "")
        if any(
            kw in obj_norm
            for kw in ("emergencial", "carater emergencial", "dispensa", "inexigibilidade", "calamidade")
        ):
            emergenciais_items.append(item)

    # Finaliza categorias (garante sempre as 14 solicitadas; inclui Outros somente se > 0)
    ordem_idx = {nome: i for i, nome in enumerate(PNCP_CATEGORIAS_ORDEM)}
    por_categoria: List[Dict[str, Any]] = []
    for cat_nome, info in cat_agg.items():
        if cat_nome == "Outros serviços e fornecimentos" and info["contratos"] == 0:
            continue
        val_r = round(info["valor_total"], 2)
        cnt = info["contratos"]
        por_categoria.append(
            {
                "categoria": cat_nome,
                "icone": info["icone"],
                "contratos": cnt,
                "valor_total": val_r,
                "ticket_medio": round(val_r / cnt, 2) if cnt else 0.0,
                "participacao_pct": round((val_r / valor_total_global) * 100.0, 1)
                if valor_total_global > 0
                else 0.0,
            }
        )
    por_categoria.sort(
        key=lambda x: (-x["valor_total"], -x["contratos"], ordem_idx.get(x["categoria"], 99))
    )

    # Fornecedores e concentração
    sorted_forn = sorted(forn_map.values(), key=lambda x: x["total_valor"], reverse=True)
    top5_sum = sum(f["total_valor"] for f in sorted_forn[:5])
    top10_sum = sum(f["total_valor"] for f in sorted_forn[:10])
    concentracao_top5_pct = (
        round((top5_sum / valor_total_global) * 100.0, 1) if valor_total_global > 0 else 0.0
    )
    concentracao_top10_pct = (
        round((top10_sum / valor_total_global) * 100.0, 1) if valor_total_global > 0 else 0.0
    )

    top_fornecedores: List[Dict[str, Any]] = [
        {
            "fornecedor_cnpj": f["fornecedor_cnpj"],
            "fornecedor_nome": f["fornecedor_nome"],
            "is_cpf": f["is_cpf"],
            "qtd_contratos": f["qtd_contratos"],
            "total_valor": round(f["total_valor"], 2),
            "participacao_pct": round((f["total_valor"] / valor_total_global) * 100.0, 1)
            if valor_total_global > 0
            else 0.0,
            "orgaos": ", ".join(sorted(f["orgaos"])),
        }
        for f in sorted_forn[:10]
    ]

    # Top 20 maiores contratos
    top_maiores = sorted(
        enriched_rows, key=lambda x: float(x.get("valor_global") or 0.0), reverse=True
    )[:20]

    # Top 15 secretarias
    por_secretaria = [
        {
            "unidade_nome": s["unidade_nome"],
            "orgao_sigla": s["orgao_sigla"],
            "contratos": s["contratos"],
            "valor_total": round(s["valor_total"], 2),
            "participacao_pct": round((s["valor_total"] / valor_total_global) * 100.0, 1)
            if valor_total_global > 0
            else 0.0,
        }
        for s in sorted(sec_map.values(), key=lambda x: x["valor_total"], reverse=True)[:15]
    ]

    # Faixas com %
    faixas_valor = [
        {
            "faixa": f["faixa"],
            "contratos": f["contratos"],
            "valor_total": round(f["valor_total"], 2),
            "participacao_pct": round((f["valor_total"] / valor_total_global) * 100.0, 1)
            if valor_total_global > 0
            else 0.0,
        }
        for f in faixas_agg
    ]

    # Natureza CPF vs CNPJ com %
    natureza_fornecedor = {
        "cpf": {
            "contratos": cpf_stats["contratos"],
            "valor_total": round(cpf_stats["valor_total"], 2),
            "participacao_pct": round((cpf_stats["valor_total"] / valor_total_global) * 100.0, 1)
            if valor_total_global > 0
            else 0.0,
        },
        "cnpj": {
            "contratos": cnpj_stats["contratos"],
            "valor_total": round(cnpj_stats["valor_total"], 2),
            "participacao_pct": round((cnpj_stats["valor_total"] / valor_total_global) * 100.0, 1)
            if valor_total_global > 0
            else 0.0,
        },
    }

    comparativo_anual = [
        {
            "ano": yr,
            "contratos": anos_map[yr]["contratos"],
            "valor_total": round(anos_map[yr]["valor_total"], 2),
            "ticket_medio": round(anos_map[yr]["valor_total"] / anos_map[yr]["contratos"], 2)
            if anos_map[yr]["contratos"]
            else 0.0,
        }
        for yr in sorted(anos_map.keys(), reverse=True)
    ]

    outliers.sort(key=lambda x: float(x.get("valor_global") or 0.0), reverse=True)
    emergenciais_items.sort(key=lambda x: float(x.get("valor_global") or 0.0), reverse=True)
    vencendo_items.sort(key=lambda x: (x.get("dias_restantes", 999), -float(x.get("valor_global") or 0.0)))

    duplicidades = [
        {
            "fornecedor_cnpj": grp[0].get("fornecedor_cnpj", ""),
            "fornecedor_nome": grp[0].get("fornecedor_nome", ""),
            "valor_global": val_g,
            "qtd": len(grp),
            "ano_contrato": ano_g,
            "controles": [x.get("numero_controle_pncp", "") for x in grp],
        }
        for (_, val_g, ano_g), grp in dup_groups.items()
        if len(grp) >= 2
    ]
    duplicidades.sort(key=lambda x: x["valor_global"] * x["qtd"], reverse=True)

    return {
        "resumo_geral": {
            "total_contratos": total_contratos,
            "valor_total_global": valor_total_global,
            "ticket_medio": ticket_medio,
            "valor_mediano": valor_mediano,
            "maior_contrato_valor": maior_contrato_valor,
            "menor_contrato_valor": menor_contrato_valor,
            "fornecedores_unicos": len(forn_map),
            "concentracao_top5_pct": concentracao_top5_pct,
            "concentracao_top10_pct": concentracao_top10_pct,
            "contratos_ativos": contratos_ativos,
            "contratos_vencendo_60d": len(vencendo_items),
            "contratos_retificados": contratos_retificados,
        },
        "por_categoria": por_categoria,
        "top_maiores_contratos": top_maiores,
        "por_secretaria": por_secretaria,
        "faixas_valor": faixas_valor,
        "natureza_fornecedor": natureza_fornecedor,
        "comparativo_anual": comparativo_anual,
        "top_fornecedores": top_fornecedores,
        "alertas_auditoria": {
            "possiveis_erros_outliers": outliers[:12],
            "possiveis_duplicidades": duplicidades[:10],
            "emergenciais_ou_dispensas": {
                "total_contratos": len(emergenciais_items),
                "valor_total": round(
                    sum(float(x.get("valor_global") or 0.0) for x in emergenciais_items), 2
                ),
                "items": emergenciais_items[:10],
            },
            "vencendo_em_60d": {
                "total_contratos": len(vencendo_items),
                "valor_total": round(
                    sum(float(x.get("valor_global") or 0.0) for x in vencendo_items), 2
                ),
                "items": vencendo_items[:10],
            },
        },
    }
