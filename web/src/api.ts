export interface Asset {
  id: string
  kind: string
  filename: string
  path: string
  url: string
  width: number
  height: number
  duration: number
  meta: Record<string, any>
}

export interface Project {
  id: string
  name: string
  description: string
  platform: string
  duration: number
  ratio: string
  status: string
  cover?: Asset | null
  has_video?: boolean
  created_at?: string
  updated_at?: string
}

export interface Product {
  id: string
  project_id: string
  title: string
  category: string
  selling_points: string[]
  audience: string
  price: string
  tone: string
  brief: Record<string, any>
}

export interface Shot {
  id: string
  idx: number
  seconds: number
  beat: string
  scene: string
  camera: string
  action: string
  voiceover: string
  on_screen: string
  prompt: string
  status: string
  error: string
  keyframe_asset_id?: string | null
  subtitle_asset_id?: string | null
  clip_asset_id?: string | null
  keyframe_path?: string | null
  subtitle_path?: string | null
  clip_path?: string | null
}

export interface Script {
  id: string
  project_id: string
  skill_id: string
  platform: string
  ratio: string
  duration: number
  status: string
  version: number
  skill_version?: number
  title: string
  style_bible: Record<string, any>
  cover: Record<string, any>
  caption: { title?: string; body?: string; hashtags?: string[] }
  compliance: Array<Record<string, any>>
  shots: Shot[]
  source?: string
}

export interface Video {
  id: string
  project_id: string
  script_id: string
  path: string
  url: string
  cover: Asset | null
  duration: number
  width: number
  height: number
  status: string
  created_at?: string
}

export interface Generation {
  id: string
  project_id: string
  stage: string
  status: string
  progress: number
  message: string
  result: Record<string, any>
  error: string
  created_at?: string
  updated_at?: string
  project_name?: string
}

export interface Skill {
  id: string
  name: string
  category: string
  goal: string
  version: number
  beats: number
  suitable_categories: string[]
  platforms: string[]
  scenes: string[]
  palette: string[]
  score?: number
  reason?: string
  config?: Record<string, any>
}

export interface ProjectDetail {
  project: Project
  product: Product | null
  assets: Asset[]
  script: Script | null
  video: Video | null
  latest_generation: Generation | null
}

export interface SettingsInfo {
  app_name: string
  version: string
  ai_mode: string
  video_provider: string
  image_provider: string
  llm_provider: string
  ark_key_present: boolean
  mcp_configured: boolean
  mcp_video_url: string
  mcp_video_resolution: string
  rest_art_configured: boolean
  deepseek_configured: boolean
  rest_art_base_url: string
  rest_image_params: string
  models: Record<string, string>
  data_dir: string
  video: { width: number; height: number; fps: number }
  sync_jobs: boolean
  ffmpeg: { available: boolean; codec?: string; path?: string; error?: string; libx264?: boolean }
  skills: number
}

const BASE = '/api'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {})
  if (!(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${BASE}${path}`, { ...init, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (data as any).detail
    const message =
      typeof detail === 'string' ? detail : detail?.message || `请求失败（${response.status}）`
    throw new Error(message)
  }
  return data as T
}

export function mediaUrl(path?: string | null): string {
  if (!path) return ''
  if (path.startsWith('http') || path.startsWith('/')) return path
  return `/media/${path}`
}

export const api = {
  health: () => request<Record<string, any>>('/health'),
  stats: () => request<{ projects: number; videos: number; running: number; skills: number }>('/stats'),
  platforms: () => request<Array<Record<string, any>>>('/platforms'),

  projects: () => request<Project[]>('/projects'),
  project: (id: string) => request<ProjectDetail>(`/projects/${id}`),
  createProject: (body: Record<string, any>) =>
    request<Project>('/projects', { method: 'POST', body: JSON.stringify(body) }),
  updateProject: (id: string, body: Record<string, any>) =>
    request<Project>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  upload: (id: string, files: File[]) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return request<Asset[]>(`/projects/${id}/upload`, { method: 'POST', body: form })
  },
  analyze: (id: string) => request<Product>(`/projects/${id}/analyze`, { method: 'POST' }),
  updateProduct: (id: string, body: Record<string, any>) =>
    request<Product>(`/products/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  skills: () => request<Skill[]>('/skills'),
  skill: (id: string) => request<Skill>(`/skills/${id}`),
  recommend: (projectId: string) =>
    request<{ items: Skill[] }>('/skills/recommend', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId }),
    }),

  createScript: (projectId: string, body: Record<string, any>) =>
    request<Script>(`/projects/${projectId}/scripts`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  script: (id: string) => request<Script>(`/scripts/${id}`),
  patchScript: (id: string, body: Record<string, any>) =>
    request<Script>(`/scripts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  previs: (id: string, force = false) =>
    request<Generation>(`/scripts/${id}/previs?force=${force ? 'true' : 'false'}`, { method: 'POST' }),
  render: (id: string) => request<Generation>(`/scripts/${id}/render`, { method: 'POST' }),
  regenerateShot: (id: string) => request<Generation>(`/shots/${id}/regenerate`, { method: 'POST' }),
  generation: (id: string) => request<Generation>(`/generations/${id}`),
  tasks: () => request<Generation[]>('/tasks'),

  video: (projectId: string) => request<Video | null>(`/projects/${projectId}/video`),
  exportVideo: (id: string) =>
    request<{ id: string; download_url: string; filename: string; size: number }>(
      `/videos/${id}/export`,
      { method: 'POST' },
    ),
  downloadUrl: (exportId: string) => `/api/exports/${exportId}/download`,
  videoDownloadUrl: (videoId: string) => `/api/videos/${videoId}/download`,

  settings: () => request<SettingsInfo>('/settings'),
  updateSettings: (payload: {
    ai_mode?: string
    video_provider?: string
    image_provider?: string
    llm_provider?: string
  }) =>
    request<Record<string, any>>('/settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  mcpStatus: () =>
    request<{ ok: boolean; configured: boolean; tools?: string[]; error?: string }>('/mcp/status'),
  restStatus: () =>
    request<{ ok: boolean; configured: boolean; total?: number; error?: string }>('/rest/status'),
}

export const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export async function waitGeneration(
  id: string,
  onTick?: (generation: Generation) => void,
  timeoutMs = 20 * 60 * 1000,
): Promise<Generation> {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const generation = await api.generation(id)
    onTick?.(generation)
    if (generation.status === 'succeeded' || generation.status === 'failed') {
      return generation
    }
    await sleep(1200)
  }
  throw new Error('任务超时，请到任务中心查看')
}

export function formatDate(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function formatSize(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
