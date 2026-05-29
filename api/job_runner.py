import asyncio
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import blotato_client as bc
import image_provider as improv
import higgsfield_client as hf

try:
    import image_overlay as ov
    _HAS_OVERLAY = True
except ImportError:
    _HAS_OVERLAY = False

from post_writer import write_posts

_loop_executor = None
_OUTPUTS_DIR = Path(__file__).parent / "outputs"


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


async def _push(queue: asyncio.Queue, event: dict):
    await queue.put(event)


async def run_pipeline(job: dict):
    q: asyncio.Queue = job["_queue"]
    params: dict = job["params"]
    cfg = job["_cfg"]

    url: str = params["youtube_url"]
    solo: str = params.get("solo", "")
    dry_run: bool = params.get("dry_run", False)
    formato_ig: str = params.get("formato_instagram", "imagen-unica")

    do_linkedin = solo != "instagram"
    do_instagram = solo != "linkedin"

    try:
        # ── Step 1: Extract ──────────────────────────────────────────────
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
        forced_lang = params.get("idioma", "auto")
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

        li_account_id = cfg.linkedin_account_id
        ig_account_id = cfg.instagram_account_id

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

        job["accounts"] = {"linkedin_id": li_account_id, "instagram_id": ig_account_id}
        await _push(q, {"step": "accounts", "status": "done", "msg": "Cuentas configuradas"})

        # ── Step 3: Resolve tone/objective ───────────────────────────────
        tono_li = params.get("tono_linkedin") or params.get("tono") or "educativo"
        tono_ig = params.get("tono_instagram") or params.get("tono") or "inspiracional"
        obj_li = params.get("objetivo_linkedin") or params.get("objetivo") or "engagement"
        obj_ig = params.get("objetivo_instagram") or params.get("objetivo") or "engagement"

        params.update({
            "tono_linkedin": tono_li,
            "tono_instagram": tono_ig,
            "objetivo_linkedin": obj_li,
            "objetivo_instagram": obj_ig,
            "formato_instagram": formato_ig,
        })

        # ── Step 4/4.5: Write + humanize posts ───────────────────────────
        if cfg.llm_provider == "perplexity":
            writer_label = params.get("modelo_perplexity") or "sonar-pro"
        else:
            writer_label = "Claude"
        await _push(q, {"step": "writing", "status": "running", "msg": f"Escribiendo posts con {writer_label}..."})

        posts = await write_posts(content, params, clean_url, q, cfg)
        job["posts"] = posts
        await _push(q, {"step": "writing", "status": "done", "msg": "Posts escritos y humanizados"})

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
                job["images"]["blotato_urls"] = {
                    "linkedin": play_url if do_linkedin else "",
                    "instagram": [play_url] if do_instagram else [],
                }
                await _push(q, {"step": "video", "status": "done", "msg": "Video listo"})
            else:
                # No media — the user can still publish text-only, or retry.
                job["_li_media_urls"] = []
                job["_ig_media_urls"] = []

            job["status"] = "review"
            await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})
            return

        # ── Steps 5-7: Images (generate + overlay + upload) ──────────────────

        expected_subkeys: list[str] = []
        if do_linkedin:
            expected_subkeys.append("li-hook")
        if do_instagram:
            if formato_ig == "carrusel":
                expected_subkeys.extend(["ig-0", "ig-1", "ig-2"])
            else:
                expected_subkeys.append("ig-single")

        provider = improv.make_provider(
            hf_key=cfg.higgsfield_api_key,
            hf_secret=cfg.higgsfield_api_secret,
            hf_model=cfg.higgsfield_model,
            hf_resolution=cfg.higgsfield_resolution,
        )

        await _push(q, {"step": "images", "status": "init", "subkeys": expected_subkeys})
        await _push(q, {"step": "images", "status": "running", "msg": f"Generando imágenes con {provider.label}..."})

        # image_bytes is mutable — /image/{key} can serve mid-pipeline as soon as a key is set
        image_bytes: dict[str, bytes] = job["images"]["bytes"]
        # raw_urls: provider image URL per subkey, used as upload fallback when overlay/upload fails
        raw_urls: dict[str, str] = {}
        # image_warnings: reasons Higgsfield fell back to Pollinations (empty when not applicable)
        image_warnings: list[str] = []

        # ── 5a: Base image (shared by LinkedIn, IG single, and carousel slide 0) ──
        base_url: str | None = None
        if do_linkedin or do_instagram:
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
            extra_prompts = [
                f"Conceptual editorial visual about: {topic}. Lateral composition or texture. Same color palette as the main image. No text, no typography, no logos, no watermarks.",
                f"Minimal closing visual about: {topic}. Simple centered composition, low saturation. Same style as the main image. No text, no typography, no logos, no watermarks.",
            ]
            # Start generating slides 1 & 2 now (Higgsfield submits the jobs; Pollinations
            # fires background triggers) so they render while LinkedIn/IG-0 overlays run.
            # raw_urls for these slides are filled in at resolve time, once we have a real URL.
            extra_handles = await _run(provider.prewarm_extras, extra_prompts)

        if not _HAS_OVERLAY:
            await _push(q, {"step": "images", "status": "warn", "msg": "Pillow no instalado — usando imágenes sin overlay"})

        # Overlay helpers (computed once, used across all subkeys)
        li_hook = _extract_hook(posts.get("linkedin_text", ""), max_words=12)
        ig_hook = _extract_hook(posts.get("instagram_text", ""), max_words=10)
        channel = content.get("channel", "")
        title_str = content.get("title", "")
        body_lines = _extract_body_lines(posts.get("instagram_text", "")) if do_instagram else []
        heading = _extract_heading(title_str) if do_instagram else ""

        # ── 5c: LinkedIn overlay (uses base_url — emits done immediately) ────────
        if do_linkedin:
            if base_url:
                raw_urls["li-hook"] = base_url
                if _HAS_OVERLAY:
                    try:
                        png = await _run(ov.render_linkedin_hook, base_url, ig_hook, lang=lang)
                        image_bytes["li-hook"] = png
                        _save_image(job["id"], "li-hook", png)
                        await _push(q, {"step": "images", "status": "done", "subkey": "li-hook"})
                    except Exception as e:
                        await _push(q, {"step": "images", "status": "warn", "subkey": "li-hook", "msg": f"Overlay falló: {e}"})
                else:
                    await _push(q, {"step": "images", "status": "done", "subkey": "li-hook"})
            else:
                await _push(q, {"step": "images", "status": "warn", "subkey": "li-hook", "msg": "Sin imagen base"})

        # ── 5d: Instagram overlay ─────────────────────────────────────────────────
        if do_instagram:
            if formato_ig != "carrusel":
                # Single image (uses base_url)
                if base_url:
                    raw_urls["ig-single"] = base_url
                    if _HAS_OVERLAY:
                        try:
                            png = await _run(ov.render_single, base_url, ig_hook, lang=lang)
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
                            png = await _run(ov.render_hook, base_url, ig_hook, lang=lang)
                            image_bytes["ig-0"] = png
                            _save_image(job["id"], "ig-0", png)
                            await _push(q, {"step": "images", "status": "done", "subkey": "ig-0"})
                        except Exception as e:
                            await _push(q, {"step": "images", "status": "warn", "subkey": "ig-0", "msg": f"Overlay falló: {e}"})
                    else:
                        await _push(q, {"step": "images", "status": "done", "subkey": "ig-0"})
                else:
                    await _push(q, {"step": "images", "status": "warn", "subkey": "ig-0", "msg": "Sin imagen base"})

                # Carousel slides 1 & 2: fetch (pre-warmed) then overlay individually
                extra_slide_defs = [
                    ("ig-1", lambda u: ov.render_info(u, body_lines, heading=heading, lang=lang)),
                    ("ig-2", lambda u: ov.render_credits(u, channel, title_str, lang=lang)),
                ]
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

        if do_linkedin:
            key = "li-hook"
            if key in image_bytes:
                try:
                    url_li = await _run(bc.upload_media_local, image_bytes[key], "linkedin-hook.png", api_key=cfg.blotato_api_key)
                    li_media_urls = [url_li]
                    job["images"]["blotato_urls"]["linkedin"] = url_li
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                    if key in raw_urls:
                        li_media_urls = [raw_urls[key]]
            elif key in raw_urls:
                li_media_urls = [raw_urls[key]]

        if do_instagram:
            if formato_ig == "carrusel":
                for key in ["ig-0", "ig-1", "ig-2"]:
                    if key in image_bytes:
                        try:
                            u = await _run(bc.upload_media_local, image_bytes[key], f"{key}.png", api_key=cfg.blotato_api_key)
                            ig_media_urls.append(u)
                        except Exception as e:
                            await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                            if key in raw_urls:
                                ig_media_urls.append(raw_urls[key])
                    elif key in raw_urls:
                        ig_media_urls.append(raw_urls[key])
            else:
                key = "ig-single"
                if key in image_bytes:
                    try:
                        u = await _run(bc.upload_media_local, image_bytes[key], "ig-single.png", api_key=cfg.blotato_api_key)
                        ig_media_urls = [u]
                    except Exception as e:
                        await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                        if key in raw_urls:
                            ig_media_urls = [raw_urls[key]]
                elif "ig-single" in raw_urls:
                    ig_media_urls = [raw_urls["ig-single"]]

            job["images"]["blotato_urls"]["instagram"] = ig_media_urls

        job["_li_media_urls"] = li_media_urls
        job["_ig_media_urls"] = ig_media_urls

        # If Higgsfield fell back to Pollinations on any image, surface why — live in the
        # progress step and durably (stored on the job → shown on the review screen).
        job["images"]["provider"] = provider.name
        if image_warnings:
            reasons = list(dict.fromkeys(image_warnings))  # dedupe, preserve order
            notice = (
                f"Higgsfield no disponible ({'; '.join(reasons)}) — "
                f"{len(image_warnings)} imagen(es) generada(s) con Pollinations."
            )
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_hook(text: str, max_words: int = 12) -> str:
    if not text:
        return ""
    first_line = text.strip().split("\n")[0].strip()
    words = first_line.split()
    return " ".join(words[:max_words])


def _extract_body_lines(text: str) -> list[str]:
    lines = [l.strip().lstrip("•→-* ") for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
    # Skip the first line (hook) and grab up to 3 body lines
    body = [l for l in lines[1:] if not l.startswith("▶") and not l.startswith("#") and len(l) > 10]
    return body[:3] or ["Mira el video completo para más detalles."]


def _extract_heading(title: str) -> str:
    words = title.upper().split()
    return " ".join(words[:5]) if len(words) > 5 else title.upper()
