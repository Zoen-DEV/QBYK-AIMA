"""
Pollinations.ai client - image generation for repurpose-youtube-video skill.
No API key required. Uses FLUX models via Pollinations.ai.

URL pattern:
  https://image.pollinations.ai/prompt/{encoded_prompt}?width=W&height=H&nologo=true&model=flux

The URL is a direct image URL. Fetching it triggers generation on first request
(~5-30s); subsequent requests hit cache and are instant.

Aspect ratios in use:
  "square_1_1"      -> 1080x1080  (Instagram single + carousel slides)
  "social_post_4_5" -> 1080x1350  (LinkedIn 4:5 vertical)
"""

import urllib.error
import urllib.parse
import urllib.request

BASE = "https://image.pollinations.ai/prompt"

_SIZES: dict[str, tuple[int, int]] = {
    "square_1_1": (1080, 1080),
    "social_post_4_5": (1080, 1350),
    "widescreen_16_9": (1920, 1080),
}

# Pollinations can take up to 60s on a cold prompt; image_overlay._TIMEOUT_SECS=30
# so we pre-fetch here to warm the cache before overlay tries to download.
_PREFETCH_TIMEOUT = 90


def _build_url(prompt: str, width: int, height: int, *, seed: int | None = None, model: str = "flux") -> str:
    encoded = urllib.parse.quote(prompt[:1000], safe="")
    params = f"width={width}&height={height}&nologo=true&model={model}"
    if seed is not None:
        params += f"&seed={seed}"
    return f"{BASE}/{encoded}?{params}"


def _prefetch(url: str) -> None:
    """Download the image to warm Pollinations' CDN cache, then discard bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "repurpose-youtube-video/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_PREFETCH_TIMEOUT) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Pollinations HTTP {e.code}: {url}") from e
    except Exception as e:
        raise RuntimeError(f"Pollinations error fetching image: {e}") from e


def generate_image(
    prompt: str,
    *,
    aspect_ratio: str = "square_1_1",
    model: str = "flux",
) -> list[str]:
    """
    Generate one image with Pollinations.ai and return the resulting URL.
    No API key needed.

    Args:
        prompt: Visual description in English. Include "no text, no typography,
                no logos, no watermarks" since text overlay is added separately.
        aspect_ratio: "square_1_1" | "social_post_4_5" | "widescreen_16_9"
        model: "flux" (default) | "flux-realism" | "turbo"
    """
    w, h = _SIZES.get(aspect_ratio, (1080, 1080))
    url = _build_url(prompt, w, h, model=model)
    print(f"   [...] Generando imagen con Pollinations ({aspect_ratio})...")
    _prefetch(url)
    print(f"   [ok] Imagen lista.")
    return [url]


def generate_carousel(
    prompts: list[str],
    *,
    aspect_ratio: str = "square_1_1",
) -> list[str | None]:
    """
    Generate N images for an Instagram carousel (one per prompt).
    Each slide gets a unique seed to avoid identical compositions.
    Returns a list of length N: URL on success, None on failure.
    Callers must handle None entries (skip or degrade) to avoid slide misalignment.
    """
    w, h = _SIZES.get(aspect_ratio, (1080, 1080))
    n = len(prompts)
    results: list[str | None] = [None] * n

    for i, prompt in enumerate(prompts):
        print(f"   [...] Generando slide {i + 1}/{n} del carrusel con Pollinations...")
        url = _build_url(prompt, w, h, seed=42 + i)
        try:
            _prefetch(url)
            results[i] = url
            print(f"   [ok] Slide {i + 1} lista.")
        except Exception as e:
            print(f"   [aviso] Slide {i + 1} falló: {e}")

    return results
