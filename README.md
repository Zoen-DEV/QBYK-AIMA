# AIMA — Web

Interfaz web para el skill `repurpose-youtube-video`. Convierte contenido en posts listos para publicar en LinkedIn, Instagram y Facebook con un visual generado por IA (imagen o video). El contenido puede venir de cuatro fuentes:

- **Link de YouTube** — extrae metadata + transcript.
- **Audio (nota de voz de WhatsApp)** — se transcribe con Whisper y reemplaza al link de YouTube como contexto.
- **Documento** (`.txt`/`.md`, **PDF** o **Word `.docx`**) — se extrae su texto y se usa como base.
- **Texto manual** — el usuario escribe o pega el texto directamente en el formulario (solo flujo individual).

## Los dos flujos de creación

La app ofrece dos formas de crear contenido. **Ambos comparten exactamente el mismo pipeline de generación y publicación** (`make_job` → `run_pipeline` → `publish_job_posts` en `api/job_runner.py`), así que cualquier mejora del pipeline (extracción, escritura, imágenes/video, publicación) aplica a los dos sin duplicar lógica.

1. **Post individual** (`/individual`) — un formulario por post. El usuario elige fuente, tono, objetivo, formato, medio, idioma y cuentas, ve el progreso en tiempo real (SSE), edita los textos y aprueba antes de publicar. Es el flujo interactivo, de un solo post.
2. **Creación en lote (bulk)** (`/bulk`) — el usuario descarga una plantilla `.xlsx`, llena una fila por post (máximo 6) con su fecha/hora de programación, elige las cuentas **una sola vez** y sube el sheet. El backend parsea cada fila a un job normal, corre el pipeline completo y publica/programa el resultado en Blotato fila por fila (secuencial, por el rate-limit de Blotato). El avance del lote se sigue en `/batches/:id`.

> **Regla para implementaciones nuevas:** toda funcionalidad nueva debe contemplar **los dos flujos**. Ver [`CLAUDE.md`](CLAUDE.md).

## Estructura

```
web/
├── api/                    # FastAPI — pipeline de generación y publicación
│   ├── app.py              # Endpoints (single + bulk) y stores en memoria
│   ├── job_runner.py       # make_job, run_pipeline y publish_job_posts (núcleo compartido)
│   ├── batch_runner.py     # Orquestación del lote: por fila → job → pipeline → publish
│   ├── sheets.py           # Plantilla .xlsx descargable y parseo del sheet subido
│   ├── post_writer.py      # Redacción de los posts (Anthropic / Perplexity)
│   └── scripts/            # Clientes externos (Blotato, Higgsfield, transcripción, overlay)
└── frontend/               # Astro + React + Tailwind — UI
    └── src/pages/
        ├── index.astro       # Landing: elige flujo (individual o bulk)
        ├── individual.astro  # Flujo 1: formulario de un post
        ├── bulk.astro        # Flujo 2: descarga plantilla + sube sheet
        └── batches/[id].astro# Progreso del lote (BulkProgress.tsx)
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
| `HIGGSFIELD_API_KEY` | No | Activa Higgsfield Soul para imágenes (requiere también el secret) |
| `HIGGSFIELD_API_SECRET` | No | Secret de Higgsfield; junto a la key activa Higgsfield |
| `HIGGSFIELD_MODEL` | No | Override del modelo de imagen (default `higgsfield-ai/soul/standard`) |
| `HIGGSFIELD_RESOLUTION` | No | Override de resolución (default `1080p`) |
| `HIGGSFIELD_VIDEO_MODEL` | No | Slug del modelo text-to-video (default `higgsfield-ai/text2video/turbo` — verificar en el catálogo) |
| `HIGGSFIELD_VIDEO_ASPECT` | No | Aspect ratio del video (default `9:16`; `16:9`/`4:3`/`1:1`/`9:16`) |
| `HIGGSFIELD_VIDEO_DURATION` | No | Duración del clip; vacío = default del modelo |

> **Imágenes:** si defines `HIGGSFIELD_API_KEY` **y** `HIGGSFIELD_API_SECRET`, se usa [Higgsfield Soul](https://cloud.higgsfield.ai) (mejor calidad, de pago, asíncrono). Si falta cualquiera de las dos, o si una generación falla, se usan las **plantillas locales** de `api/assets/templates/` (`template-1.png`…`template-3.png`, 1080×1080) con el copy superpuesto. Higgsfield cae automáticamente a la plantilla correspondiente por imagen si una generación falla.

> **Video:** si el job pide `tipo_medio = video`, el visual se genera con [Higgsfield text-to-video](https://cloud.higgsfield.ai) (un solo clip compartido por LinkedIn, Instagram y Facebook, sin overlay de texto). Requiere las **mismas** credenciales que las imágenes. **No hay fallback gratis**: sin credenciales el job avisa y cae a la rama de imágenes; si la generación falla, la publicación queda sin medio.

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

> Lo que sigue describe el **flujo individual** (`/individual`). El **flujo bulk** (`/bulk`) reutiliza los mismos pasos 2 (extracción → cuentas → escritura → imágenes/video) y 5 (publicación), pero por cada fila del sheet y sin pantalla de revisión: ver [Creación en lote](#creación-en-lote-bulk).

1. El usuario elige la **fuente del contenido** (link de YouTube, audio, archivo de texto o **texto escrito manualmente**) y configura tono, objetivo, formato (incluido el **número de slides del carrusel**, 3–6), tipo de medio (imagen o video), **fuente de las imágenes** (`higgsfield` con respaldo en plantillas, o `template` para omitir Higgsfield), idioma y, opcionalmente, **qué cuenta** de LinkedIn/Instagram/Facebook usar (y la **Company Page** de LinkedIn o la **Página** de Facebook) eligiéndola en los selectores que el formulario carga vía `GET /accounts`.
2. La API arranca un job asíncrono con las siguientes fases:
   - **Extracción** (según la fuente): YouTube → metadata + transcript con `yt-dlp` y `youtube-transcript-api`; audio → transcripción con Whisper (endpoint compatible con OpenAI); documento → extracción del texto (`.txt`/`.md` directo, PDF con `pypdf`, Word `.docx` con `python-docx`); manual → el texto que el usuario escribió en el formulario (solo disponible en el flujo individual). En audio, texto y manual no hay URL de origen, así que el post de LinkedIn omite el CTA "mira el video".
   - **Cuentas**: resuelve las cuentas con precedencia *cuenta elegida en el formulario* > IDs del `.env` > primera cuenta listada en Blotato. Para LinkedIn, una Company Page opcional (`linkedin_page_id`) publica en la página en vez del perfil personal. Facebook publica siempre en una **Página** (`facebook_page_id`); vacío deja que Blotato elija la página por defecto de la cuenta.
   - **Escritura**: Claude (Anthropic) o Sonar (Perplexity) redactan los posts en JSON; parser robusto con fallback para respuestas malformadas.
   - **Imágenes** (`tipo_medio = imagen`): genera una imagen base compartida con Higgsfield Soul (si hay credenciales), aplica overlays de texto con Pillow (LinkedIn 4:5, Facebook 4:5, Instagram single o carrusel) y sube cada imagen a Blotato. Reintentos automáticos con backoff; si Higgsfield falla en una imagen concreta (o no hay credenciales), cae a la **plantilla local** correspondiente (`template-1` base/hook, `template-2` info, `template-3` créditos). El usuario puede elegir la **fuente de las imágenes** (`fuente_imagen`): `higgsfield` (IA con respaldo en plantillas, default) o `template` (usa siempre las plantillas locales, sin llamar a Higgsfield ni consumir créditos). El frontend muestra progreso y thumbnail por imagen a medida que se completan.
   - **Video** (`tipo_medio = video`): reemplaza el paso de imágenes por un solo clip text-to-video de Higgsfield (sin overlay de texto), compartido por LinkedIn, Instagram y Facebook y re-hospedado en Blotato. Sin fallback gratis: si falla, la publicación queda sin medio.
3. El frontend sigue el progreso en tiempo real por SSE (`/jobs/:id/stream`).
4. En la pantalla de revisión el usuario puede editar los textos y aprobar.
5. Al publicar, la API llama a Blotato para enviar los posts a LinkedIn, Instagram y Facebook. La pantalla de resultado muestra el botón **"Ver publicación"** con el enlace directo a cada post (el permalink `publicUrl` que devuelve Blotato).

## Creación en lote (bulk)

El flujo bulk genera y programa varios posts de una sola subida:

1. El usuario descarga la plantilla `.xlsx` desde `/bulk` (`GET /sheets/template`). Cada **fila = un post**; columnas: `youtube_url` **o** `texto` (una sola fuente por fila), `tono`, `objetivo`, `tipo_medio`, `fuente_imagen`, `formato_instagram`, `carrusel_slides`, `idioma`, `solo` (vacío = todas; `linkedin`/`instagram`/`facebook`) y `fecha_hora` (programación; vacío = publicar ahora). Máximo **6 filas**. La plantilla trae listas desplegables, comentarios de ayuda y una hoja "Instrucciones".
2. Las **cuentas** de LinkedIn/Instagram/Facebook (y la Company Page de LinkedIn o la Página de Facebook) y el **dry-run** se eligen una sola vez en la UI, **no** en el sheet, y se inyectan por fila.
3. Al subir el sheet (`POST /sheets/jobs`), `sheets.parse_sheet` valida cada fila y la convierte al mismo `params` que consume el pipeline. Se crea un **batch** en memoria y `batch_runner.run_batch` procesa las filas **secuencialmente** (para respetar el rate-limit de subida de medios de Blotato, 10 req/min): por cada fila construye un job normal (`make_job`), corre `run_pipeline` y publica/programa con `publish_job_posts` usando la `fecha_hora` convertida a UTC (`tz_offset` del navegador).
4. Cada fila queda registrada como un **job individual** en el mismo store, así puede inspeccionarse en `/jobs/:id`. El progreso del lote completo se sigue en `/batches/:id` (componente `BulkProgress`, que hace polling a `GET /sheets/batches/:id`).

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/accounts` | Cuentas Blotato conectadas por plataforma (con sus Company Pages de LinkedIn y Páginas de Facebook) para los selectores del formulario |
| `POST` | `/jobs` | Crea un job (form data) |
| `GET` | `/jobs/:id` | Estado del job |
| `GET` | `/jobs/:id/stream` | Progreso en tiempo real (SSE) |
| `POST` | `/jobs/:id/edit` | Edita los textos antes de publicar |
| `GET` | `/jobs/:id/image/:key` | Sirve la imagen generada (`li-hook`, `fb-hook`, `ig-single`, `ig-0`…`ig-N`) |
| `POST` | `/jobs/:id/publish` | Publica en las redes configuradas (la respuesta incluye el permalink de cada post) |
| `GET` | `/sheets/template` | Descarga la plantilla `.xlsx` para la creación en lote |
| `POST` | `/sheets/jobs` | Sube el sheet llenado, crea un batch y lanza la generación + programación por fila |
| `GET` | `/sheets/batches/:id` | Estado del batch (filas, jobs y resultados de publicación) |

> Los endpoints `/jobs/*` sirven al **flujo individual**; los `/sheets/*` al **flujo bulk**. Ambos terminan ejecutando el mismo pipeline de `job_runner.py`.
