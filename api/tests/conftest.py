"""Hace importables los módulos de `api/` desde los tests (p.ej. `import cost_calc`)
sin depender del directorio de trabajo desde el que se invoque pytest.

También vive aquí el doble de Mongo que comparten los tests de identidades y de
migraciones. Es deliberadamente tonto —solo filtros de igualdad, que es todo lo que
usan `identity_store` y las migraciones— para que un test que pase aquí signifique
algo sobre la lógica y no sobre la fidelidad del doble.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import copy  # noqa: E402 — después del sys.path para poder importar los módulos de api/

import pytest  # noqa: E402

import db  # noqa: E402


@pytest.fixture(autouse=True)
def _sin_mongo_real(monkeypatch):
    """Ningún test puede hablar con la base de verdad.

    `config.py` y `db.py` cargan el `.env` de la raíz al importarse, así que en una
    máquina con `MONGODB_URI` configurado cualquier camino que llame a
    `cost_tracker.record_event` —un endpoint bajo TestClient, por ejemplo— escribe
    eventos reales en Atlas durante la suite. Vaciar la variable deja `is_configured()`
    en False y el tracking se apaga solo, que es exactamente lo que hace en producción
    cuando no hay base. Los tests que necesitan una colección la inyectan aparte
    (`identidades`), así que este guard no les quita nada.
    """
    monkeypatch.delenv("MONGODB_URI", raising=False)


class FakeCursor:
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


class FakeCollection:
    """Colección en memoria. Devuelve copias, como haría el driver de verdad: un test
    que mute lo que le dio `find_one` no puede alterar lo "guardado"."""

    def __init__(self):
        self.docs: list[dict] = []
        self.indices: list = []

    @staticmethod
    def _match(doc: dict, filt: dict | None) -> bool:
        return all(doc.get(k) == v for k, v in (filt or {}).items())

    async def create_index(self, spec):
        self.indices.append(spec)

    async def find_one(self, filt=None):
        return next((copy.deepcopy(d) for d in self.docs if self._match(d, filt)), None)

    def find(self, filt=None):
        return FakeCursor([copy.deepcopy(d) for d in self.docs if self._match(d, filt)])

    async def count_documents(self, filt=None):
        return sum(1 for d in self.docs if self._match(d, filt))

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


class FakeDatabase:
    """Solo lo que usan las migraciones: crear, tirar y listar colecciones."""

    def __init__(self):
        self.cols: dict[str, FakeCollection] = {}

    async def list_collection_names(self):
        return list(self.cols)

    async def create_collection(self, name):
        self.cols.setdefault(name, FakeCollection())

    async def drop_collection(self, name):
        self.cols.pop(name, None)

    def __getitem__(self, name):
        return self.cols.setdefault(name, FakeCollection())


@pytest.fixture
def identidades(monkeypatch) -> FakeCollection:
    """`db.get_identities` apuntando a una colección en memoria."""
    fake = FakeCollection()

    async def _get():
        return fake

    monkeypatch.setattr(db, "get_identities", _get)
    return fake


@pytest.fixture
def sin_base(monkeypatch):
    """Mongo no disponible: `get_identities` devuelve None, como en producción."""
    async def _none():
        return None

    monkeypatch.setattr(db, "get_identities", _none)


@pytest.fixture
def dbase() -> FakeDatabase:
    return FakeDatabase()
