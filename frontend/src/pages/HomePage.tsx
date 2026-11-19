import type { FormEvent } from 'react'
import { ProjectResults } from '../components/ProjectResults'
import type { BackendResult, Optimization } from '../types/project'

type Props = {
  prompt: string
  optimization: Optimization
  loading: boolean
  error: string
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
  prompt, optimization, loading, error, result, autoRefresh, isSaved,
  onPromptChange, onOptimizationChange, onGenerate, onRefresh, onToggleSaved, onNewProject,
}: Props) {
  return (
    <>
      <header className="header"><h1>AI Orchestrator</h1><p>Build software using multiple AI models</p></header>
      <main className="container">
        <section className="card">
          <div className="project-intro"><h2>Create a Project</h2><p>Describe what you want the AI to build.</p></div>
          <form onSubmit={onGenerate}>
            <label htmlFor="prompt">Your Prompt</label>
            <textarea
              id="prompt" rows={5} placeholder="Build a task management application..."
              value={prompt} onChange={event => onPromptChange(event.target.value)}
            />
            <label htmlFor="optimization">Optimization</label>
            <select
              id="optimization" value={optimization}
              onChange={event => onOptimizationChange(event.target.value as Optimization)}
            >
              <option value="balanced">Balanced</option>
              <option value="speed">Fastest</option>
              <option value="quality">Best Quality</option>
            </select>
            <button className="generate-btn" type="submit" disabled={!prompt.trim() || loading}>
              {loading ? 'Loading...' : 'Generate Project'}
            </button>
          </form>
        </section>
        {error && <div className="error-message" role="alert"><strong>Error:</strong> {error}</div>}
        {result && (
          <ProjectResults
            project={result} loading={loading} autoRefresh={autoRefresh}
            isSaved={isSaved} onRefresh={onRefresh} onToggleSaved={onToggleSaved}
            onNewProject={onNewProject}
          />
        )}
      </main>
    </>
  )
}
