# Plan: calidad profesional de imágenes y carruseles

> **Documento de ejecución.** Está escrito para arrancarse en frío, en una sesión nueva, sin
> el contexto de la conversación que lo originó. Todo lo que hace falta saber está acá.
>
> Estado: **ejecutado** (agosto 2026), rama `fix/calidad-carrusel`, un commit por fase.
> Las fases 0–8 están completas y la batería está verde (829 tests). Lo que quedó fuera,
> porque necesita generaciones reales contra Higgsfield (créditos + sesión OAuth) y no se
> puede hacer desde el código:
>
> - **El A/B de la fase 5** (cortes de línea contra la tasa de acierto del QA de texto).
>   Procedimiento y criterio de decisión en [`calidad-imagenes.md`](calidad-imagenes.md).
>   El flag `IMAGE_LINE_BREAKS` queda encendido mientras tanto.
> - **La fase 9, puntos 2 a 4** (recorrido manual de los dos flujos y del dry-run).
> - **El paso manual de la fase 4.3**: sanear en `/cuenta` las identidades guardadas
>   antes de las puertas nuevas. Checklist en
>   [`identidades-visuales.md`](identidades-visuales.md), sección «0-bis».
>
> El resultado de cada fase, con lo que se midió y lo que se decidió, está en
> [`calidad-imagenes.md`](calidad-imagenes.md) → paso 13.

---

## 0. Contexto — qué falla y por qué importa

Auditoría de un carrusel real de 5 piezas (portada + 4 slides de info, formato `carrusel`,
identidad visual de usuario, modelo Nano Banana Pro, 1856×2304). Diagnóstico resumido:

**Lo que se ve en las piezas:**

1. **La estructura de la pieza cambia tres veces dentro del mismo carrusel.** La portada sale
   con un passe-partout blanco; los slides 2, 3 y 5 con bandas negras planas arriba y abajo
   (letterbox); el slide 4 a sangre. El lector lo nota antes que cualquier contenido.
2. **Cinco localizaciones y cinco luces distintas.** Cuarto oscuro → salón cálido a mediodía →
   estante oscuro → escritorio de madera → laboratorio industrial de osciloscopios. Temperatura
   de color de fría a ~4000K sin criterio.
3. **Simultáneamente repetitivo e incoherente.** El MISMO chasis Dell aparece en la portada, en
   el slide 3 y en el slide 5 (este último es la portada re-escenificada), mientras 2 y 4 cambian
   de mundo por completo.
4. **Tres familias tipográficas distintas** en cinco piezas, un slide en **caja baja** cuando la
   identidad dice `all caps`, y los cinco kickers en una sans neutra tipo UI que lee como
   "caption pegado sobre una foto".
5. **Viuda tipográfica** en la portada: la palabra "EN" sola en la tercera línea al 14% del alto.
6. **Escalera de planos inexistente**: cuatro slides a la misma distancia y a la misma altura de
   cámara, pese a que la identidad declara `ritmo_carrusel` de close-up a plano general.
7. **Artefactos de IA**: halo de recorte chartreuse alrededor del sujeto (slide 2), objeto
   flotando sin soporte ni sombra (slide 5), unidad de DVD sin bandeja sosteniendo un disco
   (slide 4), pseudo-texto legible en carátulas, papeles y pantallas (slides 2 y 5), billetes de
   **dólar** en un post sobre España.

**Causas raíz verificadas en el código** (cada una se ataca en una fase de este plan):

| # | Causa | Evidencia |
|---|---|---|
| C1 | La cláusula `SET CONTINUITY` **nunca se emite**: `_clausula_set` exige `rol_slide == "contenido"` pero desde la escalera de beats los slides llegan como `tension\|desarrollo\|prueba\|remate` | Ejecutado: devuelve `''`; el briefing tampoco incluye `CAROUSEL COVER ALREADY SHOT` |
| C2 | Nada fija la luz entre slides: la sección 6 la escribe el LLM **una vez por imagen** sin conocer a las hermanas, y `image_style` pisa el `tono_visual` de la identidad. En ningún punto del pipeline aparece una temperatura de color | `job_runner._marca_post` líneas 718-720 |
| C3 | Las bandas y el marco los fabrica la propia identidad **desde la capa dura**: `tipografia` contiene "headline **band**" y `color_texto` contiene "over the **dark field**", y ambos se pegan verbatim en la sección 5. Cuando la escena no es oscura, el modelo pinta el "dark field" como un rectángulo | `architect.json` ya documenta que ante una contradicción el modelo sigue la instrucción positiva y no el negativo |
| C4 | El `ritmo_carrusel` de la identidad habla de un **personaje** y la instrucción del arquitecto dice `No people as the main subject`: el modelo descarta la frase entera y con ella la única variación de plano. Visible en el prompt del slide 2: *"…as sole character, no human anatomy"* | `visual_identity.validar` comprueba orden y longitud, no compatibilidad |
| C5 | Ninguna puerta valida que la tipografía de una identidad sirva a escala de póster: pasó "grotesque-inspired sans" + secundaria "regular weight, mixed case" | `validar` solo mira longitudes |
| C6 | Nada controla la caja ni los cortes de línea: la sección 4 dice "never re-wrap" pero no dicta las líneas | `_seccion_texto` |
| C7 | **No existe QA de conjunto.** Cada imagen se genera, se valida y se acepta aislada; `rubric.json` puntúa un prompt sin conocer a sus hermanos, y `image_text_qa` solo ve ortografía y recorte | Estructuralmente incapaz de detectar 1–4 |
| C8 | El sujeto pide desorden ("cluttered table") y cada objeto secundario es una superficie más donde escribir pseudo-texto; el negativo va al final de 3.550 caracteres, la posición más débil | `architect.json` `llm.instruccion` |
| C9 | El idioma/país detectado solo llega a la sección de texto, nunca al brief del sujeto → props por defecto estadounidenses | `_prompt_imagen` recibe `lang` y no lo propaga al sujeto |

---

## Reglas de ejecución

- **Idioma del proyecto: español** en comentarios, docs y mensajes de UI. Los valores que se
  inyectan en prompts de imagen van **en inglés** (es como los lee el modelo).
- **Regla de los dos flujos**: todo lo que se toque tiene que funcionar en individual **y** en
  bulk. Casi todo este plan vive en el núcleo compartido (`prompt_architect`, `job_runner`), que
  los dos heredan gratis; lo único que hay que duplicar es la **UI** de las compuertas
  (`frontend/src/pages/jobs/[id]/preview|review.astro` y el editor del lote en `batches/[id]`).
  Cada fase dice explícitamente si toca UI.
- **Sin dependencias nuevas.** El detector de bandas usa Pillow, que ya está.
- **Presupuesto de caracteres**: `architect.json → validacion.max_caracteres` (hoy 3550) debe
  seguir 50 caracteres por debajo de `higgsfield_mcp._MAX_PROMPT_CHARS` (hoy 3600). Varias fases
  añaden texto fijo a TODOS los prompts. Hay un paso de medición obligatorio en la fase 0 y una
  re-medición al final de cada fase que añada caracteres (1, 2, 3, 7).
- **Tests después de cada fase**: `cd api && python -m pytest`. Ninguna fase se da por cerrada
  con tests en rojo.
- **Un commit por fase**, con el prefijo `fix(imagen):` o `feat(imagen):` según corresponda.
- Nada de este plan puede **interrumpir** una generación: todo lo nuevo que llame a un modelo o
  a Pillow va en `try/except` y degrada a lo que había, igual que `_verificar_texto` y
  `_match_cover_grade`.

---

## Fase 0 — Preparación y línea base

**Objetivo**: poder medir si el plan mejora algo, y no quedarse sin presupuesto de caracteres a
mitad de camino.

1. Rama nueva desde `main`: `fix/calidad-carrusel`.
2. Correr la batería completa y anotar el estado de partida: `cd api && python -m pytest`.
3. **Script de medición del peor caso.** Crear `api/scripts/medir_prompt.py` (script de
   diagnóstico por terminal, no se importa desde la app): construye el prompt del caso peor
   —slide de info con `rol=remate`, con kicker, con `escena_portada` larga, identidad de usuario
   con todos los campos al máximo (`MAX_TEXTO`, `MAX_RITMO_ITEM`)— por el camino **de respaldo**
   (sin LLM) y por el camino con secciones creativas al máximo (26 palabras cada una), e imprime
   la longitud de cada sección y el total. Guardar la salida como línea base en el propio commit
   (comentario en el script o `docs/` si prefieres).
4. Anotar cuántos caracteres de margen quedan hasta `max_caracteres`. Ese margen es el
   presupuesto que reparten las fases 1, 2, 3 y 7.

**Hecho cuando**: existe el script, la batería está verde y está anotado el margen disponible.

---

## Fase 1 — Reparar la continuidad del set (C1)

> **La fase de mayor impacto y menor riesgo. Si solo se ejecuta una, es esta.**

Arregla por sí sola: mundo compartido entre slides, luz y paleta compartidas *como intención*, y
la prohibición de re-escenificar la portada (que es lo que produce el chasis repetido en 3 de 5
piezas).

### Cambios

1. **`api/prompt_architect.py` — `_clausula_set` (≈ línea 643).**
   `if c["rol_slide"] != "contenido" or not c.get("escena_portada"): return ""`
   → usar `rol_base()`, que es la función que ya existe justo para esto:
   `if rol_base(c["rol_slide"]) != "contenido" or not c.get("escena_portada"): return ""`.
   (`rol_base` devuelve `"portada"` solo para la portada y `"contenido"` para todo lo demás,
   beats incluidos. `escena_portada` ya viene vacía en la portada, así que la doble guarda se
   mantiene.)

2. **`api/prompt_architect.py` — `_mensaje_arquitecto` (≈ línea 887).** Misma corrección:
   `if rol_base(c["rol_slide"]) == "contenido" and c.get("escena_portada"):` para que la línea
   `CAROUSEL COVER ALREADY SHOT (…reuse the WORLD, never its hero object or its framing)` vuelva
   a llegar al briefing del LLM.

3. **Comentario de por qué.** Añadir en ambos sitios una línea explicando que la comparación va
   contra `rol_base` y **nunca** contra el literal `"contenido"`, porque los slides llegan con el
   nombre de su beat. Es el fallo exacto que se coló al introducir la escalera de beats.

### Tests (`api/tests/test_prompt_architect.py`)

- Parametrizado sobre **`parch.ROLES_BEAT` completo** (no sobre una lista escrita a mano: así un
  beat nuevo no puede escaparse en silencio): con `escena_portada` no vacía, el prompt final
  contiene `SET CONTINUITY` y el fragmento `DIFFERENT hero object`.
- Mismo parametrizado: `_mensaje_arquitecto` contiene `CAROUSEL COVER ALREADY SHOT`.
- La **portada** no contiene `SET CONTINUITY` (la cláusula es solo de slides).
- Un slide sin `escena_portada` tampoco la contiene.

### Riesgo y control

- Puede romper tests existentes en `test_image_prompts.py` / `test_prompt_architect.py` que
  asumían la ausencia de la cláusula. Revisarlos uno a uno: si el test afirmaba la ausencia,
  estaba blindando el bug — corregir el test, no el código.
- **Presupuesto**: la cláusula añade ~130-160 caracteres a TODOS los slides, y hasta ahora esos
  caracteres nunca se contaron en producción. Re-correr `medir_prompt.py`. Si el peor caso se
  pasa de `max_caracteres`, la salida ordenada de preferencias es:
  1. bajar `validacion.continuidad_set_palabras` de 12 a 9-10;
  2. subir `validacion.max_caracteres` **y** `higgsfield_mcp._MAX_PROMPT_CHARS` a la vez,
     conservando los 50 caracteres de margen.
  Nunca dejar que el validador tire el prompt entero: eso hace que la imagen salga con el prompt
  base y **sin bloque de texto**.

**Hecho cuando**: los tests parametrizados pasan y `medir_prompt.py` confirma que el peor caso
entra sin podar las secciones creativas por debajo de ~20 palabras.

---

## Fase 2 — Bloqueo de luz compartido (C2)

**Objetivo**: que las N piezas de un job declaren **literalmente la misma luz**, en la capa que
el LLM no puede pisar. Hoy la única atadura es una línea blanda de contexto que un LLM distinto
reinterpreta en cada llamada.

### Decisión de diseño que hay que tomar antes de escribir código

CLAUDE.md dice hoy: *"`image_style` sigue ganando a `tono_visual` … Es una decisión, no un
pendiente."* Esta fase **la matiza, no la revierte**, y hay que dejarlo escrito:

- `image_style` (lo que el LLM escribe por post) sigue mandando en el **tratamiento fotográfico**
  → sección 7 `STYLE & REFERENCES`.
- La **luz** (dirección del key, temperatura, falloff, relleno) pasa a ser propiedad de la
  identidad y la escribe la app → prefijo determinista de la sección 6 `LIGHTING & PALETTE`.

Es la separación correcta: el tratamiento es creatividad por pieza; el esquema de iluminación es
lo que hace que cinco fotos parezcan del mismo día, y por definición no puede decidirse cinco
veces.

### Cambios

1. **`api/prompts/architect.json`** — nueva clave `luz_bloqueada`:
   ```
   "luz_bloqueada": "LIGHT LOCK — identical in every piece of this set: {clave} {temperatura} Falloff to {fondo} at the edges, no ambient fill, one visible contact shadow.",
   "luz_temperatura": "Colour temperature fixed at 5400K neutral: no warm or cool drift between pieces.",
   ```
   `{clave}` = el `tono_visual` **de la identidad** (no el `image_style`), recortado a un tope
   nuevo `validacion.luz_palabras` (arrancar en 22). `{fondo}` = primer color de la paleta.
   Documentar en `_comment_luz` por qué la temperatura es fija y app-owned: es el parámetro que
   frenó la deriva cálida y no puede delegarse.

2. **`api/job_runner.py` — `_marca_post` (≈ 715-721).** Dejar el pisado de `tono_visual` por
   `image_style` **como está** y añadir un campo nuevo que viaja aparte:
   `marca["luz_identidad"] = (identidad o brand.json).tono_visual`. Comentar que son dos cosas
   distintas viajando juntas y por qué.

3. **`api/prompt_architect.py` — `normalizar_spec`.** Propagar `marca.luz_identidad` con el
   respaldo habitual `marca.get(x) or marca_def.get("tono_visual")`, para que un job sin
   identidad se comporte exactamente igual que antes (con el `tono_visual` de `brand.json`).

4. **`api/prompt_architect.py` — sección 6.** Cambiar su origen de `llm` a `llm+app` en la tabla
   `secciones` de `architect.json` y **prefijar** el `LIGHT LOCK` a lo que escriba el LLM. El
   LLM sigue aportando el detalle de escena; la app aporta la parte invariante.

5. **`api/prompts/architect.json` — `llm.instruccion`, viñeta `luz`.** Reescribirla: el LLM ya
   no decide dirección ni temperatura; describe cómo esa luz fija cae **sobre esta escena
   concreta** (qué toca, dónde muere, qué separa al sujeto del tipo). Decirle explícitamente que
   la app antepone el LIGHT LOCK y que no lo repita — repetirlo cuesta presupuesto.

### Tests (`api/tests/test_prompt_architect.py`)

- El bloque `LIGHT LOCK` es **byte a byte idéntico** en la portada y en los cuatro beats para una
  misma spec. Es la aserción central de la fase.
- Contiene la temperatura y el hex del fondo de la paleta.
- Con identidad de usuario, el `LIGHT LOCK` sale del `tono_visual` de la **identidad**, no del
  `image_style` del post (test con los dos valores distintos y comprobación cruzada).
- Sin identidad, sale del `tono_visual` de `brand.json` (no-regresión).

### Riesgo

Añade ~140-180 caracteres fijos a todos los prompts. Re-medir con `medir_prompt.py` y aplicar la
misma escalera de la fase 1 (bajar `luz_palabras` antes que subir el techo).

---

## Fase 3 — Matar las bandas planas y el passe-partout (C3)

Tres frentes, porque el defecto tiene tres orígenes y ya volvió dos veces por atacar solo uno.

### 3.1 — Saneo del texto de identidad que entra en la capa dura

`_seccion_tipografia` pega `tipografia`, `tipografia_secundaria` y `color_texto` **verbatim**. Si
esos strings contienen vocabulario de layout, la identidad está escribiendo layout sin permiso.

1. **`api/prompt_architect.py` — nuevo helper `tinta(valor: str) -> str`**: de
   `"Soft bone white (#F5F3EE) for all caps headline and body over the dark field."` devuelve
   `"Soft bone white (#F5F3EE)"` — el nombre del color y su hex, y nada más. Implementación:
   recortar en el hex (el validador ya garantiza que está). Si no hay hex, devolver el valor tal
   cual (degradar, nunca romper).
2. Usarlo en `_seccion_tipografia` para `color_texto` y `color_acento`. La sección 5 habla de la
   **tinta**; el fondo lo declaran las secciones 1, 3 y 6.
3. **Nueva clave `palabras_layout_prohibidas` en `architect.json`**: `["band", "panel", "block",
   "frame", "matte", "letterbox", "bar", "box", "backdrop", "background", "field"]`, con un
   `_comment` explicando el caso real ("headline band" y "over the dark field" fabricaron el
   letterbox). Filtrar con ellas `tipografia` / `tipografia_secundaria` antes de pegarlas —
   eliminar el sintagma, no la cadena entera, y degradar al valor original si el filtro deja el
   campo vacío.

### 3.2 — Reforzar el sangrado en positivo, en la sección 1

`architect.json → piezas.portada` y `piezas.contenido`: añadir al final
`" The photograph bleeds past all four edges: there is no border, matte, letterbox or panel of flat colour anywhere in the canvas."`

La sección 1 es la más autoritativa del brief y el negativo por sí solo ya se demostró
insuficiente. Coste: ~120 caracteres fijos. Re-medir.

### 3.3 — Detector de bandas y marcos, post-generación

**Es el único de los tres que convierte la regla en un test automático.** Los otros dos son
prompt, y el prompt ya falló dos veces.

1. **`api/scripts/image_overlay.py` — nueva función `bordes_planos(png: bytes) -> list[str]`**
   (Pillow, sin dependencias nuevas). Devuelve qué bordes son planos: `["arriba", "abajo"]`,
   `["marco"]`, `[]`. Algoritmo:
   - Convertir a escala de grises y muestrear medias y varianzas **por fila** (bordes
     horizontales) y **por columna** (verticales).
   - Un letterbox no es "una zona oscura": es un **escalón**. Recorrer desde el borde hacia
     dentro, dentro del primer 25% del lado; marcar banda plana si (a) todas las filas
     recorridas tienen varianza por debajo de `_VARIANZA_PLANA` **y** (b) existe una fila donde
     la diferencia de medias contra la anterior supera `_SALTO_MIN`.
   - Marco (`"marco"`) si los cuatro bordes son planos y sus medias caen dentro de
     `_TOLERANCIA_MARCO` entre sí.
2. **Calibrar los umbrales contra las imágenes reales que ya hay en `api/outputs/`**, no a ojo:
   una escena nocturna legítima tiene una banda alta oscura y de baja varianza, y **no** debe
   dar positivo. Sin escalón no hay banda. Dejar los números como constantes con un comentario
   que diga contra qué se calibraron.
3. **`api/config.py`**: flag `image_band_qa: bool = True` (env `IMAGE_BAND_QA`), documentado
   junto a los otros flags de imagen.
4. **`api/job_runner.py`**: conectar en el mismo punto donde ya vive el QA de texto —dentro del
   bucle de slides y para la portada—, reutilizando el patrón de `_verificar_texto`
   (`rehacer()`, registro en `job["images"]["qa"]`). Diferencias:
   - **Un solo reintento** (no 2): el coste de regenerar es real y el defecto es binario.
   - El reintento añade al prompt un refuerzo de sangrado; reutilizar el mecanismo de
     `refuerzo=True` que ya existe en `_prompt_imagen`, o añadir uno análogo `refuerzo_sangrado`.
   - Se mide sobre la imagen **cruda del proveedor**, antes del overlay y del `match_grade`:
     así se juzga lo que hizo el modelo, no lo que hizo Pillow.
5. Registrar el veredicto en `job["images"]["bandas"]` para que las dos compuertas de revisión
   puedan mostrarlo.

### Tests — nuevo `api/tests/test_image_bands.py`

Imágenes sintéticas generadas con Pillow (sin red, sin proveedor):
- Degradado a sangre → `[]`.
- Degradado + barras negras arriba y abajo → `["arriba", "abajo"]`.
- Degradado con marco blanco en los cuatro lados → `["marco"]`.
- **Escena nocturna legítima**: degradado muy oscuro en la banda alta pero *sin escalón* → `[]`.
  Este es el test que protege contra el falso positivo y es tan importante como los otros tres.

### UI (los dos flujos)

Mostrar el aviso de bandas en la compuerta de revisión de individual y de lote, junto a los
avisos de imagen que ya se pintan.

---

## Fase 4 — Puertas en la identidad visual (C4, C5)

**Objetivo**: que una identidad que no puede funcionar no llegue a generar. Hoy `validar` mira
tipos y longitudes; le faltan dos contratos que fallan en silencio.

### 4.1 — `ritmo_carrusel` no puede tener personas

La instrucción del arquitecto dice `No people as the main subject`. Un ritmo que pide un
personaje es una contradicción dentro del mismo brief, y el modelo la resuelve **descartando el
ritmo entero**: se pierde la escalera de planos sin un solo error.

1. **`api/visual_identity.py`**: nueva constante
   `PALABRAS_PERSONA = ("character", "person", "people", "face", "portrait", "torso", "figure",
   "model", "hands", "human")` con comentario explicando el contrato.
2. Añadir a **`validar`** (error, no reparo): una entrada de `ritmo_carrusel` que contenga
   cualquiera de esas palabras se rechaza, con mensaje accionable que diga **qué** palabra y
   **por qué** ("el héroe de cada pieza es un objeto; el arquitecto prohíbe personas como sujeto
   principal, así que este plano se descartaría entero y el carrusel saldría sin escalera de
   planos"). Va en `validar` y no en `revisar_diseno` porque no es un reparo discutible: es
   inoperante, como una paleta desordenada.
3. **`api/identity_extract.py` — `_reglas_esquema`**: generar la regla desde
   `vi.PALABRAS_PERSONA`, para que el extractor la reciba y su reintento pueda corregirse solo.
4. **`api/prompts/identity_extract.json` — criterio `ritmo_carrusel`**: añadir que el sujeto de
   cada beat es siempre un **objeto**, nunca una persona, y que solo se declaran distancia,
   altura de cámara y qué llena el cuadro.

### 4.2 — Puerta tipográfica

1. **`visual_identity`**: constantes `FAMILIAS_UI_PROHIBIDAS = ("inter", "helvetica", "arial",
   "roboto", "system ui", "ui sans", "neutral sans")` y `MARCAS_DISPLAY = ("display",
   "condensed", "grotesque", "grotesk", "extended", "caps", "poster")`.
2. **`validar` (error)**: `tipografia` que nombre una familia UI prohibida. Es exactamente lo que
   el propio prompt de extracción ya advierte que devuelve el look de "caption pegado sobre una
   foto"; que pasara es un agujero del validador.
3. **`revisar_diseno` (reparo, no error)**: `tipografia` sin ninguna marca de display, y
   `tipografia_secundaria` que contenga `regular weight` o `mixed case` — el kicker en peso
   regular y caja mixta es la señal de amateur más fuerte del set auditado. Reparo y no error
   porque una secundaria en caja mixta puede ser una decisión legítima; el usuario tiene que
   verla, no quedarse sin poder guardar.
4. **`identity_extract.py` — `_reglas_diseno`**: generar estas reglas desde las mismas constantes
   (mismo motivo que las de contraste y saturación: escritas dos veces se desincronizan).

### 4.3 — Corregir la identidad ya guardada

La identidad que generó el carrusel auditado incumple 4.1 y 4.2 y está en Mongo: `validar` no
corre al leer, así que seguirá generando mal hasta que se toque. Paso manual, en `/cuenta`:

- Reemplazar las cuatro entradas de `ritmo_carrusel` por una escalera **de objeto**, tensión →
  desarrollo → prueba → remate, respetando el abanico de distancias que la identidad ya quería
  (cerrado → abierto).
- Reescribir `tipografia` nombrando la clase display (peso, ancho, caja, tracking) sin la palabra
  `band`, y `tipografia_secundaria` como la misma familia en un peso que aguante, sin
  "regular weight, mixed case".
- Reescribir `color_texto` como tinta: nombre + hex, sin "over the dark field".
- Revisar `referencias`: "AAA game character splash screen with UI HUD overlays" es lo que produjo
  los glifos HUD chartreuse en la portada y en ninguna otra pieza. Es una identidad de key art de
  personaje aplicada a bodegón técnico; o se reencuadra o seguirá tirando hacia ahí.

### Tests

- `api/tests/test_visual_identity.py`: ritmo con cada palabra de `PALABRAS_PERSONA` → error;
  ritmo de objeto → sin error; `tipografia` con familia UI → error; sin marca display → reparo,
  no error; secundaria con "regular weight" → reparo.
- `api/tests/test_identity_extract.py`: las reglas generadas contienen las nuevas constantes
  (blindaje contra la desincronización prompt/código).

---

## Fase 5 — Caja y cortes de línea (C6)

> **La fase de mayor riesgo del plan**, porque toca la sección 4, que es la que sostiene el QA
> de texto. Va detrás de un flag y con A/B antes de darla por buena.

### 5.1 — Caja alta cuando la identidad la declara

1. **`api/prompt_architect.py`**: helper `_pide_caja_alta(tipografia: str) -> bool` (busca
   `all caps` / `caps` en el string de la familia; tanto `brand.json` como las identidades
   extraídas lo declaran así).
2. Si es `True`, la sección 4 cita el titular ya en `.upper()`. Seguro para el resto del
   pipeline: `image_text_qa.coincide` normaliza mayúsculas, `prompt_lint` compara texto limpio y
   el overlay de la plantilla de respaldo ya dibuja en caja alta.
3. Añadir a `texto.detalle`: `"Set every glyph in the case supplied — never change the case of a word."`

### 5.2 — Cortes de línea explícitos

1. **Nuevo `lineas_titular(titular: str, max_lineas: int = 3) -> list[str]`** en
   `prompt_architect`: reparte el titular en líneas equilibradas por longitud y **nunca deja una
   línea final con una sola palabra de ≤3 caracteres** (es el defecto "EN" de la portada
   auditada). Reutiliza el criterio de corte por coma/punto que ya usa `dividir_texto`.
2. La sección 4 cita las líneas una a una: `break the headline over exactly these lines: line 1
   "…", line 2 "…"`, seguido de `these line labels are instructions, never printed`.
3. **Flag `image_line_breaks: bool = True`** en `config.py` (env `IMAGE_LINE_BREAKS`) para poder
   apagarlo si el QA de texto empeora.

### Validación obligatoria antes de cerrar la fase

Generar **dos carruseles del mismo contenido**, uno con el flag encendido y otro apagado, y
comparar la tasa de acierto de `image_text_qa` (`job["images"]["qa"]`). Si el flag empeora la
precisión del texto, se queda apagado por defecto y se documenta por qué: la exactitud del texto
vale más que la elegancia del corte.

### Tests

- `lineas_titular` nunca devuelve una línea final huérfana de ≤3 caracteres (varios casos).
- Nunca supera `max_lineas`.
- Con `tipografia` en caja alta, el texto citado en la sección 4 está en mayúsculas; sin ella, no.

---

## Fase 6 — QA de conjunto (C7)

**Es la pieza que falta en la arquitectura.** Ningún QA por imagen puede detectar que cinco
piezas no se parecen entre sí: hace falta una llamada que las vea juntas.

### Cambios

1. **Nuevo `api/prompts/qa_set.json`** — prompt del revisor de conjunto. Le pasa las N imágenes
   ya renderizadas y le pide un JSON por slide con cuatro veredictos binarios y su motivo:
   `mismo_mundo`, `mismo_sistema_tipografico`, `mismo_grade`, `sin_marco_ni_bandas`. Nada de
   puntuaciones: binario y con motivo, que es lo que se puede accionar.
2. **Nuevo `api/image_set_qa.py`** — espejo de `image_text_qa`: `disponible(cfg)`,
   `max_reintentos()`, `revisar(imagenes, *, cfg) -> ResultadoSet`. Mismas reglas: Anthropic
   (igual que `image_text_qa`, y por el mismo motivo — abrirlo a Perplexity añade una llamada a
   quien hoy lo tiene apagado y se decide aparte), reducción en memoria, `try/except` total.
3. **`api/config.py`**: flag `image_set_qa: bool = True` (env `IMAGE_SET_QA`).
4. **`api/job_runner.py`**: llamar **después** del bucle de slides y **antes** de
   `_subir_imagenes`, sobre los bytes de `image_bytes` (que es lo que se publica: overlay y grade
   ya aplicados). Los slides marcados como outlier se regeneran por el camino que ya existe
   (`regenerate_image` / `_rehacer_slide`), **una sola ronda**, y después se sube. Registrar todo
   en `job["images"]["qa_set"]`.
5. **Coste**: instrumentar con `_track(...)` como cualquier otra llamada de pago, y añadir la
   tarifa a `pricing.example.json`. Punto único: `job_runner._track`, que los dos flujos heredan.
6. **UI (los dos flujos)**: mostrar el veredicto de conjunto en la compuerta de revisión —
   individual y lote. Un slide marcado como outlier debe ser visualmente evidente ahí, porque el
   botón de rehacer una sola imagen ya existe y es exactamente la acción que toca.

### Tests (`api/tests/test_image_set_qa.py`)

- Con el módulo desactivado o sin modelo de visión, `_run_media_phase` termina igual (no
  interrumpe).
- Un veredicto con un outlier dispara **una** regeneración y **una sola** ronda (no bucle).
- El resultado queda en `job["images"]["qa_set"]` y llega al snapshot que leen las dos UI.

---

## Fase 7 — Atrezzo, plausibilidad física y contexto cultural (C8, C9)

Ataca directamente los artefactos: pseudo-texto en carátulas y pantallas, objetos flotando,
ensamblajes imposibles y billetes de dólar en un post sobre España.

### Cambios (`api/prompts/architect.json → llm.instruccion`)

1. Viñeta `sujeto`, añadir: `"At most 2 secondary objects, and none of them printed matter,
   packaging, screens, labels, paper or signage."` Cada superficie rotulable es un sitio donde el
   modelo escribe pseudo-texto; quitar las superficies es más efectivo que prohibir el texto.
2. `HARD RULES`, añadir: `"Every object rests on or is supported by a named surface and casts a
   contact shadow — nothing floats, nothing is unsupported, every mechanism is assembled the way
   the real object works."` (Cubre el disco flotando del slide 5 y la unidad sin bandeja del 4.)
3. **Contexto cultural**: `_prompt_imagen` ya recibe `lang`. Propagarlo al briefing como línea
   dura: `CULTURAL CONTEXT: the source is in {idioma} — props, currency, plugs, packaging and
   signage must match that context; never default to US props or US currency.` Y añadir
   `"no US currency or US-specific props"` a `negativos` cuando el idioma no sea inglés.
   **Limitación conocida y que hay que documentar**: el pipeline detecta idioma, no país; `es`
   no distingue España de LatAm. Esto elimina el default estadounidense, que es el fallo real
   observado, y no pretende más.

### Presupuesto

Estas tres añaden a la **instrucción** (que no cuenta contra `max_caracteres`, es el mensaje al
LLM) salvo el negativo y la línea de contexto cultural. Re-medir igualmente.

### Tests

- `test_prompt_architect.py`: el negativo de moneda aparece con `idioma="es"` y no con `"en"`.
- La línea `CULTURAL CONTEXT` llega al briefing.

---

## Fase 8 — Red de seguridad en el lint y documentación

1. **`api/prompt_lint.py`** — avisos nuevos en la compuerta previa (los dos flujos la comparten):
   - Prompt de slide **sin** `SET CONTINUITY`: es el canario de la regresión de la fase 1, visible
     para el usuario antes de gastar créditos.
   - Identidad activa con `ritmo_carrusel` o tipografía sospechosa (reusar `visual_identity`, no
     reimplementar las reglas).
   - Mantener el estilo de la casa: **avisa, no bloquea**, y calibrado para que un carrusel bien
     escrito no genere ningún aviso.
2. **Tests** en `test_prompt_lint.py` para cada aviso nuevo, incluido el caso negativo (carrusel
   correcto → cero avisos).
3. **Documentación**:
   - `docs/calidad-imagenes.md`: pasos nuevos con el porqué de cada corrección — sobre todo la
     lección transversal, que es la que evita la próxima regresión: *una variación (o una
     restricción) declarada en la capa blanda pierde siempre contra lo declarado en la dura*, y
     su corolario nuevo: *una identidad puede escribir layout sin querer si sus campos entran
     verbatim en una sección determinista*.
   - `docs/identidades-visuales.md`: las puertas nuevas de la fase 4 y el checklist de pruebas
     manuales actualizado.
   - `CLAUDE.md`: actualizar la sección de generación de imagen (bloqueo de luz, detector de
     bandas, QA de conjunto) y **matizar** la frase sobre `image_style` vs `tono_visual` según la
     decisión de la fase 2.

---

## Fase 9 — Verificación end-to-end (obligatoria, los dos flujos)

Ninguna fase anterior cuenta como terminada hasta que esto pasa.

1. **Batería completa**: `cd api && python -m pytest` en verde.
2. **Flujo individual**: un post `carrusel` de 5 slides desde `/individual`, con una identidad de
   usuario, recorriendo las dos compuertas. Comprobar en las piezas generadas:
   - [ ] Las 5 comparten mundo, superficie y luz.
   - [ ] Ninguna repite el objeto protagonista de la portada.
   - [ ] Ninguna trae marco, letterbox ni banda de color liso.
   - [ ] Una sola familia tipográfica y una sola caja en las 5.
   - [ ] Hay escalera de planos real entre tensión y remate.
   - [ ] Ningún titular con una línea huérfana.
   - [ ] Cero pseudo-texto legible dentro de la escena.
   - [ ] Los props concuerdan con el idioma del contenido.
3. **Flujo bulk**: un `.xlsx` de 3 filas, al menos una con `fecha_hora` para programación, con la
   misma identidad. Comprobar que el lote hereda todo lo anterior y que los avisos nuevos
   (bandas, conjunto, lint) se ven en el editor del lote y en su compuerta de revisión.
4. **Dry-run**: repetir con `dry_run` para no publicar.
5. **Regresión sin identidad**: un job **sin** identidad visual tiene que generar exactamente
   como `brand.json` — no "parecido". Es la garantía de que ninguna fase impuso un blanco donde
   antes había un respaldo.

---

## Resumen ejecutable

| Fase | Ataca | Riesgo | Toca UI |
|---|---|---|---|
| 0 · Preparación y línea base | — | nulo | no |
| 1 · Continuidad del set | C1 | bajo | no |
| 2 · Bloqueo de luz | C2 | bajo | no |
| 3 · Bandas y passe-partout | C3 | medio (calibrar detector) | sí (avisos) |
| 4 · Puertas de identidad | C4, C5 | bajo | no (paso manual en `/cuenta`) |
| 5 · Caja y cortes de línea | C6 | **alto** (detrás de flag + A/B) | no |
| 6 · QA de conjunto | C7 | medio (coste por carrusel) | sí (las dos revisiones) |
| 7 · Atrezzo, física, cultura | C8, C9 | bajo | no |
| 8 · Lint y docs | regresiones | bajo | no |
| 9 · Verificación | — | — | sí (recorrido manual) |

**Si hay que recortar**: las fases 1, 2 y 3 son el 80% de la mejora visible y no dependen de las
demás. La 5 puede quedar apagada tras el flag sin coste. La 6 es la única que evita que todo esto
vuelva a degradarse sin que nadie se entere.
