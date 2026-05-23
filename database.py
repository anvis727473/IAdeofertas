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
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conexão singleton — criada uma única vez ao importar o módulo
# ---------------------------------------------------------------------------
_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
        logger.info("Supabase client inicializado.")
    return _client


# ---------------------------------------------------------------------------
# Tipos auxiliares
# ---------------------------------------------------------------------------
type Oferta = dict[str, Any]


# ---------------------------------------------------------------------------
# Queries principais
# ---------------------------------------------------------------------------

def fetch_pending_offers(limit: int = 10) -> list[Oferta]:
    """
    Retorna até `limit` ofertas que:
      - ainda não foram enviadas (enviado = FALSE)
      - têm agendamento <= agora  (agendado_para IS NULL ou <= now())
      - não estão em estado de erro permanente (tentativas < 5)
    Ordenadas por prioridade DESC, depois por criado_em ASC (FIFO).
    """
    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        response = (
            client.table("ofertas")
            .select(
                "id, titulo, preco_original, preco_desconto, "
                "percentual_desconto, url_produto, url_imagem, "
                "cupom, tag_afiliado, tentativas"
            )
            .eq("enviado", False)
            .lt("tentativas", 5)
            .or_(f"agendado_para.is.null,agendado_para.lte.{now_iso}")
            .order("prioridade", desc=True)
            .order("criado_em", desc=False)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception as exc:
        logger.error("Erro ao buscar ofertas pendentes: %s", exc)
        return []


def mark_as_sent(offer_id: str) -> bool:
    """
    Marca a oferta como enviada com sucesso.
    Usa .eq("enviado", False) como guard para evitar double-write
    em caso de race condition (execução paralela futura).
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
        # Fallback: leitura + escrita (não-atômico, mas aceitável para este caso)
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
                "Falha no fallback de incremento para %s: %s", offer_id, inner
            )
