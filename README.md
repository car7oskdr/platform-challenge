# Platform Challenge — mini-plataforma sobre k3d

API en FastAPI con PostgreSQL, empaquetada en una imagen multi-arquitectura,
publicada en GHCR por GitHub Actions y desplegada en un cluster k3d mediante
GitOps con ArgoCD. La versión vive en `model/VERSION` y es la única fuente de
verdad: subirla es lo único que hace falta para desplegar.

```
model/VERSION ──► GitHub Actions ──► GHCR ──► commit-back ──► ArgoCD ──► k3d
    0.5.0          lint · scans        imagen     kustomize      detecta    rolling
                   build multi-arch    0.5.0      edit set image el commit  update
```


---

## Arquitectura

### El flujo de entrega

1. Un push a `main` que toque el código, el `Dockerfile` o `model/VERSION`
   dispara el workflow.
2. `lint` (ruff, `uv lock --check`, hadolint) y `secrets` (gitleaks) corren en
   paralelo. Si alguno falla, no se construye nada.
3. `build-push` comprueba que el tag no exista ya en el registry, construye
   para `linux/amd64` y `linux/arm64`, escanea con Trivy y publica dos tags:
   `:0.5.0` y `:sha-<commit>`.
4. `update-manifests` ejecuta `kustomize edit set image` y hace commit-back
   a `main`. Ese commit no vuelve a disparar el CI, porque `k8s/**` no está en
   el filtro de `paths`.
5. ArgoCD detecta el commit y sincroniza el cluster.

### Lo que corre en el cluster

| Componente | Forma | Notas |
|---|---|---|
| API FastAPI | Deployment, 2 réplicas | `RollingUpdate` con `maxUnavailable: 0` |
| PostgreSQL | StatefulSet + `volumeClaimTemplate` | 1Gi sobre `local-path`, Service headless |
| Migración | Job (hook `Sync` de ArgoCD) | misma imagen que la app |
| Ingress | Traefik | publica solo `/notes` y `/version` |
| Observabilidad | `ServiceMonitor` + `PrometheusRule` + dashboard | en este repo; el stack se instala aparte |

El orden de arranque lo imponen los sync-waves de ArgoCD:

```
wave -1   PostgreSQL          hasta que no esté sano, no se sigue
wave  0   Job de migración    hook Sync; aplica migrations/*.sql
wave  1   API + Ingress       solo cuando el esquema ya existe
```

### Endpoints

| Ruta | Para qué | ¿Expuesto fuera del cluster? |
|---|---|---|
| `GET /version` | versión desplegada, leída de `model/VERSION` | sí |
| `POST /notes`, `GET /notes` | endpoint de negocio contra PostgreSQL | sí |
| `GET /health` | liveness: ¿responde el proceso? | no |
| `GET /ready` | readiness: ¿base viva y esquema aplicado? | no |
| `GET /metrics` | métricas en formato Prometheus | no |

---

## Cómo correrlo

### En local, sin Kubernetes

```bash
# PostgreSQL
docker run -d --name challenge-db \
  -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app -e POSTGRES_DB=challenge \
  -p 5432:5432 postgres:16-alpine

# Dependencias y esquema
uv sync
export DATABASE_URL="postgresql://app:app@localhost:5432/challenge"
uv run python -m app.migrate

# La API
uv run uvicorn app.main:app --reload
```

```bash
curl localhost:8000/version
curl -X POST localhost:8000/notes -H 'content-type: application/json' \
     -d '{"content":"hola"}'
curl localhost:8000/metrics
```

La configuración se pasa por entorno; los valores por defecto están en
`.env.example`:

| Variable | Por defecto | Qué hace |
|---|---|---|
| `DATABASE_URL` | — | obligatoria; sin ella la app no arranca |
| `LOG_LEVEL` | `INFO` | nivel de log |
| `DOCS_ENABLED` | `true` | expone Swagger y el OpenAPI |
| `DB_POOL_MAX_SIZE` | `10` | conexiones máximas del pool |
| `DB_COMMAND_TIMEOUT` | `5` | timeout de cada consulta, en segundos |
| `DB_PING_TIMEOUT` | `2` | timeout de la consulta de readiness |

### En el cluster

```bash
k3d cluster create mini-platform --agents 2 -p "8080:80@loadbalancer"
```

Observabilidad (los selectores vacíos son necesarios para que el operador
acepte `ServiceMonitor` sin su label de release):

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.ruleSelectorNilUsesHelmValues=false
```

ArgoCD y el despliegue:

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm install argocd argo/argo-cd --version 10.9.2 \
  --namespace argocd --create-namespace \
  --set configs.params."server\.insecure"=true

kubectl apply -f k8s/argocd/application.yaml
```

Ese `apply` es el único paso manual del proyecto. La Application es el
manifiesto de arranque y por eso no forma parte de `k8s/base`.

```bash
curl http://localhost:8080/version
curl http://localhost:8080/notes
```

Accesos de consola:

```bash
kubectl -n argocd port-forward svc/argocd-server 8081:80
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d

kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

### Desplegar una versión nueva

```bash
echo -n "0.6.0" > model/VERSION
git commit -am "chore(release): 0.6.0" && git push
```

El CI publica, hace commit-back y ArgoCD sincroniza.

---

## Decisiones y por qué

| Decisión | Motivo |
|---|---|
| **PostgreSQL como StatefulSet** | identidad de red estable y volumen propio por réplica; un Deployment con PVC no da ninguna de las dos cosas |
| **Service *headless*** | el StatefulSet lo exige en `serviceName`: de ahí salen los nombres por pod (`postgres-0.postgres`). Un Service normal daría una IP virtual que balancea, inútil para dirigirse a una instancia concreta |
| **`asyncpg` con pool en el `lifespan`** | una conexión por request es un coste fijo evitable; el pool se crea al arrancar y se cierra al apagar |
| **`min_size=0` en el pool** | permite arrancar aunque PostgreSQL no esté disponible |
| **Migración en un Job, no en el arranque** | con 2 réplicas arrancando a la vez, un `CREATE TABLE` en el `lifespan` es una carrera. El Job corre una vez, en su wave |
| **El SQL viaja dentro de la imagen de la app** | esquema y código comparten tag: es imposible desplegar uno sin el otro |
| **GHCR** | autenticación nativa con `GITHUB_TOKEN`, sin secretos que gestionar |
| **Imagen multi-arquitectura** | los runners de GitHub son amd64 y el cluster de desarrollo es arm64; sin esto, la imagen publicada no se puede ejecutar en local |
| **Kustomize con commit-back** | el repositorio es la única fuente de verdad del estado desplegado; ArgoCD solo lee |
| **Tag inmutable** | publicar dos contenidos distintos bajo el mismo tag rompe la trazabilidad |

### Fallar al arrancar frente a arrancar degradado

La aplicación trata sus dos dependencias de forma distinta, a propósito:

- Falta `model/VERSION`: la app no arranca. Es un error de construcción
  de la imagen, irrecuperable, y así el fallo se ve en el momento del
  despliegue. Si respondiera `"unknown"` a `/version`, el problema pasaría
  desapercibido y ese endpoint dejaría de servir para verificar qué hay
  desplegado.
- PostgreSQL no responde: la app arranca igual y `/ready` devuelve 503.
  Es una dependencia externa que se recupera sola. El pod queda fuera del
  Service, sigue vivo y vuelve solo cuando la base regresa: medido, sin
  reinicios.

### Liveness y readiness no preguntan lo mismo

`/health` no consulta nada externo a propósito. Si la livenessProbe mirara
PostgreSQL, una caída de 30 segundos de la base haría que kubelet reiniciara
todos los pods, con lo que una caída de la dependencia se convertiría en una caída
propia, y al volver la base todos estarían arrancando a la vez.

`/ready` sí mira, y mira dos cosas: que la base responde y que el esquema
existe (`SELECT to_regclass('public.notes')`, una consulta al catálogo que
cuesta lo mismo que un `SELECT 1`). Con solo `SELECT 1`, un despliegue que
llegara antes que su migración pasaría la probe y serviría errores.

### Superficie expuesta

El Ingress enumera las rutas publicadas en vez de bloquear las prohibidas, así
que lo que se añada en el futuro queda fuera por defecto. No salen del cluster
`/metrics` (Prometheus scrapea el pod por la red interna), `/health` ni `/ready`
(las probes las hace el kubelet contra el pod), ni `/docs`.

---

## Observabilidad

El stack (`kube-prometheus-stack`) se instala con Helm porque es infraestructura
de plataforma. Lo que describe esta aplicación, qué expone y cuándo debe
alertar, vive en el repositorio: `ServiceMonitor`, `PrometheusRule` y el
dashboard, provisionado como ConfigMap con el label `grafana_dashboard: "1"`.

### Métricas propias

| Métrica | Tipo | Labels |
|---|---|---|
| `http_request_duration_seconds` | Histogram | `method`, `path`, `status` |
| `http_requests_total` | Counter | `method`, `path`, `status`, `outcome` |
| `db_query_duration_seconds` | Histogram | `operation`, `outcome` |
| `db_acquire_duration_seconds` | Histogram | `operation` |
| `db_pool_size` / `db_pool_idle` / `db_pool_max_size` | Gauge | — |

Dos detalles que condicionan que estas métricas sirvan:

- El label `path` es la plantilla de la ruta, no la URL concreta. Con la URL
  cruda, cada `/notes/1`, `/notes/2` crearía una serie nueva y la cardinalidad
  crecería sin límite; las peticiones que no casan con ninguna ruta se agrupan
  bajo `unmatched`.
- Los buckets de base de datos empiezan en 0,5 ms, no en 5 ms como los de HTTP.
  Las consultas tardan décimas de milisegundo: con los buckets de HTTP todas
  caerían en el primero y el p95 no distinguiría nada.

### Paneles

El dashboard está organizado en tres filas, una por capa, que es el orden en el
que conviene mirarlas para diagnosticar.

| Fila | Paneles |
|---|---|
| **Código** | latencia p95/p99 por endpoint · peticiones por status · tasa de error real (solo 5xx) |
| **Base de datos** | p95 de espera de pool superpuesto al p95 de consulta · estado del pool · operaciones por resultado |
| **Contenedor y nodo** | CPU frente a requests · memoria frente al límite · saturación de CPU y carga por núcleo del nodo |

El segundo panel de la fila de base de datos es el más directo: cuando la línea
de espera se despega de la de consulta, el problema no es PostgreSQL.

<!-- TODO: capturas del dashboard, una en reposo y otra bajo carga con el pool
     apretado (DB_POOL_MAX_SIZE=1), donde la separación se ve a simple vista. -->

### Alertas

| Alerta | Condición | `for` | Severidad |
|---|---|---|---|
| `LatenciaAltaP95` | p95 > 500 ms, excluyendo las rutas de probe | 10m | warning |
| `TasaDeErrores5xx` | más del 5 % de respuestas 5xx | 5m | critical |
| `PoolDeConexionesAgotado` | `db_pool_size == db_pool_max_size` y `db_pool_idle == 0` | 5m | warning |
| `ReplicasNoListas` | réplicas listas < réplicas deseadas | 5m | critical |

Tres decisiones de diseño detrás de esa tabla:

- `outcome="failure"` incluye los 4xx, así que no sirve para la alerta: un
  cliente enviando datos inválidos dispararía el ratio sin que la aplicación
  tenga nada roto. La alerta filtra `status=~"5.."`.
- La condición del pool necesita las dos gauges. Con `min_size=0`, el pool no
  abre conexiones hasta usarlas: en reposo `size=0, idle=0`, indistinguible del
  agotamiento. La condición correcta es "he abierto todas las que podía y
  ninguna está libre".
- No hay alerta de throttling de CPU. No se declaran `limits.cpu`, así que
  no hay cuota CFS y el kernel ni siquiera genera esas métricas. La capa
  contenedor queda descartada. El límite de memoria sí existe, así que el
  OOMKill sigue siendo una causa posible.

---

## Diagnóstico: ¿de dónde viene una latencia alta?

La pregunta se responde con tres fuentes de métricas y una tabla. La clave es
instrumentar por separado la espera por una conexión del pool y la consulta en
sí: son dos problemas distintos con soluciones opuestas.

| Síntoma | Causa | Qué mirar |
|---|---|---|
| p95 HTTP alto y `db_query_duration` alto | PostgreSQL | consultas, índices, carga de la base |
| p95 HTTP alto, `db_acquire_duration` alto, consulta normal | encolamiento en el pool | `db_pool_idle` en 0 con `size == max_size` |
| p95 HTTP alto, base normal, CPU del nodo saturada | el nodo | `node_load1` por núcleo, saturación de CPU |
| p95 HTTP alto y todo lo anterior normal | el código, o el event loop bloqueado | perfilado; buscar trabajo síncrono en handlers `async` |
| Reinicios o memoria pegada al límite | el contenedor | `container_memory_working_set_bytes` frente al límite |

El encolamiento es el caso difícil porque las peticiones no están trabajando,
están esperando turno por un recurso lógico. Ni PostgreSQL ni la CPU muestran
nada anormal, ya que el proceso está ocioso, y sin una métrica de la espera el
diagnóstico es imposible. Medido en este proyecto, con la misma carga (200
peticiones, 50 concurrentes):

| | pool = 1 | pool = 10 |
|---|---|---|
| Tiempo HTTP total | 4,98 s | 2,19 s |
| **Esperando conexión** | **4,69 s (94 %)** | 1,22 s (56 %) |
| Ejecutando consulta | 0,09 s (2 %) | 0,33 s (15 %) |

Con el pool a 1, el 94 % del tiempo es cola. Con un único histograma alrededor de
la consulta se vería una media de 0,46 ms y la conclusión sería que la base va
perfecta, mientras el usuario espera 25 ms.

El encolamiento tiene dos soluciones opuestas según la causa: si PostgreSQL
aguanta, subir `DB_POOL_MAX_SIZE` (vigilando que `réplicas × max_size` no supere
el límite de conexiones del servidor); si PostgreSQL ya está al límite, más
conexiones lo empeoran y hay que reducir el trabajo por consulta.

### Descomposición medida en el cluster

Con 428 consultas/s sostenidas:

```
p95 HTTP /notes        4,77 ms
  ├─ espera de pool    0,48 ms
  └─ consulta a la DB  1,01 ms
  └─ resto (~3,3 ms)   framework y serialización
CPU del contenedor     0,26 cores      memoria 19,7 % del límite
saturación del nodo     7,3 %          carga por núcleo 0,31
```

---

## Comportamiento verificado

| Qué | Resultado |
|---|---|
| Rolling update con tráfico continuo | 120 peticiones durante un `rollout restart`, **0 fallos** |
| Base de datos caída | `/ready` responde 503 en 1,8 ms; `/health` sigue en 200; sin reinicios |
| Recuperación | al volver PostgreSQL, `/ready` vuelve a 200 sin reiniciar la app |
| Esquema ausente | `/ready` → 503 con `{"database":"ok","schema":"missing"}`; los pods salen del Service |
| Job de migración | recrea la tabla tras un `DROP`, es idempotente y reintenta si la base no está |
| Imagen | non-root (uid 10001), `readOnlyRootFilesystem`, 70 MB comprimida |
| Cadena completa | `model/VERSION` → GHCR → commit-back → ArgoCD → `/version` responde la versión nueva |

---

## Limitaciones conocidas

Cada una es una decisión con su coste, y conviene que esté escrita.

1. El Secret de PostgreSQL está en el repositorio en texto plano. Un Secret
   de Kubernetes solo codifica en base64. En producción: sealed-secrets,
   external-secrets o el gestor de secretos del proveedor.
2. El Ingress restringe rutas, no protege la red. Cualquier pod del cluster
   puede llamar a `/metrics`. La defensa que falta es una `NetworkPolicy` que
   solo permita tráfico desde `monitoring`.
3. `/ready` comprueba que la tabla existe, no que el esquema esté completo.
   Una migración a medias podría pasar la probe.
4. Trivy escanea la imagen amd64 antes de publicar; la arm64 se escanea ya
   publicada, en un paso informativo. `load: true` solo puede cargar la
   plataforma del runner.
5. `apt-get upgrade` en el Dockerfile parchea CVEs a costa de la
   reproducibilidad: dos construcciones del mismo commit en semanas distintas
   pueden producir imágenes distintas.
6. El tag inmutable tiene un coste operativo. Si el commit-back falla,
   "Re-run failed jobs" recupera un fallo transitorio; pero si el fallo está en
   el propio workflow, hay que subir la versión, porque un re-run reejecuta el
   workflow roto. Ocurrió durante el desarrollo, con la `0.3.0`.
7. La invariante del commit-back la garantiza el orden, no una verificación.
   `update-manifests` corre después de publicar, así que el repositorio nunca
   apunta a una imagen inexistente; pero no vuelve a comprobar que siga ahí.
8. `prune` no borra lo que ArgoCD nunca gestionó. Un pod creado a mano es
   invisible para la Application: GitOps no garantiza que el cluster contenga
   solo lo que hay en el repositorio.
9. Una serie de Prometheus que nace alta no produce `rate()`. Una ráfaga de
   errores entre dos scrapes es invisible, porque la primera muestra de una serie
   es su línea base. Es un argumento a favor de `for:` largos y una advertencia
   sobre las pruebas de carga cortas.
10. `prometheus_client` obliga a un worker por pod. Con varios workers de
    uvicorn cada proceso tendría su propio registro y los contadores serían
    incoherentes. Se escala con réplicas.
11. Sin HTTPS ni autenticación. Es un entorno local; en producción harían
    falta TLS en el Ingress y autenticación en la API.

---

## Cómo usé la IA

Usé Claude Code durante todo el desarrollo, configurado en modo tutor
mediante un `CLAUDE.md` versionado en este repositorio: la instrucción explícita
es que no escribe el código de la solución. Su papel fue explicar conceptos,
dividir el trabajo en fases, revisar lo que yo escribía, señalar errores y
advertir de trampas conocidas.

El registro completo está en [`ai-log.md`](ai-log.md): los prompts usados
fase por fase, qué aportó la IA, qué decidí yo y qué errores encontró en mi
código, incluidos los suyos propios, como proponerme una versión inexistente de
una acción de GitHub que tumbó el primer run del CI.

Tres ejemplos de lo que sí aportó:

- Advirtió, antes de que escribiera el workflow, que los runners de GitHub
  son amd64 y mi cluster arm64, y que el síntoma no sería "arquitectura
  incorrecta" sino un `CrashLoopBackOff` sin logs útiles.
- Detectó que mi señal de pool agotado (`db_pool_idle == 0`) era falsa, porque
  con `min_size=0` ese es también el estado en reposo: una alerta que se
  dispararía cada madrugada.
- Encontró que un `content` de solo espacios pasaba la validación de Pydantic y
  reventaba contra el `CHECK` de la tabla, y que mi manejo lo reportaba como
  "base de datos no disponible": un error del cliente disfrazado de dependencia
  caída, que habría disparado en falso la alerta de 5xx.

<!-- TODO: enlace al reporte Paxel -->

---

## Estructura del repositorio

```
app/              API, acceso a datos, métricas y migrador
  main.py         endpoints y ciclo de vida
  db.py           pool, consultas y métricas de base de datos
  metrics.py      instrumentación HTTP
  migrate.py      aplica migrations/*.sql; es el comando del Job
migrations/       SQL, idempotente, aplicado en orden
model/VERSION     única fuente de verdad de la versión
k8s/base/         manifiestos + kustomization + dashboard
k8s/argocd/       Application de ArgoCD (se aplica a mano una vez)
.github/workflows/ci.yml
```
