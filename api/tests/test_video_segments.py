"""Tests de la rama de video por segmentos (reels).

Cubren lo que hacía que un fallo puntual del proveedor rompiera el reel entero:
reintentos por segmento, corte inmediato ante un error fatal, y la alineación del
guion de voz con los clips que SÍ se generaron (antes, perder un clip dejaba el
reel mudo y sin subtítulos).
"""

import asyncio
import types

import pytest

import job_runner as jr
import higgsfield_mcp as hfmcp


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _job() -> dict:
    return {"id": "job-1", "params": {}, "video": {"url": "", "provider": "", "notice": "", "cost": None}}


def _segments(n: int) -> list[dict]:
    return [{"prompt": f"s{i}", "medias": None} for i in range(n)]


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(jr, "_SEGMENT_RETRY_SLEEP", 0)


def _stub_provider(monkeypatch, *, fail_plan: dict[str, int], error="MCP job status=failed"):
    """Stub de submit/poll: `fail_plan` = cuántas veces falla cada prompt antes de salir.

    Devuelve el registro de llamadas para poder afirmar cuántos submits hubo (un
    reintento por timeout NO debe volver a encolar: eso cobraría otra generación).
    """
    calls = {"submit": [], "poll": []}
    left = dict(fail_plan)

    def submit_video(prompt, **kw):
        calls["submit"].append(prompt)
        return {"job_id": prompt}

    def poll_video(handle, **kw):
        pid = handle["job_id"]
        calls["poll"].append(pid)
        if left.get(pid, 0):
            left[pid] -= 1
            raise RuntimeError(error)
        return {"job_id": pid, "url": f"https://cdn/{pid}.mp4"}

    monkeypatch.setattr(jr.hfmcp, "submit_video", submit_video)
    monkeypatch.setattr(jr.hfmcp, "poll_video", poll_video)
    return calls


# ----------------------------- reintentos ------------------------------------

def test_segment_retried_until_it_succeeds(monkeypatch):
    calls = _stub_provider(monkeypatch, fail_plan={"s1": 2})
    job = _job()
    q: asyncio.Queue = asyncio.Queue()

    results = asyncio.run(jr._generate_segments(job, q, _segments(3), aspect="9:16",
                                                seg_seconds=10, model="kling3_0_turbo"))

    assert [bool(r) for r in results] == [True, True, True]
    assert calls["poll"].count("s1") == 3     # 2 fallos + el bueno
    assert not job["video"]["notice"]


def test_poll_timeout_reuses_the_queued_job(monkeypatch):
    # Un timeout de espera no invalida el job encolado: se vuelve a esperar el mismo
    # handle en vez de encolar (y pagar) otra generación.
    calls = _stub_provider(monkeypatch, fail_plan={"s0": 1},
                           error="MCP poll timeout tras 300s (último estado=in_progress)")
    job = _job()
    results = asyncio.run(jr._generate_segments(job, asyncio.Queue(), _segments(1),
                                                aspect="9:16", seg_seconds=10, model="m"))

    assert results[0]["url"].endswith("s0.mp4")
    assert calls["submit"].count("s0") == 1
    assert calls["poll"].count("s0") == 2


def test_failed_segment_is_dropped_keeping_its_index(monkeypatch):
    _stub_provider(monkeypatch, fail_plan={"s1": jr._SEGMENT_ATTEMPTS})
    job = _job()
    results = asyncio.run(jr._generate_segments(job, asyncio.Queue(), _segments(3),
                                                aspect="9:16", seg_seconds=10, model="m"))

    assert results[1] is None and results[0] and results[2]
    assert "segmento 2 de 3" in job["video"]["notice"]


@pytest.mark.parametrize("exc", [
    hfmcp.ReauthRequired("sesión OAuth inválida"),
    RuntimeError("not_enough_credits (sin créditos en la suscripción de Higgsfield)"),
])
def test_fatal_error_aborts_the_batch(monkeypatch, exc):
    # Sin créditos o con la sesión muerta, reintentar los otros segmentos solo alarga
    # el job (cada espera cuesta minutos) y tapa el motivo real.
    def submit_video(prompt, **kw):
        raise exc

    polled = []
    monkeypatch.setattr(jr.hfmcp, "submit_video", submit_video)
    monkeypatch.setattr(jr.hfmcp, "poll_video", lambda h, **kw: polled.append(h) or {})
    job = _job()

    results = asyncio.run(jr._generate_segments(job, asyncio.Queue(), _segments(4),
                                                aspect="9:16", seg_seconds=10, model="m"))

    assert results == [None, None, None, None]
    assert polled == []
    assert "interrumpida" in job["video"]["notice"]


def test_fatal_mid_batch_still_waits_for_the_queued_clips(monkeypatch):
    # Si los créditos se acaban en el 3er encolado, los dos primeros ya están pagados:
    # se esperan igual (con dos clips todavía sale un reel) en vez de tirarlos.
    submitted = []

    def submit_video(prompt, **kw):
        if len(submitted) >= 2:
            raise RuntimeError("not_enough_credits (sin créditos)")
        submitted.append(prompt)
        return {"job_id": prompt}

    monkeypatch.setattr(jr.hfmcp, "submit_video", submit_video)
    monkeypatch.setattr(jr.hfmcp, "poll_video",
                        lambda h, **kw: {"job_id": h["job_id"], "url": f"https://cdn/{h['job_id']}.mp4"})
    job = _job()

    results = asyncio.run(jr._generate_segments(job, asyncio.Queue(), _segments(4),
                                                aspect="9:16", seg_seconds=10, model="m"))

    assert [bool(r) for r in results] == [True, True, False, False]
    assert "interrumpida" in job["video"]["notice"]


def test_video_warn_accumulates_reasons():
    job = _job()
    jr._video_warn(job, "Falló el 1.")
    jr._video_warn(job, "Falló el 3.")
    assert job["video"]["notice"] == "Falló el 1. Falló el 3."


# ------------------- alineación del guion con los clips vivos ----------------

def _cfg() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        reel_voiceover=True, blotato_api_key="k",
        higgsfield_mcp_video_model="kling3_0_turbo",
        higgsfield_mcp_tts_model="seed_audio",
        higgsfield_subtitle_font="", higgsfield_tts_voice_type="", higgsfield_tts_voice_id="",
    )


def test_voiceover_uses_only_the_lines_of_the_surviving_clips(monkeypatch):
    _stub_provider(monkeypatch, fail_plan={"s1": jr._SEGMENT_ATTEMPTS})
    monkeypatch.setattr(jr.hfmcp, "video_cost", lambda **kw: {})
    monkeypatch.setattr(jr, "_save_video", lambda *a, **kw: None)
    monkeypatch.setattr(jr.bc, "upload_media_local", lambda *a, **kw: "https://blotato/reel.mp4")

    seen = {}

    async def fake_assembly(job, q, cfg, seg_jobs, voice_lines, *, aspect):
        seen["jobs"] = [s["job_id"] for s in seg_jobs]
        seen["lines"] = list(voice_lines)
        return b"mp4", "https://cdn/final.mp4"

    async def no_track(*a, **kw):
        return None

    monkeypatch.setattr(jr, "_voiceover_assembly", fake_assembly)
    monkeypatch.setattr(jr, "_track", no_track)

    job = jr.make_job(_cfg(), {"tipo_post": "reel", "redes": ["instagram"]})
    asyncio.run(jr._run_video_segments(
        job, job["_queue"], _cfg(), _segments(3), aspect="9:16", seg_seconds=10,
        do_linkedin=False, do_instagram=True, do_facebook=False,
        voiceover=["linea 0", "linea 1", "linea 2"],
    ))

    # El clip 2 (índice 1) se cayó: su línea NO debe narrar el clip siguiente.
    assert seen["jobs"] == ["s0", "s2"]
    assert seen["lines"] == ["linea 0", "linea 2"]
    assert job["video"]["url"] == "https://blotato/reel.mp4"
    assert "2 de 3 segmentos" in job["video"]["notice"]


def test_partial_script_skips_voice_with_an_explicit_notice(monkeypatch):
    # Guion con menos líneas que shots (típico tras editar el preview): el reel sale
    # mudo, pero ahora el usuario ve por qué.
    _stub_provider(monkeypatch, fail_plan={})
    monkeypatch.setattr(jr.hfmcp, "video_cost", lambda **kw: {})
    monkeypatch.setattr(jr, "_save_video", lambda *a, **kw: None)
    monkeypatch.setattr(jr.bc, "upload_media_local", lambda *a, **kw: "https://blotato/reel.mp4")
    monkeypatch.setattr(jr.vstitch, "concat_videos", lambda urls, **kw: b"mp4")

    async def boom(*a, **kw):
        raise AssertionError("no debería intentar la voz con el guion incompleto")

    async def no_track(*a, **kw):
        return None

    monkeypatch.setattr(jr, "_voiceover_assembly", boom)
    monkeypatch.setattr(jr, "_track", no_track)

    job = jr.make_job(_cfg(), {"tipo_post": "reel"})
    asyncio.run(jr._run_video_segments(
        job, job["_queue"], _cfg(), _segments(3), aspect="9:16", seg_seconds=10,
        do_linkedin=False, do_instagram=True, do_facebook=False,
        voiceover=["solo una linea"],
    ))

    assert "sin voz" in job["video"]["notice"]
    assert job["video"]["url"] == "https://blotato/reel.mp4"
