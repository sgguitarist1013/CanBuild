const { defineConfig, devices } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './e2e', fullyParallel: true, forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: process.env.BASE_URL || 'http://127.0.0.1:8000', trace: 'retain-on-failure' },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: process.env.BASE_URL ? undefined : {
    command: 'python3 -m uvicorn app:app --host 127.0.0.1 --port 8000',
    url: 'http://127.0.0.1:8000/api/health', reuseExistingServer: !process.env.CI,
    env: { CANBUILD_LIVE_STARTUP_SMOKE: '0' },
  },
});
