#!/usr/bin/env python3
"""Validate and stage CAO.OPS.P1 host artifacts without activating them."""

from __future__ import annotations

import argparse
import fcntl
import grp
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

BEGIN = "<!-- CAO.OPS.P1 BEGIN -->"
END = "<!-- CAO.OPS.P1 END -->"
RELEASE_ADMIN_GROUP = "threadcells-release-admin"
CRITICAL_PACKAGE_FILES = (
    "cli_agent_orchestrator/__init__.py",
    "cli_agent_orchestrator/runtime_generation.py",
    "cli_agent_orchestrator/mcp_server/server.py",
    "cli_agent_orchestrator/services/operations_service.py",
    "cli_agent_orchestrator/services/terminal_service.py",
    "cli_agent_orchestrator/services/inbox_service.py",
    "cli_agent_orchestrator/services/workflow_service.py",
    "cli_agent_orchestrator/clients/database.py",
    "cli_agent_orchestrator/models/terminal.py",
    "cli_agent_orchestrator/utils/mcp_runtime.py",
)


def fail(reason: str) -> None:
    raise SystemExit(f"OPS_P1_STAGE_FAILED reason_code={reason}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(root: Path) -> str:
    """Hash file bytes and symlink targets without following runtime links."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root)).encode("utf-8")
        if path.is_symlink():
            digest.update(b"link\0" + relative + b"\0" + str(path.readlink()).encode("utf-8"))
        elif path.is_file():
            digest.update(b"file\0" + relative + b"\0" + path.read_bytes())
    return digest.hexdigest()


def _normalized_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _source_project_metadata(source: Path) -> tuple[str, str, tuple[str, ...]]:
    """Read the small stable subset of pyproject metadata needed for staging."""
    text = (source / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"^\[project\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.MULTILINE)
    if project is None:
        fail("PACKAGE_METADATA_UNAVAILABLE")
    name = re.search(r'^name\s*=\s*"([^"]+)"\s*$', project.group(1), re.MULTILINE)
    version = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project.group(1), re.MULTILINE)
    scripts = re.search(r"^\[project\.scripts\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.MULTILINE)
    if name is None or version is None or scripts is None:
        fail("PACKAGE_METADATA_UNAVAILABLE")
    script_names = tuple(
        match.group(1)
        for match in re.finditer(
            r'^"?([^"=]+?)"?\s*=\s*"[^"]+"\s*$', scripts.group(1), re.MULTILINE
        )
    )
    if not script_names:
        fail("PACKAGE_METADATA_UNAVAILABLE")
    return name.group(1), version.group(1), script_names


def _wheel_metadata(wheel: Path) -> tuple[str, str]:
    try:
        with ZipFile(wheel) as archive:
            metadata_paths = [
                path for path in archive.namelist() if path.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                fail("WHEEL_METADATA_INVALID")
            metadata = archive.read(metadata_paths[0]).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        fail("WHEEL_METADATA_INVALID")
    name = re.search(r"^Name:\s*(.+)$", metadata, re.MULTILINE)
    version = re.search(r"^Version:\s*(.+)$", metadata, re.MULTILINE)
    if name is None or version is None:
        fail("WHEEL_METADATA_INVALID")
    return name.group(1).strip(), version.group(1).strip()


def _wheel_package_payload(wheel: Path) -> dict[PurePosixPath, bytes]:
    """Return the wheel files installed into site-packages, excluding metadata."""
    payload: dict[PurePosixPath, bytes] = {}
    try:
        with ZipFile(wheel) as archive:
            for name in archive.namelist():
                relative = PurePosixPath(name)
                if name.endswith("/"):
                    continue
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not relative.parts
                    or relative.parts[0].endswith((".dist-info", ".data"))
                ):
                    if relative.is_absolute() or ".." in relative.parts:
                        fail("WHEEL_PAYLOAD_INVALID")
                    continue
                if relative in payload:
                    fail("WHEEL_PAYLOAD_INVALID")
                payload[relative] = archive.read(name)
    except (BadZipFile, OSError):
        fail("WHEEL_PAYLOAD_INVALID")
    if not payload:
        fail("WHEEL_PAYLOAD_INVALID")
    return payload


def _candidate_site_packages(candidate_python: Path, candidate_root: Path) -> Path:
    completed = subprocess.run(
        [
            str(candidate_python),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        fail("CANDIDATE_SITE_PACKAGES_UNAVAILABLE")
    site_packages = Path(completed.stdout.strip()).resolve()
    if not site_packages.is_dir() or not _is_within(site_packages, candidate_root.resolve()):
        fail("CANDIDATE_SITE_PACKAGES_INVALID")
    return site_packages


def _remove_candidate_path(path: Path) -> None:
    """Remove a path in the candidate without following a runtime symlink."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _seal_candidate_tree(candidate_root: Path, owner: tuple[int, int]) -> None:
    """Make a staged runtime immutable to the service user but removable by Housekeeping."""
    paths = (candidate_root, *candidate_root.rglob("*"))
    for path in paths:
        if path.is_symlink():
            os.lchown(path, *owner)
            continue
        os.chown(path, *owner)
        if path.is_dir():
            path.chmod(0o775)
            continue
        executable = bool(path.stat().st_mode & 0o111)
        path.chmod(0o755 if executable else 0o644)


def _ensure_trusted_release_anchor(
    system_root: Path,
    release_state_root: Path,
    release_root: Path,
    *,
    trusted_owner: tuple[int, int],
    release_owner: tuple[int, int],
) -> None:
    """Create a root-anchored release store outside runtime-owned state."""
    expected_state_root = system_root / "var/lib/threadcells"
    if release_state_root != expected_state_root or release_root != release_state_root / "releases":
        fail("RELEASE_CONTROL_ROOT_INVALID")
    current = system_root
    for part in ("var", "lib"):
        current /= part
        if current.is_symlink():
            fail("RELEASE_CONTROL_ROOT_INVALID")
        if not current.exists():
            current.mkdir(mode=0o755)
            os.chown(current, *trusted_owner)
            current.chmod(0o755)
        if not current.is_dir():
            fail("RELEASE_CONTROL_ROOT_INVALID")
        current_stat = current.stat()
        if (current_stat.st_uid, current_stat.st_gid) != trusted_owner or (
            current_stat.st_mode & 0o022
        ):
            fail("RELEASE_CONTROL_ROOT_UNTRUSTED")
    for path, owner, mode in (
        (release_state_root, trusted_owner, 0o755),
        (release_root, release_owner, 0o775),
    ):
        if path.is_symlink():
            fail("RELEASE_CONTROL_ROOT_INVALID")
        if not path.exists():
            path.mkdir(mode=mode)
            os.chown(path, *owner)
            path.chmod(mode)
        if not path.is_dir():
            fail("RELEASE_CONTROL_ROOT_INVALID")
        path_stat = path.stat()
        if (path_stat.st_uid, path_stat.st_gid) != owner or (path_stat.st_mode & 0o777) != mode:
            fail("RELEASE_CONTROL_ROOT_UNTRUSTED")


def _validate_candidate_location(release_root: Path, candidate_root: Path) -> None:
    if (
        not candidate_root.is_absolute()
        or candidate_root.is_symlink()
        or candidate_root.name in {"", ".", ".."}
    ):
        fail("CANDIDATE_TARGET_INVALID")
    expected = release_root / candidate_root.name
    if candidate_root.parent != release_root or candidate_root != expected:
        fail("CANDIDATE_TARGET_INVALID")


def _validate_control_file(path: Path, expected: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        fail("CONTROL_FILE_PATH_INVALID")
    if path != expected:
        fail("CONTROL_FILE_PATH_INVALID")
    if path.exists() and not path.is_file():
        fail("CONTROL_FILE_PATH_INVALID")


def _purge_candidate_package_payload(
    site_packages: Path, payload: dict[PurePosixPath, bytes]
) -> None:
    """Remove prior package roots so files absent from a new wheel cannot survive."""
    for top_level in sorted({relative.parts[0] for relative in payload}):
        target = site_packages / top_level
        if target.parent != site_packages:
            fail("CANDIDATE_PACKAGE_PATH_INVALID")
        _remove_candidate_path(target)


def _validate_candidate_package_payload(
    site_packages: Path, payload: dict[PurePosixPath, bytes]
) -> None:
    """Require candidate package files to be exactly the wheel payload (apart from bytecode)."""
    actual: dict[PurePosixPath, bytes] = {}
    for top_level in sorted({relative.parts[0] for relative in payload}):
        target = site_packages / top_level
        if target.is_symlink() or not target.exists():
            fail("CANDIDATE_PACKAGE_PAYLOAD_MISMATCH")
        paths = (target,) if target.is_file() else target.rglob("*")
        for path in paths:
            if path.is_symlink():
                fail("CANDIDATE_PACKAGE_PAYLOAD_MISMATCH")
            if not path.is_file():
                continue
            relative = PurePosixPath(path.relative_to(site_packages).as_posix())
            if "__pycache__" in relative.parts:
                continue
            actual[relative] = path.read_bytes()
    if actual != payload:
        fail("CANDIDATE_PACKAGE_PAYLOAD_MISMATCH")


def _source_commit(source: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, capture_output=True, check=False
    )
    if completed.returncode:
        fail("SOURCE_COMMIT_UNAVAILABLE")
    return completed.stdout.strip()


def _candidate_package_path(candidate_python: Path) -> Path:
    completed = subprocess.run(
        [
            str(candidate_python),
            "-c",
            "import pathlib, cli_agent_orchestrator; "
            "print(pathlib.Path(cli_agent_orchestrator.__file__).resolve())",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        fail("CANDIDATE_IMPORT_FAILED")
    package_init = Path(completed.stdout.strip())
    if not package_init.is_file():
        fail("CANDIDATE_IMPORT_PATH_INVALID")
    return package_init


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_candidate_runtime(
    source: Path,
    candidate_runtime: Path,
    wheel: Path,
    expected_commit: str,
) -> None:
    candidate_python = candidate_runtime / "bin/python"
    if not candidate_python.is_file():
        fail("CANDIDATE_PYTHON_UNAVAILABLE")
    source_name, source_version, script_names = _source_project_metadata(source)
    wheel_name, wheel_version = _wheel_metadata(wheel)
    if (
        _normalized_distribution_name(source_name) != _normalized_distribution_name(wheel_name)
        or source_version != wheel_version
        or _source_commit(source) != expected_commit
    ):
        fail("PACKAGE_SOURCE_IDENTITY_MISMATCH")
    payload = _wheel_package_payload(wheel)
    site_packages = _candidate_site_packages(candidate_python, candidate_runtime)
    _purge_candidate_package_payload(site_packages, payload)
    pip_available = subprocess.run(
        [str(candidate_python), "-c", "import pip"],
        text=True,
        capture_output=True,
        check=False,
    )
    if pip_available.returncode:
        bootstrapped = subprocess.run(
            [str(candidate_python), "-m", "ensurepip", "--upgrade"],
            text=True,
            capture_output=True,
            check=False,
        )
        if bootstrapped.returncode:
            fail("CANDIDATE_PIP_BOOTSTRAP_FAILED")
    installed = subprocess.run(
        [
            str(candidate_python),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            "--no-index",
            str(wheel),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if installed.returncode:
        fail("CANDIDATE_WHEEL_INSTALL_FAILED")
    installed_version = subprocess.run(
        [
            str(candidate_python),
            "-c",
            f"from importlib.metadata import version; print(version({source_name!r}))",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if installed_version.returncode or installed_version.stdout.strip() != source_version:
        fail("CANDIDATE_PACKAGE_IDENTITY_MISMATCH")
    _validate_candidate_package_payload(site_packages, payload)
    expected_shebang = f"#!{candidate_python}\n".encode("utf-8")
    expected_shell_wrapper = (
        b"#!/bin/sh\n'''exec' " + os.fsencode(candidate_python) + b" \"$0\" \"$@\"\n' '''\n"
    )
    for script_name in script_names:
        script = candidate_runtime / "bin" / script_name
        if not script.is_file() or not script.read_bytes().startswith(
            (expected_shebang, expected_shell_wrapper)
        ):
            fail("CANDIDATE_CONSOLE_SHEBANG_MISMATCH")
    package_init = _candidate_package_path(candidate_python)
    candidate_root = candidate_runtime.resolve()
    if not _is_within(package_init, candidate_root):
        fail("CANDIDATE_IMPORT_PATH_INVALID")
    package_root = package_init.parent
    for relative in CRITICAL_PACKAGE_FILES:
        source_file = source / "src" / relative
        candidate_file = package_root.parent / relative
        if (
            not source_file.is_file()
            or not candidate_file.is_file()
            or _sha256(source_file) != _sha256(candidate_file)
        ):
            fail("CANDIDATE_CRITICAL_HASH_MISMATCH")


def _stage_candidate_runtime(
    source: Path,
    base_runtime: Path,
    candidate_root: Path,
    wheel: Path,
    expected_commit: str,
    candidate_owner: tuple[int, int],
) -> None:
    base_runtime = base_runtime.resolve()
    candidate_root = candidate_root.resolve()
    candidate_runtime = candidate_root / "runtime"
    if not base_runtime.is_dir():
        fail("BASE_RUNTIME_UNAVAILABLE")
    if not wheel.is_file():
        fail("LOCAL_WHEEL_UNAVAILABLE")
    if (
        candidate_root.exists()
        or _is_within(candidate_root, base_runtime)
        or _is_within(base_runtime, candidate_root)
    ):
        fail("CANDIDATE_TARGET_INVALID")
    base_before = _tree_hash(base_runtime)
    candidate_created = False
    try:
        candidate_root.mkdir(parents=True)
        candidate_created = True
        shutil.copytree(base_runtime, candidate_runtime, symlinks=True)
        _validate_candidate_runtime(source, candidate_runtime, wheel.resolve(), expected_commit)
        _seal_candidate_tree(candidate_root, candidate_owner)
        if _tree_hash(base_runtime) != base_before:
            fail("BASE_RUNTIME_MUTATED")
    except BaseException:
        if candidate_created:
            shutil.rmtree(candidate_root)
        if _tree_hash(base_runtime) != base_before:
            fail("BASE_RUNTIME_MUTATED")
        raise


def _atomic_json(
    path: Path,
    value: dict,
    *,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        fail("CONTROL_FILE_PATH_INVALID")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
            if owner is not None:
                current = os.fstat(handle.fileno())
                if (current.st_uid, current.st_gid) != owner:
                    os.fchown(handle.fileno(), *owner)
        os.replace(temporary, path)
        parent_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_runtime_launcher(path: Path, target: Path, *, owner: tuple[int, int]) -> None:
    """Point the compatibility launcher at the canonical active runtime."""
    if not path.parent.exists():
        path.parent.mkdir(mode=0o750, parents=True)
        os.chown(path.parent, *owner)
        path.parent.chmod(0o750)
    if path.parent.is_symlink() or not path.parent.is_dir():
        fail("MCP_RUNTIME_LAUNCHER_INVALID")
    parent_stat = path.parent.stat()
    if (parent_stat.st_uid, parent_stat.st_gid) != owner or parent_stat.st_mode & 0o022:
        fail("MCP_RUNTIME_LAUNCHER_UNTRUSTED")
    if path.exists() and not path.is_symlink():
        fail("MCP_RUNTIME_LAUNCHER_INVALID")
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        fail("MCP_RUNTIME_LAUNCHER_TEMPORARY_EXISTS")
    try:
        temporary.symlink_to(target)
        os.lchown(temporary, *owner)
        os.replace(temporary, path)
        parent_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _record_staged_candidate(
    metadata_path: Path,
    candidate_root: Path,
    commit: str,
    *,
    metadata_owner: tuple[int, int],
    release_owner: tuple[int, int],
) -> None:
    """Publish bounded release references only after an immutable stage validates."""
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            fail("RELEASE_METADATA_INVALID")
        if (
            metadata.get("schema_version") != 1
            or not isinstance(metadata.get("rollback_releases"), list)
            or not isinstance(metadata.get("candidate_releases"), list)
        ):
            fail("RELEASE_METADATA_INVALID")
    else:
        metadata = {
            "schema_version": 1,
            "active_release": None,
            "rollback_releases": [],
            "candidate_releases": [],
        }
    candidate = str(candidate_root.resolve())
    candidates = [candidate] + [
        value
        for value in metadata["candidate_releases"]
        if isinstance(value, str) and value != candidate
    ]
    _atomic_json(
        candidate_root / ".threadcells-release.json",
        {
            "schema_version": 1,
            "release_id": candidate_root.name,
            "source_commit": commit,
            "state": "candidate",
        },
        mode=0o644,
        owner=release_owner,
    )
    metadata["candidate_releases"] = candidates[:2]
    _atomic_json(metadata_path, metadata, mode=0o644, owner=metadata_owner)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--agent-control-root", type=Path, required=True)
    parser.add_argument("--system-root", type=Path, required=True)
    parser.add_argument("--base-runtime", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--release-lock", type=Path)
    parser.add_argument("--release-metadata", type=Path)
    parser.add_argument("--test-unprivileged-staging", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    source = args.source_root
    config_source = source / "src/cli_agent_orchestrator/config/cao-operations.json"
    policy_source = source / "deployment/cao-ops-policy.md"
    unit_source = source / "deployment/systemd"
    policy_target = args.agent_control_root / "policy/ORCHESTRATION.md"
    required_units = (
        "agent-control-housekeeping.service",
        "agent-control-housekeeping.timer",
        "agent-control-housekeeping-weekly.service",
        "agent-control-housekeeping-weekly.timer",
    )
    runtime_dropin_source = unit_source / "agent-control-cao.service.d/threadcells-runtime.conf"
    if not config_source.is_file() or not policy_source.is_file() or not policy_target.is_file():
        fail("SOURCE_OR_POLICY_UNAVAILABLE")
    if any(not (unit_source / name).is_file() for name in required_units) or not (
        runtime_dropin_source.is_file()
    ):
        fail("SYSTEMD_ARTIFACT_UNAVAILABLE")
    candidate_arguments = (args.base_runtime, args.candidate_root, args.wheel, args.expected_commit)
    if any(value is not None for value in candidate_arguments) and any(
        value is None for value in candidate_arguments
    ):
        fail("CANDIDATE_ARGUMENTS_INCOMPLETE")
    config = json.loads(config_source.read_text(encoding="utf-8"))
    # The packaged file is intentionally host-neutral. Staging binds the
    # canonical ThreadCells ownership root atomically so Housekeeping cannot
    # silently inspect an unrelated default tree.
    root = args.agent_control_root.resolve()
    system_root = args.system_root.resolve()
    runtime_user = root.owner()
    production_system_root = system_root == Path("/")
    if args.test_unprivileged_staging and production_system_root:
        fail("TEST_STAGING_OVERRIDE_FORBIDDEN")
    if (
        all(value is not None for value in candidate_arguments)
        and os.geteuid() != 0
        and not args.test_unprivileged_staging
    ):
        fail("STAGING_PRIVILEGE_REQUIRED")
    if production_system_root and os.geteuid() != 0:
        fail("STAGING_PRIVILEGE_REQUIRED")
    if production_system_root:
        try:
            release_admin_gid = grp.getgrnam(RELEASE_ADMIN_GROUP).gr_gid
        except KeyError:
            fail("RELEASE_ADMIN_GROUP_UNAVAILABLE")
        release_owner = (0, release_admin_gid)
        trusted_owner = (0, 0)
    else:
        release_owner = (os.geteuid(), os.getegid())
        trusted_owner = release_owner
    release_state_root = system_root / "var/lib/threadcells"
    release_root = release_state_root / "releases"
    canonical_release_lock = release_state_root / "release-staging.lock"
    canonical_release_metadata = release_state_root / "release-metadata.json"
    canonical_active_release = release_state_root / "active"
    mcp_runtime_launcher = root / "bin/threadcells-mcp-server"
    mcp_runtime_target = canonical_active_release / "runtime/bin/threadcells-mcp-server"
    config.update(
        root=str(root),
        lock_dir=str(root / "state/cao/locks"),
        release_staging_lock=str(canonical_release_lock),
        release_metadata=str(canonical_release_metadata),
        active_release_link=str(canonical_active_release),
        release_roots=[str(release_root)],
        release_admin_group=RELEASE_ADMIN_GROUP,
        release_control_uid=trusted_owner[0],
        runtime_user=runtime_user,
        playwright_manifest_roots=[str(root / "sources"), str(root / "projects")],
        playwright_browser_cache=str(root / "cache/ms-playwright"),
        package_caches=[
            {"name": "uv", "path": str(root / "cache/uv"), "command": ["uv", "cache", "prune"]},
            {
                "name": "pnpm",
                "path": str(root / "cache/pnpm"),
                "command": ["pnpm", "store", "prune"],
            },
            {
                "name": "npm",
                "path": str(root / "cache/npm"),
                "command": ["npm", "cache", "clean", "--force"],
            },
        ],
    )
    if (
        config.get("max_resident_supervisors") != 5
        or config.get("max_provider_executions") != 3
        or config.get("max_work_contexts") != 2
        or config.get("max_heavy_execution_slots") != 1
    ):
        fail("EFFECTIVE_LIMITS_INVALID")
    policy = policy_target.read_text(encoding="utf-8")
    block = f"{BEGIN}\n{policy_source.read_text(encoding='utf-8').strip()}\n{END}"
    if BEGIN in policy or END in policy:
        if policy.count(BEGIN) != 1 or policy.count(END) != 1:
            fail("POLICY_MARKER_INVALID")
        before, remainder = policy.split(BEGIN, 1)
        _, after = remainder.split(END, 1)
        policy = before.rstrip() + "\n\n" + block + after
    else:
        policy = policy.rstrip() + "\n\n" + block + "\n"
    config_target = args.system_root / "etc/agent-control/cao-operations.json"
    unit_targets = tuple(args.system_root / "etc/systemd/system" / name for name in required_units)
    runtime_dropin_target = (
        args.system_root / "etc/systemd/system/agent-control-cao.service.d/threadcells-runtime.conf"
    )
    targets = [
        config_target,
        *unit_targets,
        runtime_dropin_target,
        mcp_runtime_launcher,
        policy_target,
    ]
    if args.dry_run:
        print("OPS_P1_STAGE_DRY_RUN " + " ".join(str(path) for path in targets))
        return 0
    if all(value is not None for value in candidate_arguments):
        release_lock = args.release_lock or Path(str(config["release_staging_lock"]))
        release_metadata = args.release_metadata or Path(str(config["release_metadata"]))
        _ensure_trusted_release_anchor(
            system_root,
            release_state_root,
            release_root,
            trusted_owner=trusted_owner,
            release_owner=release_owner,
        )
        _validate_candidate_location(release_root, args.candidate_root)
        _validate_control_file(release_lock, canonical_release_lock)
        _validate_control_file(release_metadata, canonical_release_metadata)
        try:
            release_descriptor = os.open(
                release_lock,
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o660,
            )
        except OSError:
            fail("RELEASE_LOCK_INVALID")
        with os.fdopen(release_descriptor, "a+") as lock_handle:
            lock_stat = os.fstat(lock_handle.fileno())
            if not stat.S_ISREG(lock_stat.st_mode):
                fail("RELEASE_LOCK_INVALID")
            os.fchmod(lock_handle.fileno(), 0o660)
            if (lock_stat.st_uid, lock_stat.st_gid) != release_owner:
                os.fchown(lock_handle.fileno(), *release_owner)
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("RELEASE_STAGING_BUSY")
            _stage_candidate_runtime(
                source,
                args.base_runtime,
                args.candidate_root,
                args.wheel,
                args.expected_commit,
                release_owner,
            )
            _record_staged_candidate(
                release_metadata,
                args.candidate_root,
                args.expected_commit,
                metadata_owner=release_owner,
                release_owner=release_owner,
            )
    config_target.parent.mkdir(parents=True, exist_ok=True)
    config_target.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    config_target.chmod(0o644)
    for name, target in zip(required_units, unit_targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(unit_source / name, target)
        target.chmod(0o644)
    runtime_dropin_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(runtime_dropin_source, runtime_dropin_target)
    runtime_dropin_target.chmod(0o644)
    _atomic_runtime_launcher(
        mcp_runtime_launcher,
        mcp_runtime_target,
        owner=(root.stat().st_uid, root.stat().st_gid),
    )
    policy_target.write_text(policy, encoding="utf-8")
    print("OPS_P1_STAGED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
