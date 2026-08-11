"""Persistencia de las identidades visuales (colección `visual_identities`).

Mongo entró al proyecto para el dashboard de costos con una regla explícita: la
medición es best-effort y **nunca** puede interrumpir la generación. Aquí esa regla se
parte en dos, porque no todo lo que se guarda vale lo mismo:

- **Las escrituras fallan alto.** Si el usuario crea o renombra una identidad y Mongo
  no responde, se lanza `AlmacenNoDisponible` y la UI lo dice. Tragarse el error
  dejaría a alguien mirando una identidad que no existe.
- **`activa()` nunca falla.** Es lo que lee el pipeline al crear un job, y ahí sí manda
  la regla vieja: ante cualquier problema devuelve la identidad system y la generación
  sigue exactamente como hoy. `elegida()` —la identidad que el formulario eligió para
  ESE post— hereda la misma regla y cae a la activa.

La identidad **system no es una fila**: se sirve desde `prompts/brand.json`
(`visual_identity.fila_system`). Por eso no se puede eliminar ni editar —no hay nada
que borrar— y por eso existe aunque no haya base. Clonarla sí: es crear una fila normal
con su JSON de partida.

La identidad activa se modela con `is_default` en las filas del usuario, como pide el
esquema. **Que ninguna fila esté marcada significa que la activa es la system**, así
que activar la system es limpiar las marcas, y borrar la identidad activa devuelve al
usuario a la de la casa sin ningún paso extra.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import db
import visual_identity as vi

logger = logging.getLogger("identidades")


class AlmacenNoDisponible(RuntimeError):
    """No hay base para escribir (Mongo sin configurar o caído)."""


class NoEncontrada(LookupError):
    """Esa identidad no existe, o no es de este usuario."""


class NoEditable(PermissionError):
    """La identidad system no se edita ni se elimina; solo se clona."""


_MSG_SIN_BASE = (
    "No hay conexión con la base de datos: revisa MONGODB_URI en el .env. "
    "Mientras tanto puedes seguir generando con la identidad de la casa."
)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def _serializar(doc: dict) -> dict:
    """Documento de Mongo → el shape que ven la API y la UI (mismo que `fila_system`)."""
    return {
        "id": doc.get("_id"),
        "user_id": doc.get("user_id"),
        "name": doc.get("name") or "",
        "identity_json": vi.normalizar(doc.get("identity_json")),
        "is_default": bool(doc.get("is_default")),
        "is_system": False,
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }


async def _coleccion(*, obligatoria: bool):
    coll = await db.get_identities()
    if coll is None and obligatoria:
        raise AlmacenNoDisponible(_MSG_SIN_BASE)
    return coll


async def _propia(coll, user_id: str, identity_id: str) -> dict:
    """La fila `identity_id` de `user_id`, o las excepciones del caso.

    El filtro lleva SIEMPRE el `user_id`: sin él, un id adivinado dejaría tocar la
    identidad de otro perfil. Es la misma comprobación que haría el día que esto tenga
    auth de verdad, solo que hoy el dueño lo dice una cabecera.
    """
    if identity_id == vi.SYSTEM_ID:
        raise NoEditable("La identidad de la casa no se puede modificar ni eliminar; clónala para partir de ella.")
    doc = await coll.find_one({"_id": identity_id, "user_id": user_id})
    if doc is None:
        raise NoEncontrada("Esa identidad visual no existe.")
    return doc


# ── Lectura ───────────────────────────────────────────────────────────────────

async def listar(user_id: str) -> list[dict]:
    """Identidades visibles para el usuario: la system primero, luego las suyas.

    Sin base devuelve solo la system, que es la verdad: es lo único que hay. La UI
    pinta esa lista igual y el usuario ve que no puede crear nada.
    """
    system = vi.fila_system()
    coll = await _coleccion(obligatoria=False)
    if coll is None:
        system["is_default"] = True
        return [system]
    filas = [_serializar(d) async for d in coll.find({"user_id": user_id}).sort("created_at", 1)]
    # Ninguna marcada = manda la de la casa (ver el docstring del módulo).
    system["is_default"] = not any(f["is_default"] for f in filas)
    return [system, *filas]


async def obtener(user_id: str, identity_id: str) -> dict:
    """Una identidad concreta (la system incluida). Lanza `NoEncontrada` si no existe."""
    if identity_id == vi.SYSTEM_ID:
        fila = vi.fila_system()
        fila["is_default"] = not await _hay_default(user_id)
        return fila
    coll = await _coleccion(obligatoria=True)
    doc = await coll.find_one({"_id": identity_id, "user_id": user_id})
    if doc is None:
        raise NoEncontrada("Esa identidad visual no existe.")
    return _serializar(doc)


async def _hay_default(user_id: str) -> bool:
    coll = await _coleccion(obligatoria=False)
    if coll is None:
        return False
    return await coll.find_one({"user_id": user_id, "is_default": True}) is not None


async def activa(user_id: str) -> dict:
    """La identidad activa del usuario. **Nunca lanza** — el pipeline depende de esto.

    Es el único punto por el que la generación entra a este módulo (`job_runner` la
    congela en el job al crearlo). Ante cualquier fallo —sin Mongo, base caída, fila
    corrupta— devuelve la identidad system y el post sale con el look de la casa, que
    es exactamente lo que pasa hoy.
    """
    try:
        coll = await _coleccion(obligatoria=False)
        if coll is not None:
            doc = await coll.find_one({"user_id": user_id, "is_default": True})
            if doc is not None:
                return _serializar(doc)
    except Exception:  # noqa: BLE001 — la generación no se cae por esto
        logger.warning("No se pudo leer la identidad activa de %s; se usa la de la casa",
                       user_id, exc_info=True)
    fila = vi.fila_system()
    fila["is_default"] = True
    return fila


async def elegida(user_id: str, identity_id: str | None) -> dict:
    """La identidad con la que sale un post: la que se eligió al crearlo, o la activa.

    Es el punto por el que entran los DOS flujos desde que el formulario deja elegir
    la identidad post a post. **Vacío significa "la de siempre"** —la activa del
    perfil—, que es exactamente lo que hacían los dos flujos antes de que el campo
    existiera.

    Y como `activa`, **nunca lanza**: un id que ya no existe (la identidad se borró
    entre que se pintó el formulario y se envió, o no hay base para leerla) no puede
    tumbar la creación del job; se cae a la activa y el post sale con el look que el
    perfil tenía puesto.
    """
    elegido = str(identity_id or "").strip()
    if elegido:
        try:
            return await obtener(user_id, elegido)
        except Exception:  # noqa: BLE001 — la creación del job no se cae por esto
            logger.warning("La identidad %s no está disponible para %s; se usa la activa",
                           elegido, user_id, exc_info=True)
    return await activa(user_id)


# ── Escritura ─────────────────────────────────────────────────────────────────

async def crear(user_id: str, *, name: str, identity_json: dict,
                activar_al_crear: bool = False) -> dict:
    """Crea una identidad propia del usuario. Valida el JSON antes de escribir.

    Lanza `visual_identity.IdentidadInvalida` si el JSON no cumple el esquema: nada
    entra a la base sin validarse, venga del extractor o del editor de la UI.
    """
    ident = vi.exigir_valida(identity_json)
    nombre = vi.normalizar_nombre(name) or vi.nombre_sugerido(ident)
    errores = vi.validar_nombre(nombre)
    if errores:
        raise ValueError("; ".join(errores))

    coll = await _coleccion(obligatoria=True)
    ahora = _ahora()
    doc = {
        "_id": uuid.uuid4().hex,
        "user_id": user_id,
        "name": nombre,
        "identity_json": ident,
        "is_default": False,
        # Siempre False en una fila: la única identidad system se sirve desde
        # brand.json. El campo se guarda igual para no tener que migrar el día que
        # existan identidades de sistema de verdad.
        "is_system": False,
        "created_at": ahora,
        "updated_at": ahora,
    }
    await coll.insert_one(doc)
    if activar_al_crear:
        return await activar(user_id, doc["_id"])
    return _serializar(doc)


async def actualizar(user_id: str, identity_id: str, *, name: str | None = None,
                     identity_json: dict | None = None) -> dict:
    """Renombra y/o reemplaza el JSON de una identidad propia.

    Los dos campos son opcionales e independientes: renombrar no toca el JSON. La
    system rebota con `NoEditable`.
    """
    coll = await _coleccion(obligatoria=True)
    doc = await _propia(coll, user_id, identity_id)

    cambios: dict = {}
    if name is not None:
        nombre = vi.normalizar_nombre(name)
        errores = vi.validar_nombre(nombre)
        if errores:
            raise ValueError("; ".join(errores))
        cambios["name"] = nombre
    if identity_json is not None:
        cambios["identity_json"] = vi.exigir_valida(identity_json)
    if not cambios:
        return _serializar(doc)

    cambios["updated_at"] = _ahora()
    await coll.update_one({"_id": identity_id, "user_id": user_id}, {"$set": cambios})
    return _serializar({**doc, **cambios})


async def eliminar(user_id: str, identity_id: str) -> None:
    """Elimina una identidad propia. La system lanza `NoEditable`.

    Si era la activa no hay nada que reasignar: sin fila marcada, la activa vuelve a
    ser la de la casa.
    """
    coll = await _coleccion(obligatoria=True)
    await _propia(coll, user_id, identity_id)
    await coll.delete_one({"_id": identity_id, "user_id": user_id})


async def activar(user_id: str, identity_id: str) -> dict:
    """Marca `identity_id` como la identidad activa del usuario.

    Activar la system es desmarcar todo (ver el docstring del módulo), y por eso es la
    única operación que la system sí acepta.
    """
    coll = await _coleccion(obligatoria=True)
    if identity_id != vi.SYSTEM_ID:
        await _propia(coll, user_id, identity_id)
    # Primero se limpia y después se marca: así nunca hay dos activas a la vez, ni
    # siquiera si el proceso muere entre las dos escrituras (peor caso: ninguna, que
    # es un estado válido y significa "la de la casa").
    await coll.update_many({"user_id": user_id, "is_default": True},
                           {"$set": {"is_default": False, "updated_at": _ahora()}})
    if identity_id == vi.SYSTEM_ID:
        fila = vi.fila_system()
        fila["is_default"] = True
        return fila
    await coll.update_one({"_id": identity_id, "user_id": user_id},
                          {"$set": {"is_default": True, "updated_at": _ahora()}})
    return await obtener(user_id, identity_id)
