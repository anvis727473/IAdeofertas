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
from typing import Dict, List, Tuple, Optional

import aiohttp
import asyncpg
from dotenv import load_dotenv

# =====================================================================
# 1. SERVIDOR WEB PARA O RENDER (PREVENT TIMEOUT)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Sniper IA: Online e Operante")

    def log_message(self, format, *args):
        return # Silencia logs de requisição no terminal

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    log.info(f"🌐 Servidor Health Check ativo na porta {port}")
    server.serve_forever()

# =====================================================================
# 2. LOGS E AMBIENTE
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("Sniper_V22_1")

class AliExpressSniperBot:
    MARCAS_SNIPER = [
        "Poco", "Redmi", "Xiaomi", "Nintendo Switch", "Anker", "QCY", 
        "Edifier", "Lenovo", "Baseus", "Ugreen", "Essager", "SSD", 
        "Mouse Razer", "Machenike", "Sonoff", "Parafusadeira", 
        "Projetor Magcubic", "Realme", "Amazfit", "KZ", "Fifine"
    ]
    
    CONCURRENCY = 8
    DELAY_CICLOS = 25
    MIN_SCORE = 30
    MIN_VALOR_BRL = 20.0

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
        self.pool: Optional[asyncpg.Pool] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.sem = asyncio.Semaphore(self.CONCURRENCY)
        self._active = True

    # =====================================================================
    # 3. BANCO DE DADOS POSTGRESQL (FIX PARA PYTHON 3.14)
    # =====================================================================
    async def setup_db(self):
        try:
            # Limpeza rigorosa da URL para evitar o erro de IPv6 do Python 3.14
            url = self.db_url.strip().replace('"', '').replace("'", "")
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)

            # Supabase exige SSL: "require" resolve o erro de conexão recusada
            self.pool = await asyncpg.create_pool(
                url, 
                ssl="require",
                min_size=1,
                max_size=10
            )
            
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (
                        id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT
                    );
                    CREATE TABLE IF NOT EXISTS postados (
                        id TEXT PRIMARY KEY, ts BIGINT
                    );
                ''')
            log.info("🐘 Conectado ao PostgreSQL (Supabase) - Memória Permanente Ativa!")
        except Exception as e:
            log.error(f"❌ Erro Crítico no Banco de Dados: {e}")
            sys.exit(1)

    # =====================================================================
    # 4. INTELIGÊNCIA DE DADOS
    # =====================================================================
    def sanitizar_float(self, valor) -> float:
        try:
            limpo = re.sub(r'[^\d.,]', '', str(valor)).replace(',', '.')
            return float(limpo)
        except: return 0.0

    def sanitizar_int(self, valor) -> int:
        try:
            return int(re.sub(r'[^\d]', '', str(valor)))
        except: return 0

    def limpar_titulo(self, t: str) -> str:
        t = re.sub(r"(?i)\b(Global Version|Original|Versão Global|202[0-9]|Novo|Promo|Smartphone|Tablet|Frete Grátis)\b", "", t)
        t = re.sub(r"[^\w\s-]", "", t)
        return " ".join(t.split()[:6]).strip()

    # =====================================================================
    # 5. ALIEXPRESS API
    # =====================================================================
    def sign_ali(self, p: dict) -> str:
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    async def fetch_ali(self, termo: str) -> List[dict]:
        async with self.sem:
            p = {
                "app_key": self.ali_key, "method": "aliexpress.affiliate.product.query",
                "timestamp": str(int(time.time() * 1000)), "format": "json", "v": "2.0",
                "sign_method": "md5", "keywords": termo, "page_size": "50",
                "page_no": str(random.randint(1, 4)),
                "target_currency": "BRL", "target_language": "PT",
                "tracking_id": self.ali_tracking, "ship_to_country": "BR"
            }
            p["sign"] = self.sign_ali(p)
            try:
                async with self.session.get(self.ali_api, params=p, timeout=15) as r:
                    res = await r.json()
                    return res.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
            except: return []

    async def post_tg(self, d: dict):
        stars_visual = "⭐" * int(d['estrelas'] if d['estrelas'] <= 5 else 5)
        msg = (
            f"🚨 <b>QUEDA DE PREÇO DETECTADA! (-{d['queda']}%)</b>\n\n"
            f"📦 <b>{html.escape(d['titulo'])}</b>\n\n"
            f"💰 <b>Novo Preço: R$ {d['preco_atual']:,.2f}</b>\n"
            f"📉 Preço Base: <strike>R$ {d['preco_hist']:,.2f}</strike>\n\n"
            f"{stars_visual} ({d['estrelas']:.1f})\n"
            f"🛒 +{d['vendas']} unidades vendidas\n"
            f"🤖 <i>AI Sniper Score: {int(d['score'])}/100</i>"
        )
        btns = {"inline_keyboard": [[{"text": "🎯 VER OFERTA NO ALIEXPRESS", "url": d['link']}]]}
        payload = {"chat_id": self.chat_id, "photo": d["img"], "caption": msg, "parse_mode": "HTML", "reply_markup": json.dumps(btns)}
        async with self.session.post(f"{self.tg_api}/sendPhoto", json=payload) as r:
            return r.status == 200

    # =====================================================================
    # 6. ENGINE DE PROCESSAMENTO
    # =====================================================================
    async def process_prod(self, p: dict):
        pid = str(p.get("product_id"))
        preco_atual = self.sanitizar_float(p.get("target_sale_price") or p.get("sale_price"))
        if preco_atual < self.MIN_VALOR_BRL: return

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)
            
            if row:
                p_hist = row['preco']
                if preco_atual < p_hist:
                    queda = int(((p_hist - preco_atual) / p_hist) * 100)
                    threshold = 0.93 if preco_atual > 450 else 0.85
                    
                    if preco_atual <= (p_hist * threshold):
                        ja_postado = await conn.fetchval("SELECT 1 FROM postados WHERE id = $1", pid)
                        if not ja_postado:
                            estrelas = self.sanitizar_float(p.get("evaluate_rate"))
                            vendas = self.sanitizar_int(p.get("lastest_volume") or p.get("volume"))
                            est_f = (estrelas / 100 * 5) if estrelas > 5 else estrelas
                            score = (queda * 1.8) + ((est_f - 4) * 12) + (min(vendas / 50, 15))
                            
                            if score >= self.MIN_SCORE:
                                data = {"titulo": self.limpar_titulo(p['product_title']), "preco_atual": preco_atual, "preco_hist": p_hist, "queda": queda, "link": p['promotion_link'], "img": p['product_main_image_url'], "score": score, "estrelas": est_f, "vendas": vendas}
                                if await self.post_tg(data):
                                    await conn.execute("INSERT INTO postados (id, ts) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET ts = $2", pid, int(time.time()))
                                    log.info(f"✅ POSTADO: {data['titulo']} | -{queda}%")
                
                if preco_atual < p_hist:
                    await conn.execute("UPDATE historico SET preco=$1, ts=$2 WHERE id=$3", preco_atual, int(time.time()), pid)
            else:
                await conn.execute("INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", pid, preco_atual, int(time.time()))

    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=50))
        log.info("🚀 SNIPER IA OMEGA v22.1 INICIADO (Modo Render Web)")

        while self._active:
            try:
                async with self.pool.acquire() as conn:
                    h = await conn.fetchval("SELECT COUNT(*) FROM historico")
                    p = await conn.fetchval("SELECT COUNT(*) FROM postados")
                
                log.info("-" * 50)
                log.info(f"📊 RADAR OMEGA: {h} itens catalogados | {p} enviados")
                log.info("-" * 50)

                tasks = [self.fetch_ali(m) for m in self.MARCAS_SNIPER]
                results = await asyncio.gather(*tasks)
                for prods in results:
                    if prods:
                        for item in prods: await self.process_prod(item)
                
                log.info(f"✨ Ciclo completo. Esperando {self.DELAY_CICLOS}s...")
                await asyncio.sleep(self.DELAY_CICLOS)
            except Exception as e:
                log.error(f"⚠️ Erro no Loop Principal: {e}")
                await asyncio.sleep(20)

if __name__ == "__main__":
    # Servidor Health Check para o Render
    threading.Thread(target=start_web_server, daemon=True).start()
    
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.warning("🛑 Bot parado pelo usuário.")
