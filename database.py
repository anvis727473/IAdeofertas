"""
database.py — Camada de Abstração de Dados e Integração de Alta Performance com o Supabase
"""

import logging
import os
from typing import Any, Dict, List
from supabase import create_client, Client

logger = logging.getLogger("bot.database")

_supabase_instance: Client = None

def get_client() -> Client:
    """ Garante e expõe o Singleton de conexão com o Supabase """
    global _supabase_instance
    if _supabase_instance is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _supabase_instance = create_client(url, key)
        logger.info("Supabase client inicializado com sucesso em modo estável.")
    return _supabase_instance

def fetch_pending_offers(limit: int = 5) -> List[Dict[str, Any]]:
    """ 
    Puxa produtos da fila de triagem. 
    Remove travas complexas de agendamento que geram falhas de timezone entre Render/Supabase.
    """
    try:
        client = get_client()
        # Query otimizada indexando apenas o estado lógico e teto de tentativas fracassadas
        response = client.table("ofertas")\
            .select("id, titulo, preco_original, preco_desconto, percentual_desconto, url_produto, url_imagem, cupom, tag_afiliado, tentativas, historico_precos, product_rating, sales_volume, seller_feedback_rate, cupom_loja_valor, cupom_plataforma_valor, vendas_6h, vendas_6h_anteriores")\
            .eq("enviado", False)\
            .lt("tentativas", 5)\
            .order("prioridade", desc=True)\
            .limit(limit)\
            .execute()
            
        return response.data or []
    except Exception as exc:
        logger.error("Falha crítica ao executar varredura de pendências no Supabase: %s", exc)
        return []

def mark_as_sent(offer_id: str) -> bool:
    """ Consolida o estado de envio bem-sucedido mitigando duplicações """
    try:
        client = get_client()
        client.table("ofertas").update({"enviado": True, "atualizado_em": "now()"}).eq("id", offer_id).execute()
        logger.info("Produto %s marcado como enviado com sucesso.", offer_id)
        return True
    except Exception as exc:
        logger.error("Erro ao atualizar status de envio do produto %s: %s", offer_id, exc)
        return False

def increment_attempt(offer_id: str) -> bool:
    """ Incrementa de forma atômica o contador de tentativas de envio para evitar deadlocks """
    try:
        client = get_client()
        # Captura valor atual para incremento seguro em lote
        current_res = client.table("ofertas").select("tentativas").eq("id", offer_id).execute()
        if current_res.data:
            current_attempts = int(current_res.data[0].get("tentativas") or 0)
            client.table("ofertas").update({
                "tentativas": current_attempts + 1,
                "atualizado_em": "now()"
            }).eq("id", offer_id).execute()
            return True
        return False
    except Exception as exc:
        logger.error("Falha ao registrar incremento de tentativa na oferta %s: %s", offer_id, exc)
        return False
