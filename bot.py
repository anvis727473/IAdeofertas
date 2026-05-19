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
        """Processa o produto vindo do garimpo automático."""
        product_id = prod["id"]
        
        # 1. Anti-Duplicidade do Canal (Se já foi postado, pula para evitar spam)
        if self.db.is_offer_posted(product_id):
            return

        current_price_float = parse_price(prod["price"])
        if current_price_float <= 0:
            return

        # 2. Consulta histórico de preços armazenados no Banco de Dados
        avg_price, _ = self.db.get_price_metrics(product_id)
        
        # Registra ou atualiza o preço atual no banco de dados
        self.db.save_price_if_changed(product_id, current_price_float)

        # 3. Análise Matemática de Desconto Real
        discount_msg = ""
        if avg_price and avg_price > 0:
            discount_percent = ((avg_price - current_price_float) / avg_price) * 100
            
            # Se o preço caiu de fato comparado ao histórico que o bot guardou
            if discount_percent >= 8:
                logger.info(f"🔥 QUEDA DE PREÇO DETECTADA: {product_id} com {discount_percent:.1f}% de desconto.")
                discount_msg = f"📉 *Desconto Real:* {discount_percent:.1f}% mais barato que a média registrada!"
            else:
                # Preço está normal ou subiu, apenas mantém o monitoramento silencioso no banco
                return
        else:
            # Produto novo descoberto pelo garimpo! Posta no canal para iniciar o radar
            logger.info(f"✨ NOVO PRODUTO DESCOBERTO E MONITORADO: {product_id}")
            discount_msg = f"⭐ *Produto Selecionado!* Avaliação {prod['rating']}⭐ no AliExpress e adicionado ao nosso Radar de Preços."

        # 4. Criação do Link de Afiliado
        affiliate_url = self.ali_client.generate_affiliate_link(prod["url"])

        # 5. Template para o Telegram
        message_text = f"""🛠️ *{prod['title']}*

💰 Preço Atual: *{prod['price']}*
{discount_msg}

🎯 *Destaque:* Item altamente avaliado e verificado de forma autônoma pelo sistema.

🛒 Link com desconto de Afiliado:
[Clique aqui para abrir a Oferta no AliExpress]({affiliate_url})

⚠️ *Nota:* Os estoques promocionais são gerados pelo AliExpress e mudam constantemente."""

        # 6. Postagem
        try:
            if prod.get("image"):
                await self.telegram_bot.send_photo(chat_id=self.chat_id, photo=prod["image"], caption=message_text, parse_mode='Markdown')
            else:
                await self.telegram_bot.send_message(chat_id=self.chat_id, text=message_text, parse_mode='Markdown')
            
            self.db.save_posted_offer(product_id, prod['title'], affiliate_url)
            logger.info(f"✅ Post enviado: {product_id}")
        except Exception as e:
            logger.error(f"Erro ao enviar postagem: {e}")

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
    logger.info("Bot de Garimpo Autônomo e Monitoramento de Nicho Iniciado!")
    
    while True:
        try:
            logger.info("Executando algoritmo de garimpo automático por nicho...")
            # Puxa os produtos automáticos filtrados por qualidade e país
            produtos_garimpados = bot.ali_client.discover_niche_products()
            
            for produto in produtos_garimpados:
                await bot.process_discovered_product(produto)
                await asyncio.sleep(4) # Janela de segurança contra bloqueios
                
            logger.info("Ciclo de garimpo finalizado. Aguardando 5 minutos para nova varredura...")
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    asyncio.run(main())
