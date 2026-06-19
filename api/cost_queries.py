"""Agregaciones de costos sobre `usage_events` — Fase 3 del dashboard.

Lee la colección `usage_events` (Mongo) y produce los rollups que consumen los
endpoints `/costs/*` y el dashboard:

- `summary(period)`      — totales del mes/año: variable por servicio + fijos
                           prorrateados + total + costo promedio por job.
- `timeseries(...)`      — serie temporal (día o mes) por servicio para las gráficas.
- `by_job(...)`          — costo por job (gasto por uso) con desglose de servicios.
- `events(...)`          — eventos crudos paginados, para auditoría.

**Best-effort, igual que el resto del tracking (doc §7):** si Mongo no está
disponible, cada función devuelve una estructura vacía/coherente en vez de lanzar,
para que el dashboard muestre "sin datos" sin romperse.

Diseño: los helpers de **período, prorrateo y reshape son puros** (sin I/O) para
poder testearlos aislados; las funciones `async` solo añaden la ejecución de la
agregación en Mongo. Todos los montos se calculan y devuelven en **USD** (la
conversión a moneda de visualización con `fx_rate` se aplica en el frontend).
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import cost_calc
import db

logger = logging.getLogger("cost.queries")

# Formato de bucket de `$dateToString` por granularidad.
_BUCKET_FMT = {"day": "%Y-%m-%d", "month": "%Y-%m"}

# Límites defensivos para las consultas de auditoría/por-job.
_BY_JOB_LIMIT = 500
_EVENTS_MAX_LIMIT = 200


# ─────────────────────────── helpers puros (período / fechas) ───────────────────

def parse_period(period: str) -> tuple[datetime, datetime, str]:
    """`'YYYY-MM'` → (inicio, fin_exclusivo, 'month'); `'YYYY'` → (…, 'year').

    Las fronteras se calculan en **UTC** (consistente con cómo guardamos `ts` y con
    la decisión §11.6 del doc). `fin` es exclusivo (`$lt`). Lanza `ValueError` si el
    formato no es válido.
    """
    period = (period or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"Mes inválido en period: {period}")
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = _add_month(start)
        return start, end, "month"
    m = re.fullmatch(r"(\d{4})", period)
    if m:
        year = int(m.group(1))
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        return start, end, "year"
    raise ValueError("period inválido: usa 'YYYY-MM' (mes) o 'YYYY' (año)")


def _add_month(dt: datetime) -> datetime:
    """Primer día del mes siguiente a `dt` (en UTC)."""
    if dt.month == 12:
        return datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)


def months_in_range(start: datetime, end: datetime) -> int:
    """Número de meses calendario que cubre [start, end). Un mes → 1, un año → 12."""
    return (end.year - start.year) * 12 + (end.month - start.month)


def parse_date(value: str, default: datetime) -> datetime:
    """`'YYYY-MM-DD'` o `'YYYY-MM'` → datetime UTC a medianoche; vacío → `default`."""
    value = (value or "").strip()
    if not value:
        return default
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    m = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
    raise ValueError(f"fecha inválida: {value} (usa YYYY-MM-DD)")


# ─────────────────────────── helpers puros (fijos / pricing) ────────────────────

def prorate_fixed(pricing: dict, months: int) -> list[dict]:
    """Prorratea los `fixed_monthly` de `pricing.json` al rango consultado.

    Para un mes son los montos mensuales tal cual (months=1); para un año se
    multiplican por 12. Devuelve cada servicio con su `monthly_usd` y el
    `prorated_usd` del período.
    """
    out: list[dict] = []
    for item in (pricing.get("fixed_monthly") or []):
        monthly = _num(item.get("monthly_usd"))
        out.append({
            "service": item.get("service", ""),
            "monthly_usd": monthly,
            "prorated_usd": monthly * months,
            "note": item.get("note", ""),
        })
    return out


def _num(value: Any) -> float:
    """Float best-effort: `None`/no-numérico → 0.0 (nunca lanza)."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def pricing_meta(pricing: dict) -> dict:
    """Metadatos de presentación de la tabla vigente (moneda y FX para el front)."""
    return {
        "base_currency": pricing.get("base_currency", "USD"),
        "display_currency": pricing.get("display_currency", "USD"),
        "fx_rate": _num(pricing.get("fx_rate")) or 1.0,
        "pricing_version": str(pricing.get("version", "unknown")),
    }


# ─────────────────────────── pipelines (puros, testables) ───────────────────────

def _match(start: datetime, end: datetime) -> dict:
    return {"$match": {"ts": {"$gte": start, "$lt": end}}}


def pipeline_by_service(start: datetime, end: datetime) -> list[dict]:
    """Variable por servicio en [start, end): costo total y nº de eventos."""
    return [
        _match(start, end),
        {"$group": {"_id": "$service", "cost_usd": {"$sum": "$cost_usd"}, "events": {"$sum": 1}}},
        {"$sort": {"cost_usd": -1}},
    ]


def pipeline_distinct_jobs(start: datetime, end: datetime) -> list[dict]:
    """Cuenta de jobs distintos con al menos un evento en el rango."""
    return [
        _match(start, end),
        {"$group": {"_id": "$job_id"}},
        {"$count": "jobs"},
    ]


def pipeline_timeseries(start: datetime, end: datetime, fmt: str) -> list[dict]:
    """Costo por (bucket temporal, servicio). `fmt` es el patrón de `$dateToString`."""
    return [
        _match(start, end),
        {"$group": {
            "_id": {"bucket": {"$dateToString": {"format": fmt, "date": "$ts"}}, "service": "$service"},
            "cost_usd": {"$sum": "$cost_usd"},
        }},
        {"$sort": {"_id.bucket": 1}},
    ]


def pipeline_by_job(start: datetime, end: datetime, limit: int = _BY_JOB_LIMIT) -> list[dict]:
    """Costo por job con desglose por servicio, más reciente primero."""
    return [
        _match(start, end),
        {"$group": {
            "_id": {"job_id": "$job_id", "service": "$service"},
            "cost_usd": {"$sum": "$cost_usd"},
            "flow": {"$first": "$flow"},
            "batch_id": {"$first": "$batch_id"},
            "source_type": {"$first": "$source_type"},
            "ts": {"$min": "$ts"},
        }},
        {"$group": {
            "_id": "$_id.job_id",
            "cost_usd": {"$sum": "$cost_usd"},
            "by_service": {"$push": {"service": "$_id.service", "cost_usd": "$cost_usd"}},
            "flow": {"$first": "$flow"},
            "batch_id": {"$first": "$batch_id"},
            "source_type": {"$first": "$source_type"},
            "ts": {"$min": "$ts"},
        }},
        {"$sort": {"ts": -1}},
        {"$limit": limit},
    ]


# ─────────────────────────── reshape puro de la serie ───────────────────────────

def reshape_timeseries(rows: list[dict]) -> dict:
    """Convierte las filas `{_id:{bucket,service}, cost_usd}` en series por servicio.

    Devuelve `{buckets: [...orden...], series: {service: [costo por bucket]}, total: x}`
    con los buckets ordenados y cada servicio alineado a esos buckets (0 si falta).
    """
    buckets: list[str] = []
    seen: set[str] = set()
    services: set[str] = set()
    cell: dict[tuple[str, str], float] = {}
    total = 0.0
    for r in rows:
        bucket = r["_id"]["bucket"]
        service = r["_id"].get("service") or "desconocido"
        cost = _num(r.get("cost_usd"))
        if bucket not in seen:
            seen.add(bucket)
            buckets.append(bucket)
        services.add(service)
        cell[(bucket, service)] = cell.get((bucket, service), 0.0) + cost
        total += cost
    buckets.sort()
    series = {
        svc: [round(cell.get((b, svc), 0.0), 6) for b in buckets]
        for svc in sorted(services)
    }
    return {"buckets": buckets, "series": series, "total_usd": round(total, 6)}


# ─────────────────────────── ejecución best-effort ──────────────────────────────

async def _aggregate(pipeline: list[dict]) -> list[dict]:
    """Corre una agregación en `usage_events`. Best-effort: si Mongo no está, `[]`."""
    try:
        coll = await db.get_usage_events()
        if coll is None:
            return []
        return [doc async for doc in coll.aggregate(pipeline)]
    except Exception:  # noqa: BLE001 — el dashboard nunca debe reventar por Mongo
        logger.warning("Falló la agregación de costos", exc_info=True)
        return []


# ─────────────────────────── consultas públicas (async) ─────────────────────────

async def summary(period: str, *, pricing: Optional[dict] = None) -> dict:
    """Totales del período: variable por servicio + fijos prorrateados + total."""
    pricing = pricing if pricing is not None else cost_calc.load_pricing()
    start, end, kind = parse_period(period)

    by_service_rows = await _aggregate(pipeline_by_service(start, end))
    jobs_rows = await _aggregate(pipeline_distinct_jobs(start, end))

    by_service = [
        {"service": r["_id"] or "desconocido", "cost_usd": round(_num(r.get("cost_usd")), 6),
         "events": int(r.get("events", 0))}
        for r in by_service_rows
    ]
    variable_total = round(sum(s["cost_usd"] for s in by_service), 6)
    events_count = sum(s["events"] for s in by_service)

    months = months_in_range(start, end)
    fixed_items = prorate_fixed(pricing, months)
    fixed_total = round(sum(i["prorated_usd"] for i in fixed_items), 6)

    jobs_count = int(jobs_rows[0]["jobs"]) if jobs_rows else 0
    avg_per_job = round(variable_total / jobs_count, 6) if jobs_count else 0.0

    return {
        "period": period,
        "kind": kind,
        "months": months,
        **pricing_meta(pricing),
        "variable": {"by_service": by_service, "total_usd": variable_total},
        "fixed": {"items": fixed_items, "total_usd": fixed_total},
        "total_usd": round(variable_total + fixed_total, 6),
        "events_count": events_count,
        "jobs_count": jobs_count,
        "avg_cost_per_job_usd": avg_per_job,
    }


async def timeseries(date_from: str, date_to: str, granularity: str = "day",
                     *, pricing: Optional[dict] = None) -> dict:
    """Serie temporal por servicio entre `date_from` y `date_to` (incl. el día `to`)."""
    pricing = pricing if pricing is not None else cost_calc.load_pricing()
    granularity = granularity if granularity in _BUCKET_FMT else "day"
    now = datetime.now(timezone.utc)
    # Defaults: últimos 30 días (día) o últimos 12 meses (mes).
    default_from = now - timedelta(days=365 if granularity == "month" else 30)
    start = parse_date(date_from, default_from)
    # `to` es inclusivo para el usuario → sumamos un día para el `$lt`.
    end = parse_date(date_to, now) + timedelta(days=1)
    rows = await _aggregate(pipeline_timeseries(start, end, _BUCKET_FMT[granularity]))
    shaped = reshape_timeseries(rows)
    return {
        "granularity": granularity,
        "from": start.strftime("%Y-%m-%d"),
        "to": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
        **pricing_meta(pricing),
        **shaped,
    }


async def by_job(date_from: str, date_to: str, *, pricing: Optional[dict] = None) -> dict:
    """Costo por job (gasto por uso) en el rango, con desglose por servicio."""
    pricing = pricing if pricing is not None else cost_calc.load_pricing()
    now = datetime.now(timezone.utc)
    start = parse_date(date_from, now - timedelta(days=30))
    end = parse_date(date_to, now) + timedelta(days=1)
    rows = await _aggregate(pipeline_by_job(start, end))
    jobs = []
    for r in rows:
        jobs.append({
            "job_id": r.get("_id"),
            "flow": r.get("flow"),
            "batch_id": r.get("batch_id"),
            "source_type": r.get("source_type"),
            "ts": _iso(r.get("ts")),
            "cost_usd": round(_num(r.get("cost_usd")), 6),
            "by_service": [
                {"service": s.get("service") or "desconocido", "cost_usd": round(_num(s.get("cost_usd")), 6)}
                for s in (r.get("by_service") or [])
            ],
        })
    return {
        "from": start.strftime("%Y-%m-%d"),
        "to": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
        **pricing_meta(pricing),
        "jobs": jobs,
        "count": len(jobs),
    }


async def events(*, service: str = "", limit: int = 50, skip: int = 0) -> dict:
    """Eventos crudos paginados (auditoría), más recientes primero."""
    limit = max(1, min(_EVENTS_MAX_LIMIT, int(limit or 50)))
    skip = max(0, int(skip or 0))
    query: dict = {}
    if service:
        query["service"] = service
    try:
        coll = await db.get_usage_events()
        if coll is None:
            return {"events": [], "count": 0, "skip": skip, "limit": limit}
        cursor = coll.find(query).sort("ts", -1).skip(skip).limit(limit)
        docs = [_serialize_event(d) async for d in cursor]
        return {"events": docs, "count": len(docs), "skip": skip, "limit": limit}
    except Exception:  # noqa: BLE001 — best-effort
        logger.warning("Falló la consulta de eventos", exc_info=True)
        return {"events": [], "count": 0, "skip": skip, "limit": limit}


# ─────────────────────────── serialización ──────────────────────────────────────

def _iso(value: Any) -> Optional[str]:
    """datetime → ISO-8601 string; cualquier otra cosa pasa tal cual."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _serialize_event(doc: dict) -> dict:
    """Hace JSON-serializable un `usage_event` crudo (ObjectId/ts → string)."""
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    if "ts" in out:
        out["ts"] = _iso(out["ts"])
    return out
