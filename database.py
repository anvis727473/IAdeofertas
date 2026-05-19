import json
import logging
from typing import Any, Dict, Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import Json

from config import Config

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self):
        self.connection_pool = None
        try:
            if Config.DATABASE_URL:
                self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                    1, 5, dsn=Config.DATABASE_URL
                )
            else:
                self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                    1,
                    5,
                    host=os.getenv("DB_HOST", "localhost"),
                    database=os.getenv("DB_NAME", "postgres"),
                    user=os.getenv("DB_USER", "postgres"),
                    password=os.getenv("DB_PASSWORD", "secret"),
                    port=os.getenv("DB_PORT", "5432"),
                )

            self._create_tables_if_not_exists()
        except Exception as e:
            logger.exception(f"Erro ao inicializar banco: {e}")

    def get_connection(self):
        if self.connection_pool:
            return self.connection_pool.getconn()
        return None

    def put_connection(self, conn):
        if self.connection_pool and conn:
            self.connection_pool.putconn(conn)

    def close(self):
        if self.connection_pool:
            self.connection_pool.closeall()

    def _create_tables_if_not_exists(self):
        conn = self.get_connection()
        if not conn:
            return

        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ofertas_postadas (
                        produto_id VARCHAR(80) PRIMARY KEY,
                        titulo TEXT NOT NULL,
                        url TEXT NOT NULL,
                        preco_postagem NUMERIC(12, 2),
                        origem TEXT,
                        ultima_postagem TIMESTAMPTZ DEFAULT NOW()
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS historico_precos (
                        id SERIAL PRIMARY KEY,
                        produto_id VARCHAR(80) NOT NULL,
                        titulo TEXT,
                        url TEXT,
                        preco NUMERIC(12, 2) NOT NULL,
                        image TEXT,
                        source TEXT,
                        raw JSONB,
                        data_coleta TIMESTAMPTZ DEFAULT NOW()
                    );
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_historico_produto_data
                    ON historico_precos (produto_id, data_coleta DESC);
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ofertas_ultima_postagem
                    ON ofertas_postadas (ultima_postagem DESC);
                """)

                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.exception(f"Erro ao criar tabelas: {e}")
        finally:
            self.put_connection(conn)

    def has_recent_post(self, product_id: str, cooldown_days: int) -> bool:
        conn = self.get_connection()
        if not conn:
            return False

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT 1
                    FROM ofertas_postadas
                    WHERE produto_id = %s
                      AND ultima_postagem >= NOW() - INTERVAL '{int(cooldown_days)} days'
                    LIMIT 1;
                    """,
                    (product_id,),
                )
                return cursor.fetchone() is not None
        except Exception:
            logger.exception("Erro ao verificar cooldown")
            return False
        finally:
            self.put_connection(conn)

    def save_price_sample(self, product: Dict[str, Any]):
        conn = self.get_connection()
        if not conn:
            return

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO historico_precos
                        (produto_id, titulo, url, preco, image, source, raw)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        product["id"],
                        product.get("title"),
                        product.get("url"),
                        product.get("price_value"),
                        product.get("image"),
                        product.get("source"),
                        Json(product.get("raw", {})),
                    ),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Erro ao salvar histórico de preço")
        finally:
            self.put_connection(conn)

    def get_price_metrics(self, product_id: str) -> Dict[str, Optional[float]]:
        conn = self.get_connection()
        if not conn:
            return {"avg_price": None, "min_price": None, "max_price": None, "last_price": None, "sample_count": 0}

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        AVG(preco),
                        MIN(preco),
                        MAX(preco),
                        COUNT(*)
                    FROM historico_precos
                    WHERE produto_id = %s
                      AND data_coleta >= NOW() - INTERVAL '30 days';
                    """,
                    (product_id,),
                )
                avg_price, min_price, max_price, sample_count = cursor.fetchone() or (None, None, None, 0)

                cursor.execute(
                    """
                    SELECT preco
                    FROM historico_precos
                    WHERE produto_id = %s
                    ORDER BY data_coleta DESC
                    LIMIT 1;
                    """,
                    (product_id,),
                )
                last_row = cursor.fetchone()
                last_price = float(last_row[0]) if last_row else None

                return {
                    "avg_price": float(avg_price) if avg_price is not None else None,
                    "min_price": float(min_price) if min_price is not None else None,
                    "max_price": float(max_price) if max_price is not None else None,
                    "last_price": last_price,
                    "sample_count": int(sample_count or 0),
                }
        except Exception:
            logger.exception("Erro ao buscar métricas de preço")
            return {"avg_price": None, "min_price": None, "max_price": None, "last_price": None, "sample_count": 0}
        finally:
            self.put_connection(conn)

    def register_post(self, product: Dict[str, Any], affiliate_url: str):
        conn = self.get_connection()
        if not conn:
            return

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ofertas_postadas
                        (produto_id, titulo, url, preco_postagem, origem, ultima_postagem)
                    VALUES
                        (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (produto_id)
                    DO UPDATE SET
                        titulo = EXCLUDED.titulo,
                        url = EXCLUDED.url,
                        preco_postagem = EXCLUDED.preco_postagem,
                        origem = EXCLUDED.origem,
                        ultima_postagem = NOW();
                    """,
                    (
                        product["id"],
                        product.get("title"),
                        affiliate_url,
                        product.get("price_value"),
                        product.get("source"),
                    ),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Erro ao registrar postagem")
        finally:
            self.put_connection(conn)
