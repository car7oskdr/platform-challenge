"""Aplica las migraciones SQL.

Se ejecuta como `python -m app.migrate`, el mismo comando en local y dentro del
Job de Kubernetes: un solo camino para aplicar el esquema en todos los entornos.

Vive en la imagen de la aplicación a propósito. Así el esquema y el código que
lo consulta comparten tag, y es imposible desplegar uno sin el otro.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import asyncpg

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("app.migrate")

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# El Job puede arrancar antes de que Postgres acepte conexiones. En vez de
# orquestar el orden, se reintenta: es más simple y tolera reinicios de la base.
CONNECT_RETRIES = int(os.getenv("MIGRATE_RETRIES", "30"))
RETRY_DELAY = float(os.getenv("MIGRATE_RETRY_DELAY", "2"))


async def _connect(dsn: str) -> asyncpg.Connection:
    for intento in range(1, CONNECT_RETRIES + 1):
        try:
            return await asyncpg.connect(dsn)
        except (OSError, asyncpg.PostgresError) as exc:
            if intento == CONNECT_RETRIES:
                raise
            logger.info(
                "Postgres no responde (intento %s/%s): %s",
                intento,
                CONNECT_RETRIES,
                exc,
            )
            await asyncio.sleep(RETRY_DELAY)

    raise RuntimeError("inalcanzable")


async def main() -> int:
    dsn = os.environ["DATABASE_URL"]

    # sorted() porque el orden de los archivos es el orden de aplicación: por eso
    # los nombres empiezan por un número.
    archivos = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not archivos:
        logger.error("No hay migraciones en %s", MIGRATIONS_DIR)
        return 1

    conn = await _connect(dsn)
    try:
        for archivo in archivos:
            sql = archivo.read_text(encoding="utf-8")
            # Cada archivo en su transacción: si uno falla, no deja el esquema
            # a medias. Los .sql son idempotentes, así que reintentar es seguro.
            async with conn.transaction():
                await conn.execute(sql)
            logger.info("Aplicada: %s", archivo.name)
    finally:
        await conn.close()

    logger.info("Migraciones completadas (%s archivos)", len(archivos))
    return 0


if __name__ == "__main__":
    # El código de salida es lo que mira Kubernetes para decidir si el Job
    # terminó bien o hay que reintentarlo.
    sys.exit(asyncio.run(main()))
