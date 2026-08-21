"""Build hooks that keep wheel assets tied to the authoritative web source."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Build the production web bundle immediately before assembling a wheel."""

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        # Editable installs must not unexpectedly invoke a production Node
        # build; standard wheel builds are the distributable boundary.
        if self.target_name != "wheel" or version != "standard":
            return

        root = Path(self.root)
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("npm is required to build the ThreadCells web UI for a wheel")

        web = root / "web"
        install = [npm, "ci"]
        if os.environ.get("THREADCELLS_SOURCE_REVISION"):
            install.extend(("--offline", "--ignore-scripts"))
        subprocess.run(install, cwd=web, check=True)
        subprocess.run([npm, "run", "build"], cwd=web, check=True)

        index = root / "src" / "cli_agent_orchestrator" / "web_ui" / "index.html"
        if not index.is_file():
            raise RuntimeError(
                "web UI build did not produce src/cli_agent_orchestrator/web_ui/index.html"
            )
