# CLAUDE.md

Guía para trabajar en este repositorio con Claude Code. Ver también [`README.md`](README.md) para instalación, variables de entorno y endpoints.

## Regla principal — los dos flujos

> **Toda implementación nueva debe adaptarse a los DOS flujos de creación actuales: post individual y creación en lote (bulk).**

La app tiene dos maneras de crear contenido y **comparten el mismo pipeline**. Antes de dar por terminada cualquier funcionalidad nueva (un campo de formulario, una opción de generación, un cambio en publicación, etc.), verifica que funcione en ambos:

1. **Post individual** (`/individual` → `POST /jobs`) — un formulario por post, con progreso en vivo (SSE) y revisión/edición antes de publicar.
2. **Creación en lote / bulk** (`/bulk` → `POST /sheets/jobs`) — una plantilla `.xlsx` con una fila por post (máx. 12); se genera y programa cada fila automáticamente; el avance se ve en `/batches/:id`.

**Cómo aplicarla en la práctica:**

- El núcleo compartido vive en [`api/job_runner.py`](api/job_runner.py): `make_job` (construye el job), `run_pipeline` (genera todo) y `publish_job_posts` (publica/programa). **Pon la lógica nueva aquí siempre que se pueda**, para que ambos flujos la hereden gratis.
- Si agregas un **parámetro de generación** (algo que va en `params`):
  - En el flujo individual: añádelo al form de [`frontend/src/pages/individual.astro`](frontend/src/pages/individual.astro) y al endpoint `create_job` de [`api/app.py`](api/app.py).
  - En el flujo bulk: añádelo como **columna** en `COLUMNS`/`DEFAULTS`/`ALLOWED`/`DROPDOWN_OPTIONS`/`COLUMN_HELP` de [`api/sheets.py`](api/sheets.py) y mapéalo en `_row_to_spec`.
  - Normaliza el valor (clamps, `.strip()`, defaults) en **ambos** caminos de entrada antes de pasarlo a `make_job`.
- Si cambias el **shape del job**, hazlo en `make_job` (única fuente) — no construyas jobs a mano.
- Si tocas **publicación**, hazlo en `publish_job_posts` para que el single-publish y el batch se comporten igual (respeta `params.redes` —las redes destino, normalizadas en [`api/networks.py`](api/networks.py)— y `params.dry_run`).
- En las **pruebas/verificación**, ejercita los dos flujos: un post individual y un sheet de varias filas (incluyendo una fila con `fecha_hora` para programación).

Si una funcionalidad genuinamente solo aplica a un flujo, déjalo explícito en el PR y documenta por qué.

### Formato multi-red (imagen única / carrusel / historia / reel)

El **`formato`** del post aplica a **todas las redes** elegidas; una red que no soporta el formato **se omite** (se publica en las demás). La matriz vive en [`api/networks.py`](api/networks.py) (`FORMAT_NETWORKS` + `networks_for_format`, según lo que Blotato permite):

| formato | LinkedIn | Instagram | Facebook |
|---|---|---|---|
| `imagen-unica` | ✓ | ✓ | ✓ |
| `carrusel` | ✓ (document carousel, 2–10 imágenes) | ✓ (nativo) | ✓ (multi-foto) |
| `historia` | ✗ (no existe) | ✓ (`target.mediaType=story`) | ✓ (`target.mediaType=story`) |
| `reel` | ✗ (no existe) | ✓ (`target.mediaType=reel`) | ✓ (`target.mediaType=reel`) |

- El filtrado de `redes` por formato ocurre **en la entrada** (`create_job` en `app.py` y `_row_to_spec` en `sheets.py`), así todo lo downstream (`run_pipeline`, `post_writer`, `publish_job_posts`, tracking) hereda la lista ya filtrada. Si el filtro deja la lista vacía: 400 en el individual, warning + fila omitida en el bulk.
- En `carrusel` los slides (`ig-0`…`ig-N`, nombre histórico) se generan **una sola vez** y los comparten las redes activas; LinkedIn y Facebook ya no reciben su hook 4:5 propio en ese formato.
- Internamente `historia`/`reel` se modelan como `tipo_post` (el discriminador que ya usaban `/reel` y `/historia`) y `formato_instagram` conserva el formato de feed (`imagen-unica`|`carrusel`) para el pipeline; `params.formato` guarda la elección del usuario. Los videos de feed de Facebook también se publican como reel (`publish_job_posts`) porque Facebook ya no acepta videos de feed normales.

### Reel e Historia (páginas dedicadas) y el modo `subir`

- **Reel** (`/reel` → `tipo_post=reel`) e **Historia** (`/historia` → `tipo_post=historia`) publican en **Instagram y/o Facebook** (toggles en el form; LinkedIn no aplica). Pasan por `/jobs` y el mismo núcleo (`make_job`/`run_pipeline`/`publish_job_posts`). En el **bulk** se piden con `formato=reel|historia` (la columna `tipo_medio` decide si la historia es imagen o video).
- Aceptan dos orígenes de medio vía `media_origin`: `generar` (pipeline Higgsfield, video/imagen 9:16) o `subir` (el usuario sube el video/imagen final, se publica tal cual). **El modo `subir` sigue siendo solo-individual**: no es expresable en un sheet (requiere subir un archivo por fila).
- El trigger de **audio/documento** del bulk es la columna `archivo_url` (URL pública; se descarga y clasifica en `run_pipeline` vía [`api/scripts/remote_file.py`](api/scripts/remote_file.py)) — el equivalente sheet de subir el archivo en el flujo individual.

## Arquitectura (estado actual)

- **Backend** — FastAPI en [`api/`](api/), stores **en memoria** (`jobs` y `batches` en `app.py`; se pierden al reiniciar).
  - `app.py` — endpoints + stores. Single: `/jobs*`. Bulk: `/sheets*`.
  - `job_runner.py` — **núcleo compartido**: `make_job`, `run_pipeline`, `publish_job_posts`, `_post_url`.
  - `batch_runner.py` — flujo en **dos fases con aprobación**: `run_batch` GENERA las filas **secuencialmente** (rate-limit de Blotato; por fila → `make_job` → `run_pipeline`, sin publicar) y deja el batch en `review`; tras la aprobación del usuario, `publish_batch` PUBLICA/PROGRAMA las filas generadas → `publish_job_posts`. Convierte `fecha_hora` local → UTC con `tz_offset` del navegador.
  - `sheets.py` — genera la plantilla `.xlsx` (openpyxl) y parsea el sheet subido (`.xlsx`/`.csv`).
  - `post_writer.py` — redacción de posts (Anthropic Claude o Perplexity Sonar). `write_posts` devuelve `(posts, usage)` para el tracking de costos.
  - `scripts/` — clientes externos: Blotato (publicar), **Higgsfield MCP** ([`higgsfield_mcp.py`](api/scripts/higgsfield_mcp.py) — imagen/video vía OAuth, consume créditos de la **suscripción**; el consentimiento se hace desde la página `/conexiones` de la web o con [`mcp_bootstrap.py`](api/scripts/mcp_bootstrap.py)), Higgsfield Cloud ([`higgsfield_client.py`](api/scripts/higgsfield_client.py), **legacy/rollback**), transcripción (Whisper), overlay de texto (Pillow).
  - **Dashboard de costos** (ver [`docs/dashboard-costos.md`](docs/dashboard-costos.md)): `cost_calc.py` (fórmula pura USD desde `pricing.json`), `cost_tracker.py` (`record_event`, best-effort), `db.py` (MongoDB async `motor`, conexión perezosa), `cost_queries.py` (agregaciones mes/año/serie/por-job). Endpoints `/costs/*` en `app.py`.
- **Frontend** — Astro (SSR, adapter Node) + React + Tailwind en [`frontend/`](frontend/).
  - `index.astro` (landing, 4 flujos) · `individual.astro` (post individual) · `bulk.astro` + `batches/[id].astro` + `components/BulkProgress.tsx` (bulk) · `reel.astro` e `historia.astro` (Reel e Historia de IG, solo-individuales; ver excepción arriba) · `conexiones.astro` (estado de servicios externos + conexión OAuth de Higgsfield) · `dashboard.astro` + `components/CostDashboard.tsx` (dashboard de costos).
  - Todas las llamadas al backend pasan por el proxy `src/pages/api/[...path].ts` (reenvía `/api/*` a `API_URL`, default `http://127.0.0.1:8000`; deja pasar SSE).

### Pipeline (compartido por ambos flujos)

`extracción` (YouTube / audio→Whisper / documento→texto) → `cuentas` (precedencia: form > `.env` > primera de Blotato) → `escritura` (LLM, JSON con parser robusto) → `imágenes` (Higgsfield MCP + overlay Pillow, fallback a plantillas locales) **o** `video` (Higgsfield MCP text-to-video, sin fallback) → `publicación` (Blotato, devuelve permalink `publicUrl`).

### Backend de generación: Higgsfield MCP (OAuth, créditos de suscripción)

Imágenes y video se generan vía el **MCP oficial de Higgsfield** (`https://mcp.higgsfield.ai`), no el Cloud API. El MCP autentica por **OAuth contra la cuenta del usuario**, así que consume los créditos de la **suscripción** (App) en vez del pool separado del Cloud API (`cloud.higgsfield.ai`) — que era el motivo de la migración (el Cloud estaba en 0 y la suscripción tenía ~1000).

- **Setup / reconexión:** desde la página **`/conexiones`** del frontend (botón "Conectar" → consentimiento OAuth en el navegador → vuelve solo), o por terminal con `cd api/scripts && python mcp_bootstrap.py`. Ambos dejan el token store en `api/.hf_oauth.json` (gitignored — **secretos**). El script sigue siendo la vía de diagnóstico: `--balance`, `--models image|video`, `--test-image`, `--test-video`.
  - **Flujo web** (endpoints en `app.py`, lógica en `higgsfield_mcp.py`): `GET /connections` (estado; con `check=true` verifica la sesión con `balance` — la única forma de detectar el token muerto, que llega in-band con HTTP 200 — y de paso dispara el refresh si el refresh token sigue vivo) → `POST /connections/higgsfield/start` (`start_web_auth`: cancela el flujo pendiente si lo hay, corre discovery + DCR con `_FreshStorage` —ignora tokens/client_info viejos pero persiste lo nuevo en el store real— y devuelve la `authorize_url`) → el navegador consiente → `GET /connections/higgsfield/callback` (`finish_web_auth`: entrega el `code`, espera el intercambio de tokens, verifica con `balance` y reconstruye el provider del runtime con `_reload_runtime_oauth`; responde un mini-HTML con meta refresh a `/conexiones?hf=ok|error` — no un 307, porque el proxy del frontend sigue los redirects del upstream). El `redirect_uri` se arma con el origen del frontend (override: `HIGGSFIELD_OAUTH_REDIRECT`) y se registran también el callback de terminal (`localhost:3030`) para que ambos flujos compartan el client.
- **Selección de backend:** [`config.py`](api/config.py) `image_provider`/`video_available` devuelven MCP cuando existe el token store; si no, plantillas locales (imágenes) o sin medio (video). El usuario puede forzar plantillas con `fuente_imagen=template`.
- **Cliente:** [`higgsfield_mcp.py`](api/scripts/higgsfield_mcp.py) expone funciones **síncronas** (`generate_image`/`submit_image`/`poll_image`/`generate_video`) espejo de `higgsfield_client.py`, corriendo el SDK `mcp` (async) en un event loop de fondo. Patrón MCP: `generate_image`/`generate_video` → `job_status(sync=true)` → `generation.results.rawUrl`. Sin créditos → `recovery_tool=show_plans_and_credits` (se trata como "sin créditos" → fallback a plantilla). Reautenticación necesaria → `ReauthRequired` (reconectar desde `/conexiones` o correr `mcp_bootstrap.py`). **Recomendación de preset** (video): si el prompt se parece a un preset del catálogo, el MCP no encola el job y responde sin `results`, con `notice.type=preset_recommendation` — el cliente la rechaza solo (reintenta UNA vez con `params.declined_preset_id`, generación literal, sin preguntar: la app es headless); si aun así no hay job id, el error del banner lleva el `notice.message` del server en vez del genérico "respuesta sin job id".
  - **Token OAuth (24 h) y refresh:** Higgsfield reporta el token vencido como error **in-band** (HTTP 200 con `error: "Invalid or expired token"` o el genérico `"Something went wrong. Please try again."`), nunca con 401, así que el refresh reactivo del SDK no se dispara; y al recargar tokens del disco el SDK no sabe cuándo se emitieron (los da por válidos para siempre). Por eso el cliente persiste `issued_at` en el token store, siembra `token_expiry_time` + `oauth_metadata` (el token endpoint real es `/oauth2/token`; el fallback del SDK `/token` no existe) en el context del provider (`_seed_token_expiry`), y así el primer request refresca solo. Además `_run_coro` desanida los `ExceptionGroup`s de anyio ("unhandled errors in a TaskGroup") para que el motivo real llegue a los banners y a `_short_reason`.
  - **`server_url` = la URL COMPLETA del MCP (`https://mcp.higgsfield.ai/mcp`), no la raíz.** El SDK valida por RFC 8707 que el `resource` de la metadata protegida (`.../mcp`) matchee el `server_url`; con la raíz pelada todo flujo OAuth de runtime muere en `OAuthFlowError: "Protected resource ... does not match expected ..."` y la re-autenticación nunca puede completarse (era el primer error del banner del 2026-07-06). Aplica a `_build_oauth`, al flujo web y a `mcp_bootstrap.py`.
- **Provider:** [`image_provider.py`](api/scripts/image_provider.py) `MCPProvider` reemplaza a `HiggsfieldProvider` como backend activo (mismo interfaz, mismo fallback por-imagen a plantilla, mismo contador `hf_generations` para `_track`). El Cloud API queda retirado pero se conserva por rollback.
- **Modelos** (configurables por env): imagen `HIGGSFIELD_MCP_IMAGE_MODEL` (default `nano_banana_pro`, ~2 créditos/img); video `HIGGSFIELD_MCP_VIDEO_MODEL` (default `kling3_0_turbo`). Descubrir IDs válidos con `mcp_bootstrap.py --models`.

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
- Las imágenes generadas (`api/outputs/`) y el `.env` están en `.gitignore`; no los commitees. `pricing.json` (tarifas reales) también está ignorado — commitea solo `pricing.example.json`.
- Stores en memoria: cualquier estado que deba sobrevivir a un reinicio necesitaría persistencia (hoy no la hay). Los `usage_events` del dashboard **sí** se persisten en MongoDB.
- **Tracking de costos:** si agregas una nueva llamada de pago al pipeline, regístrala con `job_runner._track(...)` (punto único, lo heredan los dos flujos) y añade su tarifa a `pricing.example.json`. La medición es **best-effort**: nunca debe poder interrumpir la generación o publicación de un post. El consumo de Higgsfield (MCP) se mide en **créditos de la suscripción** (congelados en `units.credits`; tarifas por modelo en `pricing.json → higgsfield_mcp` — imagen por generación, video por segundo). El tracking necesita `motor` instalado en el Python que corre la API: tras actualizar Python, reinstalar `api/requirements.txt` o el tracking se apaga en silencio.
