"""Tests de `job_runner._photo_to_vertical` — recorte 9:16 del modo "fotos".

En image-to-video Kling sigue el aspect de las fotos de entrada, así que el
recorrido a partir de fotos horizontales salía letterboxeado; el helper recorta
cada foto al centro a 9:16 antes de subirla.
"""

import io

import pytest

import job_runner

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _img_bytes(w: int, h: int, fmt: str = "WEBP") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, fmt)
    return buf.getvalue()


def _size_of(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def test_horizontal_photo_gets_center_cropped_to_9x16():
    out, fname = job_runner._photo_to_vertical(_img_bytes(800, 600), "01.webp")
    w, h = _size_of(out)
    assert abs(w / h - 9 / 16) < 0.01
    assert h == 600  # solo recorta, no re-escala si no hace falta
    assert fname == "01-9x16.jpg"


def test_portrait_3x4_still_crops_sides():
    # Un 3:4 vertical sigue siendo más ancho que 9:16 → recorta los lados.
    out, _ = job_runner._photo_to_vertical(_img_bytes(600, 800), "02.webp")
    w, h = _size_of(out)
    assert abs(w / h - 9 / 16) < 0.01
    assert h == 800


def test_taller_than_9x16_crops_top_and_bottom():
    out, _ = job_runner._photo_to_vertical(_img_bytes(500, 1000), "03.webp")
    w, h = _size_of(out)
    assert abs(w / h - 9 / 16) < 0.01
    assert w == 500


def test_already_9x16_returns_original_bytes():
    original = _img_bytes(1080, 1920, fmt="JPEG")
    out, fname = job_runner._photo_to_vertical(original, "listo.jpg")
    assert out == original
    assert fname == "listo.jpg"


def test_oversized_photo_downscales_to_1080x1920():
    out, _ = job_runner._photo_to_vertical(_img_bytes(4000, 3000), "grande.jpg")
    assert _size_of(out) == (1080, 1920)


def test_unreadable_bytes_fall_back_to_original():
    out, fname = job_runner._photo_to_vertical(b"no soy una imagen", "raro.bin")
    assert out == b"no soy una imagen"
    assert fname == "raro.bin"
