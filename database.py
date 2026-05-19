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

            self.connection_pool = (
                psycopg2.pool.SimpleConnectionPool(
                    1,
                    5,
                    dsn=Config.DATABASE_URL
                )
            )

            self._initialize_database()

        except Exception as e:
            logger.exception(f"Erro banco: {e}")

    def get_connection(self):

        if self.connection_pool:
            return self.connection_pool.getconn()

        return None

    def put_connection(self, conn):

        if self.connection_pool and conn:
            self.connection_pool.putconn(conn)

    def _initialize_database(self):

        conn = self.get_connection()

        if not conn:
            return

        try:

            with conn.cursor() as cursor:

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ofertas_postadas (
                        produto_id VARCHAR(80) PRIMARY KEY,
                        titulo TEXT,
                        url TEXT,
                        preco_postagem NUMERIC(12,2),
                        origem TEXT
                    );
                """)

                cursor.execute("""
                    ALTER TABLE ofertas_postadas
                    ADD COLUMN IF NOT EXISTS ultima_postagem TIMESTAMPTZ;
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS historico_precos (
                        id SERIAL PRIMARY KEY,
                        produto_id VARCHAR(80),
                        titulo TEXT,
                        url TEXT,
                        preco NUMERIC(12,2),
                        image TEXT,
                        source TEXT,
                        raw JSONB,
                        data_coleta TIMESTAMPTZ DEFAULT NOW()
                    );
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_historico_produto
                    ON historico_precos(produto_id);
                """)

                conn.commit()

                logger.info("Banco inicializado")

        except Exception as e:

            conn.rollback()

            logger.exception(
                f"Erro inicialização banco: {e}"
            )

        finally:
            self.put_connection(conn)

    def has_recent_post(
        self,
        product_id: str,
        cooldown_days: int
    ) -> bool:

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
                    AND ultima_postagem >=
                        NOW() - INTERVAL '{cooldown_days} days'
                    LIMIT 1;
                    """,
                    (product_id,)
                )

                return cursor.fetchone() is not None

        except Exception:
            logger.exception("Erro cooldown")
            return False

        finally:
            self.put_connection(conn)

    def save_price_sample(
        self,
        product: Dict[str, Any]
    ):

        conn = self.get_connection()

        if not conn:
            return

        try:

            with conn.cursor() as cursor:

                cursor.execute("""
                    INSERT INTO historico_precos (
                        produto_id,
                        titulo,
                        url,
                        preco,
                        image,
                        source,
                        raw
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    );
                """, (
                    product["id"],
                    product.get("title"),
                    product.get("url"),
                    product.get("price_value"),
                    product.get("image"),
                    product.get("source"),
                    Json(product.get("raw", {}))
                ))

                conn.commit()

        except Exception:

            conn.rollback()

            logger.exception(
                "Erro salvar histórico"
            )

        finally:
            self.put_connection(conn)

    def get_price_metrics(
        self,
        product_id: str
    ) -> Dict[str, Optional[float]]:

        conn = self.get_connection()

        if not conn:
            return {}

        try:

            with conn.cursor() as cursor:

                cursor.execute("""
                    SELECT
                        AVG(preco),
                        MIN(preco),
                        MAX(preco),
                        COUNT(*)
                    FROM historico_precos
                    WHERE produto_id = %s
                    AND data_coleta >=
                        NOW() - INTERVAL '30 days';
                """, (product_id,))

                result = cursor.fetchone()

                return {
                    "avg_price": (
                        float(result[0])
                        if result[0]
                        else None
                    ),
                    "min_price": (
                        float(result[1])
                        if result[1]
                        else None
                    ),
                    "max_price": (
                        float(result[2])
                        if result[2]
                        else None
                    ),
                    "sample_count": (
                        int(result[3])
                        if result[3]
                        else 0
                    )
                }

        except Exception:

            logger.exception(
                "Erro métricas preço"
            )

            return {}

        finally:
            self.put_connection(conn)

    def register_post(
        self,
        product: Dict[str, Any],
        affiliate_url: str
    ):

        conn = self.get_connection()

        if not conn:
            return

        try:

            with conn.cursor() as cursor:

                cursor.execute("""
                    INSERT INTO ofertas_postadas (
                        produto_id,
                        titulo,
                        url,
                        preco_postagem,
                        origem,
                        ultima_postagem
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        NOW()
                    )
                    ON CONFLICT (produto_id)
                    DO UPDATE SET
                        titulo = EXCLUDED.titulo,
                        url = EXCLUDED.url,
                        preco_postagem =
                            EXCLUDED.preco_postagem,
                        origem = EXCLUDED.origem,
                        ultima_postagem = NOW();
                """, (
                    product["id"],
                    product.get("title"),
                    affiliate_url,
                    product.get("price_value"),
                    product.get("source")
                ))

                conn.commit()

        except Exception:

            conn.rollback()

            logger.exception(
                "Erro registrar postagem"
            )

        finally:
            self.put_connection(conn)
