# Control-plane artifacts and AI workflow

ThreadCells publishes JSON Schema Draft 2020-12 documents for ProfileDefinition V1, ProviderConfiguration V1, AdapterManifest V1, and AdapterCapabilities V1 under `schemas/v1` in the local candidate and `cli_agent_orchestrator/public_schemas/v1` in the wheel.

Use `threadcells profiles schema|example` or `threadcells providers schema|example` to obtain a starting document. Validate before import. Field failures are stable JSON-pointer records rather than reflected raw values. UI, CLI, and API imports all call the same service and create immutable revisions.

## AI-assisted artifact workflow

1. Fetch the relevant schema, example, and the safe generation prompt from `/api/v1/profiles/ai-prompt` or `/api/v1/providers/ai-prompt`.
2. Ask the model to return one JSON object only. Do not supply credentials, private paths, executable commands, shell flags, or unreviewed MCP commands.
3. Inspect identifiers, provider references, authority, tools, timeouts, and instructions manually.
4. Run `validate`; address every JSON-pointer issue.
5. Import only after operator review. Imports requiring wildcard tools or other privileged authority need the separate trusted-operator path.
6. Use resolved preview before launch and export after import to confirm the redacted canonical artifact.

AI-generated JSON is untrusted input. A plausible document does not install adapter code, register an MCP capability, confer owner authorization, or bypass repository policy. Built-in profiles remain immutable and exports never contain provider credentials or launch grants.
