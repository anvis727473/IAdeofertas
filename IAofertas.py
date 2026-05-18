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
import aiohttp
import asyncpg
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# =====================================================================
# 1. SERVIDOR WEB (OBRIGATÓRIO PARA RENDER)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sniper v30: Pooler Mode Active")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=start_web_server, daemon=True).start()

# =====================================================================
# 2. LOGS
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V30")

class AliExpressSniperBot:
    def __init__(self):
        load_dotenv()
        # Limpeza da URL
        raw_url = os.getenv("DATABASE_URL", "").strip().replace("'", "").replace('"', "")
        if raw_url.startswith("postgres://"):
            raw_url = raw_url.replace("postgres://", "postgresql://", 1)
        self.db_url = raw_url

        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("ID_DO_GRUPO")
        self.ali_key = os.getenv("ALI_KEY")
        self.ali_secret = os.getenv("ALI_SECRET")
        self.ali_tracking = os.getenv("ALI_TRACKING_ID")
        
        self.tg_api = f"https://api.telegram.org/bot{self.token}"
        self.ali_api = "https://api-sg.aliexpress.com/sync"
        self.pool = None
        self.session = None

    # =====================================================================
    # 3. SETUP DB (VIA SUPAVISOR POOLER)
    # =====================================================================
    async def setup_db(self):
        log.info("🐘 Conectando ao Pooler do Supabase...")
        try:
            # O asyncpg lida bem com o Pooler IPv4 do Supavisor (porta 6543)
            self.pool = await asyncpg.create_pool(
                self.db_url,
                ssl="require",
                min_size=1,
                max_size=10,
                command_timeout=60
            )
            
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT);
                    CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts BIGINT);
                ''')
            log.info("🐘 Banco conectado via Pooler IPv4 com sucesso!")
        except Exception as e:
            log.error(f"❌ Erro de Conexão: {e}")
            log.error("DICA: Verifique se você está usando a URL do 'Connection Pooler' (porta 6543).")
            sys.exit(1)

    # =====================================================================
    # 4. FUNÇÕES DO BOT
    # =====================================================================
    def sign_ali(self, p):
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 SNIPER v30 ONLINE!")
        
        marcas = ["Xiaomi", "Poco", "Nintendo", "Ryzen", "Anker", "Baseus", "SSD", "Ugreen"]
        
        while True:
            try:
                for m in marcas:
                    params = {
                        "app_key": self.ali_key, "method": "aliexpress.affiliate.product.query",
                        "timestamp": str(int(time.time() * 1000)), "format": "json", "v": "2.0",
                        "sign_method": "md5", "keywords": m, "page_size": "50",
                        "target_currency": "BRL", "target_language": "PT",
                        "tracking_id": self.ali_tracking, "ship_to_country": "BR"
                    }
                    params["sign"] = self.sign_ali(params)
                    
                    async with self.session.get(self.ali_api, params=params) as r:
                        data = await r.json()
                        res = data.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {})
                        prods = res.get("products", {}).get("product", [])
                        
                        for p in prods:
                            pid = str(p['product_id'])
                            # Tratamento de preço
                            try:
                                pr_str = str(p.get("target_sale_price") or p.get("sale_price") or 0).replace(',', '.')
                                preco = float(pr_str)
                            except: continue
                            
                            if preco < 20: continue

                            async with self.pool.acquire() as conn:
                                row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)
                                if row:
                                    if preco <= (row['preco'] * 0.88):
                                        ja_postado = await conn.fetchval("SELECT 1 FROM postados WHERE id = $1", pid)
                                        if not ja_postado:
                                            # Postagem
                                            msg = (f"🚨 <b>QUEDA DE PREÇO!</b>\n\n"
                                                   f"📦 <b>{html.escape(p['product_title'][:70])}...</b>\n\n"
                                                   f"💰 <b>R$ {preco:,.2f}</b>\n"
                                                   f"📉 Antes: <strike>R$ {row['preco']:,.2f}</strike>\n\n"
                                                   f"🛒 <a href='{p['promotion_link']}'>COMPRAR AGORA</a>")
                                            
                                            await self.session.post(f"{self.tg_api}/sendPhoto", 
                                                                   json={"chat_id": self.chat_id, "photo": p['product_main_image_url'], "caption": msg, "parse_mode": "HTML"})
                                            await conn.execute("INSERT INTO postados (id, ts) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET ts = $2", pid, int(time.time()))
                                    
                                    if preco < row['preco']:
                                        await conn.execute("UPDATE historico SET preco=$1, ts=$2 WHERE id=$3", preco, int(time.time()), pid)
                                else:
                                    await conn.execute("INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", pid, preco, int(time.time()))
                
                await asyncio.sleep(45)
            except Exception as e:
                log.error(f"Erro no ciclo: {e}")
                await asyncio.sleep(20)

if __name__ == "__main__":
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
