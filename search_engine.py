import asyncio
import hashlib
import logging
import time
import uuid
from typing import Any, Dict, List
import httpx
from supabase import Client

logger = logging.getLogger("bot.search_engine")

class AliExpressSearchEngine:
    def __init__(self, supabase_client: Client, api_key: str, max_concurrent_requests: int = 3):
        self.supabase = supabase_client
        self.app_key = api_key
        self.NAMESPACE_ALI = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.http_client = httpx.AsyncClient(
            base_url="https://api-sg.aliexpress.com",
            http2=True,
            timeout=httpx.Timeout(15.0, connect=5.0)
        )

    def _generate_uuid_from_ali_id(self, ali_product_id: Any) -> str:
        return str(uuid.uuid5(self.NAMESPACE_ALI, str(ali_product_id)))

    async def fetch_keyword_page(self, keyword: str, page_no: int) -> List[Dict[str, Any]]:
        """ Busca produtos com timestamp obrigatório para validar a autenticação """
        params = {
            "app_key": self.app_key,
            "fields": "product_id,product_title,product_detail_url,product_main_image_url,target_sale_price,target_original_price,discount,evaluate_rate,shop_review_rate,volume",
            "keyword": keyword,
            "method": "aliexpress.affiliate.product.query",
            "page_no": str(page_no),
            "page_size": "20",
            "sign_method": "md5",
            "sort": "VOLUME_DESC",
            "timestamp": str(int(time.time() * 1000))
        }
        
        async with self.semaphore:
            try:
                response = await self.http_client.get("/sync", params=params)
                if response.status_code != 200:
                    return []
                
                data = response.json()
                
                # Tratamento de erro retornado pela API
                if "error_response" in data:
                    logger.error(f"Erro da API para {keyword}: {data['error_response'].get('msg')}")
                    return []
                
                # Extração segura conforme estrutura da API v2
                resp = data.get("aliexpress_affiliate_product_query_response", {})
                resp_result = resp.get("resp_result", {})
                result = resp_result.get("result", {})
                products = result.get("products", {}).get("product", [])
                
                return products if isinstance(products, list) else [products] if products else []
            except Exception as e:
                logger.error(f"Exceção ao buscar {keyword}: {e}")
                return []

    async def run_parallel_discovery(self, keywords: List[str], target_pages: int = 1) -> int:
        """ Executa busca paralela e realiza upsert no Supabase """
        tasks = [self.fetch_keyword_page(kw, page) for kw in keywords for page in range(1, target_pages + 1)]
        results = await asyncio.gather(*tasks)
        
        raw_products = [item for sublist in results for item in sublist]
        inserted_count = 0
        
        for prod in raw_products:
            try:
                ali_id = prod.get("product_id")
                sale_price = float(prod.get("target_sale_price") or 0)
                if not ali_id or sale_price <= 0: continue
                
                payload = {
                    "id": self._generate_uuid_from_ali_id(ali_id),
                    "titulo": prod.get("product_title", "Produto"),
                    "url_produto": prod.get("product_detail_url", ""),
                    "url_imagem": prod.get("product_main_image_url", ""),
                    "preco_original": float(prod.get("target_original_price") or sale_price * 1.2),
                    "preco_desconto": sale_price,
                    "percentual_desconto": float(prod.get("discount", 10)),
                    "enviado": False,
                    "tentativas": 0,
                    "product_rating": float(prod.get("evaluate_rate", 4.8)),
                    "sales_volume": int(prod.get("volume", 50)),
                    "seller_feedback_rate": float(prod.get("shop_review_rate", 0.95)),
                    "atualizado_em": "now()"
                }
                
                self.supabase.table("ofertas").upsert(payload).execute()
                inserted_count += 1
            except Exception as e:
                logger.error(f"Erro no upsert: {e}")
                
        return inserted_count

    async def close(self):
        await self.http_client.aclose()
