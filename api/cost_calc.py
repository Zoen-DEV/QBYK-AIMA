"""Cálculo de costos en USD a partir de las unidades de uso y la tabla de tarifas.

Fase 0 del dashboard de costos (ver `docs/dashboard-costos.md`). Aquí vive **solo la
fórmula pura y la carga de `pricing.json`** — todavía sin MongoDB ni instrumentación
del pipeline (eso es Fase 1 y 2).

Diseño:
- **Punto único** que convierte `(servicio, modelo/operación, unidades)` → costo USD.
  En Fase 1, `cost_tracker.record_event` llamará a `compute_cost` y guardará el costo
  "congelado" junto a las unidades, de modo que cambiar una tarifa después no
  distorsione el histórico.
- **Best-effort**: una tarifa ausente (`null`) o un modelo desconocido cuenta como
  costo 0 en lugar de reventar. La medición nunca debe tumbar la generación de posts.
- **Sin dependencias pesadas**: solo stdlib, para que los tests corran aislados de
  FastAPI / Anthropic / etc.

Las unidades siguen el esquema de `usage_events` (ver §5 del doc):
  anthropic      → {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
  perplexity     → {input_tokens, output_tokens, requests, searches}
  higgsfield     → {generations}            (Cloud API legacy; operation distingue imagen vs video)
  higgsfield_mcp → {generations, seconds?, credits?}  (créditos de la suscripción; ver
                   `higgsfield_mcp_credits` — imagen cobra por generación, video por segundo)
  whisper        → {minutes}                (modelo "local" = $0)
"""

import json
from pathlib import Path
from typing import Any, Mapping, Optional

# Raíz del repo (igual que config.py: api/ está un nivel debajo).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRICING_PATH = _REPO_ROOT / "pricing.json"
_PRICING_EXAMPLE_PATH = _REPO_ROOT / "pricing.example.json"

# Cache del pricing cargado de disco. Las funciones de cálculo aceptan un dict
# `pricing` explícito (los tests lo aprovechan); solo `load_pricing` toca el disco.
_pricing_cache: Optional[dict] = None


def load_pricing(path: Optional[Path] = None, *, force_reload: bool = False) -> dict:
    """Carga la tabla de tarifas desde `pricing.json` (raíz del repo).

    Si `pricing.json` no existe, cae a `pricing.example.json` para que el módulo
    funcione recién clonado (con las tarifas de Claude ya confirmadas). El resultado
    se cachea; usa `force_reload=True` tras editar el archivo en caliente.
    """
    global _pricing_cache
    if path is not None:
        # Carga explícita (tests / herramientas): nunca usa ni puebla el cache.
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if _pricing_cache is not None and not force_reload:
        return _pricing_cache
    source = _PRICING_PATH if _PRICING_PATH.exists() else _PRICING_EXAMPLE_PATH
    _pricing_cache = json.loads(source.read_text(encoding="utf-8"))
    return _pricing_cache


def pricing_version(pricing: Optional[Mapping[str, Any]] = None) -> str:
    """Versión de la tabla vigente, para trazabilidad (`pricing_version` en el evento)."""
    pricing = pricing if pricing is not None else load_pricing()
    return str(pricing.get("version", "unknown"))


def _rate(table: Optional[Mapping[str, Any]], key: str) -> float:
    """Tarifa numérica de `table[key]`, tratando ausente/`null`/no-numérico como 0."""
    if not table:
        return 0.0
    value = table.get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _units(units: Optional[Mapping[str, Any]], key: str) -> float:
    """Cantidad de unidades `units[key]`, tratando ausente/no-numérico como 0."""
    if not units:
        return 0.0
    value = units.get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cost_anthropic(model: str, units: Mapping[str, Any], pricing: Mapping[str, Any]) -> float:
    """Costo de una llamada a Claude, incluyendo escritura/lectura de caché.

        cost = input_tokens            /1e6 * input_per_1m
             + output_tokens           /1e6 * output_per_1m
             + cache_creation_input_tokens/1e6 * cache_write_5m_per_1m
             + cache_read_input_tokens /1e6 * cache_read_per_1m
    """
    rates = (pricing.get("anthropic") or {}).get(model)
    return (
        _units(units, "input_tokens") / 1e6 * _rate(rates, "input_per_1m")
        + _units(units, "output_tokens") / 1e6 * _rate(rates, "output_per_1m")
        + _units(units, "cache_creation_input_tokens") / 1e6 * _rate(rates, "cache_write_5m_per_1m")
        + _units(units, "cache_read_input_tokens") / 1e6 * _rate(rates, "cache_read_per_1m")
    )


def cost_perplexity(model: str, units: Mapping[str, Any], pricing: Mapping[str, Any]) -> float:
    """Costo de una llamada a Perplexity Sonar: tokens + fee por request + búsqueda.

        cost = input_tokens /1e6 * input_per_1m
             + output_tokens/1e6 * output_per_1m
             + requests          * request_fee
             + searches    /1e3  * search_per_1k

    `requests` por defecto es 1 (una llamada = una request). Si la tarifa
    `request_fee` es `null`, el término simplemente no suma.
    """
    rates = (pricing.get("perplexity") or {}).get(model)
    requests = _units(units, "requests") or 1.0
    return (
        _units(units, "input_tokens") / 1e6 * _rate(rates, "input_per_1m")
        + _units(units, "output_tokens") / 1e6 * _rate(rates, "output_per_1m")
        + requests * _rate(rates, "request_fee")
        + _units(units, "searches") / 1e3 * _rate(rates, "search_per_1k")
    )


def cost_higgsfield_image(units: Mapping[str, Any], pricing: Mapping[str, Any]) -> float:
    """Costo de imágenes Higgsfield: `generations` × `image_per_generation`.

    Solo se cuentan las generaciones HF reales; las que caen a plantilla local son
    gratis y no deben llegar aquí.
    """
    rates = pricing.get("higgsfield") or {}
    return _units(units, "generations") * _rate(rates, "image_per_generation")


def cost_higgsfield_video(units: Mapping[str, Any], pricing: Mapping[str, Any]) -> float:
    """Costo de video Higgsfield: `generations` × `video_per_generation`."""
    rates = pricing.get("higgsfield") or {}
    return _units(units, "generations") * _rate(rates, "video_per_generation")


def _model_rate(table: Any, model: Optional[str]) -> float:
    """Tarifa por modelo en un dict `{modelo: tarifa, "default": tarifa}` (0 si falta)."""
    if not isinstance(table, Mapping):
        return 0.0
    value = table.get(model or "")
    if value is None:
        value = table.get("default")
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def higgsfield_mcp_credits(
    units: Mapping[str, Any],
    pricing: Mapping[str, Any],
    *,
    model: Optional[str] = None,
    operation: Optional[str] = None,
) -> float:
    """Créditos de la suscripción consumidos por un evento del MCP de Higgsfield.

    Si el evento ya trae `units.credits` (congelado al escribirse), se respeta tal
    cual. Si no, se estima con las tarifas de `pricing.higgsfield_mcp` (medidas con
    el preflight `get_cost` del MCP):
      imagen → generations × image_credits_per_generation[modelo]
      video  → generations × video_credits_per_second[modelo] × seconds
               (sin `seconds` en units se usa `video_default_seconds`, la duración
               por defecto del modelo cuando la app no manda `duration`)
    """
    frozen = _units(units, "credits")
    if frozen:
        return frozen
    rates = pricing.get("higgsfield_mcp") or {}
    generations = _units(units, "generations")
    if (operation or "").lower() == "video_generation":
        per_second = _model_rate(rates.get("video_credits_per_second"), model)
        seconds = _units(units, "seconds") or _rate(rates, "video_default_seconds")
        return generations * per_second * seconds
    per_generation = _model_rate(rates.get("image_credits_per_generation"), model)
    return generations * per_generation


def cost_higgsfield_mcp(
    units: Mapping[str, Any],
    pricing: Mapping[str, Any],
    *,
    model: Optional[str] = None,
    operation: Optional[str] = None,
) -> float:
    """Costo USD de una generación vía MCP: créditos × `usd_per_credit`.

    Con `usd_per_credit = 0` (default) el consumo se mide solo en créditos y el
    gasto real queda como fijo (`fixed_monthly.higgsfield`). Si se pone un valor
    (> 0), dejar el fijo en 0 para no contar la suscripción dos veces.
    """
    rates = pricing.get("higgsfield_mcp") or {}
    credits = higgsfield_mcp_credits(units, pricing, model=model, operation=operation)
    return credits * _rate(rates, "usd_per_credit")


def cost_whisper(model: str, units: Mapping[str, Any], pricing: Mapping[str, Any]) -> float:
    """Costo de transcripción Whisper: `minutes` × `per_minute`.

    El motor local (faster-whisper) es gratis: cualquier modelo "local" → $0.
    """
    if (model or "").lower() == "local":
        return 0.0
    rates = (pricing.get("whisper") or {}).get(model)
    return _units(units, "minutes") * _rate(rates, "per_minute")


def compute_cost(
    service: str,
    units: Mapping[str, Any],
    pricing: Optional[Mapping[str, Any]] = None,
    *,
    model: Optional[str] = None,
    operation: Optional[str] = None,
) -> float:
    """Despacha al cálculo del servicio correspondiente y devuelve el costo USD.

    `service` ∈ {anthropic, perplexity, higgsfield, higgsfield_mcp, whisper}. Para
    higgsfield/higgsfield_mcp, `operation` ("image_generation" vs "video_generation")
    elige la tarifa. Un servicio desconocido devuelve 0.0 (best-effort, nunca lanza).
    """
    pricing = pricing if pricing is not None else load_pricing()
    service = (service or "").lower()

    if service == "anthropic":
        return cost_anthropic(model or "", units, pricing)
    if service == "perplexity":
        return cost_perplexity(model or "", units, pricing)
    if service == "higgsfield_mcp":
        return cost_higgsfield_mcp(units, pricing, model=model, operation=operation)
    if service == "higgsfield":
        if (operation or "").lower() == "video_generation":
            return cost_higgsfield_video(units, pricing)
        return cost_higgsfield_image(units, pricing)
    if service == "whisper":
        return cost_whisper(model or "", units, pricing)
    return 0.0
