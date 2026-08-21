"""Provider observations used by the lightweight operational usage ledger."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class UsageObservation:
    """One provider-reported usage observation for a completed invocation.

    Token values are optional because a provider may expose a completion without
    exposing its token telemetry.  CAO never tokenizes prompts or responses.
    """

    source_run_identity: str
    extractor: str
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    cache_write_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    reasoning_output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
