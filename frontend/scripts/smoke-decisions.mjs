/**
 * Smoke test for Open Questions & Decisions UI integration.
 * Tests:
 * 1. Viewing, creating, and updating open questions
 * 2. Viewing, creating, and inspecting architectural decisions
 * 3. Explicit approval workflow with confirmation dialog
 * 4. Post-approval inspection of ADR Markdown preview and Git diff
 *
 * Usage:
 *   node scripts/smoke-decisions.mjs
 */
import { chromium } from 'playwright'

const API = process.env.SMOKE_API || 'http://localhost:5173/api'
const URL = process.env.SMOKE_URL || 'http://localhost:5173'

console.log(`Connecting to ${URL}...`)
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text())
})

try {
  await page.goto(URL, { waitUntil: 'networkidle' })
  await page.waitForSelector('.titlebar h1', { timeout: 10000 })
  console.log('✓ App loaded successfully')

  // Wait for workspace to load
  await page.waitForSelector('.tree-row', { timeout: 10000 })
  console.log('✓ Workspace tree loaded')

  // --- 1. Open Questions Workflow ---
  console.log('\nTesting Open Questions Workflow...')
  const questionsBtn = page.locator('.titlebar .modes button', { hasText: 'Questions' })
  await questionsBtn.click()
  await page.waitForSelector('.split-layout', { timeout: 5000 })
  console.log('✓ Questions panel rendered')

  // Create question
  await page.locator('.split-sidebar .btn.primary', { hasText: '+ New' }).click()
  await page.waitForSelector('form input[type="text"]', { timeout: 5000 })
  await page.locator('form input[type="text"]').fill('Smoke Test Question: Cache Layer')
  await page.locator('form textarea').first().fill('Should we use Redis or Memcached?')
  await page.locator('form .btn.primary', { hasText: 'Create Question' }).click()

  await page.waitForSelector('.list-item', { hasText: 'Smoke Test Question: Cache Layer' })
  console.log('✓ Created open question verified in list')

  // Edit question
  await page.locator('.split-content .btn', { hasText: 'Edit' }).click()
  await page.waitForSelector('form select', { timeout: 5000 })
  await page.locator('form select').selectOption('answered')
  await page.locator('form textarea').nth(1).fill('Redis selected for pub/sub capabilities.')
  await page.locator('form .btn.primary', { hasText: 'Save Changes' }).click()

  await page.waitForSelector('.badge-distinction', { timeout: 5000 })
  const updatedStatus = await page.locator('.tag.ok', { hasText: 'answered' }).first().textContent()
  console.log(`✓ Updated question status: ${updatedStatus}`)

  // --- 2. Decisions & ADR Approval Workflow ---
  console.log('\nTesting Decisions & ADR Sync Workflow...')
  const decisionsBtn = page.locator('.titlebar .modes button', { hasText: 'Decisions' })
  await decisionsBtn.click()
  await page.waitForSelector('.split-layout', { timeout: 5000 })
  console.log('✓ Decisions panel rendered')

  // Create decision
  await page.locator('.split-sidebar .btn.primary', { hasText: '+ New' }).click()
  await page.waitForSelector('form input[type="text"]', { timeout: 5000 })
  await page.locator('form input[type="text"]').first().fill('Smoke Test Decision: Adopt Redis')
  await page.locator('form textarea').nth(0).fill('Need distributed cache with persistence.')
  await page.locator('form textarea').nth(1).fill('Adopt Redis for cache and session storage.')
  await page.locator('form textarea').nth(2).fill('Redis offers clustering, in-memory performance, and persistence.')
  await page.locator('form textarea').nth(3).fill('Operational footprint of running Redis service.')
  await page.locator('form .btn.primary', { hasText: 'Create Decision' }).click()

  await page.waitForSelector('.list-item', { hasText: 'Smoke Test Decision: Adopt Redis' })
  console.log('✓ Created proposed decision verified in list')

  // Check initial status is proposed
  const proposedTag = await page.locator('.split-content .tag.warn', { hasText: 'proposed' }).textContent()
  console.log(`✓ Decision status is initially: ${proposedTag}`)

  // Trigger explicit approval
  const approveBtn = page.locator('.split-content .btn', { hasText: 'Approve Decision' })
  await approveBtn.click()

  // Verify confirmation modal
  await page.waitForSelector('.modal-overlay', { timeout: 5000 })
  console.log('✓ Approval confirmation modal displayed')
  const modalText = await page.locator('.modal-dialog').textContent()
  if (!modalText.includes('No automatic Git commit or push will occur')) {
    throw new Error('Confirmation modal missing safety reminder regarding no automatic commit!')
  }
  console.log('✓ Safety guarantees verified in confirmation modal')

  // Confirm approval
  await page.locator('.modal-dialog .btn.primary', { hasText: 'Confirm Approval & Generate ADR' }).click()
  await page.waitForSelector('.tag.ok', { hasText: 'approved', timeout: 10000 })
  console.log('✓ Decision explicitly approved, status changed to approved')

  // Inspect ADR preview
  await page.locator('.subtab-btn', { hasText: 'ADR Preview' }).click()
  await page.waitForSelector('.doc-viewer', { timeout: 10000 })
  const adrTitle = await page.locator('.doc-viewer h1').textContent()
  console.log(`✓ Rendered ADR Document heading: ${adrTitle}`)

  // Inspect Git diff
  await page.locator('.subtab-btn', { hasText: 'Git Diff' }).click()
  await page.waitForSelector('.diff', { timeout: 10000 })
  const diffContent = await page.locator('.diff').textContent()
  console.log(`✓ Proposed unified Git diff verified (length: ${diffContent.length} chars)`)

  // Verify no page errors occurred
  if (errors.length > 0) {
    console.error('Errors observed during execution:', errors)
    throw new Error(`Encountered ${errors.length} page error(s)`)
  }

  console.log('\n=== All UI Integration Checks PASSED ===')
} finally {
  await browser.close()
}
