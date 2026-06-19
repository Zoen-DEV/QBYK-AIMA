import asyncio
import json
import re
import urllib.request
import urllib.error

from networks import active_networks

PERPLEXITY_MODEL = "sonar-pro"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODELS = {"sonar", "sonar-pro"}

# Perplexity's sonar models are search-augmented by default. For this task we only
# want it to rewrite the supplied transcript, never pull in external/online facts
# (rule #3: no fabricar datos). This directive + low search context keeps it grounded.
_PERPLEXITY_GROUNDING = (
    "CRITICAL: Do NOT use web search results or any external/online knowledge. "
    "Base the posts EXCLUSIVELY on the transcript, title, and description provided "
    "in the user message. Do not add facts, figures, names, or events that are not "
    "present in that material, and do not include citation markers like [1]."
)


def _system_prompt() -> str:
    return """You are an expert AI social media manager. Your task is to write optimized posts for LinkedIn, Instagram and Facebook based on YouTube video content.

OUTPUT FORMAT: Respond with ONLY valid JSON in this exact shape — no markdown, no explanation, no preamble:
{"linkedin_text": "...", "instagram_text": "...", "facebook_text": "...", "image_text": {"hook": "...", "slides": ["...", "..."]}}
Only write text for the platforms requested in the user message; set any non-requested platform's text to an empty string.

=== IMAGE TEXT (overlay copy for the visuals) ===
The `image_text` object is the text that gets printed ON the images/carousel — it is NOT the caption. Write it as standalone, designed-poster copy:
- `hook`: ONE short, complete, punchy phrase for the cover image (max ~10 words). It must read as a finished statement, not a truncated sentence. No hashtags, no emojis, no URL, no trailing "…". Capitalize naturally (sentence case, not ALL CAPS).
- `slides`: an array of short idea-statements, ONE per info slide. The exact number of slides required is given in the user message ("INFO SLIDES NEEDED: N") — output EXACTLY that many strings. Each string is a single self-contained idea (max ~14 words), the kind of line that fills a whole slide on its own. Do NOT split one idea across slides, do NOT number them, no bullets, no emojis, no hashtags. Each must be drawn faithfully from the transcript/title (same no-fabrication rule as the posts).
- If only one platform is requested or Instagram is a single image, still provide `hook`; `slides` may be an empty array when no carousel is needed.
Write `image_text` in the same language as the posts.

=== LINKEDIN POST RULES ===
- 150–300 words
- Strong hook in the first line — NEVER start with "En este video..." / "In this video..." / "Descubre cómo..." / "Discover how..."
- 3–5 key insights or takeaways with → or bullet formatting
- Conversational but authoritative tone
- If a source video URL is provided (see the user message), include it on its own line just before the hashtags, with this exact CTA:
  Spanish: "▶ Mira el video completo aquí: <url>"
  English: "▶ Watch the full video here: <url>"
  Do NOT wrap the URL in markdown — paste it raw. If NO source URL is provided, skip this line entirely (do not invent a URL or a "watch the video" CTA).
- End with a question to spark engagement (goes after the URL line if present, before or among the hashtags)
- 3–5 relevant hashtags at the very end

=== INSTAGRAM POST RULES ===
- 80–150 words
- Bold opening hook (1 sentence)
- Short punchy sentences or bullets
- 3–6 emojis woven in naturally (not stacked at the end or beginning)
- Clear call-to-action: "Link en bio" / "Link in bio" — do NOT paste the raw YouTube URL in captions
- MAXIMUM 5 hashtags (hard limit — the platform rejects more)

=== FACEBOOK POST RULES ===
- 80–180 words
- Warm, conversational opening hook (1–2 sentences) — write like a person talking to their community, not a corporate brand
- Short paragraphs; you may use 2–3 bullets but prose is fine
- 1–3 emojis woven in naturally (optional, never stacked)
- If a source video URL is provided (see the user message), you MAY include it raw on its own line near the end (Facebook renders link previews); if NO source URL is provided, do not invent one
- End with a question or a clear call-to-action
- 2–4 relevant hashtags at the very end (Facebook hashtags are low-value, keep them few)

=== FAITHFUL CITATIONS (STRICT) ===
Any verbatim quote, number, percentage, name, or specific claim in either post MUST appear literally in the transcript (or title+description if transcript is empty). Never invent figures, attributions, or quotes. Paraphrasing is fine, fabricating is not.

=== SMART HASHTAGS ===
Build the hashtag pool primarily from the video's own `tags` and `chapters`:
1. Pick 2-3 from `tags` that fit the platform's audience (skip generic ones like #video)
2. Add 1-2 derived from `chapters` titles
3. Only invent extra if still under minimum — keep them concrete and topic-specific
4. Cap: LinkedIn 3-5, Instagram max 5, Facebook 2-4

=== HUMANIZATION CHECKLIST (apply before outputting) ===
Apply these rules to every post silently:
1. Delete AI filler connectors — ES: "En conclusión", "En resumen", "En definitiva", "Es importante destacar", "Cabe destacar", "Asimismo", "Por consiguiente", "En última instancia", "Sin lugar a dudas". EN: "In conclusion", "It's important to note that", "Furthermore", "Moreover", "That said,", "Needless to say", "At the end of the day"
2. Remove inflated AI vocabulary — ES: "revolucionario", "transformador", "disruptivo", "imprescindible", "esencialmente", "fundamentalmente". EN: "game-changer", "leverage", "unlock", "harness", "elevate", "delve into", "robust", "seamless", "cutting-edge", "synergy", "empower"
3. Vary sentence lengths — if 3+ consecutive sentences are similar length, break one or extend another
4. Break perfect bullet parallelism — not all bullets should start with the same verb
5. Em-dash moderation — maximum 1 em-dash (— or –) per post, ideally zero; replace with comma, colon, or parentheses
6. Decorative AI emojis — 🚀 🎯 💡 🌟 ✨ 🔥 💪 🌱 are forbidden as decoration; never stack emojis at the start; prefer concrete topic-specific emojis
7. No forced colloquialisms — natural language only, no artificial casual register
8. Hook check — first line cannot match these patterns:
   ES: "En este video/post/artículo…", "Descubre cómo…", "¿Alguna vez te has preguntado…?", "Imagina que…", "¿Sabías que…?", "Hoy te voy a contar…"
   EN: "In this video/post/article…", "Discover how…", "Have you ever wondered…?", "Imagine if…", "Did you know that…?"
   If the hook matches, rewrite it to something specific and concrete from the transcript
9. Never add content not in the transcript — humanization is stylistic only
"""


def _user_message(content: dict, params: dict, clean_url: str) -> str:
    lang = params.get("lang", "es")
    tono_li = params.get("tono_linkedin", "educativo")
    tono_ig = params.get("tono_instagram", "inspiracional")
    tono_fb = params.get("tono_facebook", "personal")
    obj_li = params.get("objetivo_linkedin", "engagement")
    obj_ig = params.get("objetivo_instagram", "engagement")
    obj_fb = params.get("objetivo_facebook", "engagement")
    fmt_ig = params.get("formato_instagram", "imagen-unica")
    nets = active_networks(params)
    do_li = "linkedin" in nets
    do_ig = "instagram" in nets
    do_fb = "facebook" in nets
    source_type = params.get("source_type", "youtube")
    has_url = bool((clean_url or "").strip())

    # How many info slides the carousel needs (slide 0 = hook, last = credits).
    # Only an Instagram carousel needs info slides; everything else needs 0.
    if do_ig and fmt_ig == "carrusel":
        n_slides = max(3, min(6, int(params.get("carrusel_slides", 3) or 3)))
        n_info_slides = n_slides - 2
    else:
        n_info_slides = 0

    transcript_snippet = (content.get("transcript") or "")[:6000]
    tags = content.get("tags", [])
    chapters = content.get("chapters", [])
    channel = content.get("channel", "")
    title = content.get("title", "")
    description = (content.get("description") or "")[:500]

    platforms = []
    if do_li:
        platforms.append(f"LinkedIn — tone: {tono_li}, objective: {obj_li}")
    if do_ig:
        platforms.append(f"Instagram — tone: {tono_ig}, objective: {obj_ig}, format: {fmt_ig}")
    if do_fb:
        platforms.append(f"Facebook — tone: {tono_fb}, objective: {obj_fb}")

    # The content can come from a YouTube video, a voice note transcription, or a
    # text document. Tell the model what it is reading and whether a source URL
    # exists (only YouTube has one — the LinkedIn CTA line depends on it).
    source_label = {
        "audio": "a voice-note audio transcription (e.g. WhatsApp)",
        "texto": "a text document provided by the user",
    }.get(source_type, "a YouTube video")
    url_line = f"Source URL (for LinkedIn): {clean_url}" if has_url else "Source URL: NONE (do not include any URL or 'watch the video' CTA)"
    li_url_reminder = (
        "- LinkedIn: include the raw source URL with the CTA prefix, 3-5 hashtags"
        if has_url
        else "- LinkedIn: there is NO source URL — do NOT add a URL line or a 'watch the video' CTA; just the hook, insights, engagement question and 3-5 hashtags"
    )

    return f"""Write posts for these platforms:
{chr(10).join(f'- {p}' for p in platforms)}

CONTENT SOURCE: {source_label}.
Language to write in: {lang}
INFO SLIDES NEEDED: {n_info_slides}  (output EXACTLY this many strings in image_text.slides — 0 means an empty array)
{url_line}
Channel: {channel}

TITLE: {title}

DESCRIPTION (first 500 chars): {description}

TAGS: {tags}

CHAPTERS: {chapters}

TRANSCRIPT / TEXT (first 6000 chars):
{transcript_snippet}

{"[Note: transcript is empty — use title + description only]" if not transcript_snippet.strip() else ""}

Important reminders:
- Apply the full humanization checklist before outputting
- Verify every specific claim against the transcript above
- Instagram: max 5 hashtags, no raw URL in caption
{li_url_reminder}
- image_text.slides must have EXACTLY {n_info_slides} item(s); image_text.hook is always required (a short complete cover phrase)
- Only write text for the platforms listed above; set every other platform's text to an empty string
{_off_networks_reminder(do_li, do_ig, do_fb)}
"""


def _off_networks_reminder(do_li: bool, do_ig: bool, do_fb: bool) -> str:
    """Recordatorio explícito de qué *_text dejar vacíos según las redes desactivadas."""
    off = []
    if not do_li:
        off.append("linkedin_text")
    if not do_ig:
        off.append("instagram_text")
    if not do_fb:
        off.append("facebook_text")
    if not off:
        return ""
    return f"- Set these to empty strings (those networks are disabled): {', '.join(off)}"


def _fix_control_chars(s: str) -> str:
    """Escape literal control characters inside JSON string values."""
    out = []
    in_string = False
    skip_next = False
    for ch in s:
        if skip_next:
            out.append(ch)
            skip_next = False
        elif ch == '\\' and in_string:
            out.append(ch)
            skip_next = True
        elif ch == '"':
            out.append(ch)
            in_string = not in_string
        elif in_string and ord(ch) < 0x20:
            if ch == '\n':
                out.append('\\n')
            elif ch == '\r':
                out.append('\\r')
            elif ch == '\t':
                out.append('\\t')
            else:
                out.append(f'\\u{ord(ch):04x}')
        else:
            out.append(ch)
    return ''.join(out)


def _extract_texts_fallback(raw: str) -> dict:
    """Last-resort extraction for malformed JSON.

    Handles unescaped double quotes inside string values by using lookahead
    to distinguish value-terminating quotes from embedded ones.
    """
    result: dict = {"linkedin_text": "", "instagram_text": "", "facebook_text": ""}
    for key in ("linkedin_text", "instagram_text", "facebook_text"):
        m = re.search(rf'"{key}"\s*:\s*"', raw)
        if not m:
            continue
        chars: list[str] = []
        i = m.end()
        while i < len(raw):
            c = raw[i]
            if c == "\\" and i + 1 < len(raw):
                chars.append(c)
                chars.append(raw[i + 1])
                i += 2
            elif c == '"':
                # Closing quote if the next non-space char is , } or end-of-string
                lookahead = raw[i + 1 : i + 10].lstrip()
                if not lookahead or lookahead[0] in (",", "}"):
                    break
                # Embedded unescaped quote — escape it
                chars.append('\\"')
                i += 1
            else:
                chars.append(c)
                i += 1
        raw_val = "".join(chars)
        try:
            result[key] = json.loads('"' + raw_val + '"')
        except Exception:
            result[key] = raw_val.replace('\\"', '"')
    return result


def _normalize_image_text(value) -> dict | None:
    """Coerce a parsed `image_text` into {"hook": str, "slides": [str, ...]}.

    Returns None when the value is unusable (missing/wrong shape) so the caller
    can fall back to the heuristic overlay copy. Tolerates a bare string hook and
    non-string slide entries.
    """
    if not isinstance(value, dict):
        return None
    hook = value.get("hook", "")
    if not isinstance(hook, str):
        hook = str(hook) if hook is not None else ""
    hook = hook.strip()
    raw_slides = value.get("slides", [])
    if isinstance(raw_slides, str):
        raw_slides = [raw_slides]
    if not isinstance(raw_slides, (list, tuple)):
        raw_slides = []
    slides = [str(s).strip() for s in raw_slides if str(s).strip()]
    if not hook and not slides:
        return None
    return {"hook": hook, "slides": slides}


def _parse_raw(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_fix_control_chars(raw))
        except json.JSONDecodeError:
            parsed = None
    if isinstance(parsed, dict):
        # Normalize image_text in place (None when absent/unusable -> heuristic fallback downstream).
        img = _normalize_image_text(parsed.get("image_text"))
        if img is not None:
            parsed["image_text"] = img
        else:
            parsed.pop("image_text", None)
        return parsed
    # Level-3 fallback recovers only the caption texts; image_text is left out
    # on purpose so job_runner degrades the overlay copy to its heuristics.
    result = _extract_texts_fallback(raw)
    if result.get("linkedin_text") or result.get("instagram_text") or result.get("facebook_text"):
        return result
    raise json.JSONDecodeError("Could not parse or repair response JSON", raw, 0)


def _anthropic_usage(usage) -> dict | None:
    """Normaliza el `usage` del mensaje final de Claude al shape de `usage_events`.

    Devuelve `{service, model, units}` (tokens de entrada/salida + caché) o `None`
    si no hubo usage. Best-effort: nunca lanza (el tracking no debe romper la escritura).
    """
    if usage is None:
        return None
    def _g(name: str) -> int:
        try:
            return int(getattr(usage, name, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return {
        "service": "anthropic",
        "model": "claude-sonnet-4-6",
        "units": {
            "input_tokens": _g("input_tokens"),
            "output_tokens": _g("output_tokens"),
            "cache_creation_input_tokens": _g("cache_creation_input_tokens"),
            "cache_read_input_tokens": _g("cache_read_input_tokens"),
        },
    }


async def _write_with_anthropic(content: dict, params: dict, clean_url: str, queue: asyncio.Queue, api_key: str) -> tuple[dict, dict | None]:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    loop = asyncio.get_event_loop()

    def _stream():
        chunks = []
        usage = None
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=[{
                "type": "text",
                "text": _system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": _user_message(content, params, clean_url)}],
        ) as stream:
            for chunk in stream.text_stream:
                chunks.append(chunk)
                loop.call_soon_threadsafe(
                    lambda c=chunk: asyncio.ensure_future(
                        queue.put({"step": "writing", "status": "chunk", "text": c})
                    )
                )
            # El usage real (con tokens de caché) vive en el mensaje final del stream.
            try:
                usage = stream.get_final_message().usage
            except Exception:
                usage = None
        return "".join(chunks), usage

    raw, usage = await loop.run_in_executor(None, _stream)
    return _parse_raw(raw), _anthropic_usage(usage)


def _perplexity_usage(usage, model: str) -> dict | None:
    """Normaliza el `usage` del último evento SSE de Perplexity al shape de `usage_events`.

    Mapea prompt/completion → input/output tokens, asume 1 request por llamada y
    captura las búsquedas (`num_search_queries`) si la API las reporta. `None` si no
    hubo usage. Best-effort: nunca lanza.
    """
    if not isinstance(usage, dict):
        return None
    def _g(*names: str) -> int:
        for n in names:
            v = usage.get(n)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    pass
        return 0
    return {
        "service": "perplexity",
        "model": model,
        "units": {
            "input_tokens": _g("prompt_tokens", "input_tokens"),
            "output_tokens": _g("completion_tokens", "output_tokens"),
            "requests": 1,
            "searches": _g("num_search_queries", "search_queries"),
        },
    }


async def _write_with_perplexity(content: dict, params: dict, clean_url: str, queue: asyncio.Queue, api_key: str) -> tuple[dict, dict | None]:
    """Perplexity exposes an OpenAI-compatible streaming chat endpoint. We hit it
    with urllib (no SDK) and parse the SSE `data: {...}` lines ourselves."""
    loop = asyncio.get_event_loop()

    model = params.get("modelo_perplexity") or PERPLEXITY_MODEL
    if model not in PERPLEXITY_MODELS:
        model = PERPLEXITY_MODEL

    def _stream():
        body = json.dumps({
            "model": model,
            "max_tokens": 4096,
            "stream": True,
            # Keep the model grounded in the transcript instead of leaning on web search.
            "web_search_options": {"search_context_size": "low"},
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "system", "content": _PERPLEXITY_GROUNDING},
                {"role": "user", "content": _user_message(content, params, clean_url)},
            ],
        }).encode()
        req = urllib.request.Request(
            PERPLEXITY_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        chunks = []
        usage = None
        try:
            with urllib.request.urlopen(req) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # El usage llega en el(los) último(s) evento(s); nos quedamos con el más reciente.
                    if obj.get("usage"):
                        usage = obj["usage"]
                    delta = (obj.get("choices") or [{}])[0].get("delta", {})
                    text = delta.get("content") or ""
                    if text:
                        chunks.append(text)
                        loop.call_soon_threadsafe(
                            lambda t=text: asyncio.ensure_future(
                                queue.put({"step": "writing", "status": "chunk", "text": t})
                            )
                        )
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Perplexity API error {e.code}: {e.read().decode()}")
        return "".join(chunks), usage

    raw, usage = await loop.run_in_executor(None, _stream)
    posts = _parse_raw(raw)
    # Safety net: sonar models sometimes append citation markers like [1] despite instructions.
    _strip = lambda s: re.sub(r"\s*\[\d+\]", "", s).strip()
    for key in ("linkedin_text", "instagram_text", "facebook_text"):
        if posts.get(key):
            posts[key] = _strip(posts[key])
    img = posts.get("image_text")
    if isinstance(img, dict):
        if img.get("hook"):
            img["hook"] = _strip(img["hook"])
        img["slides"] = [_strip(s) for s in img.get("slides", [])]
    return posts, _perplexity_usage(usage, model)


async def write_posts(content: dict, params: dict, clean_url: str, queue: asyncio.Queue, cfg) -> tuple[dict, dict | None]:
    """Escribe los posts y devuelve `(posts, usage)`.

    `usage` es `{service, model, units}` para el tracking de costos (o `None` si el
    proveedor no reportó consumo). El núcleo del pipeline lo pasa a `record_event`.
    """
    provider = cfg.llm_provider  # raises if neither key is set
    if provider == "perplexity":
        return await _write_with_perplexity(content, params, clean_url, queue, cfg.perplexity_api_key)
    return await _write_with_anthropic(content, params, clean_url, queue, cfg.anthropic_api_key)
