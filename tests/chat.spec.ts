import { test, expect, type Page } from '@playwright/test'

type ContextMessage = { role: string; content: string }

async function mockApi(page: Page) {
  const requests: ContextMessage[][] = []
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/chat/stream')) {
      const messages = route.request().postDataJSON().messages as ContextMessage[]
      requests.push(messages)
      await route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify({ content: `回答：${messages.at(-1)!.content}` })}\n\ndata: [DONE]\n\n` })
    } else {
      await route.fulfill({ json: path.endsWith('/health') ? { ready: true, mode: 'test' } : { reports: [], sources: [] } })
    }
  })
  return requests
}

async function openChat(page: Page) {
  await page.goto('/#/chat')
  await expect(page.getByRole('button', { name: '新建对话', exact: true })).toBeEnabled()
}

async function send(page: Page, text: string) {
  await page.getByRole('textbox', { name: '对话消息' }).fill(text)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.locator('.message-row.assistant').last()).toContainText(`回答：${text}`)
  await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0)
}

test('刷新、离开页面后恢复会话；续聊上下文互相隔离', async ({ page }) => {
  const requests = await mockApi(page)
  await openChat(page)
  await send(page, '第一个话题')
  await page.getByRole('button', { name: '新建对话', exact: true }).click()
  await send(page, '第二个话题')
  expect(requests[1]).toEqual([{ role: 'user', content: '第二个话题' }])
  await page.reload()
  await expect(page.locator('.message-row')).toHaveCount(2)
  await expect(page.locator('.session-item.active')).toContainText('第二个话题')
  await page.locator('.session-select').filter({ hasText: '第一个话题' }).click()
  await send(page, '继续第一个话题')
  expect(requests[2]).toEqual([
    { role: 'user', content: '第一个话题' }, { role: 'assistant', content: '回答：第一个话题' },
    { role: 'user', content: '继续第一个话题' },
  ])
  await page.getByRole('link', { name: '调研工作台', exact: true }).click()
  await page.getByRole('link', { name: 'AI 对话', exact: true }).click()
  await expect(page.locator('.message-row')).toHaveCount(4)
  await page.reload()
  await expect(page.locator('.session-item.active')).toContainText('第一个话题')
  await expect(page.locator('.message-row')).toHaveCount(4)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('重命名、重新生成、清空和删除持久化，其他会话保持完整', async ({ page }) => {
  const requests = await mockApi(page)
  await openChat(page)
  await send(page, '保留的会话')
  await page.getByRole('button', { name: '新建对话', exact: true }).click()
  await send(page, '待编辑的会话')
  page.once('dialog', dialog => dialog.accept('修改后的名称'))
  await page.getByRole('button', { name: '重命名对话：待编辑的会话', exact: true }).click()
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0)
  await expect.poll(() => requests.length).toBe(3)
  expect(requests[2]).toEqual([{ role: 'user', content: '待编辑的会话' }])
  await page.reload()
  await expect(page.locator('.session-item.active')).toContainText('修改后的名称')
  await expect(page.locator('.message-row')).toHaveCount(2)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '清空记录' }).click()
  await page.reload()
  await expect(page.locator('.session-item.active')).toContainText('修改后的名称')
  await expect(page.locator('.message-row')).toHaveCount(0)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '删除对话：修改后的名称', exact: true }).click()
  await expect(page.locator('.session-item')).toHaveCount(1)
  await page.reload()
  await expect(page.locator('.session-item')).toHaveCount(1)
  await expect(page.locator('.message-row.assistant')).toContainText('回答：保留的会话')
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '删除对话：保留的会话', exact: true }).click()
  await expect(page.locator('.session-item.active')).toContainText('新对话')
  await page.reload()
  await expect(page.locator('.session-item')).toHaveCount(1)
  await expect(page.locator('.message-row')).toHaveCount(0)
})

// A real ReadableStream keeps callbacks pending while conversations change.
async function installPendingStream(page: Page) {
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      if (String(input).endsWith('/chat/stream')) {
        const messages = JSON.parse(String(init?.body)).messages
        if (messages.at(-1).content === '生成中的话题') {
          const encoder = new TextEncoder()
          return new Response(new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('data: {"content":"已收到的部分回答"}\n\n'))
              // Deliberately deliver a late callback after abort to exercise request isolation.
              init?.signal?.addEventListener('abort', () => {
                setTimeout(() => {
                  controller.enqueue(encoder.encode('data: {"content":"旧请求迟到的内容"}\n\ndata: [DONE]\n\n'))
                  controller.close()
                }, 100)
              }, { once: true })
            },
          }), { headers: { 'Content-Type': 'text/event-stream' } })
        }
      }
      return nativeFetch(input, init)
    }
  })
}

test('生成中切换会话会保存部分回答，旧回调不会污染新对话', async ({ page }) => {
  await mockApi(page)
  await installPendingStream(page)
  await openChat(page)
  await page.getByRole('textbox', { name: '对话消息' }).fill('生成中的话题')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.locator('.message-row.assistant')).toContainText('已收到的部分回答')
  await page.getByRole('button', { name: '新建对话', exact: true }).click()
  await send(page, '新话题')
  await page.locator('.session-select').filter({ hasText: '生成中的话题' }).click()
  await expect(page.locator('.message-row.assistant .text-content')).toHaveText('已收到的部分回答')
  await expect(page.locator('.typewriter-cursor')).toHaveCount(0)
  await page.reload()
  await expect(page.locator('.message-row.assistant .text-content')).toHaveText('已收到的部分回答')
})

test('流式生成期间刷新可恢复已经定期保存的内容', async ({ page }) => {
  await mockApi(page)
  await installPendingStream(page)
  await openChat(page)
  await page.getByRole('textbox', { name: '对话消息' }).fill('生成中的话题')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect.poll(() => page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('just-read-chat', 1)
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    const contents = await new Promise<string>((resolve, reject) => {
      const request = db.transaction('sessions').objectStore('sessions').getAll()
      request.onsuccess = () => resolve(request.result.flatMap(session => session.messages).map(message => message.content).join(''))
      request.onerror = () => reject(request.error)
    })
    db.close()
    return contents
  })).toContain('已收到的部分回答')
  await page.reload()
  await expect(page.locator('.message-row.assistant .text-content')).toHaveText('已收到的部分回答')
  await expect(page.locator('.typewriter-cursor')).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: '对话消息' })).toBeEnabled()
})

test('IndexedDB 不可用时明确提示，并允许临时对话', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => {
    IDBFactory.prototype.open = () => { throw new DOMException('Storage disabled', 'SecurityError') }
  })
  await openChat(page)
  await expect(page.locator('.chat-alert')).toContainText('本地')
  await send(page, '临时消息')
  await expect(page.locator('.chat-alert')).toContainText('无法保存')
})
