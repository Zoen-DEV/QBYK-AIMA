import asyncio
import copy
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, Response

import model_catalog
import sheets
import cost_queries
import cost_calc
import db
import identity_store
import prompt_lint
import users
import visual_identity
from config import load_config
from job_runner import (run_pipeline, resume_media, make_job, publish_job_posts, _media_mime,
                        regenerate_image, subkeys_regenerables, _n_slides, rewrite_job_posts)
from post_writer import (_wants_images, _wants_video, _faltantes, _segments_needed,
                         captions_needed)
from networks import active_networks, networks_for_format, FORMATS
from batch_runner import run_batch, generate_batch_media, publish_batch, to_utc_iso
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


def _parse_voice(voz: str, pitch: str, velocidad: str) -> dict:
    """Normaliza la selección de voz del form al shape que consume el pipeline.

    `voz` es el valor combinado "voice_type:voice_id" del selector (de GET /voices).
    Se separa y valida; vacío/mal formado = voz por defecto del .env. `pitch` y
    `velocidad` son enums (grave/agudo, lenta/rapida) → los mapea job_runner a ratios.
    """
    voice_type, _sep, voice_id = (voz or "").partition(":")
    voice_type = voice_type.strip().lower()
    voice_id = voice_id.strip()
    if voice_type not in ("preset", "element") or not voice_id:
        voice_type, voice_id = "", ""
    pitch = (pitch or "").strip().lower()
    velocidad = (velocidad or "").strip().lower()
    return {
        "voz_tipo": voice_type,
        "voz_id": voice_id,
        "voz_pitch": pitch if pitch in ("grave", "agudo") else "",
        "voz_velocidad": velocidad if velocidad in ("lenta", "rapida") else "",
    }


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
        "tiktok": cfg.tiktok_account_id,
    }
    for platform in ("linkedin", "instagram", "facebook", "tiktok"):
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


@app.get("/voices")
def list_voices():
    """Voces disponibles para la voz en off de los reels (list_voices del MCP).

    Alimenta el selector de voz de los forms (nombre/género + preview de audio).
    Best-effort: [] si el MCP no está conectado o la consulta falla — el form cae a
    la voz por defecto del .env.
    """
    if not higgsfield_mcp.is_configured():
        return {"voices": []}
    try:
        return {"voices": higgsfield_mcp.list_voices()}
    except Exception as e:
        return {"voices": [], "error": str(e)}


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


# ── Cuenta: usuarios e identidades visuales ───────────────────────────────────
#
# Todo lo de aquí está scopeado por `users.current_user_id(request)`, que es el ÚNICO
# punto del proyecto que decide quién pide. Los endpoints exigen el `user_id` igual
# que lo harían con auth real; hoy lo dice una cabecera y mañana una sesión.


@contextmanager
def _errores_identidad():
    """Traduce los errores del store a HTTP, en un solo sitio.

    Los mensajes del validador van tal cual al `detail` porque son accionables ("usa
    #FF00AA, que no es el tercer color de la paleta (#C9F227)") y la UI los muestra
    sin reescribirlos.
    """
    try:
        yield
    except visual_identity.IdentidadInvalida as e:
        raise HTTPException(status_code=422, detail="; ".join(e.errores))
    except identity_store.NoEncontrada as e:
        raise HTTPException(status_code=404, detail=str(e))
    except identity_store.NoEditable as e:
        raise HTTPException(status_code=403, detail=str(e))
    except identity_store.AlmacenNoDisponible as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:      # nombre vacío o demasiado largo
        raise HTTPException(status_code=400, detail=str(e))


async def _cuerpo_json(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="El cuerpo de la petición no es JSON válido.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Se esperaba un objeto JSON.")
    return data


@app.get("/users")
def list_users(request: Request):
    """Perfiles disponibles + cuál está activo (alimenta el selector de la barra)."""
    return {"users": users.listar(), "current": users.current_user_id(request)}


@app.get("/identities")
async def list_identities(request: Request):
    """Identidades visibles: la de la casa primero, luego las del usuario."""
    with _errores_identidad():
        return {"identities": await identity_store.listar(users.current_user_id(request))}


@app.post("/identities")
async def create_identity(request: Request):
    """Crea una identidad propia. Cuerpo: `{name?, identity_json, activar?}`.

    El nombre es opcional: vacío se rellena con `nombre_sugerido` en vez de rechazar
    el guardado y perder la extracción que el usuario acaba de pagar.
    """
    body = await _cuerpo_json(request)
    with _errores_identidad():
        fila = await identity_store.crear(
            users.current_user_id(request),
            name=body.get("name") or "",
            identity_json=body.get("identity_json") or {},
            activar_al_crear=bool(body.get("activar")),
        )
    return fila


@app.patch("/identities/{identity_id}")
async def update_identity(identity_id: str, request: Request):
    """Renombra y/o reemplaza el JSON. Los dos campos son opcionales e independientes:
    lo que no venga en el cuerpo no se toca."""
    body = await _cuerpo_json(request)
    with _errores_identidad():
        return await identity_store.actualizar(
            users.current_user_id(request), identity_id,
            name=body["name"] if "name" in body else None,
            identity_json=body["identity_json"] if "identity_json" in body else None,
        )


@app.delete("/identities/{identity_id}")
async def delete_identity(identity_id: str, request: Request):
    """Elimina una identidad propia. Si era la activa, la activa vuelve a ser la de la casa."""
    with _errores_identidad():
        await identity_store.eliminar(users.current_user_id(request), identity_id)
    return {"ok": True}


@app.post("/identities/{identity_id}/activate")
async def activate_identity(identity_id: str, request: Request):
    """Marca la identidad activa del usuario (la de la casa incluida)."""
    with _errores_identidad():
        return await identity_store.activar(users.current_user_id(request), identity_id)


@app.post("/jobs")
async def create_job(
    source_type: Annotated[str, Form()] = "youtube",
    youtube_url: Annotated[str, Form()] = "",
    manual_text: Annotated[str, Form()] = "",
    # Contextos separados (modo manual de reel): guían por separado el video y la voz.
    # Vacíos = se usa manual_text (el copy) como base también para video/voz.
    contexto_video: Annotated[str, Form()] = "",
    contexto_voz: Annotated[str, Form()] = "",
    media_file: Annotated[UploadFile | None, File()] = None,
    final_media_file: Annotated[UploadFile | None, File()] = None,
    photos: Annotated[list[UploadFile], File()] = [],
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
    duracion_video: Annotated[int, Form()] = 0,
    camara_estilo: Annotated[str, Form()] = "dolly",
    tipo_post: Annotated[str, Form()] = "post",
    media_origin: Annotated[str, Form()] = "generar",
    historia_formato: Annotated[str, Form()] = "imagen",
    fuente_imagen: Annotated[str, Form()] = "higgsfield",
    template_set: Annotated[int, Form()] = 1,
    modelo_imagen: Annotated[str, Form()] = "",
    modelo_video: Annotated[str, Form()] = "",
    modelo_voz: Annotated[str, Form()] = "",
    voz: Annotated[str, Form()] = "",
    voz_pitch: Annotated[str, Form()] = "",
    voz_velocidad: Annotated[str, Form()] = "",
    idioma: Annotated[str, Form()] = "auto",
    modelo_perplexity: Annotated[str, Form()] = "sonar-pro",
    linkedin_account_id: Annotated[str, Form()] = "",
    linkedin_page_id: Annotated[str, Form()] = "",
    instagram_account_id: Annotated[str, Form()] = "",
    facebook_account_id: Annotated[str, Form()] = "",
    facebook_page_id: Annotated[str, Form()] = "",
    tiktok_account_id: Annotated[str, Form()] = "",
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
    if media_origin not in ("generar", "subir", "fotos"):
        media_origin = "generar"
    historia_formato = (historia_formato or "imagen").strip().lower()
    if historia_formato not in ("imagen", "video"):
        historia_formato = "imagen"
    # El recorrido a partir de fotos (image-to-video) es siempre un Reel.
    if media_origin == "fotos":
        tipo_post = "reel"
        formato = "reel"
    # Los modos "subir"/"fotos" solo aplican a reel/historia (en post siempre se genera).
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
    photo_files: list[tuple[bytes, str]] = []
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
    elif media_origin == "fotos":
        # Recorrido image-to-video: varias fotos → transiciones foto→foto → un reel.
        # El caption se escribe en manual_text y pasa por el writer (source_type manual).
        source_type = "manual"
        if not manual_text.strip():
            raise HTTPException(status_code=400, detail="Falta el texto/caption del reel")
        for f in (photos or []):
            b = await f.read()
            if b:
                photo_files.append((b, f.filename or f"foto-{len(photo_files)}.jpg"))
        if len(photo_files) < 2:
            raise HTTPException(status_code=400, detail="Sube al menos 2 fotos para armar el recorrido")
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
        # Contextos opcionales para dirigir el video y la voz por separado (reel manual).
        "contexto_video": contexto_video.strip(),
        "contexto_voz": contexto_voz.strip(),
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
        # Duración total buscada del video (segundos); 0 = 1 solo segmento (default del
        # modelo). Clamp 0–60. La app arma esa duración concatenando segmentos.
        "duracion_video": max(0, min(60, duracion_video)),
        # Estilo de cámara del recorrido de fotos (image-to-video).
        "camara_estilo": camara_estilo if camara_estilo in ("dolly", "orbit", "pan") else "dolly",
        "tipo_post": tipo_post,
        "media_origin": media_origin,
        "historia_formato": historia_formato,
        "fuente_imagen": fuente_imagen if fuente_imagen in ("higgsfield", "template") else "higgsfield",
        # Set de estilo de las plantillas (1-3). Aplica a plantillas directas y al
        # fallback del MCP; no afecta la generación con Higgsfield.
        "template_set": max(1, min(3, template_set)),
        # Modelos de generación por post (vacío = default del .env). Se validan
        # contra el catálogo curado; un id desconocido cae al default en silencio.
        "modelo_imagen": modelo_imagen.strip() if modelo_imagen.strip() in model_catalog.IMAGE_MODELS else "",
        "modelo_video": modelo_video.strip() if modelo_video.strip() in model_catalog.VIDEO_MODELS else "",
        "modelo_voz": modelo_voz.strip() if modelo_voz.strip() in model_catalog.TTS_MODELS else "",
        # Voz en off elegida en el form: valor combinado "voice_type:voice_id" (de
        # list_voices / GET /voices). Se separa aquí; vacío/mal formado = voz por
        # defecto del .env. voz_pitch/voz_velocidad ajustan tono y velocidad.
        **_parse_voice(voz, voz_pitch, voz_velocidad),
        "idioma": idioma,
        "modelo_perplexity": modelo_perplexity,
        "linkedin_account_id": linkedin_account_id.strip(),
        "linkedin_page_id": linkedin_page_id.strip(),
        "instagram_account_id": instagram_account_id.strip(),
        "facebook_account_id": facebook_account_id.strip(),
        "facebook_page_id": facebook_page_id.strip(),
        "tiktok_account_id": tiktok_account_id.strip(),
        # Redes destino (checkboxes del form) ya filtradas por el formato: historia
        # y reel solo existen en Instagram y Facebook (LinkedIn se omite).
        "redes": redes_ok,
        "solo": solo,
        "dry_run": dry_run,
        "publicar": publicar,
    }
    job = make_job(cfg, params, upload_bytes=upload_bytes, upload_filename=upload_filename,
                   final_media_bytes=final_media_bytes, final_media_filename=final_media_filename,
                   photo_files=photo_files)
    jobs[job["id"]] = job
    asyncio.create_task(run_pipeline(job))
    return {"job_id": job["id"]}


# Estado terminal del job → página a la que manda el evento de cierre. La fase A
# pausa en "preview", la fase B termina en "review" y publicar deja "done".
_STREAM_DESTINO = {"preview": "preview", "review": "review", "done": "result"}


def _evento_terminal(job: dict) -> dict | None:
    """El evento de cierre reconstruido desde `job["status"]`, o None si sigue corriendo.

    La cola es de consumo único y sin repetición: un stream que se murió (recarga de
    página, corte de red, reload del dev server) igual saca el evento de la cola antes
    de enterarse de que nadie lo escucha, y ahí se pierde para siempre. Sin esto, el
    cliente que reconecta se queda esperando un evento que ya no va a volver a
    emitirse —pantalla "Generando tu contenido" para siempre— con el job terminado
    hace rato. El estado terminal ya está espejado en `job["status"]` antes de cada
    push, así que se puede reconstruir sin guardar nada nuevo.
    """
    estado = job.get("status")
    if estado == "error":
        return {"step": "error", "msg": job.get("error_msg") or "Error desconocido"}
    destino = _STREAM_DESTINO.get(estado)
    if destino is None:
        return None
    # El front distingue "preview" (compuerta editable) de "done" (todo lo demás).
    return {
        "step": "preview" if estado == "preview" else "done",
        "redirect": f"/jobs/{job['id']}/{destino}",
    }


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]

    async def generator():
        q: asyncio.Queue = job["_queue"]
        # Reconexión sobre un job que ya llegó a destino: emitir su estado y cerrar.
        # No hay nada que esperar, los eventos de progreso ya no vuelven a la cola.
        cerrado = _evento_terminal(job)
        if cerrado is not None:
            yield f"data: {json.dumps(cerrado)}\n\n"
            return
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # Llegar acá con el job ya terminado significa que otro stream —uno
                # muerto -- se comió el evento de cierre: emitirlo en vez del ping.
                cerrado = _evento_terminal(job)
                if cerrado is not None:
                    yield f"data: {json.dumps(cerrado)}\n\n"
                    break
                yield "data: {\"step\": \"ping\"}\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("step") in ("done", "error", "preview"):
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _lint_job(job: dict, posts: dict | None = None) -> list[dict]:
    """Avisos sobre los prompts de este job (lo que va a pasar si se genera así).

    Los parámetros del lint salen del propio job para que las dos compuertas previas
    —el preview del individual y el editor por fila del lote— vean exactamente los
    mismos avisos. `posts` permite revisar una edición sin guardarla.

    Delante van los avisos de ORIGEN que anotó el runner (`job["avisos"]`: sin
    transcripción, escritura degradada), porque explican la CAUSA de los de abajo.
    El de la escritura se cae solo cuando ya no queda ningún aviso de prompts: si el
    usuario escribió a mano lo que faltaba, contar cómo llegó roto ya es ruido.
    """
    params = job["params"]
    is_carousel = params.get("formato_instagram", "imagen-unica") == "carrusel"
    avisos = prompt_lint.revisar(
        posts if posts is not None else job["posts"],
        n_info=(_n_slides(params) - 1) if is_carousel else 1,
        is_carousel=is_carousel,
        quiere_imagenes=_wants_images(params),
        quiere_video=_wants_video(params),
    )
    origen = [a for a in job.get("avisos", []) if a.get("campo") != "escritura" or avisos]
    return origen + avisos


# Campos que la revisión previa puede editar. `None` = no enviado (no tocar): así la
# pantalla de revisión, que solo manda los *_text, no borra el resto.
_CAMPOS_EDICION = ("linkedin_text", "instagram_text", "facebook_text", "image_hook",
                   "image_slides", "image_prompt", "image_style", "image_slide_prompts",
                   "video_prompt", "video_style", "video_storyboard", "video_voiceover")

# Texto impreso de CADA slide, en su propio campo. La revisión previa dejó de mandar
# un textarea con una frase por línea: con un campo por slide se ve qué texto va en
# qué imagen. Van indexados —no unidos por saltos de línea— justamente para conservar
# la POSICIÓN: vaciar el slide 2 tiene que dejar el 2 vacío, no correr el 3 a su sitio.
_RE_SLIDE_TEXT = re.compile(r"^image_slide_text_(\d+)$")


def _campos_indexados(form) -> dict:
    """`image_slide_text_{i}` del form → `{"image_slide_text": [por posición]}`."""
    por_indice: dict[int, str] = {}
    for clave in form:
        m = _RE_SLIDE_TEXT.match(str(clave))
        if m:
            por_indice[int(m.group(1))] = str(form[clave])
    if not por_indice:
        return {}
    return {"image_slide_text": [por_indice.get(i, "") for i in range(max(por_indice) + 1)]}


def _aplicar_edicion(posts: dict, campos: dict) -> dict:
    """Aplica los campos editados sobre `posts` (mismas reglas para /edit y /lint)."""
    for campo in ("linkedin_text", "instagram_text", "facebook_text"):
        if campos.get(campo):
            posts[campo] = campos[campo]

    # image_text (copy de los visuales): hook + frases de slides. Los slides llegan de
    # dos formas: un campo por slide (`image_slide_text`, la revisión previa de hoy) o
    # el textarea histórico con una frase por línea (`image_slides`). El primero manda
    # cuando están los dos, y conserva los huecos vacíos; el segundo los descarta,
    # porque en un textarea una línea en blanco es un tecleo, no un slide sin texto.
    slides_pos = campos.get("image_slide_text")
    if (campos.get("image_hook") is not None or campos.get("image_slides") is not None
            or slides_pos is not None):
        img = dict(posts["image_text"]) if isinstance(posts.get("image_text"), dict) else {}
        if campos.get("image_hook") is not None:
            img["hook"] = campos["image_hook"].strip()
        if slides_pos is not None:
            img["slides"] = [str(s).strip() for s in slides_pos]
        elif campos.get("image_slides") is not None:
            img["slides"] = [l.strip() for l in campos["image_slides"].splitlines() if l.strip()]
        posts["image_text"] = img

    # Prompts de las imágenes (la escena que genera el modelo, no el texto impreso).
    if campos.get("image_prompt") is not None:
        posts["image_prompt"] = campos["image_prompt"].strip()
    # La dirección de arte va IGUAL en la portada y en todos los slides: editarla acá
    # cambia el look de todo el set de una vez.
    if campos.get("image_style") is not None:
        posts["image_style"] = campos["image_style"].strip()
    if campos.get("image_slide_prompts") is not None:
        posts["image_slide_prompts"] = [l.strip() for l in campos["image_slide_prompts"].splitlines() if l.strip()]

    # Prompts del video (para la generación text-to-video + voz en off).
    if campos.get("video_prompt") is not None:
        posts["video_prompt"] = campos["video_prompt"].strip()
    if campos.get("video_style") is not None:
        posts["video_style"] = campos["video_style"].strip()
    if campos.get("video_storyboard") is not None:
        posts["video_storyboard"] = [l.strip() for l in campos["video_storyboard"].splitlines() if l.strip()]
    if campos.get("video_voiceover") is not None:
        posts["video_voiceover"] = [l.strip() for l in campos["video_voiceover"].splitlines() if l.strip()]
    return posts


def _needs_job(job: dict, posts: dict | None = None) -> dict:
    """Qué campos pide este job (captions y visuales) y cuáles siguen sin llenarse.

    Fuente única para las dos compuertas previas: con esto el formulario se dibuja
    a partir de lo que el job NECESITA (no de lo que el modelo entregó), y el botón
    de reintentar la escritura aparece exactamente cuando hay algo que reintentar.
    `posts` permite medir una edición sin guardarla (mismo uso que en `_lint_job`),
    para que el botón se apague solo en cuanto el usuario termina de escribir a mano.
    """
    params = job["params"]
    is_carousel = params.get("formato_instagram", "imagen-unica") == "carrusel"
    quiere_video = _wants_video(params)
    return {
        "imagenes": _wants_images(params),
        "video": quiere_video,
        # Captions que pide este job. La compuerta previa dibuja sus textareas desde
        # acá y NO desde `posts`: con el caption vacío el textarea no se renderizaba,
        # así que el único campo donde arreglarlo a mano no existía (mismo defecto que
        # ya se había corregido para los prompts visuales).
        "captions": captions_needed(params),
        "n_info": (_n_slides(params) - 1) if is_carousel else 0,
        "n_shots": _segments_needed(params) if quiere_video else 0,
        "faltan": _faltantes(posts if posts is not None else job["posts"], params),
    }


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
            # La historia se sirve desde la API (same-origin) en vez de hot-linkear
            # la URL de Blotato: así se refresca al rehacerla.
            "has_ig_story": "ig-story" in job["images"]["bytes"],
            "has_ig_carousel": any(k.startswith("ig-") and k != "ig-single" for k in job["images"]["bytes"]),
            "ig_slides": [k for k in (f"ig-{i}" for i in range(6)) if k in job["images"]["bytes"]],
            "provider": job["images"].get("provider", ""),
            "notice": job["images"].get("notice", ""),
            # ¿La imagen lleva texto? El preview lo usa para no invitar a editar un
            # texto que no se va a ver. `text_in_prompt` dice quién lo dibuja: el
            # modelo desde el prompt (hoy) o Pillow por encima (respaldo).
            "text_overlay": job["images"].get("text_overlay", True),
            "text_in_prompt": job["images"].get("text_in_prompt", True),
            # Traza de la generación: prompt final y QA del texto por imagen.
            "prompts": job["images"].get("prompts", {}),
            "qa": job["images"].get("qa", {}),
            # Imágenes que la revisión puede rehacer de a una (POST /jobs/{id}/regenerate).
            # Lo decide el backend para que las dos revisiones —individual y lote— no
            # tengan que repetir las reglas de formato en el frontend.
            "regenerables": subkeys_regenerables(job) if job["status"] == "review" else [],
        },
        # Avisos sobre los prompts (lo que va a pasar si se genera así). Los muestran
        # las dos compuertas previas: el preview del individual y el editor del lote.
        "lint": _lint_job(job),
        # Qué campos visuales necesita ESTE job y cuáles siguen vacíos. Lo decide el
        # backend (misma fuente que el escritor y el lint) para que las dos compuertas
        # previas pinten los campos que hay que llenar aunque el modelo no haya
        # entregado nada — antes, sin valor, la tarjeta entera no se dibujaba y el
        # aviso pedía escribir prompts en un formulario que no existía.
        "needs": _needs_job(job),
        "video": job.get("video", {"url": "", "provider": "", "notice": ""}),
        "li_media_urls": job.get("_li_media_urls", []),
        "ig_media_urls": job.get("_ig_media_urls", []),
        "fb_media_urls": job.get("_fb_media_urls", []),
        "tk_media_urls": job.get("_tk_media_urls", []),
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
    request: Request,
    linkedin_text: Annotated[str, Form()] = "",
    instagram_text: Annotated[str, Form()] = "",
    facebook_text: Annotated[str, Form()] = "",
    # Campos del PREVIEW editable (opcionales, default None = no tocar). El default
    # None distingue "no enviado" (revisión normal) de "vaciado" — así la pantalla de
    # revisión, que solo manda los *_text, no borra el image_text ni los prompts.
    image_hook: Annotated[str | None, Form()] = None,
    image_slides: Annotated[str | None, Form()] = None,       # una frase por línea
    image_prompt: Annotated[str | None, Form()] = None,       # escena de la portada
    image_style: Annotated[str | None, Form()] = None,        # dirección de arte compartida
    image_slide_prompts: Annotated[str | None, Form()] = None,  # una escena por línea
    video_prompt: Annotated[str | None, Form()] = None,
    video_style: Annotated[str | None, Form()] = None,
    video_storyboard: Annotated[str | None, Form()] = None,   # un shot por línea
    video_voiceover: Annotated[str | None, Form()] = None,    # una línea hablada por shot
):
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    campos: dict = {
        "linkedin_text": linkedin_text, "instagram_text": instagram_text,
        "facebook_text": facebook_text, "image_hook": image_hook,
        "image_slides": image_slides, "image_prompt": image_prompt,
        "image_style": image_style, "image_slide_prompts": image_slide_prompts,
        "video_prompt": video_prompt, "video_style": video_style,
        "video_storyboard": video_storyboard, "video_voiceover": video_voiceover,
    }
    # Los campos por slide son dinámicos (uno por imagen), así que no pueden declararse
    # como parámetros: se leen del form crudo. Mismo tratamiento que en /lint.
    campos.update(_campos_indexados(await request.form()))
    posts = _aplicar_edicion(job["posts"], campos)
    # El lint viaja con la respuesta: quien edita ve al instante si el cambio arregló
    # el aviso (o si abrió otro) sin volver a cargar la pantalla. `needs` va con él para
    # que el botón de reintentar la escritura se apague solo cuando ya no falta nada.
    return {"posts": posts, "lint": _lint_job(job), "needs": _needs_job(job)}


@app.post("/jobs/{job_id}/lint")
async def lint_job(job_id: str, request: Request):
    """Revisa los prompts SIN guardarlos: los avisos de la compuerta previa, en vivo.

    Acepta los mismos campos que `/edit` y los aplica sobre una copia, así el preview
    puede avisar mientras se escribe sin persistir un estado a medio editar.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    form = await request.form()
    campos: dict = {k: str(form[k]) for k in _CAMPOS_EDICION if k in form}
    campos.update(_campos_indexados(form))
    posts = _aplicar_edicion(copy.deepcopy(job["posts"]), campos)
    return {"lint": _lint_job(job, posts), "needs": _needs_job(job, posts)}


@app.post("/jobs/{job_id}/rewrite")
async def rewrite_job(job_id: str):
    """Vuelve a pedirle al LLM los campos visuales que la escritura dejó vacíos.

    Es el reintento MANUAL de la compuerta previa, para los dos flujos (las filas del
    lote son jobs de este mismo store). Solo pide lo que falta y lo funde sobre lo que
    ya hay, así lo que el usuario ya editó a mano no se pierde. Cuesta una llamada al
    LLM, por eso lo dispara el usuario y no un bucle automático.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    if job["status"] != "preview":
        raise HTTPException(
            status_code=409,
            detail="El job ya no está en la revisión previa: no se puede reescribir.",
        )
    if job.get("_rewriting"):
        raise HTTPException(status_code=409, detail="Ya hay un reintento de escritura en curso.")
    job["_rewriting"] = True
    try:
        avisos = await rewrite_job_posts(job)
    except Exception as e:  # noqa: BLE001 - el reintento nunca puede tumbar la revisión
        raise HTTPException(status_code=502, detail=f"No se pudo reescribir: {e}")
    finally:
        job["_rewriting"] = False
    return {
        "posts": job["posts"],
        "lint": _lint_job(job),
        "needs": _needs_job(job),
        # True cuando ya no falta ningún campo visual del contrato.
        "ok": not avisos,
    }


@app.post("/jobs/{job_id}/generate")
async def generate_job_media(job_id: str):
    """Reanuda un job pausado en el preview: arranca la fase de generación de medios.

    Solo válido cuando el job está en "preview" (fase de escritura terminada). Lanza
    la fase de media en segundo plano; el frontend sigue el avance por el SSE normal.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    if job["status"] != "preview":
        raise HTTPException(
            status_code=409,
            detail="El job no está en preview (ya se generó el contenido o aún no está listo).",
        )
    # El estado se mueve ACÁ, no dentro de la tarea: el front navega a /jobs/{id} ni
    # bien responde esto y abre el stream, que ahora mira `job["status"]` para saber si
    # el job ya terminó. Si el flip quedara en la tarea (que corre después), ese stream
    # podría leer todavía "preview" y rebotar al usuario de vuelta al preview en bucle.
    # De paso cierra la ventana en la que dos POST seguidos pasaban los dos el 409.
    job["status"] = "running"
    asyncio.create_task(resume_media(job))
    return {"job_id": job_id, "status": "running"}


@app.post("/jobs/{job_id}/regenerate")
async def regenerate_job_image(job_id: str, subkey: Annotated[str, Form()] = ""):
    """Rehace UNA imagen del post ya generado, sin tocar las demás.

    La compuerta de revisión existe para descartar lo que salió mal; sin esto, la
    unidad de reintento era el post entero. Vale para los dos flujos: la revisión
    del individual y la de cada fila del lote pegan a este mismo endpoint (las filas
    del batch son jobs del mismo store).

    Se responde cuando la imagen ya está hecha y subida — cuesta una generación, así
    que la llamada tarda lo que tarde el modelo.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    job = jobs[job_id]
    if job["status"] != "review":
        raise HTTPException(
            status_code=409,
            detail="El post no está en revisión (todavía se está generando, o ya se publicó).",
        )
    if subkey not in subkeys_regenerables(job):
        raise HTTPException(status_code=400, detail=f"No se puede rehacer «{subkey}» en este post.")
    # Una regeneración a la vez por job: dos en paralelo se pisarían los bytes y la
    # subida. El chequeo y la marca son síncronos (un solo hilo de evento), así que
    # no hay ventana entre mirar la bandera y ponerla.
    if job.get("_regenerando"):
        raise HTTPException(status_code=409, detail="Ya se está rehaciendo una imagen de este post.")
    job["_regenerando"] = True
    try:
        res = await regenerate_image(job, subkey)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo rehacer la imagen: {e}")
    finally:
        job["_regenerando"] = False
    return {
        "job_id": job_id,
        "subkeys": res["subkeys"],
        "aviso": res["aviso"],
        "prompts": job["images"].get("prompts", {}),
        "qa": job["images"].get("qa", {}),
    }


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


@app.post("/sheets/batches/{batch_id}/generate")
async def generate_sheet_batch(batch_id: str):
    """Aprueba los guiones del lote: genera el medio de todas las filas (fase 2).

    Espejo de `POST /jobs/{id}/generate` a nivel de lote. Solo válido cuando el lote
    está en "preview" (terminó de escribir y espera revisión). Las ediciones de cada
    fila se hacen antes con `POST /jobs/{job_id}/edit`, el mismo endpoint que usa el
    flujo individual. Lanza la generación en segundo plano; el frontend sigue el
    avance con el polling normal.
    """
    if batch_id not in batches:
        raise HTTPException(status_code=404)
    batch = batches[batch_id]
    if batch["status"] != "preview":
        raise HTTPException(
            status_code=409,
            detail="El lote no está en revisión de guiones (aún escribe, o ya se generó el medio).",
        )
    batch["status"] = "generating"
    asyncio.create_task(generate_batch_media(batch, jobs))
    return {"batch_id": batch_id, "status": "generating"}


@app.post("/sheets/batches/{batch_id}/publish")
async def publish_sheet_batch(batch_id: str):
    """Aprueba el lote: publica/programa todas las filas generadas (fase 3).

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
