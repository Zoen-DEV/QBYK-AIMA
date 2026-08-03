"""Cliente MongoDB (async, `motor`) para el dashboard de costos — Fase 1.

Conexión **perezosa** y **best-effort**: la persistencia de métricas nunca debe
tumbar ni frenar la generación de posts. Por eso:

- Si `motor` no está instalado → el tracking queda desactivado (la app sigue igual).
- Si `MONGODB_URI` está vacío → desactivado (estado por defecto hasta que pegues el
  URI real de Atlas en `.env`). Cero latencia: no se intenta conectar.
- Si Mongo está configurado pero no responde → se captura el fallo, se entra en un
  breve *cooldown* y `get_usage_events()` devuelve `None`. El llamador
  (`cost_tracker.record_event`) simplemente no escribe y no propaga el error.

Config por `.env` (raíz del repo): `MONGODB_URI`, `MONGODB_DB` (default `qbyk_aima`).
"""

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

# Igual que config.py: el .env vive en la raíz del repo (un nivel sobre api/).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("cost.db")

# El import de motor se hace guardado: si la dependencia falta, el tracking se
# desactiva en vez de romper el arranque de toda la API.
try:
    from motor.motor_asyncio import AsyncIOMotorClient
    _MOTOR_AVAILABLE = True
except Exception:  # noqa: BLE001 — cualquier fallo de import = sin tracking
    AsyncIOMotorClient = None  # type: ignore[assignment]
    _MOTOR_AVAILABLE = False

COLLECTION = "usage_events"
# Identidades visuales de los usuarios (la identidad system NO vive aquí: se sirve
# desde prompts/brand.json — ver visual_identity.identidad_system).
IDENTITIES_COLLECTION = "visual_identities"
_DEFAULT_DB = "qbyk_aima"

# Índices por colección; se crean una sola vez, la primera vez que se pide cada una.
# Los de `usage_events` son los de §5 del doc del dashboard.
_INDEXES: dict[str, list] = {
    COLLECTION: ["ts", "service", "job_id", "batch_id", [("ts", 1), ("service", 1)]],
    # Todas las consultas de identidades son "las de este usuario"; el compuesto
    # resuelve además "¿cuál tiene marcada la activa?".
    IDENTITIES_COLLECTION: ["user_id", [("user_id", 1), ("is_default", 1)]],
}

# Tiempos cortos para que un Mongo caído falle rápido y no frene el pipeline.
_SERVER_SELECTION_TIMEOUT_MS = 3000
_COOLDOWN_SECS = 60.0

_client = None  # type: ignore[var-annotated]
_indexed: set[str] = set()  # colecciones cuyos índices ya se crearon en este proceso
_cooldown_until = 0.0  # time.monotonic() hasta el que saltamos los intentos


def _uri() -> str:
    return os.environ.get("MONGODB_URI", "").strip()


def _db_name() -> str:
    return (os.environ.get("MONGODB_DB", "").strip() or _DEFAULT_DB)


def is_configured() -> bool:
    """¿Hay con qué intentar conectar ahora mismo? (motor + URI + fuera de cooldown)."""
    if not _MOTOR_AVAILABLE or not _uri():
        return False
    return time.monotonic() >= _cooldown_until


def _get_client():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            _uri(),
            serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS,
            connectTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS,
            tz_aware=True,
        )
    return _client


def get_database():
    """La base entera — para las **migraciones**, que crean y borran colecciones.

    A diferencia de `get_collection`, esta no se traga nada ni entra en cooldown: una
    migración que no puede hablar con Mongo tiene que fallar con el error real y a la
    vista, no seguir como si nada.
    """
    if not _MOTOR_AVAILABLE:
        raise RuntimeError("Falta la dependencia `motor` (pip install -r api/requirements.txt)")
    if not _uri():
        raise RuntimeError("MONGODB_URI no está configurado en el .env de la raíz del repo")
    return _get_client()[_db_name()]


async def get_collection(name: str):
    """Devuelve la colección `name` lista para usar (con sus índices), o `None`.

    `None` significa "Mongo no disponible ahora" (no configurado, sin motor, o caído).
    Qué hacer con eso depende de quién llame: el tracking de costos lo trata como un
    no-op silencioso, mientras que `identity_store` lo convierte en un error visible
    —perder un evento de consumo es aceptable, perder la identidad que el usuario
    acaba de crear no lo es.
    """
    global _cooldown_until
    if not is_configured():
        return None
    try:
        coll = _get_client()[_db_name()][name]
        if name not in _indexed:
            for spec in _INDEXES.get(name, []):
                await coll.create_index(spec)
            _indexed.add(name)
        return coll
    except Exception as e:  # noqa: BLE001 — best-effort, nunca propagar
        logger.warning("MongoDB no disponible; en pausa %ss: %s", int(_COOLDOWN_SECS), e)
        _cooldown_until = time.monotonic() + _COOLDOWN_SECS
        return None


async def get_usage_events():
    """La colección `usage_events`, o `None` si el tracking no está disponible."""
    return await get_collection(COLLECTION)


async def get_identities():
    """La colección `visual_identities`, o `None` si Mongo no está disponible."""
    return await get_collection(IDENTITIES_COLLECTION)


def close() -> None:
    """Cierra el cliente (para el shutdown de la app o los tests)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
    _indexed.clear()
