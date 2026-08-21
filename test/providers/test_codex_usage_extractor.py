"""Focused tests for the optional Codex usage extractor boundary."""

from cli_agent_orchestrator.providers.codex import CodexProvider


def test_codex_usage_extractor_normalizes_reported_tui_cached_input_tokens():
    provider = CodexProvider("deadbeef", "cao-usage", "worker")
    capture = (
        "› summarize this run\n"
        "• Completed.\n"
        "Token usage: total=1,300 input=1,200 (+ 300 cached) output=100\n"
        "› \n"
        "  ? for shortcuts                     96% context left\n"
    )

    observation = provider.extract_usage_observation(capture)

    assert observation is not None
    assert observation.input_tokens == 1500
    assert observation.cached_input_tokens == 300
    assert observation.output_tokens == 100
    assert observation.total_tokens == 1600
    assert observation.total_tokens == observation.input_tokens + observation.output_tokens
    assert observation.extractor == "codex_tui_completion_v2"


def test_codex_usage_extractor_preserves_reported_zero_cached_input_tokens():
    provider = CodexProvider("deadbeef", "cao-usage", "worker")
    capture = (
        "› summarize this run\n"
        "• Completed.\n"
        "Token usage: input=1,200 cached_input=0 output=400 total=1,600\n"
        "› \n"
    )

    observation = provider.extract_usage_observation(capture)

    assert observation is not None
    assert observation.input_tokens == 1200
    assert observation.cached_input_tokens == 0
    assert observation.output_tokens == 400
    assert observation.total_tokens == 1600


def test_codex_usage_extractor_keeps_absent_cached_input_unreported():
    provider = CodexProvider("deadbeef", "cao-usage", "worker")
    capture = (
        "› summarize this run\n"
        "• Completed.\n"
        "Token usage: input=1,200 output=400 total=1,600\n"
        "› \n"
    )

    observation = provider.extract_usage_observation(capture)

    assert observation is not None
    assert observation.input_tokens == 1200
    assert observation.cached_input_tokens is None
    assert observation.output_tokens == 400
    assert observation.total_tokens == 1600


def test_codex_usage_extractor_records_run_without_estimating_missing_tokens():
    provider = CodexProvider("deadbeef", "cao-usage", "worker")
    capture = "› summarize this run\n• Completed.\n› \n? for shortcuts 96% context left\n"

    observation = provider.extract_usage_observation(capture)

    assert observation is not None
    assert observation.input_tokens is None
    assert observation.cached_input_tokens is None
    assert observation.output_tokens is None
    assert observation.total_tokens is None


def test_codex_usage_identity_is_stable_for_a_read_but_distinguishes_duplicate_invocations():
    provider = CodexProvider("deadbeef", "cao-usage", "worker")
    one = "› repeat\n• Done\n› \n? for shortcuts\n"
    two = one + "› repeat\n• Done\n› \n? for shortcuts\n"

    assert (
        provider.extract_usage_observation(one).source_run_identity
        == provider.extract_usage_observation(one).source_run_identity
    )
    assert (
        provider.extract_usage_observation(one).source_run_identity
        != provider.extract_usage_observation(two).source_run_identity
    )
