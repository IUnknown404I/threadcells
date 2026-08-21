"""Usage P1 HTTP contract tests."""

from unittest.mock import patch


def test_usage_statistics_api_is_explicitly_provider_reported(client):
    response_body = {
        "label": "Provider-reported usage — not a billing statement",
        "global": {
            "provider_run_count": 1,
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
            "total_tokens": None,
        },
        "terminals": [],
        "sessions": [],
        "projects": [],
        "providers": [],
        "profiles": [],
    }
    with patch(
        "cli_agent_orchestrator.api.main.usage_service.statistics", return_value=response_body
    ):
        response = client.get("/usage/statistics")

    assert response.status_code == 200
    assert response.json()["label"].startswith("Provider-reported usage")
