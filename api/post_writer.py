import asyncio
import json


def _system_prompt() -> str:
    return """You are an expert AI social media manager. Your task is to write optimized posts for LinkedIn and Instagram based on YouTube video content.

OUTPUT FORMAT: Respond with ONLY valid JSON in this exact shape — no markdown, no explanation, no preamble:
{"linkedin_text": "...", "instagram_text": "..."}

=== LINKEDIN POST RULES ===
- 150–300 words
- Strong hook in the first line — NEVER start with "En este video..." / "In this video..." / "Descubre cómo..." / "Discover how..."
- 3–5 key insights or takeaways with → or bullet formatting
- Conversational but authoritative tone
- Always include the YouTube URL on its own line just before the hashtags, with this exact CTA:
  Spanish: "▶ Mira el video completo aquí: <url>"
  English: "▶ Watch the full video here: <url>"
  Do NOT wrap the URL in markdown — paste it raw.
- End with a question to spark engagement (goes after the URL line, before or among the hashtags)
- 3–5 relevant hashtags at the very end

=== INSTAGRAM POST RULES ===
- 80–150 words
- Bold opening hook (1 sentence)
- Short punchy sentences or bullets
- 3–6 emojis woven in naturally (not stacked at the end or beginning)
- Clear call-to-action: "Link en bio" / "Link in bio" — do NOT paste the raw YouTube URL in captions
- MAXIMUM 5 hashtags (hard limit — the platform rejects more)

=== FAITHFUL CITATIONS (STRICT) ===
Any verbatim quote, number, percentage, name, or specific claim in either post MUST appear literally in the transcript (or title+description if transcript is empty). Never invent figures, attributions, or quotes. Paraphrasing is fine, fabricating is not.

=== SMART HASHTAGS ===
Build the hashtag pool primarily from the video's own `tags` and `chapters`:
1. Pick 2-3 from `tags` that fit the platform's audience (skip generic ones like #video)
2. Add 1-2 derived from `chapters` titles
3. Only invent extra if still under minimum — keep them concrete and topic-specific
4. Cap: LinkedIn 3-5, Instagram max 5

=== HUMANIZATION CHECKLIST (apply before outputting) ===
Apply these rules to both posts silently:
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
    obj_li = params.get("objetivo_linkedin", "engagement")
    obj_ig = params.get("objetivo_instagram", "engagement")
    fmt_ig = params.get("formato_instagram", "imagen-unica")
    solo = params.get("solo", "")

    transcript_snippet = (content.get("transcript") or "")[:6000]
    tags = content.get("tags", [])
    chapters = content.get("chapters", [])
    channel = content.get("channel", "")
    title = content.get("title", "")
    description = (content.get("description") or "")[:500]

    platforms = []
    if solo != "instagram":
        platforms.append(f"LinkedIn — tone: {tono_li}, objective: {obj_li}")
    if solo != "linkedin":
        platforms.append(f"Instagram — tone: {tono_ig}, objective: {obj_ig}, format: {fmt_ig}")

    return f"""Write posts for these platforms:
{chr(10).join(f'- {p}' for p in platforms)}

Language to write in: {lang}
YouTube URL (for LinkedIn): {clean_url}
Channel: {channel}

VIDEO TITLE: {title}

DESCRIPTION (first 500 chars): {description}

TAGS: {tags}

CHAPTERS: {chapters}

TRANSCRIPT (first 6000 chars):
{transcript_snippet}

{"[Note: transcript is empty — use title + description only]" if not transcript_snippet.strip() else ""}

Important reminders:
- Apply the full humanization checklist before outputting
- Verify every specific claim against the transcript above
- Instagram: max 5 hashtags, no raw YouTube URL in caption
- LinkedIn: include the raw YouTube URL with the CTA prefix, 3-5 hashtags
{"- Only write linkedin_text (set instagram_text to empty string)" if solo == "linkedin" else ""}
{"- Only write instagram_text (set linkedin_text to empty string)" if solo == "instagram" else ""}
"""


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


def _parse_raw(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_fix_control_chars(raw))


async def _write_with_anthropic(content: dict, params: dict, clean_url: str, queue: asyncio.Queue, api_key: str) -> dict:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    loop = asyncio.get_event_loop()

    def _stream():
        chunks = []
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=2048,
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
            return "".join(chunks)

    raw = await loop.run_in_executor(None, _stream)
    return _parse_raw(raw)


async def _write_with_groq(content: dict, params: dict, clean_url: str, queue: asyncio.Queue, api_key: str) -> dict:
    from groq import Groq
    client = Groq(api_key=api_key)
    loop = asyncio.get_event_loop()

    def _stream():
        chunks = []
        stream = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_message(content, params, clean_url)},
            ],
            stream=True,
        )
        for chunk in stream:
            text = chunk.choices[0].delta.content or ""
            if text:
                chunks.append(text)
                loop.call_soon_threadsafe(
                    lambda t=text: asyncio.ensure_future(
                        queue.put({"step": "writing", "status": "chunk", "text": t})
                    )
                )
        return "".join(chunks)

    raw = await loop.run_in_executor(None, _stream)
    return _parse_raw(raw)


async def write_posts(content: dict, params: dict, clean_url: str, queue: asyncio.Queue, cfg) -> dict:
    provider = cfg.llm_provider  # raises if neither key is set
    if provider == "groq":
        return await _write_with_groq(content, params, clean_url, queue, cfg.groq_api_key)
    return await _write_with_anthropic(content, params, clean_url, queue, cfg.anthropic_api_key)
