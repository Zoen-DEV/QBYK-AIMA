"""Tests del store de identidades visuales.

Las reglas que se blindan aquí son las tres que el usuario nota si se rompen: la
identidad de la casa no se puede borrar ni editar, solo una identidad puede estar
activa a la vez, y la generación nunca se queda sin identidad —pase lo que pase con
Mongo, `activa()` devuelve la system y el post sale con el look de siempre.
"""

import copy

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


# ── Doble de la colección (equality-only, que es todo lo que usa el store) ─────

class _FakeCursor:
    def __init__(self, docs: list):
        self._docs = docs

    def sort(self, campo, direccion=1):
        self._docs.sort(key=lambda d: d.get(campo), reverse=direccion < 0)
        return self

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()


class _FakeColl:
    def __init__(self):
        self.docs: list[dict] = []

    @staticmethod
    def _match(doc: dict, filt: dict | None) -> bool:
        return all(doc.get(k) == v for k, v in (filt or {}).items())

    async def find_one(self, filt=None):
        return next((copy.deepcopy(d) for d in self.docs if self._match(d, filt)), None)

    def find(self, filt=None):
        return _FakeCursor([copy.deepcopy(d) for d in self.docs if self._match(d, filt)])

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    async def update_one(self, filt, update):
        for d in self.docs:
            if self._match(d, filt):
                d.update(update["$set"])
                return

    async def update_many(self, filt, update):
        for d in self.docs:
            if self._match(d, filt):
                d.update(update["$set"])

    async def delete_one(self, filt):
        for i, d in enumerate(self.docs):
            if self._match(d, filt):
                self.docs.pop(i)
                return


@pytest.fixture
def coll(monkeypatch):
    fake = _FakeColl()

    async def _get():
        return fake

    monkeypatch.setattr(db, "get_identities", _get)
    return fake


@pytest.fixture
def sin_base(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(db, "get_identities", _none)


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


async def test_activa_sin_ninguna_marcada_devuelve_la_de_la_casa(coll):
    await _crear()
    assert (await store.activa(_YO))["id"] == vi.SYSTEM_ID


# ── Crear ─────────────────────────────────────────────────────────────────────

async def test_crear_valida_el_json_antes_de_escribir(coll):
    with pytest.raises(vi.IdentidadInvalida):
        await store.crear(_YO, name="Rota", identity_json={**_JSON, "aspect_ratio": "nope"})
    assert coll.docs == []


async def test_crear_sugiere_el_nombre_si_va_vacio(coll):
    fila = await _crear(nombre="   ")
    assert fila["name"] == "Ember · ink"


async def test_crear_no_marca_activa_por_defecto(coll):
    fila = await _crear()
    assert fila["is_default"] is False
    assert fila["is_system"] is False
    assert fila["user_id"] == _YO


async def test_crear_puede_activar_de_una(coll):
    fila = await _crear(activar_al_crear=True)
    assert fila["is_default"] is True
    assert (await store.activa(_YO))["id"] == fila["id"]


# ── Listar ────────────────────────────────────────────────────────────────────

async def test_listar_pone_la_de_la_casa_primero(coll):
    await _crear("Una")
    await _crear("Otra")
    filas = await store.listar(_YO)
    assert filas[0]["id"] == vi.SYSTEM_ID
    assert [f["name"] for f in filas[1:]] == ["Una", "Otra"]


async def test_listar_no_muestra_las_de_otro_usuario(coll):
    await _crear("Ajena", user=_OTRO)
    assert [f["id"] for f in await store.listar(_YO)] == [vi.SYSTEM_ID]


# ── La identidad de la casa ───────────────────────────────────────────────────

async def test_la_identidad_de_la_casa_no_se_elimina(coll):
    with pytest.raises(store.NoEditable):
        await store.eliminar(_YO, vi.SYSTEM_ID)


async def test_la_identidad_de_la_casa_no_se_edita(coll):
    with pytest.raises(store.NoEditable):
        await store.actualizar(_YO, vi.SYSTEM_ID, name="Otro nombre")


async def test_la_identidad_de_la_casa_si_se_puede_activar(coll):
    mia = await _crear(activar_al_crear=True)
    fila = await store.activar(_YO, vi.SYSTEM_ID)
    assert fila["id"] == vi.SYSTEM_ID and fila["is_default"] is True
    # Activar la de la casa es desmarcar todo: ninguna fila queda activa.
    assert (await store.obtener(_YO, mia["id"]))["is_default"] is False


# ── Activa ────────────────────────────────────────────────────────────────────

async def test_solo_una_identidad_activa_a_la_vez(coll):
    a = await _crear("A", activar_al_crear=True)
    b = await _crear("B", activar_al_crear=True)
    activas = [f["id"] for f in await store.listar(_YO) if f["is_default"]]
    assert activas == [b["id"]]
    assert (await store.obtener(_YO, a["id"]))["is_default"] is False


async def test_al_borrar_la_activa_vuelve_la_de_la_casa(coll):
    fila = await _crear(activar_al_crear=True)
    await store.eliminar(_YO, fila["id"])
    assert (await store.activa(_YO))["id"] == vi.SYSTEM_ID


async def test_activar_una_identidad_ajena_no_encuentra_nada(coll):
    ajena = await _crear("Ajena", user=_OTRO)
    with pytest.raises(store.NoEncontrada):
        await store.activar(_YO, ajena["id"])


# ── Editar ────────────────────────────────────────────────────────────────────

async def test_renombrar_no_toca_el_json(coll):
    fila = await _crear("Vieja")
    nueva = await store.actualizar(_YO, fila["id"], name="Nueva")
    assert nueva["name"] == "Nueva"
    assert nueva["identity_json"] == fila["identity_json"]


async def test_editar_el_json_lo_valida(coll):
    fila = await _crear()
    with pytest.raises(vi.IdentidadInvalida):
        await store.actualizar(_YO, fila["id"],
                               identity_json={**_JSON, "color_acento": "sin hex"})
    assert (await store.obtener(_YO, fila["id"]))["identity_json"] == fila["identity_json"]


async def test_editar_el_json_lo_guarda_normalizado(coll):
    fila = await _crear()
    nueva = await store.actualizar(
        _YO, fila["id"], identity_json={**_JSON, "paleta": ["101014", "#F2EFE6", "#FF5C2B"]})
    assert nueva["identity_json"]["paleta"][0] == "#101014"


async def test_un_nombre_vacio_al_renombrar_se_rechaza(coll):
    fila = await _crear()
    with pytest.raises(ValueError):
        await store.actualizar(_YO, fila["id"], name="  ")


async def test_no_se_puede_tocar_la_identidad_de_otro_usuario(coll):
    ajena = await _crear("Ajena", user=_OTRO)
    with pytest.raises(store.NoEncontrada):
        await store.actualizar(_YO, ajena["id"], name="Secuestrada")
    with pytest.raises(store.NoEncontrada):
        await store.eliminar(_YO, ajena["id"])
    assert len(coll.docs) == 1
