import logging
import os
from supabase import create_client, Client

logger = logging.getLogger("bot.database")

_supabase_instance: Client = None

def get_client() -> Client:
    """Singleton de conexão com tratamento de erro simplificado."""
    global _supabase_instance
    if _supabase_instance is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        
        if not url or not key:
            raise ValueError("Variáveis SUPABASE_URL ou SUPABASE_KEY não configuradas.")
            
        _supabase_instance = create_client(url, key)
        
        # Teste de conexão universal (funciona em todas as versões do SDK)
        try:
            _supabase_instance.table("ofertas").select("id").limit(1).execute()
            logger.info("Supabase conectado com sucesso.")
        except Exception as e:
            logger.error(f"Falha ao validar conexão com Supabase: {e}")
            raise e
            
    return _supabase_instance
