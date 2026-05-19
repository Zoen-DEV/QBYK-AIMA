import asyncio
import json
import uuid
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response

from config import load_config
from job_runner import run_pipeline

app = FastAPI(title="repurpose-youtube-video API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4321", "http://127.0.0.1:4321"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
jobs: dict[str, dict] = {}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/jobs")
async def create_job(
    youtube_url: Annotated[str, Form()],
    tono: Annotated[str, Form()] = "",
    tono_linkedin: Annotated[str, Form()] = "",
    tono_instagram: Annotated[str, Form()] = "",
    objetivo: Annotated[str, Form()] = "",
    objetivo_linkedin: Annotated[str, Form()] = "",
    objetivo_instagram: Annotated[str, Form()] = "",
    formato_instagram: Annotated[str, Form()] = "imagen-unica",
    idioma: Annotated[str, Form()] = "auto",
    solo: Annotated[str, Form()] = "",
    dry_run: Annotated[bool, Form()] = False,
    publicar: Annotated[str, Form()] = "",
):
    try:
        cfg = load_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    job_id = str(uuid.uuid4())
    job: dict = {
        "id": job_id,
        "status": "running",
        "params": {
            "youtube_url": youtube_url,
            "tono": tono,
            "tono_linkedin": tono_linkedin,
            "tono_instagram": tono_instagram,
            "objetivo": objetivo,
            "objetivo_linkedin": objetivo_linkedin,
            "objetivo_instagram": objetivo_instagram,
            "formato_instagram": formato_instagram,
            "idioma": idioma,
            "solo": solo,
            "dry_run": dry_run,
            "publicar": publicar,
        },
        "content": {},
        "accounts": {},
        "posts": {},
        "images": {"bytes": {}, "blotato_urls": {"linkedin": "", "instagram": []}, "base_urls": {"linkedin": [], "instagram": []}},
        "result": {},
        "error_msg": None,
        "_queue": asyncio.Queue(),
        "_cfg": cfg,
        "_li_media_urls": [],
        "_ig_media_urls": [],
    }
    jobs[job_id] = job
    asyncio.create_task(run_pipeline(job))
    return {"job_id": job_id}


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
            "ig_slides": [k for k in ["ig-0", "ig-1", "ig-2"] if k in job["images"]["bytes"]],
        },
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
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent / "scripts"))
    import blotato_client as bc

    if job_id not in jobs:
        raise HTTPException(status_code=404)

    job = jobs[job_id]
    cfg = job["_cfg"]
    posts = job["posts"]
    accounts = job["accounts"]
    params = job["params"]
    dry_run = params.get("dry_run", False)
    solo = params.get("solo", "")

    li_text = posts.get("linkedin_text", "")
    ig_text = posts.get("instagram_text", "")
    li_media = job.get("_li_media_urls", [])
    ig_media = job.get("_ig_media_urls", [])

    result: dict = {}
    scheduled_at = schedule_time.strip() or None

    loop = asyncio.get_event_loop()

    async def _run(fn, *args, **kwargs):
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    if not dry_run and solo != "instagram" and accounts.get("linkedin_id") and li_text:
        try:
            resp = await _run(bc.publish_post, accounts["linkedin_id"], "linkedin", li_text, li_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["linkedin"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": status.get("postUrl")}
        except Exception as e:
            result["linkedin"] = {"error": str(e)}

    if not dry_run and solo != "linkedin" and accounts.get("instagram_id") and ig_text:
        try:
            resp = await _run(bc.publish_post, accounts["instagram_id"], "instagram", ig_text, ig_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at, share_to_feed=True)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["instagram"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": status.get("postUrl")}
        except Exception as e:
            result["instagram"] = {"error": str(e)}

    if dry_run:
        result["dry_run"] = True
        result["linkedin"] = {"status": "dry-run"}
        result["instagram"] = {"status": "dry-run"}

    job["result"] = result
    job["status"] = "done"
    return result
