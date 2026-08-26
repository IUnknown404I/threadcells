# Installation

This guide explains the supported local installation path and the reason ThreadCells installs from a verified candidate. If you want only the commands, use [Quick setup](../QUICK_SETUP.md).

## Supported baseline

The current technical preview supports a single Ubuntu/Debian Linux host. ThreadCells expects a trusted operator account and a local Git checkout. Other Linux distributions may work but are not the supported baseline; macOS and Windows can access the Web UI remotely but are not supported ThreadCells hosts.

## Prerequisites

Install or verify:

- Python 3 and `venv` support;
- Git;
- tmux;
- Node.js and npm for building the packaged Web UI;
- common POSIX utilities used by the release and service scripts;
- one supported provider CLI, installed and authenticated for the account that will run ThreadCells.

Check the important commands:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

ThreadCells can register adapters whose CLIs are absent. That is not an install failure; only providers you intend to launch must be ready. See [Providers](PROVIDERS.md).

## Where state lives

By default, operational state lives under:

```text
~/.aws/cli-agent-orchestrator/
```

The historic directory name is retained for compatibility. It can contain the SQLite database, logs, managed worktrees, agent context, attachments, provider artifacts, and other runtime state. Set `CAO_HOME_DIR` before first start to choose a different absolute location.

The installed application and its runtime state are different:

- the **candidate/install** contains versioned code and static Web assets;
- the **state root** contains the database, mutable operator data, and optional restrictive ThreadCells-owned secret files such as the Telegram bot token;
- provider CLIs may keep their own credentials and rollout history elsewhere.

Back up mutable state before replacing an installation. Never commit runtime state or provider credentials.

## Why a local candidate?

A candidate is a release-shaped directory built from one exact source revision. Its manifest and checksums let you verify what will run before touching an installation. Staging and promotion can then preserve the old candidate as rollback.

This discipline is more deliberate than running directly from a changing checkout, but it prevents the Web UI, Python code, docs, and build identity from silently coming from different revisions.

## Build the candidate

From the repository root:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a2-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Expected result: the verifier accepts the manifest, checksums, packaged documentation, and application files. Do not install a candidate that fails verification.

## Preview and install

Choose an absolute prefix that the runtime account can execute. The repository-local prefix below is convenient for evaluation:

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

The dry run is intentionally first. Review its source and target, then run the real installation.

## Verify the installed CLI

```bash
"$PWD/.threadcells/venv/bin/threadcells" info
"$PWD/.threadcells/venv/bin/threadcells" doctor
"$PWD/.threadcells/venv/bin/threadcells" providers list
```

`doctor` is read-only. Resolve missing required system utilities. Provider output should distinguish a known adapter from an installed and usable CLI.

## Start locally

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

In another shell:

```bash
curl -fsS http://127.0.0.1:9889/health
```

Open `http://127.0.0.1:9889`. Check Settings → About and confirm its version and revision match the candidate you verified.

For a persistent installation, use the repository's canonical service/deployment mechanism described in [Deployment](DEPLOYMENT.md). Do not improvise a public bind address.

## Initial failures

- **`python3 -m venv` fails:** install the distribution's Python venv package.
- **`tmux` is missing:** install it before launching agents; terminal persistence depends on it.
- **Web assets fail to build:** use the supported Node/npm baseline, install locked dependencies, and rebuild the candidate.
- **Provider says CLI not installed:** install that provider's canonical command for the runtime user, or choose an already-ready provider.
- **Provider is installed but not authenticated:** complete the provider's own login flow as the runtime user, then repeat preflight.
- **Port 9889 is busy:** stop the conflicting local process or choose another loopback port and use it consistently.
- **Browser on another machine cannot connect:** this is expected for a loopback listener. Use [Remote access](REMOTE_ACCESS.md).

## Removal boundaries

Removing an install prefix does not safely remove operational state, provider credentials, Git repositories, worktrees, backups, or service definitions. Stop ThreadCells, make a verified backup, and identify each of those categories separately. Use Housekeeping for eligible runtime artifacts; do not recursively delete the state root as an uninstall shortcut.

Next, follow [Your first project and agent](FIRST_AGENT.md).
