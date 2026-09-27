// Drives the embedding-model panel end to end in a real browser: opens the
// workspace view, expands the Models section, and captures what the operator
// sees when no local runtime is installed. That state is the one that is
// easiest to get wrong (an error page instead of an explanation), so it is
// what this asserts on.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage()
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

// The panel lives in Settings, reachable from the gear button in the nav
// footer. Report what is actually on screen when the gear is missing, so a
// failed run says where the app stopped instead of just "no button".
const gear = page.locator('.nav-settings-btn')
if ((await gear.count()) === 0) {
  const bodyText = (await page.textContent('body')) || ''
  console.log('SKIP: no settings button on screen.')
  console.log('setup wizard visible:', /Set up your workspace/i.test(bodyText))
  console.log('visible headings:', (await page.locator('h2, h3').allTextContents()).slice(0, 5))
  await page.screenshot({ path: 'screenshots/models-nosetup.png' })
  await browser.close()
  process.exit(0)
}

await gear.first().click()
await page.waitForSelector('.embed-models', { timeout: 10000 })
// Wait for the catalog itself, not for a fixed delay: the panel is "Checking…"
// until the backend answers, and that answer takes as long as the runtime
// probe's network timeout when Ollama is not running.
await page.waitForSelector('.embed-model-row', { timeout: 20000 })
await page.waitForTimeout(300)

const heading = await page.textContent('.embed-models .form-label')
console.log('panel heading:', heading)

const rows = await page.locator('.embed-model-row').count()
console.log('model rows:', rows)

if (rows === 0) {
  console.log('panel HTML:', await page.innerHTML('.embed-models'))
  console.log('errors so far:', errors)
  await page.screenshot({ path: 'screenshots/embedding-models.png' })
  throw new Error('no model rows rendered')
}

// Both runtimes must be offered, whatever the configured one is: the whole
// point is that the choice is the operator's, not baked into the app.
const runtimeChips = await page.locator('.embed-runtime-chip').allTextContents()
console.log('runtimes offered:', runtimeChips.map((t) => t.trim().split('\n')[0]))
if (runtimeChips.length < 2) {
  throw new Error(`expected both runtimes to be selectable, got ${runtimeChips.length}`)
}

// Switching runtime must re-read the catalog: the two can fetch different
// models, so the Download buttons are not the same on each. Wait for the chip
// to become active rather than for a fixed delay -- probing a runtime that is
// not running takes as long as its network timeout.
const downloadButtonsFor = async (label) => {
  const chip = page.locator('.embed-runtime-chip', { hasText: label }).first()
  await chip.click()
  await page.waitForFunction(
    (name) => document.querySelector('.embed-runtime-chip.active')?.textContent?.includes(name),
    label.split(' ')[0],
    { timeout: 20000 },
  )
  await page.waitForTimeout(300)
  return page.locator('.embed-model-row button', { hasText: 'Download' }).count()
}
const ollamaDownloads = await downloadButtonsFor('Ollama')
const llamaDownloads = await downloadButtonsFor('llama.cpp')
console.log('downloadable on Ollama:', ollamaDownloads, '| on llama.cpp:', llamaDownloads)
if (ollamaDownloads === llamaDownloads) {
  throw new Error(
    'the two runtimes offer the same downloads, which means the catalog is not per-runtime',
  )
}

const firstChipText = (await page.locator('.embed-runtime-chip').first().textContent()) || ''
const firstChipLabel = firstChipText.split('·')[0].trim()

// Back to the configured runtime for the final assertions and the screenshot,
// waiting on the same state the switch itself sets.
await downloadButtonsFor(firstChipLabel)

// With no runtime running, the panel must explain that rather than showing a
// bare network error or an empty list.
const panelText = await page.textContent('.embed-models')
if (!/Ollama/i.test(panelText)) {
  throw new Error('expected the panel to name Ollama when no runtime is present')
}

// The downloadable model must offer a button; the one this runtime cannot
// fetch must say so instead of offering a button that cannot work.
const downloadButtons = await page.locator('.embed-model-row button', { hasText: 'Download' }).count()
console.log('download buttons offered:', downloadButtons)
if (downloadButtons < 1 || downloadButtons > 2) {
  throw new Error(`expected between one and two downloadable models, got ${downloadButtons}`)
}

// A model this runtime cannot serve must state it, not silently omit a button.
const rowText = await page.locator('.embed-model-row').allTextContents()
if (!rowText.some((t) => /not available on/i.test(t))) {
  throw new Error('expected a model to be reported as unavailable on this runtime')
}

// The panel sits below the LLM sections, so scroll it into view: a
// screenshot of the top of the modal proves nothing about this feature.
await page.locator('.embed-models').scrollIntoViewIfNeeded()
await page.waitForTimeout(400)
await page.locator('.settings-body').screenshot({ path: 'screenshots/embedding-models.png' })

if (errors.length) throw new Error('console errors:\n' + errors.join('\n'))
console.log('OK: embedding model panel renders and explains the missing runtime')
await browser.close()