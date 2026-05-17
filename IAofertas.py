"""
Bot de Ofertas AliExpress → Telegram
=====================================
Versão: 14.0 — MODO TURBO + DB IMPLACÁVEL
"""

import asyncio
import hashlib
import json
import logging
import random
import math
import time
import html
import os

import aiohttp
import aiosqlite
from dotenv import load_dotenv

# =====================================================================
# 1. CARREGAMENTO DE CREDENCIAIS
# =====================================================================
load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
ID_DO_GRUPO     = os.getenv("ID_DO_GRUPO")
ALI_KEY         = os.getenv("ALI_KEY")
ALI_SECRET      = os.getenv("ALI_SECRET")
ALI_TRACKING_ID = os.getenv("ALI_TRACKING_ID")

if not all([TELEGRAM_TOKEN, ID_DO_GRUPO, ALI_KEY, ALI_SECRET, ALI_TRACKING_ID]):
    raise ValueError("⚠️ Faltam credenciais no arquivo .env!")

ALI_API_URL     = "https://api-sg.aliexpress.com/sync"
TELEGRAM_API    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# =====================================================================
# 2. CONFIGURAÇÕES DE VELOCIDADE (MODO TURBO)
# =====================================================================
MARCAS_SNIPER = [
    "Poco", "Redmi", "Xiaomi", "Nintendo Switch", "Anker", "QCY", 
    "Edifier", "Lenovo", "Baseus", "Ugreen", "Essager", "SSD", 
    "Mouse Razer", "Machenike", "Sonoff", "Parafusadeira", 
    "Projetor Magcubic", "Realme", "Amazfit", "KZ", "Fifine",
    "Monitor", "Placa de Video", "Ryzen", "Teclado Mecanico"
]

ESTRATEGIAS_SORT = ["LAST_VOLUME_DESC", "SALE_PRICE_ASC", "DISCOUNT_DESC"]

# 🚀 MOTOR TURBO ATIVADO AQUI:
INTERVALO_BUSCA_SEG    = 1    # Apenas 1 segundo de pausa entre varreduras
BUSCAS_PARALELAS       = 12   # Pesquisa 12 coisas ao mesmo tempo!
PRODUTOS_POR_BUSCA     = 50   # 50 itens por página
COOLDOWN_POSTAGEM_H    = 24  
REQUEST_TIMEOUT        = 15

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO, datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

indice_marca = 0

# =====================================================================
# 3. SISTEMA AVANÇADO DE BANCO DE DADOS
# =====================================================================
DB_NOME = "bot_sniper.db"

async def init_db():
    async with aiosqlite.connect(DB_NOME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS historico (
                            id TEXT PRIMARY KEY,
                            titulo TEXT,
                            menor_preco REAL,
                            ultima_atualizacao REAL)''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS postados (
                            id TEXT PRIMARY KEY,
                            timestamp REAL)''')
        await db.commit()

async def limpar_db_velho():
    agora = time.time()
    limite = agora - (30 * 24 * 3600)
    async with aiosqlite.connect(DB_NOME) as db:
        await db.execute("DELETE FROM historico WHERE ultima_atualizacao < ?", (limite,))
        await db.execute("DELETE FROM postados WHERE timestamp < ?", (limite,))
        await db.commit()

async def contar_produtos_db() -> int:
    try:
        async with aiosqlite.connect(DB_NOME) as db:
            async with db.execute("SELECT COUNT(*) FROM historico") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    except:
        return 0

async def verificar_db_e_avaliar_preco(p_id: str, titulo: str, preco_atual: float) -> tuple[bool, float, bool, bool]:
    agora = time.time()
    is_queda_historica = False
    menor_preco = preco_atual
    ja_postado = False
    conhecido_no_db = False

    async with aiosqlite.connect(DB_NOME) as db:
        async with db.execute("SELECT timestamp FROM postados WHERE id=?", (p_id,)) as cursor:
            row_postado = await cursor.fetchone()
            if row_postado and (agora - row_postado[0]) < (COOLDOWN_POSTAGEM_H * 3600):
                ja_postado = True
                
        async with db.execute("SELECT menor_preco FROM historico WHERE id=?", (p_id,)) as cursor:
            row_hist = await cursor.fetchone()
            
            if row_hist:
                conhecido_no_db = True
                menor_preco = row_hist[0]
                
                # REGRA IMPLACÁVEL: O preço PRECISA ser 20% MENOR que o menor preço histórico!
                if preco_atual <= (menor_preco * 0.80):
                    is_queda_historica = True
                    await db.execute("UPDATE historico SET menor_preco=?, ultima_atualizacao=? WHERE id=?", 
                                     (preco_atual, agora, p_id))
                                     
                elif preco_atual < menor_preco:
                    await db.execute("UPDATE historico SET menor_preco=?, ultima_atualizacao=? WHERE id=?", 
                                     (preco_atual, agora, p_id))
            else:
                # O BOT NUNCA VIU ESSE PRODUTO: Salva para vigiar no futuro!
                await db.execute("INSERT INTO historico (id, titulo, menor_preco, ultima_atualizacao) VALUES (?, ?, ?, ?)", 
                                 (p_id, titulo, preco_atual, agora))
        await db.commit()
        
    return is_queda_historica, menor_preco, ja_postado, conhecido_no_db

async def registrar_postagem(p_id: str):
    async with aiosqlite.connect(DB_NOME) as db:
        await db.execute("REPLACE INTO postados (id, timestamp) VALUES (?, ?)", (p_id, time.time()))
        await db.commit()

# =====================================================================
# 4. API ALIEXPRESS
# =====================================================================
def gerar_assinatura(params: dict, secret: str) -> str:
    pares = sorted(params.items())
    payload = secret + "".join(f"{k}{v}" for k, v in pares if v is not None) + secret
    return hashlib.md5(payload.encode("utf-8")).hexdigest().upper()

async def buscar_produtos(session: aiohttp.ClientSession, termo: str, sort: str) -> list[dict]:
    pagina_aleatoria = str(random.randint(1, 5)) # Busca até a página 5 agora para mais variedade
    params = {
        "app_key":         ALI_KEY,
        "method":          "aliexpress.affiliate.product.query",
        "timestamp":       str(int(time.time() * 1000)),
        "format":          "json",
        "v":               "2.0",
        "sign_method":     "md5",
        "keywords":        termo,
        "page_size":       str(PRODUTOS_POR_BUSCA),
        "page_no":         pagina_aleatoria,
        "sort":            sort,
        "target_currency": "BRL",
        "target_language": "PT",
        "tracking_id":     ALI_TRACKING_ID,
        "ship_to_country": "BR",
        "delivery_days":   "15",
    }
    params["sign"] = gerar_assinatura(params, ALI_SECRET)

    try:
        async with session.get(ALI_API_URL, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
    except Exception:
        pass
    return []

# =====================================================================
# 5. NORMALIZAÇÃO E FILTRO RÍGIDO
# =====================================================================
def normalizar(p: dict) -> dict:
    try: preco_atual = float(str(p.get("target_sale_price") or p.get("sale_price") or "0").replace(",", "."))
    except: preco_atual = 0.0

    try: preco_orig = float(str(p.get("target_original_price") or p.get("original_price") or "0").replace(",", "."))
    except: preco_orig = preco_atual

    estrelas = float(str(p.get("evaluate_rate") or p.get("avg_evaluation_rating") or "0").replace("%", "").strip())
    if estrelas > 5: estrelas = estrelas / 100 * 5

    return {
        "id":           str(p.get("product_id", "")),
        "titulo":       p.get("product_title", "Produto sem título"),
        "preco_atual":  preco_atual,
        "preco_orig":   preco_orig,
        "estrelas":     estrelas,
        "volume":       int(p.get("lastest_volume") or p.get("volume") or 0),
        "imagem":       p.get("product_main_image_url", ""),
        "link":         p.get("promotion_link", ""),
    }

async def processar_lote(produtos_brutos: list[dict]) -> list[dict]:
    candidatos = []
    for p in produtos_brutos:
        d = normalizar(p)
        
        # Filtro de qualidade extrema: Ignora porcarias
        if d["estrelas"] < 4.7 or d["volume"] < 300: 
            continue
            
        # Ignora produtos muito baratos (geralmente peças falsas ou capinhas)
        if d["preco_atual"] < 25.0: 
            continue
            
        is_queda, menor_preco, ja_postado, conhecido = await verificar_db_e_avaliar_preco(d["id"], d["titulo"], d["preco_atual"])
        
        if ja_postado:
            continue
            
        # SE NÃO FOR UMA QUEDA HISTÓRICA DO BANCO DE DADOS, ELE DESCARTA NA HORA!
        if not is_queda:
            continue
            
        d["preco_historico"] = menor_preco
        d["score"] = d["volume"] * d["estrelas"] 
        candidatos.append(d)
        
    return candidatos

# =====================================================================
# 6. ENVIOS TELEGRAM COM ANTI-BAN
# =====================================================================
def formatar_mensagem(d: dict) -> str:
    titulo_seguro = html.escape(d['titulo'][:80])

    emblema = "🚨 <b>QUEDA HISTÓRICA REGISTRADA!</b>"
    aviso_queda = f"🔥 O robô detectou que o preço desabou!\n📉 <b>Preço antigo: R$ {d['preco_historico']:.2f}</b>\n"
    volume_str = f"{int(d['volume']):,}".replace(",", ".")
    
    return (
        f"{emblema}\n\n"
        f"📦 <b>{titulo_seguro}...</b>\n\n"
        f"{aviso_queda}"
        f"💰 <b>Novo Preço: R$ {d['preco_atual']:.2f}</b>\n\n"
        f"⭐ Avaliação: {d['estrelas']:.1f}⭐\n"
        f"📦 Vendas: {volume_str} confirmadas\n\n"
        f"🛒 <b><a href='{d['link']}'>[ 🎯 CLIQUE AQUI PARA COMPRAR ]</a></b>\n\n"
        f"⚠️ <i>Estoque e preço podem sofrer alteração.</i>"
    )

async def disparar_telegram_api(session: aiohttp.ClientSession, endpoint: str, payload: dict) -> dict:
    url = f"{TELEGRAM_API}/{endpoint}"
    for _ in range(3):
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return data
                elif data.get("error_code") == 429:
                    wait_time = data.get("parameters", {}).get("retry_after", 30)
                    log.warning(f"⚠️ Anti-Spam ativado. Esperando {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    break
        except Exception:
            await asyncio.sleep(2)
    return {}

async def enviar_telegram(session: aiohttp.ClientSession, d: dict) -> bool:
    msg = formatar_mensagem(d)
    
    if d["imagem"]:
        payload = {"chat_id": ID_DO_GRUPO, "photo": d["imagem"], "caption": msg, "parse_mode": "HTML"}
        resp = await disparar_telegram_api(session, "sendPhoto", payload)
        if resp.get("ok"):
            await registrar_postagem(d["id"])
            return True

    payload = {"chat_id": ID_DO_GRUPO, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": False}
    resp = await disparar_telegram_api(session, "sendMessage", payload)
    if resp.get("ok"):
        await registrar_postagem(d["id"])
        return True
        
    return False

# =====================================================================
# 7. MOTOR DE BUSCA EM LOTES
# =====================================================================
async def executar_busca(session: aiohttp.ClientSession):
    global indice_marca
    sort = random.choice(ESTRATEGIAS_SORT)
    lote = []
    
    for _ in range(BUSCAS_PARALELAS):
        lote.append(MARCAS_SNIPER[indice_marca])
        indice_marca = (indice_marca + 1) % len(MARCAS_SNIPER)
        
    tarefas = [buscar_produtos(session, marca, sort) for marca in lote]
    resultados = await asyncio.gather(*tarefas, return_exceptions=True)
    
    candidatos_gerais = []
    for brutos in resultados:
        if isinstance(brutos, list) and brutos:
            processados = await processar_lote(brutos)
            candidatos_gerais.extend(processados)
            
    if not candidatos_gerais:
        return False

    candidatos_gerais.sort(key=lambda x: x["score"], reverse=True)
    
    for melhor in candidatos_gerais:
        enviado = await enviar_telegram(session, melhor)
        if enviado:
            log.info(f"✅ OFERTA MATADORA ENVIADA: {melhor['titulo'][:30]}... Caiu para R${melhor['preco_atual']}")
            await asyncio.sleep(1.5) 

    return True

# =====================================================================
# 8. LOOP PRINCIPAL INFINITO
# =====================================================================
async def main():
    log.info(f"🚀 MODO TURBO ATIVADO! (Velocidade Máxima permitida pela API)")
    await init_db()
    await limpar_db_velho()
    
    # Adicionado um limite de conexões no ClientSession para lidar com as 12 buscas ao mesmo tempo sem sobrecarregar seu PC
    connector = aiohttp.TCPConnector(limit=50)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        ciclos = 0
        while True:
            try:
                await executar_busca(session)
                
                # A CADA 5 CICLOS (Como é super rápido agora, isso leva menos de 10 segundos), MOSTRA O DB:
                ciclos += 1
                if ciclos % 5 == 0:
                    qtd = await contar_produtos_db()
                    log.info(f"📊 STATUS: Banco de Dados aprendendo muito rápido... Já tem {qtd} produtos vigiados.")
                
                await asyncio.sleep(INTERVALO_BUSCA_SEG)
                
            except Exception as e:
                log.error(f"❌ Erro fatal: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("🛑 Bot encerrado pelo usuário.")