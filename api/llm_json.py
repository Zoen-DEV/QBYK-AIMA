"""Llamadas cortas al LLM que devuelven JSON (sin streaming).

`post_writer` habla con el LLM en streaming porque su salida se pinta en vivo en
el preview. Las piezas nuevas (el PromptArchitect, su auto-crítica y el QA de
visión) no necesitan nada de eso: mandan un mensaje, esperan un JSON y siguen.
Este módulo es ese canal mínimo, con el mismo criterio de proveedor que el resto
del proyecto (`config.llm_provider`: Anthropic si hay key, si no Perplexity) y
sin SDKs nuevos — Perplexity va con urllib puro.

Todo es SÍNCRONO a propósito: quien llama desde el pipeline lo envuelve en
`job_runner._run` como hace con el resto de las llamadas bloqueantes.

Devuelve siempre `(datos, usage)`, donde `usage` ya viene con el shape que espera
`cost_tracker.record_event` (o `None` si el proveedor no lo reportó).
"""

from __future__ import annotations

import base64
import json
import re
import urllib.request

ANTHROPIC_MODEL = "claude-sonnet-4-6"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar-pro"

_TIMEOUT = 120


class LLMNoDisponible(RuntimeError):
    """No hay ningún proveedor de LLM configurado (ni Anthropic ni Perplexity)."""


# ── Parseo tolerante ──────────────────────────────────────────────────────────

def loads_loose(raw: str) -> dict:
    """JSON del modelo → dict, aguantando lo de siempre.

    Nunca asumir que el JSON del LLM viene bien formado (misma regla que el parser
    de `post_writer`): puede venir envuelto en ```json, con preámbulo, o con texto
    después del objeto. Devuelve {} si no se puede rescatar nada.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    # Rescate: el objeto más externo entre la primera { y la última }.
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


# ── Usage → shape de usage_events ─────────────────────────────────────────────

def _anthropic_usage(usage) -> dict | None:
    if usage is None:
        return None

    def _g(name: str) -> int:
        try:
            return int(getattr(usage, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "service": "anthropic",
        "model": ANTHROPIC_MODEL,
        "units": {
            "input_tokens": _g("input_tokens"),
            "output_tokens": _g("output_tokens"),
            "cache_creation_input_tokens": _g("cache_creation_input_tokens"),
            "cache_read_input_tokens": _g("cache_read_input_tokens"),
        },
    }


def _perplexity_usage(usage: dict | None) -> dict | None:
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
        "model": PERPLEXITY_MODEL,
        "units": {
            "input_tokens": _g("prompt_tokens", "input_tokens"),
            "output_tokens": _g("completion_tokens", "output_tokens"),
            "requests": 1,
            "searches": _g("num_search_queries", "search_queries"),
        },
    }


# ── Proveedores ───────────────────────────────────────────────────────────────

def _anthropic_json(system: str, user, api_key: str, *, max_tokens: int) -> tuple[dict, dict | None]:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    content = user if isinstance(user, list) else [{"type": "text", "text": user}]
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return loads_loose(raw), _anthropic_usage(getattr(msg, "usage", None))


def _perplexity_json(system: str, user, api_key: str, *, max_tokens: int) -> tuple[dict, dict | None]:
    """`user` es un string (camino de texto) o una lista de content blocks (con imágenes).

    El camino de texto manda el string PELADO, exactamente como antes de que existieran
    las imágenes: es el que usa todo el pipeline y no hay razón para cambiarle el formato
    del cuerpo.
    """
    body = json.dumps({
        "model": PERPLEXITY_MODEL,
        "max_tokens": max_tokens,
        # Sin búsqueda web: acá no se investiga nada, se reescribe lo que llega.
        "web_search_options": {"search_context_size": "low"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()
    req = urllib.request.Request(
        PERPLEXITY_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    return loads_loose(raw), _perplexity_usage(data.get("usage"))


# ── API pública ───────────────────────────────────────────────────────────────

def disponible(cfg) -> bool:
    """True si hay algún proveedor de texto configurado."""
    return bool(getattr(cfg, "anthropic_api_key", "") or getattr(cfg, "perplexity_api_key", ""))


def complete_json(system: str, user: str, *, cfg, max_tokens: int = 1200) -> tuple[dict, dict | None]:
    """Un mensaje, un JSON de vuelta. Lanza `LLMNoDisponible` si no hay key."""
    if getattr(cfg, "anthropic_api_key", ""):
        return _anthropic_json(system, user, cfg.anthropic_api_key, max_tokens=max_tokens)
    if getattr(cfg, "perplexity_api_key", ""):
        return _perplexity_json(system, user, cfg.perplexity_api_key, max_tokens=max_tokens)
    raise LLMNoDisponible("No hay ANTHROPIC_API_KEY ni PERPLEXITY_API_KEY configuradas")


def vision_disponible(cfg) -> bool:
    """True si hay algún proveedor que lea imágenes.

    Los DOS leen imágenes. Sonar no lo hacía cuando se escribió este módulo, pero la
    Sonar API acepta bloques `image_url` desde hace tiempo, así que quedarse en "solo
    Anthropic" dejaba sin extractor a quien solo tiene la key de Perplexity — que es la
    configuración por defecto del proyecto.
    """
    return bool(getattr(cfg, "anthropic_api_key", "") or getattr(cfg, "perplexity_api_key", ""))


def _bloque_imagen(image: bytes, media_type: str) -> dict:
    """Bloque de imagen de Anthropic (`source.base64`)."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(image).decode("ascii"),
        },
    }


def _bloque_imagen_pplx(image: bytes, media_type: str) -> dict:
    """Bloque de imagen de Perplexity (`image_url` con data URI, formato OpenAI)."""
    datos = base64.b64encode(image).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{datos}"}}


def complete_json_vision(system: str, user: str, image: bytes, *, media_type: str = "image/png",
                         cfg, max_tokens: int = 400) -> tuple[dict, dict | None]:
    """Como `complete_json` pero con una imagen adjunta. Requiere Anthropic."""
    if not getattr(cfg, "anthropic_api_key", ""):
        raise LLMNoDisponible("El QA de visión necesita ANTHROPIC_API_KEY")
    content = [_bloque_imagen(image, media_type), {"type": "text", "text": user}]
    return _anthropic_json(system, content, cfg.anthropic_api_key, max_tokens=max_tokens)


def complete_json_vision_multi(system: str, user: str, images: list[tuple[bytes, str]], *,
                               cfg, max_tokens: int = 1500) -> tuple[dict, dict | None]:
    """Como `complete_json_vision` pero con VARIAS imágenes. Sirve cualquiera de los dos
    proveedores (Anthropic primero, como en el resto del módulo).

    `images` es una lista de `(bytes, media_type)`.

    Cada proveedor recibe el layout de bloques que documenta el suyo, y no un formato
    común inventado:

    - **Anthropic** numera cada imagen (`Image 1:`) y deja el texto al final. Cuando el
      prompt habla del CONJUNTO ("lo que comparten estas fotos"), sin etiquetas el modelo
      tiende a describir solo la última.
    - **Perplexity** sigue el ejemplo de su guía al pie de la letra —el texto primero y
      las imágenes detrás, sin bloques de texto intercalados— porque es el único shape
      que su API documenta y no hay margen para probar variantes contra el endpoint real.
    """
    if not images:
        raise ValueError("No se pasó ninguna imagen")
    if getattr(cfg, "anthropic_api_key", ""):
        content: list[dict] = []
        for i, (datos, media_type) in enumerate(images, 1):
            content.append({"type": "text", "text": f"Image {i}:"})
            content.append(_bloque_imagen(datos, media_type))
        content.append({"type": "text", "text": user})
        return _anthropic_json(system, content, cfg.anthropic_api_key, max_tokens=max_tokens)
    if getattr(cfg, "perplexity_api_key", ""):
        bloques = [{"type": "text", "text": user}]
        bloques += [_bloque_imagen_pplx(datos, mt) for datos, mt in images]
        return _perplexity_json(system, bloques, cfg.perplexity_api_key, max_tokens=max_tokens)
    raise LLMNoDisponible(
        "La lectura de imágenes necesita ANTHROPIC_API_KEY o PERPLEXITY_API_KEY")
