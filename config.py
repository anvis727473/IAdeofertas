import os
from dotenv import load_dotenv

# Carrega o arquivo .env se ele existir (útil para desenvolvimento local)
load_dotenv()

class Config:
    # AliExpress
    ALI_KEY = os.getenv('ALI_KEY')
    ALI_SECRET = os.getenv('ALI_SECRET')
    ALI_TRACKING_ID = os.getenv('ALI_TRACKING_ID')

    # Banco de Dados
    DATABASE_URL = os.getenv('DATABASE_URL')
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_NAME = os.getenv('DB_NAME')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')

    # Telegram e Geral
    ID_DO_GRUPO = os.getenv('ID_DO_GRUPO')
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    PYTHON_VERSION = os.getenv('PYTHON_VERSION', '3.10.0')

    @classmethod
    def validate(cls):
        """Valida se as variáveis essenciais estão presentes."""
        required = ['TELEGRAM_TOKEN', 'ID_DO_GRUPO']
        missing = [var for var in required if not getattr(cls, var)]
        if missing:
            raise ValueError(f"Variáveis de ambiente obrigatórias ausentes: {', '.join(missing)}")
