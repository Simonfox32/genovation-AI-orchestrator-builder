import type { WorkspaceProject } from '../types/project'
import { formatDate, shortDescription } from '../utils/workspace'

type Props = {
  project: WorkspaceProject
  onOpen: (id: string) => void
  onToggleSaved: (id: string) => void
  onRemove?: (id: string) => void
}

export function ProjectCard({ project, onOpen, onToggleSaved, onRemove }: Props) {
  return (
    <article className="workspace-project">
      <div className="workspace-project-main">
        <span className={`workspace-status ${project.status}`}>{project.status}</span>
        <div className="workspace-project-title">{shortDescription(project.description) || 'Untitled project'}</div>
        {project.summary && <p className="workspace-project-subtitle">{shortDescription(project.summary)}</p>}
        <div className="workspace-project-meta">
          <span>{formatDate(project.created_at)}</span><span aria-hidden="true">·</span>
          <span>{project.optimization}</span><span aria-hidden="true">·</span>
          <code>{project.id.slice(0, 8)}…</code>
        </div>
      </div>
      <div className="workspace-project-actions">
        <button type="button" className="workspace-primary" onClick={() => onOpen(project.id)}>Open</button>
        <button type="button" className="workspace-secondary" onClick={() => onToggleSaved(project.id)}>
          {project.saved ? '★ Saved' : '☆ Save'}
        </button>
        {onRemove && <button type="button" className="workspace-secondary" title="Remove from this browser's history only" onClick={() => onRemove(project.id)}>Remove</button>}
      </div>
    </article>
  )
}
