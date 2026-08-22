import hashlib
import json
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
    assert config["release_roots"] == [str((agent_root / "releases").resolve())]
    assert config["release_metadata"] == str(
        (agent_root / "state/cao/release-metadata.json").resolve()
    )
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
    old_shebang = (base_runtime / "bin/cao").read_text(encoding="utf-8").splitlines()[0]
    assert old_shebang == f"#!{base_python}"
    legacy_runtime = tmp_path / "legacy-candidate" / "runtime"
    shutil.copytree(base_runtime, legacy_runtime, symlinks=True)
    assert (legacy_runtime / "bin/cao").read_text(encoding="utf-8").splitlines()[0] == old_shebang
    commit = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    candidate_root = tmp_path / "candidate"
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
        str(tmp_path / "locks/release-staging.lock"),
        "--release-metadata",
        str(tmp_path / "state/cao/release-metadata.json"),
        "--expected-commit",
        commit,
    ]

    rejected_root = tmp_path / "rejected-candidate"
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

    staged = subprocess.run(command, capture_output=True, text=True)

    assert staged.returncode == 0, staged.stderr
    marker = json.loads((candidate_root / ".threadcells-release.json").read_text())
    metadata = json.loads((tmp_path / "state/cao/release-metadata.json").read_text())
    assert marker == {
        "schema_version": 1,
        "release_id": "candidate",
        "source_commit": commit,
        "state": "candidate",
    }
    assert metadata["candidate_releases"] == [str(candidate_root.resolve())]
    candidate_runtime = candidate_root / "runtime"
    candidate_python = candidate_runtime / "bin/python"
    assert (candidate_runtime / "bin/cao").read_text(encoding="utf-8").splitlines()[
        0
    ] == f"#!{candidate_python}"
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
