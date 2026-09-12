import type { Locale } from '@/lib/locales'

export type AnalyticsCopy = {
  title: string
  text: string
  allow: string
  decline: string
  details: string
  settings: string
  notice: string
  choice: string
  close: string
}

export const analyticsCopy: Record<Locale, AnalyticsCopy> = {
  en: {
    title: 'Analytics',
    text: 'ThreadCells sends limited cookieless visit signals. Detailed Google Analytics starts only with your permission.',
    allow: 'Allow', decline: 'Decline', details: 'Details', settings: 'Analytics settings', close: 'Close', choice: 'Your analytics choice',
    notice: 'Before you choose, and after you decline, Google may receive limited cookieless visit signals. Analytics cookies and persistent analytics identifiers are not used while consent is denied. Choosing Allow enables detailed Google Analytics. Advertising and personalization are not used. You can change or withdraw this choice here at any time.',
  },
  ru: {
    title: 'Аналитика',
    text: 'ThreadCells отправляет ограниченный сигнал посещения без cookies. Подробная аналитика Google включается только с вашего согласия.',
    allow: 'Разрешить', decline: 'Отклонить', details: 'Подробнее', settings: 'Настройки аналитики', close: 'Закрыть', choice: 'Ваш выбор аналитики',
    notice: 'До выбора и после отказа Google может получать ограниченный сигнал посещения без cookies. Пока согласие отклонено, analytics cookies и постоянные идентификаторы аналитики не используются. Разрешить включает подробную аналитику Google. Реклама и персонализация не используются. Здесь можно изменить или отозвать выбор в любое время.',
  },
  'zh-CN': {
    title: '分析',
    text: 'ThreadCells 会发送有限的无 Cookie 访问讯号。只有在您允许后，才会启用详细的 Google Analytics。',
    allow: '允许', decline: '拒绝', details: '详情', settings: '分析设置', close: '关闭', choice: '您的分析选择',
    notice: '在您选择前及拒绝后，Google 可能会收到有限的无 Cookie 访问讯号。拒绝同意时不会使用分析 Cookie 或持久分析标识符。选择允许会启用详细的 Google Analytics。不会使用广告或个性化。您可随时在此更改或撤回选择。',
  },
  es: {
    title: 'Analítica',
    text: 'ThreadCells envía señales de visita limitadas sin cookies. Google Analytics detallado solo se inicia con tu permiso.',
    allow: 'Permitir', decline: 'Rechazar', details: 'Detalles', settings: 'Configuración de analítica', close: 'Cerrar', choice: 'Tu elección de analítica',
    notice: 'Antes de elegir y después de rechazar, Google puede recibir señales de visita limitadas sin cookies. Mientras se rechaza el consentimiento, no se usan cookies ni identificadores persistentes de analítica. Permitir activa Google Analytics detallado. No usamos publicidad ni personalización. Puedes cambiar o retirar esta elección aquí en cualquier momento.',
  },
  'pt-BR': {
    title: 'Análises',
    text: 'O ThreadCells envia sinais limitados de visita sem cookies. O Google Analytics detalhado só começa com sua permissão.',
    allow: 'Permitir', decline: 'Recusar', details: 'Detalhes', settings: 'Configurações de análises', close: 'Fechar', choice: 'Sua escolha de análises',
    notice: 'Antes da sua escolha e depois da recusa, o Google pode receber sinais limitados de visita sem cookies. Com o consentimento negado, não são usados cookies nem identificadores persistentes de análises. Permitir ativa o Google Analytics detalhado. Não usamos publicidade nem personalização. Você pode mudar ou retirar essa escolha aqui a qualquer momento.',
  },
  de: {
    title: 'Analysen',
    text: 'ThreadCells sendet begrenzte cookielose Besuchssignale. Detailliertes Google Analytics startet nur mit deiner Erlaubnis.',
    allow: 'Erlauben', decline: 'Ablehnen', details: 'Details', settings: 'Analyse-Einstellungen', close: 'Schließen', choice: 'Deine Analysewahl',
    notice: 'Vor deiner Wahl und nach einer Ablehnung kann Google begrenzte cookielose Besuchssignale erhalten. Bei verweigerter Einwilligung werden keine Analyse-Cookies oder dauerhaften Analysekennungen verwendet. Erlauben aktiviert detailliertes Google Analytics. Werbung und Personalisierung werden nicht verwendet. Du kannst diese Wahl hier jederzeit ändern oder widerrufen.',
  },
  ja: {
    title: '分析',
    text: 'ThreadCells は限定的な Cookie を使わない訪問シグナルを送信します。詳細な Google Analytics は許可後にのみ開始します。',
    allow: '許可', decline: '拒否', details: '詳細', settings: '分析設定', close: '閉じる', choice: '分析に関する選択',
    notice: '選択前および拒否後にも、Google は限定的な Cookie を使わない訪問シグナルを受け取る場合があります。同意が拒否されている間、分析 Cookie と永続的な分析識別子は使用されません。許可すると詳細な Google Analytics が有効になります。広告とパーソナライズは使用しません。ここでいつでも選択を変更または撤回できます。',
  },
}
