# Platform Challenge — mini-platform k3d

Reto de Platform Engineering: FastAPI + PostgreSQL sobre k3d, con CI en GitHub
Actions (build + push a GHCR) y despliegue GitOps con ArgoCD.

## Cómo debe trabajar Claude en este repo

**Modo tutor. El usuario escribe TODO el código del reto.** Claude no crea ni
edita los archivos de la solución (app, Dockerfile, manifiestos, workflows) salvo
que el usuario lo pida de forma explícita.

El rol de Claude es:
- explicar conceptos y dividir el trabajo en fases,
- revisar el código que el usuario escribe y señalar errores,
- recomendar buenas prácticas y advertir de trampas conocidas,
- hacer preguntas que obliguen a razonar la decisión, en vez de dar la respuesta.

Puede mostrar fragmentos ilustrativos mínimos o pseudocódigo, nunca la solución
completa. El idioma de trabajo es el español.

Motivo: es una evaluación técnica y el objetivo es aprender. Además el propio
reto exige documentar cómo se usó la IA, así que esta conversación es parte de
la entrega (ver `ai-log.md` y la sección "Cómo usé la IA" del README).

**Antes de cada commit, Claude actualiza `ai-log.md`** con la conversación que
llevó a esos cambios: prompts usados, qué aportó la IA, qué decidió el usuario y
qué correcciones hizo. La evidencia se escribe en el momento, no al final.

## Decisiones ya tomadas

| Tema | Decisión |
|---|---|
| Base de datos | PostgreSQL (imagen oficial, tag fijo), como StatefulSet + volumeClaimTemplate |
| Driver Python | `asyncpg` con pool creado en el `lifespan` de FastAPI |
| Registry | GHCR (`ghcr.io/car7oskdr/...`), paquete debe hacerse público |
| Versionado | `model/VERSION` es la única fuente de verdad del tag |
| Propagación del tag | Kustomize: el CI ejecuta `kustomize edit set image` y hace commit-back al repo; ArgoCD detecta el commit |
| Cluster local | k3d `mini-platform`, 2 agents, puerto host 8080 → loadbalancer |

## Plan de fases

| Fase | Entregable | Se da por terminada cuando |
|---|---|---|
| 0 | Entorno y repo | ✅ hecho: k3d, helm, argocd, repo público |
| 1 | FastAPI local: `/version`, `/health`, `/metrics` + Postgres en `docker run` | tras generar tráfico **con un error provocado**, `/metrics` muestra buckets del histogram poblados y el counter de fallos incrementado |
| 2 | Dockerfile multi-stage, non-root | `docker run` de la imagen funciona y el usuario no es root |
| 3 | Manifiestos k8s (Secret, StatefulSet, Services, Deployment) | `kubectl apply` manual en k3d levanta todo |
| 3.5 | Prometheus + Grafana en el cluster, scrapeando `/metrics` | se ven los paneles con datos reales; base para la sección de observabilidad del README |
| 4 | CI en GitHub Actions: scans, build, push a GHCR | la imagen aparece en GHCR con el tag de `model/VERSION` |
| 5 | Commit-back con `kustomize edit set image` | subir una versión nueva actualiza el manifiesto solo |
| 6 | ArgoCD + Application GitOps | cambiar `model/VERSION` llega al cluster sin `kubectl` |
| 7 | README: arquitectura, paneles Grafana, alertas, diagnóstico de latencia | documentación completa |
| 8 | Reporte Paxel + sección "Cómo usé la IA" | link del reporte en el README |

`ai-log.md` es **transversal**, no una tarea de la Fase 8: una entrada por fase,
commiteada junto al código de esa fase. Reconstruirlo al final se nota y se ve peor.

## Entorno local

- Docker vía **OrbStack**. Cluster: `k3d cluster start|stop mini-platform` (no borrarlo: ArgoCD vive dentro).
- Instalados: k3d 5.9, helm 4.3, argocd CLI 3.5, kubectl 1.33.
- El token de `gh` no tiene `write:packages`; solo hace falta si se hace `docker push` a GHCR desde local.

## Requisitos del reto (checklist)

- [ ] FastAPI con `/version` (lee `model/VERSION`), `/health` y `/metrics`
- [ ] Endpoint de negocio que use PostgreSQL
- [ ] `/metrics` en formato Prometheus: latencia (Histogram) + éxitos vs fallos (Counter)
- [ ] Dockerfile multi-stage, imagen liviana, usuario non-root
- [ ] CI: scans básicos, build y push a GHCR con el tag de `model/VERSION`
- [ ] Deployment de FastAPI con `RollingUpdate` + `readinessProbe`
- [ ] ArgoCD apuntando a este mismo repo
- [ ] README: arquitectura, cómo correrlo local, paneles de Grafana, alertas y
      cómo distinguir si una latencia alta viene del código, del contenedor o del nodo/red
- [ ] `ai-log.md` con los prompts usados
- [ ] Reporte Paxel y su link en el README
- [ ] Historial de commits que muestre el progreso (no un único commit final)
