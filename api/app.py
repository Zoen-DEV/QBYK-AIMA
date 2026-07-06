import asyncio
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, Response

import sheets
import cost_queries
import cost_calc
import db
from config import load_config
from job_runner import run_pipeline, make_job, publish_job_posts, _media_mime
from networks import active_networks, networks_for_format, FORMATS
from batch_runner import run_batch, publish_batch, to_utc_iso
from higgsfield_client import _USER_AGENT as _BROWSER_UA  # scripts/ ya está en sys.path (lo agrega job_runner)
import higgsfield_mcp

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
    env_fallback = {
        "linkedin": cfg.linkedin_account_id,
        "instagram": cfg.instagram_account_id,
        "facebook": cfg.facebook_account_id,
    }
    for platform in ("linkedin", "instagram", "facebook"):
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

    # Attach the Pages each account can post to (the form offers them as a second
    # selector): LinkedIn Company Pages and Facebook Pages. Failures per account are
    # non-fatal — the account stays selectable (personal profile for LinkedIn).
    for platform in ("linkedin", "facebook"):
        for acc in out.get(platform, []):
            try:
                subs = bc.get_subaccounts(acc["id"], api_key=cfg.blotato_api_key)
                acc["pages"] = [{"id": str(p.get("id", "")), "name": str(p.get("name", ""))} for p in subs if p.get("id")]
            except Exception:
                acc["pages"] = []

    return out


# ── Conexiones (OAuth del MCP de Higgsfield desde la UI) ──────────────────────
#
# La página /conexiones del frontend consulta el estado y dispara el flujo OAuth
# sin pasar por la terminal: start devuelve la URL de autorización (el navegador
# se redirige allá), Higgsfield vuelve al callback con el `code`, y la API cierra
# el intercambio de tokens. Endpoints síncronos (def): FastAPI los corre en su
# threadpool, así el bloqueo del flujo no frena el event loop de los jobs.

@app.get("/connections")
def connections_status(check: bool = True):
    """Estado de las conexiones externas. `check` verifica Higgsfield en vivo.

    La verificación en vivo (balance, gratis) es la única que detecta el token
    muerto — el MCP lo reporta in-band con HTTP 200, nunca con 401 — y de paso
    dispara el refresh proactivo si el refresh token sigue vivo.
    """
    try:
        cfg = load_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    llm = "anthropic" if cfg.anthropic_api_key else ("perplexity" if cfg.perplexity_api_key else "")
    return {
        "higgsfield": higgsfield_mcp.connection_status(live=check),
        "blotato": {"configured": bool(cfg.blotato_api_key)},
        "llm": {"provider": llm},
        "image_provider": cfg.image_provider,
    }


def _hf_redirect_uri(request: Request) -> str:
    """redirect_uri del flujo OAuth: el callback de esta API vía el proxy del front.

    Higgsfield redirige el NAVEGADOR del usuario, así que la URL se arma sobre el
    origen desde el que la página hizo el fetch (no sobre el host interno de la
    API). Override explícito: HIGGSFIELD_OAUTH_REDIRECT en .env.
    """
    env = os.environ.get("HIGGSFIELD_OAUTH_REDIRECT", "").strip()
    if env:
        return env
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        m = re.match(r"(https?://[^/]+)", request.headers.get("referer") or "")
        origin = m.group(1) if m else ""
    return f"{origin or 'http://localhost:4321'}/api/connections/higgsfield/callback"


@app.post("/connections/higgsfield/start")
def higgsfield_connect_start(request: Request):
    """Arranca el consentimiento OAuth; devuelve la URL a la que redirigir el navegador."""
    try:
        url = higgsfield_mcp.start_web_auth(_hf_redirect_uri(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo iniciar la conexión con Higgsfield: {e}")
    return {"authorize_url": url}


def _callback_page(ok: bool, detail: str = "") -> HTMLResponse:
    """Mini-página que devuelve el navegador a /conexiones con el resultado.

    Redirección por meta refresh y no por 307: el proxy del frontend sigue los
    redirects del upstream, así que un Location relativo terminaría resuelto
    contra la API (404) en vez del frontend.
    """
    if ok:
        target, msg = "/conexiones?hf=ok", "Conexión completada. Volviendo…"
    else:
        target = "/conexiones?hf=error&msg=" + urllib.parse.quote(detail[:500])
        msg = f"No se pudo conectar: {html.escape(detail[:500])}"
    return HTMLResponse(
        f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={target}"><title>Conexiones</title></head>'
        f'<body style="background:#030712;color:#e5e7eb;font-family:system-ui;display:grid;'
        f'place-items:center;min-height:100vh;margin:0"><p>{msg} '
        f'<a href="{target}" style="color:#818cf8">Continuar</a></p></body></html>'
    )


@app.get("/connections/higgsfield/callback")
def higgsfield_connect_callback(
    code: str = "", state: str = "", error: str = "", error_description: str = ""
):
    """Callback del navegador tras el consentimiento: cierra el intercambio de tokens."""
    if error:
        return _callback_page(ok=False, detail=error_description or error)
    if not code:
        return _callback_page(ok=False, detail="Higgsfield no devolvió el código de autorización.")
    try:
        higgsfield_mcp.finish_web_auth(code, state or None)
    except Exception as e:
        return _callback_page(ok=False, detail=str(e))
    return _callback_page(ok=True)


@app.post("/jobs")
async def create_job(
    source_type: Annotated[str, Form()] = "youtube",
    youtube_url: Annotated[str, Form()] = "",
    manual_text: Annotated[str, Form()] = "",
    media_file: Annotated[UploadFile | None, File()] = None,
    final_media_file: Annotated[UploadFile | None, File()] = None,
    tono: Annotated[str, Form()] = "",
    tono_linkedin: Annotated[str, Form()] = "",
    tono_instagram: Annotated[str, Form()] = "",
    tono_facebook: Annotated[str, Form()] = "",
    objetivo: Annotated[str, Form()] = "",
    objetivo_linkedin: Annotated[str, Form()] = "",
    objetivo_instagram: Annotated[str, Form()] = "",
    objetivo_facebook: Annotated[str, Form()] = "",
    formato: Annotated[str, Form()] = "",
    formato_instagram: Annotated[str, Form()] = "",
    carrusel_slides: Annotated[int, Form()] = 3,
    tipo_medio: Annotated[str, Form()] = "imagen",
    tipo_post: Annotated[str, Form()] = "post",
    media_origin: Annotated[str, Form()] = "generar",
    historia_formato: Annotated[str, Form()] = "imagen",
    fuente_imagen: Annotated[str, Form()] = "higgsfield",
    idioma: Annotated[str, Form()] = "auto",
    modelo_perplexity: Annotated[str, Form()] = "sonar-pro",
    linkedin_account_id: Annotated[str, Form()] = "",
    linkedin_page_id: Annotated[str, Form()] = "",
    instagram_account_id: Annotated[str, Form()] = "",
    facebook_account_id: Annotated[str, Form()] = "",
    facebook_page_id: Annotated[str, Form()] = "",
    redes: Annotated[str, Form()] = "",
    solo: Annotated[str, Form()] = "",
    dry_run: Annotated[bool, Form()] = False,
    publicar: Annotated[str, Form()] = "",
):
    try:
        cfg = load_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # `formato` (imagen-unica | carrusel | historia | reel) aplica a TODAS las redes
    # del post; `formato_instagram` se acepta como alias legacy. historia/reel se
    # modelan como tipo_post (las páginas /reel y /historia siguen mandando
    # tipo_post directamente; el sheet manda formato). media_origin: "generar"
    # (pipeline) | "subir" (medio propio, solo reel/historia).
    formato = (formato or "").strip().lower() or (formato_instagram or "").strip().lower() or "imagen-unica"
    if formato not in FORMATS:
        formato = "imagen-unica"
    tipo_post = (tipo_post or "post").strip().lower()
    if tipo_post not in ("post", "reel", "historia"):
        tipo_post = "post"
    if formato in ("historia", "reel"):
        tipo_post = formato
    elif tipo_post in ("reel", "historia"):
        formato = tipo_post
    media_origin = (media_origin or "generar").strip().lower()
    if media_origin not in ("generar", "subir"):
        media_origin = "generar"
    historia_formato = (historia_formato or "imagen").strip().lower()
    if historia_formato not in ("imagen", "video"):
        historia_formato = "imagen"
    # El modo "subir" solo aplica a reel/historia (en post siempre se genera).
    if tipo_post == "post":
        media_origin = "generar"

    # Redes destino filtradas por lo que el formato permite (historia/reel no
    # existen en LinkedIn). Si no queda ninguna red, es un error del usuario.
    redes_ok = networks_for_format(formato, active_networks({"redes": redes, "solo": solo}))
    if not redes_ok:
        raise HTTPException(
            status_code=400,
            detail=f"El formato '{formato}' no aplica a ninguna de las redes elegidas.",
        )

    # Resolve the content source. "youtube" needs a URL; "audio"/"texto" need an
    # uploaded file whose bytes we read now (the UploadFile is tied to this request,
    # but the pipeline runs asynchronously after we return).
    # "manual" (texto plano escrito en el form) solo existe en el flujo individual.
    source_type = (source_type or "youtube").strip().lower()
    if source_type not in ("youtube", "audio", "texto", "manual"):
        raise HTTPException(status_code=400, detail=f"source_type inválido: {source_type}")

    upload_bytes = b""
    upload_filename = ""
    final_media_bytes = b""
    final_media_filename = ""
    if media_origin == "subir":
        # El medio final (video/imagen ya hecho) se publica tal cual; el caption se
        # escribe en manual_text y pasa por el writer (source_type se fuerza a "manual").
        source_type = "manual"
        if not manual_text.strip():
            raise HTTPException(status_code=400, detail="Falta el texto/caption del post")
        if final_media_file is None:
            raise HTTPException(status_code=400, detail="Falta el archivo (video/imagen) a publicar")
        final_media_bytes = await final_media_file.read()
        if not final_media_bytes:
            raise HTTPException(status_code=400, detail="El archivo a publicar está vacío")
        final_media_filename = final_media_file.filename or "media.mp4"
    elif source_type == "youtube":
        if not youtube_url.strip():
            raise HTTPException(status_code=400, detail="Falta la URL de YouTube")
    elif source_type == "manual":
        if not manual_text.strip():
            raise HTTPException(status_code=400, detail="Falta el texto manual")
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
        "manual_text": manual_text,
        "upload_filename": upload_filename,
        "tono": tono,
        "tono_linkedin": tono_linkedin,
        "tono_instagram": tono_instagram,
        "tono_facebook": tono_facebook,
        "objetivo": objetivo,
        "objetivo_linkedin": objetivo_linkedin,
        "objetivo_instagram": objetivo_instagram,
        "objetivo_facebook": objetivo_facebook,
        "formato": formato,
        "formato_instagram": "carrusel" if formato == "carrusel" else "imagen-unica",
        "carrusel_slides": max(3, min(6, carrusel_slides)),
        "tipo_medio": tipo_medio,
        "tipo_post": tipo_post,
        "media_origin": media_origin,
        "historia_formato": historia_formato,
        "fuente_imagen": fuente_imagen if fuente_imagen in ("higgsfield", "template") else "higgsfield",
        "idioma": idioma,
        "modelo_perplexity": modelo_perplexity,
        "linkedin_account_id": linkedin_account_id.strip(),
        "linkedin_page_id": linkedin_page_id.strip(),
        "instagram_account_id": instagram_account_id.strip(),
        "facebook_account_id": facebook_account_id.strip(),
        "facebook_page_id": facebook_page_id.strip(),
        # Redes destino (checkboxes del form) ya filtradas por el formato: historia
        # y reel solo existen en Instagram y Facebook (LinkedIn se omite).
        "redes": redes_ok,
        "solo": solo,
        "dry_run": dry_run,
        "publicar": publicar,
    }
    job = make_job(cfg, params, upload_bytes=upload_bytes, upload_filename=upload_filename,
                   final_media_bytes=final_media_bytes, final_media_filename=final_media_filename)
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


def _job_snapshot(job: dict) -> dict:
    """Vista serializable de un job (sin la cola, la config ni los bytes crudos).

    Fuente única usada por el endpoint /jobs/{id} y por el preview de cada fila del
    lote, para que la revisión muestre exactamente lo mismo en ambos flujos.
    """
    return {
        "id": job["id"],
        "status": job["status"],
        "params": job["params"],
        "content": {k: v for k, v in job["content"].items() if k != "transcript"},
        "posts": job["posts"],
        "images": {
            "blotato_urls": job["images"]["blotato_urls"],
            "has_li_hook": "li-hook" in job["images"]["bytes"],
            "has_fb_hook": "fb-hook" in job["images"]["bytes"],
            "has_ig_single": "ig-single" in job["images"]["bytes"],
            "has_ig_carousel": any(k.startswith("ig-") and k != "ig-single" for k in job["images"]["bytes"]),
            "ig_slides": [k for k in (f"ig-{i}" for i in range(6)) if k in job["images"]["bytes"]],
            "provider": job["images"].get("provider", ""),
            "notice": job["images"].get("notice", ""),
        },
        "video": job.get("video", {"url": "", "provider": "", "notice": ""}),
        "li_media_urls": job.get("_li_media_urls", []),
        "ig_media_urls": job.get("_ig_media_urls", []),
        "fb_media_urls": job.get("_fb_media_urls", []),
        "result": job["result"],
        "error_msg": job["error_msg"],
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    return _job_snapshot(jobs[job_id])


@app.post("/jobs/{job_id}/edit")
async def edit_job(
    job_id: str,
    linkedin_text: Annotated[str, Form()] = "",
    instagram_text: Annotated[str, Form()] = "",
    facebook_text: Annotated[str, Form()] = "",
):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    if linkedin_text:
        job["posts"]["linkedin_text"] = linkedin_text
    if instagram_text:
        job["posts"]["instagram_text"] = instagram_text
    if facebook_text:
        job["posts"]["facebook_text"] = facebook_text
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


@app.get("/jobs/{job_id}/video")
def serve_video(job_id: str, request: Request):
    """Re-sirve el clip del job desde el mismo origen de la app.

    El preview no puede hot-linkear la URL externa: Blotato sirve el mp4 como
    application/octet-stream (Firefox/Safari se niegan a reproducir eso) y los
    bloqueadores del navegador pueden cortar un fetch de media cross-origin.
    Aquí se hace passthrough del Range (para el seek del player) y se responde
    con el MIME real. La publicación sigue usando la URL de Blotato tal cual.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    src = (jobs[job_id].get("video") or {}).get("url", "")
    if not src:
        raise HTTPException(status_code=404, detail="El job no tiene video")

    # UA de navegador: si el re-host en Blotato falló, `src` es la URL cruda del
    # proveedor, que puede estar tras el Cloudflare que banea el UA de urllib.
    upstream_headers = {"User-Agent": _BROWSER_UA}
    if request.headers.get("range"):
        upstream_headers["Range"] = request.headers["range"]
    try:
        upstream = urllib.request.urlopen(
            urllib.request.Request(src, headers=upstream_headers), timeout=30
        )
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"El origen del video respondió {e.code}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"No se pudo leer el video: {e.reason}")

    headers = {"Accept-Ranges": "bytes"}
    for h in ("Content-Length", "Content-Range"):
        if upstream.headers.get(h):
            headers[h] = upstream.headers[h]

    def chunks():
        try:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    # urlopen trata 206 (respuesta a un Range) como éxito, así que status llega intacto.
    return StreamingResponse(
        chunks(), status_code=upstream.status, media_type=_media_mime(src), headers=headers
    )


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
    facebook_account_id: Annotated[str, Form()] = "",
    facebook_page_id: Annotated[str, Form()] = "",
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
        "facebook_account_id": facebook_account_id.strip(),
        "facebook_page_id": facebook_page_id.strip(),
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
    """Vista serializable del batch (sin el spec interno ni la config).

    Cada fila incluye `preview` (el snapshot del job generado) para que la pantalla
    de revisión muestre el contenido completo —texto, imágenes/video— de cada post
    antes de aprobar la publicación. `preview` es None mientras la fila no se generó.
    """
    rows = []
    for r in batch["rows"]:
        job = jobs.get(r.get("job_id"))
        rows.append({
            "index": r["index"],
            "source": r["source"],
            "label": r["label"],
            "title": r.get("title") or r["label"],
            "schedule": r["schedule"],
            "status": r["status"],
            "job_id": r["job_id"],
            "result": r["result"],
            "error": r["error"],
            "preview": _job_snapshot(job) if job is not None else None,
        })
    return {
        "id": batch["id"],
        "status": batch["status"],
        "warnings": batch["warnings"],
        "dry_run": batch["dry_run"],
        "rows": rows,
    }


@app.get("/sheets/batches/{batch_id}")
def get_batch(batch_id: str):
    if batch_id not in batches:
        raise HTTPException(status_code=404)
    return _batch_snapshot(batches[batch_id])


@app.post("/sheets/batches/{batch_id}/publish")
async def publish_sheet_batch(batch_id: str):
    """Aprueba el lote: publica/programa todas las filas generadas (fase 2).

    Solo válido cuando el lote está en "review" (terminó de generar). Lanza la
    publicación en segundo plano; el frontend sigue el avance con el polling normal.
    """
    if batch_id not in batches:
        raise HTTPException(status_code=404)
    batch = batches[batch_id]
    if batch["status"] != "review":
        raise HTTPException(
            status_code=409,
            detail="El lote no está listo para publicar (aún genera contenido o ya se publicó).",
        )
    batch["status"] = "publishing"
    asyncio.create_task(publish_batch(batch, jobs))
    return {"batch_id": batch_id, "status": "publishing"}


# ── Dashboard de costos (lee usage_events de Mongo, best-effort) ──────────────────
#
# Las agregaciones viven en cost_queries.py; aquí solo se exponen como endpoints.
# Si Mongo no está configurado/disponible, las consultas devuelven estructuras
# vacías (sin datos) en vez de fallar — el dashboard sigue cargando.

@app.get("/costs/summary")
async def costs_summary(period: str):
    """Totales del mes (`YYYY-MM`) o año (`YYYY`): variable por servicio + fijos + total."""
    try:
        return await cost_queries.summary(period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/costs/timeseries")
async def costs_timeseries(
    from_: Annotated[str, Query(alias="from")] = "",
    to: str = "",
    granularity: str = "day",
):
    """Serie temporal por servicio. `from`/`to` son `YYYY-MM-DD`; granularity `day|month`."""
    try:
        return await cost_queries.timeseries(from_, to, granularity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/costs/by-job")
async def costs_by_job(
    from_: Annotated[str, Query(alias="from")] = "",
    to: str = "",
):
    """Costo por job (gasto por uso) en el rango, con desglose por servicio."""
    try:
        return await cost_queries.by_job(from_, to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/costs/events")
async def costs_events(service: str = "", limit: int = 50, skip: int = 0):
    """Eventos crudos paginados para auditoría (más recientes primero)."""
    return await cost_queries.events(service=service, limit=limit, skip=skip)


@app.get("/costs/status")
async def costs_status():
    """Estado del tracking de costos: si Mongo está configurado/accesible y versión de pricing."""
    configured = db.is_configured()
    reachable = False
    if configured:
        try:
            coll = await db.get_usage_events()
            reachable = coll is not None
        except Exception:
            reachable = False
    pricing = cost_calc.load_pricing()
    return {
        "mongo_configured": configured,
        "mongo_reachable": reachable,
        "pricing_version": cost_calc.pricing_version(pricing),
        "pricing_currency": pricing.get("display_currency", "USD"),
        "pricing_fx_rate": pricing.get("fx_rate", 1.0),
    }
