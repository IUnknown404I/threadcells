# Providers

A provider is the native coding-agent CLI that actually runs the model turn. ThreadCells supplies an adapter around that CLI so launches, terminal status, cancellation, capability reporting, and available usage telemetry have a common shape.

## Three different facts

The provider screens deliberately separate three facts that are easy to confuse:

| Fact | Meaning |
| --- | --- |
| Built-in adapter | This ThreadCells build contains reviewed integration code for the provider. |
| CLI installed | The required executable is on the runtime user's `PATH`. |
| Ready | Preflight considers the installed CLI compatible and authenticated, or the CLI cannot safely expose authentication state. |

Settings → Providers lists adapters, including ones whose external command is absent. Spawn Agent uses the same canonical preflight and disables providers proven unavailable.

For example, **Built-in adapter · CLI not installed** is not contradictory. It means ThreadCells knows how to operate the provider but the host does not currently have that provider's program.

## Built-in providers

The current build registers these adapters:

| Provider | Canonical command |
| --- | --- |
| Amazon Q Developer | `q` |
| Claude Code | `claude` |
| Codex | `codex` |
| Gemini CLI | `gemini` |
| GitHub Copilot CLI | `copilot` |
| Kimi CLI | `kimi` |
| Kiro CLI | `kiro-cli` |
| OpenCode CLI | `opencode` |

Registration is factual product support, not an instruction to install every CLI. Install only providers you intend to use, using that provider's official instructions and authentication workflow.

## Compatibility matrix

This matrix describes the adapter contract in this release, not a promise that every external CLI version or account is ready on a particular host. **Supported** means the adapter implements the capability directly, **Conditional** means behavior depends on the provider CLI or session mode, and **Not reported** means ThreadCells does not invent the data.

| Provider | Start/cancel | Resume and persistence | Structured completion | Usage telemetry | Model/reasoning controls | Readiness probe |
| --- | --- | --- | --- | --- | --- | --- |
| Codex | Supported | Conditional resume; supported persistence | Conditional | Supported provider-native token fields | Supported | Command, version, and authentication |
| Claude Code | Supported | Conditional | Conditional | Conditional provider-native fields | Model selection supported; other controls adapter-dependent | Command, version, and authentication |
| Amazon Q Developer | Supported | Conditional | Conditional | Not reported | Conditional | Command and version; authentication unverified |
| Gemini CLI | Supported | Conditional | Conditional | Not reported | Conditional | Command and version; authentication unverified |
| GitHub Copilot CLI | Supported | Conditional | Conditional | Not reported | Conditional | Command and version; authentication unverified |
| Kimi CLI | Supported | Conditional | Conditional | Not reported | Conditional | Command and version; authentication unverified |
| Kiro CLI | Supported | Conditional | Conditional | Not reported | Conditional | Command and version; authentication unverified |
| OpenCode CLI | Supported | Conditional | Conditional | Not reported | Conditional | Command and version; authentication unverified |

Codex is the reference and release-acceptance provider. Other built-in adapters remain usable when their public preflight is launchable, but provider-native behavior and authentication can vary. The live Settings capability view is authoritative for an installed build.

## Availability labels

ThreadCells normalizes preflight into five operator-facing states:

- **Ready** (`INSTALLED_AND_READY`): installed, compatible, and authenticated when authentication can be checked.
- **Authentication required** (`INSTALLED_NOT_AUTHENTICATED`): command exists, but the provider reports that login is required.
- **Installed but unhealthy** (`INSTALLED_BUT_UNHEALTHY`): installed, but incompatible or failing its health/version check.
- **CLI not installed** (`NOT_INSTALLED`): the canonical executable is not found for the ThreadCells runtime user.
- **Readiness unverified** (`UNKNOWN`): installed and not proven unavailable, but the provider cannot safely verify authentication or readiness non-interactively.

An unverified provider can remain launchable when its command is installed, compatible, and the only unknown is authentication state. A launch can still fail with a provider-native login prompt; inspect its terminal and complete provider authentication outside ThreadCells.

## Check the runtime user's view

Provider availability depends on the account that runs ThreadCells, not on your interactive shell. Check through ThreadCells first:

```bash
threadcells providers list
threadcells doctor
```

Then, as the runtime user, verify the expected binary and its version. For Codex:

```bash
command -v codex
codex --version
codex login status
```

Use the provider's own status command where one exists. Do not copy personal provider credential directories into the service account. Authenticate that account using the provider's supported workflow.

## Settings and Spawn Agent

Settings → Providers is the inventory and diagnostics view. It shows adapter identity, configuration, capabilities, command presence, version, authentication state, and a public-safe preflight message.

Spawn Agent is the launch view. It derives its enabled/disabled state from the same preflight result. If the two views disagree after a refresh, treat that as a product defect rather than guessing which label is correct.

## Capabilities are provider-specific

Adapters declare whether resume, structured completion, model selection, reasoning control, session persistence, and usage are supported, conditional, or unsupported. ThreadCells does not simulate an unsupported feature.

Codex is the reference adapter and supplies exact cumulative usage telemetry for supported token fields. Claude Code supports some usage and completion capabilities conditionally. Other adapters may report no usage; their Statistics fields remain unavailable instead of being estimated.

## Configuration and secrets

Provider configuration is declarative. It can select an installed adapter and adapter-owned settings, but it cannot import a binary path, shell command, arguments, environment variables, passwords, tokens, or raw credentials.

Opaque `secret_refs` can name a secret resolved by trusted adapter code. Public list and export responses omit or redact their values. Provider adapter packages are executable trusted code and must be installed and reviewed by the host operator.

## Troubleshooting

### Provider shows CLI not installed

Run `command -v` as the service account and compare its `PATH` with your shell. Install the canonical provider command only if you intend to use it, then restart or refresh preflight.

### Installed but authentication required

Run the provider's official login flow as the runtime user. ThreadCells preflight never authenticates on your behalf and never enables permission-bypass settings.

### Readiness unverified

The command exists but lacks a safe non-interactive readiness probe. Check the version and perform a small provider-native test. A ThreadCells launch may be the first definitive readiness check.

### Installed but unhealthy

Read the safe preflight reason. Common causes are a version command failure, a known-incompatible version, or an executable that exits unexpectedly. Upgrade or repair the external CLI; do not edit the adapter registry to mark it ready.

### Launch fails despite Ready

Open the terminal output. Credentials may have expired after preflight, a selected model may be unavailable, or provider service health may have changed.

For advanced integration details, see [Provider adapter authoring](PROVIDER_ADAPTERS.md). For what a launch profile controls, see [Profiles](PROFILES.md).
