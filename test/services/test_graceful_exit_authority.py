from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.clients.tmux import PaneDeliveryTarget, PaneTargetError
from cli_agent_orchestrator.services import terminal_service


def _metadata(*, lifecycle="running", provider="codex"):
    return {
        "id": "abcd1234",
        "tmux_session": "cao-live",
        "tmux_window": "worker-a",
        "provider": provider,
        "runtime_lifecycle": lifecycle,
    }


def _provider(*, command="/exit", alive=True):
    provider = MagicMock()
    provider.terminal_id = "abcd1234"
    provider.session_name = "cao-live"
    provider.window_name = "worker-a"
    provider.exit_cli.return_value = command
    provider.is_process_alive.return_value = alive
    return provider


def _wire(monkeypatch, *, metadata=None, provider=None, claim="dispatch"):
    metadata = metadata or _metadata()
    provider = provider or _provider()
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(terminal_service, "prepare_terminal_for_destruction", lambda *_: None)
    monkeypatch.setattr(terminal_service, "claim_terminal_runtime_exit", lambda *_: claim)
    monkeypatch.setattr(terminal_service, "cancel_child_assignments_for_terminal", lambda *_: None)
    monkeypatch.setattr(terminal_service, "cancel_workflows_for_terminal", lambda *_: None)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_pane_target",
        lambda *_: PaneDeliveryTarget("%41", "codex"),
    )
    return provider


def test_live_detached_reconstructed_provider_delivers_to_exact_pane(monkeypatch):
    provider = _wire(monkeypatch)
    sent = []
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "send_keys",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    monkeypatch.setattr(terminal_service, "reconcile_terminal_runtime", lambda *_: True)

    result = terminal_service.exit_terminal("abcd1234")

    assert result.success is True
    assert result.outcome == "command_delivered"
    assert result.command_delivered is True
    assert sent == [(("cao-live", "worker-a", "/exit"), {"enter_count": 1, "pane_id": "%41"})]
    provider.exit_cli.assert_called_once_with()


def test_provider_specific_control_key_delivery_uses_exact_pane(monkeypatch):
    _wire(monkeypatch, provider=_provider(command="C-d"))
    sent = []
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "send_special_key",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    monkeypatch.setattr(terminal_service, "reconcile_terminal_runtime", lambda *_: True)

    result = terminal_service.exit_terminal("abcd1234")

    assert result.success is True
    assert sent == [(("cao-live", "worker-a", "C-d"), {"pane_id": "%41"})]


def test_already_exited_is_honest_and_never_claims_or_delivers(monkeypatch):
    metadata = _metadata(lifecycle="exited")
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    claim = MagicMock()
    send = MagicMock()
    monkeypatch.setattr(terminal_service, "claim_terminal_runtime_exit", claim)
    monkeypatch.setattr(terminal_service.tmux_client, "send_keys", send)

    result = terminal_service.exit_terminal("abcd1234")

    assert result.success is True
    assert result.outcome == "already_exited"
    assert result.command_delivered is False
    claim.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize(
    ("reason_code", "uncertain"),
    [
        ("EXIT_WINDOW_MISSING", False),
        ("EXIT_PANE_MISSING", False),
        ("EXIT_PANE_AMBIGUOUS", False),
        ("EXIT_INVENTORY_UNCERTAIN", True),
    ],
)
def test_missing_ambiguous_or_uncertain_target_fails_before_claim(
    monkeypatch, reason_code, uncertain
):
    _wire(monkeypatch)
    claim = MagicMock()
    send = MagicMock()
    monkeypatch.setattr(terminal_service, "claim_terminal_runtime_exit", claim)
    monkeypatch.setattr(terminal_service.tmux_client, "send_keys", send)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_pane_target",
        MagicMock(side_effect=PaneTargetError(reason_code, "target unavailable")),
    )

    with pytest.raises(terminal_service.ExitAuthorityError) as raised:
        terminal_service.exit_terminal("abcd1234")

    assert raised.value.reason_code == reason_code
    assert raised.value.inventory_uncertain is uncertain
    claim.assert_not_called()
    send.assert_not_called()


def test_wrong_provider_terminal_fails_before_tmux_or_claim(monkeypatch):
    provider = _provider()
    provider.terminal_id = "deadbeef"
    _wire(monkeypatch, provider=provider)
    target = MagicMock()
    claim = MagicMock()
    monkeypatch.setattr(terminal_service.tmux_client, "exact_pane_target", target)
    monkeypatch.setattr(terminal_service, "claim_terminal_runtime_exit", claim)

    with pytest.raises(terminal_service.ExitAuthorityError) as raised:
        terminal_service.exit_terminal("abcd1234")

    assert raised.value.reason_code == "EXIT_PROVIDER_AUTHORITY_STALE"
    target.assert_not_called()
    claim.assert_not_called()


def test_stale_terminal_target_fails_before_claim(monkeypatch):
    original = _metadata()
    stale = {**original, "tmux_window": "replacement"}
    reads = iter([original, stale])
    provider = _provider()
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: next(reads))
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_pane_target",
        lambda *_: PaneDeliveryTarget("%41", "codex"),
    )
    claim = MagicMock()
    monkeypatch.setattr(terminal_service, "claim_terminal_runtime_exit", claim)

    with pytest.raises(terminal_service.ExitAuthorityError) as raised:
        terminal_service.exit_terminal("abcd1234")

    assert raised.value.reason_code == "EXIT_TERMINAL_AUTHORITY_STALE"
    claim.assert_not_called()


def test_pending_retry_never_reports_success_without_delivery(monkeypatch):
    _wire(monkeypatch, metadata=_metadata(lifecycle="exit_pending"), claim="observe")
    send = MagicMock()
    monkeypatch.setattr(terminal_service.tmux_client, "send_keys", send)
    monkeypatch.setattr(terminal_service, "reconcile_terminal_runtime", lambda *_: None)
    monkeypatch.setattr(terminal_service, "EXIT_CONFIRMATION_TIMEOUT_SECONDS", 0.0)

    result = terminal_service.exit_terminal("abcd1234")

    assert result.success is False
    assert result.outcome == "exit_pending"
    assert result.command_delivered is False
    send.assert_not_called()
