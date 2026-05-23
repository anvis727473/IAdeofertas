"""
main.py — Motor assíncrono do bot de ofertas
Fluxo por ciclo:
  1. Busca ofertas pendentes no Supabase
  2. Para cada oferta, gera link de afiliado via AliExpress API
  3. Formata e envia card para o canal Telegram
  4. Marca oferta como enviada ou registra falha
  5. Dorme POLL_INTERVAL segundos e repete
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

import database

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt=%Y-%m-%dT%H:%M:%S,
    stream=sys.stdout,
)
logger = logging.getLogger("bot.main")

# ---------------------------------------------------------------------------
# Configuração via variáveis de ambiente
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN: str = os.environ["TELEGRAM_TOKEN"]
CHAT_ID: str = os.environ["CHAT_ID"]
ALI_API_KEY: str = os.environ["ALI_API_KEY"]
ALI_TRACKING_ID: str = os.environ.get("ALI_TRACKING_ID", "default")

# Configurações de Polling e Batch
POLL_INTERVAL: int = int(os.environ.get("POLL_INTERVAL", "300"))
BATCH_SIZE: int = int(os.environ.get("BATCH_SIZE", "5"))
SEND_DELAY: float = float(os.environ.get("SEND_DELAY", "3.0"))

# ---------------------------------------------------------------------------
# HTTP Client Reutilizável (Connection Pool)
# ---------------------------------------------------------------------------
_http_client: httpx.AsyncClient = httpx.AsyncClient(
    timeout=httpx.Timeout(15.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)

# ---------------------------------------------------------------------------
# Integrações de APIs Externas
# ---------------------------------------------------------------------------

async def generate_affiliate_link(original_url: str, tracking_id: str) -> str:
    """
    Chama a API do AliExpress para converter o link original em um link de afiliado monetizado.
    Se falhar, retorna o link original como fallback seguro.
    """
    url = "https://api-sg.aliexpress.com/sync"
    params = {
        "method": "aliexpress.affiliate.link.generate",
        "app_key": ALI_API_KEY,
        "tracking_id": tracking_id,
        "promotion_link_type": "0",
        "source_values": original_url,
        "sign_method": "md5",  # Nota: Em produção real, calcular o hash do parâmetro 'sign'
    }

    try:
        # Implementação de política de retry contra instabilidades de rede na API do AliExpress
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
                
                # Tratamento defensivo da resposta JSON
                result = data.get("aliexpress_affiliate_link_generate_response", {}).get("resp_result", {})
                if result.get("resp_code") == 200:
                    links = result.get("result", {}).get("promotional_link_list", {}).get("promotion_link", [])
                    if links and len(links) > 0:
                        return links[0].get("promotion_link")
                
                logger.warning("AliExpress retornou estrutura vazia para %s", original_url)
                return original_url
    except Exception as exc:
        logger.error("Erro na API do AliExpress para %s: %s", original_url, exc)
    return original_url


async def send_offer_to_telegram(offer: Dict[str, Any], tracking_url: str) -> bool:
    """
    Envia o card formatado para o Telegram. Tenta enviar com foto; se falhar, envia em formato texto.
    """
    # Construção da mensagem formatada em HTML
    titulo = offer["titulo"]
    p_orig = offer["preco_original"]
    p_desc = offer["preco_desconto"]
    pct = offer["percentual_desconto"]
    cupom = offer["cupom"]

    texto = f"🔥 <b>{titulo}</b>\n\n"
    if p_orig:
        texto += f"❌ De: <s>R$ {p_orig:.2f}</s>\n"
    texto += f"✅ Por: <b>R$ {p_desc:.2f}</b>"
    if pct:
        texto += f" ({pct}% de desconto)"
    texto += "\n"

    if cupom:
        texto += f"🎟️ Cupom: <code>{cupom}</code>\n"

    texto += f"\n🛒 <b>Compre aqui:</b> <a href='{tracking_url}'>Ir para o AliExpress</a>"

    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    
    # Payload para envio com Foto
    if offer.get("url_imagem"):
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=1, min=2, max=8),
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
                        logger.warning("Telegram Rate Limit (429). Aguardando %ds...", retry_after)
                        await asyncio.sleep(retry_after)
                        raise httpx.ConnectError("rate_limit_retry")
                    
                    if resp.status_code == 400:
                        logger.warning("Imagem inválida para oferta %s. Usando fallback texto.", offer["id"])
                        break  # Sai do retry da foto para cair no fallback de texto abaixo
                        
                    resp.raise_for_status()
                    return True
        except RetryError:
            logger.error("Excedidas tentativas de envio de imagem para oferta %s. Tentando texto.", offer["id"])
        except Exception as exc:
            logger.error("Falha no endpoint sendPhoto: %s. Mudando para texto.", exc)

    # Fallback: Envio apenas como mensagem de Texto puro
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
                    raise httpx.ConnectError("rate_limit_retry")
                
                resp.raise_for_status()
                return True
    except Exception as exc:
        logger.error("Falha crítica ao enviar mensagem texto para oferta %s: %s", offer["id"], exc)
        return False


# ---------------------------------------------------------------------------
# Ciclo Principal do Worker
# ---------------------------------------------------------------------------

async def process_cycle() -> None:
    """
    Executa um lote isolado de processamento de ofertas pendentes.
    """
    logger.info("Iniciando ciclo de processamento...")
    offers = database.fetch_pending_offers(limit=BATCH_SIZE)
    logger.info("%d oferta(s) encontrada(s) para envio.", len(offers))

    for offer in offers:
        offer_id = offer["id"]
        # Prioriza a tag customizada da oferta; se nula, usa a global
        tracking_id = offer.get("tag_afiliado") or ALI_TRACKING_ID

        # 1. Monetização do link
        tracking_url = await generate_affiliate_link(offer["url_produto"], tracking_id)

        # 2. Despacho ao canal do Telegram
        success = await send_offer_to_telegram(offer, tracking_url)

        # 3. Conciliação do Estado no Banco de Dados (Idempotência)
        if success:
            database.mark_as_sent(offer_id)
        else:
            database.increment_attempt(offer_id)

        # Pausa anti-flood sequencial entre mensagens
        await asyncio.sleep(SEND_DELAY)

    logger.info("Ciclo concluído.")


async def main() -> None:
    """
    Worker contínuo: executa ciclos indefinidamente com pausa entre eles.
    Projetado para rodar como Background Worker no Render.
    """
    logger.info(
        "Bot iniciado. Intervalo de polling: %ds | Batch: %d",
        POLL_INTERVAL,
        BATCH_SIZE,
    )

    # Valida conectividade estrutural com o Supabase na inicialização
    try:
        database.get_client()
    except Exception as exc:
        logger.critical("Falha ao conectar ao Supabase: %s. Encerrando execução do container.", exc)
        sys.exit(1)

    try:
        while True:
            await process_cycle()
            logger.debug("Dormindo por %ds...", POLL_INTERVAL)
            await asyncio.sleep(POLL_INTERVAL)
    except Exception as exc:
        logger.exception("Erro inesperado e não tratado detectado no loop principal: %s", exc)
    finally:
        # Garante o encerramento correto do pool HTTP no fechamento do loop assíncrono
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
            logger.info("Pool de conexões HTTP finalizado.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot encerrado via interrupção de teclado (SIGINT).")
