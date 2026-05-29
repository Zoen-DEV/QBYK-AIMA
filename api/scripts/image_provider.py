"""
Image generation provider facade.

Picks the image backend based on available credentials and exposes a uniform
interface to job_runner so the pipeline stays provider-agnostic:

  generate_base(prompt) -> url        Blocking. The shared square base image
                                       (reused by LinkedIn, IG single, carousel
                                       slide 0). Raises only if generation fails
                                       with no usable fallback.
  prewarm_extras(prompts) -> handles   Start generating the extra carousel slides
                                       (slides 1 & 2). Returns opaque handles.
  resolve(handle) -> url               Blocking. Wait for one prewarmed handle
                                       and return its final image URL.

Two backends:
  - Pollinations (free, no key): the handle IS the image URL (lazy generation).
  - Higgsfield Soul (paid, key+secret): submit/poll; the URL exists only once the
    job completes. On any per-image failure (network, timeout, nsfw, failed) the
    Higgsfield provider falls back to Pollinations for *that* image so a single
    failure never breaks the pipeline.

Seed scheme (kept identical to the original Pollinations flow so fallbacks line
up): base = seed 42, extra carousel slides = seeds 43, 44, ... (distinct images).
"""

import pollinations_client as pc
import higgsfield_client as hf

_BASE_SEED = 42
_EXTRA_START_SEED = 43


def _short_reason(exc: Exception) -> str:
    """Translate a Higgsfield failure into a short, user-facing Spanish reason."""
    s = str(exc).lower()
    if "not_enough_credits" in s or "enough credits" in s:
        return "sin créditos en Higgsfield"
    if "1010" in s or "browser_signature" in s or "cloudflare" in s:
        return "bloqueo de Cloudflare (1010)"
    if "401" in s or "unauthorized" in s or "invalid" in s and "key" in s:
        return "credenciales rechazadas"
    if "nsfw" in s:
        return "imagen marcada como NSFW"
    if "timed out" in s or "timeout" in s:
        return "tardó demasiado (timeout)"
    msg = str(exc)
    return msg if len(msg) <= 80 else msg[:77] + "..."


class PollinationsProvider:
    name = "pollinations"
    label = "Pollinations"

    def __init__(self):
        self._warnings: list[str] = []

    def pop_warnings(self) -> list[str]:
        """Return and clear fallback warnings accumulated since the last call."""
        w = self._warnings
        self._warnings = []
        return w

    def generate_base(self, prompt: str) -> str:
        return pc.generate_image(prompt, aspect_ratio="square_1_1", seed=_BASE_SEED)[0]

    def prewarm_extras(self, prompts: list[str]) -> list:
        # Returns URLs directly; for Pollinations the handle is the image URL.
        return pc.prewarm_carousel_extra_slides(prompts, start_seed=_EXTRA_START_SEED)

    def resolve(self, handle) -> str:
        # handle is a URL string; block until generated, then return it.
        pc.fetch_url(handle)
        return handle


class HiggsfieldProvider:
    name = "higgsfield"
    label = "Higgsfield"

    def __init__(self, api_key: str, api_secret: str, *, model: str, resolution: str):
        self._key = api_key
        self._secret = api_secret
        self._model = model
        self._resolution = resolution
        self._fallback = PollinationsProvider()
        self._warnings: list[str] = []

    def pop_warnings(self) -> list[str]:
        """Return and clear fallback warnings accumulated since the last call."""
        w = self._warnings
        self._warnings = []
        return w

    def generate_base(self, prompt: str) -> str:
        try:
            return hf.generate_image(
                prompt, api_key=self._key, api_secret=self._secret,
                aspect_ratio="1:1", resolution=self._resolution, model=self._model,
            )[0]
        except Exception as e:
            reason = _short_reason(e)
            self._warnings.append(reason)
            print(f"   [aviso] Higgsfield (imagen base) falló: {e}. Fallback a Pollinations.")
            return self._fallback.generate_base(prompt)

    def prewarm_extras(self, prompts: list[str]) -> list:
        handles: list[dict] = []
        for i, prompt in enumerate(prompts):
            seed = _EXTRA_START_SEED + i
            try:
                handle = hf.submit_image(
                    prompt, api_key=self._key, api_secret=self._secret,
                    aspect_ratio="1:1", resolution=self._resolution, model=self._model,
                )
                handle["fallback_seed"] = seed
            except Exception as e:
                # Don't warn yet — warn once at resolve time, attributed to the slide.
                print(f"   [aviso] Higgsfield (envío slide {i + 2}) falló: {e}. Ese slide usará Pollinations.")
                handle = {"fallback_prompt": prompt, "fallback_seed": seed, "fallback_reason": _short_reason(e)}
            handles.append(handle)
        return handles

    def resolve(self, handle: dict) -> str:
        seed = handle.get("fallback_seed", _EXTRA_START_SEED)
        if "fallback_prompt" in handle:  # submit already failed → straight to Pollinations
            self._warnings.append(handle.get("fallback_reason", "no se pudo enviar a Higgsfield"))
            return self._pollinations_slide(handle["fallback_prompt"], seed)
        try:
            return hf.poll_image(handle, api_key=self._key, api_secret=self._secret)
        except Exception as e:
            self._warnings.append(_short_reason(e))
            print(f"   [aviso] Higgsfield (slide) falló: {e}. Fallback a Pollinations.")
            return self._pollinations_slide(handle.get("prompt", ""), seed)

    @staticmethod
    def _pollinations_slide(prompt: str, seed: int) -> str:
        # Distinct seed per slide so fallback images don't collide with the base.
        return pc.generate_image(prompt, aspect_ratio="square_1_1", seed=seed)[0]


def make_provider(*, hf_key: str = "", hf_secret: str = "",
                  hf_model: str = hf.DEFAULT_MODEL,
                  hf_resolution: str = hf.DEFAULT_RESOLUTION):
    """Return a Higgsfield provider when both credentials are set, else Pollinations."""
    if hf_key and hf_secret:
        return HiggsfieldProvider(hf_key, hf_secret, model=hf_model, resolution=hf_resolution)
    return PollinationsProvider()
