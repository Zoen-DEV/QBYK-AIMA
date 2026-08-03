"""Tests del store de identidades visuales.

Las reglas que se blindan aquí son las tres que el usuario nota si se rompen: la
identidad de la casa no se puede borrar ni editar, solo una identidad puede estar
activa a la vez, y la generación nunca se queda sin identidad —pase lo que pase con
Mongo, `activa()` devuelve la system y el post sale con el look de siempre.
"""

import pytest

import db
import identity_store as store
import visual_identity as vi

_JSON = {
    "paleta": ["#101014", "#F2EFE6", "#FF5C2B"],
    "paleta_nombres": ["ink", "paper", "ember"],
    "color_texto": "paper (#F2EFE6) over the ink",
    "color_acento": "ember (#FF5C2B)",
    "tipografia": "condensed heavy grotesque, ALL CAPS",
    "tipografia_secundaria": "same face, bold, tracking opened",
    "tono_visual": "sunlit still life, hard shadows, warm bounce",
    "aspect_ratio": "4:5",
    "referencias": ["editorial food photography"],
}

_YO = "qbyk"
_OTRO = "cliente-1"


# Los dobles de Mongo (`identidades`, `sin_base`) viven en conftest.py.


async def _crear(nombre="Mía", user=_YO, **kw) -> dict:
    return await store.crear(user, name=nombre, identity_json=_JSON, **kw)


# ── Degradación sin base ──────────────────────────────────────────────────────

async def test_sin_base_solo_existe_la_identidad_de_la_casa(sin_base):
    filas = await store.listar(_YO)
    assert [f["id"] for f in filas] == [vi.SYSTEM_ID]
    assert filas[0]["is_default"] is True


async def test_sin_base_crear_falla_alto(sin_base):
    """Perder un evento de costo es aceptable; perder la identidad que el usuario
    acaba de crear, no."""
    with pytest.raises(store.AlmacenNoDisponible):
        await _crear()


async def test_activa_nunca_lanza_aunque_mongo_reviente(monkeypatch):
    async def _boom():
        raise RuntimeError("mongo caído")

    monkeypatch.setattr(db, "get_identities", _boom)
    fila = await store.activa(_YO)
    assert fila["id"] == vi.SYSTEM_ID and fila["is_system"] is True


async def test_activa_sin_ninguna_marcada_devuelve_la_de_la_casa(identidades):
    await _crear()
    assert (await store.activa(_YO))["id"] == vi.SYSTEM_ID


# ── Crear ─────────────────────────────────────────────────────────────────────

async def test_crear_valida_el_json_antes_de_escribir(identidades):
    with pytest.raises(vi.IdentidadInvalida):
        await store.crear(_YO, name="Rota", identity_json={**_JSON, "aspect_ratio": "nope"})
    assert identidades.docs == []


async def test_crear_sugiere_el_nombre_si_va_vacio(identidades):
    fila = await _crear(nombre="   ")
    assert fila["name"] == "Ember · ink"


async def test_crear_no_marca_activa_por_defecto(identidades):
    fila = await _crear()
    assert fila["is_default"] is False
    assert fila["is_system"] is False
    assert fila["user_id"] == _YO


async def test_crear_puede_activar_de_una(identidades):
    fila = await _crear(activar_al_crear=True)
    assert fila["is_default"] is True
    assert (await store.activa(_YO))["id"] == fila["id"]


# ── Listar ────────────────────────────────────────────────────────────────────

async def test_listar_pone_la_de_la_casa_primero(identidades):
    await _crear("Una")
    await _crear("Otra")
    filas = await store.listar(_YO)
    assert filas[0]["id"] == vi.SYSTEM_ID
    assert [f["name"] for f in filas[1:]] == ["Una", "Otra"]


async def test_listar_no_muestra_las_de_otro_usuario(identidades):
    await _crear("Ajena", user=_OTRO)
    assert [f["id"] for f in await store.listar(_YO)] == [vi.SYSTEM_ID]


# ── La identidad de la casa ───────────────────────────────────────────────────

async def test_la_identidad_de_la_casa_no_se_elimina(identidades):
    with pytest.raises(store.NoEditable):
        await store.eliminar(_YO, vi.SYSTEM_ID)


async def test_la_identidad_de_la_casa_no_se_edita(identidades):
    with pytest.raises(store.NoEditable):
        await store.actualizar(_YO, vi.SYSTEM_ID, name="Otro nombre")


async def test_la_identidad_de_la_casa_si_se_puede_activar(identidades):
    mia = await _crear(activar_al_crear=True)
    fila = await store.activar(_YO, vi.SYSTEM_ID)
    assert fila["id"] == vi.SYSTEM_ID and fila["is_default"] is True
    # Activar la de la casa es desmarcar todo: ninguna fila queda activa.
    assert (await store.obtener(_YO, mia["id"]))["is_default"] is False


# ── Activa ────────────────────────────────────────────────────────────────────

async def test_solo_una_identidad_activa_a_la_vez(identidades):
    a = await _crear("A", activar_al_crear=True)
    b = await _crear("B", activar_al_crear=True)
    activas = [f["id"] for f in await store.listar(_YO) if f["is_default"]]
    assert activas == [b["id"]]
    assert (await store.obtener(_YO, a["id"]))["is_default"] is False


async def test_al_borrar_la_activa_vuelve_la_de_la_casa(identidades):
    fila = await _crear(activar_al_crear=True)
    await store.eliminar(_YO, fila["id"])
    assert (await store.activa(_YO))["id"] == vi.SYSTEM_ID


async def test_activar_una_identidad_ajena_no_encuentra_nada(identidades):
    ajena = await _crear("Ajena", user=_OTRO)
    with pytest.raises(store.NoEncontrada):
        await store.activar(_YO, ajena["id"])


# ── Editar ────────────────────────────────────────────────────────────────────

async def test_renombrar_no_toca_el_json(identidades):
    fila = await _crear("Vieja")
    nueva = await store.actualizar(_YO, fila["id"], name="Nueva")
    assert nueva["name"] == "Nueva"
    assert nueva["identity_json"] == fila["identity_json"]


async def test_editar_el_json_lo_valida(identidades):
    fila = await _crear()
    with pytest.raises(vi.IdentidadInvalida):
        await store.actualizar(_YO, fila["id"],
                               identity_json={**_JSON, "color_acento": "sin hex"})
    assert (await store.obtener(_YO, fila["id"]))["identity_json"] == fila["identity_json"]


async def test_editar_el_json_lo_guarda_normalizado(identidades):
    fila = await _crear()
    nueva = await store.actualizar(
        _YO, fila["id"], identity_json={**_JSON, "paleta": ["101014", "#F2EFE6", "#FF5C2B"]})
    assert nueva["identity_json"]["paleta"][0] == "#101014"


async def test_un_nombre_vacio_al_renombrar_se_rechaza(identidades):
    fila = await _crear()
    with pytest.raises(ValueError):
        await store.actualizar(_YO, fila["id"], name="  ")


async def test_no_se_puede_tocar_la_identidad_de_otro_usuario(identidades):
    ajena = await _crear("Ajena", user=_OTRO)
    with pytest.raises(store.NoEncontrada):
        await store.actualizar(_YO, ajena["id"], name="Secuestrada")
    with pytest.raises(store.NoEncontrada):
        await store.eliminar(_YO, ajena["id"])
    assert len(identidades.docs) == 1
