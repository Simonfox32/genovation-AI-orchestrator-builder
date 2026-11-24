import type { BackendResult } from '../types/project'
import { projectUrl } from '../services/projectApi'
import { isInProgress } from '../utils/workspace'

type Props = {
  project: BackendResult
  loading: boolean
  autoRefresh: boolean
  pollError: boolean
  isSaved: boolean
  onRefresh: () => void
  onToggleSaved: (id: string) => void
  onNewProject: () => void
}

export function ProjectResults({ project, loading, autoRefresh, pollError, isSaved, onRefresh, onToggleSaved, onNewProject }: Props) {
  const completedCount = project.tasks.filter(task => task.status === 'completed').length
  const failedCount = project.tasks.filter(task => task.status === 'failed').length
  const finishedCount = completedCount + failedCount
  const totalTasks = project.tasks.length
  const progress = totalTasks ? Math.round(finishedCount / totalTasks * 100) : 0
  const failedProgress = totalTasks ? (failedCount / totalTasks) * 100 : 0
  const successProgress = totalTasks ? (completedCount / totalTasks) * 100 : 0
  const polling = autoRefresh && !pollError && isInProgress(project.status)

  return (
    <section className="card results">
      <div className="results-header">
        <h2>Orchestration Dashboard</h2>
        <button className="refresh-btn" type="button" onClick={onRefresh} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <p><strong>Project ID:</strong> {project.id}</p>
      <p><strong>Overall Status:</strong> {project.status}{polling && ' (updating automatically...)'}</p>
      <p><strong>Optimization:</strong> {project.optimization}</p>
      {project.summary && <p><strong>Summary:</strong> {project.summary}</p>}

      <h3>Observed Task Results</h3>
      <div className="progress-bar" role="img" aria-label={`${completedCount} completed, ${failedCount} failed, ${totalTasks - finishedCount} unfinished`}>
        <div className="progress-fill" style={{ width: `${successProgress}%` }} />
        <div className="progress-failed" style={{ width: `${failedProgress}%` }} />
      </div>
      <p>{finishedCount} of {totalTasks} known tasks finished ({progress}%). {completedCount} completed · {failedCount} failed · {totalTasks - finishedCount} unfinished.</p>
      {isInProgress(project.status) && <p className="workspace-note">The orchestrator may discover more tasks. This is not a project completion percentage.</p>}

      <div className="task-list">
        {project.tasks.map(task => (
          <div className="task" key={task.id}>
            <div>
              <strong>{task.position}. {task.name}</strong>
              {task.description && <p>{task.description}</p>}
              <p>Tool: {task.tool ?? (task.status === 'failed' ? 'none passed' : 'Unassigned')} · Attempts: {task.attempts}</p>
              {task.output && <pre className="json-box">{task.output}</pre>}
            </div>
            <span className={`status ${task.status}`}>{task.status}</span>
          </div>
        ))}
      </div>
      {!project.tasks.length && <p>No tasks have been returned yet.</p>}

      <div className="workspace-inline-actions">
        <button type="button" className="workspace-secondary workspace-save-btn" onClick={() => onToggleSaved(project.id)}>
          {isSaved ? '★ Saved to Projects' : '☆ Save to Projects'}
        </button>
        <button type="button" className="workspace-secondary workspace-save-btn" disabled={loading} onClick={onNewProject}>+ New Project</button>
      </div>

      <h3>Generated Files</h3>
      {project.downloadable && (
        <a className="download-btn" href={`${projectUrl(project.id)}/download`} download>
          Download project (.zip)
        </a>
      )}
      {project.files && project.files.length > 0 ? (
        <div className="output-box">
          {project.files.map(file => (
            <details key={file.path}>
              <summary>{file.path}</summary>
              {file.description && <p>{file.description}</p>}
              {file.content != null && <pre className="json-box">{file.content}</pre>}
            </details>
          ))}
        </div>
      ) : <p>No generated files available yet.</p>}

      {project.metrics && (
        <>
          <h3>Execution Details</h3>
          {project.metrics.duration_seconds !== undefined && <p><strong>Execution Time:</strong> {project.metrics.duration_seconds} seconds</p>}
          {project.metrics.tokens_used !== undefined && <p><strong>Tokens Used:</strong> {project.metrics.tokens_used}</p>}
        </>
      )}
    </section>
  )
}
