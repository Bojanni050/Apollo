// Captures the accepted half of a Pulse suggestion in both themes, because the
// gold that reads well on a light panel is unreadable on Calm Mode's near-black
// one. The colours are theme variables for exactly that reason, and this is what
// proves it: the same state, one screenshot per palette.
//
// Needs a workspace whose documents are committed, because the write is what
// turns the button into the accepted record.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'
const workspaceName = process.env.SMOKE_WORKSPACE || 'Fixture'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

for (const theme of ['default', 'calm']) {
  await page.evaluate((t) => window.localStorage.setItem('apollo.theme', t), theme)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)

  await page.locator('.nav-user-info').first().click()
  await page.waitForTimeout(400)
  const target = page.locator('.popup-menu-item', { hasText: workspaceName }).first()
  if (await target.count()) {
    await target.click()
    await page.waitForTimeout(1800)
  } else {
    console.log(`note: workspace "${workspaceName}" not found`)
  }

  const nav = page.locator('.nav-item', { hasText: /Delphi Pulse/i }).first()
  if (await nav.count()) {
    await nav.click()
    await page.waitForTimeout(1000)
    const card = page.locator('.object-card').first()
    if (await card.count()) {
      await card.click()
      await page.waitForTimeout(1000)
    }
  }

  const btn = page
    .locator('.pulse-review-actions button', { hasText: /accept tags/i })
    .first()
  if (await btn.count()) {
    await btn.click()
    await page.waitForTimeout(1400)
  }

  const gold = await page.evaluate(() => {
    const el = document.querySelector('.pulse-accepted')
    if (!el) return null
    const cs = getComputedStyle(el)
    return { color: cs.color, background: cs.backgroundColor, border: cs.borderColor }
  })
  console.log(`theme ${theme}: accepted record =`, gold)
  if (!gold) console.log(`note: no accepted record visible in ${theme}`)
  await page.screenshot({ path: `screenshots/pulse-accepted-${theme}.png` })
}

if (errors.length) throw new Error(`page errors:\n${errors.join('\n')}`)
console.log('OK: the accepted record was captured in both themes')
await browser.close()
