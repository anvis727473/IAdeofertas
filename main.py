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
                        break
