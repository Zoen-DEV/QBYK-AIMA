"""Lint de los prompts visuales y del copy: avisa en el preview lo que se iba en silencio.

Hasta acá nadie revisaba lo que devolvía el LLM. Si entregaba menos escenas de las
pedidas, la app rellenaba con una variación del **título** — justo el fallo que el
resto del sistema se esforzó en eliminar (la escena sale de la transcripción, no del
título) — y lo hacía sin decir nada. Tampoco se comprobaba que las escenas fueran
distintas entre sí ni que esquivaran los clichés que el propio prompt del sistema
prohíbe.

Esto no bloquea nada: describe lo que va a pasar si se genera así, en la compuerta
donde corregirlo todavía es gratis. Lo consumen las DOS revisiones previas (el
preview del individual y el editor por fila del lote) desde `/jobs/{id}` y
`/jobs/{id}/lint`.

Cada aviso es `{"campo", "nivel", "mensaje"}` con `nivel` ∈ {"alto", "medio"}.
"""
import re
from difflib import SequenceMatcher

import prompt_architect as parch
import visual_identity as vi

# Escenas prohibidas por la regla de GROUNDING del prompt del sistema. Es un espejo
# de la lista que vive en `post_writer._system_prompt`; `test_prompt_lint` comprueba
# que sigan diciendo lo mismo, para que no se separen con el tiempo.
CLICHES = (
    "modern office",
    "person at a laptop",
    "business people in a meeting",
    "abstract editorial background",
    "data/network flowing",
    "glowing dashboards",
)

# "Las manos nunca son el sujeto": es donde el modelo dibuja seis dedos. Una mano
# quieta o un antebrazo entrando en cuadro están permitidos, así que solo se avisa
# cuando la mano aparece HACIENDO algo.
_MANOS = re.compile(r"\b(hand|hands|finger|fingers)\b", re.I)
_MANOS_ACCION = re.compile(
    r"\b(typ\w+|writ\w+|play\w+|count\w+|hold\w+|assembl\w+|gestur\w+|point\w+|"
    r"grasp\w+|grip\w+|tap\w+|swip\w+|scroll\w+|cook\w+|cut\w+|paint\w+)\b", re.I
)

# Colores de paleta en `image_style`. La paleta es identidad de marca y la inyecta el
# arquitecto desde brand.json: si el estilo del post nombra la suya, hay dos paletas
# compitiendo en el mismo prompt. El blanco/negro/gris son luz, no paleta.
_COLORES = re.compile(
    r"\b(amber|azure|beige|blue|bronze|brown|burgundy|cobalt|copper|coral|cream|"
    r"crimson|cyan|denim|emerald|gold|golden|green|indigo|ivory|jade|lavender|lilac|"
    r"lime|magenta|maroon|mint|mustard|navy|ochre|olive|orange|peach|pink|purple|red|"
    r"rose|ruby|rust|saffron|salmon|sepia|sage|scarlet|teal|terracotta|turquoise|"
    r"violet|yellow)\b", re.I
)
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

# Palabras de relleno que no dicen nada del contenido: se ignoran al comparar escenas.
_VACIAS = {
    "with", "that", "this", "from", "into", "onto", "over", "under", "shot", "scene",
    "image", "photo", "photograph", "frame", "framing", "view", "close", "shallow",
    "soft", "light", "lighting", "background", "foreground", "casting", "casts",
    "contact", "shadow", "rests", "resting", "sits", "sitting", "same", "single",
    "detail", "surface", "against", "while", "their", "there", "very", "much",
}


def _palabras(texto: str) -> set[str]:
    """Palabras con contenido de una escena (>3 letras, sin relleno)."""
    return {w for w in re.findall(r"[a-záéíóúñ]{4,}", (texto or "").lower())
            if w not in _VACIAS}


def _parecido(a: str, b: str) -> float:
    """0-1: cuánto se parecen dos escenas.

    Se miran las dos caras del problema — el mismo texto reformulado (secuencia) y
    los mismos objetos con otras palabras (vocabulario en común) — y manda la peor,
    porque cualquiera de las dos produce dos slides que se leen igual.
    """
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return 0.0
    secuencia = SequenceMatcher(None, a, b).ratio()
    pa, pb = _palabras(a), _palabras(b)
    jaccard = len(pa & pb) / len(pa | pb) if (pa and pb) else 0.0
    return max(secuencia, jaccard)


# Umbrales: por debajo, dos escenas del mismo mundo visual comparten materiales y
# ambiente sin ser la misma imagen (que es exactamente lo que se les pide).
_UMBRAL_PARECIDO = 0.72


def _nombre(i: int) -> str:
    """Cómo llamar a cada escena en el aviso (0 = portada)."""
    return "la portada" if i == 0 else f"el slide {i + 1}"


def _de(nombre: str) -> str:
    """«de la portada» / «del slide 3» — la contracción, para que el aviso se lea bien."""
    return f"del {nombre[3:]}" if nombre.startswith("el ") else f"de {nombre}"


# Con un arco de sujeto recurrente (transformación, cadena) DOS ESCENAS PARECIDAS SON EL
# OBJETIVO: el mismo objeto vuelve y lo que cambia es su estado. Avisar ahí sería avisar
# de que el carrusel hace lo que se le pidió, y un aviso que se dispara siempre se acaba
# ignorando —incluidos los que sí importan—. Pero «parecidas» no es «idénticas»: si dos
# escenas son literalmente la misma frase, no hay cambio de estado que mostrar y el slide
# sale repetido de verdad. Por eso el umbral sube en vez de apagarse.
_UMBRAL_PARECIDO_RECURRENTE = 0.95


def _revisar_escenas(escenas: list[str], nombres, campo: str, avisos: list[dict],
                     *, sujeto_vuelve: bool = False) -> None:
    """Clichés, manos como sujeto y escenas repetidas dentro de un juego."""
    for i, escena in enumerate(escenas):
        if not (escena or "").strip():
            continue
        bajo = escena.lower()
        for cliche in CLICHES:
            if cliche in bajo:
                avisos.append({
                    "campo": campo, "nivel": "alto",
                    "mensaje": f"La escena {_de(nombres(i))} usa «{cliche}», que el prompt del "
                               "sistema prohíbe por genérica: cámbiala por un objeto concreto "
                               "de la transcripción.",
                })
        if _MANOS.search(escena) and _MANOS_ACCION.search(escena):
            avisos.append({
                "campo": campo, "nivel": "medio",
                "mensaje": f"En {nombres(i)} las manos son el sujeto haciendo algo: es donde el "
                           "modelo dibuja dedos de más. Deja la presencia humana quieta y parcial "
                           "(un hombro, una silueta) o quítala.",
            })

    umbral = _UMBRAL_PARECIDO_RECURRENTE if sujeto_vuelve else _UMBRAL_PARECIDO
    for i in range(len(escenas)):
        for j in range(i + 1, len(escenas)):
            if _parecido(escenas[i], escenas[j]) < umbral:
                continue
            if sujeto_vuelve:
                avisos.append({
                    "campo": campo, "nivel": "alto",
                    "mensaje": f"La escena {_de(nombres(j))} es palabra por palabra la "
                               f"{_de(nombres(i))}. El arco de este carrusel pide que el mismo "
                               "objeto vuelva, pero con su ESTADO cambiado: si las dos escenas "
                               "dicen lo mismo no hay nada que mostrar y el slide sale repetido. "
                               "Nombra qué cambió (lleno→vacío, entero→partido, dónde acabó).",
                })
            else:
                avisos.append({
                    "campo": campo, "nivel": "alto",
                    "mensaje": f"La escena {_de(nombres(j))} es casi la misma que la "
                               f"{_de(nombres(i))}: cada una tiene que salir de un detalle "
                               "DISTINTO de la fuente, o el carrusel se lee como la misma "
                               "imagen repetida.",
                })


# ── Copy de los posts: se revisa la ESTRUCTURA, no las palabras ───────────────
# El prompt del sistema ofrece un catálogo de estructuras (anécdota, contraste,
# dato, tesis, pasos, lista, diagnóstico, analogía) justamente porque el esqueleto
# "gancho + viñetas + pregunta de cierre + hashtags" es lo que delata un post
# escrito por IA, por buenas que sean las frases. Acá se comprueba lo único que se
# puede comprobar sin volver a llamar al modelo: si volvió a caer en ese esqueleto,
# y si dos redes salieron con la misma apertura (el otro síntoma —tres versiones
# del mismo post— que el lector sí nota si te sigue en las dos).
#
# La lista NO está prohibida: hay contenido que de verdad enumera. Por eso el aviso
# solo salta cuando TODOS los captions del job son lista + pregunta, que es el caso
# en el que la estructura no se eligió: se aplicó la plantilla.

_CAPTIONS = (("linkedin_text", "LinkedIn"), ("instagram_text", "Instagram"),
             ("facebook_text", "Facebook"))

_VINETA = re.compile(r"^\s*(?:[-–—•*·▪●→▶✓✔]|\d+[.)])\s+")
_SOLO_HASHTAGS = re.compile(r"^\s*(?:#\S+\s*)+$")
_MIN_VINETAS = 3
_UMBRAL_APERTURA = 0.8


def _lineas_utiles(texto: str) -> list[str]:
    """Líneas del caption sin las de solo hashtags: esas son el pie, no el cuerpo."""
    return [l.strip() for l in (texto or "").splitlines()
            if l.strip() and not _SOLO_HASHTAGS.match(l)]


def _es_lista(texto: str) -> bool:
    return sum(1 for l in _lineas_utiles(texto) if _VINETA.match(l)) >= _MIN_VINETAS


def _cierra_preguntando(texto: str) -> bool:
    """¿El post cierra con una pregunta?

    Se miran las DOS últimas líneas útiles, no solo la última: en LinkedIn la
    pregunta convive con la línea del enlace al video y puede quedar cualquiera de
    las dos al final.
    """
    return any(l.rstrip().endswith(("?", "¿")) for l in _lineas_utiles(texto)[-2:])


def _revisar_copy(posts: dict, *, avisos: list[dict]) -> None:
    escritos = [(nombre, texto) for clave, nombre in _CAPTIONS
                if (texto := str(posts.get(clave) or "").strip())]
    if not escritos:
        return

    if all(_es_lista(t) and _cierra_preguntando(t) for _, t in escritos):
        cuales = " y ".join(n for n, _ in escritos)
        avisos.append({
            "campo": "copy", "nivel": "medio",
            "mensaje": f"{cuales}: el post sale con el esqueleto genérico (gancho, lista de "
                       "viñetas y pregunta de cierre), que es la forma con la que se reconoce un "
                       "texto de IA. Si la fuente no enumera cosas independientes, reescríbelo acá "
                       "abajo como anécdota, contraste, dato o tesis.",
        })

    for i in range(len(escritos)):
        for j in range(i + 1, len(escritos)):
            (na, ta), (nb, tb) = escritos[i], escritos[j]
            ia, ib = _lineas_utiles(ta), _lineas_utiles(tb)
            if ia and ib and _parecido(ia[0], ib[0]) >= _UMBRAL_APERTURA:
                avisos.append({
                    "campo": "copy", "nivel": "medio",
                    "mensaje": f"{na} y {nb} abren con la misma frase: son públicos distintos y "
                               "quien te siga en las dos ve el mismo post duplicado. Cambia la "
                               "entrada de uno de los dos.",
                })


# ── Red de seguridad: la identidad activa y la continuidad del set ────────────
#
# Los dos avisos de abajo no miran lo que escribió el LLM sino lo que va a HACER la
# app con ello. Son la última compuerta antes de gastar créditos, y existen porque los
# dos defectos que cubren son silenciosos por naturaleza: la continuidad de set se cayó
# entera durante meses sin un solo error en el log, y una identidad guardada sigue
# generando mal para siempre porque `validar` no corre al leerla.


def _revisar_continuidad(posts: dict, *, n_info: int, avisos: list[dict],
                         arco: str = "", escenario: str = "") -> None:
    """¿Lo que mantiene unido al carrusel va a llegar al prompt de los slides?

    Es el canario de la regresión que originó todo esto: `_clausula_set` comparaba el
    rol contra el literal `"contenido"` y los slides llegan con el nombre de su beat,
    así que la cláusula dejó de emitirse en TODOS los slides de carrusel. Se comprueba
    construyendo las cláusulas de verdad —son deterministas y no llaman a nadie—, no
    leyendo el código: un test protege el módulo, esto protege al usuario.

    Cubre las tres, porque las tres se caen igual de calladas: la continuidad, el
    ENLACE DEL ARCO (que va dentro de ella y es lo que hace que las piezas se cuenten
    algo entre sí) y el BLOQUEO DE MUNDO (que es lo que hace que compartan sitio).
    """
    if n_info < 1:
        return
    roles = parch.roles_carrusel(n_info)
    base = {"marca": {}, "referencias": [], "ritmo_carrusel": [],
            "arco_carrusel": arco, "escenario": escenario}
    sin_clausula = [r for r in roles if not parch._clausula_set(
        {**base, "contenido": {"rol_slide": r,
                               "escena_portada": (posts.get("image_prompt") or "").strip()}})]
    if sin_clausula:
        avisos.append({
            "campo": "image_slide_prompts", "nivel": "alto",
            "mensaje": "Los slides van a generarse SIN la cláusula de continuidad de set "
                       f"({', '.join(sorted(set(sin_clausula)))}): cada uno inventaría su "
                       "propia localización y su propia luz, y el carrusel saldría con "
                       "tantos mundos como piezas. Es un fallo del código, no de lo que "
                       "escribiste — avisa antes de gastar créditos.",
        })
    # El mundo se declara en TODAS las piezas, portada incluida: se comprueba sobre la
    # portada porque si ahí no sale, no sale en ninguna.
    if escenario and not parch._clausula_mundo({**base, "contenido": {"rol_slide": "portada"}}):
        avisos.append({
            "campo": "image_prompt", "nivel": "alto",
            "mensaje": "El bloqueo de mundo no se va a emitir: las piezas de este post no van "
                       "a compartir localización y cada imagen elegirá la suya. Es un fallo "
                       "del código, no de lo que escribiste.",
        })


def _revisar_identidad(identidad: dict, *, avisos: list[dict]) -> None:
    """Reparos de la identidad activa, con las reglas de `visual_identity`.

    Las identidades guardadas NO se revalidan al leerlas (a propósito: una identidad
    guardada nunca puede tumbar una generación en curso), así que una anterior a las
    puertas del esquema sigue generando mal hasta que alguien la abra en `/cuenta`.
    Este es el sitio donde se entera quien está a punto de generar.

    Las reglas se REUSAN, no se reimplementan: si se escribieran otra vez aquí, el
    lint y el validador se separarían en cuanto alguien tocara una constante.
    """
    if not identidad:
        return
    ident = vi.normalizar(identidad)
    for error in vi.validar(ident) + vi.revisar_diseno(ident):
        # Solo lo que afecta a la IMAGEN: de una paleta corta o un hex ausente ya se
        # queja el editor de identidades, y repetirlo acá sería ruido en otra pantalla.
        if not any(c in error for c in ("ritmo_carrusel", "tipografia", "escenario",
                                        "`desarrollo`", "`tension`", "`prueba`", "`remate`")):
            continue
        avisos.append({
            "campo": "identidad", "nivel": "medio",
            "mensaje": f"La identidad visual activa tiene un reparo que afecta a estas "
                       f"imágenes: {error} Corrígela en /cuenta — se guardó antes de que "
                       "existiera esta comprobación y no se revalida al generar.",
        })


def revisar(posts: dict, *, n_info: int = 0, is_carousel: bool = False,
            quiere_imagenes: bool = True, quiere_video: bool = False,
            identidad: dict | None = None, arco: str = "", escenario: str = "",
            sistema: str = "") -> list[dict]:
    """Avisos sobre los prompts y el copy de ESTE job. Nunca lanza: sin datos, [].

    `n_info` es la cantidad de slides de info del carrusel (los que siguen a la
    portada) — es contra ese número que se comprueba si el modelo entregó de menos.
    `identidad` es la identidad congelada en el job (`{}` = la de la casa), y `arco` /
    `escenario` son los otros dos ejes congelados: el arco cambia qué cuenta como
    escena repetida y los dos entran en el canario de continuidad.
    """
    avisos: list[dict] = []
    try:
        _revisar_copy(posts, avisos=avisos)
        if quiere_imagenes:
            _revisar_imagenes(posts, n_info=n_info, is_carousel=is_carousel,
                              avisos=avisos, arco=arco, sistema=sistema)
            if is_carousel:
                _revisar_continuidad(posts, n_info=n_info, avisos=avisos,
                                     arco=arco, escenario=escenario)
            _revisar_identidad(identidad or {}, avisos=avisos)
        if quiere_video:
            _revisar_video(posts, avisos=avisos)
    except Exception as e:  # noqa: BLE001 - un lint roto no puede tapar la pantalla
        print(f"   [aviso] El lint de prompts falló: {e}")
    return avisos


def _revisar_imagenes(posts: dict, *, n_info: int, is_carousel: bool, avisos: list[dict],
                      arco: str = "", sistema: str = "") -> None:
    portada = (posts.get("image_prompt") or "").strip()
    slides = [s.strip() for s in (posts.get("image_slide_prompts") or [])
              if isinstance(s, str) and s.strip()]

    if not portada:
        avisos.append({
            "campo": "image_prompt", "nivel": "alto",
            "mensaje": "Sin escena de portada, la imagen se arma con una frase genérica basada en "
                       "el TÍTULO en vez de en la transcripción.",
        })

    # El caso que hasta ahora era silencioso: faltan escenas y la app rellena con una
    # variación del título ("Conceptual editorial visual about: {topic}, variation N").
    if is_carousel and len(slides) < n_info:
        faltan = n_info - len(slides)
        avisos.append({
            "campo": "image_slide_prompts", "nivel": "alto",
            "mensaje": f"Hay {len(slides)} escena(s) para {n_info} slide(s) de info: "
                       f"{faltan} se van a rellenar con una variación del TÍTULO, no de la "
                       "transcripción. Escríbelas o baja la cantidad de slides.",
        })

    # El arco decide qué es una repetición y qué es la historia: con transformación o
    # cadena, que el slide 2 se parezca a la portada es lo que se pidió.
    vuelve = parch.sujeto_arco(arco) in ("recurrente", "encadenado") if arco else False
    _revisar_escenas([portada] + slides, _nombre, "image_slide_prompts", avisos,
                     sujeto_vuelve=vuelve)

    estilo = (posts.get("image_style") or "").strip()
    if not estilo:
        avisos.append({
            "campo": "image_style", "nivel": "medio",
            "mensaje": "Sin dirección de arte, todas las imágenes usan el acabado genérico de "
                       "respaldo y el set pierde lo que lo hace parecer un set.",
        })
    else:
        colores = sorted({c.lower() for c in _COLORES.findall(estilo)} |
                         {h.lower() for h in _HEX.findall(estilo)})
        if colores:
            avisos.append({
                "campo": "image_style", "nivel": "medio",
                "mensaje": f"La dirección de arte nombra colores ({', '.join(colores)}): la paleta "
                           "es identidad de marca y la pone la app, así que quedarían dos paletas "
                           "compitiendo. Deja aquí solo luz, material, óptica y acabado.",
            })

    # Copy de las piezas: es lo que el modelo va a IMPRIMIR en la imagen.
    texto = posts.get("image_text") if isinstance(posts.get("image_text"), dict) else {}
    hook = (texto.get("hook") or "").strip()
    if not hook:
        avisos.append({
            "campo": "image_hook", "nivel": "alto",
            "mensaje": "Sin texto de portada, la imagen se genera con la primera línea del caption, "
                       "que casi nunca funciona como titular.",
        })
    elif len(hook.split()) > 14:
        avisos.append({
            "campo": "image_hook", "nivel": "medio",
            "mensaje": f"El texto de portada tiene {len(hook.split())} palabras: a ese largo el "
                       "titular entra pequeño y se lee mal en el feed (apunta a 10 o menos).",
        })
    if is_carousel:
        _revisar_bloques_slide(texto.get("slides"), n_info=n_info, sistema=sistema,
                               avisos=avisos)


def _revisar_bloques_slide(slides, *, n_info: int, sistema: str, avisos: list[dict]) -> None:
    """¿Cada slide trae los bloques que su sistema de texto va a IMPRIMIR?

    Antes esto era `len(frases) < n_info`, que solo veía el slide entero vacío. Con un
    sistema de dos o tres niveles ese recuento pasa de largo el defecto que importa: N
    slides con titular y sin cuerpo son N piezas que se generan, se publican y salen
    medio vacías, y ni un solo error en el camino. Se cuenta lo que la pieza imprime.

    El detalle se nombra por bloque a propósito: «faltan frases» no dice dónde escribir,
    y la compuerta previa tiene un campo por bloque justo al lado del aviso.
    """
    lista = slides if isinstance(slides, (list, tuple)) else []
    requeridos = parch.bloques_requeridos(sistema)
    huecos: dict[str, list[int]] = {}
    for i in range(n_info):
        bloques = parch.bloques_de_slide(lista[i] if i < len(lista) else "", sistema)
        for clave in requeridos:
            if not bloques.get(clave):
                huecos.setdefault(clave, []).append(i + 2)   # el slide 1 es la portada
    for clave, posiciones in huecos.items():
        cuantos = len(posiciones)
        donde = ", ".join(str(p) for p in posiciones)
        avisos.append({
            "campo": "image_slides", "nivel": "alto",
            "mensaje": (f"Falta el {clave} en {cuantos} de {n_info} slide(s) (el {donde}): "
                        f"esa parte de la pieza se genera vacía."
                        if cuantos < n_info else
                        f"Ningún slide trae su {clave}: esa parte de la pieza se genera "
                        "vacía en todo el carrusel."),
        })


def _revisar_video(posts: dict, *, avisos: list[dict]) -> None:
    beats = [b.strip() for b in (posts.get("video_storyboard") or [])
             if isinstance(b, str) and b.strip()]
    _revisar_escenas(beats, lambda i: f"el shot {i + 1}", "video_storyboard", avisos)

    voz = [v.strip() for v in (posts.get("video_voiceover") or [])
           if isinstance(v, str) and v.strip()]
    if beats and voz and len(beats) != len(voz):
        avisos.append({
            "campo": "video_voiceover", "nivel": "alto",
            "mensaje": f"La voz tiene {len(voz)} línea(s) y el storyboard {len(beats)} shot(s): "
                       "el reel necesita una línea hablada por shot o se genera mudo y sin "
                       "subtítulos.",
        })
