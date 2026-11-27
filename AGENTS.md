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

GET: `/`, `/dashboard`, `/manifest.webmanifest`, `/sw.js`, `/apple-touch-icon.png`, `/api/push/chave-publica`, `/api/auto-atualizar`, `/api/pautas`, `/api/pautas/arquivadas`, `/api/stats`, `/api/telemetria`, `/api/clipping`, `/api/export/csv`, `/export/pautas.csv`, `/api/fontes`, `/api/pendencias`, `/api/pncp/contratos`, `/api/pncp/resumo`, `/api/pncp/estatisticas`, `/api/pncp/export/csv`, `/api/pncp/retificacoes`, `/api/pncp/ignorados`, `/api/transparencia/resumo`, `/api/transparencia/cruzamento`, `/api/transparencia/dispensas`, `/api/transparencia/export/csv`.

POST: `/api/push/inscrever`, `/api/push/cancelar`, `/api/push/testar`, `/api/auto-atualizar`, `/api/auto-atualizar/abrir` (pública), `/api/executar`, `/api/pncp/sync`, `/api/transparencia/sync`, `/api/pncp/retificar`, `/api/pncp/retificacoes/sync`, `/api/pncp/ignorar`, `/api/pautas/arquivar`, `/api/fontes`.

## Deploy

- `python app.py` serve `http://localhost:8000`.
- Docker: imagem `python:3.11-slim`, `PORT=8000`, `HOST=0.0.0.0`, `CMD ["python", "app.py"]`; prover `PORT`/`HOST` via ambiente quando necessário.
- Render é o único host em nuvem (serviço `radar-itz-web`, https://radar-itz-web.onrender.com). Não há mais Vercel.
- Render: disco temporário no plano free; sem `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` o SQLite se perde a cada reinício (`IS_RENDER` gera aviso no sync PNCP).
- Render: `startCommand: python app.py`, `PORT=10000`, `buildCommand: pip install -r requirements.txt` (só `cryptography`, usada nas notificações push), plano free.

## Configuração

Variáveis de coleta: `RADAR_TIMEOUT_SECONDS=15`, `RADAR_USER_AGENT`, `RADAR_CSV_DELIMITER=","`, `RADAR_SCHEDULE_TIMES="07:00,12:00,18:00"`.

Atualização automática: ao abrir o painel (ou F5), `static/radar_auto.js` chama `POST /api/auto-atualizar` se houver token salvo no navegador (inclui as palavras-chave salvas); sem token (ex.: app instalado no iPhone) chama `POST /api/auto-atualizar/abrir`, rota pública que ignora corpo e token, não aceita parâmetros, só dispara as tarefas vencidas (como o agendador do servidor) e tem limite de 6 chamadas por minuto por IP (429). O servidor (`src/auto_refresh.py`) roda em segundo plano, uma tarefa por vez, só o que venceu: varredura (regera o Clipping Diário), PNCP (até 15 lotes incrementais; pula se outra sessão conduz uma carga há menos de 3 min) e Portal Fiorilli. Intervalos: `RADAR_AUTO_INTERVALO_MINUTOS=30` (varredura e PNCP), `RADAR_AUTO_INTERVALO_FIORILLI_MINUTOS=180`; `RADAR_AUTO_ATUALIZAR=0` desliga. Os botões manuais usam a mesma trava (409 se a automática da mesma tarefa estiver rodando). Estado em memória do processo: reiniciar o serviço zera os intervalos.

App no celular e notificações: o painel é um PWA (`static/manifest.webmanifest`, `static/sw.js`, ícones em `static/icons/`). No iPhone (iOS 16.4+): Safari > Compartilhar > Adicionar à Tela de Início, abrir pelo ícone e tocar no sino (`static/radar_push.js`). `src/web_push.py` faz Web Push (RFC 8291/8292) com `cryptography`; chave em `RADAR_VAPID_PRIVATE_KEY` (base64url, 32 bytes; gerar com `python -m src.web_push`), remetente em `RADAR_VAPID_SUBJECT`. Inscrições na tabela `push_inscricoes` (migration `supabase/migrations/20261127_push_inscricoes.sql`), máx. 50, só endpoints https de serviços de push conhecidos. Um aviso por rodada: varredura com `pautas_novas > 0` e PNCP com contratos inseridos no modo incremental (a carga histórica não avisa). No Render, um agendador no servidor (`RADAR_AGENDADOR_SERVIDOR`, ligado por padrão no Render) dispara as tarefas vencidas a cada 5 min, então a coleta e os avisos acontecem com o app fechado.

Transparência no celular: até 768px a aba "Transparência & Cruzamento" vira um layout de app (`static/radar_transp_mobile.js` + `.css`): barra superior com busca e sino, chips de exercício, navegação inferior (Visão geral, Receitas, Órgãos, Contratos, Pendências), menu "Mais" em bottom sheet no lugar da barra lateral, listas em cartões com "Ver todos" e rolagem incremental, dossiê em tela cheia, puxar para atualizar (chama `radarAutoAtualizar` de `radar_auto.js` e recarrega as mesmas rotas `/api/transparencia/*`) e tema escuro opcional (Mais › Tema). Só liga com a classe `body.mt-on`; acima de 768px nada muda. Offline: o `sw.js` guarda página e `/static/` (rede primeiro) e o CDN; a última resposta da API fica no `localStorage` do aparelho e aparece com "dados de [data/hora]". Atalho do app: `/?aba=transparencia`. O celular mostra na hora o último resumo guardado no aparelho ("Atualizando… dados de") enquanto o servidor responde.

Cache do resumo: `GET /api/transparencia/resumo` fica guardado em memória por `RADAR_RESUMO_CACHE_SEGUNDOS` (padrão 300; `0` desliga), por exercício, com header `X-Radar-Cache: HIT|MISS`. É descartado em `POST /api/pncp/sync`, `/api/transparencia/sync`, `/api/pncp/retificar`, `/api/pncp/retificacoes/sync`, `/api/pncp/ignorar`, quando as tarefas automáticas PNCP ou Fiorilli terminam e quando retificações chegam por header; uma gravação durante o cálculo impede guardar o resultado. Scripts rodados fora do servidor (ex.: `scripts/fiorilli_sync.py`) só aparecem após o prazo.

Checagem de rede: antes da coleta, `main.py` testa DNS; sem rede a execução termina como `SEM_REDE` com um único erro (`RADAR_SKIP_NETWORK_CHECK=1` desliga).

Instagram: só coleta com `INSTAGRAM_GRAPH_TOKEN` + `INSTAGRAM_BUSINESS_ACCOUNT_ID` (Graph API Business Discovery, `src/instagram_client.py`); funciona apenas para perfis Empresa/Criador. Versão em `INSTAGRAM_GRAPH_API_VERSION` (padrão `v27.0`); tokens `IGAA...` (Login do Instagram) são recusados com mensagem clara, pois o Business Discovery exige token de usuário do Facebook (`EAA...`). Configuração do app Meta em `docs/instagram-graph-api.md`. Sem as duas variáveis, a fonte fica "Aguardando credencial" e não gera erro em `erros_coleta`.

Evidências: cada item de `pautas.evidencias_verificadas` é gravado também na tabela `evidencias` (`src/evidence_store.py`).

Segurança: `RADAR_ADMIN_TOKEN` protege todas as rotas POST e a reidratação por headers/cookies (header `X-Radar-Token` ou `Authorization: Bearer`). Sem ele, só o acesso local direto pode gravar. `/static/` só serve arquivos dentro de `static/`. Token comparado em tempo constante; 5 palpites errados diferentes por minuto bloqueiam o IP com 429 (no Render o IP é o último `X-Forwarded-For`). POST acima de 256 KB recebe 413. Supabase: RLS ligado sem policies e sem privilégios para `anon`/`authenticated` (inclusive tabelas futuras); só a chave secreta do servidor acessa.

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
