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
                "pt-BR,pt;q=0.9"
            ),
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
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
                "Erro gerar link afiliado"
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
                            2.2
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

        data = self._extract_json_data(
            html
        )

        if not data:

            logger.warning(
                f"JSON não encontrado: {product_id}"
            )

            return None

        title = self._extract_title(data)

        image = self._extract_image(data)

        price = self._extract_price(data)

        if not title:
            return None

        if not image:
            return None

        if price <= 0:
            return None

        sold_count = (
            self._extract_sold(data)
        )

        rating = (
            self._extract_rating(data)
        )

        shipping = (
            self._extract_shipping(data)
        )

        is_choice = (
            self._detect_choice(
                html
            )
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

    def _extract_json_data(
        self,
        html: str
    ) -> Optional[Dict]:

        patterns = [

            r'window.runParams\s*=\s*(\{.*?\});',

            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',

            r'window\._dida_config_._init_data_=\s*(\{.*?\});',

            r'window\.rawData\s*=\s*(\{.*?\});'
        ]

        for pattern in patterns:

            try:

                match = re.search(
                    pattern,
                    html,
                    re.S
                )

                if not match:
                    continue

                raw_json = (
                    match.group(1)
                    .strip()
                )

                data = json.loads(
                    raw_json
                )

                if isinstance(data, dict):
                    return data

            except Exception:
                continue

        return None

    def _safe_get(
        self,
        data,
        keys
    ):

        current = data

        for key in keys:

            if isinstance(current, dict):

                current = current.get(key)

            else:

                return None

        return current

    def _extract_title(
        self,
        data: Dict
    ) -> str:

        paths = [

            ["titleModule", "subject"],

            ["metaDataComponent", "title"],

            ["pageModule", "title"],

            ["productInfoComponent", "subject"]
        ]

        for path in paths:

            value = self._safe_get(
                data,
                path
            )

            if value:

                return str(value).strip()

        return ""

    def _extract_image(
        self,
        data: Dict
    ) -> str:

        paths = [

            ["imageModule", "imagePathList"],

            ["imageModule", "images"],

            ["imageModule", "imageURLs"]
        ]

        for path in paths:

            value = self._safe_get(
                data,
                path
            )

            if (
                isinstance(value, list)
                and value
            ):

                image = str(value[0])

                if image.startswith("//"):
                    image = "https:" + image

                return image

        return ""

    def _extract_price(
        self,
        data: Dict
    ) -> float:

        paths = [

            ["priceModule", "formatedActivityPrice"],

            ["priceModule", "formatedPrice"],

            ["priceModule", "minActivityAmount"],

            ["priceModule", "minAmount"],

            ["priceComponent", "discountPrice"]
        ]

        for path in paths:

            value = self._safe_get(
                data,
                path
            )

            if not value:
                continue

            try:

                number = re.sub(
                    r"[^\d,.]",
                    "",
                    str(value)
                )

                number = (
                    number
                    .replace(".", "")
                    .replace(",", ".")
                )

                return float(number)

            except Exception:
                continue

        return 0.0

    def _extract_sold(
        self,
        data: Dict
    ) -> int:

        paths = [

            ["tradeComponent", "formatTradeCount"],

            ["tradeComponent", "tradeCount"],

            ["titleModule", "formatTradeCount"]
        ]

        for path in paths:

            value = self._safe_get(
                data,
                path
            )

            if not value:
                continue

            digits = re.sub(
                r"[^\d]",
                "",
                str(value)
            )

            if digits:

                try:
                    return int(digits)
                except Exception:
                    pass

        return 0

    def _extract_rating(
        self,
        data: Dict
    ) -> float:

        paths = [

            ["titleModule", "feedbackRating"],

            ["feedbackComponent", "evarageStar"],

            ["feedbackComponent", "averageStar"]
        ]

        for path in paths:

            value = self._safe_get(
                data,
                path
            )

            if not value:
                continue

            try:

                return float(
                    str(value)
                    .replace(",", ".")
                )

            except Exception:
                continue

        return 0.0

    def _extract_shipping(
        self,
        data: Dict
    ) -> str:

        paths = [

            [
                "webGeneralFreightCalculateComponent",
                "originalLayoutResultList"
            ]
        ]

        for path in paths:

            value = self._safe_get(
                data,
                path
            )

            if value:
                return "Frete disponível"

        return ""

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
