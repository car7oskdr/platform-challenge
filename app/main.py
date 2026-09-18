import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, StringConstraints

from app import db, metrics

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

VERSION_FILE = Path(__file__).parent.parent / "model" / "VERSION"
DOCS_ENABLED = os.getenv("DOCS_ENABLED", "true").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.version = VERSION_FILE.read_text(encoding="utf-8").strip()
        logger.info("Versión de la aplicación: %s", app.state.version)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"No se encontró el archivo de versión en {VERSION_FILE}"
        ) from exc

    app.state.pool = await db.create_pool()

    yield

    await app.state.pool.close()
    logger.info("Pool de conexiones cerrado")


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)


app.middleware("http")(metrics.metrics_middleware)


class NoteIn(BaseModel):
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


class NoteOut(BaseModel):
    id: int
    content: str
    created_at: str


@app.get("/version")
async def get_version():
    return {"version": app.state.version}


@app.get("/metrics", include_in_schema=False)
async def get_metrics():
    payload, content_type = metrics.render()
    return Response(content=payload, media_type=content_type)


@app.get("/health")
async def health():
    """Liveness: solo dice que el proceso responde.

    No consulta dependencias externas a propósito. Si mirara Postgres, una
    caída de la base haría que kubelet reiniciara todos los pods a la vez.
    """
    return {"status": "ok"}


@app.get("/ready")
async def ready(response: Response):
    """Readiness: ¿puede esta réplica atender tráfico ahora mismo?

    Devuelve 503 si la base no responde. Kubernetes solo mira el código HTTP,
    así que un 200 con {"db": "down"} pasaría la probe igualmente.
    """
    try:
        await db.ping(app.state.pool)
    except Exception as exc:
        logger.warning("Readiness fallido: %s", exc)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "unreachable"}

    return {"status": "ready", "database": "ok"}


@app.post("/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(note: NoteIn):
    try:
        row = await db.insert_note(app.state.pool, note.content)
    except db.InvalidNoteError as exc:
        logger.info("Nota rechazada por la base de datos: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El contenido de la nota no es válido",
        ) from exc
    except Exception as exc:
        logger.error("Error al insertar nota: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La base de datos no está disponible",
        ) from exc

    return _serialize(row)


@app.get("/notes", response_model=list[NoteOut])
async def get_notes(limit: Annotated[int, Query(ge=1, le=100)] = 20):
    try:
        rows = await db.list_notes(app.state.pool, limit)
    except Exception as exc:
        logger.error("Error al listar notas: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La base de datos no está disponible",
        ) from exc

    return [_serialize(row) for row in rows]


def _serialize(row: dict) -> dict:
    return {**row, "created_at": row["created_at"].isoformat()}
