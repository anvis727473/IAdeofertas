-- =============================================================================
-- SCHEMA: Bot de Ofertas AliExpress → Telegram
-- Plataforma: Supabase (PostgreSQL 15+)
-- Execute este script no SQL Editor do Supabase
-- =============================================================================

-- Habilita extensão para UUID v4 nativo
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- TABELA PRINCIPAL: ofertas
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.ofertas (
    -- Identificação
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Dados do produto
    titulo          TEXT            NOT NULL CHECK (char_length(titulo) BETWEEN 3 AND 500),
    url_produto     TEXT            NOT NULL,
    url_imagem      TEXT,                               -- nullable: fallback para sendMessage
    preco_original  NUMERIC(10, 2)  CHECK (preco_original > 0),
    preco_desconto  NUMERIC(10, 2)  NOT NULL CHECK (preco_desconto > 0),
    percentual_desconto NUMERIC(5, 2) CHECK (percentual_desconto BETWEEN 0 AND 100),
    cupom           TEXT,                               -- código de cupom opcional

    -- Configuração de afiliado
    tag_afiliado    TEXT,                               -- substitui ALI_TRACKING_ID por oferta

    -- Controle de envio (idempotência)
    enviado         BOOLEAN         NOT NULL DEFAULT FALSE,
    enviado_em      TIMESTAMPTZ,                        -- preenchido ao marcar como enviado
    tentativas      SMALLINT        NOT NULL DEFAULT 0 CHECK (tentativas >= 0),

    -- Agendamento
    agendado_para   TIMESTAMPTZ,                        -- NULL = enviar imediatamente
    prioridade      SMALLINT        NOT NULL DEFAULT 0, -- maior = enviado primeiro

    -- Auditoria
    criado_em       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- CONSTRAINT: evita duplicidade por URL de produto
-- (mesmo produto não pode aparecer duas vezes como pendente)
-- =============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uq_ofertas_url_pendente
    ON public.ofertas (url_produto)
    WHERE enviado = FALSE;

-- =============================================================================
-- ÍNDICES: otimizam as queries do fetch_pending_offers
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_ofertas_pendentes
    ON public.ofertas (enviado, tentativas, prioridade DESC, criado_em ASC)
    WHERE enviado = FALSE AND tentativas < 5;

CREATE INDEX IF NOT EXISTS idx_ofertas_agendado_para
    ON public.ofertas (agendado_para)
    WHERE enviado = FALSE;

-- =============================================================================
-- TRIGGER: mantém atualizado_em sincronizado automaticamente
-- =============================================================================
CREATE OR REPLACE FUNCTION public.set_atualizado_em()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.atualizado_em = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ofertas_atualizado_em ON public.ofertas;
CREATE TRIGGER trg_ofertas_atualizado_em
    BEFORE UPDATE ON public.ofertas
    FOR EACH ROW EXECUTE FUNCTION public.set_atualizado_em();

-- =============================================================================
-- RPC: incremento atômico de tentativas (evita race condition)
-- Chamado por database.increment_attempt() via supabase.rpc()
-- =============================================================================
CREATE OR REPLACE FUNCTION public.increment_tentativas(offer_id UUID)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.ofertas
    SET tentativas = tentativas + 1
    WHERE id = offer_id;
END;
$$;

-- =============================================================================
-- RLS (Row Level Security): bloqueia acesso direto sem service_role
-- O bot usa a SUPABASE_KEY (service_role) — RLS não se aplica a ela,
-- mas protege acesso via anon key ou JWT de usuários finais.
-- =============================================================================
ALTER TABLE public.ofertas ENABLE ROW LEVEL SECURITY;

-- Política: somente service_role pode ler/escrever (padrão Supabase)
-- Nenhuma policy adicional é necessária quando o bot usa service_role key.

-- =============================================================================
-- DADOS DE EXEMPLO: 2 ofertas para teste imediato após o deploy
-- =============================================================================
INSERT INTO public.ofertas (
    titulo,
    url_produto,
    url_imagem,
    preco_original,
    preco_desconto,
    percentual_desconto,
    cupom,
    prioridade
) VALUES
(
    'Smartwatch Xiaomi Band 8 Pro — Monitor Cardíaco 24h',
    'https://www.aliexpress.com/item/1005006123456789.html',
    'https://ae01.alicdn.com/kf/sample-image.jpg',
    299.90,
    149.90,
    50.0,
    'BAND8BR',
    10
),
(
    'Fone Bluetooth TWS QCY T13 ANC — Cancelamento de Ruído',
    'https://www.aliexpress.com/item/1005005987654321.html',
    NULL,
    189.90,
    89.90,
    52.6,
    NULL,
    5
)
ON CONFLICT DO NOTHING;

-- =============================================================================
-- VERIFICAÇÃO FINAL
-- =============================================================================
SELECT
    tablename,
    (SELECT COUNT(*) FROM public.ofertas) AS total_registros
FROM pg_tables
WHERE schemaname = 'public' AND tablename = 'ofertas';
