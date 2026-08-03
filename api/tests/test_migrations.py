"""Tests del runner de migraciones y de la 001.

Lo que hay que poder demostrar sin una base delante: que `up` crea lo que dice y es
idempotente, que `down` deja la base **como estaba antes de la feature**, y que el
runner lleva bien la cuenta en `_migrations`.
"""

import db
import visual_identity as vi
from migrations import run as runner

_M001 = "001"

# El doble de la base (`dbase`) vive en conftest.py.


def _m001():
    return next(m for m in runner._modulos() if m.VERSION == _M001)


# ── La migración 001 ──────────────────────────────────────────────────────────

def test_el_runner_descubre_la_001():
    versiones = [m.VERSION for m in runner._modulos()]
    assert _M001 in versiones
    assert versiones == sorted(versiones)  # se aplican en orden


async def test_up_crea_la_coleccion_con_sus_indices(dbase):
    await _m001().up(dbase)
    assert db.IDENTITIES_COLLECTION in await dbase.list_collection_names()
    assert dbase[db.IDENTITIES_COLLECTION].indices == db._INDEXES[db.IDENTITIES_COLLECTION]


async def test_up_es_idempotente(dbase):
    """No es transaccional, así que tiene que aguantar que se corra dos veces."""
    mod = _m001()
    await mod.up(dbase)
    await mod.up(dbase)
    assert await dbase.list_collection_names() == [db.IDENTITIES_COLLECTION]


async def test_up_no_siembra_la_identidad_de_la_casa(dbase):
    """La system se sirve desde brand.json; copiarla aquí crearía dos fuentes y drift."""
    await _m001().up(dbase)
    assert await dbase[db.IDENTITIES_COLLECTION].count_documents({}) == 0


async def test_down_tira_la_coleccion(dbase):
    mod = _m001()
    await mod.up(dbase)
    await dbase[db.IDENTITIES_COLLECTION].insert_one({"_id": "x", "user_id": "qbyk"})
    detalle = await mod.down(dbase)
    assert db.IDENTITIES_COLLECTION not in await dbase.list_collection_names()
    assert "1 identidades borradas" in detalle


async def test_down_sobre_una_base_limpia_no_revienta(dbase):
    assert "no existía" in await _m001().down(dbase)


def test_revertir_no_toca_el_look_de_la_casa():
    """Revertir borra las identidades de los usuarios, no `brand.json`: la app vuelve
    exactamente al estado anterior a la feature."""
    assert vi.validar(vi.identidad_system()) == []


# ── El runner ─────────────────────────────────────────────────────────────────

async def test_up_registra_la_version_y_down_la_retira(dbase):
    await runner._up(dbase)
    aplicadas = await runner._aplicadas(dbase)
    assert _M001 in aplicadas
    assert aplicadas[_M001]["description"] == _m001().DESCRIPCION

    await runner._down(dbase)
    assert await runner._aplicadas(dbase) == {}
    assert db.IDENTITIES_COLLECTION not in await dbase.list_collection_names()


async def test_up_dos_veces_no_reaplica(dbase, capsys):
    await runner._up(dbase)
    capsys.readouterr()
    await runner._up(dbase)
    assert "Nada que aplicar" in capsys.readouterr().out


async def test_down_de_una_version_inexistente_falla_limpio(dbase, capsys):
    await runner._up(dbase)
    assert await runner._down(dbase, "999") == 1
    # La 001 sigue aplicada: un `down` equivocado no revierte otra cosa.
    assert _M001 in await runner._aplicadas(dbase)


async def test_status_no_modifica_nada(dbase):
    await runner._up(dbase)
    antes = await dbase.list_collection_names()
    assert await runner._status(dbase) == 0
    assert await dbase.list_collection_names() == antes
