import asyncio
import os
import logging
import sys
from aiohttp import web
import telegram

# Importações dos seus módulos internos
import database
from search_engine import AliExpressSearchEngine

# Configuração de Logging unificada
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("bot.unified")

# --- Configurações e Inicialização ---
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", "600"))
CHAT_ID = os.environ.get("CHAT_ID")

# Inicializa as conexões usando o seu módulo database.py
supabase = database.get_client()
bot = telegram.Bot(token=os.environ.get("TELEGRAM_TOKEN"))

# Matriz de Nichos (Trazida do seu antigo run_scraper.py)
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
        "ergonomic vertical mouse", "usb c hub multi port", "monitor light bar", "laptop stand aluminum"
    ]
}
KEYWORDS = [kw for nicho in NICHOS.values() for kw in nicho]


# =============================================================================
# 1. LÓGICA DO BOT (ENVIO PARA O TELEGRAM)
# =============================================================================
async def processar_lote():
    try:
        # Busca 5 ofertas pendentes no Supabase
        response = supabase.table("ofertas").select("*").eq("enviado", False).limit(5).execute()
        
        for item in response.data:
            mensagem = (
                f"🔥 *Nova Oferta:* {item['titulo']}\n"
                f"💰 Preço: R$ {item['preco_desconto']:.2f}\n\n"
                f"👉 [COMPRAR NO ALIEXPRESS]({item['url_produto']})"
            )
            
            # Envia para o canal
            await bot.send_message(chat_id=CHAT_ID, text=mensagem, parse_mode='Markdown')
            
            # Marca como enviado no banco
            supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
            logger.info(f"✅ TELEGRAM: Enviado com sucesso -> {item['titulo'][:40]}...")
            
            # Anti-flood da API do Telegram
            await asyncio.sleep(1) 
            
    except Exception as e:
        logger.error(f"Erro no processamento de envio: {e}")

async def bot_loop():
    logger.info("Loop do Telegram Bot iniciado (Frequência: 60s).")
    while True:
        await processar_lote()
        await asyncio.sleep(60)


# =============================================================================
# 2. LÓGICA DO BUSCADOR (SCRAPER DO ALIEXPRESS)
# =============================================================================
async def scraper_loop():
    logger.info("Loop do Buscador AliExpress iniciado.")
    try:
        api_key = os.environ["ALI_API_KEY"]
    except KeyError:
        logger.critical("Erro: Variável de ambiente 'ALI_API_KEY' não configurada. O Buscador não iniciará.")
        return

    # Inicializa o motor de busca do seu search_engine.py
    engine = AliExpressSearchEngine(supabase, api_key)

    try:
        while True:
            logger.info(f"Buscador: Iniciando varredura de {len(KEYWORDS)} palavras-chave...")
            
            # Executa a busca mapeando 2 páginas por termo
            inserted_count = await engine.run_parallel_discovery(KEYWORDS, target_pages=2)
            
            logger.info(f"Buscador: Varredura concluída. {inserted_count} novas ofertas salvas.")
            logger.info(f"Buscador: Próxima varredura em {SCRAPE_INTERVAL} segundos.")
            
            await asyncio.sleep(SCRAPE_INTERVAL)
    except Exception as e:
        logger.exception(f"Erro crítico no loop do buscador: {e}")
    finally:
        await engine.close()


# =============================================================================
# 3. SERVIDOR WEB (EVITAR TIMEOUT NO RENDER) E ORQUESTRADOR
# =============================================================================
async def handle(request):
    return web.Response(text="Bot Unificado Online: Buscador e Envio operando concorrentemente.")

async def main():
    # Configura e inicia o servidor web na porta correta
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Servidor Web de prontidão na porta {port}")

    # O "pulo do gato": Executa as duas tarefas ao mesmo tempo infinitamente
    await asyncio.gather(
        bot_loop(),
        scraper_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Aplicação encerrada pelo usuário.")
