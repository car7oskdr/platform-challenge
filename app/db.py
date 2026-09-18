import asyncio
import logging
import os
from urllib.parse import urlsplit

import asyncpg

logger = logging.getLogger(__name__)

POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "10"))

COMMAND_TIMEOUT = float(os.getenv("DB_COMMAND_TIMEOUT", "5"))

PING_TIMEOUT = float(os.getenv("DB_PING_TIMEOUT", "2"))


async def create_pool() -> asyncpg.Pool:
    dsn = os.environ["DATABASE_URL"]

    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=0,
        max_size=POOL_MAX_SIZE,
        command_timeout=COMMAND_TIMEOUT,
    )

    target = urlsplit(dsn)
    logger.info(
        "Pool de conexiones listo (host=%s, base=%s, max_size=%s)",
        target.hostname,
        target.path.lstrip("/"),
        POOL_MAX_SIZE,
    )
    return pool


async def ping(pool: asyncpg.Pool) -> None:
    """Comprueba que la base responde. Lanza excepción si no lo hace a tiempo.

    El `wait_for` acota la operación completa —incluida la espera por una
    conexión libre del pool—, no solo la consulta.
    """
    await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=PING_TIMEOUT)


class InvalidNoteError(ValueError):
    """El contenido rechazado por una restricción de la tabla.

    Existe para que la capa web pueda distinguir "el cliente mandó algo
    inválido" de "la base de datos no responde", sin tener que conocer los
    tipos de excepción de asyncpg.
    """


async def insert_note(pool: asyncpg.Pool, content: str) -> dict:
    try:
        row = await pool.fetchrow(
            "INSERT INTO notes (content) VALUES ($1) RETURNING id, content, created_at",
            content,
        )
    except asyncpg.exceptions.IntegrityConstraintViolationError as exc:
        raise InvalidNoteError(str(exc)) from exc

    return dict(row)


async def list_notes(pool: asyncpg.Pool, limit: int = 20) -> list[dict]:
    rows = await pool.fetch(
        "SELECT id, content, created_at FROM notes ORDER BY id DESC LIMIT $1",
        limit,
    )
    return [dict(row) for row in rows]
