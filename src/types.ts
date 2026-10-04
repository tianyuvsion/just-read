export interface Chapter {
  id: string
  title: string
  paragraphs: string[]
}

export interface Report {
  source?: 'import'
  id: string
  title: string
  question: string
  category: string
  summary: string
  createdAt: string
  minutes: number
  chapters: Chapter[]
  chart: { label: string; value: number }[]
  bookmarks: string[]
}

export interface ResearchRequest {
  question: string
  depth: 'brief' | 'deep'
}

export interface GenerationEvent {
  step: number
  message: string
}
