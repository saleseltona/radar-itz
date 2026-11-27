# Instagram Graph API (Business Discovery) no Radar de Pautas

O Radar lê publicações de perfis públicos do Instagram (contas **Empresa ou Criador**) pelo
endpoint **Business Discovery** da Meta Graph API (`src/instagram_client.py`). As consultas
partem de uma conta profissional própria e usam um token de usuário do Facebook.

> Nunca coloque valores de tokens neste repositório. Eles ficam só no `.env` local e nas
> variáveis de ambiente do servidor (Vercel/Render).

## Variáveis de ambiente

| Variável | O que é |
|---|---|
| `INSTAGRAM_GRAPH_TOKEN` | Token de usuário do **Facebook** (começa com `EAA`), de longa duração. |
| `INSTAGRAM_BUSINESS_ACCOUNT_ID` | ID da conta profissional própria (`nasruasdeitz`) usada como ponto de partida das consultas. |
| `INSTAGRAM_GRAPH_API_VERSION` | Opcional. Versão da Graph API (padrão `v27.0`). |

Cadastre as mesmas variáveis em *Settings → Environment Variables* na Vercel (ou no painel do
Render) e faça um novo deploy.

## Configuração atual na Meta (nov/2026)

- **App:** "Radar de Pautas", no portfólio empresarial **Nas Ruas**, em modo de desenvolvimento.
- **Caso de uso:** "Gerenciar mensagens e conteúdo no Instagram" (API do Instagram, login unificado).
- **Permissões no caso de uso usadas pelo Radar:** `instagram_basic`, `instagram_manage_insights`,
  `pages_read_engagement`, `pages_show_list`, `ads_read`.
- **Conta conectada:** `nasruasdeitz` (Testador do Instagram), ligada à Página
  "Nas Ruas de Imperatriz-MA".
- **Token:** gerado no Explorador da Graph API com só as 5 permissões acima e estendido para
  **60 dias** no Depurador de Token de Acesso.

### Tipo de token: EAA, não IGAA

O painel do caso de uso ("Gerar tokens de acesso") entrega um token do **Login do Instagram**
(`IGAA...`). Esse token só funciona em `graph.instagram.com`, que **não tem Business Discovery**.
O Radar recusa esse tipo de token com uma mensagem clara. Use o token `EAA...` gerado pelo
Explorador da Graph API.

## Renovar o token (60 dias)

1. Abra o [Explorador da Graph API](https://developers.facebook.com/tools/explorer/), escolha o
   app **Radar de Pautas** e **Token do usuário**.
2. Marque só `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`,
   `pages_show_list` e `ads_read`, e clique em **Generate Access Token**. Na janela do Facebook,
   marque a Página "Nas Ruas de Imperatriz-MA".
3. Abra o [Depurador de Token de Acesso](https://developers.facebook.com/tools/debug/accesstoken/),
   cole o token, clique em **Depurar** e depois em **Estender token de acesso**.
4. Copie o novo token para `INSTAGRAM_GRAPH_TOKEN` (local e servidor).

Teste rápido no Explorador:

```
{INSTAGRAM_BUSINESS_ACCOUNT_ID}?fields=business_discovery.username(instagram){username,followers_count,media_count}
```

### Pendente: token sem expiração (usuário do sistema)

Para não precisar renovar, crie um **usuário do sistema** no portfólio Nas Ruas. Como a Página
"Nas Ruas de Imperatriz-MA" pertence a outro portfólio (BM - Nas Ruas de ITZ), antes é preciso:

1. No portfólio **Nas Ruas**: Páginas → Adicionar → *Solicitar acesso compartilhado a uma
   Página* → "Nas Ruas de Imperatriz-MA", acesso **Insights** (requer confirmação de identidade).
2. No portfólio **BM - Nas Ruas de ITZ**: aprovar o pedido (e o do perfil do Instagram, se for separado).
3. No portfólio **Nas Ruas**: Usuários do sistema → Adicionar (`radar-de-pautas`, função
   Funcionário) → atribuir o app, a Página e o Instagram → *Gerar token* com expiração **Nunca**
   e as 5 permissões acima.

## Erros comuns

| Código | Significado | O que fazer |
|---|---|---|
| 190 | Token expirado ou revogado | Renovar o token (seção acima). |
| 10 / 200 | Falta permissão no token | Gerar o token com as 5 permissões. |
| 110 / 100 | Perfil não encontrado ou não é Empresa/Criador | Conferir o @ na `fontes.csv`; perfis pessoais não são acessíveis. |
| 4 / 17 / 32 | Limite de chamadas | Aguardar; espaçar as coletas. |
