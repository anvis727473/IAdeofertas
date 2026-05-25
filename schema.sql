-- =============================================================================
-- SCHEMA: Bot de Ofertas AliExpress → Telegram (PRODUÇÃO)
-- Plataforma: Supabase (PostgreSQL 15+)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS public.ofertas (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    titulo              TEXT            NOT NULL CHECK (char_length(titulo) BETWEEN 3 AND 500),
    url_produto         TEXT            NOT NULL,
    url_imagem          TEXT,
    preco_original      NUMERIC(10, 2)  CHECK (preco_original > 0),
    preco_desconto      NUMERIC(10, 2)  NOT NULL CHECK (preco_desconto > 0),
    percentual_desconto NUMERIC(5, 2)   CHECK (percentual_desconto BETWEEN 0 AND 100),
    cupom               TEXT,
    tag_afiliado        TEXT,
    enviado             BOOLEAN         NOT NULL DEFAULT FALSE,
    enviado_em          TIMESTAMPTZ,
    tentativas          SMALLINT        NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
    agendado_para       TIMESTAMPTZ,
    prioridade          SMALLINT        NOT NULL DEFAULT 0,
    
    -- Correção do Schema Drift: Colunas integradas para o Motor Analítico
    product_rating      NUMERIC(3, 2)   DEFAULT 5.0,
    sales_volume        INTEGER         DEFAULT 0,
    seller_feedback_rate NUMERIC(5, 4)  DEFAULT 1.0,
    historico_precos    NUMERIC(10, 2)[] DEFAULT '{}'::NUMERIC[],
    
    criado_em           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    atualizado_em       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Índices parciais para garantir Idempotência Estrita
CREATE UNIQUE INDEX IF NOT EXISTS uq_ofertas_url_pendente
    ON public.ofertas (url_produto)
    WHERE enviado = FALSE;

CREATE INDEX IF NOT EXISTS idx_ofertas_pendentes
    ON public.ofertas (enviado, tentativas, prioridade DESC, criado_em ASC)
    WHERE enviado = FALSE AND tentativas < 5;

-- Automação de timestamps
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

-- RPC atômico para incremento de falhas isoladas
CREATE OR REPLACE FUNCTION public.increment_tentativas(offer_id UUID)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.ofertas
    SET tentativas = tentativas + 1
    WHERE id = offer_id;
END;
$$;

ALTER TABLE public.ofertas ENABLE ROW LEVEL SECURITY;
