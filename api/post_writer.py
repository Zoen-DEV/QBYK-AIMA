import asyncio
import json
import math
import re
import urllib.request
import urllib.error

import prompt_architect as parch
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


def _system_prompt(text_overlay: bool = True) -> str:
    """Prompt del sistema.

    `text_overlay` dice si la app va a IMPRIMIR copy sobre las imágenes. Cambia una
    sola cosa —si la composición debe reservar un área calma para ese texto o llenar
    el cuadro— pero es la diferencia entre una imagen equilibrada y una con la mitad
    inferior vacía a propósito y nada que la llene.
    """
    space_cover = (
        "It is the cover/main visual and the piece is a designed poster: the app locks big type "
        "across the top band and the bottom band, so put the subject in the CENTRAL band and keep "
        "those two bands calm and uncluttered (empty surface, wall, shadow, shallow-focus falloff)."
        if text_overlay else
        "It is the cover/main visual and nothing will be printed over it, so compose the full "
        "frame: fill it edge to edge with intentional balance, and do not leave an empty band or "
        "dead space waiting for text."
    )
    space_slides = (
        # Un slide de contenido invierte la jerarquía de la portada: ahí el titular es el
        # elemento mayor del cuadro y el objeto lo soporta. Hay que decirlo acá además de
        # en el brief de imagen porque quien elige el objeto es este modelo: si escoge una
        # escena que solo funciona llenando el cuadro, el slide sale peleado consigo mismo.
        "- carries poster type too, and there the TYPE LEADS: the headline owns the upper two "
        "thirds and the object sits low and small underneath it, so pick something that still "
        "reads at that size — one clear silhouette, not a scene that needs the whole frame."
        if text_overlay else
        "- carries no text either: compose the full frame, no reserved empty area."
    )
    return f"""You are an expert AI social media manager. Your task is to write optimized posts for LinkedIn, Instagram and Facebook based on YouTube video content.

OUTPUT FORMAT: Respond with ONLY valid JSON in this exact shape — no markdown, no explanation, no preamble:
{{"linkedin_text": "...", "instagram_text": "...", "facebook_text": "...", "image_text": {{"hook": "...", "slides": ["...", "..."]}}, "image_prompt": "...", "image_style": "...", "image_slide_prompts": ["...", "..."], "video_prompt": "...", "video_style": "...", "video_storyboard": ["...", "..."], "video_voiceover": ["...", "..."]}}
Only write text for the platforms requested in the user message; set any non-requested platform's text to an empty string.
Every string value is PLAIN TEXT for social feeds: never use markdown inside them (**bold**, _italics_, # headers, [links](url)) — the platforms render the characters literally.

=== WHICH VISUAL FIELDS THIS JOB NEEDS (read this before writing anything) ===
The user message ends its briefing with a line "REQUIRED VISUAL FIELDS: ..." that names EXACTLY the visual fields this job needs.
- Every field on that list is MANDATORY and must come back with real content. An empty string or an empty array for a listed field is a failed response, even if the source material feels thin — there is always a concrete scene to describe.
- Any visual field NOT on that list simply does not exist for this job: leave it as "" or []. It is not a task, it is not a fallback, and it must NEVER influence what you write for the fields that ARE on the list. Skipping one block is not a reason to skip another.

=== IMAGE TEXT (overlay copy for the visuals) ===
The `image_text` object is the text that gets printed ON the images/carousel — it is NOT the caption. Write it as standalone, designed-poster copy:
- `hook`: ONE short, complete, punchy phrase for the cover image (max ~10 words). It must read as a finished statement, not a truncated sentence. No hashtags, no emojis, no URL, no trailing "…". Capitalize naturally (sentence case, not ALL CAPS).
- `slides`: an array of OBJECTS, ONE per info slide. When the piece is a carousel the user message says "INFO SLIDES NEEDED: N" — output EXACTLY that many; when that line is absent the piece is a single image and `slides` is an empty array. The user message also names a "SLIDE TEXT SYSTEM" and lists the exact keys each object must carry, with a word budget for each: those are the text blocks the app will print on that slide, at different sizes, and a key that comes back empty prints nothing. Never invent a key that is not on that list, never number the slides, no bullets, no emojis, no hashtags. Everything must be drawn faithfully from the transcript/title (same no-fabrication rule as the posts).
- THE CAROUSEL TEACHES THE SOURCE. This is the point of the whole set: someone who never watched the video must finish the last slide KNOWING the thing. So the slides are not N headlines about the topic — the topic is already on the cover. Every `cuerpo` (when the system has one) carries something concrete the source actually says: a number, a name, a mechanism, a step, a consequence, a comparison the speaker made. A slide that would still be true of any other video on the same subject has failed, and so has one that only re-states its own headline in more words.
- THE SLIDES ARE ONE SEQUENCE, NOT N INDEPENDENT SLIDES. The cover states the promise or the tension; every info slide moves it ONE step forward (slide i+1 must say something slide i did not, and reordering them should break the reading); the LAST one lands the payoff — the line the reader would screenshot. That last slide is still an informative idea taken from the source: never a sign-off, never credits, never "sígueme"/"follow me", never a summary of the slides before it.
- EACH SLIDE HAS A NAMED BEAT. When the piece is a carousel the user message lists "SLIDE BEATS": one job per info slide (tension → development → evidence → payoff). Write slide i to do the job of beat i — that beat is not a suggestion, the app is already building slide i's image for it (its shot distance, its type size and whether the accent colour appears there all come from that beat). A slide whose line ignores its beat gets a picture that argues with its own text.
- WHAT EACH BLOCK IS FOR. `titular` is the claim, and it is the only block set at poster size: short, finished, no trailing colon. `cuerpo` is what the source explains — one or two full sentences of real prose, not a caption and not a list. `etiqueta` is a bare marker for scanning ("01", "El coste", "Paso 2"): never a sentence, never punctuation-heavy. `apoyo` is a single qualifying line printed small at the foot.
- WHEN THE SYSTEM HAS NO `cuerpo`, a `titular` that carries a headline AND a qualifier can be written as `Headline — support` with a spaced em dash: the app prints what comes before the dash big and what comes after it small at the foot, and the dash itself is never printed. Keep the headline at 6 words or fewer. When one beat already says it, write a single phrase with NO dash — never force the split. Systems that already have their own second block do not need this and must not use the dash.
- If only one platform is requested or Instagram is a single image, still provide `hook`; `slides` may be an empty array when no carousel is needed.
Write `image_text` in the same language as the posts.

=== VISUAL PROMPTS: SHARED RULES (apply to image_prompt, image_style, image_slide_prompts, video_prompt and video_storyboard) ===
These fields feed AI image/video models — the audience never reads them. Write them ALL in ENGLISH regardless of the posts' language (the models follow English best). `video_voiceover` is the one exception: the audience HEARS it, so it goes in the posts' language.

GROUNDING (this is what makes the visual specific instead of generic — the #1 rule here):
- Before writing any visual prompt, list to yourself the concrete things the source actually contains: named objects, places, tools, materials, actions, examples, anecdotes, numbers the speaker mentions. Build every scene from THOSE specific details, not from the topic label.
- Pick the SINGLE most visually concrete of them for the main visual (`image_prompt` / `video_prompt`). When several visuals are requested, each one takes a DIFFERENT concrete detail from the source — never the same image reworded.
- NEVER output a generic scene. Banned: "modern office", "person at a laptop", "business people in a meeting", "abstract editorial background", "data/network flowing", glowing dashboards, and any stock-footage cliché. If the content is abstract, choose ONE concrete physical metaphor object (compound interest → a single coin dropping onto a growing stack of coins; focus → one lit desk lamp in an otherwise dark room; growth → a seedling breaking through soil).
- The scene is purely visual: no people talking to camera, no dialogue, and NO on-screen text, captions, logos, or watermarks of any kind.
- NOTHING IN THE SCENE IS LABELLED. Never name an object by the words printed on it, and never ask for a readable screen, menu, dial label, book spine, packaging or sign — image models render those as garbled pseudo-text and it is the single clearest giveaway of an AI image. When a screen or panel belongs in the shot, describe what it displays as pure graphics: a curve, a waveform, a bar, tick marks, a plain color field.
- The no-fabrication rule does not apply to the scene itself (it is a description, not a claim), but the scene MUST clearly evoke THIS specific content — a viewer who knows the source should recognize why this scene was chosen.

PHYSICAL PLAUSIBILITY (what separates a professional visual from an obviously AI-generated one — treat this as hard as the grounding rule):
- ANCHOR EVERY OBJECT. Each scene names what holds the subject up, AND says it casts a contact shadow there. An object whose support is never named comes out floating in mid-air. "Held" is not the same as "on a table": the user message names a SCENE WORLD for this post, and the anchor is whatever THAT place would use — a concrete floor, a wall bracket, a hook, a shelf, the ground, the water it sits in, another object it leans against ("the coiled cable lies on the concrete floor beside the rack, a hard shadow pooling under it"; "the enamel jug hangs from a nail in the plaster, its shadow cast sideways"). Defaulting every scene to an object on a table is the single most common way this set comes out looking generic.
- HANDS ARE NEVER THE SUBJECT. Never write a scene whose subject is hands doing fine work — typing, playing an instrument, counting, writing, gesturing, assembling. That is exactly where the model renders six fingers. If a human presence helps the story, keep it partial and still: a shoulder, a silhouette against a window, a forearm at rest entering the frame — always medium framing or wider, never an extreme close-up of a hand mid-action.
- ASYMMETRIC OBJECTS ARE A LAST RESORT. Things with a known handedness or a fixed layout — guitars and other instruments, keyboards, clocks, scissors, printed text, logos, dashboards, control panels — come out mirrored or scrambled. Prefer an object that is symmetric or has no "correct" orientation. If one is genuinely unavoidable, state its orientation explicitly ("acoustic guitar resting right-handed, strings facing camera") and frame it so the asymmetric part is partly out of frame or thrown out of focus.
- PREFER MATTER OVER MECHANISM. Objects, textures, liquids, paper, fabric, plants, light, weather and landscape hold up far better than anatomy and articulated machinery. When a scene could be built either way, choose the object over the person.

=== IMAGE PROMPT, STYLE & SLIDE PROMPTS (text-to-image generation) ===
This section applies when `image_prompt`, `image_style` and `image_slide_prompts` appear in REQUIRED VISUAL FIELDS — then all three are mandatory.
`image_prompt`: 30–60 words describing ONE still photograph — the strongest concrete image of this content, obeying the shared rules above. {space_cover} State the subject and what holds it up in the SCENE WORLD the user message names, the framing, the light with its source and quality, and the color palette. No people as the main subject, no text in the image.
`image_style`: 20–40 words of ART DIRECTION for this post — NOT a scene. The app appends it, WORD FOR WORD AND UNCHANGED, to the cover and to every carousel slide: it is the only thing that makes separately generated images read as one designed set, exactly like `video_style` does for video. It must be concrete and specific to THIS post, never a generic label like "editorial, professional, muted". Name, in prose: the light (source, direction, hour and quality — "single hard overhead key, deep falloff into black"), the dominant material or surface, the optics (focal length and depth of field), and the finish (grain, contrast, saturation level). It describes HOW everything is photographed; it never mentions the subject. Do NOT name a palette or invent colors: the palette is fixed brand identity and the app supplies it — a per-post palette here fights the brand one and the set loses its identity.
`image_slide_prompts`: an array of prompts for the remaining carousel slides. The user message says "IMAGE SLIDE PROMPTS NEEDED: N" — output EXACTLY N. (When that line is absent the piece is a single image: `image_prompt` and `image_style` are still mandatory and `image_slide_prompts` is an empty array.) Each is 20–40 words and:
- OBEYS THE CAROUSEL ARC. The user message names a "CAROUSEL ARC" and it decides how the slides relate to each other — the images are a sequence, not a catalogue of props. When the arc is TRANSFORMATION or CHAIN the hero object RECURS on purpose: carry the cover's object (or what it left behind) forward and name what has CHANGED about it in this slide — its state, its condition, its quantity, where it ended up. That change is the whole content of the image, so state it explicitly, and never re-describe the same object in the same condition twice. When the arc is WALKTHROUGH or SYSTEM each slide takes a DIFFERENT thing: before writing them, list N+1 distinct objects the source actually mentions and assign one per image — "the same device seen closer", "another view of it" or the same object with a different part in focus do NOT count as different, and that is what produces a carousel reading as one photo repeated;
- stays in the SCENE WORLD the user message names — the app locks that location into every image of the set, so the slides hold together through the PLACE and never through repeating the subject. Put your object where that place would really keep it; do not restate the location itself, and do not restate the palette or the light (they come from `image_style` and from brand identity);
- describes only its own subject: the app assigns each slide its framing and angle from its BEAT (see "SLIDE BEATS" in the user message), so do not open with a camera instruction and never state a shot distance — pick an object that works at the distance that beat is shot from (the tension is the tightest shot of the set, the payoff the widest);
{space_slides}

=== VIDEO PROMPT, STYLE & STORYBOARD (text-to-video generation) ===
`video_prompt`, `video_style` and `video_storyboard` feed an AI text-to-video model. This section applies when they appear (with `video_voiceover`) in REQUIRED VISUAL FIELDS — then all four are mandatory.
They obey the SHARED RULES above (grounding + physical plausibility), plus:

CINEMATOGRAPHY (write each shot like a director, not a keyword list):
- Every shot description states, in natural prose: the subject and its ONE action; the framing (extreme close-up / close-up / medium / wide); ONE camera move (slow push-in, lateral glide, slow orbit, tilt-up reveal, rack focus); and the light with its source and quality ("warm late-afternoon window light", "a single desk lamp in a dark room", "soft overcast daylight").
- One continuous take per shot: no cuts, no "then...", exactly one camera move, motion a real camera operator could physically execute.
- Compose for a vertical 9:16 phone screen: subject in the upper two thirds, some foreground depth, and keep the lower third of the frame visually calm — burned-in captions will sit there.

MOTION (on top of the shared physical-plausibility rules — movement is where AI video breaks):
- NO FULL-BODY PEOPLE IN MOTION. Nobody walking, running, turning or gesturing in full frame.
- KEEP THE PHYSICAL EVENT SMALL. One slow, simple change per shot (a lid closing, steam rising, a page turning, light creeping across a surface, dust settling). Fast or compound motion multiplies artifacts.

`video_style`: the look-lock that makes separately-generated segments cut together as one film — 10–18 words describing ONLY the look: lens/film character, lighting character, color palette, mood (e.g. "shot on 35mm, shallow depth of field, warm amber practicals against cool dusk blue, quiet confident mood"). No subjects, no actions, no scenery. It is appended verbatim to every shot, so never restate the look inside the beats.

`video_prompt`: 40–80 words describing ONE continuous, filmable scene — the strongest single beat of the content, obeying the grounding, cinematography and physical-plausibility rules above.

`video_storyboard`: an array of SHOT beats for a longer clip stitched from several segments. The user message says "VIDEO SEGMENTS NEEDED: N" — output EXACTLY N beats. Each beat is ONE continuous shot (25–50 words) obeying the grounding, cinematography and physical-plausibility rules. The N beats must read as ONE continuous visual story, never N disconnected clips:
- Keep a recurring anchor across all beats — the same protagonist object, character or location evolving shot to shot, so the viewer feels "same world, next moment".
- Chain the beats: open each beat on something the previous one left off (the object it ended on, the direction the camera was moving, the space it was entering) and name that carried-over element explicitly at the start of the beat.
- Vary the framing between consecutive beats (wide → close-up → medium…) so each cut feels intentional; never vary the look — that lives in `video_style`.
- Arc: beat 1 opens mid-action on the boldest image (it must grab in the first second), middle beats develop or escalate, and the final beat resolves on a payoff image that can hold a closing thought.
Beat 1 should match the spirit of `video_prompt`. Draw each beat from a different concrete moment/example in the transcript when possible, not the same image repeated.

`video_voiceover`: the narration spoken OVER the clip by a TTS voice — an array of EXACTLY N lines (same N as `video_storyboard`). Unlike the prompts above, write it in the SAME LANGUAGE as the posts: line i is read aloud while shot i plays.
- COUNT IS STRUCTURAL, NOT STYLISTIC: `video_voiceover` and `video_storyboard` must have the SAME number of items, one spoken line per shot. If the source material feels thin for N lines, split the narration into N shorter beats — never return fewer lines than shots.
- SOUND HUMAN (the narration IS the reel — it must sound like a person, not a script): write like a creator talking to the camera — everyday spoken words, natural contractions, direct address to the viewer, and rhythm (mix a short punchy sentence with a longer one). Read each line aloud in your head: if it sounds like an essay or a news anchor, rewrite it. The humanization checklist applies here too (no AI filler connectors, no inflated vocabulary).
- WORD BUDGET: the user message gives "VOICEOVER WORDS PER LINE: MIN-MAX". EVERY line must land inside that range — each line fills a fixed audio window, so a short line leaves seconds of dead air mid-reel (kills the flow) and an overlong one gets sped up.
- FLOW BETWEEN LINES: line 1 is the spoken hook — open with the tension, claim or question that earns the next twenty seconds (never a greeting, never "en este video…"/"in this video…"). From line 2 on, pick up the previous line's idea with a natural spoken connector (ES: "Y eso significa que…", "Pero acá viene lo bueno:", "¿El resultado?" — EN: "And that means…", "But here's the good part:", "The result?") — vary them, never open two lines the same way, never restart cold as if the previous line didn't exist. The last line closes the idea and may end with ONE short natural call to action (watch the full video / follow) only if it fits the budget.
- The lines are narration, NOT captions: never mention or describe what is on screen. Plain speakable prose only: no hashtags, no emojis, no URLs, no quotes, no "shot 1:" prefixes; write short numbers as words. The FAITHFUL CITATIONS rule fully APPLIES here (these lines make claims the audience hears): every fact must come from the transcript.

=== COPY STRUCTURE (choose the shape the source supports — the bulleted list is NOT the default) ===
A post whose skeleton is "hook → bulleted takeaways → engagement question → hashtags" reads as machine-written however good its sentences are, because every AI post in the feed has that exact shape. So before writing a line, decide which structure the SOURCE actually supports and write the post in it. The structure decides the paragraphs; the platform rules below only decide length, tone and ending.

1. ANECDOTE — the source tells one specific incident: drop the reader inside it (the scene, what happened, the turn), then the single thing it proves. Prose.
2. CONTRAST — myth vs reality, before vs after, what everyone does vs what actually worked. Two facing blocks and a short verdict.
3. THE NUMBER — one figure the speaker actually gives carries the whole post: state it cold in line 1, then what produced it and what it changes.
4. THESIS — a claim the source defends. State it flat, meet the obvious objection head-on, close on the reason it still holds.
5. STEP BY STEP — only when the source really describes a process in order. Each step carries the reason it exists, not just the instruction.
6. TAKEAWAYS — the classic list. Only when the source genuinely enumerates independent items with no order between them.
7. SYMPTOM → DIAGNOSIS — open on the symptom the reader recognises, name the cause the source identifies, give the fix it proposes.
8. ANALOGY — explain the unfamiliar mechanism through one concrete everyday thing from the source's own world, then bring it back to the topic.

How to use them:
- Pick by the shape of the material, never at random. When two fit, take the less list-like one.
- Structures 5 and 6 are the ONLY ones allowed to use bullets or → (max 5 items). In 1, 2, 3, 4, 7 and 8 bullet characters are forbidden — write paragraphs.
- When several platforms are requested they must NOT all use the same structure, and no two may open with the same sentence: same source, different way in.
- Vary the ending. An engagement question is one option among several: a flat statement that lands the idea, one concrete next step, or the line the reader would underline. Never a closing question that could be pasted onto any other post ("¿Y tú qué opinas?" / "What do you think?").
- Paragraph rhythm: never three paragraphs of the same length in a row. A one-line paragraph standing alone is allowed and works.

=== LINKEDIN POST RULES ===
- 150–300 words
- Strong hook in the first line — NEVER start with "En este video..." / "In this video..." / "Descubre cómo..." / "Discover how..."
- Develop 3–5 concrete points from the source, laid out in the structure chosen above — bullets or → ONLY if that structure allows them, otherwise paragraphs
- Conversational but authoritative tone
- If a source video URL is provided (see the user message), include it on its own line just before the hashtags, with this exact CTA:
  Spanish: "▶ Mira el video completo aquí: <url>"
  English: "▶ Watch the full video here: <url>"
  Do NOT wrap the URL in markdown — paste it raw. If NO source URL is provided, skip this line entirely (do not invent a URL or a "watch the video" CTA).
- Close the way the chosen structure asks (open question, flat statement or concrete next step) — it goes after the URL line if present, before or among the hashtags
- 3–5 relevant hashtags at the very end

=== INSTAGRAM POST RULES ===
- 80–150 words
- Strong opening hook (1 sentence) — plain text, no asterisks/markdown bold
- Short punchy sentences of varied length; bullets ONLY if the chosen structure allows them
- 3–6 emojis woven in naturally (not stacked at the end or beginning)
- Clear call-to-action: "Link en bio" / "Link in bio" — do NOT paste the raw YouTube URL in captions
- MAXIMUM 5 hashtags (hard limit — the platform rejects more)

=== FACEBOOK POST RULES ===
- 80–180 words
- Warm, conversational opening hook (1–2 sentences) — write like a person talking to their community, not a corporate brand
- Short paragraphs, prose by default; bullets ONLY if the chosen structure allows them
- 1–3 emojis woven in naturally (optional, never stacked)
- If a source video URL is provided (see the user message), you MAY include it raw on its own line near the end (Facebook renders link previews); if NO source URL is provided, do not invent one
- Close the way the chosen structure asks (question, statement or a clear call-to-action)
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
10. Structure check — reread the finished post: if it comes out as "hook + bulleted list + engagement question + hashtags", that IS the generic AI shape, whatever the words say. Rewrite it into one of the structures above; if the source does not enumerate independent items, the list was never the right shape for it. And if two platforms ended up with the same structure or the same opening line, rewrite one of them.
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


def _wants_images(params: dict) -> bool:
    """¿El pipeline va a generar imágenes que necesiten prompt visual del LLM?

    Complemento de `_wants_video` con la misma regla: en "subir"/"fotos" el medio ya
    existe, y un job de video no genera imágenes. Todo lo demás (post de imagen
    única, carrusel, historia-imagen) sí. Compartida por los dos flujos.
    """
    if params.get("media_origin", "generar") in ("subir", "fotos"):
        return False
    return not _wants_video(params)


# Presupuesto de transcripción que viaja al LLM. Con el corte plano anterior
# (`[:6000]`) un video de 40 minutos se resumía por su intro: los datos, el hook y
# —sobre todo— los prompts visuales salían todos del primer minuto y no del video.
_TRANSCRIPT_BUDGET = 12000


def _cut_at_word(text: str, *, head: bool) -> str:
    """Recorta el trozo hasta un límite de palabra (evita cortar a mitad de token)."""
    if head:  # se descarta el arranque parcial
        i = text.find(" ")
        return text[i + 1:] if 0 <= i < 40 else text
    i = text.rfind(" ")  # se descarta el final parcial
    return text[:i] if i > len(text) - 40 else text


def _transcript_excerpt(transcript: str, budget: int = _TRANSCRIPT_BUDGET) -> str:
    """Muestra representativa de TODA la transcripción, no solo del arranque.

    Cuando el texto excede el presupuesto se toman tres tramos: inicio (tema y
    gancho), medio (los ejemplos concretos, que es de donde salen los buenos
    prompts visuales) y cierre (conclusión y CTA), separados por un marcador
    explícito para que el modelo sepa que hay huecos y no invente continuidad.
    """
    t = (transcript or "").strip()
    if len(t) <= budget:
        return t
    head = round(budget * 0.45)
    mid = round(budget * 0.30)
    tail = budget - head - mid
    mid_start = (len(t) - mid) // 2
    parts = [
        _cut_at_word(t[:head], head=False),
        _cut_at_word(_cut_at_word(t[mid_start:mid_start + mid], head=True), head=False),
        _cut_at_word(t[len(t) - tail:], head=True),
    ]
    return "\n[...]\n".join(p.strip() for p in parts if p.strip())


def _is_excerpt(content: dict) -> bool:
    """¿La transcripción viajó recortada (tres tramos) o entera?"""
    return len((content.get("transcript") or "").strip()) > _TRANSCRIPT_BUDGET


def _info_slides_needed(params: dict) -> int:
    """Slides de INFO del carrusel: la portada más `n - 1` informativos.

    Misma cuenta que `n_info` en `job_runner._run_media_phase` — vive acá para que
    el user message y la verificación de lo que entregó el LLM no puedan separarse.
    """
    if params.get("formato_instagram", "imagen-unica") != "carrusel":
        return 0
    try:
        n = int(params.get("carrusel_slides", 3) or 3)
    except (TypeError, ValueError):
        n = 3
    return max(3, min(6, n)) - 1


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


# ── Briefing visual: SOLO el bloque que este job necesita ─────────────────────
# Antes se emitían las dos compuertas siempre ("IMAGE PROMPT NEEDED: yes" pegado a
# "VIDEO PROMPT NEEDED: no", más tres recordatorios seguidos de "set ... to empty
# strings"). Un job de imagen mandaba así una instrucción de vaciar campos justo
# debajo de la de escribirlos, y el modelo la aplicaba al bloque equivocado: JSON
# válido, `image_text` entero y los tres prompts de imagen en blanco — exactamente
# lo que `_faltantes` detectaba y el reintento ciego no arreglaba, porque el prompt
# volvía a decir lo mismo. Ahora del bloque que no aplica no se dice NADA: sus
# campos los rellena `_parse_raw` con el vacío, no el modelo.


def _linea_beats(n_info_slides: int) -> str:
    """La escalera de beats del carrusel, para el redactor.

    Es el otro extremo de `prompt_architect.roles_carrusel`: la app le da a cada slide
    un plano, una escala de titular y un acento según su beat, y acá se le pide al
    modelo el TEXTO de ese mismo beat. Sin esto los dos lados escriben una secuencia
    y ninguno conoce la del otro: la app pide el plano más cerrado del set para el
    slide 2 y el modelo le escribe encima una conclusión.
    """
    lineas = ["SLIDE BEATS (each info slide has a job in the sequence — its printed idea AND "
              "its scene are written for it):"]
    for i, rol in enumerate(parch.roles_carrusel(n_info_slides), 1):
        funcion = parch.funcion_beat(rol)
        lineas.append(f"  slide {i} — {rol}: {funcion}" if funcion else f"  slide {i} — {rol}")
    return "\n".join(lineas)


def _linea_sistema_texto(params: dict) -> str:
    """El sistema de texto congelado del job: qué bloques lleva cada slide.

    Es el contrato de forma de `image_text.slides[i]`, y sale del MISMO sitio que lo
    construye (`prompt_architect.datos_sistema`). Que se genere y no se escriba a mano
    es lo que impide el fallo silencioso obvio: el redactor entregando dos bloques donde
    la pieza imprime tres, y el tercero saliendo en blanco sin un solo error.

    Los topes de palabras no son estilo: son las que caben a la escala de ese bloque.
    """
    sistema = parch.sistema_valido(params.get("sistema_texto")) or ""
    datos = parch.datos_sistema(sistema)
    claves = ", ".join(f"`{b['clave']}`" for b in datos["bloques"])
    lineas = [f"SLIDE TEXT SYSTEM: {datos['sistema']} — {datos['encargo']}. "
              f"Every object in `image_text.slides` carries EXACTLY these keys: {claves}."]
    for b in datos["bloques"]:
        lo, hi = b["palabras"]
        rango = (f"{lo}-{hi} words" if lo else f"up to {hi} words")
        lineas.append(f"  `{b['clave']}`: {rango}")
    return "\n".join(lineas)


def _linea_mundo(params: dict) -> str:
    """El mundo congelado del job, para el redactor. `""` si el job no trae ninguno.

    El mismo string que `prompt_architect` emite como bloqueo de mundo en cada imagen.
    Tiene que llegar también acá porque quien elige el OBJETO de cada escena es este
    modelo: si escribe un objeto para un sitio que no conoce, el bloqueo —que se
    prefija a la misma sección del prompt final— acaba contradiciéndolo.
    """
    escenario = str(params.get("escenario_visual") or "").strip()
    if not escenario:
        return ""
    return (f"SCENE WORLD (fixed for every image of this post; the app states it verbatim, "
            f"so put your objects where this place would keep them and never restate the "
            f"place itself): {escenario}")


def _linea_arco(params: dict) -> str:
    """El arco del carrusel, para el redactor. `""` si el job no trae ninguno.

    Es el otro extremo de `prompt_architect._clausula_arco`, igual que `_linea_beats`
    lo es de la cláusula de plano: la app le da a cada slide su enlace con los demás y
    acá se le pide al modelo la ESCENA que ese enlace necesita. Sin esto el redactor
    escribiría N objetos sueltos —era la regla explícita hasta ahora— y las imágenes
    llegarían con una instrucción de continuidad que su propio contenido no sostiene.
    """
    arco = str(params.get("arco_carrusel") or "").strip()
    funcion = parch.funcion_arco(arco) if arco else ""
    if not funcion:
        return ""
    vuelve = parch.sujeto_arco(arco) in ("recurrente", "encadenado")
    regla = ("The hero object RECURS across the slides: carry it forward and name what has "
             "CHANGED about it in each one."
             if vuelve else
             "Each slide takes a DIFFERENT hero object; what they share is the place, not the "
             "thing.")
    return f"CAROUSEL ARC: {arco} — {funcion}. {regla}"


def _briefing_visual(*, lang: str, wants_images: bool, wants_video: bool,
                     n_info_slides: int, n_image_slide_prompts: int,
                     n_video_segments: int, vo_lo: int, vo_hi: int,
                     params: dict | None = None) -> tuple[str, list[str]]:
    """`(briefing, campos_requeridos)` para este job — sin mencionar el otro medio."""
    lineas: list[str] = []
    requeridos: list[str] = ["image_text.hook"]
    params = params if isinstance(params, dict) else {}

    if wants_images:
        # El mundo va antes que la pieza y vale también para la imagen única: es dónde
        # ocurre TODO lo que este post fotografíe, carrusel o no.
        mundo = _linea_mundo(params)
        if mundo:
            lineas.append(mundo)
        if n_image_slide_prompts:
            lineas.append(f"PIECE: carousel — 1 cover + {n_info_slides} info slides.")
            lineas.append(f"INFO SLIDES NEEDED: {n_info_slides}  (EXACTLY this many objects in "
                          "image_text.slides, one per info slide, read as one sequence: each "
                          "slide advances the previous one, the last lands the payoff)")
            lineas.append(f"IMAGE SLIDE PROMPTS NEEDED: {n_image_slide_prompts}  (EXACTLY this "
                          "many scenes in image_slide_prompts, one per info slide, each "
                          "carrying the carousel arc one step forward)")
            # El sistema de texto va antes que el arco y los beats: dice la FORMA de cada
            # slide, y los otros dos dicen qué se escribe dentro de ella.
            lineas.append(_linea_sistema_texto(params))
            arco = _linea_arco(params)
            if arco:
                lineas.append(arco)
            lineas.append(_linea_beats(n_info_slides))
            requeridos += ["image_text.slides", "image_prompt", "image_style",
                           f"image_slide_prompts (x{n_image_slide_prompts})"]
        else:
            lineas.append("PIECE: one single image — a cover visual, no carousel slides.")
            requeridos += ["image_prompt", "image_style"]

    if wants_video:
        lineas.append(f"PIECE: vertical video stitched from {n_video_segments} segment(s).")
        lineas.append(f"VIDEO SEGMENTS NEEDED: {n_video_segments}  (EXACTLY this many shot beats "
                      "in video_storyboard, chained as one continuous visual story)")
        lineas.append(f"VOICEOVER WORDS PER LINE: {vo_lo}-{vo_hi}  (video_voiceover: EXACTLY "
                      f"{n_video_segments} spoken line(s) in {lang}, every line inside that range)")
        requeridos += ["video_prompt", "video_style",
                       f"video_storyboard (x{n_video_segments})",
                       f"video_voiceover (x{n_video_segments})"]

    return "\n".join(lineas), requeridos


def _recordatorio_visual(*, lang: str, wants_images: bool, wants_video: bool,
                         n_info_slides: int, n_image_slide_prompts: int,
                         n_video_segments: int, vo_lo: int, vo_hi: int,
                         hay_arco: bool = False) -> str:
    """Recordatorio final: solo el medio que este job genera."""
    lineas: list[str] = []
    if wants_images:
        # Solo se nombra el arco si el job trae uno: recordarle al modelo que siga una
        # historia que nadie le contó es la misma clase de defecto que mencionar el
        # medio que este job no genera.
        avance = ("each moving the CAROUSEL ARC one step forward" if hay_arco
                  else "each built on a DIFFERENT concrete detail of the source")
        detalle = (f"image_text.slides must have EXACTLY {n_info_slides} item(s) and "
                   f"image_slide_prompts EXACTLY {n_image_slide_prompts} scene(s), {avance}"
                   if n_image_slide_prompts else
                   "image_text.slides is an empty array (single image, no carousel slides)")
        lineas.append(
            "- Images: image_text.hook is a short complete cover phrase; " + detalle + ". "
            "Write image_prompt (the single most concrete image of THIS content), image_style "
            "(the shared art direction — light, material, optics and finish, appended unchanged "
            "to every image; no palette) and the slide scenes in English, grounded in the "
            "transcript and set where the briefing above puts them. Never a generic stock scene, "
            "never an object on a table by default, never text inside the image."
        )
    if wants_video:
        lineas.append(
            f"- Video: write video_prompt (single strongest beat), video_style (the look-lock "
            f"appended to every shot) and EXACTLY {n_video_segments} beat(s) in video_storyboard, "
            "each grounded in a concrete detail from the transcript and chained as one continuous "
            f"visual story. video_voiceover: EXACTLY {n_video_segments} spoken line(s) in {lang}, "
            f"{vo_lo}-{vo_hi} words each, flowing as one narration (hook → build → payoff) with "
            "natural connectors; every fact from the transcript."
        )
    return "\n".join(lineas)


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

    # How many info slides the carousel needs: slide 0 = hook y TODOS los que siguen
    # son de info (el último incluido — el carrusel ya no lleva slide de créditos).
    # El carrusel es multi-red (IG nativo, LinkedIn document carousel, FB multi-foto),
    # así que los slides se piden siempre que el formato sea carrusel.
    n_info_slides = _info_slides_needed(params)

    transcript_snippet = _transcript_excerpt(content.get("transcript") or "")
    tags = content.get("tags", [])
    chapters = content.get("chapters", [])
    channel = content.get("channel", "")
    title = content.get("title", "")
    description = (content.get("description") or "")[:500]

    # Reel/historia: el caption es para un Reel/Story (IG y FB), no un feed post.
    tipo_post = params.get("tipo_post", "post")
    special_fmt = {"reel": "reel", "historia": "story"}.get(tipo_post)

    wants_video = _wants_video(params)
    # Prompts de imagen: solo cuando el pipeline va a generar imágenes. Los slides
    # extra del carrusel llevan uno cada uno (el de portada es `image_prompt`).
    wants_images = _wants_images(params)
    n_image_slide_prompts = n_info_slides if wants_images else 0
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

    briefing, requeridos = _briefing_visual(
        lang=lang, wants_images=wants_images, wants_video=wants_video,
        n_info_slides=n_info_slides, n_image_slide_prompts=n_image_slide_prompts,
        n_video_segments=n_video_segments, vo_lo=vo_lo, vo_hi=vo_hi, params=params,
    )

    return f"""Write posts for these platforms:
{chr(10).join(f'- {p}' for p in platforms)}

CONTENT SOURCE: {source_label}.
Language to write in: {lang}
{briefing}
{url_line}
Channel: {channel}

TITLE: {title}

DESCRIPTION (first 500 chars): {description}

TAGS: {tags}

CHAPTERS: {chapters}

TRANSCRIPT / TEXT{" (excerpt: beginning, middle and end of the source; [...] marks a gap)" if _is_excerpt(content) else ""}:
{transcript_snippet}

{"[Note: transcript is empty — use title + description only]" if not transcript_snippet.strip() else ""}
{_manual_context_block(params, wants_video)}
Important reminders:
- Only write text for the platforms listed above; set every other platform's text to an empty string
{_off_networks_reminder(do_li, do_ig_text, do_fb)}
- Apply the full humanization checklist before outputting
- Verify every specific claim against the transcript above
- Instagram: max 5 hashtags, no raw URL in caption
{li_url_reminder}
{_recordatorio_visual(lang=lang, wants_images=wants_images, wants_video=wants_video,
                      n_info_slides=n_info_slides,
                      n_image_slide_prompts=n_image_slide_prompts,
                      n_video_segments=n_video_segments, vo_lo=vo_lo, vo_hi=vo_hi,
                      hay_arco=bool(_linea_arco(params)))}
REQUIRED VISUAL FIELDS (all of them mandatory, none may come back empty): {", ".join(requeridos)}
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


def _normalize_slide(valor):
    """Un slide del LLM → `str` (contrato de siempre) o `dict` de bloques conocidos.

    Los dos contratos conviven a propósito: el sistema `titular` sigue pidiendo una
    frase y los que llevan cuerpo piden un objeto. Del dict se conservan SOLO las
    claves de bloque del catálogo —una clave inventada por el modelo no puede llegar
    al prompt— y se descartan las vacías, para que `separar_bloques` vea el hueco.
    """
    if isinstance(valor, dict):
        return {c: " ".join(str(valor[c]).split())
                for c in parch.CLAVES_BLOQUE
                if c in valor and str(valor[c] or "").strip()}
    return str(valor or "").strip()


def _normalize_image_text(value) -> dict | None:
    """Coerce a parsed `image_text` into {"hook": str, "slides": [str | dict, ...]}.

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
    if isinstance(raw_slides, (str, dict)):
        raw_slides = [raw_slides]
    if not isinstance(raw_slides, (list, tuple)):
        raw_slides = []
    slides = [s for s in (_normalize_slide(x) for x in raw_slides) if s]
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
    for key in ("image_prompt", "image_style", "video_prompt", "video_style"):
        if posts.get(key):
            posts[key] = _sanitize_text(posts[key])
    for key in ("image_slide_prompts", "video_storyboard", "video_voiceover"):
        if isinstance(posts.get(key), list):
            posts[key] = [_sanitize_text(s) for s in posts[key] if _sanitize_text(s)]
    return posts


# Campos visuales que el LLM debe entregar en el NIVEL SUPERIOR del JSON. Algunos
# modelos los anidan dentro de `image_text` (reproducido con sonar-pro): el JSON
# parsea sin error, pero `_normalize_image_text` se queda solo con hook/slides y
# todos los prompts se pierden en silencio. Se rescatan antes de normalizar.
_VISUAL_KEYS = ("image_prompt", "image_style", "image_slide_prompts",
                "video_prompt", "video_style", "video_storyboard", "video_voiceover")


def _lift_nested_visuals(parsed: dict) -> bool:
    """Sube al nivel superior los campos visuales que el LLM metió en `image_text`."""
    img = parsed.get("image_text")
    if not isinstance(img, dict):
        return False
    movidos = [k for k in _VISUAL_KEYS if k in img and not parsed.get(k)]
    for key in movidos:
        parsed[key] = img.pop(key)
    if movidos:
        print(f"   [aviso] El LLM anidó {', '.join(movidos)} dentro de image_text: rescatado(s).")
    return bool(movidos)


def _parse_raw(raw: str, diagnostico: list | None = None) -> dict:
    """Parsea la respuesta del LLM. `diagnostico` recoge cómo se degradó el parseo.

    Los códigos que puede anotar son `campos_anidados` (el JSON venía bien pero con
    los prompts fuera de lugar, se rescataron) y `json_malformado` (no se pudo
    parsear ni reparar: solo se recuperan los tres captions y TODO lo visual se
    pierde). Antes esto pasaba sin dejar rastro y el preview aparecía sin prompts
    sin que nada dijera por qué.
    """
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
        if _lift_nested_visuals(parsed) and diagnostico is not None:
            diagnostico.append("campos_anidados")
        # Normalize image_text in place (None when absent/unusable -> heuristic fallback downstream).
        img = _normalize_image_text(parsed.get("image_text"))
        if img is not None:
            parsed["image_text"] = img
        else:
            parsed.pop("image_text", None)
        # image_prompt / image_style / video_prompt / video_style: string no vacío o
        # nada (job_runner cae al prompt genérico / al acabado y estilo por defecto).
        for key in ("image_prompt", "image_style", "video_prompt", "video_style"):
            v = parsed.get(key)
            if isinstance(v, str) and v.strip():
                parsed[key] = v.strip()
            else:
                parsed.pop(key, None)
        # image_slide_prompts: un prompt por slide extra del carrusel (o nada →
        # job_runner cae a las variaciones del título). video_storyboard: lista de
        # shots (o nada → job_runner cae al video_prompt único → 1 segmento).
        # video_voiceover: misma forma (una línea hablada por shot) o nada (el reel
        # sale mudo, como antes).
        for key in ("image_slide_prompts", "video_storyboard", "video_voiceover"):
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
        print("   [aviso] El JSON del LLM no se pudo parsear: solo se rescataron los "
              "textos de los posts, los prompts visuales se perdieron.")
        if diagnostico is not None:
            diagnostico.append("json_malformado")
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


async def _call_anthropic(system: str, user: str, queue: asyncio.Queue,
                          api_key: str) -> tuple[str, object]:
    """Transporte: manda system+user y devuelve `(texto crudo, usage)`.

    Lo comparten la escritura completa y la reparación de campos faltantes, para que
    las dos hablen con el proveedor por el mismo sitio.
    """
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
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
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
                final = stream.get_final_message()
                usage = final.usage
                # Truncar por límite de salida deja el JSON a medias y el parser cae al
                # rescate de solo-captions: se dice en el log, que era la única pista
                # que faltaba para distinguirlo de "el modelo lo dejó vacío".
                if getattr(final, "stop_reason", "") == "max_tokens":
                    print("   [aviso] La respuesta del LLM se cortó por max_tokens: "
                          "el JSON llega incompleto.")
            except Exception:
                usage = None
        return "".join(chunks), usage

    return await loop.run_in_executor(None, _stream)


async def _write_with_anthropic(content: dict, params: dict, clean_url: str, queue: asyncio.Queue,
                                api_key: str, *, text_overlay: bool = True,
                                diagnostico: list | None = None) -> tuple[dict, dict | None]:
    raw, usage = await _call_anthropic(_system_prompt(text_overlay),
                                       _user_message(content, params, clean_url),
                                       queue, api_key)
    return _sanitize_posts(_parse_raw(raw, diagnostico)), _anthropic_usage(usage)


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


def _perplexity_model(params: dict) -> str:
    model = params.get("modelo_perplexity") or PERPLEXITY_MODEL
    return model if model in PERPLEXITY_MODELS else PERPLEXITY_MODEL


async def _call_perplexity(system: str, user: str, queue: asyncio.Queue, api_key: str,
                           model: str) -> tuple[str, object]:
    """Transporte: Perplexity expone un chat streaming compatible con OpenAI. Se pega
    con urllib (sin SDK) y se parsean las líneas SSE `data: {...}` a mano.

    Compartido por la escritura completa y la reparación de campos faltantes.
    """
    loop = asyncio.get_event_loop()

    def _stream():
        body = json.dumps({
            "model": model,
            "max_tokens": 4096,
            "stream": True,
            # Keep the model grounded in the transcript instead of leaning on web search.
            "web_search_options": {"search_context_size": "low"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "system", "content": _PERPLEXITY_GROUNDING},
                {"role": "user", "content": user},
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

    return await loop.run_in_executor(None, _stream)


async def _write_with_perplexity(content: dict, params: dict, clean_url: str, queue: asyncio.Queue,
                                 api_key: str, *, text_overlay: bool = True,
                                 diagnostico: list | None = None) -> tuple[dict, dict | None]:
    model = _perplexity_model(params)
    raw, usage = await _call_perplexity(_system_prompt(text_overlay),
                                        _user_message(content, params, clean_url),
                                        queue, api_key, model)
    return _sanitize_posts(_parse_raw(raw, diagnostico)), _perplexity_usage(usage, model)


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


# ── Verificación de lo que entregó el LLM ─────────────────────────────────────
# El contrato del prompt del sistema pide unos campos visuales concretos; cuando el
# modelo no los entrega, el pipeline los rellena con variaciones del TÍTULO y la
# pieza sale genérica. Antes eso pasaba en silencio: acá se detecta, se reintenta
# una vez y, si sigue faltando, se dice en la compuerta previa de los dos flujos.


def captions_needed(params: dict) -> list[str]:
    """Campos de caption que este job necesita, en orden canónico.

    Fuente única de "qué textos pide este job": la usan la verificación de lo que
    entregó el LLM, la reparación y las dos compuertas previas (vía `_needs_job`),
    para que ninguna pueda discrepar de lo que el user message pidió. TikTok no
    tiene caption propio: reutiliza el del reel de Instagram, igual que en
    `_user_message`.
    """
    nets = active_networks(params)
    campos = []
    if "linkedin" in nets:
        campos.append("linkedin_text")
    if "instagram" in nets or "tiktok" in nets:
        campos.append("instagram_text")
    if "facebook" in nets:
        campos.append("facebook_text")
    return campos


def sistema_texto(params: dict) -> str:
    """El sistema de texto congelado del job. Fuente única para el redactor y el lint."""
    return parch.sistema_valido((params or {}).get("sistema_texto")) or ""


def slides_incompletos(slides, params: dict, n_info: int = -1) -> list[int]:
    """Los índices de slide a los que les falta algún bloque OBLIGATORIO de su sistema.

    Cuenta lo que la pieza va a imprimir, no cuántas entradas trajo el modelo: con un
    sistema de tres bloques, N slides con solo el titular son N slides que salen medio
    vacíos —y eso pasaba entero mientras la comprobación era `len(slides) < n_info`—.
    La usan `_faltantes` (para pedir la reparación) y el lint (para avisar).
    """
    n = _info_slides_needed(params) if n_info < 0 else n_info
    if not n:
        return []
    sistema = sistema_texto(params)
    requeridos = parch.bloques_requeridos(sistema)
    lista = slides if isinstance(slides, (list, tuple)) else []
    malos = []
    for i in range(n):
        bloques = parch.bloques_de_slide(lista[i] if i < len(lista) else "", sistema)
        if any(not bloques.get(c) for c in requeridos):
            malos.append(i)
    return malos


def _faltantes(posts: dict, params: dict) -> list[str]:
    """Campos que este job necesita y el LLM no entregó (captions y visuales).

    Los captions son parte del contrato igual que los prompts: una red destino sin
    texto publica un post vacío. Antes solo se comprobaba lo visual, así que ese
    hueco pasaba entero — sin reparación, sin aviso y sin campo donde escribirlo a
    mano (la compuerta previa dibujaba el textarea solo si YA había texto).
    """
    faltan: list[str] = [c for c in captions_needed(params)
                         if not str(posts.get(c) or "").strip()]
    n_info = _info_slides_needed(params)
    if _wants_images(params):
        for key in ("image_prompt", "image_style"):
            if not (posts.get(key) or "").strip():
                faltan.append(key)
        img = posts.get("image_text") if isinstance(posts.get("image_text"), dict) else {}
        if not (img.get("hook") or "").strip():
            faltan.append("image_text.hook")
        if n_info:
            if len(posts.get("image_slide_prompts") or []) < n_info:
                faltan.append("image_slide_prompts")
            if slides_incompletos(img.get("slides"), params, n_info):
                faltan.append("image_text.slides")
    if _wants_video(params):
        for key in ("video_prompt", "video_style"):
            if not (posts.get(key) or "").strip():
                faltan.append(key)
        n_shots = _segments_needed(params)
        if len(posts.get("video_storyboard") or []) < n_shots:
            faltan.append("video_storyboard")
        if not (posts.get("video_voiceover") or []):
            faltan.append("video_voiceover")
    return faltan


# ── Merge por campo entre intentos ────────────────────────────────────────────
# El reintento ya no reemplaza el objeto entero. Antes, si el 1er intento traía la
# portada pero no los slides y el 2º al revés, ganaba el que tuviera menos faltantes
# y lo bueno del otro se tiraba: se pagaban dos llamadas y se usaba media. Ahora cada
# campo se queda con la versión más completa de las dos.

_CAMPOS_TEXTO = ("linkedin_text", "instagram_text", "facebook_text")
_CAMPOS_STR = ("image_prompt", "image_style", "video_prompt", "video_style")
_CAMPOS_LISTA = ("image_slide_prompts", "video_storyboard", "video_voiceover")


def _completa(base, alt, minimo: int = 0) -> bool:
    """¿`alt` mejora a `base` para este campo? (más completo, nunca vacío por lleno)"""
    if isinstance(base, list) or isinstance(alt, list):
        b, a = len(base or []), len(alt or [])
        if minimo and b >= minimo:
            return False  # la base ya cumple lo pedido: no se toca
        return a > b
    return not str(base or "").strip() and bool(str(alt or "").strip())


def _merge_posts(base: dict, extra: dict, params: dict) -> dict:
    """Rellena en `base` SOLO lo que le falta, con lo que trajo `extra`. Muta `base`."""
    if not isinstance(extra, dict):
        return base
    n_info = _info_slides_needed(params)
    n_shots = _segments_needed(params) if _wants_video(params) else 0

    for campo in _CAMPOS_TEXTO + _CAMPOS_STR:
        if _completa(base.get(campo), extra.get(campo)):
            base[campo] = extra[campo]
    for campo, minimo in zip(_CAMPOS_LISTA, (n_info, n_shots, n_shots)):
        if _completa(base.get(campo), extra.get(campo), minimo):
            base[campo] = extra[campo]

    # image_text se mezcla por dentro: el hook y las frases pueden venir de intentos
    # distintos sin que uno borre al otro.
    img_extra = extra.get("image_text") if isinstance(extra.get("image_text"), dict) else {}
    if img_extra:
        img = dict(base["image_text"]) if isinstance(base.get("image_text"), dict) else {}
        if _completa(img.get("hook"), img_extra.get("hook")):
            img["hook"] = img_extra["hook"]
        slides = _merge_slides(img.get("slides"), img_extra.get("slides"), params, n_info)
        if slides is not None:
            img["slides"] = slides
        if img:
            base["image_text"] = img
    return base


def _merge_slides(base, extra, params: dict, n_info: int):
    """Funde los slides POSICIÓN a posición y, dentro de cada uno, BLOQUE a bloque.

    Misma regla que el resto del merge y por el mismo motivo: si el primer intento trajo
    los titulares y el segundo los cuerpos, quedarse con el que tenga «menos faltantes»
    tira media respuesta pagada. Con un solo bloque por slide se comporta igual que la
    comparación de listas que había, así que el sistema `titular` no cambia.
    """
    if not isinstance(extra, (list, tuple)) or not extra:
        return None
    lista_base = list(base) if isinstance(base, (list, tuple)) else []
    if not lista_base:
        return list(extra)
    sistema = sistema_texto(params)
    claves = parch.claves_sistema(sistema)
    fundidos = []
    for i in range(max(n_info, len(lista_base), len(extra))):
        b = parch.bloques_de_slide(lista_base[i] if i < len(lista_base) else "", sistema)
        e = parch.bloques_de_slide(extra[i] if i < len(extra) else "", sistema)
        unido = {c: (b.get(c) or e.get(c) or "") for c in claves}
        unido = {c: t for c, t in unido.items() if t}
        if not unido:
            continue
        # Con un solo bloque se devuelve el string de siempre: el contrato viejo sigue
        # siendo válido y no hay motivo para convertirlo en objeto por el camino.
        fundidos.append(unido[claves[0]] if list(unido) == claves[:1] else unido)
    return fundidos or None


# ── Reparación dirigida de los campos que faltaron ────────────────────────────
# El reintento anterior era un resample ciego: mismo prompt, misma temperatura, sin
# decirle al modelo qué se había saltado. Si la causa no era el muestreo sino el
# prompt (o un contenido que le costaba), los dos intentos fallaban igual — que es
# justo lo que reportaba el aviso ("el reintento automático tampoco lo resolvió").
# Esta segunda llamada pide SOLO lo que falta, con lo ya escrito como contexto: la
# salida es chica (no puede truncarse) y no re-escribe los captions.

_SPEC_REPARACION = {
    "linkedin_text": '"linkedin_text": the LinkedIn post in {lang}, 150-300 words, following the '
                     'LINKEDIN POST RULES and the COPY STRUCTURE section of the system prompt. '
                     '{url_li}',
    "instagram_text": '"instagram_text": the Instagram caption in {lang}, 80-150 words, following '
                      'the INSTAGRAM POST RULES and the COPY STRUCTURE section of the system '
                      'prompt. Max 5 hashtags, no raw URL.',
    "facebook_text": '"facebook_text": the Facebook post in {lang}, 80-180 words, following the '
                     'FACEBOOK POST RULES and the COPY STRUCTURE section of the system prompt.',
    "image_prompt":'"image_prompt": ONE 30-60 word still-photo scene for the cover, built on '
                    'the most visually concrete detail of the source (see IMAGE PROMPT rules).',
    "image_style": '"image_style": 20-40 words of art direction only — light, dominant material, '
                   'optics and finish. No subject, no palette, no color names. It is appended '
                   'verbatim to every image of the set.',
    "image_slide_prompts": '"image_slide_prompts": an array of EXACTLY {n_info} scenes, one per '
                           'info slide, 20-40 words each. Every slide gets its OWN hero object, '
                           'physically different from the cover\'s and from the other slides\', '
                           'while staying in the same visual world (same room, surfaces, '
                           'materials). Slide i must match the printed idea listed below.',
    "image_text.hook": '"image_text": {{"hook": "..."}} — ONE short complete cover phrase in '
                       '{lang}, max ~10 words, sentence case, no emojis or hashtags.',
    "image_text.slides": '"image_text": {{"slides": [...]}} — EXACTLY {n_info} objects in {lang}, '
                         'one per info slide, each carrying these keys and nothing else: '
                         '{bloques}. Everything is drawn faithfully from the source. They read as '
                         'ONE sequence: each slide advances the one before it and the last lands '
                         'the payoff (never a sign-off or a summary), and every body block says '
                         'something concrete the source actually states — a number, a name, a '
                         'mechanism, a step — not a rewording of its own headline.',
    "video_prompt": '"video_prompt": 40-80 words describing ONE continuous filmable scene — the '
                    'strongest single beat of the content.',
    "video_style": '"video_style": the 10-18 word look-lock (lens/film character, lighting '
                   'character, palette, mood). No subjects, no actions.',
    "video_storyboard": '"video_storyboard": an array of EXACTLY {n_shots} shot beats (25-50 '
                        'words each), chained as ONE continuous visual story.',
    "video_voiceover": '"video_voiceover": an array of EXACTLY {n_shots} spoken lines in {lang}, '
                       '{vo_lo}-{vo_hi} words each, flowing as one narration.',
}

# Los dos faltantes de `image_text` se piden en una sola clave del JSON.
_CLAVE_JSON = {"image_text.hook": "image_text", "image_text.slides": "image_text"}


def _contexto_ya_escrito(posts: dict, faltan: list[str], n_info: int) -> str:
    """Lo que el intento anterior SÍ entregó, para que lo nuevo encaje con ello."""
    lineas: list[str] = []
    # Los captions ya escritos entran enteros cuando se está reparando otro: la regla
    # de estructura dice que dos redes del mismo job no pueden compartir esqueleto ni
    # apertura, y sin ver el hermano el modelo no puede cumplirla.
    for campo in ("linkedin_text", "instagram_text", "facebook_text"):
        valor = str(posts.get(campo) or "").strip()
        if valor and campo not in faltan:
            lineas.append(f"- {campo} (already written — the post you write must NOT repeat its "
                          f"structure or its opening line):\n{valor}")
    img = posts.get("image_text") if isinstance(posts.get("image_text"), dict) else {}
    hook = (img.get("hook") or "").strip()
    if hook and "image_text.hook" not in faltan:
        lineas.append(f"- Cover printed text: {hook}")
    slides = [s for s in (img.get("slides") or []) if str(s).strip()]
    if slides and "image_text.slides" not in faltan:
        detalle = "\n".join(f"    slide {i + 1}: {s}" for i, s in enumerate(slides[:n_info]))
        lineas.append(f"- Printed text of each info slide:\n{detalle}")
    for campo in ("image_prompt", "image_style", "video_prompt", "video_style"):
        valor = (posts.get(campo) or "").strip()
        if valor and campo not in faltan:
            lineas.append(f"- {campo} (already written, keep the new fields consistent with it): "
                          f"{valor}")
    return "\n".join(lineas)


def _repair_user_message(content: dict, params: dict, posts: dict, faltan: list[str],
                         clean_url: str = "") -> str:
    """User message de la reparación: solo los campos que faltan, con su contexto."""
    lang = params.get("lang", "es")
    n_info = _info_slides_needed(params)
    n_shots = _segments_needed(params) if _wants_video(params) else 0
    vo_lo, vo_hi = _voiceover_word_budget(params)
    # La línea del CTA de LinkedIn depende de que exista URL de origen: sin ella el
    # modelo se inventaba un "mira el video" que no lleva a ninguna parte.
    url_li = (f'Include the raw source URL on its own line before the hashtags, with the exact '
              f'CTA prefix of the system prompt: {clean_url}' if (clean_url or "").strip()
              else 'There is NO source URL: do not add a URL line or a "watch the video" CTA.')
    # Los bloques que pide el sistema congelado, con su presupuesto: la reparación tiene
    # que pedir la MISMA forma que el primer intento o devolvería un slide que la pieza
    # no sabe imprimir.
    datos_sistema = parch.datos_sistema(sistema_texto(params))
    bloques = ", ".join(f"`{b['clave']}` ({b['palabras'][0]}-{b['palabras'][1]} words)"
                        for b in datos_sistema["bloques"])
    fmt = {"n_info": n_info, "n_shots": n_shots, "lang": lang, "vo_lo": vo_lo, "vo_hi": vo_hi,
           "url_li": url_li, "bloques": bloques}

    specs, claves = [], []
    for campo in faltan:
        spec = _SPEC_REPARACION.get(campo)
        if not spec:
            continue
        clave = _CLAVE_JSON.get(campo, campo)
        if clave not in claves:
            claves.append(clave)
        specs.append("- " + spec.format(**fmt))

    # Los captions que NO se están reparando siguen intactos: decirlo explícitamente es
    # lo que impide que el modelo reescriba de paso los que ya estaban bien. Cuando el
    # que falta ES un caption, esa prohibición no puede taparlo (era el bug: la línea
    # era fija y le prohibía devolver justo lo que se le venía a pedir).
    intocables = [c for c in ("linkedin_text", "instagram_text", "facebook_text")
                  if c not in faltan]
    no_tocar = (f"Do NOT return {', '.join(intocables)}: those captions are already written and "
                "must not change. " if intocables else "")

    ya = _contexto_ya_escrito(posts, faltan, n_info)
    transcript = _transcript_excerpt(content.get("transcript") or "")

    return f"""REPAIR REQUEST — this is not a new post.

A previous pass already wrote for the source below, but left these REQUIRED fields empty. They
are mandatory: an empty caption publishes an empty post, and an empty visual field means the
image gets built from the title instead of from the source — which is the exact failure this
request exists to fix.

WRITE EXACTLY THESE FIELDS, nothing else:
{chr(10).join(specs)}

OUTPUT FORMAT: ONLY valid JSON with these keys and no others — no markdown, no explanation:
{{{", ".join(f'"{k}": ...' for k in claves)}}}
{no_tocar}All visual prompts in ENGLISH (captions and video_voiceover in {lang}).
Every rule of the system prompt still applies: grounding in the source, physical plausibility,
no generic stock scenes, no text inside the image, hands are never the subject.

{"ALREADY WRITTEN FOR THIS PIECE (make the new fields fit it):" + chr(10) + ya if ya else ""}

TITLE: {content.get("title", "")}

DESCRIPTION: {(content.get("description") or "")[:500]}

TRANSCRIPT / TEXT{" (excerpt: beginning, middle and end; [...] marks a gap)" if _is_excerpt(content) else ""}:
{transcript}

{"[Note: transcript is empty — use title + description only]" if not transcript.strip() else ""}
"""


async def _reparar(content: dict, params: dict, posts: dict, faltan: list[str],
                   cfg, clean_url: str = "") -> tuple[dict, dict | None]:
    """Pide al LLM SOLO los campos faltantes. Devuelve `(parcial, usage)`.

    La respuesta va a una cola muerta: la UI ya pintó el texto del primer intento y
    un segundo bloque encima se leería como dos posts pegados.
    """
    overlay = bool(getattr(cfg, "image_text_in_prompt", True))
    system = _system_prompt(overlay)
    user = _repair_user_message(content, params, posts, faltan, clean_url)
    muerta: asyncio.Queue = asyncio.Queue()
    if cfg.llm_provider == "perplexity":
        model = _perplexity_model(params)
        raw, usage = await _call_perplexity(system, user, muerta, cfg.perplexity_api_key, model)
        return _sanitize_posts(_parse_raw(raw)), _perplexity_usage(usage, model)
    raw, usage = await _call_anthropic(system, user, muerta, cfg.anthropic_api_key)
    return _sanitize_posts(_parse_raw(raw)), _anthropic_usage(usage)


def _merge_usage(a: dict | None, b: dict | None) -> dict | None:
    """Suma el consumo de dos llamadas al escritor: el reintento también se paga."""
    if not a or not b:
        return a or b
    units = dict(a.get("units") or {})
    for k, v in (b.get("units") or {}).items():
        try:
            units[k] = int(units.get(k, 0)) + int(v)
        except (TypeError, ValueError):
            pass
    return {**a, "units": units}


def _avisos_escritura(faltan: list[str], diagnostico: list) -> list[dict]:
    """Aviso para la compuerta previa cuando la escritura quedó incompleta.

    Mismo shape que los de `prompt_lint` (`campo`/`nivel`/`mensaje`) para que las dos
    revisiones —el preview del individual y el editor por fila del lote— lo pinten sin
    cambiar nada. Explica la CAUSA; el lint de abajo ya describe la consecuencia.
    """
    if not faltan:
        return []
    if "json_malformado" in diagnostico:
        causa = ("el JSON que devolvió el modelo llegó roto y solo se pudieron rescatar los "
                 "textos de los posts")
    else:
        causa = "el modelo los devolvió vacíos"
    # El aviso nombra lo que de verdad se perdió: un caption vacío no es "un campo
    # visual", es un post que se publicaría en blanco, y decirlo mal manda a buscar
    # el hueco al formulario equivocado.
    captions = [c for c in faltan if c.endswith("_text")]
    if captions and len(captions) == len(faltan):
        que = f"{len(captions)} caption(s)"
    elif captions:
        que = f"{len(captions)} caption(s) y {len(faltan) - len(captions)} campo(s) visual(es)"
    else:
        que = f"{len(faltan)} campo(s) visual(es)"
    return [{
        "campo": "escritura", "nivel": "alto",
        "mensaje": f"La escritura no entregó {que} "
                   f"({', '.join(faltan)}): {causa}, y el reintento automático tampoco lo "
                   "resolvió. Usa «Reintentar escritura» para volver a pedírselos al modelo, "
                   "o escríbelos a mano acá abajo antes de generar.",
    }]


async def _completar_faltantes(content: dict, params: dict, posts: dict, faltan: list[str],
                               cfg, clean_url: str = "") -> tuple[list[str], dict | None]:
    """Pide lo que falta y lo FUNDE sobre `posts`. Devuelve `(lo que sigue faltando, usage)`.

    Nunca lanza: la reparación es una mejora, no un requisito — si el proveedor falla,
    se conserva intacto lo que ya había y el aviso sale igual.
    """
    print(f"   [aviso] La escritura no entregó {', '.join(faltan)}: se piden esos campos.")
    try:
        parcial, usage = await _reparar(content, params, posts, faltan, cfg, clean_url)
    except Exception as e:  # noqa: BLE001
        print(f"   [aviso] La reparación de la escritura falló: {e}")
        return faltan, None
    _merge_posts(posts, parcial, params)
    quedan = _faltantes(posts, params)
    if quedan:
        print(f"   [aviso] Tras la reparación siguen faltando: {', '.join(quedan)}.")
    else:
        print("   [ok] La reparación completó todos los campos que faltaban.")
    return quedan, usage


async def write_posts(content: dict, params: dict, clean_url: str, queue: asyncio.Queue,
                      cfg) -> tuple[dict, dict | None, list[dict]]:
    """Escribe los posts y devuelve `(posts, usage, avisos)`.

    `usage` es `{service, model, units}` para el tracking de costos (o `None` si el
    proveedor no reportó consumo). El núcleo del pipeline lo pasa a `record_event`.
    `avisos` es lo que la compuerta previa debe mostrar si la escritura quedó
    incompleta (lista vacía cuando el LLM cumplió el contrato, que es lo normal).

    Si el primer intento se saltó campos visuales, la segunda llamada NO repite el
    encargo entero: pide solo lo que falta (`_reparar`) y lo funde campo a campo
    sobre lo ya escrito, así los captions y lo que sí llegó nunca se pierden.
    """
    provider = cfg.llm_provider  # raises if neither key is set
    # ¿La imagen va a llevar texto? Cambia cómo se le pide componer al modelo
    # (reservar bandas calmas para el tipo vs. llenar el cuadro). Lo renderiza
    # Higgsfield desde el prompt (`image_text_in_prompt`); el paso viejo de dibujarlo
    # con Pillow después ya no existe.
    overlay = bool(getattr(cfg, "image_text_in_prompt", True))

    diag: list = []
    if provider == "perplexity":
        posts, usage = await _write_with_perplexity(content, params, clean_url, queue,
                                                    cfg.perplexity_api_key,
                                                    text_overlay=overlay, diagnostico=diag)
    else:
        posts, usage = await _write_with_anthropic(content, params, clean_url, queue,
                                                   cfg.anthropic_api_key,
                                                   text_overlay=overlay, diagnostico=diag)

    faltan = _faltantes(posts, params)
    if faltan:
        faltan, usage_rep = await _completar_faltantes(content, params, posts, faltan, cfg,
                                                      clean_url)
        usage = _merge_usage(usage, usage_rep)

    return _align_video_script(posts, params), usage, _avisos_escritura(faltan, diag)


async def rewrite_posts(content: dict, params: dict, posts: dict, cfg,
                        clean_url: str = "") -> tuple[dict, dict | None, list[dict]]:
    """Reintento MANUAL desde la compuerta previa: vuelve a pedir lo que falta.

    Es el mismo camino correctivo que usa `write_posts`, expuesto para que las dos
    revisiones previas (el preview del individual y el editor por fila del lote)
    puedan repetirlo a mano sin relanzar el post entero ni perder lo ya editado.
    Devuelve `(posts, usage, avisos)` con `posts` ya fundido.
    """
    faltan = _faltantes(posts, params)
    if not faltan:
        return _align_video_script(posts, params), None, []
    faltan, usage = await _completar_faltantes(content, params, posts, faltan, cfg, clean_url)
    return _align_video_script(posts, params), usage, _avisos_escritura(faltan, [])
