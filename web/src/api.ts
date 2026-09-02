import { readStoredAppLocale, translate, type TranslationKey } from './i18n'

const BASE = ''  // Vite proxy handles routing to backend

type ApiFailureBody = { reason_code?: unknown; diagnostic_id?: unknown; detail?: unknown; message?: unknown }

type TimeoutErrorCopy = {
  title: string
  description: string
  reasonCode: string
}

type FetchJsonOptions = RequestInit & {
  timeoutMs?: number | null
  timeoutError?: TimeoutErrorCopy
}

// Filesystem inventory shares the established bounded Full Cleanup helper window.
// It must not inherit the 10-second timeout used by ordinary control-plane reads.
export const HOUSEKEEPING_PLAN_TIMEOUT_MS = 1_800_000

export class CaoApiError extends Error {
  constructor(
    public readonly title: string,
    public readonly description: string,
    public readonly status: number,
    public readonly reasonCode?: string,
    public readonly diagnosticId?: string,
  ) {
    const technical = [`HTTP ${status}`, reasonCode, diagnosticId && `Diagnostic ${diagnosticId}`].filter(Boolean).join(' · ')
    super(`${title}: ${description}${technical ? `\n${technical}` : ''}`)
    this.name = 'CaoApiError'
  }
}

export interface WorkflowInputResponse {
  success: boolean
  accepted: boolean
  duplicate: boolean
  turn_id: number
  queued: boolean
  status: 'provider_admitted' | 'already_accepted' | 'queued_provider_execution' | 'queued_runtime_recovery' | 'failed'
  reason_code: string | null
}

const REASON_COPY = {
  WORKTREE_WRITER_LEASE_HELD: ['Working directory is locked', 'Another active write-capable agent is already using this working directory. Gracefully exit that agent or choose another working directory.'],
  WORKTREE_AUTHORITY_UNRECONCILED: ['Working directory needs attention', 'ThreadCells could not verify worktree authority. Reconcile the existing worktree before starting another writer.'],
  TOTAL_PROVIDER_CAPACITY_EXHAUSTED: ['Capacity limit reached', 'No compatible provider slot is currently available. Wait for an active agent to finish or choose another provider.'],
  PROVIDER_EXECUTION_CAPACITY_EXHAUSTED: ['Provider turns are queued', 'All provider execution slots are active. This input will continue automatically when a slot is released.'],
  RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED: ['Resident supervisor limit reached', 'Five supervisors are already resident. Exit one before starting another project supervisor.'],
  SESSION_PRIMARY_SUPERVISOR_EXISTS: ['Session already has a primary supervisor', 'Create a new Session for independent work, or use recovery takeover for this same work context.'],
  PROJECT_SOURCE_NOT_GIT: ['Project source is not a Git repository', 'Managed supervisor workspaces require a canonical Git source with a valid HEAD revision.'],
  WORK_CONTEXT_REQUEST_CONFLICT: ['Workspace request changed', 'This retry identity is already bound to another managed workspace request.'],
  WORK_CONTEXT_AUTHORITY_CONFLICT: ['Workspace authority conflict', 'ThreadCells could not reserve a unique Session workspace. Retry with a new Session request.'],
  WORK_CONTEXT_AUTHORITY_CHANGED: ['Workspace authority changed', 'The reserved managed workspace no longer matches its durable identity and was preserved for inspection.'],
  WORK_CONTEXT_PROVIDER_LAUNCH_UNCERTAIN: ['Supervisor launch needs recovery', 'The writer was reserved but the provider launch outcome is uncertain. The workspace was preserved; use recovery takeover instead of starting a second writer.'],
  RECOVERY_HEALTHY_RUNTIME_ACTIVE: ['Recovery takeover blocked', 'The current supervisor runtime is still active. Retire it canonically or restore it instead of creating a second writer.'],
  RECOVERY_PROVIDER_EXECUTION_ACTIVE: ['Recovery takeover blocked', 'The current supervisor still owns an active provider execution.'],
  RECOVERY_PRIVILEGED_EFFECT_UNRESOLVED: ['Recovery takeover blocked', 'A privileged effect has an unresolved outcome. Reconcile it before replacing the supervisor.'],
  RECOVERY_GENUINE_OWNER_GATE: ['Owner decision still required', 'Recovery takeover cannot bypass a genuine semantic owner decision.'],
  RECOVERY_AUTHORITY_GENERATION_STALE: ['Recovery preview is stale', 'Writer authority changed after inspection. Inspect the supervisor again.'],
  RECOVERY_RUNTIME_GENERATION_STALE: ['Recovery preview is stale', 'Provider runtime generation changed after inspection. Inspect the supervisor again.'],
  RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS: ['Runtime authority is uncertain', 'ThreadCells could not prove that the exact old runtime can no longer write.'],
  RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE: ['Runtime inventory unavailable', 'ThreadCells could not inspect the exact old runtime and left writer authority unchanged.'],
  RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE: ['Old process tree is still active', 'ThreadCells found a live process from the old supervisor runtime and left writer authority unchanged.'],
  RECOVERY_RUNTIME_RETIREMENT_FAILED: ['Old runtime could not be fenced', 'ThreadCells could not retire the exact idle pane, so writer authority was not transferred.'],
  RECOVERY_RUNTIME_OPERATION_ACTIVE: ['Recovery takeover blocked', 'A lifecycle operation is already active for this supervisor.'],
  RECOVERY_CHILD_WORK_ACTIVE: ['Recovery takeover blocked', 'Child work is still active under this supervisor and must be reconciled first.'],
  RECOVERY_TARGET_IDENTITY_MISMATCH: ['Recovery target rejected', 'The selected terminal is not the exact project supervisor work context.'],
  RECOVERY_TARGET_NOT_TAKEOVER_ELIGIBLE: ['Recovery target rejected', 'This supervisor lifecycle is not eligible for recovery takeover.'],
  RECOVERY_REQUEST_ID_REUSED: ['Recovery request rejected', 'This request identity was already bound to different recovery authority.'],
  RECOVERY_WRITER_AUTHORITY_AMBIGUOUS: ['Writer authority is uncertain', 'ThreadCells could not prove one exact writer lease for this work context.'],
  RECOVERY_TAKEOVER_ALREADY_CLAIMED: ['Recovery already claimed', 'Another owner-authorized takeover already owns this supervisor.'],
  RECOVERY_WORKTREE_STATUS_UNAVAILABLE: ['Worktree status unavailable', 'ThreadCells could not safely inspect the existing Git worktree.'],
  RECOVERY_WORKTREE_AUTHORITY_AMBIGUOUS: ['Worktree authority is uncertain', 'The existing managed worktree identity could not be proven.'],
  RECOVERY_PROFILE_AUTHORITY_MISMATCH: ['Recovery profile rejected', 'The selected provider/profile does not match the trusted XHigh launch authority.'],
  RECOVERY_PROVIDER_DISPATCH_UNCERTAIN: ['Recovery launch needs attention', 'The provider launch crossed an uncertain boundary and will not be dispatched again blindly.'],
  RECOVERY_TAKEOVER_WRITER_FENCE_LOST: ['Recovery fencing failed', 'The reserved successor writer epoch no longer matches durable authority. No second provider was launched.'],
  RECOVERY_PROVIDER_START_FAILED: ['Recovery provider did not start', 'ThreadCells exhausted the bounded safe startup retries without creating a duplicate supervisor.'],
  WORK_CONTEXT_CAPACITY_EXHAUSTED: ['Capacity limit reached', 'No compatible work slot is currently available. Wait for an active work agent to finish and try again.'],
  RESOURCE_HEALTH_REJECTED: ['ThreadCells resources are unavailable', 'ThreadCells temporarily rejected new work because host resources are not healthy. Wait for the resource state to recover.'],
  CONTEXT_INVENTORY_UNAVAILABLE: ['Capacity status is unavailable', 'ThreadCells cannot safely confirm available execution capacity yet. Wait for runtime inventory to recover and try again.'],
  ADMISSION_FENCE_TIMEOUT: ['Admission timed out', 'ThreadCells could not safely reserve a slot in time. Try again shortly.'],
  HEAVY_SLOT_WAIT_TIMEOUT: ['Capacity limit reached', 'A compatible heavy-execution slot did not become available in time. Try again after active work finishes.'],
  HOUSEKEEPING_PLAN_CHANGED: ['Housekeeping plan changed', 'The inspected plan no longer matches current cleanup state. Build and inspect a fresh plan before executing.'],
  HOUSEKEEPING_BUSY: ['Housekeeping is already running', 'Another Housekeeping operation owns the canonical lock. Wait for it to finish, then build a fresh plan.'],
  FULL_CLEANUP_ADMISSION_BUSY: ['Full Cleanup cannot start', 'An agent admission boundary is busy. Wait for agents to become idle, then build a fresh preview.'],
  FULL_CLEANUP_NOT_IDLE: ['Agents are still working', 'Full Cleanup is available only when every agent is Ready or Exited and no provider or Heavy execution is active.'],
  FULL_CLEANUP_IDLE_INVENTORY_UNKNOWN: ['Idle state could not be proven', 'ThreadCells could not prove that every agent and execution lane is idle, so Full Cleanup was blocked.'],
  OPERATOR_AUTH_NOT_CONFIGURED: ['Operator authorization unavailable', 'The privileged Full Cleanup helper could not validate the configured operator authority. No files were deleted.'],
  TERMINAL_RUNTIME_ACTIVE: ['Exit terminal first', 'Use Graceful Exit and wait until ThreadCells confirms the provider has exited before deleting terminal history.'],
  TERMINAL_EXIT_PENDING: ['Terminal exit is pending', 'ThreadCells has not confirmed provider death yet. Wait for exit reconciliation before deleting terminal history.'],
  TERMINAL_DEATH_UNCONFIRMED: ['Terminal death is not confirmed', 'ThreadCells could not retire the exact exited runtime, so terminal metadata remains protected.'],
  TERMINAL_RUNTIME_AUTHORITY_UNCERTAIN: ['Terminal authority is uncertain', 'ThreadCells could not verify the exact terminal runtime identity, so metadata remains protected.'],
  TERMINAL_IDENTITY_CHANGED: ['Terminal identity changed', 'Terminal authority changed during deletion. Refresh and retry after lifecycle reconciliation.'],
  TERMINAL_RUNTIME_NOT_WRITABLE: ['Agent has exited', 'This historical agent cannot receive new Workflow Composer input. Open a Ready agent instead.'],
  WORKFLOW_INPUT_IDEMPOTENCY_CONFLICT: ['Workflow input changed', 'This retry identity is already bound to different workflow text. Edit the draft and submit it as a new task.'],
  WORKFLOW_INPUT_NO_LONGER_EXECUTABLE: ['Workflow input is no longer executable', 'This retry identity belongs to a workflow turn that closed before admission. Submit the text again as a new task.'],
  TERMINAL_WORKTREE_PROTECTED: ['Managed worktree retained', 'ThreadCells cannot delete this terminal history because its managed worktree contains state that must remain recoverable.'],
  SESSION_RUNTIME_ACTIVE: ['Exit every agent first', 'A live or Ready agent still owns this session. Gracefully exit every agent before deleting the session.'],
  SESSION_RUNTIME_AUTHORITY_UNPROVEN: ['Session runtime authority is uncertain', 'ThreadCells could not prove that every historical runtime is gone, so the session remains protected.'],
  EXIT_PANE_AMBIGUOUS: ['Terminal exit needs attention', 'The terminal window has multiple panes. Resolve the terminal layout before trying Graceful Exit again.'],
} satisfies Record<string, [string, string]>

type KnownReasonCode = keyof typeof REASON_COPY

const REASON_COPY_RU = {
  WORKTREE_WRITER_LEASE_HELD: ['Рабочая папка заблокирована', 'Эту рабочую папку уже использует другой активный агент с правом записи. Корректно завершите его или выберите другую рабочую папку.'],
  WORKTREE_AUTHORITY_UNRECONCILED: ['Требуется проверить рабочую папку', 'ThreadCells не удалось подтвердить полномочия рабочего дерева. Согласуйте существующее рабочее дерево, прежде чем запускать другого агента с правом записи.'],
  TOTAL_PROVIDER_CAPACITY_EXHAUSTED: ['Достигнут предел ёмкости', 'Сейчас нет свободного слота совместимого провайдера. Дождитесь завершения активного агента или выберите другого провайдера.'],
  PROVIDER_EXECUTION_CAPACITY_EXHAUSTED: ['Ходы провайдера поставлены в очередь', 'Все слоты выполнения провайдера заняты. Эта задача продолжится автоматически после освобождения слота.'],
  RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED: ['Достигнут предел резидентных супервизоров', 'Уже запущено пять резидентных супервизоров. Завершите одного из них перед запуском нового супервизора проекта.'],
  SESSION_PRIMARY_SUPERVISOR_EXISTS: ['В сессии уже есть основной супервизор', 'Создайте новую сессию для независимой работы или используйте перехват восстановления для этого рабочего контекста.'],
  PROJECT_SOURCE_NOT_GIT: ['Источник проекта не является репозиторием Git', 'Для управляемого рабочего дерева супервизора нужен канонический источник Git с корректной ревизией HEAD.'],
  WORK_CONTEXT_REQUEST_CONFLICT: ['Запрос рабочего пространства изменился', 'Этот идентификатор повтора уже связан с другим запросом управляемого рабочего пространства.'],
  WORK_CONTEXT_AUTHORITY_CONFLICT: ['Конфликт полномочий рабочего пространства', 'ThreadCells не удалось зарезервировать уникальное рабочее пространство сессии. Повторите с новым запросом сессии.'],
  WORK_CONTEXT_AUTHORITY_CHANGED: ['Полномочия рабочего пространства изменились', 'Зарезервированное рабочее пространство больше не совпадает с долговечной идентичностью и сохранено для проверки.'],
  WORK_CONTEXT_PROVIDER_LAUNCH_UNCERTAIN: ['Запуск супервизора требует восстановления', 'Writer был зарезервирован, но результат запуска провайдера не определён. Рабочее пространство сохранено; используйте recovery takeover вместо запуска второго writer.'],
  RECOVERY_HEALTHY_RUNTIME_ACTIVE: ['Перехват восстановления заблокирован', 'Среда текущего супервизора всё ещё активна. Корректно завершите или восстановите её вместо создания второго владельца записи.'],
  RECOVERY_PROVIDER_EXECUTION_ACTIVE: ['Перехват восстановления заблокирован', 'Текущий супервизор всё ещё владеет активным выполнением провайдера.'],
  RECOVERY_PRIVILEGED_EFFECT_UNRESOLVED: ['Перехват восстановления заблокирован', 'Результат привилегированного действия не определён. Сначала согласуйте его.'],
  RECOVERY_GENUINE_OWNER_GATE: ['По-прежнему требуется решение владельца', 'Перехват восстановления не обходит настоящее семантическое решение владельца.'],
  RECOVERY_AUTHORITY_GENERATION_STALE: ['Предпросмотр восстановления устарел', 'Полномочия записи изменились после проверки. Проверьте супервизора ещё раз.'],
  RECOVERY_RUNTIME_GENERATION_STALE: ['Предпросмотр восстановления устарел', 'Поколение среды провайдера изменилось после проверки. Проверьте супервизора ещё раз.'],
  RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS: ['Полномочия среды неясны', 'ThreadCells не удалось доказать, что точная старая среда больше не может записывать.'],
  RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE: ['Инвентаризация среды недоступна', 'ThreadCells не удалось проверить точную старую среду; полномочия записи не изменены.'],
  RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE: ['Старое дерево процессов всё ещё активно', 'ThreadCells обнаружил живой процесс старой среды супервизора; полномочия записи не изменены.'],
  RECOVERY_RUNTIME_RETIREMENT_FAILED: ['Не удалось оградить старую среду', 'ThreadCells не смог завершить точную простаивающую панель, поэтому полномочия записи не переданы.'],
  RECOVERY_RUNTIME_OPERATION_ACTIVE: ['Перехват восстановления заблокирован', 'Для этого супервизора уже выполняется операция жизненного цикла.'],
  RECOVERY_CHILD_WORK_ACTIVE: ['Перехват восстановления заблокирован', 'Под этим супервизором всё ещё выполняется дочерняя работа; сначала согласуйте её.'],
  RECOVERY_TARGET_IDENTITY_MISMATCH: ['Цель восстановления отклонена', 'Выбранный терминал не является точным рабочим контекстом супервизора проекта.'],
  RECOVERY_TARGET_NOT_TAKEOVER_ELIGIBLE: ['Цель восстановления отклонена', 'Состояние жизненного цикла этого супервизора не допускает перехват восстановления.'],
  RECOVERY_REQUEST_ID_REUSED: ['Запрос восстановления отклонён', 'Этот идентификатор запроса уже связан с другими полномочиями восстановления.'],
  RECOVERY_WRITER_AUTHORITY_AMBIGUOUS: ['Полномочия записи неясны', 'ThreadCells не удалось подтвердить единственную точную аренду записи для этого рабочего контекста.'],
  RECOVERY_TAKEOVER_ALREADY_CLAIMED: ['Восстановление уже заявлено', 'Другой авторизованный владельцем перехват уже владеет этим супервизором.'],
  RECOVERY_WORKTREE_STATUS_UNAVAILABLE: ['Статус рабочего дерева недоступен', 'ThreadCells не удалось безопасно проверить существующее рабочее дерево Git.'],
  RECOVERY_WORKTREE_AUTHORITY_AMBIGUOUS: ['Полномочия рабочего дерева неясны', 'Не удалось подтвердить идентичность существующего управляемого рабочего дерева.'],
  RECOVERY_PROFILE_AUTHORITY_MISMATCH: ['Профиль восстановления отклонён', 'Выбранные провайдер и профиль не соответствуют доверенным полномочиям запуска XHigh.'],
  RECOVERY_PROVIDER_DISPATCH_UNCERTAIN: ['Запуск восстановления требует внимания', 'Запуск провайдера пересёк неопределённую границу и не будет слепо отправлен повторно.'],
  RECOVERY_TAKEOVER_WRITER_FENCE_LOST: ['Ограждение восстановления не выполнено', 'Зарезервированное поколение нового владельца записи больше не совпадает с долговременными полномочиями. Второй провайдер не запущен.'],
  RECOVERY_PROVIDER_START_FAILED: ['Провайдер восстановления не запустился', 'ThreadCells исчерпал ограниченные безопасные попытки запуска, не создавая дубликат супервизора.'],
  WORK_CONTEXT_CAPACITY_EXHAUSTED: ['Достигнут предел ёмкости', 'Сейчас нет свободного совместимого рабочего слота. Дождитесь завершения активной работы и повторите попытку.'],
  RESOURCE_HEALTH_REJECTED: ['Ресурсы ThreadCells недоступны', 'ThreadCells временно отклонил новую работу из-за состояния ресурсов хоста. Дождитесь восстановления ресурсов.'],
  CONTEXT_INVENTORY_UNAVAILABLE: ['Сведения о ёмкости недоступны', 'ThreadCells пока не может безопасно подтвердить доступную ёмкость выполнения. Дождитесь восстановления инвентаризации среды и повторите попытку.'],
  ADMISSION_FENCE_TIMEOUT: ['Истекло время допуска', 'ThreadCells не успел безопасно зарезервировать слот. Повторите попытку чуть позже.'],
  HEAVY_SLOT_WAIT_TIMEOUT: ['Достигнут предел ёмкости', 'Совместимый слот тяжёлой операции не освободился вовремя. Повторите попытку после завершения активной работы.'],
  HOUSEKEEPING_PLAN_CHANGED: ['План обслуживания изменился', 'Проверенный план больше не соответствует текущему состоянию очистки. Постройте и проверьте новый план перед выполнением.'],
  HOUSEKEEPING_BUSY: ['Обслуживание уже выполняется', 'Каноническая блокировка занята другой операцией обслуживания. Дождитесь её завершения и постройте новый план.'],
  FULL_CLEANUP_ADMISSION_BUSY: ['Полную очистку нельзя запустить', 'Граница допуска агента занята. Дождитесь простоя агентов и постройте новый предпросмотр.'],
  FULL_CLEANUP_NOT_IDLE: ['Агенты ещё работают', 'Полная очистка доступна, только когда каждый агент готов или завершён, а выполнения провайдера и тяжёлые операции простаивают.'],
  FULL_CLEANUP_IDLE_INVENTORY_UNKNOWN: ['Не удалось подтвердить простой', 'ThreadCells не удалось подтвердить простой всех агентов и каналов выполнения, поэтому полная очистка заблокирована.'],
  OPERATOR_AUTH_NOT_CONFIGURED: ['Авторизация оператора недоступна', 'Привилегированный помощник полной очистки не смог проверить настроенные полномочия оператора. Файлы не удалялись.'],
  TERMINAL_RUNTIME_ACTIVE: ['Сначала завершите терминал', 'Используйте корректное завершение и дождитесь, пока ThreadCells подтвердит остановку провайдера, прежде чем удалять историю терминала.'],
  TERMINAL_EXIT_PENDING: ['Терминал ещё завершается', 'ThreadCells пока не подтвердил остановку провайдера. Дождитесь согласования завершения, прежде чем удалять историю терминала.'],
  TERMINAL_DEATH_UNCONFIRMED: ['Остановка терминала не подтверждена', 'ThreadCells не смог вывести из среды именно этот завершённый процесс, поэтому метаданные терминала остаются защищены.'],
  TERMINAL_RUNTIME_AUTHORITY_UNCERTAIN: ['Полномочия терминала неясны', 'ThreadCells не удалось проверить точную идентичность среды терминала, поэтому метаданные остаются защищены.'],
  TERMINAL_IDENTITY_CHANGED: ['Идентичность терминала изменилась', 'Во время удаления изменились полномочия терминала. Обновите данные и повторите попытку после согласования жизненного цикла.'],
  TERMINAL_RUNTIME_NOT_WRITABLE: ['Агент завершён', 'Этот исторический агент не может получать новые задачи редактора рабочего процесса. Откройте готового агента.'],
  WORKFLOW_INPUT_IDEMPOTENCY_CONFLICT: ['Задача рабочего процесса изменилась', 'Этот идентификатор повтора уже связан с другим текстом задачи. Измените черновик и отправьте его как новую задачу.'],
  WORKFLOW_INPUT_NO_LONGER_EXECUTABLE: ['Задачу рабочего процесса больше нельзя выполнить', 'Этот идентификатор повтора относится к ходу, который закрылся до допуска. Отправьте текст ещё раз как новую задачу.'],
  TERMINAL_WORKTREE_PROTECTED: ['Управляемое рабочее дерево сохранено', 'Историю терминала нельзя удалить: его управляемое рабочее дерево содержит состояние, которое должно оставаться восстанавливаемым.'],
  SESSION_RUNTIME_ACTIVE: ['Сначала завершите всех агентов', 'В этой сессии остаётся активный или готовый агент. Корректно завершите всех агентов перед удалением сессии.'],
  SESSION_RUNTIME_AUTHORITY_UNPROVEN: ['Полномочия среды сессии не подтверждены', 'ThreadCells не удалось подтвердить остановку всех исторических процессов, поэтому сессия остаётся защищена.'],
  EXIT_PANE_AMBIGUOUS: ['Требуется проверить завершение терминала', 'В окне терминала несколько панелей. Исправьте структуру терминала и повторите корректное завершение.'],
} satisfies Record<KnownReasonCode, [string, string]>

type ErrorKeyPair = readonly [TranslationKey, TranslationKey]

const STATUS_KEYS: Record<number, ErrorKeyPair> = {
  400: ['error.invalid.title', 'error.invalid.body'],
  401: ['error.unauthorized.title', 'error.unauthorized.body'],
  403: ['error.unauthorized.title', 'error.unauthorized.body'],
  404: ['error.notFound.title', 'error.notFound.body'],
  409: ['error.conflict.title', 'error.conflict.body'],
  422: ['error.invalid.title', 'error.invalid.body'],
  423: ['error.locked.title', 'error.locked.body'],
  429: ['error.capacity.title', 'error.capacity.body'],
  500: ['error.server.title', 'error.server.body'],
  502: ['error.providerUnavailable.title', 'error.providerUnavailable.body'],
  503: ['error.serviceUnavailable.title', 'error.serviceUnavailable.body'],
  504: ['error.providerTimeout.title', 'error.providerTimeout.body'],
}

// The stable reason code selects product-safe localized copy. The canonical
// code itself remains unchanged in CaoApiError's technical suffix.
const REASON_KEYS: Record<string, ErrorKeyPair> = {
  WORKTREE_WRITER_LEASE_HELD: ['error.locked.title', 'error.locked.body'],
  WORKTREE_AUTHORITY_UNRECONCILED: ['error.operationUnavailable', 'error.conflict.body'],
  TOTAL_PROVIDER_CAPACITY_EXHAUSTED: ['error.capacity.title', 'error.capacity.body'],
  PROVIDER_EXECUTION_CAPACITY_EXHAUSTED: ['error.capacity.title', 'error.capacity.body'],
  RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED: ['error.capacity.title', 'error.capacity.body'],
  SESSION_PRIMARY_SUPERVISOR_EXISTS: ['error.conflict.title', 'error.conflict.body'],
  PROJECT_SOURCE_NOT_GIT: ['error.conflict.title', 'error.conflict.body'],
  WORK_CONTEXT_REQUEST_CONFLICT: ['error.conflict.title', 'error.conflict.body'],
  WORK_CONTEXT_AUTHORITY_CONFLICT: ['error.conflict.title', 'error.conflict.body'],
  WORK_CONTEXT_AUTHORITY_CHANGED: ['error.conflict.title', 'error.conflict.body'],
  WORK_CONTEXT_PROVIDER_LAUNCH_UNCERTAIN: ['error.conflict.title', 'error.conflict.body'],
  WORK_CONTEXT_CAPACITY_EXHAUSTED: ['error.capacity.title', 'error.capacity.body'],
  RESOURCE_HEALTH_REJECTED: ['error.serviceUnavailable.title', 'error.serviceUnavailable.body'],
  CONTEXT_INVENTORY_UNAVAILABLE: ['error.serviceUnavailable.title', 'error.serviceUnavailable.body'],
  ADMISSION_FENCE_TIMEOUT: ['error.timeout.title', 'error.timeout.body'],
  HEAVY_SLOT_WAIT_TIMEOUT: ['error.timeout.title', 'error.capacity.body'],
  HOUSEKEEPING_PLAN_CHANGED: ['error.conflict.title', 'error.conflict.body'],
  HOUSEKEEPING_BUSY: ['error.locked.title', 'error.locked.body'],
  FULL_CLEANUP_ADMISSION_BUSY: ['error.locked.title', 'error.locked.body'],
  FULL_CLEANUP_NOT_IDLE: ['error.operationUnavailable', 'error.conflict.body'],
  FULL_CLEANUP_IDLE_INVENTORY_UNKNOWN: ['error.operationUnavailable', 'error.serviceUnavailable.body'],
  OPERATOR_AUTH_NOT_CONFIGURED: ['error.unauthorized.title', 'error.unauthorized.body'],
  TERMINAL_RUNTIME_ACTIVE: ['error.operationUnavailable', 'error.conflict.body'],
  TERMINAL_EXIT_PENDING: ['error.conflict.title', 'error.conflict.body'],
  TERMINAL_DEATH_UNCONFIRMED: ['error.operationUnavailable', 'error.conflict.body'],
  TERMINAL_RUNTIME_AUTHORITY_UNCERTAIN: ['error.operationUnavailable', 'error.conflict.body'],
  TERMINAL_IDENTITY_CHANGED: ['error.conflict.title', 'error.conflict.body'],
  TERMINAL_RUNTIME_NOT_WRITABLE: ['error.operationUnavailable', 'error.conflict.body'],
  WORKFLOW_INPUT_IDEMPOTENCY_CONFLICT: ['error.conflict.title', 'error.conflict.body'],
  WORKFLOW_INPUT_NO_LONGER_EXECUTABLE: ['error.operationUnavailable', 'error.conflict.body'],
  TERMINAL_WORKTREE_PROTECTED: ['error.operationUnavailable', 'error.conflict.body'],
  SESSION_RUNTIME_ACTIVE: ['error.operationUnavailable', 'error.conflict.body'],
  SESSION_RUNTIME_AUTHORITY_UNPROVEN: ['error.operationUnavailable', 'error.conflict.body'],
  EXIT_PANE_AMBIGUOUS: ['error.exitPaneAmbiguous.title', 'error.exitPaneAmbiguous.body'],
}

function localizedPair(pair: ErrorKeyPair): [string, string] {
  const locale = readStoredAppLocale()
  return [translate(locale, pair[0]), translate(locale, pair[1])]
}

export function normalizeApiError(status: number, body: ApiFailureBody | null, statusText = ''): CaoApiError {
  const structuredDetail = body?.detail && typeof body.detail === 'object' ? body.detail as ApiFailureBody : null
  const reasonValue = body?.reason_code ?? structuredDetail?.reason_code
  const reasonCode = typeof reasonValue === 'string' && reasonValue.trim() ? reasonValue.trim() : undefined
  const diagnosticValue = body?.diagnostic_id ?? structuredDetail?.diagnostic_id
  const diagnosticId = typeof diagnosticValue === 'string' && /^[0-9a-f]{32}$/.test(diagnosticValue) ? diagnosticValue : undefined
  const locale = readStoredAppLocale()
  const translated = (reasonCode && REASON_KEYS[reasonCode] && localizedPair(REASON_KEYS[reasonCode])) || (STATUS_KEYS[status] && localizedPair(STATUS_KEYS[status]))
  const knownReasonCode = reasonCode && reasonCode in REASON_COPY ? reasonCode as KnownReasonCode : undefined
  const reasonCopy = knownReasonCode
    ? locale === 'ru'
      ? REASON_COPY_RU[knownReasonCode]
      : REASON_COPY[knownReasonCode]
    : undefined
  const [title, description] = reasonCopy || translated || [translate(locale, 'error.generic.title'), translate(locale, 'error.generic.body')]
  return new CaoApiError(title, description, status, reasonCode, diagnosticId)
}

async function fetchJSON<T>(url: string, opts?: FetchJsonOptions): Promise<T> {
  const controller = new AbortController()
  const externalSignal = opts?.signal
  const abortFromCaller = () => controller.abort(externalSignal?.reason)
  if (externalSignal?.aborted) abortFromCaller()
  else externalSignal?.addEventListener('abort', abortFromCaller, { once: true })
  const timeoutMs = opts?.timeoutMs === undefined ? 10000 : opts.timeoutMs
  let timedOut = false
  const timeout = timeoutMs === null ? undefined : setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const { timeoutMs: _timeoutMs, timeoutError, ...requestOptions } = opts || {}
  try {
    const res = await fetch(`${BASE}${url}`, { ...requestOptions, signal: controller.signal })
    if (!res.ok) {
      const error = await res.json().catch(() => null) as ApiFailureBody | null
      throw normalizeApiError(res.status, error, res.statusText)
    }
    return res.json()
  } catch (reason) {
    if (timedOut) {
      const locale = readStoredAppLocale()
      const copy = timeoutError || {
        title: translate(locale, 'error.timeout.title'),
        description: translate(locale, 'error.timeout.body'),
        reasonCode: 'REQUEST_TIMEOUT',
      }
      throw new CaoApiError(copy.title, copy.description, 408, copy.reasonCode)
    }
    if (reason instanceof CaoApiError || (reason as { name?: string })?.name === 'AbortError') throw reason
    const locale = readStoredAppLocale()
    throw new CaoApiError(
      translate(locale, 'error.generic.title'),
      translate(locale, 'error.generic.body'),
      0,
      'REQUEST_NETWORK_ERROR',
    )
  } finally {
    externalSignal?.removeEventListener('abort', abortFromCaller)
    if (timeout !== undefined) clearTimeout(timeout)
  }
}

function planningNetworkError(reason: unknown, preview: boolean): never {
  if (reason instanceof CaoApiError && reason.reasonCode !== 'REQUEST_NETWORK_ERROR') throw reason
  if ((reason as { name?: string })?.name === 'AbortError') throw reason
  const locale = readStoredAppLocale()
  throw new CaoApiError(
    translate(locale, preview ? 'error.previewNetwork.title' : 'error.planNetwork.title'),
    translate(locale, 'error.planNetwork.body'),
    0,
    'HOUSEKEEPING_PLAN_NETWORK_ERROR',
  )
}

export interface Session {
  id: string
  name: string
  status: string
  created_at: string | null
}

export interface UiOverview {
  sessions: number
  agents: number
  active: number
  waiting: number
  owner_gate: number
  cancelled: number
  completed: number
}

export interface SessionSummary extends Session {
  agent_count: number
  active_agent_count: number
  workflow_counts: Record<string, number>
  activity_counts: Record<string, number>
  project_name: string | null
  last_active: string | null
  first_agent: SessionBoundaryAgent | null
  last_agent: SessionBoundaryAgent | null
}

export interface SessionBoundaryAgent {
  id: string
  activity: string | null
  execution_state: string | null
  lifecycle: string | null
  workflow_state: string | null
  workflow_reason: string | null
}

export type TerminalLifecycle = 'starting' | 'running' | 'exit_pending' | 'exited' | 'recovery_fenced'

export interface AgentSummary {
  id: string
  name: string
  provider: string
  session_id: string
  session_name: string
  agent_profile: string | null
  activity: string | null
  execution_state: string | null
  lifecycle: TerminalLifecycle | null
  workflow_state: string | null
  workflow_status: string | null
  workflow_reason: string | null
  provider_outcome_code?: string | null
  provider_outcome_detail?: string | null
  assignment_status: string | null
  result_status: string | null
  delivery_status: string | null
  context_role: string | null
  launch_worktree: string | null
  managed_worktree_kind: string | null
  managed_worktree_commit: string | null
  managed_worktree_branch: string | null
  writable_work_context_id?: string | null
  writer_authority_generation?: string | null
  workspace_classification?: 'managed_isolated' | 'legacy_shared_root' | null
  projectId: string | null
  project_name: string | null
  project_path: string | null
  creation_order: number
  last_active: string | null
}

export interface PageResult<T> {
  items: T[]
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

export interface AgentSummaryPage extends PageResult<AgentSummary> {
  facets: { activities: string[]; workflow_states: string[]; profiles: string[] }
}

export interface Terminal {
  id: string
  name: string
  provider: string
  session_name: string
  agent_profile: string | null
  status: string | null
  execution_state?: 'ready' | 'processing' | 'queued_provider_execution' | 'waiting_child_retirement' | 'waiting_resource_recovery' | 'waiting_runtime_recovery' | 'waiting_workflow_continuation' | 'exited' | null
  execution_wait_reason?: 'provider_capacity' | 'child_retirement' | 'resource_health' | 'runtime_recovery' | 'workflow_continuation' | null
  lifecycle?: TerminalLifecycle | null
  workflow_state?: 'open' | 'active' | 'waiting' | 'recoverable' | 'result_ready' | 'owner_gate' | 'completed' | 'incomplete' | 'failed' | 'cancelled' | null
  workflow_status?: string | null
  workflow_reason?: string | null
  provider_outcome_code?: string | null
  provider_outcome_detail?: string | null
  assignment_status?: string | null
  result_status?: string | null
  delivery_status?: string | null
  context_role?: 'supervisor' | 'work' | null
  launch_worktree?: string | null
  managed_worktree_kind?: 'supervisor' | 'task' | 'reviewer' | null
  managed_worktree_commit?: string | null
  managed_worktree_branch?: string | null
  writable_work_context_id?: string | null
  writer_authority_generation?: string | null
  workspace_classification?: 'managed_isolated' | 'legacy_shared_root' | null
  last_active: string | null
}

export interface ExitTerminalResponse {
  success: boolean
  lifecycle: 'exited' | 'exit_pending' | 'recovery_fenced'
  outcome: 'command_delivered' | 'already_exited' | 'exit_pending'
  message: string
  command_delivered: boolean
}

export interface SessionDetail {
  session: Session
  terminals: TerminalMeta[]
}

export interface TerminalMeta {
  id: string
  tmux_session: string
  tmux_window: string
  provider: string
  agent_profile: string | null
  last_active: string | null
  lifecycle?: TerminalLifecycle | null
  project_id?: string | null
  project_name?: string | null
  project_path?: string | null
}

export interface AgentProfileInfo {
  name: string
  description: string
  source: 'built-in' | 'custom' | 'local' | 'kiro' | 'q_cli'
  enabled?: boolean
  built_in?: boolean
  revision_id?: string
  execution_mode?: 'orchestrator' | 'owner_executor' | 'executor' | 'reviewer'
  owner_authorization_required?: boolean
  document?: Record<string, unknown>
}

export interface AgentDirsSettings {
  agent_dirs: Record<string, string>
  extra_dirs: string[]
}

export interface RuntimeBranding {
  title: string
  subtitle: string
  logoUrl: string
  customLogo: boolean
}

export interface TelegramSettings {
  schema_version: 1
  enabled: boolean
  chat_id: string | null
  message_thread_id: number | null
  token_configured: boolean
  token_state: 'missing' | 'configured' | 'invalid'
  configuration_state: 'not_configured' | 'invalid' | 'disabled' | 'enabled'
  last_result: 'connection_ok' | 'connection_failed' | 'test_sent' | 'test_failed' | 'not_configured' | null
  last_result_at: string | null
  updated_at: string | null
}

export interface OrchestrationCapacity {
  resource_state: 'GREEN' | 'YELLOW' | 'RED'
  reasons: string[]
  resident_supervisors: { active: number; limit: number; available: number; draining?: boolean; certain: boolean }
  provider_executions: { active: number; limit: number; available: number; draining?: boolean; certain: boolean }
  /** Compatibility alias; carries provider execution semantics. */
  provider_contexts?: { active: number; limit: number; available: number; certain: boolean }
  work_contexts: { active: number; limit: number; available: number; draining?: boolean; certain: boolean }
  heavy_executions: { active: number; limit: number; available: number; draining?: boolean; waiting: number | null }
  memory: { available_mib: number; swap_total_mib: number; swap_free_mib: number }
  root_disk: { state?: 'GREEN' | 'YELLOW' | 'RED' | 'CRITICAL'; used_percent: number; free_gib: number }
  memory_pressure: { some_avg10: number; full_avg10: number }
  cpu_load: { one_minute: number; cpu_count: number }
  housekeeping: { ok?: boolean; warnings?: string[] } | null
}

export interface InboxMessage {
  id: string
  sender_id: string
  receiver_id: string
  message: string
  status: 'pending' | 'delivered' | 'failed'
  result_id?: string | null
  kind?: 'message' | 'delegation_result_notice'
  superseded_at?: string | null
  created_at: string | null
}

export interface DelegationResult {
  id: string
  delegation_kind: 'assign' | 'handoff'
  status: 'awaiting' | 'complete' | 'incomplete' | 'cancelled'
  delivery_status?: string
  authorship: string
  document: { summary?: string; body_markdown?: string; changed_files?: string[]; checks?: { command: string; outcome: string }[]; risks?: string[]; blockers?: string[] } | null
  created_at: string | null
  finalized_at: string | null
}

export interface Flow {
  name: string
  file_path: string
  schedule: string
  agent_profile: string
  provider: string
  script: string | null
  last_run: string | null
  next_run: string | null
  enabled: boolean
  prompt_template: string | null
  projectId?: string | null
  project_name?: string | null
  project_path?: string | null
}

export interface Project {
  projectId: string
  name: string
  path: string
  description: string | null
  isDefault: boolean
  created_at?: string | null
  updated_at?: string | null
}

export type ProviderAvailability =
  | 'INSTALLED_AND_READY'
  | 'INSTALLED_NOT_AUTHENTICATED'
  | 'INSTALLED_BUT_UNHEALTHY'
  | 'NOT_INSTALLED'
  | 'UNKNOWN'

export interface ProviderRuntimeInfo {
  installed: boolean
  available?: boolean
  availability?: ProviderAvailability
  state?: string
  authentication?: string
  version?: string | null
  reason_code?: string | null
}

export interface ProviderInfo extends ProviderRuntimeInfo {
  name: string
  binary: string | null
  adapter_available?: boolean
  capabilities?: Record<string, string>
}

export interface RegistryRecord {
  profile_id?: string
  config_id?: string
  display_name: string
  description?: string
  enabled: boolean
  built_in: boolean
  revision_id: string
  revision_number: number
  fingerprint: string
  document: Record<string, any>
  runtime?: ProviderRuntimeInfo
}

export interface ProviderSettings {
  api_version: string
  entry_point_group: string
  adapters: Array<Record<string, any> & { runtime?: ProviderRuntimeInfo; adapter_available?: boolean }>
  configurations: RegistryRecord[]
  load_failures: Array<Record<string, string>>
}

export interface HousekeepingSettings {
  schema_version: 1
  policy: Record<string, Record<string, boolean | number>>
  schedule: Record<'frequent' | 'weekly' | 'pressure', string>
  updated_at?: string | null
}

export type HousekeepingMode = 'frequent' | 'weekly' | 'pressure'

export interface HousekeepingCandidate {
  canonical_identity: string
  category: string
  action: 'preserve' | 'compress' | 'delete' | 'terminate' | 'prune' | 'retire'
  bytes?: number
  estimated_reclaim_bytes: number
  retention_reason: string
  protection_reason: string | null
  resource_kind?: string
}

export interface HousekeepingClassSummary {
  candidate_count: number
  actionable_count: number
  reclaimable_bytes: number
  preserved_count: number
  preserved_bytes: number
  protection_reasons: Record<string, number>
}

export interface HousekeepingPlan {
  schema_version: number
  plan_id: string
  generated_at: number
  mode: HousekeepingMode
  root: string
  reclaimable_bytes: number
  class_summaries?: Record<string, HousekeepingClassSummary>
  warnings: string[]
  candidates: HousekeepingCandidate[]
}

export interface FullCleanupIdleGate {
  eligible: boolean
  reason_code: string | null
  blockers: Array<{ terminal_id: string; reason_code: string }>
  ready_agents: number
  exited_agents: number
}

export interface FullCleanupPlan extends Omit<HousekeepingPlan, 'mode'> {
  mode: 'full'
  idle_gate: FullCleanupIdleGate
  release_state: {
    metadata_certain: boolean
    active_release: string | null
    active_release_candidates: string[]
    protected_non_active_releases: number
    active_only_expected: boolean
    releases_to_delete: number
    rollback_releases_to_delete: number
    rollback_available: boolean
  }
}

export interface OwnerLaunchGrant {
  launch_id: string
  grant: string
  expires_in_seconds: number
}

export interface RecoveryTakeoverPreview {
  eligible: boolean
  reason_code: string | null
  runtime_absent: boolean | null
  consequence: 'OLD_SUPERVISOR_PERMANENTLY_LOSES_WRITER_AUTHORITY'
  terminal: null | {
    id: string
    session_id: string
    tmux_session: string
    tmux_window: string
    project_id: string
    project_name: string | null
    project_path: string | null
    launch_worktree: string
    runtime_lifecycle: TerminalLifecycle
    runtime_generation: string
    writer_authority_generation: string
  }
  worktree: null | { state: 'clean' | 'dirty' | 'unknown'; dirty: boolean | null; reason_code: string | null }
}

export interface RecoveryTakeoverCapability {
  terminal_id: string
  eligible: boolean
  reason_code: string | null
}

export interface RecoveryTakeover {
  id: string
  request_id: string
  old_terminal_id: string
  new_terminal_id: string
  state: 'claimed' | 'fenced' | 'dispatching' | 'admitted' | 'completed' | 'dispatch_uncertain' | 'failed'
  failure_reason: string | null
}

export interface OperatorSessionStatus {
  configured: boolean
  configuration_state?: 'missing' | 'invalid' | 'ready'
  authenticated: boolean
  expires_in_seconds: number
  session_ttl_seconds: number
  verifier_reference: string
}

export interface UsageAggregate {
  id?: string | null
  label?: string | null
  /** An unreconciled historical record; it was not merged by reusable name. */
  legacy?: boolean
  provider_run_count: number
  input_tokens: number | null
  cached_input_tokens: number | null
  cache_write_input_tokens: number | null
  output_tokens: number | null
  reasoning_output_tokens: number | null
  total_tokens: number | null
}

export interface UsageStatistics {
  label: string
  global: UsageAggregate
  terminals: UsageAggregate[]
  sessions: UsageAggregate[]
  projects: UsageAggregate[]
  providers: UsageAggregate[]
  profiles: UsageAggregate[]
}

export const api = {
  // Agent Profiles & Providers
  listProfiles: () => fetchJSON<AgentProfileInfo[]>('/agents/profiles'),
  listProviders: () => fetchJSON<ProviderInfo[]>('/agents/providers'),

  // Settings
  getAgentDirs: () => fetchJSON<AgentDirsSettings>('/settings/agent-dirs'),
  setAgentDirs: (data: { agent_dirs?: Record<string, string>; extra_dirs?: string[] }) =>
    fetchJSON<AgentDirsSettings>('/settings/agent-dirs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  getOrchestrationCapacity: () =>
    fetchJSON<OrchestrationCapacity>('/settings/orchestration-capacity'),
  updateOrchestrationCapacity: (data: { max_resident_supervisors: number; max_provider_executions: number; max_work_contexts: number; max_heavy_execution_slots: number }) =>
    fetchJSON<OrchestrationCapacity>('/settings/orchestration-capacity', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  getOperatorSession: () => fetchJSON<OperatorSessionStatus>('/operator/session'),
  createOperatorSession: (secret: string) => fetchJSON<{ authenticated: boolean }>('/operator/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ secret }) }),
  deleteOperatorSession: () => fetchJSON<{ revoked: boolean }>('/operator/session', { method: 'DELETE' }),
  createXHighGrant: (data: { agent_profile: string; provider: string; working_directory?: string; requested_session_name?: string; project_id?: string; launch_mode: 'new_session' | 'existing_session' | 'recovery_takeover'; target_terminal_id?: string; expected_authority_generation?: string; expected_runtime_generation?: string; confirmed: true }) =>
    fetchJSON<OwnerLaunchGrant>('/operator/xhigh-grants', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  getTelegramSettings: () => fetchJSON<TelegramSettings>('/api/v1/telegram'),
  updateTelegramSettings: (data: { enabled: boolean; chat_id: string | null; message_thread_id: number | null; bot_token: string | null; clear_bot_token?: boolean }) =>
    fetchJSON<TelegramSettings>('/api/v1/telegram', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  checkTelegramConnection: () => fetchJSON<{ ok: boolean; status: string; reason_code?: string }>('/api/v1/telegram/check', { method: 'POST' }),
  sendTelegramTest: () => fetchJSON<{ ok: boolean; status: string; reason_code?: string }>('/api/v1/telegram/test', { method: 'POST' }),
  listRegistryProfiles: (includeDisabled = true) => fetchJSON<RegistryRecord[]>(`/api/v1/profiles?include_disabled=${includeDisabled}`),
  validateProfile: (document: Record<string, unknown>) => fetchJSON<{ valid: boolean; issues: Array<Record<string, string>>; document?: Record<string, unknown> }>('/api/v1/profiles/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document }) }),
  importProfile: (document: Record<string, unknown>, duplicate_builtin = false) => fetchJSON<RegistryRecord>('/api/v1/profiles/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document, duplicate_builtin }) }),
  previewProfile: (profileId: string) => fetchJSON<Record<string, any>>(`/api/v1/profiles/${encodeURIComponent(profileId)}/preview`),
  exportProfile: (profileId: string) => fetchJSON<{ document: Record<string, unknown> }>(`/api/v1/profiles/${encodeURIComponent(profileId)}/export`),
  setProfileEnabled: (profileId: string, enabled: boolean) => fetchJSON<RegistryRecord>(`/api/v1/profiles/${encodeURIComponent(profileId)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) }),
  getProfileAiPrompt: () => fetchJSON<{ prompt: string }>('/api/v1/profiles/ai-prompt'),
  getProviderSettings: () => fetchJSON<ProviderSettings>('/api/v1/providers'),
  preflightProvider: (configId: string) => fetchJSON<Record<string, any>>(`/api/v1/providers/${encodeURIComponent(configId)}/preflight`, { method: 'POST' }),
  exportProvider: (configId: string) => fetchJSON<{ document: Record<string, unknown>; redacted: boolean }>(`/api/v1/providers/${encodeURIComponent(configId)}/export`),
  importProvider: (document: Record<string, unknown>) => fetchJSON<RegistryRecord>('/api/v1/providers/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document }) }),
  getProviderAiPrompt: () => fetchJSON<{ prompt: string }>('/api/v1/providers/ai-prompt'),
  getHousekeepingSettings: () => fetchJSON<HousekeepingSettings>('/api/v1/housekeeping'),
  updateHousekeepingSettings: (data: HousekeepingSettings) => fetchJSON<HousekeepingSettings>('/api/v1/housekeeping', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  getHousekeepingPlan: async (mode: HousekeepingMode, signal?: AbortSignal) => {
    const locale = readStoredAppLocale()
    try {
      return await fetchJSON<HousekeepingPlan>(`/api/v1/housekeeping/plan?mode=${mode}`, {
        signal,
        timeoutMs: HOUSEKEEPING_PLAN_TIMEOUT_MS,
        timeoutError: {
          title: translate(locale, 'error.planTimeout.title'),
          description: translate(locale, 'error.planTimeout.body'),
          reasonCode: 'HOUSEKEEPING_PLAN_TIMEOUT',
        },
      })
    } catch (reason) {
      planningNetworkError(reason, false)
    }
  },
  runHousekeeping: (mode: HousekeepingMode, dry_run: boolean, expectedPlanId?: string) => fetchJSON<Record<string, any>>('/api/v1/housekeeping/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, dry_run, expected_plan_id: expectedPlanId }), timeoutMs: null }),
  getFullCleanupPlan: async (signal?: AbortSignal) => {
    const locale = readStoredAppLocale()
    try {
      return await fetchJSON<FullCleanupPlan>('/api/v1/housekeeping/full-cleanup/plan', {
        signal,
        timeoutMs: HOUSEKEEPING_PLAN_TIMEOUT_MS,
        timeoutError: {
          title: translate(locale, 'error.previewTimeout.title'),
          description: translate(locale, 'error.planTimeout.body'),
          reasonCode: 'HOUSEKEEPING_PLAN_TIMEOUT',
        },
      })
    } catch (reason) {
      planningNetworkError(reason, true)
    }
  },
  runFullCleanup: (expectedPlanId: string) => fetchJSON<Record<string, any>>('/api/v1/housekeeping/full-cleanup/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_plan_id: expectedPlanId, confirmed: true }), timeoutMs: null }),
  getHousekeepingReport: () => fetchJSON<Record<string, any>>('/api/v1/housekeeping/report'),
  getUsageStatistics: () => fetchJSON<UsageStatistics>('/usage/statistics'),
  getBranding: () => fetchJSON<RuntimeBranding>('/settings/branding'),
  updateBranding: (data: { title?: string; subtitle?: string }) => fetchJSON<RuntimeBranding>('/settings/branding', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  uploadBrandingLogo: (file: File) => fetchJSON<RuntimeBranding>('/settings/branding/logo', {
    method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file,
  }),
  resetBrandingLogo: () => fetchJSON<RuntimeBranding>('/settings/branding/logo/reset', { method: 'POST' }),

  // Sessions
  getUiOverview: (signal?: AbortSignal) => fetchJSON<UiOverview>('/ui/overview', { signal }),
  listSessionSummaries: (params: { limit?: number; offset?: number; query?: string } = {}, signal?: AbortSignal) => {
    const search = new URLSearchParams()
    if (params.limit !== undefined) search.set('limit', String(params.limit))
    if (params.offset !== undefined) search.set('offset', String(params.offset))
    if (params.query) search.set('query', params.query)
    return fetchJSON<PageResult<SessionSummary>>(`/ui/sessions${search.size ? `?${search}` : ''}`, { signal })
  },
  listAgentSummaries: (params: { limit?: number; offset?: number; sessionId?: string; query?: string; activities?: string[]; workflowStates?: string[]; profiles?: string[]; homeFilter?: string | null } = {}, signal?: AbortSignal) => {
    const search = new URLSearchParams()
    if (params.limit !== undefined) search.set('limit', String(params.limit))
    if (params.offset !== undefined) search.set('offset', String(params.offset))
    if (params.sessionId) search.set('session_id', params.sessionId)
    if (params.query) search.set('query', params.query)
    if (params.activities?.length) search.set('activity', params.activities.join(','))
    if (params.workflowStates?.length) search.set('workflow_state', params.workflowStates.join(','))
    if (params.profiles?.length) search.set('profile', params.profiles.join(','))
    if (params.homeFilter) search.set('home_filter', params.homeFilter)
    return fetchJSON<AgentSummaryPage>(`/ui/agents${search.size ? `?${search}` : ''}`, { signal })
  },
  listSessions: () => fetchJSON<Session[]>('/sessions'),
  getSession: (name: string) => fetchJSON<SessionDetail>(`/sessions/${encodeURIComponent(name)}`),
  getSessionWorkingDirectory: (name: string) =>
    fetchJSON<{ working_directory: string | null }>(`/sessions/${encodeURIComponent(name)}/working-directory`),
  createSession: (provider: string, agentProfile: string, sessionName?: string, workingDirectory?: string, projectId?: string, ownerGrant?: OwnerLaunchGrant, workContextRequestId?: string) =>
    // Session startup can outlive a browser request. Keep this request owned by
    // the UI until it settles so the backend cancellation reconciliation only
    // runs for genuine caller cancellation (navigation, disconnect, etc.).
    fetchJSON<Terminal>(`/sessions?provider=${encodeURIComponent(provider)}&agent_profile=${encodeURIComponent(agentProfile)}${sessionName ? `&session_name=${encodeURIComponent(sessionName)}` : ''}${workingDirectory ? `&working_directory=${encodeURIComponent(workingDirectory)}` : ''}${projectId ? `&projectId=${encodeURIComponent(projectId)}` : ''}${ownerGrant ? `&owner_grant_launch_id=${encodeURIComponent(ownerGrant.launch_id)}` : ''}${workContextRequestId ? `&workContextRequestId=${encodeURIComponent(workContextRequestId)}` : ''}`, { method: 'POST', headers: ownerGrant ? { 'X-ThreadCells-Owner-Grant': ownerGrant.grant } : undefined, timeoutMs: null }),
  deleteSession: (name: string) => fetchJSON<{ success: boolean; deleted: string[]; errors: any[] }>(`/sessions/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  // Terminals
  getTerminalStatus: (id: string) => fetchJSON<Terminal>(`/terminals/${id}`),
  getTerminalOutput: (id: string, mode: 'full' | 'last' = 'full') =>
    fetchJSON<{ output: string; mode: string; availability?: 'available' | 'unavailable'; reason_code?: string | null }>(`/terminals/${id}/output?mode=${mode}`),
  sendInput: (id: string, message: string) =>
    fetchJSON<{ success: boolean }>(`/terminals/${id}/input?message=${encodeURIComponent(message)}`, { method: 'POST' }),
  sendWorkflowInput: (id: string, message: string, requestId: string) =>
    fetchJSON<WorkflowInputResponse>(`/terminals/${id}/workflow-input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, request_id: requestId }),
    }),
  uploadTerminalImage: (id: string, image: File) =>
    fetchJSON<{ path: string }>(`/terminals/${id}/attachments/image`, {
      method: 'POST',
      headers: { 'Content-Type': image.type },
      body: image,
      timeoutMs: 30000,
    }),
  uploadTerminalFile: (id: string, file: File) =>
    fetchJSON<{ path: string }>(`/terminals/${id}/attachments/file`, {
      method: 'POST',
      // HTTP header values are ASCII. Preserve the complete browser filename
      // across the boundary without relying on browser-specific Unicode header handling.
      headers: { 'Content-Type': file.type || 'application/octet-stream', 'X-Terminal-Filename': encodeURIComponent(file.name) },
      body: file,
      timeoutMs: 30000,
    }),
  exitTerminal: (id: string) =>
    fetchJSON<ExitTerminalResponse>(`/terminals/${id}/exit`, { method: 'POST' }),
  deleteTerminal: (id: string) => fetchJSON<{ success: boolean }>(`/terminals/${id}`, { method: 'DELETE' }),
  getWorkingDirectory: (id: string) =>
    fetchJSON<{ working_directory: string | null }>(`/terminals/${id}/working-directory`),
  getRecoveryTakeoverPreview: (id: string) =>
    fetchJSON<RecoveryTakeoverPreview>(`/terminals/${id}/recovery-takeover/preview`),
  getRecoveryTakeoverCapabilities: (terminalIds: string[], signal?: AbortSignal) =>
    fetchJSON<{ capabilities: RecoveryTakeoverCapability[] }>('/recovery-takeovers/capabilities', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ terminal_ids: terminalIds }),
      signal,
    }),
  createRecoveryTakeover: (id: string, data: { request_id: string; expected_authority_generation: string; expected_runtime_generation: string; agent_profile: string; provider: string; owner_grant_launch_id: string }, ownerGrant: OwnerLaunchGrant) =>
    fetchJSON<RecoveryTakeover>(`/terminals/${id}/recovery-takeover`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ThreadCells-Owner-Grant': ownerGrant.grant }, body: JSON.stringify(data), timeoutMs: null }),
  addTerminalToSession: (sessionName: string, provider: string, agentProfile: string, workingDirectory?: string, projectId?: string, ownerGrant?: OwnerLaunchGrant) =>
    fetchJSON<Terminal>(`/sessions/${encodeURIComponent(sessionName)}/terminals?provider=${encodeURIComponent(provider)}&agent_profile=${encodeURIComponent(agentProfile)}${workingDirectory ? `&working_directory=${encodeURIComponent(workingDirectory)}` : ''}${projectId ? `&projectId=${encodeURIComponent(projectId)}` : ''}${ownerGrant ? `&owner_grant_launch_id=${encodeURIComponent(ownerGrant.launch_id)}` : ''}`, { method: 'POST', headers: ownerGrant ? { 'X-ThreadCells-Owner-Grant': ownerGrant.grant } : undefined, timeoutMs: 90000 }),

  // Projects
  listProjects: () => fetchJSON<Project[]>('/projects'),
  createProject: (data: { name: string; path: string; description?: string; isDefault?: boolean; createDirectory?: boolean }) =>
    fetchJSON<Project>('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  setDefaultProject: (projectId: string) => fetchJSON<Project>(`/projects/${encodeURIComponent(projectId)}/default`, { method: 'POST' }),
  deleteProject: (projectId: string) => fetchJSON<{ success: boolean }>(`/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  updateProject: (projectId: string, data: { name?: string; path?: string; description?: string | null; isDefault?: boolean }) =>
    fetchJSON<Project>(`/projects/${encodeURIComponent(projectId)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),

  // Inbox
  getInboxMessages: (terminalId: string, limit?: number, status?: string, signal?: AbortSignal) =>
    fetchJSON<InboxMessage[]>(`/terminals/${terminalId}/inbox/messages?limit=${limit || 50}${status ? `&status=${status}` : ''}`, { signal }),
  sendInboxMessage: (receiverId: string, senderId: string, message: string) =>
    fetchJSON<{ success: boolean }>(`/terminals/${receiverId}/inbox/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sender_id: senderId, message }),
    }),
  getDelegationResult: (id: string) => fetchJSON<DelegationResult>(`/delegation-results/${encodeURIComponent(id)}`),
  listDelegationResults: (params?: { terminalId?: string; sessionName?: string; status?: string }, signal?: AbortSignal) => {
    const search = new URLSearchParams()
    if (params?.terminalId) search.set('terminal_id', params.terminalId)
    if (params?.sessionName) search.set('session_name', params.sessionName)
    if (params?.status) search.set('status', params.status)
    return fetchJSON<DelegationResult[]>(`/delegation-results${search.size ? `?${search}` : ''}`, { signal })
  },

  // Flows
  listFlows: (signal?: AbortSignal) => fetchJSON<Flow[]>('/flows', { signal }),
  createFlow: (data: { name: string; schedule: string; agent_profile: string; provider?: string; prompt_template: string; projectId?: string }) =>
    fetchJSON<Flow>('/flows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      timeoutMs: 30000,
    }),
  deleteFlow: (name: string) => fetchJSON<{ success: boolean }>(`/flows/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  enableFlow: (name: string) => fetchJSON<{ success: boolean }>(`/flows/${encodeURIComponent(name)}/enable`, { method: 'POST' }),
  disableFlow: (name: string) => fetchJSON<{ success: boolean }>(`/flows/${encodeURIComponent(name)}/disable`, { method: 'POST' }),
  runFlow: (name: string) => fetchJSON<{ executed: boolean }>(`/flows/${encodeURIComponent(name)}/run`, { method: 'POST', timeoutMs: 90000 }),
}
