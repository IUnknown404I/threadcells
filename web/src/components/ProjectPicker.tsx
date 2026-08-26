import { Project } from '../api'
import { SearchPicker } from './SearchPicker'
import { useI18n } from '../i18n'

export function ProjectPicker({ projects, value, onChange, disabled }: { projects: Project[]; value: string; onChange: (value: string) => void; disabled?: boolean }) {
  const { t } = useI18n()
  return <SearchPicker items={projects.map(project => ({ value: project.projectId, label: `${project.isDefault ? `${t('common.default')} · ` : ''}${project.name}`, detail: project.path }))} value={value} onChange={onChange} disabled={disabled} placeholder={t('common.selectProject')} searchPlaceholder={t('common.searchProjects')} emptyLabel={t('common.noProjectsFound')} />
}
