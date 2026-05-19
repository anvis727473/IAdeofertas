# aliexpress.py

import hashlib
import json
import logging
import random
import re
import time

from dataclasses import dataclass, field, asdict
from typing import List
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
    image: str
    price_value: float

    sold_count: int = 0
    rating: float = 0.0

    is_choice: bool = False

    score: int = 0

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
            backoff_factor=1,
            status_forcelist=[
                429,
                500,
                502,
                503,
                504
            ]
        )

        adapter = HTTPAdapter(
            max_retries=retries,
            pool_connections=20,
            pool_maxsize=20
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
            "Accept-Language":
                "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept":
                "text/html,application/xhtml+xml",
            "Referer":
                "https://pt.aliexpress.com/",
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
            "method":
                "aliexpress.affiliate.link.generate",

            "app_key":
                self.app_key,

            "timestamp":
                str(int(time.time() * 1000)),

            "format":
                "json",

            "v":
                "2.0",

            "sign_method":
                "md5",

            "source_values":
                original_url,

            "tracking_id":
                self.tracking_id
        }

        params["sign"] = (
            self._generate_sign(params)
        )

        try:

            response = self.session.get(
                self.api_url,
                params=params,
                timeout=15
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
                "Erro afiliado"
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

        url = (
            "https://pt.aliexpress.com/w/wholesale-"
            f"{quote_plus(keyword)}.html"
        )

        try:

            response = self.session.get(
                url,
                headers=self._headers(),
                timeout=20
            )

            html = response.text

            products = (
                self._extract_products_from_html(
                    html
                )
            )

            logger.info(
                f"{len(products)} produtos encontrados"
            )

            return products

        except Exception as e:

            logger.exception(
                f"Erro scraping: {e}"
            )

            return []

    def _extract_products_from_html(
        self,
        html: str
    ) -> List[Product]:

        found_products = []

        seen = set()

        product_pattern = re.finditer(
            r'/item/(\d+)\.html',
            html
        )

        for match in product_pattern:

            try:

                product_id = match.group(1)

                if product_id in seen:
                    continue

                seen.add(product_id)

                start = max(
                    0,
                    match.start() - 2500
                )

                end = min(
                    len(html),
                    match.end() + 2500
                )

                block = html[start:end]

                title = self._extract_title(
                    block
                )

                image = self._extract_image(
                    block
                )

                price = self._extract_price(
                    block
                )

                sold = self._extract_sold(
                    block
                )

                if not title:
                    continue

                if not image:
                    continue

                if price <= 0:
                    continue

                product_url = (
                    "https://pt.aliexpress.com/item/"
                    f"{product_id}.html"
                )

                product = Product(
                    id=product_id,
                    title=title,
                    url=product_url,
                    image=image,
                    price_value=price,
                    sold_count=sold,
                    is_choice=(
                        "choice" in block.lower()
                    )
                )

                product.score = (
                    self._calculate_score(
                        product
                    )
                )

                found_products.append(
                    product
                )

                if len(found_products) >= 10:
                    break

            except Exception:
                continue

        return found_products

    def _extract_title(
        self,
        text: str
    ) -> str:

        patterns = [

            r'"title":"([^"]+)"',

            r'"displayTitle":"([^"]+)"',

            r'alt="([^"]+)"'
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.I
            )

            if match:

                title = (
                    match.group(1)
                    .replace("\\u0026", "&")
                    .replace("&amp;", "&")
                    .replace("\\", "")
                    .strip()
                )

                if len(title) >= 10:
                    return title

        return ""

    def _extract_image(
        self,
        text: str
    ) -> str:

        patterns = [

            r'https://[^"]+\.(jpg|jpeg|png|webp)',

            r'//[^"]+\.(jpg|jpeg|png|webp)'
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.I
            )

            if match:

                image = match.group(0)

                if image.startswith("//"):
                    image = "https:" + image

                return image

        return ""

    def _extract_price(
        self,
        text: str
    ) -> float:

        patterns = [

            r'R\$\s?([\d.,]+)',

            r'"price":"([\d.,]+)"',

            r'"salePrice":"([\d.,]+)"'
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text
            )

            if match:

                try:

                    value = (
                        match.group(1)
                        .replace(".", "")
                        .replace(",", ".")
                    )

                    return float(value)

                except Exception:
                    continue

        return 0.0

    def _extract_sold(
        self,
        text: str
    ) -> int:

        patterns = [

            r'(\d+)\s+vendidos',

            r'"tradeCount":"(\d+)"'
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.I
            )

            if match:

                try:
                    return int(match.group(1))
                except Exception:
                    pass

        return 0

    def _calculate_score(
        self,
        product: Product
    ) -> int:

        score = 0

        if product.is_choice:
            score += 3

        if product.sold_count >= 5000:
            score += 3

        elif product.sold_count >= 1000:
            score += 2

        elif product.sold_count >= 100:
            score += 1

        if product.price_value <= 150:
            score += 1

        return score
