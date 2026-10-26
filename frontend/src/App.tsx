
import { useState, type FormEvent } from 'react'
import demoResponse from './data/demo-response.json'
import './App.css'

// Structure of the JSON results displayed by the frontend
type Task = {
  id: string
  name: string
  model: string
  status: string
  output?: string
}

type GeneratedFile = {
  path: string
  description?: string
  content?: string
}

type OrchestrationResult = {
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

// Sample JSON data loaded locally
const sampleData: OrchestrationResult = demoResponse

function App() {
  // User input states
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('auto')
  const [optimization, setOptimization] = useState('balanced')
  const [includeTests, setIncludeTests] = useState(true)

  // Frontend display state
  const [result, setResult] = useState<OrchestrationResult | null>(null)

  // Organize user inputs into a JSON-compatible object
  const requestData = {
    prompt: prompt.trim(),
    preferences: {
      preferred_model: model === 'auto' ? null : model,
      optimization: optimization,
      include_tests: includeTests
    }
  }

  // Display local demo results when the button is clicked
  function handleGenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    // No backend connection or API request
    setResult(sampleData)
  }

  // Calculate progress from the provided JSON
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

        {/* USER INPUT */}
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
              onChange={e => {
                setPrompt(e.target.value)
                setResult(null)
              }}
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

            <label htmlFor="optimization">
              Optimization Preference
            </label>

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

            <button
              className="generate-btn"
              type="submit"
              disabled={!prompt.trim()}
            >
              Generate Project (Demo)
            </button>
          </form>

          {/* Preview of JSON input */}
          <details className="json-section">
            <summary>View Input JSON</summary>
            <pre className="json-box">
              {JSON.stringify(requestData, null, 2)}
            </pre>
          </details>
        </section>

        {/* RESULTS FROM LOCAL JSON */}
        {result && (
          <section className="card results">
            <h2>Orchestration Dashboard</h2>
            <p className="demo-label">
              Sample results — frontend demonstration only
            </p>

            <p>
              <strong>Run ID:</strong> {result.run_id}
            </p>

            <p>
              <strong>Overall Status:</strong> {result.status}
            </p>

            {result.summary && (
              <p>
                <strong>Summary:</strong> {result.summary}
              </p>
            )}

            {/* PROGRESS */}
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

            {/* TASKS */}
            <h3>Assigned Tasks</h3>

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

                  <span
                    className={`status ${
                      ['pending', 'running', 'completed', 'failed']
                        .includes(task.status)
                        ? task.status
                        : 'pending'
                    }`}
                  >
                    {task.status}
                  </span>
                </div>
              ))}
            </div>

            {result.tasks.length === 0 && (
              <p>No tasks available.</p>
            )}

            {/* GENERATED FILES */}
            <h3>Generated Files</h3>

            {result.files && result.files.length > 0 ? (
              <div className="output-box">
                {result.files.map(file => (
                  <details key={file.path}>
                    <summary>{file.path}</summary>

                    {file.description && (
                      <p>{file.description}</p>
                    )}

                    {file.content && (
                      <pre className="json-box">
                        {file.content}
                      </pre>
                    )}
                  </details>
                ))}
              </div>
            ) : (
              <p>No generated files available.</p>
            )}

            {/* EXECUTION DETAILS */}
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

            {/* RAW JSON DISPLAY */}
            <details className="json-section">
              <summary>View Results JSON</summary>
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
