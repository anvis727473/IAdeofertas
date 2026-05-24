# Trecho sugerido para o seu script de processamento:
# Aumentamos o limite para 50 para processar um lote maior de cada vez.
params = {
    "enviado": "eq.False",
    "tentativas": "lt.5",
    "order": "sales_volume.desc", # Focar nos mais populares
    "limit": "50" 
}

response = supabase_client.table("ofertas").select("*").match(params).execute()
produtos = response.data

logger.info(f"Processando lote de {len(produtos)} produtos.")

for item in produtos:
    # AQUI ENTRA SUA LÓGICA DE FILTRO (ANALYTICS)
    # Sugestão: Relaxe o filtro para que produtos com bom volume de vendas 
    # ou bom desconto percentual passem.
    if item['percentual_desconto'] >= 10: # Filtro relaxado para 10%
        await enviar_para_telegram(item)
    else:
        # Se não passar, apenas marque como analizado (tentativas++) 
        # para o bot seguir para o próximo
        await incrementar_tentativa(item['id'])
