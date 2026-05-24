import asyncio
import logging
import os
from supabase import create_client

from aiohttp import web
async def handle(request): return web.Response(text="Bot is running")
app = web.Application()
app.add_routes([web.get('/', handle)])

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] bot.main: %(message)s")
logger = logging.getLogger("bot.main")

# Inicialização Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

async def enviar_para_telegram(produto):
    """ Aqui entra a sua lógica de envio para o bot do Telegram """
    logger.info(f"Enviando oferta: {produto['titulo']} - R$ {produto['preco_desconto']}")
    
    # Exemplo: Marcar como enviado no banco
    supabase.table("ofertas").update({"enviado": True}).eq("id", produto['id']).execute()

async def analisar_e_processar():
    """ Loop principal de triagem e envio """
    logger.info("Iniciando ciclo de triagem...")
    
    # Busca um lote maior para análise (50 produtos)
    response = supabase.table("ofertas")\
        .select("*")\
        .eq("enviado", False)\
        .lt("tentativas", 5)\
        .order("sales_volume", desc=True)\
        .limit(50)\
        .execute()
    
    produtos = response.data
    if not produtos:
        logger.info("Nenhum produto novo para analisar.")
        return

    logger.info(f"Analisando {len(produtos)} produtos...")

    for item in produtos:
        # LÓGICA DE FILTRO (Pode ajustar conforme necessário)
        # Critério: Desconto > 10% OU alto volume de vendas
        desconto = item.get('percentual_desconto', 0)
        volume = item.get('sales_volume', 0)
        
        if desconto >= 10 or volume > 1000:
            await enviar_para_telegram(item)
        else:
            # Caso não passe no filtro, incrementa tentativas para não reprocessar eternamente
            nova_tentativa = item.get('tentativas', 0) + 1
            supabase.table("ofertas")\
                .update({"tentativas": nova_tentativa})\
                .eq("id", item['id'])\
                .execute()
            logger.info(f"Produto {item['id']} retido no filtro (Desconto: {desconto}%).")

async def main():
    while True:
        try:
            await analisar_e_processar()
        except Exception as e:
            logger.error(f"Erro no ciclo: {e}")
        
        # Intervalo entre rodadas de análise
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
