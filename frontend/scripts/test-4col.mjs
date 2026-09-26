import { chromium } from 'playwright'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

await page.goto('http://127.0.0.1:5273', { waitUntil: 'networkidle' })

// Verify 4 columns exist
const navCol = await page.$('.nav-column')
const folderCol = await page.$('.folder-contents-column')
const fileCol = await page.$('.file-content-column')
const contextCol = await page.$('.context-sidebar')

console.log('Columns found:', {
  navCol: !!navCol,
  folderCol: !!folderCol,
  fileCol: !!fileCol,
  contextCol: !!contextCol,
})

const artifactPath = 'C:/Users/bojan/.gemini/antigravity-ide/brain/21dcb9ac-82a0-4af1-9174-b7a14217bae3/ui-layout-screenshot.png'
await page.screenshot({ path: artifactPath, fullPage: true })
console.log('Saved screenshot to:', artifactPath)

// Test collapsing context column
const closeBtn = await page.$('.context-close-btn')
if (closeBtn) {
  await closeBtn.click()
  await page.waitForTimeout(350)
  const collapsedPath = 'C:/Users/bojan/.gemini/antigravity-ide/brain/21dcb9ac-82a0-4af1-9174-b7a14217bae3/ui-collapsed-screenshot.png'
  await page.screenshot({ path: collapsedPath, fullPage: true })
  console.log('Saved collapsed screenshot to:', collapsedPath)
}

await browser.close()
