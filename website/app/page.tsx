import { ArrowRight, ArrowUpRight, Book, Check, Eye, Gauge, Github, GitBranch, Layers, Lock, Terminal } from '@/components/Icons'
import { ExecutionMesh } from '@/components/ExecutionMesh'
import { ProductShot } from '@/components/ProductShot'
import { SiteFooter } from '@/components/SiteFooter'
import { SiteHeader } from '@/components/SiteHeader'
import { assetPath, site } from '@/lib/site'

const capabilities = [
  { icon: <Eye />, id: '01', title: 'Operational truth', copy: 'See what is resident, executing, waiting, draining, blocked, or actually complete—without inferring state from a terminal tab.' },
  { icon: <GitBranch />, id: '02', title: 'Worktree authority', copy: 'Anchor work to trusted projects and managed Git worktrees. Writer leases keep concurrent mutation explicit.' },
  { icon: <Layers />, id: '03', title: 'Durable results', copy: 'Delegated work returns a persisted result that can be delivered, acknowledged, accepted, and retired without losing history.' },
  { icon: <Gauge />, id: '04', title: 'Resource-aware execution', copy: 'Resident, provider, work-context, and heavy-task limits remain separate—so capacity reflects what the host is really doing.' },
]

const steps = [
  ['01', 'Owner sets intent', 'Start a bounded workflow from a trusted local project and an explicit agent profile.'],
  ['02', 'Supervisor routes work', 'The supervisor delegates coherent work to native CLI workers and reviewers.'],
  ['03', 'ThreadCells holds the shape', 'Sessions, provider execution, work contexts, worktrees, and host-heavy work stay inspectable.'],
  ['04', 'Results return durably', 'Completion evidence comes back through the workflow; owner gates stop only for decisions that need you.'],
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
    'Optional installation-global Telegram lifecycle notifications',
  ],
}

export default function Home() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c') }} />
      <SiteHeader />
      <main id="top">
        <section className="hero section-shell" aria-labelledby="hero-title">
          <div className="hero-ambient" aria-hidden="true" />
          <div className="hero-copy">
            <p className="eyebrow"><span>SELF-HOSTED</span> CODING-AGENT OPERATIONS</p>
            <h1 id="hero-title">Run multiple coding agents.<br /><em>Keep control</em> of the machine and the result.</h1>
            <p className="hero-lede">ThreadCells is the control plane around native CLI coding agents: persistent sessions, bounded work contexts, managed worktrees, resource-aware execution, and durable completion evidence—on your own Linux host.</p>
            <div className="hero-actions">
              <a className="button button-primary" href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> View on GitHub <ArrowUpRight /></a>
              <a className="button button-secondary" href={site.docsUrl}><Book /> Read the docs <ArrowRight /></a>
              <span className="hero-note"><i /> No cloud control plane required</span>
            </div>
          </div>
          <div className="hero-visual"><div className="hero-index" aria-hidden="true"><span>01</span><i /><small>CONTROL / RESULT</small></div><ExecutionMesh /></div>
        </section>

        <section className="signal-strip" aria-label="ThreadCells operating principles">
          {['LOOPBACK FIRST', 'NATIVE CLI AGENTS', 'EXPLICIT CAPACITY', 'DURABLE RESULTS'].map((signal, index) => (
            <span key={signal}><small>0{index + 1}</small>{signal}<i /></span>
          ))}
        </section>

        <section className="problem section-shell" aria-labelledby="problem-title">
          <div className="section-heading split-heading">
            <p className="eyebrow">THE HARD PART / 02</p>
            <h2 id="problem-title">Starting more agents is easy.<br /><span>Knowing what is true is not.</span></h2>
          </div>
          <div className="problem-body">
            <p className="problem-lede">Which process owns the worktree? Which session is only idle—and which one is finished? Is the machine out of provider capacity, work contexts, or heavy execution? Did the reviewer return a result, or just stop printing?</p>
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
            <div><p className="eyebrow">THE CONTROL PLANE / 03</p><h2 id="control-plane-title">One place to see the work,<br />the host, and the handoff.</h2></div>
            <p>ThreadCells wraps native agent execution in an operational surface: sessions, projects, terminals, profiles, capacity, workflows, documentation, optional global Telegram lifecycle alerts, and current build identity.</p>
          </div>
          <ProductShot
            src="/media/screenshots/threadcells-home.webp"
            alt="ThreadCells Home screen showing two synthetic sessions, five agents, and bounded session summaries"
            label="HOME / EXECUTION OVERVIEW"
            detail="Current interface · isolated synthetic fixture"
            className="product-shot-hero"
            eager
            width={1440}
            height={960}
          />
          <div className="product-annotation annotation-a"><span>01</span><p><strong>Session truth</strong>Provider, profile, lifecycle, project, and active work remain visible.</p></div>
          <div className="product-annotation annotation-b"><span>02</span><p><strong>Independent capacity</strong>Host pressure and execution slots are not collapsed into one vague limit.</p></div>
        </section>

        <section className="capabilities section-shell" aria-labelledby="capabilities-title">
          <div className="section-heading narrow-heading">
            <p className="eyebrow">CORE IDEAS / 04</p>
            <h2 id="capabilities-title">A control room built around<br />the failure modes that matter.</h2>
          </div>
          <div className="capability-system">
            <div className="capability-spine" aria-hidden="true"><span>THREAD</span><i /><span>CELLS</span></div>
            {capabilities.map((item) => (
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
            <p className="eyebrow">WORKFLOW / 05</p>
            <h2 id="how-title">Intent goes out.<br /><span>Evidence comes back.</span></h2>
            <p>The coding agent stays native. ThreadCells makes the surrounding lifecycle explicit.</p>
          </div>
          <ol className="workflow-steps">
            {steps.map(([number, title, copy]) => (
              <li key={number}><span>{number}</span><i aria-hidden="true" /><div><h3>{title}</h3><p>{copy}</p></div></li>
            ))}
          </ol>
        </section>

        <section className="demo section-shell" aria-labelledby="demo-title">
          <div className="demo-copy">
            <p className="eyebrow">SHORT RUN / SYNTHETIC</p>
            <h2 id="demo-title">Watch a workflow resolve.</h2>
            <p>A supervisor admits the work, a worker moves from queued to running, the reviewer returns evidence, and the workflow reaches a durable completed state.</p>
          </div>
          <div className="demo-frame">
            <div className="shot-chrome" aria-hidden="true"><span className="window-dots"><i /><i /><i /></span><span>THREADCELLS / ATLAS CONTROL</span><span>00:12</span></div>
            <video controls muted playsInline preload="metadata" poster={assetPath('/media/screenshots/threadcells-home.webp')} aria-label="Synthetic ThreadCells workflow demo">
              <source src={assetPath('/media/demo/threadcells-demo.webm')} type="video/webm" />
              <source src={assetPath('/media/demo/threadcells-demo.mp4')} type="video/mp4" />
              The demo video is unavailable in this browser.
            </video>
          </div>
        </section>

        <section className="experience section-shell" aria-labelledby="experience-title">
          <div className="section-heading product-heading">
            <div><p className="eyebrow">PRODUCT EXPERIENCE / 06</p><h2 id="experience-title">Operational detail,<br />without terminal archaeology.</h2></div>
            <p>Move from fleet state to agent profiles, capacity, Statistics, and packaged documentation without losing the current operating context.</p>
          </div>
          <div className="shot-gallery">
            <ProductShot src="/media/screenshots/threadcells-agents.webp" alt="ThreadCells Agents screen with searchable synthetic agent profiles" label="AGENTS / PROFILE DISCOVERY" detail="Search authority and execution roles" />
            <ProductShot src="/media/screenshots/threadcells-capacity.webp" alt="ThreadCells Settings screen with locked Operator changes and separate resident, provider, work, and heavy limits" label="SETTINGS / AUTHORITY & CAPACITY" detail="Authorize changes and see independent host limits" />
            <ProductShot src="/media/screenshots/threadcells-docs.webp" alt="ThreadCells in-product documentation reader showing the first-time user overview" label="DOCS / BUILD-BOUND CORPUS" detail="A practical guide matched to the running build" />
          </div>
        </section>

        <section className="native section-shell" aria-labelledby="native-title">
          <div className="native-terminal" aria-hidden="true">
            <div className="terminal-bar"><span className="window-dots"><i /><i /><i /></span><span>native-agent / tmux</span></div>
            <pre><code><span>$</span> threadcells launch --agents supervisor_terra_medium{`\n`}<em>✓ project authority resolved</em>{`\n`}<em>✓ provider capacity admitted</em>{`\n`}<b>session cao-atlas-control started</b>{`\n`}{`\n`}<span>$</span> threadcells-resource-status{`\n`}resident      2 / 5   <i>READY</i>{`\n`}provider      1 / 3   <i>READY</i>{`\n`}work context  0 / 2   <i>READY</i>{`\n`}heavy         0 / 1   <i>READY</i></code></pre>
          </div>
          <div className="native-copy">
            <p className="eyebrow">NATIVE BY DESIGN / 07</p>
            <h2 id="native-title">Keep the coding agent.<br />Add the operating system around it.</h2>
            <p>ThreadCells drives native provider CLIs in real tmux terminals. Provider adapters report what the installed host genuinely supports; unsupported capabilities stay visible instead of being simulated.</p>
            <ul>
              <li><Check /> Codex reference adapter and first-class Claude Code adapter</li>
              <li><Check /> Provider-native authentication remains with the operator</li>
              <li><Check /> Profiles and provider configuration are versioned control-plane artifacts</li>
            </ul>
          </div>
        </section>

        <section id="open-source" className="ownership section-shell" aria-labelledby="ownership-title">
          <div className="ownership-copy">
            <p className="eyebrow">MACHINE OWNERSHIP / 08</p>
            <h2 id="ownership-title">Your host.<br />Your terminals.<br /><span>Your control.</span></h2>
            <p>ThreadCells is self-hosted and loopback-first. It coordinates powerful local tools; it does not pretend a worktree is a security sandbox or promise hostile multi-tenancy.</p>
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
          <p className="eyebrow">READY / 09</p>
          <h2 id="cta-title">Stop guessing what the agents are doing.</h2>
          <p>Run the work. See the machine. Keep the result.</p>
          <div className="hero-actions">
            <a className="button button-primary" href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> View on GitHub <ArrowUpRight /></a>
            <a className="button button-secondary" href={`${site.docsUrl}/getting-started`}><Book /> Open Quick Setup <ArrowRight /></a>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  )
}
