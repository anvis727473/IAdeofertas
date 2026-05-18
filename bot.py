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

# Configuração de Logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def parse_price(price_str: str) -> float:
    """Converte strings de preço em float matemático."""
    clean_str = re.sub(r'[^\d,.]', '', price_str).replace(',', '.')
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

    async def post_new_offer(self, product_id: str, title: str, original_url: str, price_str: str, image_url: str = None):
        """Avalia inteligência de preço, monetiza o link e posta no Telegram."""
        
        # 1. Filtro Anti-Duplicidade geral
        if self.db.is_offer_posted(product_id):
            return False

        # 2. Sanitização e verificação de preço
        current_price_float = parse_price(price_str)
        if current_price_float <= 0:
            return False
            
        self.db.save_price_if_changed(product_id, current_price_float)

        # 3. Inteligência de Média de Preços (Dispara posts em quedas de valor)
        avg_price, min_price = self.db.get_price_metrics(product_id)
        discount_msg = ""
        
        if avg_price and avg_price > 0 and avg_price != current_price_float:
            discount_percent = ((avg_price - current_price_float) / avg_price) * 100
            
            if discount_percent >= 10:
                logger.info(f"🔥 PROMOÇÃO APROVADA: {product_id} com {discount_percent:.1f}% de desconto real.")
                discount_msg = f"📉 *Desconto Real:* {discount_percent:.1f}% mais barato que a média do último mês!"
            else:
                return False
        else:
            logger.info(f"Monitorando item no radar de preços: {product_id}")
            discount_msg = "🌟 *Radar de Hardware Ativo!* Acompanhando variações de preço deste produto."

        # 4. Geração do Link de Afiliado (Método 100% estável e autorizado)
        affiliate_url = self.ali_client.generate_affiliate_link(original_url)

        # 5. Montagem do Template do Canal
        message_text = f"""🛠️ *{title}*

💰 Preço Agora: *{price_str}*
{discount_msg}

🔌 *Categoria:* Hardware, Tecnologia & Utensílios

🛒 Compre com segurança pelo link de afiliado:
[Clique aqui para abrir no AliExpress]({affiliate_url})

⚠️ *Nota:* Estoques promocionais esgotam rápido. O preço pode alterar a qualquer momento!"""

        # 6. Disparo Seguro para o Canal
        try:
            if image_url:
                await self.telegram_bot.send_photo(chat_id=self.chat_id, photo=image_url, caption=message_text, parse_mode='Markdown')
            else:
                await self.telegram_bot.send_message(chat_id=self.chat_id, text=message_text, parse_mode='Markdown', disable_web_page_preview=False)
            
            self.db.save_posted_offer(product_id, title, affiliate_url)
            logger.info(f"[Sucesso] Produto {product_id} publicado no canal.")
            return True

        except Exception as e:
            logger.error(f"Erro ao disparar mensagem para o Telegram: {e}")
            return False

def run_mock_server():
    """Mantém a porta aberta exigida pelo Render."""
    port = int(os.getenv('PORT', 10000))
    handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            httpd.serve_forever()
    except Exception:
        pass

async def main():
    bot = OffersBot()
    logger.info("Bot de Ofertas Inteligente (Estável) ativado no Render!")
    
    while True:
        try:
            logger.info("Iniciando ciclo automático de verificação de radar...")
            
            # Puxa a lista mapeada de alta conversão
            lista_produtos = bot.ali_client.get_monitored_products()
            
            for prod in lista_produtos:
                await bot.post_new_offer(
                    product_id=prod["id"],
                    title=prod["title"],
                    original_url=prod["url"],
                    price_str=prod["price"],
                    image_url=prod["image"]
                )
                await asyncio.sleep(2)
            
            logger.info("Varredura concluída com sucesso. Aguardando 5 minutos para o próximo ciclo...")
            await asyncio.sleep(300)
            
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    asyncio.run(main())
