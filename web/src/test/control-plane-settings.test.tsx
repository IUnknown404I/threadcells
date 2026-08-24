import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, CaoApiError } from '../api'
import { BUILD_IDENTITY } from '../buildIdentity'
import { ControlPlaneSettings } from '../components/ControlPlaneSettings'
import { OperatorAccessCard } from '../components/OperatorAccess'

describe('Control-plane settings routes', () => {
  afterEach(() => vi.restoreAllMocks())

  const operatorStatus = (authenticated = false) => ({
    configured: true,
    authenticated,
    expires_in_seconds: authenticated ? 240 : 0,
    session_ttl_seconds: 300,
    verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE',
  })

  const housekeepingSettings = () => ({
    schema_version: 1 as const,
    policy: {
      logs: { enabled: true, compress_after_minutes: 1440, retain_minutes: 10080 },
      attachments: { enabled: true, retain_minutes: 10080 },
      ephemeral: { enabled: true },
      browser_cache: { enabled: true, retain_minutes: 10080 },
      package_cache: { enabled: true },
      releases: { enabled: true, retain_count: 2, retain_minutes: 10080 },
      backups: { enabled: false },
    },
    schedule: { frequent: '6h', weekly: 'Sun 04:00 UTC', pressure: 'on_red' },
  })

  const housekeepingCapacity = () => ({
    resource_state: 'GREEN' as const,
    reasons: [],
    resident_supervisors: { active: 1, limit: 5, available: 4, certain: true },
    provider_executions: { active: 0, limit: 3, available: 3, certain: true },
    work_contexts: { active: 0, limit: 2, available: 2, certain: true },
    heavy_executions: { active: 0, limit: 1, available: 1, waiting: 0 },
    memory: { available_mib: 1024, swap_total_mib: 0, swap_free_mib: 0 },
    root_disk: { used_percent: 42, free_gib: 50 },
    memory_pressure: { some_avg10: 0, full_avg10: 0 },
    cpu_load: { one_minute: 0, cpu_count: 4 },
    housekeeping: null,
  })

  const inspectedPlan = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    plan_id: 'b'.repeat(64),
    generated_at: 100,
    mode: 'frequent' as const,
    root: '/fixture',
    reclaimable_bytes: 100,
    warnings: [],
    candidates: [{ canonical_identity: 'logs:item', category: 'logs', action: 'compress' as const, estimated_reclaim_bytes: 100, retention_reason: 'older_than_policy', protection_reason: null }],
    ...overrides,
  })

  it('keeps Housekeeping penultimate and routes through the canonical settings navigation', async () => {
    const navigate = vi.fn()
    vi.spyOn(api, 'getProviderSettings').mockResolvedValue({
      api_version: '1.0',
      entry_point_group: 'threadcells.provider_adapters.v1',
      adapters: [],
      configurations: [],
      load_failures: [],
    })
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus())
    render(<ControlPlaneSettings section="providers" navigate={navigate} />)

    const navigation = screen.getByRole('navigation', { name: 'Settings sections' })
    expect(navigation.querySelectorAll('a')).toHaveLength(6)
    expect(within(navigation).getAllByRole('link').map(link => [link.textContent, link.getAttribute('href')])).toEqual([
      ['General', '/settings'],
      ['Profiles', '/settings/profiles'],
      ['Providers', '/settings/providers'],
      ['Housekeeping', '/settings/housekeeping'],
      ['Telegram', '/settings/telegram'],
      ['About', '/settings/about'],
    ])
    expect(screen.getByRole('link', { name: 'Providers' })).toHaveAttribute('aria-current', 'page')
    expect(await screen.findByRole('heading', { name: 'Provider Adapters' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: 'Housekeeping' }))
    expect(navigate).toHaveBeenCalledWith('housekeeping')
  })

  it('separates built-in adapter availability from provider CLI readiness', async () => {
    vi.spyOn(api, 'getProviderSettings').mockResolvedValue({
      api_version: '1.0',
      entry_point_group: 'threadcells.provider_adapters.v1',
      adapters: [{
        adapter_id: 'claude_code',
        description: 'Claude Code CLI adapter.',
        plugin_api_version: '1.0',
        source: 'built-in',
        adapter_available: true,
        runtime: {
          installed: false,
          available: false,
          availability: 'NOT_INSTALLED',
          state: 'not_configured',
          authentication: 'unknown',
          version: null,
          reason_code: 'EXECUTABLE_NOT_FOUND',
        },
      }],
      configurations: [{
        config_id: 'builtin-claude',
        display_name: 'Claude Code',
        enabled: false,
        built_in: true,
        revision_id: 'provider-rev-1',
        revision_number: 1,
        fingerprint: 'fingerprint',
        document: { adapter_id: 'claude_code' },
        runtime: {
          installed: true,
          available: false,
          availability: 'UNKNOWN',
          state: 'disabled',
          authentication: 'unknown',
          version: null,
          reason_code: null,
        },
      }],
      load_failures: [],
    })
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus())

    render(<ControlPlaneSettings section="providers" navigate={() => {}} />)

    expect(await screen.findByText('Built-in adapter')).toBeInTheDocument()
    expect(screen.getByText('Configuration disabled')).toBeInTheDocument()
    expect(screen.getByText(/Adapter code availability and provider CLI readiness are separate/)).toBeInTheDocument()
  })

  it('shows immutable profile revisions and resolved-preview controls', async () => {
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus())
    const profileIds = [
      'architect_sol_high', 'code_supervisor', 'critical_sol_xhigh_owner', 'developer',
      'developer_sol_medium', 'developer_terra_high', 'developer_terra_medium',
      'framer_connect_luna_low', 'frontend_sol_medium', 'reviewer', 'reviewer_sol_high',
      'reviewer_sol_medium', 'reviewer_terra_high', 'strategist_sol_medium',
      'supervisor_sol_medium', 'supervisor_terra_medium', 'uiux_sol_high', 'worker_luna_medium',
    ]
    vi.spyOn(api, 'listRegistryProfiles').mockResolvedValue(profileIds.map((profileId, index) => ({
      profile_id: profileId,
      display_name: profileId === 'supervisor_sol_medium' ? 'Sol supervisor' : profileId,
      description: profileId === 'supervisor_sol_medium' ? 'High-reasoning orchestration' : `${profileId} profile`,
      enabled: true,
      built_in: true,
      revision_id: `profile-rev-${index + 1}`,
      revision_number: 1,
      fingerprint: `fingerprint-${index + 1}`,
      document: { execution_mode: profileId.includes('supervisor') ? 'orchestrator' : 'executor', provider_config_id: 'builtin-codex', model: 'gpt-5.6-sol', reasoning_level: 'medium' },
    })))
    render(<ControlPlaneSettings section="profiles" navigate={() => {}} />)

    expect(await screen.findByText('supervisor_sol_medium')).toBeInTheDocument()
    expect(screen.getByText('18 of 18')).toBeInTheDocument()
    expect(screen.getAllByText('Built-in · r1')).toHaveLength(18)
    expect(screen.getAllByRole('button', { name: 'Resolved preview' })).toHaveLength(18)
    expect(screen.getAllByText('builtin-codex')).toHaveLength(18)
    fireEvent.click(screen.getByRole('button', { name: /Advanced import and validation/ }))
    expect(screen.getByRole('button', { name: 'AI generation prompt' })).toBeInTheDocument()
  })

  it('exposes plan, confirmed run, policy, schedule, and report controls', async () => {
    const settings = {
      schema_version: 1 as const,
      policy: {
        logs: { enabled: true, compress_after_minutes: 1440, retain_minutes: 10080 },
        attachments: { enabled: true, retain_minutes: 10080 },
        ephemeral: { enabled: true },
        browser_cache: { enabled: true, retain_minutes: 10080 },
        package_cache: { enabled: true },
        releases: { enabled: true, retain_count: 2, retain_minutes: 10080 },
        backups: { enabled: false },
      },
      schedule: { frequent: '6h', weekly: 'Sun 04:00 UTC', pressure: 'on_red' },
    }
    vi.spyOn(api, 'getHousekeepingSettings').mockResolvedValue(settings)
    vi.spyOn(api, 'getHousekeepingReport').mockResolvedValue({ status: 'never_run' })
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus(true))
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue({
      resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 1, limit: 5, available: 4, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: 0 }, memory: { available_mib: 1024, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 42, free_gib: 50 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 4 }, housekeeping: null,
    })
    const planId = 'a'.repeat(64)
    const plan = vi.spyOn(api, 'getHousekeepingPlan').mockResolvedValue({ schema_version: 1, plan_id: planId, generated_at: 100, mode: 'frequent', root: '/fixture', reclaimable_bytes: 100, warnings: ['retirement_cleanup_claim_unknown:diagnostic-only'], candidates: [{ canonical_identity: 'logs:item', category: 'logs', action: 'compress', estimated_reclaim_bytes: 100, retention_reason: 'older_than_policy', protection_reason: null }, { canonical_identity: 'backup:protected', category: 'backups', action: 'preserve', estimated_reclaim_bytes: 0, retention_reason: 'protected_inventory', protection_reason: 'BACKUP_PROTECTED' }] })
    const run = vi.spyOn(api, 'runHousekeeping').mockResolvedValue({ ok: true, plan_id: planId })

    render(<ControlPlaneSettings section="housekeeping" navigate={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Housekeeping' })).toBeInTheDocument()
    expect(screen.getAllByText('Backups').length).toBeGreaterThan(0)
    expect(screen.getByText('Schedule')).toBeInTheDocument()
    expect(screen.getByText('Housekeeping has not run yet.')).toBeInTheDocument()
    expect(screen.queryByText('retain_minutes')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue('10080')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Execute inspected plan safely' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Build dry-run plan' }))
    await waitFor(() => expect(plan).toHaveBeenCalledWith('frequent'))
    expect((await screen.findAllByText('100 B')).length).toBeGreaterThan(0)
    expect(screen.getByText('logs:item')).toBeInTheDocument()
    expect(within(screen.getByText('Protected / skipped').parentElement as HTMLElement).getByText('1')).toBeInTheDocument()
    expect(screen.getByText(/could not safely confirm an exclusive claim/)).toBeInTheDocument()
    expect(screen.getByText(/Diagnostic ID:/)).toHaveTextContent('diagnostic-only')
    expect(screen.queryByText(/retirement cleanup claim unknown/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Execute inspected plan safely' })).not.toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Execute inspected plan safely' }))
    await waitFor(() => expect(run).toHaveBeenCalledWith('frequent', false, planId))
  })

  it('explains why an otherwise executable inspected plan is disabled', async () => {
    vi.spyOn(api, 'getHousekeepingSettings').mockResolvedValue(housekeepingSettings())
    vi.spyOn(api, 'getHousekeepingReport').mockResolvedValue({ status: 'never_run' })
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus(false))
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue(housekeepingCapacity())
    vi.spyOn(api, 'getHousekeepingPlan').mockResolvedValue(inspectedPlan())

    render(<ControlPlaneSettings section="housekeeping" navigate={() => {}} />)
    await screen.findByRole('heading', { name: 'Housekeeping' })
    fireEvent.click(screen.getByRole('button', { name: 'Build dry-run plan' }))
    await screen.findByText('logs:item')

    const execute = screen.getByRole('button', { name: 'Execute inspected plan safely' })
    expect(execute).toBeDisabled()
    expect(execute).toHaveAttribute('aria-describedby', 'housekeeping-execute-reason')
    expect(screen.getByText('Unlock operator changes to execute this inspected plan.')).toBeInTheDocument()
  })

  it('fails closed with a visible reason for invalid or empty plans', async () => {
    vi.spyOn(api, 'getHousekeepingSettings').mockResolvedValue(housekeepingSettings())
    vi.spyOn(api, 'getHousekeepingReport').mockResolvedValue({ status: 'never_run' })
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus(true))
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue(housekeepingCapacity())
    const getPlan = vi.spyOn(api, 'getHousekeepingPlan')
      .mockResolvedValueOnce(inspectedPlan({ plan_id: 'not-an-authority' }) as never)
      .mockResolvedValueOnce(inspectedPlan({ reclaimable_bytes: 0, candidates: [] }))

    render(<ControlPlaneSettings section="housekeeping" navigate={() => {}} />)
    await screen.findByRole('heading', { name: 'Housekeeping' })
    fireEvent.click(screen.getByRole('button', { name: 'Build dry-run plan' }))
    expect(await screen.findByText('The inspected plan identity is invalid. Build and inspect a fresh plan.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Execute inspected plan safely' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Build dry-run plan' }))
    expect(await screen.findByText('This inspected plan has no safe actionable candidates.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Execute inspected plan safely' })).toBeDisabled()
  })

  it('retires stale and busy execution authority until a fresh plan is inspected', async () => {
    vi.spyOn(api, 'getHousekeepingSettings').mockResolvedValue(housekeepingSettings())
    vi.spyOn(api, 'getHousekeepingReport').mockResolvedValue({ status: 'never_run' })
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus(true))
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue(housekeepingCapacity())
    vi.spyOn(api, 'getHousekeepingPlan').mockResolvedValue(inspectedPlan())
    const run = vi.spyOn(api, 'runHousekeeping')
      .mockRejectedValueOnce(new CaoApiError('Housekeeping plan changed', 'Build a fresh plan.', 409, 'HOUSEKEEPING_PLAN_CHANGED'))
      .mockRejectedValueOnce(new CaoApiError('Housekeeping is already running', 'Wait.', 423, 'HOUSEKEEPING_BUSY'))

    render(<ControlPlaneSettings section="housekeeping" navigate={() => {}} />)
    await screen.findByRole('heading', { name: 'Housekeeping' })
    fireEvent.click(screen.getByRole('button', { name: 'Build dry-run plan' }))
    await screen.findByText('logs:item')
    fireEvent.click(screen.getByRole('button', { name: 'Execute inspected plan safely' }))
    expect(await screen.findByText(/inspected plan is stale/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Execute inspected plan safely' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Build dry-run plan' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Execute inspected plan safely' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Execute inspected plan safely' }))
    expect(await screen.findByText(/Another Housekeeping operation owns the canonical lock/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Execute inspected plan safely' })).toBeDisabled()
  })

  it('keeps the exact inspected plan through operator-status polling', async () => {
    const operatorPoll: { current: (() => void) | null } = { current: null }
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: TimerHandler, timeout?: number) => {
      if (timeout === 15_000 && typeof handler === 'function') operatorPoll.current = handler as () => void
      return 1
    }) as typeof window.setInterval)
    vi.spyOn(api, 'getHousekeepingSettings').mockResolvedValue(housekeepingSettings())
    vi.spyOn(api, 'getHousekeepingReport').mockResolvedValue({ status: 'never_run' })
    const operator = vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus(true))
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue(housekeepingCapacity())
    const plan = inspectedPlan()
    vi.spyOn(api, 'getHousekeepingPlan').mockResolvedValue(plan)
    const run = vi.spyOn(api, 'runHousekeeping').mockResolvedValue({ ok: true, plan_id: plan.plan_id })

    render(<ControlPlaneSettings section="housekeeping" navigate={() => {}} />)
    await screen.findByRole('heading', { name: 'Housekeeping' })
    fireEvent.click(screen.getByRole('button', { name: 'Build dry-run plan' }))
    await screen.findByText('logs:item')
    operatorPoll.current?.()
    await waitFor(() => expect(operator).toHaveBeenCalledTimes(2))
    expect(screen.getByText('logs:item')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Execute inspected plan safely' }))
    await waitFor(() => expect(run).toHaveBeenCalledWith('frequent', false, plan.plan_id))
  })

  it('presents a real Housekeeping report as structured operational evidence', async () => {
    vi.spyOn(api, 'getHousekeepingSettings').mockResolvedValue({
      schema_version: 1,
      policy: {
        logs: { enabled: true, compress_after_minutes: 1440, retain_minutes: 10080 },
        attachments: { enabled: true, retain_minutes: 10080 },
        ephemeral: { enabled: true }, browser_cache: { enabled: true, retain_minutes: 10080 },
        package_cache: { enabled: true }, releases: { enabled: true, retain_count: 2, retain_minutes: 10080 },
        backups: { enabled: false },
      },
      schedule: { frequent: '6h', weekly: 'Sun 04:00 UTC', pressure: 'on_red' },
    })
    vi.spyOn(api, 'getHousekeepingReport').mockResolvedValue({
      ok: false, started_at: '2026-08-20T10:00:00Z', completed_at: '2026-08-20T10:00:02.5Z',
      duration_seconds: 2.5, freed_bytes: 1536, logs_compressed: 2, logs_deleted: 1,
      attachments_deleted: 0, ephemeral_resources_removed: 1, browser_revisions_removed: 0,
      cache_pruned: 1, skipped_open: 1, skipped_unknown: 0,
      execution_failures: [{ reason_code: 'FINGERPRINT_CHANGED' }], warnings: ['metadata_unknown'],
    })
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue(operatorStatus())
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue({
      resource_state: 'YELLOW', reasons: [], resident_supervisors: { active: 1, limit: 5, available: 4, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: 0 }, memory: { available_mib: 1024, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 72, free_gib: 12 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 4 }, housekeeping: null,
    })

    render(<ControlPlaneSettings section="housekeeping" navigate={() => {}} />)

    expect((await screen.findAllByText('Completed with issues')).length).toBeGreaterThan(0)
    expect(screen.getByText('2.5 seconds')).toBeInTheDocument()
    expect(screen.getByText('1.5 KiB')).toBeInTheDocument()
    expect(screen.getByText('1 protected or skipped item')).toBeInTheDocument()
    expect(screen.getByText('FINGERPRINT_CHANGED')).toBeInTheDocument()
    expect(screen.getByText('metadata unknown')).toBeInTheDocument()
    expect(screen.getByText('Raw report')).toBeInTheDocument()
  })

  it('renders About as product identity rather than General settings', () => {
    render(<ControlPlaneSettings section="about" navigate={() => {}} />)

    expect(screen.getByRole('heading', { name: 'ThreadCells' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Why it exists' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Principles' })).toBeInTheDocument()
    expect(screen.getByText('Subaev Ruslan')).toBeInTheDocument()
    expect(screen.getByText('Licensed under Apache-2.0.')).toBeInTheDocument()
    expect(screen.getByText(BUILD_IDENTITY.version)).toBeInTheDocument()
    expect(screen.getByText(BUILD_IDENTITY.revision)).toBeInTheDocument()
    expect(screen.queryByText('Orchestration Capacity')).not.toBeInTheDocument()
    expect(screen.queryByText('Projects')).not.toBeInTheDocument()
  })

  it('uses one short-lived operator unlock without retaining the secret', async () => {
    const getStatus = vi.spyOn(api, 'getOperatorSession')
      .mockResolvedValueOnce(operatorStatus(false))
      .mockResolvedValue(operatorStatus(true))
    const login = vi.spyOn(api, 'createOperatorSession').mockResolvedValue({ authenticated: true })
    vi.spyOn(api, 'getProviderSettings').mockResolvedValue({ api_version: '1.0', entry_point_group: 'threadcells.provider_adapters.v1', adapters: [], configurations: [], load_failures: [] })
    render(<ControlPlaneSettings section="providers" navigate={() => {}} />)

    const input = await screen.findByLabelText('Operator secret') as HTMLInputElement
    const unlock = screen.getByRole('button', { name: /Unlock operator changes/ })
    fireEvent.change(input, { target: { value: 'A7!q' } })
    expect(unlock).toBeDisabled()
    fireEvent.change(input, { target: { value: 'A7!qz' } })
    fireEvent.click(unlock)
    await waitFor(() => expect(login).toHaveBeenCalledWith('A7!qz'))
    expect(input.value).toBe('')
    expect(getStatus).toHaveBeenCalled()
    expect(await screen.findByText(/Unlocked for this browser/)).toBeInTheDocument()
  })

  it('explains unconfigured and expired operator states without exposing credentials', () => {
    const access = {
      status: { ...operatorStatus(), configured: false },
      loading: false,
      busy: false,
      expired: false,
      error: '',
      unlock: vi.fn(async () => true),
      lock: vi.fn(async () => {}),
      refresh: vi.fn(async () => {}),
    }
    const { rerender } = render(<OperatorAccessCard access={access} />)

    expect(screen.getByText(/Not configured/)).toBeInTheDocument()
    expect(screen.getByText('THREADCELLS_OPERATOR_VERIFIER_FILE')).toBeInTheDocument()
    expect(screen.queryByLabelText('Operator secret')).not.toBeInTheDocument()

    rerender(<OperatorAccessCard access={{ ...access, status: operatorStatus(), expired: true }} />)
    expect(screen.getByText(/Authorization expired/)).toBeInTheDocument()
    expect(screen.getByLabelText('Operator secret')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getByLabelText('Operator secret')).toHaveAttribute('minlength', '5')
  })

  it('distinguishes an invalid configured verifier without exposing its path', () => {
    const access = {
      status: { ...operatorStatus(), configured: false, configuration_state: 'invalid' as const },
      loading: false,
      busy: false,
      expired: false,
      error: '',
      unlock: vi.fn(async () => true),
      lock: vi.fn(async () => {}),
      refresh: vi.fn(async () => {}),
    }

    render(<OperatorAccessCard access={access} />)

    expect(screen.getByText(/verifier reference is present but cannot be used safely/i)).toBeInTheDocument()
    expect(screen.queryByText(/operator-verifier\.json/)).not.toBeInTheDocument()
  })
})
