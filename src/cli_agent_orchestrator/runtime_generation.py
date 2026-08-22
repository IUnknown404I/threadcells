"""Runtime compatibility identities used to fence long-lived MCP sidecars.

The identity is derived from the installed ThreadCells Python sources, Python
runtime, and dependency versions.  It therefore remains stable across an API
process restart with byte-for-byte compatible code, while a promoted runtime
with different privileged implementation code receives a different identity.
"""

import hashlib
import importlib.metadata
import sys
from pathlib import Path

RUNTIME_GENERATION_ENV = "CAO_RUNTIME_GENERATION"
RUNTIME_GENERATION_HEADER = "X-CAO-Runtime-Generation"


def _runtime_compatibility_identity() -> str:
    """Hash the immutable inputs that own privileged sidecar behaviour."""
    digest = hashlib.sha256(b"threadcells-runtime-compatibility-v1\0")
    package_root = Path(__file__).resolve().parent
    for path in sorted(
        package_root.rglob("*.py"),
        key=lambda item: item.relative_to(package_root).as_posix(),
    ):
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        digest.update(b"source\0" + relative + b"\0" + path.read_bytes())

    distributions: list[tuple[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            distributions.append((name.casefold(), distribution.version or ""))
    for name, version in sorted(set(distributions)):
        digest.update(f"distribution\0{name}\0{version}\0".encode("utf-8"))
    digest.update(
        (
            f"python\0{sys.implementation.name}\0"
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\0"
        ).encode("utf-8")
    )
    return digest.hexdigest()


ACTIVE_RUNTIME_GENERATION = _runtime_compatibility_identity()
