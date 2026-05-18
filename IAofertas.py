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
from typing import Dict, List, Tuple, Optional

import aiohttp
import aiosqlite
from dotenv import load_dotenv

# =====================================================================
# 1. CONFIGURAÇÃO DE LOGS PROFISSIONAL
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("Sniper_Omega")

class AliExpressSniperBot:
    # CONFIGURAÇÕES DE TUNING (Pode ajustar aqui)
    MARCAS_SNIPER = [
        "Poco", "Redmi", "Xiaomi", "Nintendo Switch", "Anker", "QCY", 
        "Edifier", "Lenovo", "Baseus", "Ugreen", "Essager", "SSD", 
        "Mouse Razer", "Machenike", "Sonoff", "Parafusadeira", 
        "Projetor Magcubic", "Realme", "Amazfit", "KZ", "Fifine",
        "Zeblaze", "Miyoo Mini", "DataFrog", "Ryzen", "Placa de Video"
    ]
    
    CONCURRENCY = 8           # Buscas paralelas
    DELAY_CICLOS = 25         # Pausa entre varreduras
    MIN_SCORE = 30            # Sensibilidade de postagem (0-100)
    MIN_VALOR_BRL = 20.0      # Ignora itens muito baratos
    DB_FILE = "sniper_omega_v21.db"

    def __init__(self):
        load_dotenv()
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("ID_DO_GRUPO")
        self.ali_key = os.getenv("ALI_KEY")
        self.ali_secret = os.getenv("ALI_SECRET")
        self.ali_tracking = os.getenv("ALI_TRACKING_ID")

        if not all([self.token, self.chat_id, self.ali_key, self.ali_secret, self.ali_tracking]):
            log.critical("❌ Erro: Verifique as chaves no arquivo .env!")
            sys.exit(1)

        self.tg_api = f"https://api.telegram.org/bot{self.token}"
        self.ali_api = "https://api-sg.aliexpress.com/sync"
        self.session: Optional[aiohttp.ClientSession] = None
        self.db: Optional[aiosqlite.Connection] = None
        self.sem = asyncio.Semaphore(self.CONCURRENCY)
        self._active = True

    # =====================================================================
    # 2. SISTEMA DE BANCO DE DADOS E LIMPEZA
    # =====================================================================
    async def setup_db(self):
        self.db = await aiosqlite.connect(self.DB_FILE)
        await self.db.execute("CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco REAL, ts INTEGER)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, ts INTEGER)")
        # Limpeza automática de dados velhos (> 30 dias)
        limite = int(time.time()) - (30 * 86400)
        await self.db.execute("DELETE FROM postados WHERE ts < ?", (limite,))
        await self.db.commit()

    async def get_stats(self) -> Tuple[int, int]:
        async with self.db.execute("SELECT COUNT(*) FROM historico") as c:
            h = (await c.fetchone())[0]
        async with self.db.execute("SELECT COUNT(*) FROM postados") as c:
            p = (await c.fetchone())[0]
        return h, p

    # =====================================================================
    # 3. INTELIGÊNCIA IA: TRATAMENTO DE DADOS E TEXTO
    # =====================================================================
    def sanitizar_float(self, valor) -> float:
        """ Remove %, moedas e converte string suja para float com segurança """
        if not valor: return 0.0
        try:
            # Remove tudo que não é número, ponto ou vírgula
            limpo = re.sub(r'[^\d.,]', '', str(valor)).replace(',', '.')
            return float(limpo)
        except: return 0.0

    def sanitizar_int(self, valor) -> int:
        """ Extrai apenas os números de uma string (ex: '1,000+' -> 1000) """
        if not valor: return 0
        try:
            limpo = re.sub(r'[^\d]', '', str(valor))
            return int(limpo)
        except: return 0

    def limpar_titulo_ia(self, t: str) -> str:
        """ IA: Remove ruídos de SEO e mantém o essencial """
        t = re.sub(r"(?i)\b(Global Version|Original|Versão Global|202[0-9]|Novo|Promo|Smartphone|Tablet|Frete Grátis)\b", "", t)
        t = re.sub(r"[^\w\s-]", "", t)
        return " ".join(t.split()[:6]).strip()

    def calcular_score_ia(self, queda: int, estrelas: float, vendas: int) -> float:
        """ IA Score: Algoritmo de relevância da oferta """
        # Normaliza estrelas se vierem em base 100
        if estrelas > 5: estrelas = (estrelas / 100) * 5
        return (queda * 1.8) + ((estrelas - 4) * 12) + (min(vendas / 50, 15))

    # =====================================================================
    # 4. COMUNICAÇÃO EXTERNA
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
                "page_no": str(random.randint(1, 5)),
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
            f"🤖 <i>AI Score: {int(d['score'])}/100</i>"
        )
        btns = {"inline_keyboard": [[{"text": "🎯 VER OFERTA NO ALIEXPRESS", "url": d['link']}]]}
        payload = {"chat_id": self.chat_id, "photo": d["img"], "caption": msg, "parse_mode": "HTML", "reply_markup": json.dumps(btns)}
        
        async with self.session.post(f"{self.tg_api}/sendPhoto", json=payload) as r:
            return r.status == 200

    # =====================================================================
    # 5. MOTOR DE PROCESSAMENTO
    # =====================================================================
    async def process_prod(self, p: dict):
        pid = str(p.get("product_id"))
        preco_atual = self.sanitizar_float(p.get("target_sale_price") or p.get("sale_price"))
        
        if preco_atual < self.MIN_VALOR_BRL: return

        async with self.db.execute("SELECT preco FROM historico WHERE id = ?", (pid,)) as c:
            row = await c.fetchone()
            
            if row:
                p_hist = row[0]
                if preco_atual < p_hist:
                    queda = int(((p_hist - preco_atual) / p_hist) * 100)
                    # Algoritmo de barreira: Itens caros precisam de menos queda para postar
                    threshold = 0.93 if preco_atual > 450 else 0.85
                    
                    if preco_atual <= (p_hist * threshold):
                        async with self.db.execute("SELECT 1 FROM postados WHERE id = ?", (pid,)) as cp:
                            if not await cp.fetchone():
                                estrelas = self.sanitizar_float(p.get("evaluate_rate"))
                                vendas = self.sanitizar_int(p.get("lastest_volume") or p.get("volume"))
                                score = self.calcular_score_ia(queda, estrelas, vendas)
                                
                                if score >= self.MIN_SCORE:
                                    # Corrige as estrelas para o dicionário do Telegram
                                    estrelas_final = (estrelas / 100 * 5) if estrelas > 5 else estrelas
                                    
                                    data = {
                                        "titulo": self.limpar_titulo_ia(p['product_title']),
                                        "preco_atual": preco_atual, "preco_hist": p_hist,
                                        "queda": queda, "link": p['promotion_link'],
                                        "img": p['product_main_image_url'], 
                                        "score": score, "estrelas": estrelas_final, "vendas": vendas
                                    }
                                    if await self.post_tg(data):
                                        await self.db.execute("INSERT INTO postados VALUES (?, ?)", (pid, int(time.time())))
                                        log.info(f"✅ POSTADO: {data['titulo']} | Queda: {queda}%")
                
                # Se o preço caiu (mesmo que pouco), atualizamos o histórico para a próxima varredura
                if preco_atual < p_hist:
                    await self.db.execute("UPDATE historico SET preco=?, ts=? WHERE id=?", (preco_atual, int(time.time()), pid))
            else:
                # Novo produto encontrado: Adiciona ao monitoramento
                await self.db.execute("INSERT INTO historico VALUES (?, ?, ?)", (pid, preco_atual, int(time.time())))

    async def run(self):
        await self.setup_db()
        # Conector otimizado
        connector = aiohttp.TCPConnector(limit=100, force_close=True)
        self.session = aiohttp.ClientSession(connector=connector)
        log.info("🚀 SNIPER IA OMEGA v21 INICIADO! (Proteção Anti-Crash Ativada)")

        while self._active:
            try:
                h, p = await self.get_stats()
                log.info("-" * 55)
                log.info(f"📊 RADAR: {h} itens catalogados | {p} ofertas postadas")
                log.info("-" * 55)

                tasks = [self.fetch_ali(m) for m in self.MARCAS_SNIPER]
                results = await asyncio.gather(*tasks)
                
                for prods in results:
                    if prods:
                        for item in prods:
                            await self.process_prod(item)
                
                await self.db.commit()
                log.info(f"✨ Ciclo finalizado. Próxima varredura em {self.DELAY_CICLOS}s...")
                await asyncio.sleep(self.DELAY_CICLOS)
            except Exception as e:
                log.error(f"⚠️ Erro inesperado no loop: {e}")
                await asyncio.sleep(10)

if __name__ == "__main__":
    # Ajuste para Python 3.14 no Windows
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.warning("🛑 Bot encerrado pelo usuário.")