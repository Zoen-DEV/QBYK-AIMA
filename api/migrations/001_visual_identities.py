"""001 — colección `visual_identities`.

Crea la colección donde viven las identidades visuales de cada usuario, con sus
índices. **No siembra la identidad system**: esa se sirve desde `prompts/brand.json`
(ver `visual_identity.identidad_system`), así que copiarla aquí solo habría creado dos
fuentes para lo mismo y drift en cuanto alguien editara el archivo.

Reversible: `down` tira la colección. Eso borra las identidades que los usuarios hayan
creado —es el punto de una reversión— pero **no toca el look de la casa**, que sigue
en `brand.json`; revertir devuelve la app exactamente al estado anterior a la feature.
"""

from __future__ import annotations

import db

VERSION = "001"
DESCRIPCION = "Colección visual_identities (identidades visuales por usuario)"

COLECCION = db.IDENTITIES_COLLECTION


async def up(dbase) -> str:
    """Crea la colección y sus índices. Idempotente."""
    existentes = await dbase.list_collection_names()
    if COLECCION not in existentes:
        # Explícita en vez de dejar que la cree el primer insert: así `down` tiene algo
        # que tirar aunque nadie haya creado todavía ninguna identidad, y `status` dice
        # la verdad sobre si la migración corrió.
        await dbase.create_collection(COLECCION)
    coll = dbase[COLECCION]
    for spec in db._INDEXES[COLECCION]:
        await coll.create_index(spec)
    return f"colección `{COLECCION}` lista con {len(db._INDEXES[COLECCION])} índices"


async def down(dbase) -> str:
    """Tira la colección entera (índices incluidos)."""
    existentes = await dbase.list_collection_names()
    if COLECCION not in existentes:
        return f"la colección `{COLECCION}` no existía"
    n = await dbase[COLECCION].count_documents({})
    await dbase.drop_collection(COLECCION)
    return f"colección `{COLECCION}` eliminada ({n} identidades borradas)"
