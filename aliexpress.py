import time
import hashlib
import requests
import logging
import re
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
        """Converte um link normal em link de afiliado monetizado."""
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

    def fetch_live_product_details(self, product_id: str) -> dict:
        """
        APRIMORAMENTO: Busca o preço real, título e imagem direto da API do AliExpress.
        Elimina a necessidade de colocar preços fixos no código.
        """
        params = {
            "method": "aliexpress.affiliate.product.detail.get",
            "app_key": self.app_key,
            "timestamp": str(int(time.time() * 1000)),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "product_ids": product_id,
            "target_currency": "BRL",
            "target_language": "PT"
        }
        params["sign"] = self._generate_sign(params)

        try:
            response = requests.get(self.api_url, params=params, timeout=12)
            data = response.json()
            
            result = data.get("aliexpress_affiliate_product_detail_get_response", {}).get("resp_result", {})
            if result.get("code") == 200:
                products = result.get("result", {}).get("products", {}).get("product", [])
                if products:
                    prod = products[0]
                    return {
                        "id": product_id,
                        "title": prod.get("product_title"),
                        "url": prod.get("product_detail_url"),
                        "price": f"R$ {prod.get('target_sale_price')}", 
                        "image": prod.get("product_main_image_url"),
                        "success": True
                    }
            logger.warning(f"Não foi possível obter detalhes reais para o ID {product_id}. Resposta: {data}")
            return {"success": False}
        except Exception as e:
            logger.error(f"Falha de conexão ao obter detalhes do produto {product_id}: {e}")
            return {"success": False}

    def get_target_product_ids(self) -> list:
        """
        MELHORIA: Lista de IDs de PRODUTOS REAIS e altamente desejados no Brasil.
        O bot vai usar esses IDs para buscar os dados atualizados dinamicamente.
        """
        return [
            "1005005963503541",  # SSD NVMe Fanxiang S500 Pro
            "1005006093855502",  # Roteador Xiaomi AX3000T
            "1005005118556412",  # Parafusadeira Elétrica Xiaomi Mijia
            "1005006001097262",  # Fone de Ouvido Anker Soundcore Q20i
            "1005006161474962"   # Carregador Baseus 65W GaN Fast Charger
        ]
