// Shared models for the backend API and the browser-local workspace.
export type Optimization = 'balanced' | 'speed' | 'quality'
export type Status = 'pending' | 'running' | 'completed' | 'failed'
export type Tab = 'home' | 'history' | 'projects' | 'settings' | 'help'

export type ProjectRequest = {
  description: string
  optimization: Optimization
}

export type Task = {
  id: string
  position: number
  name: string
  description: string | null
  tool: string | null
  status: Status
  attempts: number
  output: string | null
}

export type GeneratedFile = {
  path: string
  description?: string
  content?: string
}

export type BackendResult = {
  id: string
  description: string
  status: Status
  optimization: Optimization
  summary: string | null
  created_at: string
  updated_at: string
  tasks: Task[]
  files?: GeneratedFile[]
  downloadable?: boolean
  metrics?: {
    duration_seconds?: number
    tokens_used?: number
  }
}

export type WorkspaceProject = {
  id: string
  description: string
  status: Status
  optimization: Optimization
  created_at: string
  updated_at: string
  summary: string | null
  downloadable: boolean
  saved: boolean
}

export type WorkspacePreferences = {
  defaultOptimization: Optimization
  autoRefresh: boolean
}
