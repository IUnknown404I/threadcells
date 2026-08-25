export type LandingCapability = { title: string; copy: string }
export type LandingStep = { title: string; copy: string }

export type LandingLocaleData = {
  /** Exact canonical-English string passed as the first t() argument -> natural translation. */
  strings: Readonly<Record<string, string>>
  signals: readonly [string, string, string, string]
  capabilities: readonly [LandingCapability, LandingCapability, LandingCapability, LandingCapability]
  steps: readonly [LandingStep, LandingStep, LandingStep, LandingStep]
  jsonLdFeatures: readonly [string, string, string, string, string, string, string]
}
