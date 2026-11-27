-- Inscrições de notificações push (Web Push) dos aparelhos do Radar (src/web_push.py).
-- Idempotente. Acesso só pelo backend (chave secreta/service role): RLS ligado, sem policy,
-- e sem privilégios para anon/authenticated.

CREATE TABLE IF NOT EXISTS public.push_inscricoes (
    endpoint TEXT PRIMARY KEY,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT DEFAULT '',
    criado_em TEXT NOT NULL DEFAULT '',
    ultimo_envio TEXT DEFAULT '',
    ultimo_status INTEGER DEFAULT NULL
);

ALTER TABLE public.push_inscricoes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.push_inscricoes FROM anon, authenticated;
