import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    ID_DO_GRUPO = os.getenv("ID_DO_GRUPO")
    ALI_KEY = os.getenv("ALI_KEY")
    ALI_SECRET = os.getenv("ALI_SECRET")
    ALI_TRACKING_ID = os.getenv("ALI_TRACKING_ID")
    
    # URL do Supabase
    DATABASE_URL = os.getenv("DATABASE_URL")
    
    # Fallback caso não use URL completa
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_NAME = os.getenv("DB_NAME", "postgres")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "secret")
    DB_PORT = os.getenv("DB_PORT", "5432")

    @classmethod
    def validate(cls):
        required = ["TELEGRAM_TOKEN", "ID_DO_GRUPO", "ALI_KEY", "ALI_SECRET", "ALI_TRACKING_ID"]
        missing = [field for field in required if not getattr(cls, field)]
        if missing:
            raise ValueError(f"Variáveis ausentes: {', '.join(missing)}")
