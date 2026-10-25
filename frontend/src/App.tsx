
import { useState, type FormEvent } from 'react'
import './App.css'

// 1. Define the JSON structure we send to the backend
type ProjectRequest = {
  prompt: string
  preferences: {
    preferred_model: string | null
    optimization: string
    include_tests: boolean
  }
}

// 2. Define the JSON structure expected from the backend
type Task = {
  id: string
  name: string
  model: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  output?: string
}

type GeneratedFile = {
  path: string
  description?: string
  content?: string
}

type BackendResult = {
  run_id: string
  status: string
  summary?: string
  tasks: Task[]
  files?: GeneratedFile[]
  metrics?: {
    duration_seconds?: number
    tokens_used?: number
  }
}

// Backend location (configured later)
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

// Check whether the JSON has the expected basic structure
function isBackendResult(data: unknown): data is BackendResult {
  if (typeof data !== 'object' || data === null) {
    return false
  }

  const value = data as Record<string, unknown>

  return (
    typeof value.run_id === 'string' &&
    typeof value.status === 'string' &&
    Array.isArray(value.tasks)
  )
}

// Helper for retrieving JSON
async function loadJson(
  url: string,
  options?: RequestInit
): Promise<BackendResult> {
  const response = await fetch(url, options)

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`)
  }

  const json: unknown = await response.json()

  if (!isBackendResult(json)) {
    throw new Error('Unexpected backend JSON structure')
  }

  return json
}

function App() {
  // User inputs
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('auto')
  const [optimization, setOptimization] = useState('balanced')
  const [includeTests, setIncludeTests] = useState(true)

  // Data source: local JSON or real backend
  const [mode, setMode] = useState<'demo' | 'backend'>('demo')

  // Data received from JSON
  const [result, setResult] = useState<BackendResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Convert form inputs into a JSON-compatible object
  const requestData: ProjectRequest = {
    prompt: prompt.trim(),
    preferences: {
      preferred_model: model === 'auto' ? null : model,
      optimization: optimization,
      include_tests: includeTests
    }
  }

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
        // Send user inputs to the real backend as JSON
        data = await loadJson(`${API_BASE_URL}/api/orchestrate`, {
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
        mode === 'demo'
          ? '/demo-response.json'
          : `${API_BASE_URL}/api/runs/${encodeURIComponent(result.run_id)}`

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

  const totalTasks = result?.tasks.length ?? 0

  const progress =
    totalTasks > 0
      ? Math.round((completedCount / totalTasks) * 100)
      : 0

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

            <label htmlFor="model">Preferred AI Model</label>

            <select
              id="model"
              value={model}
              onChange={e => setModel(e.target.value)}
            >
              <option value="auto">Automatic Selection</option>
              <option value="gpt">GPT</option>
              <option value="claude">Claude</option>
              <option value="gemini">Gemini</option>
            </select>

            <label htmlFor="optimization">Optimization</label>

            <select
              id="optimization"
              value={optimization}
              onChange={e => setOptimization(e.target.value)}
            >
              <option value="balanced">Balanced</option>
              <option value="speed">Fastest</option>
              <option value="quality">Best Quality</option>
            </select>

            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={includeTests}
                onChange={e => setIncludeTests(e.target.checked)}
              />
              Include automated tests
            </label>

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
                {loading ? 'Refreshing...' : 'Refresh JSON'}
              </button>
            </div>

            <p><strong>Run ID:</strong> {result.run_id}</p>
            <p><strong>Overall Status:</strong> {result.status}</p>

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
              {completedCount} of {totalTasks} tasks completed ({progress}%)
            </p>

            {/* Dynamically display every task from JSON */}
            <div className="task-list">
              {result.tasks.map(task => (
                <div className="task" key={task.id}>
                  <div>
                    <strong>{task.name}</strong>
                    <p>Assigned AI: {task.model || 'Unassigned'}</p>

                    {task.output && (
                      <p>Output: {task.output}</p>
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
