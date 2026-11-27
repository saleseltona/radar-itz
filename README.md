# Radar de Pautas de Imperatriz (MA)
**Central de Inteligência Editorial, Monitoramento Jornalístico e Verificação Pública**

O **Radar de Pautas de Imperatriz** é um sistema automatizado modular, desenvolvido em Python, concebido para identificar, verificar, classificar e organizar pautas jornalísticas sobre o município de Imperatriz, Maranhão. 

O sistema combina varredura de fontes cadastradas, pesquisa em portais de transparência, diários oficiais, tribunais de contas, ministério público e imprensa regional, aplicando regras determinísticas de checagem, deduplicação com memória histórica e pontuação editorial de 0 a 100 pontos.

---

## 1. Arquitetura Modular do Sistema

O projeto adota uma arquitetura em camadas desacopladas:

```
radar-pautas-imperatriz/
│
├── fontes.csv                       # Cadastro dinâmico de fontes (Instagram + Portais Oficiais)
├── .env.example                     # Modelo seguro de variáveis de ambiente e APIs opcionais
├── main.py                          # CLI e orquestrador principal de execução
├── README.md                        # Documentação técnica e operacional
│
├── data/
│   ├── radar_imperatriz.db          # Banco relacional persistente SQLite (fontes, pautas, histórico)
│   ├── coleta_verificada_real.json  # Repositório de busca e páginas públicas verificadas
│   └── scheduler.lock               # Trava de segurança contra sobreposição de execuções
│
├── src/
│   ├── __init__.py
│   ├── config.py                    # Pesos da pontuação, consultas obrigatórias e proteção de segredos
│   ├── database.py                  # Gerenciador de persistência SQLite com histórico imutável
│   ├── error_monitor.py             # Auditoria de falhas e sanitização de logs
│   ├── source_manager.py            # Validador de URLs e cadastro dinâmico de fontes
│   ├── content_extractor.py         # Extrator e sanitizador anti-prompt-injection
│   ├── evidence_verifier.py         # Verificador de evidências e segurança editorial (Seção 14)
│   ├── editorial_classifier.py      # Filtro de descartes e cálculo de pontuação (0 a 100)
│   ├── deduplicator.py              # Memória histórica, detecção de duplicidades e fusão de fontes
│   ├── page_collector.py            # Coletor HTTP seguro com registro de latência e hash
│   ├── search_engine.py             # Motor de busca (32 consultas obrigatórias + derivadas)
│   ├── exporter.py                  # Exportador de arquivos CSV (UTF-8, delimitador customizável)
│   ├── report_generator.py          # Gerador do clipping editorial obrigatório em Markdown
│   └── scheduler.py                 # Agendador e modo manual com lockfile
│
├── output/                          # Diretório de arquivos de saída obrigatórios
│   ├── pautas_novas.csv             # Pautas inéditas da execução atual (27 colunas)
│   ├── pautas_novas_ptbr.csv        # Versão com ';' para compatibilidade direta no Excel BR
│   ├── clipping_do_dia.md           # Relatório editorial com as 10 melhores pautas e top 3
│   ├── historico_execucoes.csv      # Log cronológico persistente de cada execução
│   ├── fontes_status.csv            # Diagnóstico operacional de cada fonte monitorada
│   └── pendencias_apuracao.csv      # Pautas que exigem apuração e investigação adicional
│
└── tests/
    └── test_radar.py                # Suíte automatizada de testes (9 baterias cobrindo a Seção 16)
```

---

## 2. Como Executar

### 2.1. Execução Manual Sob Demanda
Para rodar uma rodada completa de monitoramento e geração dos relatórios:
```bash
python main.py
```

### 2.2. Execução com Delimitador Ponto e Vírgula (`;`)
Para compatibilidade nativa com planilhas brasileiras:
```bash
python main.py --delimiter ";"
```

### 2.3. Cadastrar Nova Fonte Dinamicamente (Sem alterar código)
```bash
python main.py --add-source "Nome da Fonte" "Tipo" "https://url-da-fonte.ma.gov.br/" "Alta" "tema1;tema2"
```

### 2.4. Consultar Status do Banco e Fontes
```bash
python main.py --status
```

### 2.5. Executar em Modo Agendado
Para execução periódica contínua (ex: a cada 1 hora / 3600 segundos) com prevenção de sobreposição de tarefas via `scheduler.lock`:
```bash
python main.py --mode scheduled --interval 3600
```

### 2.6. Executar o Aplicativo Web com Interface Google Stitch
Para iniciar o servidor web integrado com a interface oficial desenhada no Google Stitch:
```bash
python app.py
```
Acesse no seu navegador: **`http://localhost:8000`**

### 2.7. Executar os Testes Automatizados
```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## 3. Interface Web & Integração Google Stitch

A interface visual do Radar de Pautas foi projetada através do **Google Stitch** (`stitch.google.com`), projeto `Radar de Pautas Imperatriz` (ID: `12571963369295977519`, tela `Radar de Pautas de Imperatriz - Painel Desktop`), incorporando:

- **Feed Dinâmico de Pautas:** Renderização em tempo real das pautas auditadas com pontuação editorial, badges de criticidade, fontes oficiais e locais.
- **Filtros por Categoria:** Alternância instantânea entre pautas urgentes, denúncias e apurações, serviços públicos e cultura/cidade.
- **Busca em Tempo Real:** Pesquisa textual instantânea por secretarias (SINFRA, SEMUS), contratos, termos de alerta ou bairros.
- **Dossiê Investigativo (Modal):** Abertura de dossiê completo de cada pauta com resumo editorial, contraditório, documentos primários citados e link original.
- **Disparo de Varredura Sob Demanda:** Botão para executar um novo ciclo de rastreamento diretamente pelo navegador.
- **Exportação Integrada:** Download direto de planilhas CSV e leitura do clipping em Markdown.

### Endpoints RESTful Nativos:
- `GET /` ou `/dashboard`: Interface completa do Stitch integrada.
- `GET /api/pautas`: Retorna todas as pautas em formato JSON.
- `GET /api/stats`: Métricas de pautas, emergências, apurações e fontes.
- `GET /api/clipping`: Relatório editorial consolidado do dia em Markdown.
- `GET /api/export/csv`: Download de `pautas_novas.csv` formatado.
- `POST /api/executar`: Dispara um novo ciclo de monitoramento e auditoria.

---

## 4. Como Colocar o Radar Online (Deploy em Nuvem)

O sistema foi concebido sem dependências externas (utiliza unicamente a biblioteca padrão do Python 3 e Tailwind CSS via CDN), tornando o deploy extremamente leve e imediato.

### Opção A: Render.com (1-clique com `render.yaml`)
1. Crie uma conta gratuita em [render.com](https://render.com).
2. Conecte este repositório Git.
3. O arquivo `render.yaml` já está configurado na raiz: o Render detectará o serviço Web Python automaticamente e iniciará na porta `10000`.

### Opção B: Railway.app / Fly.io / Heroku
O arquivo `Dockerfile` na raiz permite deploy em qualquer provedor de contêineres:
```bash
docker build -t radar-pautas-imperatriz .
docker run -p 8000:8000 radar-pautas-imperatriz
```

### Opção C: Cloudflare Tunnel (Disponibilizar seu computador online gratuitamente)
Se desejar rodar no seu computador e gerar um link seguro HTTPS público instantâneo:
```bash
# 1. Inicie a aplicação
python app.py

# 2. Em outro terminal, execute o cloudflared:
cloudflared tunnel --url http://localhost:8000
```
O Cloudflare gerará uma URL segura do tipo `https://nome-aleatorio.trycloudflare.com` acessível de qualquer lugar.

---

## 3. Configuração de Variáveis de Ambiente (`.env`)

Copie o arquivo `.env.example` para `.env`:
```bash
cp .env.example .env
```
O sistema funciona de forma completa e autônoma sem dependências de chaves pagas. Caso deseje ampliar o escopo para buscas em APIs externas ou leitura de perfis oficiais da Meta via API institucional, preencha:
- `GOOGLE_SEARCH_API_KEY` e `GOOGLE_SEARCH_CX`: Para Google Custom Search.
- `SERPAPI_KEY` ou `BING_SEARCH_API_KEY`: Para motores de busca adicionais.
- `INSTAGRAM_GRAPH_TOKEN`: Para leitura institucional via Meta Graph API v19.0.

**Segurança do painel:** defina `RADAR_ADMIN_TOKEN` com um valor longo e aleatório em qualquer servidor acessível pela internet (Vercel, Render). As ações que gravam dados (varredura, retificar, ignorar, arquivar e cadastrar fonte) passam a exigir esse token, que o painel pede uma vez e guarda no navegador. Sem o token, essas ações só funcionam no acesso local direto (`localhost`).

> **Proteção de Segredos:** O módulo `config.py` e `error_monitor.py` aplicam máscaras ativas (`***REDACTED***`) em quaisquer tokens para impedir vazamento de credenciais em relatórios, arquivos CSV ou mensagens de log.

---

## 4. Sistema de Pontuação Editorial (0 a 100 pontos)

| Critério | Peso |
|---|---:|
| Impacto na população | 25 |
| Atualidade e novidade | 20 |
| Interesse público | 15 |
| Qualidade e confiabilidade das evidências | 20 |
| Potencial de desenvolvimento jornalístico | 10 |
| Exclusividade ou informação pouco explorada | 10 |

- **80 a 100:** Prioridade Alta
- **55 a 79:** Prioridade Média
- **0 a 54:** Prioridade Baixa
- **Emergência Confirmada:** Sinalização especial máxima (`🚨 PRIORIDADE MÁXIMA / EMERGÊNCIA`).
- **Segurança Editorial:** Nenhuma denúncia grave recebe status de fato confirmado apenas por alta relevância; exige documentação primária e registro obrigatório de contraditório.

---

## 5. Persistência na Nuvem com Supabase + Vercel (Carga Incremental)

Para evitar a natureza efêmera do `/tmp` em instâncias serverless da Vercel (onde retificações em SQLite dependem de sincronização por sessão), você pode ativar o backend nativo em **Supabase PostgreSQL**:

1. **Crie as tabelas:** No SQL Editor do seu projeto Supabase, execute [`supabase/schema.sql`](supabase/schema.sql).
2. **Configure as variáveis:** No `.env` local (e em *Settings → Environment Variables* na Vercel), defina:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
3. **Suba apenas dados novos (`--only-new` por padrão):**
   ```bash
   python scripts/supabase_sync.py --check
   python scripts/supabase_sync.py --dry-run
   python scripts/supabase_sync.py
   ```
   O script consulta antes as chaves já existentes no Supabase e envia **somente os registros novos**, preservando integralmente qualquer retificação já feita no painel.
4. **Faça redeploy na Vercel:** A API detectará automaticamente `storage_backend = "supabase"`.
