import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'
import { existsSync } from 'node:fs'

const python = resolve(process.env.JUSTREAD_PYTHON ?? (existsSync(resolve('.venv/bin/python')) ? '.venv/bin/python' : 'backend/.venv/bin/python'))
const quotedPython = "'" + python.replace(/'/g, "'\\''") + "'"
const apiPort = Number(process.env.JUSTREAD_E2E_API_PORT ?? '8001')
const frontendPort = Number(process.env.JUSTREAD_E2E_FRONTEND_PORT ?? '5187')
const apiOrigin = `http://127.0.0.1:${apiPort}`

process.env.NO_PROXY = [process.env.NO_PROXY, '127.0.0.1', 'localhost'].filter(Boolean).join(',')
export default defineConfig({
  testDir: './tests', fullyParallel: true, timeout: 45000,
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`, trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: [
    {
      command: `${quotedPython} -m uvicorn just_read.app:app --host 127.0.0.1 --port ${apiPort}`, cwd: './backend',
      url: `${apiOrigin}/api/v1/health`, reuseExistingServer: false,
      env: { PYTHON_DOTENV_DISABLED: '1', JUSTREAD_TEST_MODE: 'true', JUSTREAD_LLM_PROVIDER: 'fixture', JUSTREAD_SEARCH_PROVIDER: 'auto', JUSTREAD_FIXTURE_DELAY_SECONDS: '0.5', JUSTREAD_DATABASE_PATH: '../.test-data/e2e-ad.sqlite3' },
    },
    {
      command: `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      url: `http://127.0.0.1:${frontendPort}`, reuseExistingServer: false,
      env: { JUSTREAD_API_ORIGIN: apiOrigin },
    },
  ],
})
