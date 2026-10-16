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
}

export async function sendChatStream(
  messages: Array<{ role: 'user' | 'assistant' | 'system'; content: string }>,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  let response: Response
  try {
    response = await fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'same-origin',
      body: JSON.stringify({ messages }),
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
    callbacks.onDone()
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
