import requests
import hashlib
import time
from config import Config

class AliExpressClient:
    def __init__(self):
        self.app_key = Config.ALI_KEY
        self.secret = Config.ALI_SECRET
        self.tracking_id = Config.ALI_TRACKING_ID

    def generate_affiliate_link(self, original_url: str) -> str:
        """
        Gera o link de afiliado oficial do AliExpress a partir de uma URL padrão.
        Se as credenciais não estiverem prontas, usa um fallback estruturado.
        """
        if not self.app_key or not self.secret:
            print("Aviso: ALI_KEY ou ALI_SECRET não definidos. Usando link estruturado de fallback.")
            return f"{original_url}?aff_platform=api-new&sk=fallback_sk&tracking_id={self.tracking_id}"

        # URL base do endpoint oficial da API AliExpress Link Generate
        api_url = "https://eco.taobao.com/router/rest"
        
        params = {
            "method": "aliexpress.affiliate.link.generate",
            "app_key": self.app_key,
            "sign_method": "md5",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "format": "json",
            "v": "2.0",
            "promotion_link_type": "0",
            "source_values": original_url,
            "tracking_id": self.tracking_id
        }
        
        # Geração da assinatura MD5 requerida pelo ecossistema Alibaba
        params["sign"] = self._generate_sign(params)
        
        try:
            response = requests.get(api_url, params=params, timeout=10)
            data = response.json()
            
            # Parsing do JSON de resposta oficial da API
            res_wrapper = data.get('aliexpress_affiliate_link_generate_response', {})
            res_result = res_wrapper.get('resp_result', {})
            result_data = res_result.get('result', {})
            links_list = result_data.get('promotion_links', {}).get('promotion_link', [])
            
            if links_list:
                return links_list[0].get('promotion_link', original_url)
        except Exception as e:
            print(f"Erro ao processar chamada à API do AliExpress: {e}")
            
        return original_url

    def _generate_sign(self, params: dict) -> str:
        """Regra de criptografia MD5 para assinar as requisições do AliExpress."""
        sorted_params = sorted(params.items())
        sign_str = self.secret
        for k, v in sorted_params:
            sign_str += f"{k}{v}"
        sign_str += self.secret
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()
