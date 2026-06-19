"""Tests del registro de eventos (Fase 1).

Cubren `build_event` (forma del documento + costo congelado, sin I/O) y la garantía
**best-effort** de `record_event`: nunca lanza, ni cuando el tracking está apagado,
ni cuando la inserción en Mongo falla.
"""

from datetime import datetime, timezone

import pytest

import cost_tracker
import db


PRICING = {
    "version": "test-1",
    "anthropic": {
        "claude-sonnet-4-6": {
            "input_per_1m": 3.00,
            "output_per_1m": 15.00,
            "cache_write_5m_per_1m": 3.75,
            "cache_read_per_1m": 0.30,
        }
    },
}


# --------------------------------- build_event -------------------------------

def test_build_event_shape_and_frozen_cost():
    units = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }
    ev = cost_tracker.build_event(
        service="anthropic",
        operation="post_writing",
        units=units,
        model="claude-sonnet-4-6",
        flow="bulk",
        job_id="job-1",
        batch_id="batch-1",
        source_type="youtube",
        platforms=["linkedin", "instagram"],
        dry_run=False,
        pricing=PRICING,
    )
    assert ev["service"] == "anthropic"
    assert ev["operation"] == "post_writing"
    assert ev["model"] == "claude-sonnet-4-6"
    assert ev["flow"] == "bulk"
    assert ev["job_id"] == "job-1"
    assert ev["batch_id"] == "batch-1"
    assert ev["source_type"] == "youtube"
    assert ev["platforms"] == ["linkedin", "instagram"]
    assert ev["dry_run"] is False
    assert ev["status"] == "success"
    assert ev["pricing_version"] == "test-1"
    # 3 + 15 + 3.75 + 0.30
    assert ev["cost_usd"] == pytest.approx(22.05, rel=1e-9)
    # ts es datetime tz-aware en UTC
    assert isinstance(ev["ts"], datetime)
    assert ev["ts"].tzinfo == timezone.utc


def test_build_event_defaults():
    ev = cost_tracker.build_event(
        service="higgsfield",
        operation="image_generation",
        units={"generations": 4},
        pricing={"higgsfield": {"image_per_generation": 0.10}},
    )
    assert ev["flow"] == "individual"      # default
    assert ev["batch_id"] is None
    assert ev["job_id"] is None
    assert ev["platforms"] == []
    assert ev["meta"] == {}
    assert ev["cost_usd"] == pytest.approx(0.40, rel=1e-9)


def test_build_event_copies_units_and_meta():
    units = {"generations": 1}
    meta = {"request_id": "abc"}
    ev = cost_tracker.build_event(
        service="higgsfield", operation="video_generation", units=units, meta=meta,
        pricing={"higgsfield": {"video_per_generation": 0.5}},
    )
    units["generations"] = 999
    meta["request_id"] = "changed"
    # El evento guardó copias, no referencias al dict del llamador.
    assert ev["units"] == {"generations": 1}
    assert ev["meta"] == {"request_id": "abc"}


# --------------------------- record_event best-effort -------------------------

async def test_record_event_noop_when_not_configured(monkeypatch):
    async def _none():
        return None
    monkeypatch.setattr(db, "get_usage_events", _none)
    result = await cost_tracker.record_event(
        service="anthropic", operation="post_writing",
        units={"input_tokens": 100}, model="claude-sonnet-4-6", pricing=PRICING,
    )
    assert result is None  # no se escribió nada, no se lanzó nada


async def test_record_event_inserts_when_available(monkeypatch):
    inserted = []

    class _FakeColl:
        async def insert_one(self, doc):
            doc["_id"] = "fake-id"
            inserted.append(doc)
            return type("R", (), {"inserted_id": "fake-id"})()

    async def _coll():
        return _FakeColl()

    monkeypatch.setattr(db, "get_usage_events", _coll)
    result = await cost_tracker.record_event(
        service="anthropic", operation="post_writing",
        units={"input_tokens": 1_000_000, "output_tokens": 0},
        model="claude-sonnet-4-6", pricing=PRICING,
    )
    assert result is not None
    assert len(inserted) == 1
    assert inserted[0]["cost_usd"] == pytest.approx(3.00, rel=1e-9)
    assert inserted[0]["_id"] == "fake-id"


async def test_record_event_swallows_insert_errors(monkeypatch):
    class _BoomColl:
        async def insert_one(self, doc):
            raise RuntimeError("mongo caído")

    async def _coll():
        return _BoomColl()

    monkeypatch.setattr(db, "get_usage_events", _coll)
    # No debe propagar la excepción: best-effort.
    result = await cost_tracker.record_event(
        service="whisper", operation="transcription",
        units={"minutes": 5}, model="whisper-1", pricing={"whisper": {"whisper-1": {"per_minute": 0.006}}},
    )
    assert result is None


# ------------------------------- db.is_configured -----------------------------

def test_db_not_configured_when_uri_empty(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    assert db.is_configured() is False
