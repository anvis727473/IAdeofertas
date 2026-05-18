import psycopg2
from psycopg2 import pool
from config import Config
import logging

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.connection_pool = None
        try:
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
            
            # Cria as tabelas automaticamente se elas não existirem
            self._create_tables_if_not_exists()

        except Exception as e:
            logger.error(f"Erro ao inicializar o pool do banco de dados: {e}")

    def get_connection(self):
        if self.connection_pool:
            return self.connection_pool.getconn()
        return None

    def put_connection(self, conn):
        if self.connection_pool and conn:
            self.connection_pool.putconn(conn)

    def _create_tables_if_not_exists(self):
        """Cria as tabelas de histórico e de postagens no PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cursor:
                # Tabela 1: Evitar Duplicatas
                query_postadas = """
                CREATE TABLE IF NOT EXISTS ofertas_postadas (
                    produto_id VARCHAR(50) PRIMARY KEY,
                    titulo TEXT,
                    url TEXT,
                    data_postagem TIMESTAMP DEFAULT NOW()
                );
                """
                
                # Tabela 2: Inteligência de Preços
                query_precos = """
                CREATE TABLE IF NOT EXISTS historico_precos (
                    id SERIAL PRIMARY KEY,
                    produto_id VARCHAR(50),
                    preco NUMERIC(10, 2),
                    data_coleta TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_produto_data ON historico_precos(produto_id, data_coleta);
                """
                
                cursor.execute(query_postadas)
                cursor.execute(query_precos)
                conn.commit()
                logger.info("Verificação de tabelas de banco de dados concluída (OK).")
        except Exception as e:
            logger.error(f"Erro ao criar as tabelas automaticamente: {e}")
            conn.rollback()
        finally:
            self.put_connection(conn)

    def is_offer_posted(self, product_id: str) -> bool:
        """Verifica se a oferta já foi postada para evitar duplicatas."""
        conn = self.get_connection()
        if not conn: return False
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM ofertas_postadas WHERE produto_id = %s LIMIT 1;", (product_id,))
                return cursor.fetchone() is not None
        except Exception as e:
            return False
        finally:
            self.put_connection(conn)

    def save_posted_offer(self, product_id: str, title: str, url: str):
        """Registra a oferta para evitar futuras postagens duplicadas."""
        conn = self.get_connection()
        if not conn: return
        try:
            with conn.cursor() as cursor:
                query = """
                    INSERT INTO ofertas_postadas (produto_id, titulo, url)
                    VALUES (%s, %s, %s) ON CONFLICT (produto_id) DO NOTHING;
                """
                cursor.execute(query, (product_id, title, url))
                conn.commit()
        except Exception as e:
            conn.rollback()
        finally:
            self.put_connection(conn)

    # ================= NOVAS FUNÇÕES DE INTELIGÊNCIA =================

    def save_price_if_changed(self, product_id: str, price: float):
        """Eficiência Máxima: Só salva o preço no banco se ele for diferente da última checagem."""
        conn = self.get_connection()
        if not conn: return
        try:
            with conn.cursor() as cursor:
                # Checa qual foi o último preço registrado deste produto
                cursor.execute("SELECT preco FROM historico_precos WHERE produto_id = %s ORDER BY data_coleta DESC LIMIT 1;", (product_id,))
                result = cursor.fetchone()
                
                if result and float(result[0]) == price:
                    # Preço não mudou, economiza processamento e espaço
                    return 

                # Preço é novo (ou produto novo), insere no banco
                cursor.execute("INSERT INTO historico_precos (produto_id, preco) VALUES (%s, %s);", (product_id, price))
                conn.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar histórico de preço: {e}")
            conn.rollback()
        finally:
            self.put_connection(conn)

    def get_price_metrics(self, product_id: str):
        """Calcula a média de preço dos últimos 30 dias para o produto. Retorna (Média, Preço Mínimo)."""
        conn = self.get_connection()
        if not conn: return None, None
        try:
            with conn.cursor() as cursor:
                query = """
                SELECT AVG(preco), MIN(preco) 
                FROM historico_precos 
                WHERE produto_id = %s AND data_coleta >= NOW() - INTERVAL '30 days';
                """
                cursor.execute(query, (product_id,))
                result = cursor.fetchone()
                
                avg_price = float(result[0]) if result and result[0] else None
                min_price = float(result[1]) if result and result[1] else None
                return avg_price, min_price
        except Exception as e:
            logger.error(f"Erro ao buscar métricas de preço: {e}")
            return None, None
        finally:
            self.put_connection(conn)
