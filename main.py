"""
main.py — Motor assíncrono do bot de ofertas (Sniper Edition)
Responsabilidades:
  - Levantar servidor web dummy para validação de portas no Render (Health Check)
  - Orquestrar ciclos de varredura contínua contra o Supabase
  - Integrar o motor estatístico de validação de preços (analytics.py)
  - Despachar as mensagens formatadas em HTML resiliente para o Telegram
"""

import asyncio
import logging
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
from typing import Any, Dict

import httpx
from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

import database
from analytics import OfferSniperAnalytics

# ---------------------------------------------------------------------------
# Configuração de Logging Central
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("bot.main")

# ---------------------------------------------------------------------------
# Captura de Configurações de Infraestrutura via Variáveis de Ambiente
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN: str = os.environ["TELEGRAM_TOKEN"]
CHAT_ID: str = os.environ["CHAT_ID"]
ALI_API_KEY: str = os.environ["ALI_API_KEY"]
ALI_TRACKING_ID: str = os.environ.get("ALI_TRACKING_ID", "default")

POLL_INTERVAL: int = int(os.environ.get("POLL_INTERVAL", "300"))
BATCH_SIZE: int = int(os.environ.get("BATCH_SIZE", "5"))
SEND_DELAY: float = float(os.environ.get("SEND_DELAY", "3.0"))

# Connection pool persistente do HTTPX para ganho de performance de sockets
_http_client: httpx.AsyncClient = httpx.AsyncClient(
    timeout=httpx.Timeout(15.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)

# ---------------------------------------------------------------------------
# Servidor Web Fictício para Mitigar Bloqueio de Portas do Render
# ---------------------------------------------------------------------------
def run_dummy_server():
    """ Abre um socket HTTP passivo para passar pelo crivo do Port Scan do Render """
    try:
        # O Render injeta dinamicamente a porta desejada nesta variável (padrão: 10000)
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
        logger.info("Servidor de infraestrutura ativado na porta %d para o Render Health Check.", port)
        server.serve_forever()
    except Exception as exc:
        logger.error("Falha ao iniciar o servidor de portas fictício do Render: %s", exc)

# ---------------------------------------------------------------------------
# Integração de Redes e Serviços Externos
# ---------------------------------------------------------------------------
async def generate_affiliate_link(original_url: str, tracking_id: str) -> str:
    """ Monetiza URLs normais do AliExpress convertendo-as em links de afiliados. """
    url = "https://api-sg.aliexpress.com/sync"
    params = {
        "method": "aliexpress.affiliate.link.generate",
        "app_key": ALI_API_KEY,
        "tracking_id": tracking_id,
        "promotion_link_type": "0",
        "source_values": original_url,
        "sign_method": "md5",
    }

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=6),
            retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                response = await _http_client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                result = data.get("aliexpress_affiliate_link_generate_response", {}).get("resp_result", {})
                if result.get("resp_code") == 200:
                    links = result.get("result", {}).get("promotional_link_list", {}).get("promotion_link", [])
                    if links:
                        return links[0].get("promotion_link", original_url)
                
                logger.warning("Estrutura de links vazia ou inválida vinda do AliExpress para URL: %s", original_url)
                return original_url
    except Exception as exc:
        logger.error("Erro ao contactar API do AliExpress para a URL %s: %s", original_url, exc)
    return original_url


async def send_offer_to_telegram(offer: Dict[str, Any], tracking_url: str) -> bool:
    """ Transmite o card visual para os servidores do Telegram com tratamento dinâmico de falhas. """
    titulo = offer["titulo"]
    p_orig = offer.get("preco_original")
    p_desc = offer["preco_desconto"]
    pct = offer.get("percentual_desconto")
    cupom = offer.get("cupom")
    ts_score = offer.get("trending_score", 0.0)

    # Formatação limpa em HTML
    texto = f"🎯 <b>{titulo}</b>\n\n"
    if p_orig:
        texto += f"❌ De: <s>R$ {p_orig:.2f}</s>\n"
    texto += f"✅ Por: <b>R$ {p_desc:.2f}</b>"
    if pct:
        texto += f" ({int(pct)}% OFF)"
    
    if ts_score > 15:
        texto += f" | 🔥 <i>Tendência em Alta!</i>"
    texto += "\n"

    if cupom:
        texto += f"🎟️ Cupom Aplicável: <code>{cupom}</code>\n"

    texto += f"\n🛒 <b>Link de Acesso Seguro:</b> <a href='{tracking_url}'>Ir para o AliExpress</a>"

    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    fallback_necessario = False
    
    # Tentativa primária: Envio formatado com Foto de capa
    if offer.get("url_imagem"):
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=6),
                retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
                reraise=True,
            ):
                with attempt:
                    resp = await _http_client.post(
                        f"{base_url}/sendPhoto",
                        json={
                            "chat_id": CHAT_ID,
                            "photo": offer["url_imagem"],
                            "caption": texto,
                            "parse_mode": "HTML",
                        },
                    )
                    if resp.status_code == 429:
                        retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                        logger.warning("Telegram Rate Limit ativo. Aguardando %ds compulsórios...", retry_after)
                        await asyncio.sleep(retry_after)
                        raise httpx.ConnectError("rate_limit_cooldown")
                    
                    if resp.status_code == 400:
                        logger.warning("Link de imagem recusado pelo Telegram (400) para oferta %s. Ativando fallback.", offer["id"])
                        fallback_necessario = True
                        break
                        
                    resp.raise_for_status()
                    return True
        except Exception as exc:
            logger.error("Falha no disparo com foto (%s). Redirecionando para canal de texto.", exc)
            fallback_necessario = True

    # Mecanismo de Fallback Secundário ou Fluxo Padrão: Envio de Mensagem de Texto Padrão HTML
    if fallback_necessario or not offer.get("url_imagem"):
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=6),
                reraise=True,
            ):
                with attempt:
                    resp = await _http_client.post(
                        f"{base_url}/sendMessage",
                        json={
                            "chat_id": CHAT_ID,
                            "text": texto,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": False,
                        },
                    )
                    if resp.status_code == 429:
                        retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                        await asyncio.sleep(retry_after)
                        raise httpx.ConnectError("rate_limit_cooldown")
                    
                    resp.raise_for_status()
                    return True
        except Exception as exc:
            logger.error("Falha crítica terminal no canal de texto para a oferta %s: %s", offer["id"], exc)
            return False

    return False

# ---------------------------------------------------------------------------
# Orquestração de Fluxo e Loop de Execução
# ---------------------------------------------------------------------------
async def process_cycle() -> None:
    """ Processa um lote discreto de candidatos a oferta passando pelo crivo matemático de análise. """
    logger.info("Iniciando ciclo analítico de varredura...")
    raw_offers = database.fetch_pending_offers(limit=BATCH_SIZE)
    logger.info("%d item(ns) recolhido(s) do banco e prontos para triagem estatística.", len(raw_offers))

    for offer in raw_offers:
        offer_id = offer["id"]
        
        # Parse estruturado do histórico em array nativo do Python
        historical_records = offer.get("historico_precos") or []
        price_history = [float(r["preco"]) for r in historical_records if "preco" in r]
        
        telemetry_data = {
            "current_price": float(offer["preco_desconto"]),
            "product_rating": float(offer.get("product_rating") or 4.8),
            "sales_volume": int(offer.get("sales_volume") or 500),
            "seller_positive_feedback_rate": float(offer.get("seller_feedback_rate") or 0.95),
            "store_coupon_value": float(offer.get("cupom_loja_valor") or 0.0),
            "platform_coupon_value": float(offer.get("cupom_plataforma_valor") or 0.0),
            "sales_last_6h": int(offer.get("vendas_6h") or 50),
            "sales_last_6h_previous": int(offer.get("vendas_6h_anteriores") or 20)
        }
        
        # Filtro Avançado Baseado em Ciência de Dados
        approved, computed_metrics = OfferSniperAnalytics.evaluate_product(telemetry_data, price_history)
        
        if not approved:
            logger.info("Produto %s retido no filtro estatístico (Não passou nos desvios padrão/tendência).", offer_id)
            database.increment_attempt(offer_id)
            continue
            
        logger.info("🎯 Alvo validado pelo Sniper! TS: %.2f | Floor Price Calculado: R$ %.2f", 
                    computed_metrics["trending_score"], computed_metrics["floor_price"])
        
        # Atualiza dados dinâmicos com base nos cálculos analíticos
        offer["preco_desconto"] = computed_metrics["floor_price"]
        offer["trending_score"] = computed_metrics["trending_score"]

        tracking_id = offer.get("tag_afiliado") or ALI_TRACKING_ID
        tracking_url = await generate_affiliate_link(offer["url_produto"], tracking_id)
        
        success = await send_offer_to_telegram(offer, tracking_url)

        if success:
            database.mark_as_sent(offer_id)
        else:
            database.increment_attempt(offer_id)

        await asyncio.sleep(SEND_DELAY)

    logger.info("Ciclo de varredura finalizado.")


async def main() -> None:
    """ Loop assíncrono de orquestração infinita do worker. """
    logger.info("Bot Sniper de Ofertas Inicializado com Sucesso. Polling ativo: %ds", POLL_INTERVAL)

    try:
        # Validação estrutural de conectividade e chaves na subida do container
        database.get_client()
    except Exception as exc:
        logger.critical("Erro estrutural de comunicação com base de dados. Finalizando container: %s", exc)
        sys.exit(1)

    try:
        while True:
            await process_cycle()
            logger.debug("Dormindo pelo tempo de cooldown: %ds...", POLL_INTERVAL)
            await asyncio.sleep(POLL_INTERVAL)
    except Exception as exc:
        logger.exception("Exceção fatal e não controlada capturada no loop principal: %s", exc)
    finally:
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
            logger.info("Pool global de sockets HTTP destruído adequadamente.")


if __name__ == "__main__":
    try:
        # 1. Cria e inicia a Thread em segundo plano com o servidor web falso.
        # Isso faz o Render detectar a porta aberta imediatamente sem travar o loop de ofertas.
        infra_thread = threading.Thread(target=run_dummy_server, daemon=True)
        infra_thread.start()
        
        # 2. Inicializa o motor síncrono/assíncrono principal do bot
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Processamento interrompido via console pelo administrador (SIGINT).")
