"""Elección del track de subtítulos de YouTube (`_pick_transcript`).

Regla: se descarga el track en el idioma ORIGINAL del video. La lista de
preferencias vieja (`["es", "es-419", "es-ES", "en", ...]`) bajaba los subtítulos
en español de un video en inglés, y con eso todo el post salía en español.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import blotato_client as bc


class _Track:
    def __init__(self, code: str, generated: bool):
        self.language_code = code
        self.is_generated = generated

    def __repr__(self) -> str:  # solo para que falle legible
        return f"<{self.language_code}{'/auto' if self.is_generated else '/manual'}>"


def test_prefers_video_language_over_listed_order():
    tracks = [_Track("es", False), _Track("en", True)]
    assert bc._pick_transcript(tracks, "en").language_code == "en"


def test_prefers_manual_over_generated_in_the_same_language():
    tracks = [_Track("en", True), _Track("en", False), _Track("es", False)]
    picked = bc._pick_transcript(tracks, "en-US")
    assert (picked.language_code, picked.is_generated) == ("en", False)


def test_regional_variant_counts_as_the_video_language():
    tracks = [_Track("es-419", False), _Track("en-GB", True)]
    assert bc._pick_transcript(tracks, "en").language_code == "en-GB"


def test_without_metadata_the_generated_track_marks_the_original_language():
    # Sin `language` en los metadatos: YouTube solo autogenera en el idioma
    # hablado, así que ese track define el idioma original (y gana el manual de
    # ese mismo idioma si existe).
    tracks = [_Track("es", False), _Track("en", True), _Track("en", False)]
    picked = bc._pick_transcript(tracks, "")
    assert (picked.language_code, picked.is_generated) == ("en", False)


def test_falls_back_to_any_track_when_the_language_is_absent():
    tracks = [_Track("pt", False), _Track("fr", False)]
    assert bc._pick_transcript(tracks, "en") in tracks
