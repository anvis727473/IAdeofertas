"""
bot.py — Mantido para compatibilidade de infraestrutura herdada.
Redireciona a chamada para o barramento unificado e estável main.py.
"""
import asyncio
from main import main

if __name__ == "__main__":
    asyncio.run(main())
