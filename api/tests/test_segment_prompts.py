"""Tests del armado del prompt final de cada segmento text-to-video en job_runner:
el look compartido (`video_style`) se anexa a todos los beats para que los clips,
generados por separado, corten como un solo video."""

import job_runner as jr


def test_segment_prompt_appends_style_and_no_text():
    p = jr._segment_prompt("A coin drops onto a stack.", "shot on 35mm, warm amber light")
    assert p.startswith("A coin drops onto a stack.")
    assert "shot on 35mm, warm amber light" in p
    assert p.endswith(jr._NO_TEXT_SUFFIX)


def test_segment_prompt_falls_back_to_default_style():
    p = jr._segment_prompt("A coin drops onto a stack.", "")
    assert jr._DEFAULT_VIDEO_STYLE in p
    assert p.endswith(jr._NO_TEXT_SUFFIX)


def test_segment_prompt_strips_whitespace():
    p = jr._segment_prompt("  beat  ", "  style  ")
    assert p == f"beat style {jr._NO_TEXT_SUFFIX}"
