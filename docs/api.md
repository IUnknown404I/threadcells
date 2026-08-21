# ThreadCells HTTP API

The default base URL is `http://127.0.0.1:9889`. The ordinary API has no general user-authentication boundary in this preview, so keep it on loopback. Interactive OpenAPI documentation is served at `/api/docs`; `/docs` is the packaged product-documentation reader.

## Compatibility resources

- `GET /health` reports runtime health.
- `GET /agents/providers` projects installed adapter preflight and capability data into the legacy provider list.
- `GET /agents/profiles` projects the canonical profile registry into the legacy discovery shape used by Create Session.
- `/projects`, `/sessions`, `/terminals`, `/flows`, and `/delegation-results` retain the existing lifecycle APIs.
- `GET /settings/orchestration-capacity` returns persisted limits, live use, availability, certainty, and draining for resident supervisors/owners, provider executions, delegated Work contexts, and heavy executions.
- `PUT /settings/orchestration-capacity` validates exact integer/non-boolean ranges and requires operator authentication.

OpenAPI is the authoritative request/response reference for these compatibility routes.

### Usage statistics

`GET /usage/statistics` refreshes bounded durable provider telemetry before returning its latest stored projection. It includes `global`, `terminals`, `sessions`, `projects`, `providers`, and `profiles`. Every aggregate reports the observation count plus nullable `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`, and `total_tokens`. The cache-write field is retained as an optional compatibility field, but no current adapter declares it as a meaningful supported metric and the default UI omits it. A null metric means the provider did not report it; consumers must not interpret null as zero. The response is provider telemetry, not a billing statement.

## Versioned control-plane API

### Providers

- `GET /api/v1/providers`
- `POST /api/v1/providers/validate`
- `POST /api/v1/providers/import` (operator-authenticated)
- `GET /api/v1/providers/{config_id}/export`
- `POST /api/v1/providers/{config_id}/preflight`
- `GET /api/v1/providers/ai-prompt`

Validation/import bodies use `{"document": {...}}`; imports may also include `duplicate_builtin`. Failures use JSON-pointer issues with `pointer`, `code`, and `message`. Exports are declarative and secret-free.

### Profiles

- `GET /api/v1/profiles?include_disabled=false`
- `POST /api/v1/profiles/validate`
- `POST /api/v1/profiles/import` (operator-authenticated)
- `GET /api/v1/profiles/{profile_id}`
- `GET /api/v1/profiles/{profile_id}/export`
- `GET /api/v1/profiles/{profile_id}/preview`
- `PATCH /api/v1/profiles/{profile_id}` to enable/disable a custom or referenced profile
- `GET /api/v1/profiles/ai-prompt`

Built-ins are immutable. `duplicate_builtin` creates a custom ID rather than shadowing the built-in. Preview returns the resolved immutable launch inputs without starting a provider.

### Housekeeping

- `GET /api/v1/housekeeping` returns class policy and effective schedules.
- `PUT /api/v1/housekeeping` validates and persists an audited configuration (operator-authenticated).
- `GET /api/v1/housekeeping/plan?mode=frequent` returns an immutable dry-run plan.
- `POST /api/v1/housekeeping/run` requires the inspected dry-run `expected_plan_id`, rebuilds the plan under the housekeeping lock, and fails closed if the candidate set changed before execution (operator-authenticated).
- `GET /api/v1/telegram` returns installation-global enabled/destination and safe token/configuration states, never the bot token.
- `PUT /api/v1/telegram` replaces non-secret settings and optionally the write-only bot token (operator-authenticated).
- `POST /api/v1/telegram/check` validates the configured bot credential without sending a message (operator-authenticated).
- `POST /api/v1/telegram/test` explicitly sends one test message to the configured destination (operator-authenticated).
- `GET /api/v1/housekeeping/report` returns the latest atomic status/report.

The equivalent CLI flow is `cao-housekeeping --dry-run --json` followed by `cao-housekeeping --plan-id <plan_id>`. The explicit `--scheduled` path is reserved for installed timers and builds its due plan inside the same locked operation.

### Public artifacts

- `GET /schemas/v1` lists packaged schemas.
- `GET /schemas/v1/{name}` returns `profile`, `provider-config`, `adapter-manifest`, or `capabilities`.
- `GET /examples/v1/{name}` returns safe profile/provider examples.

## Operator and XHigh boundary

Provision the OS-owned verifier described in [Resource model](RESOURCE_MODEL.md#operator-authorization). `GET /operator/session` reports missing/invalid/ready configuration state, authenticated/expiry state, the five-minute TTL, and the canonical verifier-reference variable name; it never returns the verifier path or secret material. `POST /operator/session` accepts an operator secret of at least 5 characters and creates a five-minute HttpOnly, SameSite=Strict cookie. `DELETE /operator/session` revokes it. API clients may instead supply `Authorization: Bearer …` to operator-protected routes.

`POST /operator/xhigh-grants` requires that authentication plus explicit confirmation. It returns a random one-use grant bound to the requested privileged profile revision, provider configuration revision, project/canonical worktree, optional requested session, launch mode, issuer, and delegation depth. The subsequent session launch must present the grant and matching launch ID; terminal creation consumes it atomically. Ordinary child/supervisor APIs cannot issue or reuse it.

Cookie-authenticated operator mutations require the browser `Origin` to match ThreadCells directly. An owner-approved authenticated HTTPS proxy may add exact public origins through `THREADCELLS_TRUSTED_PROXY_ORIGINS`; values are comma-separated HTTPS origins without paths or wildcards. Invalid or unlisted origins fail closed. ThreadCells still listens only on loopback and does not trust arbitrary forwarded host headers.

Never place an operator secret or launch grant in a URL, terminal prompt, profile, exported artifact, log, or child environment.

## CLI parity

The `threadcells` command (with compatible `cao` aliases) provides:

```text
threadcells profiles list|validate|import|export|example|schema
threadcells providers list|validate|import|export|example|schema
threadcells operator create-verifier --output /absolute/operator-verifier.json
```

Imports call the same services as the UI/API. The CLI reads only explicit `.json` documents, prompts for operator secrets without echo, and emits redacted JSON.
