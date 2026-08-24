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

const russianJsonLd = {
  ...jsonLd,
  description: localeCopy.ru.description,
  featureList: [
    'Постоянные сессии нативных CLI-агентов',
    'Управляемые Git worktree с явными правами на запись',
    'Сохранённые результаты делегирования',
    'Явный жизненный цикл процессов и точки, где нужно решение владельца',
    'Независимые лимиты ёмкости выполнения',
    'Очистка с учётом защищённого набора и вывод завершённых сред выполнения из эксплуатации',
    'Необязательные Telegram-уведомления о жизненном цикле для всей установки',
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
    { icon: <Eye />, id: '01', title: 'Состояние без догадок', copy: 'Видно, какие процессы остаются в среде, выполняются, ждут, завершаются, заблокированы или действительно завершены — без догадок по вкладкам терминала.' },
    { icon: <GitBranch />, id: '02', title: 'Явные права на запись', copy: 'Привязывайте работу к доверенным проектам и управляемым Git worktree. Лизы на запись чётко фиксируют, кто может вносить параллельные изменения.' },
    { icon: <Layers />, id: '03', title: 'Сохранённые результаты', copy: 'Результат делегированной работы сохраняется: его можно доставить, подтвердить получение, принять и вывести из активной среды без потери истории.' },
    { icon: <Gauge />, id: '04', title: 'Рабочая среда агентов', copy: 'ThreadCells следит за нагрузкой хоста и безопасно убирает пригодные к очистке остатки сред выполнения, логов, кэша, сборок и релизов, не затрагивая активную работу и сохранённую историю.' },
  ] : capabilities
  const localizedSteps = ru ? [['01', 'Создайте сессию', 'Выберите доверенный локальный проект и профиль агента или супервизора.'], ['02', 'Поставьте задачу', 'Супервизор делегирует связный объём работы нативным CLI-исполнителям и ревьюерам.'], ['03', 'Следите за системой', 'ThreadCells ведёт открытые процессы между обращениями к модели; сессии, worktree, ёмкость и нагрузка хоста остаются на виду.'], ['04', 'Подключайтесь по запросу', 'Результаты сохраняются. Процесс ждёт решения владельца только там, где без него действительно не обойтись.']] : steps
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ru ? russianJsonLd : jsonLd).replace(/</g, '\\u003c') }} />
      <SiteHeader locale={locale} />
      <main id="top" lang={copy.htmlLang}>
        <section className="hero section-shell" aria-labelledby="hero-title">
          <div className="hero-ambient" aria-hidden="true" />
          <div className="hero-copy">
            <p className="eyebrow"><span>{t('SELF-HOSTED', 'НА СВОЁМ ХОСТЕ')}</span> {t('CODING-AGENT OPERATIONS', 'УПРАВЛЕНИЕ КОДОВЫМИ АГЕНТАМИ')}</p>
            <h1 id="hero-title">{t('Run coding agents as a system.', 'Запускайте кодовых агентов как систему.')}<br /> <em>{t('Not a pile', 'Не набор')}</em> {t('of terminals.', 'терминалов.')}</h1>
            <p className="hero-lede">{t('ThreadCells coordinates native CLI agents, keeps open workflows moving across model turns, and maintains the orchestration environment underneath them—on your own Linux host.', 'ThreadCells координирует нативных CLI-агентов, ведёт открытые процессы между обращениями к модели и поддерживает среду оркестрации на вашем Linux-хосте.')}</p>
            <div className="hero-actions">
              <a className="button button-primary" href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> {t('View on GitHub', 'Открыть на GitHub')} <ArrowUpRight /></a>
              <a className="button button-secondary" href={site.docsUrl}><Book /> {t('Read the docs', 'Читать документацию')} <ArrowRight /></a>
              <span className="hero-note"><i /> {t('Start the work. Watch the system. Step in only when it needs you.', 'Запускайте работу. Следите за системой. Подключайтесь, только когда без вас нельзя.')}</span>
            </div>
          </div>
          <div className="hero-visual"><div className="hero-index" aria-hidden="true"><span>01</span><i /><small>{t('CONTROL / RESULT', 'УПРАВЛЕНИЕ / РЕЗУЛЬТАТ')}</small></div><ExecutionMesh locale={locale} /></div>
        </section>

        <section className="signal-strip" aria-label={t('ThreadCells operating principles', 'Принципы работы ThreadCells')}>
          {(ru ? ['СОГЛАСОВАННЫЕ ПРОЦЕССЫ', 'ЗАЩИЩЁННОЕ ОБСЛУЖИВАНИЕ', 'СОХРАНЁННАЯ ИСТОРИЯ', 'СНАЧАЛА LOOPBACK'] : ['COORDINATED WORKFLOWS', 'PROTECTED HOUSEKEEPING', 'DURABLE HISTORY', 'LOOPBACK FIRST']).map((signal, index) => (
            <span key={signal}><small>0{index + 1}</small>{signal}<i /></span>
          ))}
        </section>

        <section className="problem section-shell" aria-labelledby="problem-title">
          <div className="section-heading split-heading">
            <p className="eyebrow">{t('THE HARD PART', 'СЛОЖНАЯ ЧАСТЬ')} / 02</p>
            <h2 id="problem-title">{t('Starting more agents is easy.', 'Запускать больше агентов легко.')}<br /><span>{t('Knowing what is true is not.', 'Понять, что происходит на самом деле, — нет.')}</span></h2>
          </div>
          <div className="problem-body">
            <p className="problem-lede">{t('Which process owns the worktree? Which session is only idle—and which one is finished? Is the machine out of provider capacity, work contexts, or heavy execution? Did the reviewer return a result, or just stop printing?', 'Какой процесс владеет worktree? Какая сессия просто простаивает, а какая завершена? Не исчерпана ли ёмкость провайдера, рабочих контекстов или тяжёлых запусков? Ревьюер вернул результат или просто перестал выводить сообщения?')}</p>
            <div className="truth-ledger" role="list" aria-label={t('Operational questions ThreadCells answers', 'Операционные вопросы, на которые отвечает ThreadCells')}>
              <div role="listitem"><span>tm-83A1</span><strong>{t('SUPERVISOR', 'СУПЕРВИЗОР')}</strong><em className="state-running">{t('RUNNING', 'В РАБОТЕ')}</em><small>{t('provider 01 / 03', 'провайдер 01 / 03')}</small></div>
              <div role="listitem"><span>tm-94C2</span><strong>{t('WORKER', 'ИСПОЛНИТЕЛЬ')}</strong><em className="state-complete">{t('COMPLETE', 'ГОТОВО')}</em><small>{t('result persisted', 'результат сохранён')}</small></div>
              <div role="listitem"><span>tm-07F4</span><strong>{t('REVIEWER', 'РЕВЬЮЕР')}</strong><em className="state-ready">{t('READY', 'ГОТОВ')}</em><small>{t('context 02 / 02', 'контекст 02 / 02')}</small></div>
              <div role="listitem"><span>{t('OWNER GATE', 'РЕШЕНИЕ ВЛАДЕЛЬЦА')}</span><strong>{t('AUTHORITY', 'ПОЛНОМОЧИЯ')}</strong><em className="state-gated">{t('GATED', 'ОЖИДАЕТ')}</em><small>{t('decision required', 'нужно решение')}</small></div>
            </div>
          </div>
        </section>

        <section id="control-plane" className="control-plane section-shell" aria-labelledby="control-plane-title">
          <div className="section-heading product-heading">
            <div><p className="eyebrow">{t('THE CONTROL PLANE', 'ПАНЕЛЬ УПРАВЛЕНИЯ')} / 03</p><h2 id="control-plane-title">{t('One place to see the work,', 'Работа, хост и результаты —')}<br />{t('the host, and the handoff.', 'в одном месте.')}</h2></div>
            <p>{t('ThreadCells wraps native agent execution in an operational surface: sessions, projects, terminals, profiles, capacity, workflows, documentation, optional global Telegram lifecycle alerts, and current build identity.', 'ThreadCells объединяет нативное выполнение агентов в единый интерфейс управления: сессии, проекты, терминалы, профили, ёмкость, рабочие процессы, документацию, необязательные Telegram-уведомления о жизненном цикле для всей установки и идентификатор текущей сборки.')}</p>
          </div>
          <ProductShot
            src="/media/screenshots/threadcells-home.webp"
            alt={t('ThreadCells Home showing 21 real sessions, 166 agents, aggregate lifecycle counts, and dense session status summaries', 'Главная ThreadCells: 21 реальная сессия, 166 агентов, суммарные состояния жизненного цикла и компактные сводки по статусам сессий')}
            label={t('HOME / EXECUTION OVERVIEW', 'ГЛАВНАЯ / ОБЗОР ВЫПОЛНЕНИЯ')}
            detail={t('Real release system · sensitive material excluded', 'Действующая система релизов · конфиденциальные данные не показаны')}
            stateLabel={t('LIVE RELEASE SYSTEM', 'ДЕЙСТВУЮЩАЯ СИСТЕМА РЕЛИЗОВ')}
            locale={locale}
            className="product-shot-hero"
            eager
            width={1440}
            height={960}
          />
          <div className="product-annotation annotation-a"><span>01</span><p><strong>{t('Session truth', 'Состояние сессии')}</strong>{t('Provider, profile, lifecycle, project, and active work remain visible.', 'Видны провайдер, профиль, жизненный цикл, проект и активная работа.')}</p></div>
          <div className="product-annotation annotation-b"><span>02</span><p><strong>{t('Operational scale', 'Работа в масштабе')}</strong>{t('First, Last, and Total summaries keep large durable histories readable.', 'Сводки «первый», «последний» и «всего» делают большую историю читаемой.')}</p></div>
        </section>

        <section className="capabilities section-shell" aria-labelledby="capabilities-title">
          <div className="section-heading narrow-heading">
            <p className="eyebrow">{t('CORE IDEAS', 'ОСНОВНЫЕ ИДЕИ')} / 04</p>
            <h2 id="capabilities-title">{t('A control room built around', 'Панель управления с учётом')}<br />{t('the failure modes that matter.', 'реальных сбоев.')}</h2>
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
            <h2 id="how-title">{t('Intent goes out.', 'Задача передаётся.')}<br /><span>{t('Evidence comes back.', 'Подтверждённый результат возвращается.')}</span></h2>
            <p>{t('The coding agent stays native. ThreadCells makes the surrounding lifecycle explicit.', 'Кодовый агент остаётся нативным. ThreadCells явно показывает весь жизненный цикл вокруг него.')}</p>
          </div>
          <ol className="workflow-steps">
            {localizedSteps.map(([number, title, copy]) => (
              <li key={number}><span>{number}</span><i aria-hidden="true" /><div><h3>{title}</h3><p>{copy}</p></div></li>
            ))}
          </ol>
        </section>

        <section className="demo section-shell" aria-labelledby="demo-title">
          <div className="demo-copy">
            <p className="eyebrow">{t('LIVE RELEASE TOUR', 'ПРОДУКТ В ДЕЙСТВИИ')}</p>
            <h2 id="demo-title">{t('See the control plane in motion.', 'Панель управления в действии.')}</h2>
            <p>{t('A short tour through the real release system: dense Home state, an expanded multi-agent session, protected Housekeeping, and independent capacity.', 'Короткий обзор действующей системы релизов: насыщенная сводка статусов на главной, развёрнутая многоагентная сессия, защищённое обслуживание и независимые лимиты ёмкости.')}</p>
          </div>
          <div className="demo-frame">
            <div className="shot-chrome" aria-hidden="true"><span className="window-dots"><i /><i /><i /></span><span>{t('THREADCELLS / LIVE RELEASE SYSTEM', 'THREADCELLS / ДЕЙСТВУЮЩАЯ СИСТЕМА РЕЛИЗОВ')}</span><span>00:14</span></div>
            <video autoPlay controls loop muted playsInline preload="metadata" poster={assetPath('/media/screenshots/threadcells-home.webp')} aria-label={t('Live ThreadCells release-system tour', 'Обзор действующей системы релизов ThreadCells')}>
              <source src={assetPath('/media/demo/threadcells-demo.webm')} type="video/webm" />
              <source src={assetPath('/media/demo/threadcells-demo.mp4')} type="video/mp4" />
              {t('The demo video is unavailable in this browser.', 'Демонстрационное видео недоступно в этом браузере.')}
            </video>
          </div>
        </section>

        <section className="experience section-shell" aria-labelledby="experience-title">
          <div className="section-heading product-heading">
            <div><p className="eyebrow">{t('PRODUCT EXPERIENCE', 'РАБОТА С ПРОДУКТОМ')} / 06</p><h2 id="experience-title">{t('The workflow and the host,', 'Рабочий процесс и хост')}<br />{t('in one operating view.', 'в едином интерфейсе.')}</h2></div>
            <p>{t('Open the real session behind the summary, keep the runtime healthy, and route high-signal owner attention without losing durable context.', 'Открывайте сессию прямо из сводки, поддерживайте среду выполнения в рабочем состоянии и выносите на решение владельца только действительно важное, не теряя сохранённый контекст.')}</p>
          </div>
          <div className="shot-gallery">
            <ProductShot src="/media/screenshots/threadcells-session-workflow.webp" alt={t('Expanded live ThreadCells session with one active owner and two completed reviewers', 'Развёрнутая сессия ThreadCells с одним активным владельцем и двумя ревьюерами, завершившими работу')} label={t('SESSION / MULTI-AGENT WORKFLOW', 'СЕССИЯ / МНОГОАГЕНТНЫЙ WORKFLOW')} detail={t('Real profiles, lifecycle, and durable completion', 'Реальные профили, жизненный цикл и зафиксированное завершение')} stateLabel={t('LIVE RELEASE SYSTEM', 'ДЕЙСТВУЮЩАЯ СИСТЕМА РЕЛИЗОВ')} locale={locale} />
            <ProductShot src="/media/screenshots/threadcells-housekeeping.webp" alt={t('ThreadCells Housekeeping showing disk health, protected backups, schedule, and cleanup policy', 'Раздел обслуживания ThreadCells: состояние диска, защищённые резервные копии, расписание и политика очистки')} label={t('HOUSEKEEPING / SERVER CARE', 'ОБСЛУЖИВАНИЕ / СОСТОЯНИЕ СЕРВЕРА')} detail={t('Plan, revalidate, protect active work', 'Планировать, перепроверять, защищать активную работу')} stateLabel={t('LIVE RELEASE SYSTEM', 'ДЕЙСТВУЮЩАЯ СИСТЕМА РЕЛИЗОВ')} locale={locale} />
            <ProductShot src="/media/screenshots/threadcells-telegram.webp" alt={t('ThreadCells Telegram notification settings with destination and credential fields visibly redacted', 'Настройки уведомлений Telegram в ThreadCells: получатель и учётные данные скрыты')} label={t('TELEGRAM / OWNER ATTENTION', 'TELEGRAM / ВНИМАНИЕ ВЛАДЕЛЬЦА')} detail={t('One low-noise installation-global route', 'Один тихий канал уведомлений для всей установки')} stateLabel={t('LIVE SYSTEM · SENSITIVE FIELDS REDACTED', 'ДЕЙСТВУЮЩАЯ СИСТЕМА · КОНФИДЕНЦИАЛЬНЫЕ ПОЛЯ СКРЫТЫ')} locale={locale} />
          </div>
        </section>

        <section className="native section-shell" aria-labelledby="native-title">
          <div className="native-terminal" aria-hidden="true">
            <div className="terminal-bar"><span className="window-dots"><i /><i /><i /></span><span>native-agent / tmux</span></div>
            <pre><code><span>$</span> threadcells launch --agents supervisor_terra_medium{`\n`}<em>✓ {t('project authority resolved', 'полномочия проекта определены')}</em>{`\n`}<em>✓ {t('provider capacity admitted', 'лимит провайдера выделен')}</em>{`\n`}<b>{t('session cao-atlas-control started', 'сессия cao-atlas-control запущена')}</b>{`\n`}{`\n`}<span>$</span> threadcells-resource-status{`\n`}{t('resident', 'RESIDENT')}  2 / 5   <i>{t('READY', 'ГОТОВО')}</i>{`\n`}{t('provider', 'ПРОВАЙДЕР')}   1 / 3   <i>{t('READY', 'ГОТОВО')}</i>{`\n`}{t('work context', 'РАБОЧИЙ КОНТЕКСТ')} 0 / 2   <i>{t('READY', 'ГОТОВО')}</i>{`\n`}{t('heavy', 'ТЯЖЁЛЫЕ')}      0 / 1   <i>{t('READY', 'ГОТОВО')}</i></code></pre>
          </div>
          <div className="native-copy">
            <p className="eyebrow">{t('NATIVE BY DESIGN', 'НАТИВНЫЙ ПОДХОД')} / 07</p>
            <h2 id="native-title">{t('Keep the coding agent.', 'Оставьте привычного агента.')}<br />{t('Add the operating system around it.', 'Добавьте слой управления.')}</h2>
            <p>{t('ThreadCells drives native provider CLIs in real tmux terminals. Provider adapters report what the installed host genuinely supports; unsupported capabilities stay visible instead of being simulated.', 'ThreadCells запускает нативные CLI провайдеров в настоящих tmux-терминалах. Адаптеры сообщают, что реально поддерживает установленный хост; неподдерживаемые возможности не имитируются.')}</p>
            <ul>
              <li><Check /> {t('Codex reference adapter and first-class Claude Code adapter', 'Эталонный адаптер Codex и полноценный адаптер Claude Code')}</li>
              <li><Check /> {t('Provider-native authentication remains with the operator', 'Аутентификация у провайдера остаётся у оператора')}</li>
              <li><Check /> {t('Profiles and provider configuration are versioned control-plane artifacts', 'Профили и настройки провайдера версионируются как конфигурация control plane')}</li>
            </ul>
          </div>
        </section>

        <section id="open-source" className="ownership section-shell" aria-labelledby="ownership-title">
          <div className="ownership-copy">
            <p className="eyebrow">{t('MACHINE OWNERSHIP', 'ВЛАДЕНИЕ МАШИНОЙ')} / 08</p>
            <h2 id="ownership-title">{t('Your host.', 'Ваш хост.')}<br />{t('Your terminals.', 'Ваши терминалы.')}<br /><span>{t('Your control.', 'Ваш контроль.')}</span></h2>
            <p>{t('ThreadCells is self-hosted and loopback-first. It coordinates powerful local tools; it does not pretend a worktree is a security sandbox or promise hostile multi-tenancy.', 'ThreadCells развёрнут на вашем хосте и по умолчанию доступен только через loopback. Он координирует мощные локальные инструменты, но не выдаёт Git worktree за безопасную песочницу и не обещает безопасную изоляцию недоверенных пользователей.')}</p>
            <div className="ownership-points">
              <span><Lock /><strong>{t('Loopback first', 'Сначала loopback')}</strong><small>{t('SSH-tunnel access is the supported preview boundary.', 'SSH-туннель — поддерживаемая граница для предварительного доступа.')}</small></span>
              <span><Terminal /><strong>{t('Real terminals', 'Настоящие терминалы')}</strong><small>{t('Inspectable tmux sessions, not hidden disposable jobs.', 'tmux-сессии можно проверить; это не скрытые одноразовые задачи.')}</small></span>
            </div>
          </div>
          <div className="license-panel">
            <span className="license-code">APACHE<br />2.0</span>
            <div><p className="eyebrow">{t('OPEN SOURCE', 'ОТКРЫТЫЙ КОД')}</p><h3>{t('Inspect the control plane you trust with the machine.', 'Проверяйте слой управления, которому доверяете свою машину.')}</h3><p>{t('ThreadCells is available under Apache License 2.0, with upstream attribution preserved in the appropriate legal and provenance materials.', 'ThreadCells доступен по лицензии Apache 2.0; указание исходного проекта сохранено в соответствующих юридических материалах и документах об авторстве.')}</p></div>
          </div>
        </section>

        <section className="final-cta section-shell" aria-labelledby="cta-title">
          <div className="cta-grid" aria-hidden="true" />
          <img src={assetPath('/threadcells-symbol.webp')} alt="" width="512" height="512" />
          <p className="eyebrow">{t('READY', 'ГОТОВО')} / 09</p>
          <h2 id="cta-title">{t('Stop guessing what the agents are doing.', 'Перестаньте гадать, что делают агенты.')}</h2>
          <p>{t('Run the work. See the machine. Keep the result.', 'Запускайте работу. Держите хост в поле зрения. Сохраняйте результат.')}</p>
          <div className="hero-actions">
            <a className="button button-primary" href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> {t('View on GitHub', 'Открыть на GitHub')} <ArrowUpRight /></a>
            <a className="button button-secondary" href={`${site.docsUrl}/getting-started`}><Book /> {t('Open Quick Setup', 'Открыть Quick Setup')} <ArrowRight /></a>
          </div>
        </section>
      </main>
      <SiteFooter locale={locale} />
    </>
  )
}

export default function Home() { return <LandingPage /> }
