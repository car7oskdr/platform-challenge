"""Instrumentación Prometheus.

Expone dos series: un histograma de latencia y un contador de respuestas
separadas en exitosas y fallidas, que son las dos cosas que pide el reto.
"""

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request

# Los buckets definen la resolución de los percentiles: p95 y p99 se calculan
# interpolando dentro del bucket donde caen. Estos cubren de 5 ms a 10 s, que es
# el rango útil para una API HTTP. Cada bucket es una serie temporal más, así
# que no conviene abusar.
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Duración de las peticiones HTTP en segundos",
    labelnames=("method", "path", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# El label 'outcome' evita tener que enumerar códigos en cada consulta PromQL:
# la tasa de error es rate(...{outcome="failure"}) / rate(...).
REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total de peticiones HTTP atendidas",
    labelnames=("method", "path", "status", "outcome"),
)

# Medirse a uno mismo solo añade ruido: cada scrape de Prometheus inflaría las
# series con peticiones que no son tráfico real.
EXCLUDED_PATHS = frozenset({"/metrics"})


def _route_template(request: Request) -> str:
    """Devuelve la plantilla de la ruta, no la URL concreta.

    Con la URL cruda, cada `/notes/1`, `/notes/2`... crearía una serie temporal
    distinta y la cardinalidad crecería sin límite. Starlette deja la ruta que
    hizo match en el scope durante el enrutado; si nada coincidió (un 404 a una
    URL inventada) se agrupa bajo una etiqueta fija por el mismo motivo.
    """
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


def _observe(method: str, path: str, status: int, elapsed: float) -> None:
    # 4xx y 5xx cuentan como fallo, pero el label 'status' permite separarlos:
    # para alertar suele usarse solo 5xx, porque un 422 es el cliente mandando
    # datos inválidos, no la aplicación funcionando mal.
    outcome = "success" if status < 400 else "failure"
    labels = (method, path, str(status))

    REQUEST_DURATION.labels(*labels).observe(elapsed)
    REQUESTS_TOTAL.labels(*labels, outcome).inc()


async def metrics_middleware(request: Request, call_next):
    if request.url.path in EXCLUDED_PATHS:
        return await call_next(request)

    # perf_counter y no time(): es monotónico, así que un ajuste del reloj del
    # sistema no puede producir latencias negativas.
    start = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        # Una excepción no controlada nunca llega a producir un Response, así
        # que sin este bloque los errores de servidor no aparecerían en las
        # métricas: justo los que más importa contar.
        _observe(
            request.method,
            _route_template(request),
            500,
            time.perf_counter() - start,
        )
        raise

    _observe(
        request.method,
        _route_template(request),
        response.status_code,
        time.perf_counter() - start,
    )
    return response


def render() -> tuple[bytes, str]:
    """Serializa el registro por defecto en el formato de exposición."""
    return generate_latest(), CONTENT_TYPE_LATEST
