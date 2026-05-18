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

# Configuração global de Logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def parse_price(price_str: str) -> float:
    """Converte strings como 'R$ 289,90' para float matemático 289.90"""
    clean_str = re.sub(r'[^\d,]', '', price_str).replace(',', '.')
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
        """Avalia a inteligência de preço, orquestra links e dispara a oferta."""
        
        # 1. Evita duplicatas gerais
        if self.db.is_offer_posted(product_id):
            logger.info(f"[Duplicada] Produto {product_id} já postado. Ignorando.")
            return False

        # 2. Converte o preço e salva no histórico de inteligência (somente se mudou)
        current_price_float = parse_price(price_str)
        if current_price_float <= 0:
            logger.error(f"Preço inválido para o produto {product_id}: {price_str}")
            return False
            
        self.db.save_price_if_changed(product_id, current_price_float)

        # 3. Calcula se a oferta é boa (Mínimo de 10% de desconto)
        avg_price, min_price = self.db.get_price_metrics(product_id)
        discount_msg = ""
        
        if avg_price and avg_price > 0 and avg_price != current_price_float:
            discount_percent = ((avg_price - current_price_float) / avg_price) * 100
            
            if discount_percent >= 10:
                logger.info(f"OFERTA APROVADA: {product_id} está com {discount_percent:.1f}% de desconto da média!")
                discount_msg = f"📉 *Desconto Real:* {discount_percent:.1f}% mais barato que a média do último mês!"
            else:
                logger.info(f"OFERTA REJEITADA: {product_id} com desconto fraco de {discount_percent:.1f}%.")
                return False
        else:
            logger.info(f"Novo monitoramento: Produto {product_id} cadastrado na base.")
            discount_msg = "🌟 *Novo Radar de Hardware!* Começando a monitorar este item."

        # 4. Gera link de afiliado
        affiliate_url = self.ali_client.generate_affiliate_link(original_url)

        # 5. Formata o template provando o desconto real
        message_text = f"""🛠️ *{title}*

💰 Preço Agora: *{price_str}*
{discount_msg}

🔌 *Categoria:* Hardware, Tecnologia & Utilidades

🛒 Compre com segurança pelo link de afiliado:
[Clique aqui para abrir no AliExpress]({affiliate_url})

⚠️ *Nota:* Os estoques de eletrônicos costumam esgotar rápido. O preço pode alterar a qualquer momento!"""

        # 6. Dispara pelo Telegram
        try:
            if image_url:
                await self.telegram_bot.send_photo(chat_id=self.chat_id, photo=image_url, caption=message_text, parse_mode='Markdown')
            else:
                await self.telegram_bot.send_message(chat_id=self.chat_id, text=message_text, parse_mode='Markdown', disable_web_page_preview=False)
            
            self.db.save_posted_offer(product_id, title, affiliate_url)
            return True

        except Exception as e:
            logger.error(f"Falha ao enviar ao Telegram: {e}")
            return False

def run_mock_server():
    """Servidor web fantasma dinâmico para satisfazer a checagem do Render Web Service."""
    # Coleta a porta que o Render exige através da variável de ambiente, usando 10000 como fallback
    port = int(os.getenv('PORT', 10000))
    handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            logger.info(f"Servidor fantasma rodando com sucesso na porta {port} para o Render.")
            httpd.serve_forever()
    except Exception as e:
        logger.warning(f"Aviso no servidor fantasma: {e}")

async def main():
    bot = OffersBot()
    logger.info("Bot de Ofertas Inteligente ativado no Render!")
    
    while True:
        try:
            logger.info("Iniciando ciclo de varredura...")
            
            # ALTERADO: Novo ID para testar o fluxo de histórico de preço sem ser bloqueado como duplicado
            await bot.post_new_offer(
                product_id="1005007111111",
                title="Roteador Xiaomi AX3000T Wi-Fi 6 - Smart Home",
                original_url="https://pt.aliexpress.com/item/1005007111111.html",
                price_str="R$ 149,90",
                image_url="https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?w=500"
            )
            
            # Tempo de varredura: 5 minutos (300 segundos)
            logger.info("Ciclo finalizado. Aguardando 5 minutos para a próxima busca...")
            await asyncio.sleep(300)
            
        except Exception as e:
            logger.error(f"Erro crítico: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    asyncio.run(main())
