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

# Configuração de Log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("SniperBot_v18")

class AliExpressSniperBot:
    # =================================================================
    # ⚙️ CONFIGURAÇÕES AVANÇADAS
    # =================================================================
    MARCAS_BASE = [
        "Poco", "Redmi", "Xiaomi", "Nintendo Switch", "Anker", "QCY", 
        "Edifier", "Lenovo", "Baseus", "Ugreen", "Essager", "SSD", 
        "Mouse Razer", "Machenike", "Sonoff", "Parafusadeira", 
        "Projetor Magcubic", "Realme", "Amazfit", "KZ", "Fifine"
    ]
    
    # Parâmetros de Performance
    MAX_CONCURRENCY = 3          # Quantas marcas pesquisar simultaneamente
    MIN_SCORE_POST = 50          # Nota mínima (0-100) para valer a postagem
    
    # Filtros de Qualidade
    MIN_ESTRELAS = 4.6
    MIN_VENDAS = 150
    COOLDOWN_POSTAGEM = 86400 * 2 # 48 horas para não repetir o mesmo item
    
    REGEX_LIXO = re.compile(r"(?i)\b(Global Version|Versão Global|Original|202[0-9]|New|Novo|Promoção|Smartphone|Tablet|Mobile Phone)\b")

    def __init__(self):
        load_dotenv()
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.id_grupo = os.getenv("ID_DO_GRUPO")
        self.ali_key = os.getenv("ALI_KEY")
        self.ali_secret = os.getenv("ALI_SECRET")
        self.ali_tracking_id = os.getenv("ALI_TRACKING_ID")

        self.ali_api_url = "https://api-sg.aliexpress.com/sync"
        self.telegram_api = f"https://api.telegram.org/bot{self.telegram_token}"
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.db: Optional[aiosqlite.Connection] = None
        self.semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)
        self._rodando = True

    # =================================================================
    # 🧠 INTELIGÊNCIA DE CURADORIA
    # =================================================================
    def calcular_score(self, preco: float, desconto: float, estrelas: float, vendas: float) -> float:
        """ Atribui uma nota de 0 a 100 para a qualidade da oferta """
        # Peso 1: Desconto (Quanto mais, melhor)
        score_desconto = min(desconto * 2, 50) 
        # Peso 2: Social Proof (Vendas e Estrelas)
        score_social = ((estrelas - 4.0) * 10) + (min(vendas / 100, 20))
        return score_desconto + score_social

    def processar_titulo_ia(self, titulo: str) -> str:
        titulo_limpo = self.REGEX_LIXO.sub("", titulo)
        # Remove caracteres especiais e excesso de espaços
        titulo_limpo = re.sub(r"[^\w\s-]", "", titulo_limpo)
        titulo_limpo = re.sub(r"\s+", " ", titulo_limpo).strip()
        palavras = titulo_limpo.split()
        # IA Heurística: Mantém as primeiras 6 palavras (geralmente Marca + Modelo)
        return " ".join(palavras[:6])

    # =================================================================
    # 💽 BANCO DE DADOS OTIMIZADO
    # =================================================================
    async def init_db(self):
        self.db = await aiosqlite.connect("sniper_pro_v18.db")
        # Índices adicionados para performance em bancos grandes
        await self.db.execute("CREATE TABLE IF NOT EXISTS historico (id TEXT PRIMARY KEY, preco REAL, data INTEGER)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS postados (id TEXT PRIMARY KEY, data INTEGER)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_postados ON postados(data)")
        await self.db.commit()

    # =================================================================
    # 🛒 API ALIEXPRESS COM ASSINATURA MD5
    # =================================================================
    def assinar(self, params: dict) -> str:
        sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()) if v is not None)
        payload = self.ali_secret + sorted_params + self.ali_secret
        return hashlib.md5(payload.encode("utf-8")).hexdigest().upper()

    async def buscar_produtos(self, termo: str) -> List[dict]:
        async with self.semaphore:
            params = {
                "app_key": self.ali_key,
                "method": "aliexpress.affiliate.product.query",
                "timestamp": str(int(time.time() * 1000)),
                "format": "json", "v": "2.0", "sign_method": "md5",
                "keywords": termo, "page_size": "40",
                "target_currency": "BRL", "target_language": "PT",
                "tracking_id": self.ali_tracking_id, "ship_to_country": "BR"
            }
            params["sign"] = self.assinar(params)
            try:
                async with self.session.get(self.ali_api_url, params=params) as resp:
                    res = await resp.json()
                    return res.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
            except Exception as e:
                log.error(f"Erro na busca ({termo}): {e}")
                return []

    # =================================================================
    # 📲 TELEGRAM COM BOTÕES (INLINE KEYBOARD)
    # =================================================================
    async def postar_no_telegram(self, d: dict):
        queda_txt = f"{d['queda_perc']}% OFF"
        # Estilo visual melhorado
        status_emoji = "🔥" if d['score'] > 70 else "🚨"
        
        caption = (
            f"{status_emoji} <b>{queda_txt} | QUEDA HISTÓRICA</b>\n\n"
            f"📦 <b>{html.escape(d['titulo'])}</b>\n\n"
            f"💰 <b>Por: R$ {d['preco_atual']:,.2f}</b>\n"
            f"📉 Antigo: <strike>R$ {d['preco_hist']:,.2f}</strike>\n\n"
            f"⭐ {d['estrelas']} | 🛒 +{d['vendas']} vendidos\n"
            f"🤖 <i>AI Score: {int(d['score'])}/100</i>"
        )

        # Botão interativo
        reply_markup = {
            "inline_keyboard": [[
                {"text": "🎯 VER OFERTA NO ALIEXPRESS", "url": d['link']}
            ]]
        }

        payload = {
            "chat_id": self.id_grupo,
            "photo": d["imagem"],
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(reply_markup)
        }
        
        async with self.session.post(f"{self.telegram_api}/sendPhoto", json=payload) as resp:
            return resp.status == 200

    # =================================================================
    # ⚙️ LÓGICA PRINCIPAL
    # =================================================================
    async def avaliar_produto(self, p: dict):
        pid = str(p.get("product_id"))
        preco_atual = float(p.get("target_sale_price") or 0)
        
        if preco_atual < 10: return # Ignora lixo/cabos/capinhas

        async with self.db.execute("SELECT preco FROM historico WHERE id = ?", (pid,)) as c:
            row = await c.fetchone()
            
            if row:
                preco_hist = row[0]
                if preco_atual < preco_hist:
                    diff = preco_hist - preco_atual
                    queda_perc = int((diff / preco_hist) * 100)
                    
                    # Inteligência de Postagem: Só posta se o desconto for real conforme a faixa
                    limiar = 0.93 if preco_atual > 500 else 0.85 # 7% para caros, 15% para baratos
                    
                    if preco_atual <= (preco_hist * limiar):
                        # Verifica se já postou recentemente
                        async with self.db.execute("SELECT 1 FROM postados WHERE id = ?", (pid,)) as c2:
                            if not await c2.fetchone():
                                estrelas = float(p.get("evaluate_rate") or 0)
                                vendas = int(p.get("lastest_volume") or 0)
                                score = self.calcular_score(preco_atual, queda_perc, estrelas, vendas)
                                
                                if score >= self.MIN_SCORE_POST:
                                    info = {
                                        "titulo": self.processar_titulo_ia(p['product_title']),
                                        "preco_atual": preco_atual, "preco_hist": preco_hist,
                                        "queda_perc": queda_perc, "link": p['promotion_link'],
                                        "imagem": p['product_main_image_url'], "score": score,
                                        "estrelas": estrelas, "vendas": vendas
                                    }
                                    if await self.postar_no_telegram(info):
                                        await self.db.execute("INSERT INTO postados VALUES (?, ?)", (pid, int(time.time())))
                
                # Atualiza o menor preço histórico se necessário
                if preco_atual < preco_hist:
                    await self.db.execute("UPDATE historico SET preco = ?, data = ? WHERE id = ?", (preco_atual, int(time.time()), pid))
            else:
                # Primeiro registro do produto
                await self.db.execute("INSERT INTO historico VALUES (?, ?, ?)", (pid, preco_atual, int(time.time())))
        
        await self.db.commit()

    async def iniciar(self):
        await self.init_db()
        # TCPConnector otimizado para Windows e Linux
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        self.session = aiohttp.ClientSession(connector=connector)
        
        log.info("🔥 Sniper Pro v18 iniciado! Monitorando...")

        while self._rodando:
            marcas = self.MARCAS_BASE.copy()
            random.shuffle(marcas)
            
            # Divide marcas em lotes para processamento paralelo
            for i in range(0, len(marcas), self.MAX_CONCURRENCY):
                batch = marcas[i:i+self.MAX_CONCURRENCY]
                tarefas = [self.buscar_produtos(m) for m in batch]
                resultados = await asyncio.gather(*tarefas)
                
                for produtos in resultados:
                    if produtos:
                        for p in produtos:
                            await self.avaliar_produto(p)
                
                await asyncio.sleep(2) # Pausa amigável entre lotes

            log.info("💤 Varredura concluída. Aguardando próximo ciclo...")
            await asyncio.sleep(600) # 10 minutos entre varreduras completas

if __name__ == "__main__":
    bot = AliExpressSniperBot()
    try:
        asyncio.run(bot.iniciar())
    except KeyboardInterrupt:
        log.warning("Bot parado manualmente.")