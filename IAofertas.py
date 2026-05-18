import asyncio
import hashlib
import json
import logging
import time
import html
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote_plus

import aiohttp
import asyncpg
from dotenv import load_dotenv

# =====================================================================
# 1. SERVIDOR WEB (PARA O RENDER MANTER O PROCESSO VIVO)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sniper v33: System Online")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# =====================================================================
# 2. CONFIGURAÇÃO DE LOGS
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V33")

class AliExpressSniperBot:
    def __init__(self):
        load_dotenv()

        db_user     = os.getenv("DB_USER", "").strip()
        db_password = os.getenv("DB_PASSWORD", "").strip()
        db_host     = os.getenv("DB_HOST", "").strip()
        db_port     = os.getenv("DB_PORT", "6543").strip()
        db_name     = os.getenv("DB_NAME", "postgres").strip()
        self.db_url = f"postgresql://{db_user}:{quote_plus(db_password)}@{db_host}:{db_port}/{db_name}"

        self.token        = os.getenv("TELEGRAM_TOKEN")
        self.chat_id      = os.getenv("ID_DO_GRUPO")
        self.ali_key      = os.getenv("ALI_KEY")
        self.ali_secret   = os.getenv("ALI_SECRET")
        self.ali_tracking = os.getenv("ALI_TRACKING_ID")

        self.tg_api  = f"https://api.telegram.org/bot{self.token}"
        self.ali_api = "https://api-sg.aliexpress.com/sync"
        self.pool    = None
        self.session = None

    # =====================================================================
    # 3. BANCO DE DADOS
    # =====================================================================
    async def setup_db(self):
        log.info("🐘 Conectando ao Banco de Dados (Supavisor)...")
        try:
            self.pool = await asyncpg.create_pool(
                self.db_url,
                ssl="require",
                min_size=1,
                max_size=5,
                command_timeout=60
            )
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco FLOAT, ts BIGINT);
                    CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts BIGINT);
                ''')
            log.info("✅ BANCO CONECTADO! Memória permanente ativada.")
        except Exception as e:
            log.error(f"❌ ERRO DE CONEXÃO: {e}")
            sys.exit(1)

    # =====================================================================
    # 4. ASSINATURA DA API ALIEXPRESS
    # =====================================================================
    def sign_ali(self, p):
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    # =====================================================================
    # 5. ENVIO PARA O TELEGRAM
    # =====================================================================
    async def enviar_oferta(self, p, preco, p_hist):
        try:
            queda_pct = ((p_hist - preco) / p_hist) * 100
            msg = (
                f"🚨 <b>PREÇO BAIXOU {queda_pct:.0f}%!</b>\n\n"
                f"📦 <b>{html.escape(str(p.get('product_title', ''))[:70])}...</b>\n\n"
                f"💰 <b>R$ {preco:,.2f}</b>\n"
                f"📉 Antes: <strike>R$ {p_hist:,.2f}</strike>\n\n"
                f"🛒 <a href='{p.get('promotion_link', '')}'>COMPRAR NO ALIEXPRESS</a>"
            )

            tg_resp = await self.session.post(
                f"{self.tg_api}/sendPhoto",
                json={
                    "chat_id": self.chat_id,
                    "photo": p.get("product_main_image_url", ""),
                    "caption": msg,
                    "parse_mode": "HTML"
                }
            )
            tg_data = await tg_resp.json()

            if tg_data.get("ok"):
                log.info(f"🔥 OFERTA ENVIADA com sucesso! Produto: {p['product_id']} | Queda: {queda_pct:.0f}%")
            else:
                log.error(f"❌ Telegram recusou o envio: {tg_data}")

        except Exception as e:
            log.error(f"❌ Erro ao enviar para Telegram: {e}", exc_info=True)

    # =====================================================================
    # 6. PROCESSAMENTO DE PRODUTOS
    # =====================================================================
    async def processar_produto(self, p):
        pid = str(p.get("product_id", ""))
        if not pid:
            return

        try:
            preco = float(str(p.get("target_sale_price") or p.get("sale_price") or 0).replace(",", "."))
        except Exception:
            return

        if preco < 20:
            return

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT preco FROM historico WHERE id = $1", pid)

                if row:
                    p_hist = row["preco"]

                    # Queda maior que 10% → candidato a oferta
                    if preco <= (p_hist * 0.90):
                        ja_postado = await conn.fetchval("SELECT 1 FROM postados WHERE id = $1", pid)
                        if not ja_postado:
                            await self.enviar_oferta(p, preco, p_hist)
                            await conn.execute(
                                "INSERT INTO postados (id, ts) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET ts = $2",
                                pid, int(time.time())
                            )

                    # Atualiza preço se caiu
                    if preco < p_hist:
                        await conn.execute(
                            "UPDATE historico SET preco=$1, ts=$2 WHERE id=$3",
                            preco, int(time.time()), pid
                        )
                        log.info(f"📉 Preço atualizado: {pid} | R$ {p_hist:.2f} → R$ {preco:.2f}")

                else:
                    # Primeiro registro
                    await conn.execute(
                        "INSERT INTO historico (id, preco, ts) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                        pid, preco, int(time.time())
                    )
                    log.info(f"💾 Novo produto: {pid} | R$ {preco:.2f}")

        except Exception as e:
            log.error(f"❌ Erro ao processar produto {pid}: {e}", exc_info=True)

    # =====================================================================
    # 7. BUSCA NA API ALIEXPRESS
    # =====================================================================
    async def buscar_marca(self, marca):
        params = {
            "app_key":      self.ali_key,
            "method":       "aliexpress.affiliate.product.query",
            "timestamp":    str(int(time.time() * 1000)),
            "format":       "json",
            "v":            "2.0",
            "sign_method":  "md5",
            "keywords":     marca,
            "page_size":    "50",
            "target_currency": "BRL",
            "target_language": "PT",
            "tracking_id":  self.ali_tracking,
            "ship_to_country": "BR"
        }
        params["sign"] = self.sign_ali(params)

        log.info(f"🔍 Buscando: '{marca}' | app_key={str(self.ali_key)[:6]}... | tracking={self.ali_tracking}")

        try:
            async with self.session.get(self.ali_api, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                raw = await r.text()
                log.info(f"📡 HTTP {r.status} | Resposta: {raw[:300]}")

                try:
                    data = json.loads(raw)
                except Exception as e:
                    log.error(f"❌ JSON inválido para '{marca}': {e}")
                    return

                resp       = data.get("aliexpress_affiliate_product_query_response", {})
                resp_result = resp.get("resp_result", {})
                resp_code  = resp_result.get("resp_code")
                resp_msg   = resp_result.get("resp_msg")

                log.info(f"📦 '{marca}' → code={resp_code} | msg={resp_msg}")

                if resp_code != 200:
                    log.warning(f"⚠️ API retornou erro para '{marca}': code={resp_code} msg={resp_msg}")
                    return

                prods = resp_result.get("result", {}).get("products", {}).get("product", [])
                log.info(f"✅ {len(prods)} produtos encontrados para '{marca}'")

                for p in prods:
                    await self.processar_produto(p)

        except asyncio.TimeoutError:
            log.error(f"⏱️ Timeout na busca de '{marca}'")
        except Exception as e:
            log.error(f"❌ Erro na busca de '{marca}': {e}", exc_info=True)

    # =====================================================================
    # 8. LOOP PRINCIPAL
    # =====================================================================
    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 SNIPER v33 PRONTO PARA CAÇAR OFERTAS!")

        marcas = ["Xiaomi", "Poco", "Nintendo", "Anker", "Baseus", "Ugreen", "Ryzen", "SSD"]

        while True:
            try:
                log.info("🔄 Iniciando novo ciclo de busca...")

                for marca in marcas:
                    await self.buscar_marca(marca)
                    await asyncio.sleep(5)  # Pausa entre marcas para não sobrecarregar a API

                log.info("⏳ Ciclo completo. Aguardando 45s para o próximo...")
                await asyncio.sleep(45)

            except Exception as e:
                log.error(f"❌ Erro no ciclo principal: {e}", exc_info=True)
                await asyncio.sleep(20)

# =====================================================================
# 9. ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()

    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
