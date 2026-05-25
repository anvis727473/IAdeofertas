import asyncio
import os
import logging
import sys
from aiohttp import web
import telegram
from supabase import create_client

# Configuração de Logs Corporativa
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bot.production")

# Inicialização de Clientes Seguros
try:
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    bot = telegram.Bot(token=os.environ["TELEGRAM_TOKEN"])
    CHAT_ID = os.environ["CHAT_ID"]
except KeyError as e:
    logger.critical(f"Variável de ambiente ausente no ecossistema: {e}")
    sys.exit(1)

class OfferProcessor:
    def __init__(self):
        # Tamanho do lote expansível via Variável de Ambiente para evitar travamentos
        self.batch_size = int(os.environ.get("BATCH_SIZE", "15"))
        self.poll_interval = int(os.environ.get("POLL_INTERVAL", "60"))

    def _format_html(self, item) -> str:
        """Formatação Premium HTML Limpa e Escapada contra quebras de sintaxe."""
        titulo = item['titulo'].replace("<", "&lt;").replace(">", "&gt;")
        preco_orig = float(item.get('preco_original') or 0)
        preco_desc = float(item.get('preco_desconto') or 0)
        cupom = item.get('cupom') or "N/A"
        
        return (
            f"🔥 <b>{titulo[:100]}</b>\n\n"
            f"💰 De: <s>R$ {preco_orig:.2f}</s>\n"
            f"🏷 Por: <code>R$ {preco_desc:.2f}</code>\n"
            f"🎟 Cupom: <code>{cupom}</code>\n\n"
            f"👉 <a href='{item['url_produto']}'>CLIQUE AQUI PARA COMPRAR</a>"
        )

    async def execute_batch_polling(self):
        """Executa a busca e processamento isolando o I/O síncrono da thread principal."""
        # Thread Offloading: Evita o congelamento do Event Loop (SPOF mitigado)
        def fetch_data():
            return supabase.table("ofertas").select("*")\
                .eq("enviado", False)\
                .order("prioridade", desc=True)\
                .limit(self.batch_size).execute()

        response = await asyncio.to_thread(fetch_data)
        items = response.data
        
        if not items:
            return

        logger.info(f"Lote capturado com {len(items)} candidatos pendentes.")

        for item in items:
            preco_orig = float(item.get('preco_original') or 0)
            preco_desc = float(item.get('preco_desconto') or 0)
            cupom = item.get('cupom')

            # Unificação Estrita da Regra de Negócio (Filtro Premium)
            if cupom and (preco_desc < preco_orig * 0.9):
                try:
                    mensagem_html = self._format_html(item)
                    await bot.send_message(chat_id=CHAT_ID, text=mensagem_html, parse_mode='HTML')
                    
                    # Atualiza estado para Enviado com sucesso
                    await asyncio.to_thread(
                        lambda: supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
                    )
                    logger.info(f"✅ DESPACHADO: {item['titulo'][:30]}")
                except Exception as e:
                    logger.error(f"Falha de rede ou barramento ao enviar item {item['id']}: {e}")
            else:
                # Descarta o item direto no banco para limpar a fila de forma eficiente
                await asyncio.to_thread(
                    lambda: supabase.table("ofertas").update({"enviado": True}).eq("id", item['id']).execute()
                )
                logger.info(f"🗑️ DESCARTADO (Sem margem/cupom): {item['titulo'][:30]}")
            
            # Controle Anti-Flood do Telegram regulado em 2 segundos por post real
            await asyncio.sleep(2.0)

async def daemon_loop(processor: OfferProcessor):
    """Loop infinito controlado e protegido contra quebras de execução."""
    logger.info("Iniciando Daemon de sincronismo de ofertas...")
    while True:
        try:
            await processor.execute_batch_polling()
        except Exception as e:
            logger.error(f"Erro inesperado no ciclo do Daemon: {e}", exc_info=True)
        
        # Garante o repouso real configurado (Padrão: 60 segundos)
        await asyncio.sleep(processor.poll_interval)

# --- Servidor HTTP Assíncrono (Contrato de Liveness do Render) ---
async def handle_health_check(request):
    return web.Response(text="SERVER_OK", content_type="text/plain")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Servidor HTTP Ativo com sucesso na porta {port}")

if __name__ == "__main__":
    processor = OfferProcessor()
    
    # Orquestração limpa de múltiplas corrotinas no mesmo Loop de Eventos
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    loop.run_until_complete(daemon_loop(processor))
