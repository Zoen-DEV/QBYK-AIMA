# AIMA

Convierte un contenido en posts listos para publicar. Le das una fuente (un video de YouTube, una
nota de voz, un documento o texto escrito a mano), eliges tono y formato, y la app redacta los textos
con IA, genera el visual (imagen o video) y lo publica o lo programa en tus redes.

**Redes:** LinkedIn · Instagram · Facebook · TikTok (solo reels)

---

## Las 4 formas de crear

| | Página | Para qué sirve |
|---|---|---|
| 📝 | **Crear un post** (`/individual`) | Un post a la vez. Formulario completo, progreso en vivo y revisión antes de publicar. |
| 📊 | **Varios con un sheet** (`/bulk`) | Hasta 12 posts de una sola subida. Llenas un `.xlsx`, apruebas el lote y se programan solos. |
| 🎬 | **Reel** (`/reel`) | Video vertical 9:16 para Instagram, Facebook y TikTok. Con voz en off y subtítulos. |
| ⭕ | **Historia** (`/historia`) | Historia vertical 9:16 (imagen o video) para Instagram y Facebook. |

Las cuatro usan el mismo motor por dentro, así que cualquier mejora del pipeline aplica a todas.

---

## Empezar

**Necesitas:** Python 3.11+, Node.js 18+ y una cuenta de Blotato.

```bash
# 1. Dependencias
pip install -r api/requirements.txt
cd frontend && npm install
```

```bash
# 2. Configuración — copia el ejemplo y rellena tus claves
cp .env.example .env
```

Lo mínimo para arrancar son dos claves:

| Variable | Para qué |
|---|---|
| `BLOTATO_API_KEY` | Publicar y subir medios. [Obtenerla aquí](https://my.blotato.com/settings/api) |
| `ANTHROPIC_API_KEY` **o** `PERPLEXITY_API_KEY` | Escribir los posts |

Todo lo demás es opcional y está explicado en [`.env.example`](.env.example).

```bash
# 3. Correr (dos terminales)
cd api && python -m uvicorn app:app --reload   # API en :8000
cd frontend && npm run dev                     # Web en :4321
```

Abre **http://localhost:4321**.

> El `.env` va en la **raíz del repo**, no dentro de `api/`.

---

## Conectar Higgsfield (imágenes y video)

Los visuales se generan con Higgsfield y se pagan con los créditos de **tu suscripción**. La conexión
es un OAuth de un solo clic:

**Web → `/conexiones` → botón "Conectar" → aceptas en el navegador → listo.**

Eso deja tu sesión guardada en `api/.hf_oauth.json` (secreto, nunca se commitea). El token dura 24 h
y se renueva solo; si alguna vez muere del todo, `/conexiones` te lo avisa y reconectas ahí mismo.

**Si no conectas Higgsfield la app sigue funcionando**, con dos límites:

- Las **imágenes** salen de las plantillas locales de `api/assets/templates/` con el texto encima.
- El **video no se puede generar** (no hay alternativa gratuita). Puedes subir el tuyo.

Diagnóstico por terminal, si hace falta:

```bash
cd api/scripts && python mcp_bootstrap.py --balance
```

(también `--models image|video`, `--voices`, `--test-image`, `--test-video`)

---

## Cómo funciona

```
  Fuente          →   Escritura   →   Visual          →   Publicación
  ─────────────       ─────────       ─────────────       ─────────────
  YouTube             Claude o        Imagen (IA o        Blotato
  Nota de voz         Perplexity      plantilla)          → LinkedIn
  PDF / Word / txt    escriben        Video (clips +      → Instagram
  Texto manual        un post por     voz en off +        → Facebook
  Fotos               red             subtítulos)         → TikTok
```

1. **Extracción** — YouTube (metadata + transcript), audio (Whisper), documento (PDF/Word/txt) o el
   texto que escribas. Se detecta el idioma solo.
2. **Cuentas** — elige la cuenta de cada red. Precedencia: la que elijas en el formulario > la del
   `.env` > la primera que devuelva Blotato.
3. **Escritura** — el LLM redacta un texto adaptado a cada red, más el copy del visual y, si hay
   video, el storyboard y el guion de la voz en off.
4. **Revisión** *(solo flujo individual)* — el post **se pausa aquí**. Ves y editas los textos y los
   prompts antes de que se gaste un solo crédito. Al aprobar, se genera el visual.
5. **Publicación** — se publica o se programa en Blotato. La pantalla final te da el enlace directo a
   cada publicación.

El progreso se ve en tiempo real (SSE) mientras corre.

### Sobre el video

Los modelos generan clips de ~5-10 segundos, así que un reel largo se arma **concatenando varios
clips**. Eliges la duración total (10 a 60 s) y la app calcula cuántos necesita.

Por defecto los reels salen **narrados**: el LLM escribe una línea por escena, se convierte a voz y
se queman los subtítulos. Se puede apagar con `REEL_VOICEOVER=0`.

Antes de generar, la app consulta el costo exacto en créditos y te lo muestra en la revisión.

---

## Crear varios con un sheet

1. Descarga la plantilla `.xlsx` desde `/bulk`. Trae listas desplegables, ayuda en cada celda y una
   hoja de instrucciones.
2. Llena **una fila por post** (máximo 12):

   | Columna | Qué es |
   |---|---|
   | `youtube_url` **o** `texto` | La fuente. Una sola por fila. |
   | `tono` / `objetivo` | educativo, inspiracional, personal / engagement, awareness, tráfico |
   | `formato` | `imagen-unica`, `carrusel`, `historia` o `reel` |
   | `tipo_medio` | `imagen` o `video` |
   | `duracion_video` | 10, 20, 30, 45 o 60 segundos |
   | `linkedin` / `instagram` / `facebook` | `sí` o `no` (vacío = sí) |
   | `fecha_hora` | `AAAA-MM-DD HH:MM` para programar. Vacío = publicar ya. |

   También hay columnas para elegir modelo de imagen, video y voz, número de slides del carrusel y
   set de plantillas.

3. Elige las cuentas **una sola vez** en la web (no van en el sheet) y sube el archivo.
4. La app genera todas las filas y **se detiene**. Revisas el preview completo de cada post en
   `/batches/:id` y apruebas.
5. Al aprobar, se publica o programa fila por fila.

> Las filas se procesan de a una por el límite de subida de Blotato (10 por minuto). Cada fila queda
> como un job normal, así que puedes abrirla en `/jobs/:id`.

---

## Qué formato va en qué red

El formato que elijas aplica a **todas** las redes del post. La red que no lo soporta simplemente se
omite — no es un error.

| | LinkedIn | Instagram | Facebook | TikTok |
|---|:---:|:---:|:---:|:---:|
| Imagen única | ✅ | ✅ | ✅ | — |
| Carrusel | ✅ | ✅ | ✅ | — |
| Historia | — | ✅ | ✅ | — |
| Reel | — | ✅ | ✅ | ✅ |

TikTok nunca se activa por defecto: se elige a mano y solo para reels.

---

## Dashboard de costos

`/dashboard` responde dos preguntas: **cuánto gasto al mes** y **cuánto cuesta cada post**.

- Registra cada llamada de pago del pipeline (tokens del LLM, créditos de Higgsfield, minutos de
  Whisper) y congela su costo con la tarifa del momento.
- Los precios se editan en `pricing.json` (en la raíz, gitignored). Parte de
  [`pricing.example.json`](pricing.example.json).
- Las suscripciones (Blotato, Higgsfield) se cargan como costo fijo mensual y se prorratean; no se
  reparten por post.

**Es opcional.** Necesita `MONGODB_URI` en el `.env`; si lo dejas vacío, la app funciona igual y el
dashboard solo muestra los costos fijos.

> El driver `motor` tiene que estar instalado en el mismo Python que corre la API. Si actualizas
> Python, reinstala `api/requirements.txt` o el tracking se apaga en silencio.

---

## Endpoints

<details>
<summary>Ver la lista completa</summary>

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/accounts` | Cuentas conectadas por red (con Páginas de LinkedIn y Facebook) |
| `GET` | `/voices` | Voces disponibles para la narración |
| `GET` | `/connections` | Estado de los servicios externos (`?check=false` omite la verificación en vivo) |
| `POST` | `/connections/higgsfield/start` | Arranca el OAuth de Higgsfield |
| `GET` | `/connections/higgsfield/callback` | Cierra el OAuth y vuelve a `/conexiones` |
| `POST` | `/jobs` | Crea un post (form data) |
| `GET` | `/jobs/:id` | Estado del job |
| `GET` | `/jobs/:id/stream` | Progreso en vivo (SSE) |
| `POST` | `/jobs/:id/edit` | Edita textos y prompts |
| `POST` | `/jobs/:id/generate` | Aprueba el preview y genera el visual |
| `GET` | `/jobs/:id/image/:key` | Sirve una imagen generada |
| `GET` | `/jobs/:id/video` | Sirve el video (con soporte de `Range` para el player) |
| `POST` | `/jobs/:id/publish` | Publica o programa |
| `GET` | `/sheets/template` | Descarga la plantilla `.xlsx` |
| `POST` | `/sheets/jobs` | Sube el sheet y genera el lote |
| `GET` | `/sheets/batches/:id` | Estado del lote + preview de cada fila |
| `POST` | `/sheets/batches/:id/publish` | Aprueba el lote y publica |
| `GET` | `/costs/summary?period=YYYY-MM\|YYYY` | Totales del mes o año |
| `GET` | `/costs/timeseries?from=&to=&granularity=` | Serie temporal por servicio |
| `GET` | `/costs/by-job?from=&to=` | Costo por post |
| `GET` | `/costs/events?service=&limit=&skip=` | Eventos crudos (auditoría) |

El frontend nunca llama a la API directamente: todo pasa por el proxy `/api/*` de Astro.

</details>

---

## Estructura

```
.
├── api/                  # FastAPI — pipeline de generación y publicación
│   ├── app.py            # Endpoints y estado en memoria
│   ├── job_runner.py     # El motor compartido por todos los flujos
│   ├── batch_runner.py   # Orquestación del lote (generar → aprobar → publicar)
│   ├── post_writer.py    # Redacción con Claude / Perplexity
│   ├── sheets.py         # Plantilla .xlsx y parseo del sheet
│   ├── cost_*.py, db.py  # Dashboard de costos (MongoDB)
│   └── scripts/          # Clientes externos: Blotato, Higgsfield, Whisper, overlay, ffmpeg
├── frontend/             # Astro + React + Tailwind
├── docs/                 # Notas de diseño
└── CLAUDE.md             # Guía para trabajar en el código
```

**Ojo:** los posts y los lotes viven **en memoria**. Si reinicias la API, se pierden los que estén en
curso (lo único que se persiste son los eventos de costo en MongoDB).

---

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| «BLOTATO_API_KEY is not set» | Falta el `.env` o está dentro de `api/` en vez de la raíz |
| Las imágenes salen con plantilla y no con IA | No hay sesión de Higgsfield → conéctala en `/conexiones` |
| El video no se genera | Igual que arriba: el video no tiene respaldo gratuito |
| El dashboard no muestra gasto variable | Falta `MONGODB_URI`, o `motor` no está instalado en ese Python |
| Un post publica en menos redes de las que elegiste | El formato no existe en esa red (ver la tabla de arriba) |
| El contenido de un post desapareció | Se reinició la API: el estado es en memoria |

---

## Desarrollo

```bash
cd api && python -m pytest      # tests
```

Antes de tocar el código, lee [`CLAUDE.md`](CLAUDE.md) — sobre todo la regla de que **toda
funcionalidad nueva debe funcionar en los dos flujos** (individual y bulk).
