"""
database.py — Camada de dados Supabase
Responsabilidades:
  - Estabelecer a ligação segura com o Supabase utilizando um Singleton reutilizável
  - Fornecer queries robustas para leitura de filas de processamento e atualização de estados
  - Sanitizar chaves de API contra quebras de linha ou espaços gerados pela infraestrutura do Render
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from supabase import Client, create_client

logger = logging.getLogger(__name__)

_client: Client | None = None

def get_client() -> Client:
    """
    Inicializa e retorna o cliente do Supabase com sanitização estrita de chaves de API.
    """
    global _client
    if _client is None:
        try:
            # Captura e limpa espaços vazios ou quebras de linha acidentais geradas pelo Render
            url = os.environ.get("SUPABASE_URL", "").strip()
            key = os.environ.get("SUPABASE_KEY", "").strip()
            
            if not url or not key:
                raise ValueError("SUPABASE_URL ou SUPABASE_KEY estão vazias ou ausentes no ambiente de produção.")
                
            _client = create_client(url, key)
            logger.info("Supabase client inicializado com sucesso.")
        except KeyError as exc:
            logger.critical("Variáveis de ambiente do Supabase ausentes no runtime: %s", exc)
            raise
        except Exception as exc:
            logger.critical("Falha crítica ao instanciar cliente do Supabase: %s", exc)
            raise
    return _client

# Tipagem retrocompatível para Python 3.9 / 3.10 / 3.11
Oferta = Dict[str, Any]

def fetch_pending_offers(limit: int = 10) -> List[Oferta]:
    """
    Busca no banco ofertas pendentes aptas a processamento analítico.
    """
    try:
        client = get_client()
        agora = datetime.now(timezone.utc).isoformat()

        response = (
            client.table("ofertas")
            .select(
                "id, titulo, preco_original, preco_desconto, percentual_desconto, "
                "url_produto, url_imagem, cupom, tag_afiliado, tentativas, historico_precos, "
                "product_rating, sales_volume, seller_feedback_rate, cupom_loja_valor, "
                "cupom_plataforma_valor, vendas_6h, vendas_6h_anteriores"
            )
            .eq("enviado", False)
            .lt("tentativas", 5)
            .or_(f"agendado_para.is.null,agendado_para.lte.{agora}")
            .order("prioridade", desc=True)
            .order("criado_em")
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception as exc:
        logger.error("Erro ao buscar ofertas pendentes no Supabase: %s", exc)
        return []

def mark_as_sent(offer_id: str) -> bool:
    """
    Registra que o disparo foi concluído, assegurando idempotência estrita.
    """
    try:
        client = get_client()
        client.table("ofertas").update(
            {
                "enviado": True,
                "enviado_em": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", offer_id).eq("enviado", False).execute()
        logger.info("Oferta %s marcada como enviada com sucesso no banco.", offer_id)
        return True
    except Exception as exc:
        logger.error("Falha ao atualizar estado de envio da oferta %s: %s", offer_id, exc)
        return False

def increment_attempt(offer_id: str) -> None:
    """
    Incrementa de forma atómica o contador de tentativas da oferta para suspensão em caso de erros recorrentes.
    """
    try:
        client = get_client()
        client.rpc("increment_tentativas", {"offer_id": offer_id}).execute()
    except Exception as exc:
        logger.warning("Falha na chamada RPC de incremento. Utilizando mecanismo de fallback: %s", exc)
        try:
            client = get_client()
            row = (
                client.table("ofertas")
                .select("tentativas")
                .eq("id", offer_id)
                .single()
                .execute()
            )
            current = (row.data or {}).get("tentativas", 0)
            client.table("ofertas").update(
                {"tentativas": current + 1}
            ).eq("id", offer_id).execute()
        except Exception as inner:
            logger.error("Falha crítica no fallback de incremento da oferta %s: %s", offer_id, inner)
