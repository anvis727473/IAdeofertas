import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    ID_DO_GRUPO = os.getenv("ID_DO_GRUPO")

    ALI_KEY = os.getenv("ALI_KEY")
    ALI_SECRET = os.getenv("ALI_SECRET")
    ALI_TRACKING_ID = os.getenv("ALI_TRACKING_ID")

    DATABASE_URL = os.getenv("DATABASE_URL")

    PORT = int(os.getenv("PORT", "10000"))
    LOOP_SLEEP_SECONDS = int(os.getenv("LOOP_SLEEP_SECONDS", "300"))
    PRODUCT_DELAY_SECONDS = int(os.getenv("PRODUCT_DELAY_SECONDS", "4"))
    MAX_PRODUCTS_PER_CYCLE = int(os.getenv("MAX_PRODUCTS_PER_CYCLE", "8"))

    DISCOUNT_THRESHOLD = float(os.getenv("DISCOUNT_THRESHOLD", "6"))
    REPOST_COOLDOWN_DAYS = int(os.getenv("REPOST_COOLDOWN_DAYS", "7"))

    SEARCH_KEYWORDS = [
        "SSD NVMe",
        "Xiaomi Wi-Fi 6",
        "Baseus Charger",
        "Teclado Mecanico",
        "Mouse Gamer",
        "Hub USB C",
        "Dock Station",
        "Mini PC Ryzen",
        "Smartwatch AMOLED",
        "Parafusadeira Xiaomi",
    ]

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    ]

    @classmethod
    def validate(cls):
        required = ["TELEGRAM_TOKEN", "ID_DO_GRUPO", "ALI_KEY", "ALI_SECRET", "ALI_TRACKING_ID"]
        missing = [field for field in required if not getattr(cls, field)]
        if missing:
            raise ValueError(f"Variáveis de ambiente ausentes: {', '.join(missing)}")
