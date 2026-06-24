"""
Image generation provider facade.

Picks the image backend based on available credentials and exposes a uniform
interface to job_runner so the pipeline stays provider-agnostic:

  generate_base(prompt, *, aspect_ratio="1:1") -> src
                                       Blocking. The shared base image (reused by
                                       LinkedIn, IG single, carousel slide 0).
                                       `aspect_ratio` lets a caller request 9:16
                                       (IG Story). Raises only if generation fails
                                       with no usable fallback.
  prewarm_extras(prompts) -> handles   Start generating the extra carousel slides
                                       (info slides + credits). Returns opaque
                                       handles.
  resolve(handle) -> src               Blocking. Wait for one prewarmed handle
                                       and return its final image source.

A "src" is either an http(s) URL (Higgsfield output) or a local filesystem path
(a bundled template). image_overlay._fetch_base accepts both transparently.

Backend:
  - Higgsfield Soul (paid, key+secret): submit/poll; the URL exists only once the
    job completes. On any per-image failure (network, timeout, nsfw, failed) the
    Higgsfield provider falls back to a bundled local template for *that* image
    so a single failure never breaks the pipeline.
  - Templates (free, offline): three bundled PNGs used only as a fallback when
    Higgsfield is unavailable or fails. There is no standalone paid service to
    call; when no Higgsfield credentials are set we serve templates directly.

Template assignment (confirmed product decision — one template per slide type so
a fallback carousel still looks varied):
  - base / hook (LinkedIn, IG single, carousel slide 0)  -> template-1.png
  - info / argument slides                               -> template-2.png
  - credits / closing slide (last extra)                 -> template-3.png
"""

from pathlib import Path

import higgsfield_client as hf

# Bundled fallback templates live next to the api package: api/assets/templates/.
# scripts/ is api/scripts/, so go up one level to api/ then into assets/templates.
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"
_TEMPLATE_BASE = _TEMPLATES_DIR / "template-1.png"     # base / hook
_TEMPLATE_INFO = _TEMPLATES_DIR / "template-2.png"     # info / argument slides
_TEMPLATE_CREDITS = _TEMPLATES_DIR / "template-3.png"  # credits / closing slide


def _template_path(p: Path) -> str:
    """Return the template path as a string, raising a clear error if missing.

    The templates are bundled assets the user drops into api/assets/templates/;
    a missing file is a deployment problem, not a runtime fallback, so surface it.
    """
    if not p.exists():
        raise RuntimeError(
            f"Template de respaldo no encontrado: {p}. "
            "Coloca template-1.png, template-2.png y template-3.png (1080x1080) "
            "en api/assets/templates/."
        )
    return str(p)


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


class TemplateProvider:
    """Offline fallback backend: serves bundled local template images.

    Used directly when no Higgsfield credentials are configured, and as the
    per-image fallback inside HiggsfieldProvider. Returns local file paths;
    image_overlay renders the copy on top exactly as it would over a URL.
    """

    name = "template"
    label = "plantillas locales"
    # Las plantillas locales son gratis: nunca cuentan como generación de pago.
    hf_generations = 0

    def __init__(self):
        self._warnings: list[str] = []

    def pop_warnings(self) -> list[str]:
        """Return and clear fallback warnings accumulated since the last call."""
        w = self._warnings
        self._warnings = []
        return w

    def generate_base(self, prompt: str, *, aspect_ratio: str = "1:1") -> str:
        # Base / hook slide always uses template-1. Las plantillas locales son 1:1;
        # el aspect solicitado (p. ej. 9:16 para historia) lo aplica el overlay al
        # hacer center-crop, así que aquí se ignora.
        return _template_path(_TEMPLATE_BASE)

    def prewarm_extras(self, prompts: list[str]) -> list:
        # No async work — return one handle per extra slide. The last extra is the
        # credits/closing slide; the rest are info slides. Encode that in the handle.
        handles: list[dict] = []
        n = len(prompts)
        for i in range(n):
            kind = "credits" if i == n - 1 else "info"
            handles.append({"template_kind": kind})
        return handles

    def resolve(self, handle: dict) -> str:
        kind = handle.get("template_kind", "info")
        return _template_path(_TEMPLATE_CREDITS if kind == "credits" else _TEMPLATE_INFO)


class HiggsfieldProvider:
    name = "higgsfield"
    label = "Higgsfield"

    def __init__(self, api_key: str, api_secret: str, *, model: str, resolution: str):
        self._key = api_key
        self._secret = api_secret
        self._model = model
        self._resolution = resolution
        self._fallback = TemplateProvider()
        self._warnings: list[str] = []
        # Cuenta solo las generaciones HF que terminaron con éxito (las que caen a
        # plantilla local son gratis y no se cuentan). Lo lee job_runner para el
        # tracking de costos al cerrar la fase de imágenes.
        self._hf_generations = 0

    @property
    def hf_generations(self) -> int:
        return self._hf_generations

    def pop_warnings(self) -> list[str]:
        """Return and clear fallback warnings accumulated since the last call."""
        w = self._warnings
        self._warnings = []
        return w

    def generate_base(self, prompt: str, *, aspect_ratio: str = "1:1") -> str:
        try:
            url = hf.generate_image(
                prompt, api_key=self._key, api_secret=self._secret,
                aspect_ratio=aspect_ratio, resolution=self._resolution, model=self._model,
            )[0]
            self._hf_generations += 1
            return url
        except Exception as e:
            reason = _short_reason(e)
            self._warnings.append(reason)
            print(f"   [aviso] Higgsfield (imagen base) falló: {e}. Fallback a plantilla local.")
            return self._fallback.generate_base(prompt, aspect_ratio=aspect_ratio)

    def prewarm_extras(self, prompts: list[str]) -> list:
        n = len(prompts)
        handles: list[dict] = []
        for i, prompt in enumerate(prompts):
            # Track slide kind so a fallback picks the right template.
            kind = "credits" if i == n - 1 else "info"
            try:
                handle = hf.submit_image(
                    prompt, api_key=self._key, api_secret=self._secret,
                    aspect_ratio="1:1", resolution=self._resolution, model=self._model,
                )
                handle["template_kind"] = kind
            except Exception as e:
                # Don't warn yet — warn once at resolve time, attributed to the slide.
                print(f"   [aviso] Higgsfield (envío slide {i + 2}) falló: {e}. Ese slide usará plantilla local.")
                handle = {"fallback_template": True, "template_kind": kind, "fallback_reason": _short_reason(e)}
            handles.append(handle)
        return handles

    def resolve(self, handle: dict) -> str:
        if handle.get("fallback_template"):  # submit already failed → straight to template
            self._warnings.append(handle.get("fallback_reason", "no se pudo enviar a Higgsfield"))
            return self._template_slide(handle)
        try:
            url = hf.poll_image(handle, api_key=self._key, api_secret=self._secret)
            self._hf_generations += 1
            return url
        except Exception as e:
            self._warnings.append(_short_reason(e))
            print(f"   [aviso] Higgsfield (slide) falló: {e}. Fallback a plantilla local.")
            return self._template_slide(handle)

    def _template_slide(self, handle: dict) -> str:
        return self._fallback.resolve(handle)


def make_provider(*, hf_key: str = "", hf_secret: str = "",
                  hf_model: str = hf.DEFAULT_MODEL,
                  hf_resolution: str = hf.DEFAULT_RESOLUTION,
                  force_template: bool = False):
    """Return a Higgsfield provider when both credentials are set, else templates.

    `force_template=True` ignores Higgsfield entirely and serves bundled templates
    directly (the user explicitly chose plantillas en el formulario / la hoja).
    """
    if force_template:
        return TemplateProvider()
    if hf_key and hf_secret:
        return HiggsfieldProvider(hf_key, hf_secret, model=hf_model, resolution=hf_resolution)
    return TemplateProvider()
