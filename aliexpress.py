import logging
import httpx
import json
import re
import urllib.parse
from fake_useragent import UserAgent
from dataclasses import dataclass

logger = logging.getLogger(__name__)
# Rotação de User-Agent agressiva para simular navegadores reais
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
        # A nova rota acessa a página WEB ao invés da API cega para evitar o bloqueio (404) do Firewall
        self.base_url = "https://pt.aliexpress.com/w/wholesale-{}.html"

    async def search_products(self, keyword: str):
        headers = {
            "User-Agent": ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://pt.aliexpress.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
        
        # Formata a keyword ex: "Mouse Gamer" -> "Mouse-Gamer"
        kw_slug = keyword.replace(" ", "-")
        url = self.base_url.format(urllib.parse.quote(kw_slug)) + f"?SearchText={urllib.parse.quote_plus(keyword)}"
        
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                
                # Manda o HTML cru para o nosso extrator de estado
                return self._parse_ssr_html(response.text, keyword)
                
            except httpx.HTTPStatusError as e:
                logger.warning(f"⚠️ WAF bloqueou a página HTTP ({keyword}): Status {e.response.status_code}")
                return []
            except Exception as e:
                logger.error(f"❌ Erro de conexão ao buscar {keyword}: {e}")
                return []

    def _extract_json_from_html(self, html: str):
        """ Extrai o payload JSON renderizado no Servidor (SSR) embutido nas tags <script> """
        # O Ali costuma jogar o payload de dados inteiro em uma destas variáveis globais JS:
        markers = ["window._init_data_ = ", "window.runParams = ", "_init_data_ = "]
        
        for marker in markers:
            if marker in html:
                try:
                    parts = html.split(marker, 1)
                    json_str = parts[1].split("</script>")[0].strip()
                    
                    if json_str.endswith(";"):
                        json_str = json_str[:-1]
                        
                    # Remove sujeira JS após o final do JSON
                    idx = json_str.rfind('}')
                    if idx != -1:
                        json_str = json_str[:idx+1]
                        
                    return json.loads(json_str)
                except Exception:
                    continue
        return None

    def _parse_ssr_html(self, html: str, keyword: str):
        data = self._extract_json_from_html(html)
        
        if not data:
            logger.warning(f"⚠️ Anti-Bot ativo: Não foi possível achar os dados da página para '{keyword}'. O bot vai tentar novamente no próximo ciclo.")
            return []

        results = []
        try:
            items = []
            
            # Navega na hierarquia dos objetos JSON da Ali
            if "data" in data and "root" in data["data"]:
                items = data["data"]["root"].get("fields", {}).get("mods", {}).get("itemList", {}).get("content", [])
            elif "mods" in data and "itemList" in data["mods"]:
                items = data["mods"]["itemList"]["content"]
            else:
                logger.warning(f"⚠️ Estrutura de dados do Ali alterada para o nicho {keyword}")
                return []

            for item in items:
                # Trata as variações dos testes A/B que a AliExpress faz no front-end
                prod_info = item.get("item", item)
                
                prod_id = str(prod_info.get("productId") or prod_info.get("itemId", ""))
                if not prod_id:
                    continue
                
                # -- TÍTULO --
                title_obj = prod_info.get("title", {})
                title = title_obj.get("displayTitle") if isinstance(title_obj, dict) else str(title_obj)
                title = re.sub(r'<[^>]+>', '', title) # Limpa spans e strongs inseridos para highlight
                
                url = f"https://pt.aliexpress.com/item/{prod_id}.html"
                
                # -- IMAGEM --
                img_obj = prod_info.get("image", {})
                image = img_obj.get("imgUrl") if isinstance(img_obj, dict) else str(prod_info.get("imageUrl", ""))
                if image and str(image).startswith("//"):
                    image = "https:" + image
                
                # -- PREÇO --
                prices_obj = prod_info.get("prices", {})
                price_str = prices_obj.get("salePrice", {}).get("formattedPrice", "0") if isinstance(prices_obj, dict) else "0"
                if price_str == "0":
                    price_str = prod_info.get("price", "0")
                
                try:
                    clean_price = re.sub(r'[^\d,.]', '', str(price_str)).replace(",", ".")
                    price = float(clean_price)
                except ValueError:
                    continue
                
                # -- VENDAS --
                trade_obj = prod_info.get("trade", {})
                sales_str = trade_obj.get("tradeDesc", "0") if isinstance(trade_obj, dict) else str(prod_info.get("sales", "0"))
                sales_digits = re.sub(r'\D', '', sales_str)
                sales = int(sales_digits) if sales_digits.isdigit() else 0
                
                # -- SCORE / ESTRELAS --
                eval_obj = prod_info.get("evaluation", {})
                rating = float(eval_obj.get("starRating", 0)) if isinstance(eval_obj, dict) else float(prod_info.get("rating", 0))

                # 🛡️ FILTRO CORPORATIVO
                if rating < 4.5 or sales < 100 or price <= 0:
                    continue
                
                score = (sales * (rating / 5.0))
                
                results.append(Product(
                    id=prod_id, title=title, url=url, image=image, 
                    price_value=price, sold_count=sales, rating=rating, 
                    keyword=keyword, score=score
                ))
                
            logger.info(f"✅ {len(results)} ofertas Premium filtradas (Score aprovado) para '{keyword}'")
            return sorted(results, key=lambda x: x.score, reverse=True)

        except Exception as e:
            logger.exception(f"❌ Falha ao processar as métricas para '{keyword}': {e}")
            return []

    def generate_affiliate_link(self, product_url: str) -> str:
        """ Implementação do Gerador Afiliado """
        encoded_url = urllib.parse.quote_plus(product_url)
        # Como ajustado anteriormente na API híbrida
        return f"https://s.click.aliexpress.com/e/_dummy?url={encoded_url}"
