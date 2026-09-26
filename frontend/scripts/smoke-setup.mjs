// Verifies the setup wizard renders on a truly empty database and that the
// workspace step creates a workspace through the UI (no curl).
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage()
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(URL, { waitUntil: 'networkidle' })

// The wizard, not the old curl instructions.
const heading = await page.textContent('.setup h2')
console.log('heading:', heading)
if (!/Set up your workspace/i.test(heading || '')) {
  throw new Error(`expected the setup wizard, got: ${heading}`)
}
if ((await page.content()).includes('curl -X POST')) {
  throw new Error('the old curl instructions are still on the page')
}
await page.screenshot({ path: 'screenshots/setup-step1.png' })
console.log('heading:', heading)
if (!/Set up your workspace/i.test(heading || '')) {
  throw new Error(`expected the setup wizard, got: ${heading}`)
}
if ((await page.content()).includes('curl -X POST')) {
  throw new Error('the old curl instructions are still on the page')
}

// Fill in and submit the workspace step.
const name = `Smoke WS ${Date.now()}`
await page.fill('#ws-name', name)
await page.fill('#ws-desc', 'created by the wizard smoke test')
await page.click('.setup button[type=submit]')

// Step two should appear: the repository form.
await page.waitForSelector('#repo-path', { timeout: 10000 })
console.log('step 2 heading:', await page.textContent('.setup h2'))
await page.screenshot({ path: 'screenshots/setup-step2.png' })

// Register this repository itself as the docs source.
await page.fill('#repo-path', 'C:/Users/bojan/Projects/Document-Archtiect')
await page.click('.setup button[type=submit]')

// The app should now be the normal three-panel layout.
await page.waitForSelector('.layout', { timeout: 10000 })
await page.waitForTimeout(800)
const ws = await page.textContent('.titlebar select')
console.log('workspace now selected:', ws)
await page.screenshot({ path: 'screenshots/setup-done.png' })

if (errors.length) throw new Error('console errors:\n' + errors.join('\n'))
console.log('OK: wizard created a workspace and registered a repository')
await browser.close()
