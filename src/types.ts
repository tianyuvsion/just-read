export interface Chapter {
  id: string
  title: string
  paragraphs: string[]
}

export interface Report {
  version?: number
  workflow?: Workflow
  source: 'demo' | 'generated' | 'import'
  id: string
  title: string
  question: string
  category: string
  summary: string
  createdAt: string
  minutes: number
  chapters: Chapter[]
  chart: { label: string; value: number }[]
  chartMeta?: { title: string; unit: string; note: string }
  bookmarks: string[]
}

export interface ResearchRequest {
  question: string
  depth: 'brief' | 'deep'
  source_ids?: string[]
  reader?: string
  scope?: string
  enable_3d?: boolean
}

export interface GenerationEvent {
  step: number
  message: string
}

export interface ResearchTask {
  id: string
  status: 'queued' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
  step: number
  message: string
  report: Report | null
  error: { code: string; message: string } | null
  expiresAt: string | null
}

export interface SourceMaterial {
  id: string
  name: string
  title: string
  media_type: string
  kind: string
  hash: string
  revision: number
  createdAt: string
  pages?: { page?: number; paragraph?: number; text: string }[]
  chunks?: { id: string; text: string; page?: number; paragraph?: number; start?: number; end?: number }[]
  warnings?: string[]
}

export interface TaskRun {
  id?: string
  stage?: string
  step?: string | number
  status?: string
  startedAt?: string
  finishedAt?: string
  message?: string
  [key: string]: unknown
}

export interface SourceExcerpt {
  evidence_id?: string
  chunk_id?: string
  quote?: string
  locator?: { page?: number; paragraph?: number; start?: number; end?: number }
  verification?: unknown
}

// Workflow records are versioned by the server; optional sections also permit old reports.
export interface Workflow {
  schema_version?: string
  research_spec?: ResearchRequest
  sources?: (Omit<Partial<SourceMaterial>, 'chunks'> & {
    id: string; title?: string; url?: string
    chunks?: { id: string; text?: string; page?: number; paragraph?: number; start?: number; end?: number; quotes?: SourceExcerpt[] }[]
    excerpts?: SourceExcerpt[]; selection?: Record<string, unknown>
  })[]
  evidence?: { id: string; source_id?: string; chunk_id?: string; quote?: string; locator?: { page?: number; paragraph?: number; start?: number; end?: number }; verification?: { quote_match?: boolean }; statement_kind?: string }[]
  plan?: { subquestions?: { id?: string; question?: string; query?: string; actions?: unknown[] }[] }
  research_ir?: Record<string, unknown>
  datasets?: { id: string; metric?: string; unit?: string; period?: string; rows?: { id?: string; label: string; value: number; source_id?: string }[] }[]
  calculations?: { id: string; operation?: string; inputs?: unknown; formula?: string; result?: unknown; unit?: string; source_refs?: string[]; status?: string }[]
  reading_plan?: { audience?: string; goal?: string; chapter_order?: string[]; sections?: { chapter_id: string; question?: string; goal?: string; prerequisites?: unknown; evidence_refs?: string[]; visual_ids?: string[] }[] }
  narrative_blocks?: { id: string; chapter_id: string; semantic_role?: string; text?: string; claim_refs?: string[]; evidence_refs?: string[] }[]
  visuals?: VisualSpec[]
  scene?: SceneSpec | null
  validation?: unknown
  quality_review?: unknown
  ai_quality_review?: unknown
  human_review?: unknown
  manifest?: unknown
  [key: string]: unknown
}
export interface VisualSpec {
  id: string; kind: 'flow' | 'relation' | 'bar' | 'line' | 'table'; title: string; chapter_id?: string
  nodes?: { id: string; label: string }[]
  edges?: { source: string; target: string; type?: string; label?: string; evidence_refs?: string[] }[]
  labels?: string[]; series?: { name: string; values: number[] }[]; unit?: string; period?: string; provenance?: unknown
}
export interface ScenePart {
  id: string; label: string; geometry: 'box' | 'sphere' | 'cylinder'; position: [number, number, number]; size: [number, number, number]
  color: string; description?: string; evidence_refs?: string[]; dimensions_known?: boolean
}
export interface SceneSpec {
  id: string; title: string; kind: string; parts: ScenePart[]
  relations?: { source: string; target: string; type?: string }[]; annotations?: unknown[]; unknowns?: unknown[]; provenance?: unknown
}
export interface ReportVersion { version: number; title: string; createdAt: string; hash?: string; [key: string]: unknown }
