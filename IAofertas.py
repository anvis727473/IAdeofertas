import asyncio
import hashlib
import json
import logging
import time
import os
from typing import List, Optional

import aiohttp
import asyncpg
import redis.asyncio as redis
from aiogram import Bot, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("ID_DO_GRUPO")
    ALI_KEY = os.getenv("ALI_KEY")
    ALI_SECRET = os.getenv("ALI_SECRET")
    ALI_TRACKING = os.getenv("ALI_TRACKING_ID")
    DB_URL = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost")
    
    CATEGORIAS = [
        ("Xiaomi smartphone", "smartphones"), ("Poco phone", "smartphones"),
        ("SSD NVMe", "hardware"), ("Ryzen", "cpu"), ("Anker", "audio")
    ]
    BAN_WORDS = ["wig", "hair", "dress", "nail", "toy"]

# --- CAMADA DE DADOS (POSTGRES + REDIS) ---
class Storage:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        self.redis: Optional[redis.Redis] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(Config.DB_URL, ssl="require")
        self.redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
        
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS historico (
                    id TEXT PRIMARY KEY, preco_min FLOAT, ts_update BIGINT
                );
            ''')

    async def is_duplicate(self, product_id: str) -> bool:
        # Check no Redis (expira em 24h) - muito mais rápido que Postgres
        exists = await self.redis.get(f"post:{product_id}")
        return exists is not None

    async def set_posted(self, product_id: str):
        await self.redis.set(f"post:{product_id}", "1", ex=86400)

    async def get_min_price(self, product_id: str) -> float:
        val = await self.pool.fetchval("SELECT preco_min FROM historico WHERE id=$1", product_id)
        return float(val) if val else 999999.0

    async def update_history(self, product_id: str, price: float):
        await self.pool.execute('''
            INSERT INTO historico (id, preco_min, ts_update) VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET preco_min = LEAST(historico.preco_min, $2), ts_update = $3
        ''', product_id, price, int(time.time()))

# --- CLIENTE ALIEXPRESS ---
class AliExpressAPI:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base_url = "https://api-sg.aliexpress.com/sync"

    def _sign(self, params: dict) -> str:
        data = "".join(f"{k}{v}" for k, v in sorted(params.items()) if v is not None)
        return hashlib.md5((Config.ALI_SECRET + data + Config.ALI_SECRET).encode("utf-8")).hexdigest().upper()

    async def fetch_deals(self, keywords: str):
        params = {
            "app_key": Config.ALI_KEY,
            "method": "aliexpress.affiliate.product.query",
            "timestamp": str(int(time.time() * 1000)),
            "format": "json", "v": "2.0", "sign_method": "md5",
            "keywords": keywords, "page_size": "50",
            "target_currency": "BRL", "target_language": "PT",
            "tracking_id": Config.ALI_TRACKING, "ship_to_country": "BR",
            "sort": "SALE_PRICE_ASC",
            "fields": "product_id,product_title,original_price,sale_price,target_sale_price,app_sale_price,promotion_link,product_main_image_url,evaluate_rate,relevant_market_commission_rate"
        }
        params["sign"] = self._sign(params)
        try:
            async with self.session.get(self.base_url, params=params, timeout=15) as r:
                data = await r.json()
                return data.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
        except Exception as e:
            logging.error(f"Erro API Ali: {e}")
            return []

# --- MOTOR DO BOT ---
class SniperBot:
    def __init__(self):
        self.storage = Storage()
        self.bot = Bot(token=Config.TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    def format_brl(self, value: float) -> str:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def calculate_score(self, p: dict) -> float:
        """Sistema de pontuação para curadoria automática"""
        try:
            price = float(p.get("target_sale_price") or p.get("sale_price", 0))
            orig = float(p.get("original_price", 0))
            rating = float(p.get("evaluate_rate", 0) or 0)
            
            if price < 15 or rating < 4.5: return 0
            
            discount_pct = ((orig - price) / orig) * 100 if orig > price else 0
            # Score baseado em desconto e avaliação
            score = (discount_pct * 0.6) + (rating * 10)
            return score
        except: return 0

    async def process_product(self, p: dict, category_name: str):
        pid = str(p["product_id"])
        price = float(p.get("target_sale_price") or p.get("sale_price"))
        
        # 1. Filtros Básicos
        title = p["product_title"]
        if any(w in title.lower() for w in Config.BAN_WORDS): return

        # 2. Score de Qualidade
        score = self.calculate_score(p)
        if score < 40: return # Só posta o que for realmente bom

        # 3. Histórico e Duplicatas
        if await self.storage.is_duplicate(pid): return
        
        min_price_history = await self.storage.get_min_price(pid)
        
        # Só posta se o preço for menor ou igual ao histórico conhecido
        if price <= min_price_history:
            await self.send_to_telegram(p, price)
            await self.storage.set_posted(pid)
        
        await self.storage.update_history(pid, price)

    async def send_to_telegram(self, p: dict, price: float):
        orig = float(p.get("original_price", 0))
        pct = ((orig - price) / orig * 100) if orig > price else 0
        
        caption = (
            f"🔥 <b>{p['product_title'][:70]}...</b>\n\n"
            f"❌ De: <strike>{self.format_brl(orig)}</strike>\n"
            f"✅ Por: <b>{self.format_brl(price)}</b>\n\n"
            f"📉 <b>{pct:.0f}% de DESCONTO</b>\n"
            f"⭐ Avaliação: {p.get('evaluate_rate', 'N/A')}\n\n"
            f"🛒 <a href='{p['promotion_link']}'>CLIQUE AQUI PARA COMPRAR</a>"
        )
        
        try:
            await self.bot.send_photo(
                chat_id=Config.CHAT_ID,
                photo=p["product_main_image_url"],
                caption=caption
            )
            await asyncio.sleep(3) # Rate limit preventivo
        except Exception as e:
            logging.error(f"Erro envio Telegram: {e}")

    async def run(self):
        await self.storage.connect()
        async with aiohttp.ClientSession() as session:
            ali = AliExpressAPI(session)
            logging.info("🚀 Sniper Pro Iniciado")
            
            while True:
                for kw, cat in Config.CATEGORIAS:
                    logging.info(f"🔎 Pesquisando {kw}...")
                    products = await ali.fetch_deals(kw)
                    
                    for p in products:
                        await self.process_product(p, cat)
                        await asyncio.sleep(0.5) # Evita spam no processamento
                        
                    await asyncio.sleep(10) # Pausa entre categorias
                
                logging.info("💤 Ciclo completo. Aguardando 15 min...")
                await asyncio.sleep(900)

if __name__ == "__main__":
    try:
        asyncio.run(SniperBot().run())
    except KeyboardInterrupt:
        pass
