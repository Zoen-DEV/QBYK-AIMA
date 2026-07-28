# AIMA — Web

Interfaz web para el skill `repurpose-youtube-video`. Convierte contenido en posts listos para publicar en LinkedIn, Instagram y Facebook con un visual generado por IA (imagen o video). El contenido puede venir de cuatro fuentes:

- **Link de YouTube** — extrae metadata + transcript.
- **Audio (nota de voz de WhatsApp)** — se transcribe con Whisper y reemplaza al link de YouTube como contexto.
- **Documento** (`.txt`/`.md`, **PDF** o **Word `.docx`**) — se extrae su texto y se usa como base.
- **Texto manual** — el usuario escribe o pega el texto directamente en el formulario (solo flujo individual).

## Los dos flujos de creación

La app ofrece dos formas de crear contenido. **Ambos comparten exactamente el mismo pipeline de generación y publicación** (`make_job` → `run_pipeline` → `publish_job_posts` en `api/job_runner.py`), así que cualquier mejora del pipeline (extracción, escritura, imágenes/video, publicación) aplica a los dos sin duplicar lógica.

1. **Post individual** (`/individual`) — un formulario por post. El usuario elige fuente, tono, objetivo, formato, medio, idioma y cuentas, ve el progreso en tiempo real (SSE), edita los textos y aprueba antes de publicar. Es el flujo interactivo, de un solo post.
2. **Creación en lote (bulk)** (`/bulk`) — el usuario descarga una plantilla `.xlsx`, llena una fila por post (máximo 12) con sus redes y su fecha/hora de programación, elige las cuentas **una sola vez** y sube el sheet. El backend genera el contenido de cada fila y se detiene en un **preview** para que el usuario apruebe; al confirmar, publica/programa el resultado en Blotato fila por fila (secuencial, por el rate-limit de Blotato). El avance del lote se sigue en `/batches/:id`.

> **Regla para implementaciones nuevas:** toda funcionalidad nueva debe contemplar **los dos flujos**. Ver [`CLAUDE.md`](CLAUDE.md).

### Reel e Historia (Instagram y Facebook)

Además de los dos flujos anteriores, hay dos páginas dedicadas que comparten el mismo núcleo (`make_job` → `run_pipeline` → `publish_job_posts`) y publican un medio vertical 9:16 en **Instagram y/o Facebook** (LinkedIn no tiene reels ni historias — esa red se omite automáticamente):

3. **Reel** (`/reel` → `tipo_post=reel`) — publica un Reel vertical. El video puede **generarse** desde una fuente (YouTube, nota de voz, documento o texto) con el text-to-video de Higgsfield, armarse **a partir de fotos** (recorrido image-to-video, p. ej. una casa para una inmobiliaria), o el usuario puede **subir** su propio video; el caption lo redacta el LLM.
4. **Historia** (`/historia` → `tipo_post=historia`) — publica una Historia vertical, como **imagen** o **video**. Igual que el Reel, el medio se genera desde una fuente o se sube ya hecho.

El Reel acepta tres orígenes de medio vía `media_origin`: `generar` (pipeline Higgsfield), `fotos` (recorrido a partir de varias fotos subidas — transición entre cada par consecutivo, unidas en un solo reel) o `subir` (el archivo final se publica tal cual). Los modos `fotos` y `subir` son **solo-individual** (no expresables en un sheet: requieren subir archivos). El tipo de publicación se decide en `publish_job_posts` mapeando `tipo_post` → `target.mediaType` de Blotato (aplica a Instagram **y** Facebook). En el **bulk**, reel e historia se piden con la columna `formato` (`reel` | `historia`) del sheet y la duración del video con `duracion_video`. Ver el detalle y las implicaciones en [`CLAUDE.md`](CLAUDE.md).

## Estructura

```
web/
├── api/                    # FastAPI — pipeline de generación y publicación
│   ├── app.py              # Endpoints (single + bulk) y stores en memoria
│   ├── job_runner.py       # make_job, run_pipeline y publish_job_posts (núcleo compartido)
│   ├── batch_runner.py     # Orquestación del lote en 2 fases: run_batch (genera) + publish_batch (publica tras aprobar)
│   ├── sheets.py           # Plantilla .xlsx descargable y parseo del sheet subido
│   ├── post_writer.py      # Redacción de los posts (Anthropic / Perplexity)
│   ├── cost_calc.py        # Dashboard de costos: fórmula pura USD desde pricing.json
│   ├── cost_tracker.py     # Dashboard de costos: registra usage_events (best-effort)
│   ├── cost_queries.py     # Dashboard de costos: agregaciones mes/año/serie/por-job
│   ├── db.py               # Cliente MongoDB async (motor), conexión perezosa
│   └── scripts/            # Clientes externos (Blotato, Higgsfield, transcripción, overlay)
└── frontend/               # Astro + React + Tailwind — UI
    └── src/pages/
        ├── index.astro       # Landing: elige flujo (4 cards: individual, bulk, reel, historia)
        ├── individual.astro  # Flujo 1: formulario de un post
        ├── bulk.astro        # Flujo 2: descarga plantilla + sube sheet
        ├── reel.astro        # Reel de IG (solo-individual): genera o sube video 9:16
        ├── historia.astro    # Historia de IG (solo-individual): imagen o video 9:16
        ├── conexiones.astro  # Estado de servicios externos + conexión OAuth de Higgsfield
        ├── dashboard.astro   # Dashboard de costos (CostDashboard.tsx)
        └── batches/[id].astro# Progreso + preview/aprobación del lote (BulkProgress.tsx)
```

## Requisitos

- Python 3.11+
- Node.js 18+
- Credenciales en `.env` en la raíz del repo (ver [`.env.example`](.env.example))

## Variables de entorno

El archivo `.env` debe estar en la raíz del repositorio (`web/`). La API lo carga desde `../` relativo a `web/api/`.

| Variable | Requerida | Descripción |
|---|---|---|
| `BLOTATO_API_KEY` | Sí | API key de Blotato |
| `ANTHROPIC_API_KEY` | Uno de los dos | LLM para escribir los posts |
| `PERPLEXITY_API_KEY` | Uno de los dos | Alternativa (sonar-pro) |
| `OPENAI_API_KEY` | Solo fuente audio | Whisper para transcribir notas de voz (o usa `GROQ_API_KEY`) |
| `TRANSCRIPTION_BASE_URL` | No | Base URL del endpoint Whisper (default OpenAI; usar para Groq) |
| `TRANSCRIPTION_MODEL` | No | Modelo de transcripción (default `whisper-1`) |
| `BLOTATO_LINKEDIN_ACCOUNT_ID` | No | ID de cuenta LinkedIn; si falta, se lista automáticamente |
| `BLOTATO_INSTAGRAM_ACCOUNT_ID` | No | ID de cuenta Instagram; si falta, se lista automáticamente |
| `BLOTATO_FACEBOOK_ACCOUNT_ID` | No | ID de cuenta Facebook; si falta, se lista automáticamente |
| `HIGGSFIELD_MCP_IMAGE_MODEL` | No | Modelo de imagen **por defecto** (default `nano_banana_pro`). El form/sheet puede elegir otro por post: `nano_banana_pro` 2 cr/img · `nano_banana_2` 1.5 · `nano_banana` 1 · `gpt_image_2` 0.5 · `z_image` 0.15 |
| `HIGGSFIELD_MCP_VIDEO_MODEL` | No | Modelo text-to-video **por defecto** (default `kling3_0_turbo`). Por post: `kling3_0_turbo` 1.5 cr/s · `seedance_2_0_mini` 2.5 · `seedance_2_0` 4.5 |
| `HIGGSFIELD_MCP_TOKEN_STORE` | No | Ruta del token store OAuth (default `api/.hf_oauth.json`) |
| `HIGGSFIELD_OAUTH_REDIRECT` | No | Override del redirect URI del flujo OAuth desde la web (default: `{origen del frontend}/api/connections/higgsfield/callback`) |
| `HIGGSFIELD_VIDEO_ASPECT` | No | Aspect ratio del video (default `9:16`; `16:9`/`4:3`/`1:1`/`9:16`) |
| `HIGGSFIELD_VIDEO_SEGMENT_SECONDS` | No | Segundos por segmento de video (default `5`). Los reels largos (`duracion_video`) se arman concatenando varios segmentos |
| `HIGGSFIELD_VIDEO_DURATION` | No | Legacy — la duración ahora va por segmento (arriba); ya no se usa |
| `REEL_VOICEOVER` | No | `1` (default): los reels text-to-video de 2+ segmentos salen con **voz en off + subtítulos** (tools `generate_audio` + `explainer_video` del MCP; el guion lo escribe el LLM, una línea por shot). `0` los deja mudos (concat ffmpeg local, comportamiento anterior) |
| `HIGGSFIELD_MCP_TTS_MODEL` | No | Modelo TTS **por defecto** de la voz en off (default `seed_audio`, ~0.0067 créditos/carácter). Por post también: `elevenlabs` (~0.003 cr/carácter; exige voz — sin `HIGGSFIELD_TTS_VOICE_*` se usa un preset de respaldo) |
| `HIGGSFIELD_TTS_VOICE_TYPE` / `_ID` | No | Voz fija para el TTS (`preset`\|`element` + UUID de `mcp_bootstrap.py --voices`); vacío = voz por defecto del server |
| `HIGGSFIELD_SUBTITLE_FONT` | No | Fuente de los subtítulos quemados: `patrick` (default) \| `caveat` \| `marker` \| `anton` \| `none` (sin subtítulos; cuestan 0.05 créditos por bloque con voz) |
| `HIGGSFIELD_API_KEY` / `_SECRET` / `_MODEL` / `_VIDEO_MODEL` | No | **Legacy** — Cloud API retirado, solo para rollback |
| `MONGODB_URI` | No | Persiste los `usage_events` del dashboard de costos. Vacío = tracking desactivado (la app funciona igual, sin registrar costos) |
| `MONGODB_DB` | No | Base de datos de Mongo (default `qbyk_aima`) |

> **Generación (imágenes y video):** se usa el **MCP oficial de Higgsfield** (`https://mcp.higgsfield.ai`), que autentica por **OAuth contra tu cuenta** y consume los créditos de tu **suscripción** (no el Cloud API, que tiene un pool aparte). Setup: en la página **`/conexiones`** de la web, botón **Conectar** → consentimiento en el navegador → listo (alternativa por terminal: `cd api/scripts && python mcp_bootstrap.py`). Ambos dejan el token store en `api/.hf_oauth.json` (gitignored, **secretos**). El token dura 24 h y se refresca solo; si la sesión muere del todo, `/conexiones` lo muestra y permite reconectar ahí mismo. Diagnóstico por terminal: `python mcp_bootstrap.py --balance | --models image|video | --test-image | --test-video`.

> **Imágenes:** con el token store presente se generan con el MCP (default `nano_banana_pro`, ~2 créditos/img). El **modelo se puede elegir por post** en el form individual y por fila del sheet (columnas `modelo_imagen`/`modelo_video`/`modelo_voz`; catálogo y costos en `api/model_catalog.py`). Si falta el token store, o si una generación falla, se usan las **plantillas locales** de `api/assets/templates/` (`template-1.png`…`template-3.png`, 1080×1080) con el copy superpuesto — fallback por imagen.

> **Video (por segmentos):** si el job pide `tipo_medio = video` (o es un reel/historia-video), el visual se genera con el MCP y se comparte por LinkedIn, Instagram y Facebook (sin overlay de texto). Para durar más de un clip corto (Kling produce ~5-10s por generación), la app genera **N segmentos** y los **concatena** con ffmpeg (`imageio-ffmpeg`, incluido en `requirements.txt` — no hace falta instalar ffmpeg en el sistema); `duracion_video` fija la duración total buscada. En **text-to-video** los N segmentos salen de un `video_storyboard` (shots concretos anclados al contenido). En **image-to-video** (`media_origin=fotos`) cada par de fotos consecutivas es un segmento de transición. El **costo** se calcula antes con un preflight `get_cost` del MCP (créditos exactos, sin encolar) y se muestra en la revisión. **No hay fallback gratis**: sin token store el job avisa (y en reel/foto no puede continuar); si un segmento falla, la publicación queda sin medio.

## Instalación

```bash
# API
cd web/api
pip install -r requirements.txt

# Frontend
cd web/frontend
npm install
```

## Desarrollo

```bash
# Terminal 1 — API (puerto 8000)
cd web/api
python -m uvicorn app:app --reload

# Terminal 2 — Frontend (puerto 4321)
cd web/frontend
npm run dev
```

Abrir `http://localhost:4321`.

## Producción

```bash
# Build del frontend
cd web/frontend
npm run build

# Arrancar API
cd web/api
python -m uvicorn app:app --host 0.0.0.0 --port 8000

# Arrancar frontend (Node standalone)
cd web/frontend
node dist/server/entry.mjs
```

El frontend espera la API en `http://127.0.0.1:8000` por defecto. Para cambiarlo, definir `API_URL` en el entorno antes del build.

## Flujo de la aplicación

> Lo que sigue describe el **flujo individual** (`/individual`). El **flujo bulk** (`/bulk`) reutiliza los mismos pasos 2 (extracción → cuentas → escritura → imágenes/video) y 5 (publicación), pero por cada fila del sheet y con una pantalla de **revisión por lote** (genera todas las filas, las muestra en un preview y publica al aprobar): ver [Creación en lote](#creación-en-lote-bulk). Los flujos **Reel** (`/reel`) e **Historia** (`/historia`) usan el mismo pipeline pero publican en Instagram y/o Facebook con medio vertical 9:16 y permiten subir el medio ya hecho (`media_origin=subir`); ver [Los dos flujos de creación](#los-dos-flujos-de-creación).

1. El usuario elige la **fuente del contenido** (link de YouTube, audio, archivo de texto o **texto escrito manualmente**) y configura tono, objetivo, formato (incluido el **número de slides del carrusel**, 3–6), tipo de medio (imagen o video), **fuente de las imágenes** (`higgsfield` con respaldo en plantillas, o `template` para omitir Higgsfield), idioma, **en qué redes publicar** (LinkedIn/Instagram/Facebook, activables de forma independiente; por defecto las tres) y, opcionalmente, **qué cuenta** de cada red usar (y la **Company Page** de LinkedIn o la **Página** de Facebook) eligiéndola en los selectores que el formulario carga vía `GET /accounts`.
2. La API arranca un job asíncrono con las siguientes fases:
   - **Extracción** (según la fuente): YouTube → metadata + transcript con `yt-dlp` y `youtube-transcript-api`; audio → transcripción con Whisper (endpoint compatible con OpenAI); documento → extracción del texto (`.txt`/`.md` directo, PDF con `pypdf`, Word `.docx` con `python-docx`); manual → el texto que el usuario escribió en el formulario (solo disponible en el flujo individual). En audio, texto y manual no hay URL de origen, así que el post de LinkedIn omite el CTA "mira el video".
   - **Cuentas**: resuelve las cuentas con precedencia *cuenta elegida en el formulario* > IDs del `.env` > primera cuenta listada en Blotato. Para LinkedIn, una Company Page opcional (`linkedin_page_id`) publica en la página en vez del perfil personal. Facebook publica siempre en una **Página** (`facebook_page_id`) — Blotato exige el `pageId`; si no se elige una, se usa automáticamente la primera Página de la cuenta.
   - **Escritura**: Claude (Anthropic) o Sonar (Perplexity) redactan los posts en JSON; parser robusto con fallback para respuestas malformadas.
   - **Imágenes** (`tipo_medio = imagen`): genera una imagen base compartida con Higgsfield Soul (si hay credenciales), aplica overlays de texto con Pillow (LinkedIn 4:5, Facebook 4:5, Instagram single o carrusel) y sube cada imagen a Blotato. Con `formato = carrusel`, los slides se generan **una sola vez** y se comparten con las tres redes: carrusel nativo en Instagram, **document carousel** en LinkedIn (Blotato lo construye con 2–10 imágenes en `mediaUrls`) y post **multi-foto** en Facebook. Reintentos automáticos con backoff; si Higgsfield falla en una imagen concreta (o no hay credenciales), cae a la **plantilla local** correspondiente (`template-1` base/hook, `template-2` info, `template-3` créditos). El usuario puede elegir la **fuente de las imágenes** (`fuente_imagen`): `higgsfield` (IA con respaldo en plantillas, default) o `template` (usa siempre las plantillas locales, sin llamar a Higgsfield ni consumir créditos). El frontend muestra progreso y thumbnail por imagen a medida que se completan.
   - **Video** (`tipo_medio = video`, o reel/historia-video, o `media_origin = fotos`): reemplaza el paso de imágenes por un clip generado con Higgsfield (sin overlay), compartido por las redes activas y re-hospedado en Blotato. Para durar más que un clip corto, genera **N segmentos** (del `video_storyboard`, o un segmento por par de fotos consecutivas) y los concatena con ffmpeg (`imageio-ffmpeg`); `duracion_video` fija la duración total. Costo estimado con preflight `get_cost` antes de generar. Sin fallback gratis: si un segmento falla, la publicación queda sin medio.
3. El frontend sigue el progreso en tiempo real por SSE (`/jobs/:id/stream`).
4. En la pantalla de revisión el usuario puede editar los textos y aprobar.
5. Al publicar, la API llama a Blotato para enviar los posts a las **redes seleccionadas** (LinkedIn, Instagram y/o Facebook). La pantalla de resultado muestra el botón **"Ver publicación"** con el enlace directo a cada post (el permalink `publicUrl` que devuelve Blotato).

## Creación en lote (bulk)

El flujo bulk genera y programa varios posts de una sola subida:

1. El usuario descarga la plantilla `.xlsx` desde `/bulk` (`GET /sheets/template`). Cada **fila = un post**; columnas: `youtube_url` **o** `texto` (una sola fuente por fila), `tono`, `objetivo`, `tipo_medio`, `fuente_imagen`, `formato` (`imagen-unica` | `carrusel` | `historia` | `reel` — aplica a **todas** las redes de la fila; si una red no soporta el formato se omite esa red: historia y reel no existen en LinkedIn), `carrusel_slides`, `idioma`, `linkedin`/`instagram`/`facebook` (¿publicar en esa red? `sí`/`no`, vacío = `sí`; por defecto se publica en las tres) y `fecha_hora` (programación; vacío = publicar ahora). Máximo **12 filas**. La plantilla trae listas desplegables, comentarios de ayuda y una hoja "Instrucciones". (El encabezado viejo `formato_instagram` se sigue aceptando al subir sheets antiguos.)
2. Las **cuentas** de LinkedIn/Instagram/Facebook (y la Company Page de LinkedIn o la Página de Facebook) y el **dry-run** se eligen una sola vez en la UI, **no** en el sheet, y se inyectan por fila.
3. Al subir el sheet (`POST /sheets/jobs`), `sheets.parse_sheet` valida cada fila y la convierte al mismo `params` que consume el pipeline. Se crea un **batch** en memoria y `batch_runner.run_batch` **genera** el contenido de las filas **secuencialmente** (para respetar el rate-limit de subida de medios de Blotato, 10 req/min): por cada fila construye un job normal (`make_job`) y corre `run_pipeline` — **sin publicar**. Al terminar, el lote queda en estado **`review`**.
4. El usuario revisa en `/batches/:id` el **preview** completo (texto + imágenes/video) de cada post y aprueba con un botón. Eso llama a `POST /sheets/batches/:id/publish`, que dispara `batch_runner.publish_batch`: publica/programa cada fila ya generada con `publish_job_posts` usando la `fecha_hora` convertida a UTC (`tz_offset` del navegador).
5. Cada fila queda registrada como un **job individual** en el mismo store, así puede inspeccionarse en `/jobs/:id`. El progreso del lote completo se sigue en `/batches/:id` (componente `BulkProgress`, que hace polling a `GET /sheets/batches/:id`).

## Dashboard de costos

Mide cuánto cuesta producir contenido y a qué servicio se va el dinero. **Cubre los dos flujos**: la medición se instrumenta una sola vez en el núcleo compartido (`job_runner._track`), así que el post individual y cada fila del lote registran su costo gratis.

- **Cómo se mide.** Tras cada llamada de pago del pipeline —escritura LLM (Claude/Perplexity, tokens + caché), imágenes/video de Higgsfield (generaciones reales), transcripción Whisper (minutos)— `cost_tracker.record_event` calcula el costo en USD con la tarifa vigente de `pricing.json`, lo **congela** en el evento (cambiar una tarifa después no recalcula el histórico) y lo persiste en MongoDB (`usage_events`). Todo es **best-effort**: si Mongo no está disponible o el cálculo falla, la generación/publicación **nunca** se interrumpe.
- **Tarifas (`pricing.json`).** Tabla editable en la raíz del repo (gitignored, como el `.env`). Parte de [`pricing.example.json`](pricing.example.json): las tarifas de Claude y **Perplexity** ya están con valores oficiales (Perplexity `sonar-pro`/`sonar` con el `request_fee` del tier *low*, que es el que usa la app; si subes `search_context_size`, ajusta el fee). Whisper se llena solo si usas el motor `api` (con `local` es `0`). `fixed_monthly` lista los costos de suscripción —**Blotato $20/mes** y **Higgsfield** (suscripción: `monthly_usd = anual/12` si pagas anual)—, que el dashboard **prorratea** al período (no se reparten por post). `display_currency` + `fx_rate` permiten ver los montos en otra moneda (FX manual; el cálculo base siempre es USD).
- **Consumo de Higgsfield (créditos).** Las generaciones vía MCP salen de los **créditos de la suscripción**, así que además del volumen se registra su consumo en créditos, **congelado** en `units.credits` de cada evento con las tarifas de `pricing.json → higgsfield_mcp` (medidas con el preflight `get_cost` del MCP): imagen `image_credits_per_generation` por modelo (`nano_banana_pro` = 2 cr/img) y video `video_credits_per_second` por modelo (`kling3_0_turbo` = 1.5 cr/s × duración; sin `HIGGSFIELD_VIDEO_DURATION` se asume `video_default_seconds` = 5s). El dashboard muestra el total como KPI («Créditos Higgsfield») y por servicio/job. Por defecto `usd_per_credit = 0` (el gasto real es el fijo de la suscripción); si prefieres valorizar cada crédito en USD, pon `usd_per_credit = costo mensual del plan / créditos mensuales` **y deja `fixed_monthly.higgsfield` en 0** para no contar doble.
- **Página `/dashboard`.** Selector de mes/año, KPIs (total del período, variable vs fijo, costo promedio por post, créditos Higgsfield), gráfica de gasto por servicio en el tiempo y tabla de costo por job (gasto por uso). Si `MONGODB_URI` está vacío, la página carga igual y muestra los fijos; el gasto variable aparece en blanco hasta que configures Mongo. **Ojo:** el driver de Mongo (`motor`) debe estar instalado en el Python que corre la API (`pip install -r api/requirements.txt`); sin él, el tracking se desactiva en silencio (una actualización de Python puede borrarlo — reinstalar dependencias tras actualizar).

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/accounts` | Cuentas Blotato conectadas por plataforma (con sus Company Pages de LinkedIn y Páginas de Facebook) para los selectores del formulario |
| `GET` | `/connections` | Estado de los servicios externos (`?check=false` omite la verificación en vivo de la sesión de Higgsfield) |
| `POST` | `/connections/higgsfield/start` | Arranca el consentimiento OAuth del MCP de Higgsfield; devuelve la `authorize_url` a la que redirigir el navegador |
| `GET` | `/connections/higgsfield/callback` | Callback OAuth (recibe el `code`, cierra el intercambio de tokens y devuelve al navegador a `/conexiones`) |
| `POST` | `/jobs` | Crea un job (form data) |
| `GET` | `/jobs/:id` | Estado del job |
| `GET` | `/jobs/:id/stream` | Progreso en tiempo real (SSE) |
| `POST` | `/jobs/:id/edit` | Edita los textos antes de publicar |
| `GET` | `/jobs/:id/image/:key` | Sirve la imagen generada (`li-hook`, `fb-hook`, `ig-single`, `ig-story`, `ig-0`…`ig-N`) |
| `GET` | `/jobs/:id/video` | Sirve el video del job (proxy same-origin del clip en Blotato, con `Content-Type` real y soporte de `Range`) — es lo que usa el preview |
| `POST` | `/jobs/:id/publish` | Publica en las redes configuradas (la respuesta incluye el permalink de cada post) |
| `GET` | `/sheets/template` | Descarga la plantilla `.xlsx` para la creación en lote |
| `POST` | `/sheets/jobs` | Sube el sheet llenado, crea un batch y lanza la **generación** por fila (sin publicar; deja el lote en `review`) |
| `GET` | `/sheets/batches/:id` | Estado del batch (filas, jobs, preview de cada post y resultados de publicación) |
| `POST` | `/sheets/batches/:id/publish` | Aprueba el lote: publica/programa todas las filas ya generadas (solo si está en `review`) |
| `GET` | `/costs/summary?period=YYYY-MM\|YYYY` | Totales del mes/año: variable por servicio + fijos prorrateados + total + promedio por post |
| `GET` | `/costs/timeseries?from=&to=&granularity=day\|month` | Serie temporal de gasto por servicio para las gráficas |
| `GET` | `/costs/by-job?from=&to=` | Costo por job (gasto por uso) con desglose por servicio |
| `GET` | `/costs/events?service=&limit=&skip=` | Eventos crudos paginados (auditoría) |

> Los endpoints `/jobs/*` sirven al **flujo individual**; los `/sheets/*` al **flujo bulk**. Ambos terminan ejecutando el mismo pipeline de `job_runner.py`. Los `/costs/*` alimentan el **dashboard de costos** (leen `usage_events`; vacíos si Mongo no está configurado).
