import { chromium } from 'playwright'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

await page.goto('http://127.0.0.1:5273', { waitUntil: 'networkidle' })

// Wait for cards
await page.waitForSelector('.object-card')

// Click on the second card: "Chronicle hindsight considerations"
const cards = await page.$$('.object-card')
if (cards.length > 1) {
  await cards[1].click()
  await page.waitForTimeout(600)
  const clickedPath = 'C:/Users/bojan/.gemini/antigravity-ide/brain/21dcb9ac-82a0-4af1-9174-b7a14217bae3/ui-clicked-card.png'
  await page.screenshot({ path: clickedPath, fullPage: true })
  console.log('Saved clicked card screenshot to:', clickedPath)
}

await browser.close()
