import asyncio
import hashlib
import json
import logging
import random
import time
import html
import os
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import aiohttp
import asyncpg
from dotenv import load_dotenv

# 1. SERVIDOR DE SAÚDE (RENDER WEB SERVICE)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# 2. LOGS
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V22_4")

class AliExpressSniperBot:
    def __init__(self):
        load_dotenv()
        self.db_url = os.getenv("DATABASE_URL")
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("ID_DO_GRUPO")
        self.ali_key = os.getenv("ALI_KEY")
        self.ali_secret = os.getenv("ALI_SECRET")
        self.ali_tracking = os.getenv("ALI_TRACKING_ID")
        
        self.tg_api = f"https://api.telegram.org/bot{self.token}"
        self.ali_api = "https://api-sg.aliexpress.com/sync"
        self.pool = None
        self.session = None

    async def setup_db(self):
        log.info("🐘 Conectando ao banco de dados...")
        if not self.db_url:
            raise ValueError("DATABASE_URL não configurada!")

        # Correção automática de prefixo para asyncpg
        url = self.db_url.replace("postgres://", "postgresql://", 1)
        p = urlparse(url)

        try:
            self.pool = await asyncpg.create_pool(
                user=p.username,
                password=p.password,
                host=p.hostname,
                port=p.port or 5432,
                database=p.path.lstrip('/'),
                ssl="require"
            )
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT);
                    CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts BIGINT);
                ''')
            log.info("🐘 Banco de dados pronto!")
        except Exception as e:
            log.error(f"❌ Erro no setup_db: {e}")
            raise

    async def fetch_ali(self, termo):
        ts = str(int(time.time() * 1000))
        params = {
            "app_key": self.ali_key, "method": "aliexpress.affiliate.product.query",
            "timestamp": ts, "format": "json", "v": "2.0", "sign_method": "md5",
            "keywords": termo, "page_size": "40", "target_currency": "BRL",
            "target_language": "PT", "tracking_id": self.ali_tracking, "ship_to_country": "BR"
        }
        # Assinatura
        data = "".join(f"{k}{v}" for k, v in sorted(params.items()) if v is not None)
        params["sign"] = hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()
        
        try:
            async with self.session.get(self.ali_api, params=params, timeout=15) as r:
                res = await r.json()
                return res.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
        except: return []

    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 BOT ONLINE!")
        
        marcas = ["Xiaomi", "Samsung", "iPhone", "Nintendo", "SSD", "Anker", "Baseus"]
        while True:
            try:
                for m in marcas:
                    prods = await self.fetch_ali(m)
                    if prods:
                        for p in prods:
                            pid = str(p['product_id'])
                            preco = float(p.get("target_sale_price") or p.get("sale_price") or 0)
                            if preco < 20: continue

                            async with self.pool.acquire() as conn:
                                row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)
                                if row:
                                    if preco < row['preco'] * 0.90: # 10% queda
                                        # (Lógica de postagem simplificada para teste)
                                        log.info(f"Oportunidade detectada: {pid}")
                                    await conn.execute("UPDATE historico SET preco=$1 WHERE id=$2", preco, pid)
                                else:
                                    await conn.execute("INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3)", pid, preco, int(time.time()))
                
                await asyncio.sleep(60)
            except Exception as e:
                log.error(f"Erro no loop: {e}")
                await asyncio.sleep(30)

if __name__ == "__main__":
    # 1. Inicia Web Server para o Render
    threading.Thread(target=start_web_server, daemon=True).start()
    
    # 2. Inicia o Bot com captura de erro total
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except Exception:
        print("🔴 CRASH FATAL NO BOT:")
        traceback.print_exc()
        sys.exit(1)
