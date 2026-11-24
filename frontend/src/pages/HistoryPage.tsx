import { useState } from 'react'
import { ProjectCard } from '../components/ProjectCard'
import type { Status, WorkspaceProject } from '../types/project'
import { isInProgress } from '../utils/workspace'

type Props = {
  history: WorkspaceProject[]
  onNewProject: () => void
  onOpen: (id: string) => void
  onToggleSaved: (id: string) => void
  onRemove: (id: string) => void
  onClear: () => void
}

export function HistoryPage({ history, onNewProject, onOpen, onToggleSaved, onRemove, onClear }: Props) {
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | Status>('all')
  const [confirmClear, setConfirmClear] = useState(false)
  const visibleHistory = history
    .filter(item => statusFilter === 'all' || item.status === statusFilter)
    .filter(item => `${item.description} ${item.id} ${item.summary ?? ''}`.toLowerCase().includes(searchQuery.trim().toLowerCase()))
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))

  return (
    <main className="container workspace-container">
      <div className="workspace-heading">
        <div><span className="eyebrow">WORKSPACE / ACTIVITY</span><h1>History</h1><p>Revisit projects generated from this browser.</p></div>
        <div className="workspace-actions"><button type="button" className="workspace-primary" onClick={onNewProject}>+ New Project</button></div>
      </div>
      <div className="workspace-stats">
        <div className="workspace-stat"><strong>{history.length}</strong><span>Total projects</span></div>
        <div className="workspace-stat"><strong>{history.filter(item => item.status === 'completed').length}</strong><span>Completed</span></div>
        <div className="workspace-stat"><strong>{history.filter(item => isInProgress(item.status)).length}</strong><span>In progress</span></div>
      </div>
      <div className="workspace-toolbar">
        <input aria-label="Search project history" placeholder="Search prompts, summaries, or project IDs…" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
        <select aria-label="Filter project status" value={statusFilter} onChange={e => setStatusFilter(e.target.value as 'all' | Status)}>
          <option value="all">All statuses</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>
      </div>
      {visibleHistory.length ? (
        <div className="workspace-list">
          {visibleHistory.map(item => <ProjectCard key={item.id} project={item} onOpen={onOpen} onToggleSaved={onToggleSaved} onRemove={onRemove} />)}
        </div>
      ) : (
        <div className="workspace-empty">
          <div className="workspace-empty-icon">◷</div><h2>No matching history</h2>
          <p>{history.length ? 'Try a different search or filter.' : 'Projects you create will appear here automatically.'}</p>
          <button type="button" className="workspace-primary" onClick={onNewProject}>Create a project</button>
        </div>
      )}
      <p className="workspace-note">History is saved only in this browser. Opening a project retrieves current data from the backend. Removing a history entry does not delete it on the server.</p>
      {history.length > 0 && (
        <div className="workspace-actions">
          {confirmClear ? (
            <>
              <span className="workspace-note">Clear all browser history, including saved projects?</span>
              <button type="button" className="workspace-danger" onClick={() => { onClear(); setConfirmClear(false) }}>Yes, clear</button>
              <button type="button" className="workspace-secondary" onClick={() => setConfirmClear(false)}>Cancel</button>
            </>
          ) : (
            <button type="button" className="workspace-secondary" onClick={() => setConfirmClear(true)}>Clear local history</button>
          )}
        </div>
      )}
    </main>
  )
}
