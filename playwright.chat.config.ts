import { defineConfig, devices } from '@playwright/test'

// Chat persistence uses real browser IndexedDB and mocked SSE; no model key or Python service required.
export default defineConfig({
  testDir: './tests', testMatch: 'chat.spec.ts', fullyParallel: true, timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:5188', trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5188 --strictPort',
    url: 'http://127.0.0.1:5188', reuseExistingServer: false,
  },
})
