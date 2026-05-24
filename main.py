import asyncio
import logging
import os
from supabase import create_client

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] bot.main: %(message)s")
logger = logging.getLogger("bot.main")

# Inicialização Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

async def enviar_para_telegram(produto):
    """ Lógica de envio """
    titulo = produto.get('titulo', 'Produto')
    preco = produto.get('preco_desconto', 0)
    logger.info(f"ENVIANDO OFERTA: {titulo} | R$ {preco}")
    
    # Atualiza no banco como enviado
    try:
        supabase.table("ofertas").update({"enviado": True}).eq("id", produto['id']).execute()
    except Exception as e:
        logger.error(f"Erro ao marcar como enviado: {e}")

async def processar_lote():
    """ Loop de triagem simplificado (sem filtros estatísticos proibitivos) """
    logger.info("Iniciando ciclo de processamento...")
    
    try:
        # Busca 50 produtos não enviados
        response = supabase.table("ofertas")\
            .select("*")\
            .eq("enviado", False)\
            .lt("tentativas", 5)\
            .order("sales_volume", desc=True)\
            .limit(50)\
            .execute()
        
        produtos = response.data
    except Exception as e:
        logger.error(f"Erro ao buscar produtos: {e}")
        return

    if not produtos:
        logger.info("Nenhum produto novo na fila.")
        return

    for item in produtos:
        desconto = float(item.get('percentual_desconto', 0))
        volume = int(item.get('sales_volume', 0))
        
        # FILTRO RELAXADO: Aceita se tiver desconto > 5% OU for muito popular (volume > 100)
        if desconto >= 5 or volume > 100:
            await enviar_para_telegram(item)
        else:
            # Incrementa tentativa para não reprocessar o mesmo produto toda vez
            try:
                nova_tentativa = item.get('tentativas', 0) + 1
                supabase.table("ofertas").update({"tentativas": nova_tentativa}).eq("id", item['id']).execute()
            except:
                pass

async def main():
    logger.info("Bot Sniper iniciado com sucesso.")
    while True:
        await processar_lote()
        await asyncio.sleep(30) # Espera 30 segundos antes do próximo ciclo

if __name__ == "__main__":
    # Removemos qualquer necessidade de abrir porta HTTP, 
    # pois isso deve rodar como 'Background Worker' no Render.
    asyncio.run(main())
