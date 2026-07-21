import { defineConfig, devices } from '@playwright/test'

// O4.1 E2E：起 core（sqlite throwaway + 種子使用者）+ nuxt dev，跑登入→lobby 煙霧測試。
// access TTL 設短（3s）以便測 refresh 自動換發路徑。正式 E2E（compose + CI）於 O4.6 收斂。
// 用 MATSO 專屬非預設埠（開發機共用，3000/8000 可能被其他專案占用，同 mariadb 3307 慣例）。
const CORE_PORT = 8100
const NUXT_PORT = 3100
const E2E_DB = 'sqlite:////tmp/matso_e2e_pw.db'
const JWT = 'e2e-secret-key-at-least-32-bytes-long-000'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${NUXT_PORT}`,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command:
        `bash -c "rm -f /tmp/matso_e2e_pw.db && ` +
        `DATABASE_URL=${E2E_DB} SEED_USERNAME=commander SEED_PASSWORD=exercise SEED_SESSION=1 ` +
        `uv run python ops/tools/seed_dev_user.py && ` +
        `DATABASE_URL=${E2E_DB} JWT_SECRET=${JWT} ACCESS_TOKEN_TTL_S=3 STUB_GATEWAY=1 ` +
        `CORS_ORIGINS=http://localhost:${NUXT_PORT} ` +
        `uv run uvicorn app.main:app --port ${CORE_PORT}"`,
      cwd: '..',
      url: `http://localhost:${CORE_PORT}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --port ${NUXT_PORT}`,
      port: NUXT_PORT,
      env: { NUXT_PUBLIC_API_BASE: `http://localhost:${CORE_PORT}` },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
})
