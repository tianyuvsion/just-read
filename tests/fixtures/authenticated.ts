import { test as base } from '@playwright/test'

// Existing business integration tests keep using the real fixture backend.
// Only the new, not-yet-implemented identity endpoint is mocked here.
export const test = base.extend({
  context: async ({ context }, use) => {
    await context.route('**/api/v1/auth/me', route => route.fulfill({
      json: { user: { id: 'e2e-user', username: 'tester' } },
    }))
    await use(context)
  },
})
export { expect, type Page } from '@playwright/test'
