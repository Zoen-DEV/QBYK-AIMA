import asyncio
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import blotato_client as bc
import pollinations_client as pc

try:
    import image_overlay as ov
    _HAS_OVERLAY = True
except ImportError:
    _HAS_OVERLAY = False

from post_writer import write_posts

_loop_executor = None


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
        await _push(q, {"step": "writing", "status": "running", "msg": "Escribiendo posts con Claude..."})

        posts = await write_posts(content, params, clean_url, q, cfg)
        job["posts"] = posts
        await _push(q, {"step": "writing", "status": "done", "msg": "Posts escritos y humanizados"})

        # ── Step 5: Generate images ───────────────────────────────────────
        await _push(q, {"step": "images", "status": "running", "msg": "Generando imágenes con Pollinations..."})

        li_base_urls: list[str] = []
        ig_base_urls: list[str] = []

        if do_linkedin:
            try:
                topic = content.get("title", "professional topic")
                li_prompt = (
                    f"Professional editorial photography about: {topic}. "
                    "Clean composition, soft natural lighting, muted professional palette, "
                    "composition with negative space at the bottom for overlay text. "
                    "No text, no typography, no logos, no watermarks."
                )
                li_base_urls = await _run(pc.generate_image, li_prompt, aspect_ratio="social_post_4_5")
            except Exception as e:
                await _push(q, {"step": "images", "status": "warn", "msg": f"Error generando imagen LinkedIn: {e}"})

        if do_instagram:
            try:
                topic = content.get("title", "engaging topic")
                if formato_ig == "carrusel":
                    prompts = [
                        f"Bold editorial visual about: {topic}. Centered composition with negative space in the middle. Modern style, vibrant. No text, no typography, no logos, no watermarks.",
                        f"Conceptual editorial visual about: {topic}. Lateral composition or texture. Same color palette as previous. No text, no typography, no logos, no watermarks.",
                        f"Minimal closing visual about: {topic}. Simple composition, low saturation, centered. Same style. No text, no typography, no logos, no watermarks.",
                    ]
                    ig_base_urls = await _run(pc.generate_carousel, prompts, aspect_ratio="square_1_1")
                else:
                    ig_prompt = (
                        f"Modern editorial visual about: {topic}. "
                        "Vibrant but tasteful, strong focal point, "
                        "composition with negative space at the bottom for overlay text. "
                        "No text, no typography, no logos, no watermarks."
                    )
                    ig_base_urls = await _run(pc.generate_image, ig_prompt, aspect_ratio="square_1_1")
            except Exception as e:
                await _push(q, {"step": "images", "status": "warn", "msg": f"Error generando imagen Instagram: {e}"})

        await _push(q, {"step": "images", "status": "done", "msg": "Imágenes generadas"})

        # ── Step 6: Overlay ───────────────────────────────────────────────
        await _push(q, {"step": "overlay", "status": "running", "msg": "Aplicando overlay de texto..."})

        image_bytes: dict[str, bytes] = {}

        if not _HAS_OVERLAY:
            await _push(q, {"step": "overlay", "status": "warn", "msg": "Pillow no instalado — usando imágenes sin overlay"})
        else:
            li_hook = _extract_hook(posts.get("linkedin_text", ""), max_words=12)
            ig_hook = _extract_hook(posts.get("instagram_text", ""), max_words=10)

            if do_linkedin and li_base_urls:
                try:
                    png = await _run(ov.render_linkedin_hook, li_base_urls[0], li_hook, lang=lang)
                    image_bytes["li-hook"] = png
                except Exception as e:
                    await _push(q, {"step": "overlay", "status": "warn", "msg": f"LinkedIn overlay falló: {e}"})

            if do_instagram and ig_base_urls:
                if formato_ig == "carrusel":
                    channel = content.get("channel", "")
                    title = content.get("title", "")
                    body_lines = _extract_body_lines(posts.get("instagram_text", ""))
                    heading = _extract_heading(title)
                    for i, (fname, render_fn) in enumerate([
                        ("ig-0", lambda u: ov.render_hook(u, ig_hook, lang=lang)),
                        ("ig-1", lambda u: ov.render_info(u, body_lines, heading=heading, lang=lang)),
                        ("ig-2", lambda u: ov.render_credits(u, channel, title, lang=lang)),
                    ]):
                        base = ig_base_urls[i] if i < len(ig_base_urls) and ig_base_urls[i] else None
                        if base:
                            try:
                                png = await _run(render_fn, base)
                                image_bytes[fname] = png
                            except Exception as e:
                                await _push(q, {"step": "overlay", "status": "warn", "msg": f"Carousel slide {i+1} overlay falló: {e}"})
                else:
                    if ig_base_urls:
                        try:
                            png = await _run(ov.render_single, ig_base_urls[0], ig_hook, lang=lang)
                            image_bytes["ig-single"] = png
                        except Exception as e:
                            await _push(q, {"step": "overlay", "status": "warn", "msg": f"Instagram overlay falló: {e}"})

        job["images"] = {"bytes": image_bytes, "blotato_urls": {"linkedin": "", "instagram": []}, "base_urls": {"linkedin": li_base_urls, "instagram": ig_base_urls}}
        await _push(q, {"step": "overlay", "status": "done", "msg": "Overlay aplicado"})

        # ── Step 7: Upload ────────────────────────────────────────────────
        await _push(q, {"step": "upload", "status": "running", "msg": "Subiendo imágenes a Blotato..."})

        li_media_urls: list[str] = []
        ig_media_urls: list[str] = []

        if do_linkedin:
            if "li-hook" in image_bytes:
                try:
                    url_li = await _run(bc.upload_media_local, image_bytes["li-hook"], "linkedin-hook.png", api_key=cfg.blotato_api_key)
                    li_media_urls = [url_li]
                    job["images"]["blotato_urls"]["linkedin"] = url_li
                except Exception as e:
                    await _push(q, {"step": "upload", "status": "warn", "msg": f"Upload LinkedIn falló: {e}. Usando URL directa."})
                    if li_base_urls:
                        li_media_urls = [li_base_urls[0]]
            elif li_base_urls:
                li_media_urls = [li_base_urls[0]]

        if do_instagram:
            if formato_ig == "carrusel":
                slide_keys = ["ig-0", "ig-1", "ig-2"]
                for key in slide_keys:
                    if key in image_bytes:
                        try:
                            u = await _run(bc.upload_media_local, image_bytes[key], f"{key}.png", api_key=cfg.blotato_api_key)
                            ig_media_urls.append(u)
                        except Exception:
                            idx = int(key.split("-")[1])
                            if idx < len(ig_base_urls) and ig_base_urls[idx]:
                                ig_media_urls.append(ig_base_urls[idx])
            else:
                if "ig-single" in image_bytes:
                    try:
                        u = await _run(bc.upload_media_local, image_bytes["ig-single"], "ig-single.png", api_key=cfg.blotato_api_key)
                        ig_media_urls = [u]
                    except Exception:
                        if ig_base_urls:
                            ig_media_urls = [ig_base_urls[0]]
                elif ig_base_urls:
                    ig_media_urls = [ig_base_urls[0]]

            job["images"]["blotato_urls"]["instagram"] = ig_media_urls

        job["_li_media_urls"] = li_media_urls
        job["_ig_media_urls"] = ig_media_urls

        await _push(q, {"step": "upload", "status": "done", "msg": "Imágenes subidas"})

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
