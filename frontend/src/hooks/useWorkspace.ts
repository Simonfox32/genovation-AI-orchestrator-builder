import { useCallback, useEffect, useState } from 'react'
import type { BackendResult, WorkspacePreferences, WorkspaceProject } from '../types/project'
import {
  DEFAULT_PREFERENCES,
  PROJECTS_STORAGE_KEY,
  SETTINGS_STORAGE_KEY,
  readStorage,
} from '../utils/workspace'

export function useWorkspace() {
  const [history, setHistory] = useState<WorkspaceProject[]>(() => {
    const stored = readStorage<WorkspaceProject[]>(PROJECTS_STORAGE_KEY, [])
    return Array.isArray(stored) ? stored : []
  })
  const [preferences, setPreferences] = useState<WorkspacePreferences>(() => ({
    ...DEFAULT_PREFERENCES,
    ...readStorage<Partial<WorkspacePreferences>>(SETTINGS_STORAGE_KEY, {}),
  }))

  useEffect(() => {
    try { window.localStorage.setItem(PROJECTS_STORAGE_KEY, JSON.stringify(history)) }
    catch { /* Storage may be unavailable */ }
  }, [history])

  useEffect(() => {
    try { window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(preferences)) }
    catch { /* Storage may be unavailable */ }
  }, [preferences])

  const recordProject = useCallback((project: BackendResult) => {
    setHistory(previous => {
      const existing = previous.find(item => item.id === project.id)
      const next: WorkspaceProject = {
        id: project.id,
        description: project.description || existing?.description || '',
        status: project.status,
        optimization: project.optimization,
        created_at: project.created_at || existing?.created_at || new Date().toISOString(),
        updated_at: project.updated_at || new Date().toISOString(),
        summary: project.summary ?? null,
        downloadable: Boolean(project.downloadable),
        saved: existing?.saved ?? false,
      }
      return [next, ...previous.filter(item => item.id !== project.id)].slice(0, 200)
    })
  }, [])

  const toggleSaved = useCallback((id: string) => {
    setHistory(previous => previous.map(item => item.id === id ? { ...item, saved: !item.saved } : item))
  }, [])

  const removeFromHistory = useCallback((id: string) => {
    setHistory(previous => previous.filter(item => item.id !== id))
  }, [])

  const clearHistory = useCallback(() => setHistory([]), [])

  return {
    history,
    savedProjects: history.filter(item => item.saved),
    preferences,
    setPreferences,
    recordProject,
    toggleSaved,
    removeFromHistory,
    clearHistory,
  }
}
