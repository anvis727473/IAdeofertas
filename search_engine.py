import asyncio
import hashlib
import logging
import time
import uuid
import os
from typing import Any, Dict, List
import httpx
from supabase import Client

logger = logging.getLogger("bot.search_engine")

class AliExpressSearchEngine:
    # Filtro de rejeição (termos indesejados que poluem o nicho)
    BLACKLIST = [
        "clothes", "dress", "sexy", "lingerie", "toy", "plush", "poster", "sticker", 
        "baby", "cosplay", "t-shirt", "jewelry", "makeup", "underwear", "socks"
    ]
    
    # Validação inclusiva de nicho (termos em português e variações comuns)
    NICHE_KEYWORDS = [
        "ssd", "keyboard", "teclado", "mouse", "monitor", "router", "roteador", "hub", "pc", 
        "gaming", "gamer", "usb", "headset", "ram", "ddr4", "ddr5", "nvme", "gpu", "cooler",
        "charger", "carregador", "power bank", "cable", "cabo", "pad", "baseus", "ugreen"
    ]

    def __init__(self, supabase_client: Client, api_key: str, max_concurrent_requests: int = 3):
        self.supabase = supabase_client
        self.app_key = api_key
        self.app_secret = os.environ.get("ALI_APP_SECRET", "")
        self.NAMESPACE_ALI = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.http_client = httpx.AsyncClient(
            base_url="https://api-sg.aliexpress.com",
            http2=True,
            timeout=httpx.Timeout(15.0, connect=5.0)
        )

    def _generate_sign(self, params: Dict[str, Any]) -> str:
        sorted_keys = sorted(params.keys())
        sign_str = self.app_secret
        for key in sorted_keys:
            sign_str += f"{key}{params[key]}"
        sign_str += self.app_secret
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()

    def _is_relevant(self, title: str) -> bool:
        t = title.lower()
        # 1. Se contiver termos banidos, descarta
        if any(bad in t for bad in self.BLACKLIST):
            return False
        # 2. Precisa ter relação com o nicho tech
        return any(good in t for good in self.NICHE_KEYWORDS)

    async def run_parallel_discovery(self, keywords: List[str], target_pages: int = 2) -> int:
        """
        Orquestra a busca paralela utilizando as palavras-chave fornecidas pelo run_scraper.py
        """
        logger.info(f"Iniciando varredura paralela para {len(keywords)} termos...")
        tasks = []
        for kw in keywords:
            for page in range(1, target_pages + 1):
                tasks.append(self.fetch_keyword_page(kw, page))
        
        # Executa as chamadas de API em lote assíncrono
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Achata a lista de respostas e filtra possíveis exceções
        raw_products = []
        for res in results:
            if isinstance(res, list):
                raw_products.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"Erro numa das chamadas de página da API: {res}")

        logger.info(f"API do AliExpress retornou {len(raw_products)} produtos brutos no total.")
        
        inserted_count = 0
        for prod in raw_products:
            titulo = prod.get("product_title") or prod.get("title", "")
            if not titulo:
                continue
                
            # Aplica o filtro de nicho
            if not self._is_relevant(titulo):
                continue

            try:
                # Tratamento seguro de IDS e links
                prod_id = str(prod.get("product_id") or prod.get("id"))
                link = prod.get("promotion_link") or prod.get("product_detail_url") or ""
                
                # Captura percentual de desconto de forma resiliente
                discount_raw = str(prod.get("discount", "0")).replace('%', '')
                discount_pct = float(discount_raw) if discount_raw.replace('.','',1).isdigit() else 0.0
                
                payload = {
                    "id": str(uuid.uuid5(self.NAMESPACE_ALI, prod_id)),
                    "titulo": titulo,
                    "url_produto": link,
                    "url_imagem": prod.get("product_main_image_url", ""),
                    "preco_original": float(prod.get("target_original_price") or prod.get("original_price") or 0),
                    "preco_desconto": float(prod.get("target_sale_price") or prod.get("sale_price") or 0),
                    "percentual_desconto": discount_pct,
                    "enviado": False,
                    "tentativas": 0,
                    "product_rating": float(prod.get("evaluate_rate", 4.8)),
                    "sales_volume": int(prod.get("volume") or prod.get("sales", 50)),
                    "seller_feedback_rate": float(prod.get("shop_review_rate", 0.95))
                }
                
                # Executa o Upsert seguro no Supabase
                self.supabase.table("ofertas").upsert(payload).execute()
                inserted_count += 1
            except Exception as e:
                logger.error(f"Falha ao mapear dados ou executar Upsert: {e}")
                
        return inserted_count

    async def fetch_keyword_page(self, keyword: str, page_no: int) -> List[Dict[str, Any]]:
        """ Executa a chamada real para a API do AliExpress utilizando o Semáforo de concorrência """
        async with self.semaphore:
            params = {
                "app_key": self.app_key,
                "method": "aliexpress.affiliate.product.query",
                "page_no": str(page_no),
                "page_size": "20",
                "keyword": keyword,
                "sort": "VOLUME_DESC",
                "timestamp": str(int(time.time() * 1000)),
                "sign_method": "md5",
                "v": "2.0"
            }
            params["sign"] = self._generate_sign(params)
            
            try:
                response = await self.http_client.get("/sync", params=params)
                if response.status_code != 200:
                    logger.warning(f"API respondeu com status incorreto: {response.status_code}")
                    return []
                
                data = response.json()
                # Navega na árvore de resposta padrão do AliExpress
                root = data.get("aliexpress_affiliate_product_query_response", {})
                resp_result = root.get("resp_result", {})
                if resp_result.get("resp_code") != 200:
                    logger.debug(f"Aviso da API para termo '{keyword}': {resp_result.get('resp_msg')}\")")
                    return []
                    
                products = resp_result.get("result", {}).get("products", {}).get("product", [])
                if isinstance(products, dict):
                    return [products]
                return products if isinstance(products, list) else []
            except Exception as e:
                logger.error(f"Exceção de rede na chamada da API para '{keyword}': {e}")
                return []

    async def close(self):
        await self.http_client.aclose()
