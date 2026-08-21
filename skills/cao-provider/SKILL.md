---
name: cao-provider
description: Create a trusted ThreadCells Provider Adapter V1 package. Use this skill to add a CLI-based AI agent, integrate an installed adapter, or understand provider architecture and tests.
---

# ThreadCells Provider Adapter creator

Create a separately installable trusted-code adapter registered through the `threadcells.provider_adapters.v1` entry-point group. Do not add a static provider enum member or a branch to `ProviderManager`; those are legacy compatibility mechanisms.

Before implementation, read `docs/PROVIDER_ADAPTERS.md`, the four packaged schemas under `src/cli_agent_orchestrator/public_schemas/v1`, and `examples/provider-adapters/threadcells-echo`. Use `CodexProvider` and its built-in registration as the lifecycle reference.

The adapter must provide:

1. a stable manifest and API version `1.0`;
2. truthful normalized capability declarations;
3. a strict Pydantic `AdapterSettings` model for declarative data;
4. secret-free, non-mutating availability/auth/version/model preflight;
5. a factory using `ProviderLaunchContext`;
6. normalized start/resume/cancel/status/result/usage/health/errors;
7. deterministic unit tests for command construction, state, extraction, cleanup, unsupported capabilities, preflight, and validation.

Executable names, paths, commands, arguments, flags, environments, and secret values live only in reviewed adapter code or the installation's trusted secret resolver. Registry JSON must not introduce them. Usage is absent unless the native CLI reports it. Preflight must never authenticate, edit provider settings, or enable permission bypass automatically.

Validate the installed entry point, manifest/schema compatibility, duplicate identity rejection, configuration pointer errors, and source/wheel/candidate inclusion. Live credential smoke tests are optional environment evidence; deterministic tests are required.
