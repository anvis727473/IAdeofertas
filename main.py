import asyncio
import logging
import os
import telegram
from supabase import create_client

# Configuração básica de log
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] bot.main: %(message)s")
logger = logging.getLogger("bot.main")

# Inicialização
supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))
bot = telegram.Bot(token=os.environ.get("TELEGRAM_TOKEN"))
CHAT_ID = os.environ.get("CHAT_ID")

async def enviar_para_telegram(produto):
    """ Envio direto para o Telegram sem bloqueios estatísticos """
    mensagem = (
        f"🔥 *Oportunidade Tech*\n\n"
        f"📦 {produto['titulo']}\n"
        f"💰 Preço: R$ {produto['preco_desconto']:.2f}\n\n"
        f"👉 [COMPRAR NO ALIEXPRESS]({produto['url_produto']})"
    )
    
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mensagem, parse_mode='Markdown')
        # Marca como enviado no banco
        supabase.table("ofertas").update({"enviado": True}).eq("id", produto['id']).execute()
        logger.info(f"✅ ENVIADO COM SUCESSO: {produto['titulo']}")
    except Exception as e:
        logger.error(f"❌ ERRO AO ENVIAR NO TELEGRAM: {e}")

async def processar_lote():
    # Palavras-chave de TI para filtrar o que importa
    IT_TERMS = ["keyboard", "mouse", "ssd", "ram", "monitor", "router", "hub", "pc", "gaming", "usb", "headset", "graphics"]
    
    # Busca ofertas não enviadas (Removida a limitação do filtro estatístico)
    response = supabase.table("ofertas").select("*").eq("enviado", False).limit(10).execute()
    
    for item in response.data:
        titulo = item.get('titulo', '').lower()
        
        # Só envia se for item de informática
        if any(term in titulo for term in IT_TERMS):
            await enviar_para_telegram(item)
        else:
            # Se não é TI, marca como enviado para limpar a fila
            supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
            logger.info(f"⚠️ Ignorado (Não é TI): {item['titulo'][:50]}...")

async def main():
    logger.info("Bot de Ofertas Tech Iniciado.")
    while True:
        await processar_lote()
        await asyncio.sleep(30) # Espera 30 segundos antes de buscar novos produtos

if __name__ == "__main__":
    asyncio.run(main())
