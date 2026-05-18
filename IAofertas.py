import asyncio
import hashlib
import json
import logging
import time
import html
import os
import sys
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote_plus, urlencode

import aiohttp
import asyncpg
from dotenv import load_dotenv

# =====================================================================
# 1. SERVIDOR WEB (Health Check)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sniper v38: Online")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# =====================================================================
# 2. CONFIGURAÇÕES
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V38")

CATEGORIAS = [
    ("Xiaomi smartphone", "smartphones"), ("Poco phone", "smartphones"),
    ("Redmi phone", "smartphones"), ("Anker earbuds", "audio"),
    ("TWS earphones", "audio"), ("Baseus charger", "carregadores"),
    ("Ugreen cable", "carregadores"), ("SSD M2 NVMe", "armazenamento"),
    ("Ryzen mini PC", "computadores"), ("smartwatch AMOLED", "wearables"),
    ("Nintendo Switch", "games"), ("controle gamepad PC", "games")
]

PALAVRAS_BANIDAS = ["wig", "hair", "dress", "clothes", "shoe", "nail", "toy"]

# =====================================================================
# 3. BOT PRINCIPAL
# =====================================================================
class AliExpressSniperBot:
    def __init__(self):
        load_dotenv()
        self.db_url = self._build_db_url()
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("ID_DO_GRUPO")
        self.ali_key = os.getenv("ALI_KEY")
        self.ali_secret = os.getenv("ALI_SECRET")
        self.ali_tracking = os.getenv("ALI_TRACKING_ID")
        
        self.tg_api = f"https://api.telegram.org/bot{self.token}"
        self.ali_api = "https://api-sg.aliexpress.com/sync"
        self.pool = None
        self.session = None
        self._posts_na_hora = []

    def _build_db_url(self):
        user = os.getenv("DB_USER", "postgres").strip()
        pw = quote_plus(os.getenv("DB_PASSWORD", "").strip())
        host = os.getenv("DB_HOST", "").strip()
        port = os.getenv("DB_PORT", "5432").strip()
        db = os.getenv("DB_NAME", "postgres").strip()
        return f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    # --- FUNÇÃO CRÍTICA: CORRIGE O ERRO DE STRING/PERCENTAGEM ---
    def safe_float(self, val) -> float:
        if val is None: return 0.0
        try:
            if isinstance(val, str):
                # Remove %, R$, espaços e ajusta vírgula para ponto
                val = val.replace('%', '').replace('R$', '').replace(' ', '').replace(',', '.')
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    async def setup_db(self):
        log.info("🐘 Conectando ao Banco de Dados...")
        try:
            self.pool = await asyncpg.create_pool(
                self.db_url, ssl="require", min_size=1, max_size=5,
                statement_cache_size=0 # Necessário para PgBouncer
            )
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (
                        id TEXT PRIMARY KEY, preco_original FLOAT, menor_preco FLOAT, leituras INT DEFAULT 0, ts BIGINT
                    );
                    CREATE TABLE IF NOT EXISTS postados (
                        id TEXT PRIMARY KEY, tipo TEXT, subcategoria TEXT, preco FLOAT, desconto_pct FLOAT, message_id BIGINT, ts BIGINT
                    );
                ''')
            log.info("✅ Banco de Dados pronto!")
        except Exception as e:
            log.error(f"❌ Erro Crítico DB: {e}")
            sys.exit(1)

    def sign_ali(self, p: dict) -> str:
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    def analisar_oferta(self, p: dict) -> dict | None:
        titulo = str(p.get("product_title", ""))
        if any(b in titulo.lower() for b in PALAVRAS_BANIDAS): return None

        # Uso do safe_float para evitar crash com '94.3%'
        rating = self.safe_float(p.get("evaluate_rate"))
        if 0 < rating < 4.3: return None

        orig = self.safe_float(p.get("original_price"))
        venda = self.safe_float(p.get("target_sale_price") or p.get("sale_price"))
        app = self.safe_float(p.get("app_sale_price"))
        
        precos = [v for v in [venda, app] if v > 0]
        melhor = min(precos) if precos else 0

        if melhor < 30.0 or orig <= melhor: return None
        
        pct = ((orig - melhor) / orig) * 100
        if pct > 92: return None

        # Moedas
        disc_info = p.get("sale_price_discount_info")
        tem_moedas = isinstance(disc_info, dict) and self.safe_float(disc_info.get("discount_price")) > 0
        
        tipo = "moedas" if tem_moedas else "desconto"
        if app > 0 and app < venda * 0.98: tipo = "combo"

        return {"orig": orig, "melhor": melhor, "app": app, "pct": pct, "tipo": tipo}

    def fmt_brl(self, valor: float) -> str:
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    async def enviar_telegram(self, p: dict, oferta: dict, sub: str):
        titulo = html.escape(p.get("product_title", "")[:65] + "…")
        link = f"{p.get('promotion_link')}&utm_source=tg&utm_medium=bot&utm_campaign={oferta['tipo']}"
        
        msg = (
            f"🔥 <b>OFERTA {oferta['pct']:.0f}% OFF</b>\n\n"
            f"📦 <b>{titulo}</b>\n\n"
            f"💰 De: <strike>{self.fmt_brl(oferta['orig'])}</strike>\n"
            f"🔥 Por: <b>{self.fmt_brl(oferta['melhor'])}</b>\n"
        )
        if oferta["tipo"] == "moedas": msg += "\n🪙 <i>Preço com moedas no checkout</i>"
        msg += f"\n\n🛒 <a href='{link}'>VER NO ALIEXPRESS</a>"
        
        img = p.get("product_main_image_url")
        try:
            # Rate limit preventivo
            await asyncio.sleep(2)
            if img:
                payload = {"chat_id": self.chat_id, "photo": img, "caption": msg, "parse_mode": "HTML"}
                endpoint = "sendPhoto"
            else:
                payload = {"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"}
                endpoint = "sendMessage"

            async with self.session.post(f"{self.tg_api}/{endpoint}", json=payload) as r:
                res = await r.json()
                if res.get("ok"):
                    self._posts_na_hora.append(time.time())
                    return res["result"]["message_id"]
        except Exception as e:
            log.error(f"Erro Telegram: {e}")
        return None

    async def processar_produto(self, p: dict, sub: str):
        pid = str(p.get("product_id", ""))
        oferta = self.analisar_oferta(p)
        if not pid or not oferta: return

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT menor_preco, leituras FROM historico WHERE id = $1", pid)
            agora = int(time.time())

            if row:
                if oferta['melhor'] <= row['menor_preco'] and oferta['pct'] >= 15:
                    self._posts_na_hora = [t for t in self._posts_na_hora if time.time() - t < 3600]
                    if len(self._posts_na_hora) < 10:
                        ja_postado = await conn.fetchval("SELECT ts FROM postados WHERE id=$1 AND ts > $2", pid, agora - 86400)
                        if not ja_postado:
                            mid = await self.enviar_telegram(p, oferta, sub)
                            if mid:
                                await conn.execute("INSERT INTO postados (id, tipo, subcategoria, preco, desconto_pct, message_id, ts) VALUES ($1,$2,$3,$4,$5,$6,$7)", 
                                                 pid, oferta['tipo'], sub, oferta['melhor'], oferta['pct'], mid, agora)

                await conn.execute("UPDATE historico SET menor_preco=LEAST(menor_preco, $1), leituras=leituras+1, ts=$2 WHERE id=$3", 
                                 oferta['melhor'], agora, pid)
            else:
                await conn.execute("INSERT INTO historico (id, preco_original, menor_preco, leituras, ts) VALUES ($1,$2,$3,1,$4)", 
                                 pid, oferta['orig'], oferta['melhor'], agora)

    async def run(self):
        await self.setup_db()
        async with aiohttp.ClientSession() as session:
            self.session = session
            log.info("🚀 SNIPER v38 INICIADO!")
            while True:
                for kw, sub in CATEGORIAS:
                    log.info(f"🔍 Buscando: {kw}")
                    params = {
                        "app_key": self.ali_key, "method": "aliexpress.affiliate.product.query",
                        "timestamp": str(int(time.time() * 1000)), "format": "json", "v": "2.0",
                        "sign_method": "md5", "keywords": kw, "page_size": "50",
                        "target_currency": "BRL", "target_language": "PT", "tracking_id": self.ali_tracking,
                        "ship_to_country": "BR", "sort": "SALE_PRICE_ASC",
                        "fields": "product_id,product_title,original_price,sale_price,target_sale_price,app_sale_price,promotion_link,product_main_image_url,sale_price_discount_info,evaluate_rate"
                    }
                    params["sign"] = self.sign_ali(params)
                    try:
                        async with self.session.get(self.ali_api, params=params, timeout=30) as r:
                            data = await r.json()
                            res = data.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {})
                            if res.get("resp_code") == 200:
                                prods = res.get("result", {}).get("products", {}).get("product", [])
                                log.info(f"✅ {len(prods)} produtos para '{kw}'")
                                for p in prods: await self.processar_produto(p, sub)
                            else:
                                log.warning(f"⚠️ API Ali {kw}: {res.get('resp_msg')}")
                    except Exception as e:
                        log.error(f"❌ Erro em {kw}: {e}")
                    await asyncio.sleep(5)
                
                log.info("💤 Ciclo finalizado. Aguardando 15 min...")
                await asyncio.sleep(900)

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    try:
        asyncio.run(AliExpressSniperBot().run())
    except KeyboardInterrupt:
        sys.exit(0)
