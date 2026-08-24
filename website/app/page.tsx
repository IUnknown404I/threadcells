import { ArrowRight, ArrowUpRight, Book, Check, Eye, Gauge, Github, GitBranch, Layers, Lock, Terminal } from '@/components/Icons'
import { ExecutionMesh } from '@/components/ExecutionMesh'
import { ProductShot } from '@/components/ProductShot'
import { SiteFooter } from '@/components/SiteFooter'
import { SiteHeader } from '@/components/SiteHeader'
import { assetPath, site } from '@/lib/site'
import { localeCopy, type Locale } from '@/lib/locales'
import type { Metadata } from 'next'
import { canonicalUrl } from '@/lib/site'

const capabilities = [
  { icon: <Eye />, id: '01', title: 'Operational truth', copy: 'See what is resident, executing, waiting, draining, blocked, or actually complete—without inferring state from a terminal tab.' },
  { icon: <GitBranch />, id: '02', title: 'Worktree authority', copy: 'Anchor work to trusted projects and managed Git worktrees. Writer leases keep concurrent mutation explicit.' },
  { icon: <Layers />, id: '03', title: 'Durable results', copy: 'Delegated work returns a persisted result that can be delivered, acknowledged, accepted, and retired without losing history.' },
  { icon: <Gauge />, id: '04', title: 'A healthy agent environment', copy: 'ThreadCells watches host pressure and safely cleans eligible runtime, log, cache, build, and release debris while protecting active work and durable history.' },
]

const steps = [
  ['01', 'Create a session', 'Choose a trusted local project and an agent or supervisor profile.'],
  ['02', 'Give it the job', 'A supervisor can delegate coherent work to native CLI workers and reviewers.'],
  ['03', 'Watch the system', 'ThreadCells keeps the workflow moving across model turns while sessions, worktrees, capacity, and host pressure stay visible.'],
  ['04', 'Step in when asked', 'Results return durably. Owner gates pause only for decisions that genuinely need you.'],
]

const jsonLd = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: 'ThreadCells',
  applicationCategory: 'DeveloperApplication',
  operatingSystem: 'Linux',
  description: site.description,
  license: 'https://www.apache.org/licenses/LICENSE-2.0',
  ...(site.siteUrl ? { url: site.siteUrl } : {}),
  ...(site.githubUrl ? { codeRepository: site.githubUrl } : {}),
  featureList: [
    'Persistent native CLI agent sessions',
    'Managed Git worktrees and writer authority',
    'Durable delegated results',
    'Explicit workflow lifecycle and owner gates',
    'Independent execution capacity controls',
    'Protected-set-aware Housekeeping and closed-runtime retirement',
    'Optional installation-global Telegram lifecycle notifications',
  ],
}

export const metadata: Metadata = {
  alternates: { canonical: canonicalUrl('/') || undefined, languages: { en: canonicalUrl('/') || '/', ru: canonicalUrl('/ru') || '/ru/' } },
}

export function LandingPage({ locale = 'en' }: { locale?: Locale }) {
  const copy = localeCopy[locale]
  const ru = locale === 'ru'
  const t = (english: string, russian: string) => ru ? russian : english
  const localizedCapabilities = ru ? [
    { icon: <Eye />, id: '01', title: 'Операционная правда', copy: 'Видьте, что действительно находится в памяти, выполняется, ожидает, завершается, заблокировано или готово.' },
    { icon: <GitBranch />, id: '02', title: 'Полномочия worktree', copy: 'Привязывайте работу к доверенным проектам и управляемым Git worktree.' },
    { icon: <Layers />, id: '03', title: 'Долговременные результаты', copy: 'Делегированная работа возвращает сохранённый результат без потери истории.' },
    { icon: <Gauge />, id: '04', title: 'Здоровая среда агентов', copy: 'ThreadCells следит за нагрузкой хоста и безопасно очищает подходящие артефакты.' },
  ] : capabilities
  const localizedSteps = ru ? [['01', 'Создайте сессию', 'Выберите доверенный локальный проект и профиль агента или супервизора.'], ['02', 'Дайте задачу', 'Супервизор делегирует целостную работу нативным CLI-исполнителям и ревьюерам.'], ['03', 'Наблюдайте за системой', 'ThreadCells ведёт рабочий процесс между модельными ходами, сохраняя видимость сессий, worktree и ёмкости.'], ['04', 'Вмешайтесь по запросу', 'Результаты возвращаются долговременно. Owner gate ждёт только действительно нужных решений.']] : steps
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c') }} />
      <SiteHeader locale={locale} />
      <main id="top" lang={copy.htmlLang}>
        <section className="hero section-shell" aria-labelledby="hero-title">
          <div className="hero-ambient" aria-hidden="true" />
          <div className="hero-copy">
            <p className="eyebrow"><span>{t('SELF-HOSTED', 'САМОСТОЯТЕЛЬНОЕ РАЗВЁРТЫВАНИЕ')}</span> {t('CODING-AGENT OPERATIONS', 'ОПЕРАЦИИ С КОДОВЫМИ АГЕНТАМИ')}</p>
            <h1 id="hero-title">{t('Run coding agents as a system.', 'Запускайте кодовых агентов как систему.')}<br /> <em>{t('Not a pile', 'Не как груду')}</em> {t('of terminals.', 'терминалов.')}</h1>
            <p className="hero-lede">{t('ThreadCells coordinates native CLI agents, keeps open workflows moving across model turns, and maintains the orchestration environment underneath them—on your own Linux host.', 'ThreadCells координирует нативных CLI-агентов, ведёт открытые рабочие процессы между модельными ходами и поддерживает среду оркестрации на вашем Linux-хосте.')}</p>
            <div className="hero-actions">
              <a className="button button-primary" href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> {t('View on GitHub', 'Открыть на GitHub')} <ArrowUpRight /></a>
              <a className="button button-secondary" href={site.docsUrl}><Book /> {t('Read the docs', 'Читать документацию')} <ArrowRight /></a>
              <span className="hero-note"><i /> {t('Start the work. Watch the system. Step in only when it needs you.', 'Запустите работу. Наблюдайте за системой. Вмешивайтесь только когда это нужно.')}</span>
            </div>
          </div>
          <div className="hero-visual"><div className="hero-index" aria-hidden="true"><span>01</span><i /><small>CONTROL / RESULT</small></div><ExecutionMesh /></div>
        </section>

        <section className="signal-strip" aria-label="ThreadCells operating principles">
          {['COORDINATED WORKFLOWS', 'PROTECTED HOUSEKEEPING', 'DURABLE HISTORY', 'LOOPBACK FIRST'].map((signal, index) => (
            <span key={signal}><small>0{index + 1}</small>{signal}<i /></span>
          ))}
        </section>

        <section className="problem section-shell" aria-labelledby="problem-title">
          <div className="section-heading split-heading">
            <p className="eyebrow">{t('THE HARD PART', 'СЛОЖНАЯ ЧАСТЬ')} / 02</p>
            <h2 id="problem-title">{t('Starting more agents is easy.', 'Запускать больше агентов легко.')}<br /><span>{t('Knowing what is true is not.', 'Понять, что происходит на самом деле, — нет.')}</span></h2>
          </div>
          <div className="problem-body">
            <p className="problem-lede">{t('Which process owns the worktree? Which session is only idle—and which one is finished? Is the machine out of provider capacity, work contexts, or heavy execution? Did the reviewer return a result, or just stop printing?', 'Какой процесс владеет worktree? Какая сессия просто простаивает, а какая завершена? Не исчерпана ли ёмкость провайдера, рабочих контекстов или тяжёлых задач? Ревьюер вернул результат или просто перестал писать?')}</p>
            <div className="truth-ledger" role="list" aria-label="Operational questions ThreadCells answers">
              <div role="listitem"><span>tm-83A1</span><strong>SUPERVISOR</strong><em className="state-running">RUNNING</em><small>provider 01 / 03</small></div>
              <div role="listitem"><span>tm-94C2</span><strong>WORKER</strong><em className="state-complete">COMPLETE</em><small>result persisted</small></div>
              <div role="listitem"><span>tm-07F4</span><strong>REVIEWER</strong><em className="state-ready">READY</em><small>context 02 / 02</small></div>
              <div role="listitem"><span>OWNER GATE</span><strong>AUTHORITY</strong><em className="state-gated">GATED</em><small>decision required</small></div>
            </div>
          </div>
        </section>

        <section id="control-plane" className="control-plane section-shell" aria-labelledby="control-plane-title">
          <div className="section-heading product-heading">
            <div><p className="eyebrow">{t('THE CONTROL PLANE', 'ПАНЕЛЬ УПРАВЛЕНИЯ')} / 03</p><h2 id="control-plane-title">{t('One place to see the work,', 'Одно место для работы,')}<br />{t('the host, and the handoff.', 'хоста и передачи результата.')}</h2></div>
            <p>{t('ThreadCells wraps native agent execution in an operational surface: sessions, projects, terminals, profiles, capacity, workflows, documentation, optional global Telegram lifecycle alerts, and current build identity.', 'ThreadCells объединяет нативное выполнение агентов в операционную поверхность: сессии, проекты, терминалы, профили, ёмкость, рабочие процессы, документацию, опциональные глобальные уведомления Telegram и текущую идентичность сборки.')}</p>
          </div>
          <ProductShot
            src="/media/screenshots/threadcells-home.webp"
            alt="ThreadCells Home showing 21 real sessions, 166 agents, aggregate lifecycle counts, and dense session status summaries"
            label="HOME / EXECUTION OVERVIEW"
            detail="Real release system · sensitive material excluded"
            className="product-shot-hero"
            eager
            width={1440}
            height={960}
          />
          <div className="product-annotation annotation-a"><span>01</span><p><strong>Session truth</strong>Provider, profile, lifecycle, project, and active work remain visible.</p></div>
          <div className="product-annotation annotation-b"><span>02</span><p><strong>Operational scale</strong>First, Last, and Total summaries keep large durable histories readable.</p></div>
        </section>

        <section className="capabilities section-shell" aria-labelledby="capabilities-title">
          <div className="section-heading narrow-heading">
            <p className="eyebrow">{t('CORE IDEAS', 'ОСНОВНЫЕ ИДЕИ')} / 04</p>
            <h2 id="capabilities-title">{t('A control room built around', 'Панель управления для')}<br />{t('the failure modes that matter.', 'существенных отказов.')}</h2>
          </div>
          <div className="capability-system">
            <div className="capability-spine" aria-hidden="true"><span>THREAD</span><i /><span>CELLS</span></div>
            {localizedCapabilities.map((item) => (
              <article key={item.id} className="capability-row">
                <span className="capability-number">{item.id}</span>
                <span className="capability-icon">{item.icon}</span>
                <h3>{item.title}</h3>
                <p>{item.copy}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="how-it-works" className="how section-shell" aria-labelledby="how-title">
          <div className="how-heading">
            <p className="eyebrow">{t('WORKFLOW', 'РАБОЧИЙ ПРОЦЕСС')} / 05</p>
            <h2 id="how-title">{t('Intent goes out.', 'Намерение уходит.')}<br /><span>{t('Evidence comes back.', 'Доказательства возвращаются.')}</span></h2>
            <p>{t('The coding agent stays native. ThreadCells makes the surrounding lifecycle explicit.', 'Кодовый агент остаётся нативным. ThreadCells делает окружающий жизненный цикл явным.')}</p>
          </div>
          <ol className="workflow-steps">
            {localizedSteps.map(([number, title, copy]) => (
              <li key={number}><span>{number}</span><i aria-hidden="true" /><div><h3>{title}</h3><p>{copy}</p></div></li>
            ))}
          </ol>
        </section>

        <section className="demo section-shell" aria-labelledby="demo-title">
          <div className="demo-copy">
            <p className="eyebrow">{t('LIVE RELEASE TOUR', 'ТУР ПО РАБОЧЕЙ СИСТЕМЕ')}</p>
            <h2 id="demo-title">{t('See the control plane in motion.', 'Панель управления в действии.')}</h2>
            <p>{t('A short tour through the real release system: dense Home state, an expanded multi-agent session, protected Housekeeping, and independent capacity.', 'Краткий тур по реальной системе релизов: плотная главная панель, развёрнутая мультиагентная сессия, защищённое обслуживание и независимая ёмкость.')}</p>
          </div>
          <div className="demo-frame">
            <div className="shot-chrome" aria-hidden="true"><span className="window-dots"><i /><i /><i /></span><span>THREADCELLS / LIVE RELEASE SYSTEM</span><span>00:14</span></div>
            <video autoPlay controls loop muted playsInline preload="metadata" poster={assetPath('/media/screenshots/threadcells-home.webp')} aria-label="Live ThreadCells release-system tour">
              <source src={assetPath('/media/demo/threadcells-demo.webm')} type="video/webm" />
              <source src={assetPath('/media/demo/threadcells-demo.mp4')} type="video/mp4" />
              The demo video is unavailable in this browser.
            </video>
          </div>
        </section>

        <section className="experience section-shell" aria-labelledby="experience-title">
          <div className="section-heading product-heading">
            <div><p className="eyebrow">{t('PRODUCT EXPERIENCE', 'РАБОТА С ПРОДУКТОМ')} / 06</p><h2 id="experience-title">{t('The workflow and the host,', 'Рабочий процесс и хост')}<br />{t('in one operating view.', 'в одном операционном представлении.')}</h2></div>
            <p>{t('Open the real session behind the summary, keep the runtime healthy, and route high-signal owner attention without losing durable context.', 'Открывайте реальную сессию за сводкой, поддерживайте среду в рабочем состоянии и привлекайте владельца по важным сигналам, не теряя долговременный контекст.')}</p>
          </div>
          <div className="shot-gallery">
            <ProductShot src="/media/screenshots/threadcells-session-workflow.webp" alt="Expanded live ThreadCells session with one active owner and two completed reviewers" label="SESSION / MULTI-AGENT WORKFLOW" detail="Real profiles, lifecycle, and durable completion" />
            <ProductShot src="/media/screenshots/threadcells-housekeeping.webp" alt="ThreadCells Housekeeping showing disk health, protected backups, schedule, and cleanup policy" label="HOUSEKEEPING / SERVER CARE" detail="Plan, revalidate, protect active work" />
            <ProductShot src="/media/screenshots/threadcells-telegram.webp" alt="ThreadCells Telegram notification settings with destination and credential fields visibly redacted" label="TELEGRAM / OWNER ATTENTION" detail="One low-noise installation-global route" stateLabel="LIVE SYSTEM · SENSITIVE FIELDS REDACTED" />
          </div>
        </section>

        <section className="native section-shell" aria-labelledby="native-title">
          <div className="native-terminal" aria-hidden="true">
            <div className="terminal-bar"><span className="window-dots"><i /><i /><i /></span><span>native-agent / tmux</span></div>
            <pre><code><span>$</span> threadcells launch --agents supervisor_terra_medium{`\n`}<em>✓ project authority resolved</em>{`\n`}<em>✓ provider capacity admitted</em>{`\n`}<b>session cao-atlas-control started</b>{`\n`}{`\n`}<span>$</span> threadcells-resource-status{`\n`}resident      2 / 5   <i>READY</i>{`\n`}provider      1 / 3   <i>READY</i>{`\n`}work context  0 / 2   <i>READY</i>{`\n`}heavy         0 / 1   <i>READY</i></code></pre>
          </div>
          <div className="native-copy">
            <p className="eyebrow">{t('NATIVE BY DESIGN', 'НАТИВНО ПО ЗАМЫСЛУ')} / 07</p>
            <h2 id="native-title">{t('Keep the coding agent.', 'Сохраните кодового агента.')}<br />{t('Add the operating system around it.', 'Добавьте операционную систему вокруг него.')}</h2>
            <p>{t('ThreadCells drives native provider CLIs in real tmux terminals. Provider adapters report what the installed host genuinely supports; unsupported capabilities stay visible instead of being simulated.', 'ThreadCells запускает нативные CLI провайдеров в настоящих tmux-терминалах. Адаптеры сообщают, что реально поддерживает установленный хост; неподдерживаемые возможности не имитируются.')}</p>
            <ul>
              <li><Check /> Codex reference adapter and first-class Claude Code adapter</li>
              <li><Check /> Provider-native authentication remains with the operator</li>
              <li><Check /> Profiles and provider configuration are versioned control-plane artifacts</li>
            </ul>
          </div>
        </section>

        <section id="open-source" className="ownership section-shell" aria-labelledby="ownership-title">
          <div className="ownership-copy">
            <p className="eyebrow">{t('MACHINE OWNERSHIP', 'ВЛАДЕНИЕ МАШИНОЙ')} / 08</p>
            <h2 id="ownership-title">{t('Your host.', 'Ваш хост.')}<br />{t('Your terminals.', 'Ваши терминалы.')}<br /><span>{t('Your control.', 'Ваш контроль.')}</span></h2>
            <p>{t('ThreadCells is self-hosted and loopback-first. It coordinates powerful local tools; it does not pretend a worktree is a security sandbox or promise hostile multi-tenancy.', 'ThreadCells разворачивается самостоятельно и по умолчанию слушает loopback. Он координирует мощные локальные инструменты, но не выдаёт worktree за песочницу безопасности и не обещает изоляцию враждебных арендаторов.')}</p>
            <div className="ownership-points">
              <span><Lock /><strong>Loopback first</strong><small>SSH-tunnel access is the supported preview boundary.</small></span>
              <span><Terminal /><strong>Real terminals</strong><small>Inspectable tmux sessions, not hidden disposable jobs.</small></span>
            </div>
          </div>
          <div className="license-panel">
            <span className="license-code">APACHE<br />2.0</span>
            <div><p className="eyebrow">OPEN SOURCE</p><h3>Inspect the control plane you trust with the machine.</h3><p>ThreadCells is available under Apache License 2.0, with upstream attribution preserved in the appropriate legal and provenance materials.</p></div>
          </div>
        </section>

        <section className="final-cta section-shell" aria-labelledby="cta-title">
          <div className="cta-grid" aria-hidden="true" />
          <img src={assetPath('/threadcells-symbol.webp')} alt="" width="512" height="512" />
          <p className="eyebrow">{t('READY', 'ГОТОВО')} / 09</p>
          <h2 id="cta-title">{t('Stop guessing what the agents are doing.', 'Перестаньте гадать, что делают агенты.')}</h2>
          <p>{t('Run the work. See the machine. Keep the result.', 'Запускайте работу. Видьте машину. Сохраняйте результат.')}</p>
          <div className="hero-actions">
            <a className="button button-primary" href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> View on GitHub <ArrowUpRight /></a>
            <a className="button button-secondary" href={`${site.docsUrl}/getting-started`}><Book /> Open Quick Setup <ArrowRight /></a>
          </div>
        </section>
      </main>
      <SiteFooter locale={locale} />
    </>
  )
}

export default function Home() { return <LandingPage /> }
