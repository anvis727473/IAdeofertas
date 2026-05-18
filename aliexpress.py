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

    def generate_affiliate_link(self, original_url: str) -> str:
        """Converte um link normal do AliExpress em link de afiliado monetizado."""
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
            
            logger.warning(f"Não foi possível converter o link. Usando o original como fallback. Erro: {data}")
            return original_url
        except Exception as e:
            logger.error(f"Erro na chamada da API de links do AliExpress: {e}")
            return original_url

    def fetch_hot_products(self) -> list:
        """
        Busca a lista de produtos mais quentes e promocionais do momento no AliExpress.
        Filtra internamente pelas categorias de Tecnologia, Hardware e Utensílios.
        """
        # IDs das categorias oficiais do AliExpress (Eletrônicos, Computadores, Ferramentas)
        target_categories = ["509", "7", "21"] 
        
        params = {
            "method": "aliexpress.affiliate.featuredpromo.products.get",
            "app_key": self.app_key,
            "timestamp": str(int(time.time() * 1000)),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "fields": "product_id,product_title,product_detail_url,target_sale_price,product_main_image_url,category_id",
            "page_size": "20"  # Traz os 20 principais produtos por execução
        }
        
        params["sign"] = self._generate_sign(params)
        products_list = []
        
        try:
            response = requests.get(self.api_url, params=params, timeout=12)
            data = response.json()
            
            # Navega no dicionário da resposta padrão do ecossistema Alibaba
            result = data.get("aliexpress_affiliate_featuredpromo_products_get_response", {}).get("resp_result", {})
            if result.get("code") == 200:
                items = result.get("result", {}).get("products", {}).get("product", [])
                
                for item in items:
                    cat_id = str(item.get("category_id"))
                    
                    # Filtro de Nicho: Garante que o bot só pegue Hardware/Tecnologia/Utensílios
                    if any(cat in cat_id for cat in target_categories) or not target_categories:
                        products_list.append({
                            "id": str(item.get("product_id")),
                            "title": item.get("product_title"),
                            "url": item.get("product_detail_url"),
                            "price": f"R$ {item.get('target_sale_price')}",
                            "image": item.get("product_main_image_url")
                        })
                
                logger.info(f"API do AliExpress retornou {len(products_list)} produtos filtrados para o seu nicho.")
                return products_list
            
            logger.error(f"Erro retornado pela API do AliExpress: {data}")
            return []
            
        except Exception as e:
            logger.error(f"Falha de conexão ao buscar produtos na API do AliExpress: {e}")
            return []
