"""Extracción de una identidad visual a partir de fotos de referencia.

Recibe de 5 a 10 fotos y devuelve un JSON que cumple **exactamente** el esquema de
`visual_identity` — el mismo que gobierna `prompts/brand.json`. Sin campos nuevos: lo
que sale de aquí tiene que poder entrar en el pipeline sin que nadie lo traduzca.

Tres decisiones que vale la pena no deshacer:

- **Las fotos se revisan ANTES de llamar al modelo.** Cantidad, formato y peso se
  comprueban con `revisar_fotos`, que es pura y no toca la red: subir 4 u 11 fotos
  tiene que costar cero y decirlo claro, no gastar una llamada para enterarse.
- **Las reglas del esquema las escribe la app, no el archivo de prompt.** Viven en
  `_reglas_esquema`, generadas desde las constantes de `visual_identity`, porque son el
  mismo contrato que aplica el validador: escritas dos veces se desincronizan en cuanto
  alguien mueva un límite. El JSON de prompt aporta el encuadre creativo, igual que en
  `prompt_architect` la app escribe las secciones que no se delegan.
- **Un solo reintento, con los errores del validador como feedback.** Si el segundo
  intento tampoco valida se falla limpio: nada a medias entra a la base, y las fotos
  siguen en el navegador para volver a intentarlo sin re-subirlas.

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
        "(weight, width, case, tracking), never a specific licensed font name.",
        "5. `tono_visual`: the photographic treatment ONLY — light, contrast, depth, "
        "texture, surround. Do NOT name any colour here: the palette already has its "
        "own fields and two palettes in one prompt fight each other.",
        f"6. `referencias`: {vi.MIN_REFERENCIAS}-{vi.MAX_REFERENCIAS} short "
        "art-direction references, at most "
        f"{vi.MAX_REFERENCIA} characters each.",
        '7. `aspect_ratio`: "width:height" — use "4:5" unless the set is clearly '
        "another format.",
        f"8. Every text field is at most {vi.MAX_TEXTO} characters.",
        "9. Write every value in English: these strings are injected verbatim into an "
        "image-generation prompt that is written in English.",
    ])


def _sistema(cfg_ex: dict) -> str:
    encuadre = (cfg_ex.get("sistema") or
                "You are a brand art director. Name the visual identity that these "
                "reference photographs already share. Answer with JSON only.")
    return f"{encuadre}\n\n{_reglas_esquema()}"


def _mensaje(cfg_ex: dict, n: int) -> str:
    plantilla = (cfg_ex.get("instruccion") or
                 "Extract the visual identity shared by these {n} reference photographs "
                 "of one brand.")
    return plantilla.format(n=n)


def _mensaje_reintento(cfg_ex: dict, errores: list[str], previo: dict) -> str:
    plantilla = (cfg_ex.get("reintento") or
                 "The JSON you returned was rejected by the validator:\n{errores}\n\n"
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
    `ExtraccionInvalida` si el modelo no acierta ni con el reintento.
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

    for intento in range(1, _INTENTOS + 1):
        data, uso = llm_json.complete_json_vision_multi(
            sistema, mensaje, imagenes, cfg=cfg, max_tokens=max_tokens)
        if uso:
            usos.append(uso)
        identidad = vi.normalizar(data)
        errores = vi.validar(identidad)
        if not errores:
            return ResultadoExtraccion(identidad=identidad, usos=usos, avisos=avisos,
                                       intentos=intento)
        ultimos = errores
        if intento < _INTENTOS:
            avisos.append(f"La primera extracción no validó ({'; '.join(errores)}); se reintentó.")
            mensaje = _mensaje_reintento(cfg_ex, errores, data if isinstance(data, dict) else {})

    raise ExtraccionInvalida(
        "El modelo no consiguió describir la identidad con el formato esperado. "
        "Vuelve a intentarlo, o prueba con fotos más parecidas entre sí.",
        ultimos,
    )
