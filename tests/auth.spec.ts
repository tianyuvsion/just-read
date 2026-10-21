import { test, expect, type BrowserContext, type Page } from '@playwright/test'

// The backend contract is mocked only in tests; production has no login bypass.
async function mockAuth(context: BrowserContext, initialUser: string | null = null) {
  const state = { user: initialUser, meStatus: 200, logoutStatus: 204, businessStatus: 200, malformedLogin: false, issueSession: true, calls: [] as string[] }
  await context.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    state.calls.push(path)
    const user = (name: string) => ({ id: name, username: name, display_name: name })
    if (path.endsWith('/auth/me')) {
      return route.fulfill({ status: state.meStatus !== 200 ? state.meStatus : state.user ? 200 : 401, json: { user: state.user ? user(state.user) : null } })
    }
    if (path.endsWith('/auth/login')) {
      const body = route.request().postDataJSON()
      if (body.password !== 'test-password') return route.fulfill({ status: 401, json: {} })
      if (state.malformedLogin) return route.fulfill({ json: { success: true } })
      if (state.issueSession) state.user = body.username
      return route.fulfill({ json: { user: user(body.username) } })
    }
    if (path.endsWith('/auth/logout')) {
      if (state.logoutStatus === 204) state.user = null
      return route.fulfill({ status: state.logoutStatus, body: '' })
    }
    if (state.businessStatus !== 200) return route.fulfill({ status: state.businessStatus, json: { error: { code: 'DENIED', message: '没有权限执行此操作' } } })
    if (path.endsWith('/chat/stream')) return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"content":"账号内的回答"}\n\ndata: [DONE]\n\n' })
    return route.fulfill({ json: path.endsWith('/health') ? { ready: true, mode: 'test' } : { reports: [], sources: [], templates: [] } })
  })
  return state
}

async function dismissGuide(page: Page) {
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: '前往登录', exact: true }).click()
  await expect(page.getByLabel('账号', { exact: true })).toBeFocused()
}

async function signIn(page: Page, username = 'alice', password = 'test-password') {
  await page.getByLabel('账号', { exact: true }).fill(username)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录工作空间', exact: true }).click()
}

async function expectChat(page: Page) {
  await expect(page.getByRole('textbox', { name: '对话消息' })).toBeEnabled()
  await expect(page).toHaveURL(/#\/chat$/)
}

test('未登录只请求会话检查，所有工作区路由弹出引导，布局适配屏幕', async ({ page, context }, testInfo) => {
  const state = await mockAuth(context)
  await page.goto('/#/chat')
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.screenshot({ path: `.local/auth-ui/${testInfo.project.name}-guide.png`, fullPage: true })
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByLabel('账号', { exact: true })).toBeFocused()
  await page.screenshot({ path: `.local/auth-ui/${testInfo.project.name}-login.png`, fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  for (const path of ['/', '/reports', '/bookmarks', '/report/private', '/share/private']) {
    await page.goto('/#' + path)
    await expect(page).toHaveURL(/#\/login\?redirect=/)
    await dismissGuide(page)
  }
  expect(state.calls.every(path => path === '/api/v1/auth/me')).toBe(true)
  await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(0)
})

test('密码错误留在登录页，成功后回到原页面，刷新保持登录，退出后阻断请求', async ({ page, context }) => {
  const state = await mockAuth(context)
  await page.goto('/#/chat')
  await dismissGuide(page)
  await signIn(page, 'alice', 'wrong-password')
  await expect(page.getByRole('alert')).toContainText('账号或密码不正确')
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  expect(state.calls.every(path => path.includes('/auth/'))).toBe(true)
  await signIn(page)
  await expectChat(page)
  await page.reload()
  await expectChat(page)
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))
  expect(storage).not.toContain('test-password')
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(page).toHaveURL(/#\/login$/)
  const before = state.calls.filter(path => !path.includes('/auth/')).length
  await page.goto('/#/reports')
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(state.calls.filter(path => !path.includes('/auth/')).length).toBe(before)
})

test('登录服务未接入时关闭业务入口，可重新检查恢复', async ({ page, context }) => {
  const state = await mockAuth(context)
  state.meStatus = 404
  await page.goto('/#/chat')
  await dismissGuide(page)
  await expect(page.getByRole('status')).toContainText('登录服务尚未接入')
  expect(state.calls.every(path => path.includes('/auth/'))).toBe(true)
  state.meStatus = 200
  state.user = 'alice'
  await page.getByRole('button', { name: '重新检查' }).click()
  await expectChat(page)
})

test('成功状态码不足以登录，错误响应格式或未设置会话均不放行', async ({ page, context }) => {
  const state = await mockAuth(context)
  await page.goto('/#/login')
  state.malformedLogin = true
  await signIn(page)
  await expect(page.getByRole('alert')).toContainText('返回格式不正确')
  state.malformedLogin = false
  state.issueSession = false
  await signIn(page)
  await expect(page.getByRole('alert')).toContainText('登录状态未能保存')
  expect(state.calls.every(path => path.includes('/auth/'))).toBe(true)
})

test('业务 401 统一退出并显示失效引导，403 保留登录', async ({ page, context }) => {
  const state = await mockAuth(context, 'alice')
  await page.goto('/#/chat')
  await expectChat(page)
  state.businessStatus = 403
  await page.getByRole('textbox', { name: '对话消息' }).fill('测试权限')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.locator('.chat-alert')).toContainText('没有权限')
  await expectChat(page)
  state.businessStatus = 401
  state.user = null
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('登录已失效')
  await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(0)
  await dismissGuide(page)
  state.businessStatus = 200
  await signIn(page)
  await expectChat(page)
})

test('退出失败不会假装退出，成功退出同步到其他标签页', async ({ page, context }) => {
  const state = await mockAuth(context, 'alice')
  await page.goto('/#/chat')
  await expectChat(page)
  const second = await context.newPage()
  await second.goto('/#/chat')
  await expectChat(second)
  state.logoutStatus = 503
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('登录服务暂不可用')
  await expectChat(page)
  state.logoutStatus = 204
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(page).toHaveURL(/#\/login$/)
  await expect(second.getByRole('dialog')).toContainText('登录状态已更新')
  await expect(second.getByRole('textbox', { name: '对话消息' })).toHaveCount(0)
})

test('切换账号隔离本地对话，重新登录原账号恢复历史', async ({ page, context }) => {
  await mockAuth(context, 'alice')
  await page.goto('/#/chat')
  await expectChat(page)
  await page.getByRole('textbox', { name: '对话消息' }).fill('Alice 的私有对话')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.locator('.message-row.assistant')).toContainText('账号内的回答')
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(page).toHaveURL(/#\/login$/)
  await signIn(page, 'bob')
  await page.getByRole('link', { name: 'AI 对话', exact: true }).click()
  await expectChat(page)
  await expect(page.locator('.session-item.active')).toContainText('新对话')
  await expect(page.getByText('Alice 的私有对话', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(page).toHaveURL(/#\/login$/)
  await signIn(page)
  await page.getByRole('link', { name: 'AI 对话', exact: true }).click()
  await expect(page.locator('.message-row.user')).toContainText('Alice 的私有对话')
  await expect(page.locator('.message-row.assistant')).toContainText('账号内的回答')
})

test('登录后不接受外部跳转地址', async ({ page, context, baseURL }) => {
  await mockAuth(context)
  await page.goto('/#/login?redirect=//example.com')
  await signIn(page)
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect(page).toHaveURL(`${baseURL}/#/`)
})

test('材料下载经过统一登录检查，失效时弹窗且不下载错误页面', async ({ page, context }) => {
  const state = await mockAuth(context, 'alice')
  const source = { id: 'sample', name: 'sample.txt', warnings: [], chunks: [] }
  await context.route('**/api/v1/sources', route => route.fulfill({ json: { sources: [source] } }))
  await context.route('**/api/v1/sources/sample', route => route.fulfill({ json: source }))
  await context.route('**/api/v1/sources/sample/download', route => route.fulfill(state.user
    ? { contentType: 'text/plain', body: 'example source' }
    : { status: 401, json: {} }))
  await page.goto('/')
  await page.getByRole('button', { name: '查看定位', exact: true }).click()
  const downloads: string[] = []
  page.on('download', download => downloads.push(download.suggestedFilename()))
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载原文件', exact: true }).click()
  expect((await downloaded).suggestedFilename()).toBe('sample.txt')
  state.user = null
  await page.getByRole('button', { name: '下载原文件', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('登录已失效')
  expect(downloads).toEqual(['sample.txt'])
})
