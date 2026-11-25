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
```

`main.py --mode` aceita `manual` (padrão) ou `scheduled`; `--interval` usa 3600s por padrão. `--status` mostra backend, caminho do banco e totais.

## Arquitetura

- `main.py`: CLI e orquestração manual/agendada.
- `src/config.py`: paths, variáveis de ambiente, backend SQLite/Supabase, pesos de scoring e máscara de segredos.
- `src/web_server.py`: rotas HTTP da API e do dashboard.
- `app.py`: servidor web local/Render, porta 8000 por padrão.
- `api/index.py`: handler da Vercel.
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
- Vercel: `vercel.json` usa rewrites para `/api/index?__route=...`, `maxDuration: 60` e inclui `static/`, `data/`, `output/`, `src/`, `fontes.csv` e `main.py`.
- Vercel usa diretório temporário `tempfile.gettempdir()/radar-pautas-imperatriz` e copia bundles de `data/`, `output/` e `fontes.csv` quando ausentes.
- Render: `startCommand: python app.py`, `PORT=10000`, `buildCommand: ""`, plano free.

## Configuração

Variáveis de coleta: `RADAR_TIMEOUT_SECONDS=15`, `RADAR_USER_AGENT`, `RADAR_CSV_DELIMITER=","`, `RADAR_SCHEDULE_TIMES="07:00,12:00,18:00"`.

Opcionais: `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `SERPAPI_KEY`, `BING_SEARCH_API_KEY`, `INSTAGRAM_GRAPH_TOKEN`, `INSTAGRAM_BUSINESS_ACCOUNT_ID`, `ANA_TELEMETRIA_TOKEN`.

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
