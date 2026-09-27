// Regression: opening Settings and pressing Save used to reset the layout and
// deselect the open document.
//
// The cause was in App.tsx, where the Save handler re-selected the workspace.
// selectWorkspace() clears selectedItem/documentPath as part of loading a
// workspace, so a routine settings save threw away the reader's place and sent
// them back to "No document selected".
//
// This drives the real flow -- open a document, open Settings, press Save --
// and asserts the selection and the column widths both survive. Unit-testing
// this is not possible: the bug is a callback wiring mistake across three
// components, and only the assembled app shows it.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

// --- Select a document -----------------------------------------------------
const firstCard = page.locator('.folder-contents-column .object-card').first()
if (!(await firstCard.count())) {
  throw new Error('no documents listed; cannot reproduce the deselection')
}
const docTitle = (await firstCard.locator('.item-title, .object-card-title').first().textContent())?.trim()
await firstCard.click()
await page.waitForTimeout(1200)

const selection = () =>
  page.evaluate(() => {
    const layout = document.querySelector('.mindstack-layout')
    const nav = document.querySelector('.nav-column').getBoundingClientRect()
    const cont = document.querySelector('.folder-contents-column').getBoundingClientRect()
    const active = document.querySelector('.folder-contents-column .object-card.selected')
    const heading = document.querySelector('.file-title, .file-content-column h1')?.textContent?.trim()
    return {
      activeCards: document.querySelectorAll('.folder-contents-column .object-card.selected').length,
      activeText: active?.textContent?.trim().slice(0, 40) ?? null,
      heading: heading ?? null,
      emptyState: /no document selected/i.test(
        document.querySelector('.file-content-column')?.textContent ?? '',
      ),
      widths: {
        nav: Math.round(nav.width),
        contents: Math.round(cont.width),
      },
      tracks: getComputedStyle(layout).gridTemplateColumns,
    }
  })

const before = await selection()
console.log('document clicked:', docTitle)
console.log('before Save:', before)
if (before.emptyState) throw new Error('the document did not open in the first place')
if (before.activeCards === 0) throw new Error('no row is marked selected, cannot test the deselection')

// --- Widen a column first --------------------------------------------------
// "Layout reset" includes the column widths, so drag one before saving and
// check it survives too.
const navHandle = page.locator('.col-resizer-nav')
const b = await navHandle.boundingBox()
await page.mouse.move(b.x + b.width / 2, b.y + 300)
await page.mouse.down()
await page.mouse.move(b.x + b.width / 2 + 50, b.y + 300, { steps: 10 })
await page.mouse.up()
await page.waitForTimeout(300)
const widened = await selection()
console.log('after widening nav:', widened.widths)

// --- Open Settings and press Save ------------------------------------------
await page.locator('.nav-settings-btn').first().click()
await page.waitForSelector('.settings-modal', { timeout: 10000 })
await page.waitForTimeout(1200)

const saveBtn = page.locator('.settings-modal .modal-footer button', { hasText: /save/i }).last()
await saveBtn.click()
// Long enough for the save, the chat-status refresh and any re-render to land.
await page.waitForTimeout(2500)

// The modal stays open after a save (it reports "Saved. New configuration is
// active immediately."), so dismiss it before reading the layout. "Cancel" is
// the dismiss button; a backdrop click deliberately does nothing.
const closeBtn = page.locator('.settings-modal .modal-footer button', { hasText: /cancel|close/i }).first()
if (await closeBtn.count()) {
  await closeBtn.click()
  await page.waitForTimeout(1200)
}

const after = await selection()
console.log('after Save:', after)

if (after.emptyState) {
  throw new Error('SAVING SETTINGS DESELECTED THE DOCUMENT ("No document selected" is back)')
}
if (after.activeCards === 0) {
  throw new Error('SAVING SETTINGS CLEARED THE SELECTED ROW')
}
if (after.widths.nav !== widened.widths.nav || after.widths.contents !== widened.widths.contents) {
  throw new Error(
    `SAVING SETTINGS RESET THE LAYOUT: widths went from ` +
      `${widened.widths.nav}/${widened.widths.contents} to ${after.widths.nav}/${after.widths.contents}`,
  )
}
if (after.activeText !== widened.activeText) {
  console.log(`note: active row text changed: "${widened.activeText}" -> "${after.activeText}"`)
}

await page.screenshot({ path: 'screenshots/settings-save-keeps-selection.png' })

if (errors.length) {
  throw new Error(`console/page errors:\n${errors.join('\n')}`)
}
console.log('OK: saving settings preserves the open document and the column widths')
await browser.close()
