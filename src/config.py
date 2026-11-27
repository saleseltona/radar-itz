"""
Configurações centrais, consultas obrigatórias, pesos editoriais e proteção de credenciais
do sistema Radar de Pautas de Imperatriz, Maranhão.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
IS_RENDER = bool(os.environ.get("RENDER"))

BUNDLED_DATA_DIR = BASE_DIR / "data"
BUNDLED_OUTPUT_DIR = BASE_DIR / "output"
BUNDLED_FONTES_CSV_PATH = BASE_DIR / "fontes.csv"
VERIFIED_CORPUS_PATH = BUNDLED_DATA_DIR / "coleta_verificada_real.json"

DATA_DIR = BUNDLED_DATA_DIR
OUTPUT_DIR = BUNDLED_OUTPUT_DIR
FONTES_CSV_PATH = BUNDLED_FONTES_CSV_PATH

DB_PATH = DATA_DIR / "radar_imperatriz.db"
LOCK_FILE_PATH = DATA_DIR / "scheduler.lock"


def ensure_runtime_files() -> None:
    """Garante que os diretórios graváveis de dados e saída existam."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_dotenv_file(dotenv_path: Path | None = None) -> None:
    """Carrega variáveis de um arquivo .env local sem dependências externas."""
    target = dotenv_path or (BASE_DIR / ".env")
    if not target.exists():
        return
    for raw_line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv_file()

# Pesos exatos do Sistema de Pontuação (Seção 8 - Total: 100 pontos)
PESOS_PONTUACAO: Dict[str, int] = {
    "impacto_populacao": 25,
    "atualidade_novidade": 20,
    "interesse_publico": 15,
    "qualidade_evidencias": 20,
    "potencial_desenvolvimento": 10,
    "exclusividade_pouco_explorada": 10,
}

# Consultas obrigatórias definidas na Seção 4
CONSULTAS_OBRIGATORIAS: List[str] = [
    "Imperatriz hoje",
    "Imperatriz MA notícias",
    "Imperatriz denúncia",
    "Imperatriz operação policial",
    "Imperatriz acidente",
    "Imperatriz saúde",
    "Imperatriz educação",
    "Imperatriz obras públicas",
    "Imperatriz água e saneamento",
    "Imperatriz energia elétrica",
    "Imperatriz trânsito",
    "Prefeitura de Imperatriz",
    "Câmara Municipal de Imperatriz",
    "Defesa Civil de Imperatriz",
    "Tribunal de Contas Maranhão Imperatriz",
    "Imperatriz licitação",
    "Imperatriz contratos públicos",
    "Imperatriz concurso público",
    "Imperatriz diário oficial",
    "Imperatriz transporte público",
    "Imperatriz hospitais e unidades de saúde",
    "Imperatriz escolas e creches",
    "Imperatriz meio ambiente",
    "Imperatriz alagamento e drenagem",
    "Imperatriz emprego e economia",
    "Imperatriz cultura e eventos",
    "Imperatriz decisões judiciais",
    "Imperatriz dados públicos",
    "Imperatriz obras paralisadas",
    "Imperatriz gastos públicos",
    "Imperatriz reclamação de moradores",
    "Imperatriz interdição e emergência",
]

# Consultas derivadas de temas, órgãos, bairros e contratos locais relevantes
CONSULTAS_DERIVADAS: List[str] = [
    "Imperatriz SINFRA pavimentação contrato TCE-MA",
    "Imperatriz Gaeco MPMA operação desvios",
    "Imperatriz 2ª Vara da Fazenda Pública dano moral coletivo",
    "Imperatriz Denarc Operação Vips prisão",
    "Imperatriz BR-010 acidente Corpo de Bombeiros",
    "Imperatriz Riacho Bacuri Capivara Cacau drenagem aduelas",
    "Imperatriz Novo PAC Saúde UBS Itamar Guará Planalto",
    "Imperatriz Salimp Academia Imperatrizense de Letras 2026",
    "Imperatriz Ratrans transporte coletivo sentença judicial",
    "Imperatriz Pregão Eletrônico SEMUS EJA robótica",
]

# Esquema fixo de colunas de pautas_novas.csv baseado na Seção 9
COLUNAS_PAUTAS_CSV: List[str] = [
    "id_pauta",
    "prioridade",
    "pontuacao_editorial",
    "categoria",
    "tema_especifico",
    "titulo_jornalistico",
    "resumo_3_linhas",
    "descricao_detalhada",
    "fonte_principal",
    "url_original",
    "veiculo_instituicao",
    "autor",
    "data_publicacao",
    "data_acontecimento",
    "data_hora_coleta",
    "fontes_complementares",
    "urls_complementares",
    "evidencias_verificadas",
    "angulo_jornalistico",
    "publico_afetado",
    "bairros_locais_instituicoes",
    "formato_sugerido",
    "nivel_verificacao",
    "informacoes_pendentes",
    "proxima_acao_recomendada",
    "situacao_editorial",
    "id_pauta_relacionada",
]

COLUNAS_HISTORICO_EXECUCOES: List[str] = [
    "id_execucao",
    "data_horario",
    "fontes_consultadas",
    "fontes_com_falha",
    "paginas_lidas",
    "pautas_novas",
    "pautas_atualizadas",
    "duplicidades_eliminadas",
    "itens_descartados",
    "duracao_segundos",
    "situacao_execucao",
]

COLUNAS_FONTES_STATUS: List[str] = [
    "nome",
    "url",
    "ultima_tentativa",
    "ultima_coleta_bem_sucedida",
    "status",
    "metodo_acesso",
    "erro_identificado",
    "proxima_acao_recomendada",
]

COLUNAS_PENDENCIAS_APURACAO: List[str] = [
    "id_pauta",
    "questao_pendente",
    "documento_necessario",
    "instituicao_ou_pessoa_a_consultar",
    "evidencia_ja_disponivel",
    "proxima_acao",
    "status_apuracao",
]

FORMATO_SUGERIDO_VALIDOS: List[str] = [
    "Matéria jornalística",
    "Reels",
    "Carrossel",
    "Enquete",
    "Serviço público",
    "Linha do tempo",
    "Análise de dados",
    "Denúncia em apuração",
    "Entrevista",
    "Reportagem investigativa",
    "Nota urgente",
]

CATEGORIAS_RELATORIO: List[str] = [
    "Pautas urgentes",
    "Pautas de serviço público",
    "Pautas de denúncia e apuração",
    "Pautas de cultura, economia e cidade",
]

# Órgãos municipais oficiais de Imperatriz (MA) monitorados no PNCP (2024-2026)
PNCP_ORGAOS_IMPERATRIZ: List[Dict[str, str]] = [
    {"cnpj": "06158455000116", "sigla": "PMI", "nome": "Prefeitura Municipal de Imperatriz"},
    {"cnpj": "69555019000109", "sigla": "CMI", "nome": "Câmara Municipal de Imperatriz"},
    {"cnpj": "00939023000166", "sigla": "FMS", "nome": "Fundo Municipal de Saúde de Imperatriz"},
    {"cnpj": "07801241000189", "sigla": "FME", "nome": "Fundo Municipal de Educação de Imperatriz"},
]
PNCP_ANOS_MONITORADOS: List[int] = [2024, 2025, 2026]
ANA_ESTACAO_IMPERATRIZ_CODIGO = "23800000"

# Portal Transparência Fiorilli de Imperatriz (Dados Abertos JSON)
FIORILLI_BASE_URL = os.environ.get(
    "FIORILLI_BASE_URL",
    "http://scpi3.adtrcloud.com.br:8079/transparencia",
).strip().rstrip("/")
FIORILLI_DEFAULT_YEARS: Tuple[int, ...] = (2025, 2026)
FIORILLI_DEFAULT_EMPRESA = os.environ.get("FIORILLI_DEFAULT_EMPRESA", "1").strip() or "1"

SENSITIVE_ENV_VARS = (
    "GOOGLE_SEARCH_API_KEY",
    "GOOGLE_SEARCH_CX",
    "SERPAPI_KEY",
    "BING_SEARCH_API_KEY",
    "INSTAGRAM_GRAPH_TOKEN",
    "ANA_TELEMETRIA_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_KEY",
    "SUPABASE_ANON_KEY",
)


def get_supabase_config() -> Tuple[str, str]:
    """Retorna (url, service_role_key) do Supabase a partir das variáveis de ambiente."""
    url = (
        os.environ.get("SUPABASE_URL")
        or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        or ""
    ).strip().rstrip("/")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SECRET_KEY")
        or os.environ.get("SUPABASE_KEY")
        or ""
    ).strip()
    return url, key


def is_supabase_configured() -> bool:
    """Indica se o backend Supabase deve ser utilizado nesta execução."""
    forced = os.environ.get("RADAR_STORAGE_BACKEND", "auto").strip().lower()
    if forced == "sqlite":
        return False
    url, key = get_supabase_config()
    return bool(url and key)


def mask_secrets(text: str) -> str:
    """Remove ou mascara qualquer chave de API ou token presente em textos/logs."""
    if not text:
        return ""
    sanitized = str(text)
    for env_key in SENSITIVE_ENV_VARS:
        secret_val = os.environ.get(env_key, "").strip()
        if secret_val and len(secret_val) >= 4:
            sanitized = sanitized.replace(secret_val, "***REDACTED***")
    # Mascara padrões de tokens em URLs (key=..., access_token=..., api_key=...)
    sanitized = re.sub(
        r"((?:api_?key|access_?token|key|token)=)([^&\s\"']+)",
        r"\1***REDACTED***",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


def get_configured_integrations() -> Dict[str, bool]:
    """Informa quais integrações externas via chave de ambiente estão configuradas."""
    return {
        "google_custom_search": bool(
            os.environ.get("GOOGLE_SEARCH_API_KEY") and os.environ.get("GOOGLE_SEARCH_CX")
        ),
        "serpapi": bool(os.environ.get("SERPAPI_KEY")),
        "bing_search": bool(os.environ.get("BING_SEARCH_API_KEY")),
        "instagram_graph_api": bool(os.environ.get("INSTAGRAM_GRAPH_TOKEN")),
        "ana_telemetria": bool(os.environ.get("ANA_TELEMETRIA_TOKEN")),
        "supabase": is_supabase_configured(),
    }
