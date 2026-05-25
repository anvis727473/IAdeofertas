import logging
import os
import asyncio
from typing import Optional
from supabase import create_client, Client

logger = logging.getLogger("bot.database")
_supabase_instance: Optional[Client] = None

def get_client() -> Client:
    global _supabase_instance
    if _supabase_instance is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL ou SUPABASE_KEY ausentes no ambiente.")
        _supabase_instance = create_client(url, key)
        logger.info("Instância do cliente Supabase (Singleton) configurada.")
    return _supabase_instance

async def verify_connection() -> bool:
    """Valida a conexão em thread isolada para não travar o Event Loop do servidor."""
    client = get_client()
    try:
        await asyncio.to_thread(
            lambda: client.table("ofertas").select("id").limit(1).execute()
        )
        logger.info("Conexão estável estabelecida com o Supabase.")
        return True
    except Exception as e:
        logger.error(f"Erro crítico de Handshake com o Supabase: {e}")
        return False
