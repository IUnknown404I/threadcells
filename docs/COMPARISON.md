# Where ThreadCells fits

ThreadCells is for developers who already value native coding-agent CLIs but need a clearer way to operate several of them on one machine.

## Compared with separate terminal windows

Separate tmux shells are simple, but they do not automatically record profile/provider identity, managed writer ownership, capacity admission, workflow parentage, durable child results, or operator gates. ThreadCells keeps the native terminals while adding those operational records.

## Compared with a hosted agent platform

ThreadCells is self-hosted and loopback-first. Repositories, terminals, and the coordination database remain on the operator's host. In return, the operator owns installation, provider authentication, backup, patching, resource sizing, and remote-access protection.

## Compared with container or security sandboxes

ThreadCells is not one. Managed worktrees and authority policies reduce coordination mistakes but do not isolate native provider processes from the operating-system account.

## Compared with autonomous software factories

ThreadCells emphasizes bounded delegation, inspectable terminals, explicit results, owner decisions, and evidence-backed completion. It does not promise that agents can ship arbitrary software without review.

ThreadCells is an independent downstream of AWS Labs CLI Agent Orchestrator and retains compatible `cao` internals where required. It is not a drop-in replacement for unrelated agent products such as OpenHands or Hermes. Choose it for local native-CLI operations and durable supervisor/worker control rather than for hosted multi-tenancy or broad platform abstraction.
