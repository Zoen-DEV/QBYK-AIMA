# AIMA — Web

Interfaz web para el skill `repurpose-youtube-video`. Convierte un video de YouTube en posts listos para publicar en LinkedIn e Instagram con un visual generado por IA (imagen o video).

## Estructura

```
web/
├── api/          # FastAPI — pipeline de generación y publicación
└── frontend/     # Astro + React + Tailwind — UI
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
| `BLOTATO_LINKEDIN_ACCOUNT_ID` | No | ID de cuenta LinkedIn; si falta, se lista automáticamente |
| `BLOTATO_INSTAGRAM_ACCOUNT_ID` | No | ID de cuenta Instagram; si falta, se lista automáticamente |
| `HIGGSFIELD_API_KEY` | No | Activa Higgsfield Soul para imágenes (requiere también el secret) |
| `HIGGSFIELD_API_SECRET` | No | Secret de Higgsfield; junto a la key activa Higgsfield |
| `HIGGSFIELD_MODEL` | No | Override del modelo de imagen (default `higgsfield-ai/soul/standard`) |
| `HIGGSFIELD_RESOLUTION` | No | Override de resolución (default `1080p`) |
| `HIGGSFIELD_VIDEO_MODEL` | No | Slug del modelo text-to-video (default `higgsfield-ai/text2video/turbo` — verificar en el catálogo) |
| `HIGGSFIELD_VIDEO_ASPECT` | No | Aspect ratio del video (default `9:16`; `16:9`/`4:3`/`1:1`/`9:16`) |
| `HIGGSFIELD_VIDEO_DURATION` | No | Duración del clip; vacío = default del modelo |

> **Imágenes:** si defines `HIGGSFIELD_API_KEY` **y** `HIGGSFIELD_API_SECRET`, se usa [Higgsfield Soul](https://cloud.higgsfield.ai) (mejor calidad, de pago, asíncrono). Si falta cualquiera de las dos, se usa [Pollinations.ai](https://pollinations.ai) (FLUX), que **no requiere API key**. Higgsfield cae automáticamente a Pollinations por imagen si una generación falla.

> **Video:** si el job pide `tipo_medio = video`, el visual se genera con [Higgsfield text-to-video](https://cloud.higgsfield.ai) (un solo clip compartido por LinkedIn e Instagram, sin overlay de texto). Requiere las **mismas** credenciales que las imágenes. **No hay fallback gratis**: sin credenciales el job avisa y cae a la rama de imágenes; si la generación falla, la publicación queda sin medio.

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

1. El usuario pega una URL de YouTube y configura tono, objetivo, formato, tipo de medio (imagen o video) e idioma.
2. La API arranca un job asíncrono con las siguientes fases:
   - **Extracción**: metadata + transcript con `yt-dlp` y `youtube-transcript-api`.
   - **Cuentas**: verifica los IDs de LinkedIn e Instagram desde `.env` o los consulta en Blotato.
   - **Escritura**: Claude (Anthropic) o Sonar (Perplexity) redactan los posts en JSON; parser robusto con fallback para respuestas malformadas.
   - **Imágenes** (`tipo_medio = imagen`): genera una imagen base compartida (Higgsfield Soul si hay credenciales, si no Pollinations `seed=42`), aplica overlays de texto con Pillow (LinkedIn 4:5, Instagram single o carrusel) y sube cada imagen a Blotato. Reintentos automáticos con backoff; si Higgsfield falla en una imagen concreta, cae a Pollinations para esa imagen. El frontend muestra progreso y thumbnail por imagen a medida que se completan.
   - **Video** (`tipo_medio = video`): reemplaza el paso de imágenes por un solo clip text-to-video de Higgsfield (sin overlay de texto), compartido por LinkedIn e Instagram y re-hospedado en Blotato. Sin fallback gratis: si falla, la publicación queda sin medio.
3. El frontend sigue el progreso en tiempo real por SSE (`/jobs/:id/stream`).
4. En la pantalla de revisión el usuario puede editar los textos y aprobar.
5. Al publicar, la API llama a Blotato para enviar los posts a LinkedIn e Instagram.

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/jobs` | Crea un job (form data) |
| `GET` | `/jobs/:id` | Estado del job |
| `GET` | `/jobs/:id/stream` | Progreso en tiempo real (SSE) |
| `POST` | `/jobs/:id/edit` | Edita los textos antes de publicar |
| `GET` | `/jobs/:id/image/:key` | Sirve la imagen generada (`li-hook`, `ig-single`, `ig-0/1/2`) |
| `POST` | `/jobs/:id/publish` | Publica en las redes configuradas |
