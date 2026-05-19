import logging
import httpx
import hashlib
import datetime
import urllib.parse
from dataclasses import dataclass
from config import Config

logger = logging.getLogger(__name__)

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
        # Endpoint unificado de chamadas de API do AliExpress
        self.endpoint = "https://api-sg.aliexpress.com/sync"
        self.app_key = Config.ALI_KEY
        self.secret = Config.ALI_SECRET
        self.tracking_id = Config.ALI_TRACKING_ID

    def _sign_request(self, params: dict) -> str:
        """ 
        Algoritmo Oficial de Assinatura (Signature) do AliExpress TopClient.
        Concatena o Secret com os parâmetros ordenados alfabeticamente e gera um Hash MD5.
        """
        sorted_keys = sorted(params.keys())
        sign_str = self.secret + "".join(f"{k}{params[k]}" for k in sorted_keys) + self.secret
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()

    def _build_params(self, method: str, business_params: dict) -> dict:
        """ Monta o payload padrão exigido pela plataforma """
        params = {
            "method": method,
            "app_key": self.app_key,
            "sign_method": "md5",
            "timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "v": "2.0",
            "format": "json"
        }
        params.update(business_params)
        params["sign"] = self._sign_request(params)
        return params

    async def search_products(self, keyword: str):
        """ Busca os produtos usando a API Oficial (Zero Risco de Ban/404) """
        method = "aliexpress.affiliate.product.query"
        business_params = {
            "keywords": keyword,
            "target_currency": "BRL",
            "target_language": "PT",
            "tracking_id": self.tracking_id,
            "sort": "SALE_PRICE_ASC" 
        }
        
        params = self._build_params(method, business_params)
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                # O Ali exige form-urlencoded para o envio dos parâmetros
                response = await client.post(self.endpoint, data=params)
                response.raise_for_status()
                data = response.json()
                
                # Tratamento de permissão negada caso sua conta não tenha sido aprovada ainda
                if "error_response" in data:
                    code = data["error_response"].get("code", "Erro Desconhecido")
                    msg = data["error_response"].get("msg", "")
                    logger.error(f"❌ API Oficial recusou o acesso ({code}): {msg}")
                    return []
                    
                return self._parse_api_response(data, keyword)
                
            except Exception as e:
                logger.error(f"❌ Erro de conexão com a API Oficial ao buscar {keyword}: {e}")
                return []

    def _parse_api_response(self, data: dict, keyword: str):
        """ Faz o parse do JSON oficial retornando nossos objetos Product """
        results = []
        try:
            products = data.get("aliexpress_affiliate_product_query_response", {}) \
                           .get("resp_result", {}) \
                           .get("result", {}) \
                           .get("products", {}) \
                           .get("product", [])
                           
            for item in products:
                prod_id = str(item.get("product_id", ""))
                title = item.get("product_title", "")
                
                # A grande vantagem da API: O link já vem monetizado no campo promotion_link!
                url = item.get("promotion_link", item.get("product_url", ""))
                image = item.get("product_main_image_url", "")
                
                try:
                    price = float(item.get("target_sale_price", 0))
                    sales = int(item.get("last_month_volume", 0))
                    # A API as vezes retorna o rating como string. Ex: "4.8"
                    rating_str = str(item.get("evaluate_rate", "0")).replace("%", "")
                    rating = float(rating_str)
                    
                    # Correção se a API retornar percentual (ex: 98 em vez de 4.9)
                    if rating > 5:
                        rating = rating / 20.0 
                except ValueError:
                    continue
                    
                # 🛡️ FILTRO CORPORATIVO
                if rating < 4.5 or sales < 50 or price <= 0:
                    continue
                
                score = (sales * (rating / 5.0))
                
                results.append(Product(
                    id=prod_id, title=title, url=url, image=image,
                    price_value=price, sold_count=sales, rating=rating,
                    keyword=keyword, score=score
                ))
                
            logger.info(f"✅ {len(results)} ofertas extraídas via API Oficial para '{keyword}'")
            return sorted(results, key=lambda x: x.score, reverse=True)
            
        except Exception as e:
            logger.error(f"Erro ao ler os dados da API Oficial: {e}")
            return []

    def generate_affiliate_link(self, product_url: str) -> str:
        """ 
        Na API Oficial nova, o método product.query já retorna o link comissionado.
        Mas mantemos este método ativo caso o bot tente repassar um link isolado.
        """
        method = "aliexpress.affiliate.link.generate"
        business_params = {
            "promotion_link_type": "0",
            "source_values": product_url,
            "tracking_id": self.tracking_id
        }
        params = self._build_params(method, business_params)
        
        # Mantido síncrono para não quebrar a estrutura do bot.py que você já tem rodando
        with httpx.Client(timeout=10.0) as client:
            try:
                response = client.post(self.endpoint, data=params)
                data = response.json()
                
                links = data.get("aliexpress_affiliate_link_generate_response", {}) \
                            .get("resp_result", {}) \
                            .get("result", {}) \
                            .get("promoted_links", {}) \
                            .get("promoted_link", [])
                            
                if links and len(links) > 0:
                    return links[0].get("promotion_link", product_url)
            except Exception as e:
                logger.error(f"Erro ao forçar geração do link afiliado: {e}")
                
        return product_url
