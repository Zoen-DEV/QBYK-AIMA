"""Grade común del carrusel: iguala el color de cada slide al de la portada.

Es la red de seguridad estética (gratis, sin créditos) por si el modelo deriva en
exposición o temperatura aunque comparta dirección de arte y referencia visual. Los
topes son la parte importante: esto corrige una deriva, no reinterpreta la imagen.
"""
import io

import pytest

import job_runner as jr  # deja api/scripts en sys.path

ov = pytest.importorskip("image_overlay")
Image = pytest.importorskip("PIL.Image")
ImageStat = pytest.importorskip("PIL.ImageStat")


def _png(color, size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _noisy_png(base, size=(64, 64)) -> bytes:
    """Imagen con textura (desviación > 0) alrededor de un color base."""
    img = Image.new("RGB", size, base)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            d = ((x * 7 + y * 13) % 61) - 30
            px[x, y] = tuple(max(0, min(255, c + d)) for c in base)
    return _to_bytes(img)


def _to_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mean(png: bytes) -> list[float]:
    return ImageStat.Stat(Image.open(io.BytesIO(png)).convert("RGB")).mean


def test_acerca_el_slide_a_la_portada():
    slide = _noisy_png((120, 120, 120))
    cover = _noisy_png((132, 126, 118))   # portada algo más clara y cálida
    out = _mean(ov.match_grade(slide, cover))
    before, target = _mean(slide), _mean(cover)
    for c in range(3):
        assert abs(out[c] - target[c]) < abs(before[c] - target[c])


def test_la_correccion_esta_acotada():
    # Una portada radicalmente distinta no puede repintar el slide: como mucho se
    # mueve el tope (18 niveles de 255). Un slide legítimamente oscuro sigue oscuro.
    slide = _noisy_png((40, 40, 40))
    cover = _noisy_png((230, 230, 230))
    out, before = _mean(ov.match_grade(slide, cover)), _mean(slide)
    for c in range(3):
        assert out[c] - before[c] <= ov._GRADE_MAX_SHIFT + 1


def test_igualar_contra_si_mismo_no_cambia_nada():
    slide = _noisy_png((100, 110, 120))
    out, before = _mean(ov.match_grade(slide, slide)), _mean(slide)
    assert all(abs(out[c] - before[c]) < 0.5 for c in range(3))


def test_una_imagen_plana_no_rompe_el_calculo():
    # stddev = 0 en los tres canales: no hay contraste que igualar, solo desplazar.
    out = ov.match_grade(_png((10, 10, 10)), _png((200, 200, 200)))
    assert _mean(out)[0] > 10


# ── Integración con el pipeline (best-effort: nunca interrumpe) ───────────────

class _Cfg:
    image_grade_match = True


async def test_sin_portada_devuelve_el_slide_intacto():
    slide = _noisy_png((100, 100, 100))
    assert await jr._match_cover_grade(slide, None, _Cfg()) == slide


async def test_el_flag_apagado_no_toca_el_slide():
    class Off:
        image_grade_match = False

    slide = _noisy_png((100, 100, 100))
    assert await jr._match_cover_grade(slide, _noisy_png((200, 200, 200)), Off()) == slide


async def test_un_fallo_del_grade_no_rompe_la_generacion(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("pillow se cayó")

    monkeypatch.setattr(ov, "match_grade", boom)
    slide = _noisy_png((100, 100, 100))
    assert await jr._match_cover_grade(slide, _noisy_png((150, 150, 150)), _Cfg()) == slide
