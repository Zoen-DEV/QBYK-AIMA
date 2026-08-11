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
| 12 · Los slides del carrusel eran versiones de la misma imagen | **Hecho** — ver más abajo |
| 13 · Calidad profesional del carrusel (continuidad, luz, bandas, QA de conjunto) | **Hecho** en código — falta el recorrido manual y el A/B de los cortes; ver más abajo |
| 14 · El slide de contenido lo lidera el TEXTO, y la tipografía distingue identidades | **Hecho** en código — falta el recorrido manual; ver más abajo |
| 15 · El carrusel cuenta una historia, y las piezas dejan de ser siempre una mesa | **Hecho** en código — falta el recorrido manual; ver más abajo |
| 16 · Los slides ENSEÑAN el contenido: sistemas de texto y el acento a la deriva | **Hecho** en código — falta el recorrido manual; ver más abajo |

## Paso 16 · Los slides cuentan el video, y el acento deja de cambiar (ago 2026)

Reportado mirando una tanda: **(a)** cada slide llevaba «un titular sobre el tema del video» en vez
de contar lo que el video dice, y **(b)** dentro de un mismo carrusel, cada slide salía con el
acento de un color distinto.

### (a) Un slide solo sabía imprimir un titular

El lockup tenía exactamente dos bloques —titular y apoyo al pie, partidos por la raya espaciada— y
al redactor se le pedía «a single **self-contained** idea (max ~14 words)» por slide. Con cuatro
slides eso son ~56 palabras para contar un video entero: **con ese presupuesto no se narra, solo se
titula**, y «self-contained» es literalmente pedir frases sueltas. No había dónde poner la
explicación aunque el redactor la escribiera.

Los **sistemas de texto** son el arreglo, y salen de donde la «Lección» de este mismo documento
decía que saldrían: *«los arquetipos de composición — hoy `zonas_texto` es un único lockup con dos
jerarquías»*, parametrizados por job igual que la identidad. Tres sistemas en
`architect.json → sistemas_texto`:

| sistema | bloques | lectura |
|---|---|---|
| `titular` | titular + apoyo | el de siempre |
| `titular_cuerpo` | titular + cuerpo debajo | explicativo |
| `etiqueta_titular_cuerpo` | etiqueta + titular + cuerpo al pie | ficha / paso a paso |

**El reparto es el de `ritmo_carrusel`, que es la frontera que este proyecto ya validó**: qué
bloques existen y dónde van es LAYOUT (`architect.json`, igual para todas las marcas); cuál usa esta
marca es IDENTIDAD (`sistemas_texto`, repertorio de 1-3). El job congela uno en
`params.sistema_texto` al crearse —con el arco y el mundo, en `make_job`, así que los dos flujos lo
heredan— y **la portada lo ignora siempre**: es la pieza que ya funcionaba y la que funda el set.

Lo que hace que el cuerpo sirva para algo es el encargo, no el hueco: el prompt del sistema pasó a
pedir que **el carrusel ENSEÑE la fuente** (quien no vio el video termina el último slide sabiendo
la cosa; cada cuerpo lleva un dato, un nombre, un paso o un mecanismo concretos) con su anti-patrón
nombrado — *un slide que seguiría siendo cierto de cualquier otro video sobre el tema ha fallado*.

### (b) El acento a la deriva: tres causas, y arreglar una no bastaba

| # | Causa | Arreglo |
|---|---|---|
| 1 | `acento_omitido: ["tension"]` **no emitía nada**. El silencio no es una prohibición: el modelo pintaba una palabra igual y elegía el color por su cuenta | `acento_ninguno`, que lo prohíbe y nombra el color único |
| 2 | La rama de acento **explícito** (`**palabra**`) pegaba el color crudo; la automática lo reducía a nombre + hex. Dos formulaciones del mismo color son dos colores | `tinta()` en las dos |
| 3 | La sección 6 la escribe el LLM **por imagen**, y su instrucción decía «Name the brand hex values for the palette»: la paleta se redactaba N veces | `PALETTE LOCK` determinista, hermano de `luz_bloqueada`, + prohibición explícita al LLM de nombrar colores |

La 3 es la lección de siempre en este proyecto —**lo invariante dentro de un job no puede decidirse
una vez por imagen**— y es la tercera vez que aparece con otro traje (luz, mundo, paleta).

### Qué se hizo

| # | Cambio | Dónde |
|---|---|---|
| G-1 | Catálogo `sistemas_texto` (bloques, bandas, escalas, presupuesto de palabras, caja) | `prompts/architect.json` |
| G-2 | `separar_bloques` — punto ÚNICO que convierte `str \| dict` en los bloques del sistema | `prompt_architect.py` |
| G-3 | Secciones 4 y 5 por bloque, con el `detalle` **compartido** | `prompt_architect._seccion_texto`, `_seccion_tipografia` |
| G-4 | `image_text.slides[i]` acepta un OBJETO; el string sigue valiendo | `post_writer`, `app._aplicar_edicion` |
| G-5 | QA de texto **por nivel**: display exacto, cuerpo por similitud | `image_text_qa`, `prompts/qa_vision.json` |
| G-6 | La plantilla de respaldo imprime los mismos bloques | `prompt_architect.lockup_bloques`, `image_overlay._dibujar_texto` |
| G-7 | El lint nombra el BLOQUE que falta, no «faltan frases» | `prompt_lint._revisar_bloques_slide` |
| G-8 | Un campo por bloque en las dos compuertas previas | `preview.astro`, `BulkProgress.tsx` |
| G-9 | Las tres correcciones del acento + veredicto `mismo_acento` en el QA de conjunto | `prompt_architect`, `image_set_qa`, `prompts/qa_set.json` |

**El cuerpo NO se pasa a caja alta** aunque la identidad sea de caja alta: `pide_caja_alta` mira la
familia de DISPLAY, y un párrafo de 30 palabras al 5% del alto en mayúsculas es ilegible. Es el
corolario de F-4 aplicado a un campo nuevo, y lo tienen que respetar los dos renderizadores.

**El QA del cuerpo es por similitud a propósito** (`similitud_cuerpo`, 0.90). Un titular son 3-6
palabras a tamaño de póster y una letra mal se ve desde el otro lado de la sala; un cuerpo son 30
palabras al 5% del alto y ningún generador las clava carácter a carácter. Exigirle lo mismo
convertiría cada errata en una **regeneración pagada de la imagen entera**.

### Presupuesto

El techo subió dos veces y las dos se pagó antes lo que se podía pagar, que sigue siendo la regla:

- **5050 → 5150** por el `PALETTE LOCK` (~130 caracteres fijos). Se pagó escribiéndolo en su forma
  mínima y quitando la paleta de `respaldos.luz`.
- **5150 → 5250** por los sistemas de texto. Acá lo que ocupa no es prosa de brief sino
  **contenido**: un cuerpo son ~200 caracteres que se imprimen en la pieza. Se pagó con (1) el
  `detalle` compartido de la sección 4 —emitirlo por bloque triplicaba la parte cara—, (2) los
  cortes de línea dictados apagados en los sistemas con cuerpo (`cortes: false`, ~140 caracteres: la
  viuda que corrigen es un defecto de titular largo a tamaño de póster) y (3) las escalas de la
  sección 5 en telegrama.

`medir_prompt.py` se extendió para recorrer los tres sistemas, y con **contenido realista** en cada
bloque: a un titular se le piden 6 palabras de texto impreso, y medirlo con tecnicismos de dirección
de arte de 15 caracteres infla el techo por un caso que el redactor no puede producir. 5250 es
exactamente el primer techo que sostiene el escalón de 18 palabras en los tres sistemas y los cuatro
beats; a 5200 caía a 14.

### Qué falta

- **Recorrido manual**, la única verificación que vale: un carrusel con cada uno de los tres
  sistemas, mirando si el cuerpo se lee de verdad a esa escala sobre la foto y si el modelo lo
  renderiza sin erratas gruesas.
- **Comprobar el coste real del QA por nivel**: si el cuerpo dispara más regeneraciones de las
  previstas, el umbral (`similitud_cuerpo`) es la palanca, no el número de reintentos.

## Paso 15 · El arco del carrusel y el mundo de la marca (ago 2026)

Dos defectos que se veían de un vistazo en las últimas tandas: **(a)** las N imágenes de un
carrusel no contaban nada juntas —props sin relación sobre la misma madera— y **(b)** casi todas
eran *un objeto sobre una mesa*, con **identidades visuales distintas** produciendo el mismo tipo
de foto.

### 15.a — Por qué siempre había una mesa

No lo elegía el LLM. Estaba escrito en la **capa dura**, en seis sitios a la vez, y por eso ninguna
identidad podía moverlo:

| dónde | qué decía |
|---|---|
| `architect.json` → `llm.instruccion` → `sujeto` | "ONE concrete hero object… **what it rests on**" |
| `architect.json` → HARD RULE de plausibilidad | "every object **rests on or is supported by a named surface**" |
| `architect.json` → `roles.*.ritmo` (respaldo por beat) | "**still life on a bare surface**", "**flat overhead of one object** on an empty field" |
| `brand.json` → `ritmo_carrusel` | "Mid-distance still life **on a bare table**" |
| `post_writer` → ANCHOR EVERY OBJECT | el único ejemplo era "**the closed laptop sits on a worn oak desk**" |
| `rubric.json` → `especificidad_sujeto` | premiaba "specific **objects**" — y el rubric corre **después** y reescribe |

Y `visual_identity` cerraba el círculo: `ritmo_carrusel` solo admite **distancia, altura de cámara
y qué llena el cuadro**. La identidad decidía CÓMO se fotografía y nunca DÓNDE, así que el dónde lo
ponía el vocabulario compartido — el mismo para todas las marcas.

La corrección tiene dos mitades, y hacen falta las dos:

- **`escenarios`**, campo nuevo y opcional de la identidad: un repertorio de 2-4 mundos (lugar,
  superficies, materiales). El job elige UNO al crearse y lo congela en `params.escenario_visual`;
  `prompt_architect._clausula_mundo` lo emite como **`WORLD LOCK` prefijado a la sección 2**, byte a
  byte idéntico en la portada y en todos los slides. Prefijado y no dentro de las creativas para que
  la poda no lo toque — la misma decisión que el bloqueo de luz, por la misma razón.
- **Desmontar el vocabulario de mesa** en la capa compartida. "Anclado" no significa "sobre una
  mesa": un cable en el suelo de un taller, algo colgado de un gancho, apoyado contra un muro o medio
  hundido en agua es igual de plausible y tiene su sombra de contacto. Las reglas que evitan los
  defectos reales (manos, pseudo-texto en rótulos, objetos flotando) se conservan enteras; lo que se
  cambió es la palabra que colapsaba "sostenido" en "apoyado en una superficie".

**El bodegón de mesa sigue existiendo**, y eso es deliberado: es uno de los seis mundos del
repertorio compartido y uno de los cuatro de la casa. Lo que se quitó no es la mesa, es que fuera el
default invisible que nadie eligió. `visual_identity.revisar_diseno` avisa —reparo, no error— cuando
el repertorio ENTERO de una identidad son variantes de una mesa: ahí el campo está puesto y el
defecto sigue.

### 15.b — Por qué no contaba una historia

`continuidad_set` pedía *"mismo cuarto, objeto protagonista **DISTINTO**"* y el prompt del sistema
prohibía explícitamente repetir el sujeto (*"the same device seen closer… do NOT count as
different"*). Es una regla de **catálogo**, no de relato: garantiza que las piezas no se repitan y a
la vez impide que se relacionen. Los beats ya daban función narrativa al TEXTO y al plano, pero las
imágenes no tenían hilo entre sí.

Dato que lo deja claro: el camino de **video** del mismo archivo ya lo hacía bien —*"keep a recurring
anchor across all beats… chain the beats… name that carried-over element explicitly"*—. El de imagen
se escribió con la regla contraria.

Ahora hay un **arco** por job (`architect.json` → `arcos`), elegido por la app, congelado en
`params.arco_carrusel` y declarado en las **dos puntas** —cláusula determinista en la sección 3 y
línea `CAROUSEL ARC` en el briefing del redactor—, exactamente como los beats:

| arco | qué encadena | sujeto |
|---|---|---|
| `transformacion` | el mismo objeto vuelve con el **estado** cambiado | recurrente |
| `cadena` | cada slide abre en lo que dejó el anterior | encadenado |
| `recorrido` | un mismo lugar recorrido por partes | distinto |
| `escala` | piezas del mismo sistema: la pieza, el conjunto, la instalación | distinto |

**La frontera con el beat es dura y no se puede cruzar: el arco elige QUÉ hay delante de la cámara,
el beat elige CÓMO se fotografía.** Un `enlace` que hablara de distancia o encuadre chocaría con la
cláusula de plano del beat, que va pegada a él en la misma sección, y ante dos instrucciones de
cámara contradictorias el modelo elige una. Es la lección del paso 12 aplicada a la capa nueva; un
test lo blinda (`test_ningun_arco_habla_de_distancia_ni_de_encuadre`).

**No hay arco de progresión temporal a propósito.** "El mismo sitio a otra hora" exige cambiar la luz
entre piezas, y eso contradice el `LIGHT LOCK`, que es byte a byte idéntico. Lo que se quería de él
lo cubre `transformacion`, que cambia el estado y no la luz.

En **imagen única** no hay arco (solo hay una pieza); el `WORLD LOCK` sí aplica, y es lo que da
variedad entre posts.

### 15.c — El presupuesto, y lo que se pagó antes de subir el techo

`max_caracteres` sube de 4750 a **5050** (y `higgsfield_mcp._MAX_PROMPT_CHARS` de 4800 a 5100,
manteniendo los 50 de margen). Antes de subirlo se pagó todo lo que se podía pagar, que es la regla:

- `continuidad_set` **dejó de citar `{escena_portada}`**. Citarla era re-derivar el mundo compartido
  a partir de UNA pieza —una copia, no un ancla— y era su única parte variable, con su propio tope de
  palabras. Con el mundo declarado idéntico en todas las piezas, la cita solo gastaba presupuesto.
- La cláusula quedó reducida a su única afirmación propia: "misma localización, luz y paleta" pasó a
  estar dicho, idéntico y antes, por los bloqueos de mundo y de luz.
- El `enlace` del arco **reemplaza** la segunda mitad de esa cláusula, no se le suma.

Aun así el peor caso quedaba en el escalón de poda de **14 palabras**, por debajo de la barra de 18
que fijó la v6. 5050 es el primer techo que devuelve el 18 a los cuatro beats de la identidad de la
casa (a 5000 se quedaba fuera `desarrollo`; a 4900, también `prueba` y `remate`). Medido con
[`api/scripts/medir_prompt.py`](../api/scripts/medir_prompt.py).

**Ojo con el indicador.** `medir_prompt._poda` se medía sobre `sujeto` porque era la única creativa
sin decorar — y el bloqueo de mundo se prefija justo ahí. Pasó a medirse sobre `camara`; sin ese
cambio informaba 61 "palabras" de un tope de 26 y el número con el que se decide el presupuesto se
habría vuelto ruido sin que nada fallara.

### Lección

La del paso 14 decía que el proyecto mató la varianza *dentro* de un set con constantes, y que con
ella murió la varianza *entre* posts; y que el camino no era aflojar la capa dura sino
**parametrizarla por job**. Esto es exactamente eso, aplicado a los dos ejes que esa nota señalaba
como pendientes. El patrón completo, por si hay un tercero: **elegir una vez en `make_job`, congelar
en `params`, emitir byte-idéntico y fuera de la poda, y declararlo en las dos puntas** (el prompt de
imagen y el briefing del redactor). Lo que se declara en una sola punta lo contradice la otra.

Y una advertencia que ya costó dos veces: **el rubric corre después y reescribe**, así que hubo que
decirle en la misma tanda que el mundo, el arco y la recurrencia del sujeto son de la app y no son
suyos ni para puntuar ni para reescribir. Sin eso, la auto-crítica habría revertido el arco.

### Qué falta

- **Recorrido manual**: un carrusel individual y un lote, mirando las imágenes. Con **dos identidades
  distintas** —una con `escenarios` propios y otra sin el campo— para comprobar que las dos tandas se
  ven de mundos distintos, y **dos posts seguidos con la misma identidad** para ver que el arco y el
  mundo cambian.


### Paso 13 — calidad profesional del carrusel

Auditoría de un carrusel real de 5 piezas (agosto 2026): la estructura de la pieza cambiaba tres
veces dentro del mismo carrusel, había cinco localizaciones y cinco luces distintas, el mismo
chasis se repetía en 3 de 5 piezas, tres familias tipográficas y ninguna escalera de planos. El
plan de ejecución completo, con las nueve causas raíz verificadas en el código, vive en
[`plan-calidad-carrusel.md`](plan-calidad-carrusel.md). Acá se anota lo que hay que recordar
después, no el plan.

#### La lección transversal, que es lo que evita la próxima regresión

Las nueve causas raíz son distintas, pero seis de ellas son la misma frase con otro traje:

> **Una variación —o una restricción— declarada en la capa BLANDA pierde siempre contra lo
> declarado en la dura.**

La capa dura son las secciones que escribe la app con plantillas deterministas (1, 4, 5 y 9, más
las cláusulas que se pegan a la 3 y a la 6). La blanda es todo lo que solo el LLM puede decidir:
el `prompt_base`, que el arquitecto le enseña literalmente como *"BASE PROMPT (weak, to
rewrite)"*, y las cinco secciones creativas. Lo que vive en la blanda llega al prompt final si el
modelo tiene a bien repetirlo; lo que vive en la dura llega siempre. Por eso la escalera de
planos se perdía, por eso la luz cambiaba en cada pieza, y por eso las correcciones de este paso
mueven cosas de una capa a la otra en vez de pedirlas mejor.

Y su **corolario nuevo**, que es el que nadie había visto:

> **Una identidad puede escribir layout sin querer si sus campos entran verbatim en una sección
> determinista.**

`tipografia` = *"…set inside a headline **band**"* y `color_texto` = *"…over the **dark field**"*
no son descripciones de tipo: son instrucciones de layout, y la sección 5 las pegaba tal cual. El
modelo hizo lo que le pedían y pintó el rectángulo. La corrección no es prohibirle al usuario que
escriba así —lo escribió el extractor, no el usuario— sino **sanear en la frontera**: los colores
se reducen a su tinta (`prompt_architect.tinta`) y las familias pasan por `sin_layout`. Vale para
cualquier campo que se añada mañana.

#### Presupuesto de caracteres — línea base (05/08/2026)

Varias correcciones añaden texto **fijo** a todos los prompts, y ese presupuesto es finito. Se
mide con [`api/scripts/medir_prompt.py`](../api/scripts/medir_prompt.py) (diagnóstico por
terminal, sin red y sin modelo), sobre el peor caso: slide de info con beat, texto largo que se
parte en titular + kicker, y `escena_portada` larga.

| identidad | peor caso | largo | techo | margen |
|---|---|---|---|---|
| casa (`brand.json`) | `desarrollo` | 3469 | 3550 | **81** |
| esquema (campos al tope) | `desarrollo` | 3818 | 3550 | **−268 · rechazado** |

Dos cosas que la medición dejó claras y no eran obvias:

1. **El presupuesto ya estaba agotado antes de empezar.** El peor caso de la casa entra pero
   entra **podado**: `_ajustar_longitud` recorta las secciones creativas a 14 palabras, que es
   justo el anclaje concreto del sujeto. Cualquier fase que añada texto fijo tiene que pagar el
   techo, no el margen.
2. **Una identidad de usuario válida puede tirar el prompt entero.** Con todos los campos de texto
   en su tope de esquema (240 caracteres), la sección 5 sola pesa ~960 caracteres —`tipografia`,
   `tipografia_secundaria`, `color_texto` y `color_acento` se pegan verbatim— y la poda **no toca
   las secciones fijas**. El validador rechaza, y esa imagen se genera con el prompt base: **sin
   bloque de texto**. No es hipotético: `visual_identity.validar` acepta exactamente esa identidad.

#### Cómo quedó el presupuesto tras las fases

Cada fase que añade texto fijo se midió y pagó su techo. La barra que se fijó —y donde se para—
es el **escalón de poda de 18 palabras** por sección creativa: por debajo, la sección deja de
describir un objeto concreto y la imagen sale genérica; por encima, cada escalón cuesta ~250
caracteres de prompt sin que nadie haya demostrado que se noten.

| tras la fase | qué añadió | techo | peor caso | poda |
|---|---|---|---|---|
| 0 · línea base | — | 3550 | 3469 | 14/26 |
| 1 · continuidad del set | `SET CONTINUITY` en todos los slides (~200) | 4250 | 4215 | 22/26 |
| 2 · bloqueo de luz | `LIGHT LOCK` en todas las piezas (~180) | 4300 | 4288 | 18/26 |
| 3 · bandas | sangrado en positivo en la sección 1 (~110) | 4400 | 4398 | 18/26 |
| 5 · cortes de línea | cortes dictados en la sección 4 (~140) | 4650 | 4623 | 18/26 |
| 7 · atrezzo y cultura | contexto cultural + negativo de moneda | 4800 | 4744 | 18/26 |

`higgsfield_mcp._MAX_PROMPT_CHARS` va siempre 50 por encima, para que un prompt válido nunca
llegue a truncarse. **Si una fase futura vuelve a necesitar techo, el sitio donde mirar no es
este número sino las secciones fijas**: el brief fijo ya ronda los 3400 caracteres y la poda está
activa incluso en un caso realista, no solo en el peor.

#### Pendiente: el A/B de los cortes de línea (fase 5)

Los cortes de línea dictados van tras `IMAGE_LINE_BREAKS` (encendido) porque tocan la sección 4,
que es la que sostiene el QA de texto. **La validación A/B no se ha corrido**: necesita
generaciones reales contra Higgsfield (créditos y sesión OAuth). Cómo hacerla:

1. Genera un carrusel de 5 slides con `IMAGE_LINE_BREAKS=1`, y otro del **mismo contenido** con
   `IMAGE_LINE_BREAKS=0`.
2. Compara `job["images"]["qa"]` de los dos: cuántas imágenes pasaron el QA de texto en el primer
   intento, y cuántas necesitaron reintento.
3. **Si el flag empeora la precisión del texto, se apaga por defecto** (`image_line_breaks: bool =
   False` en `config.py`) y se anota aquí por qué. La exactitud del texto vale más que la
   elegancia del corte: un titular bien partido que dice otra cosa no sirve de nada.

Lo que sí está comprobado sin generar: `image_text_qa.coincide` normaliza mayúsculas y
puntuación, así que ni la caja alta ni el reparto en líneas pueden hacer fallar la comparación
por sí mismos. El riesgo que queda es de **render**, no de comparación.

#### Qué se corrigió, causa por causa

| # | Causa | Corrección | Dónde |
|---|---|---|---|
| C1 | `SET CONTINUITY` no se emitía en ningún slide: se comparaba el rol contra el literal `"contenido"` y los slides llegan con el nombre de su beat | Comparar contra `rol_base()` en las dos comparaciones (la cláusula y el briefing), con test parametrizado sobre `ROLES_BEAT` completo | `prompt_architect._clausula_set`, `_mensaje_arquitecto` |
| C2 | La luz la escribía el LLM una vez por imagen, sin conocer a sus hermanas; ninguna temperatura de color en todo el pipeline | `LIGHT LOCK` prefijado a la sección 6, byte a byte idéntico en todas las piezas, con temperatura fija y app-owned. La luz pasa a ser propiedad de la identidad (`luz_identidad`), separada del tratamiento fotográfico | `architect.json`, `prompt_architect._clausula_luz`, `job_runner._marca_post` |
| C3 | Bandas y marco: la identidad los fabricaba desde la capa dura, y el negativo por sí solo nunca bastó | Tres frentes: saneo de lo que entra en la sección 5 (`tinta`, `sin_layout`), sangrado en positivo en la sección 1, y **detector de bandas** sobre el píxel con un reintento | `prompt_architect`, `architect.json`, `image_overlay.bordes_planos`, `job_runner._verificar_bandas` |
| C4 | Un `ritmo_carrusel` con personas contradice `No people as the main subject`: el modelo descarta el plano entero y se pierde la escalera | `PALABRAS_PERSONA` como **error** de `validar` (es inoperante, no discutible), la misma lista al extractor y al lint | `visual_identity`, `identity_extract`, `prompt_lint` |
| C5 | Ninguna puerta comprobaba que la tipografía sirviera a escala de póster | `FAMILIAS_UI_PROHIBIDAS` como error; falta de `MARCAS_DISPLAY` y secundaria débil como reparos | `visual_identity` |
| C6 | Nada dictaba la caja ni los cortes: la sección 5 pedía caja alta y la 4 citaba la contraria; las líneas las repartía el modelo | Caja alta en la cita cuando la identidad la declara, y cortes dictados con partición equilibrada y regla de viuda (tras `IMAGE_LINE_BREAKS`) | `prompt_architect.pide_caja_alta`, `lineas_titular` |
| C7 | No existía QA de conjunto: cada imagen se validaba aislada | `image_set_qa`: una llamada de visión que ve las N piezas juntas, cuatro veredictos binarios con motivo, una sola ronda de regeneración | `image_set_qa`, `job_runner._verificar_conjunto` |
| C8 | El sujeto pedía desorden y cada objeto secundario era una superficie más donde escribir pseudo-texto | Máximo 2 secundarios y ninguno rotulable; HARD RULE de plausibilidad física | `architect.json` (`llm.instruccion`) |
| C9 | El idioma llegaba solo a la sección de texto: props por defecto estadounidenses | `CULTURAL CONTEXT` en el briefing + negativo de moneda cuando el contenido no es inglés | `prompt_architect._mensaje_arquitecto`, `_seccion_negativos` |

#### Verificación — qué está comprobado y qué no

Comprobado sin gastar créditos (batería completa en verde, 829 tests, y una construcción
determinista del brief de un carrusel de 5 con identidad de usuario):

- [x] Las 5 piezas declaran el **mismo** `LIGHT LOCK`, byte a byte.
- [x] Los 4 slides llevan `SET CONTINUITY` con la escena de la portada.
- [x] La sección 5 no arrastra el vocabulario de layout de la identidad.
- [x] Las 5 declaran el sangrado en la sección 1.
- [x] Escalera de planos real: `TENSION`, `DEVELOPMENT`, `EVIDENCE`, `PAYOFF`.
- [x] Ningún titular con línea huérfana; una sola familia y una sola caja en las 5.
- [x] Negativo de moneda estadounidense presente con contenido en español.
- [x] **Regresión sin identidad**: un job sin identidad produce el prompt *idéntico* al de la
      identidad de la casa (`test_sin_identidad_el_prompt_es_el_mismo_que_con_la_identidad_de_la_casa`).

Pendiente, porque necesita generaciones reales (créditos y sesión OAuth):

- [ ] Recorrido manual del flujo **individual** (carrusel de 5, las dos compuertas) y del
      **bulk** (`.xlsx` de 3 filas con una `fecha_hora`), con `dry_run`.
- [ ] Lo que solo se ve en el píxel: cero pseudo-texto legible, props concordes con el idioma,
      y que los avisos nuevos (bandas, conjunto, lint) se vean en el editor del lote.
- [ ] El **A/B de los cortes de línea** descrito arriba.

#### Los tres controles automáticos, y por qué son tres

Se acumulan a propósito, porque miran cosas distintas y ninguno ve lo del otro:

1. **`prompt_lint`** — antes de generar, gratis. Mira lo que el LLM escribió y lo que la app va a
   hacer con ello. Es el único que puede evitar el gasto.
2. **`image_text_qa` + `bordes_planos`** — por imagen, después de generar. Ortografía, recorte y
   passe-partout. El de bandas es Pillow (gratis); el de texto es una llamada de visión.
3. **`image_set_qa`** — por carrusel, con todas las piezas juntas. Es el único capaz de ver que
   cinco imágenes no se parecen entre sí, y por tanto **el único que evita que todo esto vuelva a
   degradarse sin que nadie se entere**.

### Paso 12 — cada slide con su función: la escalera de beats

Síntoma reportado (05/08/2026): en imagen única la identidad visual funciona bien, pero **en el
carrusel todos los slides se ven iguales**. Está bien que compartan estética; lo que falta es que
cada uno tenga una función y que la imagen acompañe al texto que lleva impreso.

**Diagnóstico.** La variación entre slides existía en el código, pero declarada en la única capa
que el modelo puede pisar:

1. `prompt_architect` solo conocía dos roles, `portada` y `contenido`. Los 2-5 slides de info
   compartían la misma entrada de `piezas`, `zonas_texto` y `tipografia.escala`: el brief de nueve
   secciones del slide 1 y el del slide 4 eran **idénticos salvo la sección 2**.
2. La cláusula de lockup (`composicion_zona`) es una sola para todos: sujeto en la banda central,
   tipo arriba y abajo. Cambiara o no el objeto, **el cuadro era el mismo cuadro**.
3. `continuidad_set` pide *"same room, surfaces, light and palette"* — correcto para la cohesión,
   pero sumado a 1 y 2 lo único que variaba era el objeto.
4. La única variación estructural, la escalera `_SLIDE_FRAMINGS` de `job_runner`, entraba en el
   **`prompt_base`**… que el arquitecto solo le enseña al modelo como *"BASE PROMPT (weak, to
   rewrite)"*. Con el LLM disponible —el camino normal— el encuadre llegaba al prompt final solo si
   el modelo lo repetía por su cuenta, mientras se le pedía a la vez *"do not open with a camera
   instruction"* y se le pegaba encima el lockup. **La variación vivía en la capa blanda y la
   uniformidad en la dura.**
5. Nada relacionaba el encuadre con lo que el slide **dice**. `_SLIDE_FRAMINGS[i % 4]` era
   posicional puro; el texto sí tenía secuencia narrativa (`post_writer`: la portada promete, i+1
   avanza, el último remata) pero esa función no viajaba a la imagen. Los dos lados escribían una
   secuencia y ninguno conocía la del otro.

**Qué se hizo.** Tres capas:

- **La escalera de beats** (`architect.json` → `roles` y `secuencia_roles`,
  `prompt_architect.roles_carrusel`). Cada posición tiene una función —`tension` → `desarrollo` →
  `prueba` → `remate`— y de ella dependen las tres cosas que escribe la app y el modelo no puede
  pisar: la **escala del titular** (11-13% / 9-11% / 12-15%), la **presencia del acento** (la
  tensión lo calla: un acento que sale en todos los slides deja de ser un acento) y el **plano**,
  que pasa a ser cláusula determinista de la sección 3, junto al lockup y a la continuidad de set.
  Lo que **no** cambia entre beats es el esqueleto —titular en la banda alta, apoyo al pie, sujeto
  en la central—: unificarlo fue la corrección del paso 6 y es lo que hace que el set se lea como
  un sistema.
- **El ritmo, en la identidad visual** (`ritmo_carrusel`). El beat es estructura y es igual para
  todas las marcas; **cómo se fotografía** ese beat es identidad: la misma escalera recorrida con
  macros de textura o con naturalezas muertas abiertas da dos carruseles que no se parecen. Es una
  lista ordenada por beat y opcional — vacío = el respaldo de `architect.json`, beat a beat.
- **El beat le llega al redactor** (`post_writer._linea_beats` → `SLIDE BEATS` en el user message,
  y `needs.beats` a las dos compuertas previas). El texto impreso del slide *i* y su escena se
  encargan desde el mismo sitio, y quien reescribe a mano en la compuerta ve qué función cumple
  cada slide.

Costo de presupuesto: la cláusula de plano se paga con `composicion_zona_slide` (la versión corta
del lockup, que ya no repite en genérico de qué está hecho el aire porque cada beat lo nombra en
concreto) y con subir el techo a 3550 caracteres —y `higgsfield_mcp._MAX_PROMPT_CHARS` a 3600—,
que es exactamente lo que la nota de la v3 de `architect.json` dejaba anticipado: con 3150 la poda
recortaba el anclaje concreto del sujeto en **todos** los slides.

Esto cierra **F6** (no había plan de encuadres) y completa **F2** por el lado estructural.

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

### F6 · No hay plan de encuadres: la jerarquía visual queda al azar — **resuelto (ago 2026, paso 12)**

> Lo que sigue describe el estado anterior. Hoy el encuadre lo fija el **beat** del slide
> (`prompt_architect.roles_carrusel`) y se declara en la sección 3 del brief, que es determinista.
> El intento intermedio —la escalera `_SLIDE_FRAMINGS`— fallaba por vivir en el `prompt_base`, que
> el modelo puede ignorar; ver el paso 12.

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

## Paso 14 · El slide lo lidera el texto; la tipografía distingue identidades (ago 2026)

Dos observaciones sobre posts reales, con la misma raíz: **todo lo que el proyecto quiso volver
invariante se declaró como una constante de archivo, así que dejó de variar también entre posts.**

### 14.a — En el slide de contenido manda el texto, no el objeto

El síntoma: cada pieza era un objeto centrado sobre una superficie, y el titular lo acompañaba.
En la portada eso está bien —una portada engancha con una imagen—, pero un slide de contenido no
vende una idea con una foto: la **escribe**. La jerarquía estaba invertida y estaba escrita así en
cuatro sitios, todos deterministas:

| Dónde | Qué decía |
|---|---|
| `piezas.contenido` (sección 1) | `one type tier smaller` que la portada |
| `tipografia.escala` (sección 5) | portada 13-16%, `desarrollo`/`prueba` **9-11%** — el slide tenía el titular más pequeño del set |
| `zonas_texto.contenido` | banda alta hasta el 38%, contra el 42% de la portada |
| `composicion_zona_slide` + `roles[*].composicion` (sección 3) | el sujeto anclado o llenando la banda central |

Qué se hizo:

| # | Cambio | Dónde |
|---|---|---|
| T-1 | La pieza del slide se declara `TYPE-LED, not image-led` | `prompts/architect.json` (`piezas.contenido`) |
| T-2 | La escala del slide pasa **por encima** de la portada (15-20% contra 13-16%) y la banda alta al 68% | `prompts/architect.json` (`tipografia.escala`, `zonas_texto`) |
| T-3 | El lockup del slide deja de ser el de la portada acortado y pasa a ser su **inversa**: el titular se lleva los dos tercios altos y el sujeto queda `subordinate`, bajo | `prompts/architect.json` (`composicion_zona_slide`), `prompt_architect._clausula_aire` |
| T-4 | Los cuatro planos de beat mueven el sujeto a la parte baja **conservando su escalera de distancias** (macro → media → cenital → wide) | `prompts/architect.json` (`roles[*].composicion`), `prompt_architect._COMPOSICION_BEAT_FALLBACK` |
| T-5 | El rubric deja de premiar `the subject is anchored in the central band` y declara que la posición del sujeto no es suya | `prompts/rubric.json` |
| T-6 | La plantilla de respaldo (Pillow) sube el cuerpo del slide por encima del de la portada | `image_overlay._CUERPO`, `_BANDA_ALTA` |
| T-7 | El `prompt_base` y el briefing del redactor dejan de pedir un objeto protagonista en los slides | `job_runner._IMAGE_SPACE_SLIDE`, `post_writer` (`space_slides`), `prompt_architect._linea_beat` |
| T-8 | El respaldo creativo de la sección 3 deja de decir dónde se apoya el sujeto | `prompts/architect.json` (`respaldos.composicion`) |

**T-5 y T-8 son las dos que se olvidan.** T-5 es la lección de las bandas planas repetida: el rubric
corre *después* y reescribe, así que **lo que el rubric premia gana a lo que el brief pide** — con el
criterio viejo, la auto-crítica habría revertido la inversión sola. T-8 es más sutil: el respaldo
creativo abría la sección 3 con «the subject centred in the middle band» dos frases antes de que la
cláusula determinista lo subordinara, así que el brief se contradecía a sí mismo dentro de la misma
sección.

Lo que **no** cambió, a propósito: el esqueleto (titular arriba, apoyo al pie, sangrado a los cuatro
bordes) es el mismo en portada y slide. La inversión se declara como jerarquía de **tamaño**, no
moviendo las bandas de sitio — son las bandas compartidas lo que hace que el set se lea como un
sistema. Y el sujeto sigue siendo un objeto real y fotografiado: subordinarlo sin conservar la
escalera de planos dejaría a los cuatro slides sin nada que los distinga salvo el texto.

Coste medido con [`scripts/medir_prompt.py`](../api/scripts/medir_prompt.py): el primer intento
bajó los slides al escalón de poda de **14** palabras (el baseline era 18, que es la barra fijada en
la v6). No se subió el techo — se quitó la redundancia: la inversión se declaraba entera en las
secciones 1, 3 y 5, y basta con que la 1 la **anuncie** (`TYPE-LED`), la 3 diga cómo y la 5 cuánto.
Con eso los cinco roles vuelven a 18/26 con la identidad de la casa.

### 14.b — Todas las identidades salían con la misma tipografía

El síntoma: las fuentes se repetían entre posts aunque cambiara la identidad visual. La causa **no
estaba en el generador** —que respeta la identidad desde la v2— sino en las dos puertas de entrada:

- El encuadre del extractor le describía la pieza al modelo como «headline in the upper band at
  **9-16% of the frame height, all caps**». La caja y la escala estaban hardcodeadas ahí, así que
  toda identidad extraída nacía en caja alta y la diferencia entre marcas se reducía al adjetivo.
- `MARCAS_DISPLAY` solo contenía el vocabulario de la grotesca condensada, así que una didone, una
  egipcia o una lettering de plantilla legítimas salían con reparo — y el reparo empuja al usuario
  **y al modelo** (recibe esa misma lista) a reescribirlas hacia la única clase que la lista nombraba.
  La distinción entre marcas se perdía en el **validador**, no en el generador.
- El criterio pedía «elige la clase que el registro fotográfico pide» sin ofrecer opciones, que es
  el mismo defecto que tenía el copy antes de las ocho estructuras: sin abanico nombrado, un modelo
  devuelve siempre la respuesta segura.

Qué se hizo:

| # | Cambio | Dónde |
|---|---|---|
| F-1 | Fuera la caja y la escala del encuadre; se pide explícitamente **no** caer en caps por defecto. La escala es layout y su fuente única es `architect.json` | `prompts/identity_extract.json`, `identity_extract._CRITERIO_POR_DEFECTO`, `_reglas_esquema` |
| F-2 | Abanico **nombrado** de ocho clases de titular (condensada, extendida, didone, slab, geométrica, serif de texto, stencil, monoespaciada) con su caja | `prompts/identity_extract.json` (`criterio.tipografia`) |
| F-3 | `MARCAS_DISPLAY` cubre varias familias de clase, no una | `visual_identity.py` |
| F-4 | La caja deja de ser una constante del renderizador de respaldo: la decide la identidad, con la **misma** función que cita el texto en el prompt (`pide_caja_alta`) | `job_runner._lockup_plantilla`, `image_overlay._dibujar_texto` |

**F-4 es el acoplamiento que se descubre al soltar la caja.** Mientras todas las identidades eran
caja alta, que `image_overlay` llamara a `.upper()` sin preguntar daba igual. En cuanto una identidad
puede declararse en caja mixta, esa llamada hace que la pieza de respaldo contradiga a la generada —
justo lo que ese módulo existe para evitar. Regla general: **al convertir una constante en una
decisión, hay que buscar todos los sitios que la daban por supuesta**, y suelen estar en el camino
degradado, que es el que nadie mira.

### Lección

El proyecto mató la varianza *dentro* de un set a propósito —era el defecto— pero la mató con
constantes, así que murió también la varianza *entre* posts. No son lo mismo, y hoy el sistema no
tiene ningún concepto de "esto es fijo dentro del job y distinto entre jobs" salvo la identidad
visual, que sí se congela en `params`. Si en el futuro se quiere más variación entre posts sin
reabrir los defectos de coherencia, el camino no es aflojar la capa dura sino **parametrizarla por
job** —elegir una vez, congelar en `params` como la identidad, aplicar byte-idéntico a las N piezas—.
Los dos ejes más rentables que quedan sin tocar son el **registro fotográfico** (hoy `luz_temperatura`
es una constante app-owned de 5400K y `tono_visual` una sola receta) y los **arquetipos de
composición** (hoy `zonas_texto` es un único lockup con dos jerarquías).

### Qué falta

- **Recorrido manual**, que es la única verificación que vale acá: un carrusel individual y un lote
  de varias filas, mirando las imágenes. Ni los tests ni `medir_prompt.py` ven si el titular al 18%
  del alto se lee bien sobre la foto o si la aprieta contra el sujeto.
- **Extraer dos identidades de moodboards distintos** y comprobar que las tipografías salen de clases
  distintas — es el único modo de saber si F-1/F-2 funcionaron.
