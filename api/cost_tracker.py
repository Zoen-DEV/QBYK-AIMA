"""Registro de eventos de consumo (usage_events) — Fase 1 del dashboard de costos.

Punto **único** de instrumentación: el pipeline (Fase 2) llamará a `record_event`
con las unidades de cada llamada de pago; aquí se calcula el costo con la tarifa
vigente (`cost_calc`), se "congela" en el documento y se persiste en Mongo.

**Regla de oro (doc §7):** la medición es best-effort. Un fallo de cálculo o de Mongo
**nunca** debe interrumpir la generación o publicación de un post — todo va envuelto
en try/except y solo se loguea.

Diseño en dos piezas:
- `build_event(...)` — **pura**: arma el documento del evento (calcula costo, estampa
  `ts` UTC y `pricing_version`). Sin I/O → fácil de testear.
- `record_event(...)` — async: `build_event` + inserción best-effort en Mongo.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import cost_calc
import db

logger = logging.getLogger("cost.tracker")


def build_event(
    *,
    service: str,
    operation: str,
    units: Mapping[str, Any],
    model: Optional[str] = None,
    flow: str = "individual",
    job_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    source_type: Optional[str] = None,
    platforms: Optional[Sequence[str]] = None,
    dry_run: bool = False,
    status: str = "success",
    meta: Optional[Mapping[str, Any]] = None,
    pricing: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Construye el documento `usage_events` (sin tocar disco ni red).

    El costo se calcula aquí y queda "congelado" junto a las unidades: cambiar una
    tarifa después no recalcula el histórico. Se guarda `pricing_version` para
    trazabilidad. La forma del doc sigue §5 del doc de planificación.
    """
    pricing = pricing if pricing is not None else cost_calc.load_pricing()
    cost = cost_calc.compute_cost(service, units, pricing, model=model, operation=operation)
    return {
        "ts": datetime.now(timezone.utc),
        "flow": flow,
        "job_id": job_id,
        "batch_id": batch_id,
        "service": service,
        "operation": operation,
        "model": model,
        "units": dict(units or {}),
        "cost_usd": float(cost),
        "pricing_version": cost_calc.pricing_version(pricing),
        "source_type": source_type,
        "platforms": list(platforms or []),
        "dry_run": bool(dry_run),
        "status": status,
        "meta": dict(meta or {}),
    }


async def record_event(
    *,
    service: str,
    operation: str,
    units: Mapping[str, Any],
    model: Optional[str] = None,
    flow: str = "individual",
    job_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    source_type: Optional[str] = None,
    platforms: Optional[Sequence[str]] = None,
    dry_run: bool = False,
    status: str = "success",
    meta: Optional[Mapping[str, Any]] = None,
    pricing: Optional[Mapping[str, Any]] = None,
) -> Optional[dict]:
    """Calcula el costo y persiste el evento en Mongo. **Best-effort: nunca lanza.**

    Devuelve el documento insertado (con `_id`) si se escribió, o `None` si el
    tracking está desactivado / no disponible / hubo un error. El llamador debe
    ignorar el valor de retorno salvo para tests o logging.
    """
    try:
        event = build_event(
            service=service, operation=operation, units=units, model=model,
            flow=flow, job_id=job_id, batch_id=batch_id, source_type=source_type,
            platforms=platforms, dry_run=dry_run, status=status, meta=meta,
            pricing=pricing,
        )
    except Exception:  # noqa: BLE001 — un fallo de cálculo no debe romper el pipeline
        logger.exception("No se pudo construir el usage_event (%s/%s)", service, operation)
        return None

    try:
        coll = await db.get_usage_events()
        if coll is None:
            return None
        await coll.insert_one(event)
        return event
    except Exception:  # noqa: BLE001 — un fallo de Mongo es best-effort, solo se loguea
        logger.warning("No se pudo registrar el usage_event (%s/%s)", service, operation, exc_info=True)
        return None
