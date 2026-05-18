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
import socket
import aiohttp
import asyncpg
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer

# =====================================================================
# 1. SERVIDOR WEB (PRIORIDADE 0 - RENDER)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sniper v29 Online")
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
log = logging.getLogger("Sniper_V29")

class AliExpressSniperBot:
    def __init__(self):
        load_dotenv()
        self.db_url = os.getenv("DATABASE_URL", "").strip().replace("'", "").replace('"', "")
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
    # 3. DNS BYPASS (Resolve IP via Google se o Sistema falhar)
    # =====================================================================
    async def resolver_ip_dns_externo(self, host):
        """ Tenta resolver o IP usando o Google DNS via HTTP se o socket falhar """
        try:
            return socket.gethostbyname(host)
        except:
            log.warning(f"⚠️ DNS Local falhou para {host}. Tentando Google DNS API...")
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://dns.google/resolve?name={host}&type=A") as r:
                    data = await r.json()
                    if "Answer" in data:
                        ip = data["Answer"][0]["data"]
                        log.info(f"✅ IP resolvido via Google: {ip}")
                        return ip
        return None

    async def setup_db(self):
        log.info("🐘 Configurando banco de dados permanente...")
        try:
            # Parsing manual da URL
            url_clean = self.db_url.replace("postgresql://", "").replace("postgres://", "")
            auth, rest = url_clean.split("@")
            user, password = auth.split(":")
            host_port, dbname = rest.split("/")
            host = host_port.split(":")[0]
            port = int(host_port.split(":")[1]) if ":" in host_port else 5432

            # Resolve IP
            host_ip = await self.resolver_ip_dns_externo(host)
            if not host_ip:
                raise Exception("Não foi possível resolver o endereço do banco de dados.")

            self.pool = await asyncpg.create_pool(
                user=user, password=password, host=host_ip, port=port,
                database=dbname, ssl="require", server_hostname=host, timeout=30
            )
            
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT);
                    CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts BIGINT);
                ''')
            log.info("🐘 Supabase conectado com sucesso!")
        except Exception as e:
            log.error(f"❌ Erro Crítico: {e}")
            sys.exit(1)

    # =====================================================================
    # 4. INTELIGÊNCIA IA E TÍTULOS
    # =====================================================================
    def limpar_titulo_ia(self, t):
        """ Extrai Marca + Modelo e remove lixo de SEO """
        t = re.sub(r"(?i)\b(Global Version|Original|Versão Global|202[4-9]|Novo|Promo|Smartphone|Tablet)\b", "", t)
        t = re.sub(r"[^\w\s-]", "", t)
        palavras = t.split()
        return " ".join(palavras[:6]).strip()

    def calcular_desconto_preditivo(self, preco):
        """ IA: Produtos caros precisam de menos % de queda para serem postados """
        if preco < 100: return 0.82   # 18% queda para baratos
        if preco < 500: return 0.88   # 12% queda para médios
        return 0.94                   # 6% queda para premium (>500)

    # =====================================================================
    # 5. ENGINE PRINCIPAL
    # =====================================================================
    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 SNIPER IA ONLINE!")
        
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
                    # Assinatura MD5
                    data = "".join(f"{k}{v}" for k, v in sorted(params.items()) if v is not None)
                    params["sign"] = hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()
                    
                    async with self.session.get(self.ali_api, params=params) as r:
                        resp_json = await r.json()
                        res = resp_json.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {})
                        prods = res.get("products", {}).get("product", [])
                        
                        for p in prods:
                            pid = str(p['product_id'])
                            preco = float(str(p.get("target_sale_price") or p.get("sale_price") or 0).replace(',', '.'))
                            if preco < 20: continue

                            async with self.pool.acquire() as conn:
                                row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)
                                if row:
                                    p_hist = row['preco']
                                    fator = self.calcular_desconto_preditivo(p_hist)
                                    
                                    if preco <= (p_hist * fator):
                                        ja_postado = await conn.fetchval("SELECT 1 FROM postados WHERE id = $1", pid)
                                        if not ja_postado:
                                            titulo = self.limpar_titulo_ia(p['product_title'])
                                            queda = int(((p_hist - preco) / p_hist) * 100)
                                            msg = (f"🚨 <b>QUEDA HISTÓRICA! (-{queda}%)</b>\n\n"
                                                   f"📦 <b>{html.escape(titulo)}...</b>\n\n"
                                                   f"💰 <b>R$ {preco:,.2f}</b>\n"
                                                   f"📉 Antes: <strike>R$ {p_hist:,.2f}</strike>\n\n"
                                                   f"🛒 <a href='{p['promotion_link']}'>COMPRAR AGORA</a>")
                                            
                                            await self.session.post(f"{self.tg_api}/sendPhoto", 
                                                                   json={"chat_id": self.chat_id, "photo": p['product_main_image_url'], "caption": msg, "parse_mode": "HTML"})
                                            await conn.execute("INSERT INTO postados (id, ts) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET ts = $2", pid, int(time.time()))
                                            log.info(f"✅ POSTADO: {titulo}")
                                    
                                    if preco < p_hist:
                                        await conn.execute("UPDATE historico SET preco=$1, ts=$2 WHERE id=$3", preco, int(time.time()), pid)
                                else:
                                    await conn.execute("INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", pid, preco, int(time.time()))
                
                await asyncio.sleep(40)
            except Exception as e:
                log.error(f"Erro no ciclo: {e}")
                await asyncio.sleep(20)

if __name__ == "__main__":
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
