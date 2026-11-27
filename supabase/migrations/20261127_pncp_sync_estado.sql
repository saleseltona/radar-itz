-- Estado e lock da sincronização PNCP em lotes (src/pncp_sync.py).
-- NÃO executado automaticamente. Revisar e aplicar manualmente no SQL Editor do Supabase.
-- Idempotente: pode ser rodado mais de uma vez.

CREATE TABLE IF NOT EXISTS public.pncp_sync_estado (
    id TEXT PRIMARY KEY,
    estado_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    lock_owner TEXT DEFAULT NULL,
    lock_ate DOUBLE PRECISION DEFAULT NULL,
    atualizado_em TEXT NOT NULL DEFAULT ''
);

-- Mesmo padrão das demais tabelas: RLS ligado, acesso só pela service role do backend.
ALTER TABLE public.pncp_sync_estado ENABLE ROW LEVEL SECURITY;
