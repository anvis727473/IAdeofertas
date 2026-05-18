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

# =====================================================================
# 1. SERVIDOR WEB (INICIA PRIMEIRO PARA EVITAR TIMEOUT NO RENDER)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Online")
    def log_message(self, format, *args): return

def start_web_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        log.info(f"🌐 Servidor de porta {port} iniciado com sucesso.")
        server.serve_forever()
    except Exception as e:
        log.error(f"Erro no servidor web: {e}")

# =====================================================================
# 2. LOGS
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V25")

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

    # =====================================================================
    # 3. CONEXÃO SEGURA (IGNORA O BUG DO PYTHON 3.14)
    # =====================================================================
    async def setup_db(self):
        log.info("🐘 Extraindo dados de conexão manualmente...")
        try:
            # Regex para quebrar a URL: postgresql://usuario:senha@host:porta/banco
            pattern = r"postgres(?:ql)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(.+)"
            match = re.match(pattern, self.db_url)
            
            if not match:
                raise ValueError("Formato da DATABASE_URL inválido!")

            user, password, host, port, dbname = match.groups()
            port = int(port) if port else 5432

            log.info(f"🐘 Conectando ao Host: {host}")

            # Conectamos passando argumentos SEPARADOS (Isso evita o erro de IPv6)
            self.pool = await asyncpg.create_pool(
                user=user,
                password=password,
                host=host,
                port=port,
                database=dbname,
                ssl="require",
                timeout=30
            )
            
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT);
                    CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts BIGINT);
                ''')
            log.info("🐘 Banco de Dados Supabase Conectado!")
        except Exception as e:
            log.error(f"❌ ERRO CRÍTICO NO BANCO: {e}")
            sys.exit(1)

    # =====================================================================
    # 4. BUSCA E POSTAGEM
    # =====================================================================
    def sign_ali(self, p):
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 BOT SNIPER EM EXECUÇÃO!")
        
        termos = ["Xiaomi", "Poco", "Nintendo", "Ryzen", "SSD", "Anker"]
        
        while True:
            try:
                for termo in termos:
                    params = {
                        "app_key": self.ali_key, "method": "aliexpress.affiliate.product.query",
                        "timestamp": str(int(time.time() * 1000)), "format": "json", "v": "2.0",
                        "sign_method": "md5", "keywords": termo, "page_size": "50",
                        "target_currency": "BRL", "target_language": "PT",
                        "tracking_id": self.ali_tracking, "ship_to_country": "BR"
                    }
                    params["sign"] = self.sign_ali(params)
                    
                    async with self.session.get(self.ali_api, params=params) as r:
                        data = await r.json()
                        prods = data.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
                        
                        for p in prods:
                            pid = str(p['product_id'])
                            preco = float(str(p.get("target_sale_price") or p.get("sale_price") or 0).replace(',', '.'))
                            if preco < 20: continue

                            async with self.pool.acquire() as conn:
                                row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)
                                if row:
                                    if preco <= (row['preco'] * 0.88): # 12% queda
                                        postado = await conn.fetchval("SELECT 1 FROM postados WHERE id = $1", pid)
                                        if not postado:
                                            msg = f"🚨 <b>PREÇO BAIXOU!</b>\n\n📦 <b>{html.escape(p['product_title'][:80])}</b>\n\n💰 <b>R$ {preco:,.2f}</b>\n📉 Antes: R$ {row['preco']:,.2f}\n\n🛒 <a href='{p['promotion_link']}'>COMPRAR AGORA</a>"
                                            await self.session.post(f"{self.tg_api}/sendPhoto", json={"chat_id": self.chat_id, "photo": p['product_main_image_url'], "caption": msg, "parse_mode": "HTML"})
                                            await conn.execute("INSERT INTO postados (id, ts) VALUES ($1, $2)", pid, int(time.time()))
                                    
                                    if preco < row['preco']:
                                        await conn.execute("UPDATE historico SET preco=$1 WHERE id=$2", preco, pid)
                                else:
                                    await conn.execute("INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3)", pid, preco, int(time.time()))
                
                await asyncio.sleep(45)
            except Exception as e:
                log.error(f"Erro no ciclo: {e}")
                await asyncio.sleep(30)

if __name__ == "__main__":
    # INICIA O WEB SERVER PRIMEIRO PARA O RENDER NÃO CANCELAR
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    
    # PEQUENA PAUSA PARA O SERVIDOR SUBIR
    time.sleep(2)
    
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
