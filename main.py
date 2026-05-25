import asyncio
import os
import logging
import sys
from aiohttp import web
from telegram import Bot
from telegram.constants import ParseMode
from database import get_client, verify_connection
from tenacity import retry, stop_after_attempt, wait_exponential

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bot.main")

class OfferDispatcher:
    def __init__(self):
        self.supabase = get_client()
        self.bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
        self.chat_id = os.environ["CHAT_ID"]

    def _format_html(self, item) -> str:
        titulo = item['titulo'].replace("<", "&lt;").replace(">", "&gt;")
        return (
            f"🔥 <b>{titulo[:100]}</b>\n\n"
            f"💰 De: <s>R$ {float(item.get('preco_original', 0)):.2f}</s>\n"
            f"🏷 Por: <code>R$ {float(item['preco_desconto']):.2f}</code>\n"
            f"🎟 Cupom: <code>{item.get('cupom') or 'N/A'}</code>\n\n"
            f"👉 <a href='{item['url_produto']}'>CLIQUE AQUI PARA COMPRAR</a>"
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def send_to_telegram(self, msg: str):
        await self.bot.send_message(chat_id=self.chat_id, text=msg, parse_mode=ParseMode.HTML)

    async def process_batch(self):
        # Isola leitura síncrona do Supabase
        res = await asyncio.to_thread(
            lambda: self.supabase.table("ofertas").select("*").eq("enviado", False).order("prioridade", desc=True).limit(5).execute()
        )
        
        for item in res.data:
            preco_orig = float(item.get('preco_original', 0))
            preco_desc = float(item['preco_desconto'])
            
            # Unificação das Regras de Negócio: Filtro de Viabilidade Comercial Estrito antes do envio
            if item.get('cupom') or (preco_desc < preco_orig * 0.9):
                try:
                    await self.send_to_telegram(self._format_html(item))
                    await asyncio.to_thread(
                        lambda: self.supabase.table("ofertas").update({"enviado": True, "enviado_em": "now()"}).eq("id", item['id']).execute()
                    )
                    logger.info(f"Oferta despachada: {item['titulo'][:30]}")
                except Exception as e:
                    logger.error(f"Falha de rede ao enviar item, incrementando tentativas: {e}")
                    await asyncio.to_thread(
                        lambda: self.supabase.rpc("increment_tentativas", {"offer_id": item['id']}).execute()
                    )
            else:
                # Descarta do funil direto para manter a fila limpa
                await asyncio.to_thread(
                    lambda: self.supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
                )
            await asyncio.sleep(2.5) # Flood Control nativo da Telegram API

async def handle_health(request):
    return web.Response(text="WORKER_OK", content_type="text/plain")

async def start_server():
    app = web.Application()
    app.router.add_get('/', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    return runner

async def main():
    if not await verify_connection():
        sys.exit(1)
    
    dispatcher = OfferDispatcher()
    web_runner = await start_server()
    poll_interval = int(os.environ.get("POLL_INTERVAL", "60"))
    
    try:
        while True:
            await dispatcher.process_batch()
            await asyncio.sleep(poll_interval)
    except Exception as e:
        logger.critical(f"Falha catastrófica no loop principal do Worker: {e}")
    finally:
        await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
