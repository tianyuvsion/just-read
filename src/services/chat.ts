export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: number
}

export interface StreamCallbacks {
  onToken: (token: string) => void
  onError: (error: string) => void
  onDone: () => void
  onStatus?: (status: string) => void
}

export interface ChatTemplate {
  id: string
  name: string
  description: string
  tags: string[]
  palette: string[]
  preview_url: string
  default_prompt: string
}

export interface TemplateOptions {
  template_id?: string
  template_prompt?: string
  template_reference?: boolean
}

export async function loadChatTemplates(): Promise<ChatTemplate[]> {
  const health = await fetch('/api/v1/health', { credentials: 'same-origin' })
  if (!health.ok) throw new Error('无法连接对话服务')
  const response = await fetch('/api/v1/chat/templates', { credentials: 'same-origin' })
  if (!response.ok) throw new Error('模板加载失败')
  const data = await response.json()
  return Array.isArray(data.templates) ? data.templates : []
}

export async function sendChatStream(
  messages: Array<{ role: 'user' | 'assistant' | 'system'; content: string }>,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
  options: TemplateOptions = {},
): Promise<void> {
  let response: Response
  try {
    response = await fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'same-origin',
      body: JSON.stringify({ messages, ...options }),
      signal,
    })
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      callbacks.onDone()
      return
    }
    callbacks.onError('无法连接对话服务，请检查网络或后端服务。')
    return
  }

  if (!response.ok) {
    let message = `服务异常（HTTP ${response.status}）`
    try {
      const errBody = await response.json()
      if (errBody?.error?.message) {
        message = errBody.error.message
      }
    } catch {
      // 保留默认提示
    }
    callbacks.onError(message)
    return
  }

  if (!response.body) {
    callbacks.onError('服务器未返回数据流。')
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let currentEvent = 'message'

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const rawLine of lines) {
        const line = rawLine.trim()
        if (!line || line.startsWith(':')) continue

        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          const dataStr = line.slice(6).trim()
          if (dataStr === '[DONE]') {
            callbacks.onDone()
            return
          }

          try {
            const data = JSON.parse(dataStr)
            if (currentEvent === 'status') {
              callbacks.onStatus?.(data.status)
              currentEvent = 'message'
              continue
            }
            if (currentEvent === 'error' || data.code || data.error) {
              const msg = data.message || (typeof data.error === 'string' ? data.error : data.error?.message) || '生成中断'
              callbacks.onError(msg)
              return
            }
            if (data.content) {
              callbacks.onToken(data.content)
            }
          } catch {
            // 忽略非 JSON 数据行
          }
          currentEvent = 'message'
        }
      }
    }
    callbacks.onError('数据流在完成前结束，已保留收到的内容，请重试。')
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      callbacks.onDone()
    } else {
      callbacks.onError('数据流传输异常中断，请重试。')
    }
  } finally {
    reader.releaseLock()
  }
}
