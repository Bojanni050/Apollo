/**
 * Manual UI smoke check. Drives the real app in a browser to confirm the
 * three panels render, documents open, and conversation works end to end.
 *
 * Run with both the backend and vite dev server already running:
 *   node scripts/smoke.mjs
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'
const OUT = 'screenshots'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const errors = []
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text())
})
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(URL, { waitUntil: 'networkidle' })

// The document tree loads from the API on mount.
await page.waitForSelector('.tree-row', { timeout: 10000 })
const treeItems = await page.locator('.tree-row').count()
console.log(`tree rows: ${treeItems}`)

const title = await page.locator('.titlebar h1').textContent()
console.log(`title: ${title}`)

const modes = await page.locator('.modes button').allTextContents()
console.log(`modes: ${modes.join(', ')}`)

await page.screenshot({ path: `${OUT}/01-initial.png` })

// Open a document from the tree; it should render in the context panel.
await page.locator('.tree-row', { hasText: 'memory.md' }).first().click()
await page.waitForSelector('.doc-viewer', { timeout: 10000 })
const heading = await page.locator('.doc-viewer h1').first().textContent()
console.log(`document heading: ${heading}`)
await page.screenshot({ path: `${OUT}/02-document.png` })

// Send a message and wait for the assistant reply with sources.
await page.locator('.composer textarea').fill('How does the memory component work?')
await page.locator('.btn.primary', { hasText: 'Send' }).click()
await page.waitForSelector('.citation', { timeout: 60000 })
const citations = await page.locator('.citation .path').allTextContents()
console.log(`citations: ${citations.length}`)
citations.forEach((c) => console.log(`  - ${c}`))
await page.screenshot({ path: `${OUT}/03-conversation.png` })

// Switch mode: the conversation must survive it.
const before = await page.locator('.message').count()
await page.locator('.modes button', { hasText: 'Investigate' }).click()
await page.waitForTimeout(500)
const after = await page.locator('.message').count()
console.log(`messages before mode switch: ${before}, after: ${after}`)
await page.screenshot({ path: `${OUT}/04-mode-switch.png` })

// Run the inventory and check the plan renders.
await page.locator('.panel-header .btn', { hasText: 'inventory' }).click()
await page.locator('.btn.primary', { hasText: 'Run inventory' }).click()
await page.waitForSelector('.inventory-item', { timeout: 60000 })
const items = await page.locator('.inventory-item').count()
console.log(`inventory items: ${items}`)
const ambiguous = await page.locator('.inventory-item.ambiguous').count()
console.log(`ambiguous items: ${ambiguous}`)
await page.screenshot({ path: `${OUT}/05-inventory.png`, fullPage: true })

// Collapse the context panel.
await page.locator('.titlebar .btn', { hasText: 'Hide context' }).click()
await page.waitForTimeout(300)
const collapsed = await page.locator('.layout.context-collapsed').count()
console.log(`context collapsed: ${collapsed === 1}`)
await page.screenshot({ path: `${OUT}/06-collapsed.png` })

await browser.close()

if (errors.length) {
  console.log(`\nCONSOLE ERRORS (${errors.length}):`)
  errors.forEach((e) => console.log(`  ${e}`))
  process.exit(1)
}
console.log('\nno console errors')
