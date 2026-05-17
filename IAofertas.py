import asyncio
import hashlib
import json
import logging
import random
import time
import html
import os
import re
import signal
import sys
from typing import Dict, List, Tuple, Optional

import aiohttp
import aiosqlite
from dotenv import load_dotenv

# Configuração de Log Profissional
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("SniperBot")

class AliExpressSniperBot:
    # =================================================================
    # ⚙️ PAINEL DE CONFIGURAÇÕES (Edite aqui facilmente)
    # =================================================================
    MARCAS_BASE = [
        "Poco", "Redmi", "Xiaomi", "Nintendo Switch", "Anker", "QCY", 
        "Edifier", "Lenovo", "Baseus", "Ugreen", "Essager", "SSD", 
        "Mouse Razer", "Machenike", "Sonoff", "Parafusadeira", 
        "Projetor Magcubic", "Realme", "Amazfit", "KZ", "Fifine",
        "Monitor", "Placa de Video", "Ryzen", "Teclado Mecanico"
    ]
    ESTRATEGIAS_SORT = ["LAST_VOLUME_DESC", "SALE_PRICE_ASC", "DISCOUNT_DESC"]
    
    # Filtros de Qualidade
    MIN_ESTRELAS = 4.7
    MIN_VENDAS = 250
    MIN_PRECO_BRL = 25.0
    
    # Tempos e Limites
    COOLDOWN_POSTAGEM_SEG = 86400  # 24 horas sem repetir o mesmo produto
    DELAY_ENTRE_MARCAS_SEG = 4.0   # 4 segundos é o "sweet spot" para evitar ban da API
    DELAY_ENTRE_CICLOS_MIN = 5     # 5 minutos de descanso entre varreduras completas
    
    DB_NOME = "bot_ia_sniper_v17.db"
    REGEX_LIXO = re.compile(r"(?i)\b(Global Version|Versão Global|Original|202[0-9]|New|Novo|Promoção)\b")

    def __init__(self):
        load_dotenv()
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.id_grupo = os.getenv("ID_DO_GRUPO")
        self.ali_key = os.getenv("ALI_KEY")
        self.ali_secret = os.getenv("ALI_SECRET")
        self.ali_tracking_id = os.getenv("ALI_TRACKING_ID")

        if not all([self.telegram_token, self.id_grupo, self.ali_key, self.ali_secret, self.ali_tracking_id]):
            log.critical("Faltam credenciais no arquivo .env! Interrompendo.")
            sys.exit(1)

        self.ali_api_url = "https://api-sg.aliexpress.com/sync"
        self.telegram_api = f"https://api.telegram.org/bot{self.telegram_token}"
        
        # Estado do Bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.db: Optional[aiosqlite.Connection] = None
        self.cache_postados: Dict[str, float] = {} # RAM Cache para velocidade extrema
        self._rodando = True

    # =================================================================
    # 🧠 INTELIGÊNCIA ARTIFICIAL E UTILS
    # =================================================================
    @staticmethod
    def formatar_moeda(valor: float) -> str:
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def calcular_threshold_desconto(preco: float) -> float:
        if preco < 50: return 0.75    # Itens baratos: Exige 25% de queda
        if preco < 500: return 0.85   # Itens médios: Exige 15% de queda
        return 0.93                   # Itens caros: Exige 7% de queda

    def processar_titulo_ia(self, titulo: str) -> str:
        titulo_limpo = self.REGEX_LIXO.sub("", titulo)
        titulo_limpo = re.sub(r"\s+", " ", titulo_limpo).strip()
        palavras = titulo_limpo.split()
        return " ".join(palavras[:6])

    # =================================================================
    # 💽 BANCO DE DADOS & CACHE
    # =================================================================
    async def init_db_and_cache(self):
        self.db = await aiosqlite.connect(self.DB_NOME)
        await self.db.execute('''CREATE TABLE IF NOT EXISTS historico (
                            id TEXT PRIMARY KEY, titulo TEXT, 
                            menor_preco REAL, ultima_atualizacao INTEGER)''')
        await self.db.execute('''CREATE TABLE IF NOT EXISTS postados (
                            id TEXT PRIMARY KEY, timestamp INTEGER)''')
        
        # Limpeza de dados velhos (30 dias)
        limite_velho = int(time.time()) - (30 * 24 * 3600)
        await self.db.execute("DELETE FROM historico WHERE ultima_atualizacao < ?", (limite_velho,))
        await self.db.execute("DELETE FROM postados WHERE timestamp < ?", (limite_velho,))
        await self.db.commit()

        # Carrega os postados das últimas 24h para a RAM (Cache)
        agora = time.time()
        limite_cache = agora - self.COOLDOWN_POSTAGEM_SEG
        async with self.db.execute("SELECT id, timestamp FROM postados WHERE timestamp >= ?", (limite_cache,)) as cursor:
            async for row in cursor:
                self.cache_postados[row[0]] = row[1]
                
        log.info(f"💾 Banco inicializado. {len(self.cache_postados)} produtos carregados no cache de proteção.")

    # =================================================================
    # 🛒 COMUNICAÇÃO ALIEXPRESS
    # =================================================================
    def gerar_assinatura(self, params: dict) -> str:
        pares = sorted(params.items())
        payload = self.ali_secret + "".join(f"{k}{v}" for k, v in pares if v is not None) + self.ali_secret
        return hashlib.md5(payload.encode("utf-8")).hexdigest().upper()

    async def buscar_produtos(self, termo: str) -> List[dict]:
        params = {
            "app_key": self.ali_key,
            "method": "aliexpress.affiliate.product.query",
            "timestamp": str(int(time.time() * 1000)),
            "format": "json", "v": "2.0", "sign_method": "md5",
            "keywords": termo, 
            "page_size": "50", 
            "page_no": str(random.randint(1, 5)), # Busca profunda aleatória
            "sort": random.choice(self.ESTRATEGIAS_SORT),
            "target_currency": "BRL", "target_language": "PT",
            "tracking_id": self.ali_tracking_id, "ship_to_country": "BR"
        }
        params["sign"] = self.gerar_assinatura(params)
        
        try:
            async with self.session.get(self.ali_api_url, params=params, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("aliexpress_affiliate_product_query_response", {}) \
                               .get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
        except asyncio.TimeoutError:
            log.warning(f"⏳ Timeout na API AliExpress para o termo '{termo}'.")
        except Exception as e:
            log.error(f"❌ Erro API AliExpress ({termo}): {e}")
        return []

    # =================================================================
    # 📲 ENVIOS TELEGRAM (COM ANTI-BAN E RETRY)
    # =================================================================
    async def enviar_telegram(self, d: dict) -> bool:
        titulo_ia = html.escape(self.processar_titulo_ia(d['titulo']))
        queda = int((1 - (d['preco_atual'] / d['preco_historico'])) * 100)
        
        msg = (
            f"🚨 <b>QUEDA HISTÓRICA REGISTRADA! (-{queda}%)</b>\n\n"
            f"📦 <b>{titulo_ia}...</b>\n\n"
            f"📉 Preço antigo: <strike>R$ {self.formatar_moeda(d['preco_historico'])}</strike>\n"
            f"💰 <b>Novo Preço: R$ {self.formatar_moeda(d['preco_atual'])}</b>\n\n"
            f"⭐ Avaliação: {d['estrelas']:.1f}⭐\n"
            f"🛒 Vendas: {d['volume']}+ confirmadas\n\n"
            f"🎯 <b><a href='{d['link']}'>[ CLIQUE AQUI PARA COMPRAR ]</a></b>\n\n"
            f"⚠️ <i>Estoque e preço podem sofrer alteração.</i>"
        )
        
        payload = {"chat_id": self.id_grupo, "photo": d["imagem"], "caption": msg, "parse_mode": "HTML"}
        url = f"{self.telegram_api}/sendPhoto"
        
        for tentativa in range(3):
            try:
                async with self.session.post(url, json=payload, timeout=10) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        log.info(f"✅ POSTADO: {titulo_ia} | R$ {d['preco_atual']}")
                        return True
                    elif resp.status == 429:
                        wait_time = data.get("parameters", {}).get("retry_after", 15)
                        log.warning(f"⚠️ Telegram Anti-Spam! Pausando por {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        log.error(f"❌ Erro Telegram: {data.get('description')}")
                        break
            except Exception as e:
                log.error(f"🌐 Erro de rede Telegram (Tentativa {tentativa+1}): {e}")
                await asyncio.sleep(2)
        return False

    # =================================================================
    # ⚙️ MOTOR DE AVALIAÇÃO PRINCIPAL
    # =================================================================
    async def avaliar_produto(self, p: dict):
        p_id = str(p.get("product_id"))
        titulo = p.get("product_title", "Produto sem título")
        agora = int(time.time())
        
        # 1. Filtro Rápido na RAM (Super rápido)
        if p_id in self.cache_postados and (agora - self.cache_postados[p_id]) < self.COOLDOWN_POSTAGEM_SEG:
            return

        # 2. Extração e Validação de Dados Básicos
        try:
            estrelas = float(str(p.get("evaluate_rate") or "0").replace("%", "").strip())
            volume = int(p.get("lastest_volume") or p.get("volume") or 0)
            preco_atual = float(str(p.get("target_sale_price") or p.get("sale_price") or "0").replace(",", "."))
        except ValueError:
            return

        # 3. Filtros de Qualidade Rígidos
        if estrelas < self.MIN_ESTRELAS or volume < self.MIN_VENDAS or preco_atual < self.MIN_PRECO_BRL:
            return

        # 4. Avaliação Histórica (Apenas para produtos que passaram na triagem)
        async with self.db.execute("SELECT menor_preco FROM historico WHERE id=?", (p_id,)) as cursor:
            row = await cursor.fetchone()
            
            if row:
                menor_preco_antigo = row[0]
                fator_ia = self.calcular_threshold_desconto(menor_preco_antigo)
                
                # É OFERTA?
                if preco_atual <= (menor_preco_antigo * fator_ia):
                    d_formatado = {
                        "id": p_id, "titulo": titulo, "preco_atual": preco_atual,
                        "preco_historico": menor_preco_antigo, "link": p.get("promotion_link"),
                        "imagem": p.get("product_main_image_url"), "estrelas": estrelas, "volume": volume
                    }
                    if await self.enviar_telegram(d_formatado):
                        # Atualiza Cache RAM e Banco
                        self.cache_postados[p_id] = agora
                        await self.db.execute("INSERT OR REPLACE INTO postados VALUES (?, ?)", (p_id, agora))
                
                # Se o preço caiu (mesmo não sendo oferta matadora), atualiza o novo piso
                if preco_atual < menor_preco_antigo:
                    await self.db.execute("UPDATE historico SET menor_preco=?, ultima_atualizacao=? WHERE id=?", (preco_atual, agora, p_id))
            else:
                # Primeiro contato: registra no banco para monitoramento futuro
                await self.db.execute("INSERT INTO historico VALUES (?, ?, ?, ?)", (p_id, titulo, preco_atual, agora))
                
        await self.db.commit()

    # =================================================================
    # 🚀 CICLO DE VIDA DO BOT
    # =================================================================
    async def shutdown(self):
        log.info("🛑 Encerrando o bot de forma segura...")
        self._rodando = False
        if self.session: await self.session.close()
        if self.db: await self.db.close()
        log.info("✔️ Recursos liberados. Adeus!")

    async def iniciar(self):
        log.info("🚀 SNIPER IA V17 (Enterprise Edition) INICIADO!")
        connector = aiohttp.TCPConnector(limit=20)
        self.session = aiohttp.ClientSession(connector=connector)
        
        await self.init_db_and_cache()

        ciclo_count = 0
        while self._rodando:
            try:
                ciclo_count += 1
                log.info(f"🔄 Iniciando Varredura | Ciclo #{ciclo_count}")
                
                marcas_shuffled = self.MARCAS_BASE.copy()
                random.shuffle(marcas_shuffled)
                
                for termo in marcas_shuffled:
                    if not self._rodando: break # Para imediatamente se mandarem desligar
                    
                    produtos = await self.buscar_produtos(termo)
                    if produtos:
                        for p in produtos:
                            await self.avaliar_produto(p)
                    
                    await asyncio.sleep(self.DELAY_ENTRE_MARCAS_SEG)
                
                if self._rodando:
                    descanso = self.DELAY_ENTRE_CICLOS_MIN * 60
                    log.info(f"💤 Ciclo completo. BD Histórico aprendendo. Aguardando {self.DELAY_ENTRE_CICLOS_MIN} min...")
                    await asyncio.sleep(descanso)
                    
            except Exception as e:
                log.error(f"💥 Erro fatal no loop principal: {e}")
                await asyncio.sleep(15)

if __name__ == "__main__":
    if os.name == 'nt': 
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    bot = AliExpressSniperBot()
    loop = asyncio.get_event_loop()
    
    # Tratamento de sinais para desligar bonito (Ctrl+C ou fechamento do terminal)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.shutdown()))
        except NotImplementedError:
            pass # Windows não suporta add_signal_handler muito bem

    try:
        loop.run_until_complete(bot.iniciar())
    except KeyboardInterrupt:
        loop.run_until_complete(bot.shutdown())
