# Registro de uso de IA

Herramienta: **Claude Code** (modelo Opus 5) desde la terminal, con el repo
abierto como contexto.

La IA se configuró en **modo tutor** mediante un `CLAUDE.md` versionado en este
mismo repo: la instrucción explícita es que **no escribe el código de la
solución**. Su papel es explicar conceptos, dividir el trabajo en fases, revisar
lo que escribo, señalar errores y advertir de trampas conocidas. Las únicas
excepciones son las que pido de forma expresa (crear carpetas vacías, ejecutar
`ruff --fix`, redactar este propio archivo) y quedan anotadas abajo.

Cada fase registra los prompts que usé, qué aportó la IA y qué decidí yo.

---

## Fase 0 — Entorno y repo

*(Sesión previa; los prompts están reconstruidos, no son literales.)*

**Prompts usados**
- Planteamiento del reto y petición de dividirlo en fases.
- Comparativa de opciones para la base de datos (PostgreSQL vs Redis).
- Comparativa de registries (GHCR vs Docker Hub).
- Cómo propagar el tag de imagen desde el CI hasta el cluster.

**Qué aportó la IA**
- Plan de fases con criterio de cierre verificable para cada una.
- Comparativa de mecanismos de propagación del tag: `kustomize edit set image`
  con commit-back al repo, frente a alternativas como el ArgoCD Image Updater.

**Qué decidí yo**
- PostgreSQL como base de datos, GHCR como registry.
- Kustomize con commit-back desde el CI (recomendación de la IA, que acepté tras
  entender el flujo GitOps: el cluster solo lee del repo).
- `model/VERSION` como única fuente de verdad de la versión.

---

## Fase 1 — FastAPI en local

**Prompts usados**
- `seguimos con la fase 1`
- `en tu memoria ya tienes las fases, analiza`
- `relee @CLAUDE.md y empecemos con la fase 1, /health lee postgres y la tabla
  la crea un job de migracion`
- `ejecuta la estructura de carpetas sugerida`
- `uvicorn se lanza desde app.main:app haz un nuevo commit de lo que tenemos
  hasta ahora`
- `generaa un commit para los cambios que recien agregue`
- `agregue ruff como herramienta de linteo, analiza el lint y haz una prueba de
  la app con uvicorn`
- `aplica el fix de ruff, escribe nuestro ai-log.md con lo que nuestro historial
  de chat y dame opciones de commit`
- `opcion A y corrige la parte de los logs, antes del commit, de ahora en
  adelante cada que hagas un commit deberas de registrar nuestras conversaciones
  en ai-log para dejar evidencia`

**Qué aportó la IA**

*Revisión del plan de fases.* Señaló que el plan no contemplaba levantar
Prometheus y Grafana, y que documentar paneles y alertas sin haberlos visto con
datos reales se nota en la entrega. De ahí salió la **Fase 3.5**. También propuso
que `ai-log.md` fuera transversal —una entrada por fase— en lugar de una tarea
final, que es la razón de que este archivo exista ya.

*Trampas advertidas antes de escribir código.*
- **livenessProbe contra la base de datos:** si la probe de vida consulta
  Postgres y Postgres cae 30 s, kubelet mata *todos* los pods y una caída de la
  dependencia se convierte en una caída propia. Regla: solo readiness mira
  dependencias externas.
- **Ruta de `model/VERSION`:** abrirlo con ruta relativa depende del directorio
  desde el que se lance el proceso, y en el contenedor será otro.
- **Cardinalidad en las métricas:** usar la ruta cruda (`/items/123`) como label
  crea una serie temporal por cada id; hay que usar la plantilla de la ruta.
- **Jobs y ArgoCD:** un Job es prácticamente inmutable, así que reaplicar el
  manifiesto falla; se resuelve con `hook: PreSync` y `hook-delete-policy`.
- **Doble fuente de verdad de la versión:** `uv init` puso `version = "0.1.0"`
  en `pyproject.toml`, duplicando `model/VERSION`, que es lo que el CI va a
  modificar en la Fase 5.

*Errores que detectó en mi código.*
- `requires-python = ">=3.14<3.15"` sin coma: especificador inválido, `uv lock
  --check` fallaba al parsearlo.
- El `uv.lock` había quedado desincronizado respecto a `pyproject.toml`.
- Ruff `I001`: `fastapi` mezclado con los imports de la stdlib.
- Al probar con el archivo de versión ausente, el `logger.error` salía sin nivel
  ni timestamp (handler de último recurso de Python), porque uvicorn solo
  configura sus propios loggers.

*Comprobaciones que ejecutó a petición mía.*
- Verificó en PyPI que `asyncpg` 0.31.0 publica wheels `cp314`, confirmando que
  Python 3.14 no obliga a compilar en el build.
- Arrancó uvicorn y probó `/version` (200), `/docs` (200) y `/health` (404
  esperado), y comprobó el camino de error escondiendo `model/VERSION`.

**Qué decidí yo**
- `/health` consulta Postgres, y la tabla la crea un **Job de migración**, no la
  app al arrancar.
- **Fallar al arrancar** si falta `model/VERSION`, en vez del `"unknown"` que yo
  mismo había escrito primero: un pod en `CrashLoopBackOff` es ruidoso pero
  honesto, mientras que una app que miente sobre su versión rompe la
  verificación del despliegue en la Fase 6.
- `version = "0.0.0"` en `pyproject.toml` como centinela, con comentario que
  remite a `model/VERSION`.
- `[tool.uv] package = false`: es una aplicación, no una librería publicable.
- Swagger conmutable por variable de entorno (`DOCS_ENABLED`) en lugar de
  dejarlo expuesto sin pensarlo.
- Nivel y formato de log configurables con `LOG_LEVEL`.
- Ruff con `select = ["E", "F", "I", "B", "UP"]` como linter del proyecto.

- El logging del arranque usaba `logging.info(...)` con f-string en lugar del
  logger con nombre del módulo. La IA lo detectó al revisar y le pedí que lo
  corrigiera: ahora es `logger.info("...: %s", version)`, que sale etiquetado
  como `app.main` y difiere el formateo. Antes: `root` sin módulo identificable.
- Los commits se separan por tipo (herramienta / código / documentación) en
  lugar de agrupar todo, para que el historial muestre el progreso por sí solo.
- A partir de aquí, **la IA actualiza este archivo antes de cada commit**, no al
  final de cada fase. Queda anotado como regla en `CLAUDE.md`.

**Postgres local y migración** (prompt: levantar `postgres:16-alpine` con
`docker run`, esperar a `pg_isready`, aplicar `migrations/001_init.sql` y
mostrar el `\d notes`)

- Escribí yo la migración: tabla `notes` con `id BIGINT GENERATED ALWAYS AS
  IDENTITY`, `content TEXT NOT NULL CHECK (length(trim(content)) > 0)` y
  `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
- En el prompt incluí yo la precaución de borrar un `challenge-db` previo con
  `docker rm -f`, porque `CREATE TABLE IF NOT EXISTS` no modificaría una tabla
  vieja con otro esquema. No hizo falta: no existía.
- La IA verificó el resultado: `created_at` es `timestamp with time zone`, el
  `CHECK` aparece como `notes_content_check`, reaplicar el SQL es idempotente
  (`NOTICE: relation "notes" already exists, skipping`) y un `INSERT` de solo
  espacios es rechazado por la restricción.
- Hallazgo del proceso: `docker exec` sin `-i` no reenvía stdin, así que el
  primer intento de aplicar la migración terminó con código 0 **sin crear nada**.
  Un `ON_ERROR_STOP=1` no protege de esto porque no hubo error: psql leyó un
  script vacío.

**Pool de conexiones, endpoints de negocio y métricas**

- Decidí que la app **arranque degradada** si Postgres no responde, en vez de
  fallar como hace con `model/VERSION`. La asimetría es intencionada: un
  `VERSION` ausente es un error de build irrecuperable; Postgres es una
  dependencia externa que se recupera sola. La IA advirtió de la trampa
  concreta: `asyncpg.create_pool()` abre `min_size=10` conexiones al crearse, así
  que sin `min_size=0` el `lifespan` habría reventado igual y el pod acabaría en
  `CrashLoopBackOff`, justo lo contrario de lo decidido.
- La IA encontró un fallo real revisando `create_note`: un `content` de solo
  espacios pasaba la validación de Pydantic (`min_length=1` cuenta espacios),
  llegaba a Postgres y violaba el `CHECK`, y mi `except Exception` lo reportaba
  como `503 base de datos no disponible`. Un error del cliente disfrazado de
  dependencia caída, que además habría disparado en falso la alerta de 5xx de la
  Fase 3.5. Lo arreglé por partida doble: `strip_whitespace=True` en el modelo y
  una excepción propia `InvalidNoteError` en la capa de datos.
- También señaló que `limit` no tenía cota (`?limit=10000000` es una consulta
  cara gratis); lo acoté con `Query(ge=1, le=100)`.
- Escribí `app/metrics.py` a partir de un primer esqueleto suyo, cambiando las
  decisiones que no me convencieron: buckets explícitos en lugar de los del
  paquete, `status` también como label del histograma, y `outcome` reducido a
  `success`/`failure` (el label `status` ya permite separar un 500 de un 503).
- Trampas que incorporé del repaso: usar la plantilla de la ruta y no la URL
  concreta como label, contar las excepciones no controladas —que nunca llegan a
  tener `response.status_code`— y excluir `/metrics` de sus propias métricas.

**Verificación de cierre de la Fase 1** (la ejecutó la IA a petición mía)

- Tráfico real contra los endpoints: `201` al crear, `422` con espacios en
  blanco y con 1500 caracteres, `422` con `limit` fuera de rango, `404` a una
  URL inventada (agrupada como `path="unmatched"`).
- Camino degradado, parando el contenedor de Postgres: `/health` y `/version`
  siguen en `200`, mientras `/ready`, `POST /notes` y `GET /notes` responden
  `503`. Al volver a arrancar la base, `/ready` vuelve a `200` sin reiniciar la
  app: el pool se recupera solo.
- `/metrics` expone el histograma con buckets poblados y el contador separando
  `outcome="success"` de `outcome="failure"`, con `Content-Type` del formato de
  exposición de Prometheus.

- Último apunte del repaso: `pydantic` se importaba directamente pero llegaba
  como dependencia transitiva de `fastapi`. Lo declaré explícito con `uv add`,
  para no depender de que fastapi mantenga esa dependencia en el futuro.

**Qué pedí que hiciera la IA directamente**
- Crear las carpetas vacías `app/`, `model/` y `migrations/`.
- Ejecutar `uv lock` tras corregir yo el `requires-python`, y `ruff check --fix`
  para ordenar los imports.
- Corregir la llamada de logging del `lifespan`.
- Los commits de esta fase y la redacción de este archivo.
