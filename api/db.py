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
_DEFAULT_DB = "qbyk_aima"

# Tiempos cortos para que un Mongo caído falle rápido y no frene el pipeline.
_SERVER_SELECTION_TIMEOUT_MS = 3000
_COOLDOWN_SECS = 60.0

_client = None  # type: ignore[var-annotated]
_indexes_ready = False
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


async def _ensure_indexes(coll) -> None:
    """Índices de §5 del doc: ts, service, job_id, batch_id y compuesto {ts, service}."""
    await coll.create_index("ts")
    await coll.create_index("service")
    await coll.create_index("job_id")
    await coll.create_index("batch_id")
    await coll.create_index([("ts", 1), ("service", 1)])


async def get_usage_events():
    """Devuelve la colección `usage_events` lista para escribir, o `None`.

    `None` significa "tracking no disponible ahora" (no configurado, sin motor, o
    Mongo caído) — el llamador debe tratarlo como un no-op, nunca como un error.
    """
    global _indexes_ready, _cooldown_until
    if not is_configured():
        return None
    try:
        coll = _get_client()[_db_name()][COLLECTION]
        if not _indexes_ready:
            await _ensure_indexes(coll)
            _indexes_ready = True
        return coll
    except Exception as e:  # noqa: BLE001 — best-effort, nunca propagar
        logger.warning("MongoDB no disponible; tracking en pausa %ss: %s", int(_COOLDOWN_SECS), e)
        _cooldown_until = time.monotonic() + _COOLDOWN_SECS
        return None


def close() -> None:
    """Cierra el cliente (para el shutdown de la app o los tests)."""
    global _client, _indexes_ready
    if _client is not None:
        _client.close()
        _client = None
    _indexes_ready = False
