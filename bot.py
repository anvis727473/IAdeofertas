import asyncio
import logging
import os
from aiohttp import web
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config import Config
from database import DatabaseManager
from aliexpress import AliExpressClient

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

class OffersBot:
    def __init__(self):
        Config.validate()
        self.telegram_bot = Bot(token=Config.TELEGRAM_TOKEN)
        self.db = DatabaseManager()
        self.ali_client = AliExpressClient()
        self.chat_id = Config.ID_DO_GRUPO

    def _build_message(self, product, affiliate_url, is_price_drop=False):
        stars = "⭐" * min(int(product.rating), 5)
        
        # Tag especial se houver queda de preço
        header = "🚨 *ALERTA DE QUEDA DE PREÇO* 🚨" if is_price_drop else "🔥 *NOVA OFERTA ENCONTRADA*"
        
        return f"""
{header}

🛒 *{product.title}*

💰 *{product.price_text()}*

🔥 {product.sold_count} vendidos
{stars} ({product.rating})
🔎 Nicho: {product.keyword}

👇 COMPRE AGORA (Link Seguro) 👇
"""

    async def process_product(self, product):
        last_price = await self.db.get_last_price(product.id)
        is_price_drop = False
        
        if last_price is not None:
            # 📉 INTELIGÊNCIA: Se o preço atual for 15% MENOR que o histórico, reposta!
            if product.price_value <= (last_price * 0.85):
                is_price_drop = True
                logger.info(f"📉 Queda de preço detectada: {product.id} (De {last_price} para {product.price_value})")
            else:
                # Já foi postado e não teve queda significativa
                return

        affiliate_url = self.ali_client.generate_affiliate_link(product.url)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛒 Comprar Agora", url=affiliate_url)
        ]])
        message = self._build_message(product, affiliate_url, is_price_drop)

        try:
            await self.telegram_bot.send_photo(
                chat_id=self.chat_id,
                photo=product.image,
                caption=message,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            
            # Persistência Assíncrona
            await self.db.save_posted_offer(product)
            await self.db.save_price(product.id, product.price_value)
            
            logger.info(f"✅ Produto enviado: {product.title}")
            await asyncio.sleep(5)  # Respeita o rate limit do Telegram
            
        except Exception as e:
            logger.error(f"❌ Erro Telegram ao enviar {product.id}: {e}")


# --- HEALTH SERVER ASSÍNCRONO PARA O RENDER ---
async def health_handler(request):
    return web.Response(text="Bot Enterprise Operacional e Rodando! 🚀")

async def start_health_server():
    app = web.Application()
    app.router.add_get('/', health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Health server aiohttp rodando na porta {port}")


# --- LOOP PRINCIPAL ---
async def main():
    bot = OffersBot()
    await bot.db.connect()
    await start_health_server()
    
    logger.info("🚀 Bot Enterprise Iniciado com Sucesso!")

    while True:
        try:
            keywords = await bot.db.get_active_keywords()
            if not keywords:
                logger.warning("Nenhum nicho ativo no banco. Aguardando...")
                await asyncio.sleep(60)
                continue

            for keyword in keywords:
                logger.info(f"🔎 Garimpando nicho: {keyword}")
                products = await bot.ali_client.search_products(keyword)
                
                # Pegar apenas os TOP 3 do nicho para evitar spam no grupo
                top_products = products[:3] 
                
                for product in top_products:
                    await bot.process_product(product)
                
                # Backoff / Sleep entre keywords para não engatilhar o Anti-Bot
                await asyncio.sleep(15)

            # Ciclo de espera antes da próxima rodada completa
            logger.info("💤 Ciclo finalizado. Descansando por 30 minutos...")
            await asyncio.sleep(1800)

        except Exception as e:
            logger.exception(f"🔥 Erro crítico no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    # Roda a aplicação inteira no loop nativo do Python de forma otimizada
    asyncio.run(main())
