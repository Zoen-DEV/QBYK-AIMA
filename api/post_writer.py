import asyncio
import json
import math
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
{"linkedin_text": "...", "instagram_text": "...", "facebook_text": "...", "image_text": {"hook": "...", "slides": ["...", "..."]}, "video_prompt": "...", "video_style": "...", "video_storyboard": ["...", "..."], "video_voiceover": ["...", "..."]}
Only write text for the platforms requested in the user message; set any non-requested platform's text to an empty string.
Every string value is PLAIN TEXT for social feeds: never use markdown inside them (**bold**, _italics_, # headers, [links](url)) — the platforms render the characters literally.

=== IMAGE TEXT (overlay copy for the visuals) ===
The `image_text` object is the text that gets printed ON the images/carousel — it is NOT the caption. Write it as standalone, designed-poster copy:
- `hook`: ONE short, complete, punchy phrase for the cover image (max ~10 words). It must read as a finished statement, not a truncated sentence. No hashtags, no emojis, no URL, no trailing "…". Capitalize naturally (sentence case, not ALL CAPS).
- `slides`: an array of short idea-statements, ONE per info slide. The exact number of slides required is given in the user message ("INFO SLIDES NEEDED: N") — output EXACTLY that many strings. Each string is a single self-contained idea (max ~14 words), the kind of line that fills a whole slide on its own. Do NOT split one idea across slides, do NOT number them, no bullets, no emojis, no hashtags. Each must be drawn faithfully from the transcript/title (same no-fabrication rule as the posts).
- If only one platform is requested or Instagram is a single image, still provide `hook`; `slides` may be an empty array when no carousel is needed.
Write `image_text` in the same language as the posts.

=== VIDEO PROMPT, STYLE & STORYBOARD (text-to-video generation) ===
`video_prompt`, `video_style` and `video_storyboard` feed an AI text-to-video model — the audience never reads them. Provide them (and `video_voiceover`) ONLY when the user message says "VIDEO PROMPT NEEDED: yes"; otherwise set `video_prompt` and `video_style` to "", and `video_storyboard` and `video_voiceover` to [].
Write the prompt, style and storyboard in ENGLISH regardless of the posts' language (video models follow English best); `video_voiceover` is the one exception — the audience HEARS it, so it goes in the posts' language.

GROUNDING (this is what makes the clip specific instead of generic — the #1 rule here):
- First, find the SINGLE most visually concrete thing in the transcript: a named object, place, tool, action, example, anecdote, or number the speaker actually mentions. Build the scene from THAT specific detail.
- NEVER output a generic scene. Banned: "modern office", "person at a laptop", "business people in a meeting", "abstract editorial background", "data/network flowing", glowing dashboards, and any stock-footage cliché. If the content is abstract, choose ONE concrete physical metaphor object (compound interest → a single coin dropping onto a growing stack of coins; focus → one lit desk lamp in an otherwise dark room; growth → a seedling breaking through soil).
- The scene is purely visual: no people talking to camera, no dialogue, and NO on-screen text, captions, logos, or watermarks of any kind.
- The no-fabrication rule does not apply to the scene itself (it is a description, not a claim), but the scene MUST clearly evoke THIS specific content — a viewer who knows the video should recognize why this scene was chosen.

CINEMATOGRAPHY (write each shot like a director, not a keyword list):
- Every shot description states, in natural prose: the subject and its ONE action; the framing (extreme close-up / close-up / medium / wide); ONE camera move (slow push-in, lateral glide, slow orbit, tilt-up reveal, rack focus); and the light with its source and quality ("warm late-afternoon window light", "a single desk lamp in a dark room", "soft overcast daylight").
- One continuous take per shot: no cuts, no "then...", exactly one camera move, motion a real camera operator could physically execute.
- Compose for a vertical 9:16 phone screen: subject in the upper two thirds, some foreground depth, and keep the lower third of the frame visually calm — burned-in captions will sit there.

`video_style`: the look-lock that makes separately-generated segments cut together as one film — 10–18 words describing ONLY the look: lens/film character, lighting character, color palette, mood (e.g. "shot on 35mm, shallow depth of field, warm amber practicals against cool dusk blue, quiet confident mood"). No subjects, no actions, no scenery. It is appended verbatim to every shot, so never restate the look inside the beats.

`video_prompt`: 40–80 words describing ONE continuous, filmable scene — the strongest single beat of the content, obeying the grounding and cinematography rules above.

`video_storyboard`: an array of SHOT beats for a longer clip stitched from several segments. The user message says "VIDEO SEGMENTS NEEDED: N" — output EXACTLY N beats (if N is 0, output an empty array). Each beat is ONE continuous shot (25–50 words) obeying the grounding and cinematography rules. The N beats must read as ONE continuous visual story, never N disconnected clips:
- Keep a recurring anchor across all beats — the same protagonist object, character or location evolving shot to shot, so the viewer feels "same world, next moment".
- Chain the beats: open each beat on something the previous one left off (the object it ended on, the direction the camera was moving, the space it was entering) and name that carried-over element explicitly at the start of the beat.
- Vary the framing between consecutive beats (wide → close-up → medium…) so each cut feels intentional; never vary the look — that lives in `video_style`.
- Arc: beat 1 opens mid-action on the boldest image (it must grab in the first second), middle beats develop or escalate, and the final beat resolves on a payoff image that can hold a closing thought.
Beat 1 should match the spirit of `video_prompt`. Draw each beat from a different concrete moment/example in the transcript when possible, not the same image repeated.

`video_voiceover`: the narration spoken OVER the clip by a TTS voice — an array of EXACTLY N lines (same N as `video_storyboard`; empty array when N is 0). Unlike the prompts above, write it in the SAME LANGUAGE as the posts: line i is read aloud while shot i plays.
- COUNT IS STRUCTURAL, NOT STYLISTIC: `video_voiceover` and `video_storyboard` must have the SAME number of items, one spoken line per shot. If the source material feels thin for N lines, split the narration into N shorter beats — never return fewer lines than shots.
- SOUND HUMAN (the narration IS the reel — it must sound like a person, not a script): write like a creator talking to the camera — everyday spoken words, natural contractions, direct address to the viewer, and rhythm (mix a short punchy sentence with a longer one). Read each line aloud in your head: if it sounds like an essay or a news anchor, rewrite it. The humanization checklist applies here too (no AI filler connectors, no inflated vocabulary).
- WORD BUDGET: the user message gives "VOICEOVER WORDS PER LINE: MIN-MAX". EVERY line must land inside that range — each line fills a fixed audio window, so a short line leaves seconds of dead air mid-reel (kills the flow) and an overlong one gets sped up.
- FLOW BETWEEN LINES: line 1 is the spoken hook — open with the tension, claim or question that earns the next twenty seconds (never a greeting, never "en este video…"/"in this video…"). From line 2 on, pick up the previous line's idea with a natural spoken connector (ES: "Y eso significa que…", "Pero acá viene lo bueno:", "¿El resultado?" — EN: "And that means…", "But here's the good part:", "The result?") — vary them, never open two lines the same way, never restart cold as if the previous line didn't exist. The last line closes the idea and may end with ONE short natural call to action (watch the full video / follow) only if it fits the budget.
- The lines are narration, NOT captions: never mention or describe what is on screen. Plain speakable prose only: no hashtags, no emojis, no URLs, no quotes, no "shot 1:" prefixes; write short numbers as words. The FAITHFUL CITATIONS rule fully APPLIES here (these lines make claims the audience hears): every fact must come from the transcript.

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
- Strong opening hook (1 sentence) — plain text, no asterisks/markdown bold
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


def _segments_needed(params: dict) -> int:
    """Cuántos shots pedirle al storyboard: ceil(duración objetivo / seg por shot).

    `duracion_video` es la duración total buscada (segundos); `video_segment_seconds`
    es lo que dura cada segmento generado (lo fija job_runner desde la config). Sin
    duración objetivo → 1 (un solo clip, comportamiento previo).
    """
    def _int(key: str, default: int) -> int:
        try:
            v = int(params.get(key) or 0)
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default
    target = _int("duracion_video", 0)
    seg = _int("video_segment_seconds", 5)
    if target <= 0:
        return 1
    return max(1, math.ceil(target / seg))


def _wants_video(params: dict) -> bool:
    """¿El pipeline va a generar un video text-to-video que necesite guion del LLM?

    Misma condición que la rama de video de `job_runner`, calculada solo con
    `params` para que ambos flujos (individual y bulk) la compartan. En "subir" el
    medio ya existe; en "fotos" los frames son las fotos subidas (transición por
    plantilla fija, sin guion).
    """
    if params.get("media_origin", "generar") in ("subir", "fotos"):
        return False
    tipo_post = params.get("tipo_post", "post")
    return (
        params.get("tipo_medio", "imagen") == "video"
        or tipo_post == "reel"
        or (tipo_post == "historia" and params.get("historia_formato", "imagen") == "video")
    )


def _voiceover_word_budget(params: dict) -> tuple[int, int]:
    """Rango (mín, máx) de palabras por línea de voz: lo que LLENA el bloque.

    Cada línea suena dentro de una ventana FIJA de `video_segment_seconds`:
    explainer_video centra la voz corta (deja aire muerto a los costados) y
    acelera la larga sin cambiar el pitch. A ~2.2–2.8 palabras/segundo de habla
    natural (es/en), el rango apunta a llenar la ventana: quedarse corto abre
    silencios a mitad del reel (mata la fluidez); pasarse un poco solo acelera
    levemente la voz. Piso de 8 para que hasta el segmento más corto admita una
    frase completa.
    """
    try:
        seg = int(params.get("video_segment_seconds") or 5)
    except (TypeError, ValueError):
        seg = 5
    seg = seg if seg > 0 else 5
    lo = max(8, round(seg * 2.2))
    hi = max(lo + 3, round(seg * 2.8))
    return lo, hi


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
    do_tk = "tiktok" in nets
    # TikTok reutiliza el caption de reel de Instagram: si TikTok está activo, pedimos
    # igual el instagram_text (aunque Instagram no sea destino) para no dejar el post
    # de TikTok sin texto. La publicación real a IG sigue gobernada por do_ig.
    do_ig_text = do_ig or do_tk
    source_type = params.get("source_type", "youtube")
    has_url = bool((clean_url or "").strip())

    # How many info slides the carousel needs (slide 0 = hook, last = credits).
    # El carrusel es multi-red (IG nativo, LinkedIn document carousel, FB multi-foto),
    # así que los slides se piden siempre que el formato sea carrusel.
    if fmt_ig == "carrusel":
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

    # Reel/historia: el caption es para un Reel/Story (IG y FB), no un feed post.
    tipo_post = params.get("tipo_post", "post")
    special_fmt = {"reel": "reel", "historia": "story"}.get(tipo_post)

    wants_video = _wants_video(params)
    # Cuántos segmentos (shots) necesita el storyboard para llegar a la duración objetivo.
    n_video_segments = _segments_needed(params) if wants_video else 0
    # Rango de palabras por línea de voz en off: cada línea se lee sobre UNA
    # ventana fija de `video_segment_seconds` — el rango la llena sin dejar aire.
    vo_lo, vo_hi = _voiceover_word_budget(params)

    platforms = []
    if do_li:
        platforms.append(f"LinkedIn — tone: {tono_li}, objective: {obj_li}")
    if do_ig_text:
        platforms.append(f"Instagram — tone: {tono_ig}, objective: {obj_ig}, format: {special_fmt or fmt_ig}")
    if do_fb:
        fb_line = f"Facebook — tone: {tono_fb}, objective: {obj_fb}"
        if special_fmt:
            fb_line += f", format: {special_fmt}"
        platforms.append(fb_line)

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
VIDEO PROMPT NEEDED: {"yes" if wants_video else "no"}
VIDEO SEGMENTS NEEDED: {n_video_segments}  (output EXACTLY this many shot beats in video_storyboard — 0 means an empty array)
VOICEOVER WORDS PER LINE: {vo_lo}-{vo_hi}  (video_voiceover: exactly {n_video_segments} spoken line(s) in the posts' language, EVERY line within this range — 0 means an empty array)
{url_line}
Channel: {channel}

TITLE: {title}

DESCRIPTION (first 500 chars): {description}

TAGS: {tags}

CHAPTERS: {chapters}

TRANSCRIPT / TEXT (first 6000 chars):
{transcript_snippet}

{"[Note: transcript is empty — use title + description only]" if not transcript_snippet.strip() else ""}
{_manual_context_block(params, wants_video)}
Important reminders:
- Apply the full humanization checklist before outputting
- Verify every specific claim against the transcript above
- Instagram: max 5 hashtags, no raw URL in caption
{li_url_reminder}
- image_text.slides must have EXACTLY {n_info_slides} item(s); image_text.hook is always required (a short complete cover phrase)
- video_prompt / video_style / video_storyboard / video_voiceover: {f"write the English scene(s) tied to this content — video_prompt (single strongest beat), video_style (the shared look-lock appended to every shot) AND exactly {n_video_segments} beat(s) in video_storyboard (see VIDEO PROMPT, STYLE & STORYBOARD rules); ground every beat in a concrete detail from the transcript, never a generic stock scene, and chain the beats as one continuous visual story. video_voiceover: exactly {n_video_segments} spoken line(s) in {lang}, {vo_lo}-{vo_hi} words each, flowing as one spoken narration (hook → build → payoff) with natural connectors between lines; every fact from the transcript" if wants_video else "set video_prompt and video_style to empty strings, and video_storyboard and video_voiceover to empty arrays (no video will be generated)"}
- Only write text for the platforms listed above; set every other platform's text to an empty string
{_off_networks_reminder(do_li, do_ig_text, do_fb)}
"""


def _manual_context_block(params: dict, wants_video: bool) -> str:
    """Contextos separados para el video y la voz (modo manual de reel).

    Permiten dirigir el video y la locución con material DISTINTO al del copy (el
    transcript = manual_text). Solo se inyectan si el usuario los llenó y el job
    genera video. Son material de origen provisto por el usuario (data, no
    instrucciones): aplican como fuente para esas piezas y priman sobre el copy.
    """
    if not wants_video:
        return ""
    cv = (params.get("contexto_video") or "").strip()
    cz = (params.get("contexto_voz") or "").strip()
    lines = []
    if cv:
        lines.append(
            "VIDEO CONTEXT (source material for the visuals — base video_prompt, "
            "video_style and video_storyboard on THIS, not the copy text above; "
            "however short it is, still output the exact number of shots requested "
            f"above): {cv[:1500]}"
        )
    if cz:
        lines.append(
            "VOICEOVER CONTEXT (source material for the narration — base "
            "video_voiceover on THIS, not the copy text above; however short it is, "
            "still output the exact number of spoken lines requested above, splitting "
            f"this material across them): {cz[:1500]}"
        )
    return ("\n\n" + "\n\n".join(lines)) if lines else ""


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


# Red de seguridad POST-parseo, compartida por ambos proveedores: los modelos a
# veces meten artefactos que las redes muestran literales — marcadores de cita
# tipo [1] (sonar con web search) y negrita markdown **así** (ningún feed la
# renderiza; se desenvuelve conservando el texto). La humanización de fondo vive
# en el system prompt (checklist), también compartido; esto solo limpia formato.
_CITATION_RE = re.compile(r"\s*\[\d+\]")
_BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)


def _sanitize_text(s: str) -> str:
    s = _CITATION_RE.sub("", s)
    s = _BOLD_MD_RE.sub(r"\1", s)
    return s.strip()


def _sanitize_posts(posts: dict) -> dict:
    for key in ("linkedin_text", "instagram_text", "facebook_text"):
        if posts.get(key):
            posts[key] = _sanitize_text(posts[key])
    img = posts.get("image_text")
    if isinstance(img, dict):
        if img.get("hook"):
            img["hook"] = _sanitize_text(img["hook"])
        img["slides"] = [_sanitize_text(s) for s in img.get("slides", [])]
    for key in ("video_prompt", "video_style"):
        if posts.get(key):
            posts[key] = _sanitize_text(posts[key])
    for key in ("video_storyboard", "video_voiceover"):
        if isinstance(posts.get(key), list):
            posts[key] = [_sanitize_text(s) for s in posts[key] if _sanitize_text(s)]
    return posts


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
        # video_prompt / video_style: string no vacío o nada (job_runner cae al
        # prompt genérico / al estilo por defecto).
        for key in ("video_prompt", "video_style"):
            v = parsed.get(key)
            if isinstance(v, str) and v.strip():
                parsed[key] = v.strip()
            else:
                parsed.pop(key, None)
        # video_storyboard: lista de shots (strings no vacíos) o nada (job_runner
        # cae al video_prompt único → 1 segmento). video_voiceover: misma forma
        # (una línea hablada por shot) o nada (el reel sale mudo, como antes).
        for key in ("video_storyboard", "video_voiceover"):
            val = parsed.get(key)
            if isinstance(val, str):
                val = [val]
            if isinstance(val, (list, tuple)):
                val = [str(x).strip() for x in val if str(x).strip()]
            else:
                val = []
            if val:
                parsed[key] = val
            else:
                parsed.pop(key, None)
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
    return _sanitize_posts(_parse_raw(raw)), _anthropic_usage(usage)


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
    return _sanitize_posts(_parse_raw(raw)), _perplexity_usage(usage, model)


# ── Guion del video: una línea de voz por shot ────────────────────────────────
# El pipeline arma la voz con `explainer_video`: un bloque por shot, cada uno con
# su línea hablada. Si los conteos no calzan el reel sale MUDO, así que la salida
# del LLM se reconcilia acá — el aviso del preview queda solo para las ediciones
# del usuario, no para lo que genera la app.

_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")


def _split_unit(unit: str) -> list[str]:
    """Parte una frase en dos por el corte natural más cercano a la mitad."""
    words = unit.split()
    if len(words) < 4:
        return []
    mid = len(words) // 2
    breaks = [i for i, w in enumerate(words[:-1]) if w.endswith((",", ";", ":"))]
    cut = min(breaks, key=lambda i: abs(i + 1 - mid), default=mid - 1) + 1
    return [" ".join(words[:cut]), " ".join(words[cut:])]


def _script_units(lines: list[str], n: int) -> list[str]:
    """Descompone el guion en unidades (frases) suficientes para n bloques."""
    units = [u.strip() for line in lines for u in _SENTENCE_END_RE.split(line) if u.strip()]
    # Con menos frases que shots hay que partir alguna: dividir una frase larga es
    # mejor que quedarse sin voz. El guard corta si ya no queda nada divisible.
    for _ in range(n * 4):
        if len(units) >= n:
            break
        idx = max(range(len(units)), key=lambda i: len(units[i].split()))
        parts = _split_unit(units[idx])
        if not parts:
            break
        units[idx:idx + 1] = parts
    return units


def _rebalance_voiceover(lines: list[str], n: int) -> list[str]:
    """Reparte el MISMO guion en exactamente n líneas de peso parecido.

    No inventa ni descarta texto (regla #3: no fabricar): solo redistribuye las
    frases entre los n bloques. Devuelve `[]` si el guion no da para n bloques.
    """
    units = _script_units(lines, n)
    if len(units) < n:
        return []
    weights = [max(1, len(u.split())) for u in units]
    total = sum(weights)
    out: list[str] = []
    current: list[str] = []
    consumed = 0
    for i, unit in enumerate(units):
        current.append(unit)
        consumed += weights[i]
        left_units, left_groups = len(units) - i - 1, n - len(out) - 1
        # Se cierra el bloque al llegar a su cuota de palabras, o antes si quedan
        # justo las unidades necesarias para los bloques que faltan.
        if left_groups > 0 and left_units >= left_groups and (
            consumed >= total * (len(out) + 1) / n or left_units == left_groups
        ):
            out.append(" ".join(current))
            current = []
    out.append(" ".join(current))
    return out


def _align_video_script(posts: dict, params: dict) -> dict:
    """Deja `video_storyboard` y `video_voiceover` con el mismo largo (y el pedido).

    Dos desalineaciones típicas del LLM (sobre todo con contexto manual corto):
    más/menos shots que los que pide `duracion_video`, y menos líneas de voz que
    shots. La primera se corrige recortando el storyboard; la segunda repartiendo
    el guion entre los shots que hay, para no perder la voz ni acortar el video.
    """
    if not _wants_video(params):
        return posts
    storyboard = posts.get("video_storyboard")
    if isinstance(storyboard, list) and len(storyboard) > _segments_needed(params):
        storyboard = storyboard[:_segments_needed(params)]
        posts["video_storyboard"] = storyboard
    voiceover = posts.get("video_voiceover")
    if not (isinstance(storyboard, list) and storyboard and isinstance(voiceover, list) and voiceover):
        return posts
    if len(voiceover) != len(storyboard):
        aligned = _rebalance_voiceover(voiceover, len(storyboard))
        if aligned:
            posts["video_voiceover"] = aligned
        else:
            # Guion demasiado corto para repartir: se recortan los shots sobrantes.
            # Un reel más corto CON voz y subtítulos supera a uno completo mudo.
            posts["video_storyboard"] = storyboard[:len(voiceover)]
    return posts


async def write_posts(content: dict, params: dict, clean_url: str, queue: asyncio.Queue, cfg) -> tuple[dict, dict | None]:
    """Escribe los posts y devuelve `(posts, usage)`.

    `usage` es `{service, model, units}` para el tracking de costos (o `None` si el
    proveedor no reportó consumo). El núcleo del pipeline lo pasa a `record_event`.
    """
    provider = cfg.llm_provider  # raises if neither key is set
    if provider == "perplexity":
        posts, usage = await _write_with_perplexity(content, params, clean_url, queue, cfg.perplexity_api_key)
    else:
        posts, usage = await _write_with_anthropic(content, params, clean_url, queue, cfg.anthropic_api_key)
    return _align_video_script(posts, params), usage
