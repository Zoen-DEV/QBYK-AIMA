# CLAUDE.md

Guía para trabajar en este repositorio con Claude Code. Ver también [`README.md`](README.md) para instalación, variables de entorno y endpoints.

## Regla principal — los dos flujos

> **Toda implementación nueva debe adaptarse a los DOS flujos de creación actuales: post individual y creación en lote (bulk).**

La app tiene dos maneras de crear contenido y **comparten el mismo pipeline**. Antes de dar por terminada cualquier funcionalidad nueva (un campo de formulario, una opción de generación, un cambio en publicación, etc.), verifica que funcione en ambos:

1. **Post individual** (`/individual` → `POST /jobs`) — un formulario por post, con progreso en vivo (SSE) y revisión/edición antes de publicar.
2. **Creación en lote / bulk** (`/bulk` → `POST /sheets/jobs`) — una plantilla `.xlsx` con una fila por post (máx. 6); se genera y programa cada fila automáticamente; el avance se ve en `/batches/:id`.

**Cómo aplicarla en la práctica:**

- El núcleo compartido vive en [`api/job_runner.py`](api/job_runner.py): `make_job` (construye el job), `run_pipeline` (genera todo) y `publish_job_posts` (publica/programa). **Pon la lógica nueva aquí siempre que se pueda**, para que ambos flujos la hereden gratis.
- Si agregas un **parámetro de generación** (algo que va en `params`):
  - En el flujo individual: añádelo al form de [`frontend/src/pages/individual.astro`](frontend/src/pages/individual.astro) y al endpoint `create_job` de [`api/app.py`](api/app.py).
  - En el flujo bulk: añádelo como **columna** en `COLUMNS`/`DEFAULTS`/`ALLOWED`/`DROPDOWN_OPTIONS`/`COLUMN_HELP` de [`api/sheets.py`](api/sheets.py) y mapéalo en `_row_to_spec`.
  - Normaliza el valor (clamps, `.strip()`, defaults) en **ambos** caminos de entrada antes de pasarlo a `make_job`.
- Si cambias el **shape del job**, hazlo en `make_job` (única fuente) — no construyas jobs a mano.
- Si tocas **publicación**, hazlo en `publish_job_posts` para que el single-publish y el batch se comporten igual (respeta `params.solo` y `params.dry_run`).
- En las **pruebas/verificación**, ejercita los dos flujos: un post individual y un sheet de varias filas (incluyendo una fila con `fecha_hora` para programación).

Si una funcionalidad genuinamente solo aplica a un flujo, déjalo explícito en el PR y documenta por qué.

## Arquitectura (estado actual)

- **Backend** — FastAPI en [`api/`](api/), stores **en memoria** (`jobs` y `batches` en `app.py`; se pierden al reiniciar).
  - `app.py` — endpoints + stores. Single: `/jobs*`. Bulk: `/sheets*`.
  - `job_runner.py` — **núcleo compartido**: `make_job`, `run_pipeline`, `publish_job_posts`, `_post_url`.
  - `batch_runner.py` — `run_batch`: itera filas **secuencialmente** (rate-limit de Blotato), por fila → `make_job` → `run_pipeline` → `publish_job_posts`. Convierte `fecha_hora` local → UTC con `tz_offset` del navegador.
  - `sheets.py` — genera la plantilla `.xlsx` (openpyxl) y parsea el sheet subido (`.xlsx`/`.csv`).
  - `post_writer.py` — redacción de posts (Anthropic Claude o Perplexity Sonar).
  - `scripts/` — clientes externos: Blotato (publicar), Higgsfield (imagen/video), transcripción (Whisper), overlay de texto (Pillow).
- **Frontend** — Astro (SSR, adapter Node) + React + Tailwind en [`frontend/`](frontend/).
  - `index.astro` (landing, elige flujo) · `individual.astro` (flujo 1) · `bulk.astro` + `batches/[id].astro` + `components/BulkProgress.tsx` (flujo 2).
  - Todas las llamadas al backend pasan por el proxy `src/pages/api/[...path].ts` (reenvía `/api/*` a `API_URL`, default `http://127.0.0.1:8000`; deja pasar SSE).

### Pipeline (compartido por ambos flujos)

`extracción` (YouTube / audio→Whisper / documento→texto) → `cuentas` (precedencia: form > `.env` > primera de Blotato) → `escritura` (LLM, JSON con parser robusto) → `imágenes` (Higgsfield + overlay Pillow, fallback a plantillas locales) **o** `video` (Higgsfield text-to-video, sin fallback) → `publicación` (Blotato, devuelve permalink `publicUrl`).

## Desarrollo

```bash
# API (puerto 8000)
cd api && python -m uvicorn app:app --reload

# Frontend (puerto 4321)
cd frontend && npm run dev
```

El `.env` va en la **raíz del repo** (la API lo carga desde `../` relativo a `api/`). Ver [`.env.example`](.env.example).

## Convenciones

- Idioma del producto y de los comentarios/strings de cara al usuario: **español**.
- Las imágenes generadas (`api/outputs/`) y el `.env` están en `.gitignore`; no los commitees.
- Stores en memoria: cualquier estado que deba sobrevivir a un reinicio necesitaría persistencia (hoy no la hay).
