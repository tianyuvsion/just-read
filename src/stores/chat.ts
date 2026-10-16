import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { sendChatStream, type ChatMessage } from '../services/chat'
import { deleteChatSession, loadChatSessions, saveActiveChatSession, saveChatSession, type ChatSession } from '../services/chatStorage'

export interface DisplayMessage extends ChatMessage {
  isStreaming?: boolean
  copied?: boolean
}
interface Session extends ChatSession { messages: DisplayMessage[] }

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<Session[]>([])
  const activeSessionId = ref('')
  const ready = ref(false)
  const storageWarning = ref('')
  const errorMessage = ref('')
  const isStreaming = ref(false)
  const activeSession = computed(() => sessions.value.find(session => session.id === activeSessionId.value))
  const messages = computed(() => activeSession.value?.messages ?? [])
  const sortedSessions = computed(() => [...sessions.value].sort((a, b) => b.updatedAt - a.updatedAt))
  let initialization: Promise<void> | undefined
  let controller: AbortController | undefined
  let saveTimer: ReturnType<typeof setTimeout> | undefined
  let animation: { message: DisplayMessage; queue: string[]; timer: ReturnType<typeof setInterval>; done: boolean } | undefined

  async function store(operation: Promise<void>) {
    try { await operation }
    catch { storageWarning.value = '对话无法保存到本地，请检查浏览器存储权限或剩余空间；刷新可能丢失未保存内容。' }
  }

  function persist(session: Session) {
    // Snapshot synchronously so delayed writes cannot pick up another conversation.
    return store(saveChatSession({
      id: session.id, title: session.title, createdAt: session.createdAt, updatedAt: session.updatedAt,
      messages: session.messages.map(message => ({
        id: message.id, role: message.role, timestamp: message.timestamp,
        content: message.content + (animation?.message === message ? animation.queue.join('') : ''),
      })),
    }))
  }

  function flush() {
    clearTimeout(saveTimer)
    saveTimer = undefined
    if (activeSession.value) void persist(activeSession.value)
  }

  function scheduleSave() {
    if (saveTimer) return
    saveTimer = setTimeout(flush, 200)
  }

  function stopGenerating() {
    // Invalidate callbacks before aborting: an old request must never affect a new one.
    const previous = controller
    controller = undefined
    previous?.abort()
    if (animation) {
      clearInterval(animation.timer)
      animation.message.content += animation.queue.join('')
      animation = undefined
    }
    isStreaming.value = false
    for (const message of messages.value) message.isStreaming = false
    flush()
  }

  function createSession() {
    stopGenerating()
    const now = Date.now()
    const session: Session = { id: crypto.randomUUID(), title: '新对话', createdAt: now, updatedAt: now, messages: [] }
    sessions.value.unshift(session)
    activeSessionId.value = session.id
    errorMessage.value = ''
    void persist(session)
    void store(saveActiveChatSession(session.id))
  }

  function initialize() {
    if (initialization) return initialization
    initialization = (async () => {
      try {
        const saved = await loadChatSessions()
        sessions.value = saved.sessions
        activeSessionId.value = saved.sessions.some(session => session.id === saved.activeSessionId)
          ? saved.activeSessionId! : sortedSessions.value[0]?.id ?? ''
      } catch {
        storageWarning.value = '无法读取本地历史对话，当前对话暂存于内存；请检查浏览器是否允许 IndexedDB。'
      }
      if (!activeSession.value) createSession()
      ready.value = true
    })()
    return initialization
  }

  function selectSession(id: string) {
    if (id === activeSessionId.value || !sessions.value.some(session => session.id === id)) return
    stopGenerating()
    activeSessionId.value = id
    errorMessage.value = ''
    void store(saveActiveChatSession(id))
  }

  function renameSession(id: string, title: string) {
    const session = sessions.value.find(item => item.id === id)
    if (!session || !title.trim()) return
    session.title = title.trim().slice(0, 80)
    void persist(session)
  }

  async function removeSession(id: string) {
    if (!sessions.value.some(session => session.id === id)) return
    if (id === activeSessionId.value) stopGenerating()
    const nextId = id === activeSessionId.value
      ? sortedSessions.value.find(session => session.id !== id)?.id ?? '' : activeSessionId.value
    // Keep the history visible if deletion fails, so the user can retry.
    try { await deleteChatSession(id, nextId) }
    catch { storageWarning.value = '删除失败，本地历史记录未删除，请检查浏览器存储后重试。'; return }
    sessions.value = sessions.value.filter(session => session.id !== id)
    if (activeSessionId.value === id) {
      activeSessionId.value = nextId
      errorMessage.value = ''
      if (!nextId) createSession()
    }
  }

  function clearChat() {
    stopGenerating()
    if (!activeSession.value) return
    activeSession.value.messages = []
    activeSession.value.updatedAt = Date.now()
    errorMessage.value = ''
    flush()
  }

  async function send(text: string, regenerate = false) {
    text = text.trim()
    if (!ready.value || !text || isStreaming.value || !activeSession.value) return
    const session = activeSession.value
    if (regenerate) {
      const lastUserIndex = session.messages.map(message => message.role).lastIndexOf('user')
      if (lastUserIndex < 0) return
      session.messages.splice(lastUserIndex + 1)
    } else {
      if (!session.messages.length && session.title === '新对话') session.title = text.replace(/\s+/g, ' ').slice(0, 32)
      session.messages.push({ id: crypto.randomUUID(), role: 'user', content: text, timestamp: Date.now() })
    }
    const payload = session.messages.filter(message => message.content).map(({ role, content }) => ({ role, content }))
    session.messages.push({ id: crypto.randomUUID(), role: 'assistant', content: '', timestamp: Date.now(), isStreaming: true })
    const assistant = session.messages[session.messages.length - 1]!
    session.updatedAt = Date.now()
    errorMessage.value = ''
    isStreaming.value = true
    const request = new AbortController()
    controller = request
    // Save the user's message before waiting for the network.
    await persist(session)
    if (controller !== request) return
    const playback = {
      message: assistant, queue: [] as string[], done: false,
      timer: setInterval(() => {
        if (controller !== request) return
        const count = playback.queue.length > 30 ? 6 : playback.queue.length > 10 ? 3 : 1
        assistant.content += playback.queue.splice(0, count).join('')
        if (playback.done && !playback.queue.length) stopGenerating()
      }, 16),
    }
    animation = playback
    await sendChatStream(payload, {
      onToken(token) {
        if (controller !== request) return
        for (const char of token) playback.queue.push(char)
        session.updatedAt = Date.now()
        scheduleSave()
      },
      onError(error) { if (controller === request) { errorMessage.value = error; stopGenerating() } },
      onDone() { if (controller === request) { playback.done = true; flush() } },
    }, request.signal)
  }

  function regenerate() {
    const lastUser = [...messages.value].reverse().find(message => message.role === 'user')
    if (lastUser) void send(lastUser.content, true)
  }

  // Periodic writes protect received tokens even if unload cannot finish an IDB transaction.
  window.addEventListener('pagehide', stopGenerating)
  document.addEventListener('visibilitychange', () => { if (document.hidden) flush() })

  return { sessions, sortedSessions, activeSessionId, activeSession, messages, ready, storageWarning,
    errorMessage, isStreaming, initialize, createSession, selectSession, renameSession, removeSession,
    clearChat, send, regenerate, stopGenerating }
})
