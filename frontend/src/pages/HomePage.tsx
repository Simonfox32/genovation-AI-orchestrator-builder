import type { FormEvent } from 'react'
import { ProjectResults } from '../components/ProjectResults'
import { MAX_PROMPT_LENGTH } from '../services/projectApi'
import type { BackendResult, Optimization } from '../types/project'

type Props = {
  prompt: string
  optimization: Optimization
  loading: boolean
  refreshing: boolean
  generationBlocked: boolean
  selectedId: string | null
  error: string
  pollError: string
  retrying: boolean
  result: BackendResult | null
  autoRefresh: boolean
  isSaved: boolean
  onPromptChange: (value: string) => void
  onOptimizationChange: (value: Optimization) => void
  onGenerate: (event: FormEvent<HTMLFormElement>) => void
  onRefresh: () => void
  onToggleSaved: (id: string) => void
  onNewProject: () => void
}

export function HomePage({
  prompt, optimization, loading, refreshing, generationBlocked, selectedId, error, pollError, retrying,
  result, autoRefresh, isSaved, onPromptChange, onOptimizationChange, onGenerate, onRefresh,
  onToggleSaved, onNewProject,
}: Props) {
  return (
    <>
      <header className="header"><h1>AI Orchestrator</h1><p>Build software using multiple AI models</p></header>
      <main className="container">
        <section className="card">
          <div className="project-intro"><h2>Create a Project</h2><p>Describe what you want the AI to build.</p></div>
          <form onSubmit={onGenerate}>
            <label htmlFor="prompt">Your Prompt</label>
            <textarea id="prompt" rows={5} placeholder="Build a task management application..."
              value={prompt} maxLength={MAX_PROMPT_LENGTH} required disabled={loading}
              aria-describedby="prompt-count" onChange={event => onPromptChange(event.target.value)} />
            <small id="prompt-count" className="prompt-count">{prompt.length.toLocaleString()} / {MAX_PROMPT_LENGTH.toLocaleString()} characters</small>
            <label htmlFor="optimization">Optimization</label>
            <select id="optimization" value={optimization} disabled={loading}
              onChange={event => onOptimizationChange(event.target.value as Optimization)}>
              <option value="balanced">Balanced</option>
              <option value="speed">Fastest</option>
              <option value="quality">Best Quality</option>
            </select>
            <button className="generate-btn" type="submit" disabled={!prompt.trim() || generationBlocked || prompt.length > MAX_PROMPT_LENGTH}>
              {loading ? 'Submitting…' : generationBlocked ? 'Project selected / in progress' : 'Generate Project'}
            </button>
            {generationBlocked && !loading && <small className="workspace-note">Wait for the current run, or explicitly leave it using New Project. Existing runs are not cancelled.</small>}
          </form>
        </section>
        {error && <div className="error-message" role="alert"><strong>Error:</strong> {error}</div>}
        {pollError && <div className="error-message" role="status"><strong>Could not refresh:</strong> {pollError}{retrying && ' Retrying automatically in 5 seconds.'}</div>}
        {selectedId && !result && <section className="card" aria-busy={refreshing}>
          <h2>{pollError ? 'Project unavailable' : 'Opening project…'}</h2>
          <p>Project ID: {selectedId}</p>
          <div className="workspace-inline-actions">
            <button type="button" className="refresh-btn" disabled={refreshing || loading} onClick={onRefresh}>Retry</button>
            <button type="button" className="workspace-secondary" disabled={loading} onClick={onNewProject}>New Project</button>
          </div>
        </section>}
        {result && <ProjectResults project={result} loading={refreshing || loading} autoRefresh={autoRefresh}
          pollError={Boolean(pollError)} isSaved={isSaved} onRefresh={onRefresh} onToggleSaved={onToggleSaved} onNewProject={onNewProject} />}
      </main>
    </>
  )
}
