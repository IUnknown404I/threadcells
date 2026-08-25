"""Regression coverage for built-in metadata surfaced by /agents/profiles."""

from importlib import resources
from unittest.mock import patch

import frontmatter

from cli_agent_orchestrator.utils.agent_profiles import list_agent_profiles

APPROVED_ACTIVE_PROFILE_METADATA = [
    {
        "name": "architect_sol_high",
        "description": "Complex architecture, migrations, and foundational decisions.",
        "source": "built-in",
    },
    {
        "name": "code_supervisor",
        "description": "Coordinates coding tasks across agents.",
        "source": "built-in",
    },
    {
        "name": "critical_sol_xhigh_owner",
        "description": "OWNER ONLY — exceptional direct critical architecture and implementation.",
        "source": "built-in",
    },
    {
        "name": "developer",
        "description": "Implements and fixes code.",
        "source": "built-in",
    },
    {
        "name": "developer_sol_medium",
        "description": "Reasoning-heavy cross-subsystem and subtle invariant implementation.",
        "source": "built-in",
    },
    {
        "name": "developer_terra_high",
        "description": "Important product work and difficult bounded defects or refactors.",
        "source": "built-in",
    },
    {
        "name": "developer_terra_medium",
        "description": "Routine, bounded, low-ambiguity production implementation.",
        "source": "built-in",
    },
    {
        "name": "framer_connect_luna_low",
        "description": "Read-only Framer authorization and connection preflight.",
        "source": "built-in",
    },
    {
        "name": "frontend_sol_medium",
        "description": "Production frontend with high fidelity to approved visual authority.",
        "source": "built-in",
    },
    {
        "name": "reviewer",
        "description": "Reviews code for correctness and risks.",
        "source": "built-in",
    },
    {
        "name": "reviewer_sol_high",
        "description": "Critical final review.",
        "source": "built-in",
    },
    {
        "name": "reviewer_sol_medium",
        "description": "Broad cross-module, architectural, and visual review.",
        "source": "built-in",
    },
    {
        "name": "reviewer_terra_high",
        "description": "Primary comprehensive default reviewer.",
        "source": "built-in",
    },
    {
        "name": "strategist_sol_medium",
        "description": "Rare strategic planning and replanning.",
        "source": "built-in",
    },
    {
        "name": "supervisor_sol_medium",
        "description": "High-reasoning orchestration for important, risky, cross-module, and architecture-sensitive workflows.",
        "source": "built-in",
    },
    {
        "name": "supervisor_terra_medium",
        "description": "Default everyday orchestrator for ordinary workflows.",
        "source": "built-in",
    },
    {
        "name": "uiux_sol_high",
        "description": "UI/UX, visual analysis, and design systems.",
        "source": "built-in",
    },
    {
        "name": "worker_luna_medium",
        "description": "Bulk, simple, and mechanical tasks.",
        "source": "built-in",
    },
]


def _contains_cyrillic(text: str) -> bool:
    return any("\u0400" <= char <= "\u052f" for char in text)


def test_active_builtin_profile_descriptions_are_english_metadata():
    store = resources.files("cli_agent_orchestrator.agent_store")
    descriptions = [
        frontmatter.loads(item.read_text()).metadata.get("description", "")
        for item in store.iterdir()
        if item.name.endswith(".md")
    ]

    assert descriptions
    assert all(description.strip() for description in descriptions)
    assert not any(_contains_cyrillic(description) for description in descriptions)
    assert all(description.endswith(".") for description in descriptions)


def test_packaged_legacy_inventory_matches_approved_metadata(tmp_path):
    """Pin packaged discovery without depending on private host profile state."""
    with (
        patch(
            "cli_agent_orchestrator.services.control_plane_registry.registry_is_initialized",
            return_value=False,
        ),
        patch("cli_agent_orchestrator.utils.agent_profiles.LOCAL_AGENT_STORE_DIR", tmp_path),
        patch("cli_agent_orchestrator.services.settings_service.get_agent_dirs", return_value={}),
        patch(
            "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
            return_value=[],
        ),
    ):
        profiles = list_agent_profiles()

    assert profiles == APPROVED_ACTIVE_PROFILE_METADATA
