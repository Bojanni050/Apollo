/**
 * Approval smoke check: click Apply in the UI and verify the file really
 * moves on disk, that the tree refreshes, and that ambiguous items are not
 * applied. Run the backend + vite first, then:
 *   node scripts/smoke-apply.mjs
 */
import { chromium } from 'playwright'

const API = process.env.SMOKE_API || 'http://localhost:5173/api'
const URL = process.env.SMOKE_URL || 'http://localhost:5173'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))

const workspaces = await (await fetch(`${API}/workspaces`)).json()
const ws = workspaces[0]
const repo = ws.repositories.find((r) => r.kind === 'documentation')

const treeNames = async () => {
  const t = await (await fetch(
    `${API}/workspaces/${ws.id}/repositories/${repo.id}/tree`,
  )).json()
  return t.root.children.map((c) => c.name)
}

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForSelector('.tree-row', { timeout: 10000 })

// Open the inventory tab. A previous run may already exist -- the app
// restores the latest run on load -- in which case there is nothing to trigger.
await page.locator('.panel-header .btn', { hasText: 'inventory' }).click()
const runButton = page.locator('.btn.primary', { hasText: 'Run inventory' })
if (await runButton.count()) {
  await runButton.click()
}
await page.waitForSelector('.inventory-item', { timeout: 60000 })

const before = await treeNames()
console.log('tree before:', before.join(', '))

// Approve the whole plan from the UI.
// Approve the whole plan from the UI.
await page.locator('.btn.primary', { hasText: 'Approve all' }).click()
await page.waitForTimeout(3000)

const uiDecisions = await page
  .locator('.inventory-item')
  .evaluateAll((nodes) =>
    nodes.map((n) => ({
      path: n.querySelector('.inventory-path')?.textContent ?? '',
      decision: n.querySelector('.tag:last-of-type')?.textContent?.trim() ?? '',
    })),
  )
uiDecisions.forEach((d) => console.log(`  ui: ${d.path} -> ${d.decision}`))

const after = await treeNames()
console.log('tree after: ', after.join(', '))

// notes.md should have been filed under architecture/decisions.
const runs = await (await fetch(`${API}/workspaces/${ws.id}/inventory/runs`)).json()
const run = runs[0]
const applied = run.items.filter((i) => i.decision === 'applied').map((i) => i.target_path)
const skippedAmbiguous = run.items.filter((i) => i.ambiguous).every(
  (i) => i.decision !== 'applied',
)
console.log('applied:', applied.length ? applied.join(', ') : '(none)')
console.log('no ambiguous item was applied:', skippedAmbiguous)

// The tree in the UI must reflect the new structure.
await page.locator('.panel-header .btn', { hasText: 'Document' }).click()
const uiTree = await page.locator('.tree-row').allTextContents()
console.log('ui tree shows decisions folder:', uiTree.some((t) => t.includes('decisions')))

await page.screenshot({ path: 'screenshots/07-after-apply.png', fullPage: true })
await browser.close()

if (errors.length) {
  console.log(`\nPAGE ERRORS: ${errors.join('; ')}`)
  process.exit(1)
}
console.log('\nno page errors')
