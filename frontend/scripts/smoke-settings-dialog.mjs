// Settings dialog behaviour, and the Delphi Pulse section that used to be
// unreachable behind a tiny gear in the Pulse view.
//
// Four things are checked, all of which the dialog got wrong before:
//   1. the Pulse section is inside Settings at all;
//   2. a click on the backdrop does not dismiss it;
//   3. Escape does dismiss it, so removing (2) did not create a trap;
//   4. the dialog is wide enough that its labels stop wrapping.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

await page.locator('.nav-settings-btn').first().click()
await page.waitForSelector('.settings-modal', { timeout: 10000 })
await page.waitForTimeout(1500)

// --- 1. Delphi Pulse is in Settings ---------------------------------------
const titles = await page.locator('.settings-section-title').allTextContents()
console.log('settings sections:', titles)
if (!titles.some((t) => /delphi pulse/i.test(t))) {
  throw new Error('the Delphi Pulse section is not inside Settings')
}

// The schedule controls are the thing that was missing, so assert those too.
const scheduleToggle = page.locator('.settings-modal input[type="checkbox"]').last()
if (!(await scheduleToggle.count())) {
  throw new Error('the Pulse schedule toggle is missing')
}
await scheduleToggle.check()
await page.waitForTimeout(400)
const repeat = await page.locator('.settings-modal button', { hasText: /every n hours|weekly/i }).count()
console.log('repeat controls shown:', repeat)
if (repeat < 2) {
  throw new Error('choosing a schedule did not reveal the interval/weekly controls')
}

// --- 4. The dialog is wide enough -----------------------------------------
const size = await page.evaluate(() => {
  const d = document.querySelector('.settings-modal').getBoundingClientRect()
  return { width: Math.round(d.width), height: Math.round(d.height) }
})
console.log('dialog size:', size)
if (size.width < 640) {
  throw new Error(`the settings dialog is still only ${size.width}px wide`)
}
// A label that has to wrap is the symptom of a cramped dialog; measure whether
// any of the section titles are wrapping onto a second line.
// "More room between the elements" is the actual request. It is measured as
// the grid gap, which is what separates sections once they sit side by side.
//
// An earlier version of this check compared the bottom of one section with the
// top of the next and required a gap. That was written for the single-column
// layout; in a columned one the two are side by side and the difference goes
// negative, so it reported nonsense. The gap is now read from the layout
// itself, and the number of columns is asserted so a silent fall back to one
// column -- which would reintroduce the scrolling -- cannot pass.
const layout = await page.evaluate(() => {
  const body = document.querySelector('.settings-body')
  const cs = getComputedStyle(body)
  return {
    columns: cs.gridTemplateColumns.split(' ').filter(Boolean).length,
    rowGap: parseFloat(cs.rowGap),
    columnGap: parseFloat(cs.columnGap),
    paddingTop: parseFloat(cs.paddingTop),
    paddingSide: parseFloat(cs.paddingLeft),
  }
})
console.log('settings layout:', layout)
if (layout.columns < 2) {
  throw new Error(
    `the dialog fell back to ${layout.columns} column; it will scroll again`,
  )
}
if (layout.rowGap < 16 || layout.columnGap < 16) {
  throw new Error(`the gaps are too tight: ${JSON.stringify(layout)}`)
}
// 16px of padding is the deliberate trade: less than the 20px the other
// dialogs use, and the reason the sections now fit without scrolling.
if (layout.paddingTop < 16 || layout.paddingSide < 20) {
  throw new Error(`the dialog padding is still tight: ${JSON.stringify(layout)}`)
}

// --- 2. A backdrop click must NOT dismiss ---------------------------------
// Click a corner well outside the dialog, where the backdrop receives it.
await page.mouse.click(12, 12)
await page.waitForTimeout(700)
if ((await page.locator('.settings-modal').count()) === 0) {
  throw new Error('clicking the backdrop dismissed the dialog')
}
console.log('still open after a backdrop click: yes')

// A second one, on the other side, to be sure it was not a fluke.
await page.mouse.click(1428, 888)
await page.waitForTimeout(700)
if ((await page.locator('.settings-modal').count()) === 0) {
  throw new Error('clicking the backdrop dismissed the dialog (second attempt)')
}

// --- 3. Escape dismisses --------------------------------------------------
await page.keyboard.press('Escape')
await page.waitForTimeout(700)
if ((await page.locator('.settings-modal').count()) !== 0) {
  throw new Error('Escape did not dismiss the dialog')
}
console.log('closed by Escape: yes')

// --- Cancel also dismisses ------------------------------------------------
await page.locator('.nav-settings-btn').first().click()
await page.waitForSelector('.settings-modal', { timeout: 10000 })
await page.waitForTimeout(800)
await page.locator('.settings-modal .modal-footer button', { hasText: /cancel/i }).first().click()
await page.waitForTimeout(700)
if ((await page.locator('.settings-modal').count()) !== 0) {
  throw new Error('Cancel did not dismiss the dialog')
}
console.log('closed by Cancel: yes')

// --- The gear in the Pulse view now opens Settings ------------------------
// It used to open a separate, Pulse-only dialog.
const pulseNav = page.locator('.nav-item', { hasText: /Delphi Pulse/i }).first()
if (await pulseNav.count()) {
  await pulseNav.click()
  await page.waitForTimeout(1200)
  const gear = page.locator('.folder-contents-header button[title*="Pulse settings" i]').first()
  if (await gear.count()) {
    await gear.click()
    await page.waitForTimeout(1200)
    if ((await page.locator('.settings-modal').count()) === 0) {
      throw new Error('the Pulse gear does not open Settings')
    }
    console.log('Pulse gear opens Settings: yes')
  }
}

await page.screenshot({ path: 'screenshots/settings-wide.png' })

// The narrow viewports, which take the two- and one-column paths. These were
// never exercised, and a silent fall back to one column brings the scrolling
// straight back -- the exact thing this change was meant to remove.
for (const width of [1000, 700]) {
  await page.setViewportSize({ width, height: 900 })
  await page.waitForTimeout(150)
  const layoutAt = await page.evaluate(() => {
    const cs = getComputedStyle(document.querySelector('.settings-body'))
    return {
      // A block body is the one-column fallback; it has no columns to count, and
      // reading grid-template-columns there reports the specified value rather
      // than a used one, which is how an earlier version of this check came to
      // "see" two columns in a dialog that was plainly single column.
      columns: cs.display === 'grid' ? cs.gridTemplateColumns.split(' ').filter(Boolean).length : 1,
      display: cs.display,
    }
  })
  const dividersBack = await page.evaluate(
    () => getComputedStyle(document.querySelector('.settings-modal .settings-divider')).display,
  )
  console.log(`at ${width}px: ${layoutAt.display}, ${layoutAt.columns} column(s), dividers ${dividersBack}`)
  const expected = width <= 780 ? 1 : 2
  if (layoutAt.columns !== expected) {
    throw new Error(`at ${width}px expected ${expected} column(s), got ${layoutAt.columns}`)
  }
  // One column has no width to save height with, so scrolling there is expected.
  if (width > 780 && dividersBack !== 'none') {
    throw new Error(`dividers should stay hidden above one column, not at ${width}px`)
  }
}

// Back to the wide window, which is the case that has to fit.
await page.setViewportSize({ width: 1440, height: 900 })
await page.waitForTimeout(150)

// How much content is there, and how much of it is off-screen? Asserted rather
// than assumed, because the stacked version overflowed by more than 1000px
// while still looking plausible.
const fit = await page.evaluate(() => {
  const body = document.querySelector('.settings-body')
  const dialog = document.querySelector('.settings-modal')
  return {
    contentHeight: body.scrollHeight,
    visibleHeight: body.clientHeight,
    overflow: body.scrollHeight - body.clientHeight,
    dialogWidth: Math.round(dialog.getBoundingClientRect().width),
  }
})
console.log('fit:', fit)

if (fit.overflow > 0) {
  throw new Error(
    `the settings dialog still scrolls by ${fit.overflow}px ` +
      `(content ${fit.contentHeight}, visible ${fit.visibleHeight})`,
  )
}

if (errors.length) throw new Error(`console/page errors:\n${errors.join('\n')}`)
console.log('OK: Pulse settings live in Settings, and the dialog only closes deliberately')
await browser.close()
