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
import socket  # Importante para forçar IPv4
import aiohttp
import asyncpg
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer

# =====================================================================
# 1. SERVIDOR WEB (PREVENT TIMEOUT)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Sniper v24: Ativo")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V24")

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
    # 3. CONEXÃO FORÇADA IPv4 (Ignora bugs de rede do Python 3.14)
    # =====================================================================
    async def setup_db(self):
        log.info("🐘 Resolvendo endereço do banco (Forçando IPv4)...")
        try:
            # 1. Quebra a URL manualmente
            url = self.db_url.replace("postgresql://", "").replace("postgres://", "")
            auth, rest = url.split("@")
            user, password = auth.split(":")
            host_port, dbname = rest.split("/")
            
            if ":" in host_port:
                host, port = host_port.split(":")
            else:
                host, port = host_port, 5432

            # 2. TRUQUE MÁGICO: Converte o host (texto) em um IP (número)
            # Isso impede que o Python 3.14 tente usar IPv6 e falhe.
            host_ip = socket.gethostbyname(host)
            log.info(f"🐘 Host {host} resolvido para IP {host_ip}")

            # 3. Conecta usando o IP diretamente
            self.pool = await asyncpg.create_pool(
                user=user,
                password=password,
                host=host_ip,  # Usa o IP
                port=int(port),
                database=dbname,
                ssl="require",
                timeout=30
            )
            
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT);
                    CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts BIGINT);
                ''')
            log.info("🐘 Conexão OMEGA estabelecida com sucesso!")
        except Exception as e:
            log.error(f"❌ Erro de Rede Fatal: {e}")
            sys.exit(1)

    # =====================================================================
    # 4. ENGINE
    # =====================================================================
    def sign_ali(self, p):
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 BOT SNIPER ONLINE!")
        
        marcas = ["Xiaomi", "Poco", "Samsung", "Nintendo", "SSD", "Anker", "Baseus"]
        
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
                        res = await r.json()
                        prods = res.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
                        
                        for p in prods:
                            pid = str(p['product_id'])
                            # Limpa o preço de qualquer lixo de texto
                            preco_str = str(p.get("target_sale_price") or p.get("sale_price") or "0")
                            preco = float(re.sub(r'[^\d.]', '', preco_str.replace(',', '.')))
                            
                            if preco < 20: continue

                            async with self.pool.acquire() as conn:
                                row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)
                                if row:
                                    if preco <= (row['preco'] * 0.88): # 12% de queda
                                        ja_postado = await conn.fetchval("SELECT 1 FROM postados WHERE id = $1", pid)
                                        if not ja_postado:
                                            log.info(f"🔥 Oferta Detectada: {pid}")
                                            msg = f"🚨 <b>PREÇO BAIXOU!</b>\n\n📦 <b>{html.escape(p['product_title'][:70])}</b>\n\n💰 <b>R$ {preco:,.2f}</b>\n📉 Antes: R$ {row['preco']:,.2f}\n\n🛒 <a href='{p['promotion_link']}'>COMPRAR AGORA</a>"
                                            await self.session.post(f"{self.tg_api}/sendPhoto", json={"chat_id": self.chat_id, "photo": p['product_main_image_url'], "caption": msg, "parse_mode": "HTML"})
                                            await conn.execute("INSERT INTO postados (id, ts) VALUES ($1, $2)", pid, int(time.time()))
                                    
                                    if preco < row['preco']:
                                        await conn.execute("UPDATE historico SET preco=$1 WHERE id=$2", preco, pid)
                                else:
                                    await conn.execute("INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3)", pid, preco, int(time.time()))
                
                await asyncio.sleep(45)
            except Exception as e:
                log.error(f"Erro no ciclo: {e}")
                await asyncio.sleep(20)

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
