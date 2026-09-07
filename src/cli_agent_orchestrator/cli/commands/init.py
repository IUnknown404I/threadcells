"""Init command for ThreadCells."""

import os
import shutil
import tempfile
from importlib import resources
from pathlib import Path

import click

from cli_agent_orchestrator.clients.database import init_db
from cli_agent_orchestrator.constants import SKILLS_DIR

_BUILTIN_SKILL_TEXT_MIGRATIONS = {
    "cao-supervisor-protocols": (
        (
            "A handoff wait is a bounded slice, not evidence that its worker stopped. If it\n"
            "returns `state: waiting`, retain the returned `terminal_id` and later call\n"
            "`await_handoff(terminal_id, timeout)` for that same child. Do not create a\n"
            "replacement worker or resend the task. Only `state: completed` is a successful\n"
            "handoff: CAO validates stable, non-progress final output before it sends `/exit`.\n"
            "An `exited` provider lifecycle is terminal even if the tmux shell remains.",
            "A handoff wait is a bounded slice, not evidence that its worker stopped. If it\n"
            "returns `state: waiting`, retain both the returned `terminal_id` and\n"
            "`next_wait_slice_id`. Later call\n"
            "`await_handoff(terminal_id, timeout, wait_slice_id=next_wait_slice_id)` for that\n"
            "same child. Replaying the old slice only reports its already-recorded outcome;\n"
            "it does not wait again. Do not create a replacement worker or resend the task.\n"
            "Only `state: completed` is a successful handoff: CAO validates stable,\n"
            "non-progress final output before it sends `/exit`. An `exited` provider\n"
            "lifecycle is terminal even if the tmux shell remains.",
        ),
    ),
    "cao-session-management": (
        (
            "**handoff** (blocking) — conductor sends task and waits for a validated worker\n"
            "result. A `state: waiting` response means the live worker retained its durable\n"
            "terminal ID; resume that exact child with `await_handoff(terminal_id, timeout)`.\n"
            "Do not resend its task or create a duplicate. A tmux pane can outlive the\n"
            "provider process, so `lifecycle: exited` is not resumable even if the pane still\n"
            "exists.",
            "**handoff** (blocking) — conductor sends task and waits for a validated worker\n"
            "result. A `state: waiting` response means the live worker retained its durable\n"
            "terminal ID. Retain its returned `next_wait_slice_id`, then resume that exact\n"
            "child with\n"
            "`await_handoff(terminal_id, timeout, wait_slice_id=next_wait_slice_id)`. Reusing\n"
            "an old slice only reports its recorded outcome and does not wait again. Do not\n"
            "resend its task or create a duplicate. A tmux pane can outlive the provider\n"
            "process, so `lifecycle: exited` is not resumable even if the pane still exists.",
        ),
    ),
}


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _replace_text_atomically(
    path: Path, content: str, expected_identity: tuple[int, int, int, int]
) -> bool:
    """Replace one regular file only if it has not changed since it was read."""
    mode = path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        try:
            os.fchmod(descriptor, mode)
        except BaseException:
            os.close(descriptor)
            raise
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if _file_identity(path) != expected_identity:
            return False
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _upgrade_builtin_skill_guidance(skill_name: str, destination_dir: Path) -> bool:
    """Apply exact retired built-in fragments without replacing operator edits."""
    migrations = _BUILTIN_SKILL_TEXT_MIGRATIONS.get(skill_name, ())
    skill_file = destination_dir / "SKILL.md"
    if (
        not migrations
        or destination_dir.is_symlink()
        or skill_file.is_symlink()
        or not skill_file.is_file()
    ):
        return False
    identity = _file_identity(skill_file)
    original = skill_file.read_text(encoding="utf-8")
    updated = original
    for retired, replacement in migrations:
        updated = updated.replace(retired, replacement)
    if updated == original:
        return False
    return _replace_text_atomically(skill_file, updated, identity)


def seed_default_skills() -> int:
    """Seed built-ins and migrate only exact obsolete built-in guidance."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    bundled_skills = resources.files("cli_agent_orchestrator.skills")
    seeded_count = 0

    for skill_dir in bundled_skills.iterdir():
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
            continue

        destination_dir = SKILLS_DIR / skill_dir.name
        if destination_dir.exists():
            if _upgrade_builtin_skill_guidance(skill_dir.name, destination_dir):
                seeded_count += 1
            continue

        with resources.as_file(skill_dir) as source_dir:
            shutil.copytree(Path(source_dir), destination_dir)
        seeded_count += 1

    return seeded_count


@click.command()
def init():
    """Initialize the ThreadCells database."""
    try:
        init_db()
        seeded_count = seed_default_skills()
        click.echo(
            f"ThreadCells initialized successfully. " f"Seeded {seeded_count} builtin skills."
        )
    except Exception as e:
        raise click.ClickException(str(e))
