import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SOURCE = Path(__file__).parents[1]


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
    wheel = destination / "threadcells-0.1.0a1-py3-none-any.whl"
    dist_info = "threadcells-0.1.0a1.dist-info"
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
            "Metadata-Version: 2.1\nName: threadcells\nVersion: 0.1.0a1\n",
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
    ]
    dry_run = subprocess.run([*command, "--dry-run"], capture_output=True, text=True)
    assert dry_run.returncode == 0
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
        "agent-control-housekeeping.service",
        "agent-control-housekeeping.timer",
        "agent-control-housekeeping-weekly.service",
        "agent-control-housekeeping-weekly.timer",
    ):
        assert (system_root / "etc/systemd/system" / unit).is_file()
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
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in runtime_dropin.read_text()
    assert (
        "ExecStart=/var/lib/threadcells/active/runtime/bin/cao-server" in runtime_dropin.read_text()
    )
    assert (
        "THREADCELLS_MCP_SERVER_COMMAND="
        "/var/lib/threadcells/active/runtime/bin/threadcells-mcp-server"
        in runtime_dropin.read_text()
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
