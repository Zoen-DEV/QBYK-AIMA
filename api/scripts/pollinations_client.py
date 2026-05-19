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

import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://image.pollinations.ai/prompt"

_SIZES: dict[str, tuple[int, int]] = {
    "square_1_1": (1080, 1080),
    "social_post_4_5": (1080, 1350),
    "widescreen_16_9": (1920, 1080),
}

_ATTEMPT_TIMEOUT = 90        # seconds per attempt
_MAX_RETRIES = 3             # total attempts before giving up
_BACKOFF_SECS = [0, 10, 20]  # wait before attempt 1, 2, 3


def _build_url(prompt: str, width: int, height: int, *, seed: int | None = None, model: str = "flux") -> str:
    encoded = urllib.parse.quote(prompt[:1000], safe="")
    params = f"width={width}&height={height}&nologo=true&model={model}"
    if seed is not None:
        params += f"&seed={seed}"
    return f"{BASE}/{encoded}?{params}"


def _trigger(url: str) -> None:
    """Fire a request to start generation without blocking on the result.

    Pollinations generates on first request and caches — even a timed-out
    request leaves the job running on the server side. Used to pre-warm
    multiple slides in parallel before the blocking fetch loop.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "repurpose-youtube-video/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass  # timeout expected — generation is now running on the server


def _prefetch(url: str) -> None:
    """Download the image to warm Pollinations' CDN cache, then discard bytes.

    Retries up to _MAX_RETRIES times on timeout or network error.
    HTTP errors (4xx/5xx) are not retried — they indicate a permanent failure.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "repurpose-youtube-video/1.0"})
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        wait = _BACKOFF_SECS[attempt]
        if wait > 0:
            print(f"   [...] Reintentando en {wait}s (intento {attempt + 1}/{_MAX_RETRIES})...")
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=_ATTEMPT_TIMEOUT) as resp:
                resp.read()
            return  # success
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Pollinations HTTP {e.code}: {url}") from e
        except Exception as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                print(f"   [aviso] Intento {attempt + 1} falló ({e}), reintentando...")
    raise RuntimeError(f"Pollinations error fetching image: {last_exc}")


def generate_image(
    prompt: str,
    *,
    aspect_ratio: str = "square_1_1",
    model: str = "flux",
    seed: int | None = None,
) -> list[str]:
    """
    Generate one image with Pollinations.ai and return [url].
    No API key needed.

    Args:
        prompt: Visual description in English. Include "no text, no typography,
                no logos, no watermarks" since text overlay is added separately.
        aspect_ratio: "square_1_1" | "social_post_4_5" | "widescreen_16_9"
        model: "flux" (default) | "flux-realism" | "turbo"
        seed: Optional fixed seed for reproducible results. Use seed=42 for the
              shared base image so LinkedIn, Instagram single, and carousel slide 1
              all resolve to the same cached URL.
    """
    w, h = _SIZES.get(aspect_ratio, (1080, 1080))
    url = _build_url(prompt, w, h, seed=seed, model=model)
    print(f"   [...] Generando imagen base con Pollinations ({aspect_ratio})...")
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


def generate_carousel_extra_slides(
    prompts: list[str],
    *,
    aspect_ratio: str = "square_1_1",
    start_seed: int = 43,
) -> list[str | None]:
    """
    Generate slides 2..N of an Instagram carousel.

    Slide 1 is expected to come from the shared base image (generate_image with
    seed=42), so this function starts seeds at start_seed=43 to stay distinct.

    Returns a list of length len(prompts): URL on success, None on failure.
    The slide number printed is i+2 (relative to a 3-slide carousel where slide 1
    is the shared base).
    """
    w, h = _SIZES.get(aspect_ratio, (1080, 1080))
    n = len(prompts)
    results: list[str | None] = [None] * n

    urls = [_build_url(p, w, h, seed=start_seed + i) for i, p in enumerate(prompts)]

    # Pre-warm all slides in parallel so Pollinations generates them concurrently
    # while we do the blocking fetch loop below.
    trigger_threads = [threading.Thread(target=_trigger, args=(u,), daemon=True) for u in urls]
    for t in trigger_threads:
        t.start()

    for i, url in enumerate(urls):
        slide_num = i + 2
        print(f"   [...] Generando slide {slide_num} del carrusel con Pollinations...")
        try:
            _prefetch(url)
            results[i] = url
            print(f"   [ok] Slide {slide_num} lista.")
        except Exception as e:
            print(f"   [aviso] Slide {slide_num} falló: {e}")

    return results


def prewarm_carousel_extra_slides(
    prompts: list[str],
    *,
    aspect_ratio: str = "square_1_1",
    start_seed: int = 43,
) -> list[str]:
    """Fire background generation requests and return the URLs without blocking.

    Call this right after generating the base image so Pollinations starts
    working on extra slides while LinkedIn/IG-0 overlays are being rendered.
    Pair with fetch_url() to block until each slide is ready.
    """
    w, h = _SIZES.get(aspect_ratio, (1080, 1080))
    urls = [_build_url(p, w, h, seed=start_seed + i) for i, p in enumerate(prompts)]
    for url in urls:
        threading.Thread(target=_trigger, args=(url,), daemon=True).start()
    return urls


def fetch_url(url: str) -> None:
    """Block until the image at `url` is fully generated (retries on timeout).

    Use after prewarm_carousel_extra_slides() to wait for each slide in turn.
    Raises RuntimeError if all retries are exhausted.
    """
    _prefetch(url)
