"""Tests de la paridad bulk ↔ individual: el lote recorre las MISMAS compuertas.

El bulk pasó de dos fases a tres (escritura → revisión de guiones → medio →
revisión → publicación). Lo que se cubre acá es que la compuerta nueva exista de
verdad: que `run_batch` NO gaste créditos y deje el lote esperando, y que el medio
solo se genere después de la aprobación explícita.
"""

import batch_runner as br
import job_runner as jr


def test_la_compuerta_de_preview_no_distingue_flujo():
    """La regla es la misma para los dos flujos: lo único que la apaga es preview_step."""
    for flow in ("individual", "bulk"):
        assert jr._wants_preview({"flow": flow, "params": {}}) is True
        assert jr._wants_preview({"flow": flow, "params": {"preview_step": False}}) is False


def _batch(rows: int = 2, **overrides) -> dict:
    return {
        "id": "batch-1",
        "status": "running",
        "dry_run": False,
        "tz_offset": 0,
        "account_params": {},
        "_cfg": object(),
        "rows": [
            {
                "index": i + 1,
                "label": f"fila {i + 1}",
                "status": "queued",
                "job_id": None,
                "result": {},
                "error": None,
                "_spec": {"params": {}, "upload_bytes": None,
                          "upload_filename": None, "schedule_dt": None},
            }
            for i in range(rows)
        ],
        **overrides,
    }


def _fake_pipeline(monkeypatch, *, final_status: str, jobs: dict):
    """Stub de make_job/run_pipeline: deja cada job en `final_status`."""
    made = {"n": 0}

    def make_job(cfg, params, **kw):
        made["n"] += 1
        return {"id": f"job-{made['n']}", "status": "running", "params": params,
                "content": {"title": f"Título {made['n']}"}, "error_msg": ""}

    async def run_pipeline(job):
        job["status"] = final_status

    monkeypatch.setattr(br, "make_job", make_job)
    monkeypatch.setattr(br, "run_pipeline", run_pipeline)
    return made


async def test_run_batch_para_en_preview_sin_generar_medio(monkeypatch):
    """Fase 1: escribe y PARA. Ninguna fila queda lista para publicar todavía."""
    jobs: dict = {}
    _fake_pipeline(monkeypatch, final_status="preview", jobs=jobs)
    resumed = []
    monkeypatch.setattr(br, "resume_media", lambda job: resumed.append(job))

    batch = _batch(rows=3)
    await br.run_batch(batch, jobs)

    assert batch["status"] == "preview"
    assert [r["status"] for r in batch["rows"]] == ["preview"] * 3
    # La compuerta es real: no se tocó la fase de medio (y por tanto, ni un crédito).
    assert resumed == []


async def test_generate_batch_media_solo_tras_aprobar(monkeypatch):
    """Fase 2: `generate_batch_media` reanuda cada fila y deja el lote en review."""
    jobs: dict = {}
    _fake_pipeline(monkeypatch, final_status="preview", jobs=jobs)
    batch = _batch(rows=2)
    await br.run_batch(batch, jobs)

    resumed = []

    async def resume_media(job):
        resumed.append(job["id"])
        job["status"] = "review"

    monkeypatch.setattr(br, "resume_media", resume_media)
    await br.generate_batch_media(batch, jobs)

    assert resumed == ["job-1", "job-2"]
    assert batch["status"] == "review"
    assert [r["status"] for r in batch["rows"]] == ["ready", "ready"]


async def test_sin_compuerta_el_lote_va_directo_a_review(monkeypatch):
    """Con preview_step apagado el pipeline no pausa: el lote salta a la aprobación
    de publicación, como antes. No hay que aprobar guiones que nadie va a ver."""
    jobs: dict = {}
    _fake_pipeline(monkeypatch, final_status="review", jobs=jobs)

    batch = _batch(rows=2)
    await br.run_batch(batch, jobs)

    assert batch["status"] == "review"
    assert [r["status"] for r in batch["rows"]] == ["ready", "ready"]


async def test_fila_que_falla_escribiendo_no_bloquea_la_compuerta(monkeypatch):
    """Una fila con error no impide que el resto llegue a la revisión de guiones."""
    jobs: dict = {}
    made = {"n": 0}

    def make_job(cfg, params, **kw):
        made["n"] += 1
        return {"id": f"job-{made['n']}", "status": "running", "params": params,
                "content": {"title": "t"}, "error_msg": ""}

    async def run_pipeline(job):
        # La segunda fila falla escribiendo.
        if job["id"] == "job-2":
            job["status"] = "error"
            job["error_msg"] = "sin transcripción"
        else:
            job["status"] = "preview"

    monkeypatch.setattr(br, "make_job", make_job)
    monkeypatch.setattr(br, "run_pipeline", run_pipeline)

    batch = _batch(rows=3)
    await br.run_batch(batch, jobs)

    assert batch["status"] == "preview"
    assert [r["status"] for r in batch["rows"]] == ["preview", "error", "preview"]

    async def resume_media(job):
        job["status"] = "review"

    monkeypatch.setattr(br, "resume_media", resume_media)
    await br.generate_batch_media(batch, jobs)

    # La fila rota se deja como está; las otras dos avanzan.
    assert [r["status"] for r in batch["rows"]] == ["ready", "error", "ready"]
    assert batch["status"] == "review"


async def test_error_generando_el_medio_marca_solo_esa_fila(monkeypatch):
    jobs: dict = {}
    _fake_pipeline(monkeypatch, final_status="preview", jobs=jobs)
    batch = _batch(rows=2)
    await br.run_batch(batch, jobs)

    async def resume_media(job):
        if job["id"] == "job-1":
            job["status"] = "error"
            job["error_msg"] = "Higgsfield sin créditos"
        else:
            job["status"] = "review"

    monkeypatch.setattr(br, "resume_media", resume_media)
    await br.generate_batch_media(batch, jobs)

    assert [r["status"] for r in batch["rows"]] == ["error", "ready"]
    assert batch["rows"][0]["error"] == "Higgsfield sin créditos"
    assert batch["status"] == "review"
