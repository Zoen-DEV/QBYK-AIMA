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

        # ── Steps 5-7: Images (generate + overlay + upload) ──────────────────

        expected_subkeys: list[str] = []
        if do_linkedin:
            expected_subkeys.append("li-hook")
        if do_instagram:
            if formato_ig == "carrusel":
                expected_subkeys.extend(["ig-0", "ig-1", "ig-2"])
            else:
                expected_subkeys.append("ig-single")

        await _push(q, {"step": "images", "status": "init", "subkeys": expected_subkeys})
        await _push(q, {"step": "images", "status": "running", "msg": "Generando imágenes con Pollinations..."})

        # image_bytes is mutable — /image/{key} can serve mid-pipeline as soon as a key is set
        image_bytes: dict[str, bytes] = job["images"]["bytes"]
        # raw_urls: Pollinations URL per subkey, used as upload fallback when overlay fails
        raw_urls: dict[str, str] = {}

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
                urls = await _run(pc.generate_image, base_prompt, aspect_ratio="square_1_1", seed=42)
                base_url = urls[0]
            except Exception as e:
                await _push(q, {"step": "images", "status": "warn", "msg": f"Error generando imagen base: {e}"})

        # ── 5b: Pre-warm carousel extra slides immediately in background ──────────
        # Pollinations starts generating slides 1 & 2 while LinkedIn/IG-0 overlays run.
        extra_prompts: list[str] = []
        extra_urls: list[str] = []
        if do_instagram and formato_ig == "carrusel" and base_url:
            topic = content.get("title", "engaging topic")
            extra_prompts = [
                f"Conceptual editorial visual about: {topic}. Lateral composition or texture. Same color palette as the main image. No text, no typography, no logos, no watermarks.",
                f"Minimal closing visual about: {topic}. Simple centered composition, low saturation. Same style as the main image. No text, no typography, no logos, no watermarks.",
            ]
            extra_urls = pc.prewarm_carousel_extra_slides(extra_prompts)
            raw_urls["ig-1"] = extra_urls[0]
            raw_urls["ig-2"] = extra_urls[1]

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
                    if i >= len(extra_urls):
                        await _push(q, {"step": "images", "status": "warn", "subkey": fname, "msg": "Sin imagen base"})
                        continue
                    try:
                        await _run(pc.fetch_url, extra_urls[i])
                        if _HAS_OVERLAY:
                            png = await _run(render_fn, extra_urls[i])
                            image_bytes[fname] = png
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
