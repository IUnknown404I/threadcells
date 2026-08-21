# Synthetic starter result flow

```text
owner → supervisor: update one scratch Markdown heading
supervisor → developer: make the bounded edit and report its check
supervisor → reviewer: inspect the exact diff and report findings
reviewer → supervisor: no blockers; heading and link check pass
supervisor → owner: changed file, checks, risks, and next decision
```

All names and results in this illustration are synthetic. A real run remains local and requires the owner to inspect the final result before any separate publish, deployment, or service decision.
