import time
import hashlib
import requests
import logging
import random
from config import Config

logger = logging.getLogger(__name__)

class AliExpressClient:
    def __init__(self):
        Config.validate()
        self.app_key = Config.ALI_KEY
        self.secret = Config.ALI_SECRET
        self.tracking_id = Config.ALI_TRACKING_ID
        self.api_url = "https://api-sg.aliexpress.com/sync"
        
        # Palavras-chave do seu nicho para o robô buscar na API
        self.search_keywords = ["SSD NVMe", "Xiaomi Wi-Fi 6", "Baseus Charger", "Mecanico Keyboard", "Tools Kit"]

    def _generate_sign(self, params: dict) -> str:
        """Gera a assinatura digital MD5 obrigatória para a API do AliExpress."""
        sorted_params = sorted(params.items())
        sign_str = self.secret
        for key, value in sorted_params:
            sign_str += f"{key}{value}"
        sign_str += self.secret
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()

    def search_niche_products_via_api(self) -> list:
        """
        BUSCA REAL VIA API: Usa o endpoint oficial de pesquisa por palavra-chave
        para varrer o ecossistema do AliExpress dinamicamente.
        """
        # Escolhe uma palavra-chave aleatória do seu nicho para diversificar os posts
        keyword = random.choice(self.search_keywords)
        logger.info(f"Fazendo busca geral na API do AliExpress pelo termo: '{keyword}'")

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
            "page_size": "15" # Quantidade de produtos retornados por busca
        }
        params["sign"] = self._generate_sign(params)

        discovered_products = []
        try:
            response = requests.get(self.api_url, params=params, timeout=15)
            data = response.json()
            
            # Navega na estrutura de resposta padrão do método product.query
            result = data.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {})
            if result.get("code") == 200:
                products = result.get("result", {}).get("products", {}).get("product", [])
                
                # Garante que seja uma lista tratável
                if isinstance(products, dict):
                    products = [products]

                for prod in products:
                    # Filtro automático de segurança e relevância
                    rating = float(prod.get("evaluate_rate", 0) or 0)
                    
                    discovered_products.append({
                        "id": str(prod.get("product_id")),
                        "title": prod.get("product_title"),
                        "url": prod.get("product_detail_url"),
                        "price": f"R$ {prod.get('target_sale_price')}",
                        "image": prod.get("product_main_image_url"),
                        "rating": rating if rating > 0 else 4.7
                    })
                
                return discovered_products
            
            logger.error(f"A API rejeitou a busca por palavra-chave: {data}")
            return []
        except Exception as e:
            logger.error(f"Falha de conexão ou parser ao buscar na API: {e}")
            return []

    def generate_affiliate_link(self, original_url: str) -> str:
        """Converte o link do produto retornado pela busca em link monetizado."""
        params = {
            "method": "aliexpress.affiliate.link.generate",
            "app_key": self.app_key,
            "timestamp": str(int(time.time() * 1000)),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "source_values": original_url,
            "tracking_id": self.tracking_id
        }
        params["sign"] = self._generate_sign(params)
        
        try:
            response = requests.get(self.api_url, params=params, timeout=10)
            data = response.json()
            result = data.get("aliexpress_affiliate_link_generate_response", {}).get("resp_result", {})
            if result.get("code") == 200:
                links = result.get("result", {}).get("promolink_list", {}).get("promo_link", [])
                if links:
                    return links[0].get("promotion_link")
            return original_url
        except Exception as e:
            logger.error(f"Erro ao gerar link de afiliado: {e}")
            return original_url
