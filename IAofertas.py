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
# 1. SERVIDOR WEB (Health Check para Render/Railway)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sniper v37: System Online")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# =====================================================================
# 2. CONFIGURAÇÕES E LOGS
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s"
)
log = logging.getLogger("Sniper_V37")

CATEGORIAS = [
    ("Xiaomi smartphone", "smartphones"), ("Poco phone", "smartphones"),
    ("Redmi phone", "smartphones"), ("Anker earbuds", "audio"),
    ("TWS earphones bluetooth", "audio"), ("Baseus charger", "carregadores"),
    ("Ugreen cable", "carregadores"), ("SSD M2 NVMe", "armazenamento"),
    ("Ryzen mini PC", "computadores"), ("smartwatch AMOLED", "wearables"),
    ("Nintendo Switch acessorio", "games"), ("controle gamepad PC", "games")
]

PALAVRAS_BANIDAS = [
    "wig", "hair", "dress", "clothes", "shoe", "underwear", "bra",
    "nail", "makeup", "lipstick", "perfume", "garden", "toy", "doll"
]

MIN_RATING = 4.3
MIN_PRICE_BRL = 30.0
MAX_POSTS_POR_HORA = 10

# =====================================================================
# 3. UTILITÁRIOS
# =====================================================================
def fmt_brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def cortar_titulo(titulo: str, max_chars: int = 65) -> str:
    if len(titulo) <= max_chars: return titulo
    return titulo[:max_chars].rsplit(" ", 1)[0] + "…"

def link_com_utm(url: str, tipo: str, sub: str) -> str:
    params = urlencode({"utm_source": "telegram", "utm_medium": "bot", "utm_campaign": tipo, "utm_content": sub})
    return f"{url}{'&' if '?' in url else '?'}{params}"

# =====================================================================
# 4. BOT PRINCIPAL
# =====================================================================
class AliExpressSniperBot:
    def __init__(self):
        load_dotenv()
        db_user = os.getenv("DB_USER", "postgres").strip()
        db_pw = quote_plus(os.getenv("DB_PASSWORD", "").strip())
        db_host = os.getenv("DB_HOST", "").strip()
        db_port = os.getenv("DB_PORT", "5432").strip()
        db_name = os.getenv("DB_NAME", "postgres").strip()
        
        self.db_url = f"postgresql://{db_user}:{db_pw}@{db_host}:{db_port}/{db_name}"
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

    async def setup_db(self):
        log.info("🐘 Conectando ao Banco de Dados...")
        try:
            # FIX: statement_cache_size=0 é CRÍTICO para PgBouncer/Render/Supabase
            self.pool = await asyncpg.create_pool(
                self.db_url, ssl="require", min_size=1, max_size=5,
                statement_cache_size=0 
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
            log.info("✅ BANCO CONECTADO (Fix PgBouncer Ativo)!")
        except Exception as e:
            log.error(f"❌ ERRO DB: {e}")
            sys.exit(1)

    def sign_ali(self, p: dict) -> str:
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    def analisar_oferta(self, p: dict) -> dict | None:
        titulo = str(p.get("product_title", ""))
        if any(b in titulo.lower() for b in PALAVRAS_BANIDAS): return None

        rating = float(p.get("evaluate_rate") or 0)
        if 0 < rating < MIN_RATING: return None

        orig = float(p.get("original_price") or 0)
        venda = float(p.get("target_sale_price") or p.get("sale_price") or 0)
        app = float(p.get("app_sale_price") or 0)
        
        precos = [v for v in [venda, app] if v > 0]
        melhor = min(precos) if precos else 0

        if melhor < MIN_PRICE_BRL or orig <= melhor: return None
        
        desconto_pct = (orig - melhor) / orig * 100
        if desconto_pct > 90: return None

        # Identifica moedas
        disc_info = p.get("sale_price_discount_info")
        tem_moedas = isinstance(disc_info, dict) and float(disc_info.get("discount_price", 0)) > 0
        
        tipo = "moedas" if tem_moedas else "desconto"
        if app > 0 and app < venda * 0.97: tipo = "combo"

        return {"orig": orig, "melhor": melhor, "app": app, "pct": desconto_pct, "tipo": tipo}

    async def enviar_telegram(self, p: dict, oferta: dict, sub: str):
        titulo = html.escape(cortar_titulo(p.get("product_title", "")))
        link = link_com_utm(p.get("promotion_link", ""), oferta["tipo"], sub)
        
        msg = (
            f"🔥 <b>OFERTA {oferta['pct']:.0f}% OFF</b>\n\n"
            f"📦 <b>{titulo}</b>\n\n"
            f"💰 De: <strike>{fmt_brl(oferta['orig'])}</strike>\n"
            f"🔥 Por: <b>{fmt_brl(oferta['melhor'])}</b>\n"
        )
        if oferta["tipo"] == "moedas": msg += "\n🪙 <i>Use moedas para o preço final</i>"
        if oferta["app"] > 0: msg += f"\n📱 <i>No App: {fmt_brl(oferta['app'])}</i>"
        
        msg += f"\n\n🛒 <a href='{link}'>GARANTIR NO ALIEXPRESS</a>"
        
        img = p.get("product_main_image_url")
        try:
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
                    log.info(f"📨 Postado: {p['product_id']} ({oferta['pct']:.0f}% OFF)")
                    return res["result"]["message_id"]
        except Exception as e:
            log.error(f"❌ Erro Telegram: {e}")
        return None

    async def processar_produto(self, p: dict, sub: str):
        pid = str(p.get("product_id", ""))
        oferta = self.analisar_oferta(p)
        if not pid or not oferta: return

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT menor_preco, leituras FROM historico WHERE id = $1", pid)
            agora = int(time.time())

            if row:
                leituras = row['leituras'] + 1
                menor_ref = row['menor_preco']
                
                # Regra: Posta se for o menor preço já visto E desconto > 15%
                if oferta['melhor'] <= menor_ref and oferta['pct'] >= 15:
                    self._posts_na_hora = [t for t in self._posts_na_hora if time.time() - t < 3600]
                    
                    if len(self._posts_na_hora) < MAX_POSTS_POR_HORA:
                        # Evita repetir o mesmo produto em menos de 20h
                        ja_postado = await conn.fetchval("SELECT ts FROM postados WHERE id=$1 AND ts > $2", pid, agora - 72000)
                        if not ja_postado:
                            mid = await self.enviar_telegram(p, oferta, sub)
                            if mid:
                                await conn.execute("INSERT INTO postados (id, tipo, subcategoria, preco, desconto_pct, message_id, ts) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (id) DO UPDATE SET ts=$7", 
                                                 pid, oferta['tipo'], sub, oferta['melhor'], oferta['pct'], mid, agora)

                await conn.execute("UPDATE historico SET menor_preco=LEAST(menor_preco, $1), leituras=$2, ts=$3 WHERE id=$4", 
                                 oferta['melhor'], leituras, agora, pid)
            else:
                # 1ª leitura: Apenas salva para comparar na próxima (evita fakes)
                await conn.execute("INSERT INTO historico (id, preco_original, menor_preco, leituras, ts) VALUES ($1,$2,$3,1,$4)", 
                                 pid, oferta['orig'], oferta['melhor'], agora)

    async def buscar_categoria(self, kw: str, sub: str):
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
            async with self.session.get(self.ali_api, params=params, timeout=25) as r:
                data = await r.json()
                res = data.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {})
                if res.get("resp_code") == 200:
                    prods = res.get("result", {}).get("products", {}).get("product", [])
                    for p in prods: await self.processar_produto(p, sub)
                else:
                    log.warning(f"⚠️ AliAPI {kw}: {res.get('resp_msg')}")
        except Exception as e:
            log.error(f"❌ Erro na busca {kw}: {e}")

    async def run(self):
        await self.setup_db()
        async with aiohttp.ClientSession() as session:
            self.session = session
            log.info("🚀 SNIPER V37 EM EXECUÇÃO!")
            while True:
                for kw, sub in CATEGORIAS:
                    await self.buscar_categoria(kw, sub)
                    await asyncio.sleep(5)
                log.info("💤 Ciclo finalizado. Aguardando 15 min...")
                await asyncio.sleep(900)

# =====================================================================
# 5. START
# =====================================================================
if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
