import asyncio
import re
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import blotato_client as bc
import image_provider as improv
import higgsfield_client as hf
import transcribe as tr
import transcribe_local as trl
import document_text as doc

try:
    import image_overlay as ov
    _HAS_OVERLAY = True
except ImportError:
    _HAS_OVERLAY = False

from post_writer import write_posts
from networks import active_networks
import cost_tracker

_loop_executor = None
_OUTPUTS_DIR = Path(__file__).parent / "outputs"


def _event_context(job: dict) -> dict:
    """Contexto común de un usage_event extraído del job (flow, ids, plataformas…)."""
    params = job.get("params", {})
    return {
        "flow": job.get("flow", "individual"),
        "job_id": job.get("id"),
        "batch_id": job.get("batch_id"),
        "source_type": params.get("source_type"),
        "platforms": active_networks(params),
        "dry_run": bool(params.get("dry_run", False)),
    }


async def _track(job: dict, *, service: str, operation: str, units: dict,
                 model: str | None = None, status: str = "success") -> None:
    """Registra un usage_event para este job. Best-effort: nunca rompe el pipeline.

    Punto único de instrumentación (regla de los dos flujos): tanto el post
    individual como cada fila del bulk pasan por aquí y heredan el tracking gratis.
    """
    try:
        await cost_tracker.record_event(
            service=service, operation=operation, units=units, model=model,
            status=status, **_event_context(job),
        )
    except Exception:
        pass  # record_event ya es best-effort; este guard cubre el armado del contexto


def make_job(cfg, params: dict, *, upload_bytes: bytes = b"", upload_filename: str = "",
             flow: str = "individual", batch_id: str | None = None) -> dict:
    """Build a fresh in-memory job from an already-normalized `params` dict.

    Shared by the single-post endpoint (`create_job`) and the bulk batch runner so
    both produce the exact same job shape the pipeline expects. `params` must already
    be normalized by the caller (clamped slide count, stripped account ids, etc.).
    For non-YouTube sources, pass the source bytes via `upload_bytes`/`upload_filename`.
    `flow`/`batch_id` etiquetan el origen del job para el tracking de costos
    ("individual" por defecto; el bulk pasa "bulk" + el id del batch).
    """
    return {
        "id": str(uuid.uuid4()),
        "status": "running",
        "flow": flow,
        "batch_id": batch_id,
        "params": params,
        "content": {},
        "accounts": {},
        "posts": {},
        "images": {"bytes": {}, "blotato_urls": {"linkedin": "", "instagram": [], "facebook": ""}, "base_urls": {"linkedin": [], "instagram": [], "facebook": []}, "provider": "", "notice": ""},
        "video": {"url": "", "provider": "", "notice": ""},
        "result": {},
        "error_msg": None,
        "_queue": asyncio.Queue(),
        "_cfg": cfg,
        "_li_media_urls": [],
        "_ig_media_urls": [],
        "_fb_media_urls": [],
        "_upload_bytes": upload_bytes,
        "_upload_filename": upload_filename,
    }


def _save_image(job_id: str, key: str, png: bytes) -> None:
    try:
        out = _OUTPUTS_DIR / job_id
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{key}.png").write_bytes(png)
    except Exception as e:
        print(f"   [aviso] No se pudo guardar imagen en disco ({key}): {e}")


async def _run(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


def _is_local_src(src: str) -> bool:
    """True if `src` is a local file path (a bundled template) rather than a URL.

    Provider sources are either http(s) URLs (Higgsfield) or local template
    paths (the fallback). Local paths can't be published directly — they must be
    uploaded to Blotato first.
    """
    return not src.lower().startswith(("http://", "https://"))


def _publishable_media(src: str, filename: str, *, api_key: str) -> str:
    """Turn a provider source into a Blotato-hosted, publishable media URL.

    URLs pass through unchanged. A local template path is read from disk and
    uploaded to Blotato so the raw template (without overlay) can still be
    published when Pillow is unavailable or the overlay failed.
    """
    if not _is_local_src(src):
        return src
    file_bytes = Path(src).read_bytes()
    return bc.upload_media_local(file_bytes, filename, api_key=api_key)


async def _push(queue: asyncio.Queue, event: dict):
    await queue.put(event)


async def _media_fallback(q: asyncio.Queue, raw_urls: dict, key: str, filename: str, cfg) -> list[str]:
    """Best-effort publishable media for `key` from raw_urls (URL or template path).

    Returns [url] when a publishable URL is obtained, else [] (and warns). A local
    template path is uploaded to Blotato first so it can be published raw.
    """
    if key not in raw_urls:
        return []
    try:
        url = await _run(_publishable_media, raw_urls[key], filename, api_key=cfg.blotato_api_key)
        return [url] if url else []
    except Exception as e:
        await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"No se pudo subir respaldo: {e}"})
        return []


async def run_pipeline(job: dict):
    q: asyncio.Queue = job["_queue"]
    params: dict = job["params"]
    cfg = job["_cfg"]

    source_type: str = params.get("source_type", "youtube")
    url: str = params.get("youtube_url", "")
    dry_run: bool = params.get("dry_run", False)
    formato_ig: str = params.get("formato_instagram", "imagen-unica")

    # Redes destino elegidas en el form/sheet (default: las tres). Fuente única: networks.
    nets = active_networks(params)
    do_linkedin = "linkedin" in nets
    do_instagram = "instagram" in nets
    do_facebook = "facebook" in nets

    try:
        # ── Step 1: Extract ──────────────────────────────────────────────
        # The pipeline downstream only needs a `content` dict (title/transcript/…)
        # and a `clean_url` (the LinkedIn "watch the video" CTA target, empty for
        # non-YouTube sources). Each source builds those two, then everything
        # after this step is source-agnostic.
        forced_lang = params.get("idioma", "auto")
        lang_hint = forced_lang if forced_lang in ("es", "en") else None

        if source_type == "audio":
            await _push(q, {"step": "extract", "status": "running", "msg": "Transcribiendo audio..."})
            if not cfg.transcription_available:
                raise RuntimeError(
                    "Transcripción de audio no configurada. Define OPENAI_API_KEY "
                    "(o GROQ_API_KEY + TRANSCRIPTION_BASE_URL), o usa "
                    "TRANSCRIPTION_ENGINE=local, en .env."
                )
            if cfg.transcription_engine == "local":
                transcript, audio_seconds = await _run(
                    trl.transcribe_audio_local,
                    job["_upload_bytes"], job["_upload_filename"],
                    model_size=cfg.transcription_local_model,
                    device=cfg.transcription_local_device,
                    compute_type=cfg.transcription_local_compute,
                    language=lang_hint,
                )
                whisper_model = "local"
            else:
                transcript, audio_seconds = await _run(
                    tr.transcribe_audio,
                    job["_upload_bytes"], job["_upload_filename"],
                    api_key=cfg.transcription_api_key,
                    base_url=cfg.transcription_base_url,
                    model=cfg.transcription_model,
                    language=lang_hint,
                )
                whisper_model = cfg.transcription_model
            if not transcript.strip():
                raise RuntimeError("La transcripción quedó vacía — revisa el audio o las credenciales.")
            # Tracking de costos: minutos transcritos (motor local = $0, igual se registra).
            await _track(job, service="whisper", operation="transcription",
                         units={"minutes": (audio_seconds or 0.0) / 60.0}, model=whisper_model)
            content = _content_from_text(transcript, default_title="Nota de voz")
            clean_url = ""

        elif source_type == "manual":
            await _push(q, {"step": "extract", "status": "running", "msg": "Leyendo texto..."})
            # Texto plano escrito directamente en el form (solo flujo individual).
            text = (params.get("manual_text") or "").strip()
            if not text:
                raise RuntimeError("El texto manual está vacío.")
            content = _content_from_text(text, default_title="Texto manual")
            clean_url = ""

        elif source_type == "texto":
            await _push(q, {"step": "extract", "status": "running", "msg": "Leyendo documento..."})
            # Accepts .txt/.md, PDF and Word (.docx); the extractor picks the parser
            # by extension/magic bytes and raises a user-facing message on failure.
            text = (await _run(doc.extract_document_text, job["_upload_bytes"], job["_upload_filename"])).strip()
            if not text:
                raise RuntimeError("El documento está vacío o no se pudo extraer texto.")
            content = _content_from_text(
                text, default_title=Path(job["_upload_filename"] or "Documento").stem
            )
            clean_url = ""

        else:  # youtube
            await _push(q, {"step": "extract", "status": "running", "msg": "Extrayendo video de YouTube..."})
            clean_url = re.sub(r'[&?]t=\d+s?', '', url)
            try:
                content = await _run(bc.extract_youtube_local, clean_url)
            except Exception:
                try:
                    vid_match = re.search(r'[?&]v=([^&]+)', url)
                    if vid_match:
                        content = await _run(bc.extract_youtube_local, f"https://www.youtube.com/watch?v={vid_match.group(1)}")
                    else:
                        raise
                except Exception as e:
                    content = {"title": url, "description": "", "transcript": "", "tags": [], "chapters": [], "channel": ""}
                    await _push(q, {"step": "extract", "status": "warn", "msg": f"No se pudo extraer transcript: {e}. Continuando con el título."})

        # Detect language
        if forced_lang in ("es", "en"):
            lang = forced_lang
        else:
            transcript_sample = (content.get("transcript") or content.get("title") or "")[:500].lower()
            es_words = sum(1 for w in ["de", "la", "el", "en", "que", "los", "las", "es", "con", "por"] if f" {w} " in transcript_sample)
            en_words = sum(1 for w in ["the", "and", "for", "with", "this", "that", "are", "have", "from", "you"] if f" {w} " in transcript_sample)
            lang = "en" if en_words > es_words else "es"

        content["lang"] = lang
        job["content"] = content
        params["lang"] = lang

        await _push(q, {"step": "extract", "status": "done", "msg": f"Idioma detectado: {lang} | {content.get('title', '')[:60]}"})

        # ── Step 2: Accounts ─────────────────────────────────────────────
        await _push(q, {"step": "accounts", "status": "running", "msg": "Verificando cuentas..."})

        # Precedence: account picked in the UI form > .env default > first listed account.
        li_account_id = params.get("linkedin_account_id") or cfg.linkedin_account_id
        ig_account_id = params.get("instagram_account_id") or cfg.instagram_account_id
        fb_account_id = params.get("facebook_account_id") or cfg.facebook_account_id

        if do_linkedin and not li_account_id:
            try:
                accounts = await _run(bc.get_accounts, "linkedin", api_key=cfg.blotato_api_key)
                if accounts:
                    li_account_id = str(accounts[0]["id"])
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener cuenta LinkedIn: {e}"})

        if do_instagram and not ig_account_id:
            try:
                accounts = await _run(bc.get_accounts, "instagram", api_key=cfg.blotato_api_key)
                if accounts:
                    ig_account_id = str(accounts[0]["id"])
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener cuenta Instagram: {e}"})

        if do_facebook and not fb_account_id:
            try:
                accounts = await _run(bc.get_accounts, "facebook", api_key=cfg.blotato_api_key)
                if accounts:
                    fb_account_id = str(accounts[0]["id"])
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener cuenta Facebook: {e}"})

        # LinkedIn page id (a "subaccount") is optional — empty means personal profile.
        # Facebook posts always target a Page (its pageId is a subaccount too).
        li_page_id = params.get("linkedin_page_id") or ""
        fb_page_id = params.get("facebook_page_id") or ""
        job["accounts"] = {
            "linkedin_id": li_account_id, "linkedin_page_id": li_page_id,
            "instagram_id": ig_account_id,
            "facebook_id": fb_account_id, "facebook_page_id": fb_page_id,
        }
        await _push(q, {"step": "accounts", "status": "done", "msg": "Cuentas configuradas"})

        # ── Step 3: Resolve tone/objective ───────────────────────────────
        tono_li = params.get("tono_linkedin") or params.get("tono") or "educativo"
        tono_ig = params.get("tono_instagram") or params.get("tono") or "inspiracional"
        tono_fb = params.get("tono_facebook") or params.get("tono") or "personal"
        obj_li = params.get("objetivo_linkedin") or params.get("objetivo") or "engagement"
        obj_ig = params.get("objetivo_instagram") or params.get("objetivo") or "engagement"
        obj_fb = params.get("objetivo_facebook") or params.get("objetivo") or "engagement"

        params.update({
            "tono_linkedin": tono_li,
            "tono_instagram": tono_ig,
            "tono_facebook": tono_fb,
            "objetivo_linkedin": obj_li,
            "objetivo_instagram": obj_ig,
            "objetivo_facebook": obj_fb,
            "formato_instagram": formato_ig,
        })

        # ── Step 4/4.5: Write + humanize posts ───────────────────────────
        if cfg.llm_provider == "perplexity":
            writer_label = params.get("modelo_perplexity") or "sonar-pro"
        else:
            writer_label = "Claude"
        await _push(q, {"step": "writing", "status": "running", "msg": f"Escribiendo posts con {writer_label}..."})

        posts, writer_usage = await write_posts(content, params, clean_url, q, cfg)
        job["posts"] = posts
        await _push(q, {"step": "writing", "status": "done", "msg": "Posts escritos y humanizados"})

        # Tracking de costos del LLM (tokens de entrada/salida + caché en Claude).
        if writer_usage:
            await _track(job, service=writer_usage["service"], operation="post_writing",
                         units=writer_usage["units"], model=writer_usage["model"])

        # ── Media decision: video (text-to-video) OR images ─────────────────
        tipo_medio = params.get("tipo_medio", "imagen")
        want_video = tipo_medio == "video"
        if want_video and not cfg.video_available:
            want_video = False
            await _push(q, {"step": "video", "status": "warn",
                            "msg": "Video solicitado pero Higgsfield no está configurado — se generan imágenes."})

        if want_video:
            # Single clean text-to-video clip (no text overlay) shared by both platforms.
            await _push(q, {"step": "video", "status": "running", "msg": "Generando video con Higgsfield..."})
            topic = content.get("title", "professional topic")
            video_prompt = (
                f"Cinematic editorial video about: {topic}. "
                "Smooth subtle camera motion, soft natural lighting, muted professional palette, "
                "elegant minimal scene. No text, no captions, no typography, no logos, no watermarks."
            )
            video_url = ""
            play_url = ""
            try:
                video_url = await _run(
                    hf.generate_video, video_prompt,
                    api_key=cfg.higgsfield_api_key, api_secret=cfg.higgsfield_api_secret,
                    aspect_ratio=cfg.higgsfield_video_aspect,
                    duration=(cfg.higgsfield_video_duration or None),
                    model=cfg.higgsfield_video_model,
                )
                # Tracking de costos: 1 clip generado en Higgsfield (éxito = hay URL).
                await _track(job, service="higgsfield", operation="video_generation",
                             units={"generations": 1}, model=cfg.higgsfield_video_model)
                # Re-host on Blotato so the post is decoupled from Higgsfield's CDN.
                # If that fails, fall back to the raw provider URL.
                try:
                    hosted = await _run(bc.upload_media_from_url, video_url, api_key=cfg.blotato_api_key)
                    play_url = hosted or video_url
                except Exception as e:
                    play_url = video_url
                    await _push(q, {"step": "video", "status": "warn",
                                    "msg": f"No se pudo re-hospedar el video en Blotato: {e}. Se usa la URL del proveedor."})
            except Exception as e:
                job["video"]["notice"] = f"No se pudo generar el video con Higgsfield: {e}"
                await _push(q, {"step": "video", "status": "warn", "msg": job["video"]["notice"]})

            job["video"]["provider"] = "higgsfield"
            if play_url:
                job["video"]["url"] = play_url
                job["_li_media_urls"] = [play_url] if do_linkedin else []
                job["_ig_media_urls"] = [play_url] if do_instagram else []
                job["_fb_media_urls"] = [play_url] if do_facebook else []
                job["images"]["blotato_urls"] = {
                    "linkedin": play_url if do_linkedin else "",
                    "instagram": [play_url] if do_instagram else [],
                    "facebook": play_url if do_facebook else "",
                }
                await _push(q, {"step": "video", "status": "done", "msg": "Video listo"})
            else:
                # No media — the user can still publish text-only, or retry.
                job["_li_media_urls"] = []
                job["_ig_media_urls"] = []
                job["_fb_media_urls"] = []

            job["status"] = "review"
            await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})
            return

        # ── Steps 5-7: Images (generate + overlay + upload) ──────────────────

        # Carousel slide count from the form (3–6); slide 0 = hook, last = credits,
        # the slides in between are info/argument slides.
        n_slides = max(3, min(6, int(params.get("carrusel_slides", 3) or 3)))

        expected_subkeys: list[str] = []
        if do_linkedin:
            expected_subkeys.append("li-hook")
        if do_facebook:
            expected_subkeys.append("fb-hook")
        if do_instagram:
            if formato_ig == "carrusel":
                expected_subkeys.extend(f"ig-{i}" for i in range(n_slides))
            else:
                expected_subkeys.append("ig-single")

        # El usuario puede forzar plantillas locales (sin llamar a Higgsfield) o usar
        # el flujo normal (Higgsfield con fallback a plantilla). Default: higgsfield.
        force_template = params.get("fuente_imagen", "higgsfield") == "template"
        provider = improv.make_provider(
            hf_key=cfg.higgsfield_api_key,
            hf_secret=cfg.higgsfield_api_secret,
            hf_model=cfg.higgsfield_model,
            hf_resolution=cfg.higgsfield_resolution,
            force_template=force_template,
        )

        await _push(q, {"step": "images", "status": "init", "subkeys": expected_subkeys})
        await _push(q, {"step": "images", "status": "running", "msg": f"Generando imágenes con {provider.label}..."})

        # image_bytes is mutable — /image/{key} can serve mid-pipeline as soon as a key is set
        image_bytes: dict[str, bytes] = job["images"]["bytes"]
        # raw_urls: provider image source per subkey (URL or local template path),
        # used as upload fallback when overlay/upload fails
        raw_urls: dict[str, str] = {}
        # image_warnings: reasons Higgsfield fell back to local templates (empty when not applicable)
        image_warnings: list[str] = []
        # overlay_text_warnings: reasons the overlay copy fell back to heuristics (missing image_text)
        overlay_text_warnings: list[str] = []

        # ── 5a: Base image (shared by LinkedIn, Facebook, IG single, carousel slide 0) ──
        base_url: str | None = None
        if do_linkedin or do_instagram or do_facebook:
            try:
                topic = content.get("title", "professional topic")
                base_prompt = (
                    f"Editorial photography about: {topic}. "
                    "Clean composition, soft natural lighting, muted professional palette, "
                    "composition with negative space at the bottom center for overlay text. "
                    "No text, no typography, no logos, no watermarks."
                )
                base_url = await _run(provider.generate_base, base_prompt)
            except Exception as e:
                await _push(q, {"step": "images", "status": "warn", "msg": f"Error generando imagen base: {e}"})
            image_warnings.extend(provider.pop_warnings())

        # ── 5b: Pre-warm carousel extra slides immediately in background ──────────
        # The provider starts generating slides 1 & 2 while LinkedIn/IG-0 overlays run.
        extra_prompts: list[str] = []
        extra_handles: list = []
        if do_instagram and formato_ig == "carrusel" and base_url:
            topic = content.get("title", "engaging topic")
            # Slides 1..n-1: (n_slides - 2) info slides + 1 closing/credits slide.
            n_info = n_slides - 2
            info_prompts = [
                f"Conceptual editorial visual about: {topic}. Lateral composition or texture, variation {i + 1}. Same color palette as the main image. No text, no typography, no logos, no watermarks."
                for i in range(n_info)
            ]
            credits_prompt = (
                f"Minimal closing visual about: {topic}. Simple centered composition, low saturation. "
                "Same style as the main image. No text, no typography, no logos, no watermarks."
            )
            extra_prompts = info_prompts + [credits_prompt]
            # Start generating slides 1..n-1 now (Higgsfield submits the jobs; the template
            # provider returns immediate handles) so they render while LinkedIn/IG-0 overlays run.
            # raw_urls for these slides are filled in at resolve time, once we have a real src.
            extra_handles = await _run(provider.prewarm_extras, extra_prompts)

        if not _HAS_OVERLAY:
            await _push(q, {"step": "images", "status": "warn", "msg": "Pillow no instalado — usando imágenes sin overlay"})

        # Overlay copy — prefer the LLM's dedicated image_text block (a finished
        # cover phrase + one closed idea per slide); degrade to the old heuristics
        # (kept as a safety net) when it's missing/short, and warn visibly.
        channel = content.get("channel", "")
        title_str = content.get("title", "")
        # Number of info (argument) slides between the hook and the credits slide.
        n_info = (n_slides - 2) if (do_instagram and formato_ig == "carrusel") else 1

        image_text = posts.get("image_text") if isinstance(posts.get("image_text"), dict) else None
        llm_hook = (image_text or {}).get("hook", "").strip()
        llm_slides = [s for s in (image_text or {}).get("slides", []) if s.strip()]

        # Hook: image_text.hook for every network; fall back to the first caption line.
        if llm_hook:
            li_hook = llm_hook
            ig_hook = llm_hook
            fb_hook = llm_hook
        else:
            li_hook = _extract_hook(posts.get("linkedin_text", ""), max_words=12)
            ig_hook = _extract_hook(posts.get("instagram_text", ""), max_words=10)
            fb_hook = _extract_hook(posts.get("facebook_text", ""), max_words=12)
            if do_linkedin or do_instagram or do_facebook:
                overlay_text_warnings.append("sin texto de portada del modelo")

        # Info slides: exactly one closed idea per slide. Use image_text.slides; if
        # short, pad from the heuristic body lines (NOT a generic "watch the video").
        carousel = do_instagram and formato_ig == "carrusel"
        slide_texts: list[str] = []
        if carousel:
            heur_lines = _extract_body_lines(posts.get("instagram_text", ""), max_lines=n_info)
            for i in range(n_info):
                if i < len(llm_slides):
                    slide_texts.append(llm_slides[i])
                elif i < len(heur_lines):
                    slide_texts.append(heur_lines[i])
                else:
                    slide_texts.append("")  # renderer pads with empty (no filler phrase)
            if len(llm_slides) < n_info:
                overlay_text_warnings.append(
                    f"el modelo dio {len(llm_slides)} de {n_info} frases para el carrusel"
                )

        # ── 5c: LinkedIn overlay (uses base_url — emits done immediately) ────────
        if do_linkedin:
            if base_url:
                raw_urls["li-hook"] = base_url
                if _HAS_OVERLAY:
                    try:
                        png = await _run(ov.render_linkedin_hook, base_url, li_hook, lang=lang, tone=tono_li)
                        image_bytes["li-hook"] = png
                        _save_image(job["id"], "li-hook", png)
                        await _push(q, {"step": "images", "status": "done", "subkey": "li-hook"})
                    except Exception as e:
                        await _push(q, {"step": "images", "status": "warn", "subkey": "li-hook", "msg": f"Overlay falló: {e}"})
                else:
                    await _push(q, {"step": "images", "status": "done", "subkey": "li-hook"})
            else:
                await _push(q, {"step": "images", "status": "warn", "subkey": "li-hook", "msg": "Sin imagen base"})

        # ── 5c-bis: Facebook overlay (mismo formato 4:5 que LinkedIn — usa base_url) ──
        if do_facebook:
            if base_url:
                raw_urls["fb-hook"] = base_url
                if _HAS_OVERLAY:
                    try:
                        png = await _run(ov.render_linkedin_hook, base_url, fb_hook, lang=lang, tone=tono_fb)
                        image_bytes["fb-hook"] = png
                        _save_image(job["id"], "fb-hook", png)
                        await _push(q, {"step": "images", "status": "done", "subkey": "fb-hook"})
                    except Exception as e:
                        await _push(q, {"step": "images", "status": "warn", "subkey": "fb-hook", "msg": f"Overlay falló: {e}"})
                else:
                    await _push(q, {"step": "images", "status": "done", "subkey": "fb-hook"})
            else:
                await _push(q, {"step": "images", "status": "warn", "subkey": "fb-hook", "msg": "Sin imagen base"})

        # ── 5d: Instagram overlay ─────────────────────────────────────────────────
        if do_instagram:
            if formato_ig != "carrusel":
                # Single image (uses base_url)
                if base_url:
                    raw_urls["ig-single"] = base_url
                    if _HAS_OVERLAY:
                        try:
                            png = await _run(ov.render_single, base_url, ig_hook, lang=lang, tone=tono_ig)
                            image_bytes["ig-single"] = png
                            _save_image(job["id"], "ig-single", png)
                            await _push(q, {"step": "images", "status": "done", "subkey": "ig-single"})
                        except Exception as e:
                            await _push(q, {"step": "images", "status": "warn", "subkey": "ig-single", "msg": f"Overlay falló: {e}"})
                    else:
                        await _push(q, {"step": "images", "status": "done", "subkey": "ig-single"})
                else:
                    await _push(q, {"step": "images", "status": "warn", "subkey": "ig-single", "msg": "Sin imagen base"})
            else:
                # Carousel slide 0 (uses base_url — no extra generation needed)
                if base_url:
                    raw_urls["ig-0"] = base_url
                    if _HAS_OVERLAY:
                        try:
                            png = await _run(ov.render_hook, base_url, ig_hook, lang=lang, tone=tono_ig)
                            image_bytes["ig-0"] = png
                            _save_image(job["id"], "ig-0", png)
                            await _push(q, {"step": "images", "status": "done", "subkey": "ig-0"})
                        except Exception as e:
                            await _push(q, {"step": "images", "status": "warn", "subkey": "ig-0", "msg": f"Overlay falló: {e}"})
                    else:
                        await _push(q, {"step": "images", "status": "done", "subkey": "ig-0"})
                else:
                    await _push(q, {"step": "images", "status": "warn", "subkey": "ig-0", "msg": "Sin imagen base"})

                # Carousel slides 1..n-1: (n_info) info slides + 1 credits slide.
                # ONE closed idea per info slide (from image_text.slides, padded
                # from heuristics — never a generic "watch the video" filler).
                extra_slide_defs = []
                for s in range(n_info):
                    idea = slide_texts[s] if s < len(slide_texts) else ""
                    # Bind idea via default arg so each lambda captures its own text.
                    extra_slide_defs.append((
                        f"ig-{s + 1}",
                        lambda u, t=idea: ov.render_info(u, t, lang=lang, tone=tono_ig),
                    ))
                extra_slide_defs.append(
                    (f"ig-{n_slides - 1}", lambda u: ov.render_credits(u, channel, title_str, lang=lang, tone=tono_ig))
                )
                for i, (fname, render_fn) in enumerate(extra_slide_defs):
                    if i >= len(extra_handles):
                        await _push(q, {"step": "images", "status": "warn", "subkey": fname, "msg": "Sin imagen base"})
                        continue
                    try:
                        slide_url = await _run(provider.resolve, extra_handles[i])
                        image_warnings.extend(provider.pop_warnings())
                        raw_urls[fname] = slide_url
                        if _HAS_OVERLAY:
                            png = await _run(render_fn, slide_url)
                            image_bytes[fname] = png
                            _save_image(job["id"], fname, png)
                        await _push(q, {"step": "images", "status": "done", "subkey": fname})
                    except Exception as e:
                        await _push(q, {"step": "images", "status": "warn", "subkey": fname, "msg": str(e)})

        # Catch-all: warn any expected subkey that never received a status event
        for key in expected_subkeys:
            if key not in image_bytes and key not in raw_urls:
                await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": "No se pudo generar"})

        # ── 5e: Upload ────────────────────────────────────────────────────────────
        li_media_urls: list[str] = []
        ig_media_urls: list[str] = []
        fb_media_urls: list[str] = []

        if do_linkedin:
            key = "li-hook"
            if key in image_bytes:
                try:
                    url_li = await _run(bc.upload_media_local, image_bytes[key], "linkedin-hook.png", api_key=cfg.blotato_api_key)
                    li_media_urls = [url_li]
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                    li_media_urls = await _media_fallback(q, raw_urls, key, "linkedin-hook.png", cfg)
            else:
                li_media_urls = await _media_fallback(q, raw_urls, key, "linkedin-hook.png", cfg)
            if li_media_urls:
                job["images"]["blotato_urls"]["linkedin"] = li_media_urls[0]

        if do_facebook:
            key = "fb-hook"
            if key in image_bytes:
                try:
                    url_fb = await _run(bc.upload_media_local, image_bytes[key], "facebook-hook.png", api_key=cfg.blotato_api_key)
                    fb_media_urls = [url_fb]
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                    fb_media_urls = await _media_fallback(q, raw_urls, key, "facebook-hook.png", cfg)
            else:
                fb_media_urls = await _media_fallback(q, raw_urls, key, "facebook-hook.png", cfg)
            if fb_media_urls:
                job["images"]["blotato_urls"]["facebook"] = fb_media_urls[0]

        if do_instagram:
            if formato_ig == "carrusel":
                for key in [f"ig-{i}" for i in range(n_slides)]:
                    if key in image_bytes:
                        try:
                            u = await _run(bc.upload_media_local, image_bytes[key], f"{key}.png", api_key=cfg.blotato_api_key)
                            ig_media_urls.append(u)
                        except Exception as e:
                            await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                            ig_media_urls.extend(await _media_fallback(q, raw_urls, key, f"{key}.png", cfg))
                    else:
                        ig_media_urls.extend(await _media_fallback(q, raw_urls, key, f"{key}.png", cfg))
            else:
                key = "ig-single"
                if key in image_bytes:
                    try:
                        u = await _run(bc.upload_media_local, image_bytes[key], "ig-single.png", api_key=cfg.blotato_api_key)
                        ig_media_urls = [u]
                    except Exception as e:
                        await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                        ig_media_urls = await _media_fallback(q, raw_urls, key, "ig-single.png", cfg)
                else:
                    ig_media_urls = await _media_fallback(q, raw_urls, key, "ig-single.png", cfg)

            job["images"]["blotato_urls"]["instagram"] = ig_media_urls

        job["_li_media_urls"] = li_media_urls
        job["_ig_media_urls"] = ig_media_urls
        job["_fb_media_urls"] = fb_media_urls

        # Surface warnings — live in the progress step and durably (stored on the
        # job → shown on the review screen). Two independent kinds can co-occur:
        # (a) Higgsfield fell back to local templates; (b) the overlay copy fell back
        # to heuristics because the LLM's image_text was missing/short. Accumulate both.
        # Count what actually got produced (overlaid bytes) — a local template that never
        # got an overlay rendered onto it isn't publishable to Blotato on its own.
        job["images"]["provider"] = provider.name
        # Tracking de costos: solo las generaciones HF reales (las que cayeron a
        # plantilla local son gratis y no las cuenta el provider).
        hf_gens = getattr(provider, "hf_generations", 0)
        if hf_gens:
            await _track(job, service="higgsfield", operation="image_generation",
                         units={"generations": hf_gens}, model=cfg.higgsfield_model)
        produced = len({k for k in image_bytes} | {k for k in raw_urls})
        notices: list[str] = []
        if produced == 0:
            notices.append(
                "No se pudo generar ninguna imagen — Higgsfield no respondió y no se "
                "pudo aplicar la plantilla de respaldo. Revisa los créditos de Higgsfield "
                "y que las plantillas estén en api/assets/templates/. "
                "Puedes publicar sin imagen o reintentar."
            )
        elif image_warnings:
            reasons = list(dict.fromkeys(image_warnings))  # dedupe, preserve order
            notices.append(
                f"Higgsfield no disponible ({'; '.join(reasons)}) — "
                f"{produced} imagen(es) generada(s) con plantillas de respaldo."
            )
        if overlay_text_warnings:
            reasons = list(dict.fromkeys(overlay_text_warnings))  # dedupe, preserve order
            notices.append(
                f"El texto sobre las imágenes usó el método de respaldo ({'; '.join(reasons)}) — "
                "revisa que el copy de los visuales se lea bien."
            )

        if notices:
            notice = " ".join(notices)
            job["images"]["notice"] = notice
            await _push(q, {"step": "images", "status": "warn", "msg": notice})
        else:
            await _push(q, {"step": "images", "status": "done", "msg": "Imágenes listas"})

        # ── Done ─────────────────────────────────────────────────────────
        job["status"] = "review"
        await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})

    except Exception as e:
        job["status"] = "error"
        job["error_msg"] = str(e)
        await _push(q, {"step": "error", "msg": str(e)})


# ── Publishing ──────────────────────────────────────────────────────────────────

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


async def publish_job_posts(job: dict, schedule_time: str | None = "") -> dict:
    """Publish (or schedule) a job's already-generated posts to Blotato.

    Shared by the single-post publish endpoint and the bulk batch runner. Respects
    `params.redes` (the set of target networks) and `params.dry_run` (generate but
    don't publish). `schedule_time` is an ISO-8601 string for deferred publishing,
    or empty/None to publish now. Sets `job["result"]`/`job["status"]` and returns
    the result dict.
    """
    cfg = job["_cfg"]
    posts = job["posts"]
    accounts = job["accounts"]
    params = job["params"]
    dry_run = params.get("dry_run", False)

    # Redes destino (default: las tres). Fuente única compartida con run_pipeline.
    nets = active_networks(params)
    do_linkedin = "linkedin" in nets
    do_instagram = "instagram" in nets
    do_facebook = "facebook" in nets

    li_text = posts.get("linkedin_text", "")
    ig_text = posts.get("instagram_text", "")
    fb_text = posts.get("facebook_text", "")
    li_media = job.get("_li_media_urls", [])
    ig_media = job.get("_ig_media_urls", [])
    fb_media = job.get("_fb_media_urls", [])

    result: dict = {}
    scheduled_at = (schedule_time or "").strip() or None

    if not dry_run and do_linkedin and accounts.get("linkedin_id") and li_text:
        try:
            resp = await _run(bc.publish_post, accounts["linkedin_id"], "linkedin", li_text, li_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at,
                              page_id=accounts.get("linkedin_page_id") or None)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["linkedin"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["linkedin"] = {"error": str(e)}

    if not dry_run and do_instagram and accounts.get("instagram_id") and ig_text:
        try:
            resp = await _run(bc.publish_post, accounts["instagram_id"], "instagram", ig_text, ig_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at, share_to_feed=True)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["instagram"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["instagram"] = {"error": str(e)}

    if not dry_run and do_facebook and accounts.get("facebook_id") and fb_text:
        try:
            resp = await _run(bc.publish_post, accounts["facebook_id"], "facebook", fb_text, fb_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at,
                              page_id=accounts.get("facebook_page_id") or None)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["facebook"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["facebook"] = {"error": str(e)}

    if dry_run:
        result["dry_run"] = True
        result["linkedin"] = {"status": "dry-run"}
        result["instagram"] = {"status": "dry-run"}
        result["facebook"] = {"status": "dry-run"}

    job["result"] = result
    job["status"] = "done"
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _content_from_text(text: str, *, default_title: str) -> dict:
    """Build the pipeline `content` dict from a plain transcript/document.

    Shared by the audio (transcription) and text-file sources. The title is the
    first non-empty line (trimmed); the rest is treated as the transcript so the
    post writer has the full material. No tags/chapters/channel are available.
    """
    text = (text or "").strip()
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    title = (first_line[:120] or default_title).strip()
    return {
        "title": title,
        "description": "",
        "transcript": text,
        "summary": "",
        "keyPoints": [],
        "tags": [],
        "chapters": [],
        "channel": "",
    }


def _extract_hook(text: str, max_words: int = 12) -> str:
    if not text:
        return ""
    first_line = text.strip().split("\n")[0].strip()
    words = first_line.split()
    return " ".join(words[:max_words])


def _extract_body_lines(text: str, max_lines: int = 3) -> list[str]:
    """Heuristic fallback for info-slide copy: real caption lines, one idea each.

    Returns up to `max_lines` lines (possibly fewer / empty). No generic filler —
    the renderer pads missing slides with empty text instead of a canned phrase.
    """
    lines = [l.strip().lstrip("•→-* ") for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
    # Skip the first line (hook) and grab up to max_lines body lines
    body = [l for l in lines[1:] if not l.startswith("▶") and not l.startswith("#") and len(l) > 10]
    return body[:max_lines]
