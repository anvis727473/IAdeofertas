import asyncio
import hashlib
import logging
import time
import uuid
import os
from typing import Any, Dict, List
import httpx
from database import get_client
from analytics import OfferSniperAnalytics

logger = logging.getLogger("bot.search_engine")

class AliExpressSearchEngine:
    BLACKLIST = ["clothes", "dress", "sexy", "lingerie", "toy", "plush", "poster", "sticker", "baby", "cosplay"]
    NICHE_KEYWORDS = ["ssd", "keyboard", "teclado", "mouse", "monitor", "router", "roteador", "hub", "pc", "gaming", "usb", "ram", "nvme"]

    def __init__(self, api_key: str, max_concurrent_requests: int = 3):
        self.supabase = get_client()
        self.app_key = api_key
        self.app_secret = os.environ.get("ALI_APP_SECRET", "")
        self.semaphore = asyncio.Semaphore(int(max_concurrent_requests))
        self.http_client = httpx.AsyncClient(base_url="https://api-sg.aliexpress.com", http2=True, timeout=15.0)

    def _generate_sign(self, params: Dict[str, Any]) -> str:
        sorted_keys = sorted(params.keys())
        sign_str = self.app_secret
        for key in sorted_keys:
            sign_str += f"{key}{params[key]}"
        sign_str += self.app_secret
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()

    async def run_parallel_discovery(self, keywords: List[str], target_pages: int = 2) -> int:
        tasks = [self.fetch_keyword_page(kw, page) for kw in keywords for page in range(1, target_pages + 1)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        raw_products = []
        for res in results:
            if isinstance(res, list):
                raw_products.extend(res)

        inserted_count = 0
        for prod in raw_products:
            titulo = prod.get("product_title") or prod.get("title", "")
            if not any(good in titulo.lower() for good in self.NICHE_KEYWORDS) or any(bad in titulo.lower() for bad in self.BLACKLIST):
                continue

            link = prod.get("promotion_link") or prod.get("product_detail_url", "")
            if not link:
                continue

            # Proteção contra Event Loop Starvation: Isola I/O síncrono do SDK em Thread
            db_res = await asyncio.to_thread(
                lambda: self.supabase.table("ofertas").select("historico_precos").eq("url_produto", link).execute()
            )
            
            price_history = [float(x) for x in db_res.data[0].get("historico_precos", [])] if db_res.data else []
            preco_desc = float(prod.get("target_sale_price") or prod.get("sale_price") or 0.0)
            if preco_desc <= 0:
                continue
            price_history.append(preco_desc)

            telemetry = {
                "current_price": preco_desc,
                "sales_last_6h": int(prod.get("volume", 0) // 4),
                "sales_last_6h_previous": int(prod.get("volume", 0) // 5),
                "product_rating": float(prod.get("evaluate_rate") or 4.8),
                "sales_volume": int(prod.get("volume") or 50),
                "seller_feedback_rate": float(prod.get("shop_review_rate") or 0.95)
            }

            approved, meta = OfferSniperAnalytics.evaluate_product(telemetry, price_history)
            if not approved:
                continue

            payload = {
                "id": str(uuid.uuid4()),
                "titulo": titulo[:490],
                "url_produto": link,
                "url_imagem": prod.get("product_main_image_url"),
                "preco_original": float(prod.get("target_original_price") or preco_desc),
                "preco_desconto": preco_desc,
                "percentual_desconto": float(str(prod.get("discount", "0")).replace('%', '').strip() or 0.0),
                "product_rating": telemetry["product_rating"],
                "sales_volume": telemetry["sales_volume"],
                "seller_feedback_rate": telemetry["seller_feedback_rate"],
                "historico_precos": price_history
            }

            try:
                await asyncio.to_thread(lambda: self.supabase.table("ofertas").insert(payload).execute())
                inserted_count += 1
            except Exception:
                pass # Idempotência disparada pelo índice único do banco
                
        return inserted_count

    async def fetch_keyword_page(self, keyword: str, page_no: int) -> List[Dict[str, Any]]:
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
                res = await self.http_client.get("/sync", params=params)
                if res.status_code == 200:
                    data = res.json().get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {})
                    products = data.get("result", {}).get("products", {}).get("product", [])
                    return [products] if isinstance(products, dict) else products
            except Exception:
                return []
        return []

    async def close(self):
        await self.http_client.aclose()
