# Legacy Markdown agent profiles

Markdown with YAML frontmatter remains a compatibility and migration format. On first control-plane initialization, ThreadCells deterministically discovers configured legacy directories, preserves packaged built-in precedence, converts safe documents into ProfileDefinition V1 revisions, and records a migration receipt. Invalid or unsafe documents are skipped with bounded diagnostics.

New integrations should use the versioned JSON profile schema, examples, and registry APIs described in [Profile registry](PROFILES.md) and [Control-plane artifacts](CONTROL_PLANE_ARTIFACTS.md). The registry uses trusted `mcp_server_refs`; imported documents cannot define executable MCP commands. Wildcard tools require a separate trusted-operator grant.

Existing terminal/session history and profile IDs remain compatible. Existing terminals are marked `legacy/unavailable snapshot`; new terminals capture immutable profile/provider snapshots before provider start.
