import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Sidebar } from './components/Sidebar'
import { HomePage } from './pages/HomePage'
import { HistoryPage } from './pages/HistoryPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { SettingsPage } from './pages/SettingsPage'
import { HelpPage } from './pages/HelpPage'
import { useWorkspace } from './hooks/useWorkspace'
import { API_BASE_URL, ApiError, createProject, fetchProject, POLL_INTERVAL_MS, MAX_PROMPT_LENGTH } from './services/projectApi'
import type { BackendResult, Optimization, Tab } from './types/project'
import { isInProgress, readStorage } from './utils/workspace'
import './App.css'

const ACTIVE_KEY = `genovation-active-project-v1:${API_BASE_URL}`
const RETRY_INTERVAL_MS = 5000

function message(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.'
}

function App() {
  const { history, savedProjects, preferences, setPreferences, recordProject,
    toggleSaved, removeFromHistory, clearHistory } = useWorkspace()
  const [menuOpen, setMenuOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<Tab>('home')
  const [prompt, setPrompt] = useState('')
  const [optimization, setOptimization] = useState<Optimization>(preferences.defaultOptimization)
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    const stored = readStorage<unknown>(ACTIVE_KEY, null)
    return typeof stored === 'string' && stored ? stored : null
  })
  const selectedRef = useRef(selectedId)
  const pollController = useRef<AbortController | null>(null)
  const [result, setResult] = useState<BackendResult | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const submitLock = useRef(false)
  const [refreshing, setRefreshing] = useState(Boolean(selectedId))
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [actionError, setActionError] = useState('')
  const [pollError, setPollError] = useState('')
  const [retrying, setRetrying] = useState(false)

  // Only one GET loop owns the selected project. A manual refresh or selection
  // change cancels the previous GET and its scheduled timer.
  useEffect(() => {
    if (!selectedId) return
    const id = selectedId
    const controller = new AbortController()
    pollController.current = controller
    let timer: ReturnType<typeof setTimeout> | undefined
    const current = () => !controller.signal.aborted && selectedRef.current === id

    async function poll() {
      try {
        const data = await fetchProject(id, controller.signal)
        if (!current()) return
        setResult(data)
        recordProject(data)
        setPollError('')
        setRetrying(false)
        if (preferences.autoRefresh && isInProgress(data.status)) {
          timer = setTimeout(() => void poll(), POLL_INTERVAL_MS)
        }
      } catch (error) {
        if (!current()) return
        const canRetry = preferences.autoRefresh && (!(error instanceof ApiError) || error.status === undefined || error.status >= 500 || error.status === 429 || error.status === 408)
        setPollError(message(error))
        setRetrying(canRetry)
        if (canRetry) timer = setTimeout(() => void poll(), RETRY_INTERVAL_MS)
      } finally {
        if (current()) setRefreshing(false)
      }
    }
    void poll()
    return () => {
      controller.abort()
      clearTimeout(timer)
      if (pollController.current === controller) pollController.current = null
    }
  }, [selectedId, refreshVersion, preferences.autoRefresh, recordProject])

  function navigate(tab: Tab) {
    setActiveTab(tab)
    setMenuOpen(false)
  }

  function selectProject(id: string | null, data: BackendResult | null = null) {
    pollController.current?.abort()
    selectedRef.current = id
    setSelectedId(id)
    setResult(data)
    setPollError('')
    setRetrying(false)
    setRefreshing(Boolean(id && !data))
    setRefreshVersion(value => value + 1)
    try {
      if (id) window.localStorage.setItem(ACTIVE_KEY, JSON.stringify(id))
      else window.localStorage.removeItem(ACTIVE_KEY)
    } catch { /* The current session still works when storage is unavailable. */ }
  }

  const activeRun = Boolean(selectedId && (!result || isInProgress(result.status)))
  const generationBlocked = submitting || activeRun

  async function handleGenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitLock.current || generationBlocked) return
    const description = prompt.trim()
    if (!description || description.length > MAX_PROMPT_LENGTH) {
      setActionError(`Enter a prompt between 1 and ${MAX_PROMPT_LENGTH} characters.`)
      return
    }
    submitLock.current = true
    setSubmitting(true)
    setActionError('')
    try {
      // Do not retry POST automatically: a timed-out request may have created a project.
      const data = await createProject({ description, optimization })
      recordProject(data)
      selectProject(data.id, data)
    } catch (error) {
      setActionError(message(error))
    } finally {
      submitLock.current = false
      setSubmitting(false)
    }
  }

  function handleRefresh() {
    if (!selectedId || submitLock.current) return
    pollController.current?.abort()
    setRefreshing(true)
    setActionError('')
    setRefreshVersion(value => value + 1)
  }

  function openProject(id: string) {
    if (submitLock.current) return
    if (id !== selectedId && activeRun && !window.confirm('Open a different project? The current run will continue on the backend and can be reopened from History.')) return
    setActionError('')
    if (id === selectedId) handleRefresh()
    else selectProject(id)
    navigate('home')
  }

  function newProject() {
    if (submitLock.current) return
    // Explicitly leaving a run is allowed; its history entry remains available.
    if (activeRun && !window.confirm('Leave this project? This does not cancel its backend execution. You can reopen it from History if its ID was saved.')) return
    selectProject(null)
    setPrompt('')
    setOptimization(preferences.defaultOptimization)
    setActionError('')
    navigate('home')
  }

  function handleRemove(id: string) {
    if (id === selectedId) {
      window.alert('Open a different project or choose New Project before removing this entry.')
      return
    }
    removeFromHistory(id)
  }

  function handleClear() {
    if (selectedId || submitLock.current) {
      window.alert('Leave the current project before clearing browser history.')
      return
    }
    clearHistory()
  }

  return (
    <div className="app">
      <Sidebar menuOpen={menuOpen} activeTab={activeTab} savedCount={savedProjects.length}
        onToggle={() => setMenuOpen(open => !open)} onClose={() => setMenuOpen(false)} onNavigate={navigate} />
      {activeTab === 'home' && <HomePage prompt={prompt} optimization={optimization}
        loading={submitting} refreshing={refreshing} generationBlocked={generationBlocked}
        selectedId={selectedId} error={actionError} pollError={pollError} retrying={retrying}
        result={result} autoRefresh={preferences.autoRefresh}
        isSaved={history.some(item => item.id === result?.id && item.saved)}
        onPromptChange={setPrompt} onOptimizationChange={setOptimization} onGenerate={handleGenerate}
        onRefresh={handleRefresh} onToggleSaved={toggleSaved} onNewProject={newProject} />}
      {activeTab === 'history' && <HistoryPage history={history} onNewProject={newProject}
        onOpen={openProject} onToggleSaved={toggleSaved} onRemove={handleRemove} onClear={handleClear} />}
      {activeTab === 'projects' && <ProjectsPage savedProjects={savedProjects} onHistory={() => navigate('history')}
        onNewProject={newProject} onOpen={openProject} onToggleSaved={toggleSaved} />}
      {activeTab === 'settings' && <SettingsPage preferences={preferences} onPreferencesChange={setPreferences} />}
      {activeTab === 'help' && <HelpPage onNewProject={() => navigate('home')} />}
    </div>
  )
}

export default App
