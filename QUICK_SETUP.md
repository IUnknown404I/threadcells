# ThreadCells Quick Setup

This is the fastest supported path from a source checkout to a local ThreadCells server. It builds an immutable local candidate, verifies its contents, installs it under the current repository, and listens only on loopback.

For prerequisites, failure explanations, and service installation, use the full [Installation guide](docs/INSTALLATION.md).

## 1. Check the host

ThreadCells currently targets Ubuntu/Debian Linux with Python 3, Git, tmux, Node.js/npm for the Web build, and at least one supported provider CLI. Codex is the primary tested provider.

From the repository root:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

## 2. Build and verify a candidate

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.2.0a1-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Expected result: verification succeeds for the candidate manifest, files, checksums, and packaged Web UI. A candidate is a self-contained release-shaped directory; keeping it immutable makes the running build identifiable and rollback practical.

## 3. Preview, then install

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

Expected result: the dry run explains its targets without changing them, then the install creates `.threadcells` with a Python environment and ThreadCells commands.

## 4. Run diagnostics

```bash
"$PWD/.threadcells/venv/bin/threadcells" doctor
```

Resolve failed required checks before launching agents. An optional provider can remain absent; it will appear as **CLI not installed** in the UI.

## 5. Start the server

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

Open `http://127.0.0.1:9889`.

Expected result: Home loads, Settings → About shows the running build identity, and this documentation is available under Docs.

Keep the host and port exactly loopback-only for this first run. For another computer, do not change the listener to `0.0.0.0`; use [Remote access](docs/REMOTE_ACCESS.md).

The operating model is intentionally short: create a session, choose an agent or supervisor, give it the job, watch the workflow, and step in only for an explicit owner decision or final review. Provider completion alone does not close an open workflow.

## 6. Start useful work

Follow [Your first project and agent](docs/FIRST_AGENT.md). The included [safe starter example](examples/threadcells-starter/README.md) is also a bounded supervisor/developer/reviewer exercise that does not publish or change services.

## Stop and resume

Stop the foreground server with `Ctrl-C`. Agent terminals are tmux-backed and may outlive a browser connection, but do not assume an interrupted server completed their workflows. Restart the same installed `threadcells-server`, open Agents, and inspect their current state and durable results.

## Next reading

- [Core concepts](docs/CONCEPTS.md)
- [Providers](docs/PROVIDERS.md) and [Profiles](docs/PROFILES.md)
- [Capacity and resource model](docs/RESOURCE_MODEL.md)
- [Housekeeping](docs/HOUSEKEEPING.md)
- [Telegram notifications](docs/TELEGRAM_NOTIFICATIONS.md)
- [Backup and restore](docs/BACKUP_AND_RESTORE.md)
- [Security model](docs/SECURITY_MODEL.md)
