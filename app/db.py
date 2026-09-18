"""Acceso a PostgreSQL.

Este módulo no conoce FastAPI a propósito: recibe el pool como parámetro y
lanza las excepciones de asyncpg tal cual. Traducir un fallo de base de datos
a un código HTTP es responsabilidad de la capa web.

Las métricas de base de datos se declaran AQUÍ y no en metrics.py. Ese módulo
instrumenta HTTP e importa starlette; si db.py dependiera de él, la capa de
datos arrastraría el framework web. Como prometheus_client usa un registro
global, ambas familias de métricas salen igual por /metrics.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from time import perf_counter
from urllib.parse import urlsplit

import asyncpg
from prometheus_client import Gauge, Histogram

logger = logging.getLogger(__name__)

POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "10"))

COMMAND_TIMEOUT = float(os.getenv("DB_COMMAND_TIMEOUT", "5"))

PING_TIMEOUT = float(os.getenv("DB_PING_TIMEOUT", "2"))

REQUIRED_TABLE = os.getenv("DB_REQUIRED_TABLE", "public.notes")

# Escala distinta a la de HTTP: las consultas tardan décimas de milisegundo, y
# con los buckets de metrics.py (desde 5 ms) todo caería en el primero y el p95
# no distinguiría nada. La resolución tiene que estar donde vive el dato.
DB_BUCKETS = (
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
)

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Duración de las consultas a PostgreSQL",
    labelnames=("operation", "outcome"),
    buckets=DB_BUCKETS,
)

DB_ACQUIRE_DURATION = Histogram(
    "db_acquire_duration_seconds",
    "Espera hasta obtener una conexión del pool",
    labelnames=("operation",),
    buckets=DB_BUCKETS,
)

DB_POOL_SIZE = Gauge("db_pool_size", "Conexiones abiertas en el pool")
DB_POOL_IDLE = Gauge("db_pool_idle", "Conexiones libres en el pool")
DB_POOL_MAX = Gauge("db_pool_max_size", "Tamaño máximo configurado del pool")


async def create_pool() -> asyncpg.Pool:
    dsn = os.environ["DATABASE_URL"]

    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=0,
        max_size=POOL_MAX_SIZE,
        command_timeout=COMMAND_TIMEOUT,
    )

    DB_POOL_SIZE.set_function(pool.get_size)
    DB_POOL_IDLE.set_function(pool.get_idle_size)
    DB_POOL_MAX.set(POOL_MAX_SIZE)

    target = urlsplit(dsn)
    logger.info(
        "Pool de conexiones listo (host=%s, base=%s, max_size=%s)",
        target.hostname,
        target.path.lstrip("/"),
        POOL_MAX_SIZE,
    )
    return pool


@asynccontextmanager
async def _timed(pool: asyncpg.Pool, operation: str):
    """Mide por separado la espera por conexión y la consulta.

    Son dos problemas distintos con dos soluciones distintas: la espera se
    arregla subiendo max_size o bajando la concurrencia; la consulta, con
    índices o con menos trabajo en la base.
    """
    inicio_espera = perf_counter()
    espera_medida = False

    try:
        async with pool.acquire() as conn:
            DB_ACQUIRE_DURATION.labels(operation).observe(
                perf_counter() - inicio_espera
            )
            espera_medida = True

            inicio_consulta = perf_counter()
            outcome = "success"
            try:
                yield conn
            except asyncpg.exceptions.IntegrityConstraintViolationError:
                outcome = "invalid"
                raise
            except BaseException:
                outcome = "error"
                raise
            finally:
                DB_QUERY_DURATION.labels(operation, outcome).observe(
                    perf_counter() - inicio_consulta
                )
    finally:
        if not espera_medida:
            DB_ACQUIRE_DURATION.labels(operation).observe(
                perf_counter() - inicio_espera
            )


class SchemaNotReadyError(RuntimeError):
    """La base responde, pero el esquema todavía no está aplicado."""


async def check_ready(pool: asyncpg.Pool) -> None:
    """¿Se puede atender tráfico? Base viva Y esquema presente.

    Un `SELECT 1` solo prueba que Postgres contesta: con la tabla ausente el
    pod entraría al Service y serviría errores en vez de esperar fuera.
    `to_regclass` consulta el catálogo del sistema y, medido contra el Postgres
    del cluster, cuesta lo mismo que `SELECT 1` (0,04 ms): la probe honesta no
    se paga con latencia.

    El `wait_for` acota la operación completa —incluida la espera por una
    conexión libre del pool—, no solo la consulta.
    """

    async def _consultar():
        async with _timed(pool, "check_ready") as conn:
            return await conn.fetchval("SELECT to_regclass($1)", REQUIRED_TABLE)

    tabla = await asyncio.wait_for(_consultar(), timeout=PING_TIMEOUT)
    if tabla is None:
        raise SchemaNotReadyError(f"falta la tabla {REQUIRED_TABLE}")


class InvalidNoteError(ValueError):
    """El contenido rechazado por una restricción de la tabla.

    Existe para que la capa web pueda distinguir "el cliente mandó algo
    inválido" de "la base de datos no responde", sin tener que conocer los
    tipos de excepción de asyncpg.
    """


async def insert_note(pool: asyncpg.Pool, content: str) -> dict:
    try:
        async with _timed(pool, "insert_note") as conn:
            row = await conn.fetchrow(
                "INSERT INTO notes (content) VALUES ($1) "
                "RETURNING id, content, created_at",
                content,
            )
    except asyncpg.exceptions.IntegrityConstraintViolationError as exc:
        raise InvalidNoteError(str(exc)) from exc

    return dict(row)


async def list_notes(pool: asyncpg.Pool, limit: int = 20) -> list[dict]:
    async with _timed(pool, "list_notes") as conn:
        rows = await conn.fetch(
            "SELECT id, content, created_at FROM notes ORDER BY id DESC LIMIT $1",
            limit,
        )

    return [dict(row) for row in rows]
