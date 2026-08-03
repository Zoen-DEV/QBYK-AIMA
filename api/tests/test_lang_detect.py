"""Tests de la detección de idioma (es/en).

El caso que motivó el módulo: videos de YouTube en inglés que salían detectados
como español (la heurística vieja miraba 500 caracteres, contaba presencia en vez
de frecuencia y empataba a favor del español).
"""

import lang_detect as ld

_EN = (
    "So the first thing that you have to understand about this is that the market "
    "does not care about what you think it should do. I have been trading for ten "
    "years and the one thing that keeps coming back is this: you are going to be "
    "wrong, and that is fine. What matters is what you do when you are wrong. "
    "We are going to break this down into three parts, and then we will look at "
    "some of the numbers from last year."
)

_ES = (
    "Lo primero que tenés que entender de esto es que el mercado no le importa lo "
    "que vos pensás que debería hacer. Llevo diez años operando y lo que siempre "
    "vuelve es esto: te vas a equivocar, y está bien. Lo que importa es qué hacés "
    "cuando te equivocás. Vamos a dividirlo en tres partes y después miramos "
    "algunos de los números del año pasado."
)


# ── normalize_lang ────────────────────────────────────────────────────────────

def test_normalize_lang_regional_variants():
    assert ld.normalize_lang("es-419") == "es"
    assert ld.normalize_lang("es-ES") == "es"
    assert ld.normalize_lang("en-US") == "en"
    assert ld.normalize_lang("EN_GB") == "en"


def test_normalize_lang_three_letter_and_unknown():
    assert ld.normalize_lang("spa") == "es"
    assert ld.normalize_lang("eng") == "en"
    assert ld.normalize_lang("pt-BR") is None
    assert ld.normalize_lang("") is None
    assert ld.normalize_lang(None) is None


# ── Heurística de texto ───────────────────────────────────────────────────────

def test_detect_lang_english_transcript():
    assert ld.detect_lang(_EN) == "en"


def test_detect_lang_spanish_transcript():
    assert ld.detect_lang(_ES) == "es"


def test_detect_lang_english_with_spanish_quote():
    # Una cita en español no da vuelta un texto en inglés: pesa la frecuencia.
    assert ld.detect_lang(_EN + ' He said "la vida es así" and moved on.') == "en"


def test_detect_lang_short_text_falls_back_to_default():
    assert ld.detect_lang("Trading 101", default="es") == "es"
    assert ld.detect_lang("Trading 101", default="en") == "en"


def test_detect_lang_english_title_only():
    # Sin transcripción, un título con palabras funcionales alcanza.
    assert ld.detect_lang("How to build a business that works when you are not there") == "en"


# ── resolve_lang: precedencia de señales ──────────────────────────────────────

def test_resolve_lang_forced_wins():
    content = {"transcript": _EN, "transcript_lang": "en"}
    assert ld.resolve_lang("es", content) == ("es", "forzado")


def test_resolve_lang_uses_transcript_track():
    # Texto corto (sin señal propia) → manda la etiqueta del track descargado.
    content = {"transcript": "Ok.", "title": "Demo", "transcript_lang": "en-US"}
    lang, source = ld.resolve_lang("auto", content)
    assert (lang, source) == ("en", "subtítulos")


def test_resolve_lang_falls_back_to_video_metadata():
    content = {"transcript": "Ok.", "title": "Demo", "source_lang": "en"}
    lang, source = ld.resolve_lang("auto", content)
    assert (lang, source) == ("en", "metadatos")


def test_resolve_lang_text_vetoes_wrong_metadata():
    # El bug reportado: video en inglés con metadatos que decían "es".
    content = {"transcript": _EN * 2, "source_lang": "es", "transcript_lang": "es"}
    lang, source = ld.resolve_lang("auto", content)
    assert lang == "en"
    assert "texto" in source


def test_resolve_lang_text_only():
    lang, source = ld.resolve_lang("auto", {"transcript": _EN})
    assert (lang, source) == ("en", "texto")


def test_resolve_lang_defaults_to_spanish_without_signal():
    assert ld.resolve_lang("auto", {"transcript": "", "title": ""}) == ("es", "default")


def test_resolve_lang_audio_transcript_spanish():
    # Nota de voz: no hay metadatos, solo el texto transcrito.
    lang, source = ld.resolve_lang("auto", {"transcript": _ES, "title": "Nota de voz"})
    assert (lang, source) == ("es", "texto")
