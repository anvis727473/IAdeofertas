import psycopg2
from psycopg2 import pool
from config import Config
import logging

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.connection_pool = None
        try:
            # No Render, a DATABASE_URL geralmente é fornecida automaticamente para bancos PostgreSQL associados
            if Config.DATABASE_URL:
                logger.info("Conectando ao banco de dados via DATABASE_URL...")
                self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                    1, 10, dsn=Config.DATABASE_URL
                )
            else:
                logger.info("Conectando ao banco de dados via credenciais individuais...")
                self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                    1, 10,
                    host=Config.DB_HOST,
                    database=Config.DB_NAME,
                    user=Config.DB_USER,
                    password=Config.DB_PASSWORD,
                    port=Config.DB_PORT
                )
        except Exception as e:
            logger.error(f"Erro ao inicializar o pool do banco de dados: {e}")

    def get_connection(self):
        if self.connection_pool:
            return self.connection_pool.getconn()
        return None

    def put_connection(self, conn):
        if self.connection_pool and conn:
            self.connection_pool.putconn(conn)

    def is_offer_posted(self, product_id: str) -> bool:
        """
        Verifica se a oferta já foi postada para evitar duplicatas.
        Retorna True se já existir, False caso contrário.
        """
        conn = self.get_connection()
        if not conn:
            logger.warning("Sem conexão com o banco de dados. Pulando verificação por segurança.")
            return False
        
        try:
            with conn.cursor() as cursor:
                # Stub de tabela/coluna: certifique-se de criar essa tabela no seu banco
                query = "SELECT 1 FROM ofertas_postadas WHERE produto_id = %s LIMIT 1;"
                cursor.execute(query, (product_id,))
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"Erro ao verificar duplicidade no banco: {e}")
            return False
        finally:
            self.put_connection(conn)

    def save_posted_offer(self, product_id: str, title: str, url: str):
        """
        Registra a oferta no histórico para evitar futuras postagens duplicadas.
        """
        conn = self.get_connection()
        if not conn:
            return
        
        try:
            with conn.cursor() as cursor:
                query = """
                    INSERT INTO ofertas_postadas (produto_id, titulo, url, data_postagem)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (produto_id) DO NOTHING;
                """
                cursor.execute(query, (product_id, title, url))
                conn.commit()
                logger.info(f"Oferta {product_id} salva com sucesso no histórico.")
        except Exception as e:
            logger.error(f"Erro ao salvar oferta no banco: {e}")
            conn.rollback()
        finally:
            self.put_connection(conn)
