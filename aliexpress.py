# aliexpress.py

import hashlib
import json
import logging
import random
import re
import time

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import requests

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class Product:

    id: str
    title: str
    url: str
    price_value: float
    image: str

    sold_count: int = 0
    rating: float = 0.0
    shipping: str = ""

    is_choice: bool = False

    score: int = 0

    source: str = "fallback"

    raw: dict = field(default_factory=dict)

    def price_text(self):

        return (
            f"R$ {self.price_value:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    def to_dict(self):

        return asdict(self)


class AliExpressClient:

    def __init__(self):

        Config.validate()

        self.app_key = Config.ALI_KEY
        self.secret = Config.ALI_SECRET
        self.tracking_id = Config.ALI_TRACKING_ID

        self.api_url = (
            "https://api-sg.aliexpress.com/sync"
        )

        self.search_keywords = (
            Config.SEARCH_KEYWORDS
        )

        self.session = requests.Session()

        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1,
            status_forcelist=[
                429,
                500,
                502,
                503,
                504
            ],
        )

        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=retries
        )

        self.session.mount(
            "https://",
            adapter
        )

    def _headers(self):

        return {
            "User-Agent": random.choice(
                Config.USER_AGENTS
            ),

            "Accept-Language": (
                "pt-BR,pt;q=0.9,en-US;q=0.8"
            ),

            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),

            "Cache-Control": "no-cache",

            "Pragma": "no-cache",

            "Referer": "https://pt.aliexpress.com/",

            "Upgrade-Insecure-Requests": "1",
        }

    def _generate_sign(
        self,
        params: dict
    ) -> str:

        sorted_params = sorted(
            params.items()
        )

        sign_str = self.secret

        for key, value in sorted_params:

            sign_str += f"{key}{value}"

        sign_str += self.secret

        return hashlib.md5(
            sign_str.encode("utf-8")
        ).hexdigest().upper()

    def generate_affiliate_link(
        self,
        original_url: str
    ) -> str:

        params = {
            "method": (
                "aliexpress.affiliate.link.generate"
            ),
            "app_key": self.app_key,
            "timestamp": str(
                int(time.time() * 1000)
            ),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "source_values": original_url,
            "tracking_id": self.tracking_id
        }

        params["sign"] = (
            self._generate_sign(params)
        )

        try:

            response = self.session.get(
                self.api_url,
                params=params,
                timeout=(5, 15)
            )

            data = response.json()

            result = (
                data.get(
                    "aliexpress_affiliate_link_generate_response",
                    {}
                )
                .get("resp_result", {})
            )

            if result.get("code") == 200:

                links = (
                    result.get("result", {})
                    .get("promolink_list", {})
                    .get("promo_link", [])
                )

                if isinstance(links, dict):
                    links = [links]

                if links:

                    return links[0].get(
                        "promotion_link",
                        original_url
                    )

            return original_url

        except Exception:

            logger.exception(
                "Erro gerar afiliado"
            )

            return original_url

    def search_niche_products(
        self
    ) -> List[Product]:

        keyword = random.choice(
            self.search_keywords
        )

        logger.info(
            f"Garimpando keyword: {keyword}"
        )

        return self._fallback_real_scraping(
            keyword
        )

    def _fallback_real_scraping(
        self,
        keyword: str
    ) -> List[Product]:

        url = (
            "https://pt.aliexpress.com/w/wholesale-"
            f"{quote_plus(keyword.replace(' ', '-'))}.html"
        )

        try:

            response = self.session.get(
                url,
                headers=self._headers(),
                timeout=(5, 20)
            )

            html = response.text

            patterns = [

                r'/item/(\d+)\.html',

                r'"productId":"(\d+)"',

                r'"itemId":"(\d+)"',

                r'"product_id":"(\d+)"',

                r'"tradeItemId":"(\d+)"',

                r'"productId":(\d+)',

                r'"itemId":(\d+)'
            ]

            ids = []

            for pattern in patterns:

                found = re.findall(
                    pattern,
                    html
                )

                if found:
                    ids.extend(found)

            ids = list(dict.fromkeys(ids))

            ids = [
                pid for pid in ids
                if len(pid) >= 8
            ]

            ids = ids[:10]

            logger.info(
                f"{len(ids)} IDs encontrados"
            )

            products = []

            for pid in ids:

                try:

                    product = (
                        self._scrape_product_page(
                            pid
                        )
                    )

                    if product:
                        products.append(product)

                    time.sleep(
                        random.uniform(
                            1.0,
                            2.0
                        )
                    )

                except Exception as e:

                    logger.warning(
                        f"Erro produto {pid}: {e}"
                    )

            return products

        except Exception as e:

            logger.exception(
                f"Erro scraping: {e}"
            )

            return []

    def _scrape_product_page(
        self,
        product_id: str
    ) -> Optional[Product]:

        url = (
            f"https://pt.aliexpress.com/item/"
            f"{product_id}.html"
        )

        response = self.session.get(
            url,
            headers=self._headers(),
            timeout=(5, 20)
        )

        html = response.text

        if (
            "captcha" in html.lower()
            or "punish" in html.lower()
        ):

            logger.warning(
                f"Bloqueio detectado: {product_id}"
            )

            return None

        data = self._extract_product_data(
            html
        )

        if not data:

            logger.warning(
                f"Dados não encontrados: {product_id}"
            )

            return None

        title = data.get("title")

        image = data.get("image")

        price = data.get("price")

        if not title:
            return None

        if not image:
            return None

        if not price:
            return None

        sold_count = data.get(
            "sold_count",
            0
        )

        rating = data.get(
            "rating",
            0.0
        )

        shipping = data.get(
            "shipping",
            ""
        )

        is_choice = data.get(
            "is_choice",
            False
        )

        product = Product(
            id=product_id,
            title=title,
            url=url,
            price_value=price,
            image=image,
            sold_count=sold_count,
            rating=rating,
            shipping=shipping,
            is_choice=is_choice,
            raw=data
        )

        product.score = (
            self._calculate_score(
                product
            )
        )

        return product

    def _extract_product_data(
        self,
        html: str
    ) -> Optional[Dict]:

        title = self._extract_title_html(
            html
        )

        image = self._extract_image_html(
            html
        )

        price = self._extract_price_html(
            html
        )

        sold_count = self._extract_sold_html(
            html
        )

        rating = self._extract_rating_html(
            html
        )

        is_choice = (
            self._detect_choice(
                html
            )
        )

        if not title:
            return None

        if not image:
            return None

        if price <= 0:
            return None

        return {
            "title": title,
            "image": image,
            "price": price,
            "sold_count": sold_count,
            "rating": rating,
            "shipping": "Frete disponível",
            "is_choice": is_choice,
        }

    def _extract_title_html(
        self,
        html: str
    ) -> str:

        patterns = [

            r'<title>(.*?)</title>',

            r'"subject":"(.*?)"',

            r'"title":"(.*?)"',

            r'property="og:title"\s+content="(.*?)"',
        ]

        for pattern in patterns:

            try:

                match = re.search(
                    pattern,
                    html,
                    re.S | re.I
                )

                if match:

                    title = (
                        match.group(1)
                        .replace("\\u0026", "&")
                        .replace("&amp;", "&")
                        .strip()
                    )

                    title = re.sub(
                        r'\s+',
                        ' ',
                        title
                    )

                    if len(title) >= 10:
                        return title

            except Exception:
                continue

        return ""

    def _extract_image_html(
        self,
        html: str
    ) -> str:

        patterns = [

            r'property="og:image"\s+content="(.*?)"',

            r'"imagePathList":\["(.*?)"',

            r'"imageUrl":"(.*?)"',

            r'"image":"(https://.*?)"',
        ]

        for pattern in patterns:

            try:

                match = re.search(
                    pattern,
                    html,
                    re.S | re.I
                )

                if match:

                    image = (
                        match.group(1)
                        .replace("\\/", "/")
                    )

                    if image.startswith("//"):
                        image = "https:" + image

                    return image

            except Exception:
                continue

        return ""

    def _extract_price_html(
        self,
        html: str
    ) -> float:

        patterns = [

            r'"formatedActivityPrice":"([^"]+)"',

            r'"formatedPrice":"([^"]+)"',

            r'"salePrice":"([^"]+)"',

            r'R\$\s?([\d.,]+)',
        ]

        for pattern in patterns:

            try:

                match = re.search(
                    pattern,
                    html,
                    re.S | re.I
                )

                if not match:
                    continue

                value = match.group(1)

                value = re.sub(
                    r"[^\d,.]",
                    "",
                    value
                )

                value = (
                    value
                    .replace(".", "")
                    .replace(",", ".")
                )

                price = float(value)

                if price > 0:
                    return price

            except Exception:
                continue

        return 0.0

    def _extract_sold_html(
        self,
        html: str
    ) -> int:

        patterns = [

            r'"tradeCount":"(\d+)"',

            r'"formatTradeCount":"([^"]+)"',

            r'(\d+)\s+vendidos',
        ]

        for pattern in patterns:

            try:

                match = re.search(
                    pattern,
                    html,
                    re.S | re.I
                )

                if match:

                    digits = re.sub(
                        r"[^\d]",
                        "",
                        match.group(1)
                    )

                    if digits:
                        return int(digits)

            except Exception:
                continue

        return 0

    def _extract_rating_html(
        self,
        html: str
    ) -> float:

        patterns = [

            r'"averageStar":"([^"]+)"',

            r'"feedbackRating":"([^"]+)"',

            r'([\d.]+)\s+estrelas',
        ]

        for pattern in patterns:

            try:

                match = re.search(
                    pattern,
                    html,
                    re.S | re.I
                )

                if match:

                    rating = float(
                        match.group(1)
                        .replace(",", ".")
                    )

                    return rating

            except Exception:
                continue

        return 0.0

    def _detect_choice(
        self,
        html: str
    ) -> bool:

        html_lower = html.lower()

        indicators = [

            "choice",

            "choice day",

            "aliexpress choice"
        ]

        return any(
            indicator in html_lower
            for indicator in indicators
        )

    def _calculate_score(
        self,
        product: Product
    ) -> int:

        score = 0

        if product.is_choice:
            score += 3

        if product.rating >= 4.8:
            score += 3

        elif product.rating >= 4.5:
            score += 2

        elif product.rating >= 4.0:
            score += 1

        if product.sold_count >= 5000:
            score += 3

        elif product.sold_count >= 1000:
            score += 2

        elif product.sold_count >= 100:
            score += 1

        if product.price_value <= 150:
            score += 1

        if product.shipping:
            score += 1

        return score
