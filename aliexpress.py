import hashlib
import html as html_lib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config

logger = logging.getLogger(__name__)


def format_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@dataclass
class Product:
    id: str
    title: str
    url: str
    price_value: float
    price_text: str
    image: Optional[str] = None
    source: str = "api"
    keyword: str = ""
    price_origin: str = "api"
    score: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "price_value": self.price_value,
            "price_text": self.price_text,
            "image": self.image,
            "source": self.source,
            "keyword": self.keyword,
            "price_origin": self.price_origin,
            "score": self.score,
            "raw": self.raw,
        }


class AliExpressClient:
    def __init__(self):
        Config.validate()
        self.app_key = Config.ALI_KEY
        self.secret = Config.ALI_SECRET
        self.tracking_id = Config.ALI_TRACKING_ID
        self.api_url = "https://api-sg.aliexpress.com/sync"
        self.search_keywords = Config.SEARCH_KEYWORDS

        self.session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET"},
        )
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(Config.USER_AGENTS),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.aliexpress.com/",
        }

    def _generate_sign(self, params: dict) -> str:
        sorted_params = sorted(params.items())
        sign_str = self.secret
        for key, value in sorted_params:
            sign_str += f"{key}{value}"
        sign_str += self.secret
        return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

    def _estimate_price(self, keyword: str) -> float:
        k = keyword.lower()
        base_map = {
            "ssd": 179,
            "nvme": 189,
            "teclado": 129,
            "mouse": 89,
            "charger": 79,
            "dock": 159,
            "hub": 59,
            "mini pc": 799,
            "smartwatch": 199,
            "parafusadeira": 149,
            "xiaomi": 169,
        }
        base = 199
        for key, value in base_map.items():
            if key in k:
                base = value
                break
        variation = random.uniform(-0.18, 0.22)
        return round(max(19.9, base * (1 + variation)), 2)

    def _score_product(self, product: Product) -> int:
        score = 0
        t = product.title.lower()

        if product.price_origin == "extracted":
            score += 2
        if any(x in t for x in ["ssd", "nvme", "wifi 6", "teclado", "mouse", "charger", "dock", "hub", "smartwatch", "mini pc"]):
            score += 2
        if any(x in t for x in ["xiaomi", "baseus", "anker", "ugreen", "logitech", "kingston", "sandisk"]):
            score += 1
        if product.price_value <= 150:
            score += 1
        if product.price_value <= 100:
            score += 1

        return score

    def _normalize_api_product(self, p: Dict[str, Any], keyword: str) -> Product:
        pid = str(p.get("product_id") or p.get("item_id") or p.get("id") or "")
        title = p.get("product_title") or p.get("title") or keyword
        url = p.get("product_detail_url") or p.get("product_url") or f"https://pt.aliexpress.com/item/{pid}.html"
        image = p.get("product_main_image_url") or p.get("image")

        raw_price = p.get("target_sale_price") or p.get("sale_price") or p.get("price") or 0
        try:
            price_value = float(str(raw_price).replace(",", "."))
        except Exception:
            price_value = 0.0

        price_text = format_brl(price_value) if price_value > 0 else "Preço indisponível"

        product = Product(
            id=pid,
            title=str(title),
            url=str(url),
            price_value=price_value,
            price_text=price_text,
            image=image,
            source="api",
            keyword=keyword,
            price_origin="api",
            raw=p,
        )
        product.score = self._score_product(product)
        return product

    def _extract_ids_from_html(self, html: str) -> List[str]:
        patterns = [
            r'/item/(\d{8,20})\.html',
            r'"productId"\s*:\s*"?(\\d{8,20})"?',
            r'"itemId"\s*:\s*"?(\\d{8,20})"?',
            r'product_id["\']?\s*:\s*["\']?(\d{8,20})',
        ]

        ids: List[str] = []
        for pattern in patterns:
            ids.extend(re.findall(pattern, html))

        cleaned = []
        for pid in ids:
            pid = str(pid).strip()
            if pid and pid not in cleaned:
                cleaned.append(pid)
        return cleaned

    def _extract_title_candidates(self, html: str) -> List[str]:
        titles = re.findall(r'title="([^"]{5,180})"', html)
        cleaned = []
        for title in titles:
            title = html_lib.unescape(title).strip()
            if title and title not in cleaned:
                cleaned.append(title)
        return cleaned

    def _extract_price_candidates(self, html: str) -> List[float]:
        raw_prices = re.findall(r'R\$\s?([\d\.\,]{2,12})', html)
        prices: List[float] = []

        for raw in raw_prices:
            clean = raw.replace(".", "").replace(",", ".")
            try:
                value = float(clean)
                if 3 <= value <= 50000:
                    prices.append(round(value, 2))
            except Exception:
                continue

        return prices

    def search_niche_products(self) -> List[Product]:
        keyword = random.choice(self.search_keywords)
        logger.info(f"Buscando produtos para: {keyword}")

        api_products = self._search_via_api(keyword)
        if api_products:
            return api_products

        return self._fallback_web_garimpo(keyword)

    def _search_via_api(self, keyword: str) -> List[Product]:
        params = {
            "method": "aliexpress.affiliate.product.query",
            "app_key": self.app_key,
            "timestamp": str(int(time.time() * 1000)),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "keywords": keyword,
            "target_currency": "BRL",
            "target_language": "PT",
            "ship_to_country": "BR",
            "page_size": "10",
        }
        params["sign"] = self._generate_sign(params)

        try:
            response = self.session.get(self.api_url, params=params, headers=self._headers(), timeout=(5, 15))
            data = response.json()

            error_code = str(data.get("error_response", {}).get("code", ""))
            if "Permission" in error_code or "InsufficientPermission" in str(data):
                logger.warning("Permissão insuficiente na API oficial. Usando fallback público.")
                return []

            root = data.get("aliexpress_affiliate_product_query_response", {})
            resp_result = root.get("resp_result", {})
            if int(resp_result.get("code", 0)) != 200:
                return []

            products = resp_result.get("result", {}).get("products", {}).get("product", [])
            if isinstance(products, dict):
                products = [products]

            result: List[Product] = []
            for p in products[:10]:
                product = self._normalize_api_product(p, keyword)
                if product.id and product.price_value > 0:
                    result.append(product)

            return result
        except Exception as e:
            logger.warning(f"Falha na API, usando fallback. Erro: {e}")
            return []

    def _fallback_web_garimpo(self, keyword: str) -> List[Product]:
        slug = quote_plus(keyword.replace(" ", "-"))
        url = f"https://pt.aliexpress.com/w/wholesale-{slug}.html"

        try:
            res = self.session.get(url, headers=self._headers(), timeout=(5, 15))
            html = res.text

            ids = self._extract_ids_from_html(html)
            if not ids:
                logger.info("Fallback sem IDs encontrados.")
                return []

            titles = self._extract_title_candidates(html)
            prices = self._extract_price_candidates(html)

            products: List[Product] = []
            for idx, pid in enumerate(ids[:8]):
                title = titles[idx] if idx < len(titles) else f"{keyword} - Oferta detectada"
                if idx < len(prices):
                    price_value = prices[idx]
                    price_origin = "extracted"
                else:
                    price_value = self._estimate_price(keyword)
                    price_origin = "estimated"

                raw = {
                    "keyword": keyword,
                    "id_source": "public_search_html",
                }

                product = Product(
                    id=pid,
                    title=title,
                    url=f"https://pt.aliexpress.com/item/{pid}.html",
                    price_value=price_value,
                    price_text=format_brl(price_value),
                    image="https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=1200",
                    source="fallback",
                    keyword=keyword,
                    price_origin=price_origin,
                    raw=raw,
                )
                product.score = self._score_product(product)
                products.append(product)

            return products
        except Exception as e:
            logger.exception(f"Erro no fallback web garimpo: {e}")
            return []

    def generate_affiliate_link(self, original_url: str) -> str:
        params = {
            "method": "aliexpress.affiliate.link.generate",
            "app_key": self.app_key,
            "timestamp": str(int(time.time() * 1000)),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "source_values": original_url,
            "tracking_id": self.tracking_id,
        }
        params["sign"] = self._generate_sign(params)

        try:
            response = self.session.get(self.api_url, params=params, headers=self._headers(), timeout=(5, 15))
            data = response.json()

            root = data.get("aliexpress_affiliate_link_generate_response", {})
            resp_result = root.get("resp_result", {})
            if int(resp_result.get("code", 0)) != 200:
                return original_url

            result = resp_result.get("result", {})
            for container_key in ("promolink_list", "promotable_link_list"):
                container = result.get(container_key, {})
                links = container.get("promo_link", []) or container.get("promotable_link", [])
                if isinstance(links, dict):
                    links = [links]
                if links:
                    for link in links:
                        final_url = link.get("promotion_link") or link.get("promotionUrl")
                        if final_url:
                            return final_url

            return original_url
        except Exception:
            return original_url
