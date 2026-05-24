import asyncio
import os
import logging
import telegram
from supabase import create_client

# Configuração
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot.worker")

# Inicialização
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
bot = telegram.Bot(token=os.environ["TELEGRAM_TOKEN"])
CHAT_ID = os.environ["CHAT_ID"]

def formatar_oferta(item):
    # Formatação "Premium" em HTML
    titulo = item['titulo'].replace("<", "&lt;").replace(">", "&gt;")
    preco_orig = item.get('preco_original', 0)
    preco_desc = item.get('preco_desconto', 0)
    
    return (
        f"🔥 <b>{titulo[:100]}</b>\n\n"
        f"💰 De: <s>R$ {preco_orig:.2f}</s>\n"
        f"🏷 Por: <code>R$ {preco_desc:.2f}</code>\n"
        f"🎟 Cupom: <code>{item.get('cupom', 'N/A')}</code>\n\n"
        f"👉 <a href='{item['url_produto']}'>CLIQUE AQUI PARA COMPRAR</a>"
    )

async def processar_ofertas():
    while True:
        try:
            # Busca ofertas pendentes, priorizando pelas melhores
            response = supabase.table("ofertas").select("*")\
                .eq("enviado", False).order("prioridade", desc=True).limit(5).execute()
            
            for item in response.data:
                # Validação de Segurança: Cupom obrigatório e > 10% de desconto
                if item.get('cupom') and (item['preco_desconto'] < item['preco_original'] * 0.9):
                    msg = formatar_oferta(item)
                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
                    supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
                    logger.info(f"Enviado: {item['titulo'][:30]}")
                else:
                    # Descarta ofertas que não cumprem o critério para não travar a fila
                    supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
                
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Erro no loop: {e}")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(processar_ofertas())
