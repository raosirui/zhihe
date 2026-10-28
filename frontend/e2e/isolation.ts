import { readFileSync } from 'node:fs'
import { chromium, type FullConfig } from '@playwright/test'

type Account = { username: string; password: string }
export type Fixtures = {
  runId: string
  origin: string
  users: { admin: Account; buyer: Account; author: Account; legacy: Account }
  toolkits: { free: number; paid: number; empty: number; paidNoPreview: number; featured: number }
  lessons: { freeFirst: number; freeSecond: number; paidPreview: number; paidLocked: number }
}
export function fixtures(): Fixtures {
  const file = process.env.ZHIHE_E2E_FIXTURES, origin = process.env.ZHIHE_E2E_API_URL
  if (!file || !origin) throw new Error('E2E BLOCKED: set ZHIHE_E2E_API_URL and ZHIHE_E2E_FIXTURES for a freshly isolated factory server; no existing backend fallback is allowed.')
  const url = new URL(origin)
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.port === '6000' || !['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('E2E BLOCKED: only an explicit non-6000 loopback isolated server origin is permitted.')
  const data = JSON.parse(readFileSync(file, 'utf8')) as Fixtures
  if (!data.runId || new URL(data.origin).origin !== url.origin || !data.users?.buyer?.username || !data.users?.legacy?.password || !Number.isInteger(data.toolkits?.paid) || !Number.isInteger(data.lessons?.paidLocked)) throw new Error('E2E BLOCKED: incomplete or mismatched isolation fixture file.')
  return data
}
async function assertBrowserRuntime(config: FullConfig) {
  const use = config.projects[0].use
  let browser: Awaited<ReturnType<typeof chromium.launch>>
  try {
    browser = await chromium.launch({ ...use.launchOptions, headless: use.headless ?? true, channel: use.channel })
  } catch (error) {
    throw new Error(`E2E BLOCKED: Chromium cannot start; install its browser/runtime libraries before acceptance. ${error instanceof Error ? error.message : String(error)}`)
  }
  try {
    const page = await browser.newPage()
    // No backend or external font request: verify actual text glyph geometry, not
    // just CSS visibility/line height, which can pass with an empty font runtime.
    await page.setContent('<!doctype html><meta charset="utf-8"><span id="latin">Readable Latin</span><span id="cjk">可见中文课程</span><style>span { font: 16px Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }</style>')
    const glyphs = await page.evaluate(() => ['latin', 'cjk'].map(id => {
      const element = document.getElementById(id)!
      const range = document.createRange()
      range.selectNodeContents(element)
      const bounds = range.getBoundingClientRect()
      return { id, width: bounds.width, height: bounds.height }
    }))
    if (glyphs.some(bounds => bounds.width <= 0 || bounds.height <= 0)) {
      throw new Error(`E2E BLOCKED: Chromium cannot render Latin/CJK text (${JSON.stringify(glyphs)}). Install usable browser fonts/fontconfig or set FONTCONFIG_FILE to an owned rootless configuration; UI visibility assertions must not be weakened.`)
    }
  } finally {
    await browser.close()
  }
}

export default async function assertIsolation(config: FullConfig) {
  const data = fixtures()
  const response = await fetch(`${data.origin}/api/__qa__/ready`, { signal: AbortSignal.timeout(10_000) })
  if (!response.ok) throw new Error(`E2E BLOCKED: isolation readiness returned ${response.status}`)
  const ready = await response.json()
  if (ready.runId !== data.runId) throw new Error('E2E BLOCKED: server runId does not match the temporary fixture; refusing to use this backend.')
  await assertBrowserRuntime(config)
}
