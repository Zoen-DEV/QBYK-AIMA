"""Tests de la instrumentación del pipeline (Fase 2).

Cubren las piezas puras que enganchan el tracking sin tocar red:
- normalización de `usage` de Claude y Perplexity (`post_writer`),
- contador de generaciones HF reales vs plantilla (`image_provider`),
- contexto del evento y el glue `_track` del núcleo compartido (`job_runner`).
"""

import types

import pytest

import job_runner  # inserta api/scripts en sys.path al importarse
import post_writer
import image_provider
import cost_tracker


# ----------------------- post_writer: normalización de usage -----------------

def test_anthropic_usage_maps_all_token_fields():
    usage = types.SimpleNamespace(
        input_tokens=1234, output_tokens=567,
        cache_creation_input_tokens=8900, cache_read_input_tokens=200,
    )
    out = post_writer._anthropic_usage(usage)
    assert out["service"] == "anthropic"
    assert out["model"] == "claude-sonnet-4-6"
    assert out["units"] == {
        "input_tokens": 1234, "output_tokens": 567,
        "cache_creation_input_tokens": 8900, "cache_read_input_tokens": 200,
    }


def test_anthropic_usage_none_and_missing_cache_fields():
    assert post_writer._anthropic_usage(None) is None
    # Objeto sin atributos de caché → esos campos caen a 0 (best-effort).
    usage = types.SimpleNamespace(input_tokens=10, output_tokens=20)
    out = post_writer._anthropic_usage(usage)
    assert out["units"]["cache_creation_input_tokens"] == 0
    assert out["units"]["cache_read_input_tokens"] == 0


def test_perplexity_usage_maps_tokens_requests_and_searches():
    out = post_writer._perplexity_usage(
        {"prompt_tokens": 100, "completion_tokens": 50, "num_search_queries": 2}, "sonar-pro"
    )
    assert out["service"] == "perplexity"
    assert out["model"] == "sonar-pro"
    assert out["units"] == {
        "input_tokens": 100, "output_tokens": 50, "requests": 1, "searches": 2,
    }


def test_perplexity_usage_none_when_absent():
    assert post_writer._perplexity_usage(None, "sonar") is None
    assert post_writer._perplexity_usage("not-a-dict", "sonar") is None


# --------------------- image_provider: contador de generaciones --------------

def test_template_provider_never_counts_generations():
    assert image_provider.TemplateProvider().hf_generations == 0


def test_higgsfield_counts_only_successful_generations(monkeypatch):
    prov = image_provider.HiggsfieldProvider("k", "s", model="m", resolution="1080p")

    # generate_base con éxito → +1
    monkeypatch.setattr(image_provider.hf, "generate_image", lambda *a, **k: ["http://img/base.png"])
    assert prov.generate_base("p") == "http://img/base.png"
    assert prov.hf_generations == 1

    # resolve con éxito (poll_image) → +1
    monkeypatch.setattr(image_provider.hf, "poll_image", lambda *a, **k: "http://img/slide.png")
    assert prov.resolve({"id": "x", "template_kind": "info"}) == "http://img/slide.png"
    assert prov.hf_generations == 2


def test_higgsfield_does_not_count_fallbacks(monkeypatch):
    prov = image_provider.HiggsfieldProvider("k", "s", model="m", resolution="1080p")
    # Evita depender de los PNG de plantilla en disco.
    monkeypatch.setattr(image_provider, "_template_path", lambda p: "dummy.png")

    # base falla → fallback a plantilla, NO cuenta.
    def _boom(*a, **k):
        raise RuntimeError("higgsfield caído")
    monkeypatch.setattr(image_provider.hf, "generate_image", _boom)
    prov.generate_base("p")
    assert prov.hf_generations == 0

    # resolve de un handle que ya venía marcado como fallback → NO cuenta.
    prov.resolve({"fallback_template": True, "template_kind": "credits"})
    assert prov.hf_generations == 0


# ------------------------ job_runner: contexto del evento --------------------

def test_event_context_individual_all_platforms():
    job = {"id": "j1", "flow": "individual", "batch_id": None,
           "params": {"source_type": "youtube", "solo": "", "dry_run": False}}
    ctx = job_runner._event_context(job)
    assert ctx == {
        "flow": "individual", "job_id": "j1", "batch_id": None,
        "source_type": "youtube", "platforms": ["linkedin", "instagram", "facebook"],
        "dry_run": False,
    }


def test_event_context_bulk_single_platform():
    job = {"id": "j2", "flow": "bulk", "batch_id": "b1",
           "params": {"source_type": "audio", "solo": "linkedin", "dry_run": True}}
    ctx = job_runner._event_context(job)
    assert ctx["flow"] == "bulk"
    assert ctx["batch_id"] == "b1"
    assert ctx["platforms"] == ["linkedin"]
    assert ctx["dry_run"] is True
    assert ctx["source_type"] == "audio"


# ------------------------ job_runner: glue _track ----------------------------

async def test_track_merges_context_and_calls_record_event(monkeypatch):
    captured = {}

    async def _fake_record_event(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(cost_tracker, "record_event", _fake_record_event)

    job = {"id": "j9", "flow": "bulk", "batch_id": "b9",
           "params": {"source_type": "manual", "solo": "instagram", "dry_run": False}}
    await job_runner._track(
        job, service="anthropic", operation="post_writing",
        units={"input_tokens": 5}, model="claude-sonnet-4-6",
    )
    # Campos explícitos + contexto del job, todo en una sola llamada.
    assert captured["service"] == "anthropic"
    assert captured["operation"] == "post_writing"
    assert captured["units"] == {"input_tokens": 5}
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["flow"] == "bulk"
    assert captured["job_id"] == "j9"
    assert captured["batch_id"] == "b9"
    assert captured["platforms"] == ["instagram"]
    assert captured["source_type"] == "manual"


async def test_track_never_raises_even_if_record_event_throws(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("fallo de métricas")
    monkeypatch.setattr(cost_tracker, "record_event", _boom)
    job = {"id": "j", "flow": "individual", "batch_id": None, "params": {}}
    # No debe propagar: la instrumentación es best-effort.
    await job_runner._track(job, service="whisper", operation="transcription", units={"minutes": 1})


# ------------------------ make_job: shape de ambos flujos --------------------

def test_make_job_tags_flow_individual_by_default():
    job = job_runner.make_job(cfg=None, params={"source_type": "youtube"})
    assert job["flow"] == "individual"
    assert job["batch_id"] is None


def test_make_job_tags_flow_bulk_when_requested():
    job = job_runner.make_job(cfg=None, params={"source_type": "audio"}, flow="bulk", batch_id="b42")
    assert job["flow"] == "bulk"
    assert job["batch_id"] == "b42"
