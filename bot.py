# bot.py

import asyncio
import gc
import html
import http.server
import logging
import os
import random
import socketserver
import threading
from typing import Dict, Optional

from telegram import Bot

from aliexpress import AliExpressClient, Product
from config import Config
from database import DatabaseManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


def format_brl(value: float) -> str:
    return (
        f"R$ {value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def run_mock_server():

    port = int(os.getenv("PORT", Config.PORT))

    class HealthHandler(http.server.BaseHTTPRequestHandler):

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            return

    try:

        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            logger.info(f"Health server iniciado na porta {port}")
            httpd.serve_forever()

    except Exception as e:
        logger.error(f"Erro health server: {e}")


class OffersBot:

    def __init__(self):

        Config.validate()

        self.telegram_bot = Bot(token=Config.TELEGRAM_TOKEN)

        self.db = DatabaseManager()

        self.ali_client = AliExpressClient()

        try:
            self.chat_id = int(Config.ID_DO_GRUPO)
        except ValueError:
            self.chat_id = Config.ID_DO_GRUPO

    def _build_hashtags(self, title: str) -> str:

        title_lower = title.lower()

        tags = [
            "#AliExpress",
            "#Oferta",
            "#Promoção"
        ]

        if "ssd" in title_lower:
            tags.append("#SSD")

        if "nvme" in title_lower:
            tags.append("#NVMe")

        if "mouse" in title_lower:
            tags.append("#MouseGamer")

        if "teclado" in title_lower:
            tags.append("#TecladoGamer")

        if "xiaomi" in title_lower:
            tags.append("#Xiaomi")

        if "baseus" in title_lower:
            tags.append("#Baseus")

        if "smartwatch" in title_lower:
            tags.append("#Smartwatch")

        return " ".join(list(dict.fromkeys(tags)))

    def _should_publish(
        self,
        product: Product,
        metrics: Dict
    ):

        avg_price = metrics.get("avg_price")
        min_price = metrics.get("min_price")
        sample_count = metrics.get("sample_count", 0)

        if sample_count == 0:
            return True, "🌟 Novo produto inserido no radar"

        if avg_price and avg_price > 0:

            discount = (
                (avg_price - product.price_value)
                / avg_price
            ) * 100

            if discount >= Config.DISCOUNT_THRESHOLD:

                return (
                    True,
                    f"📉 {discount:.1f}% abaixo da média histórica"
                )

        if min_price and product.price_value <= min_price:

            return (
                True,
                "🏆 Menor preço registrado"
            )

        return False, "Sem desconto relevante"

    def _format_message(
        self,
        product: Product,
        metrics: Dict,
        decision_reason: str,
        affiliate_url: str
    ):

        hashtags = self._build_hashtags(product.title)

        avg_price = metrics.get("avg_price")
        min_price = metrics.get("min_price")

        lines = []

        lines.append("🔥 <b>OFERTA ENCONTRADA PELO RADAR</b>")
        lines.append("")

        lines.append(
            f"🛍 <b>{html.escape(product.title)}</b>"
        )

        lines.append("")

        if avg_price:

            fake_old = round(
                avg_price * random.uniform(1.05, 1.25),
                2
            )

            old_price = (
                f"R$ {fake_old:,.2f}"
                .replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

            lines.append(f"💸 De: <s>{old_price}</s>")

        lines.append(
            f"✅ Por: <b>{product.price_text()}</b>"
        )

        if avg_price:

            discount = (
                (avg_price - product.price_value)
                / avg_price
            ) * 100

            lines.append(
                f"📉 <b>{discount:.1f}% abaixo da média histórica</b>"
            )

        if min_price and product.price_value <= min_price:
            lines.append("🏆 <b>MENOR PREÇO REGISTRADO</b>")

        lines.append("")

        if product.is_choice:
            lines.append("⭐ Produto Choice")

        if product.rating > 0:
            lines.append(f"⭐ Nota: {product.rating}")

        if product.sold_count > 0:
            lines.append(f"📦 {product.sold_count} vendidos")

        if product.shipping:
            lines.append(f"🚚 {product.shipping}")

        lines.append(
            f"🧠 Score IA: {product.score}/11"
        )

        lines.append("")
        lines.append(
            f'🛒 <a href="{affiliate_url}">GARANTIR OFERTA</a>'
        )

        lines.append("")
        lines.append(hashtags)

        return "\n".join(lines)

    async def _send_offer(
        self,
        product: Product,
        message: str
    ):

        try:

            if product.image:

                await self.telegram_bot.send_photo(
                    chat_id=self.chat_id,
                    photo=product.image,
                    caption=message,
                    parse_mode="HTML"
                )

            else:

                await self.telegram_bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )

        except Exception as e:
            logger.exception(f"Erro Telegram: {e}")

    async def process_product(
        self,
        product: Product
    ):

        try:

            if not product.id:
                return

            if product.price_value <= 0:
                return

            if self.db.has_recent_post(
                product.id,
                Config.REPOST_COOLDOWN_DAYS
            ):
                logger.info(f"Cooldown ativo: {product.id}")
                return

            metrics = self.db.get_price_metrics(product.id)

            self.db.save_price_sample(product.to_dict())

            should_publish, reason = self._should_publish(
                product,
                metrics
            )

            if not should_publish:

                logger.info(
                    f"Produto ignorado: {product.id}"
                )

                return

            affiliate_url = (
                self.ali_client.generate_affiliate_link(
                    product.url
                )
            )

            message = self._format_message(
                product,
                metrics,
                reason,
                affiliate_url
            )

            await self._send_offer(
                product,
                message
            )

            self.db.register_post(
                product.to_dict(),
                affiliate_url
            )

            logger.info(
                f"Oferta enviada: {product.id}"
            )

        except Exception as e:
            logger.exception(
                f"Erro processando produto: {e}"
            )

    async def run(self):

        logger.info("Bot profissional iniciado")

        while True:

            try:

                products = await asyncio.to_thread(
                    self.ali_client.search_niche_products
                )

                if not products:

                    logger.info(
                        "Nenhum produto encontrado"
                    )

                    await asyncio.sleep(
                        Config.LOOP_SLEEP_SECONDS
                    )

                    continue

                logger.info(
                    f"{len(products)} produtos encontrados"
                )

                for product in products[
                    :Config.MAX_PRODUCTS_PER_CYCLE
                ]:

                    await self.process_product(product)

                    await asyncio.sleep(
                        Config.PRODUCT_DELAY_SECONDS
                        + random.uniform(0.5, 1.5)
                    )

                gc.collect()

                await asyncio.sleep(
                    Config.LOOP_SLEEP_SECONDS
                )

            except Exception as e:

                logger.exception(
                    f"Erro loop principal: {e}"
                )

                await asyncio.sleep(60)


async def main():

    bot = OffersBot()

    await bot.run()


if __name__ == "__main__":

    server_thread = threading.Thread(
        target=run_mock_server,
        daemon=True
    )

    server_thread.start()

    asyncio.run(main())
