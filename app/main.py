import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

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

    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)


@app.get("/version")
async def get_version():
    return {"version": app.state.version}
