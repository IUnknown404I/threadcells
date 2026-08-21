import { AgentProfileInfo } from '../api'
import { SearchPicker } from './SearchPicker'

export function ProfilePicker({ profiles, value, onChange, disabled }: { profiles: AgentProfileInfo[]; value: string; onChange: (value: string) => void; disabled?: boolean }) {
  return <SearchPicker items={profiles.map(profile => ({ value: profile.name, label: profile.name, detail: profile.description || 'No description provided' }))} value={value} onChange={onChange} disabled={disabled} placeholder="Select a profile..." searchPlaceholder="Search profiles..." emptyLabel="No profiles found" />
}
