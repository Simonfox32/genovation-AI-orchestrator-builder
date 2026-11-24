import type { Status, WorkspacePreferences } from '../types/project'

export const PROJECTS_STORAGE_KEY = 'genovation-workspace-projects-v1'
export const SETTINGS_STORAGE_KEY = 'genovation-workspace-settings-v1'
export const DEFAULT_PREFERENCES: WorkspacePreferences = {
  defaultOptimization: 'balanced',
  autoRefresh: true,
}

export function readStorage<T>(key: string, fallback: T): T {
  try {
    const saved = window.localStorage.getItem(key)
    return saved ? JSON.parse(saved) as T : fallback
  } catch {
    return fallback
  }
}

export function isInProgress(status: Status): boolean {
  return status === 'pending' || status === 'running'
}

export function formatDate(value: string): string {
  if (!value) return 'Unknown date'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Unknown date' : date.toLocaleString()
}

export function shortDescription(value: string): string {
  return value.length > 180 ? `${value.slice(0, 180)}…` : value
}
