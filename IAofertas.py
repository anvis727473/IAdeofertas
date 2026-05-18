"""
AliExpress Sniper Agent — Telegram Deal Bot
============================================
Arquitetura de agente com pipeline: Fetch → Filter → Score → Deduplicate → Post
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

import aiohttp
import asyncpg
import redis.asyncio as redis
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO — falha rápido se variável obrigatória estiver ausente
# ---------------------------------------------------------------------------

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Variável de ambiente obrigatória não definida: {key}")
    return val


@dataclass(frozen=True)
class Config:
    telegram_token: str = field(default_factory=lambda: _require("TELEGRAM_TOKEN"))
    chat_id: str        = field(default_factory=lambda: _require("ID_DO_GRUPO"))
    ali_key: str        = field(default_factory=lambda: _require("ALI_KEY"))
    ali_secret: str     = field(default_factory=lambda: _require("ALI_SECRET"))
    ali_tracking: str   = field(default_factory=lambda: _require("ALI_TRACKING_ID"))
    db_url: str         = field(default_factory=lambda: (
        f"postgresql://{_require('DB_USER')}:{_require('DB_PASSWORD')}"
        f"@{_require('DB_HOST')}:{_require('DB_PORT')}/{_require('DB_NAME')}"
    ))
    redis_url: str      = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost"))

    # Agente
    min_price_brl: float    = 15.0
    min_rating: float       = 4.5
    min_score: float        = 40.0
    min_discount_pct: float = 10.0   # ignora produtos com desconto irrisório
    cycle_interval_s: int   = 900    # 15 minutos entre ciclos completos
    category_pause_s: int   = 10     # pausa entre categorias
    post_cooldown_s: int    = 3      # rate-limit do Telegram
    dedup_ttl_s: int        = 86400  # 24h no Redis

    # Categorias: (keyword, label)
    categories: tuple = (
        ("Xiaomi smartphone", "smartphones"),
        ("Poco phone", "smartphones"),
        ("SSD NVMe", "hardware"),
        ("Ryzen", "cpu"),
        ("Anker", "audio"),
        ("notebook ultrabook", "notebooks"),
        ("smartwatch", "wearables"),
    )

    # Palavras banidas no título (case-insensitive)
    ban_words: tuple = ("wig", "hair", "dress", "nail", "toy", "lace", "weave")


# ---------------------------------------------------------------------------
# LOGGING ESTRUTURADO
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("sniper")


log = setup_logging()


# ---------------------------------------------------------------------------
# MODELOS DE DADOS
# ---------------------------------------------------------------------------

@dataclass
class Product:
    id: str
    title: str
    price: float
    original_price: float
    rating: float
    commission_rate: float
    link: str
    image_url: str
    category: str

    @property
    def discount_pct(self) -> float:
        if self.original_price > self.price:
            return (self.original_price - self.price) / self.original_price * 100
        return 0.0

    @property
    def title_fingerprint(self) -> str:
        """Hash do título normalizado — detecta duplicatas com IDs diferentes."""
        normalized = re.sub(r"[^a-z0-9]", "", self.title.lower())[:60]
        return hashlib.md5(normalized.encode()).hexdigest()


@dataclass
class CycleMetrics:
    fetched: int = 0
    filtered_ban: int = 0
    filtered_score: int = 0
    filtered_price: int = 0
    filtered_duplicate: int = 0
    posted: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (
            f"Ciclo — buscados={self.fetched} | ban={self.filtered_ban} | "
            f"score={self.filtered_score} | preço={self.filtered_price} | "
            f"duplicata={self.filtered_duplicate} | postados={self.posted} | erros={self.errors}"
        )


# ---------------------------------------------------------------------------
# STORAGE (PostgreSQL + Redis)
# ---------------------------------------------------------------------------

class Storage:
    def __init__(self, config: Config):
        self.config = config
        self.pool: Optional[asyncpg.Pool] = None
        self.cache: Optional[redis.Redis] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.config.db_url, ssl="require", min_size=2, max_size=10)
        self.cache = redis.from_url(self.config.redis_url, decode_responses=True)

        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    product_id   TEXT PRIMARY KEY,
                    min_price    NUMERIC(12,2) NOT NULL,
                    last_seen    BIGINT NOT NULL,
                    post_count   INT DEFAULT 0
                );
            """)
        log.info("Storage conectado (Postgres + Redis)")

    async def close(self):
        if self.pool:
            await self.pool.close()
        if self.cache:
            await self.cache.aclose()

    # --- Deduplicação ---

    async def is_duplicate(self, product: Product) -> bool:
        """Checa por ID e por fingerprint de título para evitar repost."""
        pipe = self.cache.pipeline()
        pipe.exists(f"post:id:{product.id}")
        pipe.exists(f"post:fp:{product.title_fingerprint}")
        results = await pipe.execute()
        return any(results)

    async def mark_posted(self, product: Product):
        pipe = self.cache.pipeline()
        ttl = self.config.dedup_ttl_s
        pipe.set(f"post:id:{product.id}", "1", ex=ttl)
        pipe.set(f"post:fp:{product.title_fingerprint}", "1", ex=ttl)
        await pipe.execute()

    # --- Histórico de preços ---

    async def get_min_price(self, product_id: str) -> float:
        val = await self.pool.fetchval(
            "SELECT min_price FROM price_history WHERE product_id = $1", product_id
        )
        return float(val) if val else float("inf")

    async def update_history(self, product: Product, posted: bool):
        await self.pool.execute("""
            INSERT INTO price_history (product_id, min_price, last_seen, post_count)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (product_id) DO UPDATE
                SET min_price  = LEAST(price_history.min_price, $2),
                    last_seen  = $3,
                    post_count = price_history.post_count + $4
        """, product.id, product.price, int(time.time()), 1 if posted else 0)


# ---------------------------------------------------------------------------
# CLIENTE ALIEXPRESS API
# ---------------------------------------------------------------------------

class AliExpressAPI:
    BASE_URL = "https://api-sg.aliexpress.com/sync"
    MAX_RETRIES = 3

    def __init__(self, session: aiohttp.ClientSession, config: Config):
        self.session = session
        self.config = config

    def _sign(self, params: dict) -> str:
        secret = self.config.ali_secret
        body = "".join(f"{k}{v}" for k, v in sorted(params.items()) if v is not None)
        return hashlib.md5((secret + body + secret).encode()).hexdigest().upper()

    async def fetch_products(self, keywords: str, page_size: int = 50) -> List[dict]:
        params = {
            "app_key": self.config.ali_key,
            "method": "aliexpress.affiliate.product.query",
            "timestamp": str(int(time.time() * 1000)),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "keywords": keywords,
            "page_size": str(page_size),
            "target_currency": "BRL",
            "target_language": "PT",
            "tracking_id": self.config.ali_tracking,
            "ship_to_country": "BR",
            "sort": "SALE_PRICE_ASC",
            "fields": (
                "product_id,product_title,original_price,sale_price,"
                "target_sale_price,promotion_link,product_main_image_url,"
                "evaluate_rate,relevant_market_commission_rate"
            ),
        }
        params["sign"] = self._sign(params)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                async with self.session.get(
                    self.BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    return (
                        data
                        .get("aliexpress_affiliate_product_query_response", {})
                        .get("resp_result", {})
                        .get("result", {})
                        .get("products", {})
                        .get("product", [])
                    )
            except aiohttp.ClientResponseError as e:
                log.warning(f"HTTP {e.status} ao buscar '{keywords}' (tentativa {attempt})")
            except asyncio.TimeoutError:
                log.warning(f"Timeout ao buscar '{keywords}' (tentativa {attempt})")
            except Exception as e:
                log.error(f"Erro inesperado ao buscar '{keywords}': {e}")

            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)  # backoff exponencial

        return []

    def parse(self, raw: dict, category: str) -> Optional[Product]:
        """Converte dict bruto da API em Product. Retorna None se dados críticos faltarem."""
        try:
            price = float(raw.get("target_sale_price") or raw.get("sale_price") or 0)
            orig  = float(raw.get("original_price") or 0)
            if price <= 0:
                return None
            return Product(
                id=str(raw["product_id"]),
                title=raw.get("product_title", ""),
                price=price,
                original_price=orig if orig > price else price,
                rating=float(raw.get("evaluate_rate") or 0),
                commission_rate=float(raw.get("relevant_market_commission_rate") or 0),
                link=raw.get("promotion_link", ""),
                image_url=raw.get("product_main_image_url", ""),
                category=category,
            )
        except (KeyError, ValueError, TypeError) as e:
            log.debug(f"Erro ao parsear produto: {e} | raw={raw}")
            return None


# ---------------------------------------------------------------------------
# PIPELINE DO AGENTE
# ---------------------------------------------------------------------------

class ScoringEngine:
    """Calcula a relevância de um produto para postagem."""

    def __init__(self, config: Config):
        self.config = config

    def score(self, product: Product, min_price_history: float) -> float:
        """
        Score composto (0–100):
          - 40% desconto percentual (normalizado até 60%)
          - 25% novidade de preço (quanto abaixo do histórico)
          - 20% avaliação
          - 15% taxa de comissão
        """
        if product.price < self.config.min_price_brl:
            return 0.0
        if product.rating < self.config.min_rating:
            return 0.0
        if product.discount_pct < self.config.min_discount_pct:
            return 0.0

        discount_score  = min(product.discount_pct / 60, 1.0) * 40
        rating_score    = ((product.rating - self.config.min_rating) / (5 - self.config.min_rating)) * 20
        commission_score = min(product.commission_rate / 10, 1.0) * 15

        # Bônus por preço mínimo histórico
        if min_price_history < float("inf"):
            price_ratio = (min_price_history - product.price) / min_price_history
            novelty_score = max(min(price_ratio * 100, 1.0), 0.0) * 25
        else:
            novelty_score = 12.5  # produto novo — score neutro

        return discount_score + rating_score + commission_score + novelty_score


class TelegramPublisher:
    def __init__(self, bot: Bot, config: Config):
        self.bot = bot
        self.config = config

    @staticmethod
    def _brl(value: float) -> str:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def _build_caption(self, product: Product) -> str:
        title = product.title[:80].rstrip()
        commission_tag = f"💰 Comissão: {product.commission_rate:.1f}%\n" if product.commission_rate > 5 else ""
        return (
            f"🔥 <b>{title}...</b>\n\n"
            f"❌ De: <strike>{self._brl(product.original_price)}</strike>\n"
            f"✅ Por: <b>{self._brl(product.price)}</b>\n\n"
            f"📉 <b>{product.discount_pct:.0f}% de DESCONTO</b>\n"
            f"⭐ Avaliação: {product.rating:.1f}\n"
            f"{commission_tag}"
            f"🏷️ #{product.category}\n\n"
            f"🛒 <a href='{product.link}'>CLIQUE AQUI PARA COMPRAR</a>"
        )

    async def publish(self, product: Product) -> bool:
        caption = self._build_caption(product)
        try:
            await self.bot.send_photo(
                chat_id=self.config.chat_id,
                photo=product.image_url,
                caption=caption,
            )
            await asyncio.sleep(self.config.post_cooldown_s)
            return True
        except TelegramRetryAfter as e:
            log.warning(f"Rate limit Telegram — aguardando {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 1)
            return await self.publish(product)  # re-tenta após espera
        except TelegramBadRequest as e:
            log.error(f"BadRequest Telegram [{product.id}]: {e}")
            return False
        except Exception as e:
            log.error(f"Erro ao publicar [{product.id}]: {e}")
            return False


# ---------------------------------------------------------------------------
# AGENTE PRINCIPAL
# ---------------------------------------------------------------------------

class SniperAgent:
    def __init__(self, config: Config):
        self.config = config
        self.storage = Storage(config)
        self.bot = Bot(
            token=config.telegram_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.scorer = ScoringEngine(config)
        self.publisher = TelegramPublisher(self.bot, config)

    # --- Pipeline por produto ---

    async def _process(self, raw: dict, category: str, ali: AliExpressAPI, metrics: CycleMetrics):
        metrics.fetched += 1

        product = ali.parse(raw, category)
        if product is None:
            metrics.errors += 1
            return

        # Fase 1 — Filtro de ban words
        if any(w in product.title.lower() for w in self.config.ban_words):
            metrics.filtered_ban += 1
            return

        # Fase 2 — Score inicial (sem histórico)
        preliminary_score = self.scorer.score(product, float("inf"))
        if preliminary_score < self.config.min_score:
            metrics.filtered_score += 1
            return

        # Fase 3 — Histórico de preços (I/O apenas se passou no filtro)
        min_hist = await self.storage.get_min_price(product.id)

        # Não posta se o preço atual é pior que o histórico
        if min_hist < float("inf") and product.price > min_hist * 1.02:  # tolerância de 2%
            metrics.filtered_price += 1
            await self.storage.update_history(product, posted=False)
            return

        # Fase 4 — Deduplicação (último filtro antes do post)
        if await self.storage.is_duplicate(product):
            metrics.filtered_duplicate += 1
            return

        # Fase 5 — Score final com histórico real
        final_score = self.scorer.score(product, min_hist)
        if final_score < self.config.min_score:
            metrics.filtered_score += 1
            return

        log.info(f"✅ Postando [{product.id}] score={final_score:.1f} | {product.title[:50]}")

        if await self.publisher.publish(product):
            await self.storage.mark_posted(product)
            await self.storage.update_history(product, posted=True)
            metrics.posted += 1
        else:
            metrics.errors += 1

    # --- Pipeline por categoria (com semáforo) ---

    async def _process_category(
        self,
        keyword: str,
        category: str,
        ali: AliExpressAPI,
        metrics: CycleMetrics,
        sem: asyncio.Semaphore,
    ):
        async with sem:
            log.info(f"🔎 [{category}] Buscando '{keyword}'...")
            products = await ali.fetch_products(keyword)
            log.info(f"🔎 [{category}] {len(products)} produtos recebidos")

            for raw in products:
                await self._process(raw, category, ali, metrics)
                await asyncio.sleep(0.3)

            await asyncio.sleep(self.config.category_pause_s)

    # --- Ciclo completo ---

    async def _run_cycle(self, ali: AliExpressAPI):
        metrics = CycleMetrics()
        sem = asyncio.Semaphore(3)  # máximo 3 categorias em paralelo

        tasks = [
            self._process_category(kw, cat, ali, metrics, sem)
            for kw, cat in self.config.categories
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        log.info(f"📊 {metrics.summary()}")
        return metrics

    # --- Entry point ---

    async def run(self):
        await self.storage.connect()

        async with aiohttp.ClientSession() as session:
            ali = AliExpressAPI(session, self.config)
            log.info("🚀 SniperAgent iniciado")

            cycle_count = 0
            while True:
                cycle_count += 1
                log.info(f"🔄 Ciclo #{cycle_count} iniciado")
                try:
                    await self._run_cycle(ali)
                except Exception as e:
                    log.exception(f"Erro crítico no ciclo #{cycle_count}: {e}")

                log.info(f"💤 Aguardando {self.config.cycle_interval_s}s até o próximo ciclo...")
                await asyncio.sleep(self.config.cycle_interval_s)

    async def close(self):
        await self.storage.close()
        await self.bot.session.close()


# ---------------------------------------------------------------------------
# ENTRYPOINT
# ---------------------------------------------------------------------------

async def main():
    config = Config()
    agent = SniperAgent(config)
    try:
        await agent.run()
    except KeyboardInterrupt:
        log.info("Encerrando por KeyboardInterrupt...")
    except EnvironmentError as e:
        log.critical(f"Configuração inválida: {e}")
        raise
    finally:
        await agent.close()
        log.info("Agent encerrado.")


if __name__ == "__main__":
    asyncio.run(main())
