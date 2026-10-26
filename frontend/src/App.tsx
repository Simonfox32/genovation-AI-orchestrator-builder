
import { useEffect, useState, type FormEvent } from 'react'
import './App.css'

// 1. Define the JSON structure we send to the backend (POST /projects)
type Optimization = 'balanced' | 'speed' | 'quality'

type ProjectRequest = {
  description: string
  optimization: Optimization
}

// 2. Define the JSON structure expected from the backend (GET /projects/{id})
type Status = 'pending' | 'running' | 'completed' | 'failed'

type Task = {
  id: string
  position: number
  name: string
  description: string | null
  tool: string | null
  status: Status
  attempts: number
  output: string | null
}

// Not produced by the backend yet; kept so the UI is ready when they are added
type GeneratedFile = {
  path: string
  description?: string
  content?: string
}

type BackendResult = {
  id: string
  description: string
  status: Status
  optimization: Optimization
  summary: string | null
  created_at: string
  updated_at: string
  tasks: Task[]
  files?: GeneratedFile[]
  metrics?: {
    duration_seconds?: number
    tokens_used?: number
  }
}

// Backend location: set VITE_API_BASE_URL in frontend/.env.local; defaults to the local backend
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// How often to re-check a project that is still being processed
const POLL_INTERVAL_MS = 2000

function isInProgress(status: string): boolean {
  return status === 'pending' || status === 'running'
}

// Check whether the JSON has the expected basic structure
function isBackendResult(data: unknown): data is BackendResult {
  if (typeof data !== 'object' || data === null) {
    return false
  }

  const value = data as Record<string, unknown>

  return (
    typeof value.id === 'string' &&
    typeof value.status === 'string' &&
    Array.isArray(value.tasks)
  )
}

// Turn one FastAPI validation error ({loc, msg}) into "field: message"
function describeValidationError(item: unknown): string {
  if (typeof item !== 'object' || item === null) {
    return String(item)
  }

  const { loc, msg } = item as { loc?: unknown[]; msg?: string }
  const field = Array.isArray(loc) ? loc[loc.length - 1] : undefined

  return field ? `${String(field)}: ${msg}` : msg ?? 'Invalid request'
}

// Use the backend's own error message ("detail") when there is one
async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }

    if (typeof body.detail === 'string') {
      return body.detail
    }

    if (Array.isArray(body.detail)) {
      return body.detail.map(describeValidationError).join('; ')
    }
  } catch {
    // Response body was not JSON; fall through to the generic message
  }

  return `Request failed: ${response.status}`
}

// Helper for retrieving JSON
async function loadJson(
  url: string,
  options?: RequestInit
): Promise<BackendResult> {
  let response: Response

  try {
    response = await fetch(url, options)
  } catch {
    // fetch only throws when no response arrived at all (server down, wrong URL, or CORS)
    throw new Error(`Could not reach ${url}. Is the backend running?`)
  }

  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }

  const json: unknown = await response.json()

  if (!isBackendResult(json)) {
    throw new Error('Unexpected backend JSON structure')
  }

  return json
}

function projectUrl(id: string): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(id)}`
}

function App() {
  // User inputs
  // (Preferred model and "include tests" controls are hidden until the orchestrator uses them)
  const [prompt, setPrompt] = useState('')
  const [optimization, setOptimization] = useState<Optimization>('balanced')

  // Data source: local JSON or real backend
  const [mode, setMode] = useState<'demo' | 'backend'>('demo')

  // Data received from JSON
  const [result, setResult] = useState<BackendResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Convert form inputs into a JSON-compatible object
  const requestData: ProjectRequest = {
    description: prompt.trim(),
    optimization: optimization
  }

  // While the backend is still processing, re-fetch the project every few seconds
  useEffect(() => {
    if (mode !== 'backend' || !result || !isInProgress(result.status)) {
      return
    }

    let cancelled = false

    const timer = setTimeout(async () => {
      try {
        const updatedData = await loadJson(projectUrl(result.id), {
          cache: 'no-store'
        })

        if (!cancelled) {
          setResult(updatedData)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Refresh failed')
        }
      }
    }, POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [mode, result])

  // Runs when Generate Project is clicked
  async function handleGenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    setLoading(true)
    setError('')
    setResult(null)

    try {
      let data: BackendResult

      if (mode === 'demo') {
        // Read external JSON from the public folder
        data = await loadJson('/demo-response.json', {
          cache: 'no-store'
        })
      } else {
        // Send user inputs to the real backend as JSON.
        // The backend replies immediately with a "pending" project; polling picks up progress.
        data = await loadJson(`${API_BASE_URL}/projects`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(requestData)
        })
      }

      setResult(data)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Something went wrong'
      )
    } finally {
      setLoading(false)
    }
  }

  // Reload the latest JSON data
  async function handleRefresh() {
    if (!result) return

    setLoading(true)
    setError('')

    try {
      const url =
        mode === 'demo' ? '/demo-response.json' : projectUrl(result.id)

      const updatedData = await loadJson(url, {
        cache: 'no-store'
      })

      setResult(updatedData)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Refresh failed'
      )
    } finally {
      setLoading(false)
    }
  }

  // Calculate progress from received tasks
  const completedCount =
    result?.tasks.filter(task => task.status === 'completed').length ?? 0

  const finishedCount =
    result?.tasks.filter(task => !isInProgress(task.status)).length ?? 0

  const totalTasks = result?.tasks.length ?? 0

  const progress =
    totalTasks > 0
      ? Math.round((finishedCount / totalTasks) * 100)
      : 0

  const polling =
    mode === 'backend' && result !== null && isInProgress(result.status)

  return (
    <div className="app">
      <header className="header">
        <h1>AI Orchestrator</h1>
        <p>Build software using multiple AI models</p>
      </header>

      <main className="container">
        {/* INPUT SECTION */}
        <section className="card">
          <h2>Create a Project</h2>
          <p>Describe what you want the AI to build.</p>

          <form onSubmit={handleGenerate}>
            <label htmlFor="prompt">Your Prompt</label>

            <textarea
              id="prompt"
              rows={5}
              placeholder="Build a task management application..."
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
            />

            <label htmlFor="optimization">Optimization</label>

            <select
              id="optimization"
              value={optimization}
              onChange={e => setOptimization(e.target.value as Optimization)}
            >
              <option value="balanced">Balanced</option>
              <option value="speed">Fastest</option>
              <option value="quality">Best Quality</option>
            </select>

            <label htmlFor="mode">Data Source</label>

            <select
              id="mode"
              value={mode}
              onChange={e => {
                setMode(e.target.value as 'demo' | 'backend')
                setResult(null)
                setError('')
              }}
            >
              <option value="demo">Local JSON (Demo)</option>
              <option value="backend">Backend API</option>
            </select>

            <button
              className="generate-btn"
              type="submit"
              disabled={!prompt.trim() || loading}
            >
              {loading ? 'Loading...' : 'Generate Project'}
            </button>
          </form>

          {/* Show the JSON that would be sent */}
          <details className="json-section">
            <summary>Preview JSON Request</summary>
            <pre className="json-box">
              {JSON.stringify(requestData, null, 2)}
            </pre>
          </details>
        </section>

        {/* ERROR SECTION */}
        {error && (
          <div className="error-message">
            <strong>Error:</strong> {error}
          </div>
        )}

        {/* RESULTS SECTION */}
        {result && (
          <section className="card results">
            <div className="results-header">
              <h2>Orchestration Dashboard</h2>

              <button
                className="refresh-btn"
                type="button"
                onClick={handleRefresh}
                disabled={loading}
              >
                {loading ? 'Refreshing...' : 'Refresh'}
              </button>
            </div>

            <p><strong>Project ID:</strong> {result.id}</p>
            <p>
              <strong>Overall Status:</strong> {result.status}
              {polling && ' (updating automatically...)'}
            </p>
            <p><strong>Optimization:</strong> {result.optimization}</p>

            {result.summary && (
              <p><strong>Summary:</strong> {result.summary}</p>
            )}

            <h3>Task Progress</h3>

            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${progress}%` }}
              />
            </div>

            <p>
              {finishedCount} of {totalTasks} tasks finished, {completedCount} completed ({progress}%)
            </p>

            {/* Dynamically display every task from JSON */}
            <div className="task-list">
              {result.tasks.map(task => (
                <div className="task" key={task.id}>
                  <div>
                    <strong>{task.position}. {task.name}</strong>

                    {task.description && <p>{task.description}</p>}

                    <p>
                      Tool: {task.tool ?? (task.status === 'failed' ? 'none passed' : 'Unassigned')} · Attempts: {task.attempts}
                    </p>

                    {task.output && (
                      <pre className="json-box">{task.output}</pre>
                    )}
                  </div>

                  <span className={`status ${task.status}`}>
                    {task.status}
                  </span>
                </div>
              ))}
            </div>

            {result.tasks.length === 0 && (
              <p>No tasks have been returned yet.</p>
            )}

            {/* Generated files from backend */}
            <h3>Generated Files</h3>

            {result.files && result.files.length > 0 ? (
              <div className="output-box">
                {result.files.map(file => (
                  <details key={file.path}>
                    <summary>{file.path}</summary>

                    {file.description && <p>{file.description}</p>}

                    {file.content && (
                      <pre className="json-box">{file.content}</pre>
                    )}
                  </details>
                ))}
              </div>
            ) : (
              <p>No generated files available yet.</p>
            )}

            {/* Optional execution metrics */}
            {result.metrics && (
              <>
                <h3>Execution Details</h3>

                {result.metrics.duration_seconds !== undefined && (
                  <p>
                    <strong>Execution Time:</strong>{' '}
                    {result.metrics.duration_seconds} seconds
                  </p>
                )}

                {result.metrics.tokens_used !== undefined && (
                  <p>
                    <strong>Tokens Used:</strong>{' '}
                    {result.metrics.tokens_used}
                  </p>
                )}
              </>
            )}

            {/* Raw backend response for debugging */}
            <details className="json-section">
              <summary>View Raw Backend JSON</summary>
              <pre className="json-box">
                {JSON.stringify(result, null, 2)}
              </pre>
            </details>
          </section>
        )}
      </main>
    </div>
  )
}

export default App
