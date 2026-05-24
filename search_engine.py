import asyncio
import hashlib
import logging
import time
import os
import httpx
from supabase import Client

logger = logging.getLogger("bot.search_engine")

class AliExpressSearchEngine:
    # Filtro cirúrgico: O que NÃO queremos
    BLACKLIST = ["clothes", "dress", "sexy", "lingerie", "toy", "plush", "poster", "sticker", "baby"]
    # O que obrigatoriamente precisamos
    NICHE = ["ssd", "keyboard", "mouse", "monitor", "router", "hub", "pc", "gaming", "usb", "headset", "ram"]

    def __init__(self, supabase_client: Client, api_key: str):
        self.supabase = supabase_client
        self.app_key = api_key
        self.app_secret = os.environ.get("ALI_APP_SECRET", "")
        self.http_client = httpx.AsyncClient(base_url="https://api-sg.aliexpress.com", http2=True, timeout=15.0)

    def _is_valid(self, title: str) -> bool:
        t = title.lower()
        if any(bad in t for bad in self.BLACKLIST): return False
        return any(good in t for good in self.NICHE)

    async def fetch_and_save(self, keyword: str):
        params = {
            "app_key": self.app_key, "fields": "product_id,product_title,target_sale_price,target_original_price,promotion_link",
            "keyword": keyword, "method": "aliexpress.affiliate.product.query", "page_size": "20",
            "timestamp": str(int(time.time() * 1000)), "sort": "VOLUME_DESC"
        }
        # Adicione aqui sua lógica de sinalização (sign) conforme sua API atual
        try:
            resp = await self.http_client.get("/sync", params=params)
            products = resp.json().get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
            
            for p in (products if isinstance(products, list) else [products] if products else []):
                if self._is_valid(p.get("product_title", "")):
                    payload = {
                        "titulo": p["product_title"], "url_produto": p.get("promotion_link"),
                        "preco_original": float(p.get("target_original_price", 0)),
                        "preco_desconto": float(p.get("target_sale_price", 0)),
                        "enviado": False
                    }
                    self.supabase.table("ofertas").upsert(payload).execute()
        except Exception as e:
            logger.error(f"Erro na busca: {e}")

    async def close(self):
        await self.http_client.aclose()
