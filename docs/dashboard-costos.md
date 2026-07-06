# Dashboard de Costos — Contexto y Diseño

> **Estado: implementado.** Todas las fases del plan (§10) están ✅ HECHAS: la medición vive en
> el núcleo compartido (`job_runner._track`), se persiste en MongoDB (`usage_events`) y se consulta
> desde los endpoints `/costs/*` y la página `/dashboard`. Este documento se mantiene como **registro
> de diseño** (el porqué y el cómo); para el uso día a día ver el resumen en [`../README.md`](../README.md)
> (sección «Dashboard de costos») y las reglas de instrumentación en [`../CLAUDE.md`](../CLAUDE.md)
> (recuerda: **toda implementación nueva debe contemplar los DOS flujos: individual y bulk**).

---

## 1. Objetivo

Construir un **dashboard de gastos** que permita:

1. **Gasto mensual y anual** de los servicios externos que consume la app (variable + fijos).
2. **Gasto por cada uso** — el costo variable atribuido a cada post/job generado.
3. Tomar decisiones de **precio e inversión**: cuánto cuesta producir un post, en qué servicio
   se va el dinero, cómo evoluciona el gasto en el tiempo.

El dashboard cubre **ambos flujos de creación** (post individual y creación en lote), porque
ambos comparten el mismo pipeline. La medición se instrumenta en el **núcleo compartido**
([`api/job_runner.py`](../api/job_runner.py)) para que los dos flujos hereden el tracking gratis.

---

## 2. Decisiones tomadas (alineadas con el usuario)

| Tema | Decisión |
|---|---|
| **Persistencia** | **MongoDB** (driver async `motor`, encaja con FastAPI async). Los eventos de consumo sobreviven reinicios; las agregaciones mes/año usan el framework de agregación de Mongo. |
| **Alcance UI** | **Página completa con gráficas** (nueva ruta en Astro): totales mes/año, desglose por servicio, costo por job, tendencia temporal. |
| **Costos fijos** | **Solo monto mensual configurable** (Blotato y, según el plan, Higgsfield). El dashboard lo prorratea; no se cuenta uso de estos servicios como costo. |
| **Tarifas** | **Las aporta el usuario**. Se cargan en una tabla de precios editable sin tocar código. Las de Claude ya están confirmadas (ver §6). |
| **Moneda base** | **USD** (todas las APIs facturan en USD). Conversión de visualización opcional con FX manual configurable. |

---

## 3. Mapa de servicios y cómo se facturan

Dos naturalezas de gasto, tratadas distinto:

### 3.1 Por uso (variable — se mide por job)

| Servicio | Dónde se invoca | Unidad de cobro | Cómo lo capturamos |
|---|---|---|---|
| **Anthropic Claude** (`claude-sonnet-4-6`) | [`post_writer.py:_write_with_anthropic`](../api/post_writer.py#L296) | tokens entrada/salida + caché (write/read) | `stream.get_final_message().usage` (hoy se descarta) |
| **Perplexity Sonar** (`sonar`/`sonar-pro`) | [`post_writer.py:_write_with_perplexity`](../api/post_writer.py#L326) | tokens + fee por request + búsqueda | Campo `usage` del último evento SSE (hoy se ignora) |
| **Higgsfield imagen** (Soul) | [`higgsfield_client.py:generate_image`](../api/scripts/higgsfield_client.py#L159) vía `image_provider` | por generación (1 base + N slides de carrusel) | Contador de generaciones HF reales (no las que caen a plantilla). **Costo por generación = $0** (lo cubre la suscripción, ver §3.2); el contador se conserva solo como **volumen** |
| **Higgsfield video** (text2video) | [`higgsfield_client.py:generate_video`](../api/scripts/higgsfield_client.py#L263) | por clip | 1 generación cuando `generate_video` tiene éxito. **Costo = $0** (suscripción) |
| **Whisper** (OpenAI/Groq) | [`scripts/transcribe.py`](../api/scripts/transcribe.py) | por minuto de audio | Duración del audio (de la respuesta verbose o del archivo). Motor `local` (faster-whisper) = **$0** |

> **Higgsfield es suscripción (decisión tomada):** sus generaciones se siguen **contando** (útil para medir volumen), pero su `*_per_generation` está en **0** porque el costo real es la suscripción mensual/anual, tratada como **fijo** en §3.2. Si algún día el plan pasa a pago por generación, basta con poner la tarifa en `pricing.json`.
>
> **Actualización (2026-07-03) — consumo en créditos vía MCP:** desde la migración al MCP las generaciones salen de los **créditos de la suscripción**, así que ahora el consumo también se mide en **créditos** y se congela en `units.credits` de cada evento (`service = higgsfield_mcp`). Tarifas medidas con el preflight `get_cost` del MCP y guardadas en `pricing.json → higgsfield_mcp`: imagen por generación según modelo (`nano_banana_pro` = **2 cr/img**), video **por segundo** según modelo (`kling3_0_turbo` = **1.5 cr/s**; sin duración configurada se asume `video_default_seconds` = 5s). `usd_per_credit` (default 0) permite valorizar los créditos en USD — en ese caso `fixed_monthly.higgsfield` debe quedar en 0 para no contar doble. El dashboard expone el total como `higgsfield_credits` en `/costs/summary` (KPI «Créditos Higgsfield») y los créditos por servicio/job.

### 3.2 Fijo / suscripción (monto mensual, no por uso)

| Servicio | Modelo | Tratamiento en el dashboard |
|---|---|---|
| **Blotato** (publicar + subir media) | Plan mensual **$20/mes** (confirmado) | Monto fijo $20, prorrateado por mes |
| **Higgsfield** | **Suscripción (prob. anual)** (confirmado) | Fijo. En `pricing.json` se guarda como `monthly_usd = anual/12` (monto **por confirmar**). El costo por generación es $0 (§3.1) |

> **Nota sobre "gasto por uso":** se calcula sumando solo los eventos variables (§3.1) atribuidos
> a cada job. Los costos fijos (§3.2) **no** se reparten por post (decisión tomada); se muestran
> aparte como línea mensual.

---

## 4. Arquitectura propuesta

```
                 ┌─────────────────── pipeline (job_runner.py) ───────────────────┐
                 │  escritura LLM → imágenes/video (Higgsfield) → (Whisper extracc.)│
                 │        │                 │                          │            │
                 │        ▼  usage          ▼  generaciones            ▼  minutos   │
                 │   ┌──────────────────────────────────────────────────────────┐  │
                 │   │  cost_tracker.record_event(...)  (calcula costo con tarifa)│  │
                 │   └──────────────────────────┬───────────────────────────────┘  │
                 └──────────────────────────────┼──────────────────────────────────┘
                                                ▼
                                   MongoDB  ·  colección `usage_events`
                                                ▲
                 ┌──────────────────────────────┼──────────────────────────────────┐
                 │  API  /costs/*  (agregación)  │   Frontend  /dashboard (Astro)    │
                 │  pricing.json (tarifas) ──────┘   gráficas + tablas               │
                 └───────────────────────────────────────────────────────────────────┘
```

Piezas nuevas:

- **`api/cost_tracker.py`** — `record_event(...)` (calcula costo desde la tarifa vigente y escribe
  en Mongo) + cálculo de costo por servicio. Punto único de instrumentación.
- **`api/db.py`** — cliente `motor` (conexión perezosa, configurable por `.env`: `MONGODB_URI`,
  `MONGODB_DB`). Si Mongo no está disponible, el tracking falla **silenciosamente sin romper el
  pipeline** (la generación de posts nunca debe caerse por un fallo de métricas).
- **`pricing.json`** (raíz del repo, en `.gitignore` como `.env`) — tabla de tarifas editable.
- **Endpoints `/costs/*`** en [`api/app.py`](../api/app.py).
- **Página `/dashboard`** en `frontend/` + componente de gráficas.

### 4.1 Por qué costo "congelado" al escribir el evento

`record_event` calcula el costo en USD **en el momento del evento** usando la tarifa vigente y lo
guarda junto a las unidades. Así, si cambias una tarifa después, los costos históricos no se
recalculan ni se distorsionan. Guardamos también `pricing_version` para trazabilidad.

---

## 5. Modelo de datos (MongoDB)

### Colección `usage_events` (un documento por llamada de pago)

```jsonc
{
  "_id": ObjectId,
  "ts": ISODate,              // UTC, momento de la llamada
  "flow": "individual",      // "individual" | "bulk"
  "job_id": "…",
  "batch_id": null,           // poblado solo en bulk
  "service": "anthropic",    // anthropic | perplexity | higgsfield | whisper
  "operation": "post_writing", // post_writing | image_generation | video_generation | transcription
  "model": "claude-sonnet-4-6",
  "units": {                  // forma según el servicio:
    "input_tokens": 1234,
    "output_tokens": 567,
    "cache_creation_input_tokens": 8900,
    "cache_read_input_tokens": 200
    // higgsfield:  { "generations": 4 }
    // whisper:     { "minutes": 1.5 }
  },
  "cost_usd": 0.0123,         // congelado al escribir
  "pricing_version": "2026-06-16",
  "source_type": "youtube",  // youtube | audio | texto | manual
  "platforms": ["linkedin","instagram","facebook"],
  "dry_run": false,
  "status": "success",       // success | fallback | failed
  "meta": {}                  // libre (request_id, notas)
}
```

**Índices:** `ts`, `service`, `job_id`, `batch_id`, y compuesto `{ts, service}` para los rollups.

### Costos fijos — en `pricing.json` (no en Mongo)

```jsonc
"fixed_monthly": [
  { "service": "blotato",    "monthly_usd": 0,  "note": "plan ___" },
  { "service": "higgsfield", "monthly_usd": 0,  "note": "solo si tu plan es suscripción" }
]
```

El dashboard prorratea estos montos al mes/año consultado.

---

## 6. Tabla de precios (`pricing.json`) — plantilla

> Las de **Claude están confirmadas** (oficiales). El resto las llenas tú.

```jsonc
{
  "version": "2026-06-16",
  "base_currency": "USD",
  "display_currency": "USD",
  "fx_rate": 1.0,                       // USD → display_currency (manual)

  "anthropic": {
    "claude-sonnet-4-6": {
      "input_per_1m": 3.00,
      "output_per_1m": 15.00,
      "cache_write_5m_per_1m": 3.75,    // 1.25× input
      "cache_read_per_1m": 0.30         // 0.10× input
    }
  },

  "perplexity": {
    "sonar-pro": {                      // tarifas oficiales (docs.perplexity.ai)
      "input_per_1m": 3.00,
      "output_per_1m": 15.00,
      "request_fee": 0.006,             // $6/1k req · tier "low" (el que usa la app)
      "search_per_1k": 0                // la búsqueda ya está incluida en request_fee
    },
    "sonar": { "input_per_1m": 1.00, "output_per_1m": 1.00, "request_fee": 0.005, "search_per_1k": 0 }
  },

  "higgsfield": {
    "image_per_generation": 0,          // suscripción → $0 por generación (ver §3.2)
    "video_per_generation": 0           // suscripción → $0 por clip
  },

  "whisper": {
    "whisper-1": { "per_minute": null } // ← OpenAI; "local" = 0
  },

  "fixed_monthly": [
    { "service": "blotato",    "monthly_usd": 20, "note": "plan mensual ($20/mes)" },
    { "service": "higgsfield", "monthly_usd": 0,  "note": "suscripción (prob. anual) — monthly_usd = anual/12" }
  ]
}
```

**Fórmula de costo de Claude (con caché):**

```
cost = input_tokens/1e6              * input_per_1m
     + output_tokens/1e6             * output_per_1m
     + cache_creation_input_tokens/1e6 * cache_write_5m_per_1m
     + cache_read_input_tokens/1e6     * cache_read_per_1m
```

---

## 7. Puntos de instrumentación (qué tocar y dónde)

> **Ya implementado** (ver §10). Las notas en presente/futuro de abajo («hoy se descarta»,
> «habrá que propagar») describen el punto de partida del diseño; hoy todos estos puntos están
> cableados en `job_runner._track`. Se conservan como mapa de dónde vive cada medición.

Todo se conecta en el **núcleo compartido** para cumplir la regla de los dos flujos.

1. **Claude** — en [`post_writer.py:_write_with_anthropic`](../api/post_writer.py#L296):
   tras el stream, leer `stream.get_final_message().usage` y devolverlo junto con los posts
   (hoy `write_posts` solo devuelve el dict de posts → habrá que propagar el `usage` al llamador
   en `run_pipeline`, que llama a `record_event`).
2. **Perplexity** — en [`post_writer.py:_write_with_perplexity`](../api/post_writer.py#L326):
   capturar el objeto `usage` que viene en el último evento SSE (hoy se descarta en el parser).
3. **Higgsfield imágenes** — exponer un **contador de generaciones reales** desde
   `image_provider` (distinguir HF exitoso de caída a plantilla local, que es gratis). Registrar
   tras la fase de imágenes en [`run_pipeline`](../api/job_runner.py#L114).
4. **Higgsfield video** — registrar 1 generación cuando `generate_video` tiene éxito
   ([`job_runner.py:314`](../api/job_runner.py#L314)).
5. **Whisper** — obtener la **duración del audio** (respuesta verbose de OpenAI, o medir el
   archivo) en la rama `audio` de [`run_pipeline`](../api/job_runner.py#L139). Motor `local` → costo 0.

> **Regla de oro:** la medición es **best-effort**. Un fallo de Mongo o del cálculo de costo
> **nunca** debe interrumpir la generación o publicación de un post. `record_event` envuelve todo
> en try/except y solo loguea.

---

## 8. API — endpoints nuevos (en `app.py`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/costs/summary?period=YYYY-MM` o `?period=YYYY` | Totales: variable por servicio + fijos prorrateados + total |
| `GET` | `/costs/timeseries?from=…&to=…&granularity=day\|month` | Serie temporal para las gráficas |
| `GET` | `/costs/by-job?from=…&to=…` | Costo por job (gasto por uso), con desglose de servicios |
| `GET` | `/costs/events?…` (paginado) | Eventos crudos para auditoría |

Las agregaciones mes/año usan `$group` sobre `usage_events` (por `service` y por `$dateToString`
de `ts`). Los fijos se suman desde `pricing.json`.

---

## 9. Frontend — página `/dashboard` (Astro + React)

- Selector de período (mes / año) y rango.
- **KPIs**: total del período, variable vs fijo, costo promedio por post.
- **Gráfica de barras/área**: gasto por servicio en el tiempo.
- **Tabla**: costo por job (gasto por uso) con desglose y enlace al job.
- Reutiliza el proxy [`frontend/src/pages/api/[...path].ts`](../frontend/src/pages/api/[...path].ts).
- Librería de gráficas: a elegir (Recharts / Chart.js) — decisión menor en implementación.

---

## 10. Plan por fases

- **Fase 0 — Cimientos de costo. ✅ HECHA.** `pricing.example.json` (commiteado) + `pricing.json`
  (gitignored) + [`api/cost_calc.py`](../api/cost_calc.py) (fórmula pura, best-effort) + tests
  ([`api/tests/test_cost_calc.py`](../api/tests/test_cost_calc.py), incluida la fórmula de caché de
  Claude). Sin Mongo todavía.
- **Fase 1 — MongoDB. ✅ HECHA.** [`api/db.py`](../api/db.py) (motor, conexión perezosa best-effort,
  config `.env`: `MONGODB_URI`/`MONGODB_DB`), índices de §5, y
  [`api/cost_tracker.py`](../api/cost_tracker.py): `build_event` (puro) + `record_event` (async,
  best-effort, no rompe el pipeline). Tests en
  [`api/tests/test_cost_tracker.py`](../api/tests/test_cost_tracker.py). `MONGODB_URI` vacío =
  tracking desactivado silenciosamente. **✅ `MONGODB_URI` real (Atlas) ya configurado en `.env`**
  (`MONGODB_DB=qbyk_aima`) → el tracking está activo.
- **Fase 2 — Instrumentación del pipeline. ✅ HECHA.** Punto único `job_runner._track` (lo heredan
  ambos flujos): Claude/Perplexity (tokens + caché, vía `write_posts` que ahora devuelve
  `(posts, usage)`), Higgsfield imágenes (contador `provider.hf_generations`, solo HF real),
  Higgsfield video (1 clip al tener éxito), Whisper (minutos de `transcribe*`, ahora devuelven
  `(texto, duración)`; motor local = $0). `make_job` etiqueta `flow`/`batch_id`. Tests en
  [`api/tests/test_instrumentation.py`](../api/tests/test_instrumentation.py). **Pendiente de
  verificación viva** (post individual + sheet multi-fila con `fecha_hora`) una vez haya
  `MONGODB_URI` real — los tests cubren el cableado, no la persistencia end-to-end.
- **Fase 3 — API de costos. ✅ HECHA.** [`api/cost_queries.py`](../api/cost_queries.py) (helpers
  puros de período/prorrateo/reshape + agregaciones `$group` sobre `usage_events`) y endpoints
  `/costs/{summary,timeseries,by-job,events}` en [`api/app.py`](../api/app.py). Best-effort: sin
  Mongo devuelven estructuras vacías (los fijos salen igual de `pricing.json`). Tests en
  [`api/tests/test_cost_queries.py`](../api/tests/test_cost_queries.py).
- **Fase 4 — Dashboard. ✅ HECHA.** Página [`frontend/src/pages/dashboard.astro`](../frontend/src/pages/dashboard.astro)
  + [`components/CostDashboard.tsx`](../frontend/src/components/CostDashboard.tsx): selector mes/año,
  KPIs, gráfica de barras apiladas por servicio (SVG puro, sin librerías), desglose por servicio,
  costos fijos y tabla de costo por job. Enlace en el nav (`Base.astro`) y en la landing.
- **Fase 5 — Costos fijos + cierre. ✅ HECHA.** Prorrateo de `fixed_monthly` (`cost_queries.prorate_fixed`,
  ×meses del período) y moneda de visualización (`display_currency` + `fx_rate` expuestos por la API
  y aplicados en el front). Docs actualizadas (`CLAUDE.md`, `README.md`). **Pendiente:** verificación
  end-to-end viva (post individual + sheet multi-fila) contra un `MONGODB_URI` real — los tests cubren
  el cableado y las agregaciones con colección simulada, no la persistencia real.

---

## 11. Riesgos y preguntas abiertas

1. ~~**Generaciones fallidas de Higgsfield** — ¿consumen crédito?~~ **Resuelto:** Higgsfield es
   suscripción (§3.2), así que el costo por generación es $0 — falle o no, no cambia el gasto.
2. ~~**Componentes de costo de Perplexity**~~ **Resuelto** con las tarifas oficiales
   ([docs.perplexity.ai](https://docs.perplexity.ai/docs/getting-started/pricing), jun 2026):
   `sonar-pro` = $3/1M in, $15/1M out, **$6/1k req** (tier *low*); `sonar` = $1/$1 + $5/1k. La app
   llama con `search_context_size: "low"` ([`post_writer.py`](../api/post_writer.py#L402)), así que el
   `request_fee` cargado corresponde a ese tier — **si algún día se sube a *medium/high*, hay que
   actualizar el fee** (pro: $10/$14 por 1k; sonar: $8/$12). La búsqueda va incluida en el fee
   (`search_per_1k = 0`, sin doble conteo).
3. ~~**Plan real de Blotato**~~ **Resuelto:** plan mensual **$20/mes** (fijo, sin componente por uso).
4. ~~**Higgsfield: ¿suscripción o pago por generación?**~~ **Resuelto:** suscripción (prob. anual) →
   fijo (§3.2). **Pendiente menor:** confirmar el monto anual para fijar `monthly_usd = anual/12`.
5. ~~**Hosting de MongoDB**~~ **Resuelto:** **Atlas** (`MONGODB_URI` ya cargado en `.env`).
6. **Zona horaria de los rollups** — guardamos `ts` en UTC; el agrupado mensual se hace en UTC y se
   puede ajustar a tz local en la visualización (consistente con cómo el bulk maneja `tz_offset`).
7. **Stores en memoria vs eventos persistidos** — los jobs siguen en memoria (se pierden al
   reiniciar), pero los `usage_events` quedan en Mongo, así el dashboard es histórico aunque el job
   ya no exista.

---

## 12. Definición de "listo"

- Generar un post individual y un lote deja eventos correctos en `usage_events` con costo calculado.
- `/costs/summary` de un mes cuadra con la suma de eventos + fijos prorrateados.
- El dashboard muestra gasto mensual/anual, desglose por servicio y costo por uso.
- Ningún fallo de métricas/Mongo interrumpe la generación o publicación.
- Documentado en `CLAUDE.md` (regla de instrumentar en el núcleo compartido) y `README.md`.
