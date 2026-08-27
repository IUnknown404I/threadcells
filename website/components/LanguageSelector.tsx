import { createElement, type ReactElement } from 'react'

import {
  renderLanguageSelector,
  type LanguageSelectorElementFactory,
  type LanguageSelectorProps,
} from '../../web/src/components/LanguageSelectorCore'

const element: LanguageSelectorElementFactory<ReactElement> = (type, props, ...children) =>
  createElement(type, props, ...children)

export function LanguageSelector<Locale extends string>(props: LanguageSelectorProps<Locale>) {
  return renderLanguageSelector(element, props)
}
