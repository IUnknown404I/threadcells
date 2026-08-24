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

export function ExecutionMesh({ locale = 'en' }: { locale?: 'en' | 'ru' }) {
  const ru = locale === 'ru'
  const localizedNodes: MeshNode[] = ru ? [
    { id: 'owner', label: 'ВЛАДЕЛЕЦ', meta: 'задача / полномочия', state: 'ГОТОВ', x: 8, y: 48 },
    { id: 'supervisor', label: 'СУПЕРВИЗОР', meta: 'корень workflow', state: 'В РАБОТЕ', x: 34, y: 48 },
    { id: 'worker-03', label: 'ИСПОЛНИТЕЛЬ 03', meta: 'контекст 02 / 02', state: 'В РАБОТЕ', x: 61, y: 17 },
    { id: 'worker-02', label: 'ИСПОЛНИТЕЛЬ 02', meta: 'контекст 01 / 02', state: 'ГОТОВО', x: 61, y: 73 },
    { id: 'reviewer', label: 'РЕВЬЮЕР', meta: 'точка приёмки', state: 'ГОТОВ', x: 83, y: 34 },
    { id: 'result', label: 'СОХРАНЁННЫЙ РЕЗУЛЬТАТ', meta: 'результат · 8A17', state: 'СОХРАНЁН', x: 83, y: 73 },
  ] : nodes
  const meshCopy = ru ? {
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
