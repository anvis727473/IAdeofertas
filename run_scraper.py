import asyncio
import logging
import os
import sys
from aiohttp import web
from database import verify_connection
from search_engine import AliExpressSearchEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bot.run_scraper")

SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", "600"))
KEYWORDS = ["mechanical keyboard wireless", "gaming mouse rgb", "ssd nvme 1tb", "wifi 6e router", "gan charger 65w"]

async def handle_health(request):
    return web.Response(text="SCRAPER_OK", content_type="text/plain")

async def start_server():
    app = web.Application()
    app.router.add_get('/', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Servidor HTTP Assíncrono de Produção ativo na porta {port}")
    return runner

async def main_loop():
    if not await verify_connection():
        sys.exit(1)
    
    api_key = os.environ.get("ALI_API_KEY", "")
    engine = AliExpressSearchEngine(api_key=api_key)
    web_runner = await start_server()

    try:
        while True:
            logger.info("Executando ciclo paralelo de ingestão...")
            count = await engine.run_parallel_discovery(KEYWORDS, target_pages=2)
            logger.info(f"Ciclo concluído. {count} itens inseridos com sucesso.")
            await asyncio.sleep(SCRAPE_INTERVAL)
    except Exception as e:
        logger.exception(f"Erro fatal no Scraper Server: {e}")
    finally:
        await engine.close()
        await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main_loop())
