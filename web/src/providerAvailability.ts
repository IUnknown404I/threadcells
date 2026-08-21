import type { ProviderInfo, ProviderRuntimeInfo } from './api'

type Runtime = Pick<ProviderRuntimeInfo, 'availability' | 'available' | 'installed' | 'state' | 'version'>

export function providerIsAvailable(provider: Runtime): boolean {
  return provider.available ?? provider.installed
}

export function providerRuntimeLabel(provider: Runtime): string {
  if (provider.state === 'disabled') return 'Configuration disabled'
  switch (provider.availability) {
    case 'INSTALLED_AND_READY':
      return provider.version ? `CLI ready · ${provider.version}` : 'CLI installed and ready'
    case 'INSTALLED_NOT_AUTHENTICATED':
      return 'CLI installed · Authentication required'
    case 'INSTALLED_BUT_UNHEALTHY':
      return 'CLI installed · Runtime unhealthy'
    case 'NOT_INSTALLED':
      return 'Provider CLI not installed'
    case 'UNKNOWN':
      return provider.installed
        ? 'CLI installed · Readiness unverified'
        : 'Runtime availability unavailable'
    default:
      return provider.installed ? 'Provider CLI installed' : 'Provider CLI not installed'
  }
}

export function providerSelectOption(provider: ProviderInfo) {
  return {
    value: provider.name,
    label: provider.name.replace(/_/g, ' '),
    sublabel: providerRuntimeLabel(provider),
    disabled: !providerIsAvailable(provider),
  }
}
