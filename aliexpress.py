import time
import hashlib
import requests
import logging
from config import Config

logger = logging.getLogger(__name__)

class AliExpressClient:
    def __init__(self):
        Config.validate()
        self.app_key = Config.ALI_KEY
        self.secret = Config.ALI_SECRET
        self.tracking_id = Config.ALI_TRACKING_ID
        self.api_url = "https://api-sg.aliexpress.com/sync"

    def _generate_sign(self, params: dict) -> str:
        """Gera a assinatura digital MD5 obrigatória para a API do AliExpress."""
        sorted_params = sorted(params.items())
        sign_str = self.secret
        for key, value in sorted_params:
            sign_str += f"{key}{value}"
        sign_str += self.secret
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()

    def discover_niche_products(self) -> list:
        """
        BUSCA AUTOMÁTICA: Explora o feed de produtos mais vendidos (Hot Products)
        filtrando por categorias de tecnologia/ferramentas e destino Brasil.
        """
        # IDs de Categorias Oficiais: 44 = Eletrônicos/Hardware, 1421 = Ferramentas/Utensílios
        category_ids = "44,1421" 
        
        params = {
            "method": "aliexpress.affiliate.hotproduct.query",
            "app_key": self.app_key,
            "timestamp": str(int(time.time() * 1000)),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "category_ids": category_ids,
            "target_currency": "BRL",
            "target_language": "PT",
            "ship_to_country": "BR", # Garante produtos que enviam para o Brasil
            "page_size": "20"        # Busca 20 produtos por ciclo
        }
        params["sign"] = self._generate_sign(params)

        discovered_products = []
        try:
            response = requests.get(self.api_url, params=params, timeout=15)
            data = response.json()
            
            result = data.get("aliexpress_affiliate_hotproduct_query_response", {}).get("resp_result", {})
            if result.get("code") == 200:
                products = result.get("result", {}).get("products", {}).get("product", [])
                
                if isinstance(products, dict):
                    products = [products]

                for prod in products:
                    # FILTRO DE CONFIANÇA: Só aceita produtos bem avaliados e com vendas
                    evaluate_rate = float(prod.get("evaluate_rate", 0))
                    volume = int(prod.get("first_level_order_count", 0)) # Vendas recentes
                    
                    if evaluate_rate >= 4.5 and volume > 10: # Filtro rígido de qualidade
                        discovered_products.append({
                            "id": str(prod.get("product_id")),
                            "title": prod.get("product_title"),
                            "url": prod.get("product_detail_url"),
                            "price": f"R$ {prod.get('target_sale_price')}",
                            "image": prod.get("product_main_image_url"),
                            "rating": evaluate_rate
                        })
                
                logger.info(f"Garimpo concluído: {len(discovered_products)} produtos confiáveis encontrados.")
                return discovered_products
                
            logger.error(f"Erro ao garimpar produtos no AliExpress: {data}")
            return []
        except Exception as e:
            logger.error(f"Falha de conexão no endpoint de garimpo: {e}")
            return []

    def generate_affiliate_link(self, original_url: str) -> str:
        """Converte o link do produto descoberto em link monetizado."""
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
