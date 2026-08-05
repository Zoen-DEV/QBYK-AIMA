# Identidades visuales

Hasta esta feature, el look de todas las imágenes salía de **un** archivo
([`api/prompts/brand.json`](../api/prompts/brand.json)). Funcionaba, pero todos los posts
salían visualmente iguales. Ahora la identidad visual es un dato **con nombre y dueño**:
cada usuario puede tener varias y elegir con cuál genera.

El esquema no cambió. Una identidad guardada tiene exactamente los mismos campos y los
mismos tipos que `brand.json` — lo que se persiste es otro valor del mismo contrato, no
un formato nuevo.

## El esquema y sus tres contratos escondidos

[`api/visual_identity.py`](../api/visual_identity.py) es el esquema hecho validador. Los
campos son los de `brand.json`; quedan fuera `_comment*` y `version`, que describen
el **archivo** y no la identidad (una fila tiene sus propios `created_at`/`updated_at`).

| campo | tipo | qué gobierna |
|---|---|---|
| `paleta` | `list[str]` hex | secciones 6 y 9 del brief (`palette held to …`) y el respaldo de plantilla |
| `paleta_nombres` | `list[str]` | los nombres legibles que lee el modelo de imagen |
| `color_texto` | `str` | color del titular (sección 5) y del texto dibujado sobre plantilla |
| `color_acento` | `str` | el fragmento en color de la sección 5 |
| `tipografia` | `str` | la familia display de la sección 5 |
| `tipografia_secundaria` | `str` | la del kicker |
| `tono_visual` | `str` | tratamiento fotográfico — **lo pisa `image_style` por post** |
| `aspect_ratio` | `str` | inerte: el aspecto real lo fija el formato del post |
| `referencias` | `list[str]` | la dirección de arte de las secciones 6 y 7 |
| `ritmo_carrusel` | `list[str]` | el plano de cada beat del carrusel (sección 3) — **opcional** |

Tres acuerdos vivían implícitos en el código y romperlos **no dejaba rastro**. El validador
los hace explícitos:

1. **`paleta` está ordenada: `[fondo, texto, acento]`.**
   `job_runner._lockup_plantilla` usa `paleta[1]` y `paleta[2]` como respaldo de
   `color_texto`/`color_acento`. Una paleta en otro orden pinta la plantilla con los
   colores cambiados y no hay un solo error en el log.
2. **`color_texto` y `color_acento` llevan su hex, y es el de la paleta.**
   `image_overlay._color` busca un `#RRGGBB` dentro de la frase y, si no lo encuentra, cae
   a un hueso por defecto: una identidad que diga `"warm off-white"` a secas se dibuja con
   el color de **otra** marca.
3. **`ritmo_carrusel` está ordenado por los beats del carrusel** (`prompt_architect.ROLES_BEAT`:
   `tension`, `desarrollo`, `prueba`, `remate`). La posición **es** el beat, así que una lista
   en otro orden no da error en ningún sitio: le da a cada slide el plano de otro. Por eso el
   editor pinta un campo por beat en vez de un textarea de una línea por plano, y por eso los
   huecos interiores se conservan al normalizar — vaciar el segundo tiene que caer al respaldo
   de **ese** beat, no correr el tercero a su sitio.

Los topes de longitud son presupuesto, no estética: todo esto se inyecta en el brief de
nueve secciones, que tiene un límite duro de caracteres (`architect.json` →
`validacion.max_caracteres`).

## Qué cambia (y qué no) al cambiar de identidad

Cambian **paleta, color de acento, familia tipográfica, referencias de dirección de arte** y,
en carrusel, **el plano de cada beat** (`ritmo_carrusel`).

Ese último merece su matiz, porque es donde termina la frontera. Qué **cuenta** cada slide del
carrusel —tensión, desarrollo, prueba, remate— es estructura y es igual para todas las marcas:
vive en `architect.json` y se recorre con `prompt_architect.roles_carrusel`. Lo que la identidad
decide es **cómo se fotografía** cada uno de esos momentos: distancia, altura de cámara y qué
llena el cuadro. La misma escalera recorrida con macros de textura o con naturalezas muertas
abiertas da dos carruseles que no se parecen en nada, y esa diferencia sí es de marca. Como el
resto, es opcional: lo que no se defina cae al respaldo de la casa **beat a beat**.

**No** cambia el tratamiento fotográfico: el `image_style` que el LLM escribe para cada post
sigue ganando sobre `tono_visual`, igual que antes de esta feature. La identidad fija lo que
hace reconocible a la marca entre posts; la escena y su luz las sigue eligiendo el contenido.
Es una decisión, no un pendiente: `_marca_post` lo documenta y un test lo blinda.

## La identidad de la casa (`system`)

**No es una fila de la base.** Se sirve desde `brand.json` con el id fijo `system`.

- No hay dos fuentes para lo mismo: editar `brand.json` sigue cambiando el look de la casa,
  como está documentado en `CLAUDE.md`.
- Existe aunque no haya Mongo, que es justo el fallback que necesita la generación.
- Por eso no se puede editar ni eliminar (no hay nada que borrar) pero sí **clonar**: clonar
  crea una fila normal con su JSON de partida.

La identidad **activa** se modela con `is_default` en las filas del usuario. Que ninguna esté
marcada significa que la activa es la system, así que activar la system es limpiar las
marcas, y borrar la activa devuelve al usuario a la de la casa sin ningún paso extra.

## Usuarios

No hay autenticación. Hay tres usuarios fijos en [`api/users.py`](../api/users.py) y un
selector en la barra de navegación que manda el id en la cabecera `X-User-Id`.

**Esto no autentica nada**: cualquiera puede decir que es cualquiera. Es lo correcto para lo
que es —un selector de perfil en una app local— y la deuda está acotada a un solo punto:
`users.current_user_id` es la única función de todo el proyecto que decide quién pide. La
base guarda `user_id` y los endpoints lo exigen exactamente igual que lo harían con auth
real, así que migrar es reescribir el cuerpo de esa función; el esquema, los endpoints y la
UI no se tocan.

## Extracción desde fotos

[`api/identity_extract.py`](../api/identity_extract.py): de 5 a 10 fotos → un JSON que valida
contra el esquema.

- **Las fotos se revisan antes de llamar al modelo** (cantidad, formato por bytes mágicos,
  peso). Subir 4 u 11 cuesta cero y lo dice claro.
- Se reducen a 1024 px y se reencodan a JPEG **en memoria**. Anthropic escala por su cuenta
  lo que pase de ~1568 px, así que mandar más resolución solo gasta ancho de banda.
- **Las fotos no se guardan en ningún sitio.** Se leen, se mandan y se descartan al terminar
  el request. Reintentar tras un fallo no obliga a volver a elegirlas porque el navegador las
  conserva en memoria.
- Un JSON que no valida se reintenta **una** vez con los errores del validador como feedback.
  Si el segundo tampoco valida, se falla limpio: nada a medias entra a la base.
- **Las reglas del esquema las escribe la app** (`_reglas_esquema`) desde las constantes de
  `visual_identity`, no el archivo de prompt. Son el mismo contrato que aplica el validador y,
  escritas dos veces, se desincronizan en cuanto alguien mueva un límite. El JSON de prompt
  ([`api/prompts/identity_extract.json`](../api/prompts/identity_extract.json)) aporta el
  encuadre creativo — mismo reparto que en `prompt_architect`.

### Se extrae con ojo de diseñador, no de notario

Describir con fidelidad un set de fotos de teléfono produce una identidad de fotos de teléfono,
y de ahí salen piezas de aficionado por muy afinado que esté el prompt de generación. El
encuadre (v3 de `identity_extract.json`) le pide al modelo lo que hace un director de arte con
el moodboard del cliente: **leer la intención del set y especificarla a calidad de producción**.

- **Fiel** en lo que es identidad: familia de color, dirección y dureza de la luz, materiales,
  registro. La identidad es la que el set ya tiene, no una que quede mejor.
- **Profesional** en lo que es ejecución: los accidentes de la foto de referencia —flash
  directo, balance de blancos mezclado, medios tonos embarrados, fondo con ruido, encuadre de
  instantánea— no se heredan. Se nombra la identidad que las fotos están buscando, a la calidad
  que tendría si la hubiera rodado el estudio.
- Cada valor es una **instrucción de rodaje**, no una apreciación: se inyecta literal en el
  prompt de imagen, así que «cálido y acogedor» no instruye nada y «sol bajo rasante desde
  cámara izquierda, sombras largas sobre roble sin tratar» sí. El encuadre además le nombra al
  modelo **la pieza que sus valores van a producir** (póster a sangre con capa tipográfica,
  titular al 9-16% del alto), porque un campo que no sabe para qué se usa se escribe suelto.
- El `criterio` del JSON dice cómo se lee **cada campo**: `paleta` son tres ROLES (el campo
  donde se apoyan los sujetos, el neutro en que se lee el titular, el acento saturado que se
  gana una sola palabra) y no los tres colores más frecuentes; `tipografia` es la clase de
  familia que aguanta un titular al 9-16% del alto en caja alta —nunca una sans neutra de UI,
  que es la que devuelve el look de «caption pegado sobre una foto»—; `tono_visual` es una
  receta de luz repetible que **deja zonas tranquilas** donde apoyar el tipo; `referencias` son
  linajes de dirección de arte que se pueden nombrar en un deck, no estados de ánimo.

### El criterio de diseño que sí se comprueba

`visual_identity.revisar_diseno` mira los hex y no el texto, porque hay tres defectos que
valen una pieza entera y **el esquema no los ve**: el titular que no contrasta con su fondo
(mínimo 4.5:1 WCAG — el tipo va sobre una fotografía con grano y falloff, que se come parte del
contraste), el «acento» que es un tercer gris (mínimo 25% de saturación HSV) y el acento
confundible con el texto o con el fondo. Este último se mide por **croma y no por contraste**:
hueso `#EDEAE0` y lima ácido `#C9F227` contrastan 1.03:1 y nadie los confunde, así que medirlo
con el ratio marcaría como defectuosa la identidad de la casa.

Dos cosas que no son casualidad:

- **No son errores de esquema.** `validar` no los mira: un reparo discutible no puede volver una
  identidad imposible de guardar. Gastan el reintento —con el reparo como feedback, igual que
  los errores— y, si sobreviven, salen como **aviso junto al editor** en vez de tumbar la
  extracción. Cambiar un hex ahí son dos segundos; otra llamada al modelo, no.
- **Los números los escribe la app en las dos puntas.** `_reglas_diseno` genera lo que se le
  pide al modelo desde las mismas constantes que aplica el código: pedir 3:1 y comprobar 4.5:1
  sería pedirle al modelo que falle. Un test fija que la identidad de la casa pasa su propio
  criterio — si dejara de pasarlo, lo mal calibrado sería la regla, no `brand.json`.

### Qué proveedor lee las fotos

Sirve **cualquiera de los dos**, `ANTHROPIC_API_KEY` o `PERPLEXITY_API_KEY` (Anthropic
primero, como en el resto del proyecto). La Sonar API acepta bloques `image_url` con data
URI; el comentario de `llm_json` que decía lo contrario venía de cuando no era así.

Cada proveedor recibe el layout de bloques que documenta el suyo, no un formato común
inventado: Anthropic numera cada imagen (`Image 1:`) y deja el texto al final —sin
etiquetas tiende a describir solo la última cuando la pregunta es sobre el conjunto—, y
Perplexity recibe el texto primero y las imágenes detrás, que es el único shape que su guía
documenta. Los dos cuerpos están fijados en `tests/test_llm_vision.py`, porque no hay forma
de probarlos contra los endpoints reales sin gastar.

En Perplexity las imágenes se tarifan como `(ancho × alto) / 750` tokens de entrada. Con la
reducción a 1024 px son ~1.000 tokens por foto, así que una extracción de 6 fotos ronda los
6.000 tokens de entrada. El tracking lo recoge solo: llegan como `input_tokens` en el `usage`
que devuelve la API.

**El QA de texto de las imágenes generadas (`image_text_qa`) sigue exigiendo Anthropic a
propósito.** Perplexity podría hacerlo, pero encenderlo añadiría una llamada de visión por
imagen generada, en todos los posts, a quien hoy lo tiene apagado sin saberlo. Es una
decisión aparte; para tomarla, `image_text_qa.disponible` solo tiene que volver a delegar en
`llm_json.vision_disponible`.

## Integración con la generación

El cambio es deliberadamente pequeño. `prompt_architect` **no se tocó**: `normalizar_spec` ya
resolvía cada campo con `marca.get(x) or marca_def.get(x)`, así que bastó con que
`_marca_post` le entregue la identidad en vez de solo el aspecto.

- **Se elige al crear el post**, con el selector `IdentityPicker` (`identidad_visual_id` en el
  form). `identity_store.elegida` lo resuelve en los dos flujos: **vacío = la activa del
  perfil**, que es como se generaba antes de que el campo existiera. Y hereda la regla de
  `activa`: **nunca lanza**. Un id que ya no existe —la identidad se borró entre que se pintó
  el formulario y se envió— o una base caída caen a la activa en vez de tumbar la creación,
  igual que un `modelo_imagen` desconocido cae al default.
- En el **lote** el selector está en la UI de `/bulk`, junto a las cuentas y el dry-run, y no
  como columna del sheet: un lote es un envío de un usuario en un momento y sus filas
  comparten estética por diseño (ver `batch_runner.run_batch`).
- La identidad se **congela** en `params` al crear el job (`_params_identidad`, compartido por
  los dos flujos). Cambiar la identidad activa —o el selector— a mitad de una generación no
  puede alterar un job en vuelo, y el lote la resuelve UNA vez al subir el sheet, no por fila.
- `_identidad(job)` la lee. **Vacío significa "lo de siempre"**, no "identidad en blanco": los
  campos vacíos no se pasan, así que caen a `brand.json` campo a campo. Un job sin identidad
  produce una `marca` idéntica —no parecida— a la de antes de la feature, y hay un test que
  compara el diccionario entero.
- Las `referencias` viajan al **nivel de arriba** de la spec, no dentro de `marca`: ahí es
  donde las lee `normalizar_spec`. Metidas en `marca` se perderían sin un solo error. El
  `ritmo_carrusel` viaja igual y por el mismo motivo, más uno propio: es una lista y
  `_texto_plano` la aplanaría a una frase con comas dentro de `marca`.
- El ritmo entra dos veces, a propósito: en la cláusula determinista de la sección 3
  (`_clausula_beat`) y en el `prompt_base` que el arquitecto le enseña al LLM
  (`encuadre_beat`, que usa la **misma** cadena de respaldo: identidad → `brand.json` → el beat
  de `architect.json`). Si las dos no coincidieran, el brief se contradiría a sí mismo — que es
  exactamente el defecto que tenía la escalera de encuadres anterior.

## Migración

No había migraciones en el proyecto. [`api/migrations/`](../api/migrations/) trae el mínimo
para que un cambio de esquema sea reversible, sin frameworks ni dependencias nuevas:

```bash
cd api && python -m migrations.run status   # qué hay aplicado
cd api && python -m migrations.run up       # aplica lo pendiente
cd api && python -m migrations.run down     # revierte la última
```

La 001 crea `visual_identities` con sus índices. **No siembra la identidad system** (se sirve
desde `brand.json`). `down` tira la colección: eso borra las identidades que los usuarios
hayan creado —es el punto de una reversión— pero no toca el look de la casa, así que revertir
devuelve la app exactamente al estado anterior a la feature.

---

# Checklist de pruebas manuales

Arranca los dos servidores como siempre (`python -m uvicorn app:app --reload` desde `api/`,
`npm run dev` desde `frontend/`). Antes de empezar, con `MONGODB_URI` configurado:

```bash
cd api && python -m migrations.run up
```

### 0. Nada cambió (regresión) — con la identidad de la casa

1. Ve a `/cuenta`. Debe aparecer **una sola** identidad, «QBYK — identidad de la casa», con la
   insignia **De la casa** y la insignia **Activa**, sin botón de eliminar ni de editar.
2. Genera un post individual (`/individual`) de imagen única como lo harías normalmente.
3. **Esperado:** el post sale exactamente como antes de la feature. En la compuerta de preview,
   el prompt de la portada debe contener `#0B0C0E, #EDEAE0, #C9F227` — la paleta de `brand.json`.

### 1. Crear una identidad desde 6 fotos

1. `/cuenta` → **Agregar identidad visual**.
2. Elige **6** fotos que compartan estética (mismo tipo de luz y materiales; de un set disperso
   sale una identidad dispersa). Deja el nombre vacío.
3. Pulsa **Extraer identidad**.
4. **Esperado:** unos segundos de «Leyendo tus fotos…» y luego el editor con la paleta extraída,
   los nombres de color, la tipografía y las referencias. El nombre se rellena solo con algo del
   tipo «Ember · ink».
   Mira los valores con ojo de diseñador, que es lo que se le pidió al modelo: `tono_visual`
   tiene que ser una receta de luz (dirección, dureza, hora, caída, grano) y no un adjetivo;
   `tipografia` una clase de familia que aguante un titular enorme en caja alta; `referencias`
   linajes nombrables. Si sale un banner ámbar con un **reparo de diseño** —titular que no
   contrasta con su fondo, acento que es otro gris— es correcto que la extracción haya llegado
   igual: corrígelo ahí mismo antes de guardar.
5. Repite con **6 fotos de teléfono mal iluminadas** (flash directo, fondo con ruido).
   **Esperado:** la identidad conserva la familia de color y los materiales del set pero NO
   describe el accidente: nada de «flash directo», «fondo desordenado» ni «foto de móvil».
6. Cambia el nombre a «Prueba» y pulsa **Guardar identidad**.
7. **Esperado:** el modal se cierra, aparece la identidad nueva en la lista con sus muestras de
   color, insignia ninguna (no está activa todavía) y botones Renombrar / Editar / Eliminar.

### 2. Rechazo de 4 y de 11 fotos (sin llamar al modelo)

1. Abre el modal y elige **4** fotos.
2. **Esperado:** aviso ámbar «Sube entre 5 y 10 fotos (elegiste 4).» y el botón **Extraer
   identidad** deshabilitado. No debe haber ninguna petición a `/api/identities/extract` en la
   pestaña Red del navegador.
3. Repite con **11** fotos: mismo aviso, mismo bloqueo.
4. Para comprobar que el backend también lo rechaza (y no solo la UI), desde `api/`:
   ```bash
   curl -X POST http://127.0.0.1:8000/identities/extract -F "photos=@una.jpg"
   ```
   **Esperado:** HTTP 400 y `"Sube entre 5 y 10 fotos de referencia (subiste 1)."` — y ni un
   token gastado (el log del servidor no muestra ninguna llamada a Anthropic).

### 3. Renombrar y eliminar

1. En «Prueba», pulsa **Renombrar**, escribe «Prueba 2» y pulsa Enter.
   **Esperado:** el nombre cambia y aparece el aviso verde «Nombre actualizado.»
2. Pulsa **Eliminar** y confirma.
   **Esperado:** desaparece de la lista.
3. En la identidad **de la casa**, confirma que **no existe** botón Eliminar ni Editar, solo
   **Clonar**.
4. Para comprobar que tampoco se puede por API:
   ```bash
   curl -X DELETE http://127.0.0.1:8000/identities/system
   ```
   **Esperado:** HTTP 403 con «La identidad de la casa no se puede modificar ni eliminar;
   clónala para partir de ella.»

### 4. Cambio de estética al cambiar de identidad activa

Este es el que hay que mirar con calma, porque el cambio es real pero acotado.

1. Crea (o clona y edita) una identidad con una paleta **muy distinta** a la de la casa — por
   ejemplo fondo `#FFFFFF`, texto `#222222`, acento `#0055FF`. Al tocar los colores en el
   selector, fíjate en que «Color del texto» y «Color de acento» se actualizan solos.
2. Pulsa **Usar esta**.
   **Esperado:** la insignia **Activa** se mueve a esa identidad y la de la casa la pierde.
3. Genera un post individual nuevo. En la compuerta de **preview**, mira el prompt de la portada.
   **Esperado:** aparecen `#FFFFFF`, `#222222` y `#0055FF`, y ya **no** aparece `#C9F227`.
   También debe aparecer la tipografía que definiste.
4. Deja que genere la imagen.
   **Esperado:** el titular sale en el color de texto de la identidad nueva y la palabra
   acentuada en su acento; el aire y la paleta general de la pieza siguen esa carta.
   **No esperado:** que cambie el *tipo* de foto (interior/exterior, primer plano/plano general).
   Eso lo sigue decidiendo el `image_style` que el LLM escribe para cada post — es la decisión
   que tomamos y está documentada arriba.

### 4b. El carrusel cuenta una historia (ritmo por beat)

Es el defecto que motivó el campo: los slides mantenían la estética pero eran versiones de la
misma imagen.

1. Genera un post individual en **carrusel** con 5 slides. En la compuerta de **preview**, mira
   los campos de «Texto de cada slide».
   **Esperado:** cada uno lleva su etiqueta de función — `tension`, `desarrollo`, `prueba`,
   `remate` — con la explicación debajo.
2. En esa misma pantalla, mira los prompts de las imágenes (`GET /jobs/:id` → `images.prompts`
   tras generar, o el log del servidor).
   **Esperado:** en la sección 3 de cada slide aparece una línea `SHOT — …` **distinta** por
   slide, y en la sección 5 la escala del titular cambia (11-13% en la tensión, 9-11% en el
   medio, 12-15% en el remate). En el slide de tensión **no** aparece la instrucción de acento.
3. Deja que genere las imágenes.
   **Esperado:** el set comparte paleta, luz y mundo, pero las distancias son claramente
   distintas: el primero es el plano más cerrado y el último el más abierto.
4. Edita `ritmo_carrusel` en tu identidad (por ejemplo, todo en planos generales) y genera otro
   carrusel.
   **Esperado:** la escalera sigue siendo la misma —tensión, desarrollo, prueba, remate— pero
   ejecutada con tus planos, y esos textos aparecen literales en la sección 3.
5. Deja el ritmo **vacío** y genera otro.
   **Esperado:** se usan los planos de la casa (`brand.json` → `ritmo_carrusel`). Ningún error,
   ningún aviso: vacío = lo de siempre.
6. Desde la revisión, rehaz **un** slide del medio.
   **Esperado:** vuelve con el mismo plano y la misma escala de titular que tenía: rehacer
   cambia la tirada del modelo, no la función del slide en el carrusel.

### 5. Elegir la identidad al crear el post

1. Con la identidad de prueba **activa**, ve a `/individual` y mira el campo «Identidad
   visual» (en Configuración, bajo el set de plantillas).
   **Esperado:** la primera opción es «Identidad activa (nombre de la activa)» y está
   seleccionada; debajo aparecen la de la casa y las tuyas, con «· activa» en la que lo esté.
2. Elige explícitamente **QBYK — identidad de la casa** y genera el post.
   **Esperado:** el prompt de la portada lleva `#0B0C0E, #EDEAE0, #C9F227` aunque la activa
   sea otra: el campo del formulario gana.
3. Repite dejando la opción por defecto («Identidad activa»).
   **Esperado:** sale con la paleta de la identidad activa, exactamente como antes de que el
   campo existiera.
4. Deja el formulario abierto, borra en `/cuenta` la identidad que tenías elegida y envía.
   **Esperado:** el post se crea igual, con la activa. No hay error.
5. En `/historia`, cambia el formato a **video**.
   **Esperado:** el campo se atenúa junto a los de imagen — la identidad solo pinta imágenes.
   (Por eso `/reel`, que siempre es video, no lo lleva.)

### 6. Los dos flujos

1. Con la identidad de prueba activa, sube un `.xlsx` en `/bulk` con 2–3 filas.
2. En `/batches/:id`, abre el preview de cada fila y mira el prompt.
   **Esperado:** las tres filas llevan la **misma** paleta, la de la identidad activa. La
   identidad se resuelve una sola vez al subir el sheet.
3. Con el lote a medio generar, vuelve a `/cuenta` y activa otra identidad.
   **Esperado:** el lote en curso **no** cambia — está congelado.
4. Sube otro `.xlsx` eligiendo en el paso 3 de `/bulk` una identidad distinta de la activa.
   **Esperado:** **todas** las filas salen con la elegida, no con la activa.

### 7. Perfiles

1. En la barra, cambia el selector de «QBYK» a «Cliente 1».
   **Esperado:** la página recarga y `/cuenta` muestra **solo** la identidad de la casa: las que
   creaste son de QBYK.
2. Crea una identidad como «Cliente 1» y vuelve a «QBYK».
   **Esperado:** no la ves.

### 8. Sin base de datos

1. Comenta `MONGODB_URI` en el `.env` de la raíz y reinicia la API.
2. Ve a `/cuenta`.
   **Esperado:** se lista **solo** la identidad de la casa, marcada como activa.
3. Intenta crear una.
   **Esperado:** error claro «No hay conexión con la base de datos: revisa MONGODB_URI en el
   .env. Mientras tanto puedes seguir generando con la identidad de la casa.»
4. Genera un post individual.
   **Esperado:** funciona con normalidad, con el look de la casa. La generación nunca depende
   de que haya base.

### 9. Reversibilidad de la migración

```bash
cd api && python -m migrations.run down
cd api && python -m migrations.run status   # 001 debe salir PENDIENTE
cd api && python -m migrations.run up       # y vuelve a quedar aplicada
```

**Esperado:** `down` borra la colección (y con ella las identidades de prueba); la app sigue
generando con la identidad de la casa entre uno y otro.
