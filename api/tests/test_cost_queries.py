"""Tests de las agregaciones de costos (Fase 3).

Cubren los helpers **puros** (período, prorrateo de fijos, reshape de la serie,
forma de los pipelines) y la garantía **best-effort** de las consultas async:
cuando Mongo no está disponible devuelven estructuras vacías coherentes, nunca
lanzan. La ejecución contra un Mongo real no se prueba aquí (es integración).
"""

from datetime import datetime, timezone

import pytest

import cost_queries
import db


PRICING = {
    "version": "test-q",
    "base_currency": "USD",
    "display_currency": "COP",
    "fx_rate": 4000.0,
    "fixed_monthly": [
        {"service": "blotato", "monthly_usd": 50, "note": "plan x"},
        {"service": "higgsfield", "monthly_usd": 30, "note": "sub"},
    ],
}


# ------------------------------- parse_period --------------------------------

def test_parse_period_month():
    start, end, kind = cost_queries.parse_period("2026-06")
    assert kind == "month"
    assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_parse_period_month_december_rolls_year():
    start, end, kind = cost_queries.parse_period("2026-12")
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_parse_period_year():
    start, end, kind = cost_queries.parse_period("2026")
    assert kind == "year"
    assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["", "2026-13", "2026-00", "junio", "26-6", "2026/06"])
def test_parse_period_invalid(bad):
    with pytest.raises(ValueError):
        cost_queries.parse_period(bad)


# ------------------------------ months_in_range ------------------------------

def test_months_in_range_month_is_one():
    start, end, _ = cost_queries.parse_period("2026-06")
    assert cost_queries.months_in_range(start, end) == 1


def test_months_in_range_year_is_twelve():
    start, end, _ = cost_queries.parse_period("2026")
    assert cost_queries.months_in_range(start, end) == 12


# -------------------------------- parse_date ---------------------------------

def test_parse_date_full_and_month_and_default():
    default = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert cost_queries.parse_date("2026-06-15", default) == datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert cost_queries.parse_date("2026-06", default) == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert cost_queries.parse_date("", default) is default


def test_parse_date_invalid():
    with pytest.raises(ValueError):
        cost_queries.parse_date("15-06-2026", datetime.now(timezone.utc))


# ------------------------------- prorate_fixed -------------------------------

def test_prorate_fixed_month():
    items = cost_queries.prorate_fixed(PRICING, months=1)
    assert items[0] == {"service": "blotato", "monthly_usd": 50.0, "prorated_usd": 50.0, "note": "plan x"}
    assert items[1]["prorated_usd"] == 30.0


def test_prorate_fixed_year_multiplies_by_twelve():
    items = cost_queries.prorate_fixed(PRICING, months=12)
    assert items[0]["prorated_usd"] == 600.0
    assert items[1]["prorated_usd"] == 360.0


def test_prorate_fixed_handles_missing_and_null():
    pricing = {"fixed_monthly": [{"service": "x"}, {"service": "y", "monthly_usd": None}]}
    items = cost_queries.prorate_fixed(pricing, months=1)
    assert items[0]["prorated_usd"] == 0.0
    assert items[1]["prorated_usd"] == 0.0


def test_prorate_fixed_empty_pricing():
    assert cost_queries.prorate_fixed({}, months=1) == []


# ------------------------------- pricing_meta --------------------------------

def test_pricing_meta_exposes_fx_and_currency():
    meta = cost_queries.pricing_meta(PRICING)
    assert meta == {
        "base_currency": "USD",
        "display_currency": "COP",
        "fx_rate": 4000.0,
        "pricing_version": "test-q",
    }


def test_pricing_meta_defaults_fx_to_one():
    assert cost_queries.pricing_meta({})["fx_rate"] == 1.0


# ----------------------------- pipelines (forma) -----------------------------

def test_pipeline_by_service_matches_range():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    pipe = cost_queries.pipeline_by_service(start, end)
    assert pipe[0] == {"$match": {"ts": {"$gte": start, "$lt": end}}}
    assert pipe[1]["$group"]["_id"] == "$service"


def test_pipeline_timeseries_uses_format():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    pipe = cost_queries.pipeline_timeseries(start, end, "%Y-%m-%d")
    group_id = pipe[1]["$group"]["_id"]
    assert group_id["bucket"] == {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}}
    assert group_id["service"] == "$service"


def test_pipeline_by_job_limits_and_groups_twice():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    pipe = cost_queries.pipeline_by_job(start, end, limit=10)
    assert pipe[-1] == {"$limit": 10}
    assert sum(1 for s in pipe if "$group" in s) == 2


# ----------------------------- reshape_timeseries ----------------------------

def test_reshape_timeseries_aligns_services_to_buckets():
    rows = [
        {"_id": {"bucket": "2026-06-01", "service": "anthropic"}, "cost_usd": 1.0},
        {"_id": {"bucket": "2026-06-01", "service": "higgsfield"}, "cost_usd": 2.0},
        {"_id": {"bucket": "2026-06-02", "service": "anthropic"}, "cost_usd": 0.5},
    ]
    shaped = cost_queries.reshape_timeseries(rows)
    assert shaped["buckets"] == ["2026-06-01", "2026-06-02"]
    assert shaped["series"]["anthropic"] == [1.0, 0.5]
    # higgsfield no tiene evento en el día 2 → 0 alineado al bucket.
    assert shaped["series"]["higgsfield"] == [2.0, 0.0]
    assert shaped["total_usd"] == 3.5


def test_reshape_timeseries_empty():
    shaped = cost_queries.reshape_timeseries([])
    assert shaped == {"buckets": [], "series": {}, "total_usd": 0.0}


# --------------------------- best-effort sin Mongo ---------------------------

async def test_summary_empty_when_mongo_unavailable(monkeypatch):
    async def _none():
        return None
    monkeypatch.setattr(db, "get_usage_events", _none)
    out = await cost_queries.summary("2026-06", pricing=PRICING)
    assert out["variable"]["total_usd"] == 0.0
    assert out["variable"]["by_service"] == []
    # Los fijos sí salen (vienen de pricing.json, no de Mongo).
    assert out["fixed"]["total_usd"] == 80.0
    assert out["total_usd"] == 80.0
    assert out["jobs_count"] == 0
    assert out["avg_cost_per_job_usd"] == 0.0
    assert out["fx_rate"] == 4000.0


async def test_summary_aggregates_when_available(monkeypatch):
    class _Cursor:
        def __init__(self, docs):
            self._docs = docs
        def __aiter__(self):
            async def gen():
                for d in self._docs:
                    yield d
            return gen()

    class _Coll:
        def aggregate(self, pipeline):
            # Distingue el pipeline de servicios del de jobs distintos por su $group.
            group_id = pipeline[1]["$group"]["_id"]
            if group_id == "$service":
                return _Cursor([
                    {"_id": "anthropic", "cost_usd": 3.0, "events": 2},
                    {"_id": "higgsfield", "cost_usd": 1.0, "events": 1},
                ])
            return _Cursor([{"jobs": 2}])

    async def _coll():
        return _Coll()

    monkeypatch.setattr(db, "get_usage_events", _coll)
    out = await cost_queries.summary("2026-06", pricing=PRICING)
    assert out["variable"]["total_usd"] == 4.0
    assert out["events_count"] == 3
    assert out["jobs_count"] == 2
    assert out["avg_cost_per_job_usd"] == 2.0
    # variable (4) + fijos (80) = 84
    assert out["total_usd"] == 84.0


async def test_timeseries_empty_when_mongo_unavailable(monkeypatch):
    async def _none():
        return None
    monkeypatch.setattr(db, "get_usage_events", _none)
    out = await cost_queries.timeseries("2026-06-01", "2026-06-30", "day", pricing=PRICING)
    assert out["buckets"] == []
    assert out["series"] == {}
    assert out["granularity"] == "day"


async def test_by_job_empty_when_mongo_unavailable(monkeypatch):
    async def _none():
        return None
    monkeypatch.setattr(db, "get_usage_events", _none)
    out = await cost_queries.by_job("2026-06-01", "2026-06-30", pricing=PRICING)
    assert out["jobs"] == []
    assert out["count"] == 0


async def test_events_empty_when_mongo_unavailable(monkeypatch):
    async def _none():
        return None
    monkeypatch.setattr(db, "get_usage_events", _none)
    out = await cost_queries.events(limit=10)
    assert out == {"events": [], "count": 0, "skip": 0, "limit": 10}
