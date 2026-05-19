import asyncio
import logging
import threading
import http.server
import socketserver
import re
import os
from telegram import Bot
from config import Config
from database import DatabaseManager
from aliexpress import AliExpressClient

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def parse_price(price_str: str) -> float:
    """Filtra e converte strings de preço em valores numéricos decimais."""
    if not price_str:
        return 0.0
    clean_str = re.sub(r'[^\d,.]', '', price_str)
    if ',' in clean_str and '.' in clean_str:
        clean_str = clean_str.replace('.', '').replace(',', '.')
    elif ',' in clean_str:
        clean_str = clean_str.replace(',', '.')
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

class OffersBot:
    def __init__(self):
        Config.validate()
        self.telegram_bot = Bot(token=Config.TELEGRAM_TOKEN)
        self.db = DatabaseManager()
        self.ali_client = AliExpressClient()
        self.chat_id = Config.ID_DO_GRUPO

    async def process_discovered_product(self, prod: dict):
        """Gerencia a esteira de validação, gravação em banco e postagem no Telegram."""
        product_id = prod["id"]
        
        if self.db.is_offer_posted(product_id):
            return

        current_price_float = parse_price(prod["price"])
        if current_price_float <= 0:
            return

        # Consulta métricas históricas no PostgreSQL
        avg_price, _ = self.db.get_price_metrics(product_id)
        self.db.save_price_if_changed(product_id, current_price_float)

        discount_msg = ""
        if avg_price and avg_price > 0:
            discount_percent = ((avg_price - current_price_float) / avg_price) * 100
            
            if discount_percent >= 6:  # Posta se o preço cair 6% ou mais em relação à média monitorada
                logger.info(f"🔥 QUEDA RECONHECIDA: {product_id} com {discount_percent:.1f}% de desconto real.")
                discount_msg = f"📉 *Desconto Real:* {discount_percent:.1f}% mais barato que a média registrada!"
            else:
                return
        else:
            # Fase de Aquecimento (Warmup): Registra na base e faz a postagem inicial do radar
            logger.info(f"📦 ITEM INTEGRADO AO RADAR: ID {product_id}")
            discount_msg = "🌟 *Radar Ativo:* Produto localizado pelo sistema e inserido no monitoramento de preços!"

        # Converte a URL do produto usando a API de links autorizada
        affiliate_url = self.ali_client.generate_affiliate_link(prod["url"])

        message_text = f"""🛠️ *{prod['title']}*

💰 Preço Achado: *{prod['price']}*
{discount_msg}

🎯 *Filtro:* Produto verificado de forma autônoma nos servidores do AliExpress.

🛒 Compre com segurança pelo Link de Afiliado:
[Clique aqui para abrir a Oferta no AliExpress]({affiliate_url})

⚠️ *Nota:* Os estoques promocionais são limitados e variam com frequência."""

        try:
            if prod.get("image") and "http" in prod["image"]:
                await self.telegram_bot.send_photo(chat_id=self.chat_id, photo=prod["image"], caption=message_text, parse_mode='Markdown')
            else:
                await self.telegram_bot.send_message(chat_id=self.chat_id, text=message_text, parse_mode='Markdown')
            
            self.db.save_posted_offer(product_id, prod['title'], affiliate_url)
            logger.info(f"✅ Post enviado com sucesso: {product_id}")
        except Exception as e:
            logger.error(f"Erro ao enviar postagem para o Telegram: {e}")

def run_mock_server():
    port = int(os.getenv('PORT', 10000))
    handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            httpd.serve_forever()
    except Exception:
        pass

async def main():
    bot = OffersBot()
    logger.info("Bot de Busca Geral V5 (Híbrido) Inicializado no Render!")
    
    while True:
        try:
            # Executa a busca na API com o bypass inteligente acoplado
            produtos = bot.ali_client.search_niche_products_via_api()
            
            if produtos:
                for item in produtos:
                    await bot.process_discovered_product(item)
                    await asyncio.sleep(5)  # Intervalo de segurança anti-bloqueio de IP
            else:
                logger.warning("Nenhum produto coletado neste turno. Aguardando reajuste do ciclo.")

            logger.info("Varredura de nicho finalizada. Aguardando 5 minutos para o próximo ciclo...")
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Falha interna no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    asyncio.run(main())
