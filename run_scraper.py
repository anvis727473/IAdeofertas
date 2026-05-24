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

# Intervalo entre as varreduras (padrão: 600s = 10 minutos)
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", "600"))

# Lista de palavras-chave para garimpar (pode alterar conforme o seu nicho)
KEYWORDS = [
    # Periféricos Gaming
    "mechanical keyboard", "gaming mouse", "rgb gaming mouse pad", "gaming headset", 
    "wireless gaming controller", "gaming microphone",
    
    # Armazenamento e Hardware
    "ssd nvme 1tb", "external ssd", "usb flash drive 256gb", "ram ddr4 3200", 
    "ram ddr5", "pc case fan rgb", "cpu cooler",
    
    # Conectividade e Acessórios
    "usb c hub", "docking station", "wifi 6 router", "wireless wifi adapter", 
    "bluetooth 5.3 adapter", "ethernet cable cat8", "usb c to hdmi adapter",
    
    # Home Office e Produtividade
    "laptop stand", "vertical mouse", "ergonomic keyboard", "webcam 1080p", 
    "monitor light bar", "monitor arm mount", "desktop microphone",
    
    # Gadgets Tech
    "power bank 20000mah", "portable monitor", "usb c charger 65w", 
    "digital drawing tablet", "smart watch fitness"
]

def run_dummy_server():
    """ Abre a porta 10000 para o Render não derrubar o serviço """
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
        logger.info("Servidor Dummy online na porta %d", port)
        server.serve_forever()
    except Exception as exc:
        logger.error("Falha no servidor dummy: %s", exc)

async def main_loop():
    """ Loop contínuo de busca por palavras-chave """
    logger.info("Inicializando Motor de Busca com Palavras-Chave...")
    
    try:
        supabase_client = database.get_client()
        api_key = os.environ["ALI_API_KEY"]
    except KeyError as e:
        logger.critical("Erro: Variável de ambiente ausente: %s", e)
        sys.exit(1)
        
    engine = AliExpressSearchEngine(supabase_client, api_key)

    try:
        while True:
            logger.info("Iniciando varredura com: %s", KEYWORDS)
            
            # Executa a descoberta paralela passando as palavras-chave
            inserted_count = await engine.run_parallel_discovery(KEYWORDS, target_pages=2)
            
            logger.info("Ciclo concluído. %d novos produtos injetados.", inserted_count)
            logger.info("Aguardando %d segundos...", SCRAPE_INTERVAL)
            
            await asyncio.sleep(SCRAPE_INTERVAL)
    except Exception as exc:
        logger.exception("Erro crítico no loop: %s", exc)
    finally:
        await engine.close()

if __name__ == "__main__":
    # Inicia o servidor dummy em uma Thread separada
    t = threading.Thread(target=run_dummy_server, daemon=True)
    t.start()
    
    # Inicia o loop de busca no processo principal
    asyncio.run(main_loop())
