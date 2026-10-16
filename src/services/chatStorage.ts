import type { ChatMessage } from './chat'

export interface ChatSession {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: ChatMessage[]
  templateId?: string
  templatePrompt?: string
  templateReference?: boolean
}

let database: Promise<IDBDatabase> | undefined

function openDatabase(): Promise<IDBDatabase> {
  if (database) return database
  database = new Promise((resolve, reject) => {
    const request = indexedDB.open('just-read-chat', 1)
    request.onupgradeneeded = () => {
      request.result.createObjectStore('sessions', { keyPath: 'id' })
      request.result.createObjectStore('settings')
    }
    request.onerror = () => reject(request.error)
    request.onblocked = () => reject(new Error('数据库升级被其他页面阻塞'))
    request.onsuccess = () => {
      const db = request.result
      db.onversionchange = () => { db.close(); database = undefined }
      resolve(db)
    }
  })
  void database.catch(() => { database = undefined })
  return database
}

function completed(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve()
    transaction.onabort = () => reject(transaction.error ?? new Error('本地存储事务已中止'))
    transaction.onerror = () => reject(transaction.error)
  })
}

export async function loadChatSessions() {
  const db = await openDatabase()
  const transaction = db.transaction(['sessions', 'settings'], 'readonly')
  const sessions = transaction.objectStore('sessions').getAll()
  const active = transaction.objectStore('settings').get('activeSessionId')
  await completed(transaction)
  return { sessions: sessions.result as ChatSession[], activeSessionId: active.result as string | undefined }
}

// Callers supply plain snapshots, never Vue proxies or transient UI state.
export async function saveChatSession(session: ChatSession): Promise<void> {
  const db = await openDatabase()
  const transaction = db.transaction('sessions', 'readwrite')
  transaction.objectStore('sessions').put(session)
  await completed(transaction)
}

export async function saveActiveChatSession(id: string): Promise<void> {
  const db = await openDatabase()
  const transaction = db.transaction('settings', 'readwrite')
  transaction.objectStore('settings').put(id, 'activeSessionId')
  await completed(transaction)
}

export async function deleteChatSession(id: string, nextId: string): Promise<void> {
  const db = await openDatabase()
  const transaction = db.transaction(['sessions', 'settings'], 'readwrite')
  transaction.objectStore('sessions').delete(id)
  transaction.objectStore('settings').put(nextId, 'activeSessionId')
  await completed(transaction)
}
