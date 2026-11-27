# AGENTS.md

## Visão geral

Python 3, majoritariamente stdlib. Executa coleta, scoring, deduplicação e geração de relatórios editoriais do Radar de Pautas de Imperatriz (MA).

## Comandos

```bash
python main.py
python main.py --delimiter ";"
python main.py --add-source "Nome da Fonte" "Tipo" "https://url-da-fonte.ma.gov.br/" "Alta" "tema1;tema2"
python main.py --status
python main.py --mode scheduled --interval 3600
python app.py
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/supabase_sync.py --check
python scripts/supabase_sync.py --dry-run
python scripts/supabase_sync.py
python scripts/backfill_evidencias.py [--dry-run]
```

`main.py --mode` aceita `manual` (padrão) ou `scheduled`; `--interval` usa 3600s por padrão. `--status` mostra backend, caminho do banco e totais.

## Arquitetura

- `main.py`: CLI e orquestração manual/agendada.
- `src/config.py`: paths, variáveis de ambiente, backend SQLite/Supabase, pesos de scoring e máscara de segredos.
- `src/web_server.py`: rotas HTTP da API e do dashboard.
- `app.py`: servidor web local/Render, porta 8000 por padrão.
- `scripts/supabase_sync.py`: sincronização incremental SQLite → Supabase.
- `supabase/schema.sql`: schema Supabase idempotente.
- `tests/test_radar.py`: suíte `unittest`.
- `fontes.csv`, `data/`, `output/`, `static/`: entrada, estado e saídas.

## Rotas HTTP

GET: `/`, `/dashboard`, `/api/pautas`, `/api/pautas/arquivadas`, `/api/stats`, `/api/telemetria`, `/api/clipping`, `/api/export/csv`, `/export/pautas.csv`, `/api/fontes`, `/api/pendencias`, `/api/pncp/contratos`, `/api/pncp/resumo`, `/api/pncp/estatisticas`, `/api/pncp/export/csv`, `/api/pncp/retificacoes`, `/api/pncp/ignorados`, `/api/transparencia/resumo`, `/api/transparencia/cruzamento`, `/api/transparencia/dispensas`, `/api/transparencia/export/csv`.

POST: `/api/executar`, `/api/pncp/sync`, `/api/transparencia/sync`, `/api/pncp/retificar`, `/api/pncp/retificacoes/sync`, `/api/pncp/ignorar`, `/api/pautas/arquivar`, `/api/fontes`.

## Deploy

- `python app.py` serve `http://localhost:8000`.
- Docker: imagem `python:3.11-slim`, `PORT=8000`, `HOST=0.0.0.0`, `CMD ["python", "app.py"]`; prover `PORT`/`HOST` via ambiente quando necessário.
- Render é o único host em nuvem (serviço `radar-itz-web`, https://radar-itz-web.onrender.com). Não há mais Vercel.
- Render: disco temporário no plano free; sem `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` o SQLite se perde a cada reinício (`IS_RENDER` gera aviso no sync PNCP).
- Render: `startCommand: python app.py`, `PORT=10000`, `buildCommand: ""`, plano free.

## Configuração

Variáveis de coleta: `RADAR_TIMEOUT_SECONDS=15`, `RADAR_USER_AGENT`, `RADAR_CSV_DELIMITER=","`, `RADAR_SCHEDULE_TIMES="07:00,12:00,18:00"`.

Checagem de rede: antes da coleta, `main.py` testa DNS; sem rede a execução termina como `SEM_REDE` com um único erro (`RADAR_SKIP_NETWORK_CHECK=1` desliga).

Instagram: só coleta com `INSTAGRAM_GRAPH_TOKEN` + `INSTAGRAM_BUSINESS_ACCOUNT_ID` (Graph API Business Discovery, `src/instagram_client.py`); funciona apenas para perfis Empresa/Criador. Versão em `INSTAGRAM_GRAPH_API_VERSION` (padrão `v27.0`); tokens `IGAA...` (Login do Instagram) são recusados com mensagem clara, pois o Business Discovery exige token de usuário do Facebook (`EAA...`). Configuração do app Meta em `docs/instagram-graph-api.md`. Sem as duas variáveis, a fonte fica "Aguardando credencial" e não gera erro em `erros_coleta`.

Evidências: cada item de `pautas.evidencias_verificadas` é gravado também na tabela `evidencias` (`src/evidence_store.py`).

Segurança: `RADAR_ADMIN_TOKEN` protege todas as rotas POST e a reidratação por headers/cookies (header `X-Radar-Token` ou `Authorization: Bearer`). Sem ele, só o acesso local direto pode gravar. `/static/` só serve arquivos dentro de `static/`.

Opcionais: `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `SERPAPI_KEY`, `BING_SEARCH_API_KEY`, `INSTAGRAM_GRAPH_TOKEN`, `INSTAGRAM_BUSINESS_ACCOUNT_ID`, `INSTAGRAM_GRAPH_API_VERSION`, `ANA_TELEMETRIA_TOKEN`.

Busca: `src/query_runner.py` executa cada consulta de `CONSULTAS_OBRIGATORIAS` (32), `CONSULTAS_DERIVADAS` e as adicionais do painel no Google News RSS (`when:14d`), com pausa `RADAR_QUERY_DELAY_SECONDS` (padrão 0.5) e orçamento `RADAR_QUERY_BUDGET_SECONDS` (padrão 45). HTTP 429/503 interrompe as restantes. O resultado de cada consulta vai para `output/consultas_status.csv`; falhas e consultas não executadas vão para `erros_coleta` (componente `BuscaGoogleNewsRSS`). As 5 consultas fixas de `DEFAULT_RSS_QUERIES` em `live_news.py` só rodam quando `fetch_recent_news` é chamado direto.

Supabase: defina `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`; `RADAR_STORAGE_BACKEND=auto|supabase|sqlite` (`sqlite` força SQLite; `auto` exige URL+chave). O código também reconhece aliases `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_SECRET_KEY` e `SUPABASE_KEY`. `SUPABASE_ANON_KEY` é tratado como segredo.

Segurança: `.env`, `mcp_config.json` e `data/scheduler.lock` estão no `.gitignore`. Nunca documentar ou commitar valores de chaves/tokens. `src/config.py` aplica máscara `***REDACTED***` ao expor configurações sensíveis.

## Supabase

1. Aplicar `supabase/schema.sql`.
2. `python scripts/supabase_sync.py --check`
3. `python scripts/supabase_sync.py --dry-run`
4. `python scripts/supabase_sync.py`

O padrão é sincronizar apenas novos registros, preservando correções feitas no Supabase. Opções úteis: `--only-pncp`, `--tables pncp_retificacoes,pncp_contratos`. Tabelas também testadas para Fiorilli: `fiorilli_receitas`, `fiorilli_despesas_orgaos`, `fiorilli_despesas_fornecedores`, `fiorilli_contratos`.

## Scoring e domínio

Pesos: impacto 25, atualidade 20, interesse público 15, qualidade/confiabilidade 20, potencial jornalístico 10, exclusividade 10. Faixas: `80-100` Alta, `55-79` Média, `0-54` Baixa.

A suíte cobre extração de entidades, exclusões editoriais, score total 92 (`Alta`, `Total: 92/100 pts`, `Emergência`), deduplicação, CSV/Markdown, imagem de capa, suporte SQLite/Supabase/stateless, cabeçalhos `X-PNCP-Retificacoes` e `X-Pautas-Arquivadas`, `/api/pautas?arquivadas=only`, normalização/agregação PNCP 2024–2026, parser RSS/HTML da prefeitura, `parse_brl_number`, retificações PNCP, classificação PNCP em 14 categorias, parsers e sincronização Fiorilli (`anos=(2025, 2026)`).
