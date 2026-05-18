import asyncio
import logging
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

class OffersBot:
    def __init__(self):
        # Garante que as variáveis críticas do Telegram estejam carregadas
        Config.validate()
        
        # Inicializa o cliente HTTP do Telegram (Ideal usar a classe direta Bot para envio passivo/automação)
        self.telegram_bot = Bot(token=Config.TELEGRAM_TOKEN)
        self.db = DatabaseManager()
        self.ali_client = AliExpressClient()
        self.chat_id = Config.ID_DO_GRUPO

    async def post_new_offer(self, product_id: str, title: str, original_url: str, price: str, image_url: str = None):
        """
        Orquestra a checagem, a geração de link e a postagem no canal do Telegram.
        """
        # 1. Evita duplicados verificando o banco de dados
        if self.db.is_offer_posted(product_id):
            logger.info(f"[Duplicada] Produto {product_id} já foi postado. Ignorando.")
            return False

        # 2. Converte para link de Afiliado monetizado
        affiliate_url = self.ali_client.generate_affiliate_link(original_url)

        # 3. Formata o template da mensagem (Markdown de fácil leitura)
        message_text = (
            f"🔥 *{title}*

"
            f"💰 Preço Imperdível: *{price}*

"
            f"🛒 Compre pelo link de afiliado:
[Clique aqui para ir ao AliExpress]({affiliate_url})

"
            f"⚠️ Oferta sujeita a alteração de preço a qualquer momento!"
        )

        # 4. Faz o disparo pelo Telegram
        try:
            if image_url:
                await self.telegram_bot.send_photo(
                    chat_id=self.chat_id,
                    photo=image_url,
                    caption=message_text,
                    parse_mode='Markdown'
                )
            else:
                await self.telegram_bot.send_message(
                    chat_id=self.chat_id,
                    text=message_text,
                    parse_mode='Markdown',
                    disable_web_page_preview=False
                )
            
            logger.info(f"[Sucesso] Oferta {product_id} enviada para o canal Telegram.")
            
            # 5. Registra o ID no banco para travar re-postagem involuntária
            self.db.save_posted_offer(product_id, title, affiliate_url)
            return True

        except Exception as e:
            logger.error(f"Falha ao enviar mensagem ao Telegram API: {e}")
            return False

async def main():
    # Inicializa o bot de ofertas
    bot = OffersBot()
    logger.info("Bot de Ofertas AliExpress ativado com sucesso!")
    
    # Exemplo prático de execução simulada (substitua pelo loop do seu scraper, API ou feed)
    mock_id = "1005009999999"
    mock_title = "Carregador Rápido Baseus 30W USB-C GaN"
    mock_url = "https://pt.aliexpress.com/item/1005009999999.html"
    mock_price = "R$ 38,50"
    mock_image = "https://images.unsplash.com/photo-1611532736597-de2d4265fba3?w=400" # Exemplo de imagem pública
    
    logger.info(f"Executando simulação de postagem para o produto: {mock_id}")
    await bot.post_new_offer(
        product_id=mock_id,
        title=mock_title,
        original_url=mock_url,
        price=mock_price,
        image_url=mock_image
    )

if __name__ == "__main__":
    # Loop assíncrono nativo exigido pelo python-telegram-bot v20+
    asyncio.run(main())
