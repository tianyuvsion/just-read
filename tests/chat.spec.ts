import { test, expect, type Page } from '@playwright/test'

type ContextMessage = { role: string; content: string }

async function mockApi(page: Page) {
  const requests: ContextMessage[][] = []
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { user: { id: 'chat-test', username: 'tester' } } })
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
      const request = indexedDB.open('just-read-chat:chat-test', 1)
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

test('选择模板、编辑提示词、预览下载和会话恢复', async ({ page }) => {
  const catalog = [{ id: 'storybook', name: '纸感绘本', description: '纸感与交互', tags: ['科普'], palette: ['#f7f0e1'], preview_url: '/chat-templates/storybook.jpg', default_prompt: '使用纸感绘本风格，将用户报告生成 HTML。' }]
  const requests: Record<string, unknown>[] = []
  const html = '<!doctype html><html><head><title>知识库报告</title></head><body><h1>企业知识库</h1><button onclick="this.textContent=\'已展开\'">展开</button><script>try{parent.document.body.dataset.escaped="yes"}catch{}</script></body></html>'
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { user: { id: 'chat-test', username: 'tester' } } })
    if (path.endsWith('/chat/templates')) await route.fulfill({ json: { templates: catalog } })
    else if (path.endsWith('/chat/stream')) {
      requests.push(route.request().postDataJSON())
      await route.fulfill({ contentType: 'text/event-stream', body: `event: status\ndata: {"status":"continuing"}\n\ndata: ${JSON.stringify({ content: html })}\n\ndata: [DONE]\n\n` })
    } else await route.fulfill({ json: path.endsWith('/health') ? { ready: true, mode: 'test' } : { reports: [], sources: [] } })
  })
  await openChat(page)
  await page.getByRole('button', { name: '选择模板：纸感绘本' }).click()
  await page.getByText('查看或修改默认提示词', { exact: true }).click()
  await page.getByRole('textbox', { name: '模板提示词' }).fill('保留纸感，重点用绿色。')
  await page.getByLabel('附带原案例源码').uncheck()
  await page.getByRole('textbox', { name: '对话消息' }).fill('企业知识库报告：检索和生成分工。')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByRole('button', { name: '预览网页' })).toBeEnabled()
  expect(requests[0]).toMatchObject({ template_id: 'storybook', template_prompt: '保留纸感，重点用绿色。', template_reference: false })
  await page.getByRole('button', { name: '预览网页' }).click()
  const frame = page.frameLocator('iframe[title="生成的 HTML 报告"]')
  await expect(frame.getByRole('heading', { name: '企业知识库' })).toBeVisible()
  await frame.getByRole('button', { name: '展开' }).click()
  await expect(frame.getByRole('button', { name: '已展开' })).toBeVisible()
  expect(await page.locator('body').getAttribute('data-escaped')).toBeNull()
  await page.getByRole('button', { name: '手机', exact: true }).click()
  expect(await page.locator('iframe').evaluate(el => el.clientWidth)).toBeLessThanOrEqual(390)
  await page.getByRole('button', { name: '关闭预览' }).click()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 HTML' }).click()
  expect((await downloadPromise).suggestedFilename()).toMatch(/\.html$/)
  await page.reload()
  await expect(page.getByRole('button', { name: '预览网页' })).toBeEnabled()
  await expect(page.locator('.template-heading')).toContainText('纸感绘本')
  await expect(page.getByLabel('附带原案例源码')).not.toBeChecked()
  await page.getByText('查看或修改默认提示词', { exact: true }).click()
  await expect(page.getByRole('textbox', { name: '模板提示词' })).toHaveValue('保留纸感，重点用绿色。')
  await page.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(page.getByRole('button', { name: '选择模板：纸感绘本' })).toBeVisible()
  await page.getByRole('button', { name: '选择模板：纸感绘本' }).click()
  await expect(page.getByLabel('附带原案例源码')).toBeChecked()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('未完成的 HTML 不启用预览', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/v1/chat/stream', route => route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify({ content: '<!doctype html><html><body>尚未完成' })}\n\ndata: [DONE]\n\n` }))
  await openChat(page)
  await page.getByRole('textbox', { name: '对话消息' }).fill('生成报告')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByRole('button', { name: '预览网页' })).toBeDisabled()
  await expect(page.locator('.html-artifact')).toContainText('代码尚未完整')
})

for (const failure of ['模型空响应', '流提前结束']) {
  test(`${failure}显示错误并允许重新生成`, async ({ page }) => {
    await mockApi(page)
    await page.route('**/api/v1/chat/stream', route => route.fulfill({
      contentType: 'text/event-stream',
      body: failure === '模型空响应'
        ? 'event: status\ndata: {"status":"thinking"}\n\nevent: error\ndata: {"code":"OUTPUT_BUDGET_EXHAUSTED","message":"模型用完了生成额度，但未产出正文。"}\n\n'
        : 'data: {"content":"已收到的部分内容"}\n\n',
    }))
    await openChat(page)
    await page.getByRole('textbox', { name: '对话消息' }).fill('帮我生成一份介绍奶龙的报告')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await expect(page.locator('.chat-alert')).toContainText(failure === '模型空响应' ? '生成额度' : '完成前结束')
    await expect(page.locator('.message-row.assistant .text-content')).toContainText(failure === '模型空响应' ? '本次未生成正文' : '已收到的部分内容')
    await expect(page.getByRole('button', { name: '重新生成', exact: true })).toBeEnabled()
    await expect(page.getByRole('textbox', { name: '对话消息' })).toBeEnabled()
    await expect(page.locator('.generation-status')).toHaveCount(0)
  })
}
