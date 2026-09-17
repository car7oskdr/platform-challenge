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

**Qué pedí que hiciera la IA directamente**
- Crear las carpetas vacías `app/`, `model/` y `migrations/`.
- Ejecutar `uv lock` tras corregir yo el `requires-python`, y `ruff check --fix`
  para ordenar los imports.
- Corregir la llamada de logging del `lifespan`.
- Los commits de esta fase y la redacción de este archivo.
