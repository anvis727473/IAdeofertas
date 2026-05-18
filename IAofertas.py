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
# 1. SERVIDOR WEB
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sniper v34: System Online")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# =====================================================================
# 2. LOGS
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V34")

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
    # Usa original_price como referência real — imune a produtos já baratos
    # =====================================================================
    async def setup_db(self):
        log.info("🐘 Conectando ao Banco de Dados...")
        try:
            self.pool = await asyncpg.create_pool(
                self.db_url, ssl="require",
                min_size=1, max_size=5, command_timeout=60
            )
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (
                        id              TEXT PRIMARY KEY,
                        preco_original  FLOAT,
                        menor_preco     FLOAT,
                        ts              BIGINT
                    );
                    CREATE TABLE IF NOT EXISTS postados (
                        id   TEXT PRIMARY KEY,
                        tipo TEXT,
                        ts   BIGINT
                    );
                ''')
            log.info("✅ BANCO CONECTADO!")
        except Exception as e:
            log.error(f"❌ ERRO DE CONEXÃO: {e}")
            sys.exit(1)

    # =====================================================================
    # 4. ASSINATURA ALI
    # =====================================================================
    def sign_ali(self, p):
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    # =====================================================================
    # 5. EXTRAÇÃO DE PREÇOS E TIPO DE OFERTA
    # =====================================================================
    def analisar_oferta(self, p):
        """
        Retorna dict com todos os preços e tipo de oferta detectado.
        Tipos: 'desconto', 'cupom', 'moedas', 'combo'
        """
        def to_float(v):
            try:
                return float(str(v or 0).replace(",", "."))
            except:
                return 0.0

        preco_original = to_float(p.get("original_price"))
        preco_venda    = to_float(p.get("target_sale_price") or p.get("sale_price"))
        preco_app      = to_float(p.get("app_sale_price"))          # preço exclusivo do app
        valor_cupom    = to_float(p.get("target_app_sale_price"))   # às vezes indica desconto extra
        moedas         = to_float(p.get("sale_price_discount_info", {}).get("discount_price") if isinstance(p.get("sale_price_discount_info"), dict) else 0)

        # Melhor preço real disponível
        precos_validos = [x for x in [preco_venda, preco_app] if x > 0]
        melhor_preco   = min(precos_validos) if precos_validos else 0

        if melhor_preco < 5 or preco_original < 5:
            return None

        # Desconto base em relação ao preço ORIGINAL (não ao histórico)
        desconto_pct = ((preco_original - melhor_preco) / preco_original * 100) if preco_original > 0 else 0

        # Detecta tipos de oferta
        tem_cupom  = valor_cupom > 0 and valor_cupom < melhor_preco
        tem_moedas = moedas > 0
        tem_app    = preco_app > 0 and preco_app < preco_venda

        if tem_cupom and tem_moedas:
            tipo = "combo"
        elif tem_cupom:
            tipo = "cupom"
        elif tem_moedas:
            tipo = "moedas"
        else:
            tipo = "desconto"

        return {
            "preco_original": preco_original,
            "melhor_preco":   melhor_preco,
            "preco_app":      preco_app,
            "valor_cupom":    valor_cupom,
            "moedas":         moedas,
            "desconto_pct":   desconto_pct,
            "tipo":           tipo,
            "tem_app":        tem_app,
        }

    # =====================================================================
    # 6. MONTAGEM DA MENSAGEM POR TIPO
    # =====================================================================
    def montar_mensagem(self, p, oferta):
        titulo  = html.escape(str(p.get("product_title", ""))[:70])
        link    = p.get("promotion_link", "")
        orig    = oferta["preco_original"]
        melhor  = oferta["melhor_preco"]
        pct     = oferta["desconto_pct"]
        tipo    = oferta["tipo"]

        # Cabeçalho por tipo
        if tipo == "combo":
            cabecalho = f"🔥 <b>COMBO INSANO! CUPOM + MOEDAS ({pct:.0f}% OFF)</b>"
        elif tipo == "cupom":
            cabecalho = f"🎟️ <b>CUPOM EXCLUSIVO! ({pct:.0f}% OFF)</b>"
        elif tipo == "moedas":
            cabecalho = f"🪙 <b>DESCONTO COM MOEDAS ALIEXPRESS! ({pct:.0f}% OFF)</b>"
        else:
            cabecalho = f"🚨 <b>PREÇO BAIXOU {pct:.0f}%!</b>"

        msg = (
            f"{cabecalho}\n\n"
            f"📦 <b>{titulo}...</b>\n\n"
            f"💰 <b>R$ {melhor:,.2f}</b>\n"
            f"🏷️ Preço normal: <strike>R$ {orig:,.2f}</strike>\n"
        )

        # Extras por tipo
        if tipo in ("cupom", "combo"):
            msg += f"🎟️ <i>Aplique o cupom disponível na página do produto</i>\n"
        if tipo in ("moedas", "combo"):
            msg += f"🪙 <i>Use suas moedas AliExpress para desconto adicional</i>\n"
        if oferta["tem_app"]:
            msg += f"📱 <i>Preço ainda menor no App: R$ {oferta['preco_app']:,.2f}</i>\n"

        msg += f"\n🛒 <a href='{link}'>GARANTIR OFERTA NO ALIEXPRESS</a>"
        return msg

    # =====================================================================
    # 7. ENVIO TELEGRAM
    # =====================================================================
    async def enviar_oferta(self, p, oferta):
        try:
            msg = self.montar_mensagem(p, oferta)
            resp = await self.session.post(
                f"{self.tg_api}/sendPhoto",
                json={
                    "chat_id":    self.chat_id,
                    "photo":      p.get("product_main_image_url", ""),
                    "caption":    msg,
                    "parse_mode": "HTML"
                }
            )
            data = await resp.json()
            if data.get("ok"):
                log.info(f"🔥 OFERTA ENVIADA! [{oferta['tipo'].upper()}] {p['product_id']} | {oferta['desconto_pct']:.0f}% OFF")
            else:
                log.error(f"❌ Telegram erro: {data}")
        except Exception as e:
            log.error(f"❌ Erro ao enviar Telegram: {e}", exc_info=True)

    # =====================================================================
    # 8. PROCESSAMENTO DO PRODUTO
    # =====================================================================
    async def processar_produto(self, p):
        pid = str(p.get("product_id", ""))
        if not pid:
            return

        oferta = self.analisar_oferta(p)
        if not oferta:
            return

        melhor_preco   = oferta["melhor_preco"]
        preco_original = oferta["preco_original"]
        desconto_pct   = oferta["desconto_pct"]
        tipo           = oferta["tipo"]

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT preco_original, menor_preco FROM historico WHERE id = $1", pid)

                if row:
                    # Usa o maior original_price já visto como referência confiável
                    ref_original = max(row["preco_original"], preco_original)
                    ref_menor    = row["menor_preco"]

                    # Recalcula desconto com referência confiável
                    desconto_real = ((ref_original - melhor_preco) / ref_original * 100) if ref_original > 0 else 0

                    # Posta se:
                    # 1. Desconto >= 15% em relação ao preço original
                    # 2. OU é menor que o menor preço histórico por >= 5%
                    # 3. OU tem cupom/moedas com >= 10% off
                    deve_postar = (
                        desconto_real >= 15 or
                        (melhor_preco <= ref_menor * 0.95) or
                        (tipo in ("cupom", "moedas", "combo") and desconto_real >= 10)
                    )

                    if deve_postar:
                        # Verifica se já postou este tipo hoje (86400s = 1 dia)
                        row_post = await conn.fetchrow("SELECT tipo, ts FROM postados WHERE id = $1", pid)
                        ja_postado_hoje = (
                            row_post and
                            row_post["tipo"] == tipo and
                            (int(time.time()) - row_post["ts"]) < 86400
                        )

                        if not ja_postado_hoje:
                            oferta["desconto_pct"] = desconto_real  # usa desconto real
                            await self.enviar_oferta(p, oferta)
                            await conn.execute(
                                "INSERT INTO postados (id, tipo, ts) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET tipo=$2, ts=$3",
                                pid, tipo, int(time.time())
                            )

                    # Atualiza histórico
                    novo_menor = min(ref_menor, melhor_preco)
                    await conn.execute(
                        "UPDATE historico SET preco_original=$1, menor_preco=$2, ts=$3 WHERE id=$4",
                        ref_original, novo_menor, int(time.time()), pid
                    )

                else:
                    # Primeiro registro — salva e não posta ainda
                    # EXCETO se já tem desconto absurdo (>= 40%) logo de cara
                    await conn.execute(
                        "INSERT INTO historico (id, preco_original, menor_preco, ts) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                        pid, preco_original, melhor_preco, int(time.time())
                    )
                    log.info(f"💾 Novo: {pid} | Original R$ {preco_original:.2f} | Venda R$ {melhor_preco:.2f} | {desconto_pct:.0f}% off [{tipo}]")

                    if desconto_pct >= 40:
                        log.info(f"💥 Desconto absurdo na 1ª vez ({desconto_pct:.0f}%), postando!")
                        await self.enviar_oferta(p, oferta)
                        await conn.execute(
                            "INSERT INTO postados (id, tipo, ts) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET tipo=$2, ts=$3",
                            pid, tipo, int(time.time())
                        )

        except Exception as e:
            log.error(f"❌ Erro ao processar {pid}: {e}", exc_info=True)

    # =====================================================================
    # 9. BUSCA NA API
    # =====================================================================
    async def buscar_marca(self, marca):
        params = {
            "app_key":         self.ali_key,
            "method":          "aliexpress.affiliate.product.query",
            "timestamp":       str(int(time.time() * 1000)),
            "format":          "json",
            "v":               "2.0",
            "sign_method":     "md5",
            "keywords":        marca,
            "page_size":       "50",
            "target_currency": "BRL",
            "target_language": "PT",
            "tracking_id":     self.ali_tracking,
            "ship_to_country": "BR",
            "fields":          "product_id,product_title,original_price,sale_price,target_sale_price,app_sale_price,target_app_sale_price,promotion_link,product_main_image_url,sale_price_discount_info"
        }
        params["sign"] = self.sign_ali(params)

        log.info(f"🔍 Buscando: '{marca}'")
        try:
            async with self.session.get(self.ali_api, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                raw  = await r.text()
                data = json.loads(raw)

                resp        = data.get("aliexpress_affiliate_product_query_response", {})
                resp_result = resp.get("resp_result", {})
                resp_code   = resp_result.get("resp_code")
                resp_msg    = resp_result.get("resp_msg")

                if resp_code != 200:
                    log.warning(f"⚠️ '{marca}' → code={resp_code} | {resp_msg}")
                    return

                prods = resp_result.get("result", {}).get("products", {}).get("product", [])
                log.info(f"✅ {len(prods)} produtos para '{marca}'")

                for p in prods:
                    await self.processar_produto(p)

        except asyncio.TimeoutError:
            log.error(f"⏱️ Timeout: '{marca}'")
        except Exception as e:
            log.error(f"❌ Erro em '{marca}': {e}", exc_info=True)

    # =====================================================================
    # 10. LOOP PRINCIPAL
    # =====================================================================
    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 SNIPER v34 INICIADO!")

        marcas = [
            "Xiaomi", "Poco", "Nintendo", "Anker", "Baseus",
            "Ugreen", "Ryzen", "SSD", "Headphone", "Smartwatch"
        ]

        while True:
            try:
                log.info("🔄 Novo ciclo...")
                for marca in marcas:
                    await self.buscar_marca(marca)
                    await asyncio.sleep(5)
                log.info("⏳ Ciclo completo. Aguardando 45s...")
                await asyncio.sleep(45)
            except Exception as e:
                log.error(f"❌ Erro no ciclo: {e}", exc_info=True)
                await asyncio.sleep(20)

# =====================================================================
# 11. ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
