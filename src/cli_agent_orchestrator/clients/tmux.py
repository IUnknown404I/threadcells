"""Simplified tmux client as module singleton."""

import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import libtmux
from libtmux._internal.query_list import ObjectDoesNotExist

from cli_agent_orchestrator.constants import TMUX_HISTORY_LINES

logger = logging.getLogger(__name__)
_OPERATOR_AUTH_REFERENCE_ENVS = {
    "THREADCELLS_OPERATOR_SECRET_FILE",
    "THREADCELLS_OPERATOR_VERIFIER_FILE",
    "THREADMESH_OPERATOR_SECRET_FILE",
    "THREADMESH_OPERATOR_VERIFIER_FILE",
}
_TMUX_BOOTSTRAP_ENV_VARS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "TMPDIR",
    "TMUX_TMPDIR",
    "USER",
    "XDG_RUNTIME_DIR",
}
TMUX_COMMAND_TIMEOUT_SECONDS = 10.0


class TmuxCommandTimeout(RuntimeError):
    """A local tmux control client exceeded its bounded response window."""


@dataclass(frozen=True)
class _TmuxCommandResult:
    """Small libtmux-compatible result for one bounded client command."""

    cmd: list[str]
    returncode: int
    stdout: list[str]
    stderr: list[str]


class _BoundedTmuxServer(libtmux.Server):
    """libtmux server whose control clients cannot wait forever.

    libtmux's default command runner calls ``Popen.communicate()`` without a
    timeout. A wedged tmux server could therefore retain a request or recovery
    worker indefinitely. Every Session/Window/Pane object delegates back to
    this server method, so this boundary covers both direct and enriched-object
    commands without changing libtmux's higher-level object model.
    """

    def cmd(self, cmd: str, *args: Any, target: str | int | None = None) -> _TmuxCommandResult:
        executable = shutil.which("tmux")
        if executable is None:
            raise RuntimeError("tmux executable is unavailable")
        command: list[str] = [executable]
        if self.socket_name:
            command.append(f"-L{self.socket_name}")
        if self.socket_path:
            command.append(f"-S{self.socket_path}")
        if self.config_file:
            command.append(f"-f{self.config_file}")
        if self.colors == 256:
            command.append("-2")
        elif self.colors == 88:
            command.append("-8")
        elif self.colors is not None:
            raise ValueError(f"Unsupported tmux color mode: {self.colors}")
        command.append(cmd)
        if target is not None:
            command.extend(["-t", str(target)])
        command.extend(str(argument) for argument in args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="backslashreplace",
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Do not include the command: transient new-session arguments can
            # contain a terminal-scoped capability.
            raise TmuxCommandTimeout(
                f"tmux control command exceeded {TMUX_COMMAND_TIMEOUT_SECONDS:g} seconds"
            ) from exc
        stdout = completed.stdout.splitlines()
        stderr = [line for line in completed.stderr.splitlines() if line]
        if cmd == "has-session" and stderr and not stdout:
            stdout = [stderr[0]]
        return _TmuxCommandResult(
            cmd=command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )


@dataclass(frozen=True)
class PaneDeliveryTarget:
    """One positively identified tmux pane suitable for control delivery."""

    pane_id: str
    current_command: str


@dataclass(frozen=True)
class RuntimePaneTarget:
    """Exact process-backed pane identity used only for runtime retirement."""

    pane_id: str
    pane_pid: int
    current_command: str
    terminal_id: str
    runtime_generation: str
    process_start_ticks: int
    generation_inherited: bool = True
    process_group_id: int | None = None
    process_session_id: int | None = None


class PaneTargetError(RuntimeError):
    """A fail-closed exact-pane inventory result."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class TmuxClient:
    """Simplified tmux client for basic operations."""

    def __init__(self) -> None:
        self.server = _BoundedTmuxServer()

    # Directories that should never be used as working directories.
    # Prevents user-supplied paths from pointing at sensitive system locations.
    # Includes /private/* variants for macOS (where /etc -> /private/etc, etc.).
    _BLOCKED_DIRECTORIES = frozenset(
        {
            "/",
            "/bin",
            "/sbin",
            "/usr/bin",
            "/usr/sbin",
            "/etc",
            "/var",
            "/tmp",
            "/dev",
            "/proc",
            "/sys",
            "/root",
            "/boot",
            "/lib",
            "/lib64",
            "/private/etc",
            "/private/var",
            "/private/tmp",
        }
    )

    def _resolve_and_validate_working_directory(self, working_directory: Optional[str]) -> str:
        """Resolve and validate working directory.

        Canonicalizes the path (resolves symlinks, normalizes ``..``) and
        rejects paths that point to sensitive system directories.

        **Allowed directories:**

        - Any real directory that is not a blocked system path
        - Paths outside ``~/`` are permitted (e.g., ``/Volumes/workplace``,
          ``/opt/projects``, NFS mounts)

        **Blocked (unsafe) directories:**

        - System directories: ``/``, ``/bin``, ``/sbin``, ``/usr/bin``,
          ``/usr/sbin``, ``/etc``, ``/var``, ``/tmp``, ``/dev``, ``/proc``,
          ``/sys``, ``/root``, ``/boot``, ``/lib``, ``/lib64``

        Args:
            working_directory: Optional directory path, defaults to current directory

        Returns:
            Canonicalized absolute path

        Raises:
            ValueError: If directory does not exist or is a blocked system path
        """
        if working_directory is None:
            working_directory = os.getcwd()

        # Expand ~ to the server's home directory so clients can use
        # portable paths like ~/q/my-project without knowing the server's
        # actual home path (e.g., /home/user vs /Users/user).
        working_directory = os.path.expanduser(working_directory)

        # Step 1: Canonicalize the path via realpath to resolve symlinks
        # and .. sequences.  os.path.realpath is recognized by CodeQL as a
        # PathNormalization (transitions taint to NormalizedUnchecked).
        real_path = os.path.realpath(os.path.abspath(working_directory))

        # Step 2: Path-containment guard (CodeQL SafeAccessCheck).
        # CodeQL's py/path-injection two-state taint model requires:
        #   1. PathNormalization (realpath above) → NormalizedUnchecked
        #   2. SafeAccessCheck (startswith guard) → sanitized
        # CodeQL recognizes str.startswith() as a SafeAccessCheck; when
        # the true branch flows to filesystem ops, the path is cleared.
        # The "/" prefix is always true after realpath(), but this
        # explicit guard satisfies CodeQL and rejects relative paths.
        if not real_path.startswith("/"):
            raise ValueError(f"Working directory must be an absolute path: {working_directory}")

        # Step 3: Block sensitive system directories.
        # Only the exact listed paths are blocked — not their subdirectories.
        # This prevents launching agents in /etc, /var, /root, etc., while
        # still allowing legitimate paths like /Volumes/workplace or even
        # /var/folders (macOS temp) that happen to be under a blocked prefix.
        if real_path in self._BLOCKED_DIRECTORIES:
            raise ValueError(
                f"Working directory not allowed: {working_directory} "
                f"(resolves to blocked system path {real_path})"
            )

        # Step 4: Verify the directory actually exists
        if not os.path.isdir(real_path):
            raise ValueError(f"Working directory does not exist: {working_directory}")

        return real_path

    def _start_credential_free_bootstrap(self, working_directory: str) -> str:
        """Start or join tmux through a client with a minimal safe environment.

        The first tmux client becomes the long-lived server when none exists.
        A libtmux session environment controls its child pane but does not
        sanitize the client environment inherited by that server process.
        """
        bootstrap_session_name = f"cao-bootstrap-{uuid.uuid4().hex}"
        bootstrap_environment = {
            key: value for key, value in os.environ.items() if key in _TMUX_BOOTSTRAP_ENV_VARS
        }
        executable = shutil.which("tmux", path=bootstrap_environment.get("PATH"))
        if executable is None:
            raise RuntimeError("tmux executable is unavailable")
        command = [executable]
        socket_path = getattr(self.server, "socket_path", None)
        socket_name = getattr(self.server, "socket_name", None)
        config_file = getattr(self.server, "config_file", None)
        if socket_path:
            command.extend(["-S", str(socket_path)])
        elif socket_name:
            command.extend(["-L", str(socket_name)])
        if config_file:
            command.extend(["-f", str(config_file)])
        command.extend(
            [
                "new-session",
                "-d",
                "-s",
                bootstrap_session_name,
                "-n",
                "bootstrap",
                "-c",
                working_directory,
            ]
        )
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=bootstrap_environment,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )
        return bootstrap_session_name

    def create_session(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        terminal_auth_token: Optional[str] = None,
        runtime_generation: Optional[str] = None,
    ) -> str:
        """Create detached tmux session with initial window and return window name."""
        bootstrap_session_name: Optional[str] = None
        try:
            working_directory = self._resolve_and_validate_working_directory(working_directory)

            # Filter out provider env vars that would cause "nested session"
            # errors when CAO itself runs inside a provider (e.g. Claude Code).
            # Preserve CLAUDE_CODE_USE_* and CLAUDE_CODE_SKIP_* vars needed
            # for provider authentication (Bedrock, Vertex AI, Foundry).
            blocked_prefixes = ("CLAUDE", "CODEX_")
            allowed_vars = {
                "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY",
                "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
                "CLAUDE_CODE_SKIP_VERTEX_AUTH",
                "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
            }
            environment = {
                k: v
                for k, v in os.environ.items()
                if k not in _OPERATOR_AUTH_REFERENCE_ENVS
                and (k in allowed_vars or not any(k.startswith(p) for p in blocked_prefixes))
            }
            environment["CAO_TERMINAL_ID"] = terminal_id
            if runtime_generation:
                environment["CAO_RUNTIME_GENERATION"] = runtime_generation
            if terminal_auth_token:
                environment["CAO_TERMINAL_AUTH_TOKEN"] = terminal_auth_token

            # When no tmux server exists, the first ``new-session`` client is
            # also the long-lived server process. tmux retains that original
            # command line, so passing a terminal credential with the first
            # session would expose it in process inventory for the lifetime of
            # the server. Create a short-lived credential-free session first;
            # the real session is then created by an ordinary transient client.
            bootstrap_session_name = self._start_credential_free_bootstrap(working_directory)
            session = self.server.new_session(
                session_name=session_name,
                window_name=window_name,
                start_directory=working_directory,
                detach=True,
                environment=environment,
            )
            logger.info(
                f"Created tmux session: {session_name} with window: {window_name} in directory: {working_directory}"
            )
            window_name_result = session.windows[0].name
            if window_name_result is None:
                raise ValueError(f"Window name is None for session {session_name}")
            pane = session.windows[0].active_pane
            if runtime_generation:
                if pane is None or not pane.pane_id:
                    raise RuntimeError("Could not bind runtime generation to the initial pane")
                self.server.cmd(
                    "set-option",
                    "-p",
                    "-t",
                    pane.pane_id,
                    "@cao_runtime_generation",
                    runtime_generation,
                )
            return window_name_result
        except Exception as e:
            logger.error(f"Failed to create session {session_name}: {e}")
            raise
        finally:
            if bootstrap_session_name is not None:
                try:
                    self.server.cmd("kill-session", "-t", bootstrap_session_name)
                except Exception:
                    logger.warning(
                        "Could not retire credential-free tmux bootstrap session %s",
                        bootstrap_session_name,
                        exc_info=True,
                    )

    def create_window(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        terminal_auth_token: Optional[str] = None,
        runtime_generation: Optional[str] = None,
    ) -> str:
        """Create window in session and return window name."""
        try:
            working_directory = self._resolve_and_validate_working_directory(working_directory)

            session = self.server.sessions.get(session_name=session_name)
            if not session:
                raise ValueError(f"Session '{session_name}' not found")

            # Operator authentication belongs only to the server/control client.
            # Remove even a stale inherited reference before creating a child pane.
            for variable in sorted(_OPERATOR_AUTH_REFERENCE_ENVS):
                self.server.cmd("set-environment", "-t", session_name, "-u", variable)

            window = session.new_window(
                window_name=window_name,
                start_directory=working_directory,
                environment={
                    "CAO_TERMINAL_ID": terminal_id,
                    **(
                        {"CAO_RUNTIME_GENERATION": runtime_generation} if runtime_generation else {}
                    ),
                    **(
                        {"CAO_TERMINAL_AUTH_TOKEN": terminal_auth_token}
                        if terminal_auth_token
                        else {}
                    ),
                },
            )

            logger.info(
                f"Created window '{window.name}' in session '{session_name}' in directory: {working_directory}"
            )
            window_name_result = window.name
            if window_name_result is None:
                raise ValueError(f"Window name is None for session {session_name}")
            if runtime_generation:
                pane = window.active_pane
                if pane is None or not pane.pane_id:
                    raise RuntimeError("Could not bind runtime generation to the new pane")
                self.server.cmd(
                    "set-option",
                    "-p",
                    "-t",
                    pane.pane_id,
                    "@cao_runtime_generation",
                    runtime_generation,
                )
            return window_name_result
        except Exception as e:
            logger.error(f"Failed to create window in session {session_name}: {e}")
            raise

    def send_keys(
        self,
        session_name: str,
        window_name: str,
        keys: str,
        enter_count: int = 1,
        *,
        pane_id: Optional[str] = None,
    ) -> None:
        """Send keys to window using tmux paste-buffer for instant delivery.

        Uses load-buffer + paste-buffer instead of chunked send-keys to avoid
        slow character-by-character input and special character interpretation.
        The -p flag enables bracketed paste mode so multi-line content is treated
        as a single input rather than submitting on each newline.

        Args:
            session_name: Name of tmux session
            window_name: Name of window in session
            keys: Text to send
            enter_count: Number of Enter keys to send after pasting (default 1).
                Some TUIs enter multi-line mode after bracketed paste,
                requiring 2 Enters to submit.
        """
        target = pane_id or f"{session_name}:{window_name}"
        buf_name = f"cao_{uuid.uuid4().hex[:8]}"
        try:
            logger.info(f"send_keys: {target} - keys: {keys}")
            result = subprocess.run(
                ["tmux", "load-buffer", "-b", buf_name, "-"],
                input=keys.encode(),
                check=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-p", "-b", buf_name, "-t", target],
                check=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            # Brief delay to let the TUI process the bracketed paste end sequence
            # before sending Enter. Without this, some TUIs (e.g., Claude Code 2.x)
            # swallow the Enter that immediately follows paste-buffer -p.
            time.sleep(0.3)
            for i in range(enter_count):
                if i > 0:
                    # Delay between Enter presses for TUIs that need time to
                    # process the previous Enter (e.g., Ink adding a newline)
                    # before the next Enter triggers form submission.
                    time.sleep(0.5)
                subprocess.run(
                    ["tmux", "send-keys", "-t", target, "Enter"],
                    check=True,
                    timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
                )
            logger.debug(f"Sent keys to {target}")
        except Exception as e:
            logger.error(f"Failed to send keys to {target}: {e}")
            raise
        finally:
            subprocess.run(
                ["tmux", "delete-buffer", "-b", buf_name],
                check=False,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )

    def send_keys_via_paste(self, session_name: str, window_name: str, text: str) -> None:
        """Send text to window via tmux paste buffer with bracketed paste mode.

        Uses tmux set-buffer + paste-buffer -p to send text as a bracketed paste,
        which bypasses TUI hotkey handling. Essential for Ink-based CLIs and
        other TUI apps where individual keystrokes may trigger hotkeys.

        After pasting, sends C-m (Enter) to submit the input.

        Args:
            session_name: Name of tmux session
            window_name: Name of window in session
            text: Text to paste into the pane
        """
        try:
            logger.info(
                f"send_keys_via_paste: {session_name}:{window_name} - text length: {len(text)}"
            )

            session = self.server.sessions.get(session_name=session_name)
            if not session:
                raise ValueError(f"Session '{session_name}' not found")

            window = session.windows.get(window_name=window_name)
            if not window:
                raise ValueError(f"Window '{window_name}' not found in session '{session_name}'")

            pane = window.active_pane
            if pane:
                buf_name = "cao_paste"

                # Load text into tmux buffer
                self.server.cmd("set-buffer", "-b", buf_name, text)

                # Paste with bracketed paste mode (-p flag).
                # This wraps the text in \x1b[200~ ... \x1b[201~ escape sequences,
                # telling the TUI "this is pasted text" so it bypasses hotkey handling.
                pane.cmd("paste-buffer", "-p", "-b", buf_name)

                time.sleep(0.3)

                # Send Enter to submit the pasted text
                pane.send_keys("C-m", enter=False)

                # Clean up the paste buffer
                try:
                    self.server.cmd("delete-buffer", "-b", buf_name)
                except Exception:
                    pass

                logger.debug(f"Sent text via paste to {session_name}:{window_name}")
        except Exception as e:
            logger.error(f"Failed to send text via paste to {session_name}:{window_name}: {e}")
            raise

    def send_special_key(
        self,
        session_name: str,
        window_name: str,
        key: str,
        *,
        pane_id: Optional[str] = None,
    ) -> None:
        """Send a tmux special key sequence (e.g., C-d, C-c) to a window.

        Unlike send_keys(), this sends the key as a tmux key name (not literal text)
        and does not append a carriage return. Used for control signals like Ctrl+D (EOF).

        Args:
            session_name: Name of tmux session
            window_name: Name of window in session
            key: Tmux key name (e.g., "C-d", "C-c", "Escape")
        """
        try:
            target = pane_id or f"{session_name}:{window_name}"
            logger.info(f"send_special_key: {target} - key: {key}")
            if pane_id is not None:
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane_id, key],
                    check=True,
                    timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
                )
            else:
                session = self.server.sessions.get(session_name=session_name)
                if not session:
                    raise ValueError(f"Session '{session_name}' not found")
                window = session.windows.get(window_name=window_name)
                if not window:
                    raise ValueError(
                        f"Window '{window_name}' not found in session '{session_name}'"
                    )
                pane = window.active_pane
                if not pane:
                    raise ValueError(
                        f"Window '{window_name}' has no active pane in session '{session_name}'"
                    )
                pane.send_keys(key, enter=False)
            logger.debug(f"Sent special key to {target}")
        except Exception as e:
            logger.error(f"Failed to send special key to {session_name}:{window_name}: {e}")
            raise

    def exact_pane_target(self, session_name: str, window_name: str) -> PaneDeliveryTarget:
        """Resolve exactly one live pane, distinguishing absence from uncertainty.

        Delivery is pane-addressed after this check.  A window lookup alone is
        insufficient because tmux routes a window target to whichever pane is
        active at send time, and a split or replaced window would make that
        authority ambiguous.
        """
        try:
            session = self.server.sessions.get(session_name=session_name)
        except ObjectDoesNotExist:
            raise PaneTargetError(
                "EXIT_SESSION_MISSING", f"Tmux session '{session_name}' no longer exists"
            )
        except Exception as exc:
            raise PaneTargetError(
                "EXIT_INVENTORY_UNCERTAIN",
                f"Could not inventory tmux session '{session_name}'",
            ) from exc
        if not session:
            raise PaneTargetError(
                "EXIT_SESSION_MISSING", f"Tmux session '{session_name}' no longer exists"
            )
        try:
            window = session.windows.get(window_name=window_name)
        except ObjectDoesNotExist:
            raise PaneTargetError(
                "EXIT_WINDOW_MISSING",
                f"Tmux window '{session_name}:{window_name}' no longer exists",
            )
        except Exception as exc:
            raise PaneTargetError(
                "EXIT_INVENTORY_UNCERTAIN",
                f"Could not inventory tmux window '{session_name}:{window_name}'",
            ) from exc
        if not window:
            raise PaneTargetError(
                "EXIT_WINDOW_MISSING",
                f"Tmux window '{session_name}:{window_name}' no longer exists",
            )
        try:
            panes = list(window.panes)
        except Exception as exc:
            raise PaneTargetError(
                "EXIT_INVENTORY_UNCERTAIN",
                f"Could not inventory panes for '{session_name}:{window_name}'",
            ) from exc
        if not panes:
            raise PaneTargetError(
                "EXIT_PANE_MISSING", f"Tmux window '{session_name}:{window_name}' has no pane"
            )
        if len(panes) != 1:
            raise PaneTargetError(
                "EXIT_PANE_AMBIGUOUS",
                f"Tmux window '{session_name}:{window_name}' has {len(panes)} panes",
            )
        pane = panes[0]
        pane_id = getattr(pane, "pane_id", None)
        if not isinstance(pane_id, str) or not re.fullmatch(r"%[0-9]+", pane_id):
            raise PaneTargetError(
                "EXIT_INVENTORY_UNCERTAIN",
                f"Could not establish an exact pane for '{session_name}:{window_name}'",
            )
        try:
            result = pane.cmd("display-message", "-p", "#{pane_dead} #{pane_current_command}")
            output = result.stdout[0].strip() if result.stdout else ""
        except Exception as exc:
            raise PaneTargetError(
                "EXIT_INVENTORY_UNCERTAIN", f"Could not inspect exact pane '{pane_id}'"
            ) from exc
        parts = output.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in {"0", "1"}:
            raise PaneTargetError(
                "EXIT_INVENTORY_UNCERTAIN", f"Could not inspect exact pane '{pane_id}'"
            )
        if parts[0] == "1":
            raise PaneTargetError("EXIT_PANE_DEAD", f"Tmux pane '{pane_id}' is already dead")
        command = parts[1].strip()
        if not command:
            raise PaneTargetError(
                "EXIT_INVENTORY_UNCERTAIN", f"Exact pane '{pane_id}' has no live command"
            )
        return PaneDeliveryTarget(pane_id=pane_id, current_command=command)

    def exact_runtime_target(
        self,
        session_name: str,
        window_name: str,
        *,
        proc_root: Path = Path("/proc"),
    ) -> RuntimePaneTarget:
        """Resolve a pane to its inherited durable terminal identity.

        Window names are reusable and therefore are not retirement authority.
        The exact tmux pane id, its current shell PID, and the launch-time
        ``CAO_TERMINAL_ID`` inherited by that process must all agree.
        """
        target = self.exact_pane_target(session_name, window_name)
        try:
            result = subprocess.run(
                [
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    target.pane_id,
                    "#{pane_pid}\t#{@cao_runtime_generation}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            raw_pid, runtime_generation = result.stdout.rstrip("\n").split("\t", 1)
            if not raw_pid.isdigit() or int(raw_pid) <= 1:
                raise ValueError("invalid pane pid")
            pane_pid = int(raw_pid)
            environ = (proc_root / str(pane_pid) / "environ").read_bytes()
            stat = (proc_root / str(pane_pid) / "stat").read_text(encoding="utf-8")
            suffix = stat[stat.rfind(")") + 2 :].split()
            process_group_id = int(suffix[2])
            process_session_id = int(suffix[3])
            process_start_ticks = int(suffix[19])
            if (
                process_group_id <= 1
                or process_session_id <= 1
                or process_start_ticks <= 0
                or not runtime_generation
            ):
                raise ValueError("invalid launch generation")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise PaneTargetError(
                "RUNTIME_IDENTITY_UNCERTAIN",
                f"Could not establish process identity for exact pane '{target.pane_id}'",
            ) from exc
        terminal_id = ""
        inherited_generation = ""
        for item in environ.split(b"\0"):
            if item.startswith(b"CAO_TERMINAL_ID="):
                terminal_id = item.partition(b"=")[2].decode("utf-8", errors="strict")
            elif item.startswith(b"CAO_RUNTIME_GENERATION="):
                inherited_generation = item.partition(b"=")[2].decode("utf-8", errors="strict")
        if not terminal_id:
            raise PaneTargetError(
                "RUNTIME_IDENTITY_UNKNOWN",
                f"Exact pane '{target.pane_id}' has no complete runtime identity",
            )
        if inherited_generation and inherited_generation != runtime_generation:
            raise PaneTargetError(
                "RUNTIME_GENERATION_MISMATCH",
                f"Exact pane '{target.pane_id}' has inconsistent runtime generation",
            )
        return RuntimePaneTarget(
            pane_id=target.pane_id,
            pane_pid=pane_pid,
            current_command=target.current_command,
            terminal_id=terminal_id,
            runtime_generation=runtime_generation,
            process_start_ticks=process_start_ticks,
            generation_inherited=bool(inherited_generation),
            process_group_id=process_group_id,
            process_session_id=process_session_id,
        )

    def bind_legacy_runtime_generation(
        self,
        session_name: str,
        window_name: str,
        expected_terminal_id: str,
        runtime_generation: str,
        *,
        proc_root: Path = Path("/proc"),
    ) -> RuntimePaneTarget:
        """Bind an additive generation to one positively identified legacy pane.

        Existing panes cannot have their process environment rewritten. The
        exact process start identity and inherited terminal ID therefore fence
        the one-time pane option, which is re-observed before it is persisted.
        """
        target = self.exact_pane_target(session_name, window_name)
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", target.pane_id, "#{pane_pid}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )
        raw_pid = result.stdout.strip()
        if not raw_pid.isdigit() or int(raw_pid) <= 1:
            raise PaneTargetError("RUNTIME_IDENTITY_UNCERTAIN", "Invalid legacy pane PID")
        pane_pid = int(raw_pid)
        try:
            environ = (proc_root / str(pane_pid) / "environ").read_bytes().split(b"\0")
            stat = (proc_root / str(pane_pid) / "stat").read_text(encoding="utf-8")
            suffix = stat[stat.rfind(")") + 2 :].split()
            process_start_ticks = int(suffix[19])
        except (OSError, ValueError, IndexError) as exc:
            raise PaneTargetError(
                "RUNTIME_IDENTITY_UNCERTAIN", "Could not inspect legacy process identity"
            ) from exc
        expected = f"CAO_TERMINAL_ID={expected_terminal_id}".encode()
        if expected not in environ:
            raise PaneTargetError(
                "RUNTIME_IDENTITY_UNKNOWN", "Legacy pane terminal identity does not match"
            )
        predicate = f"#{{==:#{{pane_pid}},{pane_pid}}}"
        subprocess.run(
            [
                "tmux",
                "if-shell",
                "-F",
                "-t",
                target.pane_id,
                predicate,
                (
                    f"set-option -p -t {target.pane_id} "
                    f"@cao_runtime_generation {runtime_generation}"
                ),
                "",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )
        rebound = self.exact_runtime_target(session_name, window_name, proc_root=proc_root)
        if (
            rebound.pane_id != target.pane_id
            or rebound.pane_pid != pane_pid
            or rebound.process_start_ticks != process_start_ticks
            or rebound.terminal_id != expected_terminal_id
            or rebound.runtime_generation != runtime_generation
        ):
            raise PaneTargetError(
                "RUNTIME_GENERATION_MISMATCH", "Legacy runtime changed during reconciliation"
            )
        return rebound

    def retire_runtime_pane(
        self, target: RuntimePaneTarget, *, proc_root: Path = Path("/proc")
    ) -> bool:
        """Retire only a still-matching launch generation at an idle shell.

        Observation is repeated immediately before the conditional tmux
        mutation. The tmux predicate rechecks pane PID, command, and the
        pane-scoped immutable generation before either stopping capture or
        killing the pane.
        """
        try:
            result = subprocess.run(
                [
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    target.pane_id,
                    "#{pane_pid}\t#{pane_current_command}\t#{@cao_runtime_generation}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            raw_pid, command, runtime_generation = result.stdout.rstrip("\n").split("\t", 2)
            if not raw_pid.isdigit():
                return False
            current_pid = int(raw_pid)
            if (
                current_pid != target.pane_pid
                or command != target.current_command
                or runtime_generation != target.runtime_generation
            ):
                return False
            stat = (proc_root / str(current_pid) / "stat").read_text(encoding="utf-8")
            suffix = stat[stat.rfind(")") + 2 :].split()
            if int(suffix[19]) != target.process_start_ticks:
                return False
            if target.process_group_id is not None and int(suffix[2]) != target.process_group_id:
                return False
            if (
                target.process_session_id is not None
                and int(suffix[3]) != target.process_session_id
            ):
                return False
            environ = (proc_root / str(current_pid) / "environ").read_bytes().split(b"\0")
            expected_terminal = f"CAO_TERMINAL_ID={target.terminal_id}".encode()
            expected_generation = f"CAO_RUNTIME_GENERATION={target.runtime_generation}".encode()
            if expected_terminal not in environ or (
                target.generation_inherited and expected_generation not in environ
            ):
                return False
            predicate = (
                "#{&&:"
                f"#{{&&:#{{==:#{{pane_pid}},{target.pane_pid}}},"
                f"#{{==:#{{pane_current_command}},{target.current_command}}}}},"
                f"#{{==:#{{@cao_runtime_generation}},{target.runtime_generation}}}}}"
            )
            subprocess.run(
                [
                    "tmux",
                    "if-shell",
                    "-F",
                    "-t",
                    target.pane_id,
                    predicate,
                    f"pipe-pane -t {target.pane_id} ; kill-pane -t {target.pane_id}",
                    "",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            probe = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            if probe.returncode != 0:
                return "no server running" in probe.stderr.lower()
            return all(
                line.split(maxsplit=1)[:1] != [target.pane_id] for line in probe.stdout.splitlines()
            )
        except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
            logger.warning("Failed to retire exact runtime pane %s: %s", target.pane_id, exc)
            return False

    def get_history(
        self, session_name: str, window_name: str, tail_lines: Optional[int] = None
    ) -> str:
        """Get window history.

        Args:
            session_name: Name of tmux session
            window_name: Name of window in session
            tail_lines: Number of lines to capture from end (default: TMUX_HISTORY_LINES)
        """
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                raise ValueError(f"Session '{session_name}' not found")

            window = session.windows.get(window_name=window_name)
            if not window:
                raise ValueError(f"Window '{window_name}' not found in session '{session_name}'")

            # Keep escape sequences and join only tmux-marked soft wraps.  ``-J``
            # preserves hard newlines while retaining logical long lines for all
            # provider/status consumers.
            pane = window.panes[0]
            lines = tail_lines if tail_lines is not None else TMUX_HISTORY_LINES
            result = pane.cmd("capture-pane", "-e", "-p", "-J", "-S", f"-{lines}")
            # Join all lines with newlines to get complete output
            return "\n".join(result.stdout) if result.stdout else ""
        except Exception as e:
            logger.error(f"Failed to get history from {session_name}:{window_name}: {e}")
            raise

    def list_sessions(self) -> Optional[List[Dict[str, Optional[str]]]]:
        """List all tmux sessions, or ``None`` when inventory is uncertain."""
        try:
            sessions: List[Dict[str, Optional[str]]] = []
            for session in self.server.sessions:
                # Check if session has attached clients
                is_attached = len(getattr(session, "attached_sessions", [])) > 0

                session_name = session.name if session.name is not None else ""
                # ``session_created`` is tmux's #{session_created} format value
                # (a Unix timestamp).  Keep it machine-readable rather than
                # deriving chronology from tmux's iteration order.
                session_created = getattr(session, "session_created", None)
                created_at = (
                    str(session_created) if isinstance(session_created, (str, int, float)) else None
                )
                sessions.append(
                    {
                        "id": session_name,
                        "name": session_name,
                        "status": "active" if is_attached else "detached",
                        "created_at": created_at,
                    }
                )

            return sessions
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return None

    def get_session_windows(self, session_name: str) -> List[Dict[str, str]]:
        """Get all windows in a session."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                return []

            windows: List[Dict[str, str]] = []
            for window in session.windows:
                window_name = window.name if window.name is not None else ""
                windows.append({"name": window_name, "index": str(window.index)})

            return windows
        except Exception as e:
            logger.error(f"Failed to get windows for session {session_name}: {e}")
            return []

    def kill_session(self, session_name: str) -> bool:
        """Kill tmux session."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if session:
                session.kill()
                logger.info(f"Killed tmux session: {session_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to kill session {session_name}: {e}")
            return False

    def kill_window(self, session_name: str, window_name: str) -> bool:
        """Kill a specific tmux window within a session."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                return False
            window = session.windows.get(window_name=window_name)
            if window:
                window.kill()
                logger.info(f"Killed tmux window: {session_name}:{window_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to kill window {session_name}:{window_name}: {e}")
            return False

    def session_exists(self, session_name: str) -> Optional[bool]:
        """Return exact session existence, preserving inventory uncertainty as ``None``."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            return session is not None
        except ObjectDoesNotExist:
            # libtmux uses this exact exception to report a completed lookup
            # whose requested object is absent.  It is not an inventory error.
            return False
        except Exception as exc:
            logger.warning("Failed to inventory tmux session %s: %s", session_name, exc)
            return None

    def window_exists(self, session_name: str, window_name: str) -> Optional[bool]:
        """Return exact window existence, preserving inventory uncertainty as ``None``."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                return False
            return session.windows.get(window_name=window_name) is not None
        except Exception as exc:
            logger.warning(
                "Failed to inventory tmux window %s:%s: %s", session_name, window_name, exc
            )
            return None

    def get_pane_current_command(self, session_name: str, window_name: str) -> Optional[str]:
        """Return the foreground pane command, preserving uncertainty as ``None``."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                return None
            window = session.windows.get(window_name=window_name)
            if not window or not window.active_pane:
                return None
            result = window.active_pane.cmd(
                "display-message", "-p", "#{pane_dead} #{pane_current_command}"
            )
            if not result.stdout:
                return None
            parts = result.stdout[0].strip().split(maxsplit=1)
            if len(parts) != 2 or parts[0] not in {"0", "1"}:
                return None
            if parts[0] == "1":
                return ""
            return parts[1].strip() or None
        except Exception as exc:
            logger.warning(
                "Failed to inspect tmux pane command %s:%s: %s",
                session_name,
                window_name,
                exc,
            )
            return None

    def get_pane_process_id(self, session_name: str, window_name: str) -> Optional[int]:
        """Return the exact live pane process id, preserving uncertainty as ``None``."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                return None
            window = session.windows.get(window_name=window_name)
            if not window:
                return None
            panes = list(window.panes)
            if len(panes) != 1:
                return None
            result = panes[0].cmd("display-message", "-p", "#{pane_dead} #{pane_pid}")
            if not result.stdout:
                return None
            parts = result.stdout[0].strip().split()
            if len(parts) != 2 or parts[0] != "0" or not parts[1].isdigit():
                return None
            process_id = int(parts[1])
            return process_id if process_id > 1 else None
        except Exception as exc:
            logger.warning(
                "Failed to inspect tmux pane process %s:%s: %s",
                session_name,
                window_name,
                exc,
            )
            return None

    def get_pane_working_directory(self, session_name: str, window_name: str) -> Optional[str]:
        """Get the current working directory of a pane."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                return None

            window = session.windows.get(window_name=window_name)
            if not window:
                return None

            pane = window.active_pane
            if pane:
                # Get pane_current_path from tmux
                result = pane.cmd("display-message", "-p", "#{pane_current_path}")
                if result.stdout:
                    return result.stdout[0].strip()
            return None
        except Exception as e:
            logger.error(f"Failed to get working directory for {session_name}:{window_name}: {e}")
            return None

    def get_session_root_working_directory(self, session_name: str) -> Optional[str]:
        """Get the root directory from the session's initial tmux window."""
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session or not session.windows:
                return None
            root_window = min(session.windows, key=lambda window: int(window.index))
            if not root_window.name:
                return None
            return self.get_pane_working_directory(session_name, root_window.name)
        except Exception as e:
            logger.error(f"Failed to get session root working directory for {session_name}: {e}")
            return None

    def pipe_pane(self, session_name: str, window_name: str, file_path: str) -> None:
        """Start piping pane output to file.

        Args:
            session_name: Tmux session name
            window_name: Tmux window name
            file_path: Absolute path to log file
        """
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                raise ValueError(f"Session '{session_name}' not found")

            window = session.windows.get(window_name=window_name)
            if not window:
                raise ValueError(f"Window '{window_name}' not found in session '{session_name}'")

            pane = window.active_pane
            if pane:
                pane.cmd("pipe-pane", "-o", f"cat >> {file_path}")
                logger.info(f"Started pipe-pane for {session_name}:{window_name} to {file_path}")
        except Exception as e:
            logger.error(f"Failed to start pipe-pane for {session_name}:{window_name}: {e}")
            raise

    def stop_pipe_pane(self, session_name: str, window_name: str) -> None:
        """Stop piping pane output.

        Args:
            session_name: Tmux session name
            window_name: Tmux window name
        """
        try:
            session = self.server.sessions.get(session_name=session_name)
            if not session:
                raise ValueError(f"Session '{session_name}' not found")

            window = session.windows.get(window_name=window_name)
            if not window:
                raise ValueError(f"Window '{window_name}' not found in session '{session_name}'")

            pane = window.active_pane
            if pane:
                pane.cmd("pipe-pane")
                logger.info(f"Stopped pipe-pane for {session_name}:{window_name}")
        except Exception as e:
            logger.error(f"Failed to stop pipe-pane for {session_name}:{window_name}: {e}")
            raise


# Module-level singleton
tmux_client = TmuxClient()
