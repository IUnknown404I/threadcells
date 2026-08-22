import json
import os
import pwd
import shutil
from pathlib import Path
from types import SimpleNamespace

from cli_agent_orchestrator.services.housekeeping_service import (
    HousekeepingSummary,
    _cleanup_browser_cache,
    _cleanup_ephemeral_dirs,
    _cleanup_labelled_docker_resources,
    _cleanup_logs,
    _cleanup_marked_orphan_browsers,
    _inventory_warnings,
    _open_paths,
    _open_paths_inventory,
    _reconcile_legacy_terminal_authority,
    _reconcile_supervisor_context_roles,
    _reconcile_writer_leases,
    _runtime_open_paths_inventory,
)


def _config(root: Path):
    return {
        "root": str(root),
        "lock_dir": str(root / "locks"),
        "log_compress_after_minutes": 1440,
        "retention_minutes": 10080,
        "subprocess_timeout_seconds": 1,
        "log_tree_warning_gib": 5,
        "backup_tree_warning_gib": 5,
        "orphan_browser_age_minutes": 120,
        "runtime_user": pwd.getpwuid(os.getuid()).pw_name,
        "playwright_manifest_roots": [str(root / "projects")],
        "playwright_browser_cache": str(root / "browser-cache"),
    }


def _age(path: Path, now: float, minutes: int):
    os.utime(path, (now - minutes * 60, now - minutes * 60))


def test_housekeeping_reuses_canonical_supervisor_role_reconciliation(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.reconcile_terminal_context_roles",
        lambda *, dry_run: calls.append(dry_run) or 2,
    )
    summary = HousekeepingSummary(dry_run=True)

    _reconcile_supervisor_context_roles(summary)

    assert calls == [True]
    assert summary.supervisor_roles_reconciled == 2


def test_logs_use_exact_ttl_and_preserve_open_files(tmp_path):
    now = 2_000_000_000.0
    logs = tmp_path / "state/cao/logs"
    terminal_logs = logs / "terminal"
    attachments = tmp_path / "state/cao/runtime/terminal-attachments/t1"
    terminal_logs.mkdir(parents=True)
    attachments.mkdir(parents=True)
    active = logs / "cao_active.log"
    compress = logs / "cao_closed.log"
    expired = terminal_logs / "expired.log"
    attachment = attachments / "old.png"
    for path in (active, compress, expired, attachment):
        path.write_bytes(b"x" * 100)
    _age(active, now, 2000)
    _age(compress, now, 2000)
    _age(expired, now, 10081)
    _age(attachment, now, 10081)
    summary = HousekeepingSummary()
    _cleanup_logs(
        tmp_path,
        _config(tmp_path),
        summary,
        now=now,
        open_paths={active.resolve()},
    )
    assert active.exists()
    assert not compress.exists() and compress.with_suffix(".log.gz").exists()
    assert not expired.exists() and not attachment.exists()
    assert summary.logs_compressed == 1
    assert summary.logs_deleted == 1
    assert summary.attachments_deleted == 1
    assert summary.skipped_open == 1
    assert int(compress.with_suffix(".log.gz").stat().st_mtime) == int(now - 2000 * 60)


def test_log_and_attachment_cleanup_fails_closed_when_open_inventory_is_uncertain(tmp_path):
    now = 2_000_000_000.0
    log = tmp_path / "state/cao/logs/cao_old.log"
    attachment = tmp_path / "state/cao/runtime/terminal-attachments/t1/old.png"
    log.parent.mkdir(parents=True)
    attachment.parent.mkdir(parents=True)
    log.write_bytes(b"log")
    attachment.write_bytes(b"attachment")
    _age(log, now, 10081)
    _age(attachment, now, 10081)
    summary = HousekeepingSummary()

    _cleanup_logs(
        tmp_path,
        _config(tmp_path),
        summary,
        now=now,
        open_paths=set(),
        open_paths_certain=False,
    )

    assert log.exists() and attachment.exists()
    assert summary.logs_deleted == 0
    assert summary.attachments_deleted == 0
    assert summary.skipped_unknown == 1
    assert "log_attachment_process_inventory_uncertain" in summary.warnings


def test_open_inventory_scopes_certainty_to_the_runtime_account(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    owned = proc / "100"
    foreign = proc / "200"
    (owned / "fd").mkdir(parents=True)
    foreign.mkdir()
    opened = tmp_path / "active.log"
    opened.write_text("active", encoding="utf-8")
    (owned / "fd/3").symlink_to(opened)
    (owned / "exe").symlink_to(Path("/usr/bin/python3"))
    (owned / "maps").write_text("", encoding="utf-8")
    runtime_uid = os.getuid()
    original_stat = Path.stat

    def process_owner(path, *args, **kwargs):
        if path == foreign:
            return SimpleNamespace(st_uid=runtime_uid + 1)
        return original_stat(path, *args, **kwargs)

    original_iterdir = Path.iterdir

    def unreadable_foreign_fd(path):
        if path == foreign / "fd":
            raise PermissionError("foreign process details are private")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "stat", process_owner)
    monkeypatch.setattr(Path, "iterdir", unreadable_foreign_fd)

    paths, certain = _open_paths_inventory(proc, runtime_uid=runtime_uid)

    assert certain is True
    assert opened.resolve() in paths


def test_open_inventory_fails_closed_for_unreadable_runtime_process(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    owned = proc / "100"
    owned.mkdir(parents=True)
    runtime_uid = os.getuid()
    original_iterdir = Path.iterdir

    def unreadable_owned_fd(path):
        if path == owned / "fd":
            raise PermissionError("runtime process details are unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable_owned_fd)

    paths, certain = _open_paths_inventory(proc, runtime_uid=runtime_uid)

    assert paths == set()
    assert certain is False


def test_runtime_inventory_uses_configured_owner_instead_of_caller(monkeypatch, tmp_path):
    observed = []
    monkeypatch.setattr(
        pwd,
        "getpwnam",
        lambda name: SimpleNamespace(pw_uid=314) if name == "agentctl" else None,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._open_paths_inventory",
        lambda proc_root, *, runtime_uid: observed.append((proc_root, runtime_uid))
        or ({tmp_path.resolve()}, True),
    )

    paths, certain = _runtime_open_paths_inventory({"runtime_user": "agentctl"}, tmp_path / "proc")

    assert certain is True
    assert paths == {tmp_path.resolve()}
    assert observed == [(tmp_path / "proc", 314)]


def test_runtime_inventory_fails_closed_when_owner_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(pwd, "getpwnam", lambda _name: (_ for _ in ()).throw(KeyError()))

    paths, certain = _runtime_open_paths_inventory({"runtime_user": "missing"}, tmp_path / "proc")

    assert paths == set()
    assert certain is False


def test_unknown_temp_is_preserved_and_expired_marked_temp_is_removed(tmp_path):
    now = 2_000_000_000.0
    unknown = tmp_path / "tmp/unknown"
    marked = tmp_path / "tmp/marked"
    unknown.mkdir(parents=True)
    marked.mkdir(parents=True)
    (unknown / "keep").write_text("keep")
    (marked / "delete").write_text("delete")
    (marked / ".cao-ephemeral.json").write_text(
        json.dumps({"version": 1, "expires_at": now - 1, "owner_pid": 99999999})
    )
    summary = HousekeepingSummary()
    _cleanup_ephemeral_dirs(tmp_path, summary, now=now)
    assert unknown.exists()
    assert not marked.exists()
    assert summary.ephemeral_resources_removed == 1
    assert summary.skipped_unknown == 1


def test_playwright_cache_inventories_unreferenced_revisions_without_deleting(tmp_path):
    now = 2_000_000_000.0
    manifest = tmp_path / "projects/app/node_modules/playwright-core/browsers.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "browsers": [
                    {"name": "chromium", "revision": "1228"},
                    {"name": "chromium", "revision": "1234"},
                ]
            }
        )
    )
    cache = tmp_path / "browser-cache"
    for name in ("chromium-1228", "chromium-1234", "chromium-1111", "unknown"):
        path = cache / name
        path.mkdir(parents=True)
        (path / "file").write_text("x")
        _age(path, now, 10081)
    summary = HousekeepingSummary()
    _cleanup_browser_cache(
        _config(tmp_path),
        summary,
        now=now,
        inventory_probe=lambda _: (set(), True),
    )
    assert (cache / "chromium-1228").exists()
    assert (cache / "chromium-1234").exists()
    assert (cache / "chromium-1111").exists()
    assert (cache / "unknown").exists()
    assert summary.browser_revisions_removed == 0
    assert summary.browser_revision_candidates == 1
    assert "browser_revision_cleanup_candidates:1" in summary.warnings


def test_writer_lease_reconciliation_requires_positive_tmux_death(monkeypatch):
    leases = [
        {
            "canonical_worktree": "/worktree",
            "terminal_id": "writer",
            "tmux_session": "cao-session",
            "tmux_window": "writer-window",
        }
    ]
    retired = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_worktree_writer_leases", lambda: leases
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.mark_terminal_runtime_exited",
        lambda terminal_id: retired.append(terminal_id) or True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.cancel_child_assignments_for_terminal",
        lambda *_: 0,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.cancel_workflows_for_terminal", lambda *_: 0
    )

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: None
    )
    uncertain = HousekeepingSummary()
    _reconcile_writer_leases(uncertain)
    assert retired == []
    assert uncertain.writer_leases_reconciled == 0
    assert "writer_lease_tmux_inventory_uncertain:writer" in uncertain.warnings

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: False
    )
    dead = HousekeepingSummary()
    _reconcile_writer_leases(dead)
    assert retired == ["writer"]
    assert dead.writer_leases_reconciled == 1


def test_legacy_authority_reconciliation_has_own_counter_and_fails_closed(monkeypatch):
    terminals = [
        {
            "id": "legacy",
            "tmux_session": "cao-old",
            "tmux_window": "worker",
            "launch_worktree": None,
            "write_enabled": None,
            "context_role": None,
        }
    ]
    retired = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_unreconciled_terminal_authorities",
        lambda: terminals,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.retire_unreconciled_terminal_authority",
        lambda terminal_id: retired.append(terminal_id) or True,
    )

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: None
    )
    uncertain = HousekeepingSummary()
    _reconcile_legacy_terminal_authority(uncertain)
    assert retired == []
    assert uncertain.legacy_authority_reconciled == 0
    assert "legacy_authority_tmux_inventory_uncertain:legacy" in uncertain.warnings

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: True
    )
    live = HousekeepingSummary()
    _reconcile_legacy_terminal_authority(live)
    assert retired == []
    assert live.skipped_open == 1

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: False
    )
    dead = HousekeepingSummary()
    _reconcile_legacy_terminal_authority(dead)
    assert retired == ["legacy"]
    assert dead.legacy_authority_reconciled == 1
    assert dead.writer_leases_reconciled == 0


def test_playwright_cache_fails_closed_on_manifest_uncertainty(tmp_path):
    now = 2_000_000_000.0
    manifest = tmp_path / "projects/app/node_modules/playwright-core/browsers.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("not-json")
    candidate = tmp_path / "browser-cache/chromium-1111"
    candidate.mkdir(parents=True)
    (candidate / "chrome").write_text("binary")
    _age(candidate, now, 10081)
    summary = HousekeepingSummary()
    _cleanup_browser_cache(
        _config(tmp_path),
        summary,
        now=now,
        inventory_probe=lambda _: (set(), True),
    )
    assert candidate.exists()
    assert summary.browser_revisions_removed == 0
    assert "browser_manifest_inventory_uncertain" in summary.warnings


def test_active_executables_and_mappings_are_in_open_path_inventory(tmp_path):
    now = 2_000_000_000.0
    proc = tmp_path / "proc"
    process = proc / "123"
    fds = process / "fd"
    fds.mkdir(parents=True)
    executable = tmp_path / "browser-cache/chromium-1111/chrome"
    mapped = executable.with_name("libbrowser.so")
    executable.parent.mkdir(parents=True)
    executable.write_text("binary")
    mapped.write_text("library")
    _age(executable.parent, now, 10081)
    manifest = tmp_path / "projects/app/node_modules/playwright-core/browsers.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"browsers": [{"revision": "2222"}]}))
    (process / "exe").symlink_to(executable)
    (process / "maps").write_text(f"0-1 r-xp 0 00:00 0 {mapped}\n")
    paths = _open_paths(proc)
    assert executable in paths
    assert mapped in paths
    summary = HousekeepingSummary()
    _cleanup_browser_cache(
        _config(tmp_path),
        summary,
        now=now,
        inventory_probe=lambda _: (paths, True),
    )
    assert executable.parent.exists()
    assert summary.skipped_open == 1


def test_browser_cache_race_reinventories_at_delete_boundary(tmp_path):
    now = 2_000_000_000.0
    candidate = tmp_path / "browser-cache/chromium-1111"
    executable = candidate / "chrome"
    candidate.mkdir(parents=True)
    executable.write_text("binary")
    _age(candidate, now, 10081)
    manifest = tmp_path / "projects/app/node_modules/playwright-core/browsers.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"browsers": [{"revision": "2222"}]}))
    proc = tmp_path / "proc"
    proc.mkdir()

    def browser_starts_at_boundary(proc_root):
        process = proc_root / "123"
        (process / "fd").mkdir(parents=True)
        (process / "exe").symlink_to(executable)
        (process / "maps").write_text("")
        return _open_paths_inventory(proc_root)

    summary = HousekeepingSummary()
    _cleanup_browser_cache(
        _config(tmp_path),
        summary,
        now=now,
        proc_root=proc,
        inventory_probe=browser_starts_at_boundary,
    )
    assert candidate.exists()
    assert summary.skipped_open == 1
    assert summary.browser_revisions_removed == 0


def test_browser_cache_preserves_candidates_when_proc_inventory_is_uncertain(tmp_path):
    now = 2_000_000_000.0
    candidate = tmp_path / "browser-cache/chromium-1111"
    candidate.mkdir(parents=True)
    (candidate / "chrome").write_text("binary")
    _age(candidate, now, 10081)
    manifest = tmp_path / "projects/app/node_modules/playwright-core/browsers.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"browsers": [{"revision": "2222"}]}))
    summary = HousekeepingSummary()

    _cleanup_browser_cache(
        _config(tmp_path),
        summary,
        now=now,
        proc_root=tmp_path / "missing-proc",
    )

    assert candidate.exists()
    assert summary.browser_revisions_removed == 0
    assert summary.skipped_unknown == 1
    assert "browser_process_inventory_uncertain" in summary.warnings


def test_marked_orphan_candidate_and_active_owner_preservation(tmp_path):
    now = 2_000_000_000.0
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("10000 0")
    profile = Path(f"/tmp/playwright-{tmp_path.name}")
    profile.mkdir(exist_ok=True)
    try:
        (profile / ".cao-ephemeral.json").write_text(
            json.dumps(
                {"version": 1, "kind": "playwright", "expires_at": now - 1, "owner_pid": 8888}
            )
        )
        process = proc / "7777"
        process.mkdir()
        (process / "cmdline").write_bytes(f"chromium\0--user-data-dir={profile}\0".encode())
        (process / "status").write_text("Name:\tchromium\nPPid:\t1\n")
        fields = ["7777", "(chromium)", "S", "1", "7777"] + ["0"] * 16 + ["100"]
        (process / "stat").write_text(" ".join(fields))
        summary = HousekeepingSummary(dry_run=True)
        _cleanup_marked_orphan_browsers(_config(tmp_path), summary, now=now, proc_root=proc)
        assert summary.orphan_processes_closed == 1
        (proc / "8888").mkdir()
        summary = HousekeepingSummary(dry_run=True)
        _cleanup_marked_orphan_browsers(_config(tmp_path), summary, now=now, proc_root=proc)
        assert summary.orphan_processes_closed == 0
    finally:
        shutil.rmtree(profile)


def test_docker_cleanup_only_targets_expired_labelled_dead_owner(monkeypatch, tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        joined = " ".join(command)
        if "ps -a" in joined:
            return SimpleNamespace(returncode=0, stdout="ephemeral\n")
        if "volume ls" in joined:
            return SimpleNamespace(returncode=0, stdout="")
        if "State.Running" in joined:
            return SimpleNamespace(returncode=0, stdout="false\n")
        if "inspect" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"cao.ephemeral": "true", "cao.expires_at": "1", "cao.owner_pid": "99999999"}
                ),
            )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    summary = HousekeepingSummary(dry_run=True)
    _cleanup_labelled_docker_resources(summary, now=2, runner=runner)
    assert summary.ephemeral_resources_removed == 1
    assert not any(" rm " in f" {' '.join(command)} " for command in calls)


def test_docker_cleanup_preserves_running_containers_and_referenced_volumes(monkeypatch, tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        joined = " ".join(command)
        if "ps -a --filter label=" in joined:
            return SimpleNamespace(returncode=0, stdout="running-container\n")
        if "volume ls" in joined:
            return SimpleNamespace(returncode=0, stdout="attached-volume\n")
        if "State.Running" in joined:
            return SimpleNamespace(returncode=0, stdout="true\n")
        if "ps -a --filter volume=" in joined:
            return SimpleNamespace(returncode=0, stdout="container-using-volume\n")
        if "inspect" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"cao.ephemeral": "true", "cao.expires_at": "1", "cao.owner_pid": "99999999"}
                ),
            )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    summary = HousekeepingSummary(dry_run=False)
    _cleanup_labelled_docker_resources(summary, now=2, runner=runner)
    assert summary.ephemeral_resources_removed == 0
    assert summary.skipped_unknown == 2
    assert not any(
        command[1:3] in (["rm", "running-container"], ["volume", "rm"]) for command in calls
    )


def test_backups_and_unknown_deployments_are_inventory_only(tmp_path):
    backup = tmp_path / "backups/only-copy/data"
    backup.parent.mkdir(parents=True)
    backup.write_text("recovery")
    for name in (
        "cli-agent-orchestrator-a",
        "cli-agent-orchestrator-b",
        "cli-agent-orchestrator-c",
    ):
        (tmp_path / "tools" / name).mkdir(parents=True)
    summary = HousekeepingSummary()
    _inventory_warnings(tmp_path, _config(tmp_path), summary)
    assert backup.read_text() == "recovery"
    assert "deployment_inventory_requires_retention_metadata" in summary.warnings
