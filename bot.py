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

from aliexpress import AliExpressClient, Product, format_brl
from config import Config
from database import DatabaseManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def run_mock_server():
    port = int(os.getenv("PORT", Config.PORT))

    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            return

    try:
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            httpd.serve_forever()
    except Exception:
        pass


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
        base = ["#AliExpress", "#Oferta", "#Promoção"]
        t = title.lower()

        if "ssd" in t or "nvme" in t:
            base += ["#SSD", "#Hardware"]
        if "teclado" in t:
            base += ["#Teclado", "#Gamer"]
        if "mouse" in t:
            base += ["#Mouse", "#Gamer"]
        if "xiaomi" in t:
            base += ["#Xiaomi", "#Tech"]
        if "baseus" in t:
            base += ["#Baseus", "#Acessórios"]
        if "hub" in t or "dock" in t:
            base += ["#USB", "#Tech"]

        unique = list(dict.fromkeys(base))
        return " ".join(unique)

    def _price_change_percent(self, avg_price: Optional[float], current_price: float) -> Optional[float]:
        if not avg_price or avg_price <= 0:
            return None
        return ((avg_price - current_price) / avg_price) * 100.0

    def _should_publish(self, product: Product, metrics: Dict[str, Optional[float]]) -> Dict[str, str]:
        current = product.price_value
        avg_price = metrics.get("avg_price")
        min_price = metrics.get("min_price")
        last_price = metrics.get("last_price")
        sample_count = metrics.get("sample_count", 0) or 0

        if sample_count == 0:
            return {"publish": "1", "reason": "Novo radar ativo: primeiro registro no histórico."}

        if product.source == "fallback" and product.price_origin == "estimated" and product.score < 3:
            return {"publish": "0", "reason": "Fallback estimado com score baixo."}

        if min_price is not None and current <= min_price:
            return {"publish": "1", "reason": "Menor preço já registrado nos últimos 30 dias."}

        discount_percent = self._price_change_percent(avg_price, current)
        if discount_percent is not None and discount_percent >= Config.DISCOUNT_THRESHOLD:
            return {
                "publish": "1",
                "reason": f"{discount_percent:.1f}% abaixo da média histórica.",
            }

        if last_price is not None and sample_count >= 3 and current <= (last_price * 0.96):
            return {"publish": "1", "reason": "Queda recente detectada em relação à última coleta."}

        return {"publish": "0", "reason": "Sem desconto suficiente para postagem."}

    def _format_message(
        self,
        product: Product,
        metrics: Dict[str, Optional[float]],
        decision_reason: str,
        affiliate_url: str,
    ) -> str:
        title = html.escape(product.title)
        current_price = format_brl(product.price_value)
        avg_price = metrics.get("avg_price")
        min_price = metrics.get("min_price")
        max_price = metrics.get("max_price")
        sample_count = metrics.get("sample_count", 0) or 0

        lines = [
            f"🔥 <b>{title}</b>",
            "",
            f"💰 <b>Agora:</b> {current_price}",
        ]

        if avg_price is not None:
            lines.append(f"📊 <b>Média 30d:</b> {format_brl(avg_price)}")
        if min_price is not None:
            lines.append(f"🏆 <b>Menor 30d:</b> {format_brl(min_price)}")
        if max_price is not None:
            lines.append(f"📈 <b>Maior 30d:</b> {format_brl(max_price)}")

        lines += [
            "",
            f"✅ <b>{html.escape(decision_reason)}</b>",
            f"🧠 <b>Score:</b> {product.score}/6",
        ]

        if product.price_origin != "api":
            lines.append("⚠️ <i>Preço via fallback público</i>")

        lines += [
            "",
            f"🛒 <a href=\"{html.escape(affiliate_url, quote=True)}\">Abrir oferta no AliExpress</a>",
            "",
            self._build_hashtags(product.title),
        ]

        caption = "\n".join(lines)

        if len(caption) > 1000:
            caption = caption[:995] + "…"

        return caption

    async def _send_offer(self, product: Product, caption: str):
        if product.image:
            try:
                if len(caption) <= 900:
                    await self.telegram_bot.send_photo(
                        chat_id=self.chat_id,
                        photo=product.image,
                        caption=caption,
                        parse_mode="HTML",
                    )
                    return

                short_caption = f"🔥 <b>{html.escape(product.title[:80])}</b>\n💰 {format_brl(product.price_value)}"
                await self.telegram_bot.send_photo(
                    chat_id=self.chat_id,
                    photo=product.image,
                    caption=short_caption,
                    parse_mode="HTML",
                )
                await self.telegram_bot.send_message(
                    chat_id=self.chat_id,
                    text=caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return
            except Exception as e:
                logger.warning(f"Falha ao enviar foto, fallback para texto: {e}")

        await self.telegram_bot.send_message(
            chat_id=self.chat_id,
            text=caption,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def process_product(self, product: Product):
        if not product.id or product.price_value <= 0:
            return

        if self.db.has_recent_post(product.id, Config.REPOST_COOLDOWN_DAYS):
            logger.info(f"Produto em cooldown: {product.id}")
            return

        metrics = self.db.get_price_metrics(product.id)

        product_dict = product.to_dict()
        self.db.save_price_sample(product_dict)

        decision = self._should_publish(product, metrics)
        if decision["publish"] != "1":
            logger.info(f"Produto ignorado {product.id}: {decision['reason']}")
            return

        affiliate_url = self.ali_client.generate_affiliate_link(product.url)
        caption = self._format_message(product, metrics, decision["reason"], affiliate_url)

        try:
            await self._send_offer(product, caption)
            self.db.register_post(product_dict, affiliate_url)
            logger.info(f"Post enviado com sucesso: {product.id}")
        except Exception as e:
            logger.exception(f"Erro ao enviar produto para o Telegram: {e}")

    async def run(self):
        logger.info("Bot híbrido profissional ativo.")

        while True:
            try:
                products = await asyncio.to_thread(self.ali_client.search_niche_products)

                if products:
                    for product in products[: Config.MAX_PRODUCTS_PER_CYCLE]:
                        await self.process_product(product)
                        await asyncio.sleep(Config.PRODUCT_DELAY_SECONDS + random.uniform(0.5, 2.0))
                else:
                    logger.info("Nenhum produto encontrado neste ciclo.")

                gc.collect()
                await asyncio.sleep(Config.LOOP_SLEEP_SECONDS)

            except Exception as e:
                logger.exception(f"Erro no loop principal: {e}")
                await asyncio.sleep(45)


async def main():
    bot = OffersBot()
    await bot.run()


if __name__ == "__main__":
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    asyncio.run(main())
