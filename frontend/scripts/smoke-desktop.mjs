// Renders the PRODUCTION build served by scripts\desktop.py (the static mount
// on the same origin as /api), rather than the Vite dev server. Start
// desktop.py first and point SMOKE_URL at the port it logs, e.g.
//
//   python scripts\desktop.py --browser
//   set SMOKE_URL=http://127.0.0.1:5274
//   npm run smoke:desktop
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://127.0.0.1:5274'
const API = process.env.SMOKE_API || `${URL}/api`

const browser = await chromium.launch()
const page = await browser.newPage()
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

// The API must answer on this same origin, with no CORS involved -- that is the
// whole point of mounting the bundle inside the FastAPI app.
const health = await page.request.get(`${API}/health`)
console.log('api same-origin ->', health.status(), (await health.json()).status)

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

console.log('page title:', await page.title())
await page.screenshot({ path: 'screenshots/desktop-build.png' })

const body = (await page.textContent('body')) || ''
if (body.trim().length < 20) throw new Error('the app rendered empty')
console.log('rendered chars:', body.trim().length)

const ws = await page.request.get(`${API}/workspaces`)
console.log('workspaces ->', ws.status())

if (errors.length) throw new Error('console errors:\n' + errors.join('\n'))
console.log('OK: production build served same-origin by desktop.py')
await browser.close()
