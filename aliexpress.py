# aliexpress.py

import hashlib
import logging
import random
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

    shipping: str = ""

    is_choice: bool = False

    score: int = 0

    keyword: str = ""

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

        self.session = requests.Session()

        retries = Retry(
            total=5,
            connect=5,
            read=5,
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
            pool_connections=50,
            pool_maxsize=50
        )

        self.session.mount(
            "https://",
            adapter
        )

        self.search_keywords = [

            "SSD NVMe",

            "RTX 4060",

            "Mouse Gamer",

            "Teclado Mecânico",

            "Monitor Portátil",

            "Dock USB C",

            "Headset Bluetooth",

            "Smartwatch AMOLED",

            "Tablet Xiaomi",

            "Power Bank",

            "Mini PC",

            "Projetor 4K",

            "Câmera WiFi",

            "Hub USB C",

            "Parafusadeira Xiaomi",

            "Baseus Charger",

            "Controle Bluetooth",

            "Fone Bluetooth",

            "Carregador GaN",

            "Microfone USB"
        ]

    def _headers(self):

        user_agents = [

            (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),

            (
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/135.0 Safari/537.36"
            ),

            (
                "Mozilla/5.0 "
                "(X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/134.0 Safari/537.36"
            )
        ]

        return {

            "User-Agent":
                random.choice(user_agents),

            "Accept":
                "application/json,text/plain,*/*",

            "Accept-Language":
                "pt-BR,pt;q=0.9,en-US;q=0.8",

            "Origin":
                "https://pt.aliexpress.com",

            "Referer":
                "https://pt.aliexpress.com/",

            "Cache-Control":
                "no-cache",

            "Pragma":
                "no-cache"
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
                str(
                    int(time.time() * 1000)
                ),

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
                timeout=20
            )

            data = response.json()

            result = (
                data.get(
                    "aliexpress_affiliate_link_generate_response",
                    {}
                )
                .get(
                    "resp_result",
                    {}
                )
            )

            if result.get("code") == 200:

                links = (
                    result.get(
                        "result",
                        {}
                    )
                    .get(
                        "promolink_list",
                        {}
                    )
                    .get(
                        "promo_link",
                        []
                    )
                )

                if isinstance(
                    links,
                    dict
                ):
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

        try:

            url = (
                "https://pt.aliexpress.com/"
                "aeglodetailweb/api/"
                "search/searchProducts.htm"
            )

            params = {

                "SearchText":
                    keyword,

                "page":
                    1,

                "origin":
                    "y",

                "sortType":
                    "total_tranpro_desc",

                "pageSize":
                    20
            }

            response = self.session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=20
            )

            logger.info(
                f"Status API Busca: "
                f"{response.status_code}"
            )

            if response.status_code != 200:

                return []

            data = response.json()

            items = (
                data.get(
                    "mods",
                    {}
                )
                .get(
                    "itemList",
                    {}
                )
                .get(
                    "content",
                    []
                )
            )

            logger.info(
                f"{len(items)} produtos "
                f"brutos encontrados"
            )

            products = []

            for item in items:

                try:

                    product_id = str(
                        item.get(
                            "productId",
                            ""
                        )
                    )

                    if not product_id:
                        continue

                    title = (
                        item.get(
                            "title",
                            ""
                        )
                        .replace(
                            "\n",
                            " "
                        )
                        .strip()
                    )

                    if len(title) < 10:
                        continue

                    image = (
                        item.get(
                            "image",
                            ""
                        )
                    )

                    if image.startswith("//"):
                        image = (
                            "https:" + image
                        )

                    if not image:
                        continue

                    product_url = (
                        "https://pt.aliexpress.com/item/"
                        f"{product_id}.html"
                    )

                    prices = item.get(
                        "prices",
                        {}
                    )

                    sale_price = (
                        prices.get(
                            "salePrice",
                            {}
                        )
                    )

                    min_price = (
                        sale_price.get(
                            "minPrice",
                            ""
                        )
                    )

                    if not min_price:
                        continue

                    try:

                        price = float(
                            str(min_price)
                            .replace("R$", "")
                            .replace(".", "")
                            .replace(",", ".")
                            .strip()
                        )

                    except Exception:
                        continue

                    trade_desc = (
                        item.get(
                            "tradeDesc",
                            "0"
                        )
                    )

                    sold_count = 0

                    try:

                        sold_count = int(
                            (
                                trade_desc
                                .split()[0]
                                .replace(".", "")
                            )
                        )

                    except Exception:
                        pass

                    is_choice = (
                        item.get(
                            "deliveryExt",
                            {}
                        )
                        .get(
                            "displayTagType",
                            ""
                        )
                        == "choice"
                    )

                    rating = 0.0

                    try:

                        rating = float(
                            item.get(
                                "evaluation",
                                "0"
                            )
                        )

                    except Exception:
                        pass

                    shipping = (
                        "Frete grátis"
                        if is_choice
                        else ""
                    )

                    product = Product(

                        id=product_id,

                        title=title,

                        url=product_url,

                        image=image,

                        price_value=price,

                        sold_count=sold_count,

                        rating=rating,

                        shipping=shipping,

                        is_choice=is_choice,

                        keyword=keyword,

                        raw=item
                    )

                    product.score = (
                        self._calculate_score(
                            product
                        )
                    )

                    if product.score >= 2:

                        products.append(
                            product
                        )

                except Exception as e:

                    logger.warning(
                        f"Erro item: {e}"
                    )

            logger.info(
                f"{len(products)} produtos "
                f"válidos encontrados"
            )

            products.sort(
                key=lambda x: x.score,
                reverse=True
            )

            return products[:10]

        except Exception as e:

            logger.exception(
                f"Erro no garimpo: {e}"
            )

            return []

    def _calculate_score(
        self,
        product: Product
    ) -> int:

        score = 0

        if product.is_choice:
            score += 3

        if product.rating >= 4.9:
            score += 4

        elif product.rating >= 4.8:
            score += 3

        elif product.rating >= 4.5:
            score += 2

        elif product.rating >= 4.0:
            score += 1

        if product.sold_count >= 50000:
            score += 6

        elif product.sold_count >= 20000:
            score += 5

        elif product.sold_count >= 10000:
            score += 4

        elif product.sold_count >= 5000:
            score += 3

        elif product.sold_count >= 1000:
            score += 2

        elif product.sold_count >= 100:
            score += 1

        if product.price_value <= 300:
            score += 1

        return score
