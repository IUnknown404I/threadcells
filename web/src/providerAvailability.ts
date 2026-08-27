import type { ProviderInfo, ProviderRuntimeInfo } from './api'
import type { TranslationKey } from './i18n'

type Runtime = Pick<ProviderRuntimeInfo, 'availability' | 'available' | 'installed' | 'state' | 'version'>

export function providerIsAvailable(provider: Runtime): boolean {
  return provider.available ?? provider.installed
}

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string

export function providerRuntimeLabel(provider: Runtime, t: Translate): string {
  if (provider.state === 'disabled') return t('provider.configurationDisabled')
  switch (provider.availability) {
    case 'INSTALLED_AND_READY':
      return provider.version ? t('provider.cliReadyVersion', { version: provider.version }) : t('provider.cliReady')
    case 'INSTALLED_NOT_AUTHENTICATED':
      return t('provider.authenticationRequired')
    case 'INSTALLED_BUT_UNHEALTHY':
      return t('provider.runtimeUnhealthy')
    case 'NOT_INSTALLED':
      return t('provider.notInstalled')
    case 'UNKNOWN':
      return provider.installed
        ? t('provider.readinessUnverified')
        : t('provider.availabilityUnavailable')
    default:
      return provider.installed ? t('provider.installed') : t('provider.notInstalled')
  }
}

export function providerSelectOption(provider: ProviderInfo, t: Translate) {
  return {
    value: provider.name,
    label: provider.name.replace(/_/g, ' '),
    sublabel: providerRuntimeLabel(provider, t),
    disabled: !providerIsAvailable(provider),
  }
}
