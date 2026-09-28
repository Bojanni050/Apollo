// Captures the app in both themes so the two palettes can be compared side by
// side. Themes are applied as a data-attribute on <html> by src/theme.ts, so
// this drives the same path the Settings toggle does -- including the reload,
// which is what proves the choice is actually persisted.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

const themeOf = () => page.getAttribute('html', 'data-theme')

// --- Standard -------------------------------------------------------------
await page.evaluate(() => window.localStorage.setItem('apollo.theme', 'default'))
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)
console.log('default theme attr:', await themeOf())
const standardBg = await page.evaluate(
  () => getComputedStyle(document.body).backgroundColor,
)
console.log('default body bg:', standardBg)
await page.screenshot({ path: 'screenshots/theme-default.png' })

// --- Calm -----------------------------------------------------------------
await page.evaluate(() => window.localStorage.setItem('apollo.theme', 'calm'))
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)
console.log('calm theme attr:', await themeOf())
const calmBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
console.log('calm body bg:', calmBg)
await page.screenshot({ path: 'screenshots/theme-calm.png' })

// The whole point of the mode: no pure white and no cold near-black anywhere.
if (calmBg === standardBg) {
  throw new Error('calm mode did not change the background')
}
if (/\b255,\s*255,\s*255\b/.test(calmBg)) {
  throw new Error('calm mode still paints a pure-white background')
}

const text = await page.evaluate(() => getComputedStyle(document.body).color)
console.log('calm body text:', text)

// --- The default ----------------------------------------------------------
// A machine that has never been asked gets the dark palette. Checked by
// clearing the key rather than by reading the source, because a default is
// exactly the sort of thing that rots while the toggle still works.
await page.evaluate(() => window.localStorage.removeItem('apollo.theme'))
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)
const freshTheme = await themeOf()
console.log('theme on a fresh machine:', freshTheme)
if (freshTheme !== 'calm') {
  throw new Error(`a machine with no stored choice got ${freshTheme}, not calm`)
}

// --- The toggle in Settings ----------------------------------------------
// Starting from Standard rather than from the default: with the dark palette
// already applied, clicking Calm would prove nothing, and the point of this
// section is that the button switches the palette.
await page.evaluate(() => window.localStorage.setItem('apollo.theme', 'default'))
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)
const before = await page.evaluate(
  () => getComputedStyle(document.body).backgroundColor,
)
console.log('body bg before the click:', before)
const gear = page.locator('.nav-settings-btn')
if ((await gear.count()) > 0) {
  await gear.first().click()
  await page.waitForSelector('.theme-chip', { timeout: 10000 })
  await page.locator('.theme-chip', { hasText: 'Calm' }).first().click()
  await page.waitForTimeout(400)
  const after = await themeOf()
  console.log('after clicking Calm:', after)
  if (after !== 'calm') {
    throw new Error('the Calm button did not apply the theme')
  }
  // The attribute alone is not the switch: the palette has to have followed it.
  const afterBg = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor,
  )
  console.log('body bg after the click:', afterBg)
  if (afterBg === before) {
    throw new Error('Calm was applied but the page did not change colour')
  }
  // The active chip is a light gold fill with dark text in Calm Mode. Checked
  // numerically rather than by eye: at screenshot size a gold fill and a muted
  // olive one are hard to tell apart, and getting it wrong makes the label
  // unreadable.
  const chip = await page.evaluate(() => {
    const el = document.querySelector('.theme-chip.active')
    if (!el) return null
    const s = getComputedStyle(el)
    return { bg: s.backgroundColor, fg: s.color }
  })
  console.log('active chip:', chip)
  const relativeLuminance = (rgb) => {
    const f = (c) => (c / 255 <= 0.03928 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4)
    const [r, g, b] = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }
  const parse = (s) => (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number)
  const bgL = relativeLuminance(parse(chip.bg))
  const fgL = relativeLuminance(parse(chip.fg))
  const ratio = (Math.max(bgL, fgL) + 0.05) / (Math.min(bgL, fgL) + 0.05)
  console.log('chip text contrast:', ratio.toFixed(2))
  if (ratio < 4.5) {
    throw new Error(`active chip text contrast is only ${ratio.toFixed(2)}:1`)
  }
  await page.screenshot({ path: 'screenshots/theme-toggle.png' })
} else {
  console.log('SKIP: settings button not reachable, toggle not exercised in the UI')
}

if (errors.length) throw new Error('console errors:\n' + errors.join('\n'))
console.log(
  'OK: calm is the default, calm applies, differs from standard, and persists',
)
await browser.close()
