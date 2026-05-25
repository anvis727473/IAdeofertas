import logging
import os
import time
from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("bot.database")

_supabase_instance: Client = None

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_client() -> Client:
    """ 
    Conexão resiliente: tenta 5 vezes com espera exponencial 
    caso o DNS falhe na inicialização.
    """
    global _supabase_instance
    if _supabase_instance is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        
        if not url or not key:
            raise ValueError("Variáveis de ambiente SUPABASE_URL ou KEY ausentes!")
            
        logger.info("Tentando conectar ao Supabase...")
        try:
            _supabase_instance = create_client(url, key)
            # Teste de conectividade simples
            _supabase_instance.table("ofertas").select("id", count="exact", head=True).execute()
            logger.info("Supabase client conectado com sucesso.")
        except Exception as e:
            logger.error(f"Falha na conexão com Supabase: {e}")
            raise e # O 'tenacity' capturará isso e tentará novamente
            
    return _supabase_instance
