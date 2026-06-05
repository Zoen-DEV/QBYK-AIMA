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
    youtube_url: Annotated[str, Form()],
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
        },
        "content": {},
        "accounts": {},
        "posts": {},
        "images": {"bytes": {}, "blotato_urls": {"linkedin": "", "instagram": []}, "base_urls": {"linkedin": [], "instagram": []}, "provider": "", "notice": ""},
        "video": {"url": "", "provider": "", "notice": ""},
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


def _post_url(status: dict) -> str | None:
    """Extract the published-post permalink from a Blotato post-status response.

    Blotato exposes the link as `publicUrl` on a published post (per the API
    reference). Some shapes nest the state under `state` and/or use `postUrl`;
    we check the known variants so the "Ver publicación" link is robust.
    """
    if not isinstance(status, dict):
        return None
    for key in ("publicUrl", "postUrl", "url"):
        val = status.get(key)
        if val:
            return val
    state = status.get("state")
    if isinstance(state, dict):
        for key in ("publicUrl", "postUrl", "url"):
            val = state.get(key)
            if val:
                return val
    return None


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
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at,
                              page_id=accounts.get("linkedin_page_id") or None)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["linkedin"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["linkedin"] = {"error": str(e)}

    if not dry_run and solo != "linkedin" and accounts.get("instagram_id") and ig_text:
        try:
            resp = await _run(bc.publish_post, accounts["instagram_id"], "instagram", ig_text, ig_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at, share_to_feed=True)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["instagram"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["instagram"] = {"error": str(e)}

    if dry_run:
        result["dry_run"] = True
        result["linkedin"] = {"status": "dry-run"}
        result["instagram"] = {"status": "dry-run"}

    job["result"] = result
    job["status"] = "done"
    return result
