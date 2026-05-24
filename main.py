import asyncio
import os
import logging
from aiohttp import web
import telegram
from supabase import create_client

# Configuração
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot.main")

# Inicialização
supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))
bot = telegram.Bot(token=os.environ.get("TELEGRAM_TOKEN"))
CHAT_ID = os.environ.get("CHAT_ID")

# --- Lógica do Bot (SEM FILTRO) ---
async def processar_lote():
    try:
        # Busca 5 ofertas não enviadas de qualquer tipo
        response = supabase.table("ofertas").select("*").eq("enviado", False).limit(5).execute()
        
        for item in response.data:
            mensagem = (
                f"🔥 *Nova Oferta:* {item['titulo']}\n"
                f"💰 Preço: R$ {item['preco_desconto']:.2f}\n\n"
                f"👉 [COMPRAR NO ALIEXPRESS]({item['url_produto']})"
            )
            
            # Envia diretamente sem checar termos
            await bot.send_message(chat_id=CHAT_ID, text=mensagem, parse_mode='Markdown')
            
            # Marca como enviado
            supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
            logger.info(f"✅ ENVIADO: {item['titulo'][:50]}...")
            
            # Pequena pausa para evitar bloqueio da API do Telegram
            await asyncio.sleep(1) 
            
    except Exception as e:
        logger.error(f"Erro no processamento: {e}")

async def bot_loop():
    while True:
        await processar_lote()
        await asyncio.sleep(60)

# --- Lógica do Servidor Web (para o Render) ---
async def handle(request):
    return web.Response(text="Bot está operando e processando todas as ofertas.")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Servidor Web ativo na porta {port}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    loop.run_until_complete(bot_loop())
