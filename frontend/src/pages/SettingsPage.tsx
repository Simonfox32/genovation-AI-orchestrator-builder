import { useState } from 'react'
import { API_BASE_URL, checkBackendHealth } from '../services/projectApi'
import type { Optimization, WorkspacePreferences } from '../types/project'

type Props = {
  preferences: WorkspacePreferences
  onPreferencesChange: (preferences: WorkspacePreferences) => void
}

type ApiState = 'idle' | 'checking' | 'online' | 'offline'

export function SettingsPage({ preferences, onPreferencesChange }: Props) {
  const [apiState, setApiState] = useState<ApiState>('idle')
  const [apiMessage, setApiMessage] = useState('')

  async function testConnection() {
    setApiState('checking')
    setApiMessage('')
    try {
      await checkBackendHealth()
      setApiState('online')
      setApiMessage('Backend is responding.')
    } catch (error) {
      setApiState('offline')
      setApiMessage(error instanceof Error ? error.message : 'Connection failed')
    }
  }

  return (
    <main className="container workspace-container">
      <div className="workspace-heading">
        <div><span className="eyebrow">WORKSPACE / PREFERENCES</span><h1>Settings</h1><p>Customize your project builder and check API connectivity.</p></div>
      </div>
      <div className="workspace-settings-grid">
        <section className="card">
          <h2>Generation defaults</h2>
          <p className="workspace-project-subtitle">These settings affect new projects created in this browser.</p>
          <div className="workspace-setting">
            <span><strong>Default optimization</strong><small>Preselected when you create a new project.</small></span>
            <select
              aria-label="Default optimization" value={preferences.defaultOptimization}
              onChange={event => onPreferencesChange({ ...preferences, defaultOptimization: event.target.value as Optimization })}
            >
              <option value="balanced">Balanced</option><option value="speed">Fastest</option><option value="quality">Best Quality</option>
            </select>
          </div>
          <div className="workspace-setting">
            <span><strong>Auto-refresh status</strong><small>Poll the backend every two seconds while a project is running.</small></span>
            <input
              type="checkbox" aria-label="Auto-refresh status" checked={preferences.autoRefresh}
              onChange={event => onPreferencesChange({ ...preferences, autoRefresh: event.target.checked })}
            />
          </div>
        </section>
        <section className="card">
          <h2>Backend connection</h2>
          <p className="workspace-project-subtitle">The API URL is controlled by your frontend environment configuration.</p>
          <div className="workspace-api-url">{API_BASE_URL}</div>
          <button type="button" className="workspace-secondary" disabled={apiState === 'checking'} onClick={() => void testConnection()}>
            {apiState === 'checking' ? 'Checking…' : 'Test connection'}
          </button>
          {apiState !== 'idle' && (
            <div role="status" className={`workspace-connection ${apiState}`}>
              {apiState === 'online' ? '● Connected' : apiState === 'offline' ? '● Unavailable' : '● Checking…'} {apiMessage}
            </div>
          )}
          <p className="workspace-note">To change this URL, edit <code>frontend/.env.local</code>, set <code>VITE_API_BASE_URL</code>, then restart Vite.</p>
        </section>
      </div>
      <p className="workspace-note">Preferences and browser project history are saved on this device with localStorage. They are not synced across accounts or devices.</p>
    </main>
  )
}
