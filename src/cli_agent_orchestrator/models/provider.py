from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ProviderType(str, Enum):
    """Provider type enumeration."""

    Q_CLI = "q_cli"
    KIRO_CLI = "kiro_cli"
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    KIMI_CLI = "kimi_cli"
    GEMINI_CLI = "gemini_cli"
    COPILOT_CLI = "copilot_cli"
    OPENCODE_CLI = "opencode_cli"


@dataclass(frozen=True)
class ProviderTurnOutcome:
    """One safe structured outcome for the provider's latest settled turn.

    ``detail_code`` is provider-native metadata, never provider response text.
    Adapters return ``None`` for ordinary completion and for evidence they
    cannot classify with provider-owned structured authority.
    """

    code: str
    detail_code: Optional[str] = None
