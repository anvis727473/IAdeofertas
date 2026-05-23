"""
database.py — Camada de dados Supabase
Responsabilidades:
  - Buscar ofertas agendadas e ainda não enviadas
  - Marcar oferta como enviada (idempotência)
  - Registrar erros de envio para reprocessamento
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from supabase import Client, create_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conexão singleton — criada uma única vez ao importar o módulo
# ---------------------------------------------------------------------------
_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        try:
            url = os.environ["SUPABASE_URL"]
            key = os.environ["SUPABASE_KEY"]
            _client = create_client(url, key)
            logger.info("Supabase client inicializado com sucesso.")
        except KeyError as exc:
            logger.critical("Variáveis de ambiente do Supabase ausentes: %s", exc)
            raise
        except Exception as exc:
            logger.critical("Falha crítica ao inicializar cliente Supabase: %s", exc)
            raise
    return _client


# ---------------------------------------------------------------------------
# Tipos auxiliares (Refatorado para compatibilidade com Python 3.9+)
# ---------------------------------------------------------------------------
Oferta = Dict[str, Any]


# ---------------------------------------------------------------------------
# Queries principais
# ---------------------------------------------------------------------------

def fetch_pending_offers(limit: int = 10) -> List[Oferta]:
    """
    Retorna até `limit` ofertas que:
      - ainda não foram enviadas (enviado = FALSE)
      - têm agendamento <= agora (agendado_para IS NULL ou agendado_para <= agora)
      - respeitam o limite máximo de 5 tentativas de envio falhas
    """
    try:
        client = get_client()
        agora = datetime.now(timezone.utc).isoformat()

        response = (
            client.table("ofertas")
            .select(
                "id, titulo, preco_original, preco_desconto, percentual_desconto, url_produto, url_imagem, cupom, tag_afiliado, tentativas"
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
        logger.error("Erro ao buscar ofertas pendentes: %s", exc)
        return []


def mark_as_sent(offer_id: str) -> bool:
    """
    Marca uma oferta como enviada com sucesso no Supabase para garantir idempotência.
    """
    client = get_client()
    try:
        client.table("ofertas").update(
            {
                "enviado": True,
                "enviado_em": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", offer_id).eq("enviado", False).execute()
        logger.info("Oferta %s marcada como enviada.", offer_id)
        return True
    except Exception as exc:
        logger.error("Falha ao marcar oferta %s como enviada: %s", offer_id, exc)
        return False


def increment_attempt(offer_id: str) -> None:
    """
    Incrementa o contador de tentativas malsucedidas.
    Após 5 tentativas a oferta é ignorada pelo fetch_pending_offers.
    """
    client = get_client()
    try:
        # RPC garante incremento atômico sem race condition
        client.rpc("increment_tentativas", {"offer_id": offer_id}).execute()
    except Exception as exc:
        # Fallback: leitura + escrita (não-atômico, mas aceitável para este caso de falha de RPC)
        logger.warning(
            "RPC indisponível, usando fallback para incremento: %s", exc
        )
        try:
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
            logger.error(
                "Falha crítica no fallback de incremento da oferta %s: %s",
                offer_id,
                inner,
            )
