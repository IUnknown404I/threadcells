import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api, HOUSEKEEPING_PLAN_TIMEOUT_MS } from '../api'
import { APP_LOCALE_STORAGE_KEY } from '../i18n'

describe('API wrapper', () => {
  const mockFetch = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('localizes known errors by stable reason code without translating machine identity', async () => {
    localStorage.setItem(APP_LOCALE_STORAGE_KEY, 'ru')
    mockResponse({ reason_code: 'TERMINAL_RUNTIME_ACTIVE', message: 'private backend detail' }, 409)

    const failure = api.deleteTerminal('terminal-id')
    await expect(failure).rejects.toMatchObject({
      title: 'Операция недоступна',
      reasonCode: 'TERMINAL_RUNTIME_ACTIVE',
      status: 409,
    })
    await expect(failure).rejects.not.toThrow('private backend detail')
    expect(mockFetch).toHaveBeenCalledWith('/terminals/terminal-id', expect.objectContaining({ method: 'DELETE' }))
  })

  function mockResponse(data: unknown, status = 200) {
    mockFetch.mockResolvedValueOnce({
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 200 ? 'OK' : 'Error',
      json: () => Promise.resolve(data),
    })
  }

  it('listSessions fetches /sessions', async () => {
    const sessions = [{ id: 's1', name: 'test', status: 'active' }]
    mockResponse(sessions)
    const result = await api.listSessions()
    expect(result).toEqual(sessions)
    expect(mockFetch).toHaveBeenCalledWith('/sessions', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('encodes session names used in route paths', async () => {
    mockResponse({ session: {}, terminals: [] })
    await api.getSession('release.2026 / test')
    expect(mockFetch).toHaveBeenCalledWith('/sessions/release.2026%20%2F%20test', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('listProfiles fetches /agents/profiles', async () => {
    const profiles = [{ name: 'dev', description: 'Developer', source: 'built-in' }]
    mockResponse(profiles)
    const result = await api.listProfiles()
    expect(result).toEqual(profiles)
  })

  it('listProviders fetches /agents/providers', async () => {
    const providers = [{ name: 'kiro_cli', binary: 'kiro-cli', installed: true }]
    mockResponse(providers)
    const result = await api.listProviders()
    expect(result).toEqual(providers)
  })

  it('createSession sends POST with params', async () => {
    const terminal = { id: 't1', name: 'dev', provider: 'kiro_cli', session_name: 's1' }
    mockResponse(terminal)
    await api.createSession('kiro_cli', 'developer')
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/sessions?provider=kiro_cli&agent_profile=developer'),
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('does not client-abort a slow session startup after 90 seconds', async () => {
    vi.useFakeTimers()
    let resolveResponse!: (value: unknown) => void
    mockFetch.mockImplementationOnce(() => new Promise(resolve => { resolveResponse = resolve }))

    const creating = api.createSession('codex', 'developer')
    const request = mockFetch.mock.calls[0][1]
    await vi.advanceTimersByTimeAsync(91_000)

    expect(request.signal.aborted).toBe(false)
    resolveResponse({ ok: true, json: () => Promise.resolve({ id: 't1' }) })
    await expect(creating).resolves.toEqual({ id: 't1' })
    vi.useRealTimers()
  })

  it.each([
    ['normal plan', () => api.getHousekeepingPlan('frequent')],
    ['Full Cleanup preview', () => api.getFullCleanupPlan()],
  ])('keeps a slow %s request alive beyond the ordinary 10-second timeout', async (_label, requestPlan) => {
    vi.useFakeTimers()
    let resolveResponse!: (value: unknown) => void
    mockFetch.mockImplementationOnce(() => new Promise(resolve => { resolveResponse = resolve }))

    const planning = requestPlan()
    const request = mockFetch.mock.calls[0][1]
    await vi.advanceTimersByTimeAsync(10_001)

    expect(request.signal.aborted).toBe(false)
    resolveResponse({ ok: true, json: () => Promise.resolve({ plan_id: 'a'.repeat(64) }) })
    await expect(planning).resolves.toMatchObject({ plan_id: 'a'.repeat(64) })
    vi.useRealTimers()
  })

  it('maps the bounded Full Cleanup planning timeout to product copy', async () => {
    vi.useFakeTimers()
    mockFetch.mockImplementationOnce((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('signal is aborted without reason', 'AbortError')), { once: true })
    }))

    const planning = api.getFullCleanupPlan()
    const rejected = expect(planning).rejects.toMatchObject({
      title: 'Preview took too long to build',
      description: 'ThreadCells could not finish the filesystem inventory in time. No files were deleted. Try again.',
      reasonCode: 'HOUSEKEEPING_PLAN_TIMEOUT',
    })
    await vi.advanceTimersByTimeAsync(HOUSEKEEPING_PLAN_TIMEOUT_MS)
    await rejected
    vi.useRealTimers()
  })

  it('maps a planning network failure without exposing raw browser errors', async () => {
    mockFetch.mockRejectedValueOnce(new TypeError('signal is aborted without reason'))

    await expect(api.getHousekeepingPlan('weekly')).rejects.toMatchObject({
      title: 'Plan could not be built',
      description: 'ThreadCells lost the connection while scanning resources. No files were deleted. Try again.',
      reasonCode: 'HOUSEKEEPING_PLAN_NETWORK_ERROR',
    })
  })

  it('createSession includes working directory when provided', async () => {
    mockResponse({ id: 't1' })
    await api.createSession('kiro_cli', 'developer', undefined, '/home/user/project')
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('working_directory='),
      expect.any(Object)
    )
  })

  it('createSession includes an explicit session name when provided', async () => {
    mockResponse({ id: 't1' })
    await api.createSession('kiro_cli', 'developer', 'CAO-UI-T1-IMPLEMENTATION')
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('session_name=CAO-UI-T1-IMPLEMENTATION'),
      expect.any(Object)
    )
  })

  it('sends a one-use XHigh grant only in the protected request header', async () => {
    mockResponse({ id: 't1' })
    await api.createSession('codex', 'critical_sol_xhigh_owner', undefined, undefined, undefined, {
      launch_id: 'launch-1',
      grant: 'one-use-secret',
      expires_in_seconds: 60,
    })

    const [url, options] = mockFetch.mock.calls[0]
    expect(url).toContain('owner_grant_launch_id=launch-1')
    expect(url).not.toContain('one-use-secret')
    expect(options.headers).toEqual({ 'X-ThreadCells-Owner-Grant': 'one-use-secret' })
  })

  it('sends the same protected one-use XHigh grant when adding to an existing session', async () => {
    mockResponse({ id: 't2' })
    await api.addTerminalToSession('cao-existing', 'codex', 'critical_sol_xhigh_owner', '/srv/session-root', undefined, {
      launch_id: 'add-launch-1',
      grant: 'one-use-add-secret',
      expires_in_seconds: 60,
    })

    const [url, options] = mockFetch.mock.calls[0]
    expect(url).toContain('/sessions/cao-existing/terminals?')
    expect(url).toContain('owner_grant_launch_id=add-launch-1')
    expect(url).not.toContain('one-use-add-secret')
    expect(options.headers).toEqual({ 'X-ThreadCells-Owner-Grant': 'one-use-add-secret' })
    expect(options.body).toBeUndefined()
  })

  it('uses versioned control-plane API paths distinct from Settings page routes', async () => {
    mockResponse([])
    await api.listRegistryProfiles()
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/profiles?include_disabled=true', expect.any(Object))

    mockResponse({ schema_version: 1, policy: {}, schedule: {} })
    await api.getHousekeepingSettings()
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/housekeeping', expect.any(Object))

    mockResponse({ ok: true })
    const planId = 'a'.repeat(64)
    await api.runHousekeeping('weekly', false, planId)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/housekeeping/run',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ mode: 'weekly', dry_run: false, expected_plan_id: planId }),
      })
    )

    mockResponse({ mode: 'full', plan_id: planId })
    await api.getFullCleanupPlan()
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/housekeeping/full-cleanup/plan', expect.objectContaining({ signal: expect.any(AbortSignal) }))

    mockResponse({ ok: true })
    await api.runFullCleanup(planId)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/housekeeping/full-cleanup/run',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ expected_plan_id: planId, confirmed: true }),
      })
    )
    expect(JSON.stringify(mockFetch.mock.calls[mockFetch.mock.calls.length - 1])).not.toMatch(/secret|password/i)
  })

  it('uses the canonical operator session status and revocation endpoints', async () => {
    mockResponse({ configured: true, authenticated: false, expires_in_seconds: 0, session_ttl_seconds: 300, verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE' })
    await api.getOperatorSession()
    expect(mockFetch).toHaveBeenCalledWith('/operator/session', expect.any(Object))

    mockResponse({ revoked: true })
    await api.deleteOperatorSession()
    expect(mockFetch).toHaveBeenCalledWith('/operator/session', expect.objectContaining({ method: 'DELETE' }))
  })

  it('deleteSession sends DELETE', async () => {
    mockResponse({ success: true, deleted: [], errors: [] })
    await api.deleteSession('s1')
    expect(mockFetch).toHaveBeenCalledWith('/sessions/s1', expect.objectContaining({ method: 'DELETE' }))
  })

  it('sendInput sends POST with message', async () => {
    mockResponse({ success: true })
    await api.sendInput('t1', 'hello')
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/terminals/t1/input?message=hello'),
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('sendWorkflowInput uses the explicit semantic workflow transport', async () => {
    mockResponse({ success: true, accepted: true, duplicate: false, turn_id: 73, queued: false, status: 'provider_admitted', reason_code: null })
    await api.sendWorkflowInput('t1', 'line one\nline two', '4042ff90-5a5c-45c2-9325-b6cbe38f6564')
    expect(mockFetch).toHaveBeenCalledWith(
      '/terminals/t1/workflow-input',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: 'line one\nline two',
          request_id: '4042ff90-5a5c-45c2-9325-b6cbe38f6564',
        }),
      })
    )
  })

  it('sendInboxMessage sends Unicode multiline text in the JSON body, not the URL', async () => {
    const message = 'Первая строка\\nВторая строка — ✓'
    mockResponse({ success: true })
    await api.sendInboxMessage('t1', 'ui', message)
    expect(mockFetch).toHaveBeenCalledWith(
      '/terminals/t1/inbox/messages',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sender_id: 'ui', message }),
      })
    )
  })

  it('uploads a terminal image as raw image bytes', async () => {
    const image = new File(['image-bytes'], 'image.png', { type: 'image/png' })
    mockResponse({ path: '/runtime/terminal-attachments/abcd1234/image.png' }, 201)

    await api.uploadTerminalImage('abcd1234', image)

    expect(mockFetch).toHaveBeenCalledWith(
      '/terminals/abcd1234/attachments/image',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'image/png' },
        body: image,
      })
    )
  })

  it.each([
    ['notes.md', 'notes.md'],
    ['привет.md', '%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82.md'],
    ['ThreadCells — title.md', 'ThreadCells%20%E2%80%94%20title.md'],
    ['日本語.md', '%E6%97%A5%E6%9C%AC%E8%AA%9E.md'],
    ['emoji 😀%&.md', 'emoji%20%F0%9F%98%80%25%26.md'],
  ])('encodes the full %s terminal filename into an ASCII header', async (filename, expectedHeader) => {
    const attachment = new File(['# attachment\n'], filename, { type: 'text/markdown' })
    mockResponse({ path: '/runtime/terminal-attachments/abcd1234/generated.md' }, 201)

    await api.uploadTerminalFile('abcd1234', attachment)

    const request = mockFetch.mock.calls[0][1]
    const header = request.headers['X-Terminal-Filename']
    expect(header).toBe(expectedHeader)
    expect([...header].every(character => character.charCodeAt(0) <= 127)).toBe(true)
  })

  it('getTerminalOutput fetches with mode', async () => {
    mockResponse({ output: 'test output', mode: 'last' })
    const result = await api.getTerminalOutput('t1', 'last')
    expect(result.output).toBe('test output')
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/terminals/t1/output?mode=last'),
      expect.any(Object)
    )
  })

  it('listFlows fetches /flows', async () => {
    const flows = [{ name: 'test-flow', schedule: '0 9 * * *', enabled: true }]
    mockResponse(flows)
    const result = await api.listFlows()
    expect(result).toEqual(flows)
  })

  it('createFlow sends POST with JSON body', async () => {
    const flow = { name: 'new-flow', schedule: '0 9 * * *', agent_profile: 'dev', prompt_template: 'Do stuff' }
    mockResponse(flow)
    await api.createFlow(flow)
    expect(mockFetch).toHaveBeenCalledWith(
      '/flows',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(flow),
      })
    )
  })

  it('enableFlow sends POST', async () => {
    mockResponse({ success: true })
    await api.enableFlow('my-flow')
    expect(mockFetch).toHaveBeenCalledWith('/flows/my-flow/enable', expect.objectContaining({ method: 'POST' }))
  })

  it('disableFlow sends POST', async () => {
    mockResponse({ success: true })
    await api.disableFlow('my-flow')
    expect(mockFetch).toHaveBeenCalledWith('/flows/my-flow/disable', expect.objectContaining({ method: 'POST' }))
  })

  it('runFlow sends POST with long timeout', async () => {
    mockResponse({ executed: true })
    await api.runFlow('my-flow')
    expect(mockFetch).toHaveBeenCalledWith('/flows/my-flow/run', expect.objectContaining({ method: 'POST' }))
  })

  it('deleteFlow sends DELETE', async () => {
    mockResponse({ success: true })
    await api.deleteFlow('my-flow')
    expect(mockFetch).toHaveBeenCalledWith('/flows/my-flow', expect.objectContaining({ method: 'DELETE' }))
  })

  it('uses product-safe generic copy instead of an unknown raw backend detail', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: () => Promise.resolve({ detail: "Session 'cao-duplicate' already exists" }),
    })
    const failure = api.listSessions()
    await expect(failure).rejects.toMatchObject({
      title: 'Invalid request',
      description: 'One or more submitted values are invalid. Review the form and try again.',
      status: 400,
    })
    await expect(failure).rejects.not.toThrow("Session 'cao-duplicate' already exists")
  })

  it('exitTerminal sends POST', async () => {
    mockResponse({ success: true })
    await api.exitTerminal('t1')
    expect(mockFetch).toHaveBeenCalledWith('/terminals/t1/exit', expect.objectContaining({ method: 'POST' }))
  })

  it('deleteTerminal sends DELETE', async () => {
    mockResponse({ success: true })
    await api.deleteTerminal('t1')
    expect(mockFetch).toHaveBeenCalledWith('/terminals/t1', expect.objectContaining({ method: 'DELETE' }))
  })
})
