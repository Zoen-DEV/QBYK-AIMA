import asyncio
import json
import uuid
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response

import sheets
from config import load_config
from job_runner import run_pipeline, make_job, publish_job_posts
from batch_runner import run_batch, to_utc_iso

app = FastAPI(title="repurpose-youtube-video API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4321", "http://127.0.0.1:4321"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
jobs: dict[str, dict] = {}
# In-memory batch store (creación en lote desde un sheet)
batches: dict[str, dict] = {}


@app.get("/health")
def health():
    return {"status": "ok"}


def _account_label(acc: dict) -> str:
    """Best-effort human label for a Blotato account (shape varies).

    Live Blotato shapes: LinkedIn fills `fullname` (e.g. "Juan Jose Cano"),
    Instagram fills `username` (e.g. "qbyk_aima"). Keep both, plus other
    variants seen across platforms, before falling back to the bare id.
    """
    for k in ("displayName", "fullname", "name", "username", "handle", "title", "email"):
        v = acc.get(k)
        if v:
            return str(v)
    return f"Cuenta {acc.get('id', '')}"


@app.get("/accounts")
def list_accounts():
    """List the user's connected Blotato accounts per platform for the UI selectors.

    Falls back to the .env account IDs (as a synthetic single-item list) when the
    Blotato listing call fails, so the form can still offer the configured account.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent / "scripts"))
    import blotato_client as bc

    try:
        cfg = load_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    out: dict[str, list[dict]] = {}
    env_fallback = {"linkedin": cfg.linkedin_account_id, "instagram": cfg.instagram_account_id}
    for platform in ("linkedin", "instagram"):
        try:
            accounts = bc.get_accounts(platform, api_key=cfg.blotato_api_key)
            out[platform] = [{"id": str(a.get("id", "")), "label": _account_label(a)} for a in accounts if a.get("id")]
        except Exception as e:
            out[platform] = []
            out[f"{platform}_error"] = str(e)
        # Ensure the .env-configured account is selectable even if listing failed/omitted it.
        env_id = env_fallback[platform]
        if env_id and not any(a["id"] == env_id for a in out[platform]):
            out[platform].insert(0, {"id": env_id, "label": f"Configurada en .env ({env_id})"})

    # Attach the LinkedIn Company Pages each account can post to (the form offers them
    # as a second selector). Failures per account are non-fatal — the account stays
    # selectable for the personal profile.
    for acc in out.get("linkedin", []):
        try:
            subs = bc.get_subaccounts(acc["id"], api_key=cfg.blotato_api_key)
            acc["pages"] = [{"id": str(p.get("id", "")), "name": str(p.get("name", ""))} for p in subs if p.get("id")]
        except Exception:
            acc["pages"] = []

    return out


@app.post("/jobs")
async def create_job(
    source_type: Annotated[str, Form()] = "youtube",
    youtube_url: Annotated[str, Form()] = "",
    media_file: Annotated[UploadFile | None, File()] = None,
    tono: Annotated[str, Form()] = "",
    tono_linkedin: Annotated[str, Form()] = "",
    tono_instagram: Annotated[str, Form()] = "",
    objetivo: Annotated[str, Form()] = "",
    objetivo_linkedin: Annotated[str, Form()] = "",
    objetivo_instagram: Annotated[str, Form()] = "",
    formato_instagram: Annotated[str, Form()] = "imagen-unica",
    carrusel_slides: Annotated[int, Form()] = 3,
    tipo_medio: Annotated[str, Form()] = "imagen",
    idioma: Annotated[str, Form()] = "auto",
    modelo_perplexity: Annotated[str, Form()] = "sonar-pro",
    linkedin_account_id: Annotated[str, Form()] = "",
    linkedin_page_id: Annotated[str, Form()] = "",
    instagram_account_id: Annotated[str, Form()] = "",
    solo: Annotated[str, Form()] = "",
    dry_run: Annotated[bool, Form()] = False,
    publicar: Annotated[str, Form()] = "",
):
    try:
        cfg = load_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Resolve the content source. "youtube" needs a URL; "audio"/"texto" need an
    # uploaded file whose bytes we read now (the UploadFile is tied to this request,
    # but the pipeline runs asynchronously after we return).
    source_type = (source_type or "youtube").strip().lower()
    if source_type not in ("youtube", "audio", "texto"):
        raise HTTPException(status_code=400, detail=f"source_type inválido: {source_type}")

    upload_bytes = b""
    upload_filename = ""
    if source_type == "youtube":
        if not youtube_url.strip():
            raise HTTPException(status_code=400, detail="Falta la URL de YouTube")
    else:
        if media_file is None:
            raise HTTPException(status_code=400, detail="Falta el archivo de audio/texto")
        upload_bytes = await media_file.read()
        if not upload_bytes:
            raise HTTPException(status_code=400, detail="El archivo está vacío")
        default_name = "audio.ogg" if source_type == "audio" else "texto.txt"
        upload_filename = media_file.filename or default_name

    params = {
        "source_type": source_type,
        "youtube_url": youtube_url,
        "upload_filename": upload_filename,
        "tono": tono,
        "tono_linkedin": tono_linkedin,
        "tono_instagram": tono_instagram,
        "objetivo": objetivo,
        "objetivo_linkedin": objetivo_linkedin,
        "objetivo_instagram": objetivo_instagram,
        "formato_instagram": formato_instagram,
        "carrusel_slides": max(3, min(6, carrusel_slides)),
        "tipo_medio": tipo_medio,
        "idioma": idioma,
        "modelo_perplexity": modelo_perplexity,
        "linkedin_account_id": linkedin_account_id.strip(),
        "linkedin_page_id": linkedin_page_id.strip(),
        "instagram_account_id": instagram_account_id.strip(),
        "solo": solo,
        "dry_run": dry_run,
        "publicar": publicar,
    }
    job = make_job(cfg, params, upload_bytes=upload_bytes, upload_filename=upload_filename)
    jobs[job["id"]] = job
    asyncio.create_task(run_pipeline(job))
    return {"job_id": job["id"]}


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]

    async def generator():
        q: asyncio.Queue = job["_queue"]
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=60.0)
            except asyncio.TimeoutError:
                yield "data: {\"step\": \"ping\"}\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("step") in ("done", "error"):
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    # Serializable snapshot (exclude queue, config, raw bytes)
    return {
        "id": job["id"],
        "status": job["status"],
        "params": job["params"],
        "content": {k: v for k, v in job["content"].items() if k != "transcript"},
        "posts": job["posts"],
        "images": {
            "blotato_urls": job["images"]["blotato_urls"],
            "has_li_hook": "li-hook" in job["images"]["bytes"],
            "has_ig_single": "ig-single" in job["images"]["bytes"],
            "has_ig_carousel": any(k.startswith("ig-") and k != "ig-single" for k in job["images"]["bytes"]),
            "ig_slides": [k for k in (f"ig-{i}" for i in range(6)) if k in job["images"]["bytes"]],
            "provider": job["images"].get("provider", ""),
            "notice": job["images"].get("notice", ""),
        },
        "video": job.get("video", {"url": "", "provider": "", "notice": ""}),
        "li_media_urls": job.get("_li_media_urls", []),
        "ig_media_urls": job.get("_ig_media_urls", []),
        "result": job["result"],
        "error_msg": job["error_msg"],
    }


@app.post("/jobs/{job_id}/edit")
async def edit_job(
    job_id: str,
    linkedin_text: Annotated[str, Form()] = "",
    instagram_text: Annotated[str, Form()] = "",
):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    if linkedin_text:
        job["posts"]["linkedin_text"] = linkedin_text
    if instagram_text:
        job["posts"]["instagram_text"] = instagram_text
    return {"posts": job["posts"]}


@app.get("/jobs/{job_id}/image/{key}")
def serve_image(job_id: str, key: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    img_bytes = job["images"]["bytes"].get(key)
    if not img_bytes:
        raise HTTPException(status_code=404)
    return Response(content=img_bytes, media_type="image/png")


@app.post("/jobs/{job_id}/publish")
async def publish_job(
    job_id: str,
    schedule_time: Annotated[str, Form()] = "",
):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    return await publish_job_posts(jobs[job_id], schedule_time)


# ── Creación en lote desde un sheet (.xlsx / .csv) ───────────────────────────────

@app.get("/sheets/template")
def sheets_template():
    """Descarga la plantilla .xlsx para llenar y volver a subir."""
    data = sheets.build_template_xlsx()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="aima-plantilla-posts.xlsx"'},
    )


@app.post("/sheets/jobs")
async def create_sheet_batch(
    sheet_file: Annotated[UploadFile, File()],
    linkedin_account_id: Annotated[str, Form()] = "",
    linkedin_page_id: Annotated[str, Form()] = "",
    instagram_account_id: Annotated[str, Form()] = "",
    dry_run: Annotated[bool, Form()] = False,
    tz_offset: Annotated[int, Form()] = 0,
):
    """Parsea el sheet, crea un batch y lanza la generación + programación por fila.

    Las cuentas y el dry-run son globales (de la UI) y se inyectan en cada fila.
    `tz_offset` (minutos, de Date.getTimezoneOffset()) convierte cada fecha/hora
    local del sheet a UTC para programar en Blotato.
    """
    try:
        cfg = load_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    data = await sheet_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    try:
        specs, warnings = sheets.parse_sheet(data, sheet_file.filename or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not specs:
        detail = "No se encontraron filas válidas en el archivo."
        if warnings:
            detail += " " + " ".join(warnings)
        raise HTTPException(status_code=400, detail=detail)

    account_params = {
        "linkedin_account_id": linkedin_account_id.strip(),
        "linkedin_page_id": linkedin_page_id.strip(),
        "instagram_account_id": instagram_account_id.strip(),
    }

    batch_id = str(uuid.uuid4())
    rows = []
    for i, spec in enumerate(specs, start=1):
        dt = spec["schedule_dt"]
        rows.append({
            "index": i,
            "source": spec["source"],
            "label": spec["label"],
            "title": spec["label"],
            "schedule": dt.strftime("%Y-%m-%d %H:%M") if dt else "",
            "schedule_utc": to_utc_iso(dt, tz_offset) or "",
            "status": "queued",
            "job_id": None,
            "result": {},
            "error": None,
            "_spec": spec,
        })

    batch = {
        "id": batch_id,
        "status": "running",
        "warnings": warnings,
        "dry_run": dry_run,
        "tz_offset": tz_offset,
        "account_params": account_params,
        "rows": rows,
        "_cfg": cfg,
    }
    batches[batch_id] = batch
    asyncio.create_task(run_batch(batch, jobs))
    return {"batch_id": batch_id, "warnings": warnings, "count": len(rows)}


def _batch_snapshot(batch: dict) -> dict:
    """Vista serializable del batch (sin el spec interno ni la config)."""
    return {
        "id": batch["id"],
        "status": batch["status"],
        "warnings": batch["warnings"],
        "dry_run": batch["dry_run"],
        "rows": [
            {
                "index": r["index"],
                "source": r["source"],
                "label": r["label"],
                "title": r.get("title") or r["label"],
                "schedule": r["schedule"],
                "status": r["status"],
                "job_id": r["job_id"],
                "result": r["result"],
                "error": r["error"],
            }
            for r in batch["rows"]
        ],
    }


@app.get("/sheets/batches/{batch_id}")
def get_batch(batch_id: str):
    if batch_id not in batches:
        raise HTTPException(status_code=404)
    return _batch_snapshot(batches[batch_id])
