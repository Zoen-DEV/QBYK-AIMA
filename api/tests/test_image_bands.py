"""Detector de bandas planas y marcos (`image_overlay.bordes_planos`).

El passe-partout y el letterbox se atacan en tres frentes; este es el único que no es
prompt, y por eso existe: el prompt ya falló dos veces. Las imágenes son sintéticas y
se generan con Pillow — sin red, sin proveedor y sin modelo.

El test que de verdad protege el detector es el de la **escena nocturna legítima**: una
banda alta oscura y de baja varianza es correcta —es justo el aire donde se apoya el
titular— y no puede dar positivo. Lo que delata a la banda pintada no es que sea
oscura, es que termina de golpe: **un letterbox es un escalón**.
"""

import io

import pytest

import job_runner as jr  # noqa: F401 — deja api/scripts en sys.path

ov = pytest.importorskip("image_overlay")
Image = pytest.importorskip("PIL.Image")

_W, _H = 320, 400


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fotografia(w: int = _W, h: int = _H, *, base: int = 40, rango: int = 170) -> Image.Image:
    """Una "foto": degradado vertical CON textura por fila.

    La textura importa: lo que distingue una fotografía de una banda pintada es que
    sus filas tienen varianza. Sin ella, cualquier degradado suave daría plano.
    """
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            v = base + int(rango * y / max(1, h - 1))
            # Trama determinista de ±25 niveles: el grano y el detalle de una foto.
            v += 25 if (x * 7 + y * 3) % 5 < 2 else -25
            v = max(0, min(255, v))
            px[x, y] = (v, v, v)
    return img


def _pintar_banda(img: Image.Image, caja: tuple[int, int, int, int], gris: int) -> Image.Image:
    out = img.copy()
    out.paste(Image.new("RGB", (caja[2] - caja[0], caja[3] - caja[1]), (gris, gris, gris)), caja)
    return out


# ── Los cuatro casos del plan ─────────────────────────────────────────────────

def test_una_foto_a_sangre_no_tiene_bandas():
    assert ov.bordes_planos(_png(_fotografia())) == []


def test_el_letterbox_se_detecta_arriba_y_abajo():
    img = _fotografia()
    img = _pintar_banda(img, (0, 0, _W, 40), 0)
    img = _pintar_banda(img, (0, _H - 40, _W, _H), 0)
    assert ov.bordes_planos(_png(img)) == ["arriba", "abajo"]


def test_un_marco_en_los_cuatro_lados_se_reporta_como_marco():
    img = _fotografia()
    img = _pintar_banda(img, (0, 0, _W, 40), 237)
    img = _pintar_banda(img, (0, _H - 40, _W, _H), 237)
    img = _pintar_banda(img, (0, 0, 32, _H), 237)
    img = _pintar_banda(img, (_W - 32, 0, _W, _H), 237)
    assert ov.bordes_planos(_png(img)) == ["marco"]


def test_una_escena_nocturna_legitima_no_da_positivo():
    """El falso positivo que hay que evitar, y es tan importante como los otros tres.

    Banda alta muy oscura y de baja varianza, pero SIN escalón: la escena se abre
    poco a poco. Es exactamente el aire negativo que el brief pide, hecho de la propia
    fotografía.
    """
    img = Image.new("RGB", (_W, _H))
    px = img.load()
    for y in range(_H):
        for x in range(_W):
            # Muy oscuro arriba, abriéndose progresivamente: sin corte en ninguna fila.
            v = int(2 + 150 * (y / (_H - 1)) ** 2)
            v += 2 if (x * 7 + y * 3) % 5 < 2 else -2   # apenas textura arriba
            px[x, y] = (max(0, min(255, v)),) * 3
    assert ov.bordes_planos(_png(img)) == []


# ── Robustez: esto nunca puede tumbar una generación ──────────────────────────

@pytest.mark.parametrize("basura", [b"", b"no soy una imagen", b"\x89PNG rota"])
def test_una_imagen_ilegible_no_lanza(basura):
    assert ov.bordes_planos(basura) == []


def test_una_banda_de_dos_pixeles_no_cuenta():
    # Un borde de compresión no es un elemento de diseño: por debajo del mínimo, nada.
    img = _pintar_banda(_fotografia(), (0, 0, _W, 3), 0)
    assert "arriba" not in ov.bordes_planos(_png(img))


def test_una_banda_de_color_liso_tambien_cuenta_aunque_no_sea_negra():
    # El caso real: el modelo pintaba el passe-partout con el HUESO de la paleta.
    img = _pintar_banda(_fotografia(), (0, 0, _W, 45), 237)
    assert "arriba" in ov.bordes_planos(_png(img))


def test_una_imagen_plana_entera_no_es_una_banda():
    # Sin escalón no hay banda, por definición: aquí no hay fotografía que enmarcar.
    assert ov.bordes_planos(_png(Image.new("RGB", (_W, _H), (18, 18, 18)))) == []


# ── Enganche en el pipeline (`job_runner._verificar_bandas`) ──────────────────


class _Cfg:
    image_band_qa = True


@pytest.fixture
def imagenes(monkeypatch, tmp_path):
    """`bytes_crudos` sirviendo lo que diga el test para cada src."""
    con_banda = _pintar_banda(_fotografia(), (0, 0, _W, 40), 0)
    limpia = _fotografia()
    fuentes = {"mala.png": _png(con_banda), "buena.png": _png(limpia)}
    monkeypatch.setattr(jr.ov, "bytes_crudos", lambda src: fuentes[src])
    monkeypatch.setattr(jr.improv, "es_plantilla", lambda src: src.startswith("plantilla"))
    return fuentes


def _job() -> dict:
    return {"images": {"bandas": {}}}


async def test_una_imagen_a_sangre_no_gasta_reintentos(imagenes):
    job = _job()
    llamadas = []

    async def _rehacer():
        llamadas.append(1)
        return "buena.png"

    q = jr.asyncio.Queue()
    src = await jr._verificar_bandas(job, q, _Cfg(), subkey="ig-1", src="buena.png",
                                     rehacer=_rehacer)
    assert src == "buena.png"
    assert llamadas == []
    assert job["images"]["bandas"]["ig-1"] == [{"intento": 1, "bordes": []}]


async def test_una_banda_dispara_un_reintento_y_solo_uno(imagenes):
    job = _job()
    llamadas = []

    async def _rehacer():
        llamadas.append(1)
        return "mala.png"          # el reintento tampoco lo arregla

    q = jr.asyncio.Queue()
    src = await jr._verificar_bandas(job, q, _Cfg(), subkey="ig-1", src="mala.png",
                                     rehacer=_rehacer)
    # Un solo reintento: regenerar cuesta créditos y el defecto es binario.
    assert len(llamadas) == 1
    assert src == "mala.png"
    assert [r["bordes"] for r in job["images"]["bandas"]["ig-1"]] == [["arriba"], ["arriba"]]


async def test_el_reintento_que_arregla_la_banda_se_queda(imagenes):
    job = _job()

    async def _rehacer():
        return "buena.png"

    q = jr.asyncio.Queue()
    src = await jr._verificar_bandas(job, q, _Cfg(), subkey="ig-1", src="mala.png",
                                     rehacer=_rehacer)
    assert src == "buena.png"
    assert job["images"]["bandas"]["ig-1"][-1]["bordes"] == []


async def test_una_plantilla_local_no_se_revisa(imagenes):
    # No la pintó ningún modelo: reintentarla daría la misma imagen y el aviso no
    # tendría acción posible detrás.
    job = _job()
    q = jr.asyncio.Queue()
    src = await jr._verificar_bandas(job, q, _Cfg(), subkey="ig-1", src="plantilla-1.png",
                                     rehacer=None)
    assert src == "plantilla-1.png"
    assert job["images"]["bandas"] == {}


async def test_con_el_flag_apagado_no_se_revisa(imagenes):
    class _Off:
        image_band_qa = False

    job = _job()
    q = jr.asyncio.Queue()
    assert await jr._verificar_bandas(job, q, _Off(), subkey="ig-1", src="mala.png",
                                      rehacer=None) == "mala.png"
    assert job["images"]["bandas"] == {}


async def test_un_fallo_al_descargar_no_interrumpe(monkeypatch):
    def _explota(src):
        raise OSError("timeout")

    monkeypatch.setattr(jr.ov, "bytes_crudos", _explota)
    monkeypatch.setattr(jr.improv, "es_plantilla", lambda src: False)
    job = _job()
    q = jr.asyncio.Queue()
    assert await jr._verificar_bandas(job, q, _Cfg(), subkey="ig-1", src="x.png",
                                      rehacer=None) == "x.png"
