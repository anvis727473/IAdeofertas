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
        """ Gera a assinatura MD5 seguindo a ordenação alfabética estrita """
        # Ordena as chaves
        sorted_keys = sorted(params.keys())
        # Concatena: Secret + Chave1 + Valor1 + ... + ChaveN + ValorN + Secret
        sign_str = self.app_secret
        for key in sorted_keys:
            sign_str += f"{key}{params[key]}"
        sign_str += self.app_secret
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()

    async def fetch_keyword_page(self, keyword: str, page_no: int) -> List[Dict[str, Any]]:
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
        
        # Gera a assinatura após definir todos os parâmetros
        params["sign"] = self._generate_sign(params)
        
        async with self.semaphore:
            try:
                response = await self.http_client.get("/sync", params=params)
                if response.status_code != 200: return []
                data = response.json()
                
                if "error_response" in data:
                    logger.error(f"Erro API ({keyword}): {data['error_response'].get('msg')}")
                    return []
                
                resp = data.get("aliexpress_affiliate_product_query_response", {})
                products = resp.get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
                return products if isinstance(products, list) else [products] if products else []
            except Exception as e:
                logger.error(f"Exceção ao buscar {keyword}: {e}")
                return []

    async def run_parallel_discovery(self, keywords: List[str], target_pages: int = 1) -> int:
        tasks = [self.fetch_keyword_page(kw, page) for kw in keywords for page in range(1, target_pages + 1)]
        results = await asyncio.gather(*tasks)
        raw_products = [item for sublist in results for item in sublist]
        
        inserted_count = 0
        for prod in raw_products:
            try:
                # Tratamento robusto para evitar erro de string -> float
                discount_raw = str(prod.get("discount", "0")).replace('%', '')
                
                payload = {
                    "id": str(uuid.uuid5(self.NAMESPACE_ALI, str(prod.get("product_id")))),
                    "titulo": prod.get("product_title", "Produto"),
                    "url_produto": prod.get("product_detail_url", ""),
                    "url_imagem": prod.get("product_main_image_url", ""),
                    "preco_original": float(prod.get("target_original_price") or 0),
                    "preco_desconto": float(prod.get("target_sale_price") or 0),
                    "percentual_desconto": float(discount_raw),
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
