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
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import aiohttp
import asyncpg
from dotenv import load_dotenv

# 1. SERVIDOR DE SAÚDE (Para o Render não matar o bot)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Sniper Omega v22.3 is Online")
    def log_message(self, format, *args): return

def start_web_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Erro no servidor web: {e}")

# 2. LOGS CONFIG
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V22_3")

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
        self.sem = asyncio.Semaphore(5)

    # 3. CONEXÃO BLINDADA (Ignora validação de IP do Python 3.14)
    async def setup_db(self):
        log.info("🐘 Iniciando processo de conexão com Supabase...")
        if not self.db_url:
            log.error("❌ DATABASE_URL não encontrada nas variáveis de ambiente!")
            sys.exit(1)

        try:
            # Desmontando a URL manualmente para evitar o erro de IPv6
            p = urlparse(self.db_url)
            
            # Conexão direta por parâmetros (pula a validação de URL do asyncpg)
            self.pool = await asyncpg.create_pool(
                user=p.username,
                password=p.password,
                host=p.hostname,
                port=p.port or 5432,
                database=p.path.lstrip('/'),
                ssl="require", # Obrigatório para Supabase
                timeout=30
            )
            
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT);
                    CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts BIGINT);
                ''')
            log.info("🐘 Conexão com Supabase estabelecida com sucesso!")
        except Exception as e:
            log.error(f"❌ FALHA FATAL NO BANCO DE DADOS: {e}")
            # Se falhar aqui, o bot vai imprimir o erro exato no log do Render
            sys.exit(1)

    # 4. FUNÇÕES DE APOIO
    def sanitizar_float(self, v):
        try: return float(re.sub(r'[^\d.,]', '', str(v)).replace(',', '.'))
        except: return 0.0

    def limpar_titulo(self, t):
        t = re.sub(r"(?i)\b(Global Version|Original|202[0-9]|Novo|Promo)\b", "", t)
        return " ".join(t.split()[:6]).strip()

    def sign_ali(self, p):
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    # 5. BUSCA E POSTAGEM
    async def fetch_ali(self, termo):
        async with self.sem:
            p = {
                "app_key": self.ali_key, "method": "aliexpress.affiliate.product.query",
                "timestamp": str(int(time.time() * 1000)), "format": "json", "v": "2.0",
                "sign_method": "md5", "keywords": termo, "page_size": "50",
                "target_currency": "BRL", "target_language": "PT",
                "tracking_id": self.ali_tracking, "ship_to_country": "BR"
            }
            p["sign"] = self.sign_ali(p)
            try:
                async with self.session.get(self.ali_api, params=p, timeout=15) as r:
                    res = await r.json()
                    return res.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
            except: return []

    async def process_prod(self, p):
        pid = str(p.get("product_id"))
        preco = self.sanitizar_float(p.get("target_sale_price") or p.get("sale_price"))
        if preco < 20: return

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)
            if row:
                p_hist = row['preco']
                if preco <= (p_hist * 0.88): # 12% de queda
                    ja_postado = await conn.fetchval("SELECT 1 FROM postados WHERE id = $1", pid)
                    if not ja_postado:
                        msg = f"🚨 <b>QUEDA DE PREÇO!</b>\n\n📦 <b>{html.escape(self.limpar_titulo(p['product_title']))}</b>\n\n💰 <b>R$ {preco:,.2f}</b>\n📉 Base: <strike>R$ {p_hist:,.2f}</strike>\n\n🛒 <b><a href='{p['promotion_link']}'>VER NO ALIEXPRESS</a></b>"
                        payload = {"chat_id": self.chat_id, "photo": p['product_main_image_url'], "caption": msg, "parse_mode": "HTML"}
                        async with self.session.post(f"{self.tg_api}/sendPhoto", json=payload) as resp:
                            if resp.status == 200:
                                await conn.execute("INSERT INTO postados (id, ts) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET ts = $2", pid, int(time.time()))
                                log.info(f"✅ POSTADO: {pid}")
                if preco < p_hist:
                    await conn.execute("UPDATE historico SET preco=$1, ts=$2 WHERE id=$3", preco, int(time.time()), pid)
            else:
                await conn.execute("INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", pid, preco, int(time.time()))

    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 SNIPER v22.3 RODANDO!")
        
        marcas = ["Xiaomi", "Poco", "Redmi", "Nintendo Switch", "SSD", "Baseus", "Anker", "Ugreen", "Ryzen", "QCY"]
        
        while True:
            try:
                # Relatório Simples
                async with self.pool.acquire() as conn:
                    h = await conn.fetchval("SELECT COUNT(*) FROM historico")
                    log.info(f"📊 Radar: {h} itens catalogados.")

                tasks = [self.fetch_ali(m) for m in marcas]
                results = await asyncio.gather(*tasks)
                for prods in results:
                    if prods:
                        for item in prods: await self.process_prod(item)
                
                await asyncio.sleep(40)
            except Exception as e:
                log.error(f"Erro no loop: {e}")
                await asyncio.sleep(20)

if __name__ == "__main__":
    # Inicia Web Server
    threading.Thread(target=start_web_server, daemon=True).start()
    
    # Inicia Bot
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except Exception as e:
        print(f"Erro ao iniciar asyncio: {e}")
