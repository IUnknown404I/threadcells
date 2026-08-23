# Public Issue policy

GitHub Issues are ThreadCells' curated public backlog, not a transcript of warnings, audits, or release debugging.

## Eligibility

A public Issue should normally satisfy all of these conditions:

- the problem or opportunity remains unresolved;
- it is reproducible or supported by durable evidence;
- it has meaningful user, project, reliability, documentation, or maintainability impact;
- public tracking is useful and actionable for the project or community;
- public disclosure is safe;
- the expected behavior or outcome is clear; and
- concrete acceptance criteria can be stated.

Durable technical evidence may replace reproduction steps when deterministic reproduction is impractical.

## What does not belong in public Issues

Do not create a public Issue merely for:

- repository or account owner-only administration;
- credential administration or private infrastructure work;
- credentials, secrets, or security details that are unsafe to disclose;
- transient CI, environment, network, or runner noise;
- already-resolved findings;
- isolated runtime identifiers without a reproducible underlying problem class;
- warnings that are behaving safely with no demonstrated defect;
- speculative polish without a defined problem and outcome;
- temporary release or debugging observations; or
- unclassified notes from an audit or residual-debt sweep.

Owner-only actions belong in the repository's operational owner channel, not the contributor backlog. A finding becomes a public Issue only after it passes the eligibility gate.

## Report content

Use the matching Issue form and provide the useful parts of this structure:

1. **Problem / Context**
2. **Impact**
3. **Current behavior**
4. **Expected behavior**
5. **Reproduction or Evidence**
6. **Acceptance criteria**
7. **Non-goals**, when useful

Include environment or version information only when it affects the report. Redact logs and screenshots. Never include secrets, credentials, personal data, private messages, unnecessary private paths, state databases, or terminal transcripts.

Vulnerabilities and security-sensitive findings must use the private route in [SECURITY.md](../SECURITY.md), not a public Issue.

## Triage and duplicates

Search open and closed Issues before filing. Maintainers link duplicates to the canonical Issue and close them as duplicate rather than splitting discussion and evidence.

Use the smallest useful label set. `bug`, `enhancement`, `documentation`, `accessibility`, and `technical-debt` describe the work; `duplicate` describes triage. Maintainers may request missing evidence before deciding whether a report qualifies.

Close an Issue when the acceptance criteria are satisfied, when it duplicates a canonical Issue, or as not planned with a concise reason when it is out of scope, cannot be made actionable, or no longer justifies project tracking. Already-resolved reports should point to the resolving evidence.

## Contributor labels

Use `good first issue` only for safe, bounded, low-ambiguity work with enough context and acceptance criteria for a new contributor. Use `help wanted` only when external contribution is genuinely welcome and the task is sufficiently specified.

Critical security or authentication boundaries, lifecycle and exactly-once behavior, destructive safety, release authority, provider trust or remote-code-execution boundaries, migrations, and data integrity are never automatically beginner work.
