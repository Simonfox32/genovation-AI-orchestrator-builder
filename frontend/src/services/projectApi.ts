import type { BackendResult, ProjectRequest } from '../types/project'

// Preserve the existing API contract and deployment configuration.
export const API_BASE_URL = String(import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')
export const POLL_INTERVAL_MS = 2000
export const MAX_PROMPT_LENGTH = 5000
const REQUEST_TIMEOUT_MS = 30000

export class ApiError extends Error {
  readonly status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function projectUrl(id: string): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(id)}`
}

function isBackendResult(value: unknown): value is BackendResult {
  if (!value || typeof value !== 'object') return false
  const data = value as Record<string, unknown>
  return typeof data.id === 'string' && typeof data.description === 'string' &&
    ['pending', 'running', 'completed', 'failed'].includes(String(data.status)) &&
    ['balanced', 'speed', 'quality'].includes(String(data.optimization)) && Array.isArray(data.tasks)
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
  } catch { /* The server did not return JSON. */ }
  return `Request failed: ${response.status}`
}

// Keep the timeout active while reading the response body, not just its headers.
async function request<T>(url: string, options: RequestInit, read: (response: Response) => Promise<T>): Promise<T> {
  const controller = new AbortController()
  const external = options.signal
  const cancel = () => controller.abort()
  if (external?.aborted) cancel()
  else external?.addEventListener('abort', cancel, { once: true })
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(url, { ...options, signal: controller.signal })
    if (!response.ok) throw new ApiError(await getErrorMessage(response), response.status)
    return await read(response)
  } catch (error) {
    if (external?.aborted) throw error
    if (timedOut) throw new ApiError(options.method === 'POST'
      ? 'Creation request timed out. The server may still have accepted it; check with your team before submitting again.'
      : 'The backend request timed out after 30 seconds.')
    if (error instanceof ApiError) throw error
    if (error instanceof SyntaxError) throw new ApiError('Invalid JSON response from the backend.')
    throw new ApiError(options.method === 'POST'
      ? 'Could not confirm project creation. Check the backend connection before submitting again.'
      : `Could not reach ${url}. Check the backend connection and CORS settings.`)
  } finally {
    clearTimeout(timer)
    external?.removeEventListener('abort', cancel)
  }
}

function loadProject(url: string, options: RequestInit): Promise<BackendResult> {
  return request(url, options, async response => {
    const data: unknown = await response.json()
    if (!isBackendResult(data)) throw new ApiError('Unexpected backend JSON structure')
    return data
  })
}

export function createProject(project: ProjectRequest): Promise<BackendResult> {
  return loadProject(`${API_BASE_URL}/projects`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(project),
  })
}

export async function fetchProject(id: string, signal?: AbortSignal): Promise<BackendResult> {
  const data = await loadProject(projectUrl(id), { cache: 'no-store', signal })
  if (data.id !== id) throw new ApiError('The backend returned a different project ID.')
  return data
}

export function checkBackendHealth(): Promise<void> {
  return request(`${API_BASE_URL}/health`, { cache: 'no-store' }, async response => {
    const data: unknown = await response.json()
    if (!data || typeof data !== 'object' || !('status' in data) || data.status !== 'ok') {
      throw new ApiError('Unexpected health response.')
    }
  })
}
