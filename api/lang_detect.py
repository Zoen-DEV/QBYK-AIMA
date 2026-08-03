"""Detección del idioma (es/en) del contenido de origen.

Fuente única para los DOS flujos: `run_pipeline` la usa para fijar `params["lang"]`,
que gobierna el idioma de los posts, del texto de los visuales y de la voz en off.

Orden de señales, de la más fiable a la más débil:

1. `idioma` forzado en el form / el sheet.
2. Idioma del track de subtítulos que realmente se descargó (`transcript_lang`).
3. Idioma declarado del video en los metadatos de YouTube (`source_lang`).
4. Heurística sobre el texto: frecuencia de palabras funcionales exclusivas de cada
   idioma + signos propios del español.

Las señales 2 y 3 son metadatos y a veces mienten (canales que declaran mal el
idioma, tracks subidos con la etiqueta equivocada), así que la heurística puede
**vetarlas** cuando el texto es largo y marca lo contrario con margen amplio.
"""

import re

SUPPORTED = ("es", "en")
DEFAULT_LANG = "es"

# Códigos ISO-639-2 que devuelven algunas fuentes ("spa", "eng").
_ALIASES = {"spa": "es", "cas": "es", "eng": "en"}

# Palabras funcionales EXCLUSIVAS de cada idioma. Las ambiguas quedan fuera a
# propósito: "a", "no", "me", "son", "he", "en"(en)/"in"(es)… aparecen en los dos
# y solo agregan ruido. Lo que decide es la frecuencia, no la presencia: un texto
# en inglés con una cita en español no cambia el resultado.
_ES_MARKERS = frozenset("""
que de la el los las en y un una unos unas por con para se del al es son está están
era eran pero como esto esta esos esas muy cuando porque todo todos hay ser hacer
tiene tienen vamos entonces también así sobre desde hasta nos te mi tu su sus lo le
les yo nosotros ustedes usted más bien aquí ahí ahora siempre nunca cada donde quien
cual algo alguien nada nadie mismo mientras aunque además luego ya sino
""".split())

_EN_MARKERS = frozenset("""
the and of to in is it its that this these those for with you your yours we they them
their there are was were be been being but not have has had having can could will
would should what when where which while who whom about from into than then does did
doing get got going just like really something someone anything nothing because our
she his her him don't didn't it's that's you're we're they're i'm
""".split())

# Signos y letras que en la práctica solo aparecen en español (los préstamos del
# inglés tipo "café" son raros y el bonus está topeado, así que no deciden solos).
_ES_CHARS_RE = re.compile(r"[ñ¿¡]|[áéíóúü]")

_TOKEN_RE = re.compile(r"[^\W\d_]+(?:'[a-z]+)?", re.UNICODE)

# Umbrales del veto de la heurística sobre los metadatos: hace falta texto
# suficiente Y un margen amplio para contradecir un idioma declarado.
_VETO_MIN_HITS = 25
_VETO_MIN_MARGIN = 0.35

# Muestra de transcripción que mira la heurística. Suficiente para que la intro
# (música, sponsor, saludo) no defina sola el idioma del video.
_SAMPLE_CHARS = 8000


def normalize_lang(code) -> str | None:
    """Normaliza un código de idioma a "es"/"en" (o None si no es ninguno).

    Acepta las variantes regionales que devuelven YouTube y youtube-transcript-api
    ("es-419", "es-ES", "en-US") y los códigos de tres letras ("spa", "eng").
    """
    if not code:
        return None
    base = str(code).strip().lower().replace("_", "-").split("-")[0]
    base = _ALIASES.get(base, base)
    return base if base in SUPPORTED else None


def score_text(text: str) -> tuple[int, int]:
    """(aciertos_es, aciertos_en) por frecuencia de palabras funcionales."""
    tokens = _TOKEN_RE.findall((text or "").lower())
    es = sum(1 for t in tokens if t in _ES_MARKERS)
    en = sum(1 for t in tokens if t in _EN_MARKERS)
    # Bonus por signos exclusivos del español, topeado para que un texto largo en
    # inglés con algún acento suelto no se dé vuelta.
    es += min(len(_ES_CHARS_RE.findall(text or "")), max(1, len(tokens) // 20))
    return es, en


def classify(text: str) -> tuple[str | None, float, int]:
    """Clasifica un texto: (idioma | None, margen 0..1, aciertos totales).

    `None` cuando no hay señal suficiente (texto muy corto o sin palabras
    funcionales reconocibles) — el llamador decide con qué respaldarse.
    """
    es, en = score_text(text)
    total = es + en
    if total < 3:
        return None, 0.0, total
    lang = "es" if es > en else "en" if en > es else None
    if lang is None:
        return None, 0.0, total
    return lang, abs(es - en) / total, total


def detect_lang(text: str, *, default: str = DEFAULT_LANG) -> str:
    """Idioma del texto; `default` cuando no hay señal suficiente."""
    lang, _, _ = classify(text)
    return lang or default


def _sample(content: dict) -> str:
    """Texto que mira la heurística: transcripción + título/descripción de refuerzo.

    El título y la descripción se agregan siempre: en un video sin subtítulos son
    la única señal, y cuando hay transcripción larga su peso es marginal.
    """
    transcript = (content.get("transcript") or "")[:_SAMPLE_CHARS]
    extra = " ".join([content.get("title") or "", (content.get("description") or "")[:500]])
    return f"{transcript}\n{extra}".strip()


def resolve_lang(forced, content: dict) -> tuple[str, str]:
    """Idioma final del job y la señal que lo decidió: `(lang, fuente)`.

    `forced` es `params["idioma"]` ("auto" | "es" | "en"); `content` es el dict del
    paso de extracción (`transcript`, `title`, `description` y, en YouTube,
    `transcript_lang` / `source_lang`). La `fuente` va al SSE para que el usuario
    vea POR QUÉ se eligió ese idioma (y pueda forzarlo si no le cierra).
    """
    forced_lang = normalize_lang(forced)
    if forced_lang:
        return forced_lang, "forzado"

    guess, margin, hits = classify(_sample(content))
    confident = guess is not None and hits >= _VETO_MIN_HITS and margin >= _VETO_MIN_MARGIN

    for code, source in ((content.get("transcript_lang"), "subtítulos"),
                         (content.get("source_lang"), "metadatos")):
        meta = normalize_lang(code)
        if not meta:
            continue
        if confident and guess != meta:
            # El texto que va a leer el LLM manda sobre la etiqueta: un track mal
            # declarado (o unos metadatos del canal) no puede hacer que el post
            # salga en un idioma que la fuente no habla.
            return guess, f"texto (los {source} decían {meta})"
        return meta, source

    if guess:
        return guess, "texto"
    return DEFAULT_LANG, "default"
