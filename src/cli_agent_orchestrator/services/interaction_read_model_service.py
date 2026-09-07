"""Bounded operator read model for durable work and interaction history.

The projection deliberately keeps workflow, queue/admission, runtime, and
result/delivery fields separate.  Rows are grouped only by durable identities:
workflow turn, workflow-without-a-turn, child assignment, standalone Inbox
message, recovery takeover, or runtime authority.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import text

from cli_agent_orchestrator.clients import database

DEFAULT_CURRENT_PAGE_SIZE = 25
DEFAULT_HISTORY_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")


def _stable_session_id_sql(alias: str = "t") -> str:
    return f"COALESCE({alias}.session_id, 'legacy:' || {alias}.tmux_session)"


def _scope_sql(
    session_ids: Iterable[str], terminal_id: Optional[str] = None
) -> tuple[str, Dict[str, Any]]:
    normalized = list(dict.fromkeys(str(value) for value in session_ids if value))
    if not normalized:
        return " AND 1 = 0", {}
    parameters: Dict[str, Any] = {}
    placeholders = []
    for index, value in enumerate(normalized):
        key = f"interaction_session_{index}"
        parameters[key] = value
        placeholders.append(f":{key}")
    clause = f" AND {_stable_session_id_sql()} IN ({', '.join(placeholders)})"
    if terminal_id:
        parameters["interaction_terminal_id"] = terminal_id
        clause += " AND t.id = :interaction_terminal_id"
    return clause, parameters


def _projection_cte(
    session_ids: Iterable[str],
    terminal_id: Optional[str] = None,
    *,
    current_only: bool = False,
    history_candidate_filter: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    scope, parameters = _scope_sql(session_ids, terminal_id)
    current_effect_scope = (
        " AND effect.state IN ('claimed', 'indeterminate')" if current_only else ""
    )

    def item_source(name: str) -> str:
        if current_only:
            return f"SELECT * FROM {name} WHERE is_current = 1"
        if history_candidate_filter is not None:
            return (
                f"SELECT * FROM (SELECT * FROM {name} "
                f"WHERE is_current = 0{history_candidate_filter} "
                "ORDER BY created_at DESC, interaction_id DESC LIMIT :page_limit)"
            )
        return f"SELECT * FROM {name}"

    # Keep every UNION arm explicit.  Besides making the product DTO stable,
    # this prevents a raw-table/event contract from leaking into React.
    return (
        """
WITH interaction_terminals AS MATERIALIZED (
    SELECT t.id AS terminal_id,
           COALESCE(t.session_id, 'legacy:' || t.tmux_session) AS session_id,
           t.runtime_lifecycle, t.runtime_operation_kind,
           t.writer_authority_generation, t.last_active
    FROM terminals t
    WHERE NOT EXISTS (
        SELECT 1 FROM session_deletion_receipts receipt
        WHERE receipt.session_id = COALESCE(t.session_id, 'legacy:' || t.tmux_session)
    )
"""
        + scope
        + """
), provider_capacity AS (
    SELECT (SELECT COUNT(*) FROM provider_execution_leases) AS active_count,
           COALESCE((SELECT max_provider_executions FROM capacity_settings WHERE id = 1),
                    2147483647) AS execution_limit
), effect_ranked AS (
    SELECT effect.workflow_id, effect.workflow_turn_id, effect.effect_kind, effect.state,
           ROW_NUMBER() OVER (
             PARTITION BY effect.workflow_id, effect.workflow_turn_id
             ORDER BY CASE effect.state WHEN 'indeterminate' THEN 0 WHEN 'claimed' THEN 1
                                        ELSE 2 END,
                      effect.id DESC
           ) AS effect_rank,
           SUM(CASE WHEN effect.state IN ('claimed', 'indeterminate') THEN 1 ELSE 0 END)
             OVER (PARTITION BY effect.workflow_id, effect.workflow_turn_id)
             AS unresolved_effect_count
    FROM workflow_effects effect
    JOIN workflows ew ON ew.id = effect.workflow_id
    JOIN interaction_terminals et ON et.terminal_id = ew.root_terminal_id
    WHERE 1 = 1
"""
        + current_effect_scope
        + """
), workflow_turn_facts AS (
    SELECT wt.id, wt.workflow_id, wt.kind, wt.payload, wt.state, wt.queue_reason,
           wt.provider_reconnect_requested_at, wt.provider_outcome_code,
           wt.provider_outcome_detail, wt.inbox_message_id, wt.superseded_by_turn_id,
           wt.superseded_at, wt.created_at, wt.updated_at,
           w.root_terminal_id, w.status AS workflow_status,
           w.terminal_reason AS workflow_reason, terminals.session_id,
           inbox.sender_id AS inbox_sender_id, inbox.kind AS inbox_kind,
           inbox.status AS inbox_status,
           CASE WHEN receipt.id IS NOT NULL THEN 1 ELSE 0 END AS receipt_exists,
           CASE WHEN wt.state IN ('queued', 'claimed')
                  OR (wt.state = 'sent' AND receipt.id IS NULL)
                THEN 1 ELSE 0 END AS transport_unresolved,
           CASE WHEN w.active_turn_id = wt.id THEN 1 ELSE 0 END AS is_active_turn,
           CASE WHEN lease.workflow_turn_id IS NOT NULL THEN 1 ELSE 0 END
             AS provider_execution_active,
           ranked_effect.effect_kind, ranked_effect.state AS effect_state,
           COALESCE(ranked_effect.unresolved_effect_count, 0) AS unresolved_effect_count,
           0 AS workflow_turn_count, 0 AS superseded_turn_count
    FROM workflow_turns wt
    JOIN workflows w ON w.id = wt.workflow_id
    JOIN interaction_terminals terminals ON terminals.terminal_id = w.root_terminal_id
    LEFT JOIN inbox ON inbox.id = wt.inbox_message_id
    LEFT JOIN workflow_turn_receipts receipt
      ON receipt.workflow_turn_id = wt.id AND receipt.receiver_terminal_id = w.root_terminal_id
    LEFT JOIN provider_execution_leases lease ON lease.workflow_turn_id = wt.id
    LEFT JOIN effect_ranked ranked_effect
      ON ranked_effect.workflow_id = wt.workflow_id
     AND ranked_effect.workflow_turn_id = wt.id AND ranked_effect.effect_rank = 1
), workflow_turn_item_rows AS NOT MATERIALIZED (
    SELECT 'workflow-turn:' || printf('%020d', facts.id) AS interaction_id,
           facts.session_id, 'workflow_turn' AS interaction_type, facts.kind AS task_type,
           CASE
             WHEN facts.inbox_kind = 'delegation_result_notice' THEN 'child_result'
             WHEN facts.inbox_sender_id IS NOT NULL THEN 'inbox'
             WHEN facts.kind = 'external_input' THEN 'operator'
             WHEN facts.kind LIKE '%reconnect%' OR facts.kind = 'execution_resume' THEN 'recovery'
             ELSE 'system'
           END AS source_kind,
           COALESCE(facts.inbox_sender_id, facts.root_terminal_id) AS source_terminal_id,
           facts.root_terminal_id AS target_terminal_id,
           SUBSTR(COALESCE(facts.payload, ''), 1, 1200) AS input_preview,
           facts.created_at, facts.updated_at,
           CASE
             WHEN facts.workflow_status IN ('open', 'owner_gate')
              AND facts.transport_unresolved = 1
              AND facts.superseded_by_turn_id IS NULL THEN 1
             WHEN facts.workflow_status IN ('open', 'owner_gate')
              AND facts.superseded_by_turn_id IS NULL
              AND (facts.inbox_status = 'pending'
                   OR facts.unresolved_effect_count > 0
                   OR facts.provider_execution_active = 1
                   OR (facts.is_active_turn = 1
                       AND facts.provider_reconnect_requested_at IS NOT NULL)) THEN 1
             ELSE 0
           END AS is_current,
           CASE
             WHEN facts.provider_execution_active = 1 THEN 'executing'
             WHEN facts.workflow_status = 'owner_gate'
              AND facts.transport_unresolved = 1
               THEN 'owner_gate'
             ELSE facts.state
           END AS queue_state,
           CASE
             WHEN facts.provider_execution_active = 1 THEN 'current_provider_turn'
             WHEN facts.inbox_status = 'pending' THEN 'delivery'
             WHEN facts.effect_state = 'indeterminate' THEN 'indeterminate_effect'
             WHEN facts.effect_state = 'claimed' THEN 'claimed_effect'
             WHEN facts.workflow_status = 'owner_gate'
              AND facts.transport_unresolved = 1
               THEN 'owner_gate'
             WHEN facts.is_active_turn = 1
              AND facts.provider_reconnect_requested_at IS NOT NULL
               THEN 'reconnect'
             WHEN facts.queue_reason LIKE '%RECONNECT%'
              AND facts.transport_unresolved = 1
               THEN 'reconnect'
             WHEN facts.queue_reason = 'RESOURCE_HEALTH_REJECTED'
              AND facts.transport_unresolved = 1
               THEN 'resource_health'
             WHEN facts.queue_reason IN ('TERMINAL_RUNTIME_OPERATION_BUSY',
                                         'TERMINAL_RUNTIME_RECONNECT_PENDING')
              AND facts.transport_unresolved = 1
               THEN 'writer_recovery_authority'
             WHEN facts.state = 'sent' AND facts.receipt_exists = 0 THEN 'admission'
             WHEN facts.state = 'queued'
              AND capacity.active_count >= capacity.execution_limit THEN 'provider_capacity'
             WHEN facts.state IN ('queued', 'claimed') THEN 'workflow_continuation'
             ELSE NULL
           END AS wait_reason,
           CASE WHEN facts.inbox_status = 'pending' OR facts.transport_unresolved = 1
                THEN 1 ELSE 0 END AS admission_pending,
           facts.workflow_id, facts.id AS workflow_turn_id, facts.workflow_status,
           facts.workflow_reason, facts.state AS turn_state, facts.kind AS turn_kind,
           facts.provider_outcome_code, facts.provider_outcome_detail,
           facts.effect_kind, facts.effect_state, facts.workflow_turn_count,
           facts.superseded_turn_count, NULL AS assignment_id, NULL AS result_id,
           NULL AS result_status, NULL AS result_summary, 0 AS result_available,
           NULL AS delivery_status, 0 AS delivery_pending,
           CASE
             WHEN facts.superseded_by_turn_id IS NOT NULL THEN 'superseded'
             WHEN facts.workflow_status = 'cancelled' THEN 'cancelled'
             WHEN facts.workflow_status = 'terminal' THEN 'completed'
             WHEN facts.state = 'sent' AND facts.receipt_exists = 1
              AND facts.inbox_status IS NOT 'pending'
              AND facts.unresolved_effect_count = 0
              AND facts.provider_execution_active = 0
              AND NOT (facts.is_active_turn = 1
                       AND facts.provider_reconnect_requested_at IS NOT NULL)
               THEN 'processed'
             ELSE NULL
           END AS final_disposition,
           CAST(facts.id AS TEXT) AS diagnostic_id
    FROM workflow_turn_facts facts CROSS JOIN provider_capacity capacity
), workflow_shell_item_rows AS NOT MATERIALIZED (
    SELECT 'workflow:' || printf('%020d', w.id) AS interaction_id,
           terminals.session_id, 'workflow' AS interaction_type, 'workflow' AS task_type,
           'system' AS source_kind, w.root_terminal_id AS source_terminal_id,
           w.root_terminal_id AS target_terminal_id, '' AS input_preview,
           w.created_at, w.updated_at,
           CASE WHEN w.status IN ('open', 'owner_gate') THEN 1 ELSE 0 END AS is_current,
           w.status AS queue_state,
           CASE WHEN w.status = 'owner_gate' THEN 'owner_gate'
                WHEN w.status = 'open' THEN 'workflow_continuation' ELSE NULL END AS wait_reason,
           0 AS admission_pending, w.id AS workflow_id, NULL AS workflow_turn_id,
           w.status AS workflow_status, w.terminal_reason AS workflow_reason,
           NULL AS turn_state, NULL AS turn_kind, NULL AS provider_outcome_code,
           NULL AS provider_outcome_detail, NULL AS effect_kind, NULL AS effect_state,
           0 AS workflow_turn_count, 0 AS superseded_turn_count,
           NULL AS assignment_id, NULL AS result_id, NULL AS result_status,
           NULL AS result_summary, 0 AS result_available, NULL AS delivery_status,
           0 AS delivery_pending,
           CASE WHEN w.status = 'cancelled' THEN 'cancelled'
                WHEN w.status = 'terminal' THEN 'completed' ELSE NULL END AS final_disposition,
           CAST(w.id AS TEXT) AS diagnostic_id
    FROM workflows w
    JOIN interaction_terminals terminals ON terminals.terminal_id = w.root_terminal_id
    WHERE NOT EXISTS (SELECT 1 FROM workflow_turns wt WHERE wt.workflow_id = w.id)
       OR (w.status IN ('open', 'owner_gate') AND NOT EXISTS (
         SELECT 1 FROM workflow_turns wt
         LEFT JOIN inbox linked_inbox ON linked_inbox.id = wt.inbox_message_id
         LEFT JOIN workflow_turn_receipts linked_receipt
           ON linked_receipt.workflow_turn_id = wt.id
          AND linked_receipt.receiver_terminal_id = w.root_terminal_id
         LEFT JOIN provider_execution_leases linked_lease
           ON linked_lease.workflow_turn_id = wt.id
         WHERE wt.workflow_id = w.id AND (
           ((wt.state IN ('queued', 'claimed')
             OR (wt.state = 'sent' AND linked_receipt.id IS NULL))
             AND wt.superseded_by_turn_id IS NULL)
           OR linked_inbox.status = 'pending'
           OR linked_lease.workflow_turn_id IS NOT NULL
           OR (w.active_turn_id = wt.id
               AND wt.provider_reconnect_requested_at IS NOT NULL)
           OR EXISTS (
             SELECT 1 FROM workflow_effects linked_effect
             WHERE linked_effect.workflow_id = w.id
               AND linked_effect.workflow_turn_id = wt.id
               AND linked_effect.state IN ('claimed', 'indeterminate')
           )
         )
       ))
), unresolved_authority_item_rows AS NOT MATERIALIZED (
    SELECT 'effect:' || printf('%020d', effect.id) AS interaction_id,
           terminals.session_id, 'effect' AS interaction_type,
           effect.effect_kind AS task_type, 'system' AS source_kind,
           w.root_terminal_id AS source_terminal_id,
           w.root_terminal_id AS target_terminal_id, '' AS input_preview,
           effect.created_at, effect.updated_at, 1 AS is_current,
           effect.state AS queue_state,
           CASE WHEN effect.state = 'indeterminate' THEN 'indeterminate_effect'
                ELSE 'claimed_effect' END AS wait_reason,
           1 AS admission_pending, effect.workflow_id,
           effect.workflow_turn_id, w.status AS workflow_status,
           w.terminal_reason AS workflow_reason, wt.state AS turn_state,
           wt.kind AS turn_kind, wt.provider_outcome_code,
           wt.provider_outcome_detail, effect.effect_kind, effect.state AS effect_state,
           0 AS workflow_turn_count, 0 AS superseded_turn_count,
           NULL AS assignment_id, NULL AS result_id, NULL AS result_status,
           NULL AS result_summary, 0 AS result_available, NULL AS delivery_status,
           0 AS delivery_pending, NULL AS final_disposition,
           CAST(effect.id AS TEXT) AS diagnostic_id
    FROM workflow_effects effect
    JOIN workflows w ON w.id = effect.workflow_id
    JOIN interaction_terminals terminals ON terminals.terminal_id = w.root_terminal_id
    JOIN workflow_turns wt ON wt.id = effect.workflow_turn_id
    WHERE effect.state IN ('claimed', 'indeterminate')
      AND (w.status IN ('terminal', 'cancelled')
           OR wt.superseded_by_turn_id IS NOT NULL)
), provider_authority_item_rows AS NOT MATERIALIZED (
    SELECT 'provider:' || lease.terminal_id || ':' || printf('%020d', wt.id)
             AS interaction_id,
           terminals.session_id, 'runtime_authority' AS interaction_type,
           'provider_execution' AS task_type, 'system' AS source_kind,
           lease.terminal_id AS source_terminal_id,
           lease.terminal_id AS target_terminal_id, '' AS input_preview,
           lease.acquired_at AS created_at, lease.acquired_at AS updated_at,
           1 AS is_current, 'executing' AS queue_state,
           'current_provider_turn' AS wait_reason, 0 AS admission_pending,
           wt.workflow_id, wt.id AS workflow_turn_id, w.status AS workflow_status,
           w.terminal_reason AS workflow_reason, wt.state AS turn_state,
           wt.kind AS turn_kind, wt.provider_outcome_code,
           wt.provider_outcome_detail, NULL AS effect_kind, NULL AS effect_state,
           0 AS workflow_turn_count, 0 AS superseded_turn_count,
           NULL AS assignment_id, NULL AS result_id, NULL AS result_status,
           NULL AS result_summary, 0 AS result_available, NULL AS delivery_status,
           0 AS delivery_pending, NULL AS final_disposition,
           CAST(wt.id AS TEXT) AS diagnostic_id
    FROM provider_execution_leases lease
    JOIN workflow_turns wt ON wt.id = lease.workflow_turn_id
    JOIN workflows w ON w.id = wt.workflow_id
    JOIN interaction_terminals terminals ON terminals.terminal_id = lease.terminal_id
    WHERE w.status IN ('terminal', 'cancelled')
       OR wt.superseded_by_turn_id IS NOT NULL
), writer_authority_item_rows AS NOT MATERIALIZED (
    SELECT 'writer:' || terminals.terminal_id AS interaction_id,
           terminals.session_id, 'runtime_authority' AS interaction_type,
           'writer_authority' AS task_type, 'system' AS source_kind,
           terminals.terminal_id AS source_terminal_id,
           terminals.terminal_id AS target_terminal_id, '' AS input_preview,
           lease.created_at, lease.created_at AS updated_at, 1 AS is_current,
           'writer_authority' AS queue_state,
           'writer_recovery_authority' AS wait_reason, 0 AS admission_pending,
           NULL AS workflow_id, NULL AS workflow_turn_id,
           NULL AS workflow_status, NULL AS workflow_reason,
           NULL AS turn_state, NULL AS turn_kind, NULL AS provider_outcome_code,
           NULL AS provider_outcome_detail, NULL AS effect_kind,
           NULL AS effect_state, 0 AS workflow_turn_count,
           0 AS superseded_turn_count, NULL AS assignment_id,
           NULL AS result_id, NULL AS result_status, NULL AS result_summary,
           0 AS result_available, NULL AS delivery_status,
           0 AS delivery_pending, NULL AS final_disposition,
           lease.canonical_worktree AS diagnostic_id
    FROM interaction_terminals terminals
    JOIN worktree_writer_leases lease ON lease.terminal_id = terminals.terminal_id
    WHERE terminals.runtime_lifecycle = 'exited'
), assignment_sessions AS (
    SELECT DISTINCT ca.id AS assignment_id, terminals.session_id
    FROM child_assignments ca
    JOIN interaction_terminals terminals
      ON terminals.terminal_id IN (ca.parent_terminal_id, ca.child_terminal_id)
), assignment_item_rows AS NOT MATERIALIZED (
    SELECT 'assignment:' || printf('%020d', ca.id) AS interaction_id,
           scoped.session_id, 'delegation' AS interaction_type,
           COALESCE(result.delegation_kind,
             CASE WHEN ca.status LIKE 'handoff_%' THEN 'handoff' ELSE 'assign' END) AS task_type,
           'agent' AS source_kind, ca.parent_terminal_id AS source_terminal_id,
           ca.child_terminal_id AS target_terminal_id,
           SUBSTR(COALESCE(child_turn.payload, ''), 1, 1200) AS input_preview,
           ca.created_at, ca.updated_at,
           CASE WHEN ca.review_superseded_at IS NULL AND (
             ca.status IN (
               'awaiting_result', 'result_queued', 'result_delivered', 'result_failed',
               'handoff_awaiting_result', 'handoff_recovery_awaiting_result',
               'handoff_direct_result_claimed', 'handoff_result_queued',
               'handoff_result_delivered', 'handoff_result_failed'
             ) OR result_notice.status = 'pending'
           ) THEN 1 ELSE 0 END AS is_current,
           ca.status AS queue_state,
           CASE
             WHEN ca.review_superseded_at IS NOT NULL THEN NULL
             WHEN ca.status IN ('awaiting_result', 'handoff_awaiting_result') THEN 'child_result'
             WHEN ca.status = 'handoff_recovery_awaiting_result' THEN 'reconnect'
             WHEN result_notice.status = 'pending' THEN 'delivery'
             WHEN ca.status IN ('result_queued', 'handoff_result_queued',
                                'result_failed', 'handoff_result_failed',
                                'handoff_direct_result_claimed') THEN 'delivery'
             WHEN ca.status IN ('result_delivered', 'handoff_result_delivered')
               THEN 'acknowledgement'
             ELSE NULL
           END AS wait_reason,
           0 AS admission_pending, ca.request_workflow_id AS workflow_id,
           ca.request_workflow_turn_id AS workflow_turn_id,
           request_workflow.status AS workflow_status,
           request_workflow.terminal_reason AS workflow_reason,
           NULL AS turn_state, NULL AS turn_kind, NULL AS provider_outcome_code,
           NULL AS provider_outcome_detail, NULL AS effect_kind, NULL AS effect_state,
           0 AS workflow_turn_count, 0 AS superseded_turn_count, ca.id AS assignment_id,
           result.id AS result_id, result.status AS result_status,
           CASE WHEN result.document_json IS NOT NULL AND json_valid(result.document_json)
                THEN SUBSTR(json_extract(result.document_json, '$.summary'), 1, 500)
                ELSE NULL END AS result_summary,
           CASE WHEN result.document_json IS NOT NULL AND result.content_purged_at IS NULL
                THEN 1 ELSE 0 END AS result_available,
           ca.status AS delivery_status,
           CASE WHEN ca.review_superseded_at IS NOT NULL THEN 0
                WHEN result_notice.status = 'pending' OR ca.status IN (
                  'result_queued', 'result_delivered', 'result_failed',
                  'handoff_direct_result_claimed', 'handoff_result_queued',
                  'handoff_result_delivered', 'handoff_result_failed'
                ) THEN 1 ELSE 0 END AS delivery_pending,
           CASE
             WHEN ca.review_superseded_at IS NOT NULL OR ca.status = 'result_superseded'
               THEN 'superseded'
             WHEN ca.status IN ('result_acknowledged', 'handoff_result_acknowledged')
               THEN 'acknowledged'
             WHEN ca.status = 'cancelled' THEN 'cancelled'
             ELSE NULL
           END AS final_disposition,
           COALESCE(ca.attempt_id, CAST(ca.id AS TEXT)) AS diagnostic_id
    FROM assignment_sessions scoped
    JOIN child_assignments ca ON ca.id = scoped.assignment_id
    LEFT JOIN delegation_results result ON result.child_assignment_id = ca.id
    LEFT JOIN inbox result_notice ON result_notice.id = ca.result_message_id
    LEFT JOIN workflows request_workflow ON request_workflow.id = ca.request_workflow_id
    LEFT JOIN workflow_turns child_turn ON child_turn.id = ca.child_workflow_turn_id
), inbox_sessions AS (
    SELECT DISTINCT inbox.id AS inbox_id, terminals.session_id
    FROM inbox
    JOIN interaction_terminals terminals
      ON terminals.terminal_id IN (inbox.sender_id, inbox.receiver_id)
    WHERE NOT EXISTS (
        SELECT 1 FROM delegation_results result WHERE result.id = inbox.result_id
      )
      AND (
        NOT EXISTS (SELECT 1 FROM workflow_turns wt WHERE wt.inbox_message_id = inbox.id)
        OR (
          inbox.status = 'pending' AND EXISTS (
            SELECT 1 FROM workflow_turns wt
            JOIN workflows w ON w.id = wt.workflow_id
            WHERE wt.inbox_message_id = inbox.id
              AND (w.status IN ('terminal', 'cancelled')
                   OR wt.superseded_by_turn_id IS NOT NULL)
          )
        )
      )
), inbox_item_rows AS NOT MATERIALIZED (
    SELECT 'inbox:' || printf('%020d', inbox.id) AS interaction_id,
           scoped.session_id, 'inbox' AS interaction_type, inbox.kind AS task_type,
           CASE WHEN inbox.sender_id = 'ui' THEN 'operator' ELSE 'agent' END AS source_kind,
           inbox.sender_id AS source_terminal_id, inbox.receiver_id AS target_terminal_id,
           SUBSTR(inbox.message, 1, 1200) AS input_preview,
           inbox.created_at, inbox.created_at AS updated_at,
           CASE WHEN inbox.status = 'pending' THEN 1 ELSE 0 END AS is_current,
           inbox.status AS queue_state,
           CASE WHEN inbox.status = 'pending' THEN 'delivery' ELSE NULL END AS wait_reason,
           CASE WHEN inbox.status = 'pending' THEN 1 ELSE 0 END AS admission_pending,
           NULL AS workflow_id, NULL AS workflow_turn_id, NULL AS workflow_status,
           NULL AS workflow_reason, NULL AS turn_state, NULL AS turn_kind,
           NULL AS provider_outcome_code, NULL AS provider_outcome_detail,
           NULL AS effect_kind, NULL AS effect_state, 0 AS workflow_turn_count,
           0 AS superseded_turn_count, NULL AS assignment_id, NULL AS result_id,
           NULL AS result_status, NULL AS result_summary, 0 AS result_available,
           inbox.status AS delivery_status,
           CASE WHEN inbox.status = 'pending' THEN 1 ELSE 0 END AS delivery_pending,
           CASE WHEN inbox.status IN ('delivered', 'failed', 'superseded')
                THEN inbox.status ELSE NULL END AS final_disposition,
           CAST(inbox.id AS TEXT) AS diagnostic_id
    FROM inbox_sessions scoped JOIN inbox ON inbox.id = scoped.inbox_id
), recovery_sessions AS (
    SELECT DISTINCT recovery.id AS recovery_id, terminals.session_id
    FROM recovery_takeovers recovery
    JOIN interaction_terminals terminals
      ON terminals.terminal_id IN (recovery.old_terminal_id, recovery.new_terminal_id)
), recovery_item_rows AS NOT MATERIALIZED (
    SELECT 'recovery:' || recovery.id || ':' || scoped.session_id AS interaction_id,
           scoped.session_id, 'recovery' AS interaction_type,
           'recovery_takeover' AS task_type, 'system' AS source_kind,
           recovery.old_terminal_id AS source_terminal_id,
           recovery.new_terminal_id AS target_terminal_id,
           '' AS input_preview, recovery.created_at, recovery.updated_at,
           CASE WHEN recovery.state IN ('claimed', 'fenced', 'dispatching', 'admitted')
                THEN 1 ELSE 0 END AS is_current,
           recovery.state AS queue_state,
           CASE WHEN recovery.state IN ('claimed', 'fenced', 'dispatching', 'admitted')
                THEN 'writer_recovery_authority' ELSE NULL END AS wait_reason,
           CASE WHEN recovery.state IN ('claimed', 'fenced', 'dispatching')
                THEN 1 ELSE 0 END AS admission_pending,
           NULL AS workflow_id, NULL AS workflow_turn_id, NULL AS workflow_status,
           recovery.failure_reason AS workflow_reason, NULL AS turn_state,
           NULL AS turn_kind, NULL AS provider_outcome_code,
           NULL AS provider_outcome_detail, NULL AS effect_kind, NULL AS effect_state,
           0 AS workflow_turn_count, 0 AS superseded_turn_count,
           NULL AS assignment_id, NULL AS result_id, NULL AS result_status,
           NULL AS result_summary, 0 AS result_available, NULL AS delivery_status,
           0 AS delivery_pending,
           CASE WHEN recovery.state IN ('completed', 'failed') THEN recovery.state
                ELSE NULL END AS final_disposition,
           recovery.id AS diagnostic_id
    FROM recovery_sessions scoped
    JOIN recovery_takeovers recovery ON recovery.id = scoped.recovery_id
), runtime_item_rows AS NOT MATERIALIZED (
    SELECT 'runtime:' || terminals.terminal_id AS interaction_id, terminals.session_id,
           'runtime_authority' AS interaction_type,
           COALESCE(terminals.runtime_operation_kind, 'runtime_recovery') AS task_type,
           'system' AS source_kind, terminals.terminal_id AS source_terminal_id,
           terminals.terminal_id AS target_terminal_id, '' AS input_preview,
           terminals.last_active AS created_at, terminals.last_active AS updated_at,
           1 AS is_current,
           CASE WHEN terminals.runtime_operation_kind IS NOT NULL
                THEN terminals.runtime_operation_kind ELSE terminals.runtime_lifecycle END
             AS queue_state,
           'writer_recovery_authority' AS wait_reason, 1 AS admission_pending,
           NULL AS workflow_id, NULL AS workflow_turn_id, NULL AS workflow_status,
           NULL AS workflow_reason, NULL AS turn_state, NULL AS turn_kind,
           NULL AS provider_outcome_code, NULL AS provider_outcome_detail,
           NULL AS effect_kind, NULL AS effect_state, 0 AS workflow_turn_count,
           0 AS superseded_turn_count, NULL AS assignment_id, NULL AS result_id,
           NULL AS result_status, NULL AS result_summary, 0 AS result_available,
           NULL AS delivery_status, 0 AS delivery_pending, NULL AS final_disposition,
           terminals.terminal_id AS diagnostic_id
    FROM interaction_terminals terminals
    WHERE terminals.runtime_lifecycle = 'recovery_required'
       OR terminals.runtime_operation_kind IS NOT NULL
), interaction_items AS MATERIALIZED (
    """
        + "\n    UNION ALL ".join(
            item_source(name)
            for name in (
                "workflow_turn_item_rows",
                "workflow_shell_item_rows",
                "unresolved_authority_item_rows",
                "provider_authority_item_rows",
                "writer_authority_item_rows",
                "assignment_item_rows",
                "inbox_item_rows",
                "recovery_item_rows",
                "runtime_item_rows",
            )
        )
        + """
)
""",
        parameters,
    )


def _encode_cursor(payload: Dict[str, str]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(
    cursor: str, *, mode: str, session_id: str, terminal_id: Optional[str]
) -> Dict[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    expected = {"v": "1", "mode": mode, "session_id": session_id, "terminal_id": terminal_id or ""}
    if not isinstance(payload, dict) or any(
        payload.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("cursor does not match this interaction view")
    if not isinstance(payload.get("snapshot"), str) or not isinstance(payload.get("created"), str):
        raise ValueError("cursor is invalid")
    if not isinstance(payload.get("id"), str):
        raise ValueError("cursor is invalid")
    return {str(key): str(value) for key, value in payload.items()}


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value).replace(" ", "T", 1)


def _interaction_dto(row: Dict[str, Any]) -> Dict[str, Any]:
    result_id = row.get("result_id")
    delivery_status = row.get("delivery_status")
    final_disposition = row.get("final_disposition")
    return {
        "id": row["interaction_id"],
        "interaction_type": row["interaction_type"],
        "task_type": row["task_type"],
        "source": {
            "kind": row["source_kind"],
            "terminal_id": row.get("source_terminal_id"),
            "target_terminal_id": row.get("target_terminal_id"),
        },
        "input_preview": row.get("input_preview") or None,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "current": bool(row.get("is_current")),
        "queue": {
            "state": row.get("queue_state"),
            "wait_reason": row.get("wait_reason"),
            "admission_pending": bool(row.get("admission_pending")),
        },
        "workflow": {
            "id": row.get("workflow_id"),
            "turn_id": row.get("workflow_turn_id"),
            "status": row.get("workflow_status"),
            "reason": row.get("workflow_reason"),
            "turn_state": row.get("turn_state"),
            "turn_kind": row.get("turn_kind"),
            "provider_outcome_code": row.get("provider_outcome_code"),
            "provider_outcome_detail": row.get("provider_outcome_detail"),
            "effect_kind": row.get("effect_kind"),
            "effect_state": row.get("effect_state"),
            "turn_count": int(row.get("workflow_turn_count") or 0),
            "superseded_turn_count": int(row.get("superseded_turn_count") or 0),
        },
        "result": {
            "id": result_id,
            "status": row.get("result_status"),
            "summary": row.get("result_summary"),
            "available": bool(row.get("result_available")),
        },
        "delivery": {
            "status": delivery_status,
            "pending": bool(row.get("delivery_pending")),
            "acknowledged": bool(
                delivery_status and str(delivery_status).endswith("result_acknowledged")
            ),
        },
        "final_disposition": final_disposition,
        "diagnostics": {
            "interaction_id": row["interaction_id"],
            "durable_id": row.get("diagnostic_id"),
            "assignment_id": row.get("assignment_id"),
        },
    }


def list_interactions(
    session_id: str,
    *,
    mode: str = "current",
    terminal_id: Optional[str] = None,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Return one cursor page from the durable interaction projection."""
    if mode not in {"current", "history"}:
        raise ValueError("mode must be current or history")
    if not session_id:
        raise ValueError("session_id is required")
    resolved_limit = limit or (
        DEFAULT_CURRENT_PAGE_SIZE if mode == "current" else DEFAULT_HISTORY_PAGE_SIZE
    )
    _validate_limit(resolved_limit)
    database._ensure_terminal_ui_projection_schema()
    # SQLAlchemy's SQLite DateTime adapter persists the canonical value with a
    # space separator.  Keep the comparison value in that same representation:
    # an ISO ``T`` sorts after every time on the same date and would otherwise
    # admit rows created later than this cursor snapshot.
    snapshot = (
        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    )
    after = ""
    cursor_parameters: Dict[str, Any] = {}
    direction = "ASC" if mode == "current" else "DESC"
    comparison = ">" if mode == "current" else "<"
    if cursor:
        decoded = _decode_cursor(cursor, mode=mode, session_id=session_id, terminal_id=terminal_id)
        snapshot = decoded["snapshot"]
        cursor_parameters.update({"cursor_created": decoded["created"], "cursor_id": decoded["id"]})
        after = (
            f" AND (created_at {comparison} :cursor_created "
            f"OR (created_at = :cursor_created AND interaction_id {comparison} :cursor_id))"
        )
    history_candidate_filter = None
    if mode == "history":
        history_candidate_filter = (
            " AND created_at <= :snapshot "
            "AND COALESCE(updated_at, created_at) <= :snapshot" + after
        )
    cte, parameters = _projection_cte(
        [session_id],
        terminal_id,
        current_only=mode == "current",
        history_candidate_filter=history_candidate_filter,
    )
    parameters.update(cursor_parameters)
    parameters.update(
        {
            "is_current": 1 if mode == "current" else 0,
            "snapshot": snapshot,
            "page_limit": resolved_limit + 1,
        }
    )
    base_page = f""", base_page AS MATERIALIZED (
    SELECT * FROM interaction_items
    WHERE is_current = :is_current AND created_at <= :snapshot
      AND COALESCE(updated_at, created_at) <= :snapshot
), filtered AS MATERIALIZED (
    SELECT * FROM base_page WHERE 1 = 1{after}
), page AS (
    SELECT * FROM filtered
    ORDER BY created_at {direction}, interaction_id {direction}
    LIMIT :page_limit
)
"""
    if mode == "current":
        sql = cte + base_page + f""", meta AS (SELECT COUNT(*) AS total_count FROM base_page)
SELECT page.*, meta.total_count
FROM meta LEFT JOIN page ON 1 = 1
ORDER BY page.created_at {direction}, page.interaction_id {direction}
"""
    else:
        # A full History COUNT would defeat cursor pagination by scanning all
        # retained interactions on every page.  History therefore exposes
        # has-more through the limit+1 cursor and deliberately leaves total
        # unknown. Current Queue retains its exact total because that value is
        # the canonical session indicator.
        sql = cte + base_page + f"""
SELECT page.*, NULL AS total_count FROM page
ORDER BY page.created_at {direction}, page.interaction_id {direction}
"""
    with database.SessionLocal() as db:
        rows = db.execute(text(sql), parameters).mappings().all()
    total = int(rows[0]["total_count"] or 0) if mode == "current" else None
    fetched_rows = [dict(row) for row in rows if row["interaction_id"] is not None]
    has_more = len(fetched_rows) > resolved_limit
    page_rows = fetched_rows[:resolved_limit]
    items = [_interaction_dto(row) for row in page_rows]
    next_cursor = None
    if has_more:
        last = page_rows[-1]
        next_cursor = _encode_cursor(
            {
                "v": "1",
                "mode": mode,
                "session_id": session_id,
                "terminal_id": terminal_id or "",
                "snapshot": snapshot,
                "created": str(last["created_at"]),
                "id": str(last["interaction_id"]),
            }
        )
    return {
        "items": items,
        "total": total,
        "limit": resolved_limit,
        "next_cursor": next_cursor,
        "snapshot_at": snapshot.replace(" ", "T", 1),
    }


def list_session_current_queue_counts(session_ids: Iterable[str]) -> Dict[str, int]:
    """Count the exact same current interaction rows used by the drawer."""
    normalized = list(dict.fromkeys(str(value) for value in session_ids if value))
    if not normalized:
        return {}
    if len(normalized) > 100:
        raise ValueError("at most 100 session IDs may be counted")
    database._ensure_terminal_ui_projection_schema()
    cte, parameters = _projection_cte(normalized, current_only=True)
    sql = cte + """
SELECT session_id, COUNT(*) AS current_queue_count
FROM interaction_items WHERE is_current = 1 GROUP BY session_id
"""
    with database.SessionLocal() as db:
        rows = db.execute(text(sql), parameters).mappings().all()
    counts = {session_id: 0 for session_id in normalized}
    counts.update({str(row["session_id"]): int(row["current_queue_count"]) for row in rows})
    return counts
