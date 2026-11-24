import { ProjectCard } from '../components/ProjectCard'
import type { WorkspaceProject } from '../types/project'

type Props = {
  savedProjects: WorkspaceProject[]
  onHistory: () => void
  onNewProject: () => void
  onOpen: (id: string) => void
  onToggleSaved: (id: string) => void
}

export function ProjectsPage({ savedProjects, onHistory, onNewProject, onOpen, onToggleSaved }: Props) {
  return (
    <main className="container workspace-container">
      <div className="workspace-heading">
        <div><span className="eyebrow">WORKSPACE / LIBRARY</span><h1>Saved Projects</h1><p>Quick access to projects you've bookmarked.</p></div>
        <div className="workspace-actions">
          <button type="button" className="workspace-secondary" onClick={onHistory}>Browse History</button>
          <button type="button" className="workspace-primary" onClick={onNewProject}>+ New Project</button>
        </div>
      </div>
      {savedProjects.length > 0 ? (
        <div className="workspace-list">
          {[...savedProjects].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)).map(project => (
            <ProjectCard key={project.id} project={project} onOpen={onOpen} onToggleSaved={onToggleSaved} />
          ))}
        </div>
      ) : (
        <div className="workspace-empty">
          <div className="workspace-empty-icon">☆</div><h2>No saved projects yet</h2>
          <p>Open a generated project and click “Save to Projects,” or save one directly from History.</p>
          <button type="button" className="workspace-primary" onClick={onHistory}>View History</button>
        </div>
      )}
      <p className="workspace-note">Saved Projects are bookmarks stored in this browser, not a server-side project list.</p>
    </main>
  )
}
