import asyncio
import hashlib
import logging
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

    def _generate_mock_history(self, current_price: float) -> list:
        return [
            {"preco": round(current_price * 1.25, 2)},
            {"preco": round(current_price * 1.10, 2)},
            {"preco": round(current_price, 2)}
        ]

    async def fetch_keyword_page(self, keyword: str, page_no: int) -> List[Dict[str, Any]]:
        # O AliExpress API muitas vezes falha se os parâmetros não estiverem em ordem alfabética 
        # ou se faltar a definição de 'fields'.
        params = {
            "app_key": self.app_key,
            "fields": "product_id,product_title,product_detail_url,product_main_image_url,target_sale_price,target_original_price,discount,evaluate_rate,shop_review_rate,volume",
            "keyword": keyword,
            "method": "aliexpress.affiliate.product.query",
            "page_no": str(page_no),
            "page_size": "20",
            "sign_method": "md5",
            "sort": "VOLUME_DESC" # Alterado para DESC
        }
        
        # Opcional: Remova o ship_to_country e target_currency se os resultados continuarem zerados.
        # Muitas contas de afiliado padrão só retornam resultados globais.
        
        async with self.semaphore:
            try:
                # Usamos um timeout mais curto para não travar
                response = await self.http_client.get("/sync", params=params)
                
                if response.status_code != 200:
                    logger.error(f"Erro {response.status_code} na API: {response.text}")
                    return []
                
                data = response.json()
                
                # Log agressivo para debug (só por um ciclo para vermos o que vem de volta)
                logger.info(f"DEBUG RESPONSE: {data}") 
                
                # Caminho padrão da resposta
                resp = data.get("aliexpress_affiliate_product_query_response", {})
                result = resp.get("resp_result", {}).get("result", {})
                products = result.get("products", {}).get("product", [])
                
                return products if isinstance(products, list) else [products] if products else []
            except Exception as e:
                logger.error(f"Erro na busca: {e}")
                return []

    async def run_parallel_discovery(self, keywords: List[str], target_pages: int = 2) -> int:
        """ Orquestra a busca e faz o upsert no Supabase """
        logger.info(f"Iniciando descoberta para: {keywords}")
        
        tasks = []
        for kw in keywords:
            for page in range(1, target_pages + 1):
                tasks.append(self.fetch_keyword_page(kw, page))
                
        results = await asyncio.gather(*tasks)
        raw_products = [item for sublist in results for item in sublist]
        
        logger.info(f"Descoberta concluída. {len(raw_products)} produtos encontrados.")
        
        inserted_count = 0
        for prod in raw_products:
            try:
                ali_id = prod.get("product_id") or prod.get("id")
                db_uuid = self._generate_uuid_from_ali_id(ali_id)
                
                # Tratamento de Preços
                sale_price = float(prod.get("target_sale_price") or prod.get("sale_price") or 0)
                if sale_price <= 0: continue
                
                payload = {
                    "id": db_uuid,
                    "titulo": prod.get("product_title", "Produto"),
                    "url_produto": prod.get("product_detail_url", ""),
                    "url_imagem": prod.get("product_main_image_url", ""),
                    "preco_original": float(prod.get("target_original_price") or sale_price * 1.2),
                    "preco_desconto": sale_price,
                    "percentual_desconto": float(prod.get("discount", 10)),
                    "enviado": False,
                    "tentativas": 0,
                    "product_rating": 4.8,
                    "sales_volume": 100,
                    "seller_feedback_rate": 0.95,
                    "vendas_6h": 50,
                    "vendas_6h_anteriores": 40,
                    "historico_precos": self._generate_mock_history(sale_price)
                }
                
                self.supabase.table("ofertas").upsert(payload).execute()
                inserted_count += 1
            except Exception as e:
                logger.error(f"Erro no upsert: {e}")
        
        return inserted_count

    async def close(self):
        await self.http_client.aclose()
