"""
analytics.py — Motor Analítico de Arbitragem de Preços e Clusterização de Relevância
"""

import math
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("bot.analytics")

class OfferSniperAnalytics:
    @staticmethod
    def calculate_statistics(price_history: List[float]) -> Tuple[float, float]:
        """ Calcula a média aritmética e o desvio padrão com proteção contra variância nula """
        if not price_history:
            return 0.0, 0.0
            
        n = len(price_history)
        mean = sum(price_history) / n
        
        if n < 2:
            return mean, 0.0  # Proteção contra divisão por zero se houver apenas 1 registro histórico
            
        variance = sum((x - mean) ** 2 for x in price_history) / (n - 1)
        return mean, math.sqrt(variance)

    @staticmethod
    def evaluate_product(telemetry: Dict[str, Any], price_history: List[float]) -> Tuple[bool, Dict[str, Any]]:
        """
        Executa a validação em tempo real das métricas comerciais do produto.
        Garante tolerância caso o histórico de preços esteja em fase inicial de alimentação.
        """
        current_price = telemetry["current_price"]
        sales_6h = telemetry.get("sales_last_6h", 0)
        sales_6h_prev = telemetry.get("sales_last_6h_previous", 0)
        
        # 1. Análise Estatística de Flutuação de Preço (Desvio Padrão)
        mean_price, std_dev = OfferSniperAnalytics.calculate_statistics(price_history)
        
        # Se não houver histórico válido, assume o preço atual como média estável
        if mean_price == 0.0:
            mean_price = current_price

        # Engenharia de Desconto: Determina o preço teto dinâmico tolerável
        # Se o desvio for muito baixo ou nulo, fixa uma margem segura de 10% de desconto real
        if std_dev < (mean_price * 0.01):
            floor_price_threshold = mean_price * 0.90
        else:
            floor_price_threshold = mean_price - (1.5 * std_dev)

        # 2. Cálculo do Algoritmo de Tendência e Aceleração de Vendas (Trending Score)
        delta_sales = sales_6h - sales_6h_prev
        trending_score = (delta_sales / max(sales_6h_prev, 1)) * 100.0

        metrics_summary = {
            "mean_price": mean_price,
            "std_dev": std_dev,
            "floor_price": current_price,  # Mantém o preço de desembarque real
            "trending_score": trending_score
        }

        # 3. Crivo de Decisão Comercial (Hard Filtering)
        # Filtro de Reputação Básica
        if telemetry["product_rating"] < 4.5:
            logger.info("Produto rejeitado: Avaliação insatisfatória (%.2f)", telemetry["product_rating"])
            return False, metrics_summary

        if telemetry["seller_positive_feedback_rate"] < 0.85:
            logger.info("Produto rejeitado: Reputação da loja abaixo da linha de segurança (%.2f)", telemetry["seller_positive_feedback_rate"])
            return False, metrics_summary

        # Validação do Preço contra a Linha de Base (Garante desconto legítimo)
        if current_price > floor_price_threshold:
            # Caso o produto possua uma aceleração de vendas explosiva (Viral), mitiga a barreira de preço
            if trending_score > 50.0 and telemetry["sales_volume"] > 100:
                logger.info("Preço acima do desvio médio, mas aprovado por força de tendência viral (TS: %.2f)", trending_score)
                return True, metrics_summary
            
            logger.info("Produto rejeitado: Preço atual (R$ %.2f) não representa anomalia de desconto legítima. Teto calculado: R$ %.2f", current_price, floor_price_threshold)
            return False, metrics_summary

        return True, metrics_summary
