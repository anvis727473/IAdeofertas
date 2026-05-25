import math
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("bot.analytics")

class OfferSniperAnalytics:
    @staticmethod
    def calculate_statistics(price_history: List[float]) -> Tuple[float, float]:
        if not price_history:
            return 0.0, 0.0
        n = len(price_history)
        mean = sum(price_history) / n
        if n < 2:
            return mean, 0.0
        variance = sum((x - mean) ** 2 for x in price_history) / (n - 1)
        return mean, math.sqrt(variance)

    @staticmethod
    def evaluate_product(telemetry: Dict[str, Any], price_history: List[float]) -> Tuple[bool, Dict[str, Any]]:
        current_price = float(telemetry.get("current_price", 0.0))
        sales_6h = int(telemetry.get("sales_last_6h", 0))
        sales_6h_prev = int(telemetry.get("sales_last_6h_previous", 0))
        product_rating = float(telemetry.get("product_rating", 5.0))
        seller_feedback = float(telemetry.get("seller_feedback_rate", 1.0))
        sales_volume = int(telemetry.get("sales_volume", 0))
        
        mean_price, std_dev = OfferSniperAnalytics.calculate_statistics(price_history)
        if mean_price == 0.0:
            mean_price = current_price

        floor_price_threshold = mean_price * 0.90 if std_dev < (mean_price * 0.01) else mean_price - (1.5 * std_dev)
        trending_score = ((sales_6h - sales_6h_prev) / max(sales_6h_prev, 1)) * 100.0

        metrics_summary = {
            "mean_price": mean_price,
            "std_dev": std_dev,
            "floor_price": floor_price_threshold,
            "trending_score": trending_score
        }

        if product_rating < 4.5 or seller_feedback < 0.85:
            return False, metrics_summary

        if current_price > floor_price_threshold:
            if trending_score > 50.0 and sales_volume > 100:
                return True, metrics_summary
            return False, metrics_summary

        return True, metrics_summary
