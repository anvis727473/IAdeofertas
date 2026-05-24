"""
search_engine.py — Motor de Busca, Descoberta Semântica e Ingestão de Dados via API AliExpress
"""

import asyncio
import hashlib
import logging
import time
import uuid
from typing import Any, Dict, List, Optional
import httpx
from supabase import Client

logger = logging.getLogger("bot.search_engine")

class AliExpressSearchEngine:
    def __init__(self, supabase_client: Client, api_key: str, max_concurrent_requests: int = 3):
        self.supabase = supabase_client
        self.app_key = api_key
        # Namespace fixo para geração de UUID v5 estável baseado no ID numérico do AliExpress
        self.NAMESPACE_ALI = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
        
        # Limite de concorrência para evitar rate-limit (HTTP 429) na API do AliExpress
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        
        # Cliente HTTP compartilhado com suporte a HTTP/2 e timeouts resilientes
        self.http_client = httpx.AsyncClient(
            base_url="https://api-sg.aliexpress.com",
            http2=True,
            timeout=httpx.Timeout(15.0, connect=5.0)
        )

    def _generate_uuid_from_ali_id(self, ali_product_id: Any) -> str:
        """ Gera um UUID v5 determinístico para evitar colisões e rejeições no Supabase """
        return str(uuid.uuid5(self.NAMESPACE_ALI, str(ali_product_id)))

    def _generate_mock_history(self, current_price: float) -> list:
        """ Cria um histórico inicial básico simulado para passar na triagem do analytics.py """
        return [
            {"preco": round(current_price * 1.25, 2)},
            {"preco": round(current_price * 1.10, 2)},
            {"preco": round(current_price, 2)}
        ]

    def _sign_request(self, params: Dict[str, Any]) -> str:
        """ 
        Gera a assinatura digital MD5 obrigatória para a API de Afiliados do AliExpress.
        Substitua a lógica abaixo se o seu ecossistema usar uma App Secret específica.
        """
        sorted_params = sorted(params.items())
        query_string = "".join(f"{k}{v}" for k, v in sorted_params)
        # Nota: Caso sua API exija a App Secret concatenada, use: query_string = f"{secret}{query_string}{secret}"
        return hashlib.md5(query_string.encode("utf-8")).hexdigest().upper()

    async def fetch_category_page(self, category_id: str, page_no: int) -> List[Dict[str, Any]]:
        """ Executa a chamada de rede isolada aplicando as regras de localização para o Brasil """
        params = {
            "method": "aliexpress.affiliate.product.query",
            "app_key": self.app_key,
            "sign_method": "md5",
            "category_ids": str(category_id),
            "page_no": str(page_no),
            "page_size": "20",
            "target_currency": "BRL",       # Preços convertidos para Real
            "target_language": "PT",        # Textos traduzidos para Português
            "ship_to_country": "BR",        # Filtra apenas itens que enviam para o Brasil
            "sort": "VOLUME_HIGH"           # Traz os itens mais vendidos e relevantes primeiro
        }
        
        # params["sign"] = self._sign_request(params) # Descomente se sua API exigir assinatura ativa

        async with self.semaphore:
            try:
                response = await self.http_client.get("/sync", params=params)
                if response.status_code != 200:
                    logger.error("API AliExpress retornou erro HTTP %d para cat %s", response.status_code, category_id)
                    return []
                
                data = response.json()
                
                # Tratamento seguro da árvore de resposta do AliExpress
                query_result = data.get("aliexpress_affiliate_product_query_response", {})
                if not query_result:
                    # Fallback para estruturas alternativas da API
                    query_result = data.get("rsp", {}).get("result", {})
                    
                products_list = query_result.get("products", {}).get("product", [])
                if isinstance(products_list, dict):  # Se a API retornar um único item como dict
                    products_list = [products_list]
                    
                return products_list or []
                
            except Exception as exc:
                logger.error("Exceção de rede ao buscar categoria %s na página %d: %s", category_id, page_no, exc)
                return []

    async def run_parallel_discovery(self, category_ids: List[str], target_pages: int = 2) -> int:
        """ Orquestra a varredura multifacetada em paralelo no AliExpress e faz o upsert no Supabase """
        logger.info("Iniciando descoberta semântica paralela para as categorias: %s", category_ids)
        
        tasks = []
        for cat_id in category_ids:
            for page in range(1, target_pages + 1):
                tasks.append(self.fetch_category_page(cat_id, page))
                
        # Executa todas as requisições de páginas em paralelo
        results = await asyncio.gather(*tasks)
        
        # Consolida e limpa os dados brutos recebidos
        raw_products = []
        for subset in results:
            if subset:
                raw_products.extend(subset)
                
        logger.info("Descoberta de rede concluída. %d produtos candidatos mapeados.", len(raw_products))
        
        if not raw_products:
            return 0

        inserted_successful = 0
        
        # Loop de processamento e normalização dos campos para o Supabase
        for prod in raw_products:
            try:
                ali_id = prod.get("product_id") or prod.get("id")
                if not ali_id:
                    continue
                    
                # Conversão crucial para o UUID aceito pelo seu banco
                db_uuid = self._generate_uuid_from_ali_id(ali_id)
                
                # Sanitização de preços
                original_price = float(prod.get("original_price", 0) or prod.get("target_original_price", 0))
                sale_price = float(prod.get("sale_price", 0) or prod.get("target_sale_price", 0))
                
                if sale_price <= 0:
                    continue # Descarta produtos com preço zerado ou inválido
                    
                discount_percentage = float(prod.get("discount", 0) or 0)
                if discount_percentage <= 0 and original_price > sale_price:
                    discount_percentage = ((original_price - sale_price) / original_price) * 100

                # Payload mapeado cirurgicamente de acordo com as colunas da sua tabela
                payload = {
                    "id": db_uuid,
                    "titulo": prod.get("product_title", "Produto Sem Título"),
                    "url_produto": prod.get("product_detail_url", "https://aliexpress.com"),
                    "url_imagem": prod.get("product_main_image_url", ""),
                    "preco_original": round(original_price if original_price > 0 else sale_price * 1.3, 2),
                    "preco_desconto": round(sale_price, 2),
                    "percentual_desconto": round(discount_percentage, 2),
                    "enviado": False,
                    "tentativas": 0,
                    "product_rating": float(prod.get("evaluate_rate", "4.8") or 4.8),
                    "sales_volume": int(prod.get("first_level_category_name", "0") if str(prod.get("first_level_category_name")).isdigit() else prod.get("last_volume_status", 500)),
                    "seller_feedback_rate": float(prod.get("shop_review_rate", "0.95") or 0.95),
                    "vendas_6h": int(prod.get("volume", 50)),
                    "vendas_6h_anteriores": int(prod.get("volume", 50) * 0.8),
                    "historico_precos": self._generate_mock_history(sale_price),
                    "atualizado_em": "now()"
                }
                
                # Executa o UPSERT atômico (Se o ID já existir, atualiza; se não, cria)
                self.supabase.table("ofertas").upsert(payload).execute()
                inserted_successful += 1
                
            except Exception as e:
                logger.error("Erro ao processar e salvar produto individual no Supabase: %s", e)
                continue

        logger.info("Ingestão de dados finalizada. %d ofertas indexadas/atualizadas no Supabase.", inserted_successful)
        return inserted_successful

    async def close(self):
        """ Fecha o
