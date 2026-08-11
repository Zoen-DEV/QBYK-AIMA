"""Extracción de una identidad visual a partir de fotos de referencia.

Recibe de 5 a 10 fotos y devuelve un JSON que cumple **exactamente** el esquema de
`visual_identity` — el mismo que gobierna `prompts/brand.json`. Sin campos nuevos: lo
que sale de aquí tiene que poder entrar en el pipeline sin que nadie lo traduzca.

Cuatro decisiones que vale la pena no deshacer:

- **Las fotos se revisan ANTES de llamar al modelo.** Cantidad, formato y peso se
  comprueban con `revisar_fotos`, que es pura y no toca la red: subir 4 u 11 fotos
  tiene que costar cero y decirlo claro, no gastar una llamada para enterarse.
- **Las reglas del esquema las escribe la app, no el archivo de prompt.** Viven en
  `_reglas_esquema`, generadas desde las constantes de `visual_identity`, porque son el
  mismo contrato que aplica el validador: escritas dos veces se desincronizan en cuanto
  alguien mueva un límite. El JSON de prompt aporta el encuadre creativo, igual que en
  `prompt_architect` la app escribe las secciones que no se delegan.
- **Se extrae con ojo de diseñador, no de notario.** Describir con fidelidad un set de
  fotos de teléfono produce una identidad de fotos de teléfono, y de ahí salen piezas
  de aficionado por muy bien que esté el prompt de generación. El encuadre del JSON le
  pide al modelo lo que hace un director de arte con el moodboard del cliente: leer la
  INTENCIÓN del set y especificarla a calidad de producción —fiel en lo que es
  identidad (familia de color, luz, materiales, registro), sin heredar los accidentes
  de la referencia (flash directo, balance de blancos mezclado, fondo con ruido)—, y
  con cada campo escrito como instrucción de rodaje, que es como lo va a leer el modelo
  de imagen. Lo que sí se puede comprobar sobre los hex —contraste del titular,
  saturación del acento— lo escribe la app en `_reglas_diseno` con los mismos números
  que aplica `visual_identity.revisar_diseno`, por el mismo motivo que el esquema.
- **Un solo reintento, con lo que falló como feedback.** Si el segundo intento tampoco
  valida se falla limpio: nada a medias entra a la base, y las fotos siguen en el
  navegador para volver a intentarlo sin re-subirlas. Un **reparo de diseño** no es un
  fallo de esquema: también gasta el reintento, pero si sobrevive NO tumba la
  extracción — sale como aviso junto al editor, que es donde el usuario puede corregir
  el color en dos segundos.

Las fotos NO se guardan en ningún sitio: se reducen en memoria, se mandan al modelo y
se descartan al terminar el request.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field

import llm_json
import prompt_config
import visual_identity as vi

# Rango de fotos. El mínimo es lo que hace falta para que una identidad sea una
# tendencia y no la descripción de una foto suelta; el máximo acota el costo y el peso
# del request.
MIN_FOTOS, MAX_FOTOS = 5, 10

FORMATOS = ("image/jpeg", "image/png", "image/webp")
NOMBRES_FORMATO = "JPG, PNG o WebP"
MAX_MB_FOTO = 10
MAX_MB_TOTAL = 40

# Lado mayor al que se reduce cada foto antes de mandarla. Anthropic escala por su
# cuenta cualquier cosa por encima de ~1568 px, así que subir más resolución solo
# gasta ancho de banda; 1024 conserva de sobra paleta, luz y textura, que es lo único
# que se le pregunta a estas imágenes.
LADO_MAXIMO = 1024
CALIDAD_JPEG = 82

_INTENTOS = 2  # el primero + un reintento con los errores como feedback

try:
    from PIL import Image
    _HAY_PIL = True
except Exception:  # noqa: BLE001
    _HAY_PIL = False


class FotosInvalidas(ValueError):
    """Las fotos no cumplen (cantidad, formato o peso). **No se llamó al modelo.**"""


class ExtraccionNoDisponible(RuntimeError):
    """Falta lo que hace falta para extraer (modelo de visión o Pillow)."""


class ExtraccionInvalida(RuntimeError):
    """El modelo no devolvió un JSON válido ni en el reintento. `errores` trae el detalle."""

    def __init__(self, mensaje: str, errores: list[str] | None = None):
        self.errores = list(errores or [])
        super().__init__(mensaje)


@dataclass
class ResultadoExtraccion:
    """Identidad extraída + lo que hace falta para cobrarla y auditarla."""

    identidad: dict
    usos: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    intentos: int = 1


# ── Revisión de las fotos (pura, sin red) ─────────────────────────────────────

_FIRMAS = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def tipo_imagen(datos: bytes) -> str:
    """Media type por los bytes mágicos, o "" si no es un formato admitido.

    Por los bytes y no por el `content-type` del navegador ni por la extensión: los dos
    los escribe el cliente y aquí lo que importa es qué se le va a mandar al modelo.
    """
    for firma, mime in _FIRMAS:
        if datos.startswith(firma):
            return mime
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return ""


def revisar_fotos(fotos: list[tuple[bytes, str]]) -> list[str]:
    """Errores de las fotos ([] si sirven). Pura: no llama a nadie.

    Es la compuerta que garantiza que 4 u 11 fotos no lleguen a costar una llamada.
    """
    errores: list[str] = []
    n = len(fotos or [])
    if not MIN_FOTOS <= n <= MAX_FOTOS:
        errores.append(
            f"Sube entre {MIN_FOTOS} y {MAX_FOTOS} fotos de referencia (subiste {n})."
        )
    total = 0
    for datos, nombre in (fotos or []):
        etiqueta = nombre or "una de las fotos"
        if not datos:
            errores.append(f"«{etiqueta}» está vacío.")
            continue
        total += len(datos)
        if not tipo_imagen(datos):
            errores.append(f"«{etiqueta}» no es un formato admitido: solo {NOMBRES_FORMATO}.")
        if len(datos) > MAX_MB_FOTO * 1024 * 1024:
            errores.append(
                f"«{etiqueta}» pesa {len(datos) / 1024 / 1024:.1f} MB; el máximo por foto "
                f"es {MAX_MB_FOTO} MB."
            )
    if total > MAX_MB_TOTAL * 1024 * 1024:
        errores.append(f"Las fotos suman más de {MAX_MB_TOTAL} MB en total.")
    return errores


# ── Preparación ───────────────────────────────────────────────────────────────

def _preparar(datos: bytes) -> tuple[bytes, str]:
    """Reduce una foto a `LADO_MAXIMO` y la reencoda a JPEG. Solo en memoria.

    Ante cualquier problema devuelve la original con su media type: una foto que Pillow
    no sabe reescalar puede ser perfectamente legible para el modelo.
    """
    original = (datos, tipo_imagen(datos) or "image/jpeg")
    try:
        with Image.open(io.BytesIO(datos)) as img:
            img = img.convert("RGB")
            if max(img.size) > LADO_MAXIMO:
                img.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=CALIDAD_JPEG)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return original


# ── Prompt ────────────────────────────────────────────────────────────────────

def _reglas_esquema() -> str:
    """Las reglas duras del esquema, generadas desde `visual_identity`.

    No viven en el JSON del prompt a propósito (ver el docstring del módulo): son el
    contrato que aplica el validador y tienen que moverse con él.
    """
    return "\n".join([
        "Return a JSON object with EXACTLY these keys and no others: "
        + ", ".join(f"`{c}`" for c in vi.CAMPOS) + ".",
        "",
        "HARD RULES — a response that breaks any of these is rejected:",
        f"1. `paleta`: an ORDERED list of {vi.MIN_COLORES}-{vi.MAX_COLORES} hex colours "
        "`#RRGGBB`, in this exact order: [background, text, accent]. First the dominant "
        "field the subjects sit on, second the colour type would be set in over it, "
        "third the single accent colour.",
        "2. `paleta_nombres`: one short human colour name per colour, same order, same "
        f"length as `paleta`, at most {vi.MAX_NOMBRE_COLOR} characters each.",
        "3. `color_texto` MUST contain the hex of the SECOND colour and `color_acento` "
        "the hex of the THIRD, written inside the phrase — e.g. "
        '"bone white (#EDEAE0) on the near-black" and "acid lime (#C9F227)".',
        "4. `tipografia` / `tipografia_secundaria`: describe a type family CLASS "
        "(weight, width, case, tracking), never a specific licensed font name. NEVER "
        "name a neutral interface sans (" + ", ".join(vi.FAMILIAS_UI_PROHIBIDAS) + "): "
        "at poster scale those give the 'caption pasted on a photo' look and the "
        "response is rejected. The case is part of the class you choose, not a default.",
        "5. `tono_visual`: the photographic treatment ONLY — light, contrast, depth, "
        "texture, surround. Do NOT name any colour here: the palette already has its "
        "own fields and two palettes in one prompt fight each other.",
        f"6. `referencias`: {vi.MIN_REFERENCIAS}-{vi.MAX_REFERENCIAS} short "
        "art-direction references, at most "
        f"{vi.MAX_REFERENCIA} characters each.",
        '7. `aspect_ratio`: "width:height" — use "4:5" unless the set is clearly '
        "another format.",
        f"8. `ritmo_carrusel`: an ORDERED list of {vi.MAX_RITMO} shots — never more — one per "
        f"carousel beat, in this exact order: [{', '.join(vi.ROLES_RITMO)}] — the "
        "position IS the beat. Each is at most "
        f"{vi.MAX_RITMO_ITEM} characters and names ONLY shot distance, camera height and "
        "what fills the frame (the palette and the light already have their own fields). "
        "The four must be genuinely different distances: the tension is the tightest shot "
        "of the set, the payoff the widest and deepest. The hero of every piece is an "
        "OBJECT: never write a person into a shot (" + ", ".join(vi.PALABRAS_PERSONA)
        + "). The image brief forbids people as the main subject, so a shot that asks for "
        "one is a contradiction the model resolves by dropping that shot entirely — and "
        "the carousel loses its shot ladder.",
        f"9. `escenarios`: {vi.MIN_ESCENARIOS}-{vi.MAX_ESCENARIOS} LOCATIONS this brand shoots "
        f"in — never more, never one. Each is at most {vi.MAX_ESCENARIO_PALABRAS} words and "
        "names the place, its surfaces and its materials: a location, not a scene and not a "
        "subject. One piece of the set is generated per job from ONE of these, held identical "
        "across every image of that job, so a repertoire of one makes every post of this brand "
        "come out of the same room. They must be genuinely different places, and NOT all "
        "variants of a table (" + ", ".join(vi.PALABRAS_MESA[:4]) + "): a tabletop still life "
        "is one legitimate world, but a repertoire made only of tabletops is how every brand "
        "ends up producing the same photograph. Never write a person into a location ("
        + ", ".join(vi.PALABRAS_PERSONA[:4]) + "), and never restate the palette or the light — "
        "they have their own fields.",
        f"10. `sistemas_texto`: {vi.MIN_SISTEMAS}-{vi.MAX_SISTEMAS} names, chosen ONLY from "
        f"this list: {', '.join(vi.SISTEMAS_TEXTO)}. This is how many TEXT LEVELS the content "
        "slides of this brand print — `titular` is one big headline per slide, "
        "`titular_cuerpo` adds a paragraph of body copy under it, and "
        "`etiqueta_titular_cuerpo` adds a small label above and moves the body to the foot. "
        "Pick from what the reference set actually does: a moodboard of bold single "
        "statements is `titular`; one where the pieces explain something, with small text "
        "under the headline, wants a body. Each job freezes ONE of them, so two of the "
        "brand's carousels do not come out with the same text structure. A name outside "
        "that list is rejected.",
        f"11. Every text field is at most {vi.MAX_TEXTO} characters — around "
        f"{vi.MAX_TEXTO // 7} words. Spend that budget on concrete facts: dense, not long.",
        "12. Write every value in English: these strings are injected verbatim into an "
        "image-generation prompt that is written in English.",
    ])


def _reglas_diseno() -> str:
    """Las reglas de diseño que la app COMPRUEBA sobre los hex devueltos.

    Mismo motivo que `_reglas_esquema`: los números salen de las constantes que aplica
    `visual_identity.revisar_diseno`, para que lo que se pide y lo que se comprueba no
    puedan separarse. El criterio de diseño que no se comprueba —cómo mirar el set, qué
    es una buena referencia— es encuadre creativo y vive en el JSON del prompt.
    """
    return "\n".join([
        "DESIGN RULES — these are checked in code against the hex values you return, "
        "because a palette that fails them prints an unreadable poster:",
        f"A. The type colour (2nd) must reach at least {vi.CONTRASTE_MIN}:1 WCAG "
        "contrast against the background colour (1st). A headline at 2:1 is not a mood, "
        "it is a poster nobody can read.",
        f"B. The accent (3rd) must be a saturated hue — at least "
        f"{round(vi.SATURACION_ACENTO_MIN * 100)}% HSV saturation. A third neutral is "
        "not an accent.",
        "C. The accent must read as a different colour from BOTH the background and the "
        "type colour — by hue and chroma, not only by lightness.",
        "D. `tipografia` must declare that it is a DISPLAY class — name at least one of: "
        + ", ".join(vi.MARCAS_DISPLAY) + ". Without weight, width or case declared the "
        "model sets the headline at caption size.",
        "E. `tipografia_secundaria` must not ask for "
        + " or ".join(f'"{s}"' for s in vi.SECUNDARIA_DEBIL)
        + ": a kicker in regular weight and mixed case is what makes the piece read as a "
        "photo with a caption instead of a poster.",
    ])


# Defaults mínimos: el encuadre bueno vive en el JSON y estos solo tienen que dejar el
# extractor en pie si el archivo falta o está roto (`prompt_config.load` → `{}`).
_SISTEMA_POR_DEFECTO = (
    "You are a senior graphic designer, art director of a poster studio. Read these "
    "reference photographs and specify the identity they are reaching for, at "
    "production quality: faithful to the set, free of its snapshot accidents. Every "
    "value is injected verbatim into a production image prompt, so write shootable "
    "instructions, never appreciations. Answer with JSON only."
)
_CRITERIO_POR_DEFECTO = (
    "paleta: three ROLES, not the three most frequent colours — the field the subjects "
    "sit on, the near-neutral the headline is read in, the one saturated accent.",
    "tipografia: the class of face that holds a headline at poster scale — name the "
    "class, its weight, width and CASE; never a neutral UI sans, never caps by default.",
    "tono_visual: a repeatable lighting recipe that leaves quiet areas for the type.",
)
_PROHIBICIONES_POR_DEFECTO = (
    "empty adjectives: beautiful, modern, stunning, vibrant, sleek.",
    "licensed font names and real brand names: describe the class, never the trademark.",
    "anything you cannot point to in the photographs.",
)


def _vinetas(valor, por_defecto) -> str:
    items = valor if isinstance(valor, list) else list(por_defecto)
    return "\n".join(f"- {str(x).strip()}" for x in items if str(x).strip())


def _sistema(cfg_ex: dict) -> str:
    """Encuadre creativo (JSON) + reglas duras (app).

    El orden importa: primero con qué ojo se mira, después qué es innegociable. Las
    reglas van al final porque son lo último que lee el modelo antes de responder.
    """
    bloques = [cfg_ex.get("sistema") or _SISTEMA_POR_DEFECTO]
    criterio = _vinetas(cfg_ex.get("criterio"), _CRITERIO_POR_DEFECTO)
    if criterio:
        bloques.append(f"HOW TO READ THE SET, FIELD BY FIELD:\n{criterio}")
    prohibiciones = _vinetas(cfg_ex.get("prohibiciones"), _PROHIBICIONES_POR_DEFECTO)
    if prohibiciones:
        bloques.append(f"NEVER:\n{prohibiciones}")
    bloques += [_reglas_esquema(), _reglas_diseno()]
    return "\n\n".join(bloques)


def _mensaje(cfg_ex: dict, n: int) -> str:
    plantilla = (cfg_ex.get("instruccion") or
                 "Extract the visual identity shared by these {n} reference photographs "
                 "of one brand, and specify it at production quality.")
    return plantilla.format(n=n)


def _mensaje_reintento(cfg_ex: dict, errores: list[str], previo: dict) -> str:
    plantilla = (cfg_ex.get("reintento") or
                 "The JSON you returned did not pass review:\n{errores}\n\n"
                 "This is what you returned:\n{previo}\n\nFix exactly those problems, "
                 "change nothing else, and return the complete corrected JSON object.")
    return plantilla.format(
        errores="\n".join(f"- {e}" for e in errores),
        previo=json.dumps(previo, ensure_ascii=False, indent=1),
    )


# ── Extracción ────────────────────────────────────────────────────────────────

def disponible(cfg) -> bool:
    """True si se puede extraer ahora mismo (hay modelo de visión y hay Pillow)."""
    return _HAY_PIL and llm_json.vision_disponible(cfg)


def extraer(fotos: list[tuple[bytes, str]], *, cfg) -> ResultadoExtraccion:
    """`[(bytes, nombre)]` → una identidad válida contra el esquema.

    Síncrona como el resto de `llm_json`: quien llama la despacha a un hilo.
    Lanza `FotosInvalidas` (sin gastar nada), `ExtraccionNoDisponible` o
    `ExtraccionInvalida` si el modelo no acierta ni con el reintento. Un reparo de
    **diseño** que sobreviva al reintento no lanza: vuelve en `avisos`.
    """
    errores_fotos = revisar_fotos(fotos)
    if errores_fotos:
        raise FotosInvalidas(" ".join(errores_fotos))
    if not _HAY_PIL:
        raise ExtraccionNoDisponible(
            "Falta Pillow en el servidor (pip install -r api/requirements.txt)."
        )
    if not llm_json.vision_disponible(cfg):
        raise ExtraccionNoDisponible(
            "La extracción necesita un modelo que lea imágenes: configura ANTHROPIC_API_KEY "
            "o PERPLEXITY_API_KEY en el .env de la raíz del repo."
        )

    cfg_ex = prompt_config.identity_extract()
    imagenes = [_preparar(datos) for datos, _ in fotos]
    sistema = _sistema(cfg_ex)
    mensaje = _mensaje(cfg_ex, len(fotos))
    max_tokens = int(cfg_ex.get("modelo_max_tokens") or 1500)

    usos: list[dict] = []
    avisos: list[str] = []
    ultimos: list[str] = []
    # Red de seguridad: la última identidad que sí cumplió el esquema aunque arrastre
    # un reparo de diseño. Sin esto, un segundo intento peor que el primero (válido de
    # diseño pero roto de esquema) tiraría una identidad perfectamente utilizable.
    mejor: dict | None = None
    reparos_mejor: list[str] = []

    for intento in range(1, _INTENTOS + 1):
        data, uso = llm_json.complete_json_vision_multi(
            sistema, mensaje, imagenes, cfg=cfg, max_tokens=max_tokens)
        if uso:
            usos.append(uso)
        identidad = vi.normalizar(data)
        errores = vi.validar(identidad)
        # Los reparos de diseño solo se miran sobre una identidad que ya valida: sobre
        # una paleta rota dirían lo mismo dos veces y con peores palabras.
        reparos = vi.revisar_diseno(identidad) if not errores else []
        if not errores:
            if not reparos:
                return ResultadoExtraccion(identidad=identidad, usos=usos, avisos=avisos,
                                           intentos=intento)
            mejor, reparos_mejor = identidad, reparos

        ultimos = errores or reparos
        if intento < _INTENTOS:
            motivo = "no validó" if errores else "no cumplía el criterio de diseño"
            avisos.append(f"La primera extracción {motivo} ({'; '.join(ultimos)}); se reintentó.")
            mensaje = _mensaje_reintento(cfg_ex, ultimos, data if isinstance(data, dict) else {})

    if mejor is not None:
        # Válida contra el esquema, con un reparo de diseño que el modelo no resolvió.
        # Se devuelve avisando en vez de fallar: el editor está a la vista y cambiar un
        # hex ahí es más rápido —y más barato— que otra llamada al modelo.
        avisos.append(
            "El modelo no resolvió este reparo de diseño: " + "; ".join(reparos_mejor) +
            ". Corrígelo en el editor antes de guardar: la pieza de respaldo se dibuja "
            "con estos colores."
        )
        return ResultadoExtraccion(identidad=mejor, usos=usos, avisos=avisos,
                                   intentos=_INTENTOS)

    raise ExtraccionInvalida(
        "El modelo no consiguió describir la identidad con el formato esperado. "
        "Vuelve a intentarlo, o prueba con fotos más parecidas entre sí.",
        ultimos,
    )
