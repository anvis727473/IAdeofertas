"""
run_scraper.py — Orquestrador e Servidor Dummy para o Web Service do Raspador
"""

import asyncio
import logging
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer

import database
from search_engine import AliExpressSearchEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("bot.run_scraper")

# Intervalo entre as varreduras do AliExpress (em segundos). Ex: 600 segundos = 10 minutos
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", "600"))

# IDs das categorias do AliExpress que você quer monitorar (adicione ou mude aqui)
CATEGORIES_TO_MONITOR = ['100003235', '509', '200002293'] 

def run_dummy_server():
    """ Abre a porta exigida pelo Render para passar no Health Check do Web Service """
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
        logger.info("Servidor Dummy do Raspador online na porta %d para o Render.", port)
        server.serve_forever()
    except Exception as exc:
        logger.error("Falha no servidor dummy do raspador: %s", exc)

async def main_loop():
    """ Loop contínuo de raspagem e alimentação do banco Supabase """
    logger.info("Inicializando Motor de Busca do AliExpress...")
    
    try:
        supabase_client = database.get_client()
        api_key = os.environ["ALI_API_KEY"]
    except KeyError as e:
        logger.critical("Variável de ambiente obrigatória ausente: %s", e)
        sys.exit(1)
        
    engine = AliExpressSearchEngine(supabase_client, api_key, max_concurrent_requests=3)

    try:
        while True:
            logger.info("Iniciando varredura ativa de ofertas nas categorias %s...", CATEGORIES_TO_MONITOR)
            # Executa a busca paralela que reconstrói os dados com UUID
            inserted_count = await engine.run_parallel_discovery(CATEGORIES_TO_MONITOR, target_pages=2)
            logger.info("Ciclo concluído. %d novas ofertas injetadas no Supabase.", inserted_count)
            
            logger.info("Aguardando cooldown de %d segundos para o próximo ciclo...", SCRAPE_INTERVAL)
            await asyncio.sleep(SCRAPE_INTERVAL)
    except Exception as exc:
        logger.exception("Erro crítico no loop do raspador: %s", exc)
    finally:
        await engine.close()

if __name__ == "__main__":
    # 1. Aloca a thread de rede para satisfazer o Port Scan do Render
    t = threading.Thread(target=run_dummy_server, daemon=True)
    t.start()
    
    # 2. Inicia o loop infinito de busca assíncrona
    asyncio.run(main_loop())
