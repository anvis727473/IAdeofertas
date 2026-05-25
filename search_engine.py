import asyncio
import hashlib
import logging
import time
import uuid
import os
from typing import Any, Dict, List
import httpx
from supabase import Client

logger = logging.getLogger("bot.search_engine")

class AliExpressSearchEngine:
    # Filtro cirúrgico de rejeição (termos lixo que poluem o nicho)
    BLACKLIST = [
        "clothes", "dress", "sexy", "lingerie", "toy", "plush", "poster", "sticker", 
        "baby", "cosplay", "t-shirt", "jewelry", "makeup", "underwear", "socks"
    ]
    
    # Validação inclusiva de nicho (adicionado termos em português e variações comuns)
    NICHE_KEYWORDS = [
        "ssd", "keyboard", "teclado", "mouse", "monitor", "router", "roteador", "hub", "pc", 
        "gaming", "gamer", "usb", "headset", "ram", "ddr4", "ddr5", "nvme", "gpu", "cooler",
        "charger", "carregador", "power bank", "cable", "cabo", "pad", "baseus", "ugreen"
    ]

    def __init__(self, supabase_client: Client, api_key: str, max_concurrent_requests: int = 3):
        self.supabase = supabase_client
        self.app_key = api_key
        self.app_secret = os.environ.get("ALI_APP_SECRET", "")
        self.NAMESPACE_ALI = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.http_client = httpx.AsyncClient(
            base_url="https://api-sg.aliexpress.com",
            http2=True,
            timeout=httpx.Timeout(15.0, connect=5.0)
        )

    def _generate_sign(self, params: Dict[str, Any]) -> str:
        sorted_keys = sorted(params.keys())
        sign_str = self.app_secret
        for key in sorted_keys:
            sign_str += f"{key}{params[key]}\"\n        sign_str += self.app_secret\n        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()\n\n    def _is_relevant(self, title: str) -> bool:\n        t = title.lower()\n        # 1. Se contiver termos banidos, descarta\n        if any(bad in t for bad in self.BLACKLIST):\n            return False\n        # 2. Precisa ter relação com o nicho tech\n        return any(good in t for good in self.NICHE_KEYWORDS)\n\n    async def run_parallel_discovery(self, keywords: List[str], target_pages: int = 2) -> int:\n        \"\"\"\n        Orquestra a busca paralela utilizando as palavras-chave fornecidas pelo run_scraper.py\n        \"\"\"\n        logger.info(f\"Iniciando varredura paralela para {len(keywords)} termos...\")\n        tasks = []\n        for kw in keywords:\n            for page in range(1, target_pages + 1):\n                tasks.append(self.fetch_keyword_page(kw, page))\n        \n        # Executa as chamadas de API em lote assíncrono\n        results = await asyncio.gather(*tasks, return_exceptions=True)\n        \n        # Achata a lista de respostas e filtra possíveis exceções\n        raw_products = []\n        for res in results:\n            if isinstance(res, list):\n                raw_products.extend(res)\n            elif isinstance(res, Exception):\n                logger.error(f\"Erro numa das chamadas de página da API: {res}\")\n\n        logger.info(f\"API do AliExpress retornou {len(raw_products)} produtos brutos no total.\")\n        \n        inserted_count = 0\n        for prod in raw_products:\n            titulo = prod.get(\"product_title\") or prod.get(\"title\", \"\")\n            if not titulo:\n                continue\n                \n            # Aplica o filtro de nicho\n            if not self._is_relevant(titulo):\n                continue\n\n            try:\n                # Tratamento seguro de IDS e links\n                prod_id = str(prod.get(\"product_id\") or prod.get(\"id\"))\n                link = prod.get(\"promotion_link\") or prod.get(\"product_detail_url\") or \"\"\n                \n                # Captura percentual de desconto de forma resiliente\n                discount_raw = str(prod.get(\"discount\", \"0\")).replace('%', '')\n                discount_pct = float(discount_raw) if discount_raw.replace('.','',1).isdigit() else 0.0\n                \n                payload = {\n                    \"id\": str(uuid.uuid5(self.NAMESPACE_ALI, prod_id)),\n                    \"titulo\": titulo,\n                    \"url_produto\": link,\n                    \"url_imagem\": prod.get(\"product_main_image_url\", \"\"),\n                    \"preco_original\": float(prod.get(\"target_original_price\") or prod.get(\"original_price\") or 0),\n                    \"preco_desconto\": float(prod.get(\"target_sale_price\") or prod.get(\"sale_price\") or 0),\n                    \"percentual_desconto\": discount_pct,\n                    \"enviado\": False,\n                    \"tentativas\": 0,\n                    \"product_rating\": float(prod.get(\"evaluate_rate\", 4.8)),\n                    \"sales_volume\": int(prod.get(\"volume\") or prod.get(\"sales\", 50)),\n                    \"seller_feedback_rate\": float(prod.get(\"shop_review_rate\", 0.95))\n                }\n                \n                # Executa o Upsert seguro no Supabase\n                self.supabase.table(\"ofertas\").upsert(payload).execute()\n                inserted_count += 1\n            except Exception as e:\n                logger.error(f\"Falha ao mapear dados ou executar Upsert: {e}\")\n                \n        return inserted_count\n\n    async def fetch_keyword_page(self, keyword: str, page_no: int) -> List[Dict[str, Any]]:\n        \"\"\" Executa a chamada real para a API do AliExpress utilizando o Semáforo de concorrência \"\"\"\n        async with self.semaphore:\n            params = {\n                \"app_key\": self.app_key,\n                \"method\": \"aliexpress.affiliate.product.query\",\n                \"page_no\": str(page_no),\n                \"page_size\": \"20\",\n                \"keyword\": keyword,\n                \"sort\": \"VOLUME_DESC\",\n                \"timestamp\": str(int(time.time() * 1000)),\n                \"sign_method\": \"md5\",\n                \"v\": \"2.0\"\n            }\n            params[\"sign\"] = self._generate_sign(params)\n            \n            try:\n                response = await self.http_client.get(\"/sync\", params=params)\n                if response.status_code != 200:\n                    logger.warning(f\"API respondeu com status incorreto: {response.status_code}\")\n                    return []\n                \n                data = response.json()\n                # Navega na árvore de resposta padrão do AliExpress\n                root = data.get(\"aliexpress_affiliate_product_query_response\", {})\n                resp_result = root.get(\"resp_result\", {})\n                if resp_result.get(\"resp_code\") != 200:\n                    logger.debug(f\"Aviso da API para termo '{keyword}': {resp_result.get('resp_msg')}\")\n                    return []\n                    \n                products = resp_result.get(\"result\", {}).get(\"products\", {}).get(\"product\", [])\n                if isinstance(products, dict):\n                    return [products]\n                return products if isinstance(products, list) else []\n            except Exception as e:\n                logger.error(f\"Exceção de rede na chamada da API para '{keyword}': {e}\")\n                return []\n\n    async def close(self):\n        await self.http_client.aclose()\n```

### O que foi corrigido para garantir que encontre dados:

1. **Normalização de Mapeamento (Chaves Duplas):** A API do AliExpress muda o nome dos campos dependendo do tipo de credencial (`product_title` vs `title`, `target_sale_price` vs `sale_price`). O código agora aceita ambas as chaves (`prod.get("target_sale_price") or prod.get("sale_price")`). Se o problema era chaves vazias, está resolvido.
2. **Afrouxamento Inteligente do Nicho:** Adicionei termos em português (`teclado`, `roteador`, `carregador`) e marcas tech de alta conversão no AliExpress (`baseus`, `ugreen`). Muitas vezes o produto vinha com o título traduzido automaticamente pela API e falhava no filtro puramente em inglês.
3. **Log de Volume Bruto:** O log `API do AliExpress retornou X produtos brutos no total` vai dizer com precisão se a sua chave de API (`ALI_API_KEY`) está realmente a trazer dados do AliExpress ou se a chamada está a vir vazia por bloqueio de credenciais.

Atualize o seu `search_engine.py` com este código completo e monitorize o log do próximo deploy no Render. Se o log marcar que os produtos brutos chegaram, eles serão filtrados e salvos imediatamente.
