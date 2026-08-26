import hashlib
import importlib.util
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

SOURCE = Path(__file__).parents[1]


def _project_version() -> str:
    text = (SOURCE / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"^\[project\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.MULTILINE)
    assert project is not None
    version = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project.group(1), re.MULTILINE)
    assert version is not None
    return version.group(1)


PROJECT_VERSION = _project_version()


def _stage_module():
    path = SOURCE / "deployment/stage-ops-p1.py"
    spec = importlib.util.spec_from_file_location("threadcells_stage_ops_p1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_staging_activates_and_verifies_full_cleanup_socket(tmp_path):
    stage = _stage_module()
    socket_path = tmp_path / "full-cleanup.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        socket_path.chmod(0o600)
        owner = socket_path.stat()
        commands = []

        def run(command, **kwargs):
            commands.append((command, kwargs))
            return Namespace(returncode=0)

        stage._activate_full_cleanup_socket(
            socket_path,
            expected_owner=(owner.st_uid, owner.st_gid),
            runner=run,
        )

    assert [command for command, _kwargs in commands] == [
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "enable",
            "--now",
            "agent-control-full-cleanup.socket",
        ),
        (
            "systemctl",
            "is-active",
            "--quiet",
            "agent-control-full-cleanup.socket",
        ),
    ]
    assert all(
        kwargs == {"text": True, "capture_output": True, "check": False}
        for _command, kwargs in commands
    )


def test_live_staging_rejects_missing_full_cleanup_socket(tmp_path):
    stage = _stage_module()

    def run(_command, **_kwargs):
        return Namespace(returncode=0)

    with pytest.raises(SystemExit, match="FULL_CLEANUP_SOCKET_NOT_LISTENING"):
        stage._activate_full_cleanup_socket(
            tmp_path / "missing.sock",
            expected_owner=(os.getuid(), os.getgid()),
            runner=run,
        )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root)).encode()
        if path.is_symlink():
            digest.update(b"link\0" + relative + b"\0" + str(path.readlink()).encode())
        elif path.is_file():
            digest.update(b"file\0" + relative + b"\0" + path.read_bytes())
    return digest.hexdigest()


def _local_wheel(destination: Path, *, web_assets: dict[str, bytes] | None = None) -> Path:
    """Make a deterministic wheel with the ThreadCells distribution identity."""
    destination.mkdir(parents=True, exist_ok=True)
    wheel = destination / f"threadcells-{PROJECT_VERSION}-py3-none-any.whl"
    dist_info = f"threadcells-{PROJECT_VERSION}.dist-info"
    scripts = (
        "cao",
        "cao-server",
        "cao-mcp-server",
        "cao-resource-status",
        "cao-heavy-run",
        "cao-housekeeping",
        "threadcells",
        "threadcells-server",
        "threadcells-mcp-server",
        "threadcells-resource-status",
        "threadcells-heavy-run",
        "threadcells-housekeeping",
        "threadcells-full-cleanup-helper",
    )
    with ZipFile(wheel, "w", compression=ZIP_DEFLATED) as archive:
        for relative in (
            "cli_agent_orchestrator/__init__.py",
            "cli_agent_orchestrator/runtime_generation.py",
            "cli_agent_orchestrator/mcp_server/__init__.py",
            "cli_agent_orchestrator/mcp_server/server.py",
            "cli_agent_orchestrator/services/operations_service.py",
            "cli_agent_orchestrator/services/terminal_service.py",
            "cli_agent_orchestrator/services/inbox_service.py",
            "cli_agent_orchestrator/services/workflow_service.py",
            "cli_agent_orchestrator/clients/database.py",
            "cli_agent_orchestrator/models/terminal.py",
            "cli_agent_orchestrator/utils/mcp_runtime.py",
        ):
            archive.writestr(relative, (SOURCE / "src" / relative).read_bytes())
        for relative, content in (web_assets or {}).items():
            archive.writestr(f"cli_agent_orchestrator/web_ui/{relative}", content)
        archive.writestr(
            f"{dist_info}/METADATA",
            "Metadata-Version: 2.1\n" "Name: threadcells\n" f"Version: {PROJECT_VERSION}\n",
        )
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            f"{dist_info}/entry_points.txt",
            "[console_scripts]\n"
            + "".join(f"{name} = cli_agent_orchestrator:main\n" for name in scripts),
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel


def test_stage_ops_p1_is_dry_run_capable_and_idempotent(tmp_path):
    agent_root = tmp_path / "srv/agent-control"
    runtime_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    system_root = tmp_path
    policy = agent_root / "policy/ORCHESTRATION.md"
    policy.parent.mkdir(parents=True)
    policy.write_text("# Existing authority\n")
    command = [
        sys.executable,
        str(SOURCE / "deployment/stage-ops-p1.py"),
        "--source-root",
        str(SOURCE),
        "--agent-control-root",
        str(agent_root),
        "--system-root",
        str(system_root),
        "--worktree-durable-ref",
        "refs/remotes/threadcells-public/main",
    ]
    dry_run = subprocess.run([*command, "--dry-run"], capture_output=True, text=True)
    assert dry_run.returncode == 0
    invalid_ref = subprocess.run(
        [*command[:-1], "refs/heads/invalid..ref", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert invalid_ref.returncode != 0
    assert "WORKTREE_DURABLE_REF_INVALID" in invalid_ref.stdout + invalid_ref.stderr
    assert not (system_root / "etc/agent-control/cao-operations.json").exists()
    assert policy.read_text() == "# Existing authority\n"
    for _ in range(2):
        assert subprocess.run(command, capture_output=True, text=True).returncode == 0
    config = json.loads((system_root / "etc/agent-control/cao-operations.json").read_text())
    assert config["max_resident_supervisors"] == 5
    assert config["max_provider_executions"] == 3
    assert config["max_work_contexts"] == 2
    assert config["max_heavy_execution_slots"] == 1
    assert config["root"] == str(agent_root.resolve())
    release_state_root = system_root / "var/lib/threadcells"
    assert config["release_roots"] == [str(release_state_root / "releases")]
    assert config["release_metadata"] == str(release_state_root / "release-metadata.json")
    assert config["release_staging_lock"] == str(release_state_root / "release-staging.lock")
    assert config["active_release_link"] == str(release_state_root / "active")
    assert config["release_admin_group"] == "threadcells-release-admin"
    assert config["release_control_uid"] == os.getuid()
    assert config["worktree_roots"] == [
        str(agent_root.resolve() / "tmp"),
        str(agent_root.resolve() / "state/cao/worktrees"),
    ]
    assert config["worktree_durable_refs"] == ["refs/remotes/threadcells-public/main"]
    assert config["reproducible_cache_roots"]
    assert config["reproducible_cache_owned_prefixes"] == [
        "threadcells-ci-uv-cache-",
        "threadcells-ci-venv-",
        "threadcells-ci-wheel-",
    ]
    artifact_roots = {item["path"]: item for item in config["full_cleanup_artifact_roots"]}
    temporary_artifacts = artifact_roots[str(agent_root.resolve() / "tmp")]
    assert "threadcells-" in temporary_artifacts["owned_prefixes"]
    assert "cao-" in temporary_artifacts["owned_prefixes"]
    package_artifacts = artifact_roots[str(runtime_home / ".cache")]
    assert package_artifacts["owned_names"] == ["pip", "pnpm"]
    assert package_artifacts["process_names"] == {"pip": "pip", "pnpm": "pnpm"}
    package_caches = {item["name"]: item for item in config["package_caches"]}
    assert package_caches["uv-agent-control"]["path"] == str(agent_root.resolve() / "cache")
    assert package_caches["uv-agent-control"]["full_reclaim"] is True
    assert package_caches["uv-agent-control"]["path_argument"] == "--cache-dir"
    assert package_caches["uv-runtime-user"]["full_reclaim"] is True
    assert package_caches["npm"]["path_argument"] == "--cache"
    assert "pnpm" not in package_caches
    assert {item["category"] for item in config["protected_inventory_roots"]} == {
        "tools",
        "projects",
        "sources",
        "provider_state",
        "runtime_tooling",
    }
    staged_policy = policy.read_text()
    assert staged_policy.count("<!-- CAO.OPS.P1 BEGIN -->") == 1
    assert staged_policy.count("<!-- CAO.OPS.P1 END -->") == 1
    assert "at most one writer" in staged_policy
    assert "## Safe parallelism" in staged_policy
    assert "Default execution mode is one delegated worker" in staged_policy
    assert "Free Provider or Work capacity alone is never" in staged_policy
    assert "materially reduce completion time" in staged_policy
    assert "Do not artificially split one coherent implementation" in staged_policy
    assert "never launch two heavy jobs concurrently" in staged_policy
    for unit in (
        "agent-control-full-cleanup.socket",
        "agent-control-full-cleanup@.service",
        "agent-control-housekeeping.service",
        "agent-control-housekeeping.timer",
        "agent-control-housekeeping-weekly.service",
        "agent-control-housekeeping-weekly.timer",
    ):
        assert (system_root / "etc/systemd/system" / unit).is_file()
    full_cleanup_socket = (
        system_root / "etc/systemd/system/agent-control-full-cleanup.socket"
    ).read_text()
    assert "ListenStream=/run/threadcells/full-cleanup.sock" in full_cleanup_socket
    assert "SocketUser=agentctl" in full_cleanup_socket
    assert "SocketGroup=agentctl" in full_cleanup_socket
    assert "SocketMode=0600" in full_cleanup_socket
    assert "Accept=yes" in full_cleanup_socket
    full_cleanup_helper = (
        system_root / "etc/systemd/system/agent-control-full-cleanup@.service"
    ).read_text()
    assert "User=root" in full_cleanup_helper
    assert "Group=root" in full_cleanup_helper
    assert "Environment=HOME=/home/agentctl" in full_cleanup_helper
    assert "Environment=HOME=/root" not in full_cleanup_helper
    assert "EnvironmentFile=/etc/agent-control/cao.env" in full_cleanup_helper
    assert (
        "ExecStart=/var/lib/threadcells/active/runtime/bin/threadcells-full-cleanup-helper"
        in full_cleanup_helper
    )
    assert "StandardInput=socket" in full_cleanup_helper
    assert "StandardOutput=socket" in full_cleanup_helper
    assert "NoNewPrivileges=true" in full_cleanup_helper
    assert "RestrictAddressFamilies=AF_UNIX" in full_cleanup_helper
    socket_enablement = (
        system_root / "etc/systemd/system/sockets.target.wants/agent-control-full-cleanup.socket"
    )
    assert socket_enablement.is_symlink()
    assert socket_enablement.readlink() == Path("../agent-control-full-cleanup.socket")
    for unit in (
        "agent-control-housekeeping.service",
        "agent-control-housekeeping-weekly.service",
    ):
        unit_text = (system_root / "etc/systemd/system" / unit).read_text()
        assert "SupplementaryGroups=docker threadcells-release-admin" in unit_text
        assert "Environment=PYTHONDONTWRITEBYTECODE=1" in unit_text
        assert "ExecStart=/var/lib/threadcells/active/runtime/bin/cao-housekeeping" in unit_text
    frequent_timer = (
        system_root / "etc/systemd/system/agent-control-housekeeping.timer"
    ).read_text()
    weekly_timer = (
        system_root / "etc/systemd/system/agent-control-housekeeping-weekly.timer"
    ).read_text()
    assert "OnActiveSec=15min" in frequent_timer
    assert "OnActiveSec=20min" in weekly_timer
    assert "OnBootSec=" not in frequent_timer + weekly_timer
    assert "OnUnitActiveSec=15min" in frequent_timer
    assert "OnUnitActiveSec=15min" in weekly_timer
    runtime_dropin = (
        system_root / "etc/systemd/system/agent-control-cao.service.d/threadcells-runtime.conf"
    )
    assert runtime_dropin.is_file()
    runtime_dropin_text = runtime_dropin.read_text()
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in runtime_dropin_text
    assert "User=root" not in runtime_dropin_text
    assert "threadcells-release-admin" not in runtime_dropin_text
    assert "SupplementaryGroups=" not in runtime_dropin_text
    assert "ExecStart=/var/lib/threadcells/active/runtime/bin/cao-server" in runtime_dropin_text
    assert (
        "THREADCELLS_MCP_SERVER_COMMAND="
        "/var/lib/threadcells/active/runtime/bin/threadcells-mcp-server" in runtime_dropin_text
    )
    mcp_launcher = agent_root / "bin/threadcells-mcp-server"
    assert mcp_launcher.is_symlink()
    assert mcp_launcher.readlink() == (
        release_state_root / "active/runtime/bin/threadcells-mcp-server"
    )


def test_stage_ops_p1_reinstalls_local_wheel_into_immutable_candidate_runtime(tmp_path):
    agent_root = tmp_path / "srv/agent-control"
    policy = agent_root / "policy/ORCHESTRATION.md"
    policy.parent.mkdir(parents=True)
    policy.write_text("# Existing authority\n")
    base_runtime = tmp_path / "known-good-runtime"
    # Python 3.10's venv module does not resolve a uv-created environment's
    # interpreter symlink before recording its base home. Invoke the resolved
    # base interpreter so this exercises the deployment code on every supported
    # Python version instead of constructing a broken nested test environment.
    subprocess.run(
        [str(Path(sys.executable).resolve()), "-m", "venv", str(base_runtime)], check=True
    )
    stale_wheel = _local_wheel(
        tmp_path / "stale-wheel",
        web_assets={"assets/subaev-cao-old.png": b"obsolete image"},
    )
    wheel = _local_wheel(
        tmp_path / "fresh-wheel",
        web_assets={"assets/app-fresh-a1b2.js": b"fresh web asset"},
    )
    base_python = base_runtime / "bin/python"
    assert (
        subprocess.run(
            [str(base_python), "-m", "pip", "install", "--no-index", "--no-deps", str(stale_wheel)],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    base_hash = _tree_hash(base_runtime)
    old_launcher_header = "\n".join(
        (base_runtime / "bin/cao").read_text(encoding="utf-8").splitlines()[:3]
    )
    assert str(base_python) in old_launcher_header
    legacy_runtime = tmp_path / "legacy-candidate" / "runtime"
    shutil.copytree(base_runtime, legacy_runtime, symlinks=True)
    legacy_launcher_header = "\n".join(
        (legacy_runtime / "bin/cao").read_text(encoding="utf-8").splitlines()[:3]
    )
    assert legacy_launcher_header == old_launcher_header
    commit = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    release_state_root = tmp_path / "var/lib/threadcells"
    release_root = release_state_root / "releases"
    candidate_root = release_root / "candidate"
    release_lock = release_state_root / "release-staging.lock"
    release_metadata = release_state_root / "release-metadata.json"
    command = [
        sys.executable,
        str(SOURCE / "deployment/stage-ops-p1.py"),
        "--source-root",
        str(SOURCE),
        "--agent-control-root",
        str(agent_root),
        "--system-root",
        str(tmp_path),
        "--base-runtime",
        str(base_runtime),
        "--candidate-root",
        str(candidate_root),
        "--wheel",
        str(wheel),
        "--release-lock",
        str(release_lock),
        "--release-metadata",
        str(release_metadata),
        "--test-unprivileged-staging",
        "--expected-commit",
        commit,
    ]

    release_state_root.parent.mkdir(parents=True)
    outside_control_root = tmp_path / "outside-control-root"
    outside_control_root.mkdir()
    release_state_root.symlink_to(outside_control_root, target_is_directory=True)
    control_symlink = subprocess.run(command, capture_output=True, text=True)
    assert control_symlink.returncode != 0
    assert "reason_code=RELEASE_CONTROL_ROOT_INVALID" in control_symlink.stderr
    assert list(outside_control_root.iterdir()) == []
    release_state_root.unlink()

    rejected_root = release_root / "rejected-candidate"
    rejected_command = [
        str(rejected_root) if value == str(candidate_root) else value for value in command
    ]
    rejected_command[-1] = "0" * len(commit)
    rejected = subprocess.run(rejected_command, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "reason_code=PACKAGE_SOURCE_IDENTITY_MISMATCH" in rejected.stderr
    assert not rejected_root.exists()
    assert not (tmp_path / "etc/agent-control/cao-operations.json").exists()
    assert _tree_hash(base_runtime) == base_hash

    outside_root = tmp_path / "outside-candidate"
    outside_command = [
        str(outside_root) if value == str(candidate_root) else value for value in command
    ]
    outside = subprocess.run(outside_command, capture_output=True, text=True)
    assert outside.returncode != 0
    assert "reason_code=CANDIDATE_TARGET_INVALID" in outside.stderr
    assert not outside_root.exists()

    misplaced_root = agent_root / "state/candidate"
    misplaced_command = [
        str(misplaced_root) if value == str(candidate_root) else value for value in command
    ]
    misplaced = subprocess.run(misplaced_command, capture_output=True, text=True)
    assert misplaced.returncode != 0
    assert "reason_code=CANDIDATE_TARGET_INVALID" in misplaced.stderr
    assert not misplaced_root.exists()

    replacement_root = release_root / "existing-candidate"
    replacement_root.mkdir()
    replacement_sentinel = replacement_root / "existing-state"
    replacement_sentinel.write_text("preserve", encoding="utf-8")
    replacement_command = [
        str(replacement_root) if value == str(candidate_root) else value for value in command
    ]
    replacement = subprocess.run(replacement_command, capture_output=True, text=True)
    assert replacement.returncode != 0
    assert "reason_code=CANDIDATE_TARGET_INVALID" in replacement.stderr
    assert replacement_sentinel.read_text(encoding="utf-8") == "preserve"

    dangling_root = release_root / "dangling-candidate"
    outside_dangling_target = tmp_path / "outside-dangling-target"
    dangling_root.symlink_to(outside_dangling_target, target_is_directory=True)
    dangling_command = [
        str(dangling_root) if value == str(candidate_root) else value for value in command
    ]
    dangling = subprocess.run(dangling_command, capture_output=True, text=True)
    assert dangling.returncode != 0
    assert "reason_code=CANDIDATE_TARGET_INVALID" in dangling.stderr
    assert not outside_dangling_target.exists()
    dangling_root.unlink()

    outside_lock = tmp_path / "outside-release-lock"
    release_lock.unlink()
    release_lock.symlink_to(outside_lock)
    lock_candidate = release_root / "lock-symlink-candidate"
    lock_command = [
        str(lock_candidate) if value == str(candidate_root) else value for value in command
    ]
    locked = subprocess.run(lock_command, capture_output=True, text=True)
    assert locked.returncode != 0
    assert "reason_code=CONTROL_FILE_PATH_INVALID" in locked.stderr
    assert not outside_lock.exists()
    release_lock.unlink()

    outside_metadata = tmp_path / "outside-release-metadata"
    release_metadata.symlink_to(outside_metadata)
    metadata_candidate = release_root / "metadata-symlink-candidate"
    metadata_command = [
        str(metadata_candidate) if value == str(candidate_root) else value for value in command
    ]
    metadata_result = subprocess.run(metadata_command, capture_output=True, text=True)
    assert metadata_result.returncode != 0
    assert "reason_code=CONTROL_FILE_PATH_INVALID" in metadata_result.stderr
    assert not outside_metadata.exists()
    release_metadata.unlink()

    if os.geteuid() != 0:
        production_command = [value for value in command if value != "--test-unprivileged-staging"]
        system_root_index = production_command.index("--system-root") + 1
        production_command[system_root_index] = "/"
        production = subprocess.run(production_command, capture_output=True, text=True)
        assert production.returncode != 0
        assert "reason_code=STAGING_PRIVILEGE_REQUIRED" in production.stderr

    staged = subprocess.run(command, capture_output=True, text=True)

    assert staged.returncode == 0, staged.stderr
    marker = json.loads((candidate_root / ".threadcells-release.json").read_text())
    metadata = json.loads(release_metadata.read_text())
    assert marker == {
        "schema_version": 1,
        "release_id": "candidate",
        "source_commit": commit,
        "state": "candidate",
    }
    assert metadata["candidate_releases"] == [str(candidate_root.resolve())]
    candidate_runtime = candidate_root / "runtime"
    candidate_python = candidate_runtime / "bin/python"
    assert release_state_root.stat().st_mode & 0o777 == 0o755
    assert release_root.stat().st_mode & 0o777 == 0o775
    assert candidate_root.stat().st_mode & 0o777 == 0o775
    assert candidate_runtime.stat().st_mode & 0o777 == 0o775
    assert (candidate_runtime / "bin").stat().st_mode & 0o777 == 0o775
    assert (candidate_runtime / "pyvenv.cfg").stat().st_mode & 0o777 == 0o644
    assert (candidate_runtime / "bin/cao").stat().st_mode & 0o777 == 0o755
    assert (candidate_root / ".threadcells-release.json").stat().st_mode & 0o777 == 0o644
    assert release_lock.parent.stat().st_mode & 0o777 == 0o755
    assert release_lock.stat().st_mode & 0o777 == 0o660
    assert release_metadata.parent.stat().st_mode & 0o777 == 0o755
    assert release_metadata.stat().st_mode & 0o777 == 0o644
    assert (release_state_root.stat().st_uid, release_state_root.stat().st_gid) == (
        os.geteuid(),
        os.getegid(),
    )
    for path in (release_root, candidate_root, release_lock, release_metadata):
        assert (path.stat().st_uid, path.stat().st_gid) == (os.geteuid(), os.getegid())
    launcher_header = "\n".join(
        (candidate_runtime / "bin/cao").read_text(encoding="utf-8").splitlines()[:3]
    )
    # pip uses a direct shebang when it fits and a valid /bin/sh exec
    # trampoline for long installation paths. Both must name this candidate's
    # interpreter and must not retain the base runtime's absolute path.
    assert str(candidate_python) in launcher_header
    assert str(base_python) not in launcher_header
    imported_path = subprocess.run(
        [
            str(candidate_python),
            "-c",
            "import pathlib, cli_agent_orchestrator; print(pathlib.Path(cli_agent_orchestrator.__file__).resolve())",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(imported_path).is_relative_to(candidate_runtime.resolve())
    installed_web_ui = Path(imported_path).parent / "web_ui"
    assert (installed_web_ui / "assets/app-fresh-a1b2.js").read_bytes() == b"fresh web asset"
    assert not (installed_web_ui / "assets/subaev-cao-old.png").exists()
    with ZipFile(wheel) as archive:
        wheel_web_assets = {
            Path(name).relative_to("cli_agent_orchestrator/web_ui"): archive.read(name)
            for name in archive.namelist()
            if name.startswith("cli_agent_orchestrator/web_ui/") and not name.endswith("/")
        }
    installed_web_assets = {
        path.relative_to(installed_web_ui): path.read_bytes()
        for path in installed_web_ui.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert installed_web_assets == wheel_web_assets
    assert _tree_hash(base_runtime) == base_hash

    promote_command = [
        sys.executable,
        str(SOURCE / "deployment/promote-ops-p1.py"),
        "--system-root",
        str(tmp_path),
        "--candidate-root",
        str(candidate_root),
        "--expected-commit",
        commit,
        "--test-unprivileged-promotion",
    ]
    active_link = release_state_root / "active"

    # A new release may add an executable that cannot exist in a previously
    # staged active, rollback, or candidate release. Preserve those verified
    # legacy releases without weakening the executable contract for the new
    # promotion target.
    def make_legacy_release(name: str, state: str) -> Path:
        release = release_root / name
        runtime_bin = release / "runtime/bin"
        runtime_bin.mkdir(parents=True)
        for directory in (release, release / "runtime", runtime_bin):
            directory.chmod(0o775)
        for executable in ("cao-server", "cao-housekeeping", "threadcells-mcp-server"):
            path = runtime_bin / executable
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
        marker = release / ".threadcells-release.json"
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_id": name,
                    "source_commit": "legacy-commit",
                    "state": state,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        marker.chmod(0o644)
        return release

    legacy_active = make_legacy_release("legacy-active", "active")
    legacy_candidate = make_legacy_release("legacy-candidate", "candidate")
    metadata["active_release"] = str(legacy_active)
    metadata["candidate_releases"] = [str(candidate_root.resolve()), str(legacy_candidate)]
    release_metadata.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    release_metadata.chmod(0o644)
    active_link.symlink_to(legacy_active, target_is_directory=True)
    legacy_dry_promotion = subprocess.run(
        [*promote_command, "--rollback-root", str(legacy_active), "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert legacy_dry_promotion.returncode == 0, legacy_dry_promotion.stderr
    assert "OPS_P1_PROMOTE_DRY_RUN" in legacy_dry_promotion.stdout
    assert active_link.resolve() == legacy_active.resolve()
    helper = candidate_root / "runtime/bin/threadcells-full-cleanup-helper"
    missing_helper = helper.with_name(f"{helper.name}.missing")
    helper.rename(missing_helper)
    rejected_current_candidate = subprocess.run(
        [*promote_command, "--rollback-root", str(legacy_active), "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert rejected_current_candidate.returncode != 0
    assert "RELEASE_RUNTIME_INVALID" in (
        rejected_current_candidate.stdout + rejected_current_candidate.stderr
    )
    missing_helper.rename(helper)
    active_link.unlink()
    metadata["active_release"] = None
    metadata["candidate_releases"] = [str(candidate_root.resolve())]
    release_metadata.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    release_metadata.chmod(0o644)
    shutil.rmtree(legacy_active)
    shutil.rmtree(legacy_candidate)

    outside_active = tmp_path / "outside-active"
    outside_active.mkdir()
    active_link.symlink_to(outside_active, target_is_directory=True)
    unsafe_active = subprocess.run(promote_command, capture_output=True, text=True)
    assert unsafe_active.returncode != 0
    assert "reason_code=ACTIVE_RELEASE_LINK_INVALID" in unsafe_active.stderr
    assert list(outside_active.iterdir()) == []
    active_link.unlink()

    dry_promotion = subprocess.run([*promote_command, "--dry-run"], capture_output=True, text=True)
    assert dry_promotion.returncode == 0, dry_promotion.stderr
    assert "OPS_P1_PROMOTE_DRY_RUN" in dry_promotion.stdout
    assert not active_link.exists()

    original_var_mode = (tmp_path / "var").stat().st_mode & 0o777
    (tmp_path / "var").chmod(0o775)
    untrusted_parent = subprocess.run(promote_command, capture_output=True, text=True)
    assert untrusted_parent.returncode != 0
    assert "reason_code=RELEASE_CONTROL_ROOT_UNTRUSTED" in untrusted_parent.stderr
    assert not active_link.exists()
    (tmp_path / "var").chmod(original_var_mode)

    marker_crash = subprocess.run(
        [*promote_command, "--test-crash-after", "marker"],
        capture_output=True,
        text=True,
    )
    assert marker_crash.returncode != 0
    assert "reason_code=TEST_CRASH_AFTER_MARKER" in marker_crash.stderr
    assert json.loads((candidate_root / ".threadcells-release.json").read_text())["state"] == (
        "active"
    )
    assert not active_link.exists()
    assert json.loads(release_metadata.read_text())["candidate_releases"] == [
        str(candidate_root.resolve())
    ]

    link_crash = subprocess.run(
        [*promote_command, "--test-crash-after", "link"],
        capture_output=True,
        text=True,
    )
    assert link_crash.returncode != 0
    assert "reason_code=TEST_CRASH_AFTER_LINK" in link_crash.stderr
    assert active_link.resolve() == candidate_root.resolve()
    assert json.loads(release_metadata.read_text())["active_release"] is None

    promoted = subprocess.run(promote_command, capture_output=True, text=True)
    assert promoted.returncode == 0, promoted.stderr
    assert "OPS_P1_PROMOTED" in promoted.stdout
    assert active_link.is_symlink()
    assert active_link.resolve() == candidate_root.resolve()
    assert (active_link.lstat().st_uid, active_link.lstat().st_gid) == (
        os.geteuid(),
        os.getegid(),
    )
    promoted_marker = json.loads((candidate_root / ".threadcells-release.json").read_text())
    promoted_metadata = json.loads(release_metadata.read_text())
    assert promoted_marker["state"] == "active"
    assert promoted_metadata["active_release"] == str(candidate_root)
    assert promoted_metadata["candidate_releases"] == []

    repeated = subprocess.run(promote_command, capture_output=True, text=True)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(release_metadata.read_text()) == promoted_metadata


def test_stage_ops_p1_bootstraps_pip_only_inside_a_without_pip_candidate(tmp_path):
    agent_root = tmp_path / "srv/agent-control"
    policy = agent_root / "policy/ORCHESTRATION.md"
    policy.parent.mkdir(parents=True)
    policy.write_text("# Existing authority\n")
    base_runtime = tmp_path / "without-pip-runtime"
    subprocess.run(
        [
            str(Path(sys.executable).resolve()),
            "-m",
            "venv",
            "--without-pip",
            str(base_runtime),
        ],
        check=True,
    )
    wheel = _local_wheel(tmp_path / "wheel")
    commit = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    candidate = tmp_path / "var/lib/threadcells/releases/candidate-without-pip"
    command = [
        sys.executable,
        str(SOURCE / "deployment/stage-ops-p1.py"),
        "--source-root",
        str(SOURCE),
        "--agent-control-root",
        str(agent_root),
        "--system-root",
        str(tmp_path),
        "--base-runtime",
        str(base_runtime),
        "--candidate-root",
        str(candidate),
        "--wheel",
        str(wheel),
        "--test-unprivileged-staging",
        "--expected-commit",
        commit,
    ]

    staged = subprocess.run(command, capture_output=True, text=True)

    assert staged.returncode == 0, staged.stderr
    assert (
        subprocess.run(
            [str(base_runtime / "bin/python"), "-c", "import pip"],
            capture_output=True,
            text=True,
        ).returncode
        != 0
    )
    assert (
        subprocess.run(
            [str(candidate / "runtime/bin/python"), "-c", "import pip"],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
