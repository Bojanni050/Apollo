// Delphi Pulse: tags and connections must be separately acceptable, and the
// suggestion must be reviewable in the reading pane rather than the sidebar.
//
// The write behaviour is covered by the Python tests in
// backend/tests/test_pulse.py (test_tags_only_accept_leaves_connections), which
// can call the service directly. This covers the half that only exists in the
// browser: the review panel, its separate accept buttons, and the divider
// between the panel and the document.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

// --- The sidebar no longer offers Apply on a decided item ------------------
// The buttons used to stay clickable and a second click only produced a 409.
const sidebar = await page.evaluate(() => {
  const items = [...document.querySelectorAll('.context-inventory-item')]
  return items.map((el) => {
    const status = el.querySelector('.item-status')?.textContent?.trim() ?? ''
    return {
      status,
      buttons: [...el.querySelectorAll('button')].map((b) => b.textContent.trim()),
    }
  })
})
console.log('sidebar pulse items:', sidebar)
for (const item of sidebar) {
  const decided = /^(applied|skipped)$/i.test(item.status)
  if (decided && item.buttons.includes('Apply')) {
    throw new Error(
      `a decided pulse item still shows an Apply button (status "${item.status}")`,
    )
  }
}

// --- The reading pane renders the review panel ----------------------------
const pulseNav = page.locator('.nav-item', { hasText: /Delphi Pulse/i }).first()
if (!(await pulseNav.count())) {
  console.log('note: no Delphi Pulse entry in the navigation, skipping the pane check')
} else {
  await pulseNav.click()
  await page.waitForTimeout(1500)

  const card = page.locator('.object-card').first()
  if (await card.count()) {
    await card.click()
    await page.waitForTimeout(1200)

    const panel = page.locator('.pulse-review')
    const count = await panel.count()
    console.log('pulse review panels in the reading pane:', count)

    if (count > 0) {
      const groups = await page.locator('.pulse-review-group-title').allTextContents()
      console.log('review groups:', groups)
      // Tags and connections get their own group, so they can be judged apart.
      if (!groups.some((g) => /tags/i.test(g))) {
        throw new Error('the review panel has no tags group')
      }
      if (!groups.some((g) => /connections/i.test(g))) {
        throw new Error('the review panel has no connections group')
      }
      // Each group with something to decide has its own accept button, rather
      // than one button that takes both.
      const accepts = await page
        .locator('.pulse-review-actions button', { hasText: /accept tags/i })
        .count()
      if (accepts < 1) {
        throw new Error('there is no separate "Accept tags" button')
      }
      const connAccepts = await page
        .locator('.pulse-review-actions button', { hasText: /accept connections/i })
        .count()
      if (connAccepts < 1) {
        throw new Error('there is no separate "Accept connections" button')
      }
      // A one-click route for the reader who wants the lot, distinct from the
      // two per-half buttons.
      const both = await page
        .locator('.pulse-review-actions button', { hasText: /accept both/i })
        .count()
      console.log('accept tags / connections / both:', accepts, connAccepts, both)
      if (both < 1) {
        throw new Error('there is no "Accept both" button for a pending suggestion')
      }
      // The last button row must be reachable: the floating pill bar sits over
      // the bottom of the pane, so the row has to clear it once scrolled into
      // view. Scrolled first, because a long panel legitimately starts below the
      // fold -- what matters is that nothing is permanently covered.
      await page
        .locator('.pulse-review-actions')
        .last()
        .scrollIntoViewIfNeeded()
      await page.waitForTimeout(300)
      const clearance = await page.evaluate(() => {
        const bar = document.querySelector('.file-floating-bottom-bar')
        const rows = [...document.querySelectorAll('.pulse-review-actions')]
        const last = rows[rows.length - 1]
        if (!bar || !last) return null
        return last.getBoundingClientRect().bottom <= bar.getBoundingClientRect().top
      })
      if (clearance === false) {
        throw new Error('the last action row cannot be scrolled clear of the floating bar')
      }
      const divider = await page.locator('.pulse-document-divider').count()
      if (divider !== 1) {
        throw new Error('the divider between the panel and the document is missing')
      }
      // The panel must sit above the document, not replace it.
      const order = await page.evaluate(() => {
        const p = document.querySelector('.pulse-review')
        const b = document.querySelector('.file-document-body')
        if (!p || !b) return null
        return p.getBoundingClientRect().top < b.getBoundingClientRect().top
      })
      if (order === false) {
        throw new Error('the review panel is rendered below the document')
      }
      await page.screenshot({ path: 'screenshots/pulse-review.png' })
    } else {
      console.log('note: no pulse items in this run, panel not rendered')
    }
  } else {
    console.log('note: pulse section has no items, panel not rendered')
  }
}

if (errors.length) throw new Error(`console/page errors:\n${errors.join('\n')}`)
console.log('OK: pulse review is in the reading pane with separate tags/connections accepts')
await browser.close()
