# Community Echo provider adapter

This minimal package demonstrates the public ThreadCells Provider Adapter API
without modifying orchestration core source.

The package registers trusted Python code through the
`threadcells.provider_adapters.v1` entry-point group. Its declarative settings
allow only the `echo-v1` model. The executable name and argument construction
live in reviewed adapter code; imported JSON cannot supply a command, shell,
arguments, environment, or secret value.

Install the adapter package and its separately trusted
`threadcells-echo-agent` executable, then restart a candidate/local ThreadCells
runtime so installed entry points are rediscovered. This example is not an
actual model provider and is not enabled by default.

Provider configuration:

```json
{
  "schema_version": 1,
  "config_id": "echo-local",
  "adapter_id": "community.echo",
  "display_name": "Local Echo",
  "enabled": true,
  "settings": {"model": "echo-v1"},
  "secret_refs": {}
}
```

