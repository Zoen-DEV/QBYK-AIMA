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

import higgsfield_client as hf   # Cloud API (retirado como backend activo; se conserva por rollback)
import higgsfield_mcp as hfmcp   # MCP oficial (OAuth) — backend activo, créditos de suscripción

# Bundled fallback templates live next to the api package: api/assets/templates/.
# scripts/ is api/scripts/, so go up one level to api/ then into assets/templates.
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"

# Role index within a set: 1 = base/hook, 2 = info/argument, 3 = credits/closing.
# El usuario elige uno de 3 SETS de estilo por post (template_set 1-3). Cada set es
# una carpeta set-{n}/ con sus template-1/2/3.png. Layout y fallback:
#   - set-{n}/template-{role}.png            (estilo propio del set)
#   - set-1/template-{role}.png              (por si el set-1 vive en carpeta)
#   - template-{role}.png  (layout plano)    (default histórico = set-1)
# Así los 3 sets funcionan de inmediato (set-2/3 caen al look del set-1 hasta que el
# usuario agregue sus PNG), sin errores ni archivos duplicados.
_TEMPLATE_SETS = (1, 2, 3)


def _template_file(role_idx: int, set_n: int) -> str:
    """Ruta del PNG de plantilla para (rol, set), con fallback al set-1 / layout plano.

    Los PNG son assets que el usuario deja en api/assets/templates/. Un archivo
    faltante en TODOS los candidatos es un problema de despliegue → error claro.
    """
    set_n = set_n if set_n in _TEMPLATE_SETS else 1
    fname = f"template-{role_idx}.png"
    candidates: list[Path] = []
    if set_n != 1:
        candidates.append(_TEMPLATES_DIR / f"set-{set_n}" / fname)
    candidates.append(_TEMPLATES_DIR / "set-1" / fname)
    candidates.append(_TEMPLATES_DIR / fname)  # layout plano histórico = set-1
    for p in candidates:
        if p.exists():
            return str(p)
    raise RuntimeError(
        f"Template de respaldo no encontrado: {fname} (set {set_n}). "
        "Coloca template-1.png, template-2.png y template-3.png (1080x1080) en "
        "api/assets/templates/ (y en set-2/ y set-3/ para variar el estilo por set)."
    )


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
    if "reautenticación" in s or "reauth" in s:
        return "reautenticación requerida (corré mcp_bootstrap.py)"
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

    def __init__(self, set_n: int = 1):
        # Set de estilo elegido por el usuario (1-3); un valor inválido cae al 1.
        self._set = set_n if set_n in _TEMPLATE_SETS else 1
        self._warnings: list[str] = []

    def pop_warnings(self) -> list[str]:
        """Return and clear fallback warnings accumulated since the last call."""
        w = self._warnings
        self._warnings = []
        return w

    def generate_base(self, prompt: str, *, aspect_ratio: str = "1:1") -> str:
        # Base / hook slide = role 1 del set elegido. Las plantillas locales son 1:1;
        # el aspect solicitado (p. ej. 9:16 para historia) lo aplica el overlay al
        # hacer center-crop, así que aquí se ignora.
        return _template_file(1, self._set)

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
        return _template_file(3 if kind == "credits" else 2, self._set)


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


class MCPProvider:
    """Backend activo: genera vía el MCP oficial de Higgsfield (OAuth).

    Consume los créditos de la SUSCRIPCIÓN (no el Cloud API). Misma interfaz que
    HiggsfieldProvider — mismo submit/poll para el prewarm concurrente de slides,
    mismo fallback por-imagen a plantilla local, mismo contador hf_generations para
    el tracking. El puente sync↔async lo resuelve higgsfield_mcp.
    """

    name = "higgsfield-mcp"
    label = "Higgsfield (MCP)"

    def __init__(self, *, image_model: str = "", template_set: int = 1):
        self._image_model = image_model
        self._fallback = TemplateProvider(template_set)
        self._warnings: list[str] = []
        self._hf_generations = 0

    @property
    def hf_generations(self) -> int:
        return self._hf_generations

    def pop_warnings(self) -> list[str]:
        w = self._warnings
        self._warnings = []
        return w

    def generate_base(self, prompt: str, *, aspect_ratio: str = "1:1") -> str:
        try:
            url = hfmcp.generate_image(prompt, aspect_ratio=aspect_ratio, model=self._image_model)[0]
            self._hf_generations += 1
            return url
        except Exception as e:
            self._warnings.append(_short_reason(e))
            print(f"   [aviso] MCP (imagen base) falló: {e}. Fallback a plantilla local.")
            return self._fallback.generate_base(prompt, aspect_ratio=aspect_ratio)

    def prewarm_extras(self, prompts: list[str]) -> list:
        n = len(prompts)
        handles: list[dict] = []
        for i, prompt in enumerate(prompts):
            kind = "credits" if i == n - 1 else "info"
            try:
                handle = hfmcp.submit_image(prompt, aspect_ratio="1:1", model=self._image_model)
                handle["template_kind"] = kind
            except Exception as e:
                print(f"   [aviso] MCP (envío slide {i + 2}) falló: {e}. Ese slide usará plantilla local.")
                handle = {"fallback_template": True, "template_kind": kind, "fallback_reason": _short_reason(e)}
            handles.append(handle)
        return handles

    def resolve(self, handle: dict) -> str:
        if handle.get("fallback_template"):
            self._warnings.append(handle.get("fallback_reason", "no se pudo enviar al MCP"))
            return self._fallback.resolve(handle)
        try:
            url = hfmcp.poll_image(handle)
            self._hf_generations += 1
            return url
        except Exception as e:
            self._warnings.append(_short_reason(e))
            print(f"   [aviso] MCP (slide) falló: {e}. Fallback a plantilla local.")
            return self._fallback.resolve(handle)


def make_provider(*, force_template: bool = False, mcp_image_model: str = "",
                  template_set: int = 1):
    """Devuelve el backend de imágenes.

    - force_template=True → plantillas locales directas (el usuario eligió plantillas).
    - MCP configurado (existe el token store OAuth) → MCPProvider (créditos de suscripción).
    - Si no → plantillas locales (backend offline siempre disponible).

    `template_set` (1-3) elige el set de estilo de las plantillas — se usa tanto en el
    modo plantillas directas como en el fallback por-imagen del MCP.

    El Cloud API (HiggsfieldProvider, key+secret) quedó retirado como backend activo;
    se conserva la clase por si hay que hacer rollback.
    """
    if force_template:
        return TemplateProvider(template_set)
    if hfmcp.is_configured():
        return MCPProvider(image_model=mcp_image_model, template_set=template_set)
    return TemplateProvider(template_set)
