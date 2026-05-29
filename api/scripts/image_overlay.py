"""
Image overlay - renders text over a base image (the Pollinations output).

Used by the repurpose-youtube-video skill to produce Instagram carousels with the
fixed 3-slide format (Hook / Info / Credits), Instagram single images with a
title overlay, and a LinkedIn 4:5 image with a hook overlay.

Dependencies:
  Pillow  ->  python -m pip install Pillow

Font resolution order:
  1. OVERLAY_FONT_PATH env var (if set, must point to a .ttf/.otf)
  2. font.ttf / font-bold.ttf next to this script (drop-in override)
  3. Platform defaults:
     - Windows : C:\\Windows\\Fonts\\arialbd.ttf  /  arial.ttf
     - macOS   : /System/Library/Fonts/Helvetica.ttc
     - Linux   : /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf  /  DejaVuSans.ttf
  4. Pillow's default bitmap font (last resort - looks rough, prints a warning)

Public API:
  render_hook(base_url, title, *, lang="es") -> bytes                    (IG carousel slide 1, 1080x1080)
  render_info(base_url, body_lines, *, lang="es") -> bytes               (IG carousel slide 2, 1080x1080)
  render_credits(base_url, channel, video_title, *, lang="es") -> bytes  (IG carousel slide 3, 1080x1080)
  render_single(base_url, title, *, lang="es") -> bytes                  (IG single image, 1080x1080)
  render_linkedin_hook(base_url, title, *, lang="es") -> bytes           (LinkedIn 4:5, 1080x1350)

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

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_DEFAULT_WARNED = False


def _candidate_font_paths(bold: bool) -> list[Path]:
    here = Path(__file__).parent
    override = os.environ.get("OVERLAY_FONT_PATH", "").strip()
    paths: list[Path] = []
    if override:
        paths.append(Path(override))
    paths.append(here / ("font-bold.ttf" if bold else "font.ttf"))

    if sys.platform.startswith("win"):
        winfonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        paths += [
            winfonts / ("arialbd.ttf" if bold else "arial.ttf"),
            winfonts / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        ]
    elif sys.platform == "darwin":
        paths += [Path("/System/Library/Fonts/Helvetica.ttc")]
    else:
        paths += [
            Path("/usr/share/fonts/truetype/dejavu/" + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")),
            Path("/usr/share/fonts/truetype/liberation/" + ("LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf")),
        ]
    return paths


def _load_font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    global _DEFAULT_WARNED
    key = ("bold" if bold else "reg", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    for path in _candidate_font_paths(bold):
        try:
            if path.exists():
                font = ImageFont.truetype(str(path), size=size)
                _FONT_CACHE[key] = font
                return font
        except (OSError, ValueError):
            continue
    if not _DEFAULT_WARNED:
        print("[aviso] No se encontró ninguna fuente del sistema — usando fuente bitmap por defecto (calidad reducida).")
        _DEFAULT_WARNED = True
    return ImageFont.load_default()


# ── Helpers ────────────────────────────────────────────────────────────────────

_TIMEOUT_SECS = 30

# Browser User-Agent: some image hosts (e.g. Higgsfield behind Cloudflare) reject
# urllib's default UA. Harmless for Pollinations.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _fetch_base(url: str, target_size: tuple[int, int] = (1080, 1080)) -> Image.Image:
    """Download the base image and return it as an RGB Pillow image of `target_size`.

    Defaults to 1080x1080 (IG square). Pass (1080, 1350) for LinkedIn 4:5.
    Center-crops to preserve composition.
    """
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


def render_hook(base_url: str, title: str, *, lang: str = "es") -> bytes:
    """Slide 1 - bold title centered, minimal subline."""
    img = _fetch_base(base_url)
    img = _add_gradient(img, position="full", strength=140)
    draw = ImageDraw.Draw(img)

    font_size = 82
    title_font = _load_font(font_size, bold=True)
    subline_font = _load_font(34, bold=False)

    title_lines = _wrap(title, title_font, _INNER_W, draw)
    while len(title_lines) > 4 and font_size > 50:
        font_size -= 6
        title_font = _load_font(font_size, bold=True)
        title_lines = _wrap(title, title_font, _INNER_W, draw)

    total_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 14
    start_y = (1080 - total_h) // 2 - 30
    _draw_block(draw, title_lines, title_font, xy=(_MARGIN, start_y), line_spacing=14, align="center", max_width=_INNER_W)

    subline = "Desliza →" if lang == "es" else "Swipe →"
    sub_w = draw.textlength(subline, font=subline_font)
    draw.text(((1080 - sub_w) / 2, 1080 - _MARGIN - 50), subline, font=subline_font, fill=(255, 255, 255))

    return _to_png_bytes(img)


def render_info(base_url: str, body_lines: list[str], *, lang: str = "es", heading: str | None = None) -> bytes:
    """Slide 2 - small heading + 2-3 short sentences (paragraph, centered, no bullets)."""
    img = _fetch_base(base_url)
    img = _add_gradient(img, position="full", strength=160)
    draw = ImageDraw.Draw(img)

    head_font = _load_font(36, bold=True)
    body_font = _load_font(44, bold=False)

    if heading is None:
        heading = "DE QUÉ VA" if lang == "es" else "THE IDEA"

    # Wrap all sentences into display lines first so we can vertically center the block
    all_display_lines: list[tuple[list[str], bool]] = []  # (wrapped_lines, is_first_sentence)
    for i, raw in enumerate(body_lines[:3]):
        wrapped = _wrap(raw.strip(), body_font, _INNER_W, draw)
        if wrapped:
            all_display_lines.append((wrapped, i == 0))

    line_h = body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]
    sentence_gap = 28  # extra vertical space between sentences
    line_spacing = 10

    total_body_h = 0
    for wrapped, _ in all_display_lines:
        total_body_h += len(wrapped) * (line_h + line_spacing) + sentence_gap
    total_body_h -= sentence_gap  # no trailing gap

    head_h = head_font.getbbox(heading)[3] - head_font.getbbox(heading)[1]
    head_gap = 56
    total_h = head_h + head_gap + total_body_h

    # Start centered on canvas
    y = (1080 - total_h) // 2

    # Heading
    head_w = draw.textlength(heading, font=head_font)
    draw.text(((1080 - head_w) / 2, y), heading, font=head_font, fill=(220, 220, 220))
    y += head_h + head_gap

    # Sentences — centered, first sentence in accent color
    for idx, (wrapped, is_first) in enumerate(all_display_lines):
        fill = (255, 200, 90) if is_first else (255, 255, 255)
        for line in wrapped:
            line_w = draw.textlength(line, font=body_font)
            draw.text(((1080 - line_w) / 2, y), line, font=body_font, fill=fill)
            y += line_h + line_spacing
        if idx < len(all_display_lines) - 1:
            y += sentence_gap

    return _to_png_bytes(img)


def render_credits(base_url: str, channel: str, video_title: str, *, lang: str = "es") -> bytes:
    """Slide 3 - source attribution centered."""
    img = _fetch_base(base_url)
    img = _add_gradient(img, position="full", strength=170)
    draw = ImageDraw.Draw(img)

    label_font = _load_font(34, bold=False)
    font_size = 56
    title_font = _load_font(font_size, bold=True)
    channel_font = _load_font(46, bold=True)
    cta_font = _load_font(36, bold=False)

    label = "VIDEO ORIGINAL" if lang == "es" else "ORIGINAL VIDEO"
    cta = "Link en bio 🔗" if lang == "es" else "Link in bio 🔗"

    # Compute block height first to center vertically
    title_lines = _wrap(video_title, title_font, _INNER_W, draw)
    while len(title_lines) > 4 and font_size > 36:
        font_size -= 4
        title_font = _load_font(font_size, bold=True)
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


def render_single(base_url: str, title: str, *, lang: str = "es") -> bytes:
    """Instagram single image - title overlay at the bottom over a gradient."""
    img = _fetch_base(base_url)
    img = _add_gradient(img, position="bottom", strength=210)
    draw = ImageDraw.Draw(img)

    font_size = 68
    title_font = _load_font(font_size, bold=True)
    title_lines = _wrap(title, title_font, _INNER_W, draw)
    while len(title_lines) > 3 and font_size > 44:
        font_size -= 4
        title_font = _load_font(font_size, bold=True)
        title_lines = _wrap(title, title_font, _INNER_W, draw)

    total_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 12
    start_y = 1080 - _MARGIN - total_h - 10
    _draw_block(draw, title_lines, title_font, xy=(_MARGIN, start_y), line_spacing=12, align="left", max_width=_INNER_W)

    return _to_png_bytes(img)


# ── LinkedIn renderer (1080x1350, 4:5) ─────────────────────────────────────────

_LI_W = 1080
_LI_H = 1350
_LI_INNER_W = _LI_W - 2 * _MARGIN  # 920


def render_linkedin_hook(base_url: str, title: str, *, lang: str = "es") -> bytes:
    """LinkedIn 4:5 image with hook overlay at the bottom over a gradient.

    The 4:5 canvas is taller than 1:1, so the gradient covers a larger bottom
    band and the title sits in that band with strong contrast.
    """
    img = _fetch_base(base_url, target_size=(_LI_W, _LI_H))
    img = _add_gradient(img, position="bottom", strength=215)
    draw = ImageDraw.Draw(img)

    font_size = 72
    title_font = _load_font(font_size, bold=True)
    title_lines = _wrap(title, title_font, _LI_INNER_W, draw)
    while len(title_lines) > 4 and font_size > 46:
        font_size -= 4
        title_font = _load_font(font_size, bold=True)
        title_lines = _wrap(title, title_font, _LI_INNER_W, draw)

    total_h = sum((title_font.getbbox(l)[3] - title_font.getbbox(l)[1]) for l in title_lines) + (len(title_lines) - 1) * 14
    start_y = _LI_H - _MARGIN - total_h - 20
    _draw_block(draw, title_lines, title_font, xy=(_MARGIN, start_y), line_spacing=14, align="left", max_width=_LI_INNER_W)

    return _to_png_bytes(img)
