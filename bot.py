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
    """Saneia e converte qualquer formato de preço da API em Float."""
    if not price_str:
        return 0.0
    clean_str = re.sub(r'[^\d,.]', '', price_str)
    # Trata caso venha com pontos e vírgulas invertidos
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

    async def process_product_pipeline(self, product_id: str):
        """Pipeline ponta a ponta: busca preço real, analisa histórico e decide postagem."""
        
        # 1. Filtro Anti-Duplicidade do Canal
        if self.db.is_offer_posted(product_id):
            logger.info(f"[Ignorado] {product_id} já enviado recentemente ao canal.")
            return

        # 2. Coleta de Dados ao Vivo via API
        prod_data = self.ali_client.fetch_live_product_details(product_id)
        if not prod_data.get("success"):
            return

        current_price_float = parse_price(prod_data["price"])
        if current_price_float <= 0:
            return

        # Captura métricas históricas antes de atualizar com o valor de agora
        avg_price, _ = self.db.get_price_metrics(product_id)
        self.db.save_price_if_changed(product_id, current_price_float)

        # 3. Inteligência Artificial de Preços com Warmup
        discount_msg = ""
        if avg_price and avg_price > 0:
            discount_percent = ((avg_price - current_price_float) / avg_price) * 100
            
            if discount_percent >= 8:  # Otimizado de 10% para 8% para pegar pequenas flutuações agressivas
                logger.info(f"🔥 PROMOÇÃO DETECTADA: {product_id} com {discount_percent:.1f}% abaixo da média.")
                discount_msg = f"📉 *Desconto Real:* {discount_percent:.1f}% mais barato que a média recente!"
            else:
                logger.info(f"[Retido] {product_id} preço está normal (Variação: {discount_percent:.1f}%).")
                return
        else:
            logger.info(f"🌟 NOVO RADAR: {product_id} adicionado ao monitoramento pela primeira vez.")
            discount_msg = "🌟 *Radar de Preços Ativo!* Histórico de monitoramento iniciado para este item."

        # 4. Monetização Estável
        affiliate_url = self.ali_client.generate_affiliate_link(prod_data["url"])

        # 5. Template Profissional e Limpo
        message_text = f"""🛠️ *{prod_data['title']}*

💰 Preço Atual: *{prod_data['price']}*
{discount_msg}

🔌 *Nicho:* Hardware, Tecnologia & Gadgets

🛒 Compre com segurança pelo Link de Afiliado Oficial:
[Clique aqui para abrir a Oferta no AliExpress]({affiliate_url})

⚠️ *Nota:* Os estoques promocionais da plataforma esgotam rápido e flutuam sem aviso prévio!"""

        # 6. Despacho Seguro
        try:
            if prod_data.get("image"):
                await self.telegram_bot.send_photo(chat_id=self.chat_id, photo=prod_data["image"], caption=message_text, parse_mode='Markdown')
            else:
                await self.telegram_bot.send_message(chat_id=self.chat_id, text=message_text, parse_mode='Markdown')
            
            self.db.save_posted_offer(product_id, prod_data['title'], affiliate_url)
            logger.info(f"✅ [Sucesso] Post enviado com sucesso para o Telegram para o produto {product_id}.")
        except Exception as e:
            logger.error(f"Falha crítica no envio para o Telegram: {e}")

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
    logger.info("Bot de Ofertas Inteligente V3 (Ultra-Estável) Iniciado!")
    
    while True:
        try:
            logger.info("Iniciando varredura cíclica de IDs monitorados...")
            product_ids = bot.ali_client.get_target_product_ids()
            
            for pid in product_ids:
                await bot.process_product_pipeline(pid)
                # Aumentado para 4 segundos para evitar punições por excesso de requisições (Anti-Throttling)
                await asyncio.sleep(4)
                
            logger.info("Ciclo encerrado. Aguardando 5 minutos para a próxima checagem de preços...")
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Erro crítico no loop principal do bot: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    asyncio.run(main())
