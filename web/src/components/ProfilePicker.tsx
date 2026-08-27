import { AgentProfileInfo } from '../api'
import { SearchPicker } from './SearchPicker'
import { useI18n } from '../i18n'

export function ProfilePicker({ profiles, value, onChange, disabled }: { profiles: AgentProfileInfo[]; value: string; onChange: (value: string) => void; disabled?: boolean }) {
  const { t } = useI18n()
  return <SearchPicker items={profiles.map(profile => ({ value: profile.name, label: profile.name, detail: profile.description || t('common.noDescription') }))} value={value} onChange={onChange} disabled={disabled} placeholder={t('common.selectProfile')} searchPlaceholder={t('common.searchProfiles')} emptyLabel={t('common.noProfilesFound')} />
}
