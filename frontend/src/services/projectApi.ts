import type { BackendResult, ProjectRequest } from '../types/project'

// Set VITE_API_BASE_URL in frontend/.env.local (default: local FastAPI server).
export const API_BASE_URL = String(import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')
export const POLL_INTERVAL_MS = 2000

export function projectUrl(id: string): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(id)}`
}

function isBackendResult(value: unknown): value is BackendResult {
  if (!value || typeof value !== 'object') return false
  const data = value as Record<string, unknown>
  return typeof data.id === 'string' && typeof data.status === 'string' && Array.isArray(data.tasks)
}

function describeValidationError(item: unknown): string {
  if (!item || typeof item !== 'object') return String(item)
  const { loc, msg } = item as { loc?: unknown[]; msg?: string }
  const field = Array.isArray(loc) ? loc[loc.length - 1] : undefined
  return field ? `${String(field)}: ${msg ?? 'Invalid value'}` : msg ?? 'Invalid request'
}

async function getErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json() as { detail?: unknown }
    if (typeof data.detail === 'string') return data.detail
    if (Array.isArray(data.detail)) return data.detail.map(describeValidationError).join('; ')
  } catch { /* The server did not return JSON */ }
  return `Request failed: ${response.status}`
}

async function loadProject(url: string, options?: RequestInit): Promise<BackendResult> {
  let response: Response
  try {
    response = await fetch(url, options)
  } catch {
    throw new Error(`Could not reach ${url}. Check the backend URL, server, and CORS settings.`)
  }
  if (!response.ok) throw new Error(await getErrorMessage(response))
  const data: unknown = await response.json()
  if (!isBackendResult(data)) throw new Error('Unexpected backend JSON structure')
  return data
}

export function createProject(request: ProjectRequest): Promise<BackendResult> {
  return loadProject(`${API_BASE_URL}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export function fetchProject(id: string): Promise<BackendResult> {
  return loadProject(projectUrl(id), { cache: 'no-store' })
}

export async function checkBackendHealth(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/health`, { cache: 'no-store' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
}
