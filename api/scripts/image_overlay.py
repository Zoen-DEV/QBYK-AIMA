"""
Image overlay - renders text over a base image (a provider URL or a local
template path).

Used by the repurpose-youtube-video skill to produce Instagram carousels with the
fixed 3-slide format (Hook / Info / Credits), Instagram single images with a
title overlay, and a LinkedIn 4:5 image with a hook overlay.

Dependencies:
  Pillow  ->  python -m pip install Pillow

Font resolution order (per call, given a `tone`):
  1. OVERLAY_FONT_PATH env var (if set, must point to a .ttf/.otf) — a user
     override always wins, regardless of tone.
  2. font.ttf / font-bold.ttf next to this script (drop-in override)
  3. Tone mapping (embedded OFL fonts in assets/fonts/):
     - educativo / personal  -> Montserrat (variable, pinned 400/700)
     - inspiracional         -> Poppins (static Regular/Bold)
  4. Platform defaults:
     - Windows : C:\\Windows\\Fonts\\arialbd.ttf  /  arial.ttf
     - macOS   : /System/Library/Fonts/Helvetica.ttc
     - Linux   : /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf  /  DejaVuSans.ttf
  5. Pillow's default bitmap font (last resort - looks rough, prints a warning)

Public API (all accept an optional `tone` that selects the typeface):
  render_hook(base_url, title, *, lang="es", tone="educativo") -> bytes                    (IG carousel slide 1, 1080x1080)
  render_info(base_url, body_lines, *, lang="es", tone="educativo") -> bytes               (IG carousel slide 2, 1080x1080)
  render_credits(base_url, channel, video_title, *, lang="es", tone="educativo") -> bytes  (IG carousel slide 3, 1080x1080)
  render_single(base_url, title, *, lang="es", tone="inspiracional") -> bytes              (IG single image, 1080x1080)
  render_linkedin_hook(base_url, title, *, lang="es", tone="educativo") -> bytes           (LinkedIn 4:5, 1080x1350)
  render_story(base_url, title, *, lang="es", tone="inspiracional") -> bytes               (IG Story 9:16, 1080x1920)

All renderers return PNG bytes. Pass them to bc.upload_media_local() to get a
Blotato-hosted public URL usable in mediaUrls.
"""

import io
import os
import sys
import urllib.request
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    raise RuntimeError(
        "[error] Pillow no está instalado. Ejecuta: python -m pip install Pillow"
    ) from e


# ── Font resolution ────────────────────────────────────────────────────────────
#
# A font is chosen per call by TONE (the objective never touches typography):
#   - educativo / personal -> Montserrat   (variable TTF, weight pinned 400/700)
#   - inspiracional        -> Poppins      (static Regular / Bold)
# A user override (OVERLAY_FONT_PATH or a drop-in font.ttf/font-bold.ttf) always
# wins, regardless of tone.

_FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"

# Each entry: (path, variation_weight_or_None). A variation_weight means the file
# is a variable font and Pillow must pin that weight axis at render time.
_TONE_FONTS: dict[str, dict[bool, tuple[Path, int | None]]] = {
    "montserrat": {
        False: (_FONTS_DIR / "Montserrat-Variable.ttf", 400),
        True: (_FONTS_DIR / "Montserrat-Variable.ttf", 700),
    },
    "poppins": {
        False: (_FONTS_DIR / "Poppins-Regular.ttf", None),
        True: (_FONTS_DIR / "Poppins-Bold.ttf", None),
    },
}

# Tone -> embedded family. Unknown tones fall back to Montserrat (neutral/pro).
_TONE_TO_FAMILY = {
    "educativo": "montserrat",
    "personal": "montserrat",
    "inspiracional": "poppins",
}

# Cache key includes family/weight so different tones don't collide.
_FONT_CACHE: dict[tuple[str, bool, int], ImageFont.ImageFont] = {}
_DEFAULT_WARNED = False


def _override_paths(bold: bool) -> list[Path]:
    """User overrides that win over the tone mapping (returned in priority order)."""
    here = Path(__file__).parent
    paths: list[Path] = []
    override = os.environ.get("OVERLAY_FONT_PATH", "").strip()
    if override:
        paths.append(Path(override))
    paths.append(here / ("font-bold.ttf" if bold else "font.ttf"))
    return paths


def _system_paths(bold: bool) -> list[Path]:
    """Last-resort platform fonts (used only if embedded + overrides all fail)."""
    if sys.platform.startswith("win"):
        winfonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        return [
            winfonts / ("arialbd.ttf" if bold else "arial.ttf"),
            winfonts / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        ]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts/Helvetica.ttc")]
    return [
        Path("/usr/share/fonts/truetype/dejavu/" + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")),
        Path("/usr/share/fonts/truetype/liberation/" + ("LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf")),
    ]


def _try_load(path: Path, size: int, weight: int | None) -> ImageFont.FreeTypeFont | None:
    """Load `path` at `size`; pin the variable-font weight axis if `weight` is set."""
    if not path.exists():
        return None
    try:
        font = ImageFont.truetype(str(path), size=size)
    except (OSError, ValueError):
        return None
    if weight is not None:
        try:
            font.set_variation_by_axes([weight])
        except (OSError, ValueError, AttributeError):
            # Not a variable font (or weight axis missing) — use as-is.
            pass
    return font


def _load_font(size: int, *, bold: bool, tone: str = "educativo") -> ImageFont.ImageFont:
    """Resolve a font for `size`/`bold`, biased by `tone` (Montserrat vs Poppins).

    A user override always wins; then the tone-mapped embedded family; then the
    system font; finally Pillow's bitmap default.
    """
    global _DEFAULT_WARNED
    family = _TONE_TO_FAMILY.get((tone or "").lower(), "montserrat")
    key = (family, bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    # 1. User overrides (no weight pinning — assumed a complete face).
    for path in _override_paths(bold):
        font = _try_load(path, size, None)
        if font is not None:
            _FONT_CACHE[key] = font
            return font

    # 2. Tone-mapped embedded family (Montserrat is variable -> pin the weight).
    emb_path, weight = _TONE_FONTS[family][bold]
    font = _try_load(emb_path, size, weight)
    if font is not None:
        _FONT_CACHE[key] = font
        return font

    # 3. Platform fonts.
    for path in _system_paths(bold):
        font = _try_load(path, size, None)
        if font is not None:
            _FONT_CACHE[key] = font
            return font

    if not _DEFAULT_WARNED:
        print("[aviso] No se encontró ninguna fuente embebida ni del sistema — usando fuente bitmap por defecto (calidad reducida).")
        _DEFAULT_WARNED = True
    return ImageFont.load_default()


# ── Helpers ────────────────────────────────────────────────────────────────────

_TIMEOUT_SECS = 30

# Browser User-Agent: some image hosts (e.g. Higgsfield behind Cloudflare) reject
# urllib's default UA. Only used for remote URLs; local template paths skip it.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _is_local_path(src: str) -> bool:
    """True if `src` is a local filesystem path rather than an http(s) URL.

    Template fallbacks pass a local .png path; Higgsfield passes a URL.
    """
    return not src.lower().startswith(("http://", "https://"))


def _fetch_base(url: str, target_size: tuple[int, int] = (1080, 1080)) -> Image.Image:
    """Load the base image and return it as an RGB Pillow image of `target_size`.

    `url` may be an http(s) URL (provider output) or a local filesystem path
    (template fallback). Defaults to 1080x1080 (IG square). Pass (1080, 1350)
    for LinkedIn 4:5. Center-crops to preserve composition.
    """
    if _is_local_path(url):
        img = Image.open(url).convert("RGB")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECS) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = target_size
    w, h = img.size
    if (w, h) != (tw, th):
        scale = max(tw / w, th / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
        left = (nw - tw) // 2
        top = (nh - th) // 2
        img = img.crop((left, top, left + tw, top + th))
    return img


def _add_gradient(img: Image.Image, *, position: str = "bottom", strength: int = 200) -> Image.Image:
    """Overlay a black-to-transparent gradient so text reads cleanly.

    position: "bottom" | "top" | "full" | "center"
    strength: max alpha (0-255). 200 is strong but not opaque.
    """
    w, h = img.size
    grad = Image.new("L", (1, h), 0)
    px = grad.load()
    for y in range(h):
        t = y / (h - 1)
        if position == "bottom":
            alpha = int(strength * (t ** 1.6))
        elif position == "top":
            alpha = int(strength * ((1 - t) ** 1.6))
        elif position == "center":
            alpha = int(strength * (1 - abs(t - 0.5) * 2) ** 1.6)
        else:  # full
            alpha = strength
        px[0, y] = alpha
    grad = grad.resize((w, h), Image.LANCZOS)
    black = Image.new("RGB", (w, h), (0, 0, 0))
    img = img.copy()
    img.paste(black, (0, 0), grad)
    return img


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Word-wrap `text` to fit within `max_width` pixels for the given font."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _draw_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    *,
    xy: tuple[int, int],
    line_spacing: int = 12,
    fill=(255, 255, 255),
    align: str = "left",
    max_width: int | None = None,
) -> int:
    """Draw `lines` starting at xy. Returns the y-coordinate after the block."""
    x, y = xy
    for line in lines:
        if align == "center" and max_width is not None:
            line_w = draw.textlength(line, font=font)
            draw_x = x + (max_width - line_w) / 2
        else:
            draw_x = x
        draw.text((draw_x, y), line, font=font, fill=fill)
        bbox = font.getbbox(line)
        y += (bbox[3] - bbox[1]) + line_spacing
    return y


def _to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Slide renderers (IG 1080x1080) ─────────────────────────────────────────────
#
# Layout convention:
#   - Safe margin of 80px on each side
#   - Hook = big title centered + small subline
#   - Info = stacked bullet points with a small heading
#   - Credits = "Video original" + channel + handle, centered
#   - Single = title at bottom over gradient

_MARGIN = 80
_INNER_W = 1080 - 2 * _MARGIN  # 920


def render_hook(base_url: str, title: str, *, lang: str = "es", tone: str = "educativo") -> bytes:
    """Slide 1 - bold title centered, minimal subline."""
    img = _fetch_base(base_url)
    img = _add_gradient(img, position="full", strength=140)
    draw = ImageDraw.Draw(img)

    font_size = 82
    title_font = _load_font(font_size, bold=True, tone=tone)
    subline_font = _load_font(34, bold=False, tone=tone)

    title_lines = _wrap(title, title_font, _INNER_W, draw)
    while len(title_lines) > 4 and font_size > 50:
        font_size -= 6
        title_font = _load_font(font_size, bold=True, tone=tone)
        title_lines = _wrap(title, title_font, _INNER_W, draw)

    total_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 14
    start_y = (1080 - total_h) // 2 - 30
    _draw_block(draw, title_lines, title_font, xy=(_MARGIN, start_y), line_spacing=14, align="center", max_width=_INNER_W)

    # "»" y no "→": Poppins (tono inspiracional) no trae el glifo U+2192 y rendía
    # un tofu; "»" existe en las dos fuentes embebidas (Montserrat y Poppins).
    subline = "Desliza »" if lang == "es" else "Swipe »"
    sub_w = draw.textlength(subline, font=subline_font)
    draw.text(((1080 - sub_w) / 2, 1080 - _MARGIN - 50), subline, font=subline_font, fill=(255, 255, 255))

    return _to_png_bytes(img)


def render_info(base_url: str, text, *, lang: str = "es", tone: str = "educativo") -> bytes:
    """Info slide - ONE big idea, vertically centered, lots of air.

    `text` is a single closed sentence (a string). For backwards compatibility it
    also accepts a list of strings, in which case they're joined into one idea.
    No repeated "DE QUÉ VA"/"THE IDEA" heading (it screamed "template"); a thin
    accent rule under the text gives a clean structural cue instead.
    """
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t).strip() for t in text if str(t).strip())
    text = (text or "").strip()

    img = _fetch_base(base_url)
    img = _add_gradient(img, position="center", strength=180)
    draw = ImageDraw.Draw(img)

    # One large statement. Scale the font down only if it would overflow the height.
    font_size = 72
    body_font = _load_font(font_size, bold=True, tone=tone)
    lines = _wrap(text, body_font, _INNER_W, draw)
    while len(lines) > 5 and font_size > 46:
        font_size -= 6
        body_font = _load_font(font_size, bold=True, tone=tone)
        lines = _wrap(text, body_font, _INNER_W, draw)

    line_h = body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]
    line_spacing = 18
    text_h = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing

    # Vertically center the whole block (text + accent rule).
    rule_gap = 40
    rule_h = 6
    total_h = text_h + rule_gap + rule_h
    y = (1080 - total_h) // 2

    for line in lines:
        line_w = draw.textlength(line, font=body_font)
        draw.text(((1080 - line_w) / 2, y), line, font=body_font, fill=(255, 255, 255))
        y += line_h + line_spacing

    # Thin centered accent rule (structural cue without a repeated label).
    y += rule_gap - line_spacing
    rule_w = 90
    draw.rectangle([(1080 - rule_w) / 2, y, (1080 + rule_w) / 2, y + rule_h], fill=(255, 200, 90))

    return _to_png_bytes(img)


def render_credits(base_url: str, channel: str, video_title: str, *, lang: str = "es", tone: str = "educativo") -> bytes:
    """Slide 3 - source attribution centered."""
    img = _fetch_base(base_url)
    img = _add_gradient(img, position="full", strength=170)
    draw = ImageDraw.Draw(img)

    label_font = _load_font(34, bold=False, tone=tone)
    font_size = 56
    title_font = _load_font(font_size, bold=True, tone=tone)
    channel_font = _load_font(46, bold=True, tone=tone)
    cta_font = _load_font(36, bold=False, tone=tone)

    label = "VIDEO ORIGINAL" if lang == "es" else "ORIGINAL VIDEO"
    # Sin el emoji 🔗: ninguna de las fuentes embebidas trae glifos emoji (tofu).
    cta = "Link en bio" if lang == "es" else "Link in bio"

    # Compute block height first to center vertically
    title_lines = _wrap(video_title, title_font, _INNER_W, draw)
    while len(title_lines) > 4 and font_size > 36:
        font_size -= 4
        title_font = _load_font(font_size, bold=True, tone=tone)
        title_lines = _wrap(video_title, title_font, _INNER_W, draw)

    label_h = label_font.getbbox(label)[3] - label_font.getbbox(label)[1]
    title_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 12
    channel_h = channel_font.getbbox(channel)[3] - channel_font.getbbox(channel)[1]
    cta_h = cta_font.getbbox(cta)[3] - cta_font.getbbox(cta)[1]
    gap = 40
    total = label_h + gap + title_h + gap + channel_h + gap * 2 + cta_h
    y = (1080 - total) // 2

    # Label
    w = draw.textlength(label, font=label_font)
    draw.text(((1080 - w) / 2, y), label, font=label_font, fill=(220, 220, 220))
    y += label_h + gap

    # Title (centered, wrapped)
    y = _draw_block(draw, title_lines, title_font, xy=(_MARGIN, y), line_spacing=12, align="center", max_width=_INNER_W)
    y += gap - 12

    # Channel name
    w = draw.textlength(channel, font=channel_font)
    draw.text(((1080 - w) / 2, y), channel, font=channel_font, fill=(255, 200, 90))
    y += channel_h + gap * 2

    # CTA
    w = draw.textlength(cta, font=cta_font)
    draw.text(((1080 - w) / 2, y), cta, font=cta_font, fill=(255, 255, 255))

    return _to_png_bytes(img)


def render_single(base_url: str, title: str, *, lang: str = "es", tone: str = "inspiracional") -> bytes:
    """Instagram single image - title overlay at the bottom over a gradient."""
    img = _fetch_base(base_url)
    img = _add_gradient(img, position="bottom", strength=210)
    draw = ImageDraw.Draw(img)

    font_size = 68
    title_font = _load_font(font_size, bold=True, tone=tone)
    title_lines = _wrap(title, title_font, _INNER_W, draw)
    while len(title_lines) > 3 and font_size > 44:
        font_size -= 4
        title_font = _load_font(font_size, bold=True, tone=tone)
        title_lines = _wrap(title, title_font, _INNER_W, draw)

    total_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 12
    start_y = 1080 - _MARGIN - total_h - 10
    _draw_block(draw, title_lines, title_font, xy=(_MARGIN, start_y), line_spacing=12, align="left", max_width=_INNER_W)

    return _to_png_bytes(img)


# ── LinkedIn renderer (1080x1350, 4:5) ─────────────────────────────────────────

_LI_W = 1080
_LI_H = 1350
_LI_INNER_W = _LI_W - 2 * _MARGIN  # 920


def render_linkedin_hook(base_url: str, title: str, *, lang: str = "es", tone: str = "educativo") -> bytes:
    """LinkedIn 4:5 image with hook overlay at the bottom over a gradient.

    The 4:5 canvas is taller than 1:1, so the gradient covers a larger bottom
    band and the title sits in that band with strong contrast.
    """
    img = _fetch_base(base_url, target_size=(_LI_W, _LI_H))
    img = _add_gradient(img, position="bottom", strength=215)
    draw = ImageDraw.Draw(img)

    font_size = 72
    title_font = _load_font(font_size, bold=True, tone=tone)
    title_lines = _wrap(title, title_font, _LI_INNER_W, draw)
    while len(title_lines) > 4 and font_size > 46:
        font_size -= 4
        title_font = _load_font(font_size, bold=True, tone=tone)
        title_lines = _wrap(title, title_font, _LI_INNER_W, draw)

    total_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 14
    start_y = _LI_H - _MARGIN - total_h - 20
    _draw_block(draw, title_lines, title_font, xy=(_MARGIN, start_y), line_spacing=14, align="left", max_width=_LI_INNER_W)

    return _to_png_bytes(img)


# ── Instagram Story renderer (1080x1920, 9:16) ─────────────────────────────────

_STORY_W = 1080
_STORY_H = 1920
_STORY_INNER_W = _STORY_W - 2 * _MARGIN  # 920


def render_story(base_url: str, title: str, *, lang: str = "es", tone: str = "inspiracional") -> bytes:
    """Instagram Story (9:16) with a hook overlay over a full gradient.

    The 9:16 canvas is taller than a feed image; the title sits in the lower third
    (above the safe area for IG's UI) with a strong gradient for contrast.
    """
    img = _fetch_base(base_url, target_size=(_STORY_W, _STORY_H))
    img = _add_gradient(img, position="bottom", strength=215)
    draw = ImageDraw.Draw(img)

    font_size = 80
    title_font = _load_font(font_size, bold=True, tone=tone)
    title_lines = _wrap(title, title_font, _STORY_INNER_W, draw)
    while len(title_lines) > 5 and font_size > 50:
        font_size -= 4
        title_font = _load_font(font_size, bold=True, tone=tone)
        title_lines = _wrap(title, title_font, _STORY_INNER_W, draw)

    total_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 16
    # Lower third, leaving ~300px safe margin from the bottom (IG story UI / CTA).
    start_y = _STORY_H - 320 - total_h
    _draw_block(draw, title_lines, title_font, xy=(_MARGIN, start_y), line_spacing=16, align="left", max_width=_STORY_INNER_W)

    return _to_png_bytes(img)
