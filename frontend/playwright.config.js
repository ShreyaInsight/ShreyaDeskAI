import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8766',
    launchOptions: { args: ['--no-sandbox'], ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) },
  },
  projects: [
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: { command: '../.venv/bin/python tests/login_server.py', url: 'http://127.0.0.1:8766/api/health', reuseExistingServer: false },
})
