"""
analytics.py — Motor estatístico e analítico do Sniper de Ofertas
"""
import logging
import math
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("bot.analytics")

class OfferSniperAnalytics:
    @staticmethod
    def calculate_statistics(price_history: List[float]) -> Tuple[float, float]:
        """
        Calcula a Média Móvel Simples (SMA) e o Desvio Padrão (Sigma).
        """
        n = len(price_history)
        if n < 5:  # Histórico mínimo para relevância estatística
            return sum(price_history) / n if n > 0 else 0.0, 0.0
            
        sma = sum(price_history) / n
        variance = sum((x - sma) ** 2 for x in price_history) / (n - 1)
        sigma = math.sqrt(variance)
        return sma, sigma

    @staticmethod
    def calculate_trending_score(v_6h: int, v_6h_ant: int, v_total: int, rating: float) -> float:
        """
        Calcula o Trending Score (TS) baseado na aceleração de vendas de curto prazo.
        """
        epsilon = 1e-5
        acceleration = (v_6h - v_6h_ant) / (v_6h_ant + epsilon)
        
        # Só pontua positivamente se houver aceleração real acima de 10%
        if acceleration < 0.1:
            acceleration = 0.1
            
        log_volume = math.log(v_total + 1)
        trending_score = acceleration * log_volume * rating
        return float(trending_score)

    @classmethod
    def evaluate_product(cls, raw_api_data: Dict[str, Any], historical_prices: List[float]) -> Tuple[bool, Dict[str, Any]]:
        """
        Executa a validação matricial de dados de ciência de dados e engenharia de arbitragem.
        Retorna (Aprovado, Métricas Calculadas).
        """
        try:
            # 1. Extração de Metadados de Confiabilidade
            rating = float(raw_api_data.get("product_rating", 0.0))
            sales_volume = int(raw_api_data.get("sales_volume", 0))
            seller_feedback = float(raw_api_data.get("seller_positive_feedback_rate", 0.0))
            
            # Filtro de corte rígido (Sanidade do Vendedor)
            if rating < 4.7 or sales_volume <= 100 or seller_feedback < 0.92:
                logger.debug("Produto rejeitado nos critérios mínimos de confiabilidade do seller.")
                return False, {}

            # 2. Engenharia de Arbitragem: Empilhamento de Cupons (Floor Price)
            current_base_price = float(raw_api_data["current_price"])
            store_coupon = float(raw_api_data.get("store_coupon_value", 0.0))
            platform_coupon = float(raw_api_data.get("platform_coupon_value", 0.0))
            
            floor_price = current_base_price - store_coupon - platform_coupon
            if floor_price <= 0:
                floor_price = current_base_price

            # 3. Análise Estatística de Preço
            if not historical_prices:
                historical_prices = [current_base_price]
            
            sma, sigma = cls.calculate_statistics(historical_prices)
            
            # Validação estatística por desvio padrão (K=2)
            # Se a volatilidade for zero, exige um desconto fixo de 15% contra a média
            threshold = sma - (2 * sigma) if sigma > 0.01 else sma * 0.85
            
            is_price_anomaly = floor_price <= threshold

            # 4. Cálculo de Tendência Virótica
            v_6h = int(raw_api_data.get("sales_last_6h", 0))
            v_6h_ant = int(raw_api_data.get("sales_last_6h_previous", 0))
            
            t_score = cls.calculate_trending_score(v_6h, v_6h_ant, sales_volume, rating)
            
            metrics = {
                "floor_price": floor_price,
                "price_sma_30": sma,
                "price_sigma_30": sigma,
                "trending_score": t_score,
                "is_anomaly": is_price_anomaly
            }

            # Decisão de aprovação final do Sniper
            if is_price_anomaly or (t_score > 15.0 and floor_price <= sma):
                return True, metrics

            return False, metrics

        except KeyError as err:
            logger.error("Falha ao processar chaves obrigatórias na telemetria da API: %s", err)
            return False, {}
        except Exception as err:
            logger.error("Erro inesperado no pipeline analítico: %s", err)
            return False, {}
