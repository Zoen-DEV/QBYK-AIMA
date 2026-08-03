# Calidad visual de las imágenes (post individual y bulk) — hallazgos y plan

Fecha: julio 2026. Alcance: el flujo de **creación de posts** con imagen — `imagen-unica` y
`carrusel` — a partir de un video de YouTube. No cubre reel/historia en video (ver
[`calidad-video-reels.md`](calidad-video-reels.md)), aunque varios hallazgos son los mismos con
otro traje.

## Estado

| Paso | Estado |
|---|---|
| 1 · Quitar el texto superpuesto | **Hecho** — primero tras un interruptor; **retirado del código** en el paso 9 |
| 2 · Análisis de calidad y coherencia | **Hecho** — este documento |
| 3 · Implementación de P0 y P1 | **Hecha** — ver "Qué quedó implementado" |
| 4 · P2 (regenerar un slide, lint de prompts, comparativa de modelos) | **Parcial** — P2-8 (paso 7) y P2-9 (paso 8) hechos; P2-10 pendiente |
| 5 · Texto renderizado por el modelo + arquitectura de prompt | **Hecho** — ver más abajo |
| 6 · De foto con caption a **pieza diseñada** (recorte + tipografía) | **Hecho** — ver más abajo |
| 7 · Rehacer **una** imagen desde la revisión (P2-8) | **Hecho** — ver más abajo |
| 8 · Lint de los prompts en la compuerta previa (P2-9) | **Hecho** — ver más abajo |
| 9 · Retirar el overlay de Pillow (decisión tomada) | **Hecho** — ver más abajo |
| 10 · El passe-partout: unos slides a sangre y otros con banda de color | **Hecho** — ver más abajo |
| 11 · La plantilla de respaldo salía muda | **Hecho** — ver más abajo |

### Paso 11 — la plantilla de respaldo salía muda

Síntoma reportado (31/07/2026): con Higgsfield generando, el texto sale perfecto; cuando el post
**cae a plantilla**, la imagen se publica sin una palabra. No era un bug nuevo sino la consecuencia
que el paso 9 dejó anotada y aceptada: retirado el overlay, el único que imprimía texto era el
modelo, y a la plantilla no la genera ningún modelo. El resultado es el peor de los dos mundos —una
foto de stock genérica *y además* muda, publicada como si fuera la pieza.

Se repone el dibujado con Pillow **acotado a ese caso**. Lo que hace que no sea volver atrás:

- **Quién lo decide.** `job_runner._lockup_plantilla` pasa el texto a `image_overlay` solo si
  `image_provider.es_plantilla(src)`, que compara la ruta contra `assets/templates/`. La pregunta
  NO es "¿es una ruta local?": una salida del proveedor puede ser un archivo local (un mock, un
  backend que descargue a disco) y esa ya trae el texto impreso — sobreimprimirla lo duplicaría.
  Un test lo fija en los dos sentidos.
- **Qué dibuja.** El mismo lockup que el prompt le pide al modelo, no un layout paralelo: caja alta,
  titular en la banda alta, kicker anclado al pie, área segura del 8 %, alineado a la izquierda. El
  reparto titular/kicker sale de `prompt_architect.dividir_texto` y el acento de `separar_acento`,
  así que la raya espaciada y los `**` siguen siendo notación y no pueden acabar impresos.
- **Con qué colores.** Los de `brand.json` (se extrae el hex de `color_texto`/`color_acento`, con
  respaldo en `paleta`): editar ese archivo tiene que seguir cambiando el look de **todos** los
  posts, también el de los que caen a plantilla.
- **Un solo interruptor.** `IMAGE_TEXT_IN_PROMPT` apagado deja la pieza sin texto por los dos
  caminos. Sin Pillow no hay recorte ni texto: se publica la plantilla cruda, como antes.
- **Tipografía.** La marca pide una grotesca condensada pesada; lo que hay embebido es Montserrat
  (la que quema los subtítulos del reel), así que se compone al peso 900 en caja alta.
  `OVERLAY_FONT_PATH` apunta a la fuente real de la marca sin tocar código.

Lo que **no** cambia: el QA de visión sigue sin verificar plantillas (`image_text_qa` corta en
`_es_local`) — verificar un texto que dibujamos nosotros solo gastaría tokens. Y las plantillas
siguen siendo gratis: no cuentan como generación en el tracking.

### Paso 10 — el passe-partout, o por qué "flat" no significa lo que parece

Síntoma reportado (30/07/2026, carrusel de 4 slides): los slides 1 y 2 salieron con una **banda de
color liso** arriba y abajo —un passe-partout, en hueso `#EDEAE0`, que es un color de la propia
paleta— y los slides 3 y 4 salieron **a sangre**, con el texto sobre la escena. Misma tirada, mismo
brief, resultado distinto. El acabado a sangre es el bueno: tipo e imagen se leen como una pieza.

Lo primero fue descartar el pipeline: `image_overlay._fetch_base` escala con `scale = max(...)` y
**recorta centrado**, nunca rellena, así que las bandas no las añadía la app — las **pintaba el
modelo**. La causa estaba en el brief, y era una sola palabra:

> `composicion_zona`: *"the upper and bottom bands are **flat**, uncluttered clear zones…"*

Para un modelo de imagen una banda *flat* no es "una zona tranquila de la foto" sino un **rectángulo
de color plano**. La instrucción era ambigua, así que cada tirada la resolvía a su manera: unas veces
zona oscura de la escena, otras un panel liso — y al pedirlo dentro de una pieza de marca, lo pintaba
con un color de la paleta. La palabra estaba en **cuatro** sitios del recorrido, y uno de ellos la
premiaba activamente: el rubric de la auto-crítica exigía que *"the reserved bands are genuinely flat
and uncluttered"*, así que un prompt que no las pidiera se **reescribía** hasta pedirlas.

El negativo `"no borders, frames or split panels"` ya existía y no bastaba: cuando el brief pide algo
en positivo y lo prohíbe en negativo, el modelo sigue el positivo.

| # | Cambio | Dónde |
|---|---|---|
| 10-1 | El aire se sigue reservando, pero se dice **de qué está hecho**: `"negative space made of the photograph itself (shadow, defocus, bare surface), never panels of flat colour"`. Conserva los marcadores `clear zone` / `negative space` que exige el validador | `architect.json` (`composicion_zona`), `prompt_architect._clausula_aire` |
| 10-2 | El **sangrado se declara en la sección 1**, la más autoritativa del brief. Se pagó quitando `"art-directed as print"`, que era el propio guiño a la lámina montada | `architect.json` (`piezas`) |
| 10-3 | El rubric deja de premiar bandas planas y pasa a exigir **full bleed** y que las clear zones sean regiones de la foto. Sin esto la auto-crítica revertía 10-1 | `prompts/rubric.json` (`integracion_tipo_imagen`) |
| 10-4 | Negativo explícito de passe-partout: `"no borders, frames, mattes or bands of flat colour at any edge"` | `architect.json` (`negativos`) |
| 10-5 | El LLM recibe la regla en su propia instrucción (escribe la primera mitad de la sección 3) | `architect.json` (`llm.instruccion`, viñeta `composicion`) |
| 10-6 | El camino **sin arquitecto** pide lo mismo (bandas `calm`, nunca `flat`), y de paso deja de contaminar: este texto es el `prompt_base` que el arquitecto le enseña al LLM como *"BASE PROMPT (weak, to rewrite)"*, así que la palabra se propagaba a las secciones creativas | `job_runner._IMAGE_SPACE_FEED`, `_IMAGE_SPACE_VERTICAL` |

**Presupuesto** (lo que casi rompe el cambio). Declarar el sangrado en las secciones 1, 3 y 9 costó
~110 caracteres fijos en **todos** los prompts. Medida de la poda de secciones creativas (`tope` de
`_ajustar_longitud`, con las creativas al máximo de 26 palabras):

| caso | antes | tras el cambio, sin compensar | final |
|---|---|---|---|
| portada corta | 18 palabras | 14 | **18** |
| portada larga | 14 | 10 | **14** |
| slide corto | 14 | 10 | **14** |
| slide largo (kicker + continuidad de set) | 10 | **se pasaba del techo aun podado a 10** | **10** |

Ese último caso no es "un prompt un poco peor": el validador **tira el prompt entero**, y
`_prompt_imagen` devuelve entonces el prompt base — la imagen se genera **sin bloque de texto**. Se
compensó con `max_caracteres` 3100 → **3150** (sigue 50 por debajo del corte del cliente, así que un
prompt válido nunca se trunca) y acortando la cita de la portada en `continuidad_set` de 18 a **12**
palabras (`validacion.continuidad_set_palabras`, configurable): esa cláusula solo la pagan los slides
y su propio comentario ya decía que la portada entra como ancla, no como un segundo brief.

> Ojo para el próximo que toque esto: la poda **ya era agresiva antes** de este paso (14-18 palabras
> en el caso típico, no "sin poda" como decía el paso 6). Si hay que dar aire de verdad a las
> secciones creativas, el techo a subir es `higgsfield_mcp._MAX_PROMPT_CHARS`, que es una cota
> **nuestra**: el catálogo en vivo no declara `maxLength` para `prompt` (verificado en
> `scripts/mcp_tools.json`, jul 2026).

### Paso 9 — el overlay de Pillow se retira

Decisión del 30/07/2026: el texto lo pone Higgsfield al generar la imagen y **no vuelve** el dibujado
posterior. Estaba vivo detrás de `TEXT_OVERLAY_FALLBACK` (apagado desde el paso 5) y esa rama se
eliminó entera: los interruptores (`text_enabled`, `config.text_overlay_fallback`,
`job_runner._overlay_text_on`, `_image_carries_text`), la resolución de fuentes por tono, el
degradado, el word-wrap y los cinco renderers con firma `(src, texto, lang, tone)`.

Lo que queda en `image_overlay.py` —el nombre se conserva porque es el que importan `job_runner` y
los tests— es lo que de verdad sigue haciendo falta después de generar: traer la base (URL del
proveedor o plantilla local), recortarla centrada al aspecto de destino y unificar el color del set.
De 557 líneas a ~150, y de cinco renderers a **dos**: `render_feed` (1080×1350) y `render_story`
(1080×1920). Desde que el texto lo pone el modelo, lo único que distinguía la imagen de LinkedIn de
la de Instagram era el copy que se les dibujaba encima; el recorte del feed es el mismo, así que la
fase de medios la prepara **una vez** y la comparten las tres redes (cada una conserva su subkey
porque cada una publica su propio medio).

Consecuencias que conviene tener presentes:

- La **plantilla local de respaldo** (cuando no hay token OAuth) ya no puede llevar texto: sale la
  plantilla recortada y nada más. Era así de hecho desde el paso 5 — ahora también de derecho.
  **Revertido en el paso 11 (31/07/2026)**: publicar una foto genérica *y además* muda resultó peor
  que la deuda que se quería evitar. El dibujado vuelve, pero acotado a las plantillas y con el
  mismo lockup y los mismos colores que el prompt le pide al modelo.
- El tono por red (`tono_linkedin`/`tono_instagram`/`tono_facebook`) ya no entra en la fase de
  medios: era el que elegía la tipografía del overlay. Sigue gobernando la redacción de los textos.
- `Poppins` se borró de `api/assets/fonts/` (solo la usaba el overlay). **Montserrat se queda**: la
  usan los subtítulos quemados del reel (`video_stitch.burn_subtitles`).
- Tests: se borró `test_image_overlay_toggle.py` y las dos pruebas del interruptor en
  `test_image_text_render.py`; el resto de la suite pasa sin tocar nada más.

### Paso 8 — lint de los prompts, antes de gastar

Nadie revisaba lo que devolvía el LLM (F7). Si entregaba menos escenas de las pedidas, la app
rellenaba con una variación del **título** —justo el fallo que el resto del sistema se esforzó en
eliminar— y lo hacía en silencio; tampoco se comprobaba que las escenas fueran distintas entre sí
ni que esquivaran los clichés que el propio prompt del sistema prohíbe. [`prompt_lint.py`](../api/prompt_lint.py)
los dice en la compuerta donde corregirlos todavía es gratis. **No bloquea**: describe lo que va a
pasar si se genera así.

| Aviso | Nivel | Por qué |
|---|---|---|
| Faltan escenas de slides | alto | Las que falten se rellenan con una variación del **título**, no de la transcripción |
| Dos escenas casi iguales (portada incluida) | alto | El carrusel se lee como la misma imagen repetida |
| Cliché prohibido (`modern office`, `person at a laptop`, …) | alto | Es la lista que el prompt del sistema prohíbe por genérica |
| Sin escena de portada / sin texto de portada | alto | Se cae al respaldo basado en el título o en la 1ª línea del caption |
| Faltan frases del copy | alto | Salen de las líneas del caption, o el slide sale sin texto |
| Shots y líneas de voz que no calzan | alto | `explainer_video` necesita una línea por shot o el reel sale mudo |
| Las manos son el sujeto haciendo algo | medio | Es exactamente donde el modelo dibuja seis dedos |
| La dirección de arte nombra colores | medio | La paleta es identidad de marca: habría dos compitiendo (paso 6-6) |
| Sin dirección de arte | medio | Todo el set cae al acabado genérico de respaldo |

Detalles que importan:

- **No molestar vale tanto como avisar.** A dos slides se les *pide* compartir ambiente y
  materiales, así que la comparación mira las dos caras del problema —el mismo texto reformulado
  (secuencia) y los mismos objetos con otras palabras (vocabulario en común)— y manda la peor, con
  un umbral calibrado para que un carrusel bien escrito no produzca ningún aviso. Hay un test
  dedicado a esa dirección.
- La lista de clichés es un **espejo** de la que vive en el prompt del sistema, y un test comprueba
  que sigan diciendo lo mismo: el lint busca exactamente lo que el prompt prohíbe.
- Los dos flujos, y en vivo: el preview individual revisa contra `POST /jobs/{id}/lint` (que aplica
  los campos sobre una **copia** y no guarda nada) mientras se escribe; el editor por fila del lote
  refresca con lo que devuelve su `POST /jobs/{id}/edit`. El aviso de shots vs. voz, que antes estaba
  duplicado a mano en las dos pantallas, ahora sale de la misma función.
- El lint destapó un hueco: la fila del lote no dejaba editar el **copy impreso** (el preview
  individual sí), así que un aviso sobre el texto no se podía arreglar sin salir del lote. Se
  agregaron los dos campos. Y la etiqueta del campo de dirección de arte pedía "paleta", justo lo
  que el aviso desaconseja: ahora dice "luz, material, óptica y acabado".

Tests: `api/tests/test_prompt_lint.py` — cada aviso, la sincronía con el prompt del sistema, los
falsos positivos (carrusel bien escrito, escenas del mismo mundo visual, mano quieta) y que con datos
rotos devuelva una lista vacía sin reventar.

### Paso 7 — rehacer una imagen suelta desde la revisión

La compuerta de revisión existía para descartar lo que salió mal, pero la unidad de
reintento era el **post entero**: un slide feo obligaba a regenerar los seis. Ahora se rehace
uno solo — `POST /jobs/{id}/regenerate` con el `subkey` — y cuesta **una generación (2 cr)** en
vez del carrusel completo.

| # | Pieza | Dónde |
|---|---|---|
| 7-1 | `regenerate_image(job, subkey)`: mismo prompt, mismo texto, misma referencia visual y mismo QA de visión que la primera tirada — lo único que cambia es la tirada del modelo | `job_runner.regenerate_image` |
| 7-2 | Qué se puede rehacer lo decide el **backend** (formato + redes) y viaja en el snapshot (`images.regenerables`), así las dos revisiones no repiten las reglas | `job_runner.subkeys_regenerables`, `app._job_snapshot` |
| 7-3 | El texto y las escenas salen de las **mismas funciones** que la generación (`_copy_de_imagenes`, `_slide_image_prompts`): rehacer una imagen no puede cambiar lo que la pieza dice ni su encuadre en la escalera | `job_runner._copy_de_imagenes` |
| 7-4 | Tras rehacer se vuelve a **subir el juego** a Blotato por el mismo camino que la generación (extraído a `_subir_imagenes`), o el post publicaría la imagen vieja | `job_runner._subir_imagenes` |
| 7-5 | UI en los **dos flujos**: botón sobre la imagen que se está mirando en `/jobs/:id/review`, y una fila de chips por fila en la revisión del lote. Comparten componente y el mismo endpoint | `RegenerateImage.tsx`, `ReviewCards.tsx`, `BulkProgress.tsx` |

Detalles que importan:

- **La portada no es una imagen más.** En imagen única las tres redes son la *misma* base con
  recortes distintos: rehacerla cambia la de las tres (dejar a LinkedIn con una foto y a
  Instagram con otra sería un bug, no una función). En carrusel se rehace con `generate_base`,
  así el `job_id` de la portada **nueva** pasa a ser la referencia visual de los slides que se
  rehagan después; los ya generados siguen mirando la que los hizo.
- Para eso hacía falta **persistir** en el job dos cosas que antes vivían solo en los locales de
  la fase de imágenes: `images.reference` (el job_id de la portada) y `images.raw_urls` (el
  origen de cada subkey, respaldo de subida). La regeneración crea su propio provider y no
  hereda nada de la corrida original.
- El navegador cachea `/jobs/{id}/image/{key}`, que no cambia de URL: cada regeneración marca
  esa key con una versión (`?v=`) para que la revisión muestre la imagen nueva.
- Una regeneración a la vez por job (409 si ya hay una); fuera de `review`, 409 también.
- Degrada como todo lo demás: si Higgsfield falla, cae a plantilla local, lo dice en el botón y
  el juego sigue publicable.

Tests: `api/tests/test_regenerate_image.py` — qué se ofrece por formato (carrusel, imagen única,
historia, reel y medio subido), que solo cambia la imagen pedida, que sigue diciendo y mirando lo
mismo, la portada como referencia nueva del set, la subida completa del juego, la degradación a
plantilla y el evento de costo.

### Paso 5 — el texto lo renderiza el modelo, y el prompt pasa por un arquitecto

Dos cambios que van juntos: el texto de la pieza dejó de superponerse con Pillow y ahora viaja
**dentro del prompt**, y para que eso funcione el prompt dejó de ser una frase y pasó a ser un brief
estructurado. Todo vive en el núcleo compartido → **individual y bulk lo heredan igual**.

| # | Pieza | Dónde |
|---|---|---|
| 5-1 | El texto (hook de portada, idea por slide) viaja en el prompt; el copy se resuelve **antes** de generar, no después | `job_runner._run_media_phase` |
| 5-2 | Overlay de Pillow detrás de `TEXT_OVERLAY_FALLBACK` (apagado). Los renderers siguen haciendo el center-crop por red: se quitó el dibujado, no el pipeline | `image_overlay.text_enabled`, `config._flag_overlay_fallback` |
| 5-3 | **`PromptArchitect`**: 9 secciones fijas. Las secciones 1 (formato), 4 (texto) y 9 (negativos) las escribe la app con plantillas deterministas; las creativas (sujeto, composición, tipografía, luz, estilo, cámara) las escribe el LLM sobre el prompt base, con respaldo determinista | `prompt_architect.construir` |
| 5-4 | **Auto-crítica**: 2º llamado que puntúa 0-5 contra un rubric de 5 criterios y reescribe si alguno baja de 4 (máx. 2 vueltas). La reescritura nunca toca las secciones de la app | `prompt_architect._autocritica` |
| 5-5 | **Validador programático**: rechaza sin el texto literal, sin alguna de las 9 secciones (o vacías/desordenadas), sin aspecto o aire negativo declarados, o fuera de rango de longitud | `prompt_architect.validar` |
| 5-6 | **QA de visión**: un modelo lee el texto impreso y lo compara con el esperado (acentos sí, mayúsculas y puntuación no). Si no coincide, se regenera esa imagen con la instrucción reforzada, hasta 2 veces | `image_text_qa`, `job_runner._verificar_texto` |
| 5-7 | Traza: el prompt final y el registro del QA por imagen quedan en `job["images"]["prompts"]` / `["qa"]` (los sirve `GET /jobs/{id}`) y en el log del servidor | `job_runner._prompt_para`, `app._job_snapshot` |

Prompts, rubric y datos de marca **no están en el código**: viven en
[`api/prompts/`](../api/prompts/) (`architect.json`, `rubric.json`, `brand.json`, `qa_vision.json`) y
se recargan solos al cambiar el archivo. `PROMPTS_DIR` los reapunta a otro directorio para probar
variantes sin tocar el repo. La paleta y la tipografía de `brand.json` son un **punto de partida**:
es el primer archivo a editar para que las piezas se parezcan a la marca real.

Detalles que importan (verificados al implementar):

- El texto se pide en bloques de **≤ 8 palabras**. Un texto más largo se reparte en titular +
  subtítulo (nunca se recorta): "China entra en la liga alta de la IA" son 9 palabras y sale como
  `"China entra en la liga"` + kicker `"alta de la IA"`.
- El prompt final se mantiene **por debajo de 1950 caracteres** porque `higgsfield_mcp._image_params`
  trunca a 2000, y un prompt truncado pierde justo la sección de negativos. Si las secciones
  creativas se pasan, se acortan antes de tirar el trabajo del LLM.
- La portada del feed es **una sola imagen compartida** por LinkedIn, Instagram y Facebook: con el
  texto dentro solo puede decir una cosa, así que se usa un único texto de portada (el `image_text.hook`
  del LLM, que ya era común a las tres redes).
- El reintento de la portada usa `generate_base` (no `generate_one`) para que el `job_id` que heredan
  los slides como referencia visual sea el de la portada **buena**.
- Todo degrada sin romper: sin key de LLM el arquitecto usa sus respaldos, sin modelo de visión no hay
  QA, y cualquier excepción cae al prompt base. **Generar nunca se interrumpe por esta capa.**

Tests: `test_prompt_architect.py` (caso feliz, los caminos de rechazo del validador, auto-crítica y
degradaciones), `test_image_text_qa.py` (comparación con acentos + no-verificables),
`test_image_text_render.py` (fase de imágenes de punta a punta: 9 secciones por imagen, texto por
slide, reintentos del QA y los dos interruptores).

### Paso 6 — de "foto con caption" a pieza diseñada

Con el paso 5 las imágenes ya salían bien y con el texto dentro, pero seguían pareciendo una
**fotografía de stock con un caption encima**, y algunos titulares salían cortados por el borde. Las
dos cosas venían del brief, no del modelo:

1. Todo el vocabulario del prompt pedía una **fotografía** (`piezas` decía "Editorial social carousel
   cover… print-quality single still", las referencias eran Monocle / Businessweek / Kinfolk) y encima
   pedía reservar una banda *libre de elementos focales*. Eso es literalmente la receta de foto +
   caption: el modelo compone una foto y mete el texto donde le queda sitio.
2. La **fuente la escribía el LLM** (sección 5, 15-30 palabras libres en cada post) y `brand.json`
   pedía además `"contemporary grotesque sans (Inter / Helvetica Now class)"`. Cada post inventaba su
   tipografía, ninguna era de marca, y nadie declaraba la **escala**: sin altura pedida, el modelo pone
   cuerpo de pie de foto.
3. El margen se pedía como `"from the safe margin"` **sin cuantificarlo nunca**, y los negativos no
   prohibían el recorte. Encima la portada usaba el tercio inferior y los slides el superior — el borde
   superior es donde más se cortaba.
4. El **QA de visión no veía el recorte**: solo comparaba strings, y el modelo lee "Conecta MCP" igual
   aunque a las letras les falte la mitad de arriba. Un titular cortado pasaba como correcto.

| # | Cambio | Dónde |
|---|---|---|
| 6-1 | La pieza se declara **póster diseñado** (base fotográfica + capa tipográfica), no fotografía, y las referencias de marca pasan a linaje de póster | `prompts/architect.json` (`piezas`), `prompts/brand.json` (`referencias`, `tono_visual`) |
| 6-2 | **Lockup de póster** compartido por portada y slides: titular en la banda alta, segunda línea anclada al pie, sujeto en la banda central, iluminado para separarse del tipo. Sustituye al `"keep the subject out of it"` de la v1, que era lo que producía el look de caption | `architect.json` (`zonas_texto`, `composicion_zona`), `prompt_architect._clausula_aire` |
| 6-3 | **Área segura cuantificada** (8% en los cuatro lados, ningún glifo toca el borde) + negativo explícito de recorte, y el **validador la exige**: un prompt sin área segura declarada se rechaza | `architect.json` (`texto.detalle`, `negativos`, `validacion.marcas_area_segura`), `prompt_architect.validar` |
| 6-4 | La **tipografía pasa de LLM a app**: cuarta sección determinista (1, 4, **5**, 9). Familia, color y acento son marca (`brand.json`); la escala del titular es layout por rol (13-16% del alto en portada, 9-12% en slides). El acento es **un solo span** — sin el límite el modelo pinta media frase | `prompt_architect._seccion_tipografia`, `architect.json` (`tipografia`), `brand.json` |
| 6-5 | El **QA de visión detecta el recorte** (`recortado`) y cuenta como fallo, así que dispara el mismo reintento reforzado que un titular mal escrito. El refuerzo ahora también exige el encuadre | `prompts/qa_vision.json`, `image_text_qa.verificar`, `job_runner._verificar_texto` |
| 6-6 | `image_style` deja de inventar paleta por post: la paleta es identidad y la inyecta el arquitecto desde `brand.json`. Antes había **dos paletas compitiendo** en el mismo prompt (la del post en la sección 7 y la de marca en la 6) | `post_writer._system_prompt` |
| 6-7 | El camino **sin arquitecto** (`PROMPT_ARCHITECT=0`) pide el mismo esqueleto, para que el interruptor no cambie la composición | `job_runner._IMAGE_LOOK`, `_IMAGE_SPACE_FEED`, `_IMAGE_SPACE_VERTICAL` |

**Nueva identidad** (`brand.json` v2): near-black `#0B0C0E` + bone white `#EDEAE0` + acid lime
`#C9F227`; grotesca condensada pesada en caja alta (Druk / Compacta / Anton class); un sujeto con
spotlight sobre fondo casi negro, luz de contra dura y viñeta. Es **el único archivo a editar** para
cambiar el look de todos los posts: no hay colores ni fuentes en el código.

Presupuesto de prompt: las secciones deterministas crecieron ~200 caracteres y el techo de 1950 ya
estaba saturado (**1888 de 1950** medidos en el camino de respaldo), así que con el techo viejo la poda
se comía el anclaje concreto de las secciones creativas — justo lo que evita que la imagen salga
genérica. `validacion.max_caracteres` pasó a **3000** y el corte del cliente
(`higgsfield_mcp._MAX_PROMPT_CHARS`) a **3200**. Medido tras el cambio: 2537 (portada) y 2291 (slide)
en el camino con LLM, **sin poda**.

> Histórico (resuelto justo abajo): el corte de 2000 era **nuestro**, no un límite documentado del
> MCP (≈750 tokens no es nada para nano_banana_pro). Quedaba por confirmar contra el server: si
> Higgsfield rechazara un prompt largo, el submit falla visible (RuntimeError → aviso en el job, sin
> imagen) y se baja `_MAX_PROMPT_CHARS`.

**Resuelto (30/07/2026): Higgsfield no declara ni aplica un tope de longitud de prompt.** Medido
contra el MCP en vivo, en tres capas:

| Capa | Qué dice |
|---|---|
| Schema de `generate_image` | `prompt` es `{"type": "string"}` **pelado**: sin `maxLength`, sin `minLength` |
| `models_explore(action:get, nano_banana_pro)` | los `parameters` del modelo son **solo** `resolution` (1k/2k/4k); `prompt` no aparece con restricciones |
| Preflight `get_cost:true` (no encola ni cobra) | acepta prompts de 3000, 3200, 5000, 8000, 12k, 20k, 40k y **80.000** caracteres, siempre `credits=2` |

El barrido vale porque el preflight **sí valida** lo que le mandas — control con los mismos parámetros:
modelo inexistente → `unknown model`; `resolution: "99k"` → `not in allowed options`; `prompt: ""` o
ausente → **`prompt is required for Nano Banana Pro`**; `aspect_ratio: "7:13"` → lo corrige a `9:16` y
lo reporta en `adjustments`. Es decir: el server mira el campo `prompt` y aun así no le pone techo.

> Dos matices antes de subir el número. (1) Lo medido es la capa de validación del MCP; que **acepte**
> 80k no prueba que el proveedor no trunque después — eso solo lo prueba una generación real (2 cr).
> (2) Y sobre todo: aceptar no es obedecer. El límite que importa no es el de la API sino la
> **atención del modelo** — en un prompt muy largo las instrucciones del final pesan menos, y nuestras
> secciones 9 (negativos) y 4 (texto) están justo ahí. Subir `_MAX_PROMPT_CHARS` es seguro en cuanto a
> rechazo, pero no es gratis en cuanto a obediencia: si se sube, hay que medirlo con la hoja de
> contactos, no darlo por bueno.

Pendiente de este frente (P2 del encargo original), **revisado el 30/07/2026**:

- **Palabra de énfasis** — hecho a medias. Existe la palanca **manual**: marcar `**así**` en el texto
  de la pieza durante la revisión previa (`prompt_architect.separar_acento` la extrae y quita las
  marcas antes de que el texto llegue al prompt y al QA). Lo que sigue sin hacerse es que el **writer**
  la marque solo; sin marcas, la elige el modelo de imagen leyendo el string.
- **Contador de slide** (`01 / 08`) como nivel meta — sigue sin hacerse.
- ~~Imagen de referencia de layout de marca en `medias`~~ — **descartado, no pendiente**: el catálogo
  solo expone el rol `image` y esos modelos están tagueados `image-to-image`, así que `medias` no
  presta estilo — da la imagen a **editar** (ver "Corrección · La 'referencia visual' era
  image-to-image"). Por eso `image_reference_slides` está apagado por defecto.

Tests nuevos: área segura y su rechazo por el validador, tipografía determinista (el LLM no la escribe
ni cuando la devuelve), escala por rol, kicker anclado al pie, acento opcional, recorte en el QA, y el
corte del cliente por encima del presupuesto del arquitecto.

### Paso 1 — qué cambió exactamente

> Histórico: el interruptor que describe este paso ya no existe (ver paso 9). Se conserva porque
> explica por qué se apagó el copy antes de rediseñarlo.

`api/scripts/image_overlay.py` ganó un interruptor (`text_enabled()`, env `IMAGE_TEXT_OVERLAY`,
**apagado por defecto**). Con él apagado los seis renderers devolvían la imagen base y nada más;
con `IMAGE_TEXT_OVERLAY=1` volvía el comportamiento de siempre. Lo que **no** cambiaba al apagarlo:
la descarga de la base, el center-crop al aspecto de cada red, los bytes PNG por subkey, la revisión,
la subida a Blotato y el fallback a plantillas. Es decir: se quitó el copy, no el pipeline. Cubría
los dos flujos (individual y bulk) porque vivía en el núcleo compartido.

### Qué quedó implementado (paso 3)

Todo esto vive en el núcleo compartido, así que **individual y bulk lo heredan igual**.

| # | Cambio | Dónde |
|---|---|---|
| P0-1 | Feed generado en **4:5 nativo** (antes 1:1 escalado y recortado) + `resolution: 2k` donde el modelo la acepta. Tabla de capacidades por modelo (aspectos, resolución, rol de `medias`) verificada contra el catálogo en vivo | `higgsfield_mcp` (`_IMAGE_MODEL_CAPS`, `image_aspect`, `_image_params`), `image_provider`, `image_overlay` (lienzo 1080×1350) |
| P0-2 | El espacio para el copy solo se pide si el copy se imprime; si no, se pide llenar el cuadro. Aplica al prompt de la app **y** a las reglas que recibe el LLM | `job_runner._image_space_clause`, `post_writer._system_prompt(text_overlay)` |
| P0-3 | **`image_style`**: dirección de arte por post escrita por el LLM (paleta, luz, materia, óptica, acabado) e inyectada **literal** en la portada y en todos los slides. Editable en el preview individual y en el editor de fila del lote | `post_writer`, `job_runner._image_style`, `app.py /edit`, `preview.astro`, `BulkProgress.tsx` |
| P0-4 | ~~El slide de cierre es el más resuelto del set~~ → **superado (jul 2026)**: el carrusel ya no tiene slide de cierre ni de créditos. Todos los slides después de la portada son informativos, el último incluido (misma escena del LLM, misma escalera de encuadres, misma idea impresa) | `job_runner._slide_image_prompts`, `post_writer` (`INFO SLIDES NEEDED = n_slides - 1`) |
| P1-5 | **Referencia visual real**: cada slide se genera pasando el `job_id` de la portada en `medias`. Si el modelo no la acepta o el server la rechaza, se reintenta sin referencia antes de caer a plantilla | `higgsfield_mcp.generate_image_job`, `image_provider.MCPProvider._submit_slide` |
| P1-6 | **Escalera de encuadres** determinista por posición de slide (macro → plano general → detalle medio → fragmento en contrapicado, cíclica) | `job_runner._SLIDE_FRAMINGS` |
| P1-7 | **Grade común**: cada slide se iguala en media y contraste a la portada con Pillow, con topes conservadores (±18 % de contraste, ±18 niveles). Best-effort: nunca interrumpe | `image_overlay.match_grade`, `job_runner._match_cover_grade` |

Interruptores nuevos (los dos encendidos por defecto, documentados en `.env.example`):
`IMAGE_REFERENCE_SLIDES` y `IMAGE_GRADE_MATCH`.

Tests: `test_image_params.py`, `test_image_reference.py`, `test_image_grade.py`,
`test_image_prompts.py` (reescrito) y los nuevos casos de `test_post_writer.py`.

### Lo que dijo el catálogo en vivo (`mcp_bootstrap.py --models image`, jul 2026)

Esto resolvió las cuatro incógnitas que bloqueaban la implementación:

| Modelo | 4:5 | `resolution` | rol de `medias` |
|---|---|---|---|
| `nano_banana_pro` (default) | ✅ | 1k/2k/4k | `image` |
| `nano_banana_2` | ✅ | 1k/2k/4k | `image` |
| `nano_banana` | ✅ | — | `image_references` |
| `gpt_image_2` | ❌ (mejor vertical 3:4) | 1k/2k/4k | `image` |
| `z_image` | ❌ (mejor vertical 3:4) | — | sin soporte |

Y los preflights `get_cost`: **2k cuesta lo mismo que 1k** (2 créditos) y **pasar la referencia no
cambia el costo**. Por eso los dos entraron sin discusión de presupuesto.

---

## Resumen ejecutivo

Con el texto quitado queda a la vista lo que realmente entrega el generador, y ahí hay tres
problemas de fondo, por orden de impacto:

1. **Se genera en el aspecto equivocado.** Todas las imágenes de feed se piden en 1:1 y las de
   LinkedIn/Facebook se fabrican escalando ese cuadrado un 25 % y cortándole el 20 % del ancho. Se
   publica una recomposición ciega y ablandada de algo que el modelo compuso para otro marco. Y en
   Instagram se publica 1:1 cuando 4:5 ocupa ~25 % más de pantalla en el feed: menos superficie es,
   literalmente, menos engagement.
2. **La coherencia del carrusel es una frase, no un mecanismo.** A cada slide se le añade el texto
   *"Same color palette and light as the cover image"* — pero el modelo nunca ve la portada. Cada
   slide es una llamada independiente, sin referencia visual, sin semilla y sin dirección de arte
   compartida. Que un carrusel salga coherente hoy es suerte.
3. **No hay dirección de arte por post.** El "look" es una constante global idéntica para todos los
   posts de la cuenta (`_IMAGE_LOOK`). Eso produce el peor resultado posible en los dos ejes: los
   carruseles no son coherentes por dentro, y todos los posts se parecen entre sí por fuera. El
   camino de video ya resolvió esto con `video_style` (el mismo texto en todos los segmentos); la
   imagen no tiene su equivalente.

Lo caro de arreglar es poco: (1) y (3) son cambios de parámetro y de prompt, sin plataforma nueva ni
créditos extra. (2) necesita una llamada nueva al MCP — que ya sabemos que funciona, porque el
recorrido de fotos usa `medias` en producción.

---

## Cómo funciona hoy la cadena

```
post_writer          → image_prompt (portada) + image_slide_prompts[] (slides) + image_text (copy)
job_runner           → _cover_image_prompt / _slide_image_prompts: escena + image_style +
                       espacio reservado para el texto + grounding
prompt_architect     → brief de 9 secciones con el texto exacto dentro + auto-crítica + validador
image_provider       → MCPProvider.generate_base (bloqueante) + prewarm_extras/resolve (paralelo)
higgsfield_mcp       → generate_image {model, prompt, aspect_ratio, resolution, medias}  ← 4:5 nativo
image_text_qa        → visión: ¿el texto impreso es el esperado? → reintento reforzado (máx. 2)
image_overlay        → descarga, center-crop al aspecto de la red, [texto solo si TEXT_OVERLAY_FALLBACK], PNG
blotato_client       → upload_media_local → mediaUrls
```

(Estado anterior, para leer los hallazgos de abajo en contexto: el prompt era una sola frase con "sin
texto" al final, todo se generaba 1:1 y el copy se dibujaba después con Pillow.)

Puntos de entrada del usuario: el form de `/individual` y las columnas del sheet; el preview permite
editar la escena de la portada y las de los slides antes de gastar créditos.

---

## Hallazgos

### F1 · Todo se genera en 1:1 y LinkedIn/Facebook se sirven de un recorte escalado — **alto**

`DEFAULT_IMAGE_ASPECT = "1:1"` ([higgsfield_mcp.py:91](../api/scripts/higgsfield_mcp.py)) y la rama
de feed llama `provider.generate_base(prompt)` sin aspecto ([job_runner.py:1363](../api/job_runner.py)).
Después `_fetch_base(url, target_size=(1080, 1350))` escala el cuadrado por 1.25 y recorta a lo ancho
([image_overlay.py:207-216](../api/scripts/image_overlay.py)).

Consecuencias: (a) se pierde el 20 % lateral de la composición que el modelo pensó, y el sujeto puede
quedar descentrado o cortado sin que nadie lo mire; (b) hay un upscale real de 1080→1350 px, con la
blandura correspondiente; (c) Instagram recibe 1:1 pudiendo recibir 4:5. En el carrusel pasa lo mismo:
los cinco slides se generan 1:1.

La historia (9:16) **sí** pide el aspecto nativo al proveedor
([job_runner.py:1282](../api/job_runner.py)) — el camino correcto ya existe, solo que el feed no lo usa.

### F2 · La coherencia entre slides es textual, no visual — **alto**

`_slide_image_prompts` añade *"Same color palette and light as the cover image."* y, en el slide de
cierre, *"same palette as the cover image"* ([job_runner.py:407-415](../api/job_runner.py)). El modelo
no tiene la portada delante: está adivinando qué paleta era.

Y sin embargo el mecanismo está disponible: `generate_image` acepta
`medias: [{value, role}]` donde `value` puede ser el **job_id de una generación anterior**
(schema en `mcp_tools.json`), exactamente como el recorrido de fotos ya pasa `start_image`/`end_image`
en video ([job_runner.py:1204](../api/job_runner.py)). Hoy no se usa, y además `generate_base`
**descarta el job_id** y devuelve solo la URL ([image_provider.py:250](../api/scripts/image_provider.py),
[higgsfield_mcp.py:502](../api/scripts/higgsfield_mcp.py)), así que ni siquiera tenemos a mano el
identificador que haría falta.

Tampoco hay semilla: el schema de `generate_image` no expone `seed`, de modo que la referencia visual
es el único anclaje disponible.

### F3 · No hay dirección de arte por post — **alto**

`_IMAGE_LOOK` es una constante: *"Editorial photography, clean composition, soft natural lighting,
muted professional palette, photorealistic detail"* ([job_runner.py:359](../api/job_runner.py)). Es
genérica (no dice qué paleta, qué luz, qué óptica, qué materia) e idéntica para todos los posts.

Efecto doble: dentro del carrusel cada slide interpreta "muted editorial" a su manera → deriva
estética; y entre posts distintos todo sale igual → el feed se vuelve monótono y anónimo. El video ya
tiene la pieza que falta aquí: `video_style`, escrito por el LLM y repetido **literalmente** en todos
los segmentos, es lo que hace que clips generados por separado corten como un solo video
([job_runner.py:419-428](../api/job_runner.py)). La imagen necesita su `image_style`.

### F4 · Se sigue reservando espacio para un texto que ya no existe — **medio-alto, activo ahora**

Con el overlay apagado, el pipeline sigue pidiendo al modelo que deje media imagen vacía:
`_IMAGE_SPACE_FEED` / `_IMAGE_SPACE_VERTICAL` ([job_runner.py:363-366](../api/job_runner.py)) y, en el
prompt del sistema, *"compose it with a calm, uncluttered area … in the lower half where that text will
sit"* y *"carries text on top too: keep one calm area free of clutter"*
([post_writer.py:58 y 63](../api/post_writer.py)).

O sea: se generan composiciones deliberadamente desequilibradas —peso arriba, vacío abajo— y ya no hay
nada que llene ese vacío. Hay que condicionar las tres reglas al interruptor. Es la corrección más
urgente derivada del paso 1.

### F5 · El slide de cierre y el de créditos se quedaron sin función — **resuelto (jul 2026)**

El último slide existía para atribuir el video original (canal, título, "Link en bio") y su escena se
pedía *"minimal … low saturation"*. Sin texto, el carrusel terminaba en una imagen apagada, sin
mensaje y sin atribución: un anticlímax justo donde va el CTA.

**Decisión de producto tomada: se quitó del set.** El carrusel de N slides es ahora una portada + N−1
slides informativos, sin ninguna excepción en el último: sale de su propia escena del LLM, recibe su
encuadre de la escalera y lleva impresa su propia idea. Con eso desaparecieron la atribución
(`_texto_creditos`, `cierre_creditos` de `architect.json`), el rol `cierre` del arquitecto y el
renderer `render_credits` de Pillow.

### F6 · No hay plan de encuadres: la jerarquía visual queda al azar — **medio**

Al LLM se le *sugiere* variar el encuadre (*"close-up texture → wider still life → detail on a different
object"*, [post_writer.py:62](../api/post_writer.py)), pero nada lo impone ni lo verifica, y el número de
slides es variable (3 a 6). El resultado es una secuencia arbitraria: puede haber tres primeros planos
seguidos, o que el slide más potente sea el cuarto. Un carrusel que funciona tiene un ritmo de
encuadres decidido, no emergente.

### F7 · Cero validación de lo que devuelve el LLM — **resuelto (jul 2026, paso 8)**

> Lo que sigue describe el estado anterior. Hoy [`prompt_lint.py`](../api/prompt_lint.py) avisa de las
> tres cosas en la compuerta previa de los dos flujos; el relleno por título dejó de ser silencioso.

No se comprueba que los N prompts de slides sean distintos entre sí, ni que esquiven los clichés que el
propio prompt del sistema prohíbe. Y si el modelo entrega menos de N, el relleno vuelve a colgar del
**título** (*"Conceptual editorial visual about: {topic} … variation N"*,
[job_runner.py:404-406](../api/job_runner.py)) — exactamente el fallo que el resto del sistema se
esforzó en eliminar (la escena debe salir de la transcripción, no del título). Ese camino se toma en
silencio; el aviso que existe es sobre el *copy*, no sobre las escenas.

### F8 · Una sola tirada por imagen y sin forma de rehacer una sola — **resuelto a medias (jul 2026)**

Cada slide se genera una vez. `count` (1-4 por llamada) sigue sin usarse, pero la segunda mitad del
hallazgo ya no aplica: `POST /jobs/{id}/regenerate` rehace **una** imagen desde la revisión, en los dos
flujos (paso 7). El coste de descartar un slide malo pasó del post entero a 2 créditos. Lo que queda
abierto es generar **dos candidatos** y elegir, que es lo que duplicaría el coste por reintento.

### F9 · Ningún acabado determinista sobre el resultado — **bajo-medio**

Al quitar el degradado no queda ningún post-proceso: ni grade común, ni igualación de exposición o
temperatura entre slides. Pillow ya es dependencia (`ImageStat`, `ImageEnhance`), así que unificar el
set es gratis en créditos y determinista — es la red de seguridad para cuando el modelo derive igual.

### F10 · El modelo por defecto no se eligió para este uso — **bajo**

`nano_banana_pro` (2 cr/imagen) es, según el propio catálogo del MCP, el recomendado para *4K/text/
diagrams*; para fotografía editorial recomienda `soul_2`, y `marketing_studio_image` para comercial.
Nadie los comparó con contenido real. Puede ser que estemos pagando el más caro para un uso en el que
no es el mejor.

### F11 · Cualquier parámetro nuevo debe entrar por los dos flujos — **nota de implementación**

Si la dirección de arte se vuelve elegible por post (paleta, estilo), hay que tocar el form de
[`individual.astro`](../frontend/src/pages/individual.astro) + `create_job`, **y** las columnas de
[`sheets.py`](../api/sheets.py) (`COLUMNS`/`DEFAULTS`/`ALLOWED`/`DROPDOWN_OPTIONS`/`COLUMN_HELP` +
`_row_to_spec`). Si en cambio la escribe el LLM (como `video_style`), no toca ninguna entrada — por eso
esa es la vía recomendada en la propuesta 3.

---

## Coherencia del carrusel, punto por punto

### Coherencia estructural (composición, encuadre, jerarquía)

| Dimensión | Hoy | Debería |
|---|---|---|
| Aspecto | 1:1 en todos los slides (F1) | Un único aspecto nativo, 4:5, en todo el set |
| Encuadre | Sugerido al LLM, sin control (F6) | Escalera fija por índice: portada media-amplia → detalle → textura → cierre amplio |
| Óptica / altura de cámara | No se declara nunca | Declarada una vez y repetida literal en los N prompts (p. ej. "35 mm, altura de pecho") |
| Posición del sujeto | Libre | Regla común (sujeto en el mismo tercio, mismo aire alrededor) |
| Jerarquía | Emergente | La portada manda: es la única con sujeto completo; el resto son fragmentos de su mundo |

Nota a verificar: Instagram aplica **un solo recorte a todo el carrusel** (el del primer slide). Si en
algún momento se mezclan aspectos, IG recorta el resto para igualarlo — otra razón para fijar un único
ratio en el set.

### Coherencia estética (paleta, luz, estilo)

| Dimensión | Hoy | Debería |
|---|---|---|
| Paleta | "muted professional palette", genérica y global (F3) | 3 colores concretos por post (nombre + hex), idénticos en los N prompts |
| Luz | "soft natural lighting" | Fuente, dirección, hora y calidad, declaradas una vez y repetidas |
| Materia / superficie | No se declara | Material dominante compartido (madera gastada, lino, hormigón…) |
| Referencia visual | Ninguna: solo la frase "same palette as the cover" (F2) | `medias` con el job_id de la portada en cada slide |
| Acabado | Ninguno tras quitar el degradado (F9) | Grade común en Pillow, alineando cada slide a la portada |

Los dos ejes se atacan con la misma pieza: un bloque de dirección de arte escrito una vez por post e
inyectado sin variación en todos los prompts — más una referencia visual real que impida la deriva que
el texto solo no evita.

---

## Propuestas priorizadas

### P0 — **implementadas** (ver "Qué quedó implementado")

**1. Generar en el aspecto de destino, no recortar hacia él.** (F1)
Pedir la base en 4:5 y derivar el 1:1 recortando hacia abajo (sin upscale); los slides del carrusel,
todos en 4:5. Toca `job_runner` (pasar `aspect_ratio`), `image_provider.prewarm_extras` (hoy fija
`"1:1"`) e `image_overlay` (target por red).
*Requiere verificar antes que el modelo acepte 4:5* — hay un comentario en `higgsfield_mcp.py:89` que
sugiere que no todos lo hacen. Si no, usar el ratio más alto soportado y documentarlo.
Coste: 0 créditos extra. Riesgo: bajo. Impacto: alto y en todos los posts.

**2. Desactivar el espacio para el overlay cuando el overlay está apagado.** (F4)
Condicionar `_IMAGE_SPACE_FEED` / `_IMAGE_SPACE_VERTICAL` al interruptor, y pasar a `post_writer` un
flag equivalente para las dos reglas de composición del prompt del sistema. Sustituir la petición de
vacío por una de composición plena.
Coste: 0. Riesgo: bajo. Impacto: alto mientras el texto siga apagado.

**3. `image_style`: dirección de arte por post, espejo de `video_style`.** (F3, F2 parcial)
Campo nuevo del LLM (paleta con 3 colores concretos, fuente y hora de la luz, óptica, materia
dominante, grado de saturación y grano), inyectado **literal** en la portada y en todos los slides, en
lugar de `_IMAGE_LOOK`. Cambios: schema + reglas + parser en `post_writer`, `_compose_image_prompt` en
`job_runner`, y el textarea correspondiente en el preview. Sin tocar el form ni el sheet (F11).
Coste: 0 créditos. Riesgo: bajo (fallback al look actual si el LLM no lo entrega). Impacto: alto en los
dos ejes de coherencia.

**4. Repensar el último slide mientras no haya texto.** (F5) — **decidida: se quitó.**
El slide de cierre/créditos ya no existe; el último slide es informativo como los del centro. Coste: 0
(la cantidad de slides no cambia, cambia lo que dice el último).

### P1 — **implementadas** (las incógnitas contra el MCP quedaron resueltas)

**5. Referencia visual real entre slides.** (F2)
`generate_base` devuelve también el `job_id`; `prewarm_extras` lo pasa como
`medias: [{value: <job_id de la portada>, role: <rol de referencia del modelo>}]`. Verificar el rol
admitido con `models_explore` / `mcp_bootstrap.py --models image`. Degradación: si el server rechaza el
rol, se genera sin referencia y se sigue (como el resto del pipeline).
Coste: previsiblemente 0 extra — confirmar con el preflight `get_cost`. Impacto: es *el* cambio de
coherencia estética.

**6. Escalera de encuadres determinista por índice de slide.** (F6)
Un encuadre fijo por posición, compuesto en `job_runner` (no delegado al LLM, que ya tiene bastante).
Coste: 0. Impacto: medio-alto en coherencia estructural.

**7. Grade común en Pillow.** (F9)
Alinear media y desviación por canal de cada slide a las de la portada, con tope para no romper la
imagen. Determinista, gratis, y funciona incluso cuando el modelo deriva.
Coste: 0. Riesgo: bajo si se acota la corrección.

### P2 — parcialmente implementadas (producto e iteración)

**8. Regenerar un slide suelto desde la revisión.** (F8) — **implementada** (paso 7): endpoint
`POST /jobs/{id}/regenerate` + botón en las dos revisiones. Coste: 2 cr por reintento. Queda fuera
la variante `count=2` (generar dos candidatos y elegir), que duplica el coste por reintento.

**9. Lint de prompts en el preview.** (F7) — **implementada** (paso 8): escenas casi iguales, clichés
prohibidos y el relleno basado en el título, que era el caso silencioso. En las dos compuertas previas
y en vivo mientras se edita.

**10. Comparativa de modelos con presupuesto acotado.** (F10) `nano_banana_pro` vs `soul_2` vs
`gpt_image_2` sobre los mismos 3 prompts. ~20 créditos y una decisión informada.

---

## Cómo medir (antes de dar nada por bueno)

Sin una forma barata de comparar, esto se vuelve opinión. Propuesta mínima:

- **Corpus fijo**: 3 videos de YouTube de naturaleza distinta — uno abstracto (charla/ideas), uno
  concreto (tutorial con objetos), uno de entrevista. Siempre los mismos.
- **Salida**: por cada variante, un carrusel completo; montar una hoja de contactos (una fila por
  variante, un slide por columna) con Pillow.
- **Criterios**, en este orden: (a) ¿se reconoce de qué va el contenido?; (b) ¿hay artefactos que
  delatan la IA?; (c) ¿los slides parecen del mismo set?; (d) ¿aguanta a 300 px de ancho, que es el
  tamaño real en el feed?
- **Coste por variante**: 5 slides × 2 cr = 10 créditos con `nano_banana_pro`.

## Incógnitas

Resueltas contra el MCP en vivo antes de implementar (ver la tabla del catálogo más arriba):

1. ~~¿Aceptan 4:5?~~ → sí `nano_banana_pro`/`_2`/`nano_banana`; no `gpt_image_2`/`z_image`, que caen
   a 3:4 (vertical, sin escalado) en vez de volver al cuadrado.
2. ~~¿Roles de `medias`?~~ → `image`, salvo `nano_banana` (`image_references`) y `z_image` (sin soporte).
3. ~~¿Cambia el coste con `medias` o con `2k`?~~ → no: 2 créditos en todos los preflights.

Resueltas contra la documentación de Blotato y de las plataformas (jul 2026):

4. ~~¿Qué ratio quieren el document carousel de LinkedIn y el multi-foto de Facebook?~~ → **4:5 está
   bien en las tres**, no hay nada que cambiar. Detalle abajo.
5. ~~¿El texto superpuesto vuelve?~~ → **no**: se retiró el código (paso 9). El texto lo renderiza el
   modelo y punto.

Abierta:

6. **Falta la verificación en producción**: nadie ha visto todavía un carrusel real generado con estos
   cambios. Es lo primero que hay que hacer — un post individual y un sheet de varias filas.

### El 4:5 contra las tres redes (jul 2026)

| Red | Lo que acepta | Nuestro set (1080×1350 PNG) |
|---|---|---|
| **Instagram** carrusel | 1:1, **4:5**, 1.91:1; ancho 320–1440 px; **todos los items se recortan según el primero** | ✅ nativo, y un juego uniforme en 4:5 es justo lo que evita el recorte |
| **Facebook** multi-foto | mismas specs que la imagen de feed: 1:1, **4:5**, 1.91:1; ≥1080 px de ancho | ✅ listado explícitamente |
| **LinkedIn** document carousel | 2–10 imágenes JPG/PNG; cada una con las specs de imagen de LinkedIn (≤5 MB, <36.152.320 px) | ✅ 1.46 Mpx y **máx. 1,9 MB** medidos sobre 21 imágenes reales de `api/outputs/` |

El matiz de LinkedIn: Blotato **recomienda** 1.91:1 (1200×627) para la imagen de feed, pero es una
recomendación de display heredada del formato tipo link-preview, no un límite. El rango que LinkedIn
soporta va de 1.91:1 a **4:5**, y 4:5 es el vertical más alto que muestra completo en el feed (más
alto lo recorta a 4:5). Para el carrusel-documento, 1080×1350 es además el estándar de facto.

Conclusión: **no se cambia nada**. Lo que queda por confirmar es empírico y cae dentro de la
verificación en producción (incógnita 6): ver una publicación real en LinkedIn y en Facebook y
comprobar que ninguna de las dos recorta.

---

## Corrección · La "referencia visual" era image-to-image (jul 2026)

P1-5 ("cada slide se genera pasando el `job_id` de la portada en `medias`") partía de una premisa
falsa y **produjo el defecto que pretendía evitar**. En producción el carrusel salía con la misma
foto repetida en varios slides, cada uno con un encuadre distinto.

### La evidencia

Las imágenes de un job real (`api/outputs/<job>/ig-*.png`): `ig-4` es **la misma fotografía que
`ig-0`** —misma curva del ecualizador en la pantalla, mismo papel, mismos rayones del escritorio—
con otro titular encima; `ig-3` es ese mismo encuadre con el panel del monitor cambiado. Los cuatro
`image_slide_prompts` que había escrito el LLM describían objetos distintos y no se ven por ningún
lado.

### La causa

En el catálogo en vivo (`cd api/scripts && python mcp_bootstrap.py --models image`):

```json
{ "id": "nano_banana_pro",
  "medias": [{ "name": "medias", "type": "image", "roles": ["image"] }],
  "tags": ["quality", "text-rendering", ..., "text-to-image", "image-to-image"] }
```

**No existe un rol de referencia de estilo.** El único rol es `image`, y estos modelos son
image-to-image: la imagen que se pasa en `medias` es la que se **edita**, no una guía de paleta. Así
que cada slide era, literalmente, un encargo de "reencuadrá esta foto", y el prompt propio del slide
quedaba reducido a cambiar el texto y algún detalle.

Había además una **segunda causa, íntegramente nuestra**: `job_runner._prompt_imagen` le pasaba al
arquitecto la escena de la portada como `contenido.angulo` en **todas** las imágenes, slides
incluidos. A cada slide se le estaba pidiendo, sin querer, el sujeto de la portada.

### Qué se hizo

| # | Cambio | Dónde |
|---|---|---|
| C-1 | `image_reference_slides` **apagado por defecto**: ningún slide recibe imagen de entrada | `config.py`, `.env.example` |
| C-2 | La escena de la portada viaja a los slides como `contenido.escena_portada` (contexto de set), nunca como `angulo` | `job_runner._prompt_imagen`, `prompt_architect.normalizar_spec` |
| C-3 | Cláusula determinista `continuidad_set`: declara en dos mitades lo que se **comparte** (set, superficies, luz, paleta) y lo que debe **cambiar** (objeto protagonista, cámara, encuadre) | `prompts/architect.json`, `prompt_architect._clausula_set` |
| C-4 | El escritor pide un **hero object físicamente distinto** por slide, no "otro detalle del mismo" | `post_writer._system_prompt` |
| C-5 | Negativos contra el **pseudo-texto dentro de la escena** (pantallas, perillas, etiquetas): se veían monitores rotulados `EOARFAM` y perillas `SHUMAD/ER` | `prompts/architect.json` (`negativos`), `post_writer._system_prompt` |

La coherencia del set queda entonces en tres piezas que **no** clonan la imagen: la dirección de arte
compartida (`image_style`, literal e idéntica), el lockup tipográfico determinista (marca + escala por
rol) y el grade común de Pillow. El cableado de `medias` se conserva detrás del flag por si el
catálogo llega a exponer alguna vez un rol de estilo.

### Lección

Un parámetro verificado como *existente* no es un parámetro verificado como *lo que uno cree que
hace*. `_IMAGE_MODEL_CAPS` se comprobó contra el catálogo —el rol `image` existe y el submit no
rebota— pero nadie comprobó el **efecto**, y el aviso estaba a la vista en los `tags` del propio
modelo. Cuando un cambio busca un efecto visual, la verificación es mirar las imágenes.
