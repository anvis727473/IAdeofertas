"""
search_engine.py — Motor de Busca, Raspagem Semântica e Ingestão do Sniper de Ofertas
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx
from supabase import Client

logger = logging.getLogger("bot.search_engine")

class AliExpressSearchEngine:
    def __init__(self, supabase_client: Client, api_key: str, max_concurrent_requests: int = 5):
        """
        Inicializa o motor de busca assíncrono.
        """
        self.supabase = supabase_client
        self.api_key = api_key
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.base_url = "https://api-sg.aliexpress.com/sync"
        
        # Pool de conexões HTTPX configurado para alta performance no Render
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            headers={"Content-Type": "application/json"}
        )

    async def _execute_request_with_retry(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Executa requisições HTTP controladas por semáforos, aplicando backoff exponencial
        em cenários de Rate Limiting (HTTP 429).
        """
        async with self.semaphore:
            backoff = 2.0
            for attempt in range(4):
                try:
                    response = await self.http_client.get(self.base_url, params=params)
                    
                    if response.status_code == 429:
                        logger.warning("Rate limit (429) detectado na API do AliExpress. Aguardando %.1fs...", backoff)
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue
                        
                    response.raise_for_status()
                    return response.json()
                    
                except httpx.HTTPStatusError as exc:
                    logger.error("Erro de status HTTP no endpoint do AliExpress [%d]: %s", exc.response.status_code, exc)
                    if exc.response.status_code in [400, 401, 403]:
                        break  # Falhas estruturais não passíveis de nova tentativa imediata
                except (httpx.NetworkError, httpx.TimeoutException) as exc:
                    logger.warning("Falha temporária de rede na tentativa %d: %s", attempt + 1, exc)
                
                await asyncio.sleep(backoff)
                backoff *= 2.0
                
            return None

    def _calculate_real_discount(self, base_price: float, shipping: float, store_coupon: float, sma_30: float) -> Tuple[float, float]:
        """
        Calcula a Taxa de Desconto Real expurgando fretes maquiados.
        """
        landing_price = base_price + shipping - store_coupon
        if landing_price <= 0:
            landing_price = base_price
            
        if sma_30 <= 0:
            sma_30 = base_price
            
        real_discount_rate = 1.0 - (landing_price / sma_30)
        return landing_price, real_discount_rate

    async def _generate_text_embedding(self, text: str) -> List[float]:
        """
        Mock de geração de embeddings estruturados. Em ambiente de produção empresarial,
        conecta-se ao endpoint da OpenAI/HuggingFace para obter o vetor real de 1536 dimensões.
        """
        # Simulação estável de vetor normalizado para compatibilidade estrutural
        await asyncio.sleep(0.01)  # Simula latência de rede irrisória
        hash_val = sum(ord(c) for c in text)
        dummy_vector = [0.01 * ((hash_val + i) % 100) for i in range(1536)]
        magnitude = math.sqrt(sum(x**2 for x in dummy_vector))
        return [x / magnitude for x in dummy_vector]

    async def fetch_category_page(self, category_id: str, page: int) -> List[Dict[str, Any]]:
        """
        Busca os produtos de uma determinada categoria utilizando os parâmetros da API.
        """
        params = {
            "method": "aliexpress.affiliate.product.query",
            "app_key": self.api_key,
            "category_ids": category_id,
            "page_no": str(page),
            "page_size": "20",
            "sign_method": "md5"
        }
        
        raw_data = await self._execute_request_with_retry(params)
        if not raw_data:
            return []
            
        resp_node = raw_data.get("aliexpress_affiliate_product_query_response", {})
        return resp_node.get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])

    async def process_and_ingest_product(self, raw_product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Aplica as lógicas de arbitragem de preço, gera os embeddings contextuais
        e executa o Upsert atômico direto no banco de dados do Supabase.
        """
        try:
            product_id = str(raw_product["product_id"])
            title = raw_product["product_title"]
            base_price = float(raw_product["target_sale_price"])
            shipping = float(raw_product.get("target_shipping_fee", 0.0))
            
            # Executa busca histórica para calcular o desconto contra a linha de base real
            historical_res = self.supabase.table("ofertas").select("preco_medio_30_dias").eq("id", product_id).execute()
            sma_30 = float(historical_res.data[0]["preco_medio_30_dias"]) if historical_res.data else base_price
            
            floor_price, real_discount = self._calculate_real_discount(base_price, shipping, 0.0, sma_30)
            
            # Clusterização e Poda: Elimina ruídos de falsas promoções
            if real_discount < 0.15 and float(raw_product.get("evaluate_rate", 5.0)) < 4.7:
                return None
                
            # Geração do vetor semântico
            embedding_vector = await self._generate_text_embedding(f"{title} {raw_product.get('first_level_category_name', '')}")
            
            payload = {
                "id": product_id,
                "titulo": title,
                "url_produto": raw_product["product_detail_url"],
                "url_imagem": raw_product.get("product_main_image_url"),
                "preco_original": float(raw_product.get("target_original_price") or base_price),
                "preco_desconto": floor_price,
                "percentual_desconto": float(raw_product.get("discount", 0.0)),
                "product_rating": float(raw_product.get("evaluate_rate") or 5.0),
                "sales_volume": int(raw_product.get("volume", 0)),
                "seller_feedback_rate": float(raw_product.get("shop_positive_rate", "100").replace("%", "")) / 100.0,
                "embedding": embedding_vector,
                "enviado": False,
                "tentativas": 0,
                "atualizado_em": "now()"
            }
            
            # Ingestão idempotente via ON CONFLICT (id) DO UPDATE nativo do Supabase (Upsert)
            self.supabase.table("ofertas").upsert(payload, on_conflict="id").execute()
            return payload

        except Exception as exc:
            logger.error("Erro ao processar/ingestão do produto %s: %s", raw_product.get("product_id"), exc)
            return None

    async def run_parallel_discovery(self, category_ids: List[str], target_pages: int = 3) -> int:
        """
        Orquestra a busca paralela concorrente em múltiplas verticais e categorias da API.
        """
        logger.info("Iniciando descoberta semântica paralela para as categorias: %s", category_ids)
        
        # Criação da malha de tarefas de busca
        fetch_tasks = []
        for cat in category_ids:
            for page in range(1, target_pages + 1):
                fetch_tasks.append(self.fetch_category_page(cat, page))
                
        # Resolve todas as requisições concorrentes em nível de rede externa
        pages_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        flat_products = []
        for result in pages_results:
            if isinstance(result, list):
                flat_products.extend(result)
            elif isinstance(result, Exception):
                logger.error("Exceção capturada em uma das threads assíncronas de busca: %s", result)
                
        logger.info("Descoberta de rede concluída. %d produtos candidatos mapeados.", len(flat_products))
        
        # Dispara o processamento analítico e ingestão atômica em paralelo
        ingest_tasks = [self.process_and_ingest_product(prod) for prod in flat_products]
        ingest_results = await asyncio.gather(*ingest_tasks)
        
        successful_ingests = [r for r in ingest_results if r is not None]
        logger.info("Ingestão de dados finalizada. %d ofertas indexadas/atualizadas no Supabase.", len(successful_ingests))
        return len(successful_ingests)

    async def close(self):
        """
        Fecha adequadamente os recursos de rede abertos pelo cliente de requisições.
        """
        await self.http_client.aclose()
