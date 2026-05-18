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
        self.wfile.write(b"Sniper v35: System Online")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# =====================================================================
# 2. LOGS
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("Sniper_V35")

# =====================================================================
# CATEGORIAS E KEYWORDS — só tecnologia/eletrônicos relevantes
# Formato: ("keyword de busca", categoria_id_aliexpress)
# =====================================================================
CATEGORIAS = [
    # Smartphones e acessórios
    ("Xiaomi smartphone",        None),
    ("Poco phone",               None),
    ("Redmi phone",              None),
    # Áudio
    ("Anker earbuds",            None),
    ("TWS earphones bluetooth",  None),
    ("headphone gaming",         None),
    # Carregadores e cabos
    ("Baseus charger USB-C",     None),
    ("Ugreen cable fast charge", None),
    ("GaN charger 65W",          None),
    # Armazenamento
    ("SSD M2 NVMe",              None),
    ("USB flash drive",          None),
    # Games e consoles
    ("Nintendo Switch acessorio",None),
    ("controle gamepad PC",      None),
    # Computadores
    ("Ryzen mini PC",            None),
    ("laptop cooling pad",       None),
    # Smartwatch / wearables
    ("smartwatch AMOLED",        None),
    ("smart band fitness",       None),
]

# Palavras-chave que indicam produto FORA DO NICHO — descarta automaticamente
PALAVRAS_BANIDAS = [
    "wig", "hair", "dress", "clothes", "shoe", "underwear", "bra",
    "nail", "makeup", "lipstick", "perfume", "garden", "plant",
    "kitchen", "towel", "bedding", "pillow", "curtain", "toy",
    "doll", "sticker", "poster", "keychain", "ring", "necklace",
    "bracelet", "earring", "wallet", "bag", "backpack", "pet",
    "dog", "cat", "fishing", "camping tent", "bicycle"
]

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

        # Taxa de conversão USD → BRL (atualizada no início do ciclo)
        self.usd_brl = 5.70

        self.tg_api  = f"https://api.telegram.org/bot{self.token}"
        self.ali_api = "https://api-sg.aliexpress.com/sync"
        self.pool    = None
        self.session = None

    # =====================================================================
    # 3. BANCO DE DADOS COM MIGRAÇÃO AUTOMÁTICA
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
                        id             TEXT PRIMARY KEY,
                        preco_original FLOAT,
                        menor_preco    FLOAT,
                        ts             BIGINT
                    );
                    CREATE TABLE IF NOT EXISTS postados (
                        id   TEXT PRIMARY KEY,
                        tipo TEXT,
                        ts   BIGINT
                    );
                ''')

                for coluna, tipo_col in [("preco_original", "FLOAT"), ("menor_preco", "FLOAT")]:
                    existe = await conn.fetchval(f"""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name='historico' AND column_name='{coluna}'
                    """)
                    if not existe:
                        await conn.execute(f"ALTER TABLE historico ADD COLUMN {coluna} {tipo_col}")
                        log.info(f"🔧 Coluna '{coluna}' adicionada em historico")

                existe_tipo = await conn.fetchval("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='postados' AND column_name='tipo'
                """)
                if not existe_tipo:
                    await conn.execute("ALTER TABLE postados ADD COLUMN tipo TEXT")
                    log.info("🔧 Coluna 'tipo' adicionada em postados")

                existe_preco = await conn.fetchval("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='historico' AND column_name='preco'
                """)
                if existe_preco:
                    await conn.execute("""
                        UPDATE historico
                        SET preco_original = preco, menor_preco = preco
                        WHERE preco_original IS NULL
                    """)
                    log.info("🔧 Dados migrados de 'preco' → 'preco_original' e 'menor_preco'")

            log.info("✅ BANCO CONECTADO!")
        except Exception as e:
            log.error(f"❌ ERRO DE CONEXÃO: {e}")
            sys.exit(1)

    # =====================================================================
    # 4. ATUALIZA COTAÇÃO USD → BRL
    # =====================================================================
    async def atualizar_cotacao(self):
        try:
            async with self.session.get(
                "https://api.exchangerate-api.com/v4/latest/USD",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json()
                self.usd_brl = data["rates"]["BRL"]
                log.info(f"💱 Cotação atualizada: 1 USD = R$ {self.usd_brl:.2f}")
        except Exception as e:
            log.warning(f"⚠️ Não foi possível atualizar cotação, usando R$ {self.usd_brl:.2f} | {e}")

    # =====================================================================
    # 5. ASSINATURA ALI
    # =====================================================================
    def sign_ali(self, p):
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5((self.ali_secret + data + self.ali_secret).encode("utf-8")).hexdigest().upper()

    # =====================================================================
    # 6. CONVERTE PREÇO PARA BRL
    # Detecta automaticamente se o valor está em USD ou BRL
    # =====================================================================
    def para_brl(self, valor: float) -> float:
        # Se o valor for muito baixo (< 30), provavelmente está em USD
        if 0 < valor < 30:
            convertido = round(valor * self.usd_brl, 2)
            return convertido
        return round(valor, 2)

    # =====================================================================
    # 7. FILTRA PRODUTO FORA DO NICHO
    # =====================================================================
    def is_relevante(self, titulo: str) -> bool:
        titulo_lower = titulo.lower()
        for palavra in PALAVRAS_BANIDAS:
            if palavra in titulo_lower:
                log.info(f"🚫 Descartado (fora do nicho): '{titulo[:50]}'")
                return False
        return True

    # =====================================================================
    # 8. EXTRAÇÃO DE PREÇOS E TIPO DE OFERTA
    # =====================================================================
    def analisar_oferta(self, p):
        def to_float(v):
            try:
                return float(str(v or 0).replace(",", "."))
            except:
                return 0.0

        titulo = str(p.get("product_title", ""))
        if not self.is_relevante(titulo):
            return None

        # Pega os preços brutos da API
        preco_original_raw = to_float(p.get("original_price"))
        preco_venda_raw    = to_float(p.get("target_sale_price") or p.get("sale_price"))
        preco_app_raw      = to_float(p.get("app_sale_price"))
        valor_cupom_raw    = to_float(p.get("target_app_sale_price"))
        moedas_raw         = to_float(
            p.get("sale_price_discount_info", {}).get("discount_price")
            if isinstance(p.get("sale_price_discount_info"), dict) else 0
        )

        # Converte tudo para BRL
        preco_original = self.para_brl(preco_original_raw)
        preco_venda    = self.para_brl(preco_venda_raw)
        preco_app      = self.para_brl(preco_app_raw)
        valor_cupom    = self.para_brl(valor_cupom_raw)
        moedas         = self.para_brl(moedas_raw)

        precos_validos = [x for x in [preco_venda, preco_app] if x > 0]
        melhor_preco   = min(precos_validos) if precos_validos else 0

        # Preço mínimo R$ 30 para evitar bugigangas
        if melhor_preco < 30 or preco_original < 30:
            return None

        desconto_pct = ((preco_original - melhor_preco) / preco_original * 100) if preco_original > 0 else 0

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
    # 9. MONTAGEM DA MENSAGEM POR TIPO
    # =====================================================================
    def montar_mensagem(self, p, oferta):
        titulo = html.escape(str(p.get("product_title", ""))[:70])
        link   = p.get("promotion_link", "")
        orig   = oferta["preco_original"]
        melhor = oferta["melhor_preco"]
        pct    = oferta["desconto_pct"]
        tipo   = oferta["tipo"]

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

        if tipo in ("cupom", "combo"):
            msg += "🎟️ <i>Aplique o cupom disponível na página do produto</i>\n"
        if tipo in ("moedas", "combo"):
            msg += "🪙 <i>Use suas moedas AliExpress para desconto adicional</i>\n"
        if oferta["tem_app"]:
            msg += f"📱 <i>Preço ainda menor no App: R$ {oferta['preco_app']:,.2f}</i>\n"

        msg += f"\n🛒 <a href='{link}'>GARANTIR OFERTA NO ALIEXPRESS</a>"
        return msg

    # =====================================================================
    # 10. ENVIO TELEGRAM
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
                log.info(f"🔥 ENVIADA! [{oferta['tipo'].upper()}] {p['product_id']} | {oferta['desconto_pct']:.0f}% OFF | R$ {oferta['melhor_preco']:.2f}")
            else:
                log.error(f"❌ Telegram erro: {data}")
        except Exception as e:
            log.error(f"❌ Erro ao enviar Telegram: {e}", exc_info=True)

    # =====================================================================
    # 11. PROCESSAMENTO DO PRODUTO
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
        tipo           = oferta["tipo"]

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT preco_original, menor_preco FROM historico WHERE id = $1", pid
                )

                if row:
                    ref_original = max(row["preco_original"] or 0, preco_original)
                    ref_menor    = row["menor_preco"] or melhor_preco

                    desconto_real = ((ref_original - melhor_preco) / ref_original * 100) if ref_original > 0 else 0

                    deve_postar = (
                        desconto_real >= 15 or
                        melhor_preco <= ref_menor * 0.95 or
                        (tipo in ("cupom", "moedas", "combo") and desconto_real >= 10)
                    )

                    if deve_postar:
                        row_post = await conn.fetchrow(
                            "SELECT tipo, ts FROM postados WHERE id = $1", pid
                        )
                        ja_postado_hoje = (
                            row_post and
                            row_post["tipo"] == tipo and
                            (int(time.time()) - row_post["ts"]) < 86400
                        )

                        if not ja_postado_hoje:
                            oferta["desconto_pct"] = desconto_real
                            await self.enviar_oferta(p, oferta)
                            await conn.execute(
                                "INSERT INTO postados (id, tipo, ts) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET tipo=$2, ts=$3",
                                pid, tipo, int(time.time())
                            )

                    novo_menor = min(ref_menor, melhor_preco)
                    await conn.execute(
                        "UPDATE historico SET preco_original=$1, menor_preco=$2, ts=$3 WHERE id=$4",
                        ref_original, novo_menor, int(time.time()), pid
                    )

                else:
                    await conn.execute(
                        "INSERT INTO historico (id, preco_original, menor_preco, ts) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                        pid, preco_original, melhor_preco, int(time.time())
                    )
                    log.info(f"💾 Novo: {pid} | R$ {preco_original:.2f} → R$ {melhor_preco:.2f} | {oferta['desconto_pct']:.0f}% [{tipo}]")

                    # Posta imediatamente se desconto absurdo (>= 40%) na 1ª vez
                    if oferta["desconto_pct"] >= 40:
                        log.info(f"💥 Desconto absurdo ({oferta['desconto_pct']:.0f}%) na 1ª vez, postando!")
                        await self.enviar_oferta(p, oferta)
                        await conn.execute(
                            "INSERT INTO postados (id, tipo, ts) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET tipo=$2, ts=$3",
                            pid, tipo, int(time.time())
                        )

        except Exception as e:
            log.error(f"❌ Erro ao processar {pid}: {e}", exc_info=True)

    # =====================================================================
    # 12. BUSCA NA API
    # =====================================================================
    async def buscar_categoria(self, keyword):
        params = {
            "app_key":         self.ali_key,
            "method":          "aliexpress.affiliate.product.query",
            "timestamp":       str(int(time.time() * 1000)),
            "format":          "json",
            "v":               "2.0",
            "sign_method":     "md5",
            "keywords":        keyword,
            "page_size":       "50",
            "target_currency": "BRL",       # Solicita BRL
            "target_language": "PT",
            "tracking_id":     self.ali_tracking,
            "ship_to_country": "BR",
            "sort":            "SALE_PRICE_ASC",   # Ordena pelos mais baratos
            "fields":          "product_id,product_title,original_price,sale_price,target_sale_price,app_sale_price,target_app_sale_price,promotion_link,product_main_image_url,sale_price_discount_info"
        }
        params["sign"] = self.sign_ali(params)

        log.info(f"🔍 Buscando: '{keyword}'")
        try:
            async with self.session.get(
                self.ali_api, params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                raw  = await r.text()
                data = json.loads(raw)

                resp        = data.get("aliexpress_affiliate_product_query_response", {})
                resp_result = resp.get("resp_result", {})
                resp_code   = resp_result.get("resp_code")
                resp_msg    = resp_result.get("resp_msg")

                if resp_code != 200:
                    log.warning(f"⚠️ '{keyword}' → code={resp_code} | {resp_msg}")
                    return

                prods = resp_result.get("result", {}).get("products", {}).get("product", [])
                log.info(f"✅ {len(prods)} produtos para '{keyword}'")

                for p in prods:
                    await self.processar_produto(p)

        except asyncio.TimeoutError:
            log.error(f"⏱️ Timeout: '{keyword}'")
        except Exception as e:
            log.error(f"❌ Erro em '{keyword}': {e}", exc_info=True)

    # =====================================================================
    # 13. LOOP PRINCIPAL
    # =====================================================================
    async def run(self):
        await self.setup_db()
        self.session = aiohttp.ClientSession()
        log.info("🚀 SNIPER v35 INICIADO!")

        ciclo = 0
        while True:
            try:
                # Atualiza cotação a cada 10 ciclos (~7 minutos)
                if ciclo % 10 == 0:
                    await self.atualizar_cotacao()

                log.info(f"🔄 Ciclo #{ciclo + 1} | Cotação: R$ {self.usd_brl:.2f}")

                for keyword, _ in CATEGORIAS:
                    await self.buscar_categoria(keyword)
                    await asyncio.sleep(4)

                ciclo += 1
                log.info("⏳ Ciclo completo. Aguardando 45s...")
                await asyncio.sleep(45)

            except Exception as e:
                log.error(f"❌ Erro no ciclo: {e}", exc_info=True)
                await asyncio.sleep(20)

# =====================================================================
# 14. ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
