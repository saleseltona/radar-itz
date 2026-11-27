-- ============================================================================
-- Radar de Pautas de Imperatriz (MA) — Schema PostgreSQL para Supabase
-- Executar no SQL Editor do Supabase (idempotente: pode re-executar sem risco).
-- Inclui chaves únicas naturais para carga incremental sem duplicatas
-- e Row Level Security (RLS) habilitado em todas as tabelas (acesso exclusivo
-- pelo backend serverless via SUPABASE_SERVICE_ROLE_KEY).
-- ============================================================================

-- 1. Fontes monitoradas
CREATE TABLE IF NOT EXISTS public.fontes (
    id BIGSERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    prioridade TEXT NOT NULL,
    temas TEXT NOT NULL,
    situacao_funcionamento TEXT DEFAULT 'Cadastrada',
    ultima_consulta TEXT DEFAULT '',
    ultima_coleta_sucesso TEXT DEFAULT '',
    metodo_acesso TEXT DEFAULT 'HTTP Direto / Verificador',
    observacoes_limitacoes TEXT DEFAULT '',
    erro_identificado TEXT DEFAULT '',
    proxima_acao_recomendada TEXT DEFAULT ''
);

-- 2. Histórico de execuções do radar
CREATE TABLE IF NOT EXISTS public.execucoes (
    id_execucao TEXT PRIMARY KEY,
    data_horario TEXT NOT NULL,
    modo TEXT NOT NULL DEFAULT 'manual',
    fontes_consultadas INTEGER DEFAULT 0,
    fontes_com_falha INTEGER DEFAULT 0,
    paginas_lidas INTEGER DEFAULT 0,
    pautas_novas INTEGER DEFAULT 0,
    pautas_atualizadas INTEGER DEFAULT 0,
    duplicidades_eliminadas INTEGER DEFAULT 0,
    itens_descartados INTEGER DEFAULT 0,
    duracao_segundos DOUBLE PRECISION DEFAULT 0.0,
    situacao_execucao TEXT NOT NULL DEFAULT 'EM_ANDAMENTO',
    resumo_json TEXT DEFAULT '{}'
);

-- 3. Páginas coletadas nas varreduras
CREATE TABLE IF NOT EXISTS public.paginas_coletadas (
    id BIGSERIAL PRIMARY KEY,
    id_execucao TEXT NOT NULL,
    url TEXT NOT NULL,
    fonte_nome TEXT NOT NULL,
    titulo TEXT NOT NULL,
    data_publicacao TEXT DEFAULT '',
    data_acontecimento TEXT DEFAULT '',
    data_coleta TEXT NOT NULL,
    metodo_coleta TEXT NOT NULL,
    status_leitura TEXT NOT NULL,
    conteudo_hash TEXT NOT NULL,
    conteudo_extraido TEXT NOT NULL,
    UNIQUE (id_execucao, url, conteudo_hash)
);

-- 4. Pautas jornalísticas consolidadas
CREATE TABLE IF NOT EXISTS public.pautas (
    id_pauta TEXT PRIMARY KEY,
    prioridade TEXT NOT NULL,
    pontuacao_editorial INTEGER NOT NULL,
    justificativa_pontuacao TEXT NOT NULL,
    emergencia_sinalizada INTEGER DEFAULT 0,
    categoria TEXT NOT NULL,
    tema_especifico TEXT NOT NULL,
    titulo_jornalistico TEXT NOT NULL,
    titulo_normalizado TEXT NOT NULL,
    resumo_3_linhas TEXT NOT NULL,
    descricao_detalhada TEXT NOT NULL,
    fonte_principal TEXT NOT NULL,
    url_original TEXT NOT NULL UNIQUE,
    veiculo_instituicao TEXT NOT NULL,
    autor TEXT DEFAULT '',
    data_publicacao TEXT DEFAULT '',
    data_acontecimento TEXT DEFAULT '',
    data_hora_coleta TEXT NOT NULL,
    fontes_complementares TEXT DEFAULT '',
    urls_complementares TEXT DEFAULT '',
    evidencias_verificadas TEXT NOT NULL,
    angulo_jornalistico TEXT NOT NULL,
    publico_afetado TEXT NOT NULL,
    bairros_locais_instituicoes TEXT NOT NULL,
    formato_sugerido TEXT NOT NULL,
    nivel_verificacao TEXT NOT NULL,
    informacoes_pendentes TEXT DEFAULT '',
    proxima_acao_recomendada TEXT DEFAULT '',
    situacao_editorial TEXT NOT NULL,
    id_pauta_relacionada TEXT DEFAULT '',
    identificadores_documentos TEXT DEFAULT '',
    detalhes_denuncia_json TEXT DEFAULT '{}',
    motivo_selecao TEXT DEFAULT '',
    imagem_capa TEXT DEFAULT '',
    id_execucao_origem TEXT NOT NULL,
    id_execucao_ultima_atualizacao TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pautas_data_pub ON public.pautas (data_publicacao DESC);
CREATE INDEX IF NOT EXISTS idx_pautas_score ON public.pautas (pontuacao_editorial DESC);

-- 4b. Pautas arquivadas/ocultadas da capa e do feed principal
CREATE TABLE IF NOT EXISTS public.pautas_arquivadas (
    id_pauta TEXT PRIMARY KEY,
    motivo TEXT NOT NULL DEFAULT 'Arquivada pela redação',
    atualizado_em TEXT NOT NULL
);

-- 5. Contratos públicos do PNCP (2024–2026)
CREATE TABLE IF NOT EXISTS public.pncp_contratos (
    numero_controle_pncp TEXT PRIMARY KEY,
    orgao_cnpj TEXT NOT NULL,
    orgao_sigla TEXT NOT NULL,
    orgao_nome TEXT NOT NULL,
    unidade_nome TEXT DEFAULT '',
    ano_contrato INTEGER NOT NULL,
    numero_contrato TEXT DEFAULT '',
    processo TEXT DEFAULT '',
    tipo_contrato TEXT DEFAULT '',
    fornecedor_cnpj TEXT DEFAULT '',
    fornecedor_nome TEXT DEFAULT '',
    objeto TEXT DEFAULT '',
    valor_inicial DOUBLE PRECISION DEFAULT 0.0,
    valor_global DOUBLE PRECISION DEFAULT 0.0,
    valor_acumulado DOUBLE PRECISION DEFAULT 0.0,
    data_assinatura TEXT DEFAULT '',
    data_publicacao TEXT DEFAULT '',
    data_vigencia_inicio TEXT DEFAULT '',
    data_vigencia_fim TEXT DEFAULT '',
    url_pncp TEXT DEFAULT '',
    categoria_tematica TEXT NOT NULL DEFAULT '',
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pncp_ano ON public.pncp_contratos (ano_contrato);
CREATE INDEX IF NOT EXISTS idx_pncp_orgao ON public.pncp_contratos (orgao_cnpj);
CREATE INDEX IF NOT EXISTS idx_pncp_sigla ON public.pncp_contratos (orgao_sigla);
CREATE INDEX IF NOT EXISTS idx_pncp_pub ON public.pncp_contratos (data_publicacao DESC);
CREATE INDEX IF NOT EXISTS idx_pncp_valor ON public.pncp_contratos (valor_global DESC);
CREATE INDEX IF NOT EXISTS idx_pncp_categoria ON public.pncp_contratos (categoria_tematica);

-- 6. Retificações manuais verificadas de valores do PNCP
CREATE TABLE IF NOT EXISTS public.pncp_retificacoes (
    numero_controle_pncp TEXT PRIMARY KEY,
    valor_original_pncp DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    valor_corrigido DOUBLE PRECISION NOT NULL,
    justificativa TEXT NOT NULL DEFAULT '',
    atualizado_em TEXT NOT NULL
);

-- 6b. Contratos PNCP marcados como duplicados / ignorados
CREATE TABLE IF NOT EXISTS public.pncp_ignorados (
    numero_controle_pncp TEXT PRIMARY KEY,
    motivo TEXT NOT NULL DEFAULT 'Contrato duplicado no PNCP',
    atualizado_em TEXT NOT NULL
);

-- 7. Evidências documentais vinculadas às pautas
CREATE TABLE IF NOT EXISTS public.evidencias (
    id BIGSERIAL PRIMARY KEY,
    id_pauta TEXT NOT NULL,
    tipo_evidencia TEXT NOT NULL,
    descricao TEXT NOT NULL,
    referencia_url_ou_doc TEXT NOT NULL,
    data_registro TEXT NOT NULL,
    UNIQUE (id_pauta, tipo_evidencia, referencia_url_ou_doc)
);

-- 8. Histórico de consolidação/atualização de pautas
CREATE TABLE IF NOT EXISTS public.historico_atualizacoes (
    id BIGSERIAL PRIMARY KEY,
    id_pauta TEXT NOT NULL,
    id_execucao TEXT NOT NULL,
    data_atualizacao TEXT NOT NULL,
    tipo_mudanca TEXT NOT NULL,
    descricao_mudanca TEXT NOT NULL,
    estado_anterior_json TEXT NOT NULL,
    UNIQUE (id_pauta, id_execucao, data_atualizacao, tipo_mudanca)
);

-- 9. Pendências e dossiês de apuração jornalística
CREATE TABLE IF NOT EXISTS public.pendencias_apuracao (
    id BIGSERIAL PRIMARY KEY,
    id_pauta TEXT NOT NULL UNIQUE,
    questao_pendente TEXT NOT NULL,
    documento_necessario TEXT NOT NULL,
    instituicao_ou_pessoa_a_consultar TEXT NOT NULL,
    evidencia_ja_disponivel TEXT NOT NULL,
    proxima_acao TEXT NOT NULL,
    status_apuracao TEXT NOT NULL,
    data_atualizacao TEXT NOT NULL
);

-- 10. Itens descartados editorialmente
CREATE TABLE IF NOT EXISTS public.itens_descartados (
    id BIGSERIAL PRIMARY KEY,
    id_execucao TEXT NOT NULL,
    url TEXT NOT NULL,
    titulo TEXT NOT NULL,
    motivo_descarte TEXT NOT NULL,
    data_registro TEXT NOT NULL,
    UNIQUE (id_execucao, url)
);

-- 11. Log de erros operacionais e de rede
CREATE TABLE IF NOT EXISTS public.erros_coleta (
    id BIGSERIAL PRIMARY KEY,
    id_execucao TEXT NOT NULL,
    componente TEXT NOT NULL,
    alvo_url TEXT NOT NULL,
    mensagem_erro TEXT NOT NULL,
    data_registro TEXT NOT NULL,
    UNIQUE (id_execucao, componente, alvo_url, data_registro)
);

-- 12. Portal Transparência Fiorilli — Receitas (Orçamentária, União e Estado)
CREATE TABLE IF NOT EXISTS public.fiorilli_receitas (
    chave_receita TEXT PRIMARY KEY,
    ano INTEGER NOT NULL,
    tipo_fonte TEXT NOT NULL,
    ordem TEXT DEFAULT '',
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    eh_raiz INTEGER NOT NULL DEFAULT 0,
    previsao_inicial DOUBLE PRECISION DEFAULT 0.0,
    previsao_atualizada DOUBLE PRECISION DEFAULT 0.0,
    arrecadado_periodo DOUBLE PRECISION DEFAULT 0.0,
    arrecadado_total DOUBLE PRECISION DEFAULT 0.0,
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fiorilli_rec_ano_fonte ON public.fiorilli_receitas (ano, tipo_fonte);

-- 13. Portal Transparência Fiorilli — Despesas por Órgão
CREATE TABLE IF NOT EXISTS public.fiorilli_despesas_orgaos (
    chave_orgao TEXT PRIMARY KEY,
    ano INTEGER NOT NULL,
    empresa TEXT DEFAULT '',
    codigo TEXT NOT NULL,
    orgao_nome TEXT NOT NULL,
    dotacao_inicial DOUBLE PRECISION DEFAULT 0.0,
    alteracoes DOUBLE PRECISION DEFAULT 0.0,
    dotacao_atualizada DOUBLE PRECISION DEFAULT 0.0,
    empenhado DOUBLE PRECISION DEFAULT 0.0,
    liquidado DOUBLE PRECISION DEFAULT 0.0,
    pago DOUBLE PRECISION DEFAULT 0.0,
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fiorilli_desp_org_ano ON public.fiorilli_despesas_orgaos (ano);

-- 14. Portal Transparência Fiorilli — Despesas por Fornecedor (CNPJ/CPF)
CREATE TABLE IF NOT EXISTS public.fiorilli_despesas_fornecedores (
    chave_fornecedor TEXT PRIMARY KEY,
    ano INTEGER NOT NULL,
    empresa TEXT DEFAULT '',
    codigo_fornecedor TEXT DEFAULT '',
    fornecedor_doc TEXT DEFAULT '',
    fornecedor_nome TEXT NOT NULL,
    cidade TEXT DEFAULT '',
    empenhado DOUBLE PRECISION DEFAULT 0.0,
    liquidado DOUBLE PRECISION DEFAULT 0.0,
    pago DOUBLE PRECISION DEFAULT 0.0,
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fiorilli_desp_forn_ano ON public.fiorilli_despesas_fornecedores (ano);
CREATE INDEX IF NOT EXISTS idx_fiorilli_desp_forn_doc ON public.fiorilli_despesas_fornecedores (fornecedor_doc);

-- 15. Portal Transparência Fiorilli — Contratos, Dispensas e Inexigibilidades
CREATE TABLE IF NOT EXISTS public.fiorilli_contratos (
    chave_contrato TEXT PRIMARY KEY,
    ano INTEGER NOT NULL,
    empresa TEXT DEFAULT '',
    entidade TEXT DEFAULT '',
    codigo TEXT DEFAULT '',
    contrato_num TEXT DEFAULT '',
    processo_licitacao TEXT DEFAULT '',
    processo_adm TEXT DEFAULT '',
    fund_legal TEXT DEFAULT '',
    modalidade TEXT DEFAULT '',
    tipo_contratacao TEXT DEFAULT '',
    eh_contratacao_direta INTEGER NOT NULL DEFAULT 0,
    fornecedor_doc TEXT DEFAULT '',
    fornecedor_nome TEXT DEFAULT '',
    objeto TEXT DEFAULT '',
    secretaria_gestora TEXT DEFAULT '',
    categoria_tematica TEXT DEFAULT '',
    valor_contrato DOUBLE PRECISION DEFAULT 0.0,
    valor_aditado DOUBLE PRECISION DEFAULT 0.0,
    valor_efetivo DOUBLE PRECISION DEFAULT 0.0,
    empenhado DOUBLE PRECISION DEFAULT 0.0,
    liquidado DOUBLE PRECISION DEFAULT 0.0,
    saldo_empenhar DOUBLE PRECISION DEFAULT 0.0,
    data_assinatura TEXT DEFAULT '',
    data_publicacao TEXT DEFAULT '',
    vigencia_inicio TEXT DEFAULT '',
    vigencia_fim TEXT DEFAULT '',
    data_anulacao TEXT DEFAULT '',
    anulado INTEGER NOT NULL DEFAULT 0,
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fiorilli_con_ano ON public.fiorilli_contratos (ano);
CREATE INDEX IF NOT EXISTS idx_fiorilli_con_doc ON public.fiorilli_contratos (fornecedor_doc);
CREATE INDEX IF NOT EXISTS idx_fiorilli_con_direta ON public.fiorilli_contratos (eh_contratacao_direta);

-- ============================================================================
-- Segurança: Row Level Security (RLS)
-- O backend serverless e o script CLI usam SUPABASE_SERVICE_ROLE_KEY, que
-- possui privilégio administrativo. Com RLS ativado e sem policies públicas,
-- chaves anon/públicas não conseguem ler nem adulterar os dados diretamente.
-- ============================================================================
ALTER TABLE public.fontes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.execucoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paginas_coletadas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pautas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pautas_arquivadas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pncp_contratos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pncp_retificacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pncp_ignorados ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.evidencias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.historico_atualizacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pendencias_apuracao ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.itens_descartados ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.erros_coleta ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fiorilli_receitas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fiorilli_despesas_orgaos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fiorilli_despesas_fornecedores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fiorilli_contratos ENABLE ROW LEVEL SECURITY;

-- Sem privilégios de tabela para anon/authenticated (nem nas tabelas futuras).
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
