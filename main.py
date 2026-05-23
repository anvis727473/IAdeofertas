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
from typing import Any

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
    datefmt="%Y-%m-%dT%H:%M:%S",
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

# Intervalo entre ciclos de polling (segundos). Padrão: 5 minutos.
POLL_INTERVAL: int = int(os.environ.get("POLL_INTERVAL", "300"))
# Máximo de ofertas processadas por ciclo (controle de rate limit do Telegram).
BATCH_SIZE: int = int(os.environ.get("BATCH_SIZE", "5"))
# Pausa entre envios dentro do mesmo ciclo (evita flood do Telegram).
SEND_DELAY: float = float(os.environ.get("SEND_DELAY", "3.0"))

# ---------------------------------------------------------------------------
# URLs base
# ---------------------------------------------------------------------------
ALI_AFFILIATE_URL = "https://api-sg.aliexpress.com/sync"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ---------------------------------------------------------------------------
# Cliente HTTP compartilhado (connection pool reutilizado por todo o ciclo)
# ---------------------------------------------------------------------------
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Accept": "application/json"},
        )
    return _http_client


# ---------------------------------------------------------------------------
# AliExpress — Geração de link de afiliado
# ---------------------------------------------------------------------------

async def generate_affiliate_link(product_url: str, tag: str | None = None) -> str:
    """
    Chama a AliExpress Affiliate API para converter a URL do produto
    em um link rastreável de afiliado.

    Documentação de referência:
    https://developers.aliexpress.com/en/doc.htm?docId=45047

    Retorna a URL original como fallback em caso de falha.
    """
    tracking_id = tag or ALI_TRACKING_ID
    params: dict[str, Any] = {
        "method": "aliexpress.affiliate.link.generate",
        "app_key": ALI_API_KEY,
        "tracking_id": tracking_id,
        "promotion_link_type": "0",
        "source_values": product_url,
        "sign_method": "md5",  # substituir por HMAC-SHA256 em produção
    }

    client = get_http_client()
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type(
                (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)
            ),
            reraise=True,
        ):
            with attempt:
                response = await client.get(ALI_AFFILIATE_URL, params=params)
                response.raise_for_status()

        data = response.json()
        result = (
            data.get("aliexpress_affiliate_link_generate_response", {})
            .get("resp_result", {})
            .get("result", {})
            .get("promotion_links", {})
            .get("promotion_link", [])
        )
        if result:
            affiliate_url: str = result[0].get("promotion_link", product_url)
            logger.debug("Link afiliado gerado: %s", affiliate_url)
            return affiliate_url

        logger.warning("AliExpress retornou estrutura vazia para %s", product_url)
        return product_url

    except RetryError as exc:
        logger.error(
            "AliExpress API indisponível após 3 tentativas para %s: %s",
            product_url,
            exc,
        )
        return product_url
    except httpx.HTTPStatusError as exc:
        logger.error(
            "HTTP %s da AliExpress para %s: %s",
            exc.response.status_code,
            product_url,
            exc,
        )
        return product_url


# ---------------------------------------------------------------------------
# Telegram — Formatação e envio do card de oferta
# ---------------------------------------------------------------------------

def _format_offer_caption(offer: dict[str, Any], affiliate_link: str) -> str:
    """
    Formata a mensagem em Markdown V2 compatível com o Telegram.
    Emojis e estrutura visual para maximizar CTR.
    """
    titulo = _escape_md(offer.get("titulo", "Produto sem título"))
    preco_original = offer.get("preco_original")
    preco_desconto = offer.get("preco_desconto")
    percentual = offer.get("percentual_desconto")
    cupom = offer.get("cupom")

    lines: list[str] = [
        f"🔥 *{titulo}*",
        "",
    ]

    if preco_original and preco_desconto:
        po = _escape_md(f"R$ {preco_original:.2f}")
        pd = _escape_md(f"R$ {preco_desconto:.2f}")
        lines.append(f"~~{po}~~ ➡️ *{pd}*")
    elif preco_desconto:
        pd = _escape_md(f"R$ {preco_desconto:.2f}")
        lines.append(f"💰 *{pd}*")

    if percentual:
        p = _escape_md(f"{percentual:.0f}%")
        lines.append(f"📉 Desconto de *{p}*")

    if cupom:
        c = _escape_md(cupom)
        lines.append(f"🎟 Cupom: `{c}`")

    link = _escape_md(affiliate_link)
    lines += [
        "",
        f"[🛒 Comprar agora]({link})",
        "",
        "\\#oferta \\#aliexpress",
    ]

    return "\n".join(lines)


def _escape_md(text: str) -> str:
    """Escapa caracteres reservados do MarkdownV2 do Telegram."""
    reserved = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in reserved else c for c in str(text))


async def send_offer_to_telegram(
    offer: dict[str, Any],
    affiliate_link: str,
) -> bool:
    """
    Envia o card da oferta via sendPhoto (com imagem) ou
    sendMessage (fallback sem imagem).
    Retorna True em caso de sucesso.
    """
    caption = _format_offer_caption(offer, affiliate_link)
    image_url: str | None = offer.get("url_imagem")
    client = get_http_client()

    async def _send_photo() -> httpx.Response:
        return await client.post(
            f"{TELEGRAM_API_URL}/sendPhoto",
            json={
                "chat_id": CHAT_ID,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "MarkdownV2",
            },
        )

    async def _send_text() -> httpx.Response:
        return await client.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": caption,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": False,
            },
        )

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=3, max=30),
            retry=retry_if_exception_type(
                (httpx.ConnectError, httpx.TimeoutException)
            ),
            reraise=True,
        ):
            with attempt:
                resp = await (_send_photo() if image_url else _send_text())

                # Telegram 429 Too Many Requests — respeitar retry_after
                if resp.status_code == 429:
                    retry_after = int(
                        resp.json().get("parameters", {}).get("retry_after", 10)
                    )
                    logger.warning(
                        "Rate limit Telegram. Aguardando %ds.", retry_after
                    )
                    await asyncio.sleep(retry_after)
                    raise httpx.ConnectError("rate_limit_retry")  # força nova tentativa

                if resp.status_code == 400 and image_url:
                    # Imagem inválida/inacessível — tenta fallback de texto
                    logger.warning(
                        "Imagem inválida para oferta %s. Usando fallback texto.",
                        offer["id"],
                    )
                    resp = await _send_text()

                resp.raise_for_status()

        logger.info("Oferta %s enviada ao Telegram com sucesso.", offer["id"])
        return True

    except RetryError as exc:
        logger.error(
            "Telegram indisponível após retries para oferta %s: %s",
            offer["id"],
            exc,
        )
        return False
    except httpx.HTTPStatusError as exc:
        logger.error(
            "HTTP %s ao enviar oferta %s: %s",
            exc.response.status_code,
            offer["id"],
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Loop principal assíncrono
# ---------------------------------------------------------------------------

async def process_cycle() -> None:
    """
    Executa um ciclo completo de processamento:
    busca → gera links → envia → atualiza status.
    """
    logger.info("Iniciando ciclo de processamento...")
    offers = database.fetch_pending_offers(limit=BATCH_SIZE)

    if not offers:
        logger.info("Nenhuma oferta pendente neste ciclo.")
        return

    logger.info("%d oferta(s) encontrada(s) para envio.", len(offers))

    for offer in offers:
        offer_id: str = offer["id"]
        product_url: str = offer.get("url_produto", "")

        if not product_url:
            logger.warning("Oferta %s sem URL de produto. Pulando.", offer_id)
            database.increment_attempt(offer_id)
            continue

        # 1. Gera link de afiliado
        affiliate_link = await generate_affiliate_link(
            product_url, offer.get("tag_afiliado")
        )

        # 2. Envia para o Telegram
        success = await send_offer_to_telegram(offer, affiliate_link)

        # 3. Atualiza status no Supabase
        if success:
            database.mark_as_sent(offer_id)
        else:
            database.increment_attempt(offer_id)

        # Pausa entre envios para respeitar rate limit do Telegram
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

    # Valida conectividade com Supabase na inicialização
    try:
        database.get_client()
    except Exception as exc:
        logger.critical("Falha ao conectar ao Supabase: %s", exc)
        sys.exit(1)

    while True:
        try:
            await process_cycle()
        except Exception as exc:
            # Captura genérica para evitar crash do worker
            logger.exception(
                "Erro inesperado no ciclo principal: %s. Continuando...", exc
            )
        finally:
            # asyncio.sleep libera a event loop — zero CPU ocioso
            logger.debug("Dormindo por %ds...", POLL_INTERVAL)
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot encerrado pelo operador.")
    finally:
        if _http_client and not _http_client.is_closed:
            asyncio.run(_http_client.aclose())
