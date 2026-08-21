import { Project } from '../api'
import { SearchPicker } from './SearchPicker'

export function ProjectPicker({ projects, value, onChange, disabled }: { projects: Project[]; value: string; onChange: (value: string) => void; disabled?: boolean }) {
  return <SearchPicker items={projects.map(project => ({ value: project.projectId, label: `${project.isDefault ? 'Default · ' : ''}${project.name}`, detail: project.path }))} value={value} onChange={onChange} disabled={disabled} placeholder="Select a project to work in…" searchPlaceholder="Search projects..." emptyLabel="No projects found" />
}
