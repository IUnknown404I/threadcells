export const locales = ['en', 'ru', 'zh-CN', 'es', 'pt-BR', 'de', 'ja'] as const
export const translatedLocales = locales.filter(locale => locale !== 'en')
export type Locale = (typeof locales)[number]

type LocaleCopy = {
  code: Locale
  short: string
  name: string
  htmlLang: string
  title: string
  description: string
  nav: readonly [string, string, string]
  docs: string
  github: string
  language: string
  footer: string
  back: string
  footerNav: string
  homeLabel: string
  primaryNav: string
  systemReady: string
  controlPlane: string
  creator: string
  downstream: string
  screenshot: {
    expanded: string
    close: string
    closeButton: string
    hint: string
    expand: string
    expandButton: string
  }
  docsUi: {
    browse: string
    publicGuide: string
    articles: string
    article: string
    readArticle: string
    onThisPage: string
    previous: string
    next: string
    startHere: string
    quickSetupHint: string
    openQuickSetup: string
    repository: string
    search: string
    searchPlaceholder: string
    navLabel: string
    noMatches: string
    paginationLabel: string
    viewRepository: string
    linkTo: string
    indexTitle: string
    indexAccent: string
    indexDescription: string
    groups: Record<string, string>
  }
}

export const localeCopy: Record<Locale, LocaleCopy> = {
  en: {
    code: 'en', short: 'EN', name: 'English', htmlLang: 'en',
    title: 'ThreadCells — Control plane for native CLI coding agents',
    description: 'Run coding agents as a coordinated system. ThreadCells keeps workflows moving, protects durable history, and maintains its own orchestration environment on your Linux host.',
    nav: ['Product', 'How it works', 'Open source'], docs: 'Docs', github: 'GitHub', language: 'Language',
    footer: 'Operational control for native CLI coding agents—on the machine you own.', back: 'Back to top', footerNav: 'Footer navigation',
    homeLabel: 'ThreadCells home', primaryNav: 'Primary navigation', systemReady: 'SYSTEM READY', controlPlane: 'CONTROL PLANE',
    creator: 'Created and maintained by Subaev Ruslan, with contributions from the ThreadCells community.',
    downstream: 'Independent, unofficial downstream of AWS Labs CLI Agent Orchestrator. No AWS sponsorship or endorsement is implied.',
    screenshot: { expanded: 'Expanded screenshot:', close: 'Close expanded screenshot', closeButton: 'Close', hint: 'Click the image, the backdrop, or press Esc to close.', expand: 'Click to expand:', expandButton: 'Click to expand' },
    docsUi: {
      browse: 'Browse docs', publicGuide: 'PUBLIC GUIDE', articles: 'ARTICLES', article: 'article', readArticle: 'Read article', onThisPage: 'On this page', previous: 'Previous', next: 'Next', startHere: 'Start here',
      quickSetupHint: 'Quick Setup is the fastest supported path. Concepts and architecture can wait until after your first useful agent.',
      openQuickSetup: 'Open Quick Setup', repository: 'GitHub repository', search: 'Search documentation', searchPlaceholder: 'Search articles…', navLabel: 'Documentation articles', noMatches: 'No matching articles.', paginationLabel: 'Previous and next articles', viewRepository: 'View the repository', linkTo: 'Link to', indexTitle: 'Operate ThreadCells', indexAccent: 'without repository archaeology.',
      indexDescription: 'This is the complete public guide for the current ThreadCells product: from a first local session to providers, workflows, capacity, remote access, backups, and incident recovery.',
      groups: { 'Getting started': 'Getting started', 'Using ThreadCells': 'Using ThreadCells', Configuration: 'Configuration', Operations: 'Operations', Safety: 'Safety', About: 'About' },
    },
  },
  ru: {
    code: 'ru', short: 'RU', name: 'Русский', htmlLang: 'ru',
    title: 'ThreadCells — панель управления нативными CLI-агентами для разработки',
    description: 'Запускайте кодовых агентов как согласованную систему. ThreadCells ведёт открытые процессы между обращениями к модели, сохраняет историю и поддерживает среду оркестрации на вашем Linux-хосте.',
    nav: ['Продукт', 'Как это работает', 'Открытый код'], docs: 'Документация', github: 'GitHub', language: 'Язык',
    footer: 'Управление нативными CLI-агентами для разработки — на вашей машине.', back: 'Наверх', footerNav: 'Навигация в нижней части страницы',
    homeLabel: 'Главная ThreadCells', primaryNav: 'Основная навигация', systemReady: 'СИСТЕМА ГОТОВА', controlPlane: 'ПАНЕЛЬ УПРАВЛЕНИЯ',
    creator: 'Создано и поддерживается Субаевым Русланом при участии сообщества ThreadCells.',
    downstream: 'Независимый неофициальный проект на основе AWS Labs CLI Agent Orchestrator. AWS не спонсирует и не участвует в нём.',
    screenshot: { expanded: 'Развёрнутый скриншот:', close: 'Закрыть развёрнутый скриншот', closeButton: 'Закрыть', hint: 'Нажмите на изображение, фон или Esc, чтобы закрыть.', expand: 'Открыть:', expandButton: 'Открыть' },
    docsUi: {
      browse: 'Разделы документации', publicGuide: 'ПУБЛИЧНОЕ РУКОВОДСТВО', articles: 'СТАТЕЙ', article: 'статья', readArticle: 'Читать статью', onThisPage: 'На этой странице', previous: 'Назад', next: 'Далее', startHere: 'С чего начать',
      quickSetupHint: 'Quick Setup — самый быстрый поддерживаемый путь. К концепциям и архитектуре можно вернуться после первого полезного агента.',
      openQuickSetup: 'Открыть Quick Setup', repository: 'Репозиторий GitHub', search: 'Поиск по документации', searchPlaceholder: 'Найти статью…', navLabel: 'Статьи документации', noMatches: 'Ничего не найдено.', paginationLabel: 'Предыдущая и следующая статьи', viewRepository: 'Открыть репозиторий', linkTo: 'Ссылка на раздел', indexTitle: 'Работайте с ThreadCells', indexAccent: 'без раскопок в репозитории.',
      indexDescription: 'Полное публичное руководство по текущей версии ThreadCells: от первой локальной сессии до провайдеров, workflows, ёмкости, удалённого доступа, резервных копий и восстановления.',
      groups: { 'Getting started': 'Начало работы', 'Using ThreadCells': 'Работа с ThreadCells', Configuration: 'Настройка', Operations: 'Эксплуатация', Safety: 'Безопасность', About: 'О проекте' },
    },
  },
  'zh-CN': {
    code: 'zh-CN', short: 'ZH', name: '简体中文', htmlLang: 'zh-CN',
    title: 'ThreadCells — 原生 CLI 编码智能体控制平面',
    description: '将编码智能体作为协同系统运行。ThreadCells 持续推进工作流、保护持久历史，并在您的 Linux 主机上维护编排环境。',
    nav: ['产品', '工作原理', '开源'], docs: '文档', github: 'GitHub', language: '语言',
    footer: '在您自己的机器上管理原生 CLI 编码智能体。', back: '返回顶部', footerNav: '页脚导航',
    homeLabel: 'ThreadCells 首页', primaryNav: '主导航', systemReady: '系统就绪', controlPlane: '控制平面',
    creator: '由 Subaev Ruslan 创建并维护，ThreadCells 社区共同贡献。',
    downstream: '基于 AWS Labs CLI Agent Orchestrator 的独立非官方下游项目。并非由 AWS 赞助或认可。',
    screenshot: { expanded: '展开的屏幕截图：', close: '关闭展开的屏幕截图', closeButton: '关闭', hint: '点击图片、背景或按 Esc 键关闭。', expand: '点击展开：', expandButton: '点击展开' },
    docsUi: {
      browse: '浏览文档', publicGuide: '公开指南', articles: '篇文章', article: '篇文章', readArticle: '阅读文章', onThisPage: '本页内容', previous: '上一篇', next: '下一篇', startHere: '从这里开始',
      quickSetupHint: 'Quick Setup 是最快的受支持路径。完成第一个有用的智能体任务后，再阅读概念和架构即可。',
      openQuickSetup: '打开 Quick Setup', repository: 'GitHub 仓库', search: '搜索文档', searchPlaceholder: '搜索文章…', navLabel: '文档文章', noMatches: '没有匹配的文章。', paginationLabel: '上一篇和下一篇', viewRepository: '查看仓库', linkTo: '链接到', indexTitle: '运维 ThreadCells', indexAccent: '无需翻查仓库。',
      indexDescription: '这是当前 ThreadCells 产品的完整公开指南，涵盖首次本地会话、提供商、工作流、容量、远程访问、备份和故障恢复。',
      groups: { 'Getting started': '入门', 'Using ThreadCells': '使用 ThreadCells', Configuration: '配置', Operations: '运维', Safety: '安全', About: '关于' },
    },
  },
  es: {
    code: 'es', short: 'ES', name: 'Español', htmlLang: 'es',
    title: 'ThreadCells — Plano de control para agentes de programación CLI nativos',
    description: 'Ejecuta agentes de programación como un sistema coordinado. ThreadCells mantiene los flujos en marcha, protege el historial duradero y cuida el entorno de orquestación en tu host Linux.',
    nav: ['Producto', 'Cómo funciona', 'Código abierto'], docs: 'Docs', github: 'GitHub', language: 'Idioma',
    footer: 'Control operativo para agentes de programación CLI nativos, en tu propia máquina.', back: 'Volver arriba', footerNav: 'Navegación del pie',
    homeLabel: 'Inicio de ThreadCells', primaryNav: 'Navegación principal', systemReady: 'SISTEMA LISTO', controlPlane: 'PLANO DE CONTROL',
    creator: 'Creado y mantenido por Subaev Ruslan, con contribuciones de la comunidad de ThreadCells.',
    downstream: 'Proyecto derivado, independiente y no oficial de AWS Labs CLI Agent Orchestrator. AWS no lo patrocina ni lo respalda.',
    screenshot: { expanded: 'Captura ampliada:', close: 'Cerrar la captura ampliada', closeButton: 'Cerrar', hint: 'Haz clic en la imagen, en el fondo o pulsa Esc para cerrar.', expand: 'Ampliar:', expandButton: 'Ampliar' },
    docsUi: {
      browse: 'Explorar la documentación', publicGuide: 'GUÍA PÚBLICA', articles: 'ARTÍCULOS', article: 'artículo', readArticle: 'Leer artículo', onThisPage: 'En esta página', previous: 'Anterior', next: 'Siguiente', startHere: 'Empieza aquí',
      quickSetupHint: 'Quick Setup es la ruta compatible más rápida. Puedes dejar los conceptos y la arquitectura para después de tu primer agente útil.',
      openQuickSetup: 'Abrir Quick Setup', repository: 'Repositorio de GitHub', search: 'Buscar en la documentación', searchPlaceholder: 'Buscar artículos…', navLabel: 'Artículos de documentación', noMatches: 'No hay artículos coincidentes.', paginationLabel: 'Artículos anterior y siguiente', viewRepository: 'Ver el repositorio', linkTo: 'Enlace a', indexTitle: 'Opera ThreadCells', indexAccent: 'sin excavar en el repositorio.',
      indexDescription: 'La guía pública completa del producto ThreadCells actual: desde la primera sesión local hasta proveedores, flujos, capacidad, acceso remoto, copias de seguridad y recuperación.',
      groups: { 'Getting started': 'Primeros pasos', 'Using ThreadCells': 'Uso de ThreadCells', Configuration: 'Configuración', Operations: 'Operaciones', Safety: 'Seguridad', About: 'Acerca de' },
    },
  },
  'pt-BR': {
    code: 'pt-BR', short: 'PT-BR', name: 'Português (Brasil)', htmlLang: 'pt-BR',
    title: 'ThreadCells — Plano de controle para agentes de programação CLI nativos',
    description: 'Execute agentes de programação como um sistema coordenado. O ThreadCells mantém os fluxos em andamento, protege o histórico durável e cuida do ambiente de orquestração no seu host Linux.',
    nav: ['Produto', 'Como funciona', 'Código aberto'], docs: 'Docs', github: 'GitHub', language: 'Idioma',
    footer: 'Controle operacional para agentes de programação CLI nativos, na sua própria máquina.', back: 'Voltar ao topo', footerNav: 'Navegação do rodapé',
    homeLabel: 'Início do ThreadCells', primaryNav: 'Navegação principal', systemReady: 'SISTEMA PRONTO', controlPlane: 'PLANO DE CONTROLE',
    creator: 'Criado e mantido por Subaev Ruslan, com contribuições da comunidade ThreadCells.',
    downstream: 'Projeto derivado, independente e não oficial do AWS Labs CLI Agent Orchestrator. Não é patrocinado nem endossado pela AWS.',
    screenshot: { expanded: 'Captura ampliada:', close: 'Fechar a captura ampliada', closeButton: 'Fechar', hint: 'Clique na imagem, no fundo ou pressione Esc para fechar.', expand: 'Ampliar:', expandButton: 'Ampliar' },
    docsUi: {
      browse: 'Explorar a documentação', publicGuide: 'GUIA PÚBLICO', articles: 'ARTIGOS', article: 'artigo', readArticle: 'Ler artigo', onThisPage: 'Nesta página', previous: 'Anterior', next: 'Próximo', startHere: 'Comece aqui',
      quickSetupHint: 'O Quick Setup é o caminho compatível mais rápido. Conceitos e arquitetura podem esperar até o primeiro agente útil.',
      openQuickSetup: 'Abrir o Quick Setup', repository: 'Repositório no GitHub', search: 'Pesquisar na documentação', searchPlaceholder: 'Pesquisar artigos…', navLabel: 'Artigos da documentação', noMatches: 'Nenhum artigo correspondente.', paginationLabel: 'Artigos anterior e próximo', viewRepository: 'Ver o repositório', linkTo: 'Link para', indexTitle: 'Opere o ThreadCells', indexAccent: 'sem escavar o repositório.',
      indexDescription: 'O guia público completo do produto ThreadCells atual: da primeira sessão local a provedores, fluxos, capacidade, acesso remoto, backups e recuperação.',
      groups: { 'Getting started': 'Primeiros passos', 'Using ThreadCells': 'Uso do ThreadCells', Configuration: 'Configuração', Operations: 'Operações', Safety: 'Segurança', About: 'Sobre' },
    },
  },
  de: {
    code: 'de', short: 'DE', name: 'Deutsch', htmlLang: 'de',
    title: 'ThreadCells — Steuerungsebene für native CLI-Coding-Agenten',
    description: 'Betreibe Coding-Agenten als koordiniertes System. ThreadCells hält Workflows in Bewegung, schützt den dauerhaften Verlauf und pflegt die Orchestrierungsumgebung auf deinem Linux-Host.',
    nav: ['Produkt', 'Funktionsweise', 'Open Source'], docs: 'Doku', github: 'GitHub', language: 'Sprache',
    footer: 'Operative Kontrolle für native CLI-Coding-Agenten auf deinem eigenen Rechner.', back: 'Nach oben', footerNav: 'Fußnavigation',
    homeLabel: 'ThreadCells-Startseite', primaryNav: 'Hauptnavigation', systemReady: 'SYSTEM BEREIT', controlPlane: 'STEUERUNGSEBENE',
    creator: 'Erstellt und gepflegt von Subaev Ruslan, mit Beiträgen der ThreadCells-Community.',
    downstream: 'Unabhängiges, inoffizielles Derivat des AWS Labs CLI Agent Orchestrator. Keine Förderung oder Unterstützung durch AWS.',
    screenshot: { expanded: 'Vergrößerter Screenshot:', close: 'Vergrößerten Screenshot schließen', closeButton: 'Schließen', hint: 'Zum Schließen auf das Bild oder den Hintergrund klicken oder Esc drücken.', expand: 'Vergrößern:', expandButton: 'Vergrößern' },
    docsUi: {
      browse: 'Dokumentation durchsuchen', publicGuide: 'ÖFFENTLICHER LEITFADEN', articles: 'ARTIKEL', article: 'Artikel', readArticle: 'Artikel lesen', onThisPage: 'Auf dieser Seite', previous: 'Zurück', next: 'Weiter', startHere: 'Hier beginnen',
      quickSetupHint: 'Quick Setup ist der schnellste unterstützte Weg. Konzepte und Architektur können bis nach dem ersten sinnvollen Agenten warten.',
      openQuickSetup: 'Quick Setup öffnen', repository: 'GitHub-Repository', search: 'Dokumentation durchsuchen', searchPlaceholder: 'Artikel suchen…', navLabel: 'Dokumentationsartikel', noMatches: 'Keine passenden Artikel.', paginationLabel: 'Vorheriger und nächster Artikel', viewRepository: 'Repository anzeigen', linkTo: 'Link zu', indexTitle: 'ThreadCells betreiben', indexAccent: 'ohne Repository-Archäologie.',
      indexDescription: 'Der vollständige öffentliche Leitfaden für das aktuelle ThreadCells-Produkt: von der ersten lokalen Sitzung über Anbieter, Workflows und Kapazität bis zu Fernzugriff, Backups und Wiederherstellung.',
      groups: { 'Getting started': 'Erste Schritte', 'Using ThreadCells': 'ThreadCells verwenden', Configuration: 'Konfiguration', Operations: 'Betrieb', Safety: 'Sicherheit', About: 'Über das Projekt' },
    },
  },
  ja: {
    code: 'ja', short: 'JA', name: '日本語', htmlLang: 'ja',
    title: 'ThreadCells — ネイティブ CLI コーディングエージェントのコントロールプレーン',
    description: 'コーディングエージェントを連携システムとして実行します。ThreadCells はワークフローを進め、永続履歴を保護し、Linux ホスト上のオーケストレーション環境を維持します。',
    nav: ['製品', '仕組み', 'オープンソース'], docs: 'ドキュメント', github: 'GitHub', language: '言語',
    footer: '所有するマシン上で、ネイティブ CLI コーディングエージェントを運用管理。', back: 'ページ上部へ', footerNav: 'フッターナビゲーション',
    homeLabel: 'ThreadCells ホーム', primaryNav: 'メインナビゲーション', systemReady: 'システム準備完了', controlPlane: 'コントロールプレーン',
    creator: 'Subaev Ruslan が作成・保守し、ThreadCells コミュニティが貢献しています。',
    downstream: 'AWS Labs CLI Agent Orchestrator を基にした独立・非公式の派生プロジェクトです。AWS による支援や推奨はありません。',
    screenshot: { expanded: '拡大したスクリーンショット：', close: '拡大表示を閉じる', closeButton: '閉じる', hint: '画像または背景をクリックするか、Esc キーを押して閉じます。', expand: '拡大表示：', expandButton: '拡大表示' },
    docsUi: {
      browse: 'ドキュメントを見る', publicGuide: '公開ガイド', articles: '記事', article: '記事', readArticle: '記事を読む', onThisPage: 'このページの内容', previous: '前へ', next: '次へ', startHere: 'ここから始める',
      quickSetupHint: 'Quick Setup が最短のサポート手順です。概念とアーキテクチャは、最初の実用的なエージェントを動かした後で構いません。',
      openQuickSetup: 'Quick Setup を開く', repository: 'GitHub リポジトリ', search: 'ドキュメントを検索', searchPlaceholder: '記事を検索…', navLabel: 'ドキュメント記事', noMatches: '一致する記事はありません。', paginationLabel: '前後の記事', viewRepository: 'リポジトリを見る', linkTo: 'リンク先', indexTitle: 'ThreadCells を運用する', indexAccent: 'リポジトリを掘り返さずに。',
      indexDescription: '現在の ThreadCells 製品に関する完全な公開ガイドです。最初のローカルセッションから、プロバイダー、ワークフロー、容量、リモートアクセス、バックアップ、障害復旧までを扱います。',
      groups: { 'Getting started': 'はじめに', 'Using ThreadCells': 'ThreadCells の使用', Configuration: '設定', Operations: '運用', Safety: '安全性', About: 'プロジェクトについて' },
    },
  },
}

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value)
}

export function localePrefix(locale: Locale) {
  return locale === 'en' ? '' : `/${locale}`
}

export function localizedPath(locale: Locale, path = '/') {
  const normalized = path === '/' ? '' : path.startsWith('/') ? path : `/${path}`
  return `${localePrefix(locale)}${normalized}` || '/'
}

export function landingPath(locale: Locale, anchor = '') {
  const root = localizedPath(locale).replace(/\/$/, '')
  return `${root}/${anchor}` || '/'
}

export function docsPath(locale: Locale, slug?: string) {
  return localizedPath(locale, `/docs${slug ? `/${slug}` : ''}`)
}

export function localeAlternates(path = '/') {
  return Object.fromEntries(locales.map(locale => [localeCopy[locale].htmlLang, localizedPath(locale, path)]))
}
