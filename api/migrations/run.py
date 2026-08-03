"""Runner de migraciones. Desde `api/`:

    python -m migrations.run status      # qué hay aplicado y qué falta
    python -m migrations.run up          # aplica todo lo pendiente, en orden
    python -m migrations.run down        # revierte la ÚLTIMA aplicada
    python -m migrations.run down 001    # revierte una concreta

El estado vive en la colección `_migrations` (un documento por versión aplicada). Es
deliberadamente tonto: descubre los módulos `NNN_*.py` del paquete, los ordena por
nombre y llama a su `up`/`down`. Sin dependencias nuevas.

No es transaccional —Mongo no da DDL transaccional en un standalone— así que cada
migración tiene que ser idempotente por su cuenta, como lo es la 001.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import db

COLECCION_ESTADO = "_migrations"

_NOMBRE = re.compile(r"^(\d{3})_[a-z0-9_]+\.py$")


def _modulos() -> list:
    """Los módulos de migración del paquete, ordenados por versión."""
    aqui = Path(__file__).resolve().parent
    nombres = sorted(p.stem for p in aqui.glob("*.py") if _NOMBRE.match(p.name))
    return [importlib.import_module(f"{__package__}.{n}") for n in nombres]


async def _aplicadas(dbase) -> dict[str, dict]:
    return {d["_id"]: d async for d in dbase[COLECCION_ESTADO].find({})}


async def _status(dbase) -> int:
    aplicadas = await _aplicadas(dbase)
    print(f"Base: {db._db_name()}")
    for mod in _modulos():
        marca = aplicadas.get(mod.VERSION)
        estado = f"aplicada  {marca['applied_at']:%Y-%m-%d %H:%M UTC}" if marca else "PENDIENTE"
        print(f"  [{mod.VERSION}] {estado}  — {mod.DESCRIPCION}")
    return 0


async def _up(dbase) -> int:
    aplicadas = await _aplicadas(dbase)
    pendientes = [m for m in _modulos() if m.VERSION not in aplicadas]
    if not pendientes:
        print("Nada que aplicar: la base ya está al día.")
        return 0
    for mod in pendientes:
        print(f"→ aplicando {mod.VERSION} ({mod.DESCRIPCION})")
        detalle = await mod.up(dbase)
        await dbase[COLECCION_ESTADO].insert_one({
            "_id": mod.VERSION,
            "description": mod.DESCRIPCION,
            "applied_at": datetime.now(timezone.utc),
        })
        print(f"  ok: {detalle}")
    return 0


async def _down(dbase, version: str = "") -> int:
    aplicadas = await _aplicadas(dbase)
    if not aplicadas:
        print("No hay ninguna migración aplicada.")
        return 0
    objetivo = version or max(aplicadas)
    mod = next((m for m in _modulos() if m.VERSION == objetivo), None)
    if mod is None:
        print(f"No existe la migración {objetivo}.", file=sys.stderr)
        return 1
    if objetivo not in aplicadas:
        print(f"La migración {objetivo} no está aplicada.")
        return 0
    print(f"→ revirtiendo {mod.VERSION} ({mod.DESCRIPCION})")
    detalle = await mod.down(dbase)
    await dbase[COLECCION_ESTADO].delete_one({"_id": objetivo})
    print(f"  ok: {detalle}")
    return 0


async def _main(argv: list[str]) -> int:
    accion = (argv[0] if argv else "status").lower()
    dbase = db.get_database()
    if accion == "status":
        return await _status(dbase)
    if accion == "up":
        return await _up(dbase)
    if accion == "down":
        return await _down(dbase, argv[1] if len(argv) > 1 else "")
    print(f"Acción desconocida: {accion}. Usa status | up | down [version].", file=sys.stderr)
    return 1


def main() -> int:
    try:
        return asyncio.run(_main(sys.argv[1:]))
    except RuntimeError as e:      # sin motor / sin MONGODB_URI: el mensaje ya es claro
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
