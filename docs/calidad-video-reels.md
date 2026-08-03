# Calidad visual de los reels — auditoría, diagnóstico y plan

Fecha: julio 2026. Motivo: fallos recurrentes que delatan generación por IA (manos con dedos de
más, objetos asimétricos con la lateralidad invertida, objetos flotando sin apoyo ni sombra).

## Estado de implementación

Decisiones tomadas: (1) el bulk pasa por las mismas compuertas que el individual, (2) se prioriza
calidad sobre costo en el modelo de video, (3) no se suma QA visual automatizado por ahora.

| Etapa | Estado |
|---|---|
| 0 · Verificar el schema del MCP (`negative_prompt`) | **Bloqueada** — necesita el consentimiento OAuth del usuario |
| 1 · Plantilla del storyboard (anatomía, anclaje, orientación) | **Hecha** |
| 1b · Cláusula de anclaje físico en todos los segmentos | **Hecha** |
| 2 · Recorte de frames inestables | **No se hace** — ver "Por qué la Etapa 2 quedó afuera" |
| 3 · Checklist de QC pre-publicación (los dos flujos) | **Revertida** — se implementó y se quitó el 29/07/2026 (ver Etapa 3) |
| 4 · Image-to-video con primer frame aprobado | **Pendiente** — el cambio de fondo, siguiente tramo |
| 5 · Regeneración por segmento | **Pendiente** |
| 6 · Paridad de compuertas bulk ↔ individual | **Hecha** |
| 7 · Seedance 2.0 como default de text-to-video | **Hecha** |

---

## Resumen ejecutivo

Hoy los reels salen de **text-to-video puro**: el LLM escribe un storyboard en prosa y cada beat se
manda tal cual a Kling 3.0 Turbo vía el MCP de Higgsfield, sin imagen de referencia, sin prompt
negativo y sin ninguna inspección de los píxeles resultantes. Con lo que ya tenemos se puede bajar
bastante la tasa de fallos: reescribir la plantilla del storyboard para que evite las tomas de
riesgo (manos manipulando en primer plano, objetos asimétricos conocidos) y exija anclaje físico
explícito, y recortar los frames inestables de cada segmento. Eso es prompt y post-proceso, cero
plataforma nueva.

Lo que sí requiere código nuevo — no plataforma nueva — es el cambio de fondo: **pasar el reel a
image-to-video con un primer frame aprobado por una persona**. El mecanismo ya existe y está probado
en producción en el recorrido de fotos (`medias: start_image/end_image`); falta cablearlo al reel. El
valor real no es que el modelo falle menos, es que **una imagen mala cuesta 2 créditos y se descarta
antes de gastar los 15 del clip**.

Y lo que no tiene solución hoy: ningún modelo de 2026 — ni Seedance 2.0, ni Veo 3.1, ni Kling 3.0 —
garantiza manos correctas ni lateralidad correcta de un objeto asimétrico. Sigue siendo un porcentaje
de generaciones fallidas que se reduce, no se elimina. Cualquier plan que prometa cero errores es
falso.

---

## Fase 0 — Cómo funciona el pipeline hoy (verificado en código)

### 1. Generación del storyboard

Es **texto plano en prosa dentro de un JSON**, no escenas estructuradas ni imágenes de referencia.

`post_writer._system_prompt()` pide al LLM (Anthropic Claude si hay `ANTHROPIC_API_KEY`, si no
Perplexity `sonar-pro`) cuatro campos de video:

| campo | qué es | idioma |
|---|---|---|
| `video_prompt` | 40–80 palabras, una sola escena filmable | inglés |
| `video_style` | 10–18 palabras, "look-lock" (lente, luz, paleta, mood) | inglés |
| `video_storyboard` | array de **N beats** de 25–50 palabras, uno por segmento | inglés |
| `video_voiceover` | array de N líneas habladas, alineadas 1:1 con los beats | idioma del post |

N sale de `_segments_needed(params)` = `ceil(duracion_video / video_segment_seconds)`, con tope
`_MAX_VIDEO_SEGMENTS = 8` ([job_runner.py:247](api/job_runner.py:247)).

El prompt final que recibe el modelo se arma en `_segment_prompt()`
([job_runner.py:330](api/job_runner.py:330)):

```
<beat del storyboard> + <video_style, idéntico en todos los segmentos> +
"No text, no captions, no typography, no logos, no watermarks."
```

Reglas que ya trae la plantilla y que funcionan: prohibición de escenas genéricas de stock, un solo
movimiento de cámara por toma, una acción por toma, composición 9:16 con el tercio inferior libre,
nada de texto en pantalla, nada de gente hablando a cámara.

Reglas que **no** trae: nada sobre manos, nada sobre orientación de objetos asimétricos, nada sobre
contacto físico con superficies o sombras de apoyo, nada sobre escala.

### 2. Modelos y plataforma

Todo pasa por el **MCP oficial de Higgsfield** (`https://mcp.higgsfield.ai/mcp`), autenticado por
OAuth contra la cuenta del usuario y pagado con créditos de la **suscripción**. El Cloud API
(`higgsfield_client.py`) quedó retirado, solo sirve de rollback.

Catálogo elegible por post ([model_catalog.py](api/model_catalog.py)):

- **Video**: `kling3_0_turbo` (default, 1.5 cr/s) · `seedance_2_0_mini` (2.5 cr/s) · `seedance_2_0` (4.5 cr/s)
- **Imagen**: `nano_banana_pro` (default, 2 cr) · `nano_banana_2` (1.5) · `nano_banana` (1) · `gpt_image_2` (0.5) · `z_image` (0.15)
- **Voz**: `seed_audio` (~0.007 cr/carácter) · `elevenlabs` (~0.003)

Los únicos parámetros que enviamos a `generate_video`
([higgsfield_mcp.py:508](api/scripts/higgsfield_mcp.py:508)): `model`, `prompt`, `aspect_ratio`,
`duration`, `medias`, y `generate_audio:false` para los Seedance. **No enviamos `negative_prompt`,
ni `seed`, ni ningún control de calidad/resolución** — y no está verificado si la tool los acepta.

### 3. Text-to-video vs image-to-video

**El reel es text-to-video puro.** En [job_runner.py:1159](api/job_runner.py:1159):

```python
segments = [{"prompt": _segment_prompt(b, video_style), "medias": None} for b in beats]
```

El `medias: None` es literal: no hay primer frame, el modelo inventa la escena entera desde el texto
en cada segmento.

El image-to-video **sí existe y está probado en producción**, pero solo en el recorrido de fotos
(`media_origin="fotos"`, [job_runner.py:1110](api/job_runner.py:1110)): cada par de fotos
consecutivas se sube a Blotato, se importa al MCP con `import_media_url` y se manda como
`{"value": media_id, "role": "start_image"}` / `"end_image"`. Mismo modelo de video, mismo MCP, misma
función `submit_video`. **La pieza técnica ya está construida; simplemente el reel no la usa.**

### 4. Revisión humana y reintentos

Dos compuertas humanas, y solo en el flujo individual:

1. **`status="preview"`** — el pipeline pausa antes de gastar créditos
   ([job_runner.py:982](api/job_runner.py:982), `_wants_preview`). La página
   [preview.astro](frontend/src/pages/jobs/[id]/preview.astro) deja editar `video_storyboard`,
   `video_voiceover`, `video_style` y `video_prompt` como texto libre.
   **Se revisa texto, no imágenes: nadie ve nada visual todavía.**
2. **`status="review"`** — el video ya generado y pagado se puede mirar antes de publicar.

El **flujo bulk no pausa en preview** (`_wants_preview` devuelve False para `flow="bulk"`): los reels
del sheet se generan sin que nadie vea el storyboard. Solo hay aprobación a nivel de lote, con el
video ya hecho.

Reintentos: `_SEGMENT_ATTEMPTS = 3` por segmento y por línea de voz, pero **solo ante fallo técnico**
(job failed, poll timeout, corte de red — `_await_segment`,
[job_runner.py:392](api/job_runner.py:392)). Nada inspecciona los frames. Un clip con seis dedos es,
para el pipeline, un éxito.

Y no hay endpoint de regeneración: la lista de rutas de [app.py](api/app.py) no tiene ningún
`/jobs/{id}/regenerate`. **Si un segmento sale mal, la única salida es tirar el job entero y volver a
pagarlo completo.**

### 5. Un hallazgo colateral: los segmentos con voz duran 10s

`_VOICE_BLOCK_SECONDS = 10` ([job_runner.py:260](api/job_runner.py:260)): cuando el reel lleva voz en
off, `explainer_video` arma bloques de ventana fija de ~10s, así que cada segmento se genera de 10
segundos. Diez segundos es una toma **larga** para text-to-video, y la degradación de manos y física
es acumulativa a lo largo del clip: el riesgo por segundo no es constante, crece. Los reels mudos
usan segmentos de 5s y por eso mismo son estructuralmente menos riesgosos.

---

## Fase 1 — Estado del arte (julio 2026)

Correcciones al panorama de partida, según fuentes de 2026:

- **Runway Gen-4.5 ya no lidera.** Lideró al lanzarse a fines de 2025 (1247 Elo) y hoy está fuera del
  top 10 de Artificial Analysis. Sigue teniendo el mejor conjunto de controles y ecosistema de
  producción, pero no es el líder de calidad.
- **Seedance 2.0 (ByteDance, feb 2026) encabeza el ranking**, seguido de HappyHorse-1.0. Veo 3.1 está
  en #3 y Kling 3.0 mete cuatro entradas en el top 10. Ya existe **Seedance 2.5** con 4K nativo de 30s
  y hasta 50 imágenes de referencia.
- **Nosotros ya tenemos Seedance 2.0 en el catálogo.** Es un desplegable en el formulario del reel,
  cuesta 3× Kling Turbo (4.5 vs 1.5 cr/s) y no requiere una línea de código.
- **Image-to-video le gana a text-to-video en control**, no en calidad bruta. En modo I2V la imagen es
  literalmente el primer frame; composición, iluminación y forma del sujeto quedan fijadas antes de
  que empiece el movimiento. Kling 3.0 trata la imagen inicial como ancla y preserva identidad,
  layout y detalle. Para "que la guitarra tenga las cuerdas del lado correcto", eso es exactamente lo
  que hace falta: validar la guitarra en una imagen barata antes de animarla.
- **Prompts negativos**: Kling 3.0 los soporta en su plataforma con "Negative Semantic Mapping"
  (campo separado, no dentro del prompt principal). La recomendación es **10–20 términos cortos**; más
  que eso confunde al modelo. Advertencia relevante de las fuentes: en video, un término que arregla
  la mano en el frame 1 no impide que los dedos se fusionen en el segundo 3, porque el modelo
  resuelve anatomía nueva en cada frame. **No verificado**: si la tool `generate_video` del MCP de
  Higgsfield expone ese campo. Se comprueba gratis (ver Fase 3).
- **Post-proceso (Topaz y similares)**: sirve para upscaling y nitidez. No corrige anatomía rota ni
  física imposible. Confirmado, esto se resuelve en generación o no se resuelve.
- **Herramientas para Claude Code**: existe el MCP de Higgsfield (el que ya usamos) y una skill
  instalable, `OSideMedia/higgsfield-ai-prompt-skill`, con submódulos específicos de Kling 3.0 y
  Seedance 2.0, un `FAILURE-MODES.md` con ocho fallos de render nombrados, y un
  `negative-constraints.md` con artefactos categorizados y **frases preventivas en positivo como
  alternativa para 3.0**. Vale la pena leerla como fuente de plantilla, aunque no la instalemos.
- **QA visual automatizado**: hay MCPs de análisis de video (p. ej. Vision MCP Server con
  `video_analysis`) y frameworks académicos de evaluación de video generado (FingER). Ninguno es un
  detector confiable de "seis dedos": los VLM cuentan dedos mal. Sirven como triaje, no como
  compuerta.
- **Costos de referencia en agregadores** (fal.ai, julio 2026), por si alguna vez se evalúa salir de
  Higgsfield: Kling 3.0 ~$0.029/s, Veo 3.1 Lite ~$0.05/s, Seedance 2.0 ~$0.24–0.30/s, Veo 3.1
  estándar ~$0.40/s.

---

## Fase 2 — Diagnóstico honesto

### A. Se resuelve con lo que ya tenemos (prompt y flujo)

- **Evitar las tomas de riesgo.** La plantilla del storyboard hoy permite explícitamente primeros
  planos y no dice nada sobre manos. El fallo más caro es gratis de prevenir: prohibir manos
  manipulando algo en primer plano, cuerpos completos en movimiento y objetos asimétricos conocidos
  (instrumentos, teclados, relojes, texto, logos) como sujeto principal, salvo que el encuadre oculte
  la parte asimétrica.
- **Anclaje físico explícito.** Obligar a que cada beat diga sobre qué superficie se apoya el objeto y
  que proyecta sombra de contacto. "Una computadora flotando" es, en buena medida, un beat que nunca
  dijo dónde estaba apoyada la computadora.
- **Orientación explícita** cuando el objeto asimétrico es inevitable ("acoustic guitar held
  right-handed, strings facing the camera").
- **Recorte de frames inestables.** Buena parte de los artefactos vive en los primeros y últimos
  cuadros de cada segmento. Ya tenemos ffmpeg (`video_stitch`, vía `imageio-ffmpeg`): recortar
  ~0.3s de cada punta es barato y no requiere nada nuevo.
- **Segmentos más cortos** en reels mudos (5s en vez de 10s) reducen la ventana de degradación. Con
  voz en off **no se puede**: la ventana de 10s de `explainer_video` es fija y confirmada
  empíricamente. Es un techo real del camino con voz.
- **Cambiar de modelo por post**: `seedance_2_0` ya está en el desplegable. Cero código, 3× el costo.

### B. Requiere sumar algo (código nuevo, misma plataforma)

- **Image-to-video con primer frame aprobado.** El cambio de fondo. Reutiliza `import_media_url` +
  `medias` del recorrido de fotos. Costo marginal: con `nano_banana_pro`, 2 cr por shot — en un reel
  de 30s con voz (3 segmentos × 10s × 1.5 cr/s = 45 cr) son 6 créditos, **+13%**. Con `z_image`
  (0.15 cr) es +1% y sigue sirviendo para validar composición y orientación.
- **Regenerar un segmento suelto.** Hoy un shot malo obliga a repagar el reel completo. Un endpoint
  que regenere solo el segmento i y rehaga el ensamblaje es, en relación valor/esfuerzo, seguramente
  lo mejor de toda esta lista — y no depende de que la calidad del modelo mejore.
- **Compuerta de storyboard en bulk.** Los reels del sheet se generan hoy sin que nadie vea el
  storyboard. Es una decisión de diseño consciente, pero conviene revisarla si el bulk genera reels.
- **QA visual automatizado.** Muestrear frames y pasarlos por un modelo de visión. Honestamente:
  detecta bien objetos flotando y composiciones raras, detecta **mal** el conteo de dedos. Sirve para
  ordenar qué mirar primero, no para aprobar sin humano. Suma costo de tokens por reel.

### C. Limitación real de la tecnología, sin solución garantizada hoy

- **Manos y anatomía fina en movimiento fallan en un porcentaje de generaciones en todos los modelos
  de 2026**, incluidos los que encabezan el ranking. Ningún prompt lo elimina. Lo que hacemos es bajar
  la frecuencia y hacer barato descartar.
- **La lateralidad de un objeto asimétrico no es un parámetro controlable.** El image-to-video no la
  garantiza: mueve el problema a la imagen, donde cuesta 2 créditos verlo y rehacerlo en vez de 15.
  Eso es una mejora económica, no una garantía.
- **Ningún modelo expone restricciones físicas.** "Que la computadora esté apoyada" se sugiere por
  prompt y se ancla por primer frame; no se impone.
- **El post-proceso no arregla nada de esto.** Upscaling sobre una mano de seis dedos da una mano de
  seis dedos nítida.
- **No hay detector automático confiable de estos defectos.** Mientras esto siga así, una persona
  tiene que mirar el reel antes de publicar. Cualquier plan que elimine ese paso está prometiendo de
  más.

---

## Tabla: causa raíz → solución → esfuerzo

| # | Causa raíz | Solución propuesta | Esfuerzo | Reduce |
|---|---|---|---|---|
| 1 | La plantilla del storyboard permite tomas de alto riesgo (manos manipulando en primer plano, cuerpo completo) y no dice nada de anatomía | Política de encuadre de riesgo en `_system_prompt()`: prohibir manos como sujeto, cuerpos completos y objetos asimétricos como héroe | **Bajo** (1 archivo, prompt) | Dedos, extremidades |
| 2 | Los beats no declaran contacto con superficie ni sombra de apoyo | Exigir en cada beat superficie de apoyo + sombra de contacto; sumarlo al `_DEFAULT_VIDEO_STYLE` | **Bajo** | Objetos flotantes |
| 3 | Nada fija la lateralidad de objetos asimétricos | Exigir orientación explícita cuando el objeto asimétrico sea inevitable | **Bajo** | Guitarra invertida |
| 4 | Nunca enviamos prompt negativo (10–20 términos) | Verificar el schema de `generate_video` y, si acepta `negative_prompt`, enviar una lista corta y fija; si no, frases preventivas en positivo | **Bajo** (verificar es gratis) | Artefactos varios |
| 5 | Los primeros/últimos frames de cada segmento son los más inestables | Recorte de ~0.3s por punta con el ffmpeg que ya tenemos | **Bajo-medio** | Artefactos breves |
| 6 | Text-to-video puro: el modelo inventa la escena entera sin ancla visual | Image-to-video: imagen por shot → aprobación humana en preview → `medias: start_image` | **Medio** (reusa el camino de fotos) | Todo lo anterior, y hace barato el descarte |
| 7 | Un segmento malo obliga a repagar el reel entero | Endpoint de regeneración por segmento + re-ensamblaje | **Medio** | Costo de corregir |
| 8 | Con voz, cada segmento dura 10s y la degradación es acumulativa | Segmentos de 5s en reels mudos; con voz es un techo de `explainer_video` | **Bajo** (solo mudos) | Deriva por duración |
| 9 | Nada inspecciona los píxeles antes de publicar | Checklist de QC pre-publicación en la página de review | **Bajo** (UI) | Publicar un fallo |
| 10 | El bulk genera reels sin revisión de storyboard | Decidir si el bulk con `tipo_post=reel` debe pausar | **Medio** | Fallos en lote |
| 11 | El modelo default es el más barato | `seedance_2_0` ya está en el desplegable (3× costo) | **Nulo** | Calidad general |

---

## Fase 3 — Plan de acción priorizado

### Etapa 0 — Verificación previa (gratis, 15 minutos)

Antes de decidir nada sobre prompts negativos, volcar el schema real de las tools del MCP:

```bash
cd api/scripts && python mcp_bootstrap.py
```

Sin flags lista las tools y guarda el schema completo en `mcp_tools.json`. De ahí sale la respuesta a:
¿`generate_video` acepta `negative_prompt`? ¿`seed`? ¿qué roles admite `medias`? ¿los admite
`kling3_0_turbo` o solo algunos modelos? No consume créditos. **Todo lo que sigue sobre prompts
negativos depende de este resultado.**

### Etapa 1 — Prompt y plantilla (bajo esfuerzo, sin costo, sin plataforma nueva)

Toca `post_writer._system_prompt()`, `_DEFAULT_VIDEO_STYLE` y `_segment_prompt()`. Los dos flujos lo
heredan gratis porque viven en el núcleo compartido. Suma al bloque de CINEMATOGRAPHY:

- **Política de riesgo de encuadre**: nada de manos manipulando objetos en primer plano; nada de
  cuerpos completos caminando; si aparecen manos, que sea en plano medio o más abierto y en reposo o
  con un movimiento simple.
- **Anclaje obligatorio**: cada beat nombra la superficie de apoyo del sujeto y su sombra de
  contacto.
- **Orientación obligatoria** para objetos asimétricos conocidos, o prohibición directa de usarlos
  como sujeto principal.
- **Preferir materia sobre mecanismo**: objetos, texturas, líquidos, luz y naturaleza fallan mucho
  menos que anatomía y maquinaria articulada.

Añadir tests en `api/tests/` sobre el prompt armado, en la línea de `test_segment_prompts.py`.

**Expectativa honesta: reduce la frecuencia. No la lleva a cero.**

### Etapa 2 — Por qué quedó afuera

La idea era recortar ~0.3s de cada punta de cada segmento con ffmpeg. Al implementarla apareció el
problema: **solo aplicaría al camino mudo**. Los reels con voz —el default— no se unen con el concat
local de ffmpeg sino con `explainer_video` en el server de Higgsfield, que trabaja con bloques de
ventana fija de ~10s y exige que cada bloque conserve su audio. Recortar ahí desincroniza la voz o
cuelga el join.

O sea: la Etapa 2 no tocaría el camino que más nos importa, y sí acortaría la duración final de los
reels mudos sin que nadie lo pida. Implementarla sería trabajo que no mueve la aguja del problema
reportado. Queda descartada hasta que exista una razón concreta (por ejemplo, si los reels mudos
pasan a ser un caso frecuente).

### Etapa 3 — Checklist de QC pre-publicación (bajo esfuerzo, UI)

En `/jobs/:id/review`, una lista visible que haya que marcar antes de habilitar Publicar:

- [ ] Manos: cinco dedos, sin fusiones (o no hay manos en cuadro)
- [ ] Objetos asimétricos con la orientación correcta (instrumentos, teclados, relojes, texto, logos)
- [ ] Todo lo que debe estar apoyado tiene sombra de contacto y no flota
- [ ] Escala coherente entre objetos y entre segmentos
- [ ] Sin texto, marcas de agua ni logos inventados
- [ ] Los cortes entre segmentos no rompen la continuidad
- [ ] La voz calza con lo que se ve (si hay voz)

Es la única compuerta que hoy podemos garantizar que funciona, porque la ejecuta una persona.

**Estado: revertida (29/07/2026).** Se implementó (`VideoQCChecklist.tsx`, bloqueando el botón de
publicar en `/jobs/:id/review` y en la revisión del lote) y se quitó: es una compuerta puramente
procedimental — no inspecciona un frame, no cambia la generación y no tenía enforcement en el
backend — y la fricción no compensaba en este momento del producto. Si se retoma, conviene sumarle
persistencia de la aprobación y validación en el backend, o encararla junto con la Etapa 5
(regeneración por segmento), que es lo que le da una salida distinta a "publicá o tirá todo".

### Etapa 4 — Image-to-video con primer frame aprobado (esfuerzo medio, el cambio de fondo)

1. En la fase A, generar una imagen por beat con el modelo de imagen del post.
2. Mostrarlas en `/jobs/:id/preview` junto al storyboard editable, con opción de regenerar la que no
   convenza (2 cr con `nano_banana_pro`, 0.15 con `z_image`).
3. Al aprobar: subir a Blotato → `import_media_url` → `medias: [{"value": id, "role": "start_image"}]`
   por segmento. El resto de `_run_video_segments` no cambia.
4. Contemplar los dos flujos: en bulk no hay preview, así que ahí las imágenes se generan y se usan
   sin aprobación individual (o se decide que el bulk con reel pause — ver Etapa 6).

Costo: +13% con `nano_banana_pro`, +1% con `z_image`. **El beneficio principal no es que el modelo
falle menos, es que el fallo se ve y se descarta cuando cuesta 2 créditos en vez de 15.**

### Etapa 5 — Regeneración por segmento (esfuerzo medio, mejor relación valor/esfuerzo)

`POST /jobs/{id}/segments/{i}/regenerate`: regenera un solo segmento y rehace el ensamblaje. Hoy un
shot malo cuesta el reel entero. Con esto, el checklist de la Etapa 3 deja de ser "publicá o tirá
todo" y pasa a ser accionable.

### Etapa 6 — Decisiones tomadas

- **El bulk pasa por las mismas compuertas que el individual.** El lote pasó de dos fases a tres:
  escritura → `preview` (revisar/editar guiones, gratis) → generación de medio → `review` (revisar el
  medio) → publicación. La consecuencia a asumir: un lote de 12 filas ya no es "subo el sheet y me
  voy", ahora espera dos aprobaciones. Es el precio de que ningún reel se genere ni se publique sin
  que alguien lo haya mirado.
- **`seedance_2_0` es el default de text-to-video**, a 3× el costo de Kling 3.0 Turbo. El recorrido
  de fotos quedó pinneado a Kling (`HIGGSFIELD_MCP_WALKTHROUGH_MODEL`) porque su rama depende de
  `medias` (start/end frame) y eso solo está verificado con Kling.
- **Sin QA visual automatizado por ahora.** Suma costo por reel y no detecta de forma confiable lo
  que más nos duele (conteo de dedos). Reevaluar cuando exista un detector serio.

---

## Fuentes

Investigación de julio 2026. Son en su mayoría fuentes secundarias (blogs de producto y guías); los
datos de nuestro pipeline salen del código, no de acá.

- [Best AI Video Models 2026: Seedance 2 vs Veo 3.1 vs Kling 3](https://www.teamday.ai/blog/best-ai-video-models-2026)
- [Best Video Generation AI Models in 2026 — Pinggy](https://pinggy.io/blog/best_video_generation_ai_models/)
- [Kling 3.0 Prompt Guide — Atlabs AI](https://www.atlabs.ai/blog/kling-3-0-prompting-guide-master-ai-video-generation)
- [Kling AI Negative Prompts: The Right Options in 2026 — MagicLight](https://magiclight.ai/academy/kling-ai-negative-prompts/)
- [How to Use Reference Images in Image-to-Video (2026) — MagicHour](https://magichour.ai/blog/how-to-use-reference-images-in-image-to-video)
- [Seedance 2.0 Reference Guide (2026) — MagicHour](https://magichour.ai/blog/seedance-20-reference-guide)
- [200+ AI Negative Prompts: Hands, Faces, Anatomy — ZSky AI](https://zsky.ai/blog/ai-negative-prompts-complete-list)
- [higgsfield-ai-prompt-skill (skill de Claude, con FAILURE-MODES y negative-constraints)](https://github.com/OSideMedia/higgsfield-ai-prompt-skill)
- [higgsfield-mcp (referencia de tools de un MCP alternativo)](https://github.com/jfikrat/higgsfield-mcp)
- [AI Video Generation API Pricing (julio 2026) — BuildMVPFast](https://www.buildmvpfast.com/api-costs/ai-video)
- [AI-QC: Automated Media Quality Control — Promwad](https://promwad.com/news/ai-qc-automated-media-quality-control)
- [FingER: Fine-grained Evaluation with Reasoning for AI-Generated Videos (arXiv)](https://arxiv.org/pdf/2504.10358)
