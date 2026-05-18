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
# 1. SERVIDOR WEB (health check para Render/Railway)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sniper v36: System Online")
    def log_message(self, format, *args): return

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# =====================================================================
# 2. LOGS
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s"
)
log = logging.getLogger("Sniper_V36")

# =====================================================================
# 3. CATEGORIAS E KEYWORDS
#    Formato: ("keyword de busca", subcategoria_para_utm)
# =====================================================================
CATEGORIAS = [
    # Smartphones
    ("Xiaomi smartphone",         "smartphones"),
    ("Poco phone",                "smartphones"),
    ("Redmi phone",               "smartphones"),
    # Áudio
    ("Anker earbuds",             "audio"),
    ("TWS earphones bluetooth",   "audio"),
    ("headphone gaming",          "audio"),
    # Carregadores e cabos
    ("Baseus charger USB-C",      "carregadores"),
    ("Ugreen cable fast charge",  "carregadores"),
    ("GaN charger 65W",           "carregadores"),
    # Armazenamento
    ("SSD M2 NVMe",               "armazenamento"),
    ("USB flash drive",           "armazenamento"),
    # Games
    ("Nintendo Switch acessorio", "games"),
    ("controle gamepad PC",       "games"),
    # Computadores
    ("Ryzen mini PC",             "computadores"),
    ("laptop cooling pad",        "computadores"),
    # Wearables
    ("smartwatch AMOLED",         "wearables"),
    ("smart band fitness",        "wearables"),
]

# Palavras-chave que indicam produto fora do nicho — descarte automático
PALAVRAS_BANIDAS = [
    "wig", "hair", "dress", "clothes", "shoe", "underwear", "bra",
    "nail", "makeup", "lipstick", "perfume", "garden", "plant",
    "kitchen", "towel", "bedding", "pillow", "curtain", "toy",
    "doll", "sticker", "poster", "keychain", "ring", "necklace",
    "bracelet", "earring", "wallet", "bag", "backpack", "pet",
    "dog", "cat", "fishing", "camping tent", "bicycle",
]

# Mínimo de avaliações e nota para postar
MIN_REVIEWS   = 50
MIN_RATING    = 4.3
MIN_PRICE_BRL = 30.0    # Descarta bugigangas
MAX_POSTS_POR_HORA = 10 # Rate limit do canal

# =====================================================================
# 4. TEMPLATES DE MENSAGEM — variações para evitar "banner blindness"
# =====================================================================
TEMPLATES = {
    "combo": [
        "🔥 <b>COMBO INSANO! CUPOM + MOEDAS ({pct:.0f}% OFF)</b>",
        "💥 <b>DUPLO DESCONTO! CUPOM & MOEDAS — {pct:.0f}% OFF</b>",
        "🎯 <b>MELHOR PREÇO DO ANO! CUPOM + MOEDAS ({pct:.0f}% OFF)</b>",
    ],
    "cupom": [
        "🎟️ <b>CUPOM EXCLUSIVO! ({pct:.0f}% OFF)</b>",
        "🏷️ <b>CUPOM ATIVO AGORA — {pct:.0f}% DE DESCONTO!</b>",
        "✂️ <b>CORTA O PREÇO COM CUPOM! {pct:.0f}% OFF</b>",
    ],
    "moedas": [
        "🪙 <b>DESCONTO COM MOEDAS ALIEXPRESS! ({pct:.0f}% OFF)</b>",
        "💰 <b>USE SUAS MOEDAS E ECONOMIZE {pct:.0f}%!</b>",
        "🎰 <b>MOEDAS = MAIS DESCONTO! {pct:.0f}% OFF</b>",
    ],
    "desconto": [
        "🚨 <b>PREÇO BAIXOU {pct:.0f}%!</b>",
        "📉 <b>QUEDA DE {pct:.0f}% DETECTADA!</b>",
        "⚡ <b>OFERTA RELÂMPAGO — {pct:.0f}% OFF!</b>",
    ],
}

# =====================================================================
# 5. UTILITÁRIO — formata valor em BRL (padrão brasileiro)
# =====================================================================
def fmt_brl(valor: float) -> str:
    """Converte 1234.56 → 'R$ 1.234,56'"""
    inteiro, centavos = f"{valor:.2f}".split(".")
    inteiro_fmt = ""
    for i, digito in enumerate(reversed(inteiro)):
        if i > 0 and i % 3 == 0:
            inteiro_fmt = "." + inteiro_fmt
        inteiro_fmt = digito + inteiro_fmt
    return f"R$ {inteiro_fmt},{centavos}"

# =====================================================================
# 6. UTILITÁRIO — corta título de forma inteligente
# =====================================================================
def cortar_titulo(titulo: str, max_chars: int = 65) -> str:
    """Corta no último espaço antes de max_chars e adiciona '…'"""
    if len(titulo) <= max_chars:
        return titulo
    cortado = titulo[:max_chars].rsplit(" ", 1)[0]
    return cortado + "…"

# =====================================================================
# 7. UTILITÁRIO — adiciona UTM ao link de afiliado
# =====================================================================
def link_com_utm(url: str, tipo: str, subcategoria: str) -> str:
    params = urlencode({
        "utm_source":   "telegram",
        "utm_medium":   "bot",
        "utm_campaign": tipo,
        "utm_content":  subcategoria,
    })
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{params}"

# =====================================================================
# BOT PRINCIPAL
# =====================================================================
class AliExpressSniperBot:
    def __init__(self):
        load_dotenv()

        db_user     = os.getenv("DB_USER", "").strip()
        db_password = os.getenv("DB_PASSWORD", "").strip()
        db_host     = os.getenv("DB_HOST", "").strip()
        db_port     = os.getenv("DB_PORT", "6543").strip()
        db_name     = os.getenv("DB_NAME", "postgres").strip()
        self.db_url = (
            f"postgresql://{db_user}:{quote_plus(db_password)}"
            f"@{db_host}:{db_port}/{db_name}"
        )

        self.token        = os.getenv("TELEGRAM_TOKEN")
        self.chat_id      = os.getenv("ID_DO_GRUPO")
        self.ali_key      = os.getenv("ALI_KEY")
        self.ali_secret   = os.getenv("ALI_SECRET")
        self.ali_tracking = os.getenv("ALI_TRACKING_ID")

        self.tg_api  = f"https://api.telegram.org/bot{self.token}"
        self.ali_api = "https://api-sg.aliexpress.com/sync"
        self.pool    = None
        self.session = None

        # Fila de posts + rate limiter
        self._post_queue: asyncio.Queue = None
        self._posts_na_hora: list[float] = []   # timestamps dos posts recentes

    # =====================================================================
    # 8. BANCO DE DADOS
    # =====================================================================
    async def setup_db(self):
        log.info("🐘 Conectando ao Banco de Dados...")
        try:
            self.pool = await asyncpg.create_pool(
                self.db_url, ssl="require",
                min_size=1, max_size=5, command_timeout=60
            )
            async with self.pool.acquire() as conn:
                # Tabelas principais
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS historico (
                        id             TEXT PRIMARY KEY,
                        preco_original FLOAT,
                        menor_preco    FLOAT,
                        leituras       INT DEFAULT 0,
                        ts             BIGINT
                    );
                    CREATE TABLE IF NOT EXISTS postados (
                        id            TEXT PRIMARY KEY,
                        tipo          TEXT,
                        subcategoria  TEXT,
                        preco         FLOAT,
                        desconto_pct  FLOAT,
                        message_id    BIGINT,
                        ts            BIGINT
                    );
                    CREATE TABLE IF NOT EXISTS stats (
                        id            SERIAL PRIMARY KEY,
                        keyword       TEXT,
                        tipo          TEXT,
                        subcategoria  TEXT,
                        preco         FLOAT,
                        desconto_pct  FLOAT,
                        ts            BIGINT
                    );
                ''')

                # Migrações seguras (adiciona colunas novas sem quebrar)
                migracoes = [
                    ("historico", "leituras",      "INT DEFAULT 0"),
                    ("postados",  "subcategoria",  "TEXT"),
                    ("postados",  "preco",         "FLOAT"),
                    ("postados",  "desconto_pct",  "FLOAT"),
                    ("postados",  "message_id",    "BIGINT"),
                ]
                for tabela, coluna, tipo_col in migracoes:
                    existe = await conn.fetchval(f"""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name='{tabela}' AND column_name='{coluna}'
                    """)
                    if not existe:
                        await conn.execute(
                            f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo_col}"
                        )
                        log.info(f"🔧 Migração: coluna '{coluna}' adicionada em '{tabela}'")

            log.info("✅ BANCO CONECTADO!")
        except Exception as e:
            log.error(f"❌ ERRO DE CONEXÃO: {e}")
            sys.exit(1)

    # =====================================================================
    # 9. ASSINATURA ALI
    # =====================================================================
    def sign_ali(self, p: dict) -> str:
        data = "".join(f"{k}{v}" for k, v in sorted(p.items()) if v is not None)
        return hashlib.md5(
            (self.ali_secret + data + self.ali_secret).encode("utf-8")
        ).hexdigest().upper()

    # =====================================================================
    # 10. FILTRA PRODUTO FORA DO NICHO
    # =====================================================================
    def is_relevante(self, titulo: str) -> bool:
        titulo_lower = titulo.lower()
        for palavra in PALAVRAS_BANIDAS:
            if palavra in titulo_lower:
                return False
        return True

    # =====================================================================
    # 11. EXTRAÇÃO E ANÁLISE DA OFERTA
    #
    #  FIX CRÍTICO: A API retorna BRL quando target_currency=BRL.
    #  Não há conversão de moeda aqui. Os valores já chegam em reais.
    #  A heurística "se < 30 → multiplica por câmbio" foi REMOVIDA
    #  porque corrompia preços legítimos baixos.
    # =====================================================================
    def analisar_oferta(self, p: dict) -> dict | None:
        def to_float(v) -> float:
            try:
                return float(str(v or 0).replace(",", "."))
            except Exception:
                return 0.0

        titulo = str(p.get("product_title", ""))
        if not self.is_relevante(titulo):
            return None

        # Filtro de qualidade: nota e número de avaliações
        rating   = to_float(p.get("evaluate_rate", 0))
        reviews  = to_float(p.get("lastest_volume", 0))   # vendas recentes como proxy

        # Só filtra por rating se a API retornar (pode vir vazio para produtos novos)
        if rating > 0 and rating < MIN_RATING:
            log.debug(f"🔕 Nota baixa ({rating}): '{titulo[:40]}'")
            return None

        # Preços já em BRL (API com target_currency=BRL)
        preco_original = to_float(p.get("original_price"))
        preco_venda    = to_float(p.get("target_sale_price") or p.get("sale_price"))
        preco_app      = to_float(p.get("app_sale_price"))
        valor_cupom    = to_float(p.get("target_app_sale_price"))
        moedas         = to_float(
            p.get("sale_price_discount_info", {}).get("discount_price")
            if isinstance(p.get("sale_price_discount_info"), dict) else 0
        )

        precos_validos = [x for x in [preco_venda, preco_app] if x > 0]
        melhor_preco   = min(precos_validos) if precos_validos else 0.0

        # Preço mínimo para evitar bugigangas
        if melhor_preco < MIN_PRICE_BRL or preco_original < MIN_PRICE_BRL:
            return None

        # Preço original nunca pode ser menor que o de venda (dado corrompido da API)
        if preco_original <= melhor_preco:
            return None

        desconto_pct = (preco_original - melhor_preco) / preco_original * 100

        # Desconto absurdo (> 90%) é dado sujo — ignora
        if desconto_pct > 90:
            log.debug(f"🔕 Desconto irreal ({desconto_pct:.0f}%): '{titulo[:40]}'")
            return None

        tem_cupom  = 0 < valor_cupom < melhor_preco
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
            "reviews":        reviews,
            "rating":         rating,
        }

    # =====================================================================
    # 12. MONTAGEM DA MENSAGEM
    #  FIX: Formato BRL correto (1.234,56) e título inteligente
    # =====================================================================
    def montar_mensagem(self, p: dict, oferta: dict, subcategoria: str) -> str:
        titulo  = html.escape(cortar_titulo(str(p.get("product_title", ""))))
        url_raw = p.get("promotion_link", "")
        link    = link_com_utm(url_raw, oferta["tipo"], subcategoria)

        pct   = oferta["desconto_pct"]
        orig  = oferta["preco_original"]
        melhor = oferta["melhor_preco"]
        tipo  = oferta["tipo"]

        # Cabeçalho com variação aleatória (evita banner blindness)
        template = random.choice(TEMPLATES[tipo])
        cabecalho = template.format(pct=pct)

        msg = (
            f"{cabecalho}\n\n"
            f"📦 <b>{titulo}</b>\n\n"
            f"💰 De: <strike>{fmt_brl(orig)}</strike>\n"
            f"🔥 Por: <b>{fmt_brl(melhor)}</b>\n"
        )

        if tipo in ("cupom", "combo"):
            msg += "\n🎟️ <i>Aplique o cupom disponível na página do produto</i>"
        if tipo in ("moedas", "combo"):
            msg += "\n🪙 <i>Use suas moedas AliExpress para desconto adicional</i>"
        if oferta["tem_app"]:
            msg += f"\n📱 <i>Preço ainda menor no App: {fmt_brl(oferta['preco_app'])}</i>"

        # Estoque (proxy: vendas recentes)
        if oferta["reviews"] > 0:
            msg += f"\n📊 <i>{int(oferta['reviews'])} vendas recentes</i>"

        msg += f"\n\n🛒 <a href='{link}'>GARANTIR OFERTA NO ALIEXPRESS</a>"
        return msg

    # =====================================================================
    # 13. RATE LIMITER — máximo MAX_POSTS_POR_HORA posts por hora
    # =====================================================================
    def _pode_postar(self) -> bool:
        agora = time.time()
        # Remove timestamps com mais de 1 hora
        self._posts_na_hora = [t for t in self._posts_na_hora if agora - t < 3600]
        return len(self._posts_na_hora) < MAX_POSTS_POR_HORA

    def _registrar_post(self):
        self._posts_na_hora.append(time.time())

    # =====================================================================
    # 14. ENVIO TELEGRAM
    #  FIX: Fallback para sendMessage quando não há imagem
    # =====================================================================
    async def enviar_oferta(self, p: dict, oferta: dict, subcategoria: str) -> int | None:
        """Retorna o message_id do Telegram ou None em caso de erro."""
        msg       = self.montar_mensagem(p, oferta, subcategoria)
        image_url = p.get("product_main_image_url", "").strip()
        message_id = None

        try:
            if image_url:
                resp = await self.session.post(
                    f"{self.tg_api}/sendPhoto",
                    json={
                        "chat_id":    self.chat_id,
                        "photo":      image_url,
                        "caption":    msg,
                        "parse_mode": "HTML",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                )
            else:
                # Fallback: sem imagem, envia só o texto
                resp = await self.session.post(
                    f"{self.tg_api}/sendMessage",
                    json={
                        "chat_id":                  self.chat_id,
                        "text":                     msg,
                        "parse_mode":               "HTML",
                        "disable_web_page_preview": False,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                )

            data = await resp.json()
            if data.get("ok"):
                message_id = data["result"]["message_id"]
                log.info(
                    f"📨 POST [{oferta['tipo'].upper()}] {p['product_id']} | "
                    f"{oferta['desconto_pct']:.0f}% OFF | {fmt_brl(oferta['melhor_preco'])} "
                    f"| msg_id={message_id}"
                )
                self._registrar_post()
            else:
                log.error(f"❌ Telegram erro: {data}")

        except Exception as e:
            log.error(f"❌ Erro ao enviar Telegram: {e}", exc_info=True)

        return message_id

    # =====================================================================
    # 15. PROCESSAMENTO DO PRODUTO
    #
    #  FIX CRÍTICO: Produto novo nunca é postado na 1ª leitura,
    #  mesmo com desconto alto — porque o preço "original" da API
    #  pode estar inflado (dark pattern). Só posta com histórico real.
    #
    #  Lógica:
    #   - 1ª leitura: salva no banco, aguarda confirmação
    #   - 2ª+ leitura: compara com histórico real, posta se desconto ≥ 15%
    #     ou se o preço bateu novo mínimo histórico (queda ≥ 5%)
    # =====================================================================
    async def processar_produto(self, p: dict, subcategoria: str):
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
                    "SELECT preco_original, menor_preco, leituras FROM historico WHERE id = $1",
                    pid
                )

                if row:
                    leituras     = (row["leituras"] or 0) + 1
                    ref_original = max(row["preco_original"] or 0, preco_original)
                    ref_menor    = row["menor_preco"] or melhor_preco
                    novo_menor   = min(ref_menor, melhor_preco)

                    desconto_real = (
                        (ref_original - melhor_preco) / ref_original * 100
                        if ref_original > 0 else 0
                    )

                    # Critério de postagem (baseado em histórico real)
                    deve_postar = (
                        desconto_real >= 15
                        or melhor_preco <= ref_menor * 0.95  # 5% abaixo do mínimo histórico
                        or (tipo in ("cupom", "moedas", "combo") and desconto_real >= 10)
                    )

                    if deve_postar and self._pode_postar():
                        row_post = await conn.fetchrow(
                            "SELECT tipo, ts FROM postados WHERE id = $1", pid
                        )
                        ja_postado_hoje = (
                            row_post
                            and row_post["tipo"] == tipo
                            and (int(time.time()) - row_post["ts"]) < 86400
                        )

                        if not ja_postado_hoje:
                            oferta["desconto_pct"] = desconto_real
                            msg_id = await self.enviar_oferta(p, oferta, subcategoria)

                            await conn.execute("""
                                INSERT INTO postados (id, tipo, subcategoria, preco, desconto_pct, message_id, ts)
                                VALUES ($1, $2, $3, $4, $5, $6, $7)
                                ON CONFLICT (id) DO UPDATE
                                SET tipo=$2, subcategoria=$3, preco=$4,
                                    desconto_pct=$5, message_id=$6, ts=$7
                            """, pid, tipo, subcategoria, melhor_preco,
                                desconto_real, msg_id, int(time.time()))

                            # Registra para analytics
                            await conn.execute("""
                                INSERT INTO stats (keyword, tipo, subcategoria, preco, desconto_pct, ts)
                                VALUES ($1, $2, $3, $4, $5, $6)
                            """, subcategoria, tipo, subcategoria, melhor_preco,
                                desconto_real, int(time.time()))

                    # Atualiza histórico
                    await conn.execute("""
                        UPDATE historico
                        SET preco_original=$1, menor_preco=$2, leituras=$3, ts=$4
                        WHERE id=$5
                    """, ref_original, novo_menor, leituras, int(time.time()), pid)

                else:
                    # ── 1ª LEITURA: só salva, não posta ──────────────────
                    # O preço "original" da API pode estar inflado artificialmente.
                    # Aguardamos pelo menos uma leitura subsequente para confirmar
                    # que o desconto é real antes de postar qualquer coisa.
                    await conn.execute("""
                        INSERT INTO historico (id, preco_original, menor_preco, leituras, ts)
                        VALUES ($1, $2, $3, 1, $4)
                        ON CONFLICT DO NOTHING
                    """, pid, preco_original, melhor_preco, int(time.time()))
                    log.info(
                        f"💾 Novo produto: {pid} | "
                        f"{fmt_brl(preco_original)} → {fmt_brl(melhor_preco)} | "
                        f"{oferta['desconto_pct']:.0f}% [{tipo}] — aguardando 2ª leitura"
                    )

        except Exception as e:
            log.error(f"❌ Erro ao processar {pid}: {e}", exc_info=True)

    # =====================================================================
    # 16. BUSCA NA API ALIEXPRESS
    # =====================================================================
    async def buscar_categoria(self, keyword: str, subcategoria: str):
        params = {
            "app_key":         self.ali_key,
            "method":          "aliexpress.affiliate.product.query",
            "timestamp":       str(int(time.time() * 1000)),
            "format":          "json",
            "v":               "2.0",
            "sign_method":     "md5",
            "keywords":        keyword,
            "page_size":       "50",
            "target_currency": "BRL",   # Preços retornados já em reais
            "target_language": "PT",
            "tracking_id":     self.ali_tracking,
            "ship_to_country": "BR",
            "sort":            "SALE_PRICE_ASC",
            # Adicionado: campos de qualidade do produto
            "fields": (
                "product_id,product_title,original_price,sale_price,"
                "target_sale_price,app_sale_price,target_app_sale_price,"
                "promotion_link,product_main_image_url,"
                "sale_price_discount_info,evaluate_rate,lastest_volume"
            ),
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
                    await self.processar_produto(p, subcategoria)

        except asyncio.TimeoutError:
            log.error(f"⏱️ Timeout: '{keyword}'")
        except Exception as e:
            log.error(f"❌ Erro em '{keyword}': {e}", exc_info=True)

    # =====================================================================
    # 17. RELATÓRIO SEMANAL DE PERFORMANCE (postado no próprio canal)
    # =====================================================================
    async def relatorio_semanal(self):
        try:
            async with self.pool.acquire() as conn:
                semana_atras = int(time.time()) - 7 * 86400
                rows = await conn.fetch("""
                    SELECT subcategoria, tipo, COUNT(*) as total,
                           ROUND(AVG(desconto_pct)::numeric, 1) as avg_pct,
                           ROUND(MIN(preco)::numeric, 2)  as menor_preco
                    FROM stats
                    WHERE ts >= $1
                    GROUP BY subcategoria, tipo
                    ORDER BY total DESC
                    LIMIT 10
                """, semana_atras)

            if not rows:
                return

            linhas = "\n".join(
                f"• {r['subcategoria']} [{r['tipo']}] → "
                f"{r['total']} posts | avg {r['avg_pct']}% off | "
                f"menor {fmt_brl(float(r['menor_preco']))}"
                for r in rows
            )
            msg = (
                f"📊 <b>RELATÓRIO SEMANAL — Sniper v36</b>\n\n"
                f"{linhas}\n\n"
                f"<i>Período: últimos 7 dias</i>"
            )
            await self.session.post(
                f"{self.tg_api}/sendMessage",
                json={
                    "chat_id":    self.chat_id,
                    "text":       msg,
                    "parse_mode": "HTML",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )
            log.info("📊 Relatório semanal enviado.")
        except Exception as e:
            log.error(f"❌ Erro no relatório: {e}", exc_info=True)

    # =====================================================================
    # 18. LOOP PRINCIPAL
    # =====================================================================
    async def run(self):
        await self.setup_db()
        self._post_queue = asyncio.Queue()

        # FIX: session gerenciada com context manager para garantir fechamento
        async with aiohttp.ClientSession() as session:
            self.session = session
            log.info("🚀 SNIPER v36 INICIADO!")

            ciclo          = 0
            ultimo_relatorio = 0

            while True:
                try:
                    log.info(f"🔄 Ciclo #{ciclo + 1}")

                    for keyword, subcategoria in CATEGORIAS:
                        await self.buscar_categoria(keyword, subcategoria)
                        await asyncio.sleep(4)  # respeita rate limit da API

                    ciclo += 1

                    # Relatório semanal automático
                    agora = int(time.time())
                    if agora - ultimo_relatorio >= 7 * 86400:
                        await self.relatorio_semanal()
                        ultimo_relatorio = agora

                    posts_hora = len(self._posts_na_hora)
                    log.info(
                        f"⏳ Ciclo completo. Posts na hora: {posts_hora}/{MAX_POSTS_POR_HORA}. "
                        f"Aguardando 45s..."
                    )
                    await asyncio.sleep(45)

                except Exception as e:
                    log.error(f"❌ Erro no ciclo: {e}", exc_info=True)
                    await asyncio.sleep(20)

# =====================================================================
# 19. ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        sys.exit(0)
