# Provider adapter authoring

This is an advanced guide for maintainers adding a trusted provider integration. Operators choosing among built-in providers should start with [Providers](PROVIDERS.md).

ThreadCells Provider Adapter API V1 is a trusted-code extension boundary distinct from observer plugins. Install adapters as reviewed Python packages that register `ProviderAdapterDefinition` objects under the `threadcells.provider_adapters.v1` entry-point group. Restart the local candidate/runtime after installation so entry points are rediscovered.

## Contract

An adapter definition supplies:

- an `AdapterManifest` with stable `adapter_id`, plugin API `1.0`, implementation version, description, capabilities, and JSON configuration schema;
- an `AdapterSettings` Pydantic model for declarative settings;
- a factory accepting `ProviderLaunchContext` and validated settings;
- a preflight function returning normalized state, installation, authentication, version, compatibility, models, reason code, and a secret-free message.

The returned provider implements normalized start/resume/cancel, terminal status/result, usage, and health semantics through the existing `BaseProvider` lifecycle. Declare unsupported and conditional capabilities honestly. Never synthesize usage a CLI did not report.

## Trust and configuration

Adapter packages are executable and therefore installed only by the trusted host operator. Registry JSON cannot choose binaries or inject commands. ThreadCells recursively rejects executable, command, shell, argument, flag, environment, credential, password, token, and secret keys. Raw secrets never belong in `settings`; use semantic opaque `secret_refs` and resolve them only inside trusted adapter code according to the installation's secret policy.

Keep errors normalized with stable reason codes and public-safe messages. Preflight must not mutate provider settings or authenticate on the operator's behalf.

## Sample

The installed source/candidate includes `examples/provider-adapters/threadcells-echo`, a deterministic package and manifest demonstrating the entry point, schema, configuration validation, lifecycle, preflight, and unsupported usage. It is not a model provider and is disabled by default. Build/test it independently before installation.

The packaged schemas at `schemas/v1/adapter-manifest.schema.json` and `schemas/v1/capabilities.schema.json` are the portable artifact references. Python contract validation remains authoritative for installed code.

## Readiness must stay truthful

Use the provider's canonical executable name and a bounded, non-mutating probe. Preflight answers installation, compatibility, authentication when safely detectable, and public-safe failure reason. It must not claim that adapter registration makes a CLI available.

Registry APIs, Settings, and Spawn Agent all project this same result. Add coverage that proves a not-installed command is disabled, an authentication failure is distinguished from absence, and an installed provider with genuinely unknowable authentication remains labeled unverified.

## Usage must stay truthful

Prefer a provider-native structured event over terminal-text parsing. Record only fields the provider emits, preserve cumulative checkpoint identity, and make restart/replay idempotent. Never turn an unavailable metric into zero or estimate cost from tokens without an explicit provider contract.

## Review checklist

- Stable adapter ID, version, display name, and configuration schema.
- No caller-selected executable, shell, argument, environment, or raw-secret fields.
- Bounded preflight with no settings or authentication mutation.
- Honest supported/conditional/unsupported capabilities.
- Lifecycle tests for start, status, cancellation, and recoverable failure.
- Exact usage tests when telemetry is supported.
- Registry/Settings/Spawn consistency tests.
- Public-safe errors that contain no credentials or private paths.
