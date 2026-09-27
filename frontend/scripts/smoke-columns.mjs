// Verifies the two column resizers and the dark scrollbar.
//
// A CSS-only check would not prove either: the scrollbar needs the real
// pseudo-element to be painted, and the resizer needs a real pointer drag,
// since the drag is tracked on window events rather than on the handle. So this
// drives the actual UI in Chromium and reads the geometry back out of the
// layout.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

// Start from the defaults so a stored width from an earlier run cannot make
// the first assertion pass or fail by accident.
await page.evaluate(() => window.localStorage.removeItem('apollo.column-widths'))
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)

const geom = () =>
  page.evaluate(() => {
    const nav = document.querySelector('.nav-column')
    const contents = document.querySelector('.folder-contents-column')
    const layout = document.querySelector('.mindstack-layout')
    return {
      nav: Math.round(nav.getBoundingClientRect().width),
      contents: Math.round(contents.getBoundingClientRect().width),
      viewport: window.innerWidth,
      // The document column is the flexible one; it is what must survive.
      document: Math.round(
        document.querySelector('.file-content-column')?.getBoundingClientRect().width ?? 0,
      ),
      tracks: getComputedStyle(layout).gridTemplateColumns,
    }
  })

// --- Handles exist and are wired ------------------------------------------
const handles = await page.locator('.col-resizer').count()
console.log('resizer handles found:', handles)
if (handles !== 2) throw new Error(`expected 2 resizer handles, found ${handles}`)

const before = await geom()
console.log('initial:', before)

// --- Scrollbar is themed, not the browser default --------------------------
// The complaint that started this: a light grey bar on a near-black page. The
// default theme is a LIGHT theme, so there a pale thumb is correct -- what must
// hold is that the two themes differ, and that the dark one is both dark and
// actually visible.
//
// Measured numerically because at screenshot size a 1.4:1 bar and a 3:1 bar
// look almost identical, and picking by eye is how you end up with an invisible
// scrollbar. The luminance weights (0.2126/0.7152/0.0722) matter here: summing
// the linearised channels unweighted overstates dark colours badly enough to
// make a bad choice look good.
const readThumb = () =>
  page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    const raw = root.getPropertyValue('--scrollbar-thumb').trim()
    const hex = raw.match(/^#([0-9a-f]{6})$/i)?.[1] ?? null

    const channel = (v) => {
      const c = v / 255
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    }
    const luminance = (h) => {
      const n = parseInt(h, 16)
      return (
        0.2126 * channel((n >> 16) & 255) +
        0.7152 * channel((n >> 8) & 255) +
        0.0722 * channel(n & 255)
      )
    }
    const contrast = (a, b) => {
      const l1 = Math.max(luminance(a), luminance(b))
      const l2 = Math.min(luminance(a), luminance(b))
      return (l1 + 0.05) / (l2 + 0.05)
    }

    // The two surfaces a scrollbar actually sits on in Calm Mode: the app
    // canvas and the marginally lighter navigation column.
    const bgApp = root.getPropertyValue('--bg-app').trim()
    const bgNav = root.getPropertyValue('--bg-nav').trim()
    return hex
      ? {
          raw,
          hex,
          vsApp: contrast(hex, bgApp),
          vsNav: contrast(hex, bgNav),
        }
      : { raw, hex: null }
  })

const thumbs = {}
for (const theme of ['default', 'calm']) {
  await page.evaluate((t) => window.localStorage.setItem('apollo.theme', t), theme)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  thumbs[theme] = await readThumb()
  console.log(`scrollbar thumb [${theme}]:`, thumbs[theme])
  if (!thumbs[theme].hex) {
    throw new Error(`could not read --scrollbar-thumb in ${theme}: ${thumbs[theme].raw}`)
  }
}

const r2 = (n) => Math.round(n * 100) / 100
// Calm Mode: the thumb must be visible against the background it sits on. 1.5:1
// is the floor for a control you can actually locate; the shipped colour is
// ~3:1, so this fails loudly if someone darkens it back towards the background.
if (r2(thumbs.calm.vsApp) < 1.5 || r2(thumbs.calm.vsNav) < 1.5) {
  throw new Error(
    `scrollbar thumb is not visible in calm mode: ${thumbs.calm.raw} ` +
      `(vsApp ${r2(thumbs.calm.vsApp)}, vsNav ${r2(thumbs.calm.vsNav)})`,
  )
}
// And it must not have drifted back to being a light bar on a dark page.
if (r2(thumbs.calm.vsApp) > 6) {
  throw new Error(
    `scrollbar thumb is too light for calm mode: ${thumbs.calm.raw} ` +
      `(vsApp ${r2(thumbs.calm.vsApp)})`,
  )
}
if (thumbs.default.hex === thumbs.calm.hex) {
  throw new Error('the scrollbar thumb is identical in both themes, so it does not follow the theme')
}


// --- Drag the first divider ------------------------------------------------
await page.evaluate(() => window.localStorage.setItem('apollo.theme', 'calm'))
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)

const handle = page.locator('.col-resizer-nav')
const box = await handle.boundingBox()
if (!box) throw new Error('nav resizer has no bounding box')

await page.mouse.move(box.x + box.width / 2, box.y + 300)
await page.mouse.down()
// 60px to the right: wider navigation, contents untouched.
await page.mouse.move(box.x + box.width / 2 + 60, box.y + 300, { steps: 12 })
await page.mouse.up()
await page.waitForTimeout(250)

const afterNav = await geom()
console.log('after dragging nav +60:', afterNav)
if (afterNav.nav <= before.nav + 40) {
  throw new Error(`dragging the nav divider right did not widen it (${before.nav} -> ${afterNav.nav})`)
}
if (Math.abs(afterNav.contents - before.contents) > 2) {
  throw new Error(`dragging nav should not move contents (${before.contents} -> ${afterNav.contents})`)
}

// --- Drag the second divider ----------------------------------------------
const handle2 = page.locator('.col-resizer-contents')
const box2 = await handle2.boundingBox()
if (!box2) throw new Error('contents resizer has no bounding box')
await page.mouse.move(box2.x + box2.width / 2, box2.y + 300)
await page.mouse.down()
await page.mouse.move(box2.x + box2.width / 2 + 40, box2.y + 300, { steps: 12 })
await page.mouse.up()
await page.waitForTimeout(250)

const afterContents = await geom()
console.log('after dragging contents +40:', afterContents)
if (afterContents.contents <= afterNav.contents + 20) {
  throw new Error('dragging the contents divider did not widen it')
}

// --- The combined ceiling --------------------------------------------------
// This is the constraint that matters: both columns together may never exceed
// half the screen, or the document column gets squeezed to a sliver. So far
// both drags were well inside the limit -- now push far past it, in both
// directions, and confirm the clamp actually holds.
const ceiling = Math.floor(afterContents.viewport * 0.5)
console.log('combined ceiling:', ceiling, 'current combined:', afterContents.nav + afterContents.contents)

// Shove the contents divider hard to the right, well past the ceiling.
const box3 = await page.locator('.col-resizer-contents').boundingBox()
await page.mouse.move(box3.x + box3.width / 2, box3.y + 300)
await page.mouse.down()
await page.mouse.move(box3.x + box3.width / 2 + 1200, box3.y + 300, { steps: 20 })
await page.mouse.up()
await page.waitForTimeout(250)

const pushed = await geom()
console.log('after shoving contents +1200:', pushed)
if (pushed.nav + pushed.contents > ceiling + 1) {
  throw new Error(
    `columns exceed half the screen: ${pushed.nav + pushed.contents} > ${ceiling}`,
  )
}
if (pushed.document <= 0) {
  throw new Error('the document column was squeezed out of existence')
}
// The clamp should bite exactly at the ceiling, not merely under it: if it
// stopped well short, the limit would be a rule nobody notices.
if (Math.abs(pushed.nav + pushed.contents - ceiling) > 2) {
  throw new Error(
    `the clamp stopped at ${pushed.nav + pushed.contents} instead of the ${ceiling}px ceiling`,
  )
}

// And it must not go below the minimums either: a column squeezed to nothing is
// just as unusable as one that eats the screen.
const box4 = await page.locator('.col-resizer-nav').boundingBox()
await page.mouse.move(box4.x + box4.width / 2, box4.y + 300)
await page.mouse.down()
await page.mouse.move(box4.x + box4.width / 2 - 1200, box4.y + 300, { steps: 20 })
await page.mouse.up()
await page.waitForTimeout(250)

const squeezed = await geom()
console.log('after shoving nav -1200:', squeezed)
if (squeezed.nav < 160) {
  throw new Error(`the navigation column collapsed to ${squeezed.nav}px`)
}
if (squeezed.contents < 200) {
  throw new Error(`the contents column collapsed to ${squeezed.contents}px`)
}

// --- Persistence -----------------------------------------------------------
// Asserted on a realistic set, not the shoved-to-the-limit one, so a failure
// here means persistence broke rather than that the clamp changed.
await page.evaluate(() => {
  window.localStorage.removeItem('apollo.column-widths')
})
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(700)

const handleR = page.locator('.col-resizer-nav')
const bR = await handleR.boundingBox()
await page.mouse.move(bR.x + bR.width / 2, bR.y + 300)
await page.mouse.down()
await page.mouse.move(bR.x + bR.width / 2 + 40, bR.y + 300, { steps: 10 })
await page.mouse.up()
await page.waitForTimeout(200)
const stored = await geom()
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)
const afterReload = await geom()
console.log('before reload:', stored.nav, stored.contents, '| after reload:', afterReload.nav, afterReload.contents)
if (afterReload.nav !== stored.nav || afterReload.contents !== stored.contents) {
  throw new Error('column widths did not survive a reload')
}

// --- Keyboard --------------------------------------------------------------
// Dragging alone would leave the layout unreachable without a mouse, so the
// handle is focusable and the arrow keys nudge it. Locators are re-acquired
// after the reloads above rather than reused, since the old handles are gone.
const keyHandle = page.locator('.col-resizer-contents')
await keyHandle.focus()
const focusable = await page.evaluate(
  () => document.activeElement?.className?.includes?.('col-resizer') ?? false,
)
if (!focusable) throw new Error('the contents resizer cannot take focus')

const keyBefore = (await geom()).contents
await page.keyboard.press('ArrowLeft')
await page.waitForTimeout(200)
const afterKey = await geom()
console.log('after ArrowLeft:', afterKey.contents, '(was', keyBefore + ')')
if (Math.abs(afterKey.contents - (keyBefore - 16)) > 2) {
  throw new Error(`ArrowLeft did not nudge by 16px (${keyBefore} -> ${afterKey.contents})`)
}

// Home restores the defaults, so a layout wrecked by a stray drag is one
// keystroke from recoverable.
await page.keyboard.press('Home')
await page.waitForTimeout(200)
const afterHome = await geom()
console.log('after Home:', afterHome.nav, afterHome.contents)
if (afterHome.nav !== 230 || afterHome.contents !== 290) {
  throw new Error(`Home did not restore the defaults (${afterHome.nav}, ${afterHome.contents})`)
}

// --- Context sidebar closed ------------------------------------------------
// The grid template changes when the context panel is hidden, so confirm the
// handles still line up with the column edges and the drag still works.
const hideCtx = page.locator('.context-close-btn, [aria-label*="Hide context" i]').first()
if (await hideCtx.count()) {
  await hideCtx.click().catch(() => {})
  await page.waitForTimeout(500)
  const closedGeom = await geom()
  console.log('context closed:', closedGeom)
  // The handle sits on the border, so its centre must match the column edge.
  const edge = await page.evaluate(() => {
    const nav = document.querySelector('.nav-column').getBoundingClientRect()
    const h = document.querySelector('.col-resizer-nav').getBoundingClientRect()
    return { navRight: Math.round(nav.right), handleCentre: Math.round(h.left + h.width / 2) }
  })
  console.log('nav edge vs handle centre:', edge)
  if (Math.abs(edge.navRight - edge.handleCentre) > 1) {
    throw new Error(
      `the nav handle is not on the column border (edge ${edge.navRight}, handle ${edge.handleCentre})`,
    )
  }
  if (closedGeom.nav + closedGeom.contents > ceiling + 1) {
    throw new Error('the ceiling does not hold with the context sidebar closed')
  }
}

// --- Narrower window --------------------------------------------------------
// The ceiling is a fraction of the viewport, so shrinking the window has to
// shrink the columns with it.
await page.setViewportSize({ width: 820, height: 900 })
await page.waitForTimeout(500)
const narrow = await geom()
const narrowCeiling = Math.floor(narrow.viewport * 0.5)
console.log('at 820px wide:', narrow, 'ceiling', narrowCeiling)
if (narrow.nav + narrow.contents > narrowCeiling + 1) {
  throw new Error(
    `columns did not re-clamp on resize: ${narrow.nav + narrow.contents} > ${narrowCeiling}`,
  )
}
if (narrow.document <= 0) {
  throw new Error('the document column vanished at 820px')
}
await page.setViewportSize({ width: 1440, height: 900 })
await page.waitForTimeout(400)

// --- Screenshots -----------------------------------------------------------
await page.screenshot({ path: 'screenshots/columns-calm.png' })
await page.evaluate(() => {
  window.localStorage.removeItem('apollo.column-widths')
  window.localStorage.setItem('apollo.theme', 'default')
})
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)
await page.screenshot({ path: 'screenshots/columns-default.png' })

if (errors.length) {
  throw new Error(`console/page errors:\n${errors.join('\n')}`)
}
console.log('column resize + scrollbar smoke: OK')
await browser.close()
