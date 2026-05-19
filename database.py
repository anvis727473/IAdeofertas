import logging
import asyncpg
from config import Config

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.pool = None

    async def connect(self):
        try:
            if Config.DATABASE_URL:
                self.pool = await asyncpg.create_pool(Config.DATABASE_URL, min_size=1, max_size=10)
            else:
                self.pool = await asyncpg.create_pool(
                    host=Config.DB_HOST,
                    database=Config.DB_NAME,
                    user=Config.DB_USER,
                    password=Config.DB_PASSWORD,
                    port=Config.DB_PORT,
                    min_size=1, max_size=10
                )
            await self._create_tables()
            await self._seed_initial_keywords()
            logger.info("✅ Banco PostgreSQL (asyncpg) inicializado com sucesso.")
        except Exception as e:
            logger.exception(f"❌ Erro crítico no banco: {e}")
            raise

    async def _create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS nichos_monitorados (
                id SERIAL PRIMARY KEY,
                keyword TEXT UNIQUE NOT NULL,
                ativo BOOLEAN DEFAULT TRUE
            );
            
            CREATE TABLE IF NOT EXISTS ofertas_postadas (
                produto_id TEXT PRIMARY KEY,
                titulo TEXT,
                url TEXT,
                preco NUMERIC(10,2),
                score NUMERIC(10,2),
                vendas INTEGER,
                rating NUMERIC(3,2),
                keyword TEXT,
                data_postagem TIMESTAMP DEFAULT NOW()
            );
            
            CREATE TABLE IF NOT EXISTS historico_precos (
                id SERIAL PRIMARY KEY,
                produto_id TEXT,
                preco NUMERIC(10,2),
                data_coleta TIMESTAMP DEFAULT NOW()
            );
            """)

    async def _seed_initial_keywords(self):
        """Injeta keywords iniciais se a tabela estiver vazia (Evita bot inativo no primeiro deploy)"""
        keywords = ["SSD NVMe", "RTX 4060", "Mouse Gamer", "Teclado Mecânico"]
        async with self.pool.acquire() as conn:
            for kw in keywords:
                await conn.execute(
                    "INSERT INTO nichos_monitorados (keyword) VALUES ($1) ON CONFLICT DO NOTHING", kw
                )

    async def get_active_keywords(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT keyword FROM nichos_monitorados WHERE ativo = TRUE")
            return [row['keyword'] for row in rows]

    async def get_last_price(self, product_id: str):
        """Retorna o último preço registrado para o algoritmo de 'Price Drop'"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT preco FROM historico_precos 
                WHERE produto_id = $1 
                ORDER BY data_coleta DESC LIMIT 1
            """, product_id)
            return float(row['preco']) if row else None

    async def save_posted_offer(self, product):
        async with self.pool.acquire() as conn:
            await conn.execute("""
            INSERT INTO ofertas_postadas (
                produto_id, titulo, url, preco, score, vendas, rating, keyword
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (produto_id) DO UPDATE 
            SET preco = $4, data_postagem = NOW()
            """, 
            product.id, product.title, product.url, product.price_value, 
            float(product.score), product.sold_count, product.rating, product.keyword)

    async def save_price(self, product_id: str, price: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO historico_precos (produto_id, preco) VALUES ($1, $2)",
                product_id, price
            )
