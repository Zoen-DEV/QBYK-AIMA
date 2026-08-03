# Identidades visuales

Hasta esta feature, el look de todas las imágenes salía de **un** archivo
([`api/prompts/brand.json`](../api/prompts/brand.json)). Funcionaba, pero todos los posts
salían visualmente iguales. Ahora la identidad visual es un dato **con nombre y dueño**:
cada usuario puede tener varias y elegir con cuál genera.

El esquema no cambió. Una identidad guardada tiene exactamente los mismos campos y los
mismos tipos que `brand.json` — lo que se persiste es otro valor del mismo contrato, no
un formato nuevo.

## El esquema y sus dos contratos escondidos

[`api/visual_identity.py`](../api/visual_identity.py) es el esquema hecho validador. Los
nueve campos son los de `brand.json`; quedan fuera `_comment*` y `version`, que describen
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

Dos acuerdos vivían implícitos en el código y romperlos **no dejaba rastro**. El validador
los hace explícitos:

1. **`paleta` está ordenada: `[fondo, texto, acento]`.**
   `job_runner._lockup_plantilla` usa `paleta[1]` y `paleta[2]` como respaldo de
   `color_texto`/`color_acento`. Una paleta en otro orden pinta la plantilla con los
   colores cambiados y no hay un solo error en el log.
2. **`color_texto` y `color_acento` llevan su hex, y es el de la paleta.**
   `image_overlay._color` busca un `#RRGGBB` dentro de la frase y, si no lo encuentra, cae
   a un hueso por defecto: una identidad que diga `"warm off-white"` a secas se dibuja con
   el color de **otra** marca.

Los topes de longitud son presupuesto, no estética: todo esto se inyecta en el brief de
nueve secciones, que tiene un límite duro de caracteres (`architect.json` →
`validacion.max_caracteres`).

## Qué cambia (y qué no) al cambiar de identidad

Cambian **paleta, color de acento, familia tipográfica y referencias de dirección de arte**.

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

- La identidad se **congela** en `params` al crear el job (`_params_identidad`, compartido por
  los dos flujos). Cambiar la identidad activa a mitad de una generación no puede alterar un
  job en vuelo, y un lote entero sale con la que estaba activa al subir el sheet — no por fila.
- `_identidad(job)` la lee. **Vacío significa "lo de siempre"**, no "identidad en blanco": los
  campos vacíos no se pasan, así que caen a `brand.json` campo a campo. Un job sin identidad
  produce una `marca` idéntica —no parecida— a la de antes de la feature, y hay un test que
  compara el diccionario entero.
- Las `referencias` viajan al **nivel de arriba** de la spec, no dentro de `marca`: ahí es
  donde las lee `normalizar_spec`. Metidas en `marca` se perderían sin un solo error.

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
5. Cambia el nombre a «Prueba» y pulsa **Guardar identidad**.
6. **Esperado:** el modal se cierra, aparece la identidad nueva en la lista con sus muestras de
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

### 5. Los dos flujos

1. Con la identidad de prueba activa, sube un `.xlsx` en `/bulk` con 2–3 filas.
2. En `/batches/:id`, abre el preview de cada fila y mira el prompt.
   **Esperado:** las tres filas llevan la **misma** paleta, la de la identidad activa. La
   identidad se resuelve una sola vez al subir el sheet.
3. Con el lote a medio generar, vuelve a `/cuenta` y activa otra identidad.
   **Esperado:** el lote en curso **no** cambia — está congelado.

### 6. Perfiles

1. En la barra, cambia el selector de «QBYK» a «Cliente 1».
   **Esperado:** la página recarga y `/cuenta` muestra **solo** la identidad de la casa: las que
   creaste son de QBYK.
2. Crea una identidad como «Cliente 1» y vuelve a «QBYK».
   **Esperado:** no la ves.

### 7. Sin base de datos

1. Comenta `MONGODB_URI` en el `.env` de la raíz y reinicia la API.
2. Ve a `/cuenta`.
   **Esperado:** se lista **solo** la identidad de la casa, marcada como activa.
3. Intenta crear una.
   **Esperado:** error claro «No hay conexión con la base de datos: revisa MONGODB_URI en el
   .env. Mientras tanto puedes seguir generando con la identidad de la casa.»
4. Genera un post individual.
   **Esperado:** funciona con normalidad, con el look de la casa. La generación nunca depende
   de que haya base.

### 8. Reversibilidad de la migración

```bash
cd api && python -m migrations.run down
cd api && python -m migrations.run status   # 001 debe salir PENDIENTE
cd api && python -m migrations.run up       # y vuelve a quedar aplicada
```

**Esperado:** `down` borra la colección (y con ella las identidades de prueba); la app sigue
generando con la identidad de la casa entre uno y otro.
