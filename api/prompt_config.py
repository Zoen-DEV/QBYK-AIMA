"""Carga de la configuración de prompts (`api/prompts/*.json`).

Los prompts, el rubric y los datos de marca viven en archivos JSON separados —
nunca hardcodeados en la lógica— para que se puedan afinar sin tocar código ni
volver a desplegar. Este módulo solo los lee, los cachea y deja pisar el
directorio con `PROMPTS_DIR` (útil en tests y para probar una variante).

Un archivo ausente o roto NO puede tumbar el pipeline: se devuelve `{}` y quien
llame cae a sus valores por defecto. Lo único que se pierde es el afinado.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parent / "prompts"

# {ruta absoluta: (mtime, datos)} — recarga sola cuando el archivo cambia, así se
# puede iterar el prompt con el servidor levantado.
_CACHE: dict[str, tuple[float, dict]] = {}


def prompts_dir() -> Path:
    """Directorio de configuración de prompts (env `PROMPTS_DIR` o `api/prompts/`)."""
    override = os.environ.get("PROMPTS_DIR", "").strip()
    return Path(override) if override else _DEFAULT_DIR


def load(name: str) -> dict:
    """Devuelve el JSON `<prompts_dir>/<name>.json` ({} si falta o está roto)."""
    path = prompts_dir() / f"{name}.json"
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _CACHE.pop(key, None)
        return {}
    cached = _CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # JSON inválido: se avisa y se sigue con los defaults
        print(f"   [aviso] Config de prompts ilegible ({path.name}): {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    _CACHE[key] = (mtime, data)
    return data


def architect() -> dict:
    """Config del PromptArchitect (secciones, plantillas, validación)."""
    return load("architect")


def rubric() -> dict:
    """Rubric del pase de auto-crítica."""
    return load("rubric")


def brand() -> dict:
    """Datos de marca por defecto (paleta, tipografía, tono visual, aspecto)."""
    return load("brand")


def qa_vision() -> dict:
    """Config del QA de visión post-generación."""
    return load("qa_vision")


def identity_extract() -> dict:
    """Config del extractor de identidad visual a partir de fotos de referencia."""
    return load("identity_extract")


def clear_cache() -> None:
    """Vacía el cache (los tests cambian `PROMPTS_DIR` entre casos)."""
    _CACHE.clear()
