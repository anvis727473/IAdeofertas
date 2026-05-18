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
        
        # Inicializa o cliente HTTP do Telegram para envio de automação passiva
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

        # 3. Formata o template usando aspas triplas (f""") para evitar erros de compilação
        # Customizado com foco em Hardware, Gadgets, Casa Inteligente e Ferramentas
        message_text = f"""🛠️ *{title}*

💰 Preço Especial: *{price}*

🔌 *Categoria:* Hardware, Tecnologia & Utilidades para Casa

🛒 Compre com segurança pelo link de afiliado:
[Clique aqui para abrir no AliExpress]({affiliate_url})

⚠️ *Nota:* Os estoques de hardware e eletrônicos costumam esgotar rápido. O preço pode alterar a qualquer momento!"""

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
            
            logger.info(f"[Sucesso] Oferta de tecnologia {product_id} enviada para o Telegram.")
            
            # 5. Registra o ID no banco para travar re-postagem involuntária
            self.db.save_posted_offer(product_id, title, affiliate_url)
            return True

        except Exception as e:
            logger.error(f"Falha ao enviar mensagem ao Telegram API: {e}")
            return False

async def main():
    # Inicializa o bot de ofertas
    bot = OffersBot()
    logger.info("Bot de Ofertas (Hardware & Utensílios) ativado no Render!")
    
    # LOOP INFINITO: Mantém o Background Worker do Render rodando por tempo indeterminado
    while True:
        try:
            logger.info("Iniciando ciclo de checagem de novas ofertas...")
            
            # =========================================================================
            # ONDE SEU SCRAPER/FONTE DE DADOS ENTRA:
            # 
            # Aqui você vai chamar a função que coleta os produtos do AliExpress.
            # Exemplo conceitual:
            #
            # de_ofertas = meu_scraper.pegar_promos_de_hardware()
            # for item in de_ofertas:
            #     await bot.post_new_offer(item['id'], item['titulo'], item['link'], item['preco'], item['imagem'])
            # =========================================================================
            
            # Teste simulado (O banco vai barrar se o ID já foi postado antes)
            mock_id = "1005006123456"
            mock_title = "SSD NVMe M.2 Netac 1TB PCIe 4.0 - Alta Velocidade para PC"
            mock_url = "https://pt.aliexpress.com/item/1005006123456.html"
            mock_price = "R$ 289,90"
            mock_image = "https://images.unsplash.com/photo-1591488320449-011701bb6704?w=500"
            
            await bot.post_new_offer(
                product_id=mock_id,
                title=mock_title,
                original_url=mock_url,
                price=mock_price,
                image_url=mock_image
            )
            
            # Tempo de espera entre as varreduras (600 segundos = 10 minutos)
            logger.info("Ciclo finalizado. Aguardando 10 minutos para a próxima busca...")
            await asyncio.sleep(600)
            
        except Exception as e:
            logger.error(f"Erro crítico no loop principal do bot: {e}")
            # Em caso de erro (queda de internet, API fora), aguarda 1 minuto antes de tentar de novo
            await asyncio.sleep(60)

if __name__ == "__main__":
    # Inicializa o loop assíncrono exigido pela biblioteca do Telegram
    asyncio.run(main())
