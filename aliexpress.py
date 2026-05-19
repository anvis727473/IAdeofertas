import logging
import httpx
import asyncio
import hashlib
import time
from fake_useragent import UserAgent
from dataclasses import dataclass
from config import Config

logger = logging.getLogger(__name__)
ua = UserAgent(fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36")

@dataclass
class Product:
    id: str
    title: str
    url: str
    image: str
    price_value: float
    sold_count: int
    rating: float
    keyword: str
    score: float

    def price_text(self):
        return f"R$ {self.price_value:.2f}"

class AliExpressClient:
    def __init__(self):
        self.endpoint_search = "https://pt.aliexpress.com/aeglodetailweb/api/search/searchProducts.htm"
        self.api_gateway = "https://api-sg.aliexpress.com/sync"

    async def search_products(self, keyword: str):
        headers = {
            "User-Agent": ua.random,
            "Accept": "application/json",
            "Referer": "https://pt.aliexpress.com/"
        }
        params = {"q": keyword, "page": 1, "sort": "VENDAS_DESC"}
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(self.endpoint_search, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
                return self._parse_and_filter(data, keyword)
            except httpx.HTTPStatusError as e:
                logger.warning(f"⚠️ Erro HTTP no garimpo ({keyword}): {e.response.status_code}")
                # Exponencial backoff implícito aguardando no bot.py
                return []
            except Exception as e:
                logger.error(f"❌ Erro na busca de {keyword}: {e}")
                return []

    def _parse_and_filter(self, data: dict, keyword: str):
        results = []
        try:
            items = data.get("data", {}).get("resultList", [])
            for item in items:
                item_info = item.get("item", {})
                
                # Campos básicos
                prod_id = str(item_info.get("itemId", ""))
                title = item_info.get("title", "")
                url = f"https://pt.aliexpress.com/item/{prod_id}.html"
                image = item_info.get("image", "")
                
                # Conversão e limpeza de valores
                try:
                    price_str = item_info.get("price", "0").replace("R$", "").replace(",", ".").strip()
                    price = float(price_str)
                    sales_str = item_info.get("sales", "0").replace("+", "").replace("vendidos", "").strip()
                    sales = int(sales_str) if sales_str.isdigit() else 0
                    rating = float(item_info.get("rating", 0))
                except ValueError:
                    continue
                
                # 🛡️ FILTRO ENTERPRISE (Evita postar lixo)
                if rating < 4.5 or sales < 100 or price <= 0:
                    continue
                
                # Algoritmo de Score Ponderado
                score = (sales * (rating / 5.0))
                
                results.append(Product(
                    id=prod_id, title=title, url=url, image=image, 
                    price_value=price, sold_count=sales, rating=rating, 
                    keyword=keyword, score=score
                ))
                
            # Ordena os produtos do melhor score para o pior
            return sorted(results, key=lambda x: x.score, reverse=True)
            
        except Exception as e:
            logger.error(f"Erro no parse: {e}")
            return []

    def generate_affiliate_link(self, product_url: str) -> str:
        """
        Implementação oficial baseada na documentação da API TopClient.
        Utiliza aliexpress.affiliate.link.generate.
        (Para simplificar no MVP e isolar a lógica, aqui simulamos o output esperado
        ou você injeta a lib top oficial do Ali aqui)
        """
        # Em produção, este método usa o ALI_KEY e ALI_SECRET para assinar o link via POST
        # Simulando o sucesso para manter a estrutura funcional:
        encoded_url = urllib.parse.quote_plus(product_url)
        return f"https://s.click.aliexpress.com/e/_dummy?url={encoded_url}"
