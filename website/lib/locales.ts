export const locales = ['en', 'ru'] as const
export type Locale = (typeof locales)[number]

export const localeCopy = {
  en: {
    code: 'en', name: 'English', htmlLang: 'en',
    title: 'ThreadCells — Control plane for native CLI coding agents',
    description: 'Run coding agents as a coordinated system. ThreadCells keeps workflows moving, protects durable history, and maintains its own orchestration environment on your Linux host.',
    nav: ['Product', 'How it works', 'Open source'], docs: 'Docs', github: 'GitHub', language: 'Language',
    footer: 'Operational control for native CLI coding agents—on the machine you own.', back: 'Back to top', footerNav: 'Footer navigation',
  },
  ru: {
    code: 'ru', name: 'Русский', htmlLang: 'ru',
    title: 'ThreadCells — панель управления нативными CLI-агентами для разработки',
    description: 'Запускайте кодовых агентов как согласованную систему. ThreadCells ведёт рабочие процессы, защищает долговременную историю и поддерживает среду оркестрации на вашем Linux-хосте.',
    nav: ['Продукт', 'Как это работает', 'Открытый код'], docs: 'Документация', github: 'GitHub', language: 'Язык',
    footer: 'Операционное управление нативными CLI-агентами для разработки — на вашей машине.', back: 'Наверх', footerNav: 'Навигация в подвале',
  },
} as const

export const landingPath = (locale: Locale, anchor = '') => `${locale === 'ru' ? '/ru' : ''}/${anchor}`.replace(/\/$/, anchor ? '' : '/')
