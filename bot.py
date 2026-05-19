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

    async def process_api_product(self, prod: dict):
        """Avalia o produto vindo diretamente da busca da API."""
        product_id = prod["id"]
        
        # 1. Filtro Anti-Duplicidade do Canal
        if self.db.is_offer_posted(product_id):
            return

        current_price_float = parse_price(prod["price"])
        if current_price_float <= 0:
            return

        # 2. Registra o preço no banco e analisa histórico
        avg_price, _ = self.db.get_price_metrics(product_id)
        self.db.save_price_if_changed(product_id, current_price_float)

        # 3. Análise de Desconto (Fase Warmup inclusa)
        discount_msg = ""
        if avg_price and avg_price > 0:
            discount_percent = ((avg_price - current_price_float) / avg_price) * 100
            
            if discount_percent >= 8:
                logger.info(f"🔥 PROMOÇÃO DETECTADA VIA API: {product_id} com {discount_percent:.1f}% de desconto.")
                discount_msg = f"📉 *Desconto Real:* {discount_percent:.1f}% mais barato que a média recente!"
            else:
                return  # Preço normal, mantém apenas guardado no banco salvando o histórico
        else:
            logger.info(f"🌟 NOVO ITEM CAPTURADO VIA API: {product_id} adicionado ao Radar.")
            discount_msg = f"⭐ *Radar de Preço Ativo!* Item adicionado à nossa base de monitoramento autônomo."

        # 4. Converte o link encontrado pela API para o seu Link de Afiliado
        affiliate_url = self.ali_client.generate_affiliate_link(prod["url"])

        # 5. Criação do Post
        message_text = f"""🛠️ *{prod['title']}*

💰 Preço Encontrado: *{prod['price']}*
{discount_msg}

🔌 *Nicho:* Tecnologia, Hardware & Gadgets

🛒 Compre com segurança pelo Link Oficial de Afiliado:
[Clique aqui para abrir a Oferta no AliExpress]({affiliate_url})

⚠️ *Nota:* Estoques e preços são dinâmicos e controlados diretamente pelas lojas no AliExpress."""

        # 6. Envio para o Telegram
        try:
            if prod.get("image"):
                await self.telegram_bot.send_photo(chat_id=self.chat_id, photo=prod["image"], caption=message_text, parse_mode='Markdown')
            else:
                await self.telegram_bot.send_message(chat_id=self.chat_id, text=message_text, parse_mode='Markdown')
            
            self.db.save_posted_offer(product_id, prod['title'], affiliate_url)
            logger.info(f"✅ Post enviado com sucesso para o produto: {product_id}")
        except Exception as e:
            logger.error(f"Erro ao enviar post para o Telegram: {e}")

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
    logger.info("Bot de Busca Dinâmica por API Iniciado com Sucesso!")
    
    while True:
        try:
            # Chama o motor de busca geral da API do AliExpress
            produtos_encontrados = bot.ali_client.search_niche_products_via_api()
            
            if produtos_encontrados:
                logger.info(f"API retornou {len(produtos_encontrados)} produtos. Processando esteira...")
                for produto in produtos_encontrados:
                    await bot.process_api_product(produto)
                    await asyncio.sleep(4)  # Anti-Throttling seguro
            else:
                logger.warning("Nenhum produto retornado pela API neste ciclo. Tentando novamente no próximo.")

            logger.info("Ciclo concluído. Aguardando 5 minutos para nova varredura de termos...")
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    asyncio.run(main())
