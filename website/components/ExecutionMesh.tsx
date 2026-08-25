type NodeId = 'owner' | 'supervisor' | 'worker-02' | 'worker-03' | 'reviewer' | 'result'
type MeshNode = { id: NodeId; label: string; meta: string; state: string; x: number; y: number }

const nodes: MeshNode[] = [
  { id: 'owner', label: 'OWNER', meta: 'intent / authority', state: 'READY', x: 8, y: 48 },
  { id: 'supervisor', label: 'SUPERVISOR', meta: 'workflow root', state: 'RUNNING', x: 34, y: 48 },
  { id: 'worker-03', label: 'WORKER 03', meta: 'context 02 / 02', state: 'RUNNING', x: 61, y: 17 },
  { id: 'worker-02', label: 'WORKER 02', meta: 'context 01 / 02', state: 'COMPLETE', x: 61, y: 73 },
  { id: 'reviewer', label: 'REVIEWER', meta: 'acceptance gate', state: 'READY', x: 83, y: 34 },
  { id: 'result', label: 'DURABLE RESULT', meta: 'result · 8A17', state: 'PERSISTED', x: 83, y: 73 },
]

const lines: Array<[NodeId, NodeId, number]> = [
  ['owner', 'supervisor', 0],
  ['supervisor', 'worker-03', 1],
  ['supervisor', 'worker-02', 1],
  ['worker-03', 'reviewer', 2],
  ['worker-02', 'reviewer', 2],
  ['reviewer', 'result', 3],
  ['result', 'supervisor', 3],
]

const packets: Array<[NodeId, NodeId, string]> = [
  ['owner', 'supervisor', '0s'],
  ['supervisor', 'worker-03', '2.3s'],
  ['result', 'supervisor', '4.7s'],
]

export function ExecutionMesh({ locale = 'en' }: { locale?: Locale }) {
  const ru = locale === 'ru'
  const zh = locale === 'zh-CN'
  const ja = locale === 'ja'
  const es = locale === 'es'
  const pt = locale === 'pt-BR'
  const localizedNodes: MeshNode[] = pt ? [
    { id: 'owner', label: 'PROPRIETÁRIO', meta: 'intenção / autoridade', state: 'PRONTO', x: 8, y: 48 },
    { id: 'supervisor', label: 'SUPERVISOR', meta: 'raiz do fluxo', state: 'EM EXECUÇÃO', x: 34, y: 48 },
    { id: 'worker-03', label: 'EXECUTOR 03', meta: 'contexto 02 / 02', state: 'EM EXECUÇÃO', x: 61, y: 17 },
    { id: 'worker-02', label: 'EXECUTOR 02', meta: 'contexto 01 / 02', state: 'CONCLUÍDO', x: 61, y: 73 },
    { id: 'reviewer', label: 'REVISOR', meta: 'porta de aceitação', state: 'PRONTO', x: 83, y: 34 },
    { id: 'result', label: 'RESULTADO DURÁVEL', meta: 'resultado · 8A17', state: 'PERSISTIDO', x: 83, y: 73 },
  ] : es ? [
    { id: 'owner', label: 'PROPIETARIO', meta: 'intención / autoridad', state: 'LISTO', x: 8, y: 48 },
    { id: 'supervisor', label: 'SUPERVISOR', meta: 'raíz del flujo', state: 'EN EJECUCIÓN', x: 34, y: 48 },
    { id: 'worker-03', label: 'EJECUTOR 03', meta: 'contexto 02 / 02', state: 'EN EJECUCIÓN', x: 61, y: 17 },
    { id: 'worker-02', label: 'EJECUTOR 02', meta: 'contexto 01 / 02', state: 'COMPLETO', x: 61, y: 73 },
    { id: 'reviewer', label: 'REVISOR', meta: 'puerta de aceptación', state: 'LISTO', x: 83, y: 34 },
    { id: 'result', label: 'RESULTADO DURADERO', meta: 'resultado · 8A17', state: 'PERSISTIDO', x: 83, y: 73 },
  ] : ja ? [
    { id: 'owner', label: 'オーナー', meta: '意図 / 権限', state: '準備完了', x: 8, y: 48 },
    { id: 'supervisor', label: 'スーパーバイザー', meta: 'ワークフローの起点', state: '実行中', x: 34, y: 48 },
    { id: 'worker-03', label: 'ワーカー 03', meta: 'コンテキスト 02 / 02', state: '実行中', x: 61, y: 17 },
    { id: 'worker-02', label: 'ワーカー 02', meta: 'コンテキスト 01 / 02', state: '完了', x: 61, y: 73 },
    { id: 'reviewer', label: 'レビュアー', meta: '受け入れゲート', state: '準備完了', x: 83, y: 34 },
    { id: 'result', label: '永続結果', meta: '結果 · 8A17', state: '永続化済み', x: 83, y: 73 },
  ] : zh ? [
    { id: 'owner', label: '所有者', meta: '意图 / 权限', state: '就绪', x: 8, y: 48 },
    { id: 'supervisor', label: '监督者', meta: '工作流根', state: '运行中', x: 34, y: 48 },
    { id: 'worker-03', label: '执行者 03', meta: '上下文 02 / 02', state: '运行中', x: 61, y: 17 },
    { id: 'worker-02', label: '执行者 02', meta: '上下文 01 / 02', state: '已完成', x: 61, y: 73 },
    { id: 'reviewer', label: '审阅者', meta: '验收关卡', state: '就绪', x: 83, y: 34 },
    { id: 'result', label: '持久结果', meta: '结果 · 8A17', state: '已持久化', x: 83, y: 73 },
  ] : ru ? [
    { id: 'owner', label: 'ВЛАДЕЛЕЦ', meta: 'задача / полномочия', state: 'ГОТОВ', x: 8, y: 48 },
    { id: 'supervisor', label: 'СУПЕРВИЗОР', meta: 'корень workflow', state: 'В РАБОТЕ', x: 34, y: 48 },
    { id: 'worker-03', label: 'ИСПОЛНИТЕЛЬ 03', meta: 'контекст 02 / 02', state: 'В РАБОТЕ', x: 61, y: 17 },
    { id: 'worker-02', label: 'ИСПОЛНИТЕЛЬ 02', meta: 'контекст 01 / 02', state: 'ГОТОВО', x: 61, y: 73 },
    { id: 'reviewer', label: 'РЕВЬЮЕР', meta: 'точка приёмки', state: 'ГОТОВ', x: 83, y: 34 },
    { id: 'result', label: 'СОХРАНЁННЫЙ РЕЗУЛЬТАТ', meta: 'результат · 8A17', state: 'СОХРАНЁН', x: 83, y: 73 },
  ] : nodes
  const meshCopy = pt ? {
    title: 'EXECUÇÃO / tm-web-p1', live: 'ATIVO', state: 'Estado ilustrativo do fluxo',
    sequenced: 'FLUXO SEQUENCIADO', supervisor: 'SUPERVISOR', flow: 'intenção → executores → revisor → resultado durável',
    capacity: 'Telemetria ilustrativa de capacidade', resident: 'RESIDENTE', provider: 'PROVEDOR', work: 'TRABALHO', heavy: 'PESADA',
  } : es ? {
    title: 'EJECUCIÓN / tm-web-p1', live: 'ACTIVO', state: 'Estado ilustrativo del flujo',
    sequenced: 'FLUJO SECUENCIADO', supervisor: 'SUPERVISOR', flow: 'intención → ejecutores → revisor → resultado duradero',
    capacity: 'Telemetría ilustrativa de capacidad', resident: 'RESIDENTE', provider: 'PROVEEDOR', work: 'TRABAJO', heavy: 'PESADA',
  } : ja ? {
    title: '実行 / tm-web-p1', live: '稼働中', state: 'ワークフロー状態の例',
    sequenced: 'ワークフローを編成済み', supervisor: 'スーパーバイザー', flow: '意図 → ワーカー → レビュアー → 永続結果',
    capacity: '容量テレメトリの例', resident: '常駐', provider: 'プロバイダー', work: '作業', heavy: '重い実行',
  } : zh ? {
    title: '执行 / tm-web-p1', live: '实时', state: '示意工作流状态',
    sequenced: '工作流已编排', supervisor: '监督者', flow: '意图 → 执行者 → 审阅者 → 持久结果',
    capacity: '示意容量遥测', resident: '常驻', provider: '提供商', work: '工作', heavy: '重型执行',
  } : ru ? {
    title: 'ВЫПОЛНЕНИЕ / tm-web-p1', live: 'АКТИВНО', state: 'Схема процесса',
    sequenced: 'ПРОЦЕСС ВЫСТРОЕН', supervisor: 'СУПЕРВИЗОР', flow: 'задача → исполнители → ревьюер → сохранённый результат',
    capacity: 'Условная телеметрия ёмкости', resident: 'RESIDENT', provider: 'ПРОВАЙДЕР', work: 'РАБОТА', heavy: 'ТЯЖЁЛЫЕ',
  } : {
    title: 'EXECUTION / tm-web-p1', live: 'LIVE', state: 'Illustrative workflow state',
    sequenced: 'WORKFLOW SEQUENCED', supervisor: 'SUPERVISOR', flow: 'intent → workers → reviewer → durable result',
    capacity: 'Illustrative capacity telemetry', resident: 'RESIDENT', provider: 'PROVIDER', work: 'WORK', heavy: 'HEAVY',
  }
  const nodeMap = new Map(localizedNodes.map((node) => [node.id, node]))

  return (
    <section className="mesh-panel" aria-labelledby="mesh-title">
      <div className="mesh-toolbar">
        <div>
          <span className="window-dots" aria-hidden="true"><i /><i /><i /></span>
          <span id="mesh-title">{meshCopy.title}</span>
        </div>
        <span className="mesh-live"><i /> {meshCopy.live}</span>
      </div>
      <div className="mesh-stage" data-phase="sequenced">
        <div className="mesh-grid" aria-hidden="true" />
        <svg className="mesh-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {lines.map(([from, to, phase], index) => {
            const a = nodeMap.get(from)!
            const b = nodeMap.get(to)!
            return <line key={`${from}-${to}`} className={`mesh-line line-${index} line-phase-${phase}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
          })}
          {packets.map(([from, to, begin]) => {
            const a = nodeMap.get(from)!
            const b = nodeMap.get(to)!
            return <circle key={`packet-${from}-${to}`} className="mesh-packet" r="0.6">
              <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.12;0.82;1" dur="2.5s" begin={begin} repeatCount="indefinite" />
              <animateMotion path={`M ${a.x} ${a.y} L ${b.x} ${b.y}`} dur="2.5s" begin={begin} repeatCount="indefinite" />
            </circle>
          })}
        </svg>
        {localizedNodes.map((node) => (
          <div
            key={node.id}
            className={`mesh-node mesh-node-${node.id} is-active ${node.id === 'supervisor' ? 'is-selected' : ''}`}
            style={{ left: `${node.x}%`, top: `${node.y}%` }}
            role="img"
            aria-label={`${node.label}: ${node.state}. ${node.meta}`}
          >
            <span className="node-orbit" aria-hidden="true" />
            <span className="node-core" aria-hidden="true" />
            <span className="node-copy"><strong>{node.label}</strong><small>{node.state}</small></span>
          </div>
        ))}
        <div className="mesh-readout" aria-label={meshCopy.state}>
          <span>{meshCopy.sequenced}</span>
          <strong>{meshCopy.supervisor}</strong>
          <small>{meshCopy.flow}</small>
        </div>
      </div>
      <div className="mesh-capacity" aria-label={meshCopy.capacity}>
        <span><i className="fill-40" />{meshCopy.resident} <strong>2 / 5</strong></span>
        <span><i className="fill-66" />{meshCopy.provider} <strong>2 / 3</strong></span>
        <span><i className="fill-100" />{meshCopy.work} <strong>2 / 2</strong></span>
        <span><i className="fill-0" />{meshCopy.heavy} <strong>0 / 1</strong></span>
      </div>
    </section>
  )
}
import type { Locale } from '@/lib/locales'
