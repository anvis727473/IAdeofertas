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

# =============================================================================
# ARQUITETURA DE MATRIZ DE NICHOS (Alta Conversão & Público Alvo Definido)
# =============================================================================
NICHOS = {
    "PERIFERICOS_GAMING": [
        "mechanical keyboard wireless", "gaming mouse rgb", "mousepad gaming large", 
        "gaming headset 7.1", "wireless controller pc", "streaming microphone usb",
        "keycaps mechanical keyboard", "coiled cable keyboard"
    ],
    "HARDWARE_E_UPGRADE": [
        "ssd nvme 1tb", "ssd sata 1tb", "external ssd portable", "ram ddr4 16gb", 
        "ram ddr5 desktop", "cpu cooler argb", "pc case fans pack", "thermal paste high performance"
    ],
    "PRODUTIVIDADE_HOME_OFFICE": [
        "ergonomic vertical mouse", "split mechanical keyboard", "laptop stand aluminum", 
        "monitor light bar", "dual monitor arm", "webcam 2k autolight", "desk pad leather"
    ],
    "CONECTIVIDADE_E_REDE": [
        "wifi 6e router", "usb c hub baseus", "docking station dual monitor", 
        "bluetooth 5.4 adapter", "wifi usb adapter high gain", "ethernet cable cat8 flat"
    ],
    "ENERGIA_E_ACESSORIOS_TECH": [
        "gan charger 65w", "power bank 20000mah 100w", "magnetic wireless charger", 
        "cable organizer sleeve", "portable monitor touch"
    ]
}

# Achata o dicionário em uma lista linear para o loop de varredura manter compatibilidade
KEYWORDS = [termo for subnicho in NICHOS.values() for termo in subnicho]

def run_dummy_server():
    """ Mantém a porta do Render Web Service aberta para evitar Timeout """
    class HealthCheckHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Raspador Ativo e Operando")
            
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()

    port = int(os.environ.get("PORT", 10000))
    logger.info("Servidor Dummy online na porta %d", port)
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as exc:
        logger.error("Falha no servidor dummy: %s", exc)

async def main_loop():
    """ Loop contínuo de busca segmentada """
    logger.info("Inicializando Motor de Busca com Matriz de Nichos...")
    
    try:
        supabase_client = database.get_client()
        api_key = os.environ["ALI_API_KEY"]
    except KeyError as e:
        logger.critical("Erro: Variável de ambiente ausente: %s", e)
        sys.exit(1)
        
    engine = AliExpressSearchEngine(supabase_client, api_key)

    try:
        while True:
            logger.info("Iniciando varredura expandida com %d palavras-chave estruturadas.", len(KEYWORDS))
            
            # Dispara a busca paralela utilizando a estrutura corrigida do search_engine
            inserted_count = await engine.run_parallel_discovery(KEYWORDS, target_pages=2)
            
            logger.info("Ciclo concluído. %d novos produtos injetados no funil.", inserted_count)
            logger.info("Aguardando %d segundos até o próximo mapeamento...", SCRAPE_INTERVAL)
            
            await asyncio.sleep(SCRAPE_INTERVAL)
    except Exception as exc:
        logger.exception("Erro crítico no loop de execução: %s", exc)
    finally:
        await engine.close()

if __name__ == "__main__":
    # Inicialização da infraestrutura de rede dummy em thread isolada
    t = threading.Thread(target=run_dummy_server, daemon=True)
    t.start()
    
    # Execução do loop assíncrono principal
    asyncio.run(main_loop())
