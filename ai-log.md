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

---

## Fase 2 — Dockerfile multi-stage

**Prompts usados**
- `commitea eso y empecemos con la fase 2`
- `ya tengo el Dockerfile analizalo y haz las pruebas necesarias para validarlo`
- `ya agregué el COPY, corre el build otra vez`
- `ya lo moví, corre el build`
- `corrige el .dockerignore`

**Qué aportó la IA**

Antes de escribir el Dockerfile planteó las decisiones en vez de resolverlas:
qué cruza entre etapas, en qué orden van los `COPY` para no invalidar la capa de
dependencias en cada commit, por qué `--frozen` es lo que se quiere en un build
reproducible, y por qué `alpine` —pese a dar la imagen más pequeña— obligaría a
compilar `asyncpg`, que usa musl en lugar de glibc.

Advirtió de que los scripts de un venv guardan rutas absolutas, así que la ruta
de construcción y la de destino deben coincidir, y de que uvicorn escuchando en
`127.0.0.1` dentro de un contenedor no es accesible desde fuera.

**Errores que detectó en mi Dockerfile**
- La etapa `builder` no copiaba `pyproject.toml` ni `uv.lock`, así que
  `uv sync --frozen` fallaba con *"No pyproject.toml found"*.
- Al corregirlo puse el `COPY` en la etapa `runtime`. Cada `FROM` empieza un
  sistema de archivos nuevo: lo que se copia en la segunda etapa no existe en la
  primera. Además estaba antes del `WORKDIR`, con lo que los archivos aterrizaban
  en `/` y no en `/app` — el destino relativo de un `COPY` depende del `WORKDIR`
  vigente en ese momento.
- `.gitignore/` en el `.dockerignore` no excluía nada: la barra final solo casa
  con directorios.

**Validación de la imagen** (la ejecutó la IA)
- `whoami` → `appuser`, `uid=10001`; escribir en `/app` da *Permission denied*.
- `model/` sigue siendo hermana de `app/` dentro de la imagen, así que la
  resolución de `model/VERSION` por `__file__` funciona con el WORKDIR del
  contenedor.
- `uv` y `ruff` no están en la imagen final: el multi-stage cumple su función.
- Contra el Postgres local vía `host.docker.internal`: `/version`, `/health`,
  `/ready`, `POST /notes` y `/metrics` responden correctamente.
- Tocando `app/main.py` y reconstruyendo, la capa `RUN uv sync` sale `CACHED`:
  el orden de los `COPY` hace su trabajo.
- Imagen de 287 MB, de los que 57,8 MB son el venv y el resto la base slim.

**Qué decidí yo**
- `python:3.14-slim` en vez de alpine, para no compilar `asyncpg`.
- uv entra al build con `COPY --from=ghcr.io/astral-sh/uv:0.12.3`, con versión
  fija, y no queda en la imagen final.
- Los archivos son propiedad de `root` y `appuser` solo los lee: un proceso que
  no puede escribir su propio código no puede ser modificado en caliente.
- `migrations/` queda fuera del contexto de build; cómo llega el SQL al Job de
  migración se decide en la Fase 3.

**Consecuencia anotada para la Fase 7:** la imagen no trae `ps` ni herramientas
de diagnóstico. Es lo deseable en producción, pero significa que una latencia
alta no se depura desde dentro del contenedor, sino con métricas de
cAdvisor/kubelet y `kubectl debug` con un contenedor efímero.

---

## Fase 3 — Manifiestos de Kubernetes

**Prompts usados**
- Las cuatro decisiones de diseño respondidas por mí antes de escribir YAML
  (imagen al cluster, SQL al Job, Service headless, Job fallido), y
  `ya se escribieron nuevos archivos en k8s son los manifiestos que se
  utilizaran, analizalos y lanza las pruebas necesarias para acreditar la fase`
- `ya corregí los tres, valida el Job borrando la tabla`
- `ya corregí los dos, valida y commitea`

**Qué decidí yo**
- **Imagen al cluster:** `k3d image import` con `imagePullPolicy: IfNotPresent`,
  no `Never`, para que en la Fase 4 el mismo manifiesto pueda bajarla de GHCR sin
  tocar nada.
- **SQL al Job:** dentro de la imagen de la app, con `python -m app.migrate`. El
  esquema y el código que lo consulta comparten tag, así que es imposible
  desplegar uno sin el otro, y el mismo comando sirve en local.
- **Service headless** (`clusterIP: None`) porque el StatefulSet lo exige en
  `serviceName`: de ahí salen los nombres por pod (`postgres-0.postgres`). Un
  Service normal daría una IP virtual que balancea, irrelevante con una réplica
  pero que impediría dirigirse a una concreta el día que haya varias.
- Escribí `app/migrate.py` con reintentos de conexión en vez de orquestar el
  orden de arranque entre el Job y Postgres.

**Errores que detectó la IA**
- **El Job no se podía aplicar.** En `migration-job.yaml` el `securityContext:`
  estaba vacío y sus cuatro campos colgaban del `spec` del pod. El servidor lo
  rechazaba con *strict decoding error: unknown field
  "spec.template.spec.runAsNonRoot"*. Consecuencia que yo no había visto: la
  tabla que había en el cluster la había creado yo a mano, **no el Job**, así que
  el requisito no estaba realmente demostrado.
- **La contraseña estaba en el ConfigMap** (`POSTGRES_PASSWORD: app`) además de
  en el Secret, con valores distintos. La IA comprobó cuál ganaba: en `envFrom`
  la última fuente pisa a la anterior, así que funcionaba por accidente. Si
  alguien reordenaba esas dos entradas, Postgres arrancaba con una contraseña y
  la app se conectaba con otra.
- `app/migrate.py` no pasaba el linter (`E501` y formato), lo que habría parado
  el CI de la Fase 4.
- El tag de la imagen estaba duplicado en los manifiestos y en
  `kustomization.yaml`; lo quité de los YAML para que `images:` sea la única
  verdad, que es lo que el CI tocará en la Fase 5.

**Validación del Job** (ejecutada por la IA a petición mía)
1. `DROP TABLE notes` y confirmación de que la tabla no existe.
2. Con la tabla borrada: `POST /notes` → 503, pero **`/ready` seguía en 200**.
3. `kubectl apply -k` → el Job se completa en 3 s y sus logs muestran
   `Migración aplicada: 001_init.sql`.
4. `\d notes` confirma tabla, PK, CHECK y `timestamptz`.
5. `POST /notes` → 201 y `GET` → 200, **con 0 reinicios de los pods**.
6. Idempotencia: relanzado con la tabla ya creada, completa sin error y sin
   duplicar datos.
7. Reintentos: escalando Postgres a 0, el Job falla 5 veces con *Name or service
   not known* y aplica la migración en cuanto la base vuelve.

**Límite que cerré:** la IA señaló que `/ready` hacía `SELECT 1`, así que
comprobaba que la base responde pero no que el esquema exista: con la tabla
borrada devolvía 200 mientras `POST /notes` daba 503. Cambié la probe a
`SELECT to_regclass('public.notes')`, una consulta al catálogo —barata— que
distingue "la base no responde" de "el esquema no está aplicado".

Verificado en el cluster: con la tabla presente, `/ready` → 200
`{"database":"ok","schema":"ok"}`; tras un `DROP TABLE`, → 503
`{"database":"ok","schema":"missing"}`, los pods salen del Service sin que
`/health` se vea afectado y sin reinicios. Al aplicar el Job vuelven a Ready
solos y `POST /notes` responde 201.

**Otras comprobaciones**
- `securityContext` efectivo en los pods: `uid=10001`, `runAsNonRoot`,
  `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`,
  `capabilities: drop ALL` y `readOnlyRootFilesystem` (ni `/tmp` es escribible).
- `DATABASE_URL` se compone con `$(VAR)`, que solo expande variables declaradas
  en `env` y no las de `envFrom`.
- DNS del headless: `postgres` y `postgres-0.postgres` resuelven a la misma IP.
- **RollingUpdate sin pérdida de peticiones:** 120 peticiones durante un
  `rollout restart`, 0 fallos, gracias a `maxUnavailable: 0`, la readinessProbe y
  el `preStop`. Un primer intento midiendo desde fuera con `port-forward` dio 157
  fallos falsos: el port-forward muere con el pod al que apunta.
- PVC `data-postgres-0` Bound de 1Gi sobre `local-path`, creado por el
  `volumeClaimTemplate`.

---

## Fase 3.5 — Prometheus, Grafana e instrumentación de la base de datos

Fase que no pide el enunciado, pero sin la que la sección de observabilidad del
README se escribiría a ciegas. Salió de una observación de la IA al revisar el
plan de fases.

**Prompts usados**
- `el ServiceMonitor va en el repo, analiza los nuevos cambios y valida`
- `quito la alerta de throttling y repasemos las queries de cada panel`
- `si, añado el histograma de la base`
- `ya corregí los dos, llévalo al cluster y validamos en Grafana`

**Qué decidí yo**
- `kube-prometheus-stack` se instala con Helm a mano (es infraestructura de
  plataforma), pero **el `ServiceMonitor` y las `PrometheusRule` viven en este
  repo**: describen mi aplicación, qué expone y cuándo debe alertar.
- **Quitar la alerta de throttling.** No declaro `limits.cpu`, así que no hay
  cuota CFS y el throttling es imposible por construcción. Para el README es una
  respuesta más fuerte que un panel vacío: descarta la capa contenedor de
  entrada.
- **Dos histogramas de base de datos, no uno.** `db_acquire_duration_seconds`
  mide la espera por una conexión del pool y `db_query_duration_seconds` lo que
  tarda Postgres. Con uno solo, un pool agotado parecería una base de datos
  rápida mientras el usuario espera.
- Gauges del pool leídas con `set_function` en el momento del scrape, sin coste
  en el camino de las peticiones.
- Las métricas de datos se declaran en `db.py` y no en `metrics.py`, porque ese
  módulo importa starlette: si `db.py` lo importara, la capa de datos arrastraría
  el framework web. `prometheus_client` usa un registro global, así que ambas
  familias salen por `/metrics` igual.
- Buckets desde 0,5 ms para la base de datos: con los de HTTP (desde 5 ms) todas
  las consultas caerían en el primer bucket y el p95 no distinguiría nada.

**Pregunta que me planteó la IA y respondí**
Si `/notes` sube a 800 ms, no hay throttling posible, el nodo está tranquilo y el
p95 de la base es normal, ¿qué queda? **Encolamiento en el pool**: las peticiones
no trabajan, esperan turno por un recurso lógico que no aparece en ninguna
métrica de CPU ni de Postgres. Segunda causa con el mismo perfil: bloquear el
event loop con trabajo síncrono.

**Errores que detectó la IA**
- **Mi señal de agotamiento del pool era falsa.** Propuse alertar con
  `db_pool_idle == 0`, pero con `min_size=0` el pool no abre conexiones hasta
  usarlas: en reposo `size=0, idle=0`, idéntico al caso de agotamiento. La
  condición correcta es `(db_pool_size == db_pool_max_size) and
  (db_pool_idle == 0)`. Es una consecuencia de la decisión de la Fase 1 de
  arrancar degradado, que cambió el significado de una métrica dos fases después.
- **Las esperas que acababan en timeout no se medían.** En `check_ready`, el
  `asyncio.wait_for` cancela la corrutina antes de la línea que observa el
  histograma de espera: justo cuando el pool está tan saturado que las probes
  expiran, el dato desaparecía. Lo moví a un `finally`.
- **Las violaciones de restricción contaban como `outcome="error"`**, mezclando
  un error del cliente con un fallo de la base. Ahora llevan `outcome="invalid"`,
  coherente con la separación 400/503 de la capa HTTP.

**Errores en las queries que la propia IA me había propuesto** (los encontró al
validarlas contra el cluster)
- `node_load1 / count(...) by (instance)` devolvía vacío: una operación entre
  vectores exige que coincidan **todas** las labels, y tras el `count` solo queda
  `instance`. Correcto: `node_load1 / on(instance) group_left count(...)`.
- Una variante con `bool` de la señal de agotamiento daba falso positivo con
  `idle=10`. **`and` en PromQL opera sobre la existencia de series, no sobre
  valores**: con `bool` la comparación deja de filtrar y la serie sobrevive.

**Experimento del pool** (200 peticiones, 50 en paralelo, mismo tráfico):

| | pool = 1 | pool = 10 |
|---|---|---|
| Tiempo HTTP total | 4,98 s | 2,19 s |
| Esperando conexión | 4,69 s (94 %) | 1,22 s (56 %) |
| Ejecutando consulta | 0,09 s (2 %) | 0,33 s (15 %) |

Con el pool a 1, el 94 % del tiempo es cola. Con un solo histograma se habría
visto una media de 0,46 ms por consulta y la conclusión habría sido que Postgres
va perfecto.

**Validación en el cluster, a través del datasource de Grafana**
```
p95 HTTP /notes        4,77 ms
  ├─ espera de pool    0,48 ms
  └─ consulta a la DB  1,01 ms
CPU del contenedor     0,26 cores     memoria/límite  19,7 %
saturación del nodo    7,3 %          carga/núcleo    0,31
```
Con 428 consultas/s sostenidas. Las cuatro alertas cargan con `salud=ok`.

**Dashboard provisionado**
- Decidí no incrustar el JSON dentro del YAML: vive como archivo `.json` real en
  `k8s/base/dashboards/` y lo empaqueta el `configMapGenerator` de Kustomize con
  `disableNameSuffixHash: true` y el label `grafana_dashboard: "1"`. Así el JSON
  se puede editar y diferenciar como lo que es.
- Antes de escribir nada comprobé que el sidecar corre con `NAMESPACE=ALL`, así
  que descubre el ConfigMap aunque viva en `challenge` junto a la app. Si hubiera
  estado limitado a `monitoring`, el fallo habría sido silencioso.
- Datasource como variable `${DS_PROMETHEUS}` de tipo datasource, no un uid fijo:
  el dashboard es portable a cualquier Grafana sin editar el JSON.
- Tres filas, una por capa: código, base de datos, contenedor y nodo. El panel
  central superpone p95 de espera por conexión y p95 de consulta: cuando la línea
  de espera se despega de la de consulta, el diagnóstico es inmediato.
- Verificado: el sidecar escribe `/tmp/dashboards/platform-challenge.json`, el
  dashboard aparece en Grafana como provisionado, y **las 16 queries de los 9
  paneles devuelven datos** ejecutadas a través del datasource de Grafana.

**Lección repetida dos veces (Fase 3.5), para el README:** una serie que nace
alta no produce `rate()`. Tanto la ráfaga de 5xx como las 300 peticiones de prueba
quedaron invisibles (`p95 = nan`) porque todos los incrementos ocurrieron entre
dos scrapes y la primera muestra de una serie es su línea base. Con carga
sostenida los percentiles salen correctos. Es un argumento a favor de `for:`
largos en las alertas y una advertencia sobre las pruebas de carga cortas.

---

## Fase 4 — CI en GitHub Actions

**Prompts usados**
- `sube y empecemos con la fase 4`
- `analiza los cambios que recien agregue y sube la version de 0.1.0 a 0.2.0
  para que sea una version nueva y limpia`
- `ya agregue lo de concurrencia, analiza y si todo ok commitea y da segimiento`

**La trampa que advirtió la IA antes de escribir el workflow:** mi Mac es arm64 y
los runners de GitHub son amd64. Una imagen construida en el CI sin más no podría
ejecutarse en mi k3d, y el síntoma no es "arquitectura incorrecta" sino un
`exec format error` o un CrashLoopBackOff sin logs útiles. No lo había notado
porque hasta ahora uso `k3d image import` con una imagen construida en local.
Elegí **multi-arch** (`linux/amd64,linux/arm64`): más lento por la emulación
QEMU, pero es lo único coherente con que ArgoCD despliegue lo que hay en GHCR.

**Qué decidí yo**
- **Tag inmutable:** el CI comprueba con `docker manifest inspect` que el tag no
  exista y **falla** si ya está publicado. Un tag que cambia de contenido rompe
  la trazabilidad: quien tenga `0.1.0` desplegado no sabría cuál de las dos es.
  Para que no sea un incordio, el trigger lleva `paths:`, así que cambios en el
  README o en los manifiestos no disparan publicación, y además se publica
  siempre `:sha-<commit>`, que es el identificador realmente inmutable.
- **PR vs main:** en pull request se construye todo —se valida que el Dockerfile
  compila— pero no se publica, con `push: ${{ github.event_name == 'push' }}`.
- **Trivy en dos pasos:** `CRITICAL` bloquea, `HIGH,MEDIUM` informa, ambos con
  `ignore-unfixed`. Un CI que falla por un CVE sin parche disponible es un CI que
  se acaba ignorando.
- **`USER 10001` en vez de `USER appuser`**: Kubernetes valida `runAsNonRoot`
  comparando UIDs; con un nombre no puede saber si es root sin arrancar el
  contenedor.

**Errores que detectó la IA**
- **Faltaba `concurrency`.** Dos pushes seguidos lanzaban dos runs simultáneos:
  ambos pasarían la comprobación de "el tag no existe" antes de que ninguno
  publicara, y el segundo sobrescribiría al primero — justo lo que la
  comprobación pretende evitar. Lo añadí con `cancel-in-progress` **solo en
  PRs**: cancelar un push a mitad de publicación dejaría el registry a medias.
- El filtro de `paths` estaba solo en `push` y no en `pull_request`, así que un
  PR que tocara el README lanzaba el build multi-arch completo.
- **Trivy solo escanea amd64**, porque `load: true` solo puede cargar la
  plataforma del runner. La imagen arm64 que se publica no pasa por el escáner.
  Queda documentado como limitación conocida.
- El `apt-get upgrade` que añadí al Dockerfile quita CVEs pero **rompe la
  reproducibilidad**: dos builds del mismo commit en semanas distintas dan
  imágenes distintas. Decisión consciente, a explicar en el README.

**Efecto secundario intencionado:** el filtro de `paths` no incluye `k8s/**`, así
que el commit-back de la Fase 5 sobre `kustomization.yaml` no volverá a disparar
el CI. Sin eso habría un bucle infinito.

**Política que asumo:** con el tag bloqueante, todo push a `main` que toque
`app/**` sin subir `model/VERSION` deja el CI en rojo. Es deliberado, y por eso
esta fase sube la versión a **0.2.0**.

---

## Fase 5 — Commit-back con `kustomize edit set image`

**Prompts usados**
- `ya se escribio apliacation.yaml para la fase 5 de argo, analiza`
- (respuesta razonada sobre la asimetría del fallo del commit-back)
- `sube a 0.3.0, commitea y sigue el run`

**Qué decidí yo**
- **`update-manifests` va después de publicar la imagen.** Los dos fallos
  posibles no son simétricos: si falla el commit-back tras publicar, queda una
  imagen inerte que nadie usa; si el manifiesto se actualizara antes de publicar,
  apuntaría a una imagen inexistente y el resultado sería `ImagePullBackOff` en
  producción. El orden garantiza que el repo nunca referencie algo no publicado.
- **Coste operativo asumido:** si el commit-back falla y se relanza el workflow
  entero, `build-push` fallará porque el tag ya existe. La recuperación correcta
  es *Re-run failed jobs*, que conserva las salidas de los jobs que pasaron. Si
  se relanza todo, hay que subir la versión.
- **Sync-waves en lugar del hook `PreSync`** que la IA había recomendado en la
  Fase 1: Postgres en la wave -1, el Job de migración como hook `Sync` en la
  wave 0 y la app en la wave 1. Con `PreSync`, en un cluster limpio el Job
  arrancaría antes de que Postgres exista, agotaría sus 30 reintentos y la
  sincronización fallaría entera.
- `kustomization.yaml` reescrito a la forma canónica de kustomize, para que cada
  commit-back produzca un diff de una sola línea en lugar de reindentar el
  archivo entero.

**Qué detectó la IA**
- El checkout de `update-manifests` era superficial (`fetch-depth` por defecto),
  y el bucle de reintento hace `git pull --rebase`: un rebase sobre un clon
  superficial puede fallar justo en el caso de carrera para el que se escribió el
  bucle. Corregido con `fetch-depth: 0`.
- Al normalizar el bloque `labels:` desapareció `includeSelectors: false`. La IA
  comprobó el render: los selectores siguen intactos porque en el transformador
  `labels:` ese valor ya es `false` por defecto. De lo contrario, el `selector` de
  un Deployment es inmutable y ArgoCD habría fallado con `field is immutable`.
- Verificó además que la reindentación no altera el render: la única diferencia
  frente al commit anterior son los dos `sync-wave` añadidos a propósito.
- **Límite de la invariante:** el orden protege contra el caso habitual, pero
  `update-manifests` no vuelve a comprobar que la imagen exista. Si alguien
  borrara el paquete entre el push y el commit-back, el repo apuntaría a algo
  inexistente igualmente. La garantía la da el orden, no una verificación.

**Antes, en la Fase 4:** la IA me propuso `aquasecurity/trivy-action@0.28.0` sin
verificar que existiera, y el primer run murió al resolver la acción (los tags de
ese repo llevan prefijo `v`). GitHub resuelve las acciones antes de ejecutar el
job, así que no llegó a construirse ni publicarse nada. En la revisión siguiente
la IA comprobó una por una las versiones de las ocho acciones contra la API de
GitHub, y ahí resultó que su otra sugerencia —bajar a `checkout@v5`— también era
incorrecta: la última es `v7`.

**El fallo del commit-back, en vivo.** Al ejercitar la Fase 5 con `0.3.0`,
`build-push` publicó la imagen y `update-manifests` falló:
`/usr/local/bin/kustomize exists. Remove it first.` — los runners de
`ubuntu-latest` ya traen kustomize preinstalado y el instalador oficial se niega
a sobrescribirlo.

La invariante aguantó: quedó una imagen publicada que nadie usaba, y en ningún
momento el repo apuntó a algo inexistente. El fallo cayó del lado bueno de la
asimetría.

Pero la recuperación **no** fue la que yo había descrito. *Re-run failed jobs*
reejecuta el workflow tal como estaba en ese commit, y ese workflow era
justamente el roto. Y como `.github/workflows/ci.yml` sí está en el filtro de
`paths`, subir el arreglo dispara un run nuevo que choca con la comprobación de
tag inmutable. Conclusión que va al README: *Re-run failed jobs* recupera un
fallo transitorio; **si el fallo está en el propio workflow, hay que subir la
versión**. De ahí el salto a `0.4.0`.

Arreglo: instalar kustomize en `$RUNNER_TEMP` y anteponerlo al `PATH` con
`$GITHUB_PATH`, en vez de en `/usr/local/bin`. Mantiene la versión fijada en
lugar de depender de la que traiga la imagen del runner, que es coherente con
`uv.lock` y con los tags fijos del resto del proyecto.

Alternativa que consideré y descarté: que un tag ya existente no fuera error
fatal, sino que saltara build y push dejando correr `update-manifests`. Haría el
pipeline idempotente, pero pierde el aviso de que olvidé subir la versión.

---

## Fase 6 — ArgoCD y GitOps de punta a punta

**Prompts usados**
- `instala argocd y empecemos con la fase 6`
- `sube a 0.5.0 y sigue la cadena completa`

Instalé ArgoCD v3.5.3 con el chart `argo-cd` 10.9.2 en el namespace `argocd`, y
apliqué a mano `k8s/argocd/application.yaml`: ese manifiesto es el arranque, no
forma parte de `k8s/base` y por tanto ArgoCD nunca se gestiona a sí mismo.

**Adopción de lo desplegado a mano.** ArgoCD adoptó los recursos que yo había
creado con `kubectl apply -k` —coinciden nombre, tipo y namespace— y en la
primera sincronización corrigió lo único que diferían: la imagen. El cluster
pasó de `platform-challenge:0.1.0`, importada a mano en k3d, a
`ghcr.io/car7oskdr/platform-challenge:0.4.0`, bajada del registry. El Job de
migración se ejecutó como hook en su wave antes de desplegar la app.

**selfHeal comprobado:** escalé el Deployment a 5 réplicas con `kubectl scale` y
ArgoCD lo devolvió a 2 en segundos. A partir de aquí el cluster obedece al
repositorio, no a mí: cualquier cambio que no pase por un commit se deshace solo.

**Cadena completa ejercitada con la 0.5.0.** `model/VERSION` → CI (lint,
gitleaks, build multi-arch y push a GHCR en 2m39s) → commit-back `95c4d74` que
cambia `newTag: 0.4.0` por `0.5.0` → ArgoCD detecta el commit, pasa a
`OutOfSync` y sincroniza → el cluster acaba corriendo
`ghcr.io/car7oskdr/platform-challenge:0.5.0` y `/version` responde `0.5.0`. El
único `kubectl` ejecutado en toda la cadena fue de lectura.

Durante el rollout se vieron cuatro pods a la vez —dos nuevos y dos viejos, uno
de ellos terminando—, que es `maxUnavailable: 0` con `maxSurge: 1`, y el hook de
migración ya completado antes de que la app se desplegara.

**Matiz sobre `prune` que descubrí con un pod de carga creado a mano:** ArgoCD no
borra "todo lo que no está en el repo", sino lo que él gestionó alguna vez y ya
no aparece. Un pod creado con `kubectl run`, sin las marcas de seguimiento de la
Application, le resulta invisible. Permite depurar con pods efímeros sin que los
borre, pero significa que GitOps no garantiza que el cluster contenga *solo* lo
que hay en git.

**Ingress (cierre de la Fase 6).** El puerto 8080 del host estaba mapeado al
loadbalancer de k3d desde la Fase 0 y Traefik respondía, pero no había ningún
Ingress: `http://localhost:8080/version` daba 404 y la app solo era accesible con
`port-forward`. Lo detectó la IA al preparar la sección "cómo correrlo" del
README. Se añade un Ingress sin `host` —k3d publica el loadbalancer en localhost,
así que cualquier cabecera entra— y se despliega **por commit**, no con
`kubectl`: es la primera prueba de que el flujo GitOps ya es el camino normal
para cambiar el cluster.

**Superficie expuesta reducida.** El primer Ingress publicaba `/` como prefijo, y
la IA señaló que eso dejaba `/metrics` accesible desde fuera: fuga de
información —endpoints, volumen de tráfico, detalles de la infraestructura— sin
necesidad, porque Prometheus scrapea el pod por la red interna del cluster. Pedí
que no estuviera expuesto y que la solución fuera lo más formal posible.

Se descartó un middleware de Traefik que bloqueara la ruta, porque es una lista
negra: protege lo enumerado y deja pasar todo lo que se añada después. En su
lugar, el Ingress enumera las rutas publicadas y **deniega por defecto**. Quedan
fuera `/metrics`, `/health`, `/ready`, `/docs` y `/openapi.json`; las probes las
hace el kubelet contra el pod y Swagger es documentación, no API. Publicadas solo
`/notes` y `/version`.

---

## Fase 7 — README

**Prompts usados**
- `escribe el README con la estructura que propusiste`
- `commitea y sube`

Este es el único artefacto extenso que pedí que redactara la IA, sobre una
estructura que ella misma había propuesto y que acepté: arquitectura, cómo
correrlo, decisiones, observabilidad, diagnóstico de latencia, comportamiento
verificado, limitaciones y uso de la IA. Después lo repasé entero y ajusté el
tono y varias formulaciones.

La condición que puse —y que la IA respetó— es que **no hubiera una sola cifra
inventada**: todos los números del README salen de mediciones hechas durante el
proyecto y registradas en este mismo archivo (el experimento del pool, la
descomposición del p95 en el cluster, las 120 peticiones sin fallos durante un
rollout, los 1,8 ms del `/ready` degradado, los 70 MB comprimidos de la imagen).
La IA contrastó además cada afirmación contra el código y el cluster antes de
darlas por buenas.

Quedan dos marcas `TODO`: las capturas del dashboard y el enlace al reporte
Paxel. También señaló que el comando de Helm de `kube-prometheus-stack` que
documentó está reconstruido a partir de los selectores que vio en el cluster, no
del comando que ejecuté yo, y que debo verificarlo.

**Antes del README, cierre de la superficie expuesta.** Al preparar la sección
"cómo correrlo" la IA detectó que el Ingress con prefijo `/` dejaba `/metrics`
accesible desde fuera. Pedí cerrarlo de la forma más formal posible y se
sustituyó por una lista blanca: se enumeran las rutas publicadas y el resto queda
fuera por defecto. Verificado desde fuera (`/metrics`, `/health`, `/ready`,
`/docs` → 404) y desde dentro del pod (los tres → 200), con Prometheus siguiendo
`up = 1` en las dos réplicas.

**Las capturas corrigieron una alerta.** Al revisar las dos imágenes del
dashboard antes de commitearlas, la IA contrastó el texto con lo que mostraban y
encontró dos afirmaciones falsas: la captura no se había tomado con
`DB_POOL_MAX_SIZE=1` —el panel muestra el máximo configurado plano en 20, es
decir 10 por réplica, y la variable nunca llegó a los pods— y las conexiones
libres no caían a 0 en ningún momento.

Lo segundo destapó un problema de diseño real: en el panel del pool, *abiertas* y
*libres* suben y bajan juntas. Las gauges se leen en el instante del scrape, cada
15 s, y con consultas de 1-2 ms es improbable que ese instante coincida con el
momento en que las conexiones están ocupadas. Mi alerta
`PoolDeConexionesAgotado`, basada en `db_pool_size == db_pool_max_size and
db_pool_idle == 0`, **no se habría disparado nunca**, pese a que la cola era real
y el histograma de espera la medía subiendo de 0,5 ms a casi 5 ms.

Sustituida por `EsperaAltaPorConexion`, sobre el p95 de
`db_acquire_duration_seconds`: un histograma acumula todas las esperas en lugar
de muestrear una foto cada 15 s. Las gauges se quedan como panel de contexto.

El texto del README se ajustó a lo que las capturas muestran de verdad —30
peticiones concurrentes contra el pool por defecto— y la corrección se documenta
en la propia sección, porque el hallazgo vino de mirar los paneles.
