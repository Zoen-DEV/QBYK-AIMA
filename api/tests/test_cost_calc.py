"""Tests de la fórmula de costos (Fase 0 del dashboard de costos).

Cubren la fórmula de Claude con caché (el caso central confirmado), Perplexity con
sus cuatro componentes, Higgsfield imagen/video, Whisper (incluido el motor local
gratis) y el comportamiento best-effort ante tarifas `null` / servicios desconocidos.
"""

import json
import math

import pytest

import cost_calc


# Tarifas de prueba autocontenidas: todas las cifras conocidas para verificar la
# aritmética exacta sin depender del contenido de pricing.json.
PRICING = {
    "version": "test-1",
    "base_currency": "USD",
    "anthropic": {
        "claude-sonnet-4-6": {
            "input_per_1m": 3.00,
            "output_per_1m": 15.00,
            "cache_write_5m_per_1m": 3.75,
            "cache_read_per_1m": 0.30,
        }
    },
    "perplexity": {
        "sonar-pro": {
            "input_per_1m": 3.00,
            "output_per_1m": 15.00,
            "request_fee": 0.005,
            "search_per_1k": 5.00,
        }
    },
    "higgsfield": {
        "image_per_generation": 0.10,
        "video_per_generation": 0.50,
    },
    "higgsfield_mcp": {
        "usd_per_credit": 0.02,
        "image_credits_per_generation": {"default": 2, "nano_banana_pro": 2, "z_image": 0.5},
        "video_credits_per_second": {"default": 1.5, "kling3_0_turbo": 1.5},
        "video_default_seconds": 5,
        "tts_credits_per_character": {"default": 0.00667, "seed_audio": 0.00667},
        "subtitle_credits_per_block": 0.05,
    },
    "whisper": {
        "whisper-1": {"per_minute": 0.006},
    },
}


def approx(value: float) -> float:
    return pytest.approx(value, rel=1e-9, abs=1e-12)


# ----------------------------- Anthropic / Claude -----------------------------

def test_anthropic_full_formula_with_cache():
    units = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }
    # 3.00 + 15.00 + 3.75 + 0.30
    assert cost_calc.cost_anthropic("claude-sonnet-4-6", units, PRICING) == approx(22.05)


def test_anthropic_realistic_mixed_tokens():
    units = {
        "input_tokens": 1234,
        "output_tokens": 567,
        "cache_creation_input_tokens": 8900,
        "cache_read_input_tokens": 200,
    }
    expected = (
        1234 / 1e6 * 3.00
        + 567 / 1e6 * 15.00
        + 8900 / 1e6 * 3.75
        + 200 / 1e6 * 0.30
    )
    assert cost_calc.cost_anthropic("claude-sonnet-4-6", units, PRICING) == approx(expected)


def test_anthropic_missing_cache_fields_default_to_zero():
    # Sin campos de caché → solo input/output cuentan.
    units = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert cost_calc.cost_anthropic("claude-sonnet-4-6", units, PRICING) == approx(3.00)


def test_anthropic_unknown_model_is_zero():
    units = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert cost_calc.cost_anthropic("claude-opus-nope", units, PRICING) == 0.0


# ------------------------------- Perplexity -----------------------------------

def test_perplexity_all_four_components():
    units = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "requests": 2,
        "searches": 1000,
    }
    # 3.00 + 15.00 + 2*0.005 + (1000/1000)*5.00
    assert cost_calc.cost_perplexity("sonar-pro", units, PRICING) == approx(23.01)


def test_perplexity_defaults_to_one_request():
    # Sin `requests` explícito se asume 1 llamada → un request_fee.
    units = {"input_tokens": 0, "output_tokens": 0}
    assert cost_calc.cost_perplexity("sonar-pro", units, PRICING) == approx(0.005)


def test_perplexity_null_rates_are_best_effort_zero():
    pricing = {"perplexity": {"sonar": {"input_per_1m": None, "output_per_1m": None,
                                        "request_fee": None, "search_per_1k": None}}}
    units = {"input_tokens": 9_999_999, "output_tokens": 9_999_999, "searches": 50}
    assert cost_calc.cost_perplexity("sonar", units, pricing) == 0.0


# ------------------------------- Higgsfield -----------------------------------

def test_higgsfield_image_per_generation():
    # 1 base + 3 slides de carrusel = 4 generaciones.
    assert cost_calc.cost_higgsfield_image({"generations": 4}, PRICING) == approx(0.40)


def test_higgsfield_video_per_generation():
    assert cost_calc.cost_higgsfield_video({"generations": 1}, PRICING) == approx(0.50)


def test_higgsfield_null_rate_is_zero():
    pricing = {"higgsfield": {"image_per_generation": None, "video_per_generation": None}}
    assert cost_calc.cost_higgsfield_image({"generations": 10}, pricing) == 0.0


# --------------------------- Higgsfield MCP (créditos) ------------------------

def test_higgsfield_mcp_image_credits_by_model():
    # 3 generaciones × 2 créditos (nano_banana_pro).
    credits = cost_calc.higgsfield_mcp_credits(
        {"generations": 3}, PRICING, model="nano_banana_pro", operation="image_generation")
    assert credits == approx(6.0)


def test_higgsfield_mcp_image_unknown_model_uses_default():
    credits = cost_calc.higgsfield_mcp_credits(
        {"generations": 1}, PRICING, model="modelo-nuevo", operation="image_generation")
    assert credits == approx(2.0)  # default


def test_higgsfield_mcp_video_credits_per_second():
    # kling3_0_turbo cobra por segundo: 1 clip de 10s × 1.5 cr/s = 15 créditos.
    credits = cost_calc.higgsfield_mcp_credits(
        {"generations": 1, "seconds": 10}, PRICING, model="kling3_0_turbo", operation="video_generation")
    assert credits == approx(15.0)


def test_higgsfield_mcp_video_without_seconds_uses_model_default_duration():
    # Sin `seconds` en units → video_default_seconds (5s) → 7.5 créditos.
    credits = cost_calc.higgsfield_mcp_credits(
        {"generations": 1}, PRICING, model="kling3_0_turbo", operation="video_generation")
    assert credits == approx(7.5)


def test_higgsfield_mcp_frozen_credits_take_precedence():
    # Un evento con units.credits ya congelados no se recalcula.
    credits = cost_calc.higgsfield_mcp_credits(
        {"generations": 99, "credits": 4.5}, PRICING, model="nano_banana_pro", operation="image_generation")
    assert credits == approx(4.5)


def test_higgsfield_mcp_cost_is_credits_times_usd_per_credit():
    cost = cost_calc.cost_higgsfield_mcp(
        {"generations": 3}, PRICING, model="nano_banana_pro", operation="image_generation")
    assert cost == approx(6.0 * 0.02)


def test_higgsfield_mcp_zero_usd_per_credit_means_free_but_counted():
    # Suscripción (default): usd_per_credit=0 → costo $0 aunque haya consumo.
    pricing = {"higgsfield_mcp": {"usd_per_credit": 0,
                                  "image_credits_per_generation": {"default": 2}}}
    assert cost_calc.cost_higgsfield_mcp(
        {"generations": 5}, pricing, operation="image_generation") == 0.0
    assert cost_calc.higgsfield_mcp_credits(
        {"generations": 5}, pricing, operation="image_generation") == approx(10.0)


def test_higgsfield_mcp_tts_credits_per_character():
    # Voz en off del reel: 300 caracteres × 0.00667 cr/char (seed_audio).
    credits = cost_calc.higgsfield_mcp_credits(
        {"generations": 4, "characters": 300}, PRICING, model="seed_audio", operation="tts")
    assert credits == approx(300 * 0.00667)


def test_higgsfield_mcp_tts_frozen_credits_take_precedence():
    # El pipeline congela el costo exacto del preflight get_cost; no se recalcula.
    credits = cost_calc.higgsfield_mcp_credits(
        {"generations": 4, "characters": 300, "credits": 1.8}, PRICING,
        model="seed_audio", operation="tts")
    assert credits == approx(1.8)


def test_higgsfield_mcp_assembly_subtitles_per_voiced_block():
    # explainer_video: el ensamblaje es gratis; los subtítulos cobran 0.05 cr por
    # bloque CON voz (voiced_blocks, no blocks).
    credits = cost_calc.higgsfield_mcp_credits(
        {"blocks": 6, "voiced_blocks": 4}, PRICING,
        model="explainer_video", operation="video_assembly")
    assert credits == approx(0.05 * 4)


def test_compute_cost_dispatch_higgsfield_mcp():
    img = cost_calc.compute_cost("higgsfield_mcp", {"generations": 1}, PRICING,
                                 model="nano_banana_pro", operation="image_generation")
    vid = cost_calc.compute_cost("higgsfield_mcp", {"generations": 1, "seconds": 5}, PRICING,
                                 model="kling3_0_turbo", operation="video_generation")
    assert img == approx(2 * 0.02)
    assert vid == approx(7.5 * 0.02)


# -------------------------------- Whisper -------------------------------------

def test_whisper_per_minute():
    assert cost_calc.cost_whisper("whisper-1", {"minutes": 1.5}, PRICING) == approx(0.009)


def test_whisper_local_engine_is_free():
    # El motor local no cobra, sin importar los minutos.
    assert cost_calc.cost_whisper("local", {"minutes": 120}, PRICING) == 0.0


# ----------------------------- compute_cost (dispatch) ------------------------

def test_compute_cost_dispatch_anthropic():
    units = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    assert cost_calc.compute_cost("anthropic", units, PRICING,
                                  model="claude-sonnet-4-6") == approx(22.05)


def test_compute_cost_higgsfield_uses_operation_to_pick_rate():
    img = cost_calc.compute_cost("higgsfield", {"generations": 1}, PRICING,
                                 operation="image_generation")
    vid = cost_calc.compute_cost("higgsfield", {"generations": 1}, PRICING,
                                 operation="video_generation")
    assert img == approx(0.10)
    assert vid == approx(0.50)


def test_compute_cost_unknown_service_is_zero():
    assert cost_calc.compute_cost("mystery", {"whatever": 99}, PRICING) == 0.0


# ----------------------------- carga de pricing.json --------------------------

def test_load_pricing_explicit_path(tmp_path):
    data = {"version": "from-disk", "anthropic": {}}
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    loaded = cost_calc.load_pricing(p)
    assert loaded["version"] == "from-disk"


def test_default_pricing_file_is_valid_and_has_confirmed_claude_rates():
    # Carga el pricing.json (o el .example como fallback) del repo y verifica que
    # las tarifas de Claude estén presentes (las únicas confirmadas en Fase 0).
    pricing = cost_calc.load_pricing(force_reload=True)
    claude = pricing["anthropic"]["claude-sonnet-4-6"]
    assert claude["input_per_1m"] == 3.00
    assert claude["output_per_1m"] == 15.00
    assert claude["cache_write_5m_per_1m"] == 3.75
    assert claude["cache_read_per_1m"] == 0.30


def test_pricing_version_reads_field():
    assert cost_calc.pricing_version({"version": "2026-06-16"}) == "2026-06-16"
    assert cost_calc.pricing_version({}) == "unknown"
