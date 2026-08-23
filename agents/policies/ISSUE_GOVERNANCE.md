# Issue governance

[`docs/ISSUES.md`](../../docs/ISSUES.md) is the canonical public Issue policy. Before creating a public GitHub Issue, an agent must:

1. verify the finding remains unresolved;
2. search open and closed Issues for duplicates;
3. distinguish actionable public project debt from owner-only repository, account, credential, or private-infrastructure administration;
4. verify that public disclosure is safe;
5. identify meaningful impact, the expected outcome, and concrete acceptance criteria; and
6. select the matching form and smallest useful label set.

A finding, audit result, warning, or residual-sweep note is not automatically a public Issue. Do not publish transient noise, safely handled warnings without a defect, already-resolved findings, one-off identifiers without a reproducible class, or mass-generated audit output.

Use `good first issue` only for safe, bounded, low-ambiguity work. Use `help wanted` only when external contribution is welcome and the task is sufficiently specified. Never automatically apply beginner labels to security/authentication, lifecycle/idempotency/exactly-once behavior, destructive safety, release authority, provider trust or remote-code-execution boundaries, migrations, or data integrity.
