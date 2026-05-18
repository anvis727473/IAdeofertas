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
        # Usando o endpoint padrão global estável para Afiliados
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
            
            return original_url
        except Exception as e:
            logger.error(f"Erro na chamada da API de links do AliExpress: {e}")
            return original_url

    def get_monitored_products(self) -> list:
        """
        Retorna a lista de produtos de Hardware e Utensílios altamente lucrativos para monitorar.
        Você pode adicionar novos itens aqui mudando apenas o ID, Título, Link e Preço base.
        """
        return [
            {
                "id": "1005006123456",
                "title": "SSD NVMe M.2 Netac 1TB PCIe 4.0 - Alta Velocidade para PC",
                "url": "https://pt.aliexpress.com/item/1005006123456.html",
                "price": "R$ 289,90",
                "image": "https://images.unsplash.com/photo-1591488320449-011701bb6704?w=500"
            },
            {
                "id": "1005007111111",
                "title": "Roteador Xiaomi AX3000T Wi-Fi 6 - Inteligente de Alta Cobertura",
                "url": "https://pt.aliexpress.com/item/1005007111111.html",
                "price": "R$ 149,90",
                "image": "https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?w=500"
            },
            {
                "id": "1005005555555",
                "title": "Parafusadeira Elétrica Baseus de Precisão - Kit com 24 Bits",
                "url": "https://pt.aliexpress.com/item/1005005555555.html",
                "price": "R$ 119,00",
                "image": "https://images.unsplash.com/photo-1534224039826-c7a0dea0e66a?w=500"
            },
            {
                "id": "1005004444444",
                "title": "Teclado Mecânico Gamer Injetado RGB - Switch Azul/Marrom",
                "url": "https://pt.aliexpress.com/item/1005004444444.html",
                "price": "R$ 199,90",
                "image": "https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=500"
            }
        ]
